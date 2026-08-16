"""How much of each move does the exit actually keep?

The pivot ladder was the most useful thing this research produced: a perfect
entry and exit was worth +25.1% mean premium, keeping the entry but trading a
realistic exit left +3.3%. The exit destroyed most of the value. That study was
about pivots, but nothing about it was specific to pivots -- and the exit of the
strategy we actually run has never been measured the same way.

So this does two things.

First a diagnostic: for every live trade, how far did the option go in our favour
before we got out, and what fraction of that did we keep? Maximum favourable
excursion is the ceiling any exit rule could have reached on the same entries.
The gap between it and realised R is the entire prize available from exit work.

Then a search: re-run the whole pipeline under different exit rules. Not just
the exit maths -- the full pipeline, because an earlier exit releases the
re-entry cooldown and changes the daily loss budget, so the trade set genuinely
moves. Holding the trade set fixed would flatter every faster exit.
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

import django

django.setup()

from options_tracker import strategy_backtest as SB
from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger
from options_tracker.strategy_backtest import backtest_strategy

ORIGINAL_SIMULATE = SB._simulate


def make_simulate(target_r=None, trail_gap=None, activate_r=0.0, ratchet=False,
                  record=False):
    """A drop-in _simulate with a configurable exit, mirroring the original.

    The adverse-first ordering of the original is preserved exactly: the stop is
    tested against this bar's low before this bar's high is allowed to raise the
    high-water mark, so a bar that both stops us out and makes a new high counts
    as a stop.
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
        initial_stop = stop
        high_water = entry
        peak = entry
        trough = entry
        day_peak = entry
        outcome, exit_price, exit_at = "TIME_EXIT", None, None
        bars = 0
        held = 0
        window = [row for row in rows[candidate["next_index"]:]
                  if row["local_timestamp"].time() <= SB.TIME_EXIT]
        for future in window:
            low, high = SB._number(future["low"]), SB._number(future["high"])
            day_peak = max(day_peak, high)
            if exit_price is None:
                # Excursions are scoped to the holding period, because only what
                # happened while the position was open is something the exit could
                # have kept. The rest of the day is tracked separately as a
                # perfect-foresight ceiling, which is a different question.
                peak = max(peak, high)
                trough = min(trough, low)
                if low <= stop:
                    outcome = "TRAIL_EXIT" if stop > entry else "STOP"
                    exit_price, exit_at, held = stop, future["local_timestamp"], bars
                elif target_r is not None and high >= entry + risk * target_r:
                    outcome = "TARGET"
                    exit_price = round(entry + risk * target_r, 2)
                    exit_at, held = future["local_timestamp"], bars
                else:
                    high_water = max(high_water, high)
                    gain = (high_water - entry) / risk
                    if trail_gap is not None and gain >= max(activate_r, trail_gap):
                        # A ratchet tightens the leash as the trade matures, on the
                        # theory that a move which has already paid has less left.
                        gap = (max(0.25, trail_gap - 0.25 * (gain - 1.0))
                               if ratchet else trail_gap)
                        stop = max(stop, round(high_water - risk * gap, 2))
            bars += 1
        if exit_price is None:
            if not window:
                return None
            exit_at = window[-1]["local_timestamp"]
            exit_price = SB._number(window[-1]["close"]) * 0.995
            held = len(window)
        trade = {
            **candidate,
            "signal_at": candidate["signal_at"].isoformat(),
            "exit_at": exit_at.isoformat(),
            "entry": entry,
            "stop_loss": initial_stop,
            "exit_stop": stop,
            "target": round(entry + risk * 1.25, 2),
            "runner_target": round(entry + risk * 3, 2),
            "outcome": outcome,
            "realized_r": round((exit_price - entry) / risk, 2),
        }
        if record:
            trade["mfe_r"] = round((peak - entry) / risk, 3)
            trade["mae_r"] = round((trough - entry) / risk, 3)
            trade["day_peak_r"] = round((day_peak - entry) / risk, 3)
            trade["held_bars"] = held
            trade["window_bars"] = len(window)
            # Spot and IV at the fill are what turn a level on the index into a
            # premium, so they are carried on the trade rather than looked up
            # again later against a slightly different bar.
            trade["entry_spot"] = SB._number(next_row["spot"])
            trade["entry_iv"] = SB._number(next_row["implied_volatility"])
            trade["unit_risk"] = risk
        return trade
    return simulate


def run(config, **exit_kwargs):
    SB._simulate = make_simulate(**exit_kwargs)
    try:
        return backtest_strategy("NIFTY", 1, config)
    finally:
        SB._simulate = ORIGINAL_SIMULATE


def book(trades):
    ledger, _skipped, drawdown = sized_ledger(trades)
    if not ledger:
        return None
    wins = sum(1 for row in ledger if row["net_pnl"] > 0)
    return {
        "n": len(ledger),
        "win": 100 * wins / len(ledger),
        "net": sum(row["net_pnl"] for row in ledger),
        "dd": drawdown,
        "r": sum(row["realized_r"] for row in ledger),
    }


def main():
    config = nifty_trail_config()

    print("diagnostic: what the shipped exit leaves behind\n")
    trades = run(config, trail_gap=config.trail_gap_r, record=True)
    realised = np.array([t["realized_r"] for t in trades])
    mfe = np.array([t["mfe_r"] for t in trades])
    mae = np.array([t["mae_r"] for t in trades])
    ceiling = np.array([t["day_peak_r"] for t in trades])
    held = np.array([t["held_bars"] for t in trades])
    window = np.array([t["window_bars"] for t in trades])
    live = mfe > 0.05
    capture = realised[live] / mfe[live]

    print(f"  trades                     {len(trades)}")
    print(f"  realised R    mean {realised.mean():>6.2f}   median {np.median(realised):>6.2f}")
    print("\n  while the position was open:")
    print(f"    MFE R       mean {mfe.mean():>6.2f}   median {np.median(mfe):>6.2f}"
          f"   p75 {np.percentile(mfe, 75):>5.2f}   p90 {np.percentile(mfe, 90):>5.2f}")
    print(f"    MAE R       mean {mae.mean():>6.2f}   median {np.median(mae):>6.2f}")
    print(f"    kept {100 * capture.mean():>5.1f}% of MFE on average, "
          f"median {100 * np.median(capture):.1f}%   (n={live.sum()} with MFE > 0.05R)")
    print(f"    bars held   mean {held.mean():>6.1f}   of {window.mean():>5.1f} to the close")
    print("\n  perfect foresight, same entries, exit anywhere before 15:20:")
    print(f"    best R      mean {ceiling.mean():>6.2f}   median {np.median(ceiling):>6.2f}")
    print(f"    that ceiling is unreachable; it is the size of the prize, not a target")

    winners = mfe[realised > 0]
    losers = mfe[realised <= 0]
    print(f"\n  MFE while open: winners mean {winners.mean():.2f}R, "
          f"losers mean {losers.mean():.2f}R")
    print(f"  losers that were ever up 0.5R before stopping: "
          f"{100 * (losers >= 0.5).mean():.0f}%   ever up 1R: {100 * (losers >= 1.0).mean():.0f}%")

    if "--diagnostic-only" in sys.argv:
        return

    print("\n\nexit search: full pipeline re-run under each rule\n")
    header = (f"{'exit rule':<32}{'n':>5}{'win%':>7}{'totR':>8}{'net Rs':>11}"
              f"{'maxDD':>10}{'Rs/trade':>10}")
    print(header)
    print("-" * len(header))
    variants = [
        ("shipped: trail 0.5R", dict(trail_gap=0.5)),
        ("trail 0.25R", dict(trail_gap=0.25)),
        ("trail 0.75R", dict(trail_gap=0.75)),
        ("trail 1.0R", dict(trail_gap=1.0)),
        ("trail 1.5R", dict(trail_gap=1.5)),
        ("fixed target 1.25R", dict(target_r=1.25)),
        ("fixed target 2R", dict(target_r=2.0)),
        ("fixed target 3R", dict(target_r=3.0)),
        ("trail 0.5R, arm at 1R", dict(trail_gap=0.5, activate_r=1.0)),
        ("trail 0.75R, arm at 1R", dict(trail_gap=0.75, activate_r=1.0)),
        ("trail 1.0R, arm at 1.5R", dict(trail_gap=1.0, activate_r=1.5)),
        ("ratchet from 1.0R", dict(trail_gap=1.0, ratchet=True)),
        ("ratchet from 1.5R", dict(trail_gap=1.5, ratchet=True)),
        ("trail 1.0R + cap 3R", dict(trail_gap=1.0, target_r=3.0)),
        ("trail 1.0R + cap 4R", dict(trail_gap=1.0, target_r=4.0)),
    ]
    for name, kwargs in variants:
        result = book(run(config, **kwargs))
        if not result:
            print(f"{name:<32}   no trades")
            continue
        print(f"{name:<32}{result['n']:>5}{result['win']:>7.1f}{result['r']:>8.1f}"
              f"{result['net']:>11,.0f}{result['dd']:>10,.0f}"
              f"{result['net'] / result['n']:>10,.0f}")


if __name__ == "__main__":
    main()
