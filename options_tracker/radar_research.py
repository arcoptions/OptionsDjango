"""Frozen prospective observations, not retrospective option profit claims."""

from dataclasses import asdict
from datetime import datetime, time, timedelta
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean

from django.utils import timezone

from .models import (
    RadarAssistantSnapshot, RadarBar, RadarOpportunity, RadarOptionPoint, RadarQuote,
    RadarResearchObservation, RadarResearchProtocol,
)
from .radar import Policy, VERSION, fresh


PROTOCOL = "stock-trap-forward-v17"
PRIMARY = "S1"
HORIZONS = [15, 30, 60]


def register_protocol(now: datetime) -> RadarResearchProtocol:
    specification = {
        "primary": PRIMARY, "comparators": ["S2", "S3"], "strategy_version": VERSION,
        "policy": asdict(Policy()), "horizons_minutes": HORIZONS,
        "selection": "S1 is best studied, not proven profitable. Earlier Nifty option results were negative; underlying matched-control evidence inconclusive.",
        "promotion": "No automatic live promotion. Review at least 60 future sessions and 100 triggered primary observations; counts are minimum review gates, not statistical proof. No threshold tuning within this protocol.",
        "baseline": "Same-direction equal-weight forward return of fresh other F&O stocks frozen at capture. Descriptive, not randomized/matched causal evidence.",
        "execution": "Acknowledged displayed ask/bid paper model; no guaranteed fills. Additional adverse slippage stress 0, 10, 25 bps per leg.",
        "rule_hash": sha256(Path(__file__).with_name("radar.py").read_bytes()).hexdigest(),
        "measurement_hash": sha256(Path(__file__).read_bytes()).hexdigest(),
        "support_hashes": {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                           for name in ["radar_assistant.py", "radar_feed.py", "radar_alerts.py", "radar_history.py",
                                        "radar_sessions.py", "radar_workspace.py"]},
    }
    fingerprint = sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()
    existing = RadarResearchProtocol.objects.filter(name=PROTOCOL).first()
    if existing:
        if existing.fingerprint != fingerprint or existing.specification != specification:
            raise ValueError("Frozen research protocol/code changed. Register an explicit new protocol version; do not overwrite existing evidence.")
        return existing
    next_day = timezone.localdate(now)+timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return RadarResearchProtocol.objects.create(
        name=PROTOCOL, registered_at=now,
        starts_at=timezone.make_aware(datetime.combine(next_day, time(9, 15))),
        fingerprint=fingerprint, specification=specification,
    )


def capture_observations(protocol: RadarResearchProtocol, now: datetime) -> int:
    quotes = {q.instrument.symbol: q for q in RadarQuote.objects.select_related("instrument").filter(
        instrument__kind="EQUITY", instrument__active=True,
    ) if fresh(q, now, Policy())}
    snapshot = RadarAssistantSnapshot.objects.filter(timestamp__lte=now, expires_at__gt=now).first()
    breadth = snapshot.payload.get("benchmark_proxy") if snapshot else None
    regime = ("BULLISH_PROXY" if breadth > .1 else "BEARISH_PROXY" if breadth < -.1 else "FLAT_PROXY") if breadth is not None else "UNAVAILABLE"
    count = 0
    for item in RadarOpportunity.objects.filter(
        research__isnull=True, created_at__gte=protocol.registered_at, created_at__lte=now,
    ):
        quote = quotes.get(item.symbol)
        # A missing contemporaneous reference stays missing; it is never backfilled later.
        basket = {symbol: q.last for symbol, q in quotes.items() if symbol != item.symbol}
        reference = {
            "last": quote.last if quote else None,
            "source_at": quote.source_at.isoformat() if quote else None,
            "trigger": item.trigger, "stop": item.stop, "atr": item.atr,
            "strategy": item.strategy, "version": item.version, "side": item.side,
            "regime": regime,
            "basket": basket,
            "cohort": "FORWARD" if item.created_at >= protocol.starts_at else "WARMUP_NOT_FORWARD",
            "mode": "FIRST_OBSERVATION_NOT_A_FILL",
        }
        RadarResearchObservation.objects.create(
            opportunity=item, protocol=protocol, captured_at=now, reference=reference,
        )
        count += 1
    return count


def markout(symbol: str, captured: datetime, end: datetime, reference: float, direction: int, risk: float) -> dict:
    # Start with the next whole minute to avoid pre-capture intraminute extrema.
    start = captured.replace(second=0, microsecond=0)+timedelta(minutes=1)
    count = int((end-start).total_seconds()//60)
    if count <= 0 or timezone.localdate(start) != timezone.localdate(end):
        return {"available": False, "reason": "Horizon crosses a session boundary or has no full post-capture minutes."}
    rows = list(RadarBar.objects.filter(
        instrument__symbol=symbol, instrument__kind="EQUITY", timestamp__gte=start, timestamp__lt=end.replace(second=0, microsecond=0),
    ).order_by("timestamp"))
    if (len(rows) != count or any(not r.complete or r.timestamp != start+timedelta(minutes=i) for i, r in enumerate(rows))):
        return {"available": False, "reason": "Missing/incomplete post-capture minutes; no markout inferred."}
    favourable = max(direction*((r.high if direction == 1 else r.low)-reference) for r in rows)
    adverse = min(direction*((r.low if direction == 1 else r.high)-reference) for r in rows)
    signed_return = direction*(rows[-1].close/reference-1)*100
    return {"available": True, "return_pct": signed_return,
            "mfe_r": max(0, favourable)/risk, "mae_r": max(0, -adverse)/risk,
            "end": rows[-1].timestamp.isoformat(), "note": "Underlying diagnostic only, not option P&L."}


def update_observations(protocol: RadarResearchProtocol, now: datetime) -> int:
    updated = 0
    for observation in RadarResearchObservation.objects.filter(protocol=protocol).select_related("opportunity"):
        reference = observation.reference
        outcome = dict(observation.outcomes)
        price = reference["last"]
        item = observation.opportunity
        direction = 1 if reference["side"] == "CE" else -1
        risk = abs(reference["trigger"]-reference["stop"])
        changed = False
        for minutes in HORIZONS:
            key = str(minutes)
            end = observation.captured_at+timedelta(minutes=minutes)
            if key in outcome or now < end:
                continue
            result = markout(item.symbol, observation.captured_at, end, price, direction, risk) if price and risk > 0 else {"available": False, "reason": "No fresh reference or positive risk at capture."}
            controls = []
            if result["available"]:
                for symbol, entry in reference["basket"].items():
                    control = markout(symbol, observation.captured_at, end, entry, direction, entry)
                    if control["available"]:
                        controls.append(control["return_pct"])
            baseline = mean(controls) if len(controls) >= 5 else None
            outcome[key] = {**result, "baseline_pct": baseline, "baseline_count": len(controls),
                            "excess_pct": result["return_pct"]-baseline if result["available"] and baseline is not None else None}
            changed = True
        if changed:
            observation.outcomes = outcome
            observation.save(update_fields=["outcomes"])
            updated += 1
    return updated


def research_report(now: datetime) -> dict:
    protocol = RadarResearchProtocol.objects.filter(name=PROTOCOL).first()
    if not protocol:
        return {"status": "NOT_REGISTERED", "primary": PRIMARY, "groups": []}
    groups = []
    for strategy in ["S1", "S2", "S3"]:
        observations = list(RadarResearchObservation.objects.filter(
            protocol=protocol, reference__strategy=strategy, reference__cohort="FORWARD",
            captured_at__lte=now,
        ).select_related("opportunity__contract").prefetch_related("opportunity__alerts"))
        items = [o.opportunity for o in observations]
        closed = sorted((i for i in items if i.net_pnl is not None and i.closed_at and i.closed_at <= now),
                        key=lambda i: (i.closed_at, i.pk))
        pnl = [i.net_pnl for i in closed]
        balance = peak = drawdown = 0.0
        for value in pnl:
            balance += value
            peak = max(peak, balance)
            drawdown = max(drawdown, peak-balance)
        markouts = [o.outcomes["30"] for o in observations if o.outcomes.get("30", {}).get("available")]
        excess = [m["excess_pct"] for m in markouts if m["excess_pct"] is not None]
        alerts = [a for item in items for a in item.alerts.all() if a.state == "ELIGIBLE" and a.sent_at and a.sent_at <= now]
        delays = [(a.sent_at-a.created_at).total_seconds() for a in alerts]
        trigger_delays = [(a.sent_at-a.opportunity.trigger_at).total_seconds() for a in alerts if a.opportunity.trigger_at]
        regimes = {}
        for observation in observations:
            regime = observation.reference["regime"]
            values = regimes.setdefault(regime, {"observed": 0, "closed": 0, "net_pnl": 0})
            values["observed"] += 1
            if observation.opportunity in closed:
                values["closed"] += 1
                values["net_pnl"] += observation.opportunity.net_pnl
        rejection_reasons = {}
        option_excursions = []
        for item in items:
            if item.opened_at is None:
                rejection_reasons[item.reason] = rejection_reasons.get(item.reason, 0)+1
            if item.opened_at and item.closed_at:
                points = list(RadarOptionPoint.objects.filter(
                    instrument_id=item.contract_id, timestamp__gte=item.opened_at,
                    timestamp__lte=item.closed_at, received_at__lte=now,
                ).order_by("timestamp"))
                covered = bool(points and points[0].timestamp-item.opened_at <= timedelta(seconds=45)
                               and item.closed_at-points[-1].timestamp <= timedelta(seconds=45)
                               and all(b.timestamp-a.timestamp <= timedelta(seconds=45) for a, b in zip(points, points[1:]))
                               and all(p.bid_qty is not None and p.bid_qty >= item.quantity and p.bid > 0
                                       and (p.ask == 0 or p.ask >= p.bid) and 0 <= (p.received_at-p.timestamp).total_seconds() <= 15 for p in points))
                option_excursions.append({
                    "opportunity_id": item.pk, "available": covered,
                    "sampled_bid_mfe_pct": max(0, max(p.bid for p in points)/item.entry_price-1)*100 if covered else None,
                    "sampled_bid_mae_pct": max(0, 1-min(p.bid for p in points)/item.entry_price)*100 if covered else None,
                    "reason": "Sampled displayed full-size bids only, not tick-complete or guaranteed executable extrema." if covered else "Insufficient fresh full-size bid samples/depth; no option excursion inferred.",
                })
        groups.append({
            "strategy": strategy, "primary": strategy == PRIMARY, "observed": len(items),
            "triggered": sum(i.trigger_at is not None for i in items),
            "entered": sum(i.opened_at is not None for i in items), "closed": len(pnl),
            "not_entered": sum(i.opened_at is None for i in items),
            "expired_or_rejected": sum(i.state in ["EXPIRED", "REJECTED", "INVALIDATED", "SKIPPED"] for i in items),
            "net_pnl": round(sum(pnl), 2), "expectancy": round(mean(pnl), 2) if pnl else None,
            "max_drawdown": round(drawdown, 2),
            "mean_trigger_to_delivered_alert_seconds": mean(trigger_delays) if trigger_delays else None,
            "mean_alert_delivery_seconds": mean(delays) if delays else None,
            "delivered_eligible_alerts": len(delays),
            "markout_30_count": len(markouts), "mean_mfe_r": mean(m["mfe_r"] for m in markouts) if markouts else None,
            "mean_mae_r": mean(m["mae_r"] for m in markouts) if markouts else None,
            "mean_excess_30_pct": mean(excess) if excess else None, "excess_count": len(excess),
            "slippage_stress_net": {
                str(bps): round(sum(i.net_pnl-(i.entry_price+i.exit_price)*i.quantity*bps/10000 for i in closed), 2)
                for bps in [0, 10, 25]
            },
            "regimes": regimes, "not_entered_reasons": rejection_reasons,
            "option_excursions": option_excursions,
            "expiry_days_at_entry": sorted({(i.contract.expiry-timezone.localdate(i.opened_at)).days for i in items if i.opened_at and i.contract_id}),
        })
    return {
        "status": "FORWARD_RECORDING" if now >= protocol.starts_at else "REGISTERED_NOT_STARTED",
        "primary": PRIMARY, "name": protocol.name, "starts_at": protocol.starts_at.isoformat(),
        "fingerprint": protocol.fingerprint, "specification": protocol.specification, "groups": groups,
        "limitations": "No proven edge. Missing horizons remain unavailable. Same-time basket is descriptive; not matched causal evidence. Not-entered is not automatically a broker missed fill. Paper displayed quotes are not executions. Drawdown uses closed paper equity, not intratrade mark-to-market. Regime uses the disclosed F&O proxy, not a verified index.",
    }
