from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from .market_context import market_context_evidence
from .models import BrokerPeriodSummary, BrokerPnlEntry, OptionOutcome, TipSignal
from .radar_models import RadarOpportunity


def performance_metrics(values, *, ordered=False):
    pnl = [Decimal(str(value)) for value in values if value is not None]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    net = sum(pnl, Decimal("0"))
    drawdown = None
    if ordered:
        equity = peak = Decimal("0")
        maximum = Decimal("0")
        for value in pnl:
            equity += value
            peak = max(peak, equity)
            maximum = max(maximum, peak - equity)
        drawdown = maximum
    return {
        "trades": len(pnl),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(pnl) - len(wins) - len(losses),
        "win_rate": len(wins) / len(pnl) * 100 if pnl else None,
        "net_pnl": net,
        "expectancy": net / len(pnl) if pnl else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "maximum_drawdown": drawdown,
    }


def _breakdown(rows, field):
    buckets = defaultdict(list)
    for row in rows:
        buckets[getattr(row, field) or "UNKNOWN"].append(row.realised_pnl)
    return [
        {"label": label, **performance_metrics(values)}
        for label, values in sorted(buckets.items())
    ]


def performance_context():
    broker_rows = list(BrokerPnlEntry.objects.all())
    paper_rows = list(
        RadarOpportunity.objects.filter(
            state=RadarOpportunity.State.EXITED,
            net_pnl__isnull=False,
        ).order_by("closed_at", "pk")
    )
    charges = sum(
        (summary.charges for summary in BrokerPeriodSummary.objects.all()),
        Decimal("0"),
    )
    provider_quality = _provider_quality()
    telegram_providers = _telegram_provider_quality()
    return {
        "broker": performance_metrics(
            [row.realised_pnl for row in broker_rows],
        ),
        "paper": performance_metrics(
            [row.net_pnl for row in paper_rows],
            ordered=True,
        ),
        "broker_charges": charges,
        "broker_by_broker": _breakdown(broker_rows, "broker"),
        "broker_by_underlying": _breakdown(broker_rows, "underlying"),
        "broker_by_kind": _breakdown(broker_rows, "instrument_kind"),
        "paper_by_strategy": _paper_breakdown(paper_rows, "strategy"),
        "paper_by_symbol": _paper_breakdown(paper_rows, "symbol"),
        "provider_quality": provider_quality,
        "telegram_providers": telegram_providers,
        "limitations": [
            "Broker statement rows do not contain trade timestamps, so ARC does not calculate a broker equity curve or drawdown from them.",
            "Paper radar performance is reported separately from broker-realized performance.",
            "TradeExecution records do not contain realized P&L and are excluded from return metrics.",
            "Telegram provider hit rates use recorded target and stop outcomes; they are not fill-price or execution-time P&L.",
        ],
    }


def _paper_breakdown(rows, field):
    buckets = defaultdict(list)
    for row in rows:
        buckets[getattr(row, field) or "UNKNOWN"].append(row.net_pnl)
    return [
        {"label": label, **performance_metrics(values)}
        for label, values in sorted(buckets.items())
    ]


def _provider_quality():
    buckets = defaultdict(lambda: {
        "rows": 0,
        "current": 0,
        "stale": 0,
        "invalid": 0,
    })
    evidence = market_context_evidence(timezone.now())
    for rows in evidence["groups"].values():
        for row in rows:
            bucket = buckets[row["provider"] or "UNATTRIBUTED"]
            bucket["rows"] += 1
            bucket["stale"] += int(row["stale"])
            bucket["invalid"] += int(not row["valid"])
            bucket["current"] += int(row["valid"] and not row["stale"])
    return [
        {
            "provider": provider,
            **values,
            "current_pct": values["current"] / values["rows"] * 100,
        }
        for provider, values in sorted(buckets.items())
    ]


def _telegram_provider_quality():
    buckets = defaultdict(lambda: {
        "signals": 0,
        "resolved": 0,
        "targets": 0,
        "stops": 0,
        "unresolved": 0,
    })
    rows = TipSignal.objects.filter(source_type="TELEGRAM").values(
        "source_name",
        "outcome_status",
    )
    for row in rows:
        bucket = buckets[row["source_name"] or "UNATTRIBUTED"]
        bucket["signals"] += 1
        outcome = row["outcome_status"]
        if outcome == OptionOutcome.TARGET_1:
            bucket["targets"] += 1
            bucket["resolved"] += 1
        elif outcome == OptionOutcome.STOP_LOSS:
            bucket["stops"] += 1
            bucket["resolved"] += 1
        elif outcome == OptionOutcome.UNRESOLVED:
            bucket["unresolved"] += 1
    return [
        {
            "provider": provider,
            **values,
            "hit_rate": (
                values["targets"] / values["resolved"] * 100
                if values["resolved"]
                else None
            ),
            "coverage": (
                values["resolved"] / values["signals"] * 100
                if values["signals"]
                else 0
            ),
        }
        for provider, values in sorted(buckets.items())
    ]
