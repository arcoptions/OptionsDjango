"""Unified, evidence-aware read model for the ARC home page."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .action_workspace import action_list_context
from .market_context import market_context_evidence
from .models import (
    IndexOISnapshot,
    StockFundamentalSnapshot,
    StockScoreSnapshot,
    TradeExecution,
    TradeState,
)
from .operations_health import operations_snapshot
from .radar import Policy, fresh
from .radar_models import RadarQuote


INVESTMENT_EVIDENCE_MAX_AGE = timedelta(days=120)


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
    changes = []
    for quote in RadarQuote.objects.select_related("instrument").filter(
        instrument__kind="EQUITY",
        instrument__active=True,
    ):
        if not fresh(quote, now, Policy()) or not quote.previous_close:
            continue
        change = (quote.last - quote.previous_close) / quote.previous_close * 100
        changes.append(change)
        if change > 0:
            advances += 1
        elif change < 0:
            declines += 1
        else:
            unchanged += 1
    total = advances + declines + unchanged
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total,
        "advance_percent": round(advances / total * 100, 1) if total else None,
        "average_change_percent": round(sum(changes) / len(changes), 2) if changes else None,
        "source": "Fresh Dhan radar quotes",
        "updated_at": now if total else None,
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
    return {
        "generated_at": now,
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
    }
