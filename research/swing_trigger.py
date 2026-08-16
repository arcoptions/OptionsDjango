"""Trigger on the turn itself instead of waiting for the fractal to confirm.

The ladder put 34% of the edge in the three bars spent waiting for a fractal to
become knowable. That wait is only needed because the fractal definition demands
it -- but the *thing* the definition is trying to catch, price rolling over off
a local extreme, can be detected the minute it happens.

The rule: track the highest spot of the last window minutes; the moment spot is
trigger points below it, buy the put. Mirrored for lows. No future bar is
consulted, so this fires at the turn rather than after it.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
from swing_trade import COOLDOWN, LAST_ENTRY, simulate, summarise

WINDOWS = (20, 30, 45)
TRIGGERS = (20, 30, 40, 50)
START_ROW = 45  # 10:00 IST, so the window has real history behind it


def crossings(spot, window, trigger):
    """Rows where spot first falls (or rises) trigger points off a local extreme."""
    events = []
    for row in range(max(START_ROW, window), LAST_ENTRY):
        past = spot[row - window : row]
        if np.isnan(past).any() or np.isnan(spot[row]):
            continue
        # The trigger reads spot at row, so the fill has to be the next minute.
        # Entering on row's own open would be buying before the signal exists.
        if spot[row] <= past.max() - trigger:
            events.append((row + 1, "H"))  # rolled over off a high -> buy the put
        elif spot[row] >= past.min() + trigger:
            events.append((row + 1, "L"))
    return events


def run(window, trigger):
    trades = []
    for date in C.session_dates():
        try:
            session = C.load(date)
        except OSError:
            continue
        spot = session["spot"].astype(np.float64)
        if len(spot) < 200:
            continue
        available = -1
        for row, kind in crossings(spot, window, trigger):
            if row <= available:
                continue
            side = 1 if kind == "H" else 0
            trade = simulate(session, row, side)
            if not trade:
                continue
            trades.append({**trade, "date": date})
            available = trade["exit_row"] + COOLDOWN
    return trades


def main():
    header = (f"{'window':>7}{'trigger':>9}{'n':>6}{'win%':>8}{'totR':>9}"
              f"{'avgR':>8}{'PF':>7}{'trades/day':>12}")
    print(header)
    print("-" * len(header))
    for window in WINDOWS:
        for trigger in TRIGGERS:
            result = summarise(run(window, trigger))
            if not result:
                continue
            print(f"{window:>7}{trigger:>9}{result['n']:>6}{result['win']:>8.1f}"
                  f"{result['totR']:>9.1f}{result['avgR']:>8.3f}{result['pf']:>7.2f}"
                  f"{result['n']/246:>12.1f}")


if __name__ == "__main__":
    main()
