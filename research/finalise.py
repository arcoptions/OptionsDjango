"""The two questions left before a strategy can be traded, answered together.

Everything in the register is now either closed or one of these two:

  the Rs 200 cap   The Rs 100 floor is well supported -- cheap contracts lose to
                   a bid-ask quoted in paise. The cap is not: the Rs 200-plus
                   bucket flips sign between the two halves of the sample, so it
                   may be a real ceiling or it may be one volatile quarter. If
                   the cap is noise, Rs 100-250 is strictly better than Rs
                   100-200, because it keeps trades at no cost in quality.
  does D combine   The previous-day break and the shipped strategy fire on
                   different signals. If their good days do not coincide they
                   add; if they do, they merely compete for the same capital and
                   the combination is the same edge with a bigger drawdown.

Both are run against one contract load. `exit_lab.run` reloads contracts for
every variant, which at the observed 160s-3600s per load is the difference
between minutes and most of a day, so this file passes a cached set instead.

The combination test carries one honest caveat that is measured rather than
waved away: a sequential ledger books trades one after another, but two
strategies can hold positions at the same time. Overlap is counted and reported,
because a combined result that quietly assumed the account could fund both legs
at once would be a fiction.
"""
import os
import pickle
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, time, timedelta
from math import floor

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker import strategy_backtest as SB
from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from options_tracker.nifty_trail_strategy import (MAX_CASH_FRACTION,
                                                  RISK_PER_TRADE,
                                                  STARTING_CAPITAL,
                                                  nifty_trail_config,
                                                  sized_ledger)
from options_tracker.strategy_backtest import backtest_strategy

import prev_day_break as P
import simlib as S
from exit_lab import make_simulate
from fast_backtest import cached_contracts

TRAIL = 0.7
BANDS = ((50, 250), (50, 200), (75, 200), (100, 200), (100, 250), (100, 300),
         (125, 250), (100, 1000))
ROUND_TRIPS = (0.0, 0.50, 1.00, 2.00)
OPEN = time(9, 15)
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "cache", "finalise_bands.pkl")


def run(config, contracts, **exit_kwargs):
    """One backtest against an already-loaded contract set."""
    original = SB._simulate
    SB._simulate = make_simulate(**exit_kwargs)
    try:
        return backtest_strategy("NIFTY", 1, config, contracts=contracts)
    finally:
        SB._simulate = original


def book(trades):
    ledger, _skipped, drawdown = sized_ledger(trades)
    if not ledger:
        return None
    wins = sum(1 for row in ledger if row["net_pnl"] > 0)
    return {"n": len(ledger), "win": 100 * wins / len(ledger),
            "net": sum(row["net_pnl"] for row in ledger), "dd": drawdown}


def charged(trades, round_trip):
    """The same trades with a fixed rupee bid-ask taken out of each one.

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


def band_label(low, high):
    """Rs 1000 is a sentinel for "no cap", not a real ceiling any trade meets."""
    return f"Rs {low}-{high}" if high < 1000 else f"Rs {low}+"


# ---------------------------------------------------------------------------
# putting two strategies on one account


def stamp(date, minute):
    return datetime.combine(datetime.fromisoformat(date).date(), OPEN) + \
        timedelta(minutes=int(minute))


def naive(text):
    """Shipped timestamps are IST wall clock carrying a tzinfo; the npz side is
    minutes since 09:15 and carries none. Drop the offset rather than convert,
    because the two are already the same clock."""
    return datetime.fromisoformat(text).replace(tzinfo=None)


def from_shipped(trades):
    rows = []
    for trade in trades:
        risk = trade["entry"] - trade["stop_loss"]
        if risk <= 0:
            continue
        rows.append({
            "tag": "shipped",
            "date": trade["date"],
            "at": naive(trade["signal_at"]),
            "out": naive(trade["exit_at"]),
            "entry": trade["entry"],
            "unit_risk": risk,
            "exit_price": trade["entry"] + trade["realized_r"] * risk,
        })
    return rows


def from_break(trades):
    rows = []
    for trade in trades:
        exit_minute = trade["exit_minute"]
        if exit_minute is None:
            exit_minute = S.EXIT_MINUTE
        rows.append({
            "tag": "break",
            "date": trade["date"],
            "at": stamp(trade["date"], trade["minute"]),
            "out": stamp(trade["date"], exit_minute),
            "entry": trade["entry"],
            "unit_risk": trade["unit_risk"],
            "exit_price": trade["exit_price"],
        })
    return rows


def book_mixed(rows, risk=RISK_PER_TRADE, capital=STARTING_CAPITAL,
               reserve=False):
    """Compound one account through rows from any number of strategies.

    `reserve` holds back the cash already committed to positions that are still
    open, which is what a real account does. Without it a second signal can be
    sized against money the first trade is still holding.
    """
    equity = peak = capital
    drawdown = net_total = 0.0
    taken = wins = skipped = 0
    open_rows = []
    for row in sorted(rows, key=lambda item: item["at"]):
        # Realise anything that has already closed before this signal fires.
        still_open, committed = [], 0.0
        for held in open_rows:
            if held["out"] <= row["at"]:
                continue
            still_open.append(held)
            committed += held["deployed"]
        open_rows = still_open
        free = equity - committed if reserve else equity
        lots = max(0, min(
            floor(free * risk / (row["unit_risk"] * NIFTY_LOT_SIZE)),
            floor(free * MAX_CASH_FRACTION / (row["entry"] * NIFTY_LOT_SIZE))))
        if not lots:
            skipped += 1
            continue
        quantity = lots * NIFTY_LOT_SIZE
        net = ((row["exit_price"] - row["entry"]) * quantity
               - estimate_option_charges(row["entry"],
                                         max(row["exit_price"], 0),
                                         quantity, row["date"]))
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        open_rows.append({**row, "deployed": row["entry"] * quantity})
    return {"n": taken, "win": 100 * wins / taken if taken else 0.0,
            "net": net_total, "dd": drawdown, "skipped": skipped}


def overlaps(rows):
    """How often is a second position open while the first is still running?"""
    ordered = sorted(rows, key=lambda item: item["at"])
    clashes, mixed = 0, 0
    for position, row in enumerate(ordered):
        for other in ordered[position + 1:]:
            if other["at"] >= row["out"]:
                break
            clashes += 1
            mixed += other["tag"] != row["tag"]
    return clashes, mixed


def main():
    # The contract load is 160s on a warm file cache and has been measured at
    # 3,632s on a cold one. The band grid is deterministic, so its trades are
    # kept on disk and a re-run of the later sections costs nothing.
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as handle:
            kept = pickle.load(handle)
        print(f"Reusing {len(kept)} cached band variants from {CACHE}\n",
              flush=True)
        base = nifty_trail_config()
    else:
        print("Loading contracts once...", flush=True)
        contracts = cached_contracts()
        base = nifty_trail_config()
        print(f"{len(contracts)} contract series loaded\n", flush=True)
        kept = {}
        for low, high in BANDS:
            kept[(low, high)] = run(
                replace(base, premium_min=low, premium_max=high),
                contracts, trail_gap=TRAIL, record=True)
        with open(CACHE, "wb") as handle:
            pickle.dump(kept, handle)

    print("=" * 100)
    print("1. THE PREMIUM BAND, SETTLED")
    print("=" * 100)
    print("  The Rs 100 floor is not in question. The cap is: does removing it")
    print("  cost anything, or was Rs 200 a line drawn through one quarter?\n")
    print(f"  {'premium band':<20}{'n':>5}{'win%':>8}{'net Rs':>11}{'maxDD':>10}"
          f"{'net/DD':>9}{'pts/trade':>11}")
    for (low, high), trades in list(kept.items()):
        result = book(trades)
        if not result:
            print(f"  {f'Rs {low}-{high}':<20}{0:>5}")
            kept.pop((low, high))
            continue
        ratio = result["net"] / result["dd"] if result["dd"] else float("nan")
        marker = "  <- shipped band" if (low, high) == (50, 250) else ""
        label = band_label(low, high)
        print(f"  {label:<20}{result['n']:>5}{result['win']:>8.1f}"
              f"{result['net']:>11,.0f}{result['dd']:>10,.0f}{ratio:>9.2f}"
              f"{points(trades):>11.2f}{marker}", flush=True)

    print("\n" + "=" * 100)
    print("2. THE SAME BANDS AFTER A BID-ASK, WHICH IS THE LARGEST SENSITIVITY")
    print("=" * 100)
    print("  Columns are the full round trip, charged half on entry and half on")
    print("  exit. A strategy that only works at the mid is not a strategy.\n")
    print(f"  {'premium band':<20}" + "".join(f"{f'Rs {value:.2f}':>13}"
                                              for value in ROUND_TRIPS)
          + f"{'kept at Rs 2':>14}")
    for (low, high), trades in kept.items():
        cells, first, last = [], None, None
        for round_trip in ROUND_TRIPS:
            result = book(charged(trades, round_trip))
            value = result["net"] if result else 0.0
            cells.append(f"{value:>13,.0f}")
            if round_trip == 0.0:
                first = value
            last = value
        share = f"{100 * last / first:>13.0f}%" if first else "n/a".rjust(14)
        label = band_label(low, high)
        print(f"  {label:<20}" + "".join(cells) + share, flush=True)

    print("\n" + "=" * 100)
    print("3. DOES THE PREVIOUS-DAY BREAK ADD TO THE SHIPPED STRATEGY?")
    print("=" * 100)
    loaded = S.sessions()
    breaks = P.run(loaded, max_per_day=1, steps=1, trail_gap=TRAIL)
    break_rows = from_break(breaks)
    print(f"  {len(loaded)} sessions in the npz cache, "
          f"{len(break_rows)} previous-day break signals\n")

    finalists = [key for key in ((100, 1000), (100, 200), (50, 250))
                 if key in kept]
    print(f"  {'account holds':<34}{'n':>5}{'win%':>8}{'net Rs':>11}"
          f"{'maxDD':>10}{'net/DD':>9}{'skipped':>9}")
    for low, high in finalists:
        ship_rows = from_shipped(kept[(low, high)])
        name = band_label(low, high)
        for label, rows in ((f"shipped {name} alone", ship_rows),
                            ("previous-day break alone", break_rows),
                            (f"both, {name}", ship_rows + break_rows)):
            result = book_mixed(rows, reserve=True)
            ratio = result["net"] / result["dd"] if result["dd"] else float("nan")
            print(f"  {label:<34}{result['n']:>5}{result['win']:>8.1f}"
                  f"{result['net']:>11,.0f}{result['dd']:>10,.0f}{ratio:>9.2f}"
                  f"{result['skipped']:>9}", flush=True)
        print()

    print("  How much of that is really two strategies, and how much is one")
    print("  strategy funded twice? Positions open at the same time:\n")
    for low, high in finalists:
        ship_rows = from_shipped(kept[(low, high)])
        together = ship_rows + break_rows
        clashes, mixed = overlaps(together)
        ship_days = {row["date"] for row in ship_rows}
        break_days = {row["date"] for row in break_rows}
        shared = ship_days & break_days
        print(f"  {band_label(low, high)}: {clashes} overlapping pairs "
              f"({mixed} of them one of each strategy); "
              f"{len(shared)} days have both signals "
              f"({100 * len(shared) / max(len(ship_days), 1):.0f}% of "
              f"shipped days)")

    print("\n  Combined, at risk levels the account can actually carry:\n")
    print(f"  {'risk per trade':<34}{'n':>5}{'win%':>8}{'net Rs':>11}"
          f"{'maxDD':>10}{'DD % of 1L':>12}{'net/DD':>9}")
    if finalists:
        low, high = finalists[0]
        together = from_shipped(kept[(low, high)]) + break_rows
        for risk in (0.005, 0.01, 0.015, 0.02):
            result = book_mixed(together, risk=risk, reserve=True)
            ratio = result["net"] / result["dd"] if result["dd"] else float("nan")
            print(f"  {f'{risk * 100:.1f}% ({band_label(low, high)} + break)':<34}"
                  f"{result['n']:>5}{result['win']:>8.1f}{result['net']:>11,.0f}"
                  f"{result['dd']:>10,.0f}"
                  f"{100 * result['dd'] / STARTING_CAPITAL:>11.1f}%{ratio:>9.2f}",
                  flush=True)

    print("\n" + "=" * 100)
    print("4. THE COMBINATION, HALF BY HALF")
    print("=" * 100)
    print("  A combined equity curve that only works in the first half is a")
    print("  fact about 2025, not a plan for tomorrow.\n")
    if finalists:
        low, high = finalists[0]
        ship_rows = from_shipped(kept[(low, high)])
        every = sorted({row["date"] for row in ship_rows + break_rows})
        cut = every[len(every) // 2]
        print(f"  {'half':<20}{'strategy':<24}{'n':>5}{'win%':>8}{'net Rs':>11}"
              f"{'maxDD':>10}")
        for name, keep in (("first", lambda d: d < cut),
                           ("second", lambda d: d >= cut)):
            for label, rows in ((f"shipped {band_label(low, high)}", ship_rows),
                                ("previous-day break", break_rows),
                                ("both", ship_rows + break_rows)):
                subset = [row for row in rows if keep(row["date"])]
                result = book_mixed(subset, reserve=True)
                print(f"  {name:<20}{label:<24}{result['n']:>5}"
                      f"{result['win']:>8.1f}{result['net']:>11,.0f}"
                      f"{result['dd']:>10,.0f}", flush=True)
            print()


if __name__ == "__main__":
    main()
