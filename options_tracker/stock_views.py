"""The Stock Options tab and the private breakeven dashboard.

Kept out of views.py, which is already a thousand lines of the index/tips side
of the terminal and shares nothing with this one beyond the base template.
"""
import datetime as dt
import json
from collections import defaultdict

from django.db.models import Avg, Count, Max, Min, Sum
from django.shortcuts import render

from .models import (
    BrokerPeriodSummary,
    BrokerPnlEntry,
    DownloadJob,
    StockEquityCandle,
    StockOptionCandle,
    TrackedStock,
)


def _rupees(value):
    """Indian grouping, because 98,47,945 and 9,847,945 read very differently."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0"
    sign = "-" if value < 0 else ""
    digits = f"{abs(value):.0f}"
    if len(digits) <= 3:
        return sign + digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts + [tail])


def _coverage():
    """Bars on hand per symbol, for both feeds, in one pass each."""
    equity = {
        row["symbol"]: row
        for row in StockEquityCandle.objects.values("symbol").annotate(
            bars=Count("id"), first=Min("timestamp"), last=Max("timestamp")
        )
    }
    options = {
        row["symbol"]: row
        for row in StockOptionCandle.objects.values("symbol").annotate(
            bars=Count("id"), strikes=Count("relative_strike", distinct=True)
        )
    }
    return equity, options


def stock_options(request):
    """Every stock the account has actually traded, and what data we hold on it."""
    stocks = list(TrackedStock.objects.filter(is_active=True).order_by("priority"))
    equity, options = _coverage()

    sort = request.GET.get("sort", "priority")
    query = (request.GET.get("q") or "").strip().upper()

    rows = []
    for stock in stocks:
        cover = equity.get(stock.symbol, {})
        opt = options.get(stock.symbol, {})
        turnover = float(stock.turnover or 0)
        pnl = float(stock.realised_pnl or 0)
        rows.append({
            "stock": stock,
            "symbol": stock.symbol,
            "priority": stock.priority,
            "turnover": turnover,
            "turnover_text": _rupees(turnover),
            "pnl": pnl,
            "pnl_text": _rupees(pnl),
            "edge_bps": (pnl / turnover * 10000) if turnover else 0,
            "contracts": stock.contracts_traded,
            "brokers": stock.brokers,
            "lot_size": stock.lot_size,
            "strike_step": float(stock.strike_step or 0),
            "equity_bars": cover.get("bars", 0),
            "first_bar": cover.get("first"),
            "last_bar": cover.get("last"),
            "option_bars": opt.get("bars", 0),
            "option_strikes": opt.get("strikes", 0),
        })

    if query:
        rows = [row for row in rows if query in row["symbol"]]

    keys = {
        "priority": lambda row: row["priority"],
        "pnl": lambda row: row["pnl"],
        "loss": lambda row: -row["pnl"],
        "turnover": lambda row: -row["turnover"],
        "edge": lambda row: row["edge_bps"],
        "bars": lambda row: -row["equity_bars"],
        "symbol": lambda row: row["symbol"],
    }
    rows.sort(key=keys.get(sort, keys["priority"]))

    jobs = DownloadJob.objects.values("kind", "status").annotate(n=Count("id"))
    job_table = defaultdict(lambda: {"DONE": 0, "FAIL": 0})
    for row in jobs:
        job_table[row["kind"]][row["status"]] = row["n"]

    winners = [row for row in rows if row["pnl"] > 0]
    losers = [row for row in rows if row["pnl"] < 0]

    context = {
        "rows": rows,
        "sort": sort,
        "query": query,
        "total_stocks": len(stocks),
        "shown": len(rows),
        "equity_bars": StockEquityCandle.objects.count(),
        "option_bars": StockOptionCandle.objects.count(),
        "covered": sum(1 for row in rows if row["equity_bars"] > 0),
        "option_covered": sum(1 for row in rows if row["option_bars"] > 0),
        "job_table": sorted(
            ({"kind": kind, **counts} for kind, counts in job_table.items()),
            key=lambda item: item["kind"],
        ),
        "winners": len(winners),
        "losers": len(losers),
        "won": _rupees(sum(row["pnl"] for row in winners)),
        "lost": _rupees(sum(row["pnl"] for row in losers)),
        "title": "Stock Options",
    }
    return render(request, "options_tracker/stock_options.html", context)


def stock_option_detail(request, symbol):
    """One stock: the price track, the strikes around it, and the OI on them."""
    symbol = symbol.upper()
    stock = TrackedStock.objects.filter(symbol=symbol).first()

    bars = list(
        StockEquityCandle.objects.filter(symbol=symbol, interval_minutes=15)
        .order_by("-timestamp")
        .values("timestamp", "open", "high", "low", "close", "volume")[:400]
    )
    bars.reverse()
    price_series = [
        {"t": bar["timestamp"].strftime("%d %b %H:%M"), "c": float(bar["close"] or 0)}
        for bar in bars
    ]

    # The rolling feed is ATM-relative and reaches back 18 months; the ladder feed
    # is a real strike and only exists for live expiries. Show them separately
    # rather than pretending they are the same series.
    rolling = (
        StockOptionCandle.objects.filter(symbol=symbol)
        .exclude(relative_strike__startswith="K")
        .values("relative_strike", "option_type")
        .annotate(
            bars=Count("id"), last=Max("timestamp"),
            avg_oi=Avg("oi"), avg_iv=Avg("implied_volatility"),
        )
        .order_by("option_type", "relative_strike")
    )

    ladder_rows = (
        StockOptionCandle.objects.filter(symbol=symbol, relative_strike__startswith="K")
        .values("strike", "option_type")
        .annotate(bars=Count("id"), last=Max("timestamp"), hi=Max("high"), lo=Min("low"))
        .order_by("strike", "option_type")
    )

    spot = float(bars[-1]["close"]) if bars else 0.0
    ladder = []
    for row in ladder_rows:
        strike = float(row["strike"] or 0)
        hi, lo = float(row["hi"] or 0), float(row["lo"] or 0)
        latest = (
            StockOptionCandle.objects.filter(
                symbol=symbol, strike=row["strike"], option_type=row["option_type"],
                relative_strike__startswith="K",
            )
            .order_by("-timestamp")
            .values("close", "oi", "volume", "timestamp")
            .first()
        ) or {}
        ladder.append({
            "strike": strike,
            "option_type": row["option_type"],
            "moneyness": (strike / spot - 1) * 100 if spot else 0,
            "bars": row["bars"],
            "low": lo,
            "high": hi,
            "range_x": (hi / lo) if lo > 0.05 else 0,
            "close": float(latest.get("close") or 0),
            "oi": latest.get("oi") or 0,
            "volume": latest.get("volume") or 0,
            "as_of": latest.get("timestamp"),
        })
    ladder.sort(key=lambda row: (row["option_type"], row["strike"]))

    trades = list(
        BrokerPnlEntry.objects.filter(underlying=symbol)
        .order_by("-realised_pnl")
        .values("raw_symbol", "broker", "option_type", "strike", "expiry_date",
                "quantity", "buy_value", "sell_value", "realised_pnl")
    )
    for trade in trades:
        trade["pnl_text"] = _rupees(trade["realised_pnl"])

    context = {
        "symbol": symbol,
        "stock": stock,
        "spot": spot,
        "bar_count": StockEquityCandle.objects.filter(symbol=symbol).count(),
        "first_bar": bars[0]["timestamp"] if bars else None,
        "last_bar": bars[-1]["timestamp"] if bars else None,
        "price_json": json.dumps(price_series),
        "rolling": list(rolling),
        "ladder": ladder,
        "ladder_count": len(ladder),
        "trades": trades[:60],
        "trade_count": len(trades),
        "traded_pnl": _rupees(sum(float(t["realised_pnl"] or 0) for t in trades)),
        "title": symbol,
    }
    return render(request, "options_tracker/stock_option_detail.html", context)


# --------------------------------------------------------------------------
# breakeven


def breakeven(request):
    """What the hole actually is, and what it costs to climb out of it.

    The headline number people expect here is "charges ate my account". It did
    not: charges were 0.14% of turnover against a 1.79% gross bleed, so they are
    under a twelfth of the damage. The dashboard says so plainly, because
    budgeting for cheaper brokerage would be solving the wrong problem.
    """
    periods = list(BrokerPeriodSummary.objects.order_by("broker", "period_from"))
    gross = sum(float(p.gross_realised) for p in periods)
    charges = sum(float(p.charges) for p in periods)
    unrealised = sum(float(p.unrealised) for p in periods)
    net = gross - charges
    hole = net + unrealised

    flow = BrokerPnlEntry.objects.aggregate(b=Sum("buy_value"), s=Sum("sell_value"))
    turnover = float(flow["b"] or 0) + float(flow["s"] or 0)
    charge_rate = (charges / turnover) if turnover else 0.0
    gross_edge = (gross / turnover) if turnover else 0.0

    breakdown = defaultdict(float)
    for period in periods:
        for name, amount in (period.charge_breakdown or {}).items():
            breakdown[name.strip()] += float(amount)
    charge_lines = sorted(
        ({"name": name, "amount": amount, "text": _rupees(amount),
          "share": amount / charges * 100 if charges else 0}
         for name, amount in breakdown.items() if abs(amount) > 0.5),
        key=lambda row: -row["amount"],
    )

    # Turnover needed to climb out, at a range of honest net edges. A net edge is
    # what survives after the 0.14% charge drag, so these are gross-of-nothing.
    ladder = []
    for edge_pct in (0.25, 0.5, 1.0, 2.0, 3.0, 5.0):
        needed = abs(hole) / (edge_pct / 100)
        ladder.append({
            "edge": edge_pct,
            "turnover": needed,
            "turnover_text": _rupees(needed),
            "vs_past": needed / turnover if turnover else 0,
            "charges": _rupees(needed * charge_rate),
        })

    # Same hole, expressed as a monthly grind on working capital.
    capital_plans = []
    for capital in (200000, 500000, 1000000, 2500000):
        for monthly_pct in (3, 5, 10):
            monthly = capital * monthly_pct / 100
            capital_plans.append({
                "capital": capital,
                "capital_text": _rupees(capital),
                "monthly_pct": monthly_pct,
                "monthly": monthly,
                "monthly_text": _rupees(monthly),
                "months": abs(hole) / monthly if monthly else 0,
                "years": abs(hole) / monthly / 12 if monthly else 0,
            })

    by_broker = defaultdict(lambda: {"gross": 0.0, "charges": 0.0, "unrealised": 0.0})
    for period in periods:
        entry = by_broker[period.broker]
        entry["gross"] += float(period.gross_realised)
        entry["charges"] += float(period.charges)
        entry["unrealised"] += float(period.unrealised)
    broker_rows = [
        {"broker": broker, "gross": v["gross"], "charges": v["charges"],
         "net": v["gross"] - v["charges"], "unrealised": v["unrealised"],
         "gross_text": _rupees(v["gross"]), "charges_text": _rupees(v["charges"]),
         "net_text": _rupees(v["gross"] - v["charges"])}
        for broker, v in sorted(by_broker.items())
    ]

    worst = list(
        TrackedStock.objects.filter(is_active=True, realised_pnl__lt=0)
        .order_by("realised_pnl")[:12]
    )
    best = list(
        TrackedStock.objects.filter(is_active=True, realised_pnl__gt=0)
        .order_by("-realised_pnl")[:12]
    )
    for stock in worst + best:
        stock.pnl_text = _rupees(stock.realised_pnl)
        stock.turnover_text = _rupees(stock.turnover)

    concentration = sum(float(s.realised_pnl) for s in worst)

    context = {
        "periods": periods,
        "broker_rows": broker_rows,
        "gross": gross, "gross_text": _rupees(gross),
        "charges": charges, "charges_text": _rupees(charges),
        "net": net, "net_text": _rupees(net),
        "unrealised": unrealised, "unrealised_text": _rupees(unrealised),
        "hole": hole, "hole_text": _rupees(abs(hole)),
        "turnover": turnover, "turnover_text": _rupees(turnover),
        "charge_rate_pct": charge_rate * 100,
        "charge_per_lakh": _rupees(charge_rate * 100000),
        "gross_edge_pct": gross_edge * 100,
        "charge_share_of_loss": (charges / abs(gross) * 100) if gross else 0,
        "charge_lines": charge_lines,
        "ladder": ladder,
        "capital_plans": capital_plans,
        "worst": worst,
        "best": best,
        "concentration_text": _rupees(abs(concentration)),
        "concentration_pct": (abs(concentration) / abs(gross) * 100) if gross else 0,
        "contracts": BrokerPnlEntry.objects.count(),
        "title": "Breakeven",
    }
    return render(request, "options_tracker/breakeven.html", context)
