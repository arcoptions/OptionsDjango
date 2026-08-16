"""Swing pivots on 5-minute NIFTY: how big, how many, how much is capturable.

The chart markings are local extremes -- the bar that turned out to be the
highest of its neighbourhood. That label needs future bars to exist, so this
script separates three different numbers that are easy to conflate:

  oracle      points from the pivot bar to the next opposite pivot. What the
              circles are worth if you could ring the bell at the exact bar.
  confirmed   points from the close k bars later -- the first moment the pivot
              is knowable -- to the next opposite pivot. Still uses an oracle
              exit, so this is an upper bound on a real trade.
  give-up     the difference. This is what the confirmation delay costs, and it
              is the number that decides whether the idea is tradeable at all.
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C

BAR = 5  # minutes per bar, matching the chart
KS = (2, 3, 4)


def _ffill(values):
    valid = ~np.isnan(values)
    if not valid.any():
        return values
    index = np.where(valid, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    filled = values[index]
    filled[: np.argmax(valid)] = values[valid][0]
    return filled


def bars(date):
    """Aggregate the 1-minute spot series into 5-minute OHLC bars."""
    session = C.load(date)
    spot = _ffill(session["spot"].astype(np.float64))
    minute = session["minute"].astype(np.int32)
    if len(spot) < 200 or np.isnan(spot).any():
        return None
    count = len(spot) // BAR
    high = np.empty(count)
    low = np.empty(count)
    close = np.empty(count)
    stamp = np.empty(count, dtype=np.int32)
    for index in range(count):
        window = spot[index * BAR : (index + 1) * BAR]
        high[index] = window.max()
        low[index] = window.min()
        close[index] = window[-1]
        stamp[index] = minute[index * BAR]
    return high, low, close, stamp


def pivots(high, low, k):
    """Fractal pivots: bar i is a high if it tops the k bars on either side."""
    highs, lows = [], []
    for index in range(k, len(high) - k):
        window = slice(index - k, index + k + 1)
        if high[index] == high[window].max() and high[index] > high[index - 1]:
            highs.append(index)
        if low[index] == low[window].min() and low[index] < low[index - 1]:
            lows.append(index)
    return highs, lows


def alternating(highs, lows):
    """Interleave into a strict high/low/high sequence, keeping the extreme."""
    marked = [(index, "H") for index in highs] + [(index, "L") for index in lows]
    marked.sort()
    chain = []
    for index, kind in marked:
        if chain and chain[-1][1] == kind:
            continue
        chain.append((index, kind))
    return chain


def main():
    dates = C.session_dates()
    print(f"{len(dates)} NIFTY sessions, {BAR}-minute bars\n")
    header = (
        f"{'k':>3}{'pivots/day':>12}{'medOracle':>11}{'medConfirm':>12}"
        f"{'medGiveUp':>11}{'%kept':>8}{'confirm>0':>11}{'confirm>20pt':>14}"
    )
    print(header)
    print("-" * len(header))
    for k in KS:
        oracle, confirmed, per_day = [], [], []
        for date in dates:
            data = bars(date)
            if not data:
                continue
            high, low, close, _stamp = data
            chain = alternating(*pivots(high, low, k))
            per_day.append(len(chain))
            for (index, kind), (next_index, _next_kind) in zip(chain, chain[1:]):
                if index + k >= len(close):
                    continue
                entry = close[index + k]
                if next_index <= index + k:
                    continue  # the move was over before it could be confirmed
                if kind == "H":
                    oracle.append(high[index] - low[next_index])
                    confirmed.append(entry - low[next_index])
                else:
                    oracle.append(high[next_index] - low[index])
                    confirmed.append(high[next_index] - entry)
        oracle = np.array(oracle)
        confirmed = np.array(confirmed)
        give_up = oracle - confirmed
        print(
            f"{k:>3}{np.mean(per_day):>12.1f}{np.median(oracle):>11.1f}"
            f"{np.median(confirmed):>12.1f}{np.median(give_up):>11.1f}"
            f"{100*np.median(confirmed)/np.median(oracle):>7.0f}%"
            f"{100*(confirmed>0).mean():>10.1f}%{100*(confirmed>20).mean():>13.1f}%"
        )

    print("\nper-session detail at k=3 (the chart's apparent sensitivity)")
    counts = defaultdict(int)
    spans = []
    for date in dates:
        data = bars(date)
        if not data:
            continue
        high, low, close, stamp = data
        chain = alternating(*pivots(high, low, 3))
        counts[len(chain)] += 1
        for (index, _kind), (next_index, _next) in zip(chain, chain[1:]):
            spans.append((next_index - index) * BAR)
    total = sum(counts.values())
    print(f"  mean {np.mean([k for k, v in counts.items() for _ in range(v)]):.1f} "
          f"pivots/session over {total} sessions")
    print(f"  median minutes between pivots: {np.median(spans):.0f}")
    print(f"  swing legs lasting < 15 min: {100*np.mean(np.array(spans)<15):.0f}%")


if __name__ == "__main__":
    main()
