"""Regime indicators and option greeks, shared by the four online strategies.

The strategies are specified against things this project has never needed
before: daily EMA stacks, ADX, ATR, VWAP, and strike selection by delta rather
than by offset from spot. This builds all of it once from the 1-minute cache.

Two honest substitutions, both forced by what the cache holds:

  VWAP      The cache stores index spot but no index volume, so the anchor is
            spot weighted by total option-chain volume per minute. Chain volume
            is a fair proxy for index activity -- both peak on the same bars --
            but it is not the exchange's VWAP and the strategies' "price above
            VWAP" test inherits that approximation.
  delta     Black-Scholes from the cached per-strike IV, with time to expiry
            measured to the next expiry session. Deltas near expiry move fast,
            so this is more reliable early in the week than on Monday of expiry.
"""
import os
import sys
from datetime import date as date_type

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C

RATE = 0.065
MINUTES_PER_SESSION = 375
TRADING_DAYS = 252


def _ffill(values):
    valid = ~np.isnan(values)
    if not valid.any():
        return values
    index = np.where(valid, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    filled = values[index]
    filled[: np.argmax(valid)] = values[valid][0]
    return filled


def _normal_cdf(x):
    return 0.5 * (1.0 + np.vectorize(_erf)(x / np.sqrt(2.0)))


def _erf(x):
    import math

    return math.erf(x)


def delta(spot, strike, iv, years, is_call):
    """Black-Scholes delta. iv is a fraction, years is time to expiry."""
    if years <= 0 or iv <= 0 or spot <= 0:
        return 1.0 if (is_call and spot > strike) else (
            -1.0 if (not is_call and spot < strike) else 0.0)
    d1 = (np.log(spot / strike) + (RATE + 0.5 * iv * iv) * years) / (iv * np.sqrt(years))
    call_delta = float(_normal_cdf(np.array([d1]))[0])
    return call_delta if is_call else call_delta - 1.0


def daily_bars(dates):
    """Session OHLC of spot, plus the indicators the strategies key off."""
    highs, lows, closes, opens, keep = [], [], [], [], []
    for text in dates:
        try:
            session = C.load(text)
        except OSError:
            continue
        spot = _ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        keep.append(text)
        opens.append(spot[0])
        highs.append(spot.max())
        lows.append(spot.min())
        closes.append(spot[-1])
    return (
        keep,
        np.array(opens),
        np.array(highs),
        np.array(lows),
        np.array(closes),
    )


def ema(values, span):
    alpha = 2.0 / (span + 1.0)
    out = np.full(len(values), np.nan)
    running = values[0]
    for index, value in enumerate(values):
        running = value if index == 0 else alpha * value + (1 - alpha) * running
        out[index] = running
    return out


def true_range(high, low, close):
    previous = np.roll(close, 1)
    previous[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - previous),
                                             np.abs(low - previous)))


def atr(high, low, close, length=14):
    tr = true_range(high, low, close)
    return ema(tr, length)


def adx(high, low, close, length=14):
    """Wilder's ADX on daily bars."""
    up = high - np.roll(high, 1)
    down = np.roll(low, 1) - low
    up[0] = down[0] = 0.0
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(high, low, close)
    smooth_tr = ema(tr, length)
    plus_di = 100 * ema(plus, length) / np.maximum(smooth_tr, 1e-9)
    minus_di = 100 * ema(minus, length) / np.maximum(smooth_tr, 1e-9)
    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-9)
    return ema(dx, length), plus_di, minus_di


def regime(dates):
    """{date: {...}} of daily-close indicators, each usable from the next open."""
    keep, opens, highs, lows, closes = daily_bars(dates)
    fast, slow = ema(closes, 20), ema(closes, 50)
    average_range = atr(highs, lows, closes, 14)
    trend_strength, _plus, _minus = adx(highs, lows, closes, 14)
    table = {}
    for index in range(1, len(keep)):
        previous = index - 1  # only yesterday's completed bar is knowable today
        table[keep[index]] = {
            "prev_close": closes[previous],
            "prev_high": highs[previous],
            "prev_low": lows[previous],
            "ema20": fast[previous],
            "ema50": slow[previous],
            "ema20_rising": fast[previous] > fast[max(previous - 3, 0)],
            "ema50_rising": slow[previous] > slow[max(previous - 3, 0)],
            "atr": average_range[previous],
            "adx": trend_strength[previous],
            "bars_available": index,
        }
    return table


def vwap_proxy(session):
    """Spot weighted by total chain volume, cumulative from the open."""
    spot = _ffill(session["spot"].astype(np.float64))
    volume = np.nan_to_num(session["v"].astype(np.float64)).sum(axis=(0, 1))
    weight = np.where(volume > 0, volume, 1.0)
    return np.cumsum(spot * weight) / np.maximum(np.cumsum(weight), 1e-9)


def expiry_calendar(dates):
    """{session: days until the expiry that expiry_code 1 refers to}.

    Derived from the weekly schedule rather than from observed expiry sessions,
    so the last week of the data still gets a real distance instead of a
    negative one -- the next expiry can lie beyond the end of the cache.
    """
    from datetime import timedelta

    switch = date_type.fromisoformat(C.NIFTY_TUESDAY_EXPIRY_START)
    observed = C.expiry_dates(dates)
    table = {}
    for text in dates:
        day = date_type.fromisoformat(text)
        weekday = 1 if day >= switch else 3  # Tuesday, earlier Thursday
        ahead = (weekday - day.weekday()) % 7
        expiry = day + timedelta(days=ahead)
        # A holiday pulls expiry forward, and those sessions are the observed
        # ones, so trust the observation when it disagrees with the schedule.
        if text in observed:
            expiry = day
        table[text] = (expiry - day).days
    return table


def atm_iv(session, row):
    """ATM implied volatility as a fraction, averaged across call and put."""
    spot = _ffill(session["spot"].astype(np.float64))
    strikes = session["strikes"].astype(np.float64)
    column = int(np.argmin(np.abs(strikes - spot[row])))
    values = session["iv"][:, column, row].astype(np.float64)
    values = values[~np.isnan(values) & (values > 0)]
    if not len(values):
        return None
    average = float(values.mean())
    return average / 100.0 if average > 3 else average


def pick_by_delta(session, row, target_delta, is_call, days_to_expiry,
                  tolerance=0.12):
    """Column index of the strike whose delta is closest to target_delta."""
    spot = _ffill(session["spot"].astype(np.float64))
    strikes = session["strikes"].astype(np.float64)
    years = max(days_to_expiry, 0.5) / 365.0
    best, best_gap = None, tolerance
    for column, strike in enumerate(strikes):
        iv = session["iv"][0 if is_call else 1, column, row]
        if np.isnan(iv) or iv <= 0:
            continue
        iv = float(iv) / 100.0 if iv > 3 else float(iv)
        value = abs(delta(spot[row], strike, iv, years, is_call))
        gap = abs(value - target_delta)
        if gap < best_gap:
            best, best_gap = column, gap
    return best


if __name__ == "__main__":
    dates = C.session_dates()
    table = regime(dates)
    calendar = expiry_calendar(dates)
    print(f"{len(table)} sessions with daily indicators, "
          f"{len(calendar)} with an expiry distance\n")
    sample = sorted(table)[-5:]
    print(f"{'date':<12}{'DTE':>5}{'ema20':>10}{'ema50':>10}{'adx':>7}{'atr':>8}")
    for text in sample:
        row = table[text]
        print(f"{text:<12}{calendar.get(text, -1):>5}{row['ema20']:>10.0f}"
              f"{row['ema50']:>10.0f}{row['adx']:>7.1f}{row['atr']:>8.0f}")

    session = C.load(sample[-1])
    days = calendar.get(sample[-1], 1)
    print(f"\ndelta ladder on {sample[-1]} at 10:00, DTE {days}")
    for target in (0.75, 0.65, 0.50, 0.35, 0.25):
        column = pick_by_delta(session, 45, target, True, days)
        if column is None:
            print(f"  target {target:.2f}  no strike within tolerance")
            continue
        strike = session["strikes"][column]
        print(f"  target {target:.2f}  strike {strike:.0f}  "
              f"premium {session['c'][0, column, 45]:.1f}")
