"""Does the entry premium predict whether a trade is worth taking?

The breakdown says yes, loudly: contracts entered between Rs 100 and Rs 200 paid
about 9.6 premium points each, while contracts under Rs 100 paid 1.7 and those
over Rs 200 paid *negative* 1.1. If that is real it is the most valuable thing
found all night, because `premium_min` and `premium_max` are already config
parameters -- there is nothing to build.

It is also exactly the shape a false positive takes. Sixty-four trades split four
ways is thirteen trades a bucket, and the extreme buckets are the small ones. So
this file does not just re-run the filter; it tries to break it.

  split-half     the sample is cut chronologically in two and the band is scored
                 separately on each. A real property of the instrument holds in
                 both halves. An artefact of which trades happened to land where
                 does not, and this is the test it fails.
  what it proxies the premium of an at-the-money option is not a free parameter:
                 it is roughly the index times implied volatility times the square
                 root of the time left. A premium filter is therefore a disguised
                 filter on IV and days-to-expiry, and if that is what is doing the
                 work the honest description is different even when the trade list
                 is identical.
  the spread     cheap contracts capture few points, and a bid-ask quoted in paise
                 is the same absolute tax whatever the premium. The sub-Rs 100
                 bucket may be unprofitable for a reason that has nothing to do
                 with signal quality, which would make it a costs finding rather
                 than an alpha one.
"""
import os
import sys
from dataclasses import replace
from datetime import datetime

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config

import common as C
from exit_lab import book, run

TRAIL = 0.7
BANDS = ((50, 250), (75, 250), (100, 250), (100, 200), (75, 200), (50, 200),
         (100, 175), (125, 250))


def net_points(trade):
    return trade["realized_r"] * trade["unit_risk"]


def describe(label, trades):
    if not trades:
        print(f"  {label:<26}{0:>5}")
        return
    values = np.array([t["realized_r"] for t in trades])
    points = np.array([net_points(t) for t in trades])
    print(f"  {label:<26}{len(trades):>5}{100 * (values > 0).mean():>8.1f}"
          f"{points.mean():>11.2f}{points.sum():>11.1f}")


def main():
    trades = run(nifty_trail_config(), trail_gap=TRAIL, record=True)
    trades.sort(key=lambda t: t["signal_at"])
    print(f"{len(trades)} trades at a {TRAIL}R trail\n", flush=True)

    print("  split-half: the same premium bands, scored on each half of the "
          "sample separately.\n  A real effect survives the cut; a lucky "
          "arrangement of trades does not.\n")
    middle = len(trades) // 2
    halves = (("first half", trades[:middle]), ("second half", trades[middle:]))
    print(f"  {'band':<26}{'n':>5}{'win%':>8}{'pts/trade':>11}{'pts total':>11}")
    for name, half in halves:
        span = (f"{datetime.fromisoformat(half[0]['date']):%d %b %Y} - "
                f"{datetime.fromisoformat(half[-1]['date']):%d %b %Y}")
        print(f"\n  {name} ({span})")
        for low, high in ((0, 100), (100, 200), (200, 10_000)):
            subset = [t for t in half if low <= t["entry"] < high]
            edge = f"Rs {low}-{high}" if high < 10_000 else f"Rs {low}+"
            describe(f"  entry {edge}", subset)

    print(f"\n\n  what the premium is standing in for: the same buckets, "
          f"described by IV and expiry.\n")
    print(f"  {'entry premium':<26}{'n':>5}{'ATM IV':>9}{'days to expiry':>16}"
          f"{'spot':>10}")
    for low, high in ((0, 100), (100, 150), (150, 200), (200, 10_000)):
        subset = [t for t in trades if low <= t["entry"] < high]
        if not subset:
            continue
        ivs, dtes, spots = [], [], []
        for trade in subset:
            try:
                data = C.load(trade["date"])
            except OSError:
                continue
            strikes = np.asarray(data["strikes"], dtype=float)
            spot = np.asarray(data["spot"], dtype=float)
            side = 0 if trade["option_type"] == "CALL" else 1
            valid = np.where(np.isfinite(spot))[0]
            if not len(valid):
                continue
            index = int(np.argmin(np.abs(strikes - spot[valid[0]])))
            series = np.asarray(data["iv"][side, index], dtype=float)
            series = series[np.isfinite(series) & (series > 0)]
            if len(series):
                ivs.append(float(np.median(series)))
            spots.append(float(spot[valid[0]]))
            expiry = datetime.fromisoformat(str(trade.get("expiry", trade["date"])))
            dtes.append((expiry - datetime.fromisoformat(trade["date"])).days)
        edge = f"Rs {low}-{high}" if high < 10_000 else f"Rs {low}+"
        print(f"  {edge:<26}{len(subset):>5}"
              f"{(np.median(ivs) if ivs else float('nan')):>9.1f}"
              f"{(np.median(dtes) if dtes else float('nan')):>16.1f}"
              f"{(np.median(spots) if spots else float('nan')):>10.0f}")

    print(f"\n\n  full pipeline, Rs 1,00,000, each band re-run end to end so the "
          f"cooldown and\n  daily-loss rules get to respond to the trades the "
          f"filter removes.\n")
    print(f"  {'premium band':<26}{'n':>5}{'win%':>8}{'net Rs':>11}{'maxDD':>10}"
          f"{'Rs/trade':>10}{'pts/trade':>11}")
    base = nifty_trail_config()
    for low, high in BANDS:
        taken = run(replace(base, premium_min=low, premium_max=high),
                    trail_gap=TRAIL, record=True)
        result = book(taken)
        if not result:
            continue
        points = np.mean([net_points(t) for t in taken])
        marker = "  <- shipped" if (low, high) == (50, 250) else ""
        print(f"  {f'Rs {low}-{high}':<26}{result['n']:>5}{result['win']:>8.1f}"
              f"{result['net']:>11,.0f}{result['dd']:>10,.0f}"
              f"{result['net'] / result['n']:>10,.0f}{points:>11.2f}{marker}",
              flush=True)

    print(f"\n\n  net at Rs 1,00,000 once a half bid-ask is charged each way. "
          f"A fixed rupee spread\n  is a far bigger share of a cheap contract's "
          f"few points than of an expensive one's.\n")
    spreads = (0.0, 0.25, 0.50, 1.00)
    print(f"  {'premium band':<26}" + "".join(f"{'Rs ' + f'{s:.2f}':>13}"
                                              for s in spreads))
    for low, high in ((50, 250), (100, 200), (100, 250)):
        cells = []
        for spread in spreads:
            taken = run(replace(base, premium_min=low, premium_max=high),
                        trail_gap=TRAIL, record=True)
            for trade in taken:
                # Charged on both legs, against the same unit risk, so R moves
                # with the cost rather than being quietly rebased on it.
                trade["realized_r"] -= 2 * spread / trade["unit_risk"]
            result = book(taken)
            cells.append(f"{result['net']:>13,.0f}" if result else "n/a".rjust(13))
        print(f"  {f'Rs {low}-{high}':<26}" + "".join(cells), flush=True)


if __name__ == "__main__":
    main()
