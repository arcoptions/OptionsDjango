"""Deterministic experimental stock setups and a paper-only opportunity lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from hashlib import sha256
from html import escape
from itertools import groupby
import json
import logging
from math import floor, isfinite
import re

from django.db import transaction
from django.utils import timezone

from .capital_pnl import estimate_option_charges
from .models import (
    ChatMessage, RadarAlert, RadarBar, RadarDecision, RadarInstrument, RadarOpportunity, RadarQuote, RadarWorker,
)


LOGGER = logging.getLogger(__name__)
VERSION = "experimental-v3"
STRATEGIES = {
    "S1": "Failed-break / reclaim",
    "S2": "Compression breakout",
    "S3": "Trend pullback",
}
S = RadarOpportunity.State
WATCHING = [S.ARMED, S.TRIGGERED, S.REJECTED, S.ELIGIBLE]
HOLDING = [S.TAKEN, S.EXIT_PENDING]


@dataclass(frozen=True)
class Policy:
    capital: float = 100_000
    risk_fraction: float = .01
    debit_fraction: float = .15
    total_debit_fraction: float = .30
    daily_loss_fraction: float = .02
    max_positions: int = 2
    max_spread: float = .02
    quote_age_seconds: int = 15
    max_extension_atr: float = .25
    premium_stop_fraction: float = .25
    trail_activation: float = .15
    trail_distance: float = .10


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Setup:
    strategy: str
    side: str
    timestamp: datetime
    trigger: float
    stop: float
    atr: float
    triggered: bool
    reason: str


def policy_hash(policy: Policy) -> str:
    return sha256(json.dumps(asdict(policy), sort_keys=True).encode()).hexdigest()[:12]


def validate_candle(bar: Candle) -> None:
    if (
        timezone.is_naive(bar.timestamp)
        or bar.timestamp.second or bar.timestamp.microsecond
        or not all(isfinite(x) and x > 0 for x in (bar.open, bar.high, bar.low, bar.close))
        or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)
        or bar.volume < 0
    ):
        raise ValueError("Invalid minute OHLC, volume or timestamp.")


def five_minute_bars(rows: list[Candle]) -> list[Candle]:
    from .radar_sessions import continuous_timestamp
    buckets: dict[datetime, list[Candle]] = {}
    for row in rows:
        validate_candle(row)
        stamp = timezone.localtime(row.timestamp)
        if not continuous_timestamp(stamp):
            continue
        start = stamp.replace(minute=stamp.minute - stamp.minute % 5)
        buckets.setdefault(start, []).append(row)
    result = []
    for start, values in sorted(buckets.items()):
        values.sort(key=lambda bar: bar.timestamp)
        if [bar.timestamp for bar in values] != [start + timedelta(minutes=i) for i in range(5)]:
            continue
        result.append(Candle(start, values[0].open, max(b.high for b in values),
                             min(b.low for b in values), values[-1].close,
                             sum(b.volume for b in values)))
    return result


def contiguous(bars: list[Candle]) -> bool:
    return all(b.timestamp - a.timestamp == timedelta(minutes=5) for a, b in zip(bars, bars[1:]))


def atr(bars: list[Candle]) -> float:
    values = [
        max(b.high-b.low, abs(b.high-a.close), abs(b.low-a.close))
        for a, b in zip(bars[-15:], bars[-14:])
    ]
    return sum(values) / len(values) if len(bars) >= 15 else 0


def detect_setups(
    bars: list[Candle], previous: list[Candle], now: datetime,
) -> list[Setup]:
    """Use only completed, contiguous five-minute bars in the current session."""
    from .radar_sessions import session_minutes
    bars = [b for b in bars if b.timestamp + timedelta(minutes=5) <= now]
    prior_usable = (
        bool(previous) and len(previous) == session_minutes(timezone.localdate(previous[0].timestamp))//5
        and timezone.localtime(previous[0].timestamp).time() == time(9, 15) and contiguous(previous)
        and timedelta(0) < bars[0].timestamp-previous[-1].timestamp <= timedelta(days=4)
    ) if bars else False
    context = (previous if prior_usable else [])+bars
    if len(bars) < 3 or len(context) < 22 or not contiguous(bars[-22:]):
        return []
    latest = bars[-1]
    if now - (latest.timestamp + timedelta(minutes=5)) > timedelta(seconds=30):
        return []
    local = timezone.localtime(now)
    if not time(9, 30) <= local.time() <= time(14, 30):
        return []
    result = []
    current_atr = atr(context)
    if current_atr <= 0:
        return []
    for direction, side in ((1, "CE"), (-1, "PE")):
        def high(bar: Candle) -> float:
            return bar.high if direction == 1 else -bar.low

        def low(bar: Candle) -> float:
            return bar.low if direction == 1 else -bar.high

        def close(bar: Candle) -> float:
            return direction * bar.close

        # A previous-session level requires the complete date-specific continuous session.
        if prior_usable:
            level = min(b.low for b in previous) if direction == 1 else -max(b.high for b in previous)
            for offset in range(1, 4):
                reclaim = bars[-offset]
                frozen = atr(context[:len(context)-offset+1])
                penetration = level - low(reclaim)
                if frozen <= 0 or not .05*frozen <= penetration <= .30*frozen:
                    continue
                if close(reclaim) < level + .05*frozen:
                    continue
                following = bars[len(bars)-offset+1:]
                if any(low(b) <= low(reclaim) for b in following):
                    continue
                first_hit = next((b for b in following if high(b) > high(reclaim)), None)
                if first_hit is not None and first_hit is not latest:
                    continue
                result.append(Setup("S1", side, reclaim.timestamp, direction*high(reclaim),
                                    direction*(low(reclaim)-.05*frozen), frozen,
                                    first_hit is latest, "Previous-session reclaim; subsequent break required."))
        base = bars[-7:-1]
        base_atr = atr(context[:-1])
        upper, lower = max(high(b) for b in base), min(low(b) for b in base)
        baseline_volume = sum(b.volume for b in context[-21:-1]) / 20
        if len(base) == 6 and base_atr > 0 and upper-lower <= 1.5*base_atr and baseline_volume > 0:
            breakout = close(latest) > upper + .05*base_atr and latest.volume >= 1.5*baseline_volume
            near = upper - .25*base_atr <= close(latest) <= upper + .05*base_atr
            if breakout or near:
                result.append(Setup("S2", side, base[-1].timestamp, direction*(upper+.05*base_atr),
                                    direction*(lower-.05*base_atr), base_atr, breakout,
                                    "Six-bar compression; close beyond range and 1.5x volume required."))
        prior = bars[-2]
        mean20 = sum(close(b) for b in context[-21:-1]) / 20
        mean20_before = sum(close(b) for b in context[-22:-2]) / 20
        trend = mean20 > mean20_before and close(bars[-3]) > mean20
        pullback = mean20-.20*current_atr <= low(prior) <= mean20+.20*current_atr and close(prior) > mean20
        if trend and pullback and low(latest) > low(prior):
            triggered = close(latest) > high(prior) + .05*current_atr
            result.append(Setup("S3", side, prior.timestamp, direction*(high(prior)+.05*current_atr),
                                direction*(low(prior)-.05*current_atr), current_atr, triggered,
                                "Rising 20-bar mean, pullback hold and resumption close required."))
    return result


def news_context(symbol: str, now: datetime) -> list[dict]:
    pattern = re.compile(r"(?<![A-Z0-9])" + re.escape(symbol) + r"(?![A-Z0-9])", re.I)
    messages = ChatMessage.objects.filter(
        source_category="NEWS", created_at__gte=now-timedelta(hours=6), created_at__lte=now,
        telegram_message_at__gte=now-timedelta(hours=6), telegram_message_at__lte=now,
    ).order_by("-created_at")[:300]
    seen, output = set(), []
    for message in messages:
        text = " ".join(message.raw_text.split())
        normalized = text.casefold()
        if not pattern.search(text) or normalized in seen:
            continue
        seen.add(normalized)
        link = ""
        if message.source_name == "BeatTheStreet News" and message.telegram_message_id:
            link = f"https://t.me/BeatTheStreetnews/{message.telegram_message_id}"
        output.append({
            "message_id": message.pk, "source": message.source_name, "text": text[:1000],
            "published_at": message.telegram_message_at.isoformat(),
            "received_at": message.created_at.isoformat(),
            "match": "exact_symbol_only_not_sentiment",
            "url": link,
        })
        if len(output) == 3:
            break
    return output


def transition(item: RadarOpportunity, state: str, reason: str, now: datetime) -> None:
    item.state, item.reason = state, reason
    item.save()
    last = item.decisions.first()
    if last is None or last.state != state or last.reason != reason:
        RadarDecision.objects.create(
            opportunity=item, state=state, reason=reason, created_at=now,
            context={
                "contract_id": item.contract_id, "quantity": item.quantity,
                "entry_limit": item.entry_limit, "entry_price": item.entry_price,
                "exit_price": item.exit_price, "evidence": item.evidence,
                "entry_valid_until": item.valid_until.isoformat(),
                "policy": item.policy, "underlying_trigger": item.trigger,
                "underlying_stop": item.stop, "atr": item.atr,
            },
        )
    RadarAlert.objects.filter(
        opportunity=item, sent_at__isnull=True, cancelled_at__isnull=True,
    ).exclude(state=state).update(cancelled_at=now)
    states = [S.ELIGIBLE, S.TAKEN, S.EXIT_PENDING, S.EXITED, S.INVALIDATED]
    if item.evidence.get("assistant"):
        states = [S.TAKEN, S.EXIT_PENDING, S.EXITED]
        if item.strategy == "S1":
            states += [S.ARMED, S.TRIGGERED, S.ELIGIBLE, S.INVALIDATED]
    if state in states:
        contract = item.contract
        label = f"{contract.expiry} {contract.strike:g} {contract.side}" if contract else "no contract"
        quote_evidence = item.evidence.get("exit_quote", item.evidence.get("fill", {}))
        quote_at = quote_evidence.get("source_at", item.evidence.get("option_quote_at", "not priced"))
        text = (
            f"<b>PAPER ONLY | {escape(item.symbol)} | {item.strategy} {item.side} | {state}</b>\n"
            f"{escape(STRATEGIES[item.strategy])} ({item.version})\n"
            f"Contract: {escape(label)}\n"
            f"Underlying trigger {item.trigger:.2f}; invalidation {item.stop:.2f}\n"
            f"Entry limit: {item.entry_limit}; quantity: {item.quantity}\n"
            f"Quote source time: {escape(str(quote_at))}; refresh before acknowledgement.\n"
            f"Valid until: {timezone.localtime(item.valid_until):%H:%M:%S %Z}\n"
            f"{escape(reason)}\nOpportunity #{item.pk}. Acknowledge in Radar; no broker order is placed."
        )
        alert, created = RadarAlert.objects.get_or_create(
            opportunity=item, state=state,
            defaults={"text": text, "created_at": now, "next_attempt_at": now},
        )
        if not created and alert.sent_at is None and alert.cancelled_at is not None:
            alert.text, alert.cancelled_at, alert.next_attempt_at = text, None, now
            alert.save()


def fresh(quote: RadarQuote | None, now: datetime, policy: Policy) -> bool:
    return quote is not None and all(
        0 <= (now-stamp).total_seconds() <= policy.quote_age_seconds
        for stamp in (quote.source_at, quote.received_at)
    ) and isfinite(quote.last) and quote.last > 0


def quote_rejection(quote: RadarQuote | None, now: datetime, policy: Policy) -> str:
    if not fresh(quote, now, policy):
        return "Missing, future-dated or stale quote."
    if quote is None:
        raise ValueError("Quote freshness invariant violated.")
    if not all(isfinite(x) and x > 0 for x in (quote.bid, quote.ask)):
        return "Missing executable bid/ask."
    if quote.ask < quote.bid or (quote.ask-quote.bid)/((quote.ask+quote.bid)/2) > policy.max_spread:
        return "Crossed market or spread exceeds policy."
    if not quote.bid_qty or not quote.ask_qty or not quote.volume:
        return "No displayed quantity or traded activity."
    return ""


def option_accelerating(quote: RadarQuote) -> bool:
    elapsed = (quote.source_at-quote.previous_at).total_seconds() if quote.previous_at else 0
    return (
        15 <= elapsed <= 120 and quote.previous_last is not None and quote.previous_last > 0
        and quote.last >= quote.previous_last*1.005
        and quote.previous_volume is not None and quote.volume > quote.previous_volume
    )


def option_priority(quote: RadarQuote, spot: float, now: datetime, policy: Policy) -> tuple:
    rejection = quote_rejection(quote, now, policy)
    spread = (quote.ask-quote.bid)/((quote.ask+quote.bid)/2) if not rejection else float("inf")
    lot = quote.instrument.lot_size
    depth_lots = min(quote.bid_qty, quote.ask_qty)//lot if lot > 0 else 0
    return (bool(rejection), not option_accelerating(quote), spread, -depth_lots,
            quote.instrument.expiry, abs(quote.instrument.strike-spot), quote.instrument_id)


def exit_bid_available(quote: RadarQuote | None, now: datetime, policy: Policy) -> bool:
    # A wide spread can block entry, but must not hide an executable adverse bid.
    return (
        fresh(quote, now, policy) and quote is not None
        and isfinite(quote.bid) and quote.bid > 0 and quote.bid_qty > 0
        and isfinite(quote.ask) and (quote.ask == 0 or quote.ask >= quote.bid)
    )


def sizing(item: RadarOpportunity, quote: RadarQuote, policy: Policy, now: datetime) -> tuple[int, str]:
    if quote.instrument.expiry is None or quote.instrument.expiry <= timezone.localdate(now)+timedelta(days=2):
        return 0, "Expiry buffer: no entries within two calendar days of stock-option expiry."
    holdings = RadarOpportunity.objects.filter(state__in=HOLDING)
    if holdings.filter(symbol=item.symbol).exclude(pk=item.pk).exists():
        return 0, "Another strategy already holds this stock."
    if holdings.count() >= policy.max_positions:
        return 0, "Maximum open paper positions reached."
    closed = RadarOpportunity.objects.filter(state=S.EXITED, closed_at__date=timezone.localdate(now))
    pnl = sum(closed.values_list("net_pnl", flat=True))
    if pnl <= -policy.capital*policy.daily_loss_fraction:
        return 0, "Realised daily-loss gate reached."
    deployed = sum(row.entry_price*row.quantity for row in holdings)
    lifetime_pnl = sum(RadarOpportunity.objects.filter(state=S.EXITED).values_list("net_pnl", flat=True))
    cash = policy.capital+lifetime_pnl-deployed
    budget = min(policy.capital*policy.debit_fraction, policy.capital*policy.total_debit_fraction-deployed, cash)
    lot = quote.instrument.lot_size
    if lot <= 0:
        return 0, "Invalid contract lot size."
    max_lots = min(floor(budget/(quote.ask*lot)), quote.ask_qty//lot, quote.bid_qty//lot)
    for lots in range(max_lots, 0, -1):
        quantity = lots*lot
        fees = estimate_option_charges(quote.ask, quote.ask*(1-policy.premium_stop_fraction), quantity, timezone.localdate(now))
        if quote.ask*policy.premium_stop_fraction*quantity+fees <= policy.capital*policy.risk_fraction:
            return quantity, ""
    return 0, "No whole lot fits premium, displayed depth and planned-risk limits."


def check_entry(item: RadarOpportunity, now: datetime) -> str:
    policy = Policy(**item.policy)
    if not time(9, 30) <= timezone.localtime(now).time() <= time(14, 30):
        return "Outside intraday entry window."
    if now >= item.valid_until:
        return "Entry validity window expired."
    equity = RadarQuote.objects.filter(instrument__symbol=item.symbol, instrument__kind="EQUITY", instrument__active=True).first()
    if not fresh(equity, now, policy):
        return "Underlying quote missing or stale."
    if equity is None:
        raise ValueError("Underlying freshness invariant violated.")
    if now-equity.instrument.refreshed_at > timedelta(hours=24):
        return "Instrument registry is stale; refresh before new paper entries."
    direction = 1 if item.side == "CE" else -1
    if direction*(equity.last-item.stop) <= 0:
        return "Underlying invalidated."
    extension = direction*(equity.last-item.trigger)
    if extension < 0 or extension > policy.max_extension_atr*item.atr:
        return "Underlying below trigger or extended; do not chase."
    return ""


@transaction.atomic
def evaluate_entry(item: RadarOpportunity, now: datetime) -> None:
    reason = check_entry(item, now)
    if reason:
        transition(item, S.REJECTED, reason, now)
        return
    options = RadarQuote.objects.select_related("instrument").filter(
        instrument__symbol=item.symbol, instrument__kind="OPTION", instrument__active=True,
        instrument__side=item.side, instrument__expiry__gt=timezone.localdate(now)+timedelta(days=2),
    )
    if item.contract_id:
        options = options.filter(instrument_id=item.contract_id)
    equity = RadarQuote.objects.get(instrument__symbol=item.symbol, instrument__kind="EQUITY", instrument__active=True)
    ranked = sorted(options, key=lambda q: option_priority(q, equity.last, now, Policy(**item.policy)))
    reasons = []
    for quote in ranked:
        rejection = quote_rejection(quote, now, Policy(**item.policy))
        if rejection:
            reasons.append(rejection)
            continue
        quantity, rejection = sizing(item, quote, Policy(**item.policy), now)
        if rejection:
            reasons.append(rejection)
            continue
        # Acceleration is an observed premium move, not inferred dealer gamma.
        if not option_accelerating(quote):
            reasons.append("Awaiting observed option acceleration and increasing cumulative volume.")
            continue
        item.contract, item.quantity, item.entry_limit = quote.instrument, quantity, quote.ask
        item.evidence = {**item.evidence, "option_quote_at": quote.source_at.isoformat(),
                         "bid": quote.bid, "ask": quote.ask, "acceleration": "observed_not_gamma"}
        transition(item, S.ELIGIBLE, "Paper entry eligible; fresh ask, depth and risk checks passed.", now)
        return
    transition(item, S.REJECTED, "; ".join(dict.fromkeys(reasons)) or "Awaiting option subscriptions / executable quotes.", now)


@transaction.atomic
def acknowledge(item_id: int, action: str, now: datetime) -> RadarOpportunity:
    lock, _ = RadarWorker.objects.get_or_create(name="paper-risk")
    RadarWorker.objects.select_for_update().get(pk=lock.pk)
    item = RadarOpportunity.objects.select_for_update().get(pk=item_id)
    if action not in ["take", "skip"]:
        raise ValueError("Action must be take or skip.")
    if item.state != S.ELIGIBLE:
        raise ValueError("Only a currently eligible opportunity can be acknowledged.")
    if action == "skip":
        transition(item, S.SKIPPED, "User skipped paper opportunity.", now)
        return item
    quote = executable_entry_quote(item, now)
    item.entry_price, item.opened_at, item.high_water = quote.ask, now, quote.bid
    item.evidence = {**item.evidence, "fill": {
        "mode": "PAPER_DISPLAYED_ASK_NOT_GUARANTEED_FILL", "ask": quote.ask,
        "ask_qty": quote.ask_qty, "source_at": quote.source_at.isoformat(), "received_at": quote.received_at.isoformat(),
    }}
    transition(item, S.TAKEN, "Acknowledged PAPER position at displayed ask; no live order.", now)
    return item


def executable_entry_quote(item: RadarOpportunity, now: datetime) -> RadarQuote:
    rejection = check_entry(item, now)
    if rejection:
        raise ValueError(rejection)
    quote = RadarQuote.objects.select_related("instrument").filter(instrument=item.contract).first()
    policy = Policy(**item.policy)
    rejection = quote_rejection(quote, now, policy)
    if rejection:
        raise ValueError(rejection)
    if quote is None:
        raise ValueError("Executable option quote missing.")
    if not option_accelerating(quote):
        raise ValueError("Option acceleration no longer confirmed; do not enter a fading signal.")
    quantity, rejection = sizing(item, quote, policy, now)
    if rejection or item.entry_limit is None or quote.ask > item.entry_limit or quantity < item.quantity:
        raise ValueError(rejection or "Original limit or size no longer executable; refresh opportunity.")
    return quote


@transaction.atomic
def manage_positions(now: datetime) -> None:
    for item in RadarOpportunity.objects.select_for_update().filter(state__in=HOLDING):
        policy = Policy(**item.policy)
        if item.contract.expiry is None or item.contract.expiry < timezone.localdate(now):
            item.exit_reason = "Expired contract; offline paper settlement review required."
            if item.state != S.EXIT_PENDING:
                transition(item, S.EXIT_PENDING, item.exit_reason+" No live quote or exit fill will be assumed.", now)
            else:
                item.save()
            continue
        quote = RadarQuote.objects.filter(instrument=item.contract).first()
        underlying = RadarQuote.objects.filter(instrument__symbol=item.symbol, instrument__kind="EQUITY", instrument__active=True).first()
        stale = not fresh(quote, now, policy) or not fresh(underlying, now, policy)
        if stale:
            RadarAlert.objects.get_or_create(
                opportunity=item, state="DATA_STALE",
                defaults={
                    "created_at": now, "next_attempt_at": now,
                    "text": f"PAPER position #{item.pk} {escape(item.symbol)}: market data stale. "
                            "Stops cannot be evaluated reliably. No exit fill has been assumed.",
                },
            )
        else:
            RadarAlert.objects.filter(
                opportunity=item, state="DATA_STALE", sent_at__isnull=True,
                cancelled_at__isnull=True,
            ).update(cancelled_at=now)
        reason = item.exit_reason
        if timezone.localtime(now).time() >= time(15, 10) or timezone.localdate(now) > timezone.localdate(item.opened_at):
            reason = reason or "Intraday time exit"
        if fresh(underlying, now, policy) and underlying is not None:
            if (1 if item.side == "CE" else -1)*(underlying.last-item.stop) <= 0:
                reason = reason or "Underlying invalidation"
        executable = exit_bid_available(quote, now, policy)
        if executable and quote is not None:
            item.high_water = max(item.high_water, quote.bid)
            if quote.bid <= item.entry_price*(1-policy.premium_stop_fraction):
                reason = reason or "Emergency premium stop"
            if item.high_water >= item.entry_price*(1+policy.trail_activation) and quote.bid <= item.high_water*(1-policy.trail_distance):
                reason = reason or "Premium trailing exit"
        if not reason:
            item.save()
            continue
        item.exit_reason = reason
        if not executable or quote is None or quote.bid_qty < item.quantity:
            if item.state != S.EXIT_PENDING:
                transition(item, S.EXIT_PENDING, "Exit required; awaiting fresh executable bid and full displayed size.", now)
            else:
                item.save()
            continue
        item.exit_price, item.closed_at = quote.bid, now
        item.evidence = {**item.evidence, "exit_quote": {
            "bid": quote.bid, "ask": quote.ask, "bid_qty": quote.bid_qty,
            "source_at": quote.source_at.isoformat(), "received_at": quote.received_at.isoformat(),
            "mode": "PAPER_DISPLAYED_BID_NOT_GUARANTEED_FILL",
        }}
        item.charges = estimate_option_charges(item.entry_price, quote.bid, item.quantity, timezone.localdate(item.opened_at))
        item.net_pnl = round((quote.bid-item.entry_price)*item.quantity-item.charges, 2)
        transition(item, S.EXITED, reason+"; PAPER exit at displayed bid.", now)


def scan(
    now: datetime,
    policy: Policy | None = None,
    *,
    assistant: bool = False,
    progress: Callable[[], None] | None = None,
) -> dict:
    policy = policy or Policy()
    manage_positions(now)
    with transaction.atomic():
        for item in RadarOpportunity.objects.select_for_update().filter(state__in=WATCHING):
            if now >= item.valid_until:
                transition(item, S.EXPIRED, "Unacknowledged opportunity expired.", now)
                continue
            quote = RadarQuote.objects.filter(instrument__symbol=item.symbol, instrument__kind="EQUITY", instrument__active=True).first()
            if fresh(quote, now, Policy(**item.policy)) and quote is not None:
                if (1 if item.side == "CE" else -1)*(quote.last-item.stop) <= 0:
                    transition(item, S.INVALIDATED, "Underlying invalidation before entry.", now)
                    continue
            if item.trigger_at:
                if item.state == S.ELIGIBLE:
                    try:
                        executable_entry_quote(item, now)
                    except ValueError as error:
                        transition(item, S.REJECTED, str(error), now)
                else:
                    evaluate_entry(item, now)
    today = timezone.localdate(now)
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    latest_day = RadarBar.objects.filter(instrument__kind="EQUITY", timestamp__lt=today_start).order_by("-timestamp").values_list("timestamp", flat=True).first()
    previous_day = timezone.localdate(latest_day) if latest_day else None
    history_start = timezone.make_aware(datetime.combine(previous_day, time.min)) if previous_day else today_start
    found = 0
    instruments = {
        instrument.pk: instrument
        for instrument in RadarInstrument.objects.filter(kind="EQUITY", active=True)
    }
    bar_rows = RadarBar.objects.filter(
        instrument_id__in=instruments, complete=True, timestamp__gte=history_start,
        timestamp__lte=now-timedelta(minutes=1),
    ).order_by("instrument_id", "timestamp").iterator(chunk_size=2000)
    for group_index, (instrument_id, instrument_rows) in enumerate(
        groupby(bar_rows, key=lambda row: row.instrument_id)
    ):
        if progress and group_index % 5 == 0:
            progress()
        instrument = instruments[instrument_id]
        rows = list(instrument_rows)
        current = [Candle(r.timestamp, r.open, r.high, r.low, r.close, r.volume) for r in rows if timezone.localdate(r.timestamp) == today]
        previous = [Candle(r.timestamp, r.open, r.high, r.low, r.close, r.volume) for r in rows if timezone.localdate(r.timestamp) != today]
        current_bars, previous_bars = five_minute_bars(current), five_minute_bars(previous)
        for setup in detect_setups(current_bars, previous_bars, now):
            key = f"{instrument.symbol}:{setup.strategy}:{VERSION}:{policy_hash(policy)}:{setup.side}:{setup.timestamp.isoformat()}"
            with transaction.atomic():
                item, created = RadarOpportunity.objects.select_for_update().get_or_create(key=key, defaults={
                    "symbol": instrument.symbol, "strategy": setup.strategy, "version": VERSION,
                    "side": setup.side, "state": S.ARMED, "setup_at": setup.timestamp,
                    "valid_until": setup.timestamp+timedelta(minutes=20),
                    "trigger": setup.trigger, "stop": setup.stop, "atr": setup.atr,
                    "reason": setup.reason, "policy": asdict(policy), "created_at": now,
                    "evidence": {
                        "assistant": assistant,
                        "news": news_context(instrument.symbol, now),
                        "source": "completed_observed_or_imported_bars",
                        "input_sources": sorted({row.source for row in rows}),
                        "confirmation_available_at": (current_bars[-1].timestamp+timedelta(minutes=5)).isoformat(),
                    },
                })
                if created:
                    found += 1
                    transition(item, S.ARMED, setup.reason, now)
                if setup.triggered and item.state == S.ARMED and now < item.valid_until:
                    item.trigger_at = now
                    item.valid_until = now+timedelta(minutes=2)
                    transition(item, S.TRIGGERED, setup.reason, now)
                    evaluate_entry(item, now)
    return {"new_setups": found, "watching": RadarOpportunity.objects.filter(state__in=WATCHING).count()}
