"""Buying the drawdown back with sizing rules.

Widening the trail to 0.7R nearly doubles net rupees, and costs drawdown:
Rs 8,876 against the shipped Rs 5,213. The request was for less drawdown, not
more, so the wider trail is only an improvement if that can be paid for.

Cutting risk per trade cannot pay for it -- at one lakh with a 65 lot, sizing is
one to three lots and dropping the risk fraction deletes trades rather than
shrinking them. What might work instead is cutting risk only when the account is
already hurting, which is the part of the curve the drawdown number comes from.

Two versions, both applied to the identical trade sequence so nothing here is
selection: throttle when equity is below its high water mark by some percentage,
and throttle after a run of consecutive losses. Both restore on a new high.

A throttle can only ever remove trades from the ledger, so a rule that improves
net *and* drawdown is suspicious and a rule that trades some net for a lot less
drawdown is the realistic best case.
"""
import os
import sys
from math import floor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.capital_pnl import (NIFTY_LOT_SIZE,
                                         estimate_option_charges)
from options_tracker.nifty_trail_strategy import (MAX_CASH_FRACTION,
                                                  RISK_PER_TRADE,
                                                  STARTING_CAPITAL,
                                                  nifty_trail_config)

from exit_lab import run


def book(trades, risk_for=None):
    """The shipped ledger, with risk per trade decided by a callable.

    risk_for(drop, streak) sees how far equity is below its high water mark and
    how many losses have run in a row, and returns the risk fraction to use for
    the next trade. Everything else matches sized_ledger exactly.
    """
    equity = peak = STARTING_CAPITAL
    drawdown = 0.0
    net_total = 0.0
    taken = wins = skipped = 0
    streak = 0
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry = trade["entry"]
        unit_risk = entry - trade["stop_loss"]
        if unit_risk <= 0:
            skipped += 1
            continue
        drop = (peak - equity) / peak
        risk = RISK_PER_TRADE if risk_for is None else risk_for(drop, streak)
        lots = max(0, min(
            floor(equity * risk / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE)),
        ))
        if not lots:
            skipped += 1
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = entry + trade["realized_r"] * unit_risk
        gross = (exit_price - entry) * quantity
        net = gross - estimate_option_charges(entry, exit_price, quantity,
                                              trade["date"])
        equity += net
        net_total += net
        taken += 1
        if net > 0:
            wins += 1
            streak = 0
        else:
            streak += 1
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "skipped": skipped, "win": 100 * wins / taken if taken else 0,
            "net": net_total, "dd": drawdown}


def show(name, result):
    if not result["n"]:
        print(f"  {name:<38}   nothing sized")
        return
    print(f"  {name:<38}{result['n']:>5}{result['skipped']:>6}"
          f"{result['win']:>8.1f}{result['net']:>11,.0f}{result['dd']:>9,.0f}"
          f"{result['net'] / result['dd']:>8.2f}")


def main():
    trades = run(nifty_trail_config(), trail_gap=0.7)
    print(f"{len(trades)} signals at a 0.7R trail\n", flush=True)
    print(f"  {'sizing rule':<38}{'n':>5}{'skip':>6}{'win%':>8}"
          f"{'net Rs':>11}{'maxDD':>9}{'net/DD':>8}")

    show("flat 2.0% (the candidate)", book(trades))
    for limit in (0.04, 0.06, 0.08):
        show(f"halve risk while {100 * limit:.0f}% below peak",
             book(trades, lambda drop, streak, limit=limit:
                  0.01 if drop >= limit else RISK_PER_TRADE))
    for limit in (0.04, 0.06, 0.08):
        show(f"stand aside while {100 * limit:.0f}% below peak",
             book(trades, lambda drop, streak, limit=limit:
                  0.0 if drop >= limit else RISK_PER_TRADE))
    for run_length in (2, 3):
        show(f"halve risk after {run_length} straight losses",
             book(trades, lambda drop, streak, n=run_length:
                  0.01 if streak >= n else RISK_PER_TRADE))
        show(f"stand aside after {run_length} straight losses",
             book(trades, lambda drop, streak, n=run_length:
                  0.0 if streak >= n else RISK_PER_TRADE))

    print("\n  for reference, the shipped 0.5R trail at flat 2.0%")
    show("shipped", book(run(nifty_trail_config(), trail_gap=0.5)))


if __name__ == "__main__":
    main()
