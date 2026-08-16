"""Is the 0.75R trail a real plateau or one lucky point?

The coarse sweep put 0.5R at +Rs 21,221, 0.75R at +Rs 35,217 and 1.0R at
+Rs 15,742. A parameter that is genuinely better is better than its neighbours
too; a single peak between two troughs is the shape a 64-trade sample produces
by accident. So this fills in the gaps and then tries to break whatever wins.

Three ways to break it, all of which have killed candidates in this project
before: split the sample in half by date, drop the three best trades, and look
at each quarter on its own. A rule that only works in one quarter, or only
because of three trades, is not a rule.
"""
import os
import sys
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger

from exit_lab import run

TRAILS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0)


def quarter(date):
    year, month = int(date[:4]), int(date[5:7])
    return f"{year}Q{(month - 1) // 3 + 1}"


def main():
    config = nifty_trail_config()
    books = {}
    for trail in TRAILS:
        ledger, _skipped, drawdown = sized_ledger(run(config, trail_gap=trail))
        books[trail] = (sorted(ledger, key=lambda row: row["date"]), drawdown)
        print(f"  ran trail {trail}R", flush=True)

    header = (f"\n{'trail':>7}{'n':>5}{'win%':>7}{'net Rs':>10}{'maxDD':>9}"
              f"{'1st half':>10}{'2nd half':>10}{'less top 3':>12}")
    print(header)
    print("-" * (len(header) - 1))
    for trail in TRAILS:
        ledger, drawdown = books[trail]
        net = [row["net_pnl"] for row in ledger]
        half = len(net) // 2
        trimmed = sum(sorted(net)[:-3])
        wins = sum(1 for value in net if value > 0)
        print(f"{trail:>6}R{len(net):>5}{100 * wins / len(net):>7.1f}{sum(net):>10,.0f}"
              f"{drawdown:>9,.0f}{sum(net[:half]):>10,.0f}{sum(net[half:]):>10,.0f}"
              f"{trimmed:>12,.0f}")

    print("\nnet rupees by quarter\n")
    quarters = sorted({quarter(row["date"]) for row in books[TRAILS[0]][0]})
    print(f"{'trail':>7}" + "".join(f"{q:>12}" for q in quarters))
    for trail in TRAILS:
        ledger, _drawdown = books[trail]
        buckets = defaultdict(float)
        counts = defaultdict(int)
        for row in ledger:
            buckets[quarter(row["date"])] += row["net_pnl"]
            counts[quarter(row["date"])] += 1
        print(f"{trail:>6}R" + "".join(
            f"{buckets[q]:>8,.0f}/{counts[q]:<3}" for q in quarters))

    print("\nhow concentrated is each result: share of net from the best trade\n")
    for trail in TRAILS:
        ledger, _drawdown = books[trail]
        net = np.array([row["net_pnl"] for row in ledger])
        total = net.sum()
        best = net.max()
        gains = net[net > 0].sum()
        print(f"  {trail}R   best trade Rs {best:>7,.0f}"
              f"   = {100 * best / total:>5.1f}% of net"
              f"   = {100 * best / gains:>5.1f}% of gross gains")


if __name__ == "__main__":
    main()
