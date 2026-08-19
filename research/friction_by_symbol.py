"""Is the 7.3% toll a property of stock options, or of the average stock option?

THE QUESTION THIS ANSWERS.  Every friction number in this programme has been
POOLED across 181 underlyings and bucketed by PREMIUM. That is the right way to
charge an individual trade, and it is the wrong way to ask whether a strategy can
exist, because a pooled 7.3% is perfectly consistent with a handful of names at
2% hiding inside a long tail at 30%.

WHY IT MATTERS NOW.  `BREAKOUT_SCANS_REPORT.md` established that the shipped
NIFTY rule works because it takes a +7.6% bite against a 1.7% toll -- a ratio of
about 4.5 -- and that the same architecture dies on stocks because the toll is
7.3%, which is nearly the whole bite.  That framing makes a sharp prediction:
if ANY underlying has NIFTY-like friction, the NIFTY architecture should port to
it.  This file looks for those underlyings.  If none exist the buying programme
is closed on a measurement rather than on an average, and if some do exist they
are a shortlist, not a strategy.

WHAT WOULD MAKE THE ANSWER FAKE, and is therefore guarded here.
  * A tight quote you cannot transact in is not a low toll.  A 0.5% spread on a
    contract with zero volume and 300 open interest is a market maker's posted
    fiction. Liquidity gates are applied and reported, not assumed.
  * The spread must be measured where you would actually trade -- near the money
    -- because the same symbol quotes 2% at ATM and 60% four strikes out, and
    averaging those describes no trade anyone would place.
  * One snapshot is one moment.  This is a single chain capture, so it can rank
    symbols and size the gap, but it cannot speak to how the ranking moves
    intraday or across regimes. Stated, not hidden.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NIFTY_TOLL = 0.0163        # Rs2 round trip on the Rs123 median ATM premium
NIFTY_BITE = 0.076         # 9.14 premium points on Rs123
ATM_BAND = 0.03            # |strike/spot - 1| within 3% == "the strike you'd trade"
MIN_PREM = 2.50


def load():
    q = pd.read_csv(os.path.join(HERE, "spread_curve.csv"))
    q["toll"] = q["spread_pct"] / 100.0
    return q


def per_symbol(q, kind="CE", require_volume=False):
    a = q[(q["kind"] == kind) & (q["otm"].abs() <= ATM_BAND) &
          (q["mid"] >= MIN_PREM) & (q["toll"] > 0)].copy()
    if require_volume:
        a = a[a["volume"] > 0]
    g = a.groupby("symbol").agg(quotes=("toll", "size"), toll=("toll", "median"),
                                prem=("mid", "median"), vol=("volume", "sum"),
                                oi=("oi", "median"))
    return g[g["quotes"] >= 2].sort_values("toll")


def main():
    q = load()
    print("=" * 100)
    print("IS THE TOLL A PROPERTY OF STOCK OPTIONS, OR OF THE AVERAGE STOCK OPTION?")
    print("  one live chain capture, {:,} quotes, {} underlyings".format(
        len(q), q["symbol"].nunique()))
    print("  the bar to clear: NIFTY pays {:.1%} to capture {:.1%}".format(
        NIFTY_TOLL, NIFTY_BITE))
    print("=" * 100)

    for lbl, rv in (("all quoted strikes", False), ("only strikes that TRADED today", True)):
        g = per_symbol(q, "CE", require_volume=rv)
        if not len(g):
            print("\n  {:<34} nothing passes the gate".format(lbl))
            continue
        print()
        print("  {} -- {} underlyings with a near-ATM call".format(lbl, len(g)))
        print("    median symbol toll {:.1%}   best {:.1%}   worst {:.1%}".format(
            g["toll"].median(), g["toll"].min(), g["toll"].max()))
        for thr in (NIFTY_TOLL, 0.03, 0.05):
            n = (g["toll"] <= thr).sum()
            print("    at or below {:>5.1%}: {:>3} of {:>3} names ({:>5.1%})".format(
                thr, n, len(g), n / len(g)))
        print("    the ten cheapest to trade:")
        print("      {:<14} {:>7} {:>9} {:>10} {:>10}".format(
            "symbol", "toll", "premium", "vol today", "median OI"))
        for s, r in g.head(10).iterrows():
            print("      {:<14} {:>6.2%} {:>9,.1f} {:>10,.0f} {:>10,.0f}".format(
                s, r["toll"], r["prem"], r["vol"], r["oi"]))

    # The decisive cut: cheap to trade AND actually liquid.
    g = per_symbol(q, "CE", require_volume=True)
    live = g[(g["toll"] <= 0.03) & (g["vol"] > 0)]
    print()
    print("  " + "-" * 96)
    print("  THE SHORTLIST -- near-ATM toll <= 3% and a contract that printed today")
    if not len(live):
        print("    empty. No underlying in this capture is cheap enough to run the NIFTY bite on.")
    else:
        print("    {} names: {}".format(len(live), ", ".join(live.index[:20])))
        print("    their median toll {:.2%} vs NIFTY {:.2%} -- a bite of {:.1%} would net {:.1%}"
              .format(live["toll"].median(), NIFTY_TOLL, NIFTY_BITE,
                      NIFTY_BITE - live["toll"].median()))

    # And the honest counterweight: how much of the universe is that?
    allsym = q["symbol"].nunique()
    print()
    print("  coverage check: {} of {} underlyings ({:.1%}) even quote a near-ATM call"
          " above Rs{:.2f} with a printable spread".format(
              len(g), allsym, len(g) / allsym, MIN_PREM))


if __name__ == "__main__":
    main()
