"""The 15-minute opening range breakout, tested as specified.

The rule as given: mark the high and low of the 09:15-09:30 candle; go long a
call when the index *closes* above that high while price is above its 9 EMA and
above VWAP, and the mirror for puts; buy ITM or ATM only, never cheap OTM; stop
at 15-20% of premium or when the index closes back inside the range; take 20-30%
on the premium or trail aggressively.

Three parts of that need a decision the rule does not make, so each becomes an
axis of the grid rather than a silent assumption:

  what "closes" means   a 1-, 5- or 15-minute bar. This is not a detail. On a
                        1-minute bar the range breaks most days and most breaks
                        are noise; on a 15-minute bar it breaks rarely and late.
  which chart the        the 9 EMA is quoted without a timeframe. It is taken on
  EMA lives on           the same bars as the trigger, which is what a trader
                         watching one chart would actually see.
  how many trades        a classic ORB is one trade a day. Taking the first
                         trigger only is the strict reading; allowing the
                         opposite side to trigger later is the loose one, and
                         both are run.

Everything else -- fills, slippage, sizing, charges, the adverse-first bar
ordering -- comes from simlib, so these numbers sit on the same footing as the
shipped strategy's Rs 40,341.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicators as I
import simlib as S
import vwap as V

RANGE_MINUTES = 15
LAST_ENTRY = 345          # no fresh breakout after 15:00; 20 minutes to work
PREMIUM_MIN, PREMIUM_MAX = 30.0, 1000.0


def setups(data, date, timeframe, use_filter, first_only=True):
    """Every breakout trigger in one session, oldest first.

    Returns dicts of side, entry minute, and the minute at which the index first
    closes back inside the range after entry -- which is the rule's second stop
    and is computed here because it depends on the index, not the premium.
    """
    spot = data["spot"]
    if len(spot) < RANGE_MINUTES + timeframe + 2:
        return []
    opening = spot[:RANGE_MINUTES]
    opening = opening[np.isfinite(opening)]
    if len(opening) < RANGE_MINUTES - 2:
        return []
    high, low = float(opening.max()), float(opening.min())

    closes, _, _ = I.resample(spot, timeframe)
    trend = I.ema(closes, 9)
    line = V.synthetic(date, spot)
    if line is None:
        return []

    # A bar spanning [b*tf, (b+1)*tf - 1] is only readable at its last minute,
    # and the first bar that is allowed to trigger is the first one starting
    # after the opening range has finished.
    first_bar = int(np.ceil(RANGE_MINUTES / timeframe))
    found = []
    for bar in range(first_bar, len(closes)):
        closed_at = (bar + 1) * timeframe - 1
        entry = closed_at + 1
        if entry > LAST_ENTRY or closed_at >= len(spot):
            break
        close = closes[bar]
        if not np.isfinite(close):
            continue
        if close > high:
            side = S.CALL
        elif close < low:
            side = S.PUT
        else:
            continue
        if use_filter:
            average, level = trend[bar], line[min(closed_at, len(line) - 1)]
            if not np.isfinite(average) or not np.isfinite(level):
                continue
            if side == S.CALL and not (close > average and close > level):
                continue
            if side == S.PUT and not (close < average and close < level):
                continue
        # The index closing back inside the range is the rule's other stop.
        abort = None
        for later in range(bar + 1, len(closes)):
            value = closes[later]
            if np.isfinite(value) and low <= value <= high:
                abort = min((later + 1) * timeframe - 1, len(spot) - 1)
                break
        found.append({"side": side, "minute": entry, "abort": abort,
                      "range": high - low})
        if first_only:
            break
        # A loose reading still should not re-arm on the same side immediately.
        if len(found) >= 2:
            break
    if not first_only and len(found) > 1:
        found = [found[0]] + [f for f in found[1:] if f["side"] != found[0]["side"]]
    return found


def run(loaded, *, timeframe, steps, use_filter=True, stop=0.15,
        target=None, trail_percent=None, trail_gap=None, first_only=True,
        use_abort=True):
    trades, unaffordable = [], 0
    for date, data in loaded.items():
        for setup in setups(data, date, timeframe, use_filter, first_only):
            minute = setup["minute"]
            if minute >= len(data["spot"]) or not np.isfinite(data["spot"][minute]):
                continue
            index = S.strike_index(data["strikes"], data["spot"][minute],
                                   setup["side"], steps)
            if index is None:
                continue
            leg = S.simulate(data, setup["side"], index, minute,
                             stop_percent=stop, target_percent=target,
                             trail_percent=trail_percent, trail_gap=trail_gap,
                             abort_at=setup["abort"] if use_abort else None,
                             premium_min=PREMIUM_MIN, premium_max=PREMIUM_MAX)
            if leg is None:
                continue
            if leg["entry"] * S.NIFTY_LOT_SIZE > S.STARTING_CAPITAL * S.MAX_CASH_FRACTION:
                unaffordable += 1
            trades.append({**leg, "date": date, "minute": minute,
                           "side": setup["side"]})
    return trades, unaffordable


def outcomes(trades):
    tally = {}
    for trade in trades:
        tally[trade["outcome"]] = tally.get(trade["outcome"], 0) + 1
    return " ".join(f"{key}:{value}" for key, value in sorted(tally.items()))


def main():
    print("Loading sessions...", flush=True)
    loaded = S.sessions()
    print(f"{len(loaded)} sessions\n")
    print("Shipped strategy for reference: 246 sessions, 68.8% win, "
          "Rs 40,341, DD Rs 8,876\n")

    print("=" * 96)
    print("1. WHAT DOES 'CLOSES ABOVE THE RANGE' MEAN? (ATM, 15% stop, close-inside abort, no target)")
    print("=" * 96)
    print(S.HEADER)
    best_timeframe, best_net = None, None
    for timeframe in (1, 5, 15):
        for use_filter in (False, True):
            trades, poor = run(loaded, timeframe=timeframe, steps=0,
                               use_filter=use_filter, stop=0.15)
            label = f"{timeframe:>2}-min close" + (" + 9EMA/VWAP" if use_filter else "")
            result = S.report(label, trades)
            if result and (best_net is None or result["net"] > best_net):
                best_timeframe, best_net = (timeframe, use_filter), result["net"]
            if trades:
                print(f"       {outcomes(trades)}"
                      + (f"   [{poor} too dear to buy a lot]" if poor else ""))
    print(f"\n  best trigger: {best_timeframe[0]}-min close"
          f"{' with the filter' if best_timeframe[1] else ' unfiltered'}")

    timeframe, use_filter = best_timeframe
    base = dict(timeframe=timeframe, use_filter=use_filter)

    print("\n" + "=" * 96)
    print("2. STRIKE: THE RULE SAYS ITM OR ATM, NEVER CHEAP OTM. IS THAT RIGHT HERE?")
    print("=" * 96)
    print(S.HEADER)
    for steps, name in ((-2, "2 OTM (rule says avoid)"), (-1, "1 OTM (rule says avoid)"),
                        (0, "ATM"), (1, "1 ITM"), (2, "2 ITM"), (3, "3 ITM")):
        trades, poor = run(loaded, steps=steps, stop=0.15, **base)
        result = S.report(name, trades, best_net)
        if poor and result:
            print(f"       {poor} of {len(trades)} cost more than the 40% cash cap allows")

    print("\n" + "=" * 96)
    print("3. THE STOP: 15% OR 20%, AND IS THE CLOSE-BACK-INSIDE ABORT WORTH IT?")
    print("=" * 96)
    print(S.HEADER)
    for stop in (0.15, 0.20, 0.25):
        for use_abort in (True, False):
            trades, _ = run(loaded, steps=0, stop=stop, use_abort=use_abort, **base)
            tag = "+ close-inside abort" if use_abort else "premium stop only"
            S.report(f"{int(stop * 100)}% stop {tag}", trades, best_net)

    print("\n" + "=" * 96)
    print("4. THE EXIT: 20-30% TARGET, OR TRAIL AGGRESSIVELY?")
    print("=" * 96)
    print(S.HEADER)
    exits = [("+20% target", dict(target=0.20)),
             ("+30% target", dict(target=0.30)),
             ("+50% target", dict(target=0.50)),
             ("trail 10% off high", dict(trail_percent=0.10)),
             ("trail 15% off high", dict(trail_percent=0.15)),
             ("trail 20% off high", dict(trail_percent=0.20)),
             ("trail 0.7R (house exit)", dict(trail_gap=0.7)),
             ("trail 1.0R", dict(trail_gap=1.0)),
             ("no exit, run to 15:20", {})]
    winner, winning_net, winning_trades = None, None, None
    for name, kwargs in exits:
        trades, _ = run(loaded, steps=0, stop=0.15, **base, **kwargs)
        result = S.report(name, trades, best_net)
        if result and (winning_net is None or result["net"] > winning_net):
            winner, winning_net, winning_trades = name, result["net"], trades
        if trades:
            print(f"       {outcomes(trades)}")

    print("\n" + "=" * 96)
    print("5. ONE TRADE A DAY, OR LET THE OTHER SIDE TRIGGER TOO?")
    print("=" * 96)
    print(S.HEADER)
    best_exit = dict(exits[[name for name, _ in exits].index(winner)][1])
    for first_only, name in ((True, "first trigger only"), (False, "allow the reverse too")):
        trades, _ = run(loaded, steps=0, stop=0.15, first_only=first_only,
                        **base, **best_exit)
        S.report(name, trades, best_net)

    print("\n" + "=" * 96)
    print(f"6. IS IT SKILL? control on the best variant: {winner}")
    print("=" * 96)
    check = S.control(winning_trades, loaded, stop_percent=0.15,
                      premium_min=PREMIUM_MIN, premium_max=PREMIUM_MAX,
                      **best_exit)
    if check:
        print(f"  real Rs {check['real']:>10,.0f}")
        print(f"  random median Rs {check['median']:>10,.0f}   "
              f"5th-95th Rs {check['p5']:,.0f} to Rs {check['p95']:,.0f}")
        print(f"  beats {check['beats']:.1f}% of 200 random draws on the same days")
        print(f"  -> {'real edge' if check['beats'] >= 95 else 'INSIDE THE NOISE'}")


if __name__ == "__main__":
    main()
