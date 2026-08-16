"""Before the Rs 100 floor is committed to, three things need checking.

Section 1 of finalise.py made Rs 100 with no upper cap the best band on every
axis that matters -- net per unit of drawdown, points per trade, and above all
survival under a bid-ask. But section 4 showed something that could undercut it:
the Rs 100+ variant took 8 trades in the first half of the sample and 42 in the
second. A filter that barely bound for a year and then started binding is either
a filter that finally found its regime, or a filter fitted to the last six
months. Which one decides whether it should be traded tomorrow.

  is it the band  Every band is split the same way. If all of them are
                  second-half loaded then this is a fact about the market -- more
                  signals, or dearer options -- and not about Rs 100.
  what it admits  The distribution of entry premiums actually taken, so "no cap"
                  can be stated as a fact rather than a hope. A cap of Rs 1,000
                  that nothing ever approaches is not a parameter.
  how often       Trades a month, because a strategy that fires twice a quarter
                  cannot be judged by its owner inside a quarter.

The band trades are already on disk from finalise.py, so none of this pays the
contract load again.
"""
import os
import pickle
import sys
from collections import Counter

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from finalise import CACHE, band_label, book, charged, points


def main():
    with open(CACHE, "rb") as handle:
        kept = pickle.load(handle)

    every = sorted({trade["date"] for trades in kept.values()
                    for trade in trades})
    cut = every[len(every) // 2]
    print(f"{len(every)} trading days carry at least one trade; "
          f"the halves split at {cut}\n")

    print("=" * 100)
    print("1. IS THE SECOND-HALF LOADING A PROPERTY OF Rs 100, OR OF THE MARKET?")
    print("=" * 100)
    print(f"  {'premium band':<16}{'first half':>28}{'second half':>28}")
    print(f"  {'':<16}{'n':>6}{'win%':>7}{'net Rs':>15}{'n':>6}{'win%':>7}"
          f"{'net Rs':>15}")
    for (low, high), trades in kept.items():
        first = [t for t in trades if t["date"] < cut]
        second = [t for t in trades if t["date"] >= cut]
        cells = []
        for half in (first, second):
            result = book(half)
            cells.append(f"{result['n']:>6}{result['win']:>7.1f}"
                         f"{result['net']:>15,.0f}" if result
                         else f"{0:>6}{0:>7}{0:>15}")
        print(f"  {band_label(low, high):<16}" + "".join(cells))

    print("\n" + "=" * 100)
    print("2. WHAT THE Rs 100 FLOOR WITH NO CAP ACTUALLY ADMITS")
    print("=" * 100)
    chosen = kept[(100, 1000)]
    entries = np.array([t["entry"] for t in chosen])
    print(f"  {len(entries)} trades taken")
    print(f"  entry premium: min Rs {entries.min():.2f}, "
          f"median Rs {np.median(entries):.2f}, max Rs {entries.max():.2f}")
    print(f"  90th percentile Rs {np.percentile(entries, 90):.2f}, "
          f"99th Rs {np.percentile(entries, 99):.2f}")
    over = (entries > 250).sum()
    print(f"  above the old Rs 250 cap: {over} trades "
          f"({100 * over / len(entries):.0f}%) -- these are what removing the "
          f"cap buys")
    print(f"  the Rs 1,000 ceiling is {entries.max() / 1000:.0%} of the way to "
          f"binding, so it is a sentinel and not a parameter")

    print("\n  the trades the old cap threw away, on their own:")
    excluded = [t for t in chosen if t["entry"] > 250]
    result = book(excluded)
    if result:
        print(f"    {result['n']} trades, {result['win']:.1f}% win, "
              f"Rs {result['net']:,.0f} booked alone, "
              f"{points(excluded):.2f} points a trade")

    print("\n" + "=" * 100)
    print("3. HOW OFTEN IT FIRES, AND WHETHER THAT IS ENOUGH TO JUDGE IT BY")
    print("=" * 100)
    months = Counter(trade["date"][:7] for trade in chosen)
    ordered = sorted(months)
    print(f"  {len(ordered)} months, {len(chosen)} trades, "
          f"{len(chosen) / len(ordered):.1f} a month on average")
    print(f"  quietest month {min(months.values())}, "
          f"busiest {max(months.values())}")
    print()
    for month in ordered:
        bar = "#" * months[month]
        print(f"    {month}  {months[month]:>2}  {bar}")

    recent = [t for t in chosen if t["date"] >= "2026-02-13"]
    result = book(recent)
    print(f"\n  the last six months alone: {result['n']} trades, "
          f"{result['win']:.1f}% win, Rs {result['net']:,.0f}, "
          f"maxDD Rs {result['dd']:,.0f}")
    for round_trip in (1.00, 2.00):
        charged_result = book(charged(recent, round_trip))
        print(f"    with a Rs {round_trip:.2f} round-trip bid-ask: "
              f"Rs {charged_result['net']:,.0f}")


if __name__ == "__main__":
    main()
