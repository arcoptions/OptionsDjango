"""Run the NIFTY trailing strategy on SENSEX, unchanged except for scale.

This is an out-of-sample test in the strictest sense available here: a
different instrument, a different exchange, and a date range (2026-02-16
onward) that barely overlaps the NIFTY sample. Only the parameters that are
mechanically tied to instrument scale are adjusted -- premium band and lot
size. Signal logic, stop and trail are identical.
"""
import os
import sys
from dataclasses import replace

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger
from options_tracker.strategy_backtest import backtest_strategy

SENSEX_LOT_SIZE = 20


def summary(trades, ledger, drawdown, label, capital):
    if not ledger:
        print(f"{label}: {len(trades)} signals, none executable")
        return
    wins = [row for row in ledger if row["net_pnl"] > 0]
    net = sum(row["net_pnl"] for row in ledger)
    points = sum(
        (row["entry"] + row["realized_r"] * (row["entry"] - row["stop_loss"])) - row["entry"]
        for row in ledger
    )
    streak = longest = 0
    for row in ledger:
        streak = streak + 1 if row["net_pnl"] <= 0 else 0
        longest = max(longest, streak)
    print(
        f"{label:<10}{len(ledger):>5} trades{100*len(wins)/len(ledger):>7.1f}% win"
        f"{points:>10.1f} pts{points/len(ledger):>8.2f} avg"
        f"{net:>11,.0f} net{100*net/capital:>8.1f}%"
        f"{drawdown:>10,.0f} dd{longest:>4} streak"
    )


def main():
    capital = 100_000.0
    print("SENSEX, same signal logic as NIFTY, premium band and lot size rescaled\n")
    for premium_min, premium_max in ((150, 800), (200, 600), (150, 1200)):
        config = replace(
            nifty_trail_config(), premium_min=premium_min, premium_max=premium_max
        )
        trades = backtest_strategy("SENSEX", 1, config)
        ledger, _skipped, drawdown = sized_ledger(
            trades, starting_capital=capital, lot_size=SENSEX_LOT_SIZE
        )
        summary(trades, ledger, drawdown, f"{premium_min}-{premium_max}", capital)

    print("\nNIFTY over the same date window for comparison")
    trades = [
        trade
        for trade in backtest_strategy("NIFTY", 1, nifty_trail_config())
        if trade["date"] >= "2026-02-16"
    ]
    ledger, _skipped, drawdown = sized_ledger(trades, starting_capital=capital)
    summary(trades, ledger, drawdown, "NIFTY", capital)


if __name__ == "__main__":
    main()
