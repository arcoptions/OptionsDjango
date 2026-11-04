"""Explainable, bounded stock discovery; priority is not a probability."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from django.utils import timezone

from .models import (
    RadarAlert, RadarAssistantSnapshot, RadarBar, RadarCatalyst, RadarHistoryConflict,
    RadarInstrument, RadarOpportunity, RadarPriceAlert, RadarQuote, RadarWorker, TrackedStock, RadarSector,
)
from .radar import Candle, Policy, WATCHING, atr, five_minute_bars, fresh
from .radar_workspace import session_start, table_metrics


RANK_VERSION = "priority-v2"
MAX_PER_SIDE = 5
MOMENTUM_VERSION = "opening-range-momentum-v1"
MOMENTUM_RVOL_FLOOR = 1.2
MOMENTUM_EVIDENCE = {
    "candidate": MOMENTUM_VERSION,
    "status": "BULLISH_WATCH_HYPOTHESIS_NOT_OPTION_EDGE",
    "data": "218 stocks, 10 complete sessions (2026-10-21 to 2026-11-03); five sessions eligible after five-session RVOL warmup.",
    "rules": "Bullish five-minute close beyond the first 15-minute high, above session VWAP, stock return at least the positive F&O breadth proxy, same-time RVOL >= 1.2.",
    "all_bullish": {
        "trades": 194, "days": 4, "expectancy_r": .161,
        "profit_factor": 1.81, "max_drawdown_r": 4.436,
    },
    "held_out_bullish": {
        "trades": 110, "days": 2, "expectancy_r": .102,
        "profit_factor": 1.50, "max_drawdown_r": 3.041,
    },
    "limitations": "Underlying proxy only. Three held-out calendar sessions, with bullish signals on two. The available option history covers only 12 contracts over two dates, so no representative executable option result is claimed. Bearish promotion is disabled because its full-sample expectancy was negative.",
    "report": "research/FNO_RADAR_BACKTEST.md",
}


def opening_range_watch(
    bars: list[Candle], change_pct: float | None, rvol: float | None,
    breadth: float | None,
) -> list[dict]:
    """Return evidence-backed stock watches; this does not authorize an entry."""
    if (
        len(bars) < 4 or change_pct is None or rvol is None or breadth is None
        or rvol < MOMENTUM_RVOL_FLOOR
    ):
        return []
    opening = bars[:3]
    latest = bars[-1]
    opening_high = max(bar.high for bar in opening)
    opening_low = min(bar.low for bar in opening)
    current_atr = atr(bars)
    volume = sum(bar.volume for bar in bars)
    vwap = (
        sum(((bar.high+bar.low+bar.close)/3)*bar.volume for bar in bars)/volume
        if volume else None
    )
    if current_atr <= 0:
        current_atr = opening_high-opening_low
    if current_atr <= 0 or vwap is None:
        return []
    result = []
    # The recorded sample supports the bullish side only. Keep bearish empty
    # until prospective or broader historical evidence supports it.
    for side, direction in (("CE", 1),):
        if direction*breadth <= 0 or direction*change_pct < max(0, direction*breadth):
            continue
        boundary = opening_high if side == "CE" else opening_low
        stop = opening_low if side == "CE" else opening_high
        distance = direction*(boundary-latest.close)/current_atr
        confirmed = direction*(latest.close-boundary) > 0 and direction*(latest.close-vwap) > 0
        forming = -.25 <= distance <= .50 and direction*(latest.close-vwap) > 0
        if not (confirmed or forming):
            continue
        result.append({
            "side": side, "state": "OPENING_RANGE_CONFIRMED" if confirmed else "OPENING_RANGE_FORMING",
            "trigger": latest.close if confirmed else boundary, "stop": stop,
            "atr": current_atr, "vwap": vwap, "distance_atr": distance,
            "version": MOMENTUM_VERSION,
        })
    return result


def worker_health(now: datetime | None = None) -> dict:
    from .radar_sessions import continuous_timestamp
    workers = {w.name: w for w in RadarWorker.objects.all()}
    equities = list(RadarQuote.objects.filter(instrument__kind="EQUITY", instrument__active=True))
    count = RadarInstrument.objects.filter(kind="EQUITY", active=True).count()
    now = now or timezone.now()
    output = {}
    for name in ["paper-radar", "radar-evaluator", "paper-alerts"]:
        worker = workers.get(name)
        heartbeat_age = (now-worker.heartbeat_at).total_seconds() if worker else 0
        alive = bool(worker and worker.lease_until > now and -30 <= heartbeat_age <= 30)
        output[name] = {"alive": alive, "state": worker.state if alive else "OFFLINE / STALE"}
    fresh_count = sum(fresh(q, now, Policy()) for q in equities)
    market_open = continuous_timestamp(now)
    return {
        "workers": output, "universe": count, "fresh": fresh_count,
        "market_open_weekday_only": market_open,
        "healthy": output["paper-radar"]["alive"] and output["radar-evaluator"]["alive"]
        and (not market_open or (count > 0 and fresh_count/count >= .8)),
        "delivery_enabled": output["paper-alerts"]["alive"],
        "note": "Weekday clock only; exchange holidays and external process supervision remain separate.",
    }


def gap_diagnostics(now: datetime, symbols: list[str] | None = None) -> dict:
    from .radar_sessions import session_minutes
    start = session_start(timezone.localdate(now))
    elapsed = max(0, min(session_minutes(timezone.localdate(now)), int((now-start).total_seconds()//60)))
    instruments = RadarInstrument.objects.filter(kind="EQUITY", active=True).order_by("symbol")
    if symbols:
        instruments = instruments.filter(symbol__in=symbols)
    rows = defaultdict(dict)
    for instrument_id, stamp, complete in RadarBar.objects.filter(
        instrument__in=instruments, timestamp__gte=start, timestamp__lt=start+timedelta(minutes=elapsed),
    ).values_list("instrument_id", "timestamp", "complete"):
        rows[instrument_id][stamp] = complete
    expected = [start+timedelta(minutes=i) for i in range(elapsed)]
    output = []
    for instrument in instruments:
        observed = rows[instrument.pk]
        missing = [stamp.isoformat() for stamp in expected if stamp not in observed]
        incomplete = [stamp.isoformat() for stamp in expected if stamp in observed and not observed[stamp]]
        if missing or incomplete:
            output.append({"symbol": instrument.symbol, "missing": len(missing), "incomplete": len(incomplete),
                           "first_missing": missing[:3], "first_incomplete": incomplete[:3]})
    return {
        "at": now.isoformat(), "expected_minutes": elapsed, "affected": output,
        "conflicting_history_versions": RadarHistoryConflict.objects.count(),
        "accepted_incomplete_repairs": RadarHistoryConflict.objects.filter(accepted_at__isnull=False).count(),
        "reconciliation": "radar_reconcile --once --symbols SYMBOL fills absent minutes and audits disagreements. Add --accept-incomplete to repair incomplete canonical bars after preserving original/proposed versions; complete bars are not replaced.",
    }


def build_shortlist(now: datetime) -> dict:
    instruments = list(RadarInstrument.objects.filter(kind="EQUITY", active=True).order_by("symbol"))
    quotes = {q.instrument_id: q for q in RadarQuote.objects.filter(instrument__in=instruments)}
    summaries = table_metrics(instruments, quotes, now)
    sectors = dict(TrackedStock.objects.exclude(sector="").values_list("symbol", "sector"))
    sectors.update(dict(RadarSector.objects.filter(
        verified_at__lte=now, verified_at__gte=now-timedelta(days=30),
    ).values_list("instrument__symbol", "industry")))
    returns = {}
    sector_returns = defaultdict(dict)
    for instrument in instruments:
        value = summaries[instrument.symbol]["change_pct"]
        if fresh(quotes.get(instrument.pk), now, Policy()) and value is not None:
            returns[instrument.symbol] = value
            if instrument.symbol in sectors:
                sector_returns[sectors[instrument.symbol]][instrument.symbol] = value
    # Explicit proxies: neither is represented as an exchange index return.
    breadth = mean(returns.values()) if len(returns) >= max(1, len(instruments)//2) else None
    prequalified = [
        instrument for instrument in instruments
        if fresh(quotes.get(instrument.pk), now, Policy())
        and summaries[instrument.symbol]["rvol"] is not None
        and summaries[instrument.symbol]["rvol"] >= MOMENTUM_RVOL_FLOOR
    ]
    history = defaultdict(list)
    for row in RadarBar.objects.filter(
        instrument__in=prequalified, complete=True, timestamp__gte=session_start(timezone.localdate(now)),
        timestamp__lt=now.replace(second=0, microsecond=0),
    ).order_by("timestamp"):
        history[row.instrument_id].append(Candle(row.timestamp, row.open, row.high, row.low, row.close, row.volume))
    setups = {}
    for item in RadarOpportunity.objects.filter(
        state__in=WATCHING, valid_until__gt=now, created_at__lte=now,
    ).order_by("created_at", "pk"):
        setups[(item.symbol, item.side)] = item
    catalysts = {}
    for item in RadarCatalyst.objects.filter(
        verified_at__lte=now, published_at__lte=now, expires_at__gt=now,
    ).order_by("verified_at", "pk"):
        if item.source_url.startswith(("https://", "http://")) and item.published_at <= item.verified_at < item.expires_at:
            catalysts[(item.symbol, item.side)] = item
    candidates = {"CE": [], "PE": []}
    excluded = defaultdict(int)
    for instrument in instruments:
        quote = quotes.get(instrument.pk)
        if not fresh(quote, now, Policy()) or quote is None:
            excluded["stale_or_missing_quote"] += 1
            continue
        summary = summaries[instrument.symbol]
        if summary["rvol"] is None:
            excluded["unavailable_same_time_rvol"] += 1
            continue
        if summary["rvol"] < MOMENTUM_RVOL_FLOOR:
            excluded["below_1.2_same_time_rvol"] += 1
            continue
        bars = five_minute_bars(history[instrument.pk])
        boundary = now.replace(minute=now.minute-now.minute % 5, second=0, microsecond=0)
        if (len(bars) < 4 or timezone.localtime(bars[0].timestamp).time() != time(9, 15)
                or bars[-1].timestamp+timedelta(minutes=5) != boundary
                or any(b.timestamp-a.timestamp != timedelta(minutes=5) for a, b in zip(bars, bars[1:]))):
            excluded["incomplete_recent_structure"] += 1
            continue
        volatility = atr(bars)
        if volatility <= 0 and len(bars) >= 3:
            volatility = max(bar.high for bar in bars[:3])-min(bar.low for bar in bars[:3])
        if volatility <= 0 or summary["change_pct"] is None:
            excluded["missing_atr_or_prior_session"] += 1
            continue
        momentum = opening_range_watch(
            bars, summary["change_pct"], summary["rvol"], breadth,
        )
        momentum_by_side = {item["side"]: item for item in momentum}
        for side, direction in [("CE", 1), ("PE", -1)]:
            watch = momentum_by_side.get(side)
            setup = setups.get((instrument.symbol, side))
            if watch is None and setup is None:
                continue
            trigger = setup.trigger if setup else watch["trigger"]
            stop = setup.stop if setup else watch["stop"]
            extension = direction*(quote.last-trigger)/volatility
            if not -.75 <= extension <= .25 or direction*(quote.last-stop) <= 0:
                continue
            components = [
                {"name": "Structure / proximity", "points": 30,
                 "detail": f"{'Recorded '+setup.strategy if setup else watch['state']+' '+watch['version']}; extension {extension:.2f} ATR."},
                {"name": "Same-time RVOL", "points": 20 if summary["rvol"] >= 1.5 else 10,
                 "detail": f"{summary['rvol']:.2f}x; identical complete minute prefixes, at least five baseline sessions."},
            ]
            relative = summary["change_pct"]-breadth if breadth is not None else None
            components.append({"name": "Stock-relative strength", "points": 20 if relative is not None and direction*relative > 0 else 0,
                               "detail": f"{relative:.2f} percentage points versus F&O equal-weight proxy." if relative is not None else "Unavailable: insufficient fresh complete-prior-session peers."})
            components.append({"name": "Market direction", "points": 10 if breadth is not None and direction*breadth > 0 else 0,
                               "detail": f"F&O equal-weight return proxy {breadth:.2f}%, {len(returns)} stocks; not Nifty." if breadth is not None else "Unavailable: no sufficiently covered fresh proxy."})
            peers = {s: r for s, r in sector_returns.get(sectors.get(instrument.symbol), {}).items() if s != instrument.symbol}
            sector = mean(peers.values()) if len(peers) >= 3 else None
            components.append({"name": "Sector direction", "points": 10 if sector is not None and direction*sector > 0 else 0,
                               "detail": f"Stored sector/industry peer proxy {sector:.2f}%, {len(peers)} other stocks; not a sector index." if sector is not None else "Unavailable: fewer than three mapped fresh peers."})
            catalyst = catalysts.get((instrument.symbol, side))
            components.append({"name": "Verified catalyst", "points": 10 if catalyst else 0,
                               "detail": catalyst.summary if catalyst else "Unavailable: raw Telegram text is not verified directional evidence.",
                               "source_url": catalyst.source_url if catalyst else None})
            score = sum(c["points"] for c in components)
            if score < 50:
                continue
            expiry = min(setup.valid_until if setup else now+timedelta(minutes=5), now+timedelta(seconds=60))
            candidates[side].append({
                "instrument_id": instrument.pk, "symbol": instrument.symbol, "side": side,
                "score": score, "components": components, "trigger": trigger, "stop": stop,
                "expires_at": expiry.isoformat(), "source_at": quote.source_at.isoformat(),
                "why": f"Directional structure with {summary['rvol']:.2f}x same-time volume; priority {score}/100, not a win probability.",
                "confirmation": f"{setup.strategy+' detector rules and ' if setup else 'Completed five-minute close beyond the 15-minute opening range, above/below session VWAP, and '}fresh executable option/risk checks required; a stock watch is not an entry.",
                "invalidation": f"Underlying {'below' if side == 'CE' else 'above'} {stop:g}, excessive extension, stale data or expiry.",
                "opportunity_id": setup.pk if setup else None,
                "state": setup.state if setup else "STOCK_WATCH",
            })
    for side in candidates:
        candidates[side] = sorted(candidates[side], key=lambda c: (-c["score"], c["symbol"]))[:MAX_PER_SIDE]
    return {
        "version": RANK_VERSION, "at": now.isoformat(), "expires_at": (now+timedelta(seconds=60)).isoformat(),
        "bullish": candidates["CE"], "bearish": candidates["PE"], "excluded": dict(excluded),
        "universe": len(instruments), "benchmark_proxy": breadth,
        "message": "No suitable trade/watch: required data or qualifying conditions are absent." if not any(candidates.values()) else "Watch priority only. No entry until strategy, option and risk checks pass.",
        "coverage": "Unavailable components earn no points. No score normalization or probability claim. Sector labels use stored metadata; catalysts require explicit source verification.",
    }


def persist_shortlist(now: datetime) -> RadarAssistantSnapshot:
    stamp = now.replace(second=0, microsecond=0)
    existing = RadarAssistantSnapshot.objects.filter(timestamp=stamp).first()
    if existing:
        return existing
    payload = build_shortlist(now)
    payload["gaps"] = gap_diagnostics(now)
    return RadarAssistantSnapshot.objects.create(timestamp=stamp, expires_at=now+timedelta(seconds=60), payload=payload)


def trigger_report(user, now: datetime) -> dict:
    """Return durable trigger records; never infer a trigger from a current quote."""
    events = []
    opportunities = RadarOpportunity.objects.filter(
        trigger_at__isnull=False, trigger_at__lte=now,
    ).prefetch_related("alerts", "decisions").order_by("-trigger_at", "-pk")
    for item in opportunities:
        alerts = list(item.alerts.all())
        latest_alert = max(alerts, key=lambda alert: (alert.created_at, alert.pk), default=None)
        decision = item.decisions.first()
        if latest_alert is None:
            delivery = "No strategy notification was created."
        elif latest_alert.sent_at:
            delivery = f"Delivered at {latest_alert.sent_at.isoformat()}."
        elif latest_alert.cancelled_at:
            delivery = "Notification superseded or cancelled before delivery."
        elif latest_alert.last_error:
            delivery = f"Delivery pending: {latest_alert.last_error}"
        else:
            delivery = "In-app record; delivery pending."
        events.append({
            "kind": "STRATEGY",
            "id": item.pk,
            "at": item.trigger_at.isoformat(),
            "symbol": item.symbol,
            "label": f"{item.strategy} {item.side} strategy confirmation",
            "state": item.state,
            "trigger": item.trigger,
            "stop": item.stop,
            "valid_until": item.valid_until.isoformat(),
            "reason": decision.reason if decision else item.reason,
            "delivery": delivery,
            "paper_outcome": item.net_pnl,
            "paper_outcome_label": "Net paper P&L" if item.net_pnl is not None else "No closed paper outcome",
        })
    if user is not None:
        alerts = RadarPriceAlert.objects.filter(
            user=user, triggered_at__isnull=False, triggered_at__lte=now,
        ).select_related("instrument").order_by("-triggered_at", "-pk")
        for alert in alerts:
            delivery = (
                f"Delivered at {alert.sent_at.isoformat()}." if alert.sent_at else
                f"Delivery pending: {alert.last_error}" if alert.last_error else
                "Observed crossing; delivery pending."
            )
            events.append({
                "kind": "PERSONAL_PRICE_ALERT",
                "id": alert.pk,
                "at": alert.triggered_at.isoformat(),
                "symbol": alert.instrument.symbol,
                "label": f"Personal price crossing {alert.direction} {alert.level:g}",
                "state": "DELIVERED" if alert.sent_at else "TRIGGERED",
                "trigger": alert.trigger_price,
                "stop": None,
                "valid_until": alert.expires_at.isoformat(),
                "reason": "Observed one-shot quote crossing. It is an alert, not a strategy entry or broker order.",
                "delivery": delivery,
                "paper_outcome": None,
                "paper_outcome_label": "No paper outcome: alert only",
            })
    events.sort(key=lambda event: event["at"], reverse=True)
    return {
        "as_of": now.isoformat(),
        "count": len(events),
        "strategy_count": sum(event["kind"] == "STRATEGY" for event in events),
        "personal_price_alert_count": sum(event["kind"] == "PERSONAL_PRICE_ALERT" for event in events),
        "events": events,
        "note": "Every row is a durable recorded confirmation or observed personal price crossing. The report refreshes from canonical event records and never reconstructs past triggers from current prices.",
    }


def assistant_payload(now: datetime, user=None) -> dict:
    snapshot = RadarAssistantSnapshot.objects.filter(timestamp__lte=now).first()
    current = snapshot is not None and snapshot.expires_at > now
    notices = RadarAlert.objects.select_related("opportunity").filter(
        created_at__lte=now, cancelled_at__isnull=True,
    ).order_by("-created_at", "-pk")[:20]
    return {
        "health": worker_health(now), "current": current,
        "momentum_evidence": MOMENTUM_EVIDENCE,
        "shortlist": snapshot.payload if current else {"bullish": [], "bearish": [], "message": "No current shortlist: evaluator has not published a fresh snapshot."},
        "last_snapshot_at": snapshot.timestamp.isoformat() if snapshot else None,
        "trigger_report": trigger_report(user, now),
        "exceptions": [{
            "id": a.pk, "symbol": a.opportunity.symbol, "state": a.state, "text": a.text,
            "created_at": a.created_at.isoformat(), "sent_at": a.sent_at.isoformat() if a.sent_at else None,
            "delivery_error": a.last_error,
        } for a in notices],
    }
