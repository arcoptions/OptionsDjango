"""Rate-limited, resumable minute history for the paper radar, never orders."""

from datetime import date, datetime, time, timedelta
import logging
import json
from hashlib import sha256
from math import isfinite
import time as clock

from django.db import transaction
from django.utils import timezone
import requests

from .models import DownloadJob, RadarBar, RadarHistoryConflict, RadarInstrument, StockEquityCandle
from .radar import Candle, validate_candle
from .services import _token_is_spent, get_dhan_credentials


LOGGER = logging.getLogger(__name__)
URL = "https://api.dhan.co/v2/charts/intraday"
JOB_KIND = "RADAR_1M"


def credentials() -> tuple[str, str]:
    token, client = get_dhan_credentials()
    if not token or not client or _token_is_spent(token):
        raise ValueError("Dhan credentials are missing or expired. Update the existing credential settings before starting live data.")
    return token, client


def parse_history(data: dict, start: date, end: date, now: datetime, kind="EQUITY") -> list[Candle]:
    from .radar_sessions import continuous_timestamp
    fields = ["timestamp", "open", "high", "low", "close", "volume"]
    if not isinstance(data, dict) or any(not isinstance(data.get(key), list) for key in fields):
        raise ValueError("Dhan minute response is missing OHLCV arrays; no download checkpoint saved.")
    size = len(data["timestamp"])
    if any(len(data[key]) != size for key in fields):
        raise ValueError("Dhan minute arrays have different lengths.")
    result, seen = [], set()
    for i, epoch in enumerate(data["timestamp"]):
        try:
            if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or not isfinite(epoch):
                raise ValueError("Invalid timestamp.")
            stamp = datetime.fromtimestamp(epoch, timezone.get_current_timezone())
            volume = float(data["volume"][i])
            if not isfinite(volume) or not volume.is_integer():
                raise ValueError("Invalid volume.")
            bar = Candle(stamp, *(float(data[key][i]) for key in fields[1:5]), int(volume))
            validate_candle(bar)
        except (ValueError, TypeError, OverflowError, OSError) as error:
            raise ValueError("Invalid Dhan minute OHLCV record; response not imported.") from error
        if stamp in seen:
            raise ValueError("Duplicate timestamps in Dhan minute response.")
        seen.add(stamp)
        if not start <= stamp.date() <= end or not continuous_timestamp(stamp, kind):
            continue
        if stamp+timedelta(minutes=1) > now:
            continue
        result.append(bar)
    return sorted(result, key=lambda bar: bar.timestamp)


@transaction.atomic
def store_bars(instrument: RadarInstrument, bars: list[Candle], source: str) -> dict:
    if not bars:
        return {"inserted": 0, "retained": 0, "conflicts": 0}
    existing = {row.timestamp: row for row in RadarBar.objects.filter(
        instrument=instrument, timestamp__gte=bars[0].timestamp, timestamp__lte=bars[-1].timestamp,
    )}
    pending, conflicts = [], 0
    for bar in bars:
        old = existing.get(bar.timestamp)
        if old is not None:
            differs = any(getattr(old, key) != getattr(bar, key) for key in ["open", "high", "low", "close", "volume"]) or not old.complete
            if differs:
                conflicts += 1
                proposed = {key: getattr(bar, key) for key in ["open", "high", "low", "close", "volume"]}
                fingerprint = sha256(json.dumps({"source": source, **proposed}, sort_keys=True).encode()).hexdigest()
                RadarHistoryConflict.objects.get_or_create(
                    bar=old, fingerprint=fingerprint,
                    defaults={"source": source, "proposed": proposed,
                              "original": {**{key: getattr(old, key) for key in ["open", "high", "low", "close", "volume"]},
                                           "complete": old.complete, "source": old.source, "timestamp": old.timestamp.isoformat()}},
                )
        else:
            pending.append(RadarBar(
                instrument=instrument, timestamp=bar.timestamp, open=bar.open, high=bar.high,
                low=bar.low, close=bar.close, volume=bar.volume, complete=True, source=source,
            ))
    # Live observations and their validity flags are immutable, even when REST disagrees.
    RadarBar.objects.bulk_create(pending, ignore_conflicts=True, batch_size=500)
    if conflicts:
        LOGGER.warning("Radar history retained %s conflicting immutable bars for %s / %s", conflicts, instrument.symbol, instrument.security_id)
    return {"inserted": len(pending), "retained": len(bars)-len(pending), "conflicts": conflicts}


def accept_incomplete_repairs(now: datetime, symbols: list[str] | None = None) -> int:
    """Explicit opt-in canonical repair; retain original and proposal audit first."""
    candidates = RadarHistoryConflict.objects.filter(
        accepted_at__isnull=True, source="DHAN_HISTORY_1M", bar__complete=False,
        bar__timestamp__lte=now-timedelta(minutes=1),
    )
    if symbols:
        candidates = candidates.filter(bar__instrument__symbol__in=symbols)
    count = 0
    for candidate in candidates.order_by("-observed_at", "-pk"):
        with transaction.atomic():
            bar = RadarBar.objects.select_for_update().get(pk=candidate.bar_id)
            if bar.complete:
                continue
            proposed = candidate.proposed
            candle = Candle(bar.timestamp, *(proposed[key] for key in ["open", "high", "low", "close", "volume"]))
            validate_candle(candle)
            if not candidate.original or candidate.original.get("complete") is not False:
                raise ValueError("Incomplete repair has no preserved original audit; refusing canonical update.")
            if any(getattr(bar, key) != candidate.original[key] for key in ["open", "high", "low", "close", "volume", "source"]):
                raise ValueError("Canonical bar changed since repair audit; review before accepting.")
            for key, value in proposed.items():
                if key not in ["open", "high", "low", "close", "volume"]:
                    raise ValueError("Unexpected repair field.")
                setattr(bar, key, value)
            bar.complete, bar.source = True, "DHAN_RECONCILED_1M"
            bar.save(update_fields=["open", "high", "low", "close", "volume", "complete", "source"])
            candidate.accepted_at = now
            candidate.save(update_fields=["accepted_at"])
            count += 1
    return count


def reuse_equity_cache(instrument: RadarInstrument, start: date, end: date, now: datetime) -> dict:
    if instrument.kind != "EQUITY":
        return {"inserted": 0, "retained": 0, "conflicts": 0}
    rows = StockEquityCandle.objects.filter(
        symbol=instrument.symbol, interval_minutes=1,
        timestamp__gte=timezone.make_aware(datetime.combine(start, time.min)),
        timestamp__lt=timezone.make_aware(datetime.combine(end+timedelta(days=1), time.min)),
    ).order_by("timestamp")
    data = {key: [] for key in ["timestamp", "open", "high", "low", "close", "volume"]}
    for row in rows:
        data["timestamp"].append(row.timestamp.timestamp())
        for key in ["open", "high", "low", "close", "volume"]:
            value = getattr(row, key)
            data[key].append(float(value) if value is not None else None)
    return store_bars(instrument, parse_history(data, start, end, now), "DHAN_CACHE_1M")


def window_covered(instrument: RadarInstrument, start: date, end: date, now: datetime) -> bool:
    from .radar_sessions import session_minutes
    day, expected_total = start, 0
    while day <= end:
        if day.weekday() < 5:
            opening = timezone.make_aware(datetime.combine(day, time(9, 15)))
            count = max(0, min(session_minutes(day, instrument.kind), int((now-opening).total_seconds()//60)))
            if count:
                covered = RadarBar.objects.filter(
                    instrument=instrument, complete=True, timestamp__gte=opening,
                    timestamp__lt=opening+timedelta(minutes=count),
                ).count()
                if covered != count:
                    return False
                expected_total += count
        day += timedelta(days=1)
    return expected_total > 0


class HistoryClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.next_request = 0.0
        self.requests = 0

    def close(self) -> None:
        self.session.close()

    def fetch(self, instrument: RadarInstrument, start: date, end: date, now: datetime) -> list[Candle]:
        token, client = credentials()
        payload = {
            "securityId": instrument.security_id, "exchangeSegment": instrument.segment,
            "instrument": "EQUITY" if instrument.kind == "EQUITY" else "OPTSTK",
            "interval": "1", "oi": False,
            # Pad beyond session boundaries; Dhan can exclude exact boundary stamps.
            "fromDate": f"{start} 09:00:00", "toDate": f"{end} 15:40:00",
        }
        for attempt in range(4):
            clock.sleep(max(0, self.next_request-clock.monotonic()))
            self.next_request = clock.monotonic()+1.1
            self.requests += 1
            try:
                response = self.session.post(
                    URL, json=payload, headers={"access-token": token, "client-id": client, "Accept": "application/json"},
                    timeout=30,
                )
            except requests.RequestException:
                if attempt == 3:
                    raise ValueError("Dhan history network request failed after four attempts; credentials and response bodies are not logged.") from None
                self.next_request = max(self.next_request, clock.monotonic()+2**(attempt+1))
                continue
            if response.status_code in {401, 403}:
                raise ValueError("Dhan rejected authentication or data entitlement; update credentials/check subscription.")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 3:
                    raise ValueError(f"Dhan history HTTP {response.status_code} after four attempts; retry later.")
                retry_after = response.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2**(attempt+1)
                self.next_request = max(self.next_request, clock.monotonic()+min(30, max(2, wait)))
                continue
            if not response.ok:
                raise ValueError(f"Dhan history HTTP {response.status_code}; request stopped without saving a completed checkpoint.")
            try:
                data = response.json()
            except ValueError:
                raise ValueError("Dhan history returned invalid JSON.") from None
            return parse_history(data, start, end, now, instrument.kind)
        raise ValueError("Dhan history retry invariant violated.")


def download_instrument(instrument: RadarInstrument, start: date, end: date, now: datetime, client: HistoryClient, cache_only: bool = False) -> dict:
    if not instrument.active or (instrument.kind == "OPTION" and (instrument.expiry is None or instrument.expiry < timezone.localdate(now))):
        raise ValueError("Expired/inactive instruments cannot be downloaded by the live radar.")
    totals = reuse_equity_cache(instrument, start, end, now)
    totals.update(requests=0, checkpoints=0, returned=0)
    if cache_only:
        return totals
    cursor = start
    while cursor <= end:
        last = min(cursor+timedelta(days=6), end)
        identity = dict(kind=JOB_KIND, symbol=instrument.symbol, detail=f"{instrument.segment}:{instrument.security_id}", window_from=cursor, window_to=last)
        old = DownloadJob.objects.filter(**identity, status="DONE").first()
        if (old and last < timezone.localdate(now)) or window_covered(instrument, cursor, last, now):
            totals["checkpoints"] += 1
        else:
            count = client.requests
            try:
                bars = client.fetch(instrument, cursor, last, now)
                if not bars:
                    raise ValueError("Dhan returned no complete regular-session candles; not treating this as a completed download.")
                stored = store_bars(instrument, bars, "DHAN_HISTORY_1M")
            except ValueError as error:
                DownloadJob.objects.update_or_create(**identity, defaults={"status": "FAILED", "error": str(error), "rows": 0})
                raise
            for key, value in stored.items():
                totals[key] += value
            totals["requests"] += client.requests-count
            totals["returned"] += len(bars)
            DownloadJob.objects.update_or_create(
                **identity, defaults={"status": "DONE", "rows": len(bars), "error": ""},
            )
        cursor = last+timedelta(days=1)
    return totals
