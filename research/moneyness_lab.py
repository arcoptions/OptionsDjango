"""Buy the same signal on a different strike, and see how much room that buys.

The hunt diagnostic says the stop fires after a median of 24 index points. That
is not a statement about the stop percentage, it is a statement about the
instrument: a 10% fall in an at-the-money premium is a small move in the index,
because an ATM option is the most percentage-volatile contract on the board.

Move in the money and the arithmetic changes. A deeper contract has more delta
and more intrinsic value, so the same 10% of premium corresponds to a much larger
index move -- and because risk per lot rises in proportion, the rupee risk per
trade is unchanged at a fixed risk budget. The same money buys more room. What it
costs is gamma: an ITM option's payoff is flatter, so a big move returns fewer R.

Which effect wins is an empirical question, and it is asked here without
disturbing anything else. The entries, the minutes, the stop percentage and the
trail are all the parent strategy's; only the strike is substituted, read
straight from the minute cache. Out-of-the-money strikes are included as the
other direction of the same experiment -- they should be worse if the reasoning
holds, which is the check that the reasoning is doing any work.
"""
import os
import sys
from datetime import datetime
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
                                                  nifty_trail_config)
from options_tracker.strategy_backtest import backtest_strategy

import common as C
from stop_hunt import ORIGINAL_SIMULATE, make_simulate

TRAIL = 0.7
STOP_PERCENT = 0.10
SLIPPAGE = 1.005
EXIT_MINUTE = 365
STEPS = (-3, -2, -1, 0, 1, 2)  # negative = into the money


def minute_of(stamp):
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    return stamp.hour * 60 + stamp.minute - 555


def run_leg(data, side, index, start, call, spread=0.0):
    """Parent exit rules on one contract, plus how far the index had to move.

    `spread` is a half bid-ask in rupees, paid on the way in and again on the way
    out. It is modelled in rupees rather than percent on purpose: a quoted spread
    on NIFTY weeklies is roughly a fixed number of paise wide whatever the
    premium, so it is a far heavier tax on a cheap out-of-the-money contract than
    on an expensive one -- which is exactly the effect that could manufacture a
    fake preference for cheap strikes.
    """
    opens, highs = data["o"][side, index], data["h"][side, index]
    lows, closes = data["l"][side, index], data["c"][side, index]
    spot = np.asarray(data["spot"], dtype=float)
    last = min(EXIT_MINUTE, opens.shape[0] - 1)
    if start > last or not np.isfinite(opens[start]) or opens[start] <= 0:
        return None
    entry = round(float(opens[start]) * SLIPPAGE + spread, 2)
    stop = round(entry * (1 - STOP_PERCENT), 2)
    risk = entry - stop
    if risk <= 0 or not np.isfinite(spot[start]):
        return None
    entry_spot = float(spot[start])
    high_water, exit_price, outcome, exit_minute = entry, None, "TIME_EXIT", last
    for minute in range(start, last + 1):
        low, high = float(lows[minute]), float(highs[minute])
        if not np.isfinite(low) or not np.isfinite(high):
            continue
        if low <= stop:
            outcome = "TRAIL_EXIT" if stop > entry else "STOP"
            exit_price, exit_minute = stop, minute
            break
        high_water = max(high_water, high)
        if (high_water - entry) / risk >= TRAIL:
            stop = max(stop, round(high_water - risk * TRAIL, 2))
    if exit_price is None:
        if not np.isfinite(closes[last]):
            return None
        exit_price = float(closes[last]) * 0.995
    exit_price = max(exit_price - spread, 0.0)
    exit_spot = spot[exit_minute] if np.isfinite(spot[exit_minute]) else np.nan
    against = ((entry_spot - exit_spot) if call else (exit_spot - entry_spot))
    return {"entry": entry, "exit_price": exit_price, "outcome": outcome,
            "unit_risk": risk, "realized_r": (exit_price - entry) / risk,
            "stop_room": against if outcome == "STOP" else np.nan}


def book(legs, capital):
    equity = peak = capital
    drawdown = net_total = 0.0
    taken = wins = 0
    cash_bound = 0
    for leg in sorted(legs, key=lambda item: item["signal_at"]):
        entry, unit_risk = leg["entry"], leg["unit_risk"]
        risk_lots = floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE))
        cash_lots = floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE))
        lots = max(0, min(risk_lots, cash_lots))
        if not lots:
            continue
        cash_bound += cash_lots < risk_lots
        quantity = lots * NIFTY_LOT_SIZE
        net = ((leg["exit_price"] - entry) * quantity
               - estimate_option_charges(entry, max(leg["exit_price"], 0),
                                         quantity, leg["date"]))
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "win": 100 * wins / taken if taken else 0.0,
            "net": net_total, "dd": drawdown, "cash_bound": cash_bound}


def main():
    SB._simulate = make_simulate(TRAIL)
    try:
        parents = backtest_strategy("NIFTY", 1, nifty_trail_config())
    finally:
        SB._simulate = ORIGINAL_SIMULATE
    print(f"{len(parents)} parent trades; substituting the strike at the same "
          f"entry minute, everything else held fixed\n", flush=True)

    setups = []
    for parent in parents:
        try:
            data = C.load(parent["date"])
        except OSError:
            continue
        strikes = np.asarray(data["strikes"], dtype=float)
        spot = np.asarray(data["spot"], dtype=float)
        start = minute_of(parent["signal_at"]) + 1
        if start < 0 or start >= len(spot) or not np.isfinite(spot[start]):
            continue
        call = parent["option_type"] == "CALL"
        atm = int(np.argmin(np.abs(strikes - spot[start])))
        setups.append((parent, data, strikes, atm, start, call))

    def ladder(spread):
        results = {}
        for step in STEPS:
            legs = []
            for parent, data, strikes, atm, start, call in setups:
                # A call goes into the money by stepping down the strike ladder,
                # a put by stepping up, so the sign flips with the option type.
                index = atm + (step if call else -step)
                if not 0 <= index < len(strikes):
                    continue
                leg = run_leg(data, 0 if call else 1, index, start, call, spread)
                if leg:
                    legs.append({**leg, "date": parent["date"],
                                 "signal_at": parent["signal_at"],
                                 "strike": float(strikes[index])})
            results[step] = legs
        return results

    def label(step):
        return "ATM" if step == 0 else f"{abs(step)} {'ITM' if step < 0 else 'OTM'}"

    results = ladder(0.0)
    print(f"  {'strike':<14}{'n':>4}{'win%':>7}{'entry Rs':>10}{'risk Rs':>9}"
          f"{'stop room':>11}{'mean R':>8}{'totR':>8}{'pts/trade':>11}")
    for step in STEPS:
        legs = results[step]
        if not legs:
            continue
        values = np.array([leg["realized_r"] for leg in legs])
        room = np.array([leg["stop_room"] for leg in legs])
        room = room[np.isfinite(room)]
        points = np.mean([leg["exit_price"] - leg["entry"] for leg in legs])
        print(f"  {label(step):<14}{len(legs):>4}{100 * (values > 0).mean():>7.1f}"
              f"{np.mean([l['entry'] for l in legs]):>10.0f}"
              f"{np.mean([l['unit_risk'] for l in legs]):>9.1f}"
              f"{(np.median(room) if len(room) else float('nan')):>10.0f}p"
              f"{values.mean():>8.2f}{values.sum():>8.1f}{points:>11.2f}")

    print(f"\n  'stop room' is the median index move, in points, that it took to "
          f"stop the trade out.\n  Deeper contracts should need a larger move for "
          f"the same 10% of premium.\n")
    print(f"  {'strike':<14}{'Rs 1L':>11}{'DD 1L':>10}{'cash-capped':>13}"
          f"{'Rs 5L':>12}{'DD 5L':>11}")
    for step in STEPS:
        if not results[step]:
            continue
        small, large = book(results[step], 100_000), book(results[step], 500_000)
        print(f"  {label(step):<14}{small['net']:>11,.0f}{small['dd']:>10,.0f}"
              f"{small['cash_bound']:>10}/{small['n']:<3}"
              f"{large['net']:>12,.0f}{large['dd']:>11,.0f}")

    print(f"\n  net at Rs 1,00,000 against a half bid-ask paid each way, in rupees "
          f"of premium.\n  A cheap strike is taxed hardest by a fixed spread, so "
          f"this is where a fake\n  preference for cheap options would fall apart.\n")
    spreads = (0.0, 0.25, 0.50, 1.00, 1.50)
    print(f"  {'strike':<14}" + "".join(f"{'Rs ' + f'{s:.2f}':>13}" for s in spreads))
    for step in STEPS:
        cells = []
        for spread in spreads:
            legs = ladder(spread)[step] if spread else results[step]
            cells.append(f"{book(legs, 100_000)['net']:>13,.0f}" if legs
                         else "n/a".rjust(13))
        print(f"  {label(step):<14}" + "".join(cells), flush=True)


if __name__ == "__main__":
    main()
