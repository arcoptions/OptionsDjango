"""Zero-skill control for the trailing-stop exit scheme.

A tight trailing stop mechanically inflates win rate: most trades tick up a
little before rolling over, so they close green. To know whether the entry
signal carries information we run the identical exit machinery on random
entries drawn from the same eligible universe (same sessions, same entry
windows, same moneyness and premium filters) and compare distributions.
"""
import os
import pickle
import random
import sys
from datetime import timedelta

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.strategy_backtest import (
    TIME_EXIT,
    _distance,
    _entry_window_index,
    _matches_moneyness,
    _number,
    load_contract_rows,
)

import sweep_exits as S

POOL = os.path.join(ROOT, "research", "entry_pool.pkl")
SCHEME = {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5}
ITERATIONS = 2000


def entry_pool():
    """Every bar a trade could legally have been opened on, by session."""
    if os.path.exists(POOL):
        with open(POOL, "rb") as handle:
            return pickle.load(handle)
    contracts = load_contract_rows("NIFTY", 1)
    pool = {}
    for contract_key, rows in contracts.items():
        if not contract_key[1]:
            continue
        for index in range(len(rows) - 1):
            row = rows[index]
            clock = row["local_timestamp"].time()
            premium = _number(row["close"])
            if _entry_window_index(clock, S.CONFIG) is None:
                continue
            if row["option_type"] not in S.CONFIG.option_types:
                continue
            if not (S.CONFIG.premium_min <= premium <= S.CONFIG.premium_max):
                continue
            if _distance(row["relative_strike"]) > S.CONFIG.max_distance:
                continue
            if not _matches_moneyness(row["relative_strike"], row["option_type"], S.CONFIG):
                continue
            date = row["local_timestamp"].date().isoformat()
            forward = [
                (
                    item["local_timestamp"],
                    _number(item["open"]),
                    _number(item["high"]),
                    _number(item["low"]),
                    _number(item["close"]),
                )
                for item in rows[index + 1:]
                if item["local_timestamp"].time() <= TIME_EXIT
            ]
            if len(forward) < 10:
                continue
            pool.setdefault(date, []).append(
                {
                    "date": date,
                    "signal_at": row["local_timestamp"],
                    "signal_close": premium,
                    "forward": forward,
                }
            )
    with open(POOL, "wb") as handle:
        pickle.dump(pool, handle)
    return pool


def random_run(pool, per_date, rng, scheme=None):
    """Draw the same number of trades on the same days, honouring the cooldown."""
    scheme = SCHEME if scheme is None else scheme
    trades = []
    for date, count in per_date.items():
        options = pool.get(date)
        if not options:
            continue
        available_at = None
        taken = 0
        for candidate in rng.sample(options, min(len(options), 200)):
            if taken >= count:
                break
            if available_at and candidate["signal_at"] < available_at:
                continue
            trade = S.simulate(candidate, **scheme)
            if not trade:
                continue
            trades.append(trade)
            taken += 1
            available_at = trade["exit_at"] + timedelta(
                minutes=S.CONFIG.reentry_cooldown_minutes
            )
    return trades


def main():
    signals = S.collect()
    actual = S.execute(signals, **SCHEME)
    actual_metrics = S.metrics(actual)
    print("actual   ", actual_metrics)

    pool = entry_pool()
    print(f"entry pool: {sum(len(v) for v in pool.values())} bars over {len(pool)} sessions")

    per_date = {}
    for trade in actual:
        per_date[trade["date"]] = per_date.get(trade["date"], 0) + 1

    rng = random.Random(20260814)
    wins, totals, factors = [], [], []
    for _ in range(ITERATIONS):
        result = S.metrics(random_run(pool, per_date, rng))
        if not result:
            continue
        wins.append(result["win_rate"])
        totals.append(result["total_r"])
        factors.append(result["profit_factor"])

    wins.sort()
    totals.sort()

    def percentile(values, fraction):
        return values[min(len(values) - 1, int(fraction * len(values)))]

    print(f"\nzero-skill control over {len(wins)} random runs, identical exit scheme")
    print(f"  win%   mean {sum(wins)/len(wins):.1f}  p50 {percentile(wins,0.5):.1f}"
          f"  p95 {percentile(wins,0.95):.1f}  p99 {percentile(wins,0.99):.1f}  max {wins[-1]:.1f}")
    print(f"  totR   mean {sum(totals)/len(totals):+.2f}  p50 {percentile(totals,0.5):+.2f}"
          f"  p95 {percentile(totals,0.95):+.2f}  p99 {percentile(totals,0.99):+.2f}  max {totals[-1]:+.2f}")
    beat_win = sum(1 for value in wins if value >= actual_metrics["win_rate"]) / len(wins)
    beat_r = sum(1 for value in totals if value >= actual_metrics["total_r"]) / len(totals)
    print(f"\n  P(random win% >= {actual_metrics['win_rate']}) = {beat_win*100:.1f}%")
    print(f"  P(random totR >= {actual_metrics['total_r']}) = {beat_r*100:.1f}%")
    print(f"  P(random totR >  0)                = "
          f"{sum(1 for value in totals if value > 0)/len(totals)*100:.1f}%")


if __name__ == "__main__":
    main()
