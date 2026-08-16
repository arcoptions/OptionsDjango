"""Is the stop being hunted, or is it right?

Two of the overnight questions point in opposite directions from the same event.
"Avoid stop-loss hunting" assumes the market pokes through the stop and comes
back, so the fix is to give the trade more room. "Reverse after the stop" assumes
the move is genuinely against us and keeps going, so the fix is to turn around.
Both cannot be true of the same trades, and which one is true is measurable: look
at what the option and the index did *after* each stop.

The current stop is intrabar on the premium -- `low <= entry * 0.9` -- so a
single wick triggers it, and a 10% premium stop on a roughly 0.4 delta option is
only about 35 index points. That is well inside a normal minute-scale swing,
which is exactly the condition under which hunting would show up.

Nothing is changed here. This only records the counterfactual path each trade
took after we were already out.
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

from options_tracker import strategy_backtest as SB
from options_tracker.nifty_trail_strategy import nifty_trail_config
from options_tracker.strategy_backtest import backtest_strategy

ORIGINAL_SIMULATE = SB._simulate


def make_simulate(trail_gap=0.7):
    """The shipped exit, plus a recording of everything after the exit bar."""
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
        initial_stop, high_water = stop, entry
        outcome, exit_price, exit_at, exit_index = "TIME_EXIT", None, None, None
        window = [row for row in rows[candidate["next_index"]:]
                  if row["local_timestamp"].time() <= SB.TIME_EXIT]
        for index, future in enumerate(window):
            low, high = SB._number(future["low"]), SB._number(future["high"])
            if exit_price is not None:
                continue
            if low <= stop:
                outcome = "TRAIL_EXIT" if stop > entry else "STOP"
                exit_price, exit_at, exit_index = stop, future["local_timestamp"], index
            else:
                high_water = max(high_water, high)
                if (high_water - entry) / risk >= trail_gap:
                    stop = max(stop, round(high_water - risk * trail_gap, 2))
        if exit_price is None:
            if not window:
                return None
            exit_at = window[-1]["local_timestamp"]
            exit_price = SB._number(window[-1]["close"]) * 0.995
            exit_index = len(window) - 1

        after = window[exit_index + 1:]
        highs = [SB._number(row["high"]) for row in after]
        lows = [SB._number(row["low"]) for row in after]
        spots = [SB._number(row["spot"]) for row in after]
        entry_spot = SB._number(next_row["spot"])
        exit_spot = SB._number(window[exit_index]["spot"])
        call = candidate["option_type"] == "CALL"
        # Signed so that positive always means "the trade's thesis was right":
        # up for a call, down for a put.
        travel = [((s - exit_spot) if call else (exit_spot - s))
                  for s in spots if np.isfinite(s)]
        return {
            **candidate,
            "signal_at": candidate["signal_at"].isoformat(),
            "exit_at": exit_at.isoformat(),
            "entry": entry, "stop_loss": initial_stop, "exit_stop": stop,
            "target": round(entry + risk * 1.25, 2),
            "runner_target": round(entry + risk * 3, 2),
            "outcome": outcome,
            "realized_r": round((exit_price - entry) / risk, 2),
            "unit_risk": risk,
            "entry_spot": entry_spot,
            "exit_spot": exit_spot,
            "bars_after": len(after),
            "post_max_r": (max(highs) - entry) / risk if highs else None,
            "post_min_r": (min(lows) - entry) / risk if lows else None,
            "spot_best_after": max(travel) if travel else None,
            "spot_worst_after": min(travel) if travel else None,
            "spot_end_after": travel[-1] if travel else None,
            "stop_spot_move": ((exit_spot - entry_spot) if call
                               else (entry_spot - exit_spot)),
        }
    return simulate


def describe(name, values, unit=""):
    values = np.array([v for v in values if v is not None and np.isfinite(v)])
    if not len(values):
        print(f"  {name:<34}  (none)")
        return
    print(f"  {name:<34}{len(values):>6}{np.median(values):>10.2f}"
          f"{np.mean(values):>10.2f}{np.percentile(values, 25):>10.2f}"
          f"{np.percentile(values, 75):>10.2f}  {unit}")


def main():
    SB._simulate = make_simulate(0.7)
    try:
        trades = backtest_strategy("NIFTY", 1, nifty_trail_config())
    finally:
        SB._simulate = ORIGINAL_SIMULATE

    stopped = [t for t in trades if t["outcome"] == "STOP"]
    live = [t for t in stopped if t["bars_after"] >= 5]
    print(f"{len(trades)} trades at a 0.7R trail, {len(stopped)} hit the initial "
          f"stop; {len(live)} of those had 5+ minutes of session left to judge\n")

    print(f"  {'after the stop':<34}{'n':>6}{'median':>10}{'mean':>10}"
          f"{'p25':>10}{'p75':>10}")
    describe("option best, in R from entry", [t["post_max_r"] for t in live], "R")
    describe("option worst, in R from entry", [t["post_min_r"] for t in live], "R")
    describe("spot best, our direction", [t["spot_best_after"] for t in live], "pts")
    describe("spot worst, our direction", [t["spot_worst_after"] for t in live], "pts")
    describe("spot at 15:20, our direction", [t["spot_end_after"] for t in live], "pts")

    recovered = [t for t in live if t["post_max_r"] is not None and t["post_max_r"] > 0]
    to_target = [t for t in live if t["post_max_r"] is not None and t["post_max_r"] >= 1.0]
    kept_going = [t for t in live if t["spot_end_after"] is not None
                  and t["spot_end_after"] < 0]
    print(f"\n  after the stop, the same option later traded back above our entry "
          f"in {len(recovered)}/{len(live)} = {100 * len(recovered) / len(live):.0f}%")
    print(f"  ... and reached +1R in {len(to_target)}/{len(live)} = "
          f"{100 * len(to_target) / len(live):.0f}%")
    print(f"  the index kept moving against us into the close in "
          f"{len(kept_going)}/{len(live)} = {100 * len(kept_going) / len(live):.0f}%"
          f"   <- this is what a reversal trade would need")

    moves = np.array([t["stop_spot_move"] for t in stopped
                      if t["stop_spot_move"] is not None
                      and np.isfinite(t["stop_spot_move"])])
    print(f"\n  how deep was the poke? index points from entry to the stop trigger")
    print(f"  median {np.median(moves):.0f}, p25 {np.percentile(moves, 25):.0f}, "
          f"p75 {np.percentile(moves, 75):.0f}")
    print(f"  stops where the index had barely moved (better than -20 pts): "
          f"{(moves > -20).sum()}/{len(moves)}   <- decay and spread, not a real move")


if __name__ == "__main__":
    main()
