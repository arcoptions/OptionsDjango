"""Everything the dashboard displays, assembled from what the engine recorded.

The engine already writes its whole life into two places: `TradeExecution` for
positions and `DhanOrderEvent` for every quote, fill, trail and exit. Nothing new
is stored for the dashboard's benefit and there is no migration -- this module
only reads those back and does the arithmetic.

One thing is worth stating plainly, because it decides how the Trades tab reads.
In observe-only mode `open_position` returns before placing anything, so no
`TradeExecution` exists and a day on which the strategy fired three times looks
identical to a day it stayed silent. That would make the dashboard actively
misleading during exactly the period it is most needed, so observed signals are
read out of the `DRY_RUN_ENTRY` events and shown alongside real trades, clearly
marked and never counted in the P&L.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

from django.utils import timezone

from .capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from .live_config import live_settings, live_strategy_config, panel_rows, risk_surface
from .models import AppSetting, DhanOrderEvent, TradeExecution, TradeState
from .nifty_trail_strategy import sized_ledger

STATUS_KEY = "nifty_live_status"
STATE_KEY = "nifty_live_state"

# A tick runs about every 15 seconds. Past this the engine is not merely late,
# it has stopped, and the dashboard should say so rather than show a stale
# position as if it were current.
STALE_AFTER_SECONDS = 120


# Notes the operator must not have to go looking for. "Why it is not trading"
# renders rejections *or* notes, so a note about sixteen unevaluated bars would
# have been hidden behind "no breakout on the 12:37 bar" -- which is exactly how
# the outage of 19 August stayed invisible while it was happening.
#
# The three read very differently and should not share a colour. A feed gap is
# the engine blind; a missed signal is the gap costing a trade; a recovery is the
# gap being paid back. Only the first is a fault.
ALERT_LEVELS = (
    ("FEED GAP", "error"),
    ("MISSED:", "warning"),
    ("RECOVERED:", "success"),
)


def _alerts(notes):
    """Gap notes lifted out of the general stream, each with its severity."""
    alerts = []
    for note in notes:
        for prefix, level in ALERT_LEVELS:
            if note.startswith(prefix):
                alerts.append({"level": level, "text": note})
                break
    return alerts


def _load(key):
    raw = AppSetting.objects.filter(key=key).values_list("value", flat=True).first()
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def engine_snapshot(now=None):
    """Is the engine alive, what is it doing, and what is it holding?"""
    now = now or timezone.localtime()
    status = _load(STATUS_KEY)
    state = _load(STATE_KEY)
    config = live_strategy_config()

    age = None
    if status.get("at"):
        try:
            age = (now - datetime.fromisoformat(status["at"])).total_seconds()
        except (TypeError, ValueError):
            age = None

    position = status.get("position") or state.get("position")
    if position:
        position = dict(position)
        entry = position.get("fill_price") or position.get("entry") or 0
        high_water = position.get("high_water") or entry
        unit_risk = entry - position.get("initial_stop", entry)
        position["open_r"] = (
            round((high_water - entry) / unit_risk, 2) if unit_risk > 0 else 0
        )
        position["locked_r"] = (
            round((position.get("stop", 0) - entry) / unit_risk, 2) if unit_risk > 0 else 0
        )
        position["deployed"] = round(entry * position.get("quantity", 0), 2)
        position["at_risk"] = round(
            max(entry - position.get("stop", 0), 0) * position.get("quantity", 0), 2
        )

    return {
        "state": status.get("state", "UNKNOWN"),
        "dry_run": bool(status.get("dry_run", True)),
        "at": status.get("at"),
        "age_seconds": round(age) if age is not None else None,
        "stale": age is None or age > STALE_AFTER_SECONDS,
        "notes": status.get("notes") or [],
        "alerts": _alerts(status.get("notes") or []),
        "rejections": status.get("rejections") or [],
        "session": status.get("session") or {},
        "error": status.get("error"),
        "position": position,
        "trades_today": int(state.get("trades_today") or 0),
        "realized_r": float(state.get("realized_r") or 0),
        "max_trades_per_day": config.max_trades_per_day,
        "daily_loss_limit_r": config.daily_loss_limit_r,
    }


def _events_by_order():
    index = defaultdict(dict)
    for event in DhanOrderEvent.objects.exclude(order_id="").only(
        "order_id", "status", "payload_json", "created_at",
    ):
        index[event.order_id][event.status] = event.payload_json
    return index


def trade_rows():
    """Every trade the engine has taken, newest first, with observed signals."""
    events = _events_by_order()
    rows = []

    for execution in TradeExecution.objects.select_related("signal").filter(
        signal__source_type="ENGINE",
    ):
        order_events = events.get(execution.dhan_order_id, {})
        exit_event = order_events.get("EXIT") or {}
        fill_event = order_events.get("FILL") or {}
        try:
            journal = json.loads(execution.journal_reason or "{}")
        except (TypeError, ValueError):
            journal = {}

        opened = timezone.localtime(execution.opened_at)
        entry = float(fill_event.get("filled_at") or execution.entry_price or 0)
        limit = float(execution.entry_price or 0)
        quantity = execution.quantity
        exit_price = exit_event.get("exit_price")
        closed = execution.state == TradeState.CLOSED and exit_price is not None

        row = {
            "kind": "TRADE",
            "date": opened.date().isoformat(),
            "opened_at": opened,
            "closed_at": timezone.localtime(execution.closed_at) if execution.closed_at else None,
            "symbol": execution.signal.option_symbol,
            "option_type": execution.signal.direction,
            "order_id": execution.dhan_order_id,
            "limit": round(limit, 2),
            "entry": round(entry, 2),
            "slippage": round(entry - limit, 2) if entry and limit else None,
            "quantity": quantity,
            "lots": round(quantity / NIFTY_LOT_SIZE, 2) if quantity else 0,
            "stop": float(execution.stop_loss or 0),
            "deployed": round(entry * quantity, 2),
            "state": execution.state,
            "outcome": exit_event.get("reason") or ("OPEN" if not closed else "CLOSED"),
            "quote_at_signal": journal.get("quote_at_signal") or {},
            "realized_r": exit_event.get("realized_r"),
        }
        if closed:
            exit_price = float(exit_price)
            gross = (exit_price - entry) * quantity
            charges = estimate_option_charges(entry, exit_price, quantity, row["date"])
            row.update({
                "exit": round(exit_price, 2),
                "gross_pnl": round(gross, 2),
                "charges": charges,
                "net_pnl": round(gross - charges, 2),
            })
        rows.append(row)

    # Observed signals: the engine found a trade and was not allowed to take it.
    for event in DhanOrderEvent.objects.filter(status="DRY_RUN_ENTRY").only(
        "payload_json", "created_at",
    ):
        payload = event.payload_json or {}
        at = timezone.localtime(event.created_at)
        rows.append({
            "kind": "OBSERVED",
            "date": at.date().isoformat(),
            "opened_at": at,
            "closed_at": None,
            "symbol": payload.get("option", "-"),
            "option_type": (payload.get("option", "") or "").split()[-1],
            "order_id": "",
            "limit": payload.get("entry_limit"),
            "entry": payload.get("entry_limit"),
            "slippage": None,
            "quantity": payload.get("quantity") or 0,
            "lots": payload.get("lots") or 0,
            "stop": payload.get("stop"),
            "deployed": round(
                float(payload.get("entry_limit") or 0) * float(payload.get("quantity") or 0), 2
            ),
            "state": "OBSERVED",
            "outcome": "NOT PLACED",
            "quote_at_signal": payload.get("quote_at_signal") or {},
            "realized_r": None,
        })

    rows.sort(key=lambda row: row["opened_at"], reverse=True)
    return rows


def performance(rows, capital=None):
    """Realised results. Observed signals are excluded -- they made no money."""
    capital = capital or float(live_settings()["capital"])
    closed = [row for row in rows if row.get("net_pnl") is not None]
    closed.sort(key=lambda row: row["opened_at"])

    equity = capital
    peak = capital
    drawdown = 0.0
    curve = [{"at": None, "equity": round(capital, 2), "label": "start"}]
    streak = worst_streak = 0
    for row in closed:
        equity += row["net_pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if row["net_pnl"] <= 0 else 0
        worst_streak = max(worst_streak, streak)
        curve.append({
            "at": row["opened_at"].isoformat(),
            "equity": round(equity, 2),
            "label": f"{row['symbol']} {row['net_pnl']:+,.0f}",
        })

    wins = [row for row in closed if row["net_pnl"] > 0]
    net = sum(row["net_pnl"] for row in closed)
    observed = [row for row in rows if row["kind"] == "OBSERVED"]
    open_rows = [row for row in rows if row["state"] == TradeState.OPEN]
    fills = [row["slippage"] for row in closed if row.get("slippage") is not None]

    return {
        "trades": len(closed),
        "open_trades": len(open_rows),
        "observed_signals": len(observed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "gross_pnl": round(sum(row["gross_pnl"] for row in closed), 2),
        "charges": round(sum(row["charges"] for row in closed), 2),
        "net_pnl": round(net, 2),
        "starting_capital": round(capital, 2),
        "ending_capital": round(capital + net, 2),
        "return_percent": round(net / capital * 100, 2) if capital else 0.0,
        "max_drawdown": round(drawdown, 2),
        "max_drawdown_percent": round(drawdown / capital * 100, 2) if capital else 0.0,
        "average_net_pnl": round(net / len(closed), 2) if closed else 0.0,
        "best_trade": round(max((row["net_pnl"] for row in closed), default=0), 2),
        "worst_trade": round(min((row["net_pnl"] for row in closed), default=0), 2),
        "longest_losing_streak": worst_streak,
        "total_r": round(sum(row["realized_r"] or 0 for row in closed), 2),
        # The number day one exists to buy: modelled fills against real ones.
        "average_slippage": round(sum(fills) / len(fills), 2) if fills else None,
        "fills_measured": len(fills),
        "curve": curve,
    }


def by_month(rows):
    groups = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "wins": 0})
    for row in rows:
        if row.get("net_pnl") is None:
            continue
        bucket = groups[row["date"][:7]]
        bucket["trades"] += 1
        bucket["net_pnl"] += row["net_pnl"]
        bucket["wins"] += 1 if row["net_pnl"] > 0 else 0
    return [
        {
            "month": month,
            "trades": values["trades"],
            "net_pnl": round(values["net_pnl"], 2),
            "win_rate": round(values["wins"] / values["trades"] * 100, 1),
        }
        for month, values in sorted(groups.items(), reverse=True)
    ]


# --------------------------------------------------------------------------- #
# What a settings change would have done
# --------------------------------------------------------------------------- #

def sizing_preview(settings):
    """Replay the 246-session trade list at a given sizing. Exact, not modelled.

    Capital, risk fraction, cash fraction and lot cap do not change which trades
    the strategy takes -- only how many lots each one gets -- so the historical
    result at any sizing is a direct replay of the same trades, not an estimate.
    That is why these four controls can show a real answer the instant they move.
    """
    surface = risk_surface()
    trades = surface.get("trades") or []
    if not trades:
        return None

    capital = float(settings["capital"])
    ledger, skipped, drawdown = sized_ledger(
        trades,
        starting_capital=capital,
        risk_per_trade=float(settings["risk_per_trade"]),
        max_cash_fraction=float(settings["max_cash_fraction"]),
    )
    cap = int(settings.get("fixed_lots") or 0)
    if cap:
        # Re-run the ledger with every position held to the cap. Compounding
        # means this cannot be scaled from the uncapped result.
        ledger, skipped, drawdown = _capped_ledger(trades, capital, settings, cap)

    if not ledger:
        # Every signal sized to zero lots. Real, and the honest answer to a
        # capital or risk setting too small to trade -- so it returns the same
        # shape as a normal result rather than a stub the page has to special-case.
        return {
            "trades": 0, "skipped": len(skipped), "net_pnl": 0.0, "win_rate": 0.0,
            "max_drawdown": 0.0, "max_drawdown_percent": 0.0, "return_percent": 0.0,
            "ending_capital": round(capital, 2), "max_deployed": 0.0,
            "max_stop_risk": 0.0, "average_lots": 0.0,
            "sessions": surface.get("sessions"), "no_trades": True,
        }
    net = sum(row["net_pnl"] for row in ledger)
    wins = [row for row in ledger if row["net_pnl"] > 0]
    return {
        "trades": len(ledger),
        "skipped": len(skipped),
        "net_pnl": round(net, 2),
        "win_rate": round(len(wins) / len(ledger) * 100, 1),
        "max_drawdown": drawdown,
        "max_drawdown_percent": round(drawdown / capital * 100, 2) if capital else 0.0,
        "return_percent": round(net / capital * 100, 2) if capital else 0.0,
        "ending_capital": round(capital + net, 2),
        "max_deployed": max(row["deployed"] for row in ledger),
        "max_stop_risk": max(row["stop_risk"] for row in ledger),
        "average_lots": round(sum(row["lots"] for row in ledger) / len(ledger), 2),
        "sessions": surface.get("sessions"),
    }


def _capped_ledger(trades, capital, settings, cap):
    """`sized_ledger` with a hard lot ceiling, compounding as it goes."""
    from math import floor

    equity = peak = capital
    drawdown = 0.0
    ledger, skipped = [], []
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry = trade["entry"]
        unit_risk = entry - trade["stop_loss"]
        if unit_risk <= 0:
            skipped.append(trade)
            continue
        risk_lots = floor(equity * float(settings["risk_per_trade"]) / (unit_risk * NIFTY_LOT_SIZE))
        cash_lots = floor(equity * float(settings["max_cash_fraction"]) / (entry * NIFTY_LOT_SIZE))
        lots = min(max(0, min(risk_lots, cash_lots)), cap)
        if not lots:
            skipped.append(trade)
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = entry + trade["realized_r"] * unit_risk
        charges = estimate_option_charges(entry, max(exit_price, 0), quantity, trade["date"])
        net = (exit_price - entry) * quantity - charges
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        ledger.append({
            **trade, "lots": lots, "quantity": quantity,
            "deployed": round(entry * quantity, 2),
            "stop_risk": round(unit_risk * quantity, 2),
            "net_pnl": round(net, 2), "equity": round(equity, 2),
        })
    return ledger, skipped, round(drawdown, 2)


def strategy_effect(key, value):
    """The measured sweep around a strategy setting, for the panel to draw."""
    surface = risk_surface().get("surface", {}).get(key)
    if not surface:
        return None
    points = surface["points"]
    nearest = min(points, key=lambda point: abs(float(point["value"]) - float(value)))
    return {
        "points": points,
        "nearest": nearest,
        "exact": abs(float(nearest["value"]) - float(value)) < 1e-9,
        "shipped": surface["shipped"],
        "sessions": risk_surface().get("sessions"),
    }


def recent_events(limit=60):
    """The engine's own log, newest first, for the activity feed."""
    rows = []
    for event in DhanOrderEvent.objects.all()[:limit]:
        rows.append({
            "at": timezone.localtime(event.created_at),
            "status": event.status,
            "order_id": event.order_id,
            "payload": event.payload_json,
        })
    return rows


def today_summary(rows, now=None):
    now = now or timezone.localtime()
    today = now.date().isoformat()
    todays = [row for row in rows if row["date"] == today]
    closed = [row for row in todays if row.get("net_pnl") is not None]
    return {
        "trades": len([row for row in todays if row["kind"] == "TRADE"]),
        "observed": len([row for row in todays if row["kind"] == "OBSERVED"]),
        "net_pnl": round(sum(row["net_pnl"] for row in closed), 2),
        "rows": todays,
    }


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #

CURVE_WIDTH = 720
CURVE_HEIGHT = 180


def curve_svg(curve):
    """An equity line as SVG points. Django templates cannot do this arithmetic."""
    if len(curve) < 2:
        return None
    values = [point["equity"] for point in curve]
    low, high = min(values), max(values)
    span = high - low or 1
    step = CURVE_WIDTH / (len(curve) - 1)
    points = " ".join(
        f"{index * step:.1f},{CURVE_HEIGHT - (value - low) / span * (CURVE_HEIGHT - 10) - 5:.1f}"
        for index, value in enumerate(values)
    )
    return {
        "points": points,
        "area": f"0,{CURVE_HEIGHT} {points} {CURVE_WIDTH},{CURVE_HEIGHT}",
        "width": CURVE_WIDTH,
        "height": CURVE_HEIGHT,
        "low": round(low, 2),
        "high": round(high, 2),
        "last": round(values[-1], 2),
        "up": values[-1] >= values[0],
    }


def risk_panel():
    """`panel_rows` with the bar widths the sweep chart needs."""
    rows = panel_rows()
    for row in rows:
        points = row.get("points") or []
        scale = max((abs(point["net_pnl"]) for point in points), default=0) or 1
        best = max((point["net_pnl"] for point in points), default=0)
        row["points"] = [
            {
                **point,
                "display": point["value"] * 100 if row.get("percent") else point["value"],
                "bar": round(abs(point["net_pnl"]) / scale * 100, 1),
                "positive": point["net_pnl"] >= 0,
                "best": point["net_pnl"] == best,
                "current": abs(float(point["value"]) - float(row["value"])) < 1e-9,
            }
            for point in points
        ]
    return rows

