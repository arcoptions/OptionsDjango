"""Does anything actually pick the SIDE? An out-of-sample test.

THE BACKGROUND.  `PREMOVE_REPORT.md` established that the move is predictable
(2.7x lift) and the side is not (AUC 0.510).  That was measured against a
10%-in-5-sessions label.  The HAL example the user raised shows why that label
was the wrong question: HAL ran +11.1% but took EIGHT sessions, so the label
scores it zero -- while an 8%-OTM call went 8.96x, most of it on a single +6.4%
gap day.  What pays an OTM call is a fast move, not a 5-day cumulative one.

Re-labelled on the largest single-day move within 10 sessions, IV rank turns out
to be cleanly monotonic for motion (top decile 1.82x up, 1.94x down) and still
side-blind.  But inside the top IV decile, mean-reversion features separate the
sides hard: below-EMA names gap up 17.3% vs down 6.9% (2.5:1) while above-EMA
names run 10.2% vs 11.4% (0.89:1), against 1.62:1 unconditional.

WHY THIS FILE IS SCEPTICAL OF THAT.  Those two features were chosen after
looking at ten.  Choosing the best of ten and then reporting its spread is the
exact machine that produced every false positive in this programme.  So:

  - The rule is FROZEN on the first 60% of dates and only scored on the last
    40%.  Nothing is chosen using test-period data.
  - The sample rose overall, so "beaten-down names bounce" could be pure beta.
    Every bucket is therefore also reported against the SAME-DAY cross-section,
    which removes the market move by construction.
  - Both sides are reported. A side-picker has to work on puts too, or it is a
    bull-market artifact wearing a hat.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

FEATURES = "research/premove_features.parquet"
HORIZON = 10
THRESH = 0.06
IV_CUT = 0.70          # "high implied vol" -- frozen, not swept
SPLIT = 0.60


def label(d):
    """Largest single-day up move and largest single-day down move ahead.

    This is the quantity an OTM option actually converts into a multiple. A
    cumulative 5-day move of the same size pays far less, because theta runs
    while the move develops and the strike is only reached at the end.
    """
    g = d.groupby("symbol")
    up = dn = None
    for k in range(1, HORIZON + 1):
        step = g["c"].shift(-k) / g["c"].shift(-(k - 1)) - 1
        up = step if up is None else np.maximum(up, step)
        dn = step if dn is None else np.minimum(dn, step)
    d["up_gap"], d["dn_gap"] = up, dn
    return d.dropna(subset=["up_gap", "dn_gap"])


def report(name, frame, base_u, base_d):
    if not len(frame):
        return
    u = (frame.up_gap >= THRESH).mean()
    v = (frame.dn_gap <= -THRESH).mean()
    print("    {:<34} n {:>6,}   up {:>6.1%} ({:.2f}x)   dn {:>6.1%} ({:.2f}x)   "
          "up:dn {:>5.2f}".format(name, len(frame), u, u / base_u if base_u else 0,
                                  v, v / base_d if base_d else 0, u / v if v else float("inf")))


def main():
    d = pd.read_parquet(FEATURES).sort_values(["symbol", "day"])
    d = d[~d["contaminated"]].copy()
    d = label(d)

    days = np.sort(d["day"].unique())
    cut = days[int(len(days) * SPLIT)]
    train, test = d[d.day < cut], d[d.day >= cut]
    print("=" * 96)
    print("SPLIT  train {} .. {} ({:,} rows)   TEST {} .. {} ({:,} rows)".format(
        days[0], cut, len(train), cut, days[-1], len(test)))
    print("       rule frozen on train; every number below is TEST only")
    print("=" * 96)

    bu, bd = (test.up_gap >= THRESH).mean(), (test.dn_gap <= -THRESH).mean()
    print("\n  TEST base rates: up >={:.0%} {:.1%}   down <=-{:.0%} {:.1%}   "
          "unconditional up:dn {:.2f}".format(THRESH, bu, THRESH, bd, bu / bd))

    # The rule, frozen: high IV rank, then split on whether price is below its EMAs.
    print("\n  1. THE FROZEN RULE ON TEST DATA")
    hi = test[test.iv_rank >= IV_CUT]
    report("high IV, all", hi, bu, bd)
    report("high IV + below EMAs  -> CE", hi[hi.ema_stack <= 0], bu, bd)
    report("high IV + above EMAs  -> PE", hi[hi.ema_stack > 0], bu, bd)
    report("low IV, all (control)", test[test.iv_rank < IV_CUT], bu, bd)

    # Is it beta?  Rank within the same day, so the market move cancels.
    print("\n  2. SAME-DAY CROSS-SECTION -- the market move cannot survive this")
    t = test.copy()
    t["ema_r"] = t.groupby("day")["ema_stack"].rank(pct=True)
    t["iv_r"] = t.groupby("day")["iv_rank"].rank(pct=True)
    hi = t[t.iv_r >= 0.70]
    report("high IV + bottom-third EMA rank", hi[hi.ema_r <= 0.33], bu, bd)
    report("high IV + top-third EMA rank", hi[hi.ema_r >= 0.67], bu, bd)

    # Does the effect hold per month, or is it one episode?
    print("\n  3. MONTH BY MONTH -- one episode, or a persistent effect?")
    t["m"] = pd.to_datetime(t["day"]).dt.to_period("M").astype(str)
    hi = t[t.iv_rank >= IV_CUT]
    print("    {:<10} {:>6} {:>9} {:>9} {:>8}   {:>6} {:>9} {:>9}".format(
        "month", "n_dn", "up", "dn", "up:dn", "n_up", "up", "dn"))
    for m, grp in hi.groupby("m"):
        a, b = grp[grp.ema_stack <= 0], grp[grp.ema_stack > 0]
        if len(a) < 30 or len(b) < 30:
            continue
        ua, da = (a.up_gap >= THRESH).mean(), (a.dn_gap <= -THRESH).mean()
        ub, db = (b.up_gap >= THRESH).mean(), (b.dn_gap <= -THRESH).mean()
        print("    {:<10} {:>6,} {:>8.1%} {:>9.1%} {:>8.2f}   {:>6,} {:>8.1%} {:>9.1%}".format(
            m, len(a), ua, da, ua / da if da else float("inf"), len(b), ub, db))

    # How much of the universe does this actually flag?
    print("\n  4. HOW OFTEN DOES IT FIRE?")
    hi = test[test.iv_rank >= IV_CUT]
    ce, pe = hi[hi.ema_stack <= 0], hi[hi.ema_stack > 0]
    days_n = test.day.nunique()
    print("    CE candidates {:,} over {} sessions = {:.1f}/day".format(len(ce), days_n, len(ce) / days_n))
    print("    PE candidates {:,} over {} sessions = {:.1f}/day".format(len(pe), days_n, len(pe) / days_n))


if __name__ == "__main__":
    main()
