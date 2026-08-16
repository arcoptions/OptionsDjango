"""Trade the confirmed pivots with real option premium, against a random control.

A fractal high at bar i is knowable at the close of bar i+k and can never be
un-confirmed, so this is a legitimate causal signal -- unlike the circles on the
chart, which are drawn knowing everything that followed.

Entry is the *open of the bar after* confirmation, so nothing in the fill uses
the price that triggered it. The strike is fixed at entry, because relative
strike is recomputed every minute as spot drifts and a position has to hold one
contract. Exits reuse the production machinery: a percent stop that trails
0.5R behind the running high once the trade is 0.5R in profit.

Both directions are run. Reversal buys the option that pays if the pivot holds;
continuation buys the one that pays if it breaks. Every prior reversal test in
this project failed, so continuation is not a throwaway control.
"""
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
from swing_pivots import BAR, alternating, bars, pivots
from swing_chart_match import significant

K = 3
STOP_PERCENT = 0.10
TRAIL_R = 0.5
LAST_ENTRY = 330  # 14:45 IST, leaving room to be stopped before the close
TIME_EXIT = 354  # 15:09 IST
PREMIUM_MIN, PREMIUM_MAX = 40.0, 400.0
COOLDOWN = 15  # minutes, so one move cannot be traded three times


def atm_index(strikes, spot):
    return int(np.argmin(np.abs(strikes - spot)))


def simulate(session, row, side):
    """Buy one option at row's open, hold under a trailing stop. side: 0 CE 1 PE."""
    strikes = session["strikes"]
    spot = session["spot"].astype(np.float64)
    if row >= min(LAST_ENTRY, len(spot)) or np.isnan(spot[row]):
        return None
    column = atm_index(strikes, spot[row])
    opens = session["o"][side, column].astype(np.float64)
    highs = session["h"][side, column].astype(np.float64)
    lows = session["l"][side, column].astype(np.float64)
    entry = opens[row]
    if not (PREMIUM_MIN <= entry <= PREMIUM_MAX):
        return None
    risk = entry * STOP_PERCENT
    stop = entry - risk
    high_water = entry
    for index in range(row + 1, min(TIME_EXIT, len(highs))):
        low, high = lows[index], highs[index]
        if np.isnan(low) or np.isnan(high):
            continue
        if low <= stop:  # adverse resolves first -- the conservative assumption
            return {"r": (stop - entry) / risk, "entry": entry, "exit_row": index}
        high_water = max(high_water, high)
        if high_water - entry >= risk * TRAIL_R:
            stop = max(stop, high_water - risk * TRAIL_R)
    final = lows[min(TIME_EXIT, len(lows)) - 1]
    if np.isnan(final):
        return None
    return {"r": (final - entry) / risk, "entry": entry, "exit_row": TIME_EXIT}


def signals(date, minimum_swing):
    """Confirmed fractal pivots, filtered on the leg that already happened."""
    data = bars(date)
    if not data:
        return []
    high, low, _close, _stamp = data
    chain = alternating(*pivots(high, low, K))
    out = []
    for position, (index, kind) in enumerate(chain):
        if not position:
            continue
        previous, _ = chain[position - 1]
        incoming = (high[index] - low[previous]) if kind == "H" else (high[previous] - low[index])
        if incoming < minimum_swing:
            continue
        row = (index + K + 1) * BAR  # open of the bar after confirmation
        out.append({"row": row, "kind": kind, "incoming": incoming})
    return out


def run(minimum_swing, mode):
    """mode: 'reversal' fades the pivot, 'continuation' backs the break."""
    trades = []
    for date in C.session_dates():
        try:
            session = C.load(date)
        except OSError:
            continue
        available = -1
        for signal in signals(date, minimum_swing):
            if signal["row"] <= available:
                continue
            fade = signal["kind"] == "H"
            if mode == "continuation":
                fade = not fade
            side = 1 if fade else 0  # fade a top by buying the put
            trade = simulate(session, signal["row"], side)
            if not trade:
                continue
            trades.append({**trade, "date": date, "kind": signal["kind"]})
            available = trade["exit_row"] + COOLDOWN
    return trades


def entry_rows(date):
    """Every row a random control could have entered on, same eligibility."""
    return list(range(BAR * (K + 1), LAST_ENTRY))


def control(actual, iterations, seed):
    """Random entries, same count per session, same exits, both sides."""
    rng = random.Random(seed)
    per_date = {}
    for trade in actual:
        per_date[trade["date"]] = per_date.get(trade["date"], 0) + 1
    sessions = {}
    for date in per_date:
        try:
            sessions[date] = C.load(date)
        except OSError:
            pass
    wins, totals = [], []
    for _ in range(iterations):
        drawn = []
        for date, count in per_date.items():
            session = sessions.get(date)
            if session is None:
                continue
            available = -1
            taken = 0
            for row in sorted(rng.sample(entry_rows(date), min(60, LAST_ENTRY))):
                if taken >= count:
                    break
                if row <= available:
                    continue
                trade = simulate(session, row, rng.choice((0, 1)))
                if not trade:
                    continue
                drawn.append(trade)
                available = trade["exit_row"] + COOLDOWN
                taken += 1
        if drawn:
            wins.append(100 * sum(1 for t in drawn if t["r"] > 0) / len(drawn))
            totals.append(sum(t["r"] for t in drawn))
    return wins, totals


def summarise(trades):
    if not trades:
        return None
    r = np.array([t["r"] for t in trades])
    gains = r[r > 0].sum()
    losses = -r[r <= 0].sum()
    return {
        "n": len(r),
        "win": 100 * (r > 0).mean(),
        "totR": r.sum(),
        "avgR": r.mean(),
        "pf": gains / losses if losses else float("inf"),
    }


def main():
    header = (f"{'mode':<13}{'minSwing':>9}{'n':>5}{'win%':>7}{'totR':>9}"
              f"{'avgR':>8}{'PF':>7}")
    print(header)
    print("-" * len(header))
    best = {}
    for mode in ("reversal", "continuation"):
        for minimum_swing in (0, 30, 50, 70):
            result = summarise(run(minimum_swing, mode))
            if not result:
                continue
            print(f"{mode:<13}{minimum_swing:>9}{result['n']:>5}{result['win']:>7.1f}"
                  f"{result['totR']:>9.1f}{result['avgR']:>8.2f}{result['pf']:>7.2f}")
            best[(mode, minimum_swing)] = result
    return best


if __name__ == "__main__":
    main()
