"""Stability breakdown for the chosen exit scheme.

A single headline win rate can hide a strategy that only worked in one quarter
or one entry window. This prints the trade-by-trade record grouped by month,
entry window and option side.
"""
import os
import sys
from collections import defaultdict
from datetime import time
from math import floor

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges

import sweep_exits as S

SCHEME = {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5}
BASELINE = {"stop_percent": 0.10, "reward": 1.25}
START = 100_000.0
RISK = 0.02


def window(signal_at):
    clock = signal_at.time()
    if clock < time(13, 30):
        return "MORNING"
    if clock < time(14, 30):
        return "AFTERNOON"
    return "CLOSING"


def priced(trades):
    """Attach compounded 2%-risk position sizing and rupee P&L."""
    equity = START
    out = []
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        unit_risk = trade["risk"]
        lots = floor(equity * RISK / (unit_risk * NIFTY_LOT_SIZE))
        if not lots:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = trade["entry"] + trade["gross_r"] * unit_risk
        charges = estimate_option_charges(
            trade["entry"], max(exit_price, 0), quantity, trade["date"]
        )
        net = trade["gross_r"] * unit_risk * quantity - charges
        equity += net
        out.append({**trade, "lots": lots, "net": net, "equity": equity})
    return out


def group(trades, key):
    buckets = defaultdict(list)
    for trade in trades:
        buckets[key(trade)].append(trade)
    for name in sorted(buckets):
        rows = buckets[name]
        wins = sum(1 for row in rows if row["net"] > 0)
        total = sum(row["net"] for row in rows)
        print(f"  {name:<12}{len(rows):>4} trades{wins*100/len(rows):>7.1f}% win"
              f"{total:>12,.0f}")


def main():
    signals = S.collect()
    for label, scheme in (("TRAIL 0.5R", SCHEME), ("baseline 1.25R target", BASELINE)):
        trades = priced(S.execute(signals, **scheme))
        wins = sum(1 for trade in trades if trade["net"] > 0)
        total = sum(trade["net"] for trade in trades)
        print(f"\n=== {label} === {len(trades)} trades  "
              f"{wins*100/len(trades):.1f}% win  net Rs {total:+,.0f}  "
              f"ending Rs {START+total:,.0f}")
        print(" by month")
        group(trades, lambda trade: trade["date"][:7])
        print(" by window")
        group(trades, lambda trade: window(trade["signal_at"]))
        print(" by outcome")
        group(trades, lambda trade: trade["outcome"])
        losses = sorted(trades, key=lambda trade: trade["net"])[:3]
        gains = sorted(trades, key=lambda trade: -trade["net"])[:3]
        print(f" worst 3: {[round(trade['net']) for trade in losses]}")
        print(f" best  3: {[round(trade['net']) for trade in gains]}")
        without_best = total - sum(trade["net"] for trade in gains)
        print(f" net excluding best 3: Rs {without_best:+,.0f}")
        streak = worst_streak = 0
        for trade in trades:
            streak = streak + 1 if trade["net"] <= 0 else 0
            worst_streak = max(worst_streak, streak)
        print(f" longest losing streak: {worst_streak}")


if __name__ == "__main__":
    main()
