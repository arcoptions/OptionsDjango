"""One execution model, shared by every strategy test in this project.

Each new idea used to arrive with its own copy of the fill, stop, trail and
ledger code. That made results quietly incomparable -- a strategy could look
better because its author rounded the entry differently. Everything here is
lifted from the shipped strategy so that when two ideas are put side by side the
only thing differing is the entry and exit *rule*, never the plumbing.

The house rules, applied to everything:

  fill        next minute's open, times 1.005 -- half a percent against us
  strike      chosen at the signal minute, never revised
  flat by     15:20, at that bar's close times 0.995
  sizing      Rs 1,00,000 compounding, 2% risk a trade, 40% cash cap, real
              brokerage and STT via estimate_option_charges
  bar order   adverse first: the stop is tested against this bar's low before
              this bar's high is allowed to raise the high-water mark, so a bar
              that both stops us out and makes a new high counts as a stop

That last rule matters more than it looks. Reversing it flatters every trailing
exit by letting a losing bar pay out first, and it is the single easiest way to
manufacture a strategy that cannot be traded.
"""
import os
import sys
from collections import defaultdict
from math import floor

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from options_tracker.nifty_trail_strategy import (MAX_CASH_FRACTION,
                                                  RISK_PER_TRADE,
                                                  STARTING_CAPITAL)

import common as C

CALL, PUT = 0, 1
EXIT_MINUTE = 365      # 15:20 IST on the 375-minute grid
SLIPPAGE = 1.005
EXIT_SLIPPAGE = 0.995

_SESSIONS = {}


PRICE_KEYS = ("o", "h", "l", "c")


def session(date, keys=PRICE_KEYS):
    """Decompressed arrays for one session, cached.

    np.load hands back a lazy handle that re-inflates the whole array on every
    key access, so reaching for data["o"] inside a per-signal loop costs a
    megabyte of decompression each time. Pulling them out once turns that into
    a dictionary lookup.

    Only the four price cubes are taken by default. Volume, open interest and IV
    are another three cubes of the same size, and holding all 246 sessions with
    them costs about 700MB for arrays a price strategy never reads. A study that
    wants them asks for them.
    """
    if date in _SESSIONS:
        return _SESSIONS[date]
    raw = C.load(date)
    data = {"strikes": np.asarray(raw["strikes"], dtype=float),
            "spot": np.asarray(raw["spot"], dtype=float)}
    for key in keys:
        data[key] = np.asarray(raw[key], dtype=np.float32)
    _SESSIONS[date] = data
    return data


def sessions(minimum_minutes=60):
    """Every usable session, in date order. Loaded once and kept."""
    out = {}
    for date in C.session_dates():
        try:
            data = session(date)
        except (OSError, KeyError):
            continue
        if len(data["spot"]) >= minimum_minutes:
            out[date] = data
    return out


def strike_index(strikes, spot, side, steps=0):
    """Index of the wanted strike. steps > 0 is in the money, < 0 is out.

    A call is in the money at a *lower* strike and a put at a higher one, so the
    sign flips with the side. Returns None when the chain does not reach that
    far, rather than silently clamping to the end of the array and trading a
    strike nobody asked for.
    """
    atm = int(np.argmin(np.abs(strikes - spot)))
    wanted = atm - steps if side == CALL else atm + steps
    if not 0 <= wanted < len(strikes):
        return None
    return wanted


def simulate(data, side, index, start, *, stop_percent=0.10, target_percent=None,
             trail_gap=None, trail_percent=None, abort_at=None,
             premium_min=30.0, premium_max=500.0, exit_minute=EXIT_MINUTE):
    """Hold one option from `start` until a rule takes us out.

    stop_percent     hard stop this far below the fill, as a fraction of premium
    target_percent   take profit this far above the fill (0.20 is "+20%")
    trail_gap        trail this many R behind the running high
    trail_percent    trail this fraction below the running high
    abort_at         minute at which an index-side condition forces an exit at
                     that bar's close, whatever the premium is doing

    Returns None when the contract cannot be traded at all -- no fill, a premium
    outside the band, or a degenerate risk unit. That is deliberately different
    from a losing trade, so the caller can tell "we passed" from "we lost".
    """
    opens, highs = data["o"][side, index], data["h"][side, index]
    lows, closes = data["l"][side, index], data["c"][side, index]
    last = min(exit_minute, opens.shape[0] - 1)
    if start > last or not np.isfinite(opens[start]) or opens[start] <= 0:
        return None
    entry = round(float(opens[start]) * SLIPPAGE, 2)
    if not premium_min <= entry <= premium_max:
        return None
    stop = round(entry * (1 - stop_percent), 2)
    risk = entry - stop
    if risk <= 0:
        return None
    target = round(entry * (1 + target_percent), 2) if target_percent else None

    high_water = entry
    exit_price, exit_at, outcome = None, last, "TIME"
    for minute in range(start, last + 1):
        low, high = float(lows[minute]), float(highs[minute])
        if not np.isfinite(low) or not np.isfinite(high):
            continue
        if low <= stop:
            exit_price, exit_at = stop, minute
            outcome = "TRAIL" if stop > entry else "STOP"
            break
        if target is not None and high >= target:
            exit_price, exit_at, outcome = target, minute, "TARGET"
            break
        if abort_at is not None and minute >= abort_at:
            value = float(closes[minute])
            if np.isfinite(value):
                exit_price, exit_at, outcome = value, minute, "ABORT"
                break
        high_water = max(high_water, high)
        if trail_gap is not None and (high_water - entry) / risk >= trail_gap:
            stop = max(stop, round(high_water - risk * trail_gap, 2))
        if trail_percent is not None and high_water > entry:
            stop = max(stop, round(high_water * (1 - trail_percent), 2))
    if exit_price is None:
        if not np.isfinite(closes[last]):
            return None
        exit_price = float(closes[last]) * EXIT_SLIPPAGE
    return {"entry": entry, "unit_risk": risk, "exit_price": exit_price,
            "realized_r": (exit_price - entry) / risk,
            "gain_percent": 100 * (exit_price - entry) / entry,
            "exit_minute": exit_at, "outcome": outcome}


def book(trades, capital=STARTING_CAPITAL, risk=RISK_PER_TRADE):
    """Compound Rs 1,00,000 through the trades in the order they happened."""
    equity = peak = capital
    drawdown = net_total = 0.0
    taken = wins = 0
    for trade in sorted(trades, key=lambda item: (item["date"], item["minute"])):
        entry, unit_risk = trade["entry"], trade["unit_risk"]
        lots = max(0, min(
            floor(equity * risk / (unit_risk * NIFTY_LOT_SIZE)),
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
            "net": net_total, "dd": drawdown,
            "pts": float(np.mean([t["realized_r"] * t["unit_risk"]
                                  for t in trades])) if trades else 0.0}


HEADER = (f"  {'variant':<40}{'n':>5}{'win%':>8}{'pts/trade':>11}{'net Rs':>11}"
          f"{'maxDD':>10}{'vs base':>11}")


def report(label, trades, baseline=None):
    """One line of the results table. Returns the booked result."""
    if not trades:
        print(f"  {label:<40}{0:>5}{'-':>8}{'-':>11}{'-':>11}{'-':>10}")
        return None
    result = book(trades)
    delta = f"{result['net'] - baseline:>+11,.0f}" if baseline is not None else " " * 11
    print(f"  {label:<40}{result['n']:>5}{result['win']:>8.1f}{result['pts']:>11.2f}"
          f"{result['net']:>11,.0f}{result['dd']:>10,.0f}{delta}", flush=True)
    return result


def control(trades, loaded, draws=200, seed=20260816, **exit_kwargs):
    """Same days, same number of trades, random minutes and random sides.

    This is the bar every idea in this project has to clear. It answers the only
    question that matters about a backtest: is the *timing* worth anything, or
    would throwing darts on the same days have done as well? Anything under
    about 95% is inside the noise.
    """
    if not trades:
        return None
    rng = np.random.default_rng(seed)
    per_day = defaultdict(int)
    for trade in trades:
        per_day[trade["date"]] += 1
    totals = []
    for _draw in range(draws):
        drawn = []
        for date, count in per_day.items():
            data = loaded.get(date)
            if data is None:
                continue
            strikes, spot = data["strikes"], data["spot"]
            latest = min(EXIT_MINUTE, len(spot) - 1) - 10
            if latest <= 20:
                continue
            for _ in range(count):
                for _attempt in range(6):
                    minute = int(rng.integers(20, latest))
                    if not np.isfinite(spot[minute]):
                        continue
                    side = int(rng.integers(0, 2))
                    index = strike_index(strikes, spot[minute], side, 0)
                    if index is None:
                        continue
                    leg = simulate(data, side, index, minute + 1, **exit_kwargs)
                    if leg:
                        drawn.append({**leg, "date": date, "minute": minute})
                        break
        totals.append(book(drawn)["net"] if drawn else 0.0)
    totals = np.array(totals)
    real = book(trades)["net"]
    return {"real": real, "median": float(np.median(totals)),
            "beats": 100 * float((real > totals).mean()),
            "p5": float(np.percentile(totals, 5)),
            "p95": float(np.percentile(totals, 95))}
