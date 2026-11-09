"""Unified, evidence-aware read model for the ARC home page."""

from collections import defaultdict
from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone

from .action_workspace import action_list_context
from .market_context import market_context_evidence
from .market_breadth import latest_market_breadth_snapshot
from .models import (
    AIBriefingSnapshot,
    EconomicEvent,
    IndexOISnapshot,
    StockFundamentalSnapshot,
    StockScoreSnapshot,
    TradeExecution,
    TradeState,
)
from .operations_health import operations_snapshot
from .radar import Policy, fresh
from .radar_models import RadarInstrument, RadarQuote, RadarSector


INVESTMENT_EVIDENCE_MAX_AGE = timedelta(days=120)
GAP_THRESHOLD_PERCENT = 0.1
MARKET_SESSION_WINDOWS = (
    ("PRE_MARKET", "Pre-Market", "Before 09:15 IST"),
    ("OPENING_DRIVE", "Opening Drive", "09:15-10:30 IST"),
    ("MIDDAY", "Midday", "10:30-14:00 IST"),
    ("POWER_HOUR", "Power Hour", "14:00-15:30 IST"),
    ("AFTER_HOURS", "After Hours", "After 15:30 IST"),
)


def market_session_context(now=None):
    now = timezone.localtime(now or timezone.now())
    local_time = now.time()
    weekend = now.weekday() >= 5
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    holiday = None
    if not weekend:
        holiday = (
            EconomicEvent.objects.filter(
                starts_at__gte=day_start,
                starts_at__lt=day_end,
                event_type__in=("MARKET_HOLIDAY", "EXCHANGE_HOLIDAY"),
            )
            .order_by("starts_at", "pk")
            .first()
        )

    if weekend or holiday:
        active_key = "AFTER_HOURS"
    elif local_time < time(9, 15):
        active_key = "PRE_MARKET"
    elif local_time < time(10, 30):
        active_key = "OPENING_DRIVE"
    elif local_time < time(14):
        active_key = "MIDDAY"
    elif local_time < time(15, 30):
        active_key = "POWER_HOUR"
    else:
        active_key = "AFTER_HOURS"

    sessions = [
        {
            "key": key,
            "label": label,
            "window": window,
            "active": key == active_key,
        }
        for key, label, window in MARKET_SESSION_WINDOWS
    ]
    active = next(session for session in sessions if session["active"])
    if weekend:
        calendar_state = "WEEKEND"
        calendar_source = "Deterministic weekday rule"
        warning = ""
    elif holiday:
        calendar_state = "VERIFIED_HOLIDAY"
        calendar_source = f"{holiday.provider}: {holiday.title}"
        warning = ""
    else:
        calendar_state = "SCHEDULE_ONLY"
        calendar_source = "Indian cash-market clock"
        warning = (
            "No attributed exchange-holiday record exists for this date. "
            "Session timing is schedule evidence, not proof that the exchange is open."
        )
    return {
        "key": active["key"],
        "label": active["label"],
        "market_open": active["key"] in {
            "OPENING_DRIVE",
            "MIDDAY",
            "POWER_HOUR",
        } and not weekend and holiday is None,
        "sessions": sessions,
        "calendar_state": calendar_state,
        "calendar_source": calendar_source,
        "warning": warning,
        "trading_date": now.date(),
        "generated_at": now,
    }


def _index_card(underlying, now):
    snapshot = IndexOISnapshot.objects.filter(underlying=underlying).order_by("-created_at").first()
    if snapshot is None:
        return {
            "symbol": underlying,
            "price": None,
            "change": None,
            "change_percent": None,
            "pcr": None,
            "regime": "NO DATA",
            "source": "DHAN",
            "updated_at": None,
            "age_seconds": None,
            "stale": True,
        }

    price = float(snapshot.underlying_price) if snapshot.underlying_price is not None else None
    change = float(snapshot.underlying_change) if snapshot.underlying_change is not None else None
    previous = price - change if price is not None and change is not None else None
    age = max(0, int((now - snapshot.created_at).total_seconds()))
    return {
        "symbol": underlying,
        "price": price,
        "change": change,
        "change_percent": round(change / previous * 100, 2) if previous else None,
        "pcr": snapshot.pcr,
        "regime": snapshot.regime or "UNCLASSIFIED",
        "source": snapshot.source,
        "updated_at": snapshot.created_at,
        "age_seconds": age,
        "stale": age > 300,
    }


def _breadth(now):
    advances = declines = unchanged = 0
    rows = []
    for quote in RadarQuote.objects.select_related("instrument").filter(
        instrument__kind="EQUITY",
        instrument__active=True,
    ):
        if not fresh(quote, now, Policy()) or not quote.previous_close:
            continue
        change = (quote.last - quote.previous_close) / quote.previous_close * 100
        gap = (
            (quote.day_open - quote.previous_close) / quote.previous_close * 100
            if quote.day_open is not None
            else None
        )
        rows.append({
            "instrument_id": quote.instrument_id,
            "change": change,
            "gap": gap,
            "gap_filled": (
                quote.day_low <= quote.previous_close
                if gap is not None
                and gap >= GAP_THRESHOLD_PERCENT
                and quote.day_low is not None
                else quote.day_high >= quote.previous_close
                if gap is not None
                and gap <= -GAP_THRESHOLD_PERCENT
                and quote.day_high is not None
                else None
            ),
            "source_at": quote.source_at,
        })
        if change > 0:
            advances += 1
        elif change < 0:
            declines += 1
        else:
            unchanged += 1
    total = advances + declines + unchanged
    active_total = RadarInstrument.objects.filter(
        kind="EQUITY",
        active=True,
    ).count()
    distributions = [
        {"label": "<= -2%", "count": sum(row["change"] <= -2 for row in rows)},
        {"label": "-2% to -1%", "count": sum(-2 < row["change"] <= -1 for row in rows)},
        {"label": "-1% to 0%", "count": sum(-1 < row["change"] < 0 for row in rows)},
        {"label": "0% to 1%", "count": sum(0 <= row["change"] < 1 for row in rows)},
        {"label": "1% to 2%", "count": sum(1 <= row["change"] < 2 for row in rows)},
        {"label": ">= 2%", "count": sum(row["change"] >= 2 for row in rows)},
    ]
    gap_ups = [
        row
        for row in rows
        if row["gap"] is not None and row["gap"] >= GAP_THRESHOLD_PERCENT
    ]
    gap_downs = [
        row
        for row in rows
        if row["gap"] is not None and row["gap"] <= -GAP_THRESHOLD_PERCENT
    ]
    sector_names = {
        row.instrument_id: row.industry
        for row in RadarSector.objects.filter(
            instrument_id__in=[row["instrument_id"] for row in rows],
        )
    }
    sector_groups = defaultdict(list)
    for row in rows:
        sector = sector_names.get(row["instrument_id"])
        if sector:
            sector_groups[sector].append(row["change"])
    sectors = sorted(
        (
            {
                "name": name,
                "average_change_percent": round(sum(changes) / len(changes), 2),
                "advances": sum(change > 0 for change in changes),
                "declines": sum(change < 0 for change in changes),
                "total": len(changes),
            }
            for name, changes in sector_groups.items()
        ),
        key=lambda row: (row["average_change_percent"], row["total"], row["name"]),
        reverse=True,
    )
    snapshot = latest_market_breadth_snapshot(now)
    historical = snapshot.metrics if snapshot else {}
    unavailable = snapshot.unavailable if snapshot else [
        {
            "name": "EMA breadth",
            "reason": "No current qualifying offline breadth snapshot exists.",
        },
        {
            "name": "RSI distribution",
            "reason": "No current qualifying offline breadth snapshot exists.",
        },
        {
            "name": "Volume surge",
            "reason": "No current qualifying offline breadth snapshot exists.",
        },
        {
            "name": "52-week highs and lows",
            "reason": "No current qualifying offline breadth snapshot exists.",
        },
    ]
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total,
        "advance_percent": round(advances / total * 100, 1) if total else None,
        "decline_percent": round(declines / total * 100, 1) if total else None,
        "unchanged_percent": round(unchanged / total * 100, 1) if total else None,
        "advance_decline_ratio": round(advances / declines, 2) if declines else None,
        "average_change_percent": (
            round(sum(row["change"] for row in rows) / len(rows), 2)
            if rows
            else None
        ),
        "active_universe": active_total,
        "coverage_percent": round(total / active_total * 100, 1) if active_total else None,
        "price_distribution": distributions,
        "gaps": {
            "threshold_percent": GAP_THRESHOLD_PERCENT,
            "up": len(gap_ups),
            "down": len(gap_downs),
            "filled": sum(row["gap_filled"] is True for row in gap_ups + gap_downs),
            "unfilled": sum(row["gap_filled"] is False for row in gap_ups + gap_downs),
        },
        "sectors": sectors[:8],
        "historical": historical,
        "historical_source_at": snapshot.source_at if snapshot else None,
        "historical_universe": snapshot.universe if snapshot else "FNO",
        "unavailable": unavailable,
        "source": "Fresh Dhan radar quotes",
        "updated_at": max((row["source_at"] for row in rows), default=None),
    }


def derive_regime(nifty_change, breadth_change):
    if nifty_change is None or breadth_change is None:
        return {
            "label": "INSUFFICIENT DATA",
            "tone": "warning",
            "approach": "Wait for fresh index and breadth evidence before choosing a directional approach.",
        }
    if nifty_change >= 0.3 and breadth_change >= 0.2:
        return {
            "label": "BULLISH / RISK-ON",
            "tone": "success",
            "approach": "Prioritize confirmed long setups; avoid chasing extended entries.",
        }
    if nifty_change <= -0.3 and breadth_change <= -0.2:
        return {
            "label": "BEARISH / RISK-OFF",
            "tone": "error",
            "approach": "Prioritize capital protection and confirmed bearish setups.",
        }
    if abs(nifty_change) <= 0.3 and abs(breadth_change) <= 0.2:
        return {
            "label": "SIDEWAYS / RANGE",
            "tone": "warning",
            "approach": "Reduce directional conviction and require clean range-edge confirmation.",
        }
    return {
        "label": "MIXED / DIVERGENT",
        "tone": "warning",
        "approach": "Index and breadth disagree; use stock-specific evidence and smaller risk.",
    }


def _scenarios(regime):
    active = regime["label"]
    return [
        {
            "name": "Bullish",
            "active": active == "BULLISH / RISK-ON",
            "condition": "NIFTY change at least +0.3% with average fresh-stock breadth at least +0.2%.",
            "response": "Review confirmed BUY rows and bullish scanner triggers.",
        },
        {
            "name": "Bearish",
            "active": active == "BEARISH / RISK-OFF",
            "condition": "NIFTY change at most -0.3% with average fresh-stock breadth at most -0.2%.",
            "response": "Protect open risk and review confirmed bearish option setups.",
        },
        {
            "name": "Sideways or mixed",
            "active": active in {"SIDEWAYS / RANGE", "MIXED / DIVERGENT"},
            "condition": "Index and breadth are muted or disagree.",
            "response": "Wait for confirmation; prefer manage, watch, and review actions over new entries.",
        },
    ]


def _ranked_investments(now):
    scores = {}
    for score in StockScoreSnapshot.objects.filter(
        score_type="INVESTMENT",
        score__isnull=False,
        confidence__gte=0.6,
        source_at__lte=now,
    ).order_by("symbol", "-source_at", "-pk"):
        scores.setdefault(score.symbol, score)

    fundamentals = {}
    for fundamental in StockFundamentalSnapshot.objects.filter(
        symbol__in=scores,
        source_at__lte=now,
    ).order_by("symbol", "-source_at", "-pk"):
        fundamentals.setdefault(fundamental.symbol, fundamental)

    rows = []
    for symbol, score in scores.items():
        fundamental = fundamentals.get(symbol)
        if fundamental is None or now - fundamental.source_at > INVESTMENT_EVIDENCE_MAX_AGE:
            continue
        strongest_components = sorted(
            (
                (name, float(value))
                for name, value in score.components.items()
                if isinstance(value, (int, float))
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        rows.append({
            "symbol": symbol,
            "score": score.score,
            "confidence": score.confidence,
            "version": score.version,
            "provider": fundamental.provider,
            "source_at": fundamental.source_at,
            "score_source_at": score.source_at,
            "evidence": [
                f"{name.replace('_', ' ').title()} {value:.1f}"
                for name, value in strongest_components
            ],
            "missing_data": score.missing_data,
            "detail_url": reverse("stock_option_detail", args=[symbol]),
        })
    return sorted(
        rows,
        key=lambda row: (row["score"], row["confidence"], row["symbol"]),
        reverse=True,
    )[:8]


def command_centre_context(now=None):
    now = now or timezone.now()
    actions = action_list_context()
    indexes = [_index_card("NIFTY", now), _index_card("SENSEX", now)]
    breadth = _breadth(now)
    regime = derive_regime(indexes[0]["change_percent"], breadth["average_change_percent"])
    operations = operations_snapshot(now)
    market_context = market_context_evidence(now)
    briefing = AIBriefingSnapshot.objects.filter(
        briefing_date=timezone.localdate(now),
    ).first()
    return {
        "generated_at": now,
        "market_session": market_session_context(now),
        "indexes": indexes,
        "breadth": breadth,
        "regime": regime,
        "scenarios": _scenarios(regime),
        "opportunities": actions["rows"][:8],
        "investment_opportunities": _ranked_investments(now),
        "action_counts": actions,
        "open_trades": TradeExecution.objects.select_related("signal").filter(
            state=TradeState.OPEN,
        )[:8],
        "operations": operations,
        "market_context": market_context,
        "briefing": briefing,
    }
