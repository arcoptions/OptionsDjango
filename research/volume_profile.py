"""A volume profile for an index that has no volume.

NIFTY prints no traded quantity, which is why every level in this project so far
has been drawn from price alone -- highs, lows, opens, round numbers. A volume
profile needs to know how much trade happened at each price, and the one honest
source available offline is the constituents: 49 stocks, 1-minute close and
quantity, already cached. Their rupee turnover summed per minute is how hard the
market traded while the index sat at that level.

The profile is *developing*: at any minute it is built only from the session so
far, so a level used as a target at 11:00 was computable at 11:00. The prior
session's completed profile is carried too, because that is the one traders
actually have drawn on the chart at the open.

Value area is the conventional 70% of turnover grown outward from the point of
control, taking the heavier neighbour at each step.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breadth as B
import common as C

BIN = 5.0  # index points; NIFTY ticks in 0.05 but trades in ranges of tens
VALUE_AREA = 0.70


def session_turnover(date):
    """Index spot and total constituent rupee turnover, per minute."""
    data = B.load_stocks(date)
    close = np.asarray(data["close"], dtype=np.float64)
    volume = np.asarray(data["volume"], dtype=np.float64)
    turnover = np.nansum(np.where(np.isfinite(close) & np.isfinite(volume),
                                  close * volume, 0.0), axis=0)
    spot = np.asarray(C.load(date)["spot"], dtype=np.float64)
    length = min(len(spot), len(turnover))
    return spot[:length], turnover[:length]


def profile(spot, turnover, upto=None):
    """Turnover accumulated into price bins over spot[:upto]."""
    end = len(spot) if upto is None else min(upto + 1, len(spot))
    prices, weights = spot[:end], turnover[:end]
    good = np.isfinite(prices) & (prices > 0) & np.isfinite(weights) & (weights > 0)
    if good.sum() < 5:
        return None, None
    prices, weights = prices[good], weights[good]
    low = np.floor(prices.min() / BIN) * BIN
    high = np.ceil(prices.max() / BIN) * BIN + BIN
    edges = np.arange(low, high + BIN, BIN)
    if len(edges) < 3:
        return None, None
    histogram, _ = np.histogram(prices, bins=edges, weights=weights)
    centres = edges[:-1] + BIN / 2
    return centres, histogram


def poc_value_area(centres, histogram):
    """Point of control and the 70% value area grown outward from it."""
    if centres is None or not histogram.sum():
        return None
    peak = int(np.argmax(histogram))
    target = VALUE_AREA * histogram.sum()
    total = histogram[peak]
    low = high = peak
    while total < target and (low > 0 or high < len(histogram) - 1):
        below = histogram[low - 1] if low > 0 else -1.0
        above = histogram[high + 1] if high < len(histogram) - 1 else -1.0
        if above >= below:
            high += 1
            total += histogram[high]
        else:
            low -= 1
            total += histogram[low]
    return {"poc": float(centres[peak]),
            "val": float(centres[low]),
            "vah": float(centres[high])}


def developing(date, minute):
    """POC/VAH/VAL from the open through `minute` of `date`."""
    spot, turnover = session_turnover(date)
    return poc_value_area(*profile(spot, turnover, minute))


def completed(date):
    """The session's finished profile, for use as prior-day levels."""
    spot, turnover = session_turnover(date)
    return poc_value_area(*profile(spot, turnover))


def main():
    dates = [d for d in C.session_dates() if d in set(B.stock_dates())]
    print(f"{len(dates)} sessions with both index and constituent data\n")
    print(f"{'date':<12}{'POC':>10}{'VAL':>10}{'VAH':>10}{'width':>8}"
          f"{'close':>10}{'in VA':>7}")
    widths = []
    inside = 0
    counted = 0
    for date in dates[-12:]:
        levels = completed(date)
        if not levels:
            continue
        spot, _turnover = session_turnover(date)
        good = spot[np.isfinite(spot) & (spot > 0)]
        close = good[-1] if len(good) else np.nan
        width = levels["vah"] - levels["val"]
        widths.append(width)
        print(f"{date:<12}{levels['poc']:>10,.0f}{levels['val']:>10,.0f}"
              f"{levels['vah']:>10,.0f}{width:>8,.0f}{close:>10,.0f}"
              f"{'yes' if levels['val'] <= close <= levels['vah'] else 'no':>7}")
    for date in dates:
        levels = completed(date)
        if not levels:
            continue
        spot, _turnover = session_turnover(date)
        good = spot[np.isfinite(spot) & (spot > 0)]
        if not len(good):
            continue
        counted += 1
        inside += levels["val"] <= good[-1] <= levels["vah"]
        widths.append(levels["vah"] - levels["val"])
    print(f"\nover {counted} sessions: median value area width "
          f"{np.median(widths):,.0f} points, close inside the value area "
          f"{100 * inside / counted:.0f}% of the time")


if __name__ == "__main__":
    main()
