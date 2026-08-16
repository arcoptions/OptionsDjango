"""Does the previous-day break survive being tested honestly?

The grid in prev_day_break.py was chosen by looking at all 246 sessions and
picking the best cell. That is how every promising backtest in this project has
started, and it is also how most of them have died. Three specific things in
that grid ask to be checked before the number is believed:

  the halves disagree   first half 2.48 points a trade, second half -0.10. The
                        Rs 70,025 is real arithmetic on real fills, but if the
                        per-trade edge has gone to zero then the total is a
                        historical fact rather than a forecast.
  the drawdown          Rs 30,578 on a Rs 1,00,000 account. Three times the
                        money of the shipped strategy for three and a half times
                        the pain is not obviously a better trade.
  parameter cliffs      asking price to clear the level by 0.05% instead of
                        touching it takes Rs 70,025 to Rs 12,647. An edge that
                        sits on a cliff edge is usually standing on noise.

So: choose everything on the first half, change nothing, and see what the second
half pays. That is the only test that cannot be gamed by hindsight.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prev_day_break as P
import simlib as S

GRID = [
    {"max_per_day": 1, "steps": 1, "trail_gap": 0.7},
    {"max_per_day": 1, "steps": 1, "trail_gap": 0.5},
    {"max_per_day": 1, "steps": 2, "trail_gap": 0.7},
    {"max_per_day": 2, "steps": 1, "trail_gap": 0.7},
    {"max_per_day": 2, "steps": 1, "trail_gap": 0.5},
    {"max_per_day": 2, "steps": 2, "trail_gap": 0.7},
    {"max_per_day": 1, "steps": 1, "trail_gap": 0.7, "last_entry": 180},
    {"max_per_day": 1, "steps": 0, "trail_gap": 0.7},
]


def label(params):
    return (f"{params['max_per_day']}/day, {params['steps']} ITM, "
            f"{params['trail_gap']}R"
            + (", morning" if params.get("last_entry") == 180 else ""))


def main():
    print("Loading sessions...", flush=True)
    loaded = S.sessions()
    order = sorted(loaded)
    cut = len(order) // 2
    early = {date: loaded[date] for date in order[:cut]}
    late = {date: loaded[date] for date in order[cut:]}
    print(f"{len(loaded)} sessions: {len(early)} in the first half "
          f"({order[0]} to {order[cut - 1]}), {len(late)} in the second "
          f"({order[cut]} to {order[-1]})\n")

    print("=" * 100)
    print("1. WALK FORWARD: PICK ON THE FIRST HALF, PAY ON THE SECOND")
    print("=" * 100)
    print(f"  {'variant':<34}{'first half':>26}{'second half':>26}")
    print(f"  {'':<34}{'n':>6}{'win%':>7}{'net Rs':>13}{'n':>6}{'win%':>7}{'net Rs':>13}")
    scored = []
    for params in GRID:
        # Each half starts its own Rs 1,00,000, so the second half is not
        # flattered by capital the first half happened to build.
        first = S.book(P.run(early, **params))
        second = S.book(P.run(late, **params))
        print(f"  {label(params):<34}{first['n']:>6}{first['win']:>7.1f}"
              f"{first['net']:>13,.0f}{second['n']:>6}{second['win']:>7.1f}"
              f"{second['net']:>13,.0f}")
        scored.append((first["net"], second["net"], params))

    scored.sort(reverse=True, key=lambda row: row[0])
    best_first, best_second, best = scored[0]
    print(f"\n  chosen on the first half alone: {label(best)}")
    print(f"  it earned Rs {best_first:,.0f} there, and Rs {best_second:,.0f} "
          f"out of sample")
    if best_second <= 0:
        print("  -> the selection does not carry. This is a fitted result.")
    else:
        print(f"  -> it carries, at {100 * best_second / best_first:.0f}% of the "
              f"in-sample rate")

    ranks = [row[1] for row in scored]
    print(f"\n  second-half result of every variant, worst to best: "
          f"{', '.join(f'{value:,.0f}' for value in sorted(ranks))}")
    print(f"  variants profitable out of sample: "
          f"{sum(1 for value in ranks if value > 0)} of {len(ranks)}")

    print("\n" + "=" * 100)
    print("2. THE FULL-SAMPLE HEADLINE, AND WHAT IT COSTS IN DRAWDOWN")
    print("=" * 100)
    print(f"  {'risk per trade':<24}{'n':>6}{'win%':>8}{'net Rs':>12}{'maxDD':>10}"
          f"{'DD % of 1L':>13}{'net/DD':>9}")
    trades = P.run(loaded, max_per_day=1, steps=1)
    for risk in (0.005, 0.01, 0.015, 0.02, 0.03):
        result = S.book(trades, risk=risk)
        ratio = result["net"] / result["dd"] if result["dd"] else float("nan")
        print(f"  {risk * 100:>5.1f}%{'':<18}{result['n']:>6}{result['win']:>8.1f}"
              f"{result['net']:>12,.0f}{result['dd']:>10,.0f}"
              f"{100 * result['dd'] / 100_000:>12.1f}%{ratio:>9.2f}")
    print("\n  Shipped strategy for comparison, at its own 2% risk:")
    print(f"  {'':<24}{52:>6}{68.8:>8.1f}{40341:>12,.0f}{8876:>10,.0f}"
          f"{8.9:>12.1f}%{4.55:>9.2f}")

    print("\n" + "=" * 100)
    print("3. CONTROL ON THE 1-TRADE-A-DAY VARIANT, WHICH IS THE ONE THAT MADE Rs 1.24L")
    print("=" * 100)
    check = S.control(trades, loaded, stop_percent=0.10, trail_gap=0.7,
                      premium_min=P.PREMIUM_MIN, premium_max=P.PREMIUM_MAX)
    if check:
        print(f"  real Rs {check['real']:>10,.0f}")
        print(f"  random median Rs {check['median']:>10,.0f}   "
              f"5th-95th Rs {check['p5']:,.0f} to Rs {check['p95']:,.0f}")
        print(f"  beats {check['beats']:.1f}% of 200 draws")
        print(f"  -> {'real edge' if check['beats'] >= 95 else 'INSIDE THE NOISE'}")

    print("\n" + "=" * 100)
    print("4. WHICH TRADE OF THE DAY IS THE GOOD ONE?")
    print("=" * 100)
    print("  The 1/day cap beat the 2/day cap by Rs 54,000, so the second signal")
    print("  of the day is not merely weaker -- it is actively losing money.")
    print(S.HEADER)
    both = P.run(loaded, max_per_day=2, steps=1)
    seen, firsts, seconds = set(), [], []
    for trade in sorted(both, key=lambda item: (item["date"], item["minute"])):
        (seconds if trade["date"] in seen else firsts).append(trade)
        seen.add(trade["date"])
    S.report("first signal of the day", firsts)
    S.report("second signal of the day", seconds)


if __name__ == "__main__":
    main()
