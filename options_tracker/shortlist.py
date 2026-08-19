"""The daily call shortlist: which stocks may travel 5%+ in the coming week.

This is `research/call_shortlist.py` moved into the application so it can run
on a schedule and be held to account. The research script reads a parquet file
built once from history; this reads the live candle table, so the list moves
with the market instead of re-serving a frozen snapshot.

WHAT THE MODEL CLAIMS, AND WHAT IT DOES NOT.  It claims these names are far
more likely than average to make a 5% move -- 73.0% against a 41.3% base rate --
and that roughly half of those moves are up.  It does NOT claim to know which
way any individual name goes.  Direction was tested exhaustively and no rule
survived pricing on real options, so the list is a watchlist and the edge, if
there is one, is the user's chart read on top of it.

WHY THE THRESHOLD IS ABSOLUTE AND NOT A RANKING.  Taking the top decile by ATR
each day would keep the list a constant size, and measured it is strictly
worse: the same ~18 names a day at a 35.7% up-rate against 48.4% for the fixed
cut, and in a calm market it hands over 19 names at 27.8% while the fixed cut
correctly hands over 5.6 at 39.3%.  What matters is how far a stock actually
moves, not how it ranks among stocks that are all still.  So a quiet tape
SHOULD produce a short list, and a two-name day is the model working.

THE NUMBERS HERE MUST MATCH THE RESEARCH.  Every definition below -- true range,
the shifted 20-day window, the median rather than mean turnover, the five-session
horizon, the corporate-action guard -- is copied from
`research/premove_features.py` rather than re-derived.  A subtly different ATR
would produce a different list while still looking plausible, and the measured
expectations printed beside the list would then belong to a different model.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .models import StockEquityCandle

# The model, in three numbers. ATR is the whole signal; the other two only
# remove names that cannot be traded cleanly.
ATR_CUT = 0.040
MIN_CLOSE = 100.0
MIN_TURNOVER = 3e8          # rupees of daily turnover

HORIZON = 5                 # sessions the move gets to happen in
MAX_DAILY = 0.25            # beyond this a single session is a corporate action
MIN_HISTORY = 60            # sessions needed before the features mean anything

# Measured on 6,499 signals, 138 symbols, 351 sessions (2025-03 to 2026-08).
P_MOVE, P_UP, P_UP10 = 0.730, 0.484, 0.180
P_MOVE_BASE, P_UP_BASE = 0.413, 0.234
HURDLE = 0.568

SCAN = """( {cash} (
    latest atr( 14 ) > latest close * 0.04
    and latest close > 100
    and latest sma( latest volume , 20 ) > 300000
) )"""


def daily_bars(since: dt.date | None = None) -> pd.DataFrame:
    """Sessions rebuilt from the 15-minute candles, one row per stock-day.

    The feed is intraday, so the daily open is the first bar's open and the
    close the last bar's close rather than either end of the day's range.
    """
    rows = StockEquityCandle.objects.filter(interval_minutes=15)
    if since is not None:
        rows = rows.filter(timestamp__date__gte=since)
    rows = rows.values_list("symbol", "timestamp", "open", "high", "low",
                            "close", "volume")
    frame = pd.DataFrame(list(rows), columns=[
        "symbol", "ts", "open", "high", "low", "close", "volume"])
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "day", "o", "h", "l", "c", "v"])
    frame["ts"] = (pd.to_datetime(frame["ts"], utc=True)
                   .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["day"] = frame["ts"].dt.date
    out = frame.groupby(["symbol", "day"]).agg(
        o=("open", "first"), h=("high", "max"), l=("low", "min"),
        c=("close", "last"), v=("volume", "sum")).reset_index()
    return out.sort_values(["symbol", "day"]).reset_index(drop=True)


def features(daily: pd.DataFrame) -> pd.DataFrame:
    """Everything knowable at a session's close, per symbol.

    Only the four columns the shortlist and its screen actually use, computed
    exactly as `research/premove_features.py::features()` computes them.
    """
    out = []
    for symbol, raw in daily.groupby("symbol", sort=False):
        f = raw.sort_values("day").reset_index(drop=True).copy()
        if len(f) < MIN_HISTORY:
            continue
        c, h, l, v = f["c"], f["h"], f["l"], f["v"]

        # True range, then ATR as its 14-session mean. The gap terms matter:
        # a stock that opens 6% away and then sits still has moved, and a
        # high-minus-low would miss it entirely.
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
                       axis=1).max(axis=1)
        f["atr_pct"] = tr.rolling(14).mean() / c

        # Median, not mean: one block deal should not qualify a name that is
        # otherwise untradeable.
        f["dollar_vol"] = (v * c).rolling(20).median()

        # Shifted by one so "the range" excludes today's own bar -- otherwise a
        # name that just made a new high always reads as sitting at 100%.
        prior_hi = h.rolling(20).max().shift(1)
        prior_lo = l.rolling(20).min().shift(1)
        span = (prior_hi - prior_lo).replace(0, np.nan)
        f["pos20"] = (c - prior_lo) / span
        f["mom5"] = c / c.shift(5) - 1

        f["symbol"] = symbol
        out.append(f)
    if not out:
        return pd.DataFrame(columns=list(daily.columns) + [
            "atr_pct", "dollar_vol", "pos20", "mom5"])
    return pd.concat(out, ignore_index=True)


def shortlist(frame: pd.DataFrame, day) -> pd.DataFrame:
    """The names that qualify on one session's close, best-traded first."""
    today = frame[frame["day"] == day]
    hit = today[(today["atr_pct"] > ATR_CUT)
                & (today["c"] > MIN_CLOSE)
                & (today["dollar_vol"] > MIN_TURNOVER)].copy()
    # No directional ranking -- none survived pricing on real options. Ordering
    # by turnover puts the names that cost least to get in and out of at the
    # top, which is the only ordering here that is defensible.
    return hit.sort_values("dollar_vol", ascending=False).reset_index(drop=True)


def outcomes(daily: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    """What each stock-day went on to do over the next `horizon` sessions.

    Returned per stock-day rather than per pick so one pass serves every pick,
    and so a pick can be scored identically whether it was recorded live or
    reconstructed.
    """
    frame = daily.sort_values(["symbol", "day"]).copy()
    g = frame.groupby("symbol", sort=False)

    # `transform`, not `g.h.rolling(...).shift(-horizon)`: a rolling result is
    # MultiIndexed and shifting it walks off the end of one symbol into the
    # first rows of the next, which would score RELIANCE against SBIN's move.
    fwd_hi = g["h"].transform(lambda x: x.rolling(horizon).max().shift(-horizon))
    fwd_lo = g["l"].transform(lambda x: x.rolling(horizon).min().shift(-horizon))
    frame["fwd_high_pct"] = fwd_hi / frame["c"] - 1
    frame["fwd_low_pct"] = fwd_lo / frame["c"] - 1
    frame["fwd_close_pct"] = g["c"].shift(-horizon) / frame["c"] - 1

    # How many of the horizon's sessions have actually happened yet. A pick
    # scored on three sessions is not a five-session result and is held back
    # rather than quietly counted as a miss.
    frame["sessions_ahead"] = g.cumcount(ascending=False)

    # A split is not a move. Blank the label rather than learn from it: any
    # single session beyond +/-25% is a corporate action, not price.
    action = g["c"].pct_change().abs() > MAX_DAILY
    ahead = action.groupby(frame["symbol"], sort=False).transform(
        lambda x: x.shift(-1).rolling(horizon, min_periods=1).max().shift(-horizon + 1))
    contaminated = action | ahead.fillna(0).astype(bool)
    for col in ("fwd_high_pct", "fwd_low_pct", "fwd_close_pct"):
        frame.loc[contaminated, col] = np.nan
    frame["contaminated"] = contaminated

    # Nullable booleans, because "the five sessions have not happened yet" and
    # "the move did not come" are different facts and must not collapse.
    for col, series in (("hit_up5", frame["fwd_high_pct"] >= 0.05),
                        ("hit_dn5", frame["fwd_low_pct"] <= -0.05),
                        ("hit_up10", frame["fwd_high_pct"] >= 0.10)):
        frame[col] = series.astype("boolean")
        frame.loc[frame["fwd_high_pct"].isna(), col] = pd.NA
    return frame
