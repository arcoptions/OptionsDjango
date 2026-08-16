from collections import defaultdict
from datetime import date
from math import floor


NIFTY_LOT_SIZE = 65


def estimate_option_charges(entry_price, exit_price, quantity, trade_date=None):
    buy_turnover = entry_price * quantity
    sell_turnover = max(exit_price, 0) * quantity
    turnover = buy_turnover + sell_turnover
    brokerage = 40.0
    transaction_charges = turnover * 0.0003503
    sebi_charges = turnover * 0.000001
    if isinstance(trade_date, str):
        trade_date = date.fromisoformat(trade_date)
    stt_rate = 0.0015 if trade_date and trade_date >= date(2026, 4, 1) else 0.001
    stt = sell_turnover * stt_rate
    stamp_duty = buy_turnover * 0.00003
    gst = (brokerage + transaction_charges + sebi_charges) * 0.18
    return round(
        brokerage + transaction_charges + sebi_charges + stt + stamp_duty + gst,
        2,
    )


def size_trade(
    entry_price,
    unit_stop_risk,
    max_position,
    lot_size=NIFTY_LOT_SIZE,
    policy="MAX_BUDGET",
    risk_cap=None,
    fixed_lots=None,
):
    lot_cost = entry_price * lot_size
    budget_lots = floor(max_position / lot_cost) if lot_cost > 0 else 0
    if policy == "FIXED_LOTS":
        lots = fixed_lots or 0
    elif policy == "ONE_LOT":
        lots = min(budget_lots, 1)
    elif policy == "RISK_CAP":
        lot_risk = unit_stop_risk * lot_size
        risk_lots = floor(risk_cap / lot_risk) if risk_cap and lot_risk > 0 else 0
        lots = min(budget_lots, risk_lots)
    else:
        lots = budget_lots
    return {
        "lots": lots,
        "quantity": lots * lot_size,
        "deployed": round(lots * lot_cost, 2),
        "stop_risk": round(lots * unit_stop_risk * lot_size, 2),
    }


def cash_trade(
    raw_trade,
    max_position,
    lot_size,
    policy,
    risk_cap=None,
    fixed_lots=None,
):
    sizing = size_trade(
        raw_trade["entry"],
        raw_trade["unit_stop_risk"],
        max_position,
        lot_size,
        policy,
        risk_cap,
        fixed_lots,
    )
    if not sizing["quantity"]:
        return None
    gross_pnl = (
        raw_trade["unit_exit"] - raw_trade["entry"]
    ) * sizing["quantity"]
    charges = estimate_option_charges(
        raw_trade["entry"], raw_trade["unit_exit"], sizing["quantity"],
        raw_trade["date"],
    )
    return {
        **raw_trade,
        **sizing,
        "gross_pnl": round(gross_pnl, 2),
        "charges": charges,
        "net_pnl": round(gross_pnl - charges, 2),
    }


def cash_ledger(
    raw_trades,
    max_position,
    lot_size,
    policy,
    risk_cap=None,
    fixed_lots=None,
):
    trades = []
    skipped = []
    for raw_trade in raw_trades:
        trade = cash_trade(
            raw_trade, max_position, lot_size, policy, risk_cap, fixed_lots,
        )
        if trade:
            trades.append(trade)
        else:
            skipped.append(raw_trade)
    return trades, skipped


def cash_metrics(trades, starting_capital, total_signals=None):
    ordered = sorted(trades, key=lambda trade: trade["entry_at"])
    equity = starting_capital
    peak = starting_capital
    maximum_drawdown = 0
    maximum_drawdown_percent = 0
    monthly = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})
    by_strategy = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})
    by_side = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})
    for trade in ordered:
        equity += trade["net_pnl"]
        peak = max(peak, equity)
        drawdown = peak - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        maximum_drawdown_percent = max(
            maximum_drawdown_percent,
            drawdown / peak * 100 if peak else 0,
        )
        month = trade["date"][:7]
        monthly[month]["trades"] += 1
        monthly[month]["net_pnl"] += trade["net_pnl"]
        by_strategy[trade["strategy"]]["trades"] += 1
        by_strategy[trade["strategy"]]["net_pnl"] += trade["net_pnl"]
        by_side[trade["option_type"]]["trades"] += 1
        by_side[trade["option_type"]]["net_pnl"] += trade["net_pnl"]

    net_pnl = sum(trade["net_pnl"] for trade in ordered)
    gross_pnl = sum(trade["gross_pnl"] for trade in ordered)
    winning = [trade for trade in ordered if trade["net_pnl"] > 0]
    losing = [trade for trade in ordered if trade["net_pnl"] <= 0]
    return {
        "signals": total_signals if total_signals is not None else len(ordered),
        "executed_trades": len(ordered),
        "skipped_signals": (
            total_signals - len(ordered)
            if total_signals is not None
            else 0
        ),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "net_win_rate": round(len(winning) / len(ordered) * 100, 1) if ordered else 0,
        "gross_pnl": round(gross_pnl, 2),
        "estimated_charges": round(sum(trade["charges"] for trade in ordered), 2),
        "net_pnl": round(net_pnl, 2),
        "ending_capital": round(starting_capital + net_pnl, 2),
        "return_on_starting_capital_percent": round(net_pnl / starting_capital * 100, 2),
        "average_net_pnl": round(net_pnl / len(ordered), 2) if ordered else 0,
        "maximum_drawdown": round(maximum_drawdown, 2),
        "maximum_drawdown_percent": round(maximum_drawdown_percent, 2),
        "maximum_deployed": max((trade["deployed"] for trade in ordered), default=0),
        "trades_requiring_more_than_starting_capital": sum(
            trade["deployed"] > starting_capital for trade in ordered
        ),
        "maximum_planned_stop_risk": max((trade["stop_risk"] for trade in ordered), default=0),
        "maximum_planned_stop_risk_percent": round(
            max((trade["stop_risk"] for trade in ordered), default=0)
            / starting_capital
            * 100,
            2,
        ) if starting_capital else 0,
        "best_trade": _trade_extreme(ordered, max),
        "worst_trade": _trade_extreme(ordered, min),
        "monthly": {
            month: {
                "trades": values["trades"],
                "net_pnl": round(values["net_pnl"], 2),
            }
            for month, values in sorted(monthly.items())
        },
        "by_strategy": _rounded_groups(by_strategy),
        "by_side": _rounded_groups(by_side),
    }


def _trade_extreme(trades, selector):
    if not trades:
        return None
    trade = selector(trades, key=lambda row: row["net_pnl"])
    return {
        key: trade[key]
        for key in (
            "date", "strategy", "option_type", "strike", "entry",
            "quantity", "deployed", "outcome", "net_pnl",
        )
    }


def _rounded_groups(groups):
    return {
        name: {
            "trades": values["trades"],
            "net_pnl": round(values["net_pnl"], 2),
        }
        for name, values in sorted(groups.items())
    }