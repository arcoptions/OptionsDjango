"""Read-only, source-aware analytics for the stock-centric paper workspace."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
from math import isfinite
from statistics import mean

from django.utils import timezone
from django.core.cache import cache
from django.db.models import Count, Min, Max, Q, Sum

from .capital_pnl import estimate_option_charges
from .models import (
    AppSetting, IndexOISnapshot, RadarAlert, RadarBar, RadarInstrument, RadarOpportunity,
    RadarOptionPoint, RadarQuote, RadarWatch, RadarWorker, RadarWorkspace,
)
from .radar import (
    Candle, HOLDING, Policy, S, STRATEGIES, VERSION, WATCHING, atr,
    executable_entry_quote, exit_bid_available, five_minute_bars, fresh,
    news_context, option_accelerating, quote_rejection,
    option_priority,
)
from .radar_sessions import CAS_EFFECTIVE, continuous_close, continuous_timestamp, session_minutes


DEFAULTS = {
    "view": "all", "symbol": "", "q": "", "sort": "symbol", "strategy": "",
    "state": "", "interval": "5", "contract": "", "overlays": "vwap,levels,strategy,markers",
    "period": "1D", "chart_date": "", "layout": "desk",
}
INTERVALS = {"1", "2", "3", "5", "10", "15", "25", "30", "60", "75", "125", "240", "D", "W", "M"}
PERIOD_DAYS = {"1D": 1, "5D": 7, "1M": 31, "3M": 93}
SORTS = {"symbol", "change", "rvol", "distance"}
VIEWS = {"all", "watchlist", "radar", "positions"}
OVERLAYS = {"vwap", "levels", "strategy", "markers"}
STRATEGY_RULES = {
    "S1": [
        "Complete prior session; failed break of its low (CE) or high (PE) by 0.05-0.30 ATR.",
        "Reclaim close at least 0.05 ATR inside; subsequent break within two bars, without stop-side invalidation.",
    ],
    "S2": [
        "Six-bar compression range no wider than 1.5 ATR.",
        "Confirmation close beyond range by 0.05 ATR, with at least 1.5x previous 20-bar average volume.",
    ],
    "S3": [
        "Directional 20-bar mean and preceding trend; pullback within 0.20 ATR of mean and close holding it.",
        "Next close breaks pullback extreme by 0.05 ATR without breaching its adverse extreme.",
    ],
}


def preferences(values: dict) -> dict:
    output = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in values:
            output[key] = str(values[key]).strip()
    if output["view"] not in VIEWS or output["sort"] not in SORTS:
        raise ValueError("Invalid workspace view or sort.")
    if output["strategy"] and output["strategy"] not in STRATEGIES:
        raise ValueError("Unknown strategy filter.")
    if output["state"] and output["state"] not in S.values:
        raise ValueError("Unknown opportunity state.")
    if output["interval"] not in INTERVALS:
        raise ValueError("Unknown chart interval.")
    if output["period"] not in PERIOD_DAYS:
        raise ValueError("Unknown chart history range.")
    if output["layout"] not in {"desk", "focus"}:
        raise ValueError("Unknown workspace layout.")
    if output["chart_date"]:
        try:
            date.fromisoformat(output["chart_date"])
        except ValueError as error:
            raise ValueError("Chart date must be YYYY-MM-DD.") from error
    if len(output["symbol"]) > 40 or len(output["q"]) > 80:
        raise ValueError("Stock selection or search is too long.")
    if output["contract"] and (not output["contract"].isdigit() or len(output["contract"]) > 15):
        raise ValueError("Invalid contract selection.")
    flags = output["overlays"].split(",") if output["overlays"] else []
    if set(flags)-OVERLAYS:
        raise ValueError("Unknown chart overlay.")
    output["symbol"] = output["symbol"].upper()
    output["overlays"] = ",".join(sorted(set(flags)))
    return output


def session_start(day) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(9, 15)))


def percent(value: float | None, reference: float | None) -> float | None:
    return (value/reference-1)*100 if value is not None and reference is not None and reference > 0 else None


def metric(label: str, value: float | None, unit: str, reason: str, at=None) -> dict:
    return {
        "label": label, "value": value if value is not None and isfinite(value) else None,
        "unit": unit, "reason": reason, "at": at.isoformat() if at else None,
    }


def as_candle(row: RadarBar) -> Candle:
    return Candle(row.timestamp, row.open, row.high, row.low, row.close, row.volume)


def complete_prefix(rows: list[RadarBar], day, count: int) -> bool:
    start = session_start(day)
    return len(rows) == count and all(
        row.complete and row.timestamp == start+timedelta(minutes=i)
        for i, row in enumerate(rows)
    )


def aggregate_chart(rows: list[RadarBar], interval: int, kind="EQUITY") -> list[dict]:
    buckets = defaultdict(list)
    for row in rows:
        stamp = timezone.localtime(row.timestamp)
        if not row.complete or not continuous_timestamp(stamp, kind):
            continue
        minutes = (stamp.hour*60+stamp.minute-555)//interval*interval
        start = session_start(stamp.date())+timedelta(minutes=minutes)
        buckets[start].append(row)
    result = []
    for start, group in sorted(buckets.items()):
        group.sort(key=lambda r: r.timestamp)
        if len(group) != interval or any(row.timestamp != start+timedelta(minutes=i) for i, row in enumerate(group)):
            continue
        result.append({
            "time": start.isoformat(), "open": group[0].open,
            "high": max(r.high for r in group), "low": min(r.low for r in group),
            "close": group[-1].close, "volume": sum(r.volume for r in group),
        })
    return result


def indicator_chart(rows: list[RadarBar], interval: int, kind="EQUITY") -> list[dict]:
    """Wilder RSI(14), simple-mean seed, and SMA(9) of valid RSI values."""
    bars = aggregate_chart(rows, interval, kind)
    previous = None
    gains, losses, values = [], [], []
    average_gain = average_loss = None
    for bar in bars:
        bar["rsi"] = bar["rsi_ma"] = None
        stamp = datetime.fromisoformat(bar["time"])
        if previous is None or stamp-previous[0] != timedelta(minutes=interval) or timezone.localdate(stamp) != timezone.localdate(previous[0]):
            gains, losses, values = [], [], []
            average_gain = average_loss = None
        else:
            change = bar["close"]-previous[1]
            gain, loss = max(change, 0), max(-change, 0)
            if average_gain is None:
                gains.append(gain)
                losses.append(loss)
                if len(gains) == 14:
                    average_gain, average_loss = mean(gains), mean(losses)
            else:
                average_gain = (average_gain*13+gain)/14
                average_loss = (average_loss*13+loss)/14
            if average_gain is not None and average_loss is not None:
                value = (
                    50.0 if average_gain == average_loss == 0 else
                    100.0 if average_loss == 0 else
                    100-100/(1+average_gain/average_loss)
                )
                bar["rsi"] = value
                values.append(value)
                if len(values) >= 9:
                    bar["rsi_ma"] = mean(values[-9:])
        previous = (stamp, bar["close"])
    return bars


def historical_chart(rows: list[RadarBar], interval: str, kind: str) -> list[dict]:
    if interval.isdigit():
        return indicator_chart(rows, int(interval), kind)
    sessions = defaultdict(list)
    for row in rows:
        if continuous_timestamp(row.timestamp, kind):
            sessions[timezone.localdate(row.timestamp)].append(row)
    daily = []
    for day, group in sorted(sessions.items()):
        group.sort(key=lambda r: r.timestamp)
        if complete_prefix(group, day, session_minutes(day, kind)):
            daily.append({"time": session_start(day).isoformat(), "open": group[0].open,
                          "high": max(r.high for r in group), "low": min(r.low for r in group),
                          "close": group[-1].close, "volume": sum(r.volume for r in group),
                          "rsi": None, "rsi_ma": None})
    if interval == "D":
        return daily
    groups = defaultdict(list)
    for bar in daily:
        day = datetime.fromisoformat(bar["time"]).date()
        key = day-timedelta(days=day.weekday()) if interval == "W" else day.replace(day=1)
        groups[key].append(bar)
    return [{"time": session_start(day).isoformat(), "open": group[0]["open"],
             "high": max(b["high"] for b in group), "low": min(b["low"] for b in group),
             "close": group[-1]["close"], "volume": sum(b["volume"] for b in group),
             "rsi": None, "rsi_ma": None, "observed_sessions": len(group)}
            for day, group in sorted(groups.items())]


@dataclass
class StockData:
    instrument: RadarInstrument
    quote: RadarQuote | None
    rows: list[RadarBar]
    now: datetime
    review: bool = False

    def day_rows(self, day) -> list[RadarBar]:
        return [r for r in self.rows if timezone.localdate(r.timestamp) == day and continuous_timestamp(r.timestamp, self.instrument.kind)]

    def previous(self) -> list[RadarBar]:
        days = sorted({timezone.localdate(r.timestamp) for r in self.rows if timezone.localdate(r.timestamp) < timezone.localdate(self.now)})
        if not days:
            return []
        if (timezone.localdate(self.now)-days[-1]).days > 4:
            return []
        prior = self.day_rows(days[-1])
        return prior if complete_prefix(prior, days[-1], session_minutes(days[-1], self.instrument.kind)) else []

    def metrics(self) -> dict:
        today = timezone.localdate(self.now)
        current = self.day_rows(today)
        prior = self.previous()
        quote_today = self.quote is not None and timezone.localdate(self.quote.source_at) == today and self.quote.source_at <= self.now and self.quote.received_at <= self.now
        last = self.quote.last if quote_today else None
        prior_close = prior[-1].close if prior else None
        opening = self.quote.day_open if quote_today and self.quote.day_open else None
        if opening is None and current and current[0].timestamp == session_start(today) and current[0].complete:
            opening = current[0].open
        elapsed = max(0, min(session_minutes(today, self.instrument.kind), int((self.now-session_start(today)).total_seconds()//60)))
        # A partial minute is never treated as completed volume.
        prefix = complete_prefix(current, today, elapsed) and elapsed > 0
        if self.review:
            last = current[-1].close if prefix else None
        weighted = sum((r.high+r.low+r.close)/3*r.volume for r in current)
        current_volume = sum(r.volume for r in current) if prefix else None
        vwap = weighted/current_volume if current_volume else None
        days = sorted({timezone.localdate(r.timestamp) for r in self.rows if timezone.localdate(r.timestamp) < today})[-20:]
        comparisons = []
        for day in days:
            matched = [r for r in self.day_rows(day) if r.timestamp < session_start(day)+timedelta(minutes=elapsed)]
            if elapsed > 0 and complete_prefix(matched, day, elapsed):
                comparisons.append(sum(r.volume for r in matched))
        baseline = mean(comparisons) if len(comparisons) >= 5 else None
        rvol = current_volume/baseline if current_volume is not None and baseline and baseline > 0 else None
        valid_current = [r for r in current if r.complete]
        context = five_minute_bars([as_candle(r) for r in prior+valid_current])
        contiguous_context = []
        for bar in reversed(context):
            if contiguous_context:
                following = contiguous_context[-1]
                same_day = timezone.localdate(following.timestamp) == timezone.localdate(bar.timestamp)
                if same_day and following.timestamp-bar.timestamp != timedelta(minutes=5):
                    break
            contiguous_context.append(bar)
            if len(contiguous_context) == 15:
                break
        contiguous_context.reverse()
        atr_value = atr(contiguous_context) if len(contiguous_context) == 15 else None
        high = self.quote.day_high if quote_today and self.quote.day_high else None
        low = self.quote.day_low if quote_today and self.quote.day_low else None
        if prefix and current:
            if high is None:
                high = max(r.high for r in current)
                if quote_today:
                    high = max(high, last)
            if low is None:
                low = min(r.low for r in current)
                if quote_today:
                    low = min(low, last)
        at = current[-1].timestamp+timedelta(minutes=1) if self.review and prefix else self.quote.source_at if self.quote else None
        metrics = {
            "last": metric("Recorded CTS close" if self.review else "LTP", last, "INR",
                           "Final complete continuous-session candle close; review only, not an executable quote." if self.review else "Latest stored current-session trade; freshness shown separately.", at),
            "change": metric("From prior CTS close", last-prior_close if last is not None and prior_close else None, "INR", "LTP minus complete prior continuous-session close, not the auction close.", at),
            "change_pct": metric("From prior CTS close", percent(last, prior_close), "%", "Requires every date-specific continuous minute of the most recent prior session. This is not the official auction-close return.", at),
            "open": metric("Day open", opening, "INR", "Dhan session open or observed complete 09:15 minute.", at),
            "gap": metric("Opening gap", percent(opening, prior_close), "%", "Day open versus complete prior-session close.", at),
            "high": metric("Day high", high, "INR", "Broker session field or complete observed session prefix.", at),
            "low": metric("Day low", low, "INR", "Broker session field or complete observed session prefix.", at),
            "volume": metric("Completed volume", current_volume, "units", "Requires every completed minute from 09:15; no partial-minute volume.", self.now),
            "rvol": metric("Same-time relative volume", rvol, "x", f"Current completed volume / mean identical window in {len(comparisons)} complete baseline sessions; minimum 5.", self.now),
            "vwap": metric("VWAP", vwap, "INR", "Session-open complete minute typical-price x volume / volume; not tick VWAP.", self.now),
            "vwap_distance": metric("Distance from VWAP", percent(last, vwap), "%", "Current-session LTP relative to session-open minute VWAP.", at),
            "atr": metric("5-minute ATR(14)", atr_value, "INR", "14 arithmetic true ranges from 15 complete five-minute bars, matching radar strategy ATR.", context[-1].timestamp+timedelta(minutes=5) if context else None),
            "range_atr": metric("Session range / ATR", (high-low)/atr_value if high is not None and low is not None and atr_value else None, "x", "Session high-low divided by current five-minute ATR.", self.now),
            "previous_high": metric("Previous high", max(r.high for r in prior) if prior else None, "INR", "Complete most recent observed prior session.", prior[-1].timestamp if prior else None),
            "previous_low": metric("Previous low", min(r.low for r in prior) if prior else None, "INR", "Complete most recent observed prior session.", prior[-1].timestamp if prior else None),
            "previous_close": metric("Previous CTS close", prior_close, "INR", "Final close of the complete prior continuous session, not the official closing-auction price.", prior[-1].timestamp if prior else None),
            "sector_strength": metric("Sector-relative strength", None, "pp", "Unavailable: no verified stock-sector/sector benchmark source is configured."),
        }
        return {"metrics": metrics, "previous_session": timezone.localdate(prior[-1].timestamp).isoformat() if prior else None, "baseline_sessions": len(comparisons), "coverage": {
            "observed_minutes": len(current), "complete_minutes": len(valid_current), "expected_minutes": elapsed,
            "prefix_complete": prefix, "as_of": self.now.isoformat(),
        }}


def stock_rows(instrument_ids: list[int], now: datetime) -> dict[int, list[RadarBar]]:
    start = session_start(timezone.localdate(now)-timedelta(days=40))
    result = defaultdict(list)
    for row in RadarBar.objects.filter(
        instrument_id__in=instrument_ids, timestamp__gte=start, timestamp__lte=now-timedelta(minutes=1),
    ).order_by("timestamp"):
        result[row.instrument_id].append(row)
    return result


def table_metrics(instruments: list[RadarInstrument], quotes: dict, now: datetime, cached=False) -> dict:
    """Aggregate same-clock prefixes in SQL, not millions of Python bar objects."""
    today = timezone.localdate(now)
    elapsed = max(0, min(session_minutes(today), int((now-session_start(today)).total_seconds()//60)))
    summaries = []
    prefixes = defaultdict(dict)
    # One indexed aggregate per day, independent of the number of stocks.
    # SQLite date/time casts invoke Python per row and stall the live feed.
    key = "radar-display-prefix-v2:"+sha256(
        f"{today}:{elapsed}:{','.join(str(i.pk) for i in instruments)}".encode()
    ).hexdigest()
    saved = cache.get(key) if cached else None
    for offset in ([] if saved is not None else range(40, -1, -1)):
        day = today-timedelta(days=offset)
        start = session_start(day)
        prefix = Q(timestamp__lt=start+timedelta(minutes=elapsed))
        rows = RadarBar.objects.filter(
            instrument__in=instruments, timestamp__gte=start,
            timestamp__lt=start+timedelta(minutes=session_minutes(day)), timestamp__lte=now-timedelta(minutes=1),
        ).values("instrument_id").annotate(
            count=Count("id"), first=Min("timestamp"), last=Max("timestamp"),
            complete_count=Count("id", filter=Q(complete=True)),
            prefix_count=Count("id", filter=prefix),
            prefix_complete=Count("id", filter=prefix & Q(complete=True)),
            prefix_volume=Sum("volume", filter=prefix),
        ).order_by()
        for row in rows:
            row["day"] = day
            summaries.append(row)
            if elapsed and row["prefix_count"] == row["prefix_complete"] == elapsed:
                prefixes[row["instrument_id"]][day] = row["prefix_volume"]
    if saved is not None:
        summaries, prefixes, closes, recorded_closes = saved
    prior = {}
    for row in summaries:
        if row["day"] < today:
            prior[row["instrument_id"]] = row
    valid_prior = {
        key: value for key, value in prior.items()
        if value["count"] == value["complete_count"] == session_minutes(value["day"]) and 0 < (today-value["day"]).days <= 4
        and value["first"] == session_start(value["day"])
        and value["last"] == session_start(value["day"])+timedelta(minutes=session_minutes(value["day"])-1)
    }
    if saved is None:
        closes = {(row.instrument_id, timezone.localdate(row.timestamp)): row.close for row in RadarBar.objects.filter(
            instrument_id__in=valid_prior, timestamp__in=[r["last"] for r in valid_prior.values()],
        )}
        current_rows = {r["instrument_id"]: r for r in summaries if r["day"] == today
                        and r["count"] == r["complete_count"] == session_minutes(today)
                        and r["first"] == session_start(today)
                        and r["last"] == session_start(today)+timedelta(minutes=session_minutes(today)-1)}
        recorded_closes = {r.instrument_id: r.close for r in RadarBar.objects.filter(
            instrument_id__in=current_rows, timestamp=session_start(today)+timedelta(minutes=session_minutes(today)-1),
        )}
        if cached:
            cache.set(key, (summaries, prefixes, closes, recorded_closes), timeout=60)
    result = {}
    for instrument in instruments:
        quote = quotes.get(instrument.pk)
        quote_today = quote and timezone.localdate(quote.source_at) == today and quote.source_at <= now and quote.received_at <= now
        prior_row = valid_prior.get(instrument.pk)
        close = closes.get((instrument.pk, prior_row["day"])) if prior_row else None
        samples = prefixes[instrument.pk]
        baseline = [volume for day, volume in samples.items() if day < today][-20:]
        average = mean(baseline) if len(baseline) >= 5 else None
        current = samples.get(today)
        result[instrument.symbol] = {
            "change_pct": percent(quote.last, close) if quote_today else None,
            "recorded_close": recorded_closes.get(instrument.pk),
            "recorded_change_pct": percent(recorded_closes.get(instrument.pk), close),
            "rvol": current/average if current is not None and average else None,
            "rvol_reason": ("Missing/incomplete current minute prefix." if current is None else
                            "Fewer than five complete same-time baseline sessions." if len(baseline) < 5 else
                            "Baseline volume is zero." if not average else "Available: complete same-time prefixes."),
        }
    return result


def quote_payload(quote: RadarQuote | None, now: datetime) -> dict:
    return {
        "last": quote.last if quote else None, "bid": quote.bid if quote else None,
        "ask": quote.ask if quote else None, "bid_qty": quote.bid_qty if quote else None,
        "ask_qty": quote.ask_qty if quote else None, "volume": quote.volume if quote else None,
        "oi": quote.oi if quote else None, "fresh": fresh(quote, now, Policy()),
        "source_at": quote.source_at.isoformat() if quote else None,
        "received_at": quote.received_at.isoformat() if quote else None,
        "age_seconds": round((now-quote.source_at).total_seconds(), 1) if quote else None,
    }


def opportunity_payload(item: RadarOpportunity, now: datetime) -> dict:
    policy = Policy(**item.policy)
    quote = RadarQuote.objects.select_related("instrument").filter(instrument_id=item.contract_id).first() if item.contract_id else None
    eligible = item.state == S.ELIGIBLE and now < item.valid_until
    blocker = ""
    if eligible:
        try:
            executable_entry_quote(item, now)
        except ValueError as error:
            blocker, eligible = str(error), False
    elif item.state == S.ELIGIBLE:
        blocker = "Entry validity window expired."
    entry = item.entry_price if item.entry_price is not None else item.entry_limit
    deployed = entry*item.quantity if entry is not None else None
    emergency = entry*(1-policy.premium_stop_fraction) if entry is not None else None
    fees = estimate_option_charges(entry, emergency, item.quantity, timezone.localdate(now)) if entry is not None and item.quantity else None
    planned_risk = (entry-emergency)*item.quantity+fees if fees is not None else None
    unexpired = item.contract_id is not None and item.contract.expiry is not None and item.contract.expiry >= timezone.localdate(now)
    can_mark = item.state in HOLDING and unexpired and exit_bid_available(quote, now, policy) and quote is not None and quote.bid_qty >= item.quantity
    unrealised = None
    if can_mark:
        unrealised = (quote.bid-item.entry_price)*item.quantity-estimate_option_charges(item.entry_price, quote.bid, item.quantity, timezone.localdate(item.opened_at))
    return {
        "id": item.pk, "strategy": item.strategy, "name": STRATEGIES[item.strategy],
        "version": item.version, "side": item.side, "state": item.state,
        "display_state": "EXPIRED / awaiting worker" if item.state in WATCHING and now >= item.valid_until else item.get_state_display(),
        "reason": item.reason, "setup_at": item.setup_at.isoformat(),
        "trigger_at": item.trigger_at.isoformat() if item.trigger_at else None,
        "valid_until": item.valid_until.isoformat(), "trigger": item.trigger, "stop": item.stop,
        "atr": item.atr, "contract_id": item.contract_id, "quantity": item.quantity,
        "lot_size": item.contract.lot_size if item.contract_id else None,
        "entry_limit": item.entry_limit, "entry_price": item.entry_price,
        "opened_at": item.opened_at.isoformat() if item.opened_at else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at else None,
        "exit_price": item.exit_price, "net_pnl": item.net_pnl, "charges": item.charges,
        "exit_reason": item.exit_reason, "high_water": item.high_water,
        "can_acknowledge": eligible, "action_block": blocker,
        "quote": quote_payload(quote, now), "premium_deployed": deployed,
        "planned_stop_risk": planned_risk, "emergency_stop": emergency,
        "trail_active": item.state in HOLDING and item.high_water is not None and item.entry_price is not None and item.high_water >= item.entry_price*(1+policy.trail_activation),
        "trail_level": item.high_water*(1-policy.trail_distance) if item.high_water is not None else None,
        "net_unrealised": unrealised, "executable_bid": quote.bid if can_mark else None, "policy": item.policy,
        "decisions": [{"at": d.created_at.isoformat(), "state": d.state, "reason": d.reason} for d in item.decisions.all()[:8]],
        "checks": [
            {"label": "Underlying setup", "status": "recorded", "detail": item.evidence.get("source", item.reason)},
            {"label": "Confirmation", "status": "recorded" if item.trigger_at else "pending",
             "detail": "Confirmation event recorded." if item.trigger_at else "No confirmation event recorded."},
            {"label": "Option / risk / validity", "status": "passed" if eligible else "not_currently_eligible",
             "detail": blocker or (item.reason if not eligible else "Rechecked against current quote and shared risk rules.")},
        ],
    }


def option_candidates(instrument: RadarInstrument, spot: float | None, opportunities: list[RadarOpportunity], now: datetime) -> list[dict]:
    contracts = list(RadarInstrument.objects.filter(
        symbol=instrument.symbol, kind="OPTION", active=True, expiry__gt=timezone.localdate(now)+timedelta(days=2),
    ).order_by("expiry", "strike", "side"))
    expiries = sorted({c.expiry for c in contracts})[:2]
    selected = {}
    for expiry in (expiries if spot is not None and spot > 0 else []):
        for side in ["CE", "PE"]:
            legs = sorted((c for c in contracts if c.expiry == expiry and c.side == side), key=lambda c: abs(c.strike-spot) if spot is not None else c.strike)[:3]
            selected.update({c.pk: c for c in legs})
    for item in opportunities:
        if item.contract_id and item.contract.expiry is not None and item.contract.expiry >= timezone.localdate(now):
            selected[item.contract_id] = item.contract
    quotes = {q.instrument_id: q for q in RadarQuote.objects.filter(instrument_id__in=selected)}
    for quote in quotes.values():
        quote.instrument = selected[quote.instrument_id]
    priorities = {quote.instrument_id: index+1 for index, quote in enumerate(sorted(
        quotes.values(), key=lambda q: option_priority(q, spot or q.instrument.strike, now, Policy()),
    ))}
    result = []
    for contract in selected.values():
        quote = quotes.get(contract.pk)
        rejection = quote_rejection(quote, now, Policy())
        if not contract.active or contract.expiry <= timezone.localdate(now)+timedelta(days=2):
            rejection = "Inactive contract or within two-calendar-day expiry buffer."
        momentum = None
        if quote and quote.previous_last and quote.previous_at and 15 <= (quote.source_at-quote.previous_at).total_seconds() <= 120:
            momentum = percent(quote.last, quote.previous_last)
        if not rejection and quote and not option_accelerating(quote):
            rejection = "Option acceleration not confirmed."
        spread = (quote.ask-quote.bid)/((quote.ask+quote.bid)/2)*100 if quote and quote.bid > 0 and quote.ask >= quote.bid else None
        result.append({
            "id": contract.pk, "security_id": contract.security_id, "expiry": str(contract.expiry),
            "execution_rank": priorities.get(contract.pk),
            "strike": contract.strike, "side": contract.side, "lot_size": contract.lot_size,
            "distance_pct": percent(contract.strike, spot),
            "label": f"{contract.expiry} {contract.strike:g} {contract.side}",
            "quote": quote_payload(quote, now), "spread_pct": spread,
            "premium_momentum_pct": momentum, "momentum_seconds": (quote.source_at-quote.previous_at).total_seconds() if quote and quote.previous_at else None,
            "rejection": rejection, "status": "QUOTE_CHECKS_PASSED_NOT_ENTRY_AUTHORIZATION" if not rejection else "UNAVAILABLE_OR_REJECTED",
            "greeks": {name: None for name in ["iv", "delta", "gamma", "theta", "vega"]},
            "greeks_reason": "Unavailable: this stream does not provide verified IV/Greeks. No estimated values are substituted.",
        })
    return sorted(result, key=lambda row: (row["expiry"], abs(row["strike"]-(spot or row["strike"])), row["side"]))


def option_history(contract_id: int | None, now: datetime) -> list[dict]:
    if contract_id is None:
        return []
    points = RadarOptionPoint.objects.filter(
        instrument_id=contract_id, timestamp__gte=session_start(timezone.localdate(now)),
        timestamp__lte=now, received_at__lte=now,
    ).order_by("timestamp")
    return [{
        "time": p.timestamp.isoformat(), "last": p.last, "bid": p.bid,
        "ask": p.ask, "volume": p.volume, "oi": p.oi,
    } for p in points]


def market_context(now: datetime) -> dict:
    output = {}
    for symbol in ["NIFTY", "BANKNIFTY"]:
        latest = IndexOISnapshot.objects.filter(
            underlying=symbol, created_at__lte=now, underlying_price__gt=0,
        ).order_by("-created_at").first()
        prior = IndexOISnapshot.objects.filter(
            Q(created_at__date__lt=CAS_EFFECTIVE, created_at__time__gte=time(15, 30))
            | Q(created_at__date__gte=CAS_EFFECTIVE, created_at__time__gte=time(15, 35)),
            underlying=symbol, created_at__date__lt=timezone.localdate(now),
            created_at__time__lte=time(15, 40),
            underlying_price__gt=0,
        ).order_by("-created_at").first()
        is_fresh = latest is not None and 0 <= (now-latest.created_at).total_seconds() <= 15
        valid_prior = prior is not None and timedelta(0) < now-prior.created_at <= timedelta(days=4)
        change = percent(float(latest.underlying_price), float(prior.underlying_price)) if is_fresh and valid_prior else None
        output[symbol] = {
            "last": float(latest.underlying_price) if latest else None,
            "fresh": is_fresh, "source_at": latest.created_at.isoformat() if latest else None,
            "change_pct": change, "reason": "Current stored snapshot versus prior post-cash-session snapshot (15:35-15:40 after CAS introduction; previously 15:30-15:40); capture timestamps, not exchange quote timestamps. 15s capture freshness and <=4 calendar-day prior-close bound.",
            "prior_at": prior.created_at.isoformat() if prior else None,
            "pcr": latest.pcr if latest else None,
            "regime": latest.regime if latest else "",
            "support": float(latest.support_strike) if latest and latest.support_strike is not None else None,
            "resistance": float(latest.resistance_strike) if latest and latest.resistance_strike is not None else None,
        }
    return output


def portfolio(now: datetime) -> dict:
    rows = list(RadarOpportunity.objects.select_related("contract").filter(state__in=HOLDING+[S.EXITED]))
    groups = defaultdict(list)
    exposure, risk = 0.0, 0.0
    for item in rows:
        if item.state in HOLDING and item.entry_price is not None:
            exposure += item.entry_price*item.quantity
            policy = Policy(**item.policy)
            stop = item.entry_price*(1-policy.premium_stop_fraction)
            risk += (item.entry_price-stop)*item.quantity+estimate_option_charges(item.entry_price, stop, item.quantity, timezone.localdate(item.opened_at))
        elif item.state == S.EXITED and item.net_pnl is not None:
            groups[(item.strategy, item.version)].append(item.net_pnl)
    return {
        "open_count": sum(r.state in HOLDING for r in rows), "premium_exposure": exposure,
        "planned_stop_risk": risk, "net_realised": sum(sum(values) for values in groups.values()),
        "warning": "Shared PAPER portfolio across staff; planned stops are not guaranteed loss limits.",
        "performance": [{
            "strategy": strategy, "version": version, "closed": len(values),
            "win_rate": sum(v > 0 for v in values)/len(values)*100,
            "expectancy": mean(values), "net_pnl": sum(values),
            "mode": "PAPER_DISPLAYED_QUOTES_NOT_BROKER_FILLS",
        } for (strategy, version), values in sorted(groups.items())],
    }


def live_workspace_payload(user, query: dict) -> dict:
    from .action_workspace import action_list_context
    from .cas_views import cas_dashboard_state
    from .live_dashboard import engine_snapshot, today_summary, trade_rows
    from .models import AppSetting
    from .radar_assistant import worker_health

    result = workspace_payload(user, query, timezone.now(), cached_metrics=True)
    health = worker_health()
    result["assistant"]["health"] = health
    result["health"]["feed"] = health["workers"]["paper-radar"]["state"]
    result["health"]["delivery"] = health["workers"]["paper-alerts"]["state"]
    result["action_queue"] = action_list_context()
    engine = engine_snapshot()
    today = cache.get_or_set("radar:nifty-today", lambda: today_summary(trade_rows()), 10)
    result["nifty_engine"] = {
        "enabled": str(
            AppSetting.objects.filter(key="nifty_live_enabled").values_list("value", flat=True).first() or ""
        ).strip().lower() in {"1", "true", "yes", "on"},
        "state": engine["state"],
        "dry_run": engine["dry_run"],
        "stale": engine["stale"],
        "age_seconds": engine["age_seconds"],
        "at": engine["at"],
        "trades_today": engine["trades_today"],
        "max_trades_per_day": engine["max_trades_per_day"],
        "realized_r": engine["realized_r"],
        "daily_loss_limit_r": engine["daily_loss_limit_r"],
        "net_pnl_today": today["net_pnl"],
        "observed_today": today["observed"],
        "position": engine["position"],
        "error": engine["error"],
    }
    cas = cas_dashboard_state()
    result["cas"] = {
        "enabled": cas["enabled"],
        "stale": cas["stale"],
        "event_count": cas["event_count"],
        "nowcast_count": cas["nowcast_count"],
        "healthy": bool(cas["enabled"] and not cas["stale"] and cas["event_count"] and cas["nowcast_count"]),
    }
    return result


def review_session(now: datetime) -> dict:
    today = timezone.localdate(now)
    if continuous_timestamp(now):
        return {"open": True, "day": today.isoformat(), "reference_at": now,
                "label": "Continuous session open", "review": False}
    latest = None
    for offset in range(8):
        day = today-timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        start = session_start(day)
        end = start+timedelta(minutes=session_minutes(day))
        observed_until = min(now-timedelta(minutes=1), end)
        if observed_until <= start:
            continue
        latest = (
            RadarBar.objects.filter(
                instrument__kind="EQUITY",
                timestamp__gte=start,
                timestamp__lt=observed_until,
            )
            .order_by("-timestamp")
            .values_list("timestamp", flat=True)
            .first()
        )
        if latest:
            break
    day = timezone.localdate(latest) if latest else today
    reference = session_start(day)+timedelta(minutes=session_minutes(day)) if latest else now
    return {"open": False, "day": day.isoformat() if latest else None,
            "reference_at": reference, "review": True,
            "label": f"Market closed - reviewing recorded session {day}" if latest else "Market closed - no recent recorded session"}


def workspace_payload(user, query: dict, now: datetime, cached_metrics=False) -> dict:
    from .radar_assistant import assistant_payload
    from .radar_research import research_report
    from .models import RadarPriceAlert, RadarNotificationRoute
    from .radar_price_alerts import alert_payload
    saved = RadarWorkspace.objects.filter(user=user).first()
    state = preferences({**(saved.preferences if saved else {}), **query})
    instruments = list(RadarInstrument.objects.filter(kind="EQUITY", active=True).order_by("symbol"))
    watches = {w.instrument_id: w for w in RadarWatch.objects.filter(user=user).select_related("instrument")}
    quotes = {q.instrument_id: q for q in RadarQuote.objects.filter(instrument__in=instruments)}
    session = review_session(now)
    display_at = session["reference_at"] if session["review"] else now
    # Bounded daily aggregates avoid loading historical bars per stock.
    summaries = table_metrics(instruments, quotes, display_at, cached=cached_metrics)
    event_query = RadarOpportunity.objects.select_related("contract").prefetch_related("decisions").filter(created_at__lte=now)
    active_filter = Q(state__in=HOLDING) | Q(state__in=WATCHING, valid_until__gt=now)
    opportunities = list(event_query.filter(active_filter))
    opportunities += list(event_query.exclude(active_filter).order_by("-created_at", "-pk")[:1000])
    opportunities.sort(key=lambda item: (item.created_at, item.pk), reverse=True)
    by_symbol = defaultdict(list)
    for item in opportunities:
        by_symbol[item.symbol].append(item)
    table = []
    for instrument in instruments:
        data = summaries[instrument.symbol]
        events = by_symbol[instrument.symbol]
        relevant = [e for e in events if e.state in HOLDING or (e.state in WATCHING and e.valid_until > now)]
        filtered = [e for e in events if (not state["strategy"] or e.strategy == state["strategy"]) and (not state["state"] or e.state == state["state"])]
        visible = (
            state["view"] == "all" or (state["view"] == "watchlist" and instrument.pk in watches)
            or (state["view"] == "radar" and any(e in relevant for e in filtered))
            or (state["view"] == "positions" and any(e.state in HOLDING for e in filtered))
        )
        if state["q"] and state["q"].casefold() not in f"{instrument.symbol} {instrument.name}".casefold():
            visible = False
        if (state["strategy"] or state["state"]) and not filtered:
            visible = False
        latest = next((e for e in filtered if e in relevant), filtered[0] if filtered else None)
        quote = quotes.get(instrument.pk)
        last = quote.last if quote else None
        distance = percent(latest.trigger, last) if latest and last else None
        table.append({
            "id": instrument.pk, "symbol": instrument.symbol, "name": instrument.name,
            "watched": instrument.pk in watches, "visible": visible, "quote": quote_payload(quotes.get(instrument.pk), now),
            "change_pct": data["recorded_change_pct"] if session["review"] else data["change_pct"], "rvol": data["rvol"],
            "display_price": data["recorded_close"] if session["review"] else last,
            "display_status": "SESSION REVIEW" if session["review"] and data["recorded_close"] is not None else
                              "INCOMPLETE SESSION" if session["review"] else "FRESH" if fresh(quote, now, Policy()) else "STALE / MISSING",
            "rvol_reason": data.get("rvol_reason", "Coverage unavailable."),
            "strategy": latest.strategy if latest else "", "state": latest.state if latest else "",
            "bias": ("Bullish" if latest.side == "CE" else "Bearish") if latest and latest in relevant else "Unconfirmed",
            "distance_pct": distance, "notes": watches[instrument.pk].notes if instrument.pk in watches else "",
            "filter_events": [{"strategy": e.strategy, "state": e.state, "current": e in relevant,
                               "holding": e.state in HOLDING, "trigger": e.trigger, "side": e.side} for e in events],
        })
    visible_rows = [r for r in table if r["visible"]]
    sort_key = {"change": "change_pct", "rvol": "rvol", "distance": "distance_pct"}.get(state["sort"])
    if sort_key:
        visible_rows.sort(key=lambda r: (r[sort_key] is None, -(r[sort_key] or 0) if state["sort"] != "distance" else abs(r[sort_key] or 0), r["symbol"]))
    symbols = {i.symbol: i for i in instruments}
    visible_symbols = {row["symbol"] for row in visible_rows}
    selected = (symbols.get(state["symbol"]) if state["symbol"] in visible_symbols else None) or (
        symbols[visible_rows[0]["symbol"]] if visible_rows else None)
    state["symbol"] = selected.symbol if selected else ""
    worker = RadarWorker.objects.filter(name="paper-radar").first()
    delivery = RadarWorker.objects.filter(name="paper-alerts").first()
    history_worker = RadarWorker.objects.filter(name="radar-history").first()
    news_worker = RadarWorker.objects.filter(name="radar-news").first()
    market = market_context(now)
    result = {
        "state": state, "as_of": now.isoformat(), "rows": visible_rows, "all_rows": table, "universe": len(instruments),
        "watch_count": len(watches), "fresh_count": sum(r["quote"]["fresh"] for r in table),
        "market": market, "portfolio": portfolio(now),
        "session": {key: value for key, value in session.items() if key != "reference_at"},
        "health": {
            "feed": worker.state if worker and 0 <= (now-worker.heartbeat_at).total_seconds() <= 30 and worker.lease_until > now else "OFFLINE / STALE",
            "feed_detail": worker.detail if worker else "No worker has run.",
            "delivery": delivery.state if delivery and 0 <= (now-delivery.heartbeat_at).total_seconds() <= 30 and delivery.lease_until > now else "OFFLINE / NOT ENABLED",
            "pending_alerts": RadarAlert.objects.filter(sent_at__isnull=True, cancelled_at__isnull=True).count(),
            "public_news": {
                "alive": bool(news_worker and news_worker.lease_until > now and 0 <= (now-news_worker.heartbeat_at).total_seconds() <= 90),
                "last_checked_at": news_worker.heartbeat_at.isoformat() if news_worker else None,
                "coverage": "Latest public BeatTheStreet text/captions only; not complete history, image OCR or verified catalysts.",
            },
            "monitor": (
                json.loads(monitor_state.value)
                if (monitor_state := AppSetting.objects.filter(key="radar_monitor_state").first())
                else None
            ),
            "history": {
                "state": ("INTERRUPTED / STALE" if history_worker.state == "DOWNLOADING" and history_worker.lease_until <= now else history_worker.state) if history_worker else "NOT STARTED",
                "detail": history_worker.detail if history_worker else "Run radar_backfill explicitly to reuse cached minute data and download missing history.",
                "at": history_worker.heartbeat_at.isoformat() if history_worker else None,
            },
            "errors": list(RadarAlert.objects.filter(sent_at__isnull=True, cancelled_at__isnull=True).exclude(last_error="").values("id", "last_error")[:5]),
        },
        "selected": None,
        "assistant": assistant_payload(now, user),
        "research": research_report(now),
        "price_alerts": [alert_payload(a, now) for a in RadarPriceAlert.objects.filter(user=user).select_related("instrument")[:100]],
        "private_alerts_connected": RadarNotificationRoute.objects.filter(user=user).exists(),
    }
    if selected is None:
        return result
    history = stock_rows([selected.pk], now)
    data = StockData(selected, None if session["review"] else quotes.get(selected.pk), history[selected.pk],
                     display_at, review=session["review"]).metrics()
    change = data["metrics"]["change_pct"]["value"]
    aligned_prior = market["NIFTY"]["prior_at"] is not None and timezone.localdate(datetime.fromisoformat(market["NIFTY"]["prior_at"])).isoformat() == data["previous_session"]
    relative = change-market["NIFTY"]["change_pct"] if aligned_prior and change is not None and market["NIFTY"]["change_pct"] is not None and fresh(quotes.get(selected.pk), now, Policy()) else None
    data["metrics"]["market_strength"] = metric(
        "Relative to Nifty (reference proxy)", relative, "pp",
        f"Stock return from prior continuous close minus Nifty post-session snapshot return; not matched official-close relative strength. Fresh current stock/benchmark and matching prior-session dates required. "
        f"{market['NIFTY']['reason']} Nifty current capture: {market['NIFTY']['source_at']}; prior capture: {market['NIFTY']['prior_at']}. "
        f"Stock prior session: {data['previous_session']}. No directional probability implied.", now,
    )
    stock_events = by_symbol[selected.symbol]
    # Never let archived events crowd out an open position or currently valid setup.
    events = [e for e in stock_events if e.state in HOLDING or (e.state in WATCHING and e.valid_until > now)]
    events += [e for e in stock_events if e not in events][:30]
    reference_price = data["metrics"]["last"]["value"]
    if reference_price is None:
        quote = quotes.get(selected.pk)
        if quote and 0 <= (now-quote.source_at).total_seconds() <= 4*86400 and quote.received_at <= now and quote.last > 0:
            reference_price = quote.last
    candidates = option_candidates(selected, reference_price, events, now)
    candidate_ids = {str(c["id"]) for c in candidates}
    chosen = state["contract"] if state["contract"] in candidate_ids else str(candidates[0]["id"]) if candidates else ""
    state["contract"] = chosen
    watch = watches.get(selected.pk)
    latest_day = timezone.localdate(now)
    chart_end = date.fromisoformat(state["chart_date"]) if state["chart_date"] else date.fromisoformat(session["day"]) if session["day"] else latest_day
    if chart_end > latest_day:
        raise ValueError("Chart date cannot be in the future.")
    chart_start = chart_end-timedelta(days=PERIOD_DAYS[state["period"]]-1)
    chart_rows = list(RadarBar.objects.filter(
        instrument_id__in=[selected.pk]+([int(chosen)] if chosen else []),
        timestamp__gte=session_start(chart_start), timestamp__lt=session_start(chart_end+timedelta(days=1)),
        timestamp__lte=now-timedelta(minutes=1),
    ).order_by("timestamp"))
    equity_rows = [r for r in chart_rows if r.instrument_id == selected.pk]
    option_rows = [r for r in chart_rows if r.instrument_id != selected.pk]
    chart = historical_chart(equity_rows, state["interval"], "EQUITY")
    vwap_points, cumulative_volume, cumulative_value = [], 0, 0.0
    prefix_count, prefix_day, prefix_valid = 0, None, True
    for row in equity_rows:
        day = timezone.localdate(row.timestamp)
        if not continuous_timestamp(row.timestamp):
            continue
        if day != prefix_day:
            prefix_count, cumulative_volume, cumulative_value, prefix_valid = 0, 0, 0.0, True
            prefix_day = day
        if row.timestamp != session_start(day)+timedelta(minutes=prefix_count) or not row.complete:
            prefix_valid = False
        prefix_count += 1
        if not prefix_valid:
            continue
        cumulative_volume += row.volume
        cumulative_value += (row.high+row.low+row.close)/3*row.volume
        vwap_points.append({"time": row.timestamp.isoformat(), "value": cumulative_value/cumulative_volume if cumulative_volume else None})
    strategies = []
    for code, name in STRATEGIES.items():
        latest = next((e for e in events if e.strategy == code), None)
        strategies.append({"code": code, "name": name, "version": latest.version if latest else VERSION,
                           "rules": STRATEGY_RULES[code],
                           "opportunity": opportunity_payload(latest, now) if latest else None})
    news = news_context(selected.symbol, now)
    result["selected"] = {
        "id": selected.pk, "symbol": selected.symbol, "name": selected.name,
        "quote": quote_payload(quotes.get(selected.pk), now), **data,
        "watched": watch is not None, "notes": watch.notes if watch else "",
        "watched_level": watch.watched_level if watch else None,
        "chart": chart, "vwap_points": vwap_points,
        "option_chart": historical_chart(option_rows, state["interval"], "OPTION"),
        "chart_range": {"from": chart_start.isoformat(), "to": chart_end.isoformat(),
                        "current_reference": chart_end.isoformat() == session["day"],
                        "first_available": chart[0]["time"] if chart else None,
                        "last_available": chart[-1]["time"] if chart else None,
                        "note": "Recorded bars only; missing intervals are not filled. Intraday candles require every interval minute; incomplete closing buckets are omitted. Daily bars require complete sessions. Weekly/monthly bars aggregate recorded complete sessions, not certified calendar coverage. Intraday RSI resets each session or gap; daily/weekly/monthly RSI is unavailable. Metrics, quotes and strategies remain current, not as-of historical reconstructions. Current reference lines are hidden when reviewing past dates. Table volume/reference inputs may be cached for up to 60 seconds; freshness and action eligibility are checked live."},
        "option_chart_source": sorted({r.source for r in option_rows}),
        "indicator_definition": "Session-only Wilder RSI(14): mean of first 14 close changes, then 1/14 smoothing; flat seed = 50. RSI MA is SMA(9) of RSI, not price. Needs 15/23 consecutive complete candles respectively; resets at gaps/session boundaries. Candle volume is observed cumulative-volume change, not buyer/seller flow. Analytical only; strategy rules unchanged.",
        "chart_source": sorted({r.source for r in equity_rows}),
        "news": news, "strategies": strategies, "candidates": candidates,
        "option_reference": {"price": reference_price, "basis": "Recorded session close" if session["review"] and data["metrics"]["last"]["value"] is not None else "Latest stored underlying quote; check source time",
                             "at": data["metrics"]["last"]["at"] or quote_payload(quotes.get(selected.pk), now)["source_at"],
                             "note": "Strike proximity is analytics only; fresh executable bid/ask and strategy/risk gates are still required."},
        "decision": {
            "status": "SESSION REVIEW - NO LIVE ENTRY" if session["review"] else
                      "PAPER ENTRY AVAILABLE" if any(s["opportunity"] and s["opportunity"]["can_acknowledge"] for s in strategies) else
                      "NO EXECUTABLE PAPER ENTRY",
            "reason": "The market is outside continuous trading hours. Recorded metrics are preparation context, not current signals." if session["review"] else
                      "Only an independently recorded setup with current option, validity and risk checks can authorize a paper acknowledgement.",
            "next_action": "Review the last session, save watch levels, then wait for fresh market data and a recorded confirmation." if session["review"] else
                           "Inspect the recorded setup and its blockers. Do not infer an entry from RSI or a rising chart alone.",
            "coverage": f"{data['coverage']['complete_minutes']}/{data['coverage']['expected_minutes']} complete minutes; RVOL baselines {data['baseline_sessions']}.",
            "setup_evidence": "No recorded setup is not proof that every strategy was evaluated and failed.",
        },
        "option_history": option_history(int(chosen) if chosen else None, now),
        "opportunities": [opportunity_payload(item, now) for item in events],
        "oi_context": {
            "full_chain_pcr": None,
            "reason": "Unavailable: subscribed near-money candidates are not a full option chain; no partial PCR is presented as chain PCR.",
        },
    }
    return result
