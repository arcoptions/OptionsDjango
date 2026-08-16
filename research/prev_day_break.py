"""The one setup in the batch that worked, taken apart properly.

Buying a call when the index closes above yesterday's high, and a put when it
closes below yesterday's low, made Rs 70,025 at one strike in the money against
the shipped strategy's Rs 40,341, and beat 99.5% of random draws on the same
days. That is the first genuine positive in this batch and it deserves more than
one line in a table.

It also arrived with a problem: a Rs 35,367 maximum drawdown, four times the
shipped strategy's Rs 8,876, on an account of Rs 1,00,000. Making more money by
risking a third of the account is not obviously an improvement, and most of this
file is about whether the drawdown can be cut without giving the edge back.

Order of business:
  1. confirm the edge at the strike that actually made the money
  2. find where the drawdown comes from
  3. try to cut it -- fewer trades a day, a time window, a distance filter
  4. re-run the control on whatever survives, because every filter tested here
     shrinks the sample and a shrinking sample makes noise look like skill
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicators as I
import momentum_setups as M
import simlib as S
import vwap as V

PREMIUM_MIN, PREMIUM_MAX = 30.0, 1000.0


def signals(data, previous, timeframe=5, buffer_percent=0.0):
    """Closes beyond yesterday's extremes, with an optional buffer.

    The buffer asks price to clear the level by a margin rather than merely
    touch it, which is the standard answer to a level that gets brushed all day.
    """
    if previous is None:
        return []
    old = previous["spot"]
    old = old[np.isfinite(old)]
    if len(old) < 100:
        return []
    high, low = float(old.max()), float(old.min())
    high *= 1 + buffer_percent
    low *= 1 - buffer_percent
    closes, _, _ = I.resample(data["spot"], timeframe)
    found = []
    for bar in range(1, len(closes)):
        now, before = closes[bar], closes[bar - 1]
        if not np.isfinite(now) or not np.isfinite(before):
            continue
        if before <= high < now:
            found.append((S.CALL, (bar + 1) * timeframe, bar))
        elif before >= low > now:
            found.append((S.PUT, (bar + 1) * timeframe, bar))
    return found


def run(loaded, *, steps=1, stop=0.10, trail_gap=0.7, target=None,
        trail_percent=None, timeframe=5, buffer_percent=0.0, max_per_day=2,
        first_entry=0, last_entry=345, require_vwap=False, require_ema=False):
    trades = []
    order = sorted(loaded)
    for position, date in enumerate(order):
        data = loaded[date]
        previous = loaded[order[position - 1]] if position else None
        line = V.synthetic(date, data["spot"]) if require_vwap else None
        closes, _, _ = I.resample(data["spot"], timeframe)
        trend = I.ema(closes, 20) if require_ema else None
        taken = 0
        for side, minute, bar in signals(data, previous, timeframe, buffer_percent):
            if taken >= max_per_day:
                break
            if not first_entry <= minute <= last_entry:
                continue
            if minute >= len(data["spot"]) or not np.isfinite(data["spot"][minute]):
                continue
            close = closes[bar]
            if require_vwap:
                if line is None:
                    continue
                level = line[min((bar + 1) * timeframe - 1, len(line) - 1)]
                if not np.isfinite(level):
                    continue
                if (side == S.CALL) != (close > level):
                    continue
            if require_ema:
                if not np.isfinite(trend[bar]):
                    continue
                if (side == S.CALL) != (close > trend[bar]):
                    continue
            index = S.strike_index(data["strikes"], data["spot"][minute], side, steps)
            if index is None:
                continue
            leg = S.simulate(data, side, index, minute, stop_percent=stop,
                             trail_gap=trail_gap, target_percent=target,
                             trail_percent=trail_percent,
                             premium_min=PREMIUM_MIN, premium_max=PREMIUM_MAX)
            if leg is None:
                continue
            trades.append({**leg, "date": date, "minute": minute, "side": side})
            taken += 1
    return trades


def equity_path(trades):
    """Where the drawdown actually happens, month by month."""
    from math import floor
    from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
    from options_tracker.nifty_trail_strategy import (MAX_CASH_FRACTION,
                                                      RISK_PER_TRADE,
                                                      STARTING_CAPITAL)
    equity = STARTING_CAPITAL
    monthly = defaultdict(float)
    counts = defaultdict(int)
    for trade in sorted(trades, key=lambda item: (item["date"], item["minute"])):
        entry, unit_risk = trade["entry"], trade["unit_risk"]
        lots = max(0, min(
            floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE))))
        if not lots:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        net = ((trade["exit_price"] - entry) * quantity
               - estimate_option_charges(entry, max(trade["exit_price"], 0),
                                         quantity, trade["date"]))
        equity += net
        monthly[trade["date"][:7]] += net
        counts[trade["date"][:7]] += 1
    return monthly, counts


def main():
    print("Loading sessions...", flush=True)
    loaded = S.sessions()
    print(f"{len(loaded)} sessions")
    print("Shipped strategy: 68.8% win, Rs 40,341, DD Rs 8,876 (8.9% of the account)\n")

    print("=" * 100)
    print("1. STRIKE DEPTH -- THE EDGE GOT DEEPER IN THE MONEY, HOW FAR DOES THAT GO?")
    print("=" * 100)
    print(S.HEADER)
    for steps, name in ((-1, "1 OTM"), (0, "ATM"), (1, "1 ITM"), (2, "2 ITM"),
                        (3, "3 ITM"), (4, "4 ITM")):
        S.report(name, run(loaded, steps=steps), 40341)

    print("\n" + "=" * 100)
    print("2. WHERE DOES THE DRAWDOWN COME FROM? (1 ITM, house exit, by month)")
    print("=" * 100)
    trades = run(loaded, steps=1)
    monthly, counts = equity_path(trades)
    running = 0.0
    print(f"  {'month':<10}{'trades':>8}{'net Rs':>12}{'cumulative':>14}")
    for month in sorted(monthly):
        running += monthly[month]
        flag = "  <-- losing month" if monthly[month] < 0 else ""
        print(f"  {month:<10}{counts[month]:>8}{monthly[month]:>12,.0f}"
              f"{running:>14,.0f}{flag}")

    print("\n" + "=" * 100)
    print("3. CAN THE DRAWDOWN BE CUT? (1 ITM throughout)")
    print("=" * 100)
    print(S.HEADER)
    base = S.report("baseline: 2/day, all day, 0.7R", run(loaded, steps=1), 40341)
    baseline = base["net"]
    print()
    for cap in (1, 2, 3):
        S.report(f"at most {cap} trade(s) a day", run(loaded, steps=1, max_per_day=cap), baseline)
    print()
    for first, last, name in ((0, 345, "any time"), (0, 180, "morning only, to 12:15"),
                              (30, 345, "after 09:45"), (60, 345, "after 10:15"),
                              (180, 345, "afternoon only, from 12:15")):
        S.report(name, run(loaded, steps=1, first_entry=first, last_entry=last), baseline)
    print()
    for buffer_percent, name in ((0.0, "touch the level"), (0.0005, "clear it by 0.05%"),
                                 (0.001, "clear it by 0.10%"), (0.002, "clear it by 0.20%")):
        S.report(name, run(loaded, steps=1, buffer_percent=buffer_percent), baseline)
    print()
    S.report("also require the right side of VWAP",
             run(loaded, steps=1, require_vwap=True), baseline)
    S.report("also require the right side of 20 EMA",
             run(loaded, steps=1, require_ema=True), baseline)
    S.report("require both",
             run(loaded, steps=1, require_vwap=True, require_ema=True), baseline)
    print()
    for stop, trail, name in ((0.10, 0.7, "10% stop, 0.7R trail"),
                              (0.10, 0.5, "10% stop, 0.5R trail"),
                              (0.10, 1.0, "10% stop, 1.0R trail"),
                              (0.08, 0.7, "8% stop, 0.7R trail"),
                              (0.15, 0.7, "15% stop, 0.7R trail")):
        S.report(name, run(loaded, steps=1, stop=stop, trail_gap=trail), baseline)
    print()
    for timeframe in (1, 3, 5, 15):
        S.report(f"{timeframe}-min bar for the break",
                 run(loaded, steps=1, timeframe=timeframe), baseline)

    print("\n" + "=" * 100)
    print("4. CONTROL ON THE VARIANT THAT MADE THE MONEY (1 ITM, house exit)")
    print("=" * 100)
    check = S.control(trades, loaded, stop_percent=0.10, trail_gap=0.7,
                      premium_min=PREMIUM_MIN, premium_max=PREMIUM_MAX)
    if check:
        print(f"  real Rs {check['real']:>10,.0f}")
        print(f"  random median Rs {check['median']:>10,.0f}   "
              f"5th-95th Rs {check['p5']:,.0f} to Rs {check['p95']:,.0f}")
        print(f"  beats {check['beats']:.1f}% of 200 draws")
        print(f"  -> {'real edge' if check['beats'] >= 95 else 'INSIDE THE NOISE'}")

    print("\n" + "=" * 100)
    print("5. DOES IT OVERLAP THE SHIPPED STRATEGY, OR IS IT NEW MONEY?")
    print("=" * 100)
    days = sorted({trade["date"] for trade in trades})
    halves = len(days) // 2
    first_half = [t for t in trades if t["date"] <= days[halves]]
    second_half = [t for t in trades if t["date"] > days[halves]]
    print(S.HEADER)
    S.report(f"first half  ({days[0]} to {days[halves]})", first_half)
    S.report(f"second half ({days[halves + 1]} to {days[-1]})", second_half)
    calls = [t for t in trades if t["side"] == S.CALL]
    puts = [t for t in trades if t["side"] == S.PUT]
    S.report(f"calls only (breaks of yesterday's high)", calls)
    S.report(f"puts only  (breaks of yesterday's low)", puts)


if __name__ == "__main__":
    main()
