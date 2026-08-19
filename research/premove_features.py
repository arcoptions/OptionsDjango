"""What the tape looks like BEFORE a stock moves 5-10%, either way.

The question this file exists to answer: given everything knowable at today's
close, can we say a stock is about to travel 5% or more in the next five
sessions -- and which way?  If yes, that is a CE.  If down, a PE.  If neither,
no trade.

WHY THE LABEL IS A TOUCH, NOT A CLOSE.  An option holder can exit whenever they
like, so what they monetise is the best price reached during the hold, not the
price at the end of it.  So the label is `max(high) over the next 5 sessions`
against the entry close, and its mirror `min(low)`.  This roughly doubles the
event count versus a close-to-close label (23.5% vs 12.3% at +5%) and it is the
honest target for a buyer.

THE BASE RATE IS THE ENTIRE HURDLE, and it is high:

    5-day touch    +5%  23.5%     -5%  19.4%
                  +10%   5.1%    -10%   3.1%

Nearly a quarter of all stock-days ALREADY precede a 5% up-touch.  A signal
firing on 20% of days with a 25% hit rate has discovered nothing at all.  Every
number this file produces is therefore a LIFT over the base rate of the same
label, never a raw hit rate.  This is exactly the trap the Chartink work fell
into -- the trigger bar turned out statistically indistinguishable from a random
bar of the same session -- and it is worth failing loudly rather than quietly.

DRIFT CUTS THE TWO SIDES OPPOSITE WAYS.  The sample runs +0.074% a day, so the
up label is flattered and the down label is penalised, by construction.  Neither
side may be compared to zero, only to its own base rate.

FEATURES ARE DIRECTION-AGNOSTIC ON PURPOSE.  Distance to resistance AND to
support, compression, volume, IV state -- so one model can choose a side.  Two
separately tuned models would each overfit their own direction and the pair
would look better than either deserves.

NO LOOKAHEAD.  Every rolling window that describes "the level to break" is
shifted by one day so it cannot contain today's bar.  Labels use `shift(-h)`
strictly after the entry close.  The two are built in separate functions so the
boundary stays visible.

CORPORATE ACTIONS.  A split is a 50% "move" that no feature predicts and no
option monetises -- and in this feed it re-based option strikes without re-basing
`spot`, which is what made 1% of rows carry 69% of the loss in the spread study.
Here the guard is on the equity series itself: any day whose move exceeds what
price can plausibly do is dropped from the labels rather than learned from.
"""

import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import StockEquityCandle, StockOptionCandle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(HERE, "daily_equity.parquet")
OUT = os.path.join(HERE, "premove_features.parquet")

HORIZON = 5          # sessions the move gets to happen in
MAX_DAILY = 0.25     # beyond this a single session is a corporate action


def log(message):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), message), flush=True)


# ---------------------------------------------------------------------------
# data


def daily_equity(rebuild=False):
    if os.path.exists(DAILY) and not rebuild:
        return pd.read_parquet(DAILY)
    rows = StockEquityCandle.objects.filter(interval_minutes=15).values_list(
        "symbol", "timestamp", "open", "high", "low", "close", "volume")
    frame = pd.DataFrame(list(rows), columns=[
        "symbol", "ts", "open", "high", "low", "close", "volume"])
    frame["ts"] = (pd.to_datetime(frame.ts, utc=True)
                   .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["day"] = frame.ts.dt.date
    out = frame.groupby(["symbol", "day"]).agg(
        o=("open", "first"), h=("high", "max"), l=("low", "min"),
        c=("close", "last"), v=("volume", "sum")).reset_index()
    out = out.sort_values(["symbol", "day"])
    out.to_parquet(DAILY)
    return out


def option_day_stats():
    """ATM IV and OI per stock-day, plus the call-minus-put IV skew.

    Skew is the one option feature that is genuinely directional: puts bid over
    calls is the market paying up for downside, which is the thing a PE buyer
    would like to front-run.
    """
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15, relative_strike="ATM", implied_volatility__gt=0
    ).values_list("symbol", "timestamp", "option_type", "implied_volatility", "oi")
    frame = pd.DataFrame(list(rows), columns=["symbol", "ts", "side", "iv", "oi"])
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "day", "iv", "oi", "skew"])
    frame["ts"] = (pd.to_datetime(frame.ts, utc=True)
                   .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    frame["iv"] = pd.to_numeric(frame.iv, errors="coerce")
    frame["oi"] = pd.to_numeric(frame.oi, errors="coerce")
    frame["day"] = frame.ts.dt.date
    per_side = frame.groupby(["symbol", "day", "side"]).agg(
        iv=("iv", "median"), oi=("oi", "last")).reset_index()
    wide = per_side.pivot_table(index=["symbol", "day"], columns="side",
                                values=["iv", "oi"])
    wide.columns = ["{}_{}".format(a, b.lower()) for a, b in wide.columns]
    wide = wide.reset_index()
    wide["iv"] = wide[[c for c in ("iv_call", "iv_put") if c in wide]].mean(axis=1)
    wide["oi"] = wide[[c for c in ("oi_call", "oi_put") if c in wide]].sum(axis=1)
    if "iv_put" in wide and "iv_call" in wide:
        wide["skew"] = wide.iv_put - wide.iv_call
    else:
        wide["skew"] = np.nan
    return wide[["symbol", "day", "iv", "oi", "skew"]]


# ---------------------------------------------------------------------------
# features -- everything here is knowable at today's close


def features(daily):
    out = []
    for symbol, raw in daily.groupby("symbol", sort=False):
        f = raw.sort_values("day").reset_index(drop=True).copy()
        if len(f) < 60:
            continue
        c, h, l, v = f.c, f.h, f.l, f.v
        ret = c.pct_change()

        # -- levels: shifted so "the level to break" excludes today -------
        for n in (20, 50):
            prior_hi = h.rolling(n).max().shift(1)
            prior_lo = l.rolling(n).min().shift(1)
            f["to_hi{}".format(n)] = c / prior_hi - 1      # <0 below resistance
            f["to_lo{}".format(n)] = c / prior_lo - 1      # >0 above support
            span = (prior_hi - prior_lo).replace(0, np.nan)
            f["pos{}".format(n)] = (c - prior_lo) / span   # 0 at support, 1 at resistance
            f["brk_up{}".format(n)] = (c > prior_hi).astype(float)
            f["brk_dn{}".format(n)] = (c < prior_lo).astype(float)

        # how stale the extremes are: a level untouched for weeks matters more
        f["age_hi"] = h.rolling(50, min_periods=5).apply(
            lambda x: len(x) - 1 - int(np.argmax(x)), raw=True)
        f["age_lo"] = l.rolling(50, min_periods=5).apply(
            lambda x: len(x) - 1 - int(np.argmin(x)), raw=True)

        # -- energy: compression before expansion --------------------------
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
                       axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        f["atr_pct"] = atr / c
        f["atr_rank"] = atr.rolling(120, min_periods=40).rank(pct=True)
        rng = (h - l) / c
        f["nr7"] = (rng == rng.rolling(7).min()).astype(float)
        f["rng_rank"] = rng.rolling(120, min_periods=40).rank(pct=True)
        f["rvol20"] = ret.rolling(20).std()
        f["rvol_rank"] = f.rvol20.rolling(120, min_periods=40).rank(pct=True)

        # -- participation --------------------------------------------------
        f["vol_surge"] = v / v.rolling(20).median()
        f["vol_rank"] = v.rolling(120, min_periods=40).rank(pct=True)
        f["dollar_vol"] = (v * c).rolling(20).median()

        # -- trend and momentum ---------------------------------------------
        ema20, ema50 = c.ewm(span=20).mean(), c.ewm(span=50).mean()
        f["to_ema20"] = c / ema20 - 1
        f["ema_stack"] = ema20 / ema50 - 1
        for n in (1, 5, 10, 20):
            f["mom{}".format(n)] = c / c.shift(n) - 1
        f["gap"] = f.o / c.shift(1) - 1
        f["close_pos"] = (c - l) / (h - l).replace(0, np.nan)   # where in the bar it shut

        # -- streaks: consecutive up or down closes --------------------------
        sign = np.sign(ret.fillna(0))
        streak, run = [], 0
        for s in sign:
            run = run + s if (run >= 0) == (s >= 0) else s
            streak.append(run)
        f["streak"] = streak

        f["symbol"] = symbol
        out.append(f)
    return pd.concat(out, ignore_index=True)


def cross_sectional(frame):
    """Relative strength: a 3% day is nothing when the whole market rose 3%."""
    frame = frame.copy()
    for col, name in [("mom1", "rs1"), ("mom5", "rs5"), ("mom20", "rs20")]:
        frame[name] = frame[col] - frame.groupby("day")[col].transform("median")
    frame["breadth"] = frame.groupby("day")["mom1"].transform(
        lambda x: (x > 0).mean())
    return frame


# ---------------------------------------------------------------------------
# labels -- strictly after the entry close


def labels(frame, horizon=HORIZON):
    frame = frame.sort_values(["symbol", "day"]).copy()
    g = frame.groupby("symbol", sort=False)

    # Best and worst price REACHED over the next `horizon` sessions.
    #
    # `transform`, not `g.h.rolling(...).shift(-horizon)`: a rolling result is
    # MultiIndexed and shifting it walks off the end of one symbol into the
    # first rows of the next, which silently prices RELIANCE against a label
    # taken from SBIN. transform keeps the shift inside the group.
    fwd_hi = g.h.transform(lambda x: x.rolling(horizon).max().shift(-horizon))
    fwd_lo = g.l.transform(lambda x: x.rolling(horizon).min().shift(-horizon))
    frame["up_max"] = fwd_hi / frame.c - 1
    frame["dn_max"] = fwd_lo / frame.c - 1
    frame["fwd_close"] = g.c.shift(-horizon) / frame.c - 1

    # A split is not a move. Drop the label rather than learn from it: any
    # single session beyond +/-25% is an action, not price. Blank the entry day
    # too -- its own features are computed off a broken close.
    action = g.c.pct_change().abs() > MAX_DAILY
    ahead = action.groupby(frame.symbol, sort=False).transform(
        lambda x: x.shift(-1).rolling(horizon, min_periods=1).max().shift(-horizon + 1))
    contaminated = action | ahead.fillna(0).astype(bool)
    for col in ("up_max", "dn_max", "fwd_close"):
        frame.loc[contaminated, col] = np.nan

    for tag, col, sign in [("up", "up_max", 1), ("dn", "dn_max", -1)]:
        for pct in (5, 10, 20):
            frame["{}{}".format(tag, pct)] = (
                frame[col] * sign >= pct / 100).astype(float)
            frame.loc[frame[col].isna(), "{}{}".format(tag, pct)] = np.nan
    frame["contaminated"] = contaminated
    return frame


def main():
    log("loading daily equity")
    daily = daily_equity()
    log("{:,} stock-days, {} symbols".format(len(daily), daily.symbol.nunique()))

    log("building features")
    frame = features(daily)
    frame = cross_sectional(frame)
    log("{:,} rows after features".format(len(frame)))

    log("attaching option state (ATM IV, OI, put-call skew)")
    opts = option_day_stats()
    log("  option stats for {:,} stock-days, {} symbols".format(
        len(opts), opts.symbol.nunique() if len(opts) else 0))
    if len(opts):
        frame = frame.merge(opts, on=["symbol", "day"], how="left")
        frame = frame.sort_values(["symbol", "day"])
        g = frame.groupby("symbol", sort=False)
        frame["iv_rank"] = g.iv.transform(
            lambda x: x.rolling(60, min_periods=20).rank(pct=True))
        frame["iv_chg5"] = frame.iv - g.iv.shift(5)
        frame["oi_chg5"] = g.oi.pct_change(5)
        # IV rich or cheap against what the stock actually does. Annualised.
        frame["iv_vs_rv"] = frame.iv / (frame.rvol20 * np.sqrt(252) * 100)

    log("building labels")
    frame = labels(frame)

    dropped = int(frame.contaminated.sum())
    log("corporate-action guard: {:,} rows ({:.2f}%) had labels blanked".format(
        dropped, 100 * dropped / len(frame)))

    log("")
    log("BASE RATES (what any signal must beat), {}-session touch:".format(HORIZON))
    for tag, name in [("up", "up"), ("dn", "down")]:
        cells = []
        for pct in (5, 10, 20):
            col = "{}{}".format(tag, pct)
            cells.append("{:>2}%: {:5.2f}%".format(pct, frame[col].mean() * 100))
        log("  {:<6} {}   n={:,}".format(
            name, "   ".join(cells), int(frame["{}5".format(tag)].notna().sum())))

    frame.to_parquet(OUT)
    log("")
    log("wrote {} -- {:,} rows x {} columns".format(OUT, len(frame), frame.shape[1]))


if __name__ == "__main__":
    main()
