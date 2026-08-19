"""What would a stock have to DO for a bought call to pay?  And does anything do it?

THE INVERSION THIS FILE PERFORMS.  Every attempt so far has picked a signal, built
an option backtest around it, and discovered at the end that it loses. That is
expensive and it buries the reason. All three quantities that decide the answer
are now measured, so the break-even can be written down FIRST and every candidate
signal screened against it on equity bars alone -- no option data, seconds not
minutes. A signal that cannot clear the bar on the underlying cannot clear it on
a levered, decaying claim on the underlying. This is a screen that can only reject.

THE BAR, derived from three measurements rather than assumed:

  toll      2.01% median round trip on the cheap-toll shortlist  (friction_by_symbol)
  decay    -0.142% per 15-minute bar, gross, before spread       (elasticity fit)
  leverage  22.4x option move per 1% stock move, 829,299 bars    (elasticity fit)

  stock move needed = (toll + decay * bars) / leverage

which comes to +0.096% / +0.102% / +0.115% for a 15 / 30 / 60-minute hold. The
toll dominates, so the bar is essentially FLAT at **+0.10% of underlying movement,
above what the stock would have done anyway**. That last clause is the whole
difficulty: a stock drifts up on an average afternoon, and buying that drift is
not an edge, it is a coin flip with a fee. So every number here is measured
against a same-symbol, same-session control, and the raw forward return is
reported alongside it to show how much of the apparent edge is just drift.

WHY THE SIGN OF THE PREVIOUS RESULT MATTERS.  The opening-range breakout is
NEGATIVE at the stock level (-0.014%, t -4.56): individual stocks mean-revert
where NIFTY continues. A negative edge is a signal with its sign inverted, so the
fade belongs in this screen on equal terms with the continuation -- and so does
every other pair, which is why each signal is screened in both directions and the
better side is not cherry-picked but reported as a pair.

WHAT THIS SCREEN CANNOT DO.  It cannot promote anything. Clearing +0.10% on the
underlying is necessary, not sufficient: the option leg still has to survive
whole-lot capital, a 15-minute bar's worth of fill slippage, and the strike being
wherever it is rather than exactly at the money. Survivors earn an option
backtest; they do not earn a conclusion.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

TOLL = 0.0201              # median round trip, cheap-toll shortlist
DECAY = 0.00142            # option decay per 15-minute bar, gross
ELAST = 22.4               # option % move per 1% stock move
HOLDS = (1, 2, 4)          # bars, i.e. 15 / 30 / 60 minutes


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def bar_for(k):
    """Underlying move needed to break even on a k-bar hold, as a fraction."""
    return (TOLL + DECAY * k) / ELAST


def load():
    e = pd.read_parquet("research/equity_15m.parquet")
    e = e.sort_values(["symbol", "ts"]).reset_index(drop=True)
    e["t"] = e["ts"].dt.strftime("%H:%M")
    g = e.groupby(["symbol", "day"], sort=False)
    e["bar"] = g.cumcount()
    e["n_bars"] = g["close"].transform("size")
    # session anchors, computed once and reused by every signal below
    e["o_open"] = g["open"].transform("first")
    e["or_hi"] = g["high"].transform(lambda s: s.iloc[0])
    e["or_lo"] = g["low"].transform(lambda s: s.iloc[0])
    e["day_hi"] = g["high"].cummax()
    e["day_lo"] = g["low"].cummin()
    e["vwap"] = (g.apply(lambda x: (x["close"] * x["volume"]).cumsum() /
                         x["volume"].cumsum().replace(0, np.nan),
                         include_groups=False).reset_index(level=[0, 1], drop=True))
    e["v_mean"] = g["volume"].transform(lambda s: s.expanding().mean())
    e["ret1"] = g["close"].pct_change()
    return e


def forward(e, k):
    """Forward return over k bars, same session only."""
    g = e.groupby(["symbol", "day"], sort=False)
    f = g["close"].shift(-k) / e["close"] - 1
    return f.where(g["close"].shift(-k).notna())


def signals(e):
    """Every candidate, as a boolean mask over the bar it FIRES on.

    Entry is the next bar's open in any real trade, so the forward return is
    measured from this bar's close -- one bar of look-ahead is removed by
    construction, not by hope.
    """
    mid = (e["t"] > "09:15") & (e["t"] <= "14:30") & (e["bar"] >= 1)
    volup = e["volume"] >= e["v_mean"]
    s = {}
    s["ORB up: closes above the opening high"] = mid & (e["close"] > e["or_hi"]) & volup
    s["ORB down: closes below the opening low"] = mid & (e["close"] < e["or_lo"]) & volup
    s["new session high"] = mid & (e["high"] >= e["day_hi"]) & volup
    s["new session low"] = mid & (e["low"] <= e["day_lo"]) & volup
    s["gap up, still above open"] = mid & (e["close"] > e["o_open"] * 1.005)
    s["gap down, still below open"] = mid & (e["close"] < e["o_open"] * 0.995)
    s["reclaims VWAP from below"] = mid & (e["close"] > e["vwap"]) & (
        e.groupby(["symbol", "day"], sort=False)["close"].shift(1) <
        e.groupby(["symbol", "day"], sort=False)["vwap"].shift(1))
    s["loses VWAP from above"] = mid & (e["close"] < e["vwap"]) & (
        e.groupby(["symbol", "day"], sort=False)["close"].shift(1) >
        e.groupby(["symbol", "day"], sort=False)["vwap"].shift(1))
    g = e.groupby(["symbol", "day"], sort=False)["ret1"]
    up3 = (e["ret1"] > 0) & (g.shift(1) > 0) & (g.shift(2) > 0)
    dn3 = (e["ret1"] < 0) & (g.shift(1) < 0) & (g.shift(2) < 0)
    s["three up bars in a row"] = mid & up3
    s["three down bars in a row"] = mid & dn3
    s["volume spike 3x, bar up"] = mid & (e["volume"] >= 3 * e["v_mean"]) & (e["ret1"] > 0)
    s["volume spike 3x, bar down"] = mid & (e["volume"] >= 3 * e["v_mean"]) & (e["ret1"] < 0)
    s["biggest up bar of the day so far"] = mid & (
        e["ret1"] >= g.transform(lambda x: x.expanding().max()))
    s["biggest down bar of the day so far"] = mid & (
        e["ret1"] <= g.transform(lambda x: x.expanding().min()))
    return s


def measure(e, mask, k, fwd, mkt):
    """Signal edge over a SAME-TIMESTAMP cross-sectional control.

    THE CONTROL THIS REPLACES, AND WHY IT WAS WRONG.  The obvious control is
    "every other bar of the same symbol-day". It produced a spectacular table --
    gap-down +0.328% against gap-up -0.357% at thirty minutes, t near 60 -- and
    the near-perfect mirror is the tell: those are not two signals, they are one
    number and its negative, because the control set for "below the open" is
    mostly bars "above the open" and vice versa. Worse, a symbol-day only enters
    that comparison if it contains BOTH states, which is to say only if the stock
    crossed back over its opening price at some point. Bars below the open, on
    the subset of days where the stock crossed back up, have positive forward
    returns BY SELECTION. That is conditioning on the outcome wearing a disguise,
    the same trap as "never added" meaning "never fell 25%".

    The control used instead is the cross-section at the SAME INSTANT: the median
    forward return across every symbol trading in that bar. It removes the market
    move, it holds the clock fixed, and it cannot see the rest of the session, so
    no day-level selection can leak into it. What survives is the only thing worth
    paying for -- movement RELATIVE to everything else you could have bought at
    the same moment.
    """
    win = (e["t"] > "09:15") & (e["t"] <= "14:30") & (e["bar"] >= 1) & fwd.notna()
    hit = mask & win
    if not hit.any():
        return None
    rel = (fwd - mkt)[hit]
    day = e["day"][hit]
    byday = rel.groupby(day).mean()
    if len(byday) < 30:
        return None
    t = byday.mean() / (byday.std(ddof=1) / np.sqrt(len(byday)))
    return {"n": int(hit.sum()), "raw": fwd[hit].mean(), "edge": rel.mean(),
            "t": t, "days": len(byday), "win": (rel > 0).mean()}


def tail(e, mask, k, fwd, thresholds=(0.005, 0.010, 0.020)):
    """Does the signal fatten the RIGHT TAIL, which is what a call actually owns?

    WHY THE MEAN IS THE WRONG STATISTIC HERE, and this is the gap in everything
    above.  The 22.4x elasticity is a LOCAL, LINEAR number: it says what the
    option does for a small move. A call is convex -- gamma means a big move pays
    more than linearly, and the loss is bounded by the premium. So a signal that
    leaves the mean exactly where it was, but makes a +2% move twice as likely at
    the cost of more small losers, is worth real money to a buyer of convexity
    and shows up as nothing in a mean.

    This is also the one shape that keeps surviving in this programme: motion is
    predictable (2.7x lift on a 10% move) where direction is not. If that motion
    is at all one-sided it belongs to a call, and this is the measurement that
    would find it. The base rate is the SAME-INSTANT cross-section -- what
    fraction of everything else trading in that bar cleared the same threshold --
    so a day when the whole market ran does not read as a signal.
    """
    win = (e["t"] > "09:15") & (e["t"] <= "14:30") & (e["bar"] >= 1) & fwd.notna()
    hit = mask & win
    if hit.sum() < 500:
        return None
    out = {"n": int(hit.sum())}
    for x in thresholds:
        over = (fwd > x)
        base = over.where(win).groupby(e["ts"]).transform("mean")
        s, b = over[hit].mean(), base[hit].mean()
        out["p{:.1f}".format(x * 100)] = s
        out["lift{:.1f}".format(x * 100)] = (s / b) if b > 0 else np.nan
        d = (over[hit].astype(float) - base[hit]).groupby(e["day"][hit]).mean()
        out["t{:.1f}".format(x * 100)] = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
    return out


def main():
    log("loading equity bars ...")
    e = load()
    log("{:,} bars, {} symbols, {} sessions".format(
        len(e), e["symbol"].nunique(), e["day"].nunique()))

    print()
    print("=" * 104)
    print("THE BREAK-EVEN BAR, AND WHETHER ANY SIGNAL CLEARS IT")
    print("  toll {:.2%} + decay {:.3%}/bar, divided by {:.1f}x leverage".format(
        TOLL, DECAY, ELAST))
    print("=" * 104)
    for k in HOLDS:
        print("  a {:>2}-minute hold needs the stock to move {:+.3%} MORE than it otherwise would"
              .format(k * 15, bar_for(k)))

    sigs = signals(e)
    # the null: every eligible bar, no signal whatsoever. Anything a real signal
    # scores has to be read as a DIFFERENCE from this row, not from zero.
    sigs = {"(null) every eligible bar, no signal": pd.Series(True, index=e.index), **sigs}
    for k in HOLDS:
        fwd = forward(e, k)
        # the market move at that same instant, which nobody gets paid for
        mkt = fwd.groupby(e["ts"]).transform("median")
        bar = bar_for(k)
        print()
        print("-" * 104)
        print("  HOLD {} MINUTES -- bar to clear: {:+.3%}".format(k * 15, bar))
        print("  {:<38} {:>8} {:>10} {:>10} {:>8} {:>7} {:>9}".format(
            "signal", "fires", "raw fwd", "vs market", "t(day)", "win%", "clears?"))
        out = []
        for name, m in sigs.items():
            r = measure(e, m, k, fwd, mkt)
            if r is None:
                continue
            r["name"] = name
            out.append(r)
        for r in sorted(out, key=lambda x: -x["edge"]):
            print("  {:<38} {:>8,} {:>9.3%} {:>9.3%} {:>8.2f} {:>6.1%} {:>9}".format(
                r["name"], r["n"], r["raw"], r["edge"], r["t"], r["win"],
                "YES" if r["edge"] >= bar and r["t"] > 2 else ""))

    print()
    print("=" * 104)
    print("  Read 'vs market', not 'raw fwd'. The raw column is mostly the index moving,")
    print("  and a call bought on a stock that rose because everything rose is not a signal.")
    print("  A signal and its mirror should NOT be equal and opposite here -- if they are,")
    print("  the control is measuring the two against each other rather than against a bar.")

    # ---- the convexity question the mean cannot answer -------------------
    k = 2
    fwd = forward(e, k)
    print()
    print("=" * 104)
    print("DOES ANY SIGNAL FATTEN THE RIGHT TAIL?  30-minute hold -- what a call actually owns")
    print("  lift is against the SAME-INSTANT cross-section: everything else you could have bought")
    print("=" * 104)
    print("  {:<38} {:>8} {:>16} {:>16} {:>16}".format(
        "signal", "fires", "P(+0.5%) lift", "P(+1.0%) lift", "P(+2.0%) lift"))
    rows = []
    for name, m in sigs.items():
        r = tail(e, m, k, fwd)
        if r is None:
            continue
        r["name"] = name
        rows.append(r)
    for r in sorted(rows, key=lambda x: -(x["lift1.0"] if x["lift1.0"] == x["lift1.0"] else 0)):
        print("  {:<38} {:>8,} {:>8.2%} {:>5.2f}x {:>8.2%} {:>5.2f}x {:>8.2%} {:>5.2f}x".format(
            r["name"], r["n"], r["p0.5"], r["lift0.5"], r["p1.0"], r["lift1.0"],
            r["p2.0"], r["lift2.0"]))
    print()
    print("  t(day) on the P(+1.0%) lift, which is the one that matters for a bought call:")
    for r in sorted(rows, key=lambda x: -(x["lift1.0"] if x["lift1.0"] == x["lift1.0"] else 0))[:6]:
        print("    {:<38} {:>+7.2f}".format(r["name"], r["t1.0"]))


if __name__ == "__main__":
    main()
