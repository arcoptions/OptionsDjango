"""Strength dips: F&O leaders on a short, sharp pullback, as a daily alert.

This is `research/breakout2_portfolio.py` moved into the application so it runs
every evening and is held to account (`research/BREAKOUT_ALERTS_REPORT.md`).

THE RULE.  On a session's close, rank the F&O universe (20-day turnover of at
least Rs 10 cr) on 3, 6 and 12-month return, average the three percentile
ranks, and rank that average again. A LEADER is in the top 20%. A SIGNAL is a
leader whose 2-day RSI closed below 10. Buy at the next open, hold 20 sessions,
exit early on a close 8% below entry. Ten equal slots; when signals outnumber
free slots, the strongest leaders go first.

WHY NOT A BREAKOUT.  Ten years of F&O data found no breakout definition that
beats the same-day universe over 10 or 20 sessions, and buying a leader on its
52-week-high breakout day did measurably worse than buying the same leader on an
arbitrary day. Strength is the signal; a dip is the better moment to act on it.

THE NUMBERS HERE MUST MATCH THE RESEARCH.  Momentum windows, the RSI(2)
smoothing (alpha = 1/2 on close-to-close changes), the turnover floor applied
before ranking, next-open entry, the closing stop and the 0.30% round trip are
copied from `research/breakout2_rank.py` and `breakout2_portfolio.py`. A subtly
different definition would still produce a plausible list, and the expectations
shown beside it would then belong to a different model.

WHY THE BARS ARE BACK-ADJUSTED HERE.  The research used split- and
dividend-adjusted prices; Dhan's daily history is raw. A 1:1 bonus reads as a 50%
crash and would throw a leader out of the ranking for a year. An opening gap of
30% or more below the previous close is treated as a corporate action and every
earlier bar of that stock is rescaled by it. F&O names do not gap that far on
news often enough for the guard to misfire, and when a name does it only
distorts that one stock's own momentum.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .models import StockEquityCandle

DAILY_INTERVAL = 1440

LEADER_CUT = 0.80
RSI_CUT = 10.0
HOLD_SESSIONS = 20
STOP_PCT = 0.08
SLOTS = 10
MIN_TURNOVER_CR = 10.0
ROUND_TRIP = 0.0030
SPLIT_GAP = -0.30
MIN_HISTORY = 64                  # the 3-month return is the shortest window that ranks
LOOKBACK_DAYS = 420               # calendar days of bars the features need

# Measured in research/breakout2_rank.py, 194 F&O names, Jan 2016 to Sep 2026,
# next-open entry, 20-session hold, 0.30% round trip.
EXP_NET_MEAN = 2.5                # % per trade, both periods (2.61 / 2.47)
EXP_WIN = 57.0                    # % of trades positive (59.0 / 56.6)
EXP_EXCESS = 1.0                  # % over the same-day F&O average (0.98 / 1.13)
EXP_ANYDAY_EXCESS = 0.65          # the same leaders bought on any day (0.57 / 0.74)
BOOK_CAGR, RANDOM_LEADER_CAGR, RANDOM_STOCK_CAGR = 28.7, 23.3, 14.9


def daily_bars(since: dt.date | None = None) -> pd.DataFrame:
    """Dhan daily candles as one row per stock-session, IST dates."""
    rows = StockEquityCandle.objects.filter(interval_minutes=DAILY_INTERVAL)
    if since is not None:
        rows = rows.filter(timestamp__date__gte=since)
    frame = pd.DataFrame(list(rows.values_list(
        "symbol", "timestamp", "open", "high", "low", "close", "volume")),
        columns=["symbol", "ts", "o", "h", "l", "c", "v"])
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "day", "o", "h", "l", "c", "v"])
    frame["day"] = (pd.to_datetime(frame["ts"], utc=True)
                    .dt.tz_convert("Asia/Kolkata").dt.date)
    for col in ("o", "h", "l", "c", "v"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["o", "c"]).drop(columns="ts")
    frame = frame.drop_duplicates(["symbol", "day"], keep="last")
    return frame.sort_values(["symbol", "day"]).reset_index(drop=True)


def back_adjust(bars: pd.DataFrame) -> pd.DataFrame:
    """Rescale history across corporate-action gaps, per stock. Volume is left raw.

    Adjusted prices are for ranking and RSI only. Entries, stops and exits are
    always taken from the raw bar of the day they happen on.
    """
    out = []
    for _, f in bars.groupby("symbol", sort=False):
        f = f.sort_values("day").reset_index(drop=True).copy()
        gap = f["o"] / f["c"].shift(1) - 1
        factor = pd.Series(1.0, index=f.index)
        for i in f.index[gap <= SPLIT_GAP]:
            factor.iloc[:i] *= f.at[i, "o"] / f.at[i - 1, "c"]
        for col in ("o", "h", "l", "c"):
            f[f"adj_{col}"] = f[col] * factor
        out.append(f)
    return pd.concat(out, ignore_index=True) if out else bars.assign(
        adj_o=[], adj_h=[], adj_l=[], adj_c=[])


def rsi2(close: pd.Series) -> pd.Series:
    change = close.diff()
    up = change.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    down = (-change).clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    return 100 - 100 / (1 + up / down)


def features(bars: pd.DataFrame) -> pd.DataFrame:
    """Per stock-session: momentum, RSI(2), turnover, then same-day ranks."""
    adj = back_adjust(bars)
    parts = []
    for _, f in adj.groupby("symbol", sort=False):
        if len(f) < MIN_HISTORY:
            continue
        c = f["adj_c"]
        f = f.copy()
        f["ret63"] = c / c.shift(63) - 1
        f["ret126"] = c / c.shift(126) - 1
        f["ret252"] = c / c.shift(252) - 1
        f["rsi2"] = rsi2(c)
        f["ret5"] = c / c.shift(5) - 1
        f["turnover_cr"] = (f["c"] * f["v"]).rolling(20).mean() / 1e7
        parts.append(f)
    if not parts:
        return pd.DataFrame()
    frame = pd.concat(parts, ignore_index=True)
    frame = frame[frame["turnover_cr"] >= MIN_TURNOVER_CR].copy()
    by_day = frame.groupby("day")
    for col in ("ret63", "ret126", "ret252"):
        frame[f"r_{col}"] = by_day[col].rank(pct=True)
    frame["composite"] = frame[["r_ret63", "r_ret126", "r_ret252"]].mean(axis=1)
    frame["strength"] = frame.groupby("day")["composite"].rank(pct=True)
    return frame.reset_index(drop=True)


def signals(frame: pd.DataFrame, day) -> pd.DataFrame:
    """Leaders whose RSI(2) closed below the cut on `day`, strongest first."""
    today = frame[frame["day"] == day]
    hit = today[(today["strength"] >= LEADER_CUT) & (today["rsi2"] < RSI_CUT)]
    return hit.sort_values("strength", ascending=False).reset_index(drop=True)


def leaders(frame: pd.DataFrame, day) -> int:
    today = frame[frame["day"] == day]
    return int((today["strength"] >= LEADER_CUT).sum())


def stop_price(entry: float) -> float:
    return round(entry * (1 - STOP_PCT), 2)


def _dec(value, places):
    from decimal import Decimal
    if value is None or pd.isna(value):
        return None
    return Decimal(str(round(float(value), places)))


def advance(source, day, bars_today: dict, frame: pd.DataFrame | None = None) -> dict:
    """Move one book forward by one session. Safe to run twice for the same day.

    `bars_today` maps symbol -> (open, high, low, close) as RAW prices for `day`.
    Order matches the research simulation: yesterday's picks fill at today's
    open, every held trade is marked on today's close (stop, then the 20-session
    clock), and only then are today's signals ranked into whatever slots remain.
    """
    from .models import SwingSignal, SwingStatus

    filled, exited = [], []
    for row in SwingSignal.objects.filter(source=source, status=SwingStatus.PENDING, day__lt=day):
        bar = bars_today.get(row.symbol)
        if bar is None:
            continue                                  # halted: fill on its next session
        row.status, row.entry_day = SwingStatus.OPEN, day
        row.entry_price = _dec(bar[0], 2)
        row.stop_price = _dec(stop_price(bar[0]), 2)
        row.sessions_held, row.last_close = 0, _dec(bar[0], 2)
        row.save()
        filled.append(row)

    for row in SwingSignal.objects.filter(source=source, status=SwingStatus.OPEN):
        bar = bars_today.get(row.symbol)
        if bar is None or (row.marked_on and row.marked_on >= day):
            continue
        o, _, _, c = (float(x) for x in bar)
        prev = float(row.last_close) if row.last_close is not None else None
        if prev and row.entry_day != day and o / prev - 1 <= SPLIT_GAP:
            # Bonus or split while held: rescale the trade, not the market.
            factor = o / prev
            row.entry_price = _dec(float(row.entry_price) * factor, 2)
            row.stop_price = _dec(float(row.stop_price) * factor, 2)
        if row.entry_day != day:
            row.sessions_held += 1
        row.last_close, row.marked_on = _dec(c, 2), day
        reason = ""
        if c <= float(row.stop_price):
            reason = "STOP"
        elif row.sessions_held >= HOLD_SESSIONS:
            reason = "TIME"
        if reason:
            row.status, row.exit_day, row.exit_reason = SwingStatus.CLOSED, day, reason
            row.exit_price = _dec(c, 2)
            row.net_return = _dec(c / float(row.entry_price) - 1 - ROUND_TRIP, 4)
            exited.append(row)
        row.save()

    new, taken = [], []
    if frame is not None and not frame.empty:
        held = set(SwingSignal.objects.filter(
            source=source, status__in=[SwingStatus.OPEN, SwingStatus.PENDING]).values_list("symbol", flat=True))
        free = SLOTS - len(held)
        for rank, hit in enumerate(signals(frame, day).itertuples(index=False), start=1):
            row, created = SwingSignal.objects.get_or_create(
                day=day, symbol=hit.symbol, source=source,
                defaults={
                    "rank": rank, "close": _dec(hit.c, 2), "strength": _dec(hit.strength, 4),
                    "rsi2": _dec(hit.rsi2, 2), "ret63": _dec(hit.ret63, 4), "ret252": _dec(hit.ret252, 4),
                    "ret5": _dec(hit.ret5, 4), "turnover_cr": _dec(hit.turnover_cr, 2),
                })
            if created and free > 0 and hit.symbol not in held:
                row.status = SwingStatus.PENDING
                row.save(update_fields=["status"])
                held.add(hit.symbol)
                free -= 1
            new.append(row)
            if row.status == SwingStatus.PENDING:
                taken.append(row)
    return {"filled": filled, "exited": exited, "signals": new, "taken": taken}


def format_alert(day, result: dict, open_rows, leader_count: int) -> str:
    """The Telegram message. HTML parse mode; plain enough to read in a notification."""
    lines = [f"<b>Strength dips · {day:%a %d %b %Y}</b>"]
    taken = result["taken"]
    watch = [r for r in result["signals"] if r not in taken]
    if taken:
        lines.append("\n<b>BUY at tomorrow's open</b>")
        for r in taken:
            lines.append(f"• <b>{r.symbol}</b> close ₹{r.close} · RSI2 {float(r.rsi2):.1f} · "
                         f"strength {float(r.strength) * 100:.0f} · 3m {float(r.ret63 or 0) * 100:+.0f}% · "
                         f"stop 8% below fill")
    else:
        lines.append("\nNo new entries.")
    if watch:
        lines.append("\n<i>Also signalled, no free slot:</i> " + ", ".join(r.symbol for r in watch))
    if result["exited"]:
        lines.append("\n<b>Exited on today's close</b>")
        for r in result["exited"]:
            lines.append(f"• {r.symbol} {r.exit_reason.lower()} · {float(r.net_return) * 100:+.1f}% net "
                         f"after {r.sessions_held} sessions")
    held = [r for r in open_rows if r.entry_price]
    buying = len(open_rows) - len(held)
    if open_rows:
        lines.append(f"\n<b>Book</b> {len(open_rows)}/{SLOTS} slots"
                     + (f" ({buying} buying at the open)" if buying else ""))
        for r in held:
            move = float(r.last_close) / float(r.entry_price) - 1 if r.last_close else 0.0
            lines.append(f"• {r.symbol} {move * 100:+.1f}% · day {r.sessions_held}/{HOLD_SESSIONS} · stop ₹{r.stop_price}")
    lines.append(f"\n{leader_count} leaders today. Rule: top-20% strength & RSI(2) &lt; 10, "
                 f"hold {HOLD_SESSIONS} sessions, 8% closing stop.")
    return "\n".join(lines)
