"""Targets read off the chart instead of off a multiple of risk.

A 1R target is a statement about our stop, not about the market: it sits at the
same distance whether the index is pinned under yesterday's high or has clear air
above it. A level target is a statement about where buyers and sellers have
actually met before, which is what the chart is showing when price turns at the
prior-day high or the value-area edge.

The bridge from a level to a limit price is Black-Scholes delta. At the fill we
know spot, strike and the strike's own implied volatility, so a spot target of L
becomes a premium target of roughly entry + delta * (L - spot). That is a first
order approximation and it understates a call's value on a large move, because
delta rises as spot advances -- it is the conservative direction, which is the
right way to be wrong when testing whether an idea works at all.

Nothing here needs re-simulation. The runner keeps the same trail, so its exit is
unchanged, and whether the target filled is just a question of whether the
premium's best price while open reached it. One backtest per trail, then every
level family scored off the same 64 trades.
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

import common as C
import regime as R
from exit_lab import run

OPEN_MINUTE = 555
OPENING_RANGE = 30  # minutes; the chart's opening balance
CAPITAL = 500_000  # a split needs two lots, which one lakh rarely funds


def minute_of(stamp):
    return stamp.hour * 60 + stamp.minute - OPEN_MINUTE


def session_levels(dates):
    """Every candidate level per session, in index points.

    Prior-day values come from the previous cached session so nothing is read
    from the future. The opening range is closed before the strategy's first
    entry at 09:30, so it is known by the time any trade fires.
    """
    table = {}
    previous = None
    for date in dates:
        try:
            data = C.load(date)
        except OSError:
            continue
        spot = np.asarray(data["spot"], dtype=float)
        good = np.isfinite(spot) & (spot > 0)
        if not good.any():
            continue
        levels = {}
        if previous is not None:
            levels["prior high"] = previous.max()
            levels["prior low"] = previous.min()
            levels["prior close"] = previous[-1]
        window = spot[:OPENING_RANGE][good[:OPENING_RANGE]]
        if len(window):
            levels["opening high"] = window.max()
            levels["opening low"] = window.min()
        table[date] = (levels, spot[good][0])
        previous = spot[good]
    return table


def fraction_iv(value):
    """The candle stores implied volatility in percent; the maths wants a fraction.

    Guarded rather than divided blindly, to match regime.atm_iv and to survive a
    source that ever starts storing fractions.
    """
    if not value or value <= 0:
        return None
    return value / 100.0 if value > 3 else value


def targets_for(trade, levels, open_spot):
    """Candidate spot targets in the direction the trade needs, nearest first."""
    spot = trade["entry_spot"]
    call = trade["option_type"] == "CALL"
    found = {}
    for name, level in levels.items():
        if (level > spot) if call else (level < spot):
            found[name] = level
    # Round numbers are not a level anyone drew, but price does pause at them and
    # they are always available, which makes them the honest baseline for "any
    # level at all beats a fixed multiple".
    step = 50.0
    found["round 50"] = (np.ceil(spot / step) if call else np.floor(spot / step)) * step
    if found["round 50"] == spot:
        found["round 50"] += step if call else -step
    # The day's own expected range, from the strike's implied volatility.
    iv = fraction_iv(trade["entry_iv"])
    if iv:
        move = spot * iv * np.sqrt(1.0 / 365.0)
        found["IV 1 sigma"] = spot + (move if call else -move)
        found["IV half sigma"] = spot + (0.5 * move if call else -0.5 * move)
    if open_spot:
        found["day open"] = open_spot
    return found


def target_r(trade, level, days_to_expiry=3.0):
    """The level expressed as a multiple of the trade's risk, via delta."""
    spot = trade["entry_spot"]
    iv = fraction_iv(trade["entry_iv"])
    if not iv or not spot:
        return None
    call = trade["option_type"] == "CALL"
    slope = R.delta(spot, trade["strike"], iv, max(days_to_expiry, 0.5) / 365.0, call)
    # Put delta is negative; only its magnitude matters for how fast the premium
    # moves against a move in spot.
    slope = abs(slope)
    if not np.isfinite(slope) or slope <= 0.01:
        return None
    gain = slope * abs(level - spot)
    return gain / trade["unit_risk"]


def book(trades, plans, capital=CAPITAL):
    """Sell half the lots at the planned premium, trail the rest as usual."""
    equity = peak = capital
    drawdown = net_total = 0.0
    taken = wins = splits = 0
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry, unit_risk = trade["entry"], trade["unit_risk"]
        lots = max(0, min(
            floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE)),
        ))
        if not lots:
            continue
        exit_price = entry + trade["realized_r"] * unit_risk
        plan = plans.get(id(trade))
        legs = [(lots, exit_price)]
        if plan is not None and lots >= 2 and trade["mfe_r"] >= plan:
            out = lots // 2
            legs = [(out, entry + plan * unit_risk), (lots - out, exit_price)]
            splits += 1
        net = 0.0
        for leg_lots, price in legs:
            quantity = leg_lots * NIFTY_LOT_SIZE
            net += ((price - entry) * quantity
                    - estimate_option_charges(entry, price, quantity, trade["date"]))
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "splits": splits, "win": 100 * wins / taken if taken else 0,
            "net": net_total, "dd": drawdown}


def main():
    levels_by_date = session_levels(C.session_dates())
    for trail in (0.5, 0.7):
        trades = run(nifty_trail_config(), trail_gap=trail, record=True)
        usable = [t for t in trades if t["date"] in levels_by_date]
        print(f"\n\n{'=' * 84}\ntrail {trail}R   {len(usable)} of {len(trades)} "
              f"trades on sessions with level data", flush=True)

        names = ["prior high", "prior low", "prior close", "opening high",
                 "opening low", "day open", "round 50", "IV half sigma",
                 "IV 1 sigma", "nearest level"]
        plans = {name: {} for name in names}
        distances = {name: [] for name in names}
        for trade in usable:
            levels, open_spot = levels_by_date[trade["date"]]
            found = targets_for(trade, levels, open_spot)
            structural = {k: v for k, v in found.items()
                          if k not in ("round 50", "IV 1 sigma", "IV half sigma")}
            if structural:
                spot = trade["entry_spot"]
                found["nearest level"] = min(structural.values(),
                                             key=lambda v: abs(v - spot))
            for name in names:
                if name not in found:
                    continue
                multiple = target_r(trade, found[name])
                if multiple is None or multiple <= 0.05:
                    continue
                plans[name][id(trade)] = multiple
                distances[name].append(multiple)

        print(f"\n  {'target':<16}{'trades':>8}{'median R':>10}{'p25':>7}{'p75':>7}"
              f"{'reached':>9}")
        for name in names:
            values = np.array(distances[name])
            if not len(values):
                continue
            reached = [t["mfe_r"] >= plans[name][id(t)]
                       for t in usable if id(t) in plans[name]]
            print(f"  {name:<16}{len(values):>8}{np.median(values):>10.2f}"
                  f"{np.percentile(values, 25):>7.2f}{np.percentile(values, 75):>7.2f}"
                  f"{100 * np.mean(reached):>8.0f}%")

        print(f"\n  half out at the target, runner trails; capital "
              f"Rs {CAPITAL:,}")
        print(f"  {'rule':<20}{'n':>5}{'splits':>8}{'win%':>8}{'net Rs':>11}"
              f"{'maxDD':>10}")
        base = book(usable, {})
        print(f"  {'trail only':<20}{base['n']:>5}{base['splits']:>8}"
              f"{base['win']:>8.1f}{base['net']:>11,.0f}{base['dd']:>10,.0f}")
        for name in names:
            if not plans[name]:
                continue
            result = book(usable, plans[name])
            print(f"  {name:<20}{result['n']:>5}{result['splits']:>8}"
                  f"{result['win']:>8.1f}{result['net']:>11,.0f}"
                  f"{result['dd']:>10,.0f}")
        for fixed in (1.0, 1.5, 2.0, 3.0):
            result = book(usable, {id(t): fixed for t in usable})
            print(f"  {f'fixed {fixed}R':<20}{result['n']:>5}{result['splits']:>8}"
                  f"{result['win']:>8.1f}{result['net']:>11,.0f}"
                  f"{result['dd']:>10,.0f}")


if __name__ == "__main__":
    main()
