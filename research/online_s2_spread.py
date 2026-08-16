"""Strategy 2: the same signal expressed as a debit spread instead of a long option.

Identical entries to Strategy 1, so this isolates one variable -- structure. Buy
the 0.65 delta, sell the 0.30 delta of the same expiry, and manage the pair on
the index exactly as the outright is managed. The report claims the spread is
the best drawdown-control variant when IV is elevated, so the runs are also cut
by ATM-IV percentile to see whether that condition is doing any work.

The short leg is sold at its bid and bought back at its ask in spirit: both legs
cross the same modelled half-spread, applied in charges rather than here, so the
comparison against the outright stays like-for-like on gross premium.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import online_s1_breakout as S
import regime as R

LONG_DELTA = 0.65
SHORT_DELTA = 0.30
MAX_DEBIT_FRACTION = 0.50  # of spread width, per the report


def iv_percentile_table(dates, lookback=60):
    """ATM IV at the open, ranked against the trailing lookback sessions."""
    series = []
    for date in dates:
        try:
            session = C.load(date)
        except OSError:
            series.append((date, None))
            continue
        spot = R._ffill(session["spot"].astype(np.float64))
        if len(spot) < 200:
            series.append((date, None))
            continue
        series.append((date, R.atm_iv(session, 30)))
    table = {}
    values = [value for _date, value in series]
    for index, (date, value) in enumerate(series):
        if value is None or index < 20:
            continue
        history = [v for v in values[max(0, index - lookback):index] if v is not None]
        if len(history) < 15:
            continue
        table[date] = 100.0 * sum(1 for v in history if v < value) / len(history)
    return table


def run_spread(session, spot, signal, days_to_expiry):
    """Price both legs off the same fill and manage the pair on the index."""
    is_call = signal["bullish"]
    entry_row = None
    for row in range((signal["bar"] + 1) * S.BAR, min(S.LAST_ENTRY, len(spot))):
        if (spot[row] > signal["trigger"]) if is_call else (spot[row] < signal["trigger"]):
            entry_row = row
            break
        if (spot[row] < signal["stop_spot"]) if is_call else (spot[row] > signal["stop_spot"]):
            return None
    if entry_row is None:
        return None

    long_column = R.pick_by_delta(session, entry_row, LONG_DELTA, is_call, days_to_expiry)
    short_column = R.pick_by_delta(session, entry_row, SHORT_DELTA, is_call, days_to_expiry)
    if long_column is None or short_column is None or long_column == short_column:
        return None
    side = 0 if is_call else 1
    closes = session["c"][side].astype(np.float64)
    long_leg = closes[long_column]
    short_leg = closes[short_column]
    debit = long_leg[entry_row] - short_leg[entry_row]
    width = abs(session["strikes"][long_column] - session["strikes"][short_column])
    if np.isnan(debit) or debit <= 0 or width <= 0:
        return None
    if debit > MAX_DEBIT_FRACTION * width:
        return None  # paying too much of the width for the payoff

    entry_spot = spot[entry_row]
    stop_spot = signal["stop_spot"]
    risk = abs(entry_spot - stop_spot)
    if risk <= 0:
        return None

    high, low, _bar_close = S.bars15(spot)
    booked = 0.0
    remaining = 1.0
    partial_done = False
    entry_bar = signal["bar"] + 1
    limit = min(S.TIME_EXIT, len(spot), closes.shape[1])

    for row in range(entry_row + 1, limit):
        value = long_leg[row] - short_leg[row]
        if np.isnan(value):
            continue
        move = (spot[row] - entry_spot) if is_call else (entry_spot - spot[row])
        hit = (spot[row] <= stop_spot) if is_call else (spot[row] >= stop_spot)
        # Take profit once the spread has earned most of what it can.
        if value >= 0.70 * width:
            return _close(debit, value, booked, remaining, risk, "TARGET", row)
        if hit:
            return _close(debit, value, booked, remaining, risk, "STOP", row)
        if not partial_done and move >= risk:
            booked += S.PARTIAL_FRACTION * (value - debit)
            remaining -= S.PARTIAL_FRACTION
            partial_done = True
            stop_spot = entry_spot
        bar_index = row // S.BAR
        if bar_index > entry_bar:
            if partial_done:
                swing = low[bar_index - 1] if is_call else high[bar_index - 1]
                stop_spot = max(stop_spot, swing) if is_call else min(stop_spot, swing)
            elif bar_index - entry_bar >= S.TIME_STOP_BARS:
                return _close(debit, value, booked, remaining, risk, "TIME_STOP", row)
    final = long_leg[limit - 1] - short_leg[limit - 1]
    if np.isnan(final):
        return None
    return _close(debit, final, booked, remaining, risk, "TIME_EXIT", limit - 1)


def _close(debit, value, booked, remaining, risk, outcome, row):
    total = booked + remaining * (value - debit)
    unit_risk = min((LONG_DELTA - SHORT_DELTA) * risk, debit)
    return {
        "entry": debit,
        "premium_pnl": total,
        "unit_risk": unit_risk,
        "premium_r": total / unit_risk if unit_risk > 0 else 0.0,
        "outcome": outcome,
        "exit_row": row,
        "index_risk": risk,
    }


def run(dates=None, iv_floor=None, iv_table=None):
    dates = dates or C.session_dates()
    table = R.regime(dates)
    calendar = R.expiry_calendar(dates)
    trades = []
    for date in dates:
        if iv_floor is not None:
            percentile = (iv_table or {}).get(date)
            if percentile is None or percentile < iv_floor:
                continue
        try:
            session = C.load(date)
        except OSError:
            continue
        spot = R._ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        anchor = R.vwap_proxy(session)
        days = calendar.get(date, 1)
        for signal in S.signals(date, table, spot, anchor):
            trade = run_spread(session, spot, signal, days)
            if trade:
                trades.append({**trade, "date": date, "dte": days})
    return trades


def main():
    dates = C.session_dates()
    print("Strategy 2: debit spread on the Strategy 1 signal, NIFTY\n")
    ivs = iv_percentile_table(dates)
    header = (f"{'variant':<26}{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}"
              f"{'PF':>7}{'avgWin':>9}{'avgLoss':>9}")
    print(header)
    print("-" * len(header))
    cases = [("all signals", None), ("IV percentile >= 50", 50.0),
             ("IV percentile >= 60", 60.0)]
    for name, floor in cases:
        result = S.summarise(run(dates, iv_floor=floor, iv_table=ivs))
        if not result:
            print(f"{name:<26}   no trades")
            continue
        print(f"{name:<26}{result['n']:>5}{result['win']:>7.1f}{result['totR']:>9.1f}"
              f"{result['avgR']:>8.3f}{result['pf']:>7.2f}{result['avgWin']:>9.2f}"
              f"{result['avgLoss']:>9.2f}")

    print("\nsame signals as an outright long, for comparison")
    for target in (0.65, 0.55):
        result = S.summarise(S.run(target, dates))
        print(f"  outright delta {target:.2f}      {result['n']:>3}"
              f"{result['win']:>7.1f}{result['totR']:>9.1f}{result['avgR']:>8.3f}"
              f"{result['pf']:>7.2f}{result['avgWin']:>9.2f}{result['avgLoss']:>9.2f}")


if __name__ == "__main__":
    main()
