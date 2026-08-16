"""Strategy 3: buy the breakout out of a low-volatility compression.

Compression is measured three ways, as the report allows, and a session
qualifies on any of them: two consecutive inside days, 14-day ATR in the bottom
fifth of its six-month range, or Bollinger width in the bottom fifth. On top of
that the ATM IV percentile has to be under 40 -- the premise is that the market
is charging little for a move it is about to make.

Entry needs a 15-minute close outside both the compression range and the opening
range, then a second close in the same direction, with price on the correct side
of the volume-weighted anchor. Exit if price closes back inside the compression
range, on a three-bar time stop, or at the profit ladder.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import online_s1_breakout as S
import online_s2_spread as S2
import regime as R

DELTA = 0.55
IV_CEILING = 40.0
COMPRESSION_LOOKBACK = 120  # roughly six months of sessions
BOTTOM_FRACTION = 0.20
TARGET_R = 2.0


def compression_table(dates):
    """Per-session compression flags, all computed from completed prior bars."""
    keep, opens, highs, lows, closes = R.daily_bars(dates)
    average_range = R.atr(highs, lows, closes, 14)
    width = np.full(len(closes), np.nan)
    for index in range(20, len(closes)):
        window = closes[index - 20:index]
        middle = window.mean()
        spread = window.std()
        if middle > 0:
            width[index] = 4 * spread / middle
    table = {}
    for index in range(1, len(keep)):
        previous = index - 1
        if previous < 25:
            continue
        start = max(0, previous - COMPRESSION_LOOKBACK)
        history_atr = average_range[start:previous]
        history_width = width[start:previous]
        history_atr = history_atr[~np.isnan(history_atr)]
        history_width = history_width[~np.isnan(history_width)]
        inside = (
            previous >= 2
            and highs[previous] < highs[previous - 1]
            and lows[previous] > lows[previous - 1]
            and highs[previous - 1] < highs[previous - 2]
            and lows[previous - 1] > lows[previous - 2]
        )
        tight_atr = (len(history_atr) > 30 and not np.isnan(average_range[previous])
                     and average_range[previous]
                     <= np.quantile(history_atr, BOTTOM_FRACTION))
        tight_width = (len(history_width) > 30 and not np.isnan(width[previous])
                       and width[previous]
                       <= np.quantile(history_width, BOTTOM_FRACTION))
        if inside or tight_atr or tight_width:
            table[keep[index]] = {
                "high": max(highs[previous], highs[previous - 1]),
                "low": min(lows[previous], lows[previous - 1]),
                "atr": average_range[previous],
                "reason": ("inside" if inside else
                           "atr" if tight_atr else "bbwidth"),
            }
    return table


def signals(spot, anchor, box):
    """Second 15-minute close outside both the compression box and the open range."""
    high, low, close = S.bars15(spot)
    if len(close) < 8:
        return []
    average_range = S.atr15(high, low, close)
    opening_high, opening_low = high[:2].max(), low[:2].min()
    upper = max(box["high"], opening_high)
    lower = min(box["low"], opening_low)
    out = []
    pending = None
    for index in range(2, len(close) - 1):
        row = index * S.BAR
        if row >= S.LAST_ENTRY:
            break
        above = close[index] > upper and close[index] > anchor[row]
        below = close[index] < lower and close[index] < anchor[row]
        if pending == "up" and above:
            band = average_range[index] if not np.isnan(average_range[index]) else 0.0
            out.append({"bar": index, "bullish": True, "trigger": high[index],
                        "stop_spot": min(upper, high[index] - max(band, 1.0)),
                        "level": upper})
            pending = None
            continue
        if pending == "down" and below:
            band = average_range[index] if not np.isnan(average_range[index]) else 0.0
            out.append({"bar": index, "bullish": False, "trigger": low[index],
                        "stop_spot": max(lower, low[index] + max(band, 1.0)),
                        "level": lower})
            pending = None
            continue
        pending = "up" if above else "down" if below else None
    return out


def run(dates=None, iv_table=None):
    dates = dates or C.session_dates()
    boxes = compression_table(dates)
    calendar = R.expiry_calendar(dates)
    ivs = iv_table if iv_table is not None else S2.iv_percentile_table(dates)
    trades = []
    for date in dates:
        box = boxes.get(date)
        if not box:
            continue
        percentile = ivs.get(date)
        if percentile is None or percentile > IV_CEILING:
            continue
        try:
            session = C.load(date)
        except OSError:
            continue
        spot = R._ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        anchor = R.vwap_proxy(session)
        days = calendar.get(date, 1)
        for signal in signals(spot, anchor, box):
            trade = S.run_trade(session, spot, anchor, signal, DELTA, days)
            if trade:
                trades.append({**trade, "date": date, "reason": box["reason"]})
    return trades


def main():
    dates = C.session_dates()
    boxes = compression_table(dates)
    ivs = S2.iv_percentile_table(dates)
    qualified = [d for d in boxes if ivs.get(d) is not None and ivs[d] <= IV_CEILING]
    print("Strategy 3: low-IV compression breakout, NIFTY\n")
    print(f"  compressed sessions          {len(boxes)}")
    print(f"  also IV percentile <= {IV_CEILING:.0f}    {len(qualified)}")
    reasons = {}
    for date in qualified:
        reasons[boxes[date]["reason"]] = reasons.get(boxes[date]["reason"], 0) + 1
    print(f"  qualifying reason            {reasons}\n")

    trades = run(dates, iv_table=ivs)
    result = S.summarise(trades)
    header = f"{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}{'PF':>7}{'avgWin':>9}{'avgLoss':>9}"
    print(header)
    print("-" * len(header))
    if not result:
        print("  no trades")
        return
    print(f"{result['n']:>5}{result['win']:>7.1f}{result['totR']:>9.1f}"
          f"{result['avgR']:>8.3f}{result['pf']:>7.2f}{result['avgWin']:>9.2f}"
          f"{result['avgLoss']:>9.2f}")
    outcomes = {}
    for trade in trades:
        outcomes[trade["outcome"]] = outcomes.get(trade["outcome"], 0) + 1
    print(f"\noutcomes: {outcomes}")


if __name__ == "__main__":
    main()
