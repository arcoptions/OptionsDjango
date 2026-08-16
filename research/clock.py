"""What the clock alone gives an option buyer, and what expiry day alone does.

Two questions that ought to be answered by the physics of the tape rather than by
any one trigger, because a trigger that fires four times after 13:00 in 246
sessions cannot settle either of them:

  is the late session worth trading?   The claim is that premium is cheaper after
                                       14:30, so the same rupee buys more
                                       leverage on the same spike.
  does expiry day help or hurt?        Same entry, same exit, expiry versus not.

So: buy the ATM at a given minute, run the *shipped* exit on it -- 10% stop, trail
0.7R once 7% up, hard out at 15:20 -- and read the result by clock time. Two
directional rules are run side by side:

  both sides   Buy the call and the put at every sampled minute. Direction cancels,
               so what is left is the price of holding an option at that hour: the
               balance of gamma against theta, with no skill involved.
  momentum     Take only the side the last five minutes of spot already favours,
               with the shipped 0.15% move threshold. This is the closest thing to
               "catch the 3 PM spike" that does not require knowing the future.

Entry is the option's own close at that minute with no slippage uplift, because the
question here is what the hour is worth, not what the fill costs.
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C

OPEN_INDEX = 0          # index 0 is 09:15
TIME_EXIT = 365         # 15:20
SAMPLE = range(15, 351, 5)   # 09:30 .. 15:05 every five minutes
STOP_PCT = 0.10
TRAIL_R = 0.7
MOVE_PCT = 0.15
TREND_MINUTES = 5
BUCKETS = ((15, 105, "09:30-11:00"), (105, 225, "11:00-13:00"),
           (225, 315, "13:00-14:30"), (315, 351, "14:30-15:05"))


def ffill(values):
    valid = ~np.isnan(values)
    if not valid.any():
        return values
    index = np.where(valid, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    filled = values[index]
    filled[: int(np.argmax(valid))] = values[valid][0]
    return filled


def simulate(highs, lows, entry):
    """The shipped exit, bar by bar. Returns (points, outcome, armed)."""
    stop = entry * (1 - STOP_PCT)
    unit_risk = entry - stop
    arm = entry + unit_risk        # +10% of premium is 1R; the trail arms at 0.7R
    gap = TRAIL_R * unit_risk
    trigger = entry + gap          # running high must clear +7% before the trail moves
    high_water = entry
    armed = False
    for index in range(len(highs)):
        low, high = lows[index], highs[index]
        if low != low or high != high:          # NaN
            continue
        if low <= stop:
            return stop - entry, "STOP" if not armed else "TRAIL_EXIT", armed
        if high > high_water:
            high_water = high
        if high_water >= trigger:
            armed = True
            stop = max(stop, high_water - gap)
    return 0.0, "TIME_EXIT", armed


def bucket(minute):
    for low, high, label in BUCKETS:
        if low <= minute < high:
            return label
    return None


def collect():
    dates = C.session_dates()
    expiries = C.expiry_dates(dates)
    rows = []
    for date in dates:
        session = C.load(date)
        spot = ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        strikes = session["strikes"].astype(np.float64)
        close = session["c"].astype(np.float64)
        high = session["h"].astype(np.float64)
        low = session["l"].astype(np.float64)
        limit = min(TIME_EXIT, close.shape[2] - 1)
        for minute in SAMPLE:
            if minute >= limit:
                break
            k = int(np.abs(strikes - spot[minute]).argmin())
            prior = spot[max(0, minute - TREND_MINUTES)]
            move = 100 * (spot[minute] - prior) / prior
            for side, sign in ((C.CALL, +1), (C.PUT, -1)):
                entry = close[side, k, minute]
                if not np.isfinite(entry) or entry < 5:
                    continue
                path_h = high[side, k, minute + 1:limit + 1].tolist()
                path_l = low[side, k, minute + 1:limit + 1].tolist()
                if not path_h:
                    continue
                points, outcome, armed = simulate(path_h, path_l, entry)
                rows.append({
                    "date": date, "expiry": date in expiries, "minute": minute,
                    "side": side, "entry": entry, "points": points,
                    "r": points / (entry * STOP_PCT), "outcome": outcome,
                    "armed": armed,
                    # the momentum rule: does the last five minutes favour this side?
                    "momentum": abs(move) >= MOVE_PCT and np.sign(move) == sign,
                    "abs_move": abs(move),
                })
    return rows, len(dates), len(expiries)


def table(rows, key, label, width=14):
    groups = defaultdict(list)
    for entry in rows:
        name = key(entry)
        if name is not None:
            groups[name].append(entry)
    print(f"  {label:<{width}}{'n':>7}{'med prem':>10}{'win%':>8}"
          f"{'reached +7%':>13}{'pts/trade':>11}{'R/trade':>9}{'timed out':>11}")
    for name in sorted(groups):
        group = groups[name]
        wins = sum(1 for e in group if e["points"] > 0)
        armed = sum(1 for e in group if e["armed"])
        timed = sum(1 for e in group if e["outcome"] == "TIME_EXIT")
        print(f"  {str(name):<{width}}{len(group):>7}"
              f"{np.median([e['entry'] for e in group]):>10.0f}"
              f"{100 * wins / len(group):>8.1f}"
              f"{100 * armed / len(group):>12.1f}%"
              f"{np.mean([e['points'] for e in group]):>11.2f}"
              f"{np.mean([e['r'] for e in group]):>9.2f}"
              f"{100 * timed / len(group):>10.1f}%")


def main():
    rows, sessions, expiry_count = collect()
    print(f"{sessions} sessions, {expiry_count} of them expiry, "
          f"{len(rows)} ATM entries sampled every 5 minutes\n")

    print("=" * 104)
    print("1. THE PRICE OF THE HOUR: BUY BOTH SIDES, SO DIRECTION CANCELS")
    print("=" * 104)
    print("  Every sampled minute, buy the call and the put. What survives is"
          " gamma against theta.\n")
    table(rows, lambda e: bucket(e["minute"]), "entry window")

    print("\n  Normal sessions only:\n")
    table([e for e in rows if not e["expiry"]],
          lambda e: bucket(e["minute"]), "entry window")
    print("\n  Expiry sessions only:\n")
    table([e for e in rows if e["expiry"]],
          lambda e: bucket(e["minute"]), "entry window")

    print("\n" + "=" * 104)
    print("2. TAKING ONLY THE SIDE MOMENTUM FAVOURS -- 'CATCH THE SPIKE'")
    print("=" * 104)
    print(f"  Spot has moved at least {MOVE_PCT}% in the last"
          f" {TREND_MINUTES} minutes, and we buy that direction.\n")
    momentum = [e for e in rows if e["momentum"]]
    table(momentum, lambda e: bucket(e["minute"]), "entry window")

    print("\n  Normal sessions only:\n")
    table([e for e in momentum if not e["expiry"]],
          lambda e: bucket(e["minute"]), "entry window")
    print("\n  Expiry sessions only:\n")
    table([e for e in momentum if e["expiry"]],
          lambda e: bucket(e["minute"]), "entry window")

    print("\n" + "=" * 104)
    print("3. THE Rs 100 FLOOR AGAINST THE CLOCK")
    print("=" * 104)
    print("  If late premium really is cheaper, the floor removes more of the"
          " late session than the early one.\n")
    print(f"  {'entry window':<14}{'n':>7}{'med prem':>10}{'below Rs 100':>14}"
          f"{'pts if <100':>13}{'pts if >=100':>14}")
    groups = defaultdict(list)
    for entry in momentum:
        name = bucket(entry["minute"])
        if name:
            groups[name].append(entry)
    for name in sorted(groups):
        group = groups[name]
        cheap = [e for e in group if e["entry"] < 100]
        rich = [e for e in group if e["entry"] >= 100]
        print(f"  {name:<14}{len(group):>7}"
              f"{np.median([e['entry'] for e in group]):>10.0f}"
              f"{100 * len(cheap) / len(group):>13.0f}%"
              f"{(np.mean([e['points'] for e in cheap]) if cheap else float('nan')):>13.2f}"
              f"{(np.mean([e['points'] for e in rich]) if rich else float('nan')):>14.2f}")

    print("\n" + "=" * 104)
    print("4. IS THERE ACTUALLY MORE MOVEMENT LATE?")
    print("=" * 104)
    print("  Absolute 5-minute spot move at the sampled minute, and how often it"
          f" clears {MOVE_PCT}%.\n")
    print(f"  {'entry window':<14}{'samples':>9}{'med |move| %':>14}"
          f"{'90th pct':>10}{'clears 0.15%':>14}")
    groups = defaultdict(list)
    for entry in rows:
        if entry["side"] != C.CALL:
            continue                      # one row per minute, not two
        name = bucket(entry["minute"])
        if name:
            groups[name].append(entry["abs_move"])
    for name in sorted(groups):
        moves = np.array(groups[name])
        print(f"  {name:<14}{len(moves):>9}{np.median(moves):>14.3f}"
              f"{np.percentile(moves, 90):>10.3f}"
              f"{100 * np.mean(moves >= MOVE_PCT):>13.1f}%")


if __name__ == "__main__":
    main()
