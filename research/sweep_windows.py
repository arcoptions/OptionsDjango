"""Entry-window / stop / trail sweep, scored in premium points captured.

The expiry-anatomy base-rate map showed the friendliest stretch for an option
buyer is roughly 11:00-12:30 IST -- a window the live config skips entirely
(it trades 09:30-10:59, then nothing until 13:30). This sweep collects the
candidate set once with a wide window and then filters by time, so window
choice costs nothing to test.

Scoring adds premium points captured per trade, since points -- not just win
rate -- is what the account actually banks.
"""
import os
import pickle
import sys
from dataclasses import replace
from datetime import time, timedelta
from statistics import median

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from options_tracker.strategy_backtest import (
    TIME_EXIT,
    _candidate,
    _distance,
    _number,
    _spot_context,
    _spot_setups,
    load_contract_rows,
)
from options_tracker.management.commands.research_normal_day_strategy import (
    normal_day_config,
)

import sweep_exits as S

WIDE = replace(normal_day_config(), entry_windows=((time(9, 25), time(15, 9)),))
CACHE = os.path.join(ROOT, "research", "wide_candidates.pkl")
VALIDATION_START = "2026-04-29"

WINDOW_SETS = {
    "live 930-1059+1330-1509": ((time(9, 30), time(10, 59)), (time(13, 30), time(15, 9))),
    "morning only 930-1059": ((time(9, 30), time(10, 59)),),
    "midday only 1100-1230": ((time(11, 0), time(12, 30)),),
    "midday wide 1100-1330": ((time(11, 0), time(13, 30)),),
    "morning+midday 930-1230": ((time(9, 30), time(12, 30)),),
    "morning+midday 930-1330": ((time(9, 30), time(13, 30)),),
    "all day 930-1509": ((time(9, 30), time(15, 9)),),
}
EXITS = {
    "S10_TRAIL0.5": {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5},
    "S15_TRAIL0.5": {"stop_percent": 0.15, "reward": None, "trail_gap": 0.5},
    "S20_TRAIL0.5": {"stop_percent": 0.20, "reward": None, "trail_gap": 0.5},
    "S20_TRAIL0.75": {"stop_percent": 0.20, "reward": None, "trail_gap": 0.75},
    "S25_TRAIL0.5": {"stop_percent": 0.25, "reward": None, "trail_gap": 0.5},
    "S30_TRAIL0.5": {"stop_percent": 0.30, "reward": None, "trail_gap": 0.5},
    "S15_TRAIL0.75": {"stop_percent": 0.15, "reward": None, "trail_gap": 0.75},
    "S15_T1.25": {"stop_percent": 0.15, "reward": 1.25},
}


def collect_wide():
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as handle:
            return pickle.load(handle)
    contracts = load_contract_rows("NIFTY", 1)
    spot_by_date, opening_ranges = _spot_context(contracts, WIDE.opening_range_minutes)
    spot_setups = _spot_setups(spot_by_date, opening_ranges, WIDE)
    signals = {}
    for contract_key, rows in contracts.items():
        if not contract_key[1]:
            continue
        first = WIDE.lookback + WIDE.confirmation_bars - 1
        for index in range(first, len(rows) - 1):
            candidate = _candidate(
                contract_key, rows, index, WIDE, spot_by_date, opening_ranges, spot_setups,
            )
            if not candidate:
                continue
            candidate["forward"] = [
                (
                    row["local_timestamp"],
                    _number(row["open"]),
                    _number(row["high"]),
                    _number(row["low"]),
                    _number(row["close"]),
                )
                for row in rows[candidate["next_index"]:]
                if row["local_timestamp"].time() <= TIME_EXIT
            ]
            candidate.pop("next_index")
            signals.setdefault(candidate["date"], []).append(candidate)
    with open(CACHE, "wb") as handle:
        pickle.dump(signals, handle)
    return signals


def in_windows(signal_at, windows):
    clock = signal_at.time()
    return any(start <= clock <= end for start, end in windows)


def execute(signals, windows, scheme, max_trades=3):
    trades = []
    for trade_date in sorted(signals):
        daily = [item for item in signals[trade_date] if in_windows(item["signal_at"], windows)]
        taken = 0
        available_at = None
        daily_r = 0.0
        for signal_at in sorted({item["signal_at"] for item in daily}):
            if taken >= max_trades or daily_r <= -WIDE.daily_loss_limit_r:
                break
            if available_at and signal_at < available_at:
                continue
            simultaneous = sorted(
                (item for item in daily if item["signal_at"] == signal_at),
                key=lambda item: (
                    item["volume_ratio"] * item["breakout_percent"],
                    -_distance(item["relative_strike"]),
                ),
                reverse=True,
            )
            for selected in simultaneous:
                trade = S.simulate(selected, **scheme)
                if not trade:
                    continue
                trade["points"] = trade["gross_r"] * trade["risk"]
                trades.append(trade)
                taken += 1
                daily_r += trade["net_r"]
                available_at = trade["exit_at"] + timedelta(
                    minutes=WIDE.reentry_cooldown_minutes
                )
                break
    return trades


def score(trades):
    if len(trades) < 10:
        return None
    wins = [trade for trade in trades if trade["net_r"] > 0]
    points = [trade["points"] for trade in trades]
    gross_profit = sum(t["net_r"] for t in trades if t["net_r"] > 0)
    gross_loss = abs(sum(t["net_r"] for t in trades if t["net_r"] < 0))
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade["net_r"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    validation = [t for t in trades if t["date"] >= VALIDATION_START]
    val_wins = [t for t in validation if t["net_r"] > 0]
    return {
        "n": len(trades),
        "win": round(100 * len(wins) / len(trades), 1),
        "pts_total": round(sum(points), 1),
        "pts_avg": round(sum(points) / len(trades), 2),
        "pts_med": round(median(points), 2),
        "totR": round(sum(t["net_r"] for t in trades), 2),
        "pf": round(gross_profit / gross_loss, 2) if gross_loss else 99.0,
        "dd": round(drawdown, 2),
        "valn": len(validation),
        "valwin": round(100 * len(val_wins) / len(validation), 1) if validation else 0,
    }


def main():
    signals = collect_wide()
    print(f"wide candidates: {sum(len(v) for v in signals.values())} "
          f"across {len(signals)} sessions\n")
    header = (
        f"{'window':<26}{'exit':<16}{'n':>4}{'win%':>7}{'ptsTot':>9}{'ptsAvg':>8}"
        f"{'ptsMed':>8}{'totR':>7}{'PF':>6}{'ddR':>7}{'valN':>6}{'valWin%':>8}"
    )
    print(header)
    print("-" * len(header))
    rows = []
    for window_name, windows in WINDOW_SETS.items():
        for exit_name, scheme in EXITS.items():
            result = score(execute(signals, windows, scheme))
            if result:
                rows.append((window_name, exit_name, result))
    rows.sort(key=lambda row: row[2]["pts_total"], reverse=True)
    for window_name, exit_name, result in rows:
        print(
            f"{window_name:<26}{exit_name:<16}{result['n']:>4}{result['win']:>7}"
            f"{result['pts_total']:>9}{result['pts_avg']:>8}{result['pts_med']:>8}"
            f"{result['totR']:>7}{result['pf']:>6}{result['dd']:>7}"
            f"{result['valn']:>6}{result['valwin']:>8}"
        )


if __name__ == "__main__":
    main()
