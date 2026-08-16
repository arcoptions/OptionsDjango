"""Does the pivot entry beat a coin flip, or is the 65% just the trailing stop?

Reversal and continuation -- opposite trades on the same bars -- both won about
64%. When two contradictory signals score the same, the score is coming from
the exit, not the entry. This runs random entries drawn from the same sessions
through the identical exit machinery, so whatever the trail manufactures on its
own shows up in the control too and cancels out.

Simulations are deterministic, so the random trades are computed once into a
pool and the Monte Carlo just resamples it. Rebuilding them inside every
iteration is the same arithmetic several hundred times over.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import swing_trade as T

ITERATIONS = 1000
POOL_PER_SESSION = 40
CASES = (("reversal", 30), ("reversal", 50), ("reversal", 70))


def build_pool(dates, rng):
    """Precomputed random trades: {date: [trade, ...]} sorted by entry row."""
    pool = {}
    rows = list(range(T.BAR * (T.K + 1), T.LAST_ENTRY))
    for date in dates:
        try:
            session = C.load(date)
        except OSError:
            continue
        drawn = []
        for row in sorted(rng.sample(rows, min(POOL_PER_SESSION, len(rows)))):
            for side in (0, 1):
                trade = T.simulate(session, row, side)
                if trade:
                    drawn.append({**trade, "row": row})
        if drawn:
            pool[date] = drawn
    return pool


def draw(pool, per_date, rng):
    trades = []
    for date, count in per_date.items():
        options = pool.get(date)
        if not options:
            continue
        available = -1
        taken = 0
        for trade in rng.sample(options, len(options)):
            if taken >= count:
                break
            if trade["row"] <= available:
                continue
            trades.append(trade)
            available = trade["exit_row"] + T.COOLDOWN
            taken += 1
    return trades


def percentile(values, fraction):
    return values[min(len(values) - 1, int(fraction * len(values)))]


def main():
    rng = random.Random(20260814)
    pool = build_pool(C.session_dates(), rng)
    print(f"control pool: {sum(len(v) for v in pool.values())} trades "
          f"over {len(pool)} sessions\n")

    for mode, minimum_swing in CASES:
        actual = T.run(minimum_swing, mode)
        summary = T.summarise(actual)
        per_date = {}
        for trade in actual:
            per_date[trade["date"]] = per_date.get(trade["date"], 0) + 1

        wins, totals, averages = [], [], []
        for _ in range(ITERATIONS):
            drawn = draw(pool, per_date, rng)
            if not drawn:
                continue
            wins.append(100 * sum(1 for t in drawn if t["r"] > 0) / len(drawn))
            totals.append(sum(t["r"] for t in drawn))
            averages.append(sum(t["r"] for t in drawn) / len(drawn))
        wins.sort()
        totals.sort()
        averages.sort()

        print(f"{mode}, minimum incoming swing {minimum_swing} pts")
        print(f"  actual   n {summary['n']}  win {summary['win']:.1f}%  "
              f"totR {summary['totR']:+.1f}  avgR {summary['avgR']:+.3f}  "
              f"PF {summary['pf']:.2f}")
        print(f"  control  win% mean {sum(wins)/len(wins):.1f} "
              f"p95 {percentile(wins,0.95):.1f}   "
              f"avgR mean {sum(averages)/len(averages):+.3f} "
              f"p95 {percentile(averages,0.95):+.3f}")
        print(f"  P(random win%  >= actual) = "
              f"{100*sum(1 for v in wins if v >= summary['win'])/len(wins):.1f}%")
        print(f"  P(random totR  >= actual) = "
              f"{100*sum(1 for v in totals if v >= summary['totR'])/len(totals):.1f}%")
        print(f"  P(random avgR  >= actual) = "
              f"{100*sum(1 for v in averages if v >= summary['avgR'])/len(averages):.1f}%\n")


if __name__ == "__main__":
    main()
