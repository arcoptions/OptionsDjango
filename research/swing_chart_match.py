"""Reproduce the marked pivots on the chart's own sessions, then filter by size.

Two things to settle here. First, do the mechanically-detected pivots on
2026-08-11..14 line up with the hand-drawn circles? If they do not, I am
studying a different pattern than the one that was pointed at. Second, the
chart marks roughly 3 pivots per session while a raw k=3 fractal finds 11, so
the circles are clearly the *large* pivots only -- this finds the amplitude
filter that reproduces that count.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
from swing_pivots import BAR, alternating, bars, pivots

CHART = ("2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14")
MINIMUM_SWING = (0, 20, 30, 40, 50, 60)


def clock(minute):
    total = 555 + minute  # minute 0 is 09:15 IST
    return f"{total // 60:02d}:{total % 60:02d}"


def significant(high, low, chain, floor_points):
    """Keep a pivot only if the leg leaving it is at least floor_points."""
    kept = []
    for position, (index, kind) in enumerate(chain):
        legs = []
        if position:
            previous, _ = chain[position - 1]
            legs.append(abs(high[index] - low[previous]) if kind == "H"
                        else abs(high[previous] - low[index]))
        if position + 1 < len(chain):
            following, _ = chain[position + 1]
            legs.append(abs(high[index] - low[following]) if kind == "H"
                        else abs(high[following] - low[index]))
        if legs and max(legs) >= floor_points:
            kept.append((index, kind))
    return kept


def main():
    print("pivots detected on the four sessions shown in the chart (k=3)\n")
    for date in CHART:
        data = bars(date)
        if not data:
            print(f"{date}: no data")
            continue
        high, low, close, stamp = data
        chain = alternating(*pivots(high, low, 3))
        big = significant(high, low, chain, 40)
        big_set = {index for index, _ in big}
        print(f"{date}  spot {close[0]:.0f} -> {close[-1]:.0f}   "
              f"range {high.max()-low.min():.0f} pts")
        for index, kind in chain:
            price = high[index] if kind == "H" else low[index]
            mark = "  <-- 40pt+" if index in big_set else ""
            side = "TOP  (buy PE)" if kind == "H" else "BOTTOM (buy CE)"
            print(f"    {clock(stamp[index])}  {side}  {price:8.1f}"
                  f"   confirmed {clock(stamp[min(index+3, len(stamp)-1)])}"
                  f" @ {close[min(index+3, len(close)-1)]:.1f}{mark}")
        print()

    print("how the amplitude filter thins the count, all 246 sessions\n")
    dates = C.session_dates()
    header = (f"{'minSwing':>9}{'pivots/day':>12}{'medOracle':>11}"
              f"{'medConfirm':>12}{'%kept':>8}{'confirm>25pt':>14}")
    print(header)
    print("-" * len(header))
    cached = {}
    for date in dates:
        data = bars(date)
        if data:
            cached[date] = (data, alternating(*pivots(data[0], data[1], 3)))
    for floor_points in MINIMUM_SWING:
        oracle, confirmed, per_day = [], [], []
        for (high, low, close, _stamp), chain in cached.values():
            kept = significant(high, low, chain, floor_points) if floor_points else chain
            per_day.append(len(kept))
            for (index, kind), (next_index, _) in zip(kept, kept[1:]):
                if index + 3 >= len(close) or next_index <= index + 3:
                    continue
                entry = close[index + 3]
                if kind == "H":
                    oracle.append(high[index] - low[next_index])
                    confirmed.append(entry - low[next_index])
                else:
                    oracle.append(high[next_index] - low[index])
                    confirmed.append(high[next_index] - entry)
        oracle, confirmed = np.array(oracle), np.array(confirmed)
        print(f"{floor_points:>9}{np.mean(per_day):>12.1f}{np.median(oracle):>11.1f}"
              f"{np.median(confirmed):>12.1f}"
              f"{100*np.median(confirmed)/np.median(oracle):>7.0f}%"
              f"{100*(confirmed>25).mean():>13.1f}%")


if __name__ == "__main__":
    main()
