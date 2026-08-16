"""RSI and a 20-period moving average, in both places they could possibly help.

There are two entirely different questions hiding in "use RSI and a 20 EMA to
find entries", and they deserve separate answers:

  as a gate       our existing signal fires; do RSI and the moving average tell
                  us which of those firings to skip? This keeps everything that
                  already works and only removes trades.
  as the signal   forget the premium breakout; let RSI and the average generate
                  the entries themselves. This is what the phrase literally asks
                  for, and it is the more demanding test, because it has to beat
                  a strategy that has already survived a lot of scrutiny.

Four classic setups are tried as generators, including the two that ought to fail
if the indicators are working the way traders believe they do -- a mean-reversion
setup fighting the trend, and a momentum setup following it. Testing only the one
we expect to win would tell us nothing about whether the indicator is doing the
work or the exit is.

Everything is held identical to the parent strategy: at-the-money strike, entry
on the next minute's open with the same 0.5% slippage, a 10% premium stop, a 0.7R
trail, flat by 15:20, three trades a day, ten-minute cooldown. So any difference
in the results is the entry and nothing else. The random-entry control at the end
is there because in this project every promising signal so far has turned out to
be indistinguishable from picking a minute out of a hat.
"""
import os
import sys
from collections import defaultdict
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

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from options_tracker.nifty_trail_strategy import (MAX_CASH_FRACTION,
                                                  RISK_PER_TRADE,
                                                  STARTING_CAPITAL,
                                                  nifty_trail_config)

import common as C
import indicators as I
from exit_lab import run

TRAIL = 0.7
STOP_PERCENT = 0.10
SLIPPAGE = 1.005
EXIT_MINUTE = 365  # 15:20 IST
FIRST_MINUTE = 15  # no entries before 09:30, as the parent strategy has it
PREMIUM_MIN, PREMIUM_MAX = 50.0, 250.0
MAX_TRADES_PER_DAY = 3
COOLDOWN = 10
RSI_PERIOD = 14
LENGTH = 20
TIMEFRAMES = (3, 5, 15)
KINDS = ("EMA", "SMA")


def minute_of(stamp):
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    return stamp.hour * 60 + stamp.minute - 555


def simulate(data, side, strike_index, start):
    """The parent strategy's exit, applied to one contract from `start`."""
    opens = data["o"][side, strike_index]
    highs = data["h"][side, strike_index]
    lows = data["l"][side, strike_index]
    closes = data["c"][side, strike_index]
    last = min(EXIT_MINUTE, opens.shape[0] - 1)
    if start > last or not np.isfinite(opens[start]) or opens[start] <= 0:
        return None
    entry = round(float(opens[start]) * SLIPPAGE, 2)
    if not PREMIUM_MIN <= entry <= PREMIUM_MAX:
        return None
    stop = round(entry * (1 - STOP_PERCENT), 2)
    risk = entry - stop
    if risk <= 0:
        return None
    high_water, exit_price, exit_minute = entry, None, last
    for minute in range(start, last + 1):
        low, high = float(lows[minute]), float(highs[minute])
        if not np.isfinite(low) or not np.isfinite(high):
            continue
        if low <= stop:
            exit_price, exit_minute = stop, minute
            break
        high_water = max(high_water, high)
        if (high_water - entry) / risk >= TRAIL:
            stop = max(stop, round(high_water - risk * TRAIL, 2))
    if exit_price is None:
        if not np.isfinite(closes[last]):
            return None
        exit_price = float(closes[last]) * 0.995
    return {"entry": entry, "unit_risk": risk, "exit_price": exit_price,
            "realized_r": (exit_price - entry) / risk, "exit_minute": exit_minute}


def book(trades, capital=STARTING_CAPITAL):
    equity = peak = capital
    drawdown = net_total = 0.0
    taken = wins = 0
    for trade in sorted(trades, key=lambda item: (item["date"], item["minute"])):
        entry, unit_risk = trade["entry"], trade["unit_risk"]
        lots = max(0, min(
            floor(equity * RISK_PER_TRADE / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * MAX_CASH_FRACTION / (entry * NIFTY_LOT_SIZE))))
        if not lots:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        net = ((trade["exit_price"] - entry) * quantity
               - estimate_option_charges(entry, max(trade["exit_price"], 0),
                                         quantity, trade["date"]))
        equity += net
        net_total += net
        taken += 1
        wins += net > 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": taken, "win": 100 * wins / taken if taken else 0.0,
            "net": net_total, "dd": drawdown}


def indicator_frames(dates, sessions):
    """RSI and both averages per timeframe, computed *continuously* across days.

    Resetting the indicators at each open would be a silent crippling of the
    test. RSI(14) on 15-minute bars needs fourteen closed bars, which is 3.5
    hours -- so a session-local RSI does not exist until nearly 12:45, while the
    parent strategy takes more than half its trades before 10:30. Worse, it would
    not be the number the user sees: a chart carries the average and the RSI over
    the overnight gap. So the bars are concatenated in date order, the indicators
    are computed once over the whole run, and each session records the offset of
    its first bar so a minute can be mapped back to a global bar.
    """
    frames = {}
    for minutes in TIMEFRAMES:
        closes, offsets, cursor = [], {}, 0
        for date in dates:
            bars, _highs, _lows = I.resample(sessions[date], minutes)
            offsets[date] = cursor
            closes.append(bars)
            cursor += len(bars)
        closes = np.concatenate(closes) if closes else np.array([])
        frames[minutes] = {
            "closes": closes, "offsets": offsets,
            "rsi": I.rsi(closes, RSI_PERIOD),
            **{kind: I.average(closes, kind, LENGTH) for kind in KINDS},
        }
    return frames


def readings(frames, date, minute, minutes, kind):
    """What an entry at `minute` is allowed to know. None if not yet defined."""
    frame = frames[minutes]
    local = I.last_closed_bar(minute, minutes)
    if local < 0 or date not in frame["offsets"]:
        return None
    bar = frame["offsets"][date] + local
    if bar < 1 or bar >= len(frame["closes"]):
        return None
    values = (frame["rsi"][bar], frame["rsi"][bar - 1],
              frame["closes"][bar], frame["closes"][bar - 1],
              frame[kind][bar], frame[kind][bar - 1])
    if not all(np.isfinite(value) for value in values):
        return None
    now_rsi, prev_rsi, close, prev_close, avg, prev_avg = values
    return {"rsi": now_rsi, "prev_rsi": prev_rsi, "close": close,
            "prev_close": prev_close, "avg": avg, "prev_avg": prev_avg,
            "above": close > avg, "prev_above": prev_close > prev_avg}


# Each setup returns "CALL", "PUT" or None. `r` is the reading dict above.
def setup_pullback(r):
    """Trend from the average, timing from RSI leaving a shallow pullback."""
    if r["above"] and r["prev_rsi"] < 45 <= r["rsi"]:
        return "CALL"
    if not r["above"] and r["prev_rsi"] > 55 >= r["rsi"]:
        return "PUT"
    return None


def setup_cross(r):
    """Price crossing the average, with RSI agreeing about the direction."""
    if not r["prev_above"] and r["above"] and r["rsi"] > 50:
        return "CALL"
    if r["prev_above"] and not r["above"] and r["rsi"] < 50:
        return "PUT"
    return None


def setup_reversal(r):
    """The textbook oversold/overbought bounce, ignoring the trend entirely."""
    if r["prev_rsi"] < 30 <= r["rsi"]:
        return "CALL"
    if r["prev_rsi"] > 70 >= r["rsi"]:
        return "PUT"
    return None


def setup_momentum(r):
    """RSI breaking into strength, on the same side as the average."""
    if r["above"] and r["prev_rsi"] < 60 <= r["rsi"]:
        return "CALL"
    if not r["above"] and r["prev_rsi"] > 40 >= r["rsi"]:
        return "PUT"
    return None


SETUPS = {"pullback": setup_pullback, "EMA cross": setup_cross,
          "RSI reversal": setup_reversal, "RSI momentum": setup_momentum}


def generate(date, data, frames, setup, minutes, kind):
    """Walk the session once, taking signals subject to the day's trade rules."""
    strikes = np.asarray(data["strikes"], dtype=float)
    spot = np.asarray(data["spot"], dtype=float)
    last = min(EXIT_MINUTE, len(spot) - 1)
    trades = []
    blocked_until = FIRST_MINUTE
    for minute in range(FIRST_MINUTE, last):
        if len(trades) >= MAX_TRADES_PER_DAY or minute < blocked_until:
            continue
        if not np.isfinite(spot[minute]):
            continue
        reading = readings(frames, date, minute, minutes, kind)
        if reading is None:
            continue
        side = setup(reading)
        if side is None:
            continue
        strike = int(np.argmin(np.abs(strikes - spot[minute])))
        leg = simulate(data, 0 if side == "CALL" else 1, strike, minute + 1)
        if leg is None:
            continue
        trades.append({**leg, "date": date, "minute": minute,
                       "option_type": side})
        blocked_until = leg["exit_minute"] + COOLDOWN
    return trades


def summarise(label, trades, baseline=None):
    if not trades:
        print(f"  {label:<34}{0:>5}")
        return
    values = np.array([t["realized_r"] for t in trades])
    points = np.array([t["realized_r"] * t["unit_risk"] for t in trades])
    result = book(trades)
    delta = f"{result['net'] - baseline:>+11,.0f}" if baseline is not None else " " * 11
    print(f"  {label:<34}{len(trades):>5}{100 * (values > 0).mean():>8.1f}"
          f"{points.mean():>11.2f}{result['net']:>11,.0f}{result['dd']:>10,.0f}"
          f"{delta}", flush=True)


def main():
    dates = C.session_dates()
    print(f"{len(dates)} sessions; RSI({RSI_PERIOD}) and a {LENGTH}-period "
          f"average on the index, no lookahead\n", flush=True)

    sessions, spots = {}, {}
    for date in dates:
        try:
            data = C.load(date)
        except OSError:
            continue
        spot = np.asarray(data["spot"], dtype=float)
        if len(spot) < 60:
            continue
        sessions[date] = data
        spots[date] = spot
    ordered = sorted(spots)
    frames = indicator_frames(ordered, spots)
    print(f"{len(sessions)} sessions loaded; indicators run continuously across "
          f"them, as a chart would\n", flush=True)

    # ---------------------------------------------------------------- as a gate
    parents = run(nifty_trail_config(), trail_gap=TRAIL, record=True)
    for trade in parents:
        # `exit_lab` records R and the risk unit rather than a price; the ledger
        # here needs the price, and this is the same identity the parent uses.
        trade["exit_price"] = trade["entry"] + trade["realized_r"] * trade["unit_risk"]
        trade["minute"] = minute_of(trade["signal_at"])
    print(f"AS A GATE on the {len(parents)} trades we already take.")
    print(f"Read this first: if the indicators cannot separate winners from "
          f"losers on entries\nwe have already validated, they are unlikely to "
          f"find better ones from scratch.\n")
    for minutes in TIMEFRAMES:
        print(f"  {minutes}-minute bars, EMA")
        print(f"  {'':<32}{'n':>5}{'win%':>8}{'pts/trade':>11}")
        buckets = defaultdict(list)
        for trade in parents:
            if trade["date"] not in sessions:
                continue
            reading = readings(frames, trade["date"], trade["minute"],
                               minutes, "EMA")
            if reading is None:
                continue
            call = trade["option_type"] == "CALL"
            # Stated from the trade's own point of view: "with us" means the
            # index was above its average on a call, or below it on a put.
            aligned = reading["above"] == call
            strength = reading["rsi"] if call else 100 - reading["rsi"]
            buckets["average with us" if aligned else "average against us"].append(trade)
            buckets["RSI under 40" if strength < 40 else
                    "RSI 40-50" if strength < 50 else
                    "RSI 50-60" if strength < 60 else
                    "RSI 60-70" if strength < 70 else
                    "RSI over 70"].append(trade)
        for name in ("average with us", "average against us", "RSI under 40",
                     "RSI 40-50", "RSI 50-60", "RSI 60-70", "RSI over 70"):
            rows = buckets.get(name, [])
            if not rows:
                print(f"    {name:<30}{0:>5}")
                continue
            values = np.array([t["realized_r"] for t in rows])
            points = np.array([t["realized_r"] * t["unit_risk"] for t in rows])
            print(f"    {name:<30}{len(rows):>5}"
                  f"{100 * (values > 0).mean():>8.1f}{points.mean():>11.2f}")
        print(flush=True)

    # ------------------------------------------------------------- as the signal
    print(f"\nAS THE SIGNAL: RSI and the average generate the entries themselves.")
    print(f"Same strike, stop, trail, exit time and sizing as the parent, so the "
          f"entry is the\nonly thing that differs. 'vs parent' compares against "
          f"the shipped strategy's net.\n")
    base = book(parents)
    print(f"  {'setup':<34}{'n':>5}{'win%':>8}{'pts/trade':>11}{'net Rs':>11}"
          f"{'maxDD':>10}{'vs parent':>11}")
    print(f"  {'shipped strategy':<34}{base['n']:>5}{base['win']:>8.1f}"
          f"{np.mean([t['realized_r'] * t['unit_risk'] for t in parents]):>11.2f}"
          f"{base['net']:>11,.0f}{base['dd']:>10,.0f}{0:>+11,.0f}")
    print()

    best = {}
    for name, setup in SETUPS.items():
        for minutes in TIMEFRAMES:
            for kind in KINDS:
                trades = []
                for date, data in sessions.items():
                    trades.extend(generate(date, data, frames, setup, minutes, kind))
                summarise(f"{name}, {minutes}min, {kind}", trades, base["net"])
                if trades:
                    result = book(trades)
                    if name not in best or result["net"] > best[name][0]["net"]:
                        best[name] = (result, trades, minutes, kind)
        print(flush=True)

    # ----------------------------------------------------------------- control
    print(f"\nZERO-SKILL CONTROL. Each setup's best variant, against the same "
          f"number of trades\ntaken on the same days at random minutes, 200 "
          f"draws. A real signal should beat\nalmost all of them; anything under "
          f"about 95% is inside the noise.\n")
    rng = np.random.default_rng(20260815)
    print(f"  {'setup'  :<34}{'real Rs':>11}{'random median':>15}{'beats':>8}")
    for name, (result, trades, minutes, kind) in best.items():
        per_day = defaultdict(int)
        for trade in trades:
            per_day[trade["date"]] += 1
        totals = []
        for _draw in range(200):
            drawn = []
            for date, count in per_day.items():
                data = sessions[date]
                strikes = np.asarray(data["strikes"], dtype=float)
                spot = np.asarray(data["spot"], dtype=float)
                latest = min(EXIT_MINUTE, len(spot) - 1) - 10
                if latest <= FIRST_MINUTE:
                    continue
                for _ in range(count):
                    for _attempt in range(6):
                        minute = int(rng.integers(FIRST_MINUTE, latest))
                        if not np.isfinite(spot[minute]):
                            continue
                        strike = int(np.argmin(np.abs(strikes - spot[minute])))
                        side = int(rng.integers(0, 2))
                        leg = simulate(data, side, strike, minute + 1)
                        if leg:
                            drawn.append({**leg, "date": date, "minute": minute})
                            break
            totals.append(book(drawn)["net"] if drawn else 0.0)
        totals = np.array(totals)
        print(f"  {f'{name}, {minutes}min, {kind}':<34}{result['net']:>11,.0f}"
              f"{np.median(totals):>15,.0f}"
              f"{100 * (result['net'] > totals).mean():>7.0f}%", flush=True)


if __name__ == "__main__":
    main()
