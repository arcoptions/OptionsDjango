"""Strategy 1: trend-aligned breakout-and-retest, ITM option, index-based stop.

Implemented as specified rather than as convenient:

  regime      previous daily close above EMA50, EMA20 above EMA50 with both
              rising, ADX(14) at or above 20, price on the correct side of the
              volume-weighted anchor, and no entry when the opening gap has
              already eaten more than one daily ATR.
  entry       after the first 30 minutes, a 15-minute close beyond the opening
              range or the previous day's extreme, then a retest that holds the
              broken level and the anchor, then a break of the retest bar.
  stop        on the index -- the retest extreme or one 15-minute ATR, whichever
              is nearer -- with a 30% premium stop underneath it for the case
              where the option gaps through.
  exits       half at +1R, remainder trailed under the last 15-minute swing,
              a 3-bar time stop if the index does not extend, and an immediate
              exit if the broken level fails on a 15-minute close.

R is measured in index points, because that is where the invalidation lives.
The premium risk used for sizing is the index risk multiplied by delta, which is
what a trader can actually estimate at entry.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import regime as R

BAR = 15
OPENING_RANGE_BARS = 2  # first 30 minutes
LAST_ENTRY = 330  # 14:45 IST
TIME_EXIT = 354  # 15:09 IST
PREMIUM_STOP = 0.30
TIME_STOP_BARS = 3
PARTIAL_FRACTION = 0.5
MIN_ADX = 20.0
PREMIUM_MIN, PREMIUM_MAX = 20.0, 900.0


def bars15(spot):
    count = len(spot) // BAR
    high = np.array([spot[i * BAR:(i + 1) * BAR].max() for i in range(count)])
    low = np.array([spot[i * BAR:(i + 1) * BAR].min() for i in range(count)])
    close = np.array([spot[i * BAR:(i + 1) * BAR][-1] for i in range(count)])
    return high, low, close


def atr15(high, low, close, length=14):
    return R.ema(R.true_range(high, low, close), length)


def signals(date, table, spot, anchor):
    """Every breakout-retest-trigger sequence in one session, both directions."""
    daily = table.get(date)
    if not daily or daily["bars_available"] < 50:
        return []
    high, low, close = bars15(spot)
    if len(high) < 8:
        return []
    average_range = atr15(high, low, close)

    gap = abs(spot[0] - daily["prev_close"])
    if daily["atr"] > 0 and gap > daily["atr"]:
        return []  # the move may already be spent
    if daily["adx"] < MIN_ADX:
        return []

    bullish = (daily["prev_close"] > daily["ema50"]
               and daily["ema20"] > daily["ema50"]
               and daily["ema20_rising"] and daily["ema50_rising"])
    bearish = (daily["prev_close"] < daily["ema50"]
               and daily["ema20"] < daily["ema50"]
               and not daily["ema20_rising"] and not daily["ema50_rising"])
    if not (bullish or bearish):
        return []

    opening_high = high[:OPENING_RANGE_BARS].max()
    opening_low = low[:OPENING_RANGE_BARS].min()
    level = (max(opening_high, daily["prev_high"]) if bullish
             else min(opening_low, daily["prev_low"]))

    out = []
    broken_at = None
    for index in range(OPENING_RANGE_BARS, len(close) - 1):
        row = index * BAR
        if row >= LAST_ENTRY:
            break
        side_ok = (close[index] > anchor[row] if bullish
                   else close[index] < anchor[row])
        if broken_at is None:
            if (close[index] > level if bullish else close[index] < level) and side_ok:
                broken_at = index
            continue
        # Retest: pulls back toward the level, closes back on the right side of
        # both the level and the anchor. The spike itself is deliberately skipped.
        touched = (low[index] <= level * 1.001 if bullish
                   else high[index] >= level * 0.999)
        held = (close[index] > level if bullish else close[index] < level)
        if not (touched and held and side_ok):
            if (close[index] < level if bullish else close[index] > level):
                broken_at = None  # the level failed on a close, setup is dead
            continue
        trigger = high[index] if bullish else low[index]
        stop_level = low[index] if bullish else high[index]
        band = average_range[index] if not np.isnan(average_range[index]) else 0.0
        if band > 0:
            # The retest bar sets the invalidation, but a bar can be so narrow
            # that the implied stop is unplaceable, and so wide that it blows
            # the risk budget. Keep the distance between a quarter and a whole
            # 15-minute ATR.
            distance = min(max(abs(trigger - stop_level), 0.25 * band), band)
            stop_level = trigger - distance if bullish else trigger + distance
        if abs(trigger - stop_level) < 1e-6:
            continue
        out.append({
            "bar": index,
            "bullish": bullish,
            "trigger": trigger,
            "stop_spot": stop_level,
            "level": level,
        })
        broken_at = None  # one trade per breakout structure
    return out


def run_trade(session, spot, anchor, signal, target_delta, days_to_expiry):
    """Fill on the trigger break, then manage on the index with a premium backstop."""
    is_call = signal["bullish"]
    # The spec buys when price breaks the retest bar, so the fill is the first
    # minute that actually trades through the trigger -- not the next bar's
    # open, which lets spot drift down to the stop and leaves nothing to risk.
    entry_row = None
    scan_from = (signal["bar"] + 1) * BAR
    for row in range(scan_from, min(LAST_ENTRY, len(spot))):
        if (spot[row] > signal["trigger"]) if is_call else (spot[row] < signal["trigger"]):
            entry_row = row
            break
        # Give up on the setup if the level fails before the trigger is taken.
        if (spot[row] < signal["stop_spot"]) if is_call else (spot[row] > signal["stop_spot"]):
            return None
    if entry_row is None:
        return None

    column = R.pick_by_delta(session, entry_row, target_delta, is_call, days_to_expiry)
    if column is None:
        return None
    side = 0 if is_call else 1
    closes = session["c"][side, column].astype(np.float64)
    entry_premium = closes[entry_row]
    if np.isnan(entry_premium) or not (PREMIUM_MIN <= entry_premium <= PREMIUM_MAX):
        return None

    entry_spot = spot[entry_row]
    stop_spot = signal["stop_spot"]
    risk = abs(entry_spot - stop_spot)
    if risk <= 0:
        return None
    premium_floor = entry_premium * (1 - PREMIUM_STOP)

    high, low, close = bars15(spot)
    booked = 0.0
    remaining = 1.0
    partial_done = False
    best_spot = entry_spot
    entry_bar = signal["bar"] + 1

    limit = min(TIME_EXIT, len(spot), len(closes))
    for row in range(entry_row + 1, limit):
        move = (spot[row] - entry_spot) if is_call else (entry_spot - spot[row])
        premium = closes[row]
        if np.isnan(premium):
            continue
        hit_index_stop = (spot[row] <= stop_spot) if is_call else (spot[row] >= stop_spot)
        if hit_index_stop or premium <= premium_floor:
            return _close(entry_premium, premium, booked, remaining, risk,
                          "STOP", row, target_delta)
        if not partial_done and move >= risk:
            booked += PARTIAL_FRACTION * (premium - entry_premium)
            remaining -= PARTIAL_FRACTION
            partial_done = True
            stop_spot = entry_spot  # the rest rides free
        best_spot = max(best_spot, spot[row]) if is_call else min(best_spot, spot[row])

        bar_index = row // BAR
        if bar_index > entry_bar:
            # Trail under the previous 15-minute swing once it is in profit.
            if partial_done:
                swing = low[bar_index - 1] if is_call else high[bar_index - 1]
                stop_spot = max(stop_spot, swing) if is_call else min(stop_spot, swing)
            # Time stop: no extension after three bars.
            if not partial_done and bar_index - entry_bar >= TIME_STOP_BARS:
                return _close(entry_premium, premium, booked, remaining, risk,
                              "TIME_STOP", row, target_delta)
            # Broken level failed on a close.
            failed = (close[bar_index - 1] < signal["level"] if is_call
                      else close[bar_index - 1] > signal["level"])
            if failed:
                return _close(entry_premium, premium, booked, remaining, risk,
                              "LEVEL_FAIL", row, target_delta)
    final = closes[limit - 1]
    if np.isnan(final):
        return None
    return _close(entry_premium, final, booked, remaining, risk, "TIME_EXIT",
                  limit - 1, target_delta)


def _close(entry_premium, exit_premium, booked, remaining, risk, outcome, row,
           target_delta):
    total = booked + remaining * (exit_premium - entry_premium)
    # What the trader can actually estimate at entry: the index risk converted
    # to premium through delta, floored by the 30% catastrophic stop, which is
    # the most that can be lost if the index stop is jumped over.
    unit_risk = min(target_delta * risk, entry_premium * PREMIUM_STOP)
    return {
        "entry": entry_premium,
        "premium_pnl": total,
        "unit_risk": unit_risk,
        "premium_r": total / unit_risk if unit_risk > 0 else 0.0,
        "outcome": outcome,
        "exit_row": row,
        "index_risk": risk,
    }


def run(target_delta, dates=None):
    dates = dates or C.session_dates()
    table = R.regime(dates)
    calendar = R.expiry_calendar(dates)
    trades = []
    for date in dates:
        try:
            session = C.load(date)
        except OSError:
            continue
        spot = R._ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        anchor = R.vwap_proxy(session)
        days = calendar.get(date, 1)
        for signal in signals(date, table, spot, anchor):
            trade = run_trade(session, spot, anchor, signal, target_delta, days)
            if trade:
                trades.append({**trade, "date": date, "dte": days,
                               "bullish": signal["bullish"]})
    return trades


def summarise(trades):
    if not trades:
        return None
    r = np.array([t["premium_r"] for t in trades])
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {
        "n": len(r),
        "win": 100 * (r > 0).mean(),
        "totR": r.sum(),
        "avgR": r.mean(),
        "pf": gains / losses if losses else float("inf"),
        "avgWin": r[r > 0].mean() if (r > 0).any() else 0.0,
        "avgLoss": r[r <= 0].mean() if (r <= 0).any() else 0.0,
    }


def main():
    header = (f"{'delta':>7}{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}{'PF':>7}"
              f"{'avgWin':>9}{'avgLoss':>9}{'W/L':>7}")
    print("Strategy 1: trend breakout-and-retest, NIFTY, 246 sessions\n")
    print(header)
    print("-" * len(header))
    for target_delta in (0.75, 0.65, 0.55, 0.45, 0.35, 0.25):
        result = summarise(run(target_delta))
        if not result:
            print(f"{target_delta:>7.2f}   no trades")
            continue
        ratio = abs(result["avgWin"] / result["avgLoss"]) if result["avgLoss"] else 0
        print(f"{target_delta:>7.2f}{result['n']:>5}{result['win']:>7.1f}"
              f"{result['totR']:>9.1f}{result['avgR']:>8.3f}{result['pf']:>7.2f}"
              f"{result['avgWin']:>9.2f}{result['avgLoss']:>9.2f}{ratio:>7.2f}")


if __name__ == "__main__":
    main()
