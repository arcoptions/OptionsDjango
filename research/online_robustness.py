"""Does Strategy 1's edge hold up, or does it live in one quarter and one trade?

The source report's own gate says results must not depend on a single quarter or
a handful of outliers, and must survive out of sample. Twenty-five trades cannot
satisfy that gate, but it can be shown exactly how badly it fails: split the
sample by time, strip the best trades, and re-run on a second index.

SENSEX is the only genuine out-of-sample market available. Its cache starts
2026-02-16, so after the EMA50 burn-in there are far fewer usable sessions than
NIFTY -- the run is reported for completeness, not as validation.
"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import online_s1_breakout as S
import regime as R

DELTA = 0.55
SENSEX_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cache", "SENSEX")


def quarter(date):
    day = dt.date.fromisoformat(date)
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def show(label, trades):
    result = S.summarise(trades)
    if not result:
        print(f"{label:<26}   no trades")
        return
    print(f"{label:<26}{result['n']:>5}{result['win']:>7.1f}{result['totR']:>9.1f}"
          f"{result['avgR']:>8.3f}{result['pf']:>7.2f}")


def sensex_calendar(dates):
    """BSE weeklies settle on Thursday over the whole SENSEX cache window."""
    table = {}
    observed = set()
    by_week = {}
    for text in dates:
        day = dt.date.fromisoformat(text)
        by_week.setdefault(day.isocalendar()[:2], []).append(day)
    for days in by_week.values():
        monday = days[0] - dt.timedelta(days=days[0].weekday())
        scheduled = monday + dt.timedelta(days=3)
        eligible = [day for day in days if day <= scheduled]
        if eligible:
            observed.add(max(eligible).isoformat())
    for text in dates:
        day = dt.date.fromisoformat(text)
        ahead = (3 - day.weekday()) % 7
        table[text] = 0 if text in observed else ahead
    return table


def main():
    header = f"{'slice':<26}{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}{'PF':>7}"

    trades = S.run(DELTA)
    print(f"Strategy 1 stability, NIFTY, delta {DELTA}\n")
    print(header)
    print("-" * len(header))
    show("all trades", trades)

    ordered = sorted(trades, key=lambda t: t["date"])
    half = len(ordered) // 2
    show("first half by date", ordered[:half])
    show("second half by date", ordered[half:])

    print()
    for name in sorted({quarter(t["date"]) for t in ordered}):
        show(f"  {name}", [t for t in ordered if quarter(t["date"]) == name])

    print()
    by_r = sorted(trades, key=lambda t: t["premium_r"], reverse=True)
    for drop in (1, 2, 3):
        show(f"drop best {drop} trade(s)", by_r[drop:])
    show("drop worst 1 trade", by_r[:-1])

    print("\ndirection split")
    show("  long calls", [t for t in trades if t["bullish"]])
    show("  long puts", [t for t in trades if not t["bullish"]])

    print("\nout of sample: SENSEX")
    if not os.path.isdir(SENSEX_CACHE):
        print("  no SENSEX cache")
        return
    original_cache, original_calendar = C.CACHE, R.expiry_calendar
    C.CACHE = SENSEX_CACHE
    R.expiry_calendar = sensex_calendar
    try:
        dates = C.session_dates()
        table = R.regime(dates)
        usable = sum(1 for d in dates
                     if table.get(d) and table[d]["bars_available"] >= 50)
        print(f"  {len(dates)} sessions, {usable} past the EMA50 burn-in\n")
        print(header)
        print("-" * len(header))
        show("SENSEX as specified", S.run(DELTA, dates))
        original_adx = S.MIN_ADX
        S.MIN_ADX = 15.0
        try:
            show("SENSEX, ADX >= 15", S.run(DELTA, dates))
        finally:
            S.MIN_ADX = original_adx
    finally:
        C.CACHE = original_cache
        R.expiry_calendar = original_calendar


if __name__ == "__main__":
    main()
