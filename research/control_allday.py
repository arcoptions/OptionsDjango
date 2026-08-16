"""Zero-skill control for the all-day entry window.

Opening the entry window adds 16 trades. Those extra trades have to earn their
place: if random entries taken all day do just as well, the window change is
just more exposure, not more edge. The pool here is rebuilt over the full
09:30-15:09 span so the control samples the same universe the signal does.
"""
import os
import pickle
import random
import sys
from datetime import time, timedelta

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.strategy_backtest import (
    TIME_EXIT,
    _distance,
    _matches_moneyness,
    _number,
    load_contract_rows,
)

import sweep_exits as S
import sweep_windows as W

POOL = os.path.join(ROOT, "research", "entry_pool_wide.pkl")
SCHEME = {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5}
WINDOWS = ((time(9, 30), time(15, 9)),)
ITERATIONS = 1000


def wide_pool():
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
            if not (time(9, 30) <= clock <= time(15, 9)):
                continue
            if row["option_type"] not in S.CONFIG.option_types:
                continue
            if not (S.CONFIG.premium_min <= premium <= S.CONFIG.premium_max):
                continue
            if _distance(row["relative_strike"]) > S.CONFIG.max_distance:
                continue
            if not _matches_moneyness(row["relative_strike"], row["option_type"], S.CONFIG):
                continue
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
            pool.setdefault(row["local_timestamp"].date().isoformat(), []).append(
                {
                    "date": row["local_timestamp"].date().isoformat(),
                    "signal_at": row["local_timestamp"],
                    "signal_close": premium,
                    "forward": forward,
                }
            )
    with open(POOL, "wb") as handle:
        pickle.dump(pool, handle)
    return pool


def random_run(pool, per_date, rng):
    trades = []
    for date, count in per_date.items():
        options = pool.get(date)
        if not options:
            continue
        available_at = None
        taken = 0
        for candidate in rng.sample(options, min(len(options), 300)):
            if taken >= count:
                break
            if available_at and candidate["signal_at"] < available_at:
                continue
            trade = S.simulate(candidate, **SCHEME)
            if not trade:
                continue
            trades.append(trade)
            taken += 1
            available_at = trade["exit_at"] + timedelta(
                minutes=S.CONFIG.reentry_cooldown_minutes
            )
    return trades


def main():
    signals = W.collect_wide()
    actual = W.execute(signals, WINDOWS, SCHEME)
    summary = S.metrics(actual)
    print("actual all-day:", summary)

    pool = wide_pool()
    print(f"pool: {sum(len(v) for v in pool.values())} bars over {len(pool)} sessions")
    per_date = {}
    for trade in actual:
        per_date[trade["date"]] = per_date.get(trade["date"], 0) + 1

    rng = random.Random(20260814)
    wins, totals = [], []
    for _ in range(ITERATIONS):
        result = S.metrics(random_run(pool, per_date, rng))
        if result:
            wins.append(result["win_rate"])
            totals.append(result["total_r"])
    wins.sort()
    totals.sort()

    def percentile(values, fraction):
        return values[min(len(values) - 1, int(fraction * len(values)))]

    print(f"\nzero-skill control, {len(wins)} runs, identical exit and window")
    print(f"  win%  mean {sum(wins)/len(wins):.1f}  p95 {percentile(wins,0.95):.1f}"
          f"  p99 {percentile(wins,0.99):.1f}")
    print(f"  totR  mean {sum(totals)/len(totals):+.2f}  p95 {percentile(totals,0.95):+.2f}"
          f"  p99 {percentile(totals,0.99):+.2f}")
    print(f"\n  P(random win% >= {summary['win_rate']}) = "
          f"{100*sum(1 for v in wins if v >= summary['win_rate'])/len(wins):.1f}%")
    print(f"  P(random totR >= {summary['total_r']}) = "
          f"{100*sum(1 for v in totals if v >= summary['total_r'])/len(totals):.1f}%")


if __name__ == "__main__":
    main()
