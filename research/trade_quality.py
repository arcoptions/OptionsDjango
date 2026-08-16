"""Which trades are worth their transaction costs?

The spread test reframed the problem. The strategy captures about five premium
points per trade, and a round-trip bid-ask of one rupee is a fifth of that. So
the question is no longer only "how do we make more per trade" but "which trades
were never going to clear the cost of taking them". Cutting those raises the
average and cuts the drawdown at the same time, which no parameter tested so far
has managed to do in the same direction.

This is a breakdown, not a search. Every cut here is something already known at
entry -- the clock, whether it is the day's first trade, which side we are on --
so a group that stands out is directly actionable rather than a curiosity. The
cuts are deliberately few and pre-declared: with 64 trades, slicing until
something looks good would find something whether or not it is there.
"""
import os
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config

from exit_lab import book, run

TRAIL = 0.7


def group(trades, key):
    buckets = defaultdict(list)
    for trade in trades:
        buckets[key(trade)].append(trade)
    return buckets


def report(title, buckets, order=None):
    print(f"\n  {title}")
    print(f"  {'':<22}{'n':>5}{'win%':>8}{'mean R':>9}{'total R':>9}"
          f"{'pts/trade':>11}{'pts total':>11}")
    keys = order if order is not None else sorted(buckets)
    for key in keys:
        rows = buckets.get(key)
        if not rows:
            continue
        values = np.array([t["realized_r"] for t in rows])
        points = np.array([t["realized_r"] * t["unit_risk"] for t in rows])
        print(f"  {str(key):<22}{len(rows):>5}{100 * (values > 0).mean():>8.1f}"
              f"{values.mean():>9.2f}{values.sum():>9.1f}{points.mean():>11.2f}"
              f"{points.sum():>11.1f}")


def main():
    trades = run(nifty_trail_config(), trail_gap=TRAIL, record=True)
    print(f"{len(trades)} trades at a {TRAIL}R trail\n", flush=True)

    ordinal = {}
    seen = defaultdict(int)
    for trade in sorted(trades, key=lambda t: t["signal_at"]):
        seen[trade["date"]] += 1
        ordinal[id(trade)] = seen[trade["date"]]

    def clock(trade):
        stamp = datetime.fromisoformat(trade["signal_at"])
        if stamp.time() < time(10, 30):
            return "1 morning  <10:30"
        if stamp.time() < time(12, 30):
            return "2 midday   <12:30"
        if stamp.time() < time(14, 0):
            return "3 early pm <14:00"
        return "4 late pm  >=14:00"

    report("by the clock", group(trades, clock))
    report("by trade number that day",
           group(trades, lambda t: f"trade {ordinal[id(t)]} of the day"))
    report("by side", group(trades, lambda t: t["option_type"]))
    report("by weekday",
           group(trades, lambda t: datetime.fromisoformat(t["date"]).strftime("%A")),
           order=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    report("by entry premium",
           group(trades, lambda t: ("A  under Rs 100" if t["entry"] < 100 else
                                    "B  Rs 100-150" if t["entry"] < 150 else
                                    "C  Rs 150-200" if t["entry"] < 200 else
                                    "D  Rs 200+")))

    print(f"\n\n  full-pipeline re-runs: does capping trades per day help?\n")
    print(f"  {'config':<28}{'n':>5}{'win%':>8}{'net Rs':>11}{'maxDD':>10}"
          f"{'Rs/trade':>10}")
    base = nifty_trail_config()
    for cap in (1, 2, 3):
        result = book(run(replace(base, max_trades_per_day=cap), trail_gap=TRAIL))
        if not result:
            continue
        print(f"  {f'max {cap} trades/day':<28}{result['n']:>5}{result['win']:>8.1f}"
              f"{result['net']:>11,.0f}{result['dd']:>10,.0f}"
              f"{result['net'] / result['n']:>10,.0f}", flush=True)
    for cutoff in (time(12, 30), time(14, 0)):
        # Only the entry window is narrowed. `end_time` is left alone so this
        # answers "stop taking new trades" and not "go flat earlier", which is a
        # different question with a different answer.
        result = book(run(replace(base, entry_windows=((time(9, 30), cutoff),)),
                          trail_gap=TRAIL))
        if not result:
            continue
        print(f"  {f'no entries after {cutoff:%H:%M}':<28}{result['n']:>5}"
              f"{result['win']:>8.1f}{result['net']:>11,.0f}{result['dd']:>10,.0f}"
              f"{result['net'] / result['n']:>10,.0f}", flush=True)


if __name__ == "__main__":
    main()
