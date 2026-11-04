"""Strength dips: tonight's signals, the paper book, and its record.

LIVE and BACKFILL are two separate ten-slot books and are never added together,
for the same reason as the call shortlist: the backfill was reconstructed with
the answer available, and only the live book is evidence about the future.
"""
from __future__ import annotations

import datetime as dt
from statistics import median

from django.shortcuts import render

from . import swing as W
from . import telegram_bot
from .models import PickSource, SwingSignal, SwingStatus

RECENT_LIMIT = 40


def _pct(value):
    return float(value) * 100 if value is not None else None


def _record(source):
    closed = SwingSignal.objects.filter(source=source, status=SwingStatus.CLOSED)
    rets = [float(r) for r in closed.values_list("net_return", flat=True) if r is not None]
    if not rets:
        return None
    wins = [r for r in rets if r > 0]
    first = closed.order_by("entry_day").values_list("entry_day", flat=True).first()
    last = closed.order_by("-exit_day").values_list("exit_day", flat=True).first()
    return {
        "trades": len(rets),
        "mean": sum(rets) / len(rets) * 100,
        "median": median(rets) * 100,
        "win": len(wins) / len(rets) * 100,
        "stops": closed.filter(exit_reason="STOP").count(),
        "compounded": _compound(rets),
        "first": first,
        "last": last,
    }


def _compound(rets):
    """Growth of the whole book with each trade sized at one tenth of it."""
    equity = 1.0
    for r in rets:
        equity *= 1 + r / W.SLOTS
    return (equity - 1) * 100


def _book(source):
    rows = []
    for r in SwingSignal.objects.filter(source=source, status__in=[SwingStatus.OPEN, SwingStatus.PENDING]) \
            .order_by("entry_day", "symbol"):
        move = None
        if r.entry_price and r.last_close:
            move = (float(r.last_close) / float(r.entry_price) - 1) * 100
        rows.append({"row": r, "move": move,
                     "to_stop": (float(r.last_close) / float(r.stop_price) - 1) * 100
                     if r.stop_price and r.last_close else None})
    return rows


def swing_options(request):
    """The option version of the strength-dip book: tonight's contracts, the book, the plan."""
    from . import swing_options as WO
    from .models import SwingOptionTrade

    try:
        capital = max(float(request.GET.get("capital") or WO.DEFAULT_CAPITAL), 10_000)
    except ValueError:
        capital = WO.DEFAULT_CAPITAL
    latest_day = SwingOptionTrade.objects.order_by("-signal__day").values_list("signal__day", flat=True).first()

    def decorate(t):
        lots, lot_pct = WO.lots_for(t.signal_premium or t.entry_premium, t.lot_size, capital)
        move = None
        if t.entry_premium and (t.exit_premium or t.last_premium):
            move = (float(t.exit_premium or t.last_premium) / float(t.entry_premium) - 1) * 100
        lot_cost = float(t.signal_premium or t.entry_premium or 0) * t.lot_size or None
        exit_by = None
        if t.entry_day and t.status == SwingStatus.OPEN:
            import numpy as np
            exit_by = np.busday_offset(t.entry_day, WO.HOLD, roll="forward").astype(dt.date)
            if t.expiry:
                exit_by = min(exit_by, np.busday_offset(t.expiry, -WO.EXPIRY_BUFFER, roll="backward").astype(dt.date))
        return {"row": t, "lots": lots, "lot_pct": lot_pct, "lot_cost": lot_cost, "move": move,
                "net": _pct(t.net_return), "exit_by": exit_by}

    tonight = [decorate(t) for t in SwingOptionTrade.objects.filter(signal__day=latest_day)
               .select_related("signal").order_by("signal__rank")] if latest_day else []
    held = [decorate(t) for t in SwingOptionTrade.objects.filter(status=SwingStatus.OPEN)
            .select_related("signal").order_by("entry_day")]
    closed_qs = SwingOptionTrade.objects.filter(status=SwingStatus.CLOSED, net_return__isnull=False)
    rets = [float(r) for r in closed_qs.values_list("net_return", flat=True)]
    record = None
    if rets:
        record = {"trades": len(rets), "mean": sum(rets) / len(rets) * 100, "median": median(rets) * 100,
                  "win": sum(r > 0 for r in rets) / len(rets) * 100,
                  "lost_half": sum(r < -0.5 for r in rets) / len(rets) * 100}
    closed = [decorate(t) for t in closed_qs.select_related("signal").order_by("-exit_day")[:RECENT_LIMIT]]
    age = (dt.date.today() - latest_day).days if latest_day else None
    return render(request, "options_tracker/swing_options.html", {
        "latest_day": latest_day, "stale": age is not None and age > 4, "age_days": age,
        "tonight": tonight, "held": held, "closed": closed, "record": record,
        "capital": capital, "risk_pct": WO.RISK_PCT * 100, "risk_rupees": capital * WO.RISK_PCT,
        "plan": {"hold": WO.HOLD, "min_dte": WO.MIN_DTE, "buffer": WO.EXPIRY_BUFFER, "cost": WO.ROUND_TRIP * 100},
        "exp": {"mean": WO.EXP_MEAN, "median": WO.EXP_MEDIAN, "win": WO.EXP_WIN, "lost_half": WO.EXP_LOST_HALF,
                "control": WO.CONTROL_MEAN, "cagr": WO.BOOK_CAGR_1PCT, "dd": WO.BOOK_DD_1PCT,
                "control_cagr": WO.CONTROL_BOOK_CAGR_1PCT},
        "plans": WO.PLANS,
    })


def swing_alerts(request):
    live = SwingSignal.objects.filter(source=PickSource.LIVE)
    latest_day = live.order_by("-day").values_list("day", flat=True).first()
    source = PickSource.LIVE
    if latest_day is None:
        latest_day = SwingSignal.objects.filter(source=PickSource.BACKFILL).order_by("-day") \
            .values_list("day", flat=True).first()
        source = PickSource.BACKFILL
    today = []
    if latest_day is not None:
        for r in SwingSignal.objects.filter(source=source, day=latest_day).order_by("rank"):
            today.append({"row": r, "strength": _pct(r.strength), "ret63": _pct(r.ret63),
                          "ret252": _pct(r.ret252), "ret5": _pct(r.ret5)})
    recent = [{"row": r, "net": _pct(r.net_return)} for r in SwingSignal.objects.filter(
        source=PickSource.LIVE, status=SwingStatus.CLOSED).order_by("-exit_day", "symbol")[:RECENT_LIMIT]]
    recent_backfill = [{"row": r, "net": _pct(r.net_return)} for r in SwingSignal.objects.filter(
        source=PickSource.BACKFILL, status=SwingStatus.CLOSED).order_by("-exit_day", "symbol")[:RECENT_LIMIT]]
    age = (dt.date.today() - latest_day).days if latest_day else None
    return render(request, "options_tracker/swing_alerts.html", {
        "latest_day": latest_day,
        "latest_is_live": source == PickSource.LIVE,
        "age_days": age,
        "stale": age is not None and age > 4,
        "today": today,
        "book": _book(PickSource.LIVE),
        "backfill_book": _book(PickSource.BACKFILL) if source == PickSource.BACKFILL else [],
        "live": _record(PickSource.LIVE),
        "backtest": _record(PickSource.BACKFILL),
        "recent": recent,
        "recent_backfill": recent_backfill,
        "bot_ready": telegram_bot.configured(),
        "rule": {"leader": int(W.LEADER_CUT * 100), "rsi": int(W.RSI_CUT), "hold": W.HOLD_SESSIONS,
                 "stop": int(W.STOP_PCT * 100), "slots": W.SLOTS, "turnover": int(W.MIN_TURNOVER_CR),
                 "cost": W.ROUND_TRIP * 100},
        "exp": {"mean": W.EXP_NET_MEAN, "win": W.EXP_WIN, "excess": W.EXP_EXCESS,
                "anyday": W.EXP_ANYDAY_EXCESS, "cagr": W.BOOK_CAGR, "rand_leader": W.RANDOM_LEADER_CAGR,
                "rand_stock": W.RANDOM_STOCK_CAGR},
    })
