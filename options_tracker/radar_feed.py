"""Dhan v2 feed adapter. No order APIs or credentials are persisted here."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
import heapq
import logging
from math import isfinite
import pickle
import struct
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
import requests

from .models import (
    RadarBar, RadarInstrument, RadarOpportunity, RadarOptionPoint, RadarQuote, RadarWatch, RadarWorker,
    RadarAssistantSnapshot,
)
from .radar import Candle, HOLDING, WATCHING, validate_candle
from .radar_sessions import continuous_timestamp


LOGGER = logging.getLogger(__name__)
SEGMENTS = {1: "NSE_EQ", 2: "NSE_FNO"}
IST = ZoneInfo("Asia/Kolkata")
DHAN_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"


@dataclass(frozen=True)
class Tick:
    segment: str
    security_id: str
    source_at: datetime
    received_at: datetime
    last: float
    volume: int
    bid: float = 0
    ask: float = 0
    bid_qty: int = 0
    ask_qty: int = 0
    oi: int | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    previous_close: float | None = None


def decode_packets(data: bytes, received_at: datetime) -> list[Tick]:
    ticks = []
    offset = 0
    while offset < len(data):
        if len(data)-offset < 8:
            LOGGER.warning(
                "Discarded malformed Dhan frame remainder: truncated header with %s bytes.",
                len(data)-offset,
            )
            break
        code, length, segment, security_id = struct.unpack_from("<BHBI", data, offset)
        if length < 8 or offset+length > len(data):
            LOGGER.warning(
                "Discarded malformed Dhan frame remainder: packet length %s exceeds %s available bytes.",
                length, len(data)-offset,
            )
            break
        packet = data[offset:offset+length]
        offset += length
        if code == 50:
            reason = struct.unpack_from("<H", packet, 8)[0] if length >= 10 else 0
            raise ConnectionError(f"Dhan disconnected the feed (code {reason}).")
        if code != 8 or segment not in SEGMENTS:
            continue
        if length != 162:
            LOGGER.warning(
                "Discarded unsupported Dhan packet for %s:%s with code %s and length %s.",
                SEGMENTS[segment], security_id, code, length,
            )
            continue
        last, _, epoch = struct.unpack_from("<fHI", packet, 8)
        volume = struct.unpack_from("<I", packet, 22)[0]
        bid_qty, ask_qty, _, _, bid, ask = struct.unpack_from("<IIHHff", packet, 62)
        oi = struct.unpack_from("<I", packet, 34)[0] if segment == 2 else None
        opening, _closing, maximum, minimum = struct.unpack_from("<ffff", packet, 46)
        if not all(isfinite(x) and x >= 0 for x in (last, bid, ask)) or last <= 0:
            LOGGER.warning(
                "Discarded invalid Dhan price packet for %s:%s; other packets remain usable.",
                SEGMENTS[segment], security_id,
            )
            continue
        ticks.append(Tick(SEGMENTS[segment], str(security_id),
                          # Feed LTT encodes exchange wall time in the UTC components.
                          datetime.fromtimestamp(epoch, dt_timezone.utc).replace(tzinfo=IST), received_at,
                          last, volume, bid, ask, bid_qty, ask_qty, oi,
                          opening if isfinite(opening) and opening > 0 else None,
                          maximum if isfinite(maximum) and maximum > 0 else None,
                          minimum if isfinite(minimum) and minimum > 0 else None,
                          None))
    return ticks


def fetch_quote_snapshot(
    instruments: list[RadarInstrument],
    access_token: str,
    client_id: str,
    received_at: datetime,
) -> list[Tick] | None:
    """Fetch one rate-coordinated quote snapshot when the socket is silent."""
    from .services import (
        DHAN_LTP_RATE_LIMIT_SECONDS,
        _acquire_dhan_ltp_slot,
        _finish_dhan_ltp_request,
    )

    slot = _acquire_dhan_ltp_slot()
    if not slot["acquired"]:
        return None
    payload: dict[str, list[int]] = {}
    for instrument in instruments:
        payload.setdefault(instrument.segment, []).append(int(instrument.security_id))
    try:
        response = requests.post(
            DHAN_QUOTE_URL,
            json=payload,
            headers={
                "access-token": access_token,
                "client-id": client_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        if response.status_code == 429:
            try:
                retry_after = int(response.headers.get("Retry-After", "0"))
            except (TypeError, ValueError):
                retry_after = 0
            cooldown = max(DHAN_LTP_RATE_LIMIT_SECONDS, retry_after)
            _finish_dhan_ltp_request(cooldown_seconds=cooldown)
            raise ConnectionError(
                f"Dhan quote rate limit active for {cooldown} seconds."
            )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Dhan quote fallback returned invalid JSON.")
    except (requests.RequestException, ValueError) as error:
        _finish_dhan_ltp_request()
        raise ConnectionError(
            "Dhan quote fallback failed without exposing credentials."
        ) from error
    _finish_dhan_ltp_request()

    by_identity = {
        (instrument.segment, instrument.security_id): instrument
        for instrument in instruments
    }
    ticks = []
    invalid = 0
    for segment, quotes in body.get("data", {}).items():
        if not isinstance(quotes, dict):
            invalid += 1
            continue
        for security_id, quote in quotes.items():
            if (segment, str(security_id)) not in by_identity or not isinstance(quote, dict):
                invalid += 1
                continue
            try:
                source_at = timezone.make_aware(
                    datetime.strptime(
                        str(quote["last_trade_time"]),
                        "%d/%m/%Y %H:%M:%S",
                    ),
                    IST,
                )
                depth = quote.get("depth") or {}
                buys = depth.get("buy") or []
                sells = depth.get("sell") or []
                best_bid = next(
                    (item for item in buys if float(item.get("price") or 0) > 0),
                    {},
                )
                best_ask = next(
                    (item for item in sells if float(item.get("price") or 0) > 0),
                    {},
                )
                ohlc = quote.get("ohlc") or {}
                ticks.append(Tick(
                    segment=segment,
                    security_id=str(security_id),
                    source_at=source_at,
                    received_at=received_at,
                    last=float(quote["last_price"]),
                    volume=int(quote.get("volume") or 0),
                    bid=float(best_bid.get("price") or 0),
                    ask=float(best_ask.get("price") or 0),
                    bid_qty=int(best_bid.get("quantity") or 0),
                    ask_qty=int(best_ask.get("quantity") or 0),
                    oi=int(quote.get("oi") or 0) if segment == "NSE_FNO" else None,
                    day_open=float(ohlc.get("open") or 0) or None,
                    day_high=float(ohlc.get("high") or 0) or None,
                    day_low=float(ohlc.get("low") or 0) or None,
                    previous_close=float(ohlc.get("close") or 0) or None,
                ))
            except (KeyError, TypeError, ValueError):
                invalid += 1
    if invalid:
        LOGGER.warning(
            "Dhan quote fallback ignored %s malformed or unexpected records.",
            invalid,
        )
    return ticks


class TickBuffer:
    """Wait within the existing freshness budget; never publish future quotes."""

    def __init__(self):
        self.pending = []
        self.sequence = 0

    def ready(self, ticks: list[Tick], now: datetime) -> list[Tick]:
        ready = []
        for tick in ticks:
            if tick.source_at > now+timedelta(seconds=15):
                ready.append(tick)  # Existing collector validation rejects larger anomalies.
                continue
            self.sequence += 1
            heapq.heappush(self.pending, (tick.source_at, self.sequence, tick))
            if len(self.pending) > 5000:
                raise ValueError("Source-clock wait buffer exceeded its bounded capacity.")
        while self.pending and self.pending[0][0] <= now:
            ready.append(heapq.heappop(self.pending)[2])
        return ready


def sync_instruments(rows: Iterable[dict], now: datetime) -> dict:
    """Derive the stock F&O universe from active NSE OPTSTK master rows."""
    today = timezone.localdate(now)
    equities = {}
    symbols = set()
    identities = set()
    option_count = 0

    with tempfile.TemporaryFile() as records:
        for row in rows:
            if row.get("EXCH_ID") != "NSE":
                continue
            symbol = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
            security_id = (row.get("SECURITY_ID") or "").strip()
            if not symbol or not security_id:
                continue
            if row.get("INSTRUMENT") == "EQUITY" and row.get("SEGMENT") == "E":
                equities[symbol] = row
            if row.get("INSTRUMENT") != "OPTSTK":
                continue
            try:
                expiry = datetime.fromisoformat((row.get("SM_EXPIRY_DATE") or "").strip()).date()
                strike, lot = float(row["STRIKE_PRICE"]), float(row["LOT_SIZE"])
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError(f"Malformed option reference row for {symbol}.") from error
            if expiry < today:
                continue
            if not isfinite(strike) or strike <= 0 or not isfinite(lot) or lot < 1 or not lot.is_integer() or row.get("OPTION_TYPE") not in ["CE", "PE"]:
                raise ValueError(f"Invalid option contract metadata for {symbol}.")
            identity = ("NSE_FNO", security_id)
            if identity in identities:
                raise ValueError("Duplicate master identities; registry unchanged.")
            identities.add(identity)
            symbols.add(symbol)
            option_count += 1
            pickle.dump({
                "security_id": security_id, "segment": "NSE_FNO", "symbol": symbol,
                "name": row.get("DISPLAY_NAME", ""), "kind": "OPTION",
                "expiry": expiry, "strike": strike, "side": row["OPTION_TYPE"],
                "lot_size": int(lot), "active": True, "refreshed_at": now,
            }, records)

        if not symbols or not symbols <= equities.keys():
            raise ValueError("Master has no active stock options or is missing corresponding NSE equities; registry unchanged.")
        for symbol in sorted(symbols):
            equity = equities[symbol]
            security_id = equity["SECURITY_ID"].strip()
            identity = ("NSE_EQ", security_id)
            if identity in identities:
                raise ValueError("Duplicate master identities; registry unchanged.")
            identities.add(identity)
            pickle.dump({
                "security_id": security_id, "segment": "NSE_EQ", "symbol": symbol,
                "name": equity.get("DISPLAY_NAME", symbol), "kind": "EQUITY",
                "expiry": None, "strike": None, "side": "", "lot_size": 1,
                "active": True, "refreshed_at": now,
            }, records)

        records.seek(0)
        created_identities = 0
        identity_fields = ("segment", "security_id", "symbol", "kind", "expiry", "strike", "side")
        with transaction.atomic():
            RadarInstrument.objects.filter(active=True).update(active=False)
            while True:
                batch = []
                try:
                    for _ in range(500):
                        batch.append(pickle.load(records))
                except EOFError:
                    pass
                if not batch:
                    break

                query = Q()
                for segment in ("NSE_EQ", "NSE_FNO"):
                    security_ids = [record["security_id"] for record in batch if record["segment"] == segment]
                    if security_ids:
                        query |= Q(segment=segment, security_id__in=security_ids)
                existing = {}
                for instrument in RadarInstrument.objects.filter(query).only(
                    "pk", *identity_fields, "name", "lot_size", "active", "refreshed_at"
                ).order_by("pk"):
                    identity = tuple(getattr(instrument, field) for field in identity_fields)
                    existing[identity] = instrument

                to_update = []
                to_create = []
                for record in batch:
                    identity = tuple(record[field] for field in identity_fields)
                    instrument = existing.get(identity)
                    if instrument is None:
                        to_create.append(RadarInstrument(**record))
                        continue
                    instrument.name = record["name"]
                    instrument.lot_size = record["lot_size"]
                    instrument.active = True
                    instrument.refreshed_at = now
                    to_update.append(instrument)
                if to_update:
                    RadarInstrument.objects.bulk_update(
                        to_update,
                        ["name", "lot_size", "active", "refreshed_at"],
                        batch_size=500,
                    )
                if to_create:
                    RadarInstrument.objects.bulk_create(to_create, batch_size=500)
                    created_identities += len(to_create)
                if len(batch) < 500:
                    break

    return {
        "underlyings": len(symbols),
        "options": option_count,
        "created_identities": created_identities,
    }


def subscriptions(now: datetime) -> list[RadarInstrument]:
    equities = list(RadarInstrument.objects.filter(active=True, kind="EQUITY").order_by("symbol"))
    if len(equities) > 4000:
        raise ValueError("Underlying universe exceeds this worker's subscription budget.")
    selected = {item.pk: item for item in equities}
    positions = RadarOpportunity.objects.filter(state__in=HOLDING, contract__isnull=False).select_related("contract")
    for item in positions:
        if item.contract.expiry is not None and item.contract.expiry >= timezone.localdate(now):
            selected[item.contract_id] = item.contract
    watching = RadarOpportunity.objects.filter(state__in=WATCHING, valid_until__gt=now)
    symbols = set(watching.values_list("symbol", flat=True).distinct())
    symbols.update(RadarWatch.objects.filter(instrument__active=True).values_list("instrument__symbol", flat=True))
    snapshot = RadarAssistantSnapshot.objects.filter(timestamp__lte=now, expires_at__gt=now).first()
    if snapshot:
        symbols.update(row["symbol"] for side in ["bullish", "bearish"] for row in snapshot.payload.get(side, []))
    for symbol in symbols:
        quote = RadarQuote.objects.filter(instrument__symbol=symbol, instrument__kind="EQUITY", instrument__active=True).first()
        if quote is None:
            continue
        contracts = list(RadarInstrument.objects.filter(
            symbol=symbol, kind="OPTION", active=True,
            expiry__gt=timezone.localdate(now)+timedelta(days=2),
        ))
        expiries = sorted({c.expiry for c in contracts})[:2]
        for expiry in expiries:
            for side in ["CE", "PE"]:
                legs = sorted((c for c in contracts if c.expiry == expiry and c.side == side),
                              key=lambda c: abs(c.strike-quote.last))[:3]
                for leg in legs:
                    selected[leg.pk] = leg
    if len(selected) > 5000:
        raise ValueError("Subscription budget exceeded; do not silently drop held contracts.")
    return list(selected.values())


class CandleCollector:
    def __init__(self, *, buffer_quotes: bool = False) -> None:
        self.pending: dict[int, dict] = {}
        self.previous: dict[int, Tick] = {}
        self.last_flush: datetime | None = None
        self.option_sample_at: dict[int, datetime] = {}
        self.discard_logged_at: dict[int, datetime] = {}
        self.buffer_quotes = buffer_quotes
        self.quotes: dict[int, RadarQuote] = {}
        self.quote_pending: dict[int, RadarQuote] = {}
        self.last_quote_flush: datetime | None = None

    def disconnect(self) -> None:
        for item in self.pending.values():
            item["complete"] = False
        self.previous.clear()

    def ingest(self, instrument: RadarInstrument, tick: Tick, *, accepted_at: datetime | None = None) -> None:
        if timezone.is_naive(tick.source_at) or timezone.is_naive(tick.received_at):
            raise ValueError("Feed timestamps must be aware.")
        observed_at = accepted_at or tick.received_at
        if not all(0 <= (observed_at-stamp).total_seconds() <= 15 for stamp in (tick.source_at, tick.received_at)):
            last_warning = self.discard_logged_at.get(instrument.pk)
            if last_warning is None or tick.received_at-last_warning >= timedelta(seconds=60):
                LOGGER.warning("Discarded stale/future tick for %s (source age %.3fs)", instrument.symbol,
                               (observed_at-tick.source_at).total_seconds())
                self.discard_logged_at[instrument.pk] = tick.received_at
            self.pending.pop(instrument.pk, None)
            self.previous.pop(instrument.pk, None)
            return
        if not all(isfinite(x) and x >= 0 for x in (tick.last, tick.bid, tick.ask)) or tick.last <= 0 or min(tick.volume, tick.bid_qty, tick.ask_qty) < 0:
            raise ValueError("Invalid tick values.")
        old = self.previous.get(instrument.pk)
        if old is not None and tick.source_at < old.source_at:
            return
        previous_quote = self.quotes.get(instrument.pk) if self.buffer_quotes else None
        if previous_quote is None:
            previous_quote = RadarQuote.objects.filter(instrument=instrument).first()
        if previous_quote and tick.source_at < previous_quote.source_at:
            return
        self.previous[instrument.pk] = tick
        values = {
            "source_at": tick.source_at, "received_at": tick.received_at,
            "last": tick.last, "bid": tick.bid, "ask": tick.ask,
            "bid_qty": tick.bid_qty, "ask_qty": tick.ask_qty, "volume": tick.volume,
            "oi": tick.oi, "day_open": tick.day_open, "day_high": tick.day_high,
            "day_low": tick.day_low, "previous_close": tick.previous_close,
        }
        # Retain a baseline instead of comparing every premium move with one tick.
        if previous_quote and (tick.source_at-previous_quote.source_at).total_seconds() >= 0:
            if previous_quote.previous_at is None or tick.source_at-previous_quote.previous_at > timedelta(seconds=60):
                values.update(previous_last=previous_quote.last, previous_at=previous_quote.source_at,
                              previous_volume=previous_quote.volume)
        if self.buffer_quotes:
            quote = previous_quote or RadarQuote(instrument=instrument)
            for key, value in values.items():
                setattr(quote, key, value)
            self.quotes[instrument.pk] = quote
            self.quote_pending[instrument.pk] = quote
        else:
            RadarQuote.objects.update_or_create(instrument=instrument, defaults=values)
        if instrument.kind != "EQUITY":
            last_sample = self.option_sample_at.get(instrument.pk)
            if last_sample is None:
                last_sample = RadarOptionPoint.objects.filter(instrument=instrument).order_by("-timestamp").values_list("timestamp", flat=True).first()
            if last_sample is None or tick.source_at-last_sample >= timedelta(seconds=15):
                RadarOptionPoint.objects.get_or_create(
                    instrument=instrument, timestamp=tick.source_at,
                    defaults={
                        "received_at": tick.received_at, "last": tick.last, "bid": tick.bid,
                        "ask": tick.ask, "volume": tick.volume, "oi": tick.oi,
                        "bid_qty": tick.bid_qty, "ask_qty": tick.ask_qty,
                    },
                )
                self.option_sample_at[instrument.pk] = tick.source_at
            elif instrument.pk not in self.option_sample_at:
                self.option_sample_at[instrument.pk] = last_sample
        local = timezone.localtime(tick.source_at)
        if not continuous_timestamp(local, instrument.kind):
            return
        stamp = local.replace(second=0, microsecond=0)
        bucket = self.pending.get(instrument.pk)
        if bucket and bucket["timestamp"] != stamp:
            self._persist(instrument.pk, bucket)
            bucket = None
        gap_ok = old is not None and 0 <= (tick.source_at-old.source_at).total_seconds() <= 30
        volume_ok = old is not None and tick.volume >= old.volume and timezone.localdate(old.source_at) == local.date()
        delta = tick.volume-old.volume if volume_ok else 0
        if bucket is None:
            bucket = {
                "timestamp": stamp, "open": tick.last, "high": tick.last,
                "low": tick.last, "close": tick.last, "volume": delta,
                "complete": bool(gap_ok and volume_ok and old.source_at < stamp),
                "last_at": tick.source_at,
            }
            self.pending[instrument.pk] = bucket
        else:
            bucket.update(high=max(bucket["high"], tick.last), low=min(bucket["low"], tick.last),
                          close=tick.last, volume=bucket["volume"]+delta,
                          complete=bucket["complete"] and gap_ok and volume_ok, last_at=tick.source_at)

    def _persist(self, instrument_id: int, bucket: dict) -> None:
        values = {key: value for key, value in bucket.items() if key != "last_at"}
        validate_candle(Candle(values["timestamp"], values["open"], values["high"], values["low"], values["close"], values["volume"]))
        _, created = RadarBar.objects.get_or_create(instrument_id=instrument_id, timestamp=values.pop("timestamp"), defaults=values)
        if not created:
            LOGGER.warning("Retained immutable existing radar bar for instrument %s", instrument_id)

    def flush(self, now: datetime) -> None:
        if self.quote_pending and (self.last_quote_flush is None or now-self.last_quote_flush >= timedelta(milliseconds=250)):
            RadarQuote.objects.bulk_create(
                list(self.quote_pending.values()), update_conflicts=True,
                unique_fields=["instrument"],
                update_fields=[
                    "source_at", "received_at", "last", "bid", "ask", "bid_qty", "ask_qty",
                    "volume", "oi", "day_open", "day_high", "day_low", "previous_close",
                    "previous_last", "previous_at", "previous_volume",
                ],
            )
            self.quote_pending.clear()
            self.last_quote_flush = now
        for instrument_id, bucket in list(self.pending.items()):
            end = bucket["timestamp"]+timedelta(minutes=1)
            if now >= end:
                if end-bucket["last_at"] > timedelta(seconds=30):
                    bucket["complete"] = False
                self._persist(instrument_id, bucket)
                del self.pending[instrument_id]


@transaction.atomic
def acquire_worker(now: datetime, owner: str | None = None, name: str = "paper-radar") -> str:
    identity = owner or uuid4().hex
    row, _ = RadarWorker.objects.get_or_create(name=name)
    row = RadarWorker.objects.select_for_update().get(pk=row.pk)
    if row.owner and row.owner != identity and row.lease_until > now:
        raise ValueError("Another paper radar worker holds the lease.")
    row.owner, row.lease_until, row.heartbeat_at = identity, now+timedelta(seconds=90), now
    row.save()
    return identity


def heartbeat(owner: str, state: str, detail: str, now: datetime, count: int = 0, name: str = "paper-radar") -> None:
    changed = RadarWorker.objects.filter(name=name, owner=owner, lease_until__gte=now).update(
        state=state, detail=detail, heartbeat_at=now,
        lease_until=now+timedelta(seconds=90), subscriptions=count,
    )
    if not changed:
        raise ValueError("Paper radar worker lease lost.")
