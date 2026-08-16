"""Take half off at a target, trail the rest -- does it pay?

There is no target in the shipped strategy today. `reward_risk` is computed and
written into the trade record, but `_simulate` only consults it when the trail is
off, and the trail is always on. So this is not tuning an existing rule, it is
adding one.

The runner keeps trailing on exactly the same rule, so its exit is unchanged from
the baseline. That means the whole scale-out can be evaluated exactly from one
backtest per trail: the only thing that changes is the first half, which fills at
the target whenever the position's best price while open reached it. No
re-simulation and no drift in the trade set.

Two things get modelled that a spreadsheet version of this would miss. Scaling
out is a second sell ticket, so the fixed brokerage is paid twice. And half of an
odd number of lots is not half -- at one lakh the strategy sizes one to three
lots, and half of one lot is nothing at all, so the rule is checked against the
sizes actually taken rather than against a notional 10 lots.
"""
import os
import sys
from math import floor

import numpy as np

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
                                                  nifty_trail_config)

from exit_lab import run

TARGETS = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
TRAILS = (0.5, 0.7)


def book(trades, capital, target_r=None):
    """Compound the account, optionally selling half the lots at target_r.

    The half that stays on exits where the unsplit trade would have exited, so
    the only difference is the first leg. With an odd lot count the smaller half
    is sold, and with a single lot nothing is sold, because a fraction of a
    NIFTY lot cannot be traded.
    """
    equity = peak = capital
    drawdown = 0.0
    net_total = 0.0
    taken = wins = splits = 0
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry = trade["entry"]
        unit_risk = entry - trade["stop_loss"]
        if unit_risk <= 0:
            continue
        lots = max(0, min(
            floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE)),
        ))
        if not lots:
            continue
        exit_price = entry + trade["realized_r"] * unit_risk
        reached = target_r is not None and trade["mfe_r"] >= target_r
        legs = [(lots, exit_price)]
        if reached and lots >= 2:
            out = lots // 2
            legs = [(out, entry + target_r * unit_risk), (lots - out, exit_price)]
            splits += 1
        net = 0.0
        for leg_lots, price in legs:
            quantity = leg_lots * NIFTY_LOT_SIZE
            net += ((price - entry) * quantity
                    - estimate_option_charges(entry, price, quantity,
                                              trade["date"]))
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "splits": splits, "win": 100 * wins / taken if taken else 0,
            "net": net_total, "dd": drawdown,
            "growth": 100 * net_total / capital}


def show(label, result):
    print(f"  {label:<26}{result['n']:>5}{result['splits']:>8}{result['win']:>8.1f}"
          f"{result['net']:>11,.0f}{result['growth']:>8.1f}%{result['dd']:>9,.0f}")


def main():
    for trail in TRAILS:
        trades = run(nifty_trail_config(), trail_gap=trail, record=True)
        mfe = np.array([t["mfe_r"] for t in trades])
        print(f"\n\n{'=' * 78}\ntrail {trail}R, {len(trades)} trades", flush=True)
        print("  how often the position ever reached each target while open:")
        print("   " + "   ".join(f"{t}R {100 * (mfe >= t).mean():.0f}%"
                                 for t in TARGETS))

        for capital in (100_000, 500_000):
            lots = []
            equity = capital
            for trade in sorted(trades, key=lambda i: i["signal_at"]):
                unit_risk = trade["entry"] - trade["stop_loss"]
                if unit_risk > 0:
                    lots.append(min(
                        floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE)),
                        floor(equity * MAX_CASH_FRACTION
                              / (trade["entry"] * NIFTY_LOT_SIZE))))
            single = sum(1 for value in lots if value == 1)
            print(f"\n  starting capital Rs {capital:,}"
                  f"   ({single} of {len(lots)} trades size to a single lot at "
                  f"the opening equity, so cannot be split)")
            print(f"  {'rule':<26}{'n':>5}{'splits':>8}{'win%':>8}"
                  f"{'net Rs':>11}{'growth':>9}{'maxDD':>9}")
            show("trail only, no target", book(trades, capital))
            for target in TARGETS:
                show(f"half out at {target}R", book(trades, capital, target))


if __name__ == "__main__":
    main()
