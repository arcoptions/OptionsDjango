"""Size the chosen exit scheme to a real Rs 1,00,000 account.

The prior backtest sized at a flat 10 lots, which peaked at Rs 1,61,616
deployed against Rs 1,00,000 of capital -- not executable. Here position size
is derived from equity that compounds trade by trade, under a per-trade risk
budget, and is hard-capped by the cash actually on hand.
"""
import os
import sys
from math import floor

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges

import sweep_exits as S

START = 100_000.0
SCHEMES = {
    "S10_TRAIL0.5": {"stop_percent": 0.10, "reward": None, "trail_gap": 0.5},
    "S10_T1.25 (current)": {"stop_percent": 0.10, "reward": 1.25},
    "S8_TRAIL0.75": {"stop_percent": 0.08, "reward": None, "trail_gap": 0.75},
    "S10_TRAIL0.75": {"stop_percent": 0.10, "reward": None, "trail_gap": 0.75},
}


def ledger(trades, risk_percent, cash_fraction, start=START):
    """Walk the trades in order, compounding equity and sizing off it."""
    equity = peak = start
    drawdown = 0.0
    executed = skipped = 0
    max_deployed = 0.0
    wins = 0
    worst = 0.0
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry = trade["entry"]
        unit_risk = trade["risk"]
        lot_cost = entry * NIFTY_LOT_SIZE
        risk_lots = floor(equity * risk_percent / (unit_risk * NIFTY_LOT_SIZE))
        cash_lots = floor(equity * cash_fraction / lot_cost)
        lots = max(0, min(risk_lots, cash_lots))
        if not lots:
            skipped += 1
            continue
        quantity = lots * NIFTY_LOT_SIZE
        # net_rupees from the sweep is per-lot and already net of one lot's
        # charges; rebuild gross then charge the real quantity.
        gross = trade["gross_r"] * unit_risk * quantity
        exit_price = entry + trade["gross_r"] * unit_risk
        charges = estimate_option_charges(entry, max(exit_price, 0), quantity, trade["date"])
        net = gross - charges
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        max_deployed = max(max_deployed, lots * lot_cost)
        executed += 1
        wins += net > 0
        worst = min(worst, net)
    return {
        "executed": executed,
        "skipped": skipped,
        "win_rate": round(100 * wins / executed, 1) if executed else 0,
        "ending": round(equity),
        "return_pct": round((equity / start - 1) * 100, 1),
        "max_dd": round(drawdown),
        "max_dd_pct": round(100 * drawdown / peak, 1) if peak else 0,
        "max_deployed": round(max_deployed),
        "deployed_pct": round(100 * max_deployed / start, 1),
        "worst_trade": round(worst),
    }


def main():
    signals = S.collect()
    print(f"start capital Rs {START:,.0f}   lot {NIFTY_LOT_SIZE}\n")
    header = (
        f"{'scheme':<22}{'risk%':>6}{'cash%':>6}{'n':>4}{'skip':>5}{'win%':>6}"
        f"{'ending':>10}{'ret%':>7}{'maxDD':>9}{'DD%':>6}{'peakDeploy':>12}{'dep%':>6}{'worst':>9}"
    )
    print(header)
    print("-" * len(header))
    for name, scheme in SCHEMES.items():
        trades = S.execute(signals, **scheme)
        for risk_percent in (0.01, 0.02, 0.03):
            for cash_fraction in (0.25, 0.40, 0.60):
                result = ledger(trades, risk_percent, cash_fraction)
                print(
                    f"{name:<22}{risk_percent*100:>6.0f}{cash_fraction*100:>6.0f}"
                    f"{result['executed']:>4}{result['skipped']:>5}{result['win_rate']:>6}"
                    f"{result['ending']:>10,}{result['return_pct']:>7}"
                    f"{result['max_dd']:>9,}{result['max_dd_pct']:>6}"
                    f"{result['max_deployed']:>12,}{result['deployed_pct']:>6}"
                    f"{result['worst_trade']:>9,}"
                )
        print()


if __name__ == "__main__":
    main()
