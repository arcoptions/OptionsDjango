"""The pivot strategy in rupees, on the same account and cost model as the live one.

R-multiples hide the two things that decide whether a signal is worth trading:
how much premium 1R actually is, and how many times you pay brokerage to collect
it. At 1.7 trades a session the charge line is not a rounding error.
"""
import os
import sys
from math import floor

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges

import swing_trade as T

START, RISK, CASH = 100_000.0, 0.02, 0.40


def ledger(trades):
    equity = peak = START
    drawdown = gross_total = charge_total = 0.0
    wins = count = 0
    for trade in sorted(trades, key=lambda item: (item["date"], item["exit_row"])):
        entry = trade["entry"]
        unit_risk = entry * T.STOP_PERCENT
        lots = min(
            floor(equity * RISK / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * CASH / (entry * NIFTY_LOT_SIZE)),
        )
        if lots < 1:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = entry + trade["r"] * unit_risk
        charges = estimate_option_charges(
            entry, max(exit_price, 0), quantity, trade["date"]
        )
        gross = (exit_price - entry) * quantity
        equity += gross - charges
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        gross_total += gross
        charge_total += charges
        wins += gross - charges > 0
        count += 1
    return count, wins, gross_total, charge_total, equity - START, drawdown


def main():
    header = (f"{'minSwing':>9}{'n':>6}{'win%':>7}{'gross Rs':>12}{'charges':>11}"
              f"{'net Rs':>11}{'ret%':>8}{'maxDD':>10}")
    print(header)
    print("-" * len(header))
    for minimum_swing in (30, 50, 70):
        count, wins, gross, charges, net, drawdown = ledger(
            T.run(minimum_swing, "reversal")
        )
        print(f"{minimum_swing:>9}{count:>6}{100*wins/count:>7.1f}{gross:>12,.0f}"
              f"{charges:>11,.0f}{net:>11,.0f}{100*net/START:>8.1f}{drawdown:>10,.0f}")


if __name__ == "__main__":
    main()
