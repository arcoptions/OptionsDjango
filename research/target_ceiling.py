"""The ceiling on any target rule, level-based or not.

The level test produced a suspicious pattern: every target's result tracked how
far away it sat in R, and not at all what it was named. Round numbers land about
1R out and behave exactly like a fixed 1R target. The opening range lands at
0.6R and behaves like a fixed 0.6R target. The day's open lands at 2R and matches
fixed 3R almost to the rupee.

If that is the whole story then no amount of better level-drawing helps, and
building a volume profile to find prettier levels would be work spent on the
wrong variable. Two tests settle it.

The ceiling: sell half at the exact best price the position ever saw. No rule can
beat that, because no rule can know the high in advance. If perfect foresight on
the target is worth little, the family is capped and level quality is irrelevant.

The control: keep each level's distance distribution but shuffle which trade gets
which distance. If shuffled distances do as well as the real ones, the level was
never identifying anything about that particular trade.
"""
import os
import sys

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
from exit_lab import run
from level_targets import CAPITAL, book, session_levels, target_r, targets_for

SEED = 20260815
SHUFFLES = 400


def main():
    levels_by_date = session_levels(C.session_dates())
    trades = run(nifty_trail_config(), trail_gap=0.7, record=True)
    trades = [t for t in trades if t["date"] in levels_by_date]
    print(f"{len(trades)} trades, trail 0.7R, capital Rs {CAPITAL:,}\n", flush=True)

    base = book(trades, {})
    print(f"  {'rule':<34}{'splits':>8}{'net Rs':>11}{'vs trail':>11}{'maxDD':>10}")

    def line(name, plans):
        result = book(trades, plans)
        print(f"  {name:<34}{result['splits']:>8}{result['net']:>11,.0f}"
              f"{result['net'] - base['net']:>+11,.0f}{result['dd']:>10,.0f}")
        return result

    line("trail only", {})
    # Perfect foresight, and the same thing shaded back so the limit would
    # actually fill rather than touch.
    line("half out at the exact high", {id(t): t["mfe_r"] for t in trades})
    line("half out at 90% of the high",
         {id(t): 0.9 * t["mfe_r"] for t in trades if t["mfe_r"] > 0})
    line("half out at 75% of the high",
         {id(t): 0.75 * t["mfe_r"] for t in trades if t["mfe_r"] > 0})

    print("\n  fixed multiples, for the shape of the curve")
    for fixed in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
        line(f"fixed {fixed}R", {id(t): fixed for t in trades})

    print("\n  each level against its own distances, shuffled between trades")
    print(f"  {'level':<34}{'real net':>11}{'shuffled':>11}{'p05':>10}{'p95':>10}"
          f"{'better than':>13}")
    rng = np.random.default_rng(SEED)
    for name in ("round 50", "day open", "nearest level", "opening high"):
        plans = {}
        for trade in trades:
            levels, open_spot = levels_by_date[trade["date"]]
            found = targets_for(trade, levels, open_spot)
            if name == "nearest level":
                structural = {k: v for k, v in found.items()
                              if k not in ("round 50", "IV 1 sigma", "IV half sigma")}
                if not structural:
                    continue
                level = min(structural.values(),
                            key=lambda v: abs(v - trade["entry_spot"]))
            elif name in found:
                level = found[name]
            else:
                continue
            multiple = target_r(trade, level)
            if multiple and multiple > 0.05:
                plans[id(trade)] = multiple
        if not plans:
            continue
        real = book(trades, plans)["net"]
        keys = list(plans)
        values = np.array([plans[key] for key in keys])
        draws = []
        for _ in range(SHUFFLES):
            order = rng.permutation(len(values))
            draws.append(book(trades, dict(zip(keys, values[order])))["net"])
        draws = np.array(draws)
        print(f"  {name:<34}{real:>11,.0f}{np.median(draws):>11,.0f}"
              f"{np.percentile(draws, 5):>10,.0f}{np.percentile(draws, 95):>10,.0f}"
              f"{100 * (real > draws).mean():>12.0f}%")


if __name__ == "__main__":
    main()
