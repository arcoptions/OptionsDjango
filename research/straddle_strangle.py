"""Long straddle and long strangle -- both legs bought, so both are affordable.

These are the only two ideas in the batch that are structurally different from
everything else tested here: they take no view on direction, only on movement.
That makes them worth running even though a buyer pays theta on two legs instead
of one, because every directional idea in this project shares one failure mode --
being right about movement and wrong about which way -- and this is the structure
that is immune to it.

The calendar spread from the same list is *not* here. It requires selling the
near-dated leg, and selling needs margin this account does not have.

Two legs need their own execution model, because the interesting question is
whether the position is managed as one thing or two:

  combined   stop and target on the total premium paid. The textbook way, and
             the only way that expresses "I am long volatility".
  per leg    each leg gets its own stop. Cheaper to be wrong on one side, but it
             quietly converts the position into a directional bet the moment one
             leg stops out.

Both are run. The entry timing question -- straddles are supposed to be put on
before an event -- is handled by testing entry at the open against entry only on
days that were quiet beforehand, since a low-IV morning is the closest thing in
this data to "before the move".
"""
import os
import sys
from collections import defaultdict
from math import floor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import simlib as S
from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from options_tracker.nifty_trail_strategy import (MAX_CASH_FRACTION,
                                                  RISK_PER_TRADE,
                                                  STARTING_CAPITAL)


def legs(data, spot_price, minute, width):
    """Call and put index for a straddle (width 0) or strangle (width > 0)."""
    call = S.strike_index(data["strikes"], spot_price, S.CALL, -width)
    put = S.strike_index(data["strikes"], spot_price, S.PUT, -width)
    if call is None or put is None:
        return None
    return call, put


def simulate_pair(data, call, put, start, *, stop_percent=0.30,
                  target_percent=None, trail_percent=None, per_leg=False,
                  exit_minute=S.EXIT_MINUTE):
    """Hold both legs from `start`. Returns a trade or None.

    Combined mode values the pair at every minute using both legs' lows and
    highs together, which is the honest reading: the total can only be at its
    minimum when both legs are at theirs, and no single minute offers both.
    Using low+low as the stop trigger is therefore slightly pessimistic, and
    that is the direction to err in.
    """
    fields = {}
    for name, side, index in (("call", S.CALL, call), ("put", S.PUT, put)):
        opens = data["o"][side, index]
        if start >= opens.shape[0] or not np.isfinite(opens[start]) or opens[start] <= 0:
            return None
        fields[name] = {
            "entry": round(float(opens[start]) * S.SLIPPAGE, 2),
            "h": data["h"][side, index], "l": data["l"][side, index],
            "c": data["c"][side, index]}
    paid = fields["call"]["entry"] + fields["put"]["entry"]
    if paid <= 0 or paid > 1200:
        return None
    last = min(exit_minute, len(fields["call"]["c"]) - 1)
    if start > last:
        return None

    stop = round(paid * (1 - stop_percent), 2)
    risk = paid - stop
    target = round(paid * (1 + target_percent), 2) if target_percent else None
    high_water = paid
    exit_value, exit_at, outcome = None, last, "TIME"
    dead = set()

    for minute in range(start, last + 1):
        low = high = 0.0
        usable = True
        for name in ("call", "put"):
            if name in dead:
                continue
            leg_low, leg_high = float(fields[name]["l"][minute]), float(fields[name]["h"][minute])
            if not np.isfinite(leg_low) or not np.isfinite(leg_high):
                usable = False
                break
            low += leg_low
            high += leg_high
        if not usable:
            continue
        if per_leg:
            # Each leg carries its own stop; a stopped leg stops contributing.
            for name in ("call", "put"):
                if name in dead:
                    continue
                floor_price = fields[name]["entry"] * (1 - stop_percent)
                if float(fields[name]["l"][minute]) <= floor_price:
                    fields[name]["stopped_at"] = floor_price
                    fields[name]["stop_minute"] = minute
                    dead.add(name)
            if len(dead) == 2:
                exit_value = sum(fields[n]["stopped_at"] for n in ("call", "put"))
                exit_at, outcome = minute, "STOP"
                break
        elif low <= stop:
            exit_value, exit_at = stop, minute
            outcome = "TRAIL" if stop > paid else "STOP"
            break
        if target is not None and high >= target:
            exit_value, exit_at, outcome = target, minute, "TARGET"
            break
        high_water = max(high_water, high)
        if trail_percent is not None and high_water > paid and not per_leg:
            stop = max(stop, round(high_water * (1 - trail_percent), 2))

    if exit_value is None:
        exit_value = 0.0
        for name in ("call", "put"):
            if name in dead:
                exit_value += fields[name]["stopped_at"]
                continue
            value = float(fields[name]["c"][last])
            if not np.isfinite(value):
                return None
            exit_value += value * S.EXIT_SLIPPAGE
    if risk <= 0:
        return None
    return {"entry": paid, "unit_risk": risk, "exit_price": exit_value,
            "realized_r": (exit_value - paid) / risk,
            "gain_percent": 100 * (exit_value - paid) / paid,
            "exit_minute": exit_at, "outcome": outcome}


def book_pairs(trades, cash_sized=False):
    """Same ledger as simlib.book, but two legs cost two lots of charges.

    `cash_sized` drops the 2%-of-equity risk rule and sizes on the cash cap
    alone. That is not a licence to risk more -- it exists because the risk rule
    and a stopless straddle are incompatible by construction. A straddle held to
    the close risks the whole premium, so 2% of Rs 1,00,000 is Rs 2,000 of
    allowed risk against a Rs 16,000 position, and the sizer correctly answers
    "zero lots". Reporting that as "straddles lose money" would be a lie about a
    strategy that was never actually traded, so it is measured both ways.
    """
    equity = peak = STARTING_CAPITAL
    drawdown = net_total = 0.0
    taken = wins = skipped = 0
    for trade in sorted(trades, key=lambda item: (item["date"], item["minute"])):
        paid, unit_risk = trade["entry"], trade["unit_risk"]
        by_cash = floor(equity * MAX_CASH_FRACTION / (paid * NIFTY_LOT_SIZE))
        by_risk = floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE))
        lots = max(0, by_cash if cash_sized else min(by_risk, by_cash))
        if not lots:
            skipped += 1
            continue
        quantity = lots * NIFTY_LOT_SIZE
        charges = 2 * estimate_option_charges(paid / 2, max(trade["exit_price"], 0) / 2,
                                              quantity, trade["date"])
        net = (trade["exit_price"] - paid) * quantity - charges
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "skipped": skipped,
            "win": 100 * wins / taken if taken else 0.0,
            "net": net_total, "dd": drawdown,
            "pts": float(np.mean([t["realized_r"] * t["unit_risk"]
                                  for t in trades])) if trades else 0.0}


def report(label, trades, baseline=None, cash_sized=False):
    if not trades:
        print(f"  {label:<40}{0:>5}{'-':>8}{'-':>11}{'-':>11}{'-':>10}")
        return None
    result = book_pairs(trades, cash_sized)
    delta = f"{result['net'] - baseline:>+11,.0f}" if baseline is not None else " " * 11
    note = f"   [{result['skipped']} unsizeable at 2% risk]" if result["skipped"] else ""
    print(f"  {label:<40}{result['n']:>5}{result['win']:>8.1f}{result['pts']:>11.2f}"
          f"{result['net']:>11,.0f}{result['dd']:>10,.0f}{delta}{note}", flush=True)
    return result


def quiet_open(data, minutes=30, threshold=0.20):
    """Was the first half hour unusually still? A proxy for 'before the move'."""
    spot = data["spot"][:minutes]
    spot = spot[np.isfinite(spot)]
    if len(spot) < minutes - 5:
        return False
    return 100 * (spot.max() - spot.min()) / spot[0] < threshold


def run(loaded, *, width=0, minute=30, only_quiet=False, **kwargs):
    trades = []
    for date, data in loaded.items():
        if minute >= len(data["spot"]) or not np.isfinite(data["spot"][minute]):
            continue
        if only_quiet and not quiet_open(data):
            continue
        pair = legs(data, data["spot"][minute], minute, width)
        if pair is None:
            continue
        result = simulate_pair(data, pair[0], pair[1], minute, **kwargs)
        if result:
            trades.append({**result, "date": date, "minute": minute})
    return trades


def main():
    print("Loading sessions...", flush=True)
    loaded = S.sessions()
    print(f"{len(loaded)} sessions")
    print("Shipped directional strategy for reference: 68.8% win, Rs 40,341, DD Rs 8,876")
    print("Calendar spreads from the same list are excluded: they require selling.\n")

    print("=" * 100)
    print("1. THE BASIC POSITION: ATM STRADDLE VS OTM STRANGLE, HELD TO 15:20")
    print("=" * 100)
    print(S.HEADER)
    for width, name in ((0, "ATM straddle"), (1, "strangle, 1 strike out"),
                        (2, "strangle, 2 strikes out"), (3, "strangle, 3 strikes out"),
                        (4, "strangle, 4 strikes out")):
        report(name, run(loaded, width=width, stop_percent=0.99))

    print("\n" + "=" * 100)
    print("1b. THE SAME POSITIONS, SIZED ON CASH ALONE SO THEY ACTUALLY GET TRADED")
    print("=" * 100)
    print("  The 2% risk rule refuses a stopless straddle, because the whole")
    print("  premium is at risk. Sizing on the 40% cash cap instead is a much")
    print("  more aggressive account, but it is the only way to see the P&L.")
    print(S.HEADER)
    for width, name in ((0, "ATM straddle"), (1, "strangle, 1 strike out"),
                        (2, "strangle, 2 strikes out"), (3, "strangle, 3 strikes out")):
        report(name, run(loaded, width=width, stop_percent=0.99), cash_sized=True)
    for stop, name in ((0.30, "ATM straddle, 30% combined stop"),
                       (0.50, "ATM straddle, 50% combined stop")):
        report(name, run(loaded, width=0, stop_percent=stop), cash_sized=True)
    for target, name in ((0.20, "ATM straddle, +20% target / 30% stop"),
                         (0.30, "ATM straddle, +30% target / 30% stop")):
        report(name, run(loaded, width=0, stop_percent=0.30, target_percent=target),
               cash_sized=True)

    print("\n" + "=" * 100)
    print("2. WHEN TO PUT IT ON")
    print("=" * 100)
    print(S.HEADER)
    for minute, name in ((1, "09:16, straight off the open"), (15, "09:30"),
                         (30, "09:45"), (60, "10:15"), (120, "11:15")):
        report(name, run(loaded, width=0, minute=minute, stop_percent=0.99),
               cash_sized=True)
    report("09:45, only after a quiet first 30 min",
           run(loaded, width=0, minute=30, only_quiet=True, stop_percent=0.99),
           cash_sized=True)

    print("\n" + "=" * 100)
    print("3. MANAGING IT: COMBINED STOP VS PER-LEG STOP, AND TARGETS")
    print("=" * 100)
    print(S.HEADER)
    base = report("hold to 15:20, no stop",
                  run(loaded, width=0, stop_percent=0.99))
    baseline = base["net"] if base else None
    for stop in (0.20, 0.30, 0.40):
        report(f"combined stop {int(stop * 100)}%",
               run(loaded, width=0, stop_percent=stop), baseline)
    for stop in (0.30, 0.50):
        report(f"per-leg stop {int(stop * 100)}%",
               run(loaded, width=0, stop_percent=stop, per_leg=True), baseline)
    for target in (0.20, 0.30, 0.50):
        report(f"+{int(target * 100)}% target, 30% stop",
               run(loaded, width=0, stop_percent=0.30, target_percent=target), baseline)
    for trail in (0.15, 0.25):
        report(f"trail {int(trail * 100)}% off high, 30% stop",
               run(loaded, width=0, stop_percent=0.30, trail_percent=trail), baseline)

    print("\n" + "=" * 100)
    print("4. WHAT A STRADDLE BUYER IS ACTUALLY PAYING FOR")
    print("=" * 100)
    costs, moves = [], []
    for date, data in loaded.items():
        if not np.isfinite(data["spot"][30]):
            continue
        pair = legs(data, data["spot"][30], 30, 0)
        if pair is None:
            continue
        opens_call = data["o"][S.CALL, pair[0]][30]
        opens_put = data["o"][S.PUT, pair[1]][30]
        if not np.isfinite(opens_call) or not np.isfinite(opens_put):
            continue
        spot = data["spot"][30:S.EXIT_MINUTE + 1]
        spot = spot[np.isfinite(spot)]
        if len(spot) < 100:
            continue
        costs.append(float(opens_call + opens_put))
        moves.append(float(np.max(np.abs(spot - spot[0]))))
    costs, moves = np.array(costs), np.array(moves)
    print(f"  sessions                                 {len(costs)}")
    print(f"  median straddle cost at 09:45            {np.median(costs):>8.1f} points")
    print(f"  median largest move afterwards           {np.median(moves):>8.1f} points")
    print(f"  days the move exceeded the cost          "
          f"{100 * float((moves > costs).mean()):>8.1f}%")
    print(f"  ...and it must exceed it *before* 15:20 and survive theta,")
    print(f"     so this is the ceiling, not the result.")


if __name__ == "__main__":
    main()
