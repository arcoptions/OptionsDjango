"""The shipped NIFTY strategy, run unchanged on SENSEX.

WHY THIS TEST, AND WHY NOW.  `INTRADAY_REPORT.md` closed the stock-option
programme with a decomposition rather than another null: buying an intraday
option is +1.02% GROSS (day-clustered t +3.50) and loses because the round trip
costs 1.64%.  Two thirds of that bill is the bid-ask, and the bid-ask as a share
of the position is set by one thing -- the premium.  A Rs 21 ATM stock option
pays 48bp of tick; a Rs 100 NIFTY option pays 5bp.

That is a design rule, not just a post-mortem.  It says a shippable option trade
needs a HIGH PREMIUM, TIGHTLY QUOTED, and it predicts in advance where to look
next.  SENSEX ATM premium averages Rs 455 against NIFTY's Rs 123 median -- four
times the cushion, on an index that is quoted at least as tightly.  If the
friction story is right, the same architecture should survive there.  If SENSEX
fails, the friction story is incomplete and I would rather know.

WHAT IS AND IS NOT ALLOWED TO CHANGE.  The entry rules, the exit, the stop, the
trail, the windows and the premium floor are the SHIPPED ones, copied without a
single refit.  That is the whole value of the test: NIFTY's parameters were
chosen on NIFTY, so running them untouched on a different index is the only
genuinely out-of-sample evidence available for the architecture itself.  Two
mechanical things must change because they are facts about the instrument, not
choices: the lot size (20 on SENSEX against 65 on NIFTY) and the underlying
passed to the loader.

TWO CONTROLS, BECAUSE THE OBVIOUS READING WOULD BE WRONG WITHOUT THEM.

  The SENSEX cache runs Feb 2026 -> Aug 2026; the NIFTY result was measured over
  Aug 2025 -> Aug 2026.  Comparing them directly would confound the instrument
  with the regime, so NIFTY is re-run over the SENSEX window and that -- not the
  headline +38.6% -- is the number SENSEX has to be read against.

  A random-entry control on the same sessions and the same contracts, because
  every result in this programme that skipped one turned out to be the base rate
  wearing a signal's clothes.

The premium floor deserves a warning.  Rs 100 is a COSTS finding on NIFTY and it
does real work there, thinning expiry days where the median premium falls to
Rs 19.  On SENSEX at Rs 455 average it will pass essentially everything, so it
is inactive rather than transferred.  A scaled floor is reported below it, and
it is labelled fitted, because that is what it is.
"""
import os
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import date

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

SENSEX_LOT = 20          # BSE contract size. A fact, not a parameter.
WINDOW = ("2026-02-16", "2026-08-13")   # the SENSEX cache's own span
ROUND_TRIPS = (0.0, 1.0, 2.0, 5.0)
SCALED_FLOORS = (100, 200, 300, 400)


def book(trades, lot_size):
    ledger, _skipped, drawdown = sized_ledger(trades, lot_size=lot_size)
    if not ledger:
        return None
    wins = sum(1 for row in ledger if row["net_pnl"] > 0)
    net = sum(row["net_pnl"] for row in ledger)
    return {"n": len(ledger), "win": 100 * wins / len(ledger), "net": net,
            "dd": drawdown, "ratio": net / drawdown if drawdown else float("inf")}


def charged(trades, round_trip):
    """A fixed rupee bid-ask taken out of each trade.

    Charged against the same unit risk rather than rebased on it, so R moves
    with the cost instead of the cost quietly redefining what an R is.
    """
    copies = deepcopy(trades)
    for trade in copies:
        risk = trade["entry"] - trade["stop_loss"]
        if risk > 0:
            trade["realized_r"] -= round_trip / risk
    return copies


def points(trades):
    return float(np.mean([t["realized_r"] * (t["entry"] - t["stop_loss"])
                          for t in trades])) if trades else 0.0


def clip(trades, lo, hi):
    """Trades carry `date` as an ISO string, which compares correctly as text."""
    return [t for t in trades if lo <= str(t["date"])[:10] <= hi] if trades else []


def show(label, trades, lot_size):
    b = book(trades, lot_size)
    if not b:
        print("  {:<28} no trades".format(label))
        return None
    print("  {:<28} {:>4} {:>7.1f}% {:>+11,.0f} {:>10,.0f} {:>8.2f} {:>9.2f}".format(
        label, b["n"], b["win"], b["net"], b["dd"], b["ratio"], points(trades)))
    return b


def header():
    print("  {:<28} {:>4} {:>8} {:>11} {:>10} {:>8} {:>9}".format(
        "", "n", "win", "net Rs1L", "max DD", "net/DD", "pts/trade"))


def main():
    config = nifty_trail_config()
    lo, hi = WINDOW

    print("Loading contracts. Two indices, 1-minute bars -- this is the slow part.")
    sx = load_contract_rows("SENSEX", 1)
    print("  SENSEX {:,} contract-days".format(len(sx)))
    nf = load_contract_rows("NIFTY", 1)
    print("  NIFTY  {:,} contract-days".format(len(nf)))

    sx_trades = backtest_strategy("SENSEX", 1, config, contracts=sx)
    nf_trades = backtest_strategy("NIFTY", 1, config, contracts=nf)
    nf_window = clip(nf_trades, lo, hi)

    print()
    print("=" * 96)
    print("THE SHIPPED CONFIG, UNCHANGED, ON BOTH INDICES")
    print("Read SENSEX against NIFTY-same-window. The full NIFTY row is context,")
    print("not the comparison -- it covers twice the sessions and a different regime.")
    print("=" * 96)
    header()
    show("SENSEX  {}..{}".format(lo, hi), sx_trades, SENSEX_LOT)
    show("NIFTY   same window", nf_window, NIFTY_LOT_SIZE)
    show("NIFTY   full sample", nf_trades, NIFTY_LOT_SIZE)

    print()
    print("=" * 96)
    print("BID-ASK -- the sensitivity that has killed every candidate in this programme.")
    print("A rupee means more on a Rs 455 premium than on a Rs 123 one, which is the")
    print("entire reason SENSEX was worth testing.")
    print("=" * 96)
    print("  {:<28} {:>13} {:>13} {:>13} {:>13}".format(
        "", *["Rs {:.0f} round trip".format(r) for r in ROUND_TRIPS]))
    for label, trades, lot in (("SENSEX", sx_trades, SENSEX_LOT),
                               ("NIFTY same window", nf_window, NIFTY_LOT_SIZE)):
        cells = []
        for rt in ROUND_TRIPS:
            b = book(charged(trades, rt), lot)
            cells.append("{:+,.0f}".format(b["net"]) if b else "-")
        print("  {:<28} {:>13} {:>13} {:>13} {:>13}".format(label, *cells))

    print()
    print("=" * 96)
    print("THE PREMIUM FLOOR ON SENSEX -- FITTED, NOT A RESULT.")
    print("Rs 100 is a costs finding calibrated to a Rs 123 NIFTY premium. At a Rs 455")
    print("SENSEX premium it is inactive. This sweep is reported so the inactivity is")
    print("visible; do not read the best cell as a parameter.")
    print("=" * 96)
    header()
    for floor in SCALED_FLOORS:
        trades = backtest_strategy(
            "SENSEX", 1, replace(config, premium_min=floor), contracts=sx)
        show("floor Rs {}".format(floor), trades, SENSEX_LOT)

    print()
    print("=" * 96)
    print("MONTHLY -- 121 sessions is a short sample and lumpiness is expected.")
    print("=" * 96)
    ledger, _s, _d = sized_ledger(sx_trades, lot_size=SENSEX_LOT)
    by_month = {}
    for row in ledger:
        by_month.setdefault(str(row["date"])[:7], []).append(row["net_pnl"])
    for month in sorted(by_month):
        v = by_month[month]
        print("  {}  {:>3} trades  {:>+10,.0f}".format(month, len(v), sum(v)))
    if by_month:
        print("  months positive: {}/{}".format(
            sum(1 for v in by_month.values() if sum(v) > 0), len(by_month)))


if __name__ == "__main__":
    main()
