"""Where do expiry-day points actually come from, and can a buyer capture them?

Expiry-day moves look enormous in percentage terms because ATM gamma explodes
as time to expiry goes to zero -- but so does theta. The question is not
whether the premium can multiply, it is whether it multiplies before it decays.

So the test is a first-touch race: buy the ATM (or OTM) option at minute t and
see which happens first, a target multiple of the entry premium or a stop
multiple. The strike is fixed at entry -- re-picking ATM every minute is not a
position anyone can hold.
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C

ENTRY_MINUTES = range(30, 360, 15)  # 09:45 .. 15:00 IST
OFFSETS = (0, 1, 2, 3)              # strikes out of the money
RACES = ((1.5, 0.6), (2.0, 0.6), (2.0, 0.5), (3.0, 0.5), (1.3, 0.7))
HORIZON = 90


def _ffill(values):
    valid = ~np.isnan(values)
    if not valid.any():
        return values
    index = np.where(valid, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    filled = values[index]
    filled[: np.argmax(valid)] = values[valid][0]
    return filled


def race(path, target_multiple, stop_multiple):
    """Which level the premium touches first. Returns 1 win, 0 loss, None open."""
    entry = path[0]
    if not entry or entry <= 0 or np.isnan(entry):
        return None
    target = entry * target_multiple
    stop = entry * stop_multiple
    for price in path[1:]:
        if np.isnan(price):
            continue
        if price <= stop:
            return 0
        if price >= target:
            return 1
    return None


def session_rows(date, expiry):
    session = C.load(date)
    spot = _ffill(session["spot"].astype(np.float64))
    strikes = session["strikes"].astype(np.float64)
    close = session["c"].astype(np.float64)
    if len(spot) < 200 or np.isnan(spot).any():
        return []
    rows = []
    for minute in ENTRY_MINUTES:
        if minute + HORIZON >= len(spot):
            break
        atm = int(np.abs(strikes - spot[minute]).argmin())
        for offset in OFFSETS:
            for side, direction in ((C.CALL, +1), (C.PUT, -1)):
                index = atm + direction * offset
                if not 0 <= index < len(strikes):
                    continue
                # Strike fixed at entry: this is a position, not a rolling index.
                path = close[side, index, minute : minute + HORIZON + 1]
                if np.isnan(path[0]) or path[0] < 5:
                    continue
                best = np.nanmax(path[1:]) / path[0]
                worst = np.nanmin(path[1:]) / path[0]
                rows.append(
                    {
                        "expiry": expiry,
                        "minute": minute,
                        "offset": offset,
                        "side": side,
                        "entry": path[0],
                        "mfe": best,
                        "mae": worst,
                        "races": {
                            key: race(path, *key) for key in RACES
                        },
                    }
                )
    return rows


def report(rows, label, key):
    buckets = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    print(f"\n  by {label}")
    header = (
        f"    {'bucket':<10}{'n':>6}{'medMFE':>8}{'medMAE':>8}"
        + "".join(f"{f'{t}x/{s}x':>10}" for t, s in RACES)
    )
    print(header)
    for name in sorted(buckets):
        group = buckets[name]
        line = (
            f"    {str(name):<10}{len(group):>6}"
            f"{np.median([row['mfe'] for row in group]):>8.2f}"
            f"{np.median([row['mae'] for row in group]):>8.2f}"
        )
        for key_race in RACES:
            decided = [row["races"][key_race] for row in group if row["races"][key_race] is not None]
            line += f"{100*np.mean(decided):>9.1f}%" if decided else f"{'-':>10}"
        print(line)


def main():
    dates = C.session_dates()
    expiries = C.expiry_dates(dates)
    print(f"{len(dates)} sessions, {len(expiries)} expiry sessions\n")

    expiry_rows, normal_rows = [], []
    spot_ranges = {True: [], False: []}
    for date in dates:
        is_expiry = date in expiries
        rows = session_rows(date, is_expiry)
        (expiry_rows if is_expiry else normal_rows).extend(rows)
        session = C.load(date)
        spot = _ffill(session["spot"].astype(np.float64))
        if len(spot) > 100 and not np.isnan(spot).any():
            spot_ranges[is_expiry].append(spot.max() - spot.min())

    print(f"median day range  expiry {np.median(spot_ranges[True]):.0f} pts   "
          f"normal {np.median(spot_ranges[False]):.0f} pts")
    print("(win% below = first touch of target before stop, strike fixed at entry, "
          f"{HORIZON}-minute horizon)")

    for label, rows in (("EXPIRY", expiry_rows), ("NORMAL", normal_rows)):
        print(f"\n=== {label} === {len(rows)} entry samples")
        report(rows, "strikes OTM", lambda row: row["offset"])
        report(rows, "entry time", lambda row: f"{9+(555+row['minute'])//60-9:02d}:{(555+row['minute'])%60:02d}")


if __name__ == "__main__":
    main()
