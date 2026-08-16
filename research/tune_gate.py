"""Tune the shipped strategy with the one thing the indicator study salvaged.

RSI and a 20-period average failed completely as entry *generators* -- all 24
variants lost money. But one reading held up as a *gate* on the entries we
already take: when the index was on the correct side of its 20 EMA, the parent
strategy won 74-80% of the time against 60-62% when it was not. That is the piece
worth keeping, and this file tests it properly.

"Properly" means through the whole pipeline rather than by striking rows out of
the finished trade list. Removing a trade frees a slot under the three-a-day cap
and can move the cooldown and the daily loss limit, so the filtered strategy is
not simply the old one minus some rows -- it can take trades the original never
reached. The gate is therefore injected into candidate qualification and the
backtest is re-run from scratch for every variant.

The RSI half is included as a floor rather than the textbook ceiling, because the
gate study found the opposite of the textbook: the *highest* RSI bucket was the
best one in all three timeframes (7.65, 9.01 and 10.70 points a trade at 80-91%
win rates). On a trend-following strategy an extended RSI is confirmation, not a
warning. If that is real a floor will help and a ceiling will hurt, so both are
tried.
"""
import os
import sys
from dataclasses import replace
from datetime import datetime

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

import common as C
import indicators as I
from exit_lab import book, run
from fast_backtest import cached_contracts

TRAIL = 0.7
RSI_PERIOD = 14
LENGTH = 20
ORIGINAL_CANDIDATE = SB._candidate

FRAMES = {}


def build_frames(timeframes):
    """Continuous cross-session RSI and averages, keyed by timeframe."""
    spots = {}
    for date in C.session_dates():
        try:
            spot = np.asarray(C.load(date)["spot"], dtype=float)
        except OSError:
            continue
        if len(spot) >= 60:
            spots[date] = spot
    ordered = sorted(spots)
    for minutes in timeframes:
        closes, offsets, cursor = [], {}, 0
        for date in ordered:
            bars, _highs, _lows = I.resample(spots[date], minutes)
            offsets[date] = cursor
            closes.append(bars)
            cursor += len(bars)
        closes = np.concatenate(closes)
        FRAMES[minutes] = {"closes": closes, "offsets": offsets,
                           "rsi": I.rsi(closes, RSI_PERIOD),
                           "EMA": I.average(closes, "EMA", LENGTH)}
    return len(spots)


def reading(date, minute, minutes):
    frame = FRAMES[minutes]
    local = I.last_closed_bar(minute, minutes)
    if local < 0 or date not in frame["offsets"]:
        return None
    bar = frame["offsets"][date] + local
    if bar < 0 or bar >= len(frame["closes"]):
        return None
    close, avg, strength = frame["closes"][bar], frame["EMA"][bar], frame["rsi"][bar]
    if not (np.isfinite(close) and np.isfinite(avg) and np.isfinite(strength)):
        return None
    return close > avg, strength


def make_candidate(minutes, require_trend=True, rsi_floor=None, rsi_ceiling=None):
    """The shipped qualification, plus an index-level gate on top of it.

    A candidate that the original rejects is still rejected; this can only ever
    remove trades, never invent them, so any improvement has to come from the
    trades it declines rather than from a different signal.
    """
    def candidate(contract_key, rows, index, config, *args, **kwargs):
        result = ORIGINAL_CANDIDATE(contract_key, rows, index, config,
                                    *args, **kwargs)
        if result is None:
            return None
        # The candidate carries its own date and timestamp, so there is no need
        # to reach back into `rows` and risk disagreeing with it.
        stamp = result["signal_at"]
        values = reading(result["date"],
                         stamp.hour * 60 + stamp.minute - 555, minutes)
        if values is None:
            # Undefined indicator is not evidence either way. Letting the trade
            # through keeps the comparison honest -- otherwise the gate would
            # also be quietly filtering out early sessions.
            return result
        above, strength = values
        call = result["option_type"] == "CALL"
        if require_trend and above != call:
            return None
        # Read RSI from the trade's own side so a put is judged on the same
        # scale as a call rather than by an inverted threshold.
        aligned = strength if call else 100 - strength
        if rsi_floor is not None and aligned < rsi_floor:
            return None
        if rsi_ceiling is not None and aligned > rsi_ceiling:
            return None
        return result
    return candidate


def evaluate(label, config, candidate=None, baseline=None):
    SB._candidate = candidate or ORIGINAL_CANDIDATE
    try:
        trades = run(config, trail_gap=TRAIL, record=True)
    finally:
        SB._candidate = ORIGINAL_CANDIDATE
    result = book(trades)
    if not result:
        print(f"  {label:<38}{0:>5}")
        return None
    points = np.mean([t["realized_r"] * t["unit_risk"] for t in trades])
    delta = f"{result['net'] - baseline:>+11,.0f}" if baseline is not None else " " * 11
    print(f"  {label:<38}{result['n']:>5}{result['win']:>8.1f}{points:>11.2f}"
          f"{result['net']:>11,.0f}{result['dd']:>10,.0f}{delta}", flush=True)
    return result


def main():
    # Cache contracts once; all variants reuse it (27s each instead of 187s).
    print("Loading contracts...", flush=True)
    import time
    t = time.time()
    contracts = cached_contracts()
    print(f"Contracts cached ({time.time() - t:.1f}s)\n", flush=True)

    # Monkeypatch load_contract_rows to return the cached set.
    from options_tracker.strategy_backtest import load_contract_rows as original_load
    SB.load_contract_rows = lambda *args, **kwargs: contracts

    loaded = build_frames((3, 5, 15))
    print(f"{loaded} sessions; 20 EMA and RSI({RSI_PERIOD}) run continuously "
          f"across them\n", flush=True)

    base = nifty_trail_config()
    header = (f"  {'variant':<38}{'n':>5}{'win%':>8}{'pts/trade':>11}"
              f"{'net Rs':>11}{'maxDD':>10}{'vs shipped':>11}")

    print("THE TREND GATE ALONE: index must be on the correct side of its 20 EMA.\n")
    print(header)
    shipped = evaluate("shipped strategy", base)
    reference = shipped["net"]
    for minutes in (3, 5, 15):
        evaluate(f"+ 20 EMA gate, {minutes}min", base,
                 make_candidate(minutes), reference)

    print(f"\n\nTHE RSI HALF: a floor keeps only strong readings, a ceiling only "
          f"calm ones.\n  The gate study says the floor should help and the "
          f"ceiling should hurt.\n")
    print(header)
    for floor in (40, 50, 60):
        evaluate(f"+ RSI floor {floor}, 15min", base,
                 make_candidate(15, require_trend=False, rsi_floor=floor),
                 reference)
    evaluate("+ RSI ceiling 70, 15min", base,
             make_candidate(15, require_trend=False, rsi_ceiling=70), reference)

    print(f"\n\nCOMBINED with the premium band from last night's review.\n"
          f"  Rs 100-200 was the other change on the table; these are the joint "
          f"configurations.\n")
    print(header)
    narrow = replace(base, premium_min=100, premium_max=200)
    evaluate("premium 100-200 only", narrow, None, reference)
    for minutes in (5, 15):
        evaluate(f"premium 100-200 + EMA gate {minutes}min", narrow,
                 make_candidate(minutes), reference)
    evaluate("premium 100-200 + EMA 15min + RSI 50", narrow,
             make_candidate(15, rsi_floor=50), reference)


if __name__ == "__main__":
    main()
