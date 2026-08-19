"""The finalised call shortlist: which stocks may travel 5%+ in the next week.

This is the shipped version of the stock-move work, scoped to what the user
actually asked for -- a daily list of candidates, calls only, with the chart
read left to them. Everything here is measured; the reasoning behind each
choice is recorded so the model is not silently re-litigated later.

WHAT IT DOES AND DOES NOT CLAIM. It claims that these names are far more
likely than average to make a 5% move, and that roughly half of those moves
are up. It does NOT claim to know which way any individual name goes -- that
was tested exhaustively and does not exist. The list is a watchlist. The edge,
if there is one, is the user's chart read on top of it.

WHY THE THRESHOLD IS ABSOLUTE AND NOT A RANKING. The obvious way to keep the
list a constant size is to take the top decile by ATR each day. Measured, that
is strictly worse: the same ~18 names a day at a 35.7% up-rate against 48.4%
for the fixed cut, and in a calm market it hands over 19 names at 27.8% while
the fixed cut correctly hands over 5.6 at 39.3%. What matters is how much a
stock actually moves, not how it ranks among stocks that are all still. So a
quiet tape SHOULD produce a short list, and a two-name day is the model
working rather than failing.

WHY THERE IS NO TREND FILTER. Momentum genuinely lifts the up-rate on the
underlying -- a combined 20-day relative-strength and EMA-stack rank adds
+5.1pp out of sample at an overlap-safe t of +2.18. Priced on real calls it
INVERTS: break-even rises from 56.8% to 68.6% and the payoff when right falls
from +73.7% to +49.2%. A trending stock's rise is already in the premium. This
is the eleventh signal in this programme that is real on the stock and dead on
the option, and it is why the model ships with no directional overlay at all.

    python research/call_shortlist.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")

# The model, in three numbers. ATR is the whole signal; the other two only
# remove names that cannot be traded cleanly.
ATR_CUT = 0.040
MIN_CLOSE = 100.0
MIN_TURNOVER = 3e8          # rupees of daily turnover

# Measured on 6,499 signals, 138 symbols, 351 sessions (2025-03 to 2026-08).
P_MOVE, P_UP, P_UP10 = 0.730, 0.484, 0.180
HURDLE = 0.568

SCAN = """( {cash} (
    latest atr( 14 ) > latest close * 0.04
    and latest close > 100
    and latest sma( latest volume , 20 ) > 300000
) )"""


def shortlist(frame: pd.DataFrame, day) -> pd.DataFrame:
    """The names that qualify on one session's close."""
    today = frame[frame["day"] == day]
    hit = today[(today["atr_pct"] > ATR_CUT)
                & (today["c"] > MIN_CLOSE)
                & (today["dollar_vol"] > MIN_TURNOVER)].copy()
    # No directional ranking -- none survived pricing. Ordering by turnover puts
    # the names that cost least to get in and out of at the top, which is the
    # only ordering here that is defensible.
    return hit.sort_values("dollar_vol", ascending=False)


def main() -> None:
    frame = pd.read_parquet(FEATURES)
    frame["day"] = pd.to_datetime(frame["day"])
    day = frame["day"].max()
    hit = shortlist(frame, day)

    print("=" * 78)
    print("CALL SHORTLIST for the session after {}".format(day.date()))
    print("=" * 78)
    print("\nscan: ATR(14) > 4% of close, close > Rs{:.0f}, turnover > Rs{:.0f}cr"
          .format(MIN_CLOSE, MIN_TURNOVER / 1e7))

    if hit.empty:
        print("\nNo names qualify. The tape is quiet -- ATR is compressed across")
        print("the universe. This is the model declining to trade, not a fault.")
        print("Do not lower the threshold to manufacture a list: measured, a")
        print("relative cut in a calm market gives 19 names at a 27.8% up-rate.")
        return

    print("\n{} name(s). Median over the sample is ~18/day; 2-6 means a calm tape."
          .format(len(hit)))
    print()
    print("  {:<14} {:>9} {:>7} {:>9} {:>8} {:>9}".format(
        "symbol", "close", "ATR%", "20d range", "5d move", "turnover"))
    print("  " + "-" * 62)
    for _, row in hit.iterrows():
        print("  {:<14} {:>9,.1f} {:>6.1f}% {:>8.0%} {:>+8.1f}% {:>7,.0f}cr".format(
            row["symbol"], row["c"], row["atr_pct"] * 100, row["pos20"],
            row["mom5"] * 100, row["dollar_vol"] / 1e7))

    print("\n  '20d range' is where the close sits between the 20-day low (0%)")
    print("  and high (100%). It is chart context for your read, not a signal --")
    print("  measured on its own it does not predict the direction.")

    print("\n" + "=" * 78)
    print("WHAT TO EXPECT, SO THE RESULT CAN BE JUDGED HONESTLY")
    print("=" * 78)
    print("  {:.0%} of these move 5%+ either way within 5 sessions (base {:.0%})"
          .format(P_MOVE, 0.413))
    print("  {:.1%} rise 5%+ -- that is your free hit rate if you pick blindly"
          .format(P_UP))
    print("  {:.0%} rise 10%+".format(P_UP10))
    print()
    print("  You need {:.1%} of your CALL reads correct to break even after".format(HURDLE))
    print("  premium and friction. Blind picking gives {:.1%}, so the read has".format(P_UP))
    print("  to add about {:.0f} points of genuine skill.".format((HURDLE - P_UP) * 100))
    print()
    print("  The hurdle is an average and it is not stable: month by month it")
    print("  ran 18% to unreachable, and in 2026-03 even a perfect read lost")
    print("  money. That regime risk cannot be timed -- breadth known at the")
    print("  prior close does not predict it. Size accordingly.")
    print()
    print("  Puts are excluded by measurement, not preference: the put side")
    print("  needs 122.3% of reads correct, i.e. no read is good enough. Even")
    print("  a perfect put read has a negative median. Re-priced on the")
    print("  repaired cache it is still unreachable, but on four scoreable")
    print("  months -- the put cache has an ATM gap that is not being backfilled.")

    print("\nChartink equivalent:\n{}".format(SCAN))


if __name__ == "__main__":
    main()
