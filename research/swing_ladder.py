"""Where the money goes: perfect timing -> confirmed timing -> real exit.

The circles are worth something. The question is how much of it survives contact
with the two things a chart drawn after the fact does not have to pay for:
waiting for the pivot to be confirmed, and not knowing where the next pivot is.
This walks the ladder one rung at a time in option premium, so it is visible
exactly which rung costs what.

  rung 1  enter at the pivot bar, exit at the next opposite pivot   (pure oracle)
  rung 2  enter k bars later at confirmation, exit at the next pivot
  rung 3  enter at confirmation, exit on the trailing stop          (tradeable)
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
from swing_pivots import BAR, alternating, bars, pivots
from swing_trade import (
    K,
    PREMIUM_MAX,
    PREMIUM_MIN,
    LAST_ENTRY,
    atm_index,
    simulate,
)

MINIMUM_SWING = 50


def premium_move(session, entry_row, exit_row, side):
    """Percent change in the fixed-strike option between two rows."""
    spot = session["spot"].astype(np.float64)
    if entry_row >= LAST_ENTRY or exit_row >= len(spot) or np.isnan(spot[entry_row]):
        return None
    column = atm_index(session["strikes"], spot[entry_row])
    closes = session["c"][side, column].astype(np.float64)
    entry, exit_price = closes[entry_row], closes[exit_row]
    if np.isnan(entry) or np.isnan(exit_price):
        return None
    if not (PREMIUM_MIN <= entry <= PREMIUM_MAX):
        return None
    return 100 * (exit_price - entry) / entry


def main():
    perfect, confirmed, spot_perfect, spot_confirmed = [], [], [], []
    tradeable = []
    for date in C.session_dates():
        data = bars(date)
        if not data:
            continue
        try:
            session = C.load(date)
        except OSError:
            continue
        high, low, _close, _stamp = data
        chain = alternating(*pivots(high, low, K))
        for position in range(1, len(chain) - 1):
            index, kind = chain[position]
            previous, _ = chain[position - 1]
            following, _ = chain[position + 1]
            incoming = (high[index] - low[previous]) if kind == "H" else (
                high[previous] - low[index])
            if incoming < MINIMUM_SWING:
                continue
            side = 1 if kind == "H" else 0  # fade a top with a put
            pivot_row = index * BAR
            entry_row = (index + K + 1) * BAR
            exit_row = following * BAR
            if exit_row <= entry_row:
                continue
            one = premium_move(session, pivot_row, exit_row, side)
            two = premium_move(session, entry_row, exit_row, side)
            if one is None or two is None:
                continue
            perfect.append(one)
            confirmed.append(two)
            spot_one = (high[index] - low[following]) if kind == "H" else (
                high[following] - low[index])
            spot_confirmed.append(spot_one - abs(
                high[index] - high[min(index + K, len(high) - 1)]))
            spot_perfect.append(spot_one)
            trade = simulate(session, entry_row, side)
            if trade:
                tradeable.append(trade["r"] * 10)  # 1R is a 10% premium move

    def describe(label, values):
        values = np.array(values)
        print(f"{label:<38}{len(values):>6}{np.mean(values):>10.1f}"
              f"{np.median(values):>10.1f}{100*(values>0).mean():>9.1f}%")

    print(f"pivots with a {MINIMUM_SWING}pt incoming leg, ATM option, strike fixed at entry\n")
    header = f"{'':<38}{'n':>6}{'mean%':>10}{'med%':>10}{'win%':>10}"
    print(header)
    print("-" * len(header))
    describe("1. enter at pivot, exit at next pivot", perfect)
    describe("2. enter at confirmation, same exit", confirmed)
    describe("3. enter at confirmation, trail out", tradeable)

    print(f"\nspot leg, pivot to next pivot: median {np.median(spot_perfect):.1f} pts")
    print(f"cost of waiting {K} bars for confirmation: "
          f"{np.mean(perfect) - np.mean(confirmed):.1f} points of premium, "
          f"{100*(1 - np.mean(confirmed)/np.mean(perfect)):.0f}% of the edge")
    print(f"cost of not knowing the exit:              "
          f"{np.mean(confirmed) - np.mean(tradeable):.1f} points of premium")


if __name__ == "__main__":
    main()
