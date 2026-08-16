"""Final head-to-head in rupees, not points.

Points captured and position size pull in opposite directions: a wider stop
banks more premium points per trade but forces a smaller position at the same
risk budget, so more points can mean less money. Only the compounded rupee
ledger settles it.
"""
import os
import sys
from datetime import time
from math import floor

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges

import sweep_windows as W

START = 100_000.0
RISK = 0.02
CASH_CAP = 0.40

LIVE = ((time(9, 30), time(10, 59)), (time(13, 30), time(15, 9)))
ALLDAY = ((time(9, 30), time(15, 9)),)

CASES = [
    ("live windows", LIVE, "10% stop, 0.5R trail", {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5}),
    ("live windows", LIVE, "15% stop, 0.5R trail", {"stop_percent": 0.15, "reward": None, "trail_gap": 0.5}),
    ("live windows", LIVE, "20% stop, 0.5R trail", {"stop_percent": 0.20, "reward": None, "trail_gap": 0.5}),
    ("live windows", LIVE, "30% stop, 0.5R trail", {"stop_percent": 0.30, "reward": None, "trail_gap": 0.5}),
    ("live windows", LIVE, "15% stop, 1.25R target", {"stop_percent": 0.15, "reward": 1.25}),
    ("all day", ALLDAY, "10% stop, 0.5R trail", {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5}),
    ("all day", ALLDAY, "15% stop, 0.5R trail", {"stop_percent": 0.15, "reward": None, "trail_gap": 0.5}),
    ("all day", ALLDAY, "20% stop, 0.5R trail", {"stop_percent": 0.20, "reward": None, "trail_gap": 0.5}),
]


def rupees(trades):
    equity = peak = START
    drawdown = 0.0
    wins = executed = 0
    points = 0.0
    worst = 0.0
    streak = longest = 0
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        unit_risk = trade["risk"]
        lots = min(
            floor(equity * RISK / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * CASH_CAP / (trade["entry"] * NIFTY_LOT_SIZE)),
        )
        if lots < 1:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = trade["entry"] + trade["gross_r"] * unit_risk
        charges = estimate_option_charges(
            trade["entry"], max(exit_price, 0), quantity, trade["date"]
        )
        net = (exit_price - trade["entry"]) * quantity - charges
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        executed += 1
        wins += net > 0
        points += exit_price - trade["entry"]
        worst = min(worst, net)
        streak = streak + 1 if net <= 0 else 0
        longest = max(longest, streak)
    if not executed:
        return None
    return {
        "n": executed,
        "win": round(100 * wins / executed, 1),
        "points": round(points, 1),
        "net": round(equity - START),
        "ret": round(100 * (equity - START) / START, 1),
        "dd": round(drawdown),
        "ddpct": round(100 * drawdown / peak, 1),
        "worst": round(worst),
        "streak": longest,
    }


def main():
    signals = W.collect_wide()
    header = (
        f"{'windows':<14}{'exit':<24}{'n':>4}{'win%':>7}{'points':>9}"
        f"{'net Rs':>10}{'ret%':>7}{'maxDD':>9}{'DD%':>6}{'worst':>9}{'streak':>7}"
    )
    print(header)
    print("-" * len(header))
    for window_name, windows, exit_name, scheme in CASES:
        result = rupees(W.execute(signals, windows, scheme))
        if not result:
            continue
        print(
            f"{window_name:<14}{exit_name:<24}{result['n']:>4}{result['win']:>7}"
            f"{result['points']:>9}{result['net']:>10,}{result['ret']:>7}"
            f"{result['dd']:>9,}{result['ddpct']:>6}{result['worst']:>9,}"
            f"{result['streak']:>7}"
        )


if __name__ == "__main__":
    main()
