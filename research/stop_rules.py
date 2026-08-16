"""Stops that ask whether the index actually moved.

The hunt diagnostic is unambiguous: 13 of 18 initial-stop losses later traded
back above our entry and 12 reached +1R, while the index kept going against us
into the close in only 5. The stop, not the entry, is manufacturing those losses.
Seven of the eighteen fired with the index better than -20 points -- there was no
adverse move at all, only decay and a widening spread on a wick.

A 10% stop on the premium is a stop on the wrong variable. It conflates "I was
wrong about direction" with "an option lost 10% of its value", and on a roughly
0.4 delta contract the second happens after about 25 index points, which is
inside ordinary minute-scale noise.

Three repairs, all of which keep the entry untouched:

  close confirmation   the initial stop triggers on a bar close, not a wick, and
                       fills at that close -- a worse price on the trades it does
                       take, in exchange for not being taken out by one tick
  spot confirmation    the premium stop is only armed once the index itself has
                       gone N points against the entry; a hard premium stop still
                       caps the loss
  a delay              no stop at all for the first few minutes, hard stop only

Every variant that can lose more than the soft stop is *sized on its hard stop*,
so nothing here wins by quietly taking more risk per trade. The trail keeps
measuring R in the original 10% unit so the exit stays comparable across rows.
"""
import os
import sys
from dataclasses import replace
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
                                                  nifty_trail_config)
from options_tracker.strategy_backtest import backtest_strategy

ORIGINAL_SIMULATE = SB._simulate
TRAIL = 0.7


def make_simulate(trail_gap=TRAIL, close_confirm=False, spot_confirm=None,
                  delay_bars=0, hard_percent=None):
    """The shipped exit with a configurable trigger for the *initial* stop.

    Confirmation applies only while the stop still sits at or below entry. Once
    the trail has carried it into profit we are protecting money already made,
    and there is no hunting argument for letting that run on a wick.
    """
    def simulate(candidate, rows, config):
        next_row = rows[candidate["next_index"]]
        entry = round(max(SB._number(next_row["open"]),
                          candidate["signal_close"]) * 1.005, 2)
        if SB._number(next_row["high"]) < entry:
            return None
        stop = round(entry * (1 - config.stop_percent), 2)
        risk = entry - stop
        if risk <= 0:
            return None
        hard = round(entry * (1 - hard_percent), 2) if hard_percent else stop
        sizing_risk = entry - hard
        entry_spot = SB._number(next_row["spot"])
        call = candidate["option_type"] == "CALL"
        initial_stop, high_water = stop, entry
        outcome, exit_price, exit_at = "TIME_EXIT", None, None
        window = [row for row in rows[candidate["next_index"]:]
                  if row["local_timestamp"].time() <= SB.TIME_EXIT]
        for index, future in enumerate(window):
            if exit_price is not None:
                continue
            low = SB._number(future["low"])
            high = SB._number(future["high"])
            close = SB._number(future["close"])
            spot = SB._number(future["spot"])
            protecting = stop > entry

            if hard_percent and not protecting and low <= hard:
                # The backstop is unconditional; it is what makes the soft stop
                # affordable rather than open-ended. Only meaningful when it sits
                # genuinely below the soft stop, so it is skipped when unset --
                # otherwise it would pre-empt the very rule under test.
                outcome = "HARD_STOP"
                exit_price, exit_at = hard, future["local_timestamp"]
            elif low <= stop:
                allowed = True
                if not protecting:
                    if index < delay_bars:
                        allowed = False
                    if close_confirm and close > stop:
                        allowed = False
                    if spot_confirm is not None and np.isfinite(spot):
                        against = (entry_spot - spot) if call else (spot - entry_spot)
                        if against < spot_confirm:
                            allowed = False
                if allowed:
                    outcome = "TRAIL_EXIT" if protecting else "STOP"
                    # A close-confirmed stop cannot fill at the stop: by the time
                    # the bar closed the price was already past it.
                    fill = min(stop, close) if (close_confirm and not protecting) else stop
                    exit_price, exit_at = fill, future["local_timestamp"]
            if exit_price is None:
                high_water = max(high_water, high)
                if (high_water - entry) / risk >= trail_gap:
                    stop = max(stop, round(high_water - risk * trail_gap, 2))
        if exit_price is None:
            if not window:
                return None
            exit_at = window[-1]["local_timestamp"]
            exit_price = SB._number(window[-1]["close"]) * 0.995
        return {
            **candidate,
            "signal_at": candidate["signal_at"].isoformat(),
            "exit_at": exit_at.isoformat(),
            "entry": entry, "stop_loss": initial_stop, "hard_stop": hard,
            "exit_stop": stop, "outcome": outcome,
            "target": round(entry + risk * 1.25, 2),
            "runner_target": round(entry + risk * 3, 2),
            "exit_price": exit_price,
            "sizing_risk": sizing_risk,
            "realized_r": round((exit_price - entry) / risk, 2),
        }
    return simulate


def book(trades, capital=STARTING_CAPITAL):
    """Compound the account, sizing off the hard stop so risk is comparable."""
    equity = peak = capital
    drawdown = net_total = 0.0
    taken = wins = 0
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry, unit_risk = trade["entry"], trade["sizing_risk"]
        if unit_risk <= 0:
            continue
        lots = max(0, min(
            floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE)),
        ))
        if not lots:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = trade["exit_price"]
        net = ((exit_price - entry) * quantity
               - estimate_option_charges(entry, max(exit_price, 0), quantity,
                                         trade["date"]))
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "win": 100 * wins / taken if taken else 0.0,
            "net": net_total, "dd": drawdown}


def run(config, **kwargs):
    SB._simulate = make_simulate(**kwargs)
    try:
        return backtest_strategy("NIFTY", 1, config)
    finally:
        SB._simulate = ORIGINAL_SIMULATE


VARIANTS = [
    ("shipped trigger, trail 0.7R", {}, {}),
    ("close-confirmed stop", {}, dict(close_confirm=True)),
    ("close-confirmed + hard 15%", {}, dict(close_confirm=True, hard_percent=0.15)),
    ("spot must move 15 pts", {}, dict(spot_confirm=15, hard_percent=0.15)),
    ("spot must move 25 pts", {}, dict(spot_confirm=25, hard_percent=0.15)),
    ("spot must move 35 pts", {}, dict(spot_confirm=35, hard_percent=0.20)),
    ("spot 25 pts + close confirm", {}, dict(spot_confirm=25, close_confirm=True,
                                             hard_percent=0.15)),
    ("no stop for 3 bars", {}, dict(delay_bars=3, hard_percent=0.15)),
    ("no stop for 5 bars", {}, dict(delay_bars=5, hard_percent=0.15)),
    # The two rows below widen `config.stop_percent`, which widens `risk` -- and
    # the trail is 0.7 *of that risk*, so they secretly widen the exit as well.
    # They are kept because that coupling is worth showing, not because they
    # isolate the stop.
    ("plain wider stop 15%", dict(stop_percent=0.15), {}),
    ("plain wider stop 20%", dict(stop_percent=0.20), {}),
    # Decoupled: the soft stop is never allowed to fire, so the hard stop is the
    # only way to lose, while `risk` -- and therefore the trail -- stays pinned to
    # the original 10%. This is the wider stop the two rows above meant to test.
    ("no soft stop, hard 12%", {}, dict(delay_bars=10**6, hard_percent=0.12)),
    ("no soft stop, hard 15%", {}, dict(delay_bars=10**6, hard_percent=0.15)),
    ("no soft stop, hard 20%", {}, dict(delay_bars=10**6, hard_percent=0.20)),
]


def main():
    base = nifty_trail_config()
    print(f"NIFTY, trail {TRAIL}R; every variant sized on its own hard stop, so "
          f"none of them wins by taking more risk.\n"
          f"Premium points are sizing-free and comparable across every row; the "
          f"rupee columns are not,\nbecause a wider hard stop buys fewer lots and "
          f"at Rs 1L a third of trades are already a single lot.\n", flush=True)
    header = (f"  {'stop rule':<30}{'n':>4}{'win%':>7}{'pts/trade':>11}"
              f"{'Rs 1L':>10}{'DD 1L':>9}{'Rs 5L':>11}{'DD 5L':>10}{'soft':>6}{'hard':>6}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    baseline = {}
    for name, config_kwargs, exit_kwargs in VARIANTS:
        config = replace(base, **config_kwargs) if config_kwargs else base
        trades = run(config, **exit_kwargs)
        small, large = book(trades), book(trades, 500_000)
        points = np.mean([t["exit_price"] - t["entry"] for t in trades])
        if not baseline:
            baseline = {"small": small["net"], "large": large["net"], "pts": points}
        stops = sum(1 for t in trades if t["outcome"] == "STOP")
        hard = sum(1 for t in trades if t["outcome"] == "HARD_STOP")
        print(f"  {name:<30}{small['n']:>4}{small['win']:>7.1f}{points:>11.2f}"
              f"{small['net']:>10,.0f}{small['dd']:>9,.0f}{large['net']:>11,.0f}"
              f"{large['dd']:>10,.0f}{stops:>6}{hard:>6}", flush=True)
    print(f"\n  baseline for reference: {baseline['pts']:.2f} pts/trade, "
          f"Rs {baseline['small']:,.0f} at 1L, Rs {baseline['large']:,.0f} at 5L")


if __name__ == "__main__":
    main()
