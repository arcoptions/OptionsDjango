"""The trail change at matched risk, and why it works.

The sweep says a 0.7R trail nearly doubles net rupees. It also says drawdown
rises from Rs 5,213 to Rs 8,876, and a bigger number that costs more to hold is
not obviously an improvement -- the request was explicitly for less drawdown,
not more profit at any price.

The fair comparison is at matched pain. Risk per trade is a free dial: the same
trade sequence at 1.2% instead of 2% produces a smaller version of the same
curve. So each trail is booked across a range of risk fractions, and the honest
question becomes: at the drawdown we already accept, which trail earns more?

The excursion diagnostic runs alongside, because a result you cannot explain is
a result you should not deploy.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger

from exit_lab import run

TRAILS = (0.5, 0.6, 0.7)
RISKS = (0.010, 0.012, 0.015, 0.020)


def main():
    config = nifty_trail_config()
    trades = {}
    for trail in TRAILS:
        trades[trail] = run(config, trail_gap=trail, record=True)
        print(f"  ran trail {trail}R", flush=True)

    print("\nexcursion while the position was open, by trail gap\n")
    print(f"{'trail':>7}{'realised R':>12}{'MFE R':>9}{'kept':>8}"
          f"{'bars held':>11}{'stops':>7}{'trails':>8}{'time':>7}")
    for trail in TRAILS:
        rows = trades[trail]
        realised = np.array([t["realized_r"] for t in rows])
        mfe = np.array([t["mfe_r"] for t in rows])
        held = np.array([t["held_bars"] for t in rows])
        live = mfe > 0.05
        outcomes = [t["outcome"] for t in rows]
        print(f"{trail:>6}R{realised.mean():>12.2f}{mfe.mean():>9.2f}"
              f"{100 * (realised[live] / mfe[live]).mean():>7.0f}%{held.mean():>11.1f}"
              f"{outcomes.count('STOP'):>7}{outcomes.count('TRAIL_EXIT'):>8}"
              f"{outcomes.count('TIME_EXIT'):>7}")

    ceiling = np.array([t["day_peak_r"] for t in trades[0.5]])
    print(f"\n  perfect foresight on the same entries, any exit before 15:20: "
          f"mean {ceiling.mean():.2f}R")
    print("  that is the size of the prize, not a reachable target")

    print("\n\nnet rupees and max drawdown at each risk per trade\n")
    print(f"{'trail':>7}" + "".join(f"{f'{100 * r:.1f}% risk':>20}" for r in RISKS))
    for trail in TRAILS:
        cells = []
        for risk in RISKS:
            ledger, _skipped, drawdown = sized_ledger(trades[trail],
                                                      risk_per_trade=risk)
            net = sum(row["net_pnl"] for row in ledger)
            cells.append(f"{net:>10,.0f} /{drawdown:>6,.0f}".rjust(20))
        print(f"{trail:>6}R" + "".join(cells))

    print("\nreturn earned per rupee of drawdown\n")
    print(f"{'trail':>7}" + "".join(f"{f'{100 * r:.1f}% risk':>20}" for r in RISKS))
    for trail in TRAILS:
        cells = []
        for risk in RISKS:
            ledger, _skipped, drawdown = sized_ledger(trades[trail],
                                                      risk_per_trade=risk)
            net = sum(row["net_pnl"] for row in ledger)
            cells.append(f"{net / drawdown:>8.2f}".rjust(20) if drawdown else
                         "n/a".rjust(20))
        print(f"{trail:>6}R" + "".join(cells))


if __name__ == "__main__":
    main()
