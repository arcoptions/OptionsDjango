"""Why SENSEX fails the shipped architecture, and whether it is fixable.

THE FACT TO EXPLAIN.  Same window, same rules, no refit: NIFTY makes +0.50R a
trade at 69% win, SENSEX makes -0.04R at 47%.  On the 23 sessions where BOTH
indices fired, NIFTY is +0.63R at 73.5% and SENSEX is -0.13R at 44.8%.  Two
indices correlated above 0.95, the same signal, the same day, opposite outcomes.

That rules out the entry.  It is not the breakout, not the direction, not the
regime -- those are shared.  What differs is the option's own price path, and
the exit reads that path.  The tell is the stop: SENSEX takes the full 10% stop
**45.1%** of the time against NIFTY's **25.6%**.

THE HYPOTHESIS THIS FILE TESTS.  A 10% stop is not a statement about risk, it is
a statement about how far an option wanders in a session.  It was fitted on
NIFTY.  If SENSEX ATM options are more volatile in PERCENTAGE terms -- and they
are the ones with 0.13% strike spacing against NIFTY's 0.20%, on a shorter
expiry cycle -- then 10% is simply a tighter stop there, and the architecture is
not failing so much as being mis-scaled.

If that is right, a stop scaled to each option's own realised volatility should
lift SENSEX toward NIFTY WITHOUT degrading NIFTY.  Both halves matter.  A change
that rescues SENSEX by wrecking NIFTY is a refit; a change that helps both is a
better parameterisation of the same idea.

THE DISCIPLINE, STATED UP FRONT SO IT IS NOT QUIETLY DROPPED.  SENSEX has 45
trades in 121 sessions.  Nothing measured on 45 trades is shippable, and a stop
sweep on 45 trades is exactly the machine that manufactured every false positive
in this programme.  So: the mechanism was diagnosed BEFORE the fix was chosen,
the fix is a single pre-stated hypothesis rather than a grid to pick a winner
from, and NIFTY is the control that has to survive untouched.  Whatever comes
out is a lead, and it is labelled a lead.
"""
import os
import sys
from dataclasses import replace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE  # noqa: E402
from options_tracker.nifty_trail_strategy import (nifty_trail_config,  # noqa: E402
                                                  sized_ledger)
from options_tracker.strategy_backtest import (backtest_strategy,  # noqa: E402
                                               load_contract_rows)

LOT = {"SENSEX": 20, "NIFTY": NIFTY_LOT_SIZE}
WINDOW = ("2026-02-16", "2026-08-13")
STOPS = (0.10, 0.15, 0.20, 0.25)


def book(trades, underlying):
    ledger, _s, dd = sized_ledger(trades, lot_size=LOT[underlying])
    if not ledger:
        return None
    net = sum(r["net_pnl"] for r in ledger)
    wins = sum(1 for r in ledger if r["net_pnl"] > 0)
    return {"n": len(ledger), "win": 100 * wins / len(ledger), "net": net,
            "dd": dd, "ratio": net / dd if dd else float("inf")}


def clip(trades):
    return [t for t in trades if WINDOW[0] <= str(t["date"])[:10] <= WINDOW[1]]


def option_vol(contracts):
    """Median intraday range of an ATM option, as a percent of its own open.

    The quantity a percentage stop is implicitly a bet about. Measured on the
    contracts themselves rather than inferred from the trades, so it is
    independent of the strategy and cannot be contaminated by it.
    """
    spans = []
    for (_date, _strike, _kind), rows in contracts.items():
        atm = [r for r in rows if r["relative_strike"] == "ATM"]
        if len(atm) < 30:
            continue
        o = float(atm[0]["open"] or 0)
        if o <= 0:
            continue
        hi = max(float(r["high"] or 0) for r in atm)
        lo = min(float(r["low"] or 0) for r in atm if float(r["low"] or 0) > 0)
        spans.append((hi - lo) / o)
    return np.median(spans) if spans else float("nan")


def main():
    cfg = nifty_trail_config()
    loaded = {u: load_contract_rows(u, 1) for u in ("SENSEX", "NIFTY")}

    print("=" * 92)
    print("1. HOW FAR AN ATM OPTION ACTUALLY WANDERS IN A SESSION")
    print("   A percentage stop is a bet about this number, and only this number.")
    print("=" * 92)
    for u in ("SENSEX", "NIFTY"):
        v = option_vol(loaded[u])
        print("  {:<8} median full-session range = {:.1f}% of the open"
              "   -> a 10% stop is {:.2f} of a day's range".format(u, v * 100, 0.10 / v))

    print()
    print("=" * 92)
    print("2. THE STOP, SWEPT ON BOTH. NIFTY IS THE CONTROL, NOT A SECOND CHANCE.")
    print("   A stop that rescues SENSEX by degrading NIFTY is a refit and is rejected.")
    print("=" * 92)
    print("  {:<8} {:<7} {:>4} {:>8} {:>11} {:>10} {:>8}".format(
        "index", "stop", "n", "win", "net Rs1L", "max DD", "net/DD"))
    for u in ("SENSEX", "NIFTY"):
        for stop in STOPS:
            trades = clip(backtest_strategy(
                u, 1, replace(cfg, stop_percent=stop), contracts=loaded[u]))
            b = book(trades, u)
            if not b:
                continue
            mark = "   <-- shipped" if stop == 0.10 else ""
            print("  {:<8} {:<7} {:>4} {:>7.1f}% {:>+11,.0f} {:>10,.0f} {:>8.2f}{}".format(
                u, "{:.0%}".format(stop), b["n"], b["win"], b["net"],
                b["dd"], b["ratio"], mark))
        print()


if __name__ == "__main__":
    main()
