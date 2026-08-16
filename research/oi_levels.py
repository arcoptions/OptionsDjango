"""Do OI walls act as support and resistance?

The claim under test: when spot approaches the strike carrying the largest put
open interest it should bounce (buy CE), and when it approaches the largest
call OI strike it should stall (buy PE).

Every number is compared against the unconditional forward move measured at
the same minutes of the same sessions. A signal that "works" but matches the
base rate is not a signal.
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C

HORIZONS = (15, 30, 60)
TOLERANCES = (10, 20, 30, 50)
COOLDOWN = 30  # minutes; stops one approach counting as 40 correlated events
WARMUP = 30


def walls(session, near_percent=2.5):
    """Causal per-minute max-OI strikes on each side, restricted to near strikes."""
    strikes = session["strikes"].astype(np.float64)
    spot = session["spot"].astype(np.float64)
    open_interest = np.nan_to_num(session["oi"].astype(np.float64))
    count = len(spot)
    call_wall = np.full(count, np.nan)
    put_wall = np.full(count, np.nan)
    call_share = np.full(count, np.nan)
    put_share = np.full(count, np.nan)
    for index in range(count):
        if np.isnan(spot[index]):
            continue
        near = np.abs(strikes - spot[index]) <= spot[index] * near_percent / 100
        if not near.any():
            continue
        calls = np.where(near, open_interest[C.CALL, :, index], 0)
        puts = np.where(near, open_interest[C.PUT, :, index], 0)
        if calls.sum() > 0:
            call_wall[index] = strikes[calls.argmax()]
            call_share[index] = calls.max() / calls.sum()
        if puts.sum() > 0:
            put_wall[index] = strikes[puts.argmax()]
            put_share[index] = puts.max() / puts.sum()
    return call_wall, put_wall, call_share, put_share


def forward(spot, index, horizon):
    window = spot[index + 1 : index + 1 + horizon]
    if len(window) < horizon or np.isnan(window).any():
        return None
    return window.max() - spot[index], window.min() - spot[index], window[-1] - spot[index]


def _ffill(values):
    valid = ~np.isnan(values)
    if not valid.any():
        return values
    index = np.where(valid, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    filled = values[index]
    filled[: np.argmax(valid)] = values[valid][0]
    return filled


def collect(dates):
    events = defaultdict(list)
    baseline = defaultdict(list)
    distances = []
    for date in dates:
        session = C.load(date)
        spot = _ffill(session["spot"].astype(np.float64))
        if np.isnan(spot).any() or len(spot) < 200:
            continue
        call_wall, put_wall, call_share, put_share = walls(session)
        distances.append(
            (np.nanmean(np.abs(put_wall - spot)), np.nanmean(np.abs(call_wall - spot)))
        )
        last_fired = {}
        for index in range(WARMUP, len(spot)):
            for horizon in HORIZONS:
                moved = forward(spot, index, horizon)
                if moved:
                    baseline[horizon].append(moved)
            approaching_down = spot[index] < spot[index - 15] if index >= 15 else False
            approaching_up = spot[index] > spot[index - 15] if index >= 15 else False
            for tolerance in TOLERANCES:
                # Support: spot falling onto the biggest put-OI strike -> buy CE
                if (
                    approaching_down
                    and not np.isnan(put_wall[index])
                    and abs(spot[index] - put_wall[index]) <= tolerance
                    and index - last_fired.get(("SUP", tolerance), -999) >= COOLDOWN
                ):
                    last_fired[("SUP", tolerance)] = index
                    for horizon in HORIZONS:
                        moved = forward(spot, index, horizon)
                        if moved:
                            events[("SUPPORT_CE", tolerance, horizon)].append(
                                (*moved, put_share[index])
                            )
                # Resistance: spot rising into the biggest call-OI strike -> buy PE
                if (
                    approaching_up
                    and not np.isnan(call_wall[index])
                    and abs(spot[index] - call_wall[index]) <= tolerance
                    and index - last_fired.get(("RES", tolerance), -999) >= COOLDOWN
                ):
                    last_fired[("RES", tolerance)] = index
                    for horizon in HORIZONS:
                        moved = forward(spot, index, horizon)
                        if moved:
                            events[("RESISTANCE_PE", tolerance, horizon)].append(
                                (*moved, call_share[index])
                            )
    return events, baseline, distances


def summarise(name, rows, sign):
    """sign +1 = we want spot up (bought CE), -1 = we want spot down (bought PE)."""
    highs = np.array([row[0] for row in rows])
    lows = np.array([row[1] for row in rows])
    ends = np.array([row[2] for row in rows])
    favourable = highs if sign > 0 else -lows
    adverse = -lows if sign > 0 else highs
    net = ends * sign
    return (
        f"{name:<26}{len(rows):>6}{net.mean():>9.1f}{np.median(net):>9.1f}"
        f"{100*(net>0).mean():>8.1f}%{favourable.mean():>10.1f}{adverse.mean():>9.1f}"
        f"{favourable.mean()/adverse.mean():>8.2f}"
    )


def main():
    dates = C.session_dates()
    print(f"{len(dates)} NIFTY sessions\n")
    events, baseline, distances = collect(dates)
    supports = np.array([row[0] for row in distances])
    calls = np.array([row[1] for row in distances])
    print(f"mean distance spot->put wall {np.nanmean(supports):.0f} pts, "
          f"spot->call wall {np.nanmean(calls):.0f} pts\n")

    header = (
        f"{'signal':<26}{'n':>6}{'meanPts':>9}{'medPts':>9}{'win%':>9}"
        f"{'MFE':>10}{'MAE':>9}{'MFE/MAE':>8}"
    )
    print(header)
    print("-" * len(header))
    for horizon in HORIZONS:
        rows = [(row[0], row[1], row[2], 0) for row in baseline[horizon]]
        print(summarise(f"baseline UP +{horizon}m", rows, +1))
        print(summarise(f"baseline DOWN +{horizon}m", rows, -1))
    print("-" * len(header))
    for (name, tolerance, horizon), rows in sorted(events.items()):
        if len(rows) < 30:
            continue
        sign = +1 if name.endswith("CE") else -1
        print(summarise(f"{name} {tolerance}pt +{horizon}m", rows, sign))

    print("\nfiltered on OI concentration (wall >= 25% of near-strike OI)")
    print("-" * len(header))
    for (name, tolerance, horizon), rows in sorted(events.items()):
        strong = [row for row in rows if row[3] >= 0.25]
        if len(strong) < 30:
            continue
        sign = +1 if name.endswith("CE") else -1
        print(summarise(f"{name} {tolerance}pt +{horizon}m", strong, sign))


if __name__ == "__main__":
    main()
