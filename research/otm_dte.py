"""Is the 21-35 DTE result a maturity effect, or one good fortnight?

WHAT PROVOKED IT.  `otm_exits.py` on the complete cache produced the first slice
in this programme whose POOLED net clears 1.0 on every one of twelve exit rules:
calls at 21-35 days to expiry, 1.01x to 1.09x, win rate up to 45.9%.  Three things
say do not believe it yet.  It spans TEN SESSIONS.  Those ten are 2026-07-22 to
2026-08-04, which the fortnight control already flagged as a strong stretch.  And
the per-session median stays at 0.74-0.95x while the pooled mean clears 1.0 --
the lottery shape that has now manufactured a result three times here.

THE TEST THAT SETTLES IT is paired, not sliced.  Both maturities trade on the
SAME sessions, so ask each session to rank them against each other and throw the
level away.  If 21-35 beats 35-70 session by session, maturity is doing work.  If
they rise and fall together, the fortnight is doing it and the DTE cut is a
relabelled calendar.

WHY THE PAIRING IS NOT OPTIONAL.  With ten sessions and ~2,200 trades in each,
the effective sample is TEN.  Every trade on one session shares one market, so an
unpaired comparison is really n=10 against n=10 dressed up as n=22,190 -- which
is how a t of 1.35 gets printed next to a 1.09x and reads like evidence.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import charge, load_spreads, log  # noqa: E402

TRADES = os.path.join(HERE, "otm_exits.parquet")
RULE = "trail 30% of peak"
NEAR, FAR = (21, 35), (35, 70)


def net(b, spreads, rule=RULE):
    s = charge(b["entry"].to_numpy(float), spreads)
    return b[rule].to_numpy(float) * (1 - s / 2) / (1 + s / 2)


def main():
    spreads = load_spreads()
    t = pd.read_parquet(TRADES)
    t = t[t["entry"] >= 2.50].copy()

    for kind, word in (("CE", "CALLS"), ("PE", "PUTS")):
        k = t[t["kind"] == kind].copy()
        k["net"] = net(k, spreads)
        near = k[(k["dte"] >= NEAR[0]) & (k["dte"] < NEAR[1])]
        far = k[(k["dte"] >= FAR[0]) & (k["dte"] < FAR[1])]
        both = sorted(set(near["day"]) & set(far["day"]))
        if len(both) < 5:
            log("{}: only {} shared sessions -- cannot pair".format(word, len(both)))
            continue

        print()
        print("=" * 104)
        print("{} -- {} sessions carry BOTH maturities; the calendar is held fixed"
              .format(word, len(both)))
        print("=" * 104)
        print("  {:<14} {:>8} {:>10} {:>8} {:>10} {:>12} {:>10}".format(
            "session", "n near", "near med", "n far", "far med", "difference", "near wins"))
        diffs = []
        for d in both:
            a = near[near["day"] == d]["net"]
            b = far[far["day"] == d]["net"]
            if len(a) < 50 or len(b) < 50:
                continue
            dd = a.median() - b.median()
            diffs.append(dd)
            print("  {:<14} {:>8,} {:>9.3f}x {:>8,} {:>9.3f}x {:>11.3f}x {:>10}".format(
                str(d), len(a), a.median(), len(b), b.median(), dd,
                "yes" if dd > 0 else "no"))
        diffs = np.array(diffs)
        if len(diffs) < 5:
            continue
        se = diffs.std(ddof=1) / np.sqrt(len(diffs))
        print("  " + "-" * 100)
        print("  mean paired difference {:+.3f}x over {} sessions, t = {:+.2f}, {} of {} positive"
              .format(diffs.mean(), len(diffs), diffs.mean() / se if se else float("nan"),
                      int((diffs > 0).sum()), len(diffs)))
        print("  -> {}".format(
            "maturity is doing real work; it is not the fortnight"
            if diffs.mean() > 0 and (diffs > 0).mean() >= 0.7
            else "the two maturities move together -- this is the calendar, not DTE"))

        # And the level question, which the pairing deliberately discards: even if
        # 21-35 beats 35-70, does it beat ONE?
        n_lvl = pd.DataFrame({"v": near["net"], "d": near["day"]}).groupby("d").v.median()
        f_lvl = pd.DataFrame({"v": far["net"], "d": far["day"]}).groupby("d").v.median()
        print("  levels on the shared sessions: near median-of-medians {:.3f}x, "
              "far {:.3f}x, bar 1.000x".format(
                  n_lvl.loc[both].median(), f_lvl.loc[both].median()))
        print("  sessions where the NEAR leg's median trade actually made money: {} of {}"
              .format(int((n_lvl.loc[both] > 1).sum()), len(both)))


if __name__ == "__main__":
    main()
