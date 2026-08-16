"""Can risk per trade actually be dialled at Rs 1,00,000?

The matched-risk table jumped around: 0.5R earned Rs 6,909 at 1.0% risk and
Rs 13,750 at 1.2%, which is not how a smooth risk dial behaves. The suspicion is
arithmetic rather than edge. Size is floor(equity * risk / (unit risk * 65)), and
one NIFTY lot is 65 contracts, so on a one lakh account the answer is usually
zero, one or two lots. A trade that rounds to zero is not taken at all, so
lowering risk silently deletes trades instead of shrinking them.

If that is what is happening, "run the wider trail at lower risk to keep the old
drawdown" is not advice that survives contact with the lot size, and it should
not be offered.
"""
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger

from exit_lab import run

RISKS = (0.010, 0.012, 0.015, 0.020, 0.025, 0.030)


def main():
    config = nifty_trail_config()
    trades = run(config, trail_gap=0.7)
    print(f"  {len(trades)} signals at a 0.7R trail\n", flush=True)

    print(f"{'risk':>7}{'taken':>7}{'skipped':>9}{'net Rs':>11}{'maxDD':>9}"
          f"{'net/DD':>9}   lots taken")
    for risk in RISKS:
        ledger, skipped, drawdown = sized_ledger(trades, risk_per_trade=risk)
        if not ledger:
            print(f"{100 * risk:>6.1f}%{0:>7}{len(skipped):>9}   nothing sized")
            continue
        net = sum(row["net_pnl"] for row in ledger)
        spread = Counter(row["lots"] for row in ledger)
        shape = "  ".join(f"{lots}x{spread[lots]}" for lots in sorted(spread))
        print(f"{100 * risk:>6.1f}%{len(ledger):>7}{len(skipped):>9}{net:>11,.0f}"
              f"{drawdown:>9,.0f}{net / drawdown:>9.2f}   {shape}")

    print("\nlots is the position size in NIFTY lots of 65; 'AxB' reads "
          "B trades sized at A lots")


if __name__ == "__main__":
    main()
