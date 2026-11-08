from collections import defaultdict
from decimal import Decimal

from .models import BrokerPeriodSummary, BrokerPnlEntry
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
        "limitations": [
            "Broker statement rows do not contain trade timestamps, so ARC does not calculate a broker equity curve or drawdown from them.",
            "Paper radar performance is reported separately from broker-realized performance.",
            "TradeExecution records do not contain realized P&L and are excluded from return metrics.",
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
