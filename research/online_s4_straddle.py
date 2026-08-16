"""Strategy 4: buy the ATM straddle when the implied move looks too cheap.

The report scopes this to known catalysts, and there is no event calendar in
this project, so what is tested is the measurable half of its entry rule: buy
only when a forecast of the day's absolute move is materially larger than the
move the options are pricing.

  implied     S * IV * sqrt(DTE/365), the report's own formula, cross-checked
              against 85% of the ATM straddle premium.
  forecast    trailing median absolute session move. A naive forecast, but it
              is the honest one -- anything cleverer would be fitted to the
              same data it is being tested on.

An unconditional every-session straddle is run alongside it. If the filter is
worth anything, the filtered set has to beat that baseline; otherwise the rule
is just trading less.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import regime as R

ENTRY_ROW = 30  # 09:45 IST
TIME_EXIT = 354
COMBINED_STOP = 0.30
LOOKBACK = 20
EDGE_REQUIRED = 1.25  # forecast must exceed implied by this multiple
HORIZON_DAYS = 1.0  # the straddle is closed the same session, not held to expiry


def straddle_series(session, row):
    """Combined ATM call+put premium path from row, strike fixed at entry."""
    spot = R._ffill(session["spot"].astype(np.float64))
    strikes = session["strikes"].astype(np.float64)
    column = int(np.argmin(np.abs(strikes - spot[row])))
    call = session["c"][0, column].astype(np.float64)
    put = session["c"][1, column].astype(np.float64)
    return call + put, spot


def realised_moves(dates):
    """Absolute open-to-close move of each completed session, in points."""
    keep, opens, highs, lows, closes = R.daily_bars(dates)
    return keep, np.abs(closes - opens)


def run(dates=None, require_edge=True):
    dates = dates or C.session_dates()
    calendar = R.expiry_calendar(dates)
    keep, moves = realised_moves(dates)
    history = {date: index for index, date in enumerate(keep)}
    trades = []
    for date in dates:
        index = history.get(date)
        if index is None or index < LOOKBACK:
            continue
        try:
            session = C.load(date)
        except OSError:
            continue
        spot = R._ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        iv = R.atm_iv(session, ENTRY_ROW)
        days = max(calendar.get(date, 1), 1)
        if iv is None:
            continue
        # The report's formula prices the move to expiry, but the position is
        # closed the same session, so the horizon that matters is one day. The
        # sqrt(2/pi) turns a standard deviation into an expected absolute move,
        # which is what the realised forecast measures.
        implied = (spot[ENTRY_ROW] * iv * np.sqrt(HORIZON_DAYS / 365.0)
                   * np.sqrt(2.0 / np.pi))
        forecast = float(np.mean(np.abs(moves[index - LOOKBACK:index])))
        if implied <= 0:
            continue
        if require_edge and forecast < EDGE_REQUIRED * implied:
            continue

        premium, _ = straddle_series(session, ENTRY_ROW)
        entry = premium[ENTRY_ROW]
        if np.isnan(entry) or entry <= 0:
            continue
        floor_price = entry * (1 - COMBINED_STOP)
        target = entry + abs(forecast) * 0.5  # roughly one delta-neutral leg paying off
        outcome, exit_price = "TIME_EXIT", None
        for row in range(ENTRY_ROW + 1, min(TIME_EXIT, len(premium))):
            value = premium[row]
            if np.isnan(value):
                continue
            if value <= floor_price:
                outcome, exit_price = "STOP", floor_price
                break
            if value >= target:
                outcome, exit_price = "TARGET", value
                break
        if exit_price is None:
            tail = premium[min(TIME_EXIT, len(premium)) - 1]
            if np.isnan(tail):
                continue
            exit_price = tail
        risk = entry * COMBINED_STOP
        trades.append({
            "date": date,
            "entry": entry,
            "premium_pnl": exit_price - entry,
            "unit_risk": risk,
            "premium_r": (exit_price - entry) / risk,
            "outcome": outcome,
            "implied": implied,
            "forecast": forecast,
            "dte": days,
        })
    return trades


def summarise(trades):
    if not trades:
        return None
    r = np.array([t["premium_r"] for t in trades])
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {
        "n": len(r),
        "win": 100 * (r > 0).mean(),
        "totR": r.sum(),
        "avgR": r.mean(),
        "pf": gains / losses if losses else float("inf"),
        "meanPct": 100 * np.mean([t["premium_pnl"] / t["entry"] for t in trades]),
    }


def main():
    dates = C.session_dates()
    print("Strategy 4: ATM straddle, NIFTY\n")
    header = (f"{'variant':<34}{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}"
              f"{'PF':>7}{'mean %':>9}")
    print(header)
    print("-" * len(header))
    for name, edge in (("every session (baseline)", False),
                       (f"forecast >= {EDGE_REQUIRED:.2f}x implied", True)):
        result = summarise(run(dates, require_edge=edge))
        if not result:
            print(f"{name:<34}   no trades")
            continue
        print(f"{name:<34}{result['n']:>5}{result['win']:>7.1f}{result['totR']:>9.1f}"
              f"{result['avgR']:>8.3f}{result['pf']:>7.2f}{result['meanPct']:>9.1f}")

    everything = run(dates, require_edge=False)
    ratio = np.array([t["forecast"] / t["implied"] for t in everything])
    print(f"\nforecast / implied over {len(ratio)} sessions: "
          f"min {ratio.min():.2f}  med {np.median(ratio):.2f}  max {ratio.max():.2f}"
          f"   ({100*(ratio>=1).mean():.0f}% of sessions above 1.00)")
    print(f"\n{'cheapness cut':<34}{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}{'PF':>7}")
    for threshold in (0.8, 0.9, 1.0, 1.1, EDGE_REQUIRED):
        subset = [t for t in everything if t["forecast"] >= threshold * t["implied"]]
        result = summarise(subset)
        if not result:
            print(f"{'forecast >= %.2fx implied' % threshold:<34}   no trades")
            continue
        print(f"{'forecast >= %.2fx implied' % threshold:<34}{result['n']:>5}"
              f"{result['win']:>7.1f}{result['totR']:>9.1f}{result['avgR']:>8.3f}"
              f"{result['pf']:>7.2f}")

    if everything:
        by_dte = {}
        for trade in everything:
            by_dte.setdefault(trade["dte"], []).append(trade["premium_r"])
        print(f"\n{'DTE':>5}{'n':>6}{'win%':>8}{'avgR':>9}")
        for days in sorted(by_dte):
            values = np.array(by_dte[days])
            print(f"{days:>5}{len(values):>6}{100*(values>0).mean():>8.1f}"
                  f"{values.mean():>9.3f}")


if __name__ == "__main__":
    main()
