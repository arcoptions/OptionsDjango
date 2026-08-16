"""Zero-skill control across every leading exit scheme.

Each exit scheme has its own mechanical win rate: a tight trail books lots of
small winners, a 3R target books few. Comparing a scheme's actual result to a
random-entry control under the SAME exit is the only way to see how much of
the number comes from the entry signal rather than the exit's arithmetic.
"""
import os
import random
import sys

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

import sweep_exits as S
from control_trail import entry_pool, random_run

ITERATIONS = 1000
SCHEMES = {
    "S10_T1.25 (current)": {"stop_percent": 0.10, "reward": 1.25},
    "S10_TRAIL0.5": {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5},
    "S10_TRAIL0.75": {"stop_percent": 0.10, "reward": None, "trail_gap": 0.75},
    "S8_TRAIL0.75": {"stop_percent": 0.08, "reward": None, "trail_gap": 0.75},
    "S8_TRAIL1.0": {"stop_percent": 0.08, "reward": None, "trail_gap": 1.0},
    "S8_T2.0": {"stop_percent": 0.08, "reward": 2.0},
    "S6_T3.0": {"stop_percent": 0.06, "reward": 3.0},
    "S10_P1BE_TRAIL1.0": {"stop_percent": 0.10, "reward": None, "partial_at": 1.0,
                          "partial_frac": 0.5, "breakeven": True, "trail_gap": 1.0},
}


def percentile(values, fraction):
    return values[min(len(values) - 1, int(fraction * len(values)))]


def main():
    signals = S.collect()
    pool = entry_pool()
    header = (
        f"{'scheme':<22}{'win%':>7}{'rndWin':>8}{'p99':>7}{'pWin':>7}"
        f"{'totR':>8}{'rndR':>8}{'p95':>8}{'pR':>7}"
    )
    print(header)
    print("-" * len(header))
    for name, scheme in SCHEMES.items():
        actual = S.execute(signals, **scheme)
        summary = S.metrics(actual)
        per_date = {}
        for trade in actual:
            per_date[trade["date"]] = per_date.get(trade["date"], 0) + 1
        rng = random.Random(20260814)
        wins, totals = [], []
        for _ in range(ITERATIONS):
            result = S.metrics(random_run(pool, per_date, rng, scheme))
            if result:
                wins.append(result["win_rate"])
                totals.append(result["total_r"])
        wins.sort()
        totals.sort()
        p_win = sum(1 for value in wins if value >= summary["win_rate"]) / len(wins)
        p_r = sum(1 for value in totals if value >= summary["total_r"]) / len(totals)
        print(
            f"{name:<22}{summary['win_rate']:>7}{sum(wins)/len(wins):>8.1f}"
            f"{percentile(wins,0.99):>7.1f}{p_win*100:>6.1f}%"
            f"{summary['total_r']:>8}{sum(totals)/len(totals):>+8.2f}"
            f"{percentile(totals,0.95):>+8.2f}{p_r*100:>6.1f}%"
        )


if __name__ == "__main__":
    main()
