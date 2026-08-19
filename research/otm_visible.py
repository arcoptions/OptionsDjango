"""The gap between the trades you can SEE and the trades you can BUY.

This is the arithmetic behind the whole brief.  The challenge was: "In just HAL
alone there were many entries which could have easily gotten us these trades.
There was a 10X trade too."  That is TRUE, and this file measures exactly how
true, then measures the thing that actually decides the money.

Three quantities, and conflating any two of them is the error the brief rests on:

  WHAT THE CHART SHOWS.  A contract's lifetime low to its lifetime high. This
  needs no timing at all -- it is visible after the fact on any chart and it is
  what makes the opportunity look abundant.

  WHAT AN ENTRY COULD CAPTURE.  From a real session close you could have traded
  at, the highest high over the NEXT ten sessions. Still generous -- a high is a
  touch, not a fill -- but it at least starts from a moment that existed.

  WHAT THE ENTRY IS WORTH ON AVERAGE.  The same entries, every one of them, held
  under a fixed rule. This is the only one that is a strategy.

The first number counts each contract once and rewards it for its best moment.
The second counts every day you could have started and asks what was in front of
you. Both are computed on the SAME contracts so the difference is the timing,
not the sample.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import charge, load_spreads, log  # noqa: E402

MULTS = [2, 3, 5, 10]
MIN_PREM = 2.50


def main():
    d = pd.read_parquet(os.path.join(HERE, "deep_otm.parquet"))
    d["day"] = pd.to_datetime(d["ts"]).dt.date
    d = d[d["kind"] == "CE"].sort_values(["sid", "day"])
    # A contract only counts if it ever actually traded -- a printed high on a
    # zero-volume day is not a price anyone got.
    live = d[d["volume"] > 0]
    # ORDER MATTERS AND IS EASY TO SKIP.  A lifetime low-to-high multiple is only
    # takeable if the high came AFTER the low; a contract that fell all window
    # has a big low-to-high ratio and nothing you could have done with it. So the
    # high is taken over the sessions at or after the low, not over the whole
    # life. Without this the top row overstates itself, and it is the row the
    # entire "the trades are obviously there" case rests on.
    def forward_span(g):
        g = g[g["volume"] > 0]
        lows = g["low"][g["low"] > 0]
        if not len(lows):
            return pd.Series({"lo": np.nan, "hi": np.nan, "gap": np.nan})
        i = lows.idxmin()
        after = g.loc[i:]
        return pd.Series({"lo": lows.loc[i], "hi": after["high"].max(),
                          "gap": len(after) - 1})
    span = live.groupby("sid").apply(forward_span, include_groups=False)
    span = span[np.isfinite(span["lo"]) & (span["lo"] > 0)]
    span["mult"] = span["hi"] / span["lo"]

    t = pd.read_parquet(os.path.join(HERE, "otm_exits.parquet"))
    t = t[(t["kind"] == "CE") & (t["entry"] >= MIN_PREM)]
    sp = load_spreads()
    s = charge(t["entry"].to_numpy(float), sp)
    t = t.assign(net=t["trail 30% of peak"].to_numpy(float) * (1 - s / 2) / (1 + s / 2))
    # Best of the twelve rules per trade = the MFE-flavoured upper bound: what
    # the trade was worth if you had picked its best exit with hindsight.
    rules = [c for c in t.columns if not c.startswith("days::") and c not in
             {"symbol", "day", "kind", "otm", "entry", "sid", "dte", "net"}]
    t = t.assign(best=t[rules].max(axis=1))

    print("=" * 96)
    print("WHAT YOU SEE vs WHAT YOU CAN BUY -- calls, premium >= Rs{:.2f}".format(MIN_PREM))
    print("=" * 96)
    print("  {:,} contracts that actually traded; {:,} entries you could have taken"
          .format(len(span), len(t)))
    print()
    print("  {:<52} {:>8} {:>8} {:>8} {:>8}".format("", "2x", "3x", "5x", "10x"))
    print("  {:<52} {:>8} {:>8} {:>8} {:>8}".format(
        "share of CONTRACTS, lifetime low -> lifetime high",
        *["{:.1%}".format((span["mult"] >= m).mean()) for m in MULTS]))
    print("  {:<52} {:>8} {:>8} {:>8} {:>8}".format(
        "share of ENTRIES whose best exit reached it",
        *["{:.1%}".format((t["best"] >= m).mean()) for m in MULTS]))
    print("  {:<52} {:>8} {:>8} {:>8} {:>8}".format(
        "share of ENTRIES, one fixed rule (trail 30%), NET",
        *["{:.1%}".format((t["net"] >= m).mean()) for m in MULTS]))
    print()
    print("  the average entry under that one fixed rule, net:  {:.3f}x per rupee"
          .format(t["net"].mean()))
    print("  the median entry:                                  {:.3f}x"
          .format(t["net"].median()))
    print("  per-session median (the honest headline):          {:.3f}x"
          .format(t.groupby("day")["net"].median().median()))
    print()
    print("  the high arrives a median {:.0f} sessions after the low, and the low is"
          .format(span["gap"].median()))
    print("  ONE session out of a median {:.0f} the contract trades.".format(
        live.groupby("sid").size().median()))
    print()
    print("  Read the three rows together: a 2x is visible on {:.0%} of contracts,".format(
        (span["mult"] >= 2).mean()))
    print("  survivable-with-hindsight on {:.0%} of entries, and delivered by a rule".format(
        (t["best"] >= 2).mean()))
    print("  you could actually place on {:.1%} of them.".format((t["net"] >= 2).mean()))


if __name__ == "__main__":
    main()
