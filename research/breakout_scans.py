"""The two Chartink scans, replicated and backtested at the STOCK level.

WHY THE STOCK LEVEL FIRST.  The brief is "a Chartink trigger suggests a stock,
we buy a CE, we exit with gains", and the second half cannot rescue the first.
An option is a leveraged, decaying claim on the stock's move: if the underlying
does not go up more than it usually does after these bars, no strike or exit
rule makes the option leg pay. So this file answers one question only -- after
these two scans fire, does the stock outperform a bar picked at random from the
same moment? -- and the option pricing lives in `scan_option_leg.py`.

THE TWO SCANS ARE NOT THE SAME KIND OF SETUP, despite arriving together.
  1-hour: close > EMA20 AND close > UpperBB(20,2) AND volume > 2x SMA(volume,20)
          AND RSI(14) > 60.  A genuine BREAKOUT -- price outside the upper band
          on heavy volume with strong momentum.
  15-min: EMA20 > EMA63, EMA63 rising now and on >= 30 of the last 63 bars,
          this bar's LOW pierces the EMA63 and the previous bar's low did not.
          A PULLBACK -- the first touch of a rising slow average in an uptrend.
          It buys weakness inside strength, the opposite of the above.
Reporting them under one heading would hide that they can disagree completely.

EVERYTHING IS MEASURED AT BAR LEVEL, NOT SESSION LEVEL, and that is not
fussiness.  A 15-minute scan that fires at 11:00 is acted on at 11:15; rolling
it up to a daily close throws away four hours of the move it is trying to catch
and silently converts an intraday rule into a different, slower rule.  Entry
here is the OPEN OF THE BAR AFTER the signal -- the earliest price a close-based
scan can actually transact at.

THE SAME-TIMESTAMP CONTROL IS THE POINT, not a footnote.  A scan that fires on
strong days shows a fat forward return merely by firing on strong days, and that
is not tradeable -- you cannot know at 09:15 which sessions those are.  Every
number is paired against the OTHER SYMBOLS' BARS AT THE SAME TIMESTAMP, and the
t-statistic is clustered by session.  This is the control that killed the
previous programme's headline, and it is applied here from the start.

CORPORATE ACTIONS ARE HANDLED, NOT ASSUMED AWAY.  A 2:1 split prints as a -50%
overnight gap in an unadjusted feed, manufacturing both fake signals and fake
forward returns.  Any forward window spanning a suspect gap is voided.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.models import StockEquityCandle  # noqa: E402

CACHE = "research/equity_15m.parquet"
GAP_LIMIT = 0.25            # overnight move beyond this is treated as a corporate action
BARS_PER_SESSION = 25       # 09:15-15:30 on a 15-minute grid
HORIZONS = (1, 2, 3, 5, 10)  # sessions


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def load():
    if os.path.exists(CACHE):
        return pd.read_parquet(CACHE)
    log("reading StockEquityCandle ...")
    d = pd.DataFrame(list(StockEquityCandle.objects.order_by()
                          .values("symbol", "timestamp", "open", "high", "low",
                                  "close", "volume")))
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = d[c].astype(float)
    d["ts"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("Asia/Kolkata")
    d = d.drop(columns=["timestamp"])
    d["day"] = d["ts"].dt.date
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    d.to_parquet(CACHE, index=False)
    log("cached {:,} bars, {} symbols".format(len(d), d["symbol"].nunique()))
    return d


# ---------------------------------------------------------------- indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    """Wilder's RSI -- the one Chartink uses. A plain rolling mean is a
    different indicator and crosses 60 on different bars."""
    d = s.diff()
    up, dn = d.clip(lower=0.0), (-d).clip(lower=0.0)
    rs = (up.ewm(alpha=1.0 / n, adjust=False).mean() /
          dn.ewm(alpha=1.0 / n, adjust=False).mean().replace(0, np.nan))
    return 100 - 100 / (1 + rs)


def add_indicators(g):
    g = g.copy()
    c = g["close"]
    g["ema20"], g["ema63"] = ema(c, 20), ema(c, 63)
    m, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    g["bb_up"], g["bb_dn"] = m + 2 * sd, m - 2 * sd
    g["rsi14"] = rsi(c, 14)
    g["vol_sma20"] = g["volume"].rolling(20).mean()
    return g


def to_hourly(g):
    """15-min -> 1-hour anchored at the 09:15 open.

    Anchoring matters: a plain 60-minute resample lands on the clock hour and
    builds a 09:00-10:00 bar out of only three of the session's bars, so every
    hourly indicator would be computed on a mis-aligned series.
    """
    x = g.set_index("ts")
    o = (x.resample("60min", label="left", closed="left",
                    origin=x.index[0].normalize() + pd.Timedelta(hours=9, minutes=15))
          .agg({"open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"})
          .dropna(subset=["close"]))
    return o.reset_index()


# --------------------------------------------------------------------- scans
def scan_1h(h, bb_expansion=6.0):
    """The 1-hour breakout.

    THE ONE AMBIGUOUS TERM.  The screenshot shows
    `UpperBB(20,2) - UpperBB(20,2) < 6` with both offsets rendered as [0], which
    is a self-subtraction and identically zero.  Chartink collapses the [-1] in
    that view, so the intended filter is almost certainly the ONE-BAR CHANGE in
    the upper band -- the band is not already flying away.  Reported both with
    and without, so the difference is visible rather than assumed.
    """
    s = ((h["close"] > h["ema20"]) & (h["close"] > h["bb_up"]) &
         (h["volume"] > 2 * h["vol_sma20"]) & (h["rsi14"] > 60))
    if bb_expansion is not None:
        s &= (h["bb_up"] - h["bb_up"].shift(1)) < bb_expansion
    return s.fillna(False)


def scan_15m(f):
    """The 15-minute first-pullback-to-a-rising-EMA63."""
    rising = f["ema63"] > f["ema63"].shift(1)
    return ((f["ema20"] > f["ema63"]) & rising &
            (rising.rolling(63).sum() >= 30) &
            (f["low"] < f["ema63"]) &
            (f["low"].shift(1) >= f["ema63"].shift(1))).fillna(False)


# --------------------------------------------------------------- forward maths
def fwd_max(high, k):
    """max(high[i+1 .. i+k]) -- FORWARD, and per symbol only.

    The obvious `.shift(-1).rolling(k).max()` is backwards: rolling looks at the
    PRECEDING k values, so it returns past highs, and after a groupby shift it
    also runs straight across the boundary into the next symbol.  Reversing,
    rolling, and un-reversing is O(n) and actually forward.
    """
    r = high[::-1]
    m = pd.Series(r).rolling(k, min_periods=1).max().to_numpy()[::-1]
    return np.r_[m[1:], np.nan]          # drop the current bar, keep i+1..i+k


def forward_stats(d):
    """Entry at the NEXT bar's open; returns and reach over each horizon."""
    out = []
    for sym, g in d.groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        o, h, c = g["open"].to_numpy(), g["high"].to_numpy(), g["close"].to_numpy()
        entry = np.r_[o[1:], np.nan]                     # buy the next bar's open
        # corporate-action guard: first bar of a session gapping too far
        first = g["day"].ne(g["day"].shift(1)).to_numpy()
        prev_close = np.r_[np.nan, c[:-1]]
        susp = first & (np.abs(o / prev_close - 1) > GAP_LIMIT)
        res = {"symbol": sym, "ts": g["ts"].to_numpy(), "day": g["day"].to_numpy(),
               "entry": entry}
        for n in HORIZONS:
            k = n * BARS_PER_SESSION
            ex = np.r_[c[k:], np.full(k, np.nan)]        # close k bars later
            # void any window that spans a suspect gap
            bad = pd.Series(susp[::-1]).rolling(k, min_periods=1).max().to_numpy()[::-1]
            bad = np.r_[bad[1:], np.nan] > 0
            r = ex / entry - 1
            m = fwd_max(h, k) / entry - 1
            r[bad], m[bad] = np.nan, np.nan
            res["fwd{}".format(n)], res["mfe{}".format(n)] = r, m
        out.append(pd.DataFrame(res))
    return pd.concat(out, ignore_index=True)


def evaluate(f, fired, label):
    """Signal bars vs every OTHER symbol's bar at the same timestamp."""
    sig = f[f["sig_" + fired]]
    ctl = f[~f["sig_" + fired] & f["ts"].isin(set(sig["ts"]))]
    if len(sig) < 30:
        print("\n  {:<38} only {} signals".format(label, len(sig)))
        return
    print()
    print("  {}   {:,} signals, {} sessions, {} symbols".format(
        label, len(sig), sig["day"].nunique(), sig["symbol"].nunique()))
    print("    {:>4} {:>10} {:>11} {:>9} {:>9} {:>8} {:>9}".format(
        "sess", "signal", "same-bar", "edge", "t (day)", "win%", "ctl win%"))
    for n in HORIZONS:
        col = "fwd{}".format(n)
        a, b = sig[col].dropna(), ctl[col].dropna()
        if len(a) < 30:
            continue
        da, db = sig.groupby("day")[col].mean(), ctl.groupby("day")[col].mean()
        j = pd.concat([da, db], axis=1, keys=["s", "c"]).dropna()
        diff = j["s"] - j["c"]
        t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 2 else np.nan
        print("    {:>4} {:>9.2%} {:>11.2%} {:>+9.2%} {:>9.2f} {:>8.1%} {:>9.1%}".format(
            n, a.mean(), b.mean(), a.mean() - b.mean(), t, (a > 0).mean(), (b > 0).mean()))
    print("    how far it RUNS in 5 sessions (this is what the option leg needs):")
    print("      {:>6} {:>12} {:>12} {:>9}".format("move", "signal", "same-bar", "lift"))
    for thr in (0.03, 0.05, 0.10):
        a = (sig["mfe5"].dropna() >= thr).mean()
        b = (ctl["mfe5"].dropna() >= thr).mean()
        print("      {:>+6.0%} {:>12.1%} {:>12.1%} {:>8.2f}x".format(
            thr, a, b, a / b if b else np.nan))


def main():
    d = load()
    log("{:,} bars, {} symbols, {} sessions, {} .. {}".format(
        len(d), d["symbol"].nunique(), d["day"].nunique(), d["day"].min(), d["day"].max()))

    f = forward_stats(d)
    log("forward stats built for {:,} bars".format(len(f)))

    # ---- signals, mapped back onto the 15-minute grid
    for name in ("15m", "1h", "1h_nobb"):
        f["sig_" + name] = False
    idx = {(s, t): i for i, (s, t) in enumerate(zip(f["symbol"], f["ts"]))}
    hits = {"15m": [], "1h": [], "1h_nobb": []}
    for sym, g in d.groupby("symbol", sort=False):
        fi = add_indicators(g)
        for t in fi.loc[scan_15m(fi), "ts"]:
            hits["15m"].append(idx.get((sym, t)))
        h = add_indicators(to_hourly(g))
        for name, expn in (("1h", 6.0), ("1h_nobb", None)):
            for t in h.loc[scan_1h(h, expn), "ts"]:
                # an hourly signal is actionable at the END of that hour, i.e.
                # the 15-minute bar three slots later closes it
                hits[name].append(idx.get((sym, t + pd.Timedelta(minutes=45))))
    for name, ii in hits.items():
        ii = [i for i in ii if i is not None]
        f.loc[ii, "sig_" + name] = True
        log("{:<8} {:,} signal bars".format(name, len(ii)))

    print()
    print("=" * 100)
    print("STOCK-LEVEL RESULT -- entry at the next bar's open, vs other symbols at the SAME BAR")
    print("=" * 100)
    evaluate(f, "15m", "15-min pullback to rising EMA63")
    evaluate(f, "1h", "1-hour breakout (band change < 6)")
    evaluate(f, "1h_nobb", "1-hour breakout (no band filter)")

    f.to_parquet("research/scan_signals.parquet", index=False)
    log("wrote research/scan_signals.parquet")


if __name__ == "__main__":
    main()
