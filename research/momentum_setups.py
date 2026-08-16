"""Six momentum and scalping setups, run through one execution model.

VWAP pullback, Supertrend+RSI, EMA scalping, RSI+MACD, previous-day
high/low breaks, and last-hour momentum. Each is written as a pure signal
generator -- given a session it returns (side, minute) pairs and nothing else --
so the comparison between them is a comparison of *timing* and nothing else. The
fill, the stop, the trail, the sizing and the charges are identical across all
six and identical to the shipped strategy.

Every one of them reads only closed bars. `last_closed_bar` semantics are
enforced by construction: a signal found on bar b is entered at the first minute
of bar b+1, never inside the bar that produced it.

Signals are capped per session. Left uncapped, EMA scalping alone fires a dozen
times a day and the result stops being a strategy and becomes a measurement of
brokerage.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicators as I
import simlib as S
import vwap as V

LAST_ENTRY = 345
MAX_PER_DAY = 2
PREMIUM_MIN, PREMIUM_MAX = 30.0, 1000.0


def _bars(spot, timeframe):
    return I.resample(spot, timeframe)


def _entry(bar, timeframe):
    """First minute after bar `bar` has closed."""
    return (bar + 1) * timeframe


def vwap_pullback(data, date, timeframe=5, band=0.0015):
    """An established trend, a pull back to VWAP, then a bar that confirms.

    "Established" is price on one side of both VWAP and the 20 EMA. The pullback
    is a bar whose low reaches within `band` of VWAP; the confirmation is the
    next bar closing back away from it. Both are needed -- buying the touch
    itself is buying into whatever is causing the pullback.
    """
    spot = data["spot"]
    closes, highs, lows = _bars(spot, timeframe)
    trend = I.ema(closes, 20)
    line = V.synthetic(date, spot)
    if line is None:
        return []
    found = []
    for bar in range(20, len(closes) - 1):
        closed_at = min((bar + 1) * timeframe - 1, len(line) - 1)
        level = line[closed_at]
        previous = min(bar * timeframe - 1, len(line) - 1)
        if not np.isfinite(level) or not np.isfinite(trend[bar]):
            continue
        prior, close = closes[bar - 1], closes[bar]
        if not np.isfinite(prior) or not np.isfinite(close):
            continue
        reach = level * band
        # Up: trending above VWAP, the previous bar dipped to it, this bar
        # closed back above it.
        if (prior > trend[bar - 1] and lows[bar - 1] <= line[previous] + reach
                and close > level and close > trend[bar] and close > prior):
            found.append((S.CALL, _entry(bar, timeframe)))
        elif (prior < trend[bar - 1] and highs[bar - 1] >= line[previous] - reach
              and close < level and close < trend[bar] and close < prior):
            found.append((S.PUT, _entry(bar, timeframe)))
    return found


def supertrend_rsi(data, date, timeframe=5, within=3, floor=60.0):
    """Supertrend flips, then RSI confirms within a few bars.

    The flip alone is a lagging signal that fires on every chop; requiring RSI to
    reach 60 within `within` bars is what the rule adds, and it is the whole
    point of the combination.
    """
    spot = data["spot"]
    closes, highs, lows = _bars(spot, timeframe)
    _, direction = I.supertrend(highs, lows, closes, 10, 3.0)
    strength = I.rsi(closes, 14)
    found = []
    for bar in range(1, len(closes)):
        if not np.isfinite(direction[bar]) or not np.isfinite(direction[bar - 1]):
            continue
        if direction[bar] == direction[bar - 1]:
            continue
        wanted = S.CALL if direction[bar] > 0 else S.PUT
        for step in range(bar, min(bar + within + 1, len(closes))):
            value = strength[step]
            if not np.isfinite(value):
                continue
            if wanted == S.CALL and value > floor:
                found.append((S.CALL, _entry(step, timeframe)))
                break
            if wanted == S.PUT and value < 100 - floor:
                found.append((S.PUT, _entry(step, timeframe)))
                break
    return found


def ema_scalp(data, date, timeframe=5, span=9):
    """Price closing back across a fast EMA on the 5-minute chart."""
    closes, _, _ = _bars(data["spot"], timeframe)
    average = I.ema(closes, span)
    found = []
    for bar in range(1, len(closes)):
        now, before = closes[bar], closes[bar - 1]
        line, prior = average[bar], average[bar - 1]
        if not (np.isfinite(now) and np.isfinite(before)
                and np.isfinite(line) and np.isfinite(prior)):
            continue
        if before <= prior and now > line:
            found.append((S.CALL, _entry(bar, timeframe)))
        elif before >= prior and now < line:
            found.append((S.PUT, _entry(bar, timeframe)))
    return found


def rsi_macd(data, date, timeframe=5, high=70.0, low=30.0):
    """RSI at an extreme with a MACD crossover in the same direction.

    Worth noting what this rule actually says: buy calls when RSI is *above* 70.
    That is momentum continuation, not mean reversion, and it agrees with the
    earlier finding here that a high RSI on a trend follower is confirmation
    rather than a warning.
    """
    closes, _, _ = _bars(data["spot"], timeframe)
    strength = I.rsi(closes, 14)
    line, trigger, _ = I.macd(closes)
    found = []
    for bar in range(1, len(closes)):
        if not (np.isfinite(line[bar]) and np.isfinite(trigger[bar])
                and np.isfinite(line[bar - 1]) and np.isfinite(trigger[bar - 1])
                and np.isfinite(strength[bar])):
            continue
        up = line[bar - 1] <= trigger[bar - 1] and line[bar] > trigger[bar]
        down = line[bar - 1] >= trigger[bar - 1] and line[bar] < trigger[bar]
        if up and strength[bar] > high:
            found.append((S.CALL, _entry(bar, timeframe)))
        elif down and strength[bar] < low:
            found.append((S.PUT, _entry(bar, timeframe)))
    return found


def prev_day_break(data, date, timeframe=5, previous=None):
    """A close beyond yesterday's high or low."""
    if previous is None:
        return []
    old = previous["spot"]
    old = old[np.isfinite(old)]
    if len(old) < 100:
        return []
    high, low = float(old.max()), float(old.min())
    closes, _, _ = _bars(data["spot"], timeframe)
    found = []
    for bar in range(1, len(closes)):
        now, before = closes[bar], closes[bar - 1]
        if not np.isfinite(now) or not np.isfinite(before):
            continue
        if before <= high < now:
            found.append((S.CALL, _entry(bar, timeframe)))
        elif before >= low > now:
            found.append((S.PUT, _entry(bar, timeframe)))
    return found


def last_hour(data, date, timeframe=5, from_minute=315):
    """Momentum in the final hour, taken in the direction the day is already going.

    Direction comes from VWAP and from the day's own drift, which is the closest
    honest reading of "the 3 PM move" -- there is no separate trigger in the
    rule, so the trigger is the clock.
    """
    spot = data["spot"]
    line = V.synthetic(date, spot)
    if line is None:
        return []
    closes, _, _ = _bars(spot, timeframe)
    bar = I.last_closed_bar(from_minute, timeframe)
    if bar < 1 or bar >= len(closes):
        return []
    closed_at = min((bar + 1) * timeframe - 1, len(line) - 1)
    close, level = closes[bar], line[closed_at]
    if not np.isfinite(close) or not np.isfinite(level):
        return []
    # Require the last few bars to agree, or this is just a coin flip on VWAP.
    recent = closes[max(0, bar - 3):bar + 1]
    recent = recent[np.isfinite(recent)]
    if len(recent) < 2:
        return []
    drift = recent[-1] - recent[0]
    if close > level and drift > 0:
        return [(S.CALL, _entry(bar, timeframe))]
    if close < level and drift < 0:
        return [(S.PUT, _entry(bar, timeframe))]
    return []


def run(loaded, generator, *, steps=0, stop=0.10, trail_gap=0.7,
        target=None, trail_percent=None, max_per_day=MAX_PER_DAY, **params):
    trades = []
    order = sorted(loaded)
    for position, date in enumerate(order):
        data = loaded[date]
        extra = dict(params)
        if generator is prev_day_break:
            extra["previous"] = loaded[order[position - 1]] if position else None
        taken, seen = 0, set()
        for side, minute in sorted(generator(data, date, **extra),
                                   key=lambda pair: pair[1]):
            if taken >= max_per_day or minute > LAST_ENTRY:
                continue
            if minute >= len(data["spot"]) or not np.isfinite(data["spot"][minute]):
                continue
            if minute in seen:
                continue
            seen.add(minute)
            index = S.strike_index(data["strikes"], data["spot"][minute], side, steps)
            if index is None:
                continue
            leg = S.simulate(data, side, index, minute, stop_percent=stop,
                             trail_gap=trail_gap, target_percent=target,
                             trail_percent=trail_percent,
                             premium_min=PREMIUM_MIN, premium_max=PREMIUM_MAX)
            if leg is None:
                continue
            trades.append({**leg, "date": date, "minute": minute, "side": side})
            taken += 1
    return trades


SETUPS = [
    ("VWAP pullback, 5-min", vwap_pullback, {"timeframe": 5}),
    ("VWAP pullback, 15-min", vwap_pullback, {"timeframe": 15}),
    ("Supertrend flip + RSI>60, 5-min", supertrend_rsi, {"timeframe": 5}),
    ("Supertrend flip + RSI>60, 15-min", supertrend_rsi, {"timeframe": 15}),
    ("Supertrend flip alone (no RSI)", supertrend_rsi, {"timeframe": 5, "floor": 50.0}),
    ("EMA-9 scalp, 5-min", ema_scalp, {"timeframe": 5, "span": 9}),
    ("EMA-5 scalp, 5-min", ema_scalp, {"timeframe": 5, "span": 5}),
    ("EMA-9 scalp, 15-min", ema_scalp, {"timeframe": 15, "span": 9}),
    ("RSI 70/30 + MACD cross, 5-min", rsi_macd, {"timeframe": 5}),
    ("RSI 60/40 + MACD cross, 5-min", rsi_macd, {"timeframe": 5, "high": 60.0, "low": 40.0}),
    ("Previous day high/low break, 5-min", prev_day_break, {"timeframe": 5}),
    ("Previous day high/low break, 15-min", prev_day_break, {"timeframe": 15}),
    ("Last hour momentum, from 14:30", last_hour, {"from_minute": 315}),
    ("Last hour momentum, from 15:00", last_hour, {"from_minute": 345}),
]


def main():
    print("Loading sessions...", flush=True)
    loaded = S.sessions()
    print(f"{len(loaded)} sessions")
    print("House exit throughout: ATM, 10% stop, 0.7R trail, flat 15:20.")
    print("Shipped strategy on the same footing: 68.8% win, Rs 40,341, DD Rs 8,876\n")

    print("=" * 96)
    print("EVERY SETUP ON THE HOUSE EXIT")
    print("=" * 96)
    print(S.HEADER)
    results = []
    for name, generator, params in SETUPS:
        trades = run(loaded, generator, **params)
        result = S.report(name, trades, 40341)
        if result:
            results.append((result["net"], name, generator, params, trades))

    results.sort(reverse=True, key=lambda row: row[0])
    if not results:
        print("\n  nothing produced a trade")
        return

    print("\n" + "=" * 96)
    print("THE THREE BEST, PUT THROUGH STRIKE AND EXIT VARIATION")
    print("=" * 96)
    for net, name, generator, params, _ in results[:3]:
        print(f"\n  {name}   (Rs {net:,.0f} at ATM on the house exit)")
        print(S.HEADER)
        for steps, tag in ((1, "1 ITM"), (0, "ATM"), (-1, "1 OTM")):
            trades = run(loaded, generator, steps=steps, **params)
            S.report(f"  strike {tag}", trades, net)
        for label, kwargs in (("trail 1.0R", dict(trail_gap=1.0)),
                              ("trail 15% off high", dict(trail_gap=None, trail_percent=0.15)),
                              ("+25% target", dict(trail_gap=None, target=0.25)),
                              ("15% stop", dict(stop=0.15))):
            trades = run(loaded, generator, **params, **kwargs)
            S.report(f"  exit {label}", trades, net)

    print("\n" + "=" * 96)
    print("IS THE BEST ONE SKILL? 200 random draws on the same days")
    print("=" * 96)
    net, name, generator, params, trades = results[0]
    check = S.control(trades, loaded, stop_percent=0.10, trail_gap=0.7,
                      premium_min=PREMIUM_MIN, premium_max=PREMIUM_MAX)
    if check:
        print(f"  {name}")
        print(f"  real Rs {check['real']:>10,.0f}")
        print(f"  random median Rs {check['median']:>10,.0f}   "
              f"5th-95th Rs {check['p5']:,.0f} to Rs {check['p95']:,.0f}")
        print(f"  beats {check['beats']:.1f}% of draws")
        print(f"  -> {'real edge' if check['beats'] >= 95 else 'INSIDE THE NOISE'}")


if __name__ == "__main__":
    main()
