"""RSI and moving averages on the index, computed without lookahead.

Everything here works on the 1-minute spot path from the session cache and
resamples up, because a 1-minute RSI on an index is mostly microstructure noise
and nobody trades off it. The resampling is the part worth being careful about:
a bar is only usable once it has *closed*, so `last_closed_bar` is what decides
which value an entry at a given minute is allowed to see. Getting that wrong is
the single easiest way to manufacture a strategy that cannot be traded.
"""
import numpy as np


def resample(series, minutes):
    """Close, high and low of each `minutes`-long bar, in order.

    Trailing partial bars are kept; `last_closed_bar` is what stops them being
    used before they finish.
    """
    series = np.asarray(series, dtype=float)
    count = int(np.ceil(len(series) / minutes))
    closes = np.full(count, np.nan)
    highs = np.full(count, np.nan)
    lows = np.full(count, np.nan)
    for bar in range(count):
        chunk = series[bar * minutes:(bar + 1) * minutes]
        chunk = chunk[np.isfinite(chunk)]
        if len(chunk):
            closes[bar], highs[bar], lows[bar] = chunk[-1], chunk.max(), chunk.min()
    return closes, highs, lows


def last_closed_bar(minute, minutes):
    """Index of the newest bar that has fully closed by `minute`, or -1.

    A bar spanning minutes [k*n, (k+1)*n - 1] is only complete at its final
    minute, so a decision taken at `minute` may read bar k only once
    `minute` has reached that final minute.
    """
    return (minute + 1) // minutes - 1


def rsi(closes, period=14):
    """Wilder's RSI. Returns NaN until there is enough history to define it."""
    closes = np.asarray(closes, dtype=float)
    out = np.full(len(closes), np.nan)
    if len(closes) <= period:
        return out
    change = np.diff(closes)
    gain = np.where(change > 0, change, 0.0)
    loss = np.where(change < 0, -change, 0.0)
    # Seed with a simple mean, then smooth -- Wilder's original definition.
    avg_gain = np.nanmean(gain[:period])
    avg_loss = np.nanmean(loss[:period])
    if not np.isfinite(avg_gain) or not np.isfinite(avg_loss):
        return out
    for index in range(period, len(closes)):
        if index > period:
            step = change[index - 1]
            if not np.isfinite(step):
                continue
            avg_gain = (avg_gain * (period - 1) + max(step, 0.0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-step, 0.0)) / period
        if avg_loss == 0:
            out[index] = 100.0
        else:
            out[index] = 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def ema(closes, span=20):
    closes = np.asarray(closes, dtype=float)
    out = np.full(len(closes), np.nan)
    alpha = 2.0 / (span + 1)
    running = None
    for index, value in enumerate(closes):
        if not np.isfinite(value):
            continue
        running = value if running is None else alpha * value + (1 - alpha) * running
        if index >= span - 1:
            out[index] = running
    return out


def sma(closes, window=20):
    closes = np.asarray(closes, dtype=float)
    out = np.full(len(closes), np.nan)
    for index in range(window - 1, len(closes)):
        chunk = closes[index - window + 1:index + 1]
        if np.isfinite(chunk).all():
            out[index] = chunk.mean()
    return out


def average(closes, kind, length=20):
    return ema(closes, length) if kind == "EMA" else sma(closes, length)


def macd(closes, fast=12, slow=26, signal=9):
    """MACD line, signal line and histogram on already-resampled closes."""
    line = ema(closes, fast) - ema(closes, slow)
    trigger = ema(line, signal)
    return line, trigger, line - trigger


def atr(highs, lows, closes, period=14):
    """Wilder's average true range. NaN until there is enough history."""
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    count = len(closes)
    out = np.full(count, np.nan)
    if count <= period:
        return out
    true_range = np.full(count, np.nan)
    true_range[0] = highs[0] - lows[0]
    for index in range(1, count):
        previous = closes[index - 1]
        true_range[index] = max(highs[index] - lows[index],
                                abs(highs[index] - previous),
                                abs(lows[index] - previous))
    running = np.nanmean(true_range[1:period + 1])
    if not np.isfinite(running):
        return out
    out[period] = running
    for index in range(period + 1, count):
        step = true_range[index]
        if not np.isfinite(step):
            out[index] = running
            continue
        running = (running * (period - 1) + step) / period
        out[index] = running
    return out


def supertrend(highs, lows, closes, period=10, multiplier=3.0):
    """Supertrend line and direction (+1 green, -1 red), one value per bar.

    The band-carrying rule is the standard one: a band only moves in the
    direction that tightens it, and resets when price closes through it. Without
    that memory the indicator flickers on every bar and stops being the thing
    traders are looking at.
    """
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    count = len(closes)
    line = np.full(count, np.nan)
    direction = np.full(count, np.nan)
    span = atr(highs, lows, closes, period)
    mid = (highs + lows) / 2.0
    upper = mid + multiplier * span
    lower = mid - multiplier * span
    final_upper = np.full(count, np.nan)
    final_lower = np.full(count, np.nan)
    for index in range(count):
        if not np.isfinite(span[index]) or not np.isfinite(closes[index]):
            continue
        previous = index - 1
        carry = previous >= 0 and np.isfinite(final_upper[previous])
        if not carry:
            final_upper[index], final_lower[index] = upper[index], lower[index]
            direction[index] = 1.0 if closes[index] >= final_lower[index] else -1.0
        else:
            final_upper[index] = (upper[index]
                                  if upper[index] < final_upper[previous]
                                  or closes[previous] > final_upper[previous]
                                  else final_upper[previous])
            final_lower[index] = (lower[index]
                                  if lower[index] > final_lower[previous]
                                  or closes[previous] < final_lower[previous]
                                  else final_lower[previous])
            if direction[previous] == 1.0:
                direction[index] = -1.0 if closes[index] < final_lower[index] else 1.0
            else:
                direction[index] = 1.0 if closes[index] > final_upper[index] else -1.0
        line[index] = (final_lower[index] if direction[index] == 1.0
                       else final_upper[index])
    return line, direction


def vwap(prices, weights):
    """Session VWAP from a price path and a per-minute weight (turnover).

    Cumulative and session-local by construction, so the value at minute t uses
    only minutes 0..t. Minutes with no weight carry the running value forward
    rather than dropping out, because a chart's VWAP does not blank out when a
    minute prints nothing.
    """
    prices = np.asarray(prices, dtype=float)
    weights = np.asarray(weights, dtype=float)
    length = min(len(prices), len(weights))
    prices, weights = prices[:length], weights[:length]
    good = np.isfinite(prices) & (prices > 0) & np.isfinite(weights) & (weights > 0)
    value = np.where(good, prices * weights, 0.0)
    mass = np.where(good, weights, 0.0)
    cumulative_value = np.cumsum(value)
    cumulative_mass = np.cumsum(mass)
    out = np.full(length, np.nan)
    live = cumulative_mass > 0
    out[live] = cumulative_value[live] / cumulative_mass[live]
    return out
