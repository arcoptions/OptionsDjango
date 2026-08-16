"""Why Strategy 1 only fires 25 times, and whether the edge survives loosening.

Twenty-five trades in 246 sessions passes every quality gate in the source
report except the one that decides whether the others mean anything -- its own
"at least 150-200 trades" rule. This counts how many sessions each filter
removes, then re-runs the strategy with each filter relaxed in turn. If the edge
is real it should degrade smoothly as the gates open; if it is an artefact of
twenty-five lucky sessions it will collapse.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import online_s1_breakout as S
import regime as R

DELTA = 0.55


def funnel():
    dates = C.session_dates()
    table = R.regime(dates)
    counts = {"sessions": 0, "have regime": 0, "gap ok": 0, "adx ok": 0,
              "trend aligned": 0, "broke level": 0, "retested": 0, "filled": 0}
    calendar = R.expiry_calendar(dates)
    for date in dates:
        counts["sessions"] += 1
        daily = table.get(date)
        if not daily or daily["bars_available"] < 50:
            continue
        counts["have regime"] += 1
        try:
            session = C.load(date)
        except OSError:
            continue
        spot = R._ffill(session["spot"].astype(np.float64))
        if len(spot) < 200 or np.isnan(spot).any():
            continue
        gap = abs(spot[0] - daily["prev_close"])
        if daily["atr"] > 0 and gap > daily["atr"]:
            continue
        counts["gap ok"] += 1
        if daily["adx"] < S.MIN_ADX:
            continue
        counts["adx ok"] += 1
        bullish = (daily["prev_close"] > daily["ema50"]
                   and daily["ema20"] > daily["ema50"]
                   and daily["ema20_rising"] and daily["ema50_rising"])
        bearish = (daily["prev_close"] < daily["ema50"]
                   and daily["ema20"] < daily["ema50"]
                   and not daily["ema20_rising"] and not daily["ema50_rising"])
        if not (bullish or bearish):
            continue
        counts["trend aligned"] += 1
        anchor = R.vwap_proxy(session)
        high, low, close = S.bars15(spot)
        level = (max(high[:2].max(), daily["prev_high"]) if bullish
                 else min(low[:2].min(), daily["prev_low"]))
        if (close[2:] > level).any() if bullish else (close[2:] < level).any():
            counts["broke level"] += 1
        found = S.signals(date, table, spot, anchor)
        if found:
            counts["retested"] += 1
        days = calendar.get(date, 1)
        if any(S.run_trade(session, spot, anchor, signal, DELTA, days)
               for signal in found):
            counts["filled"] += 1
    return counts


def variant(name, **overrides):
    original = {key: getattr(S, key) for key in overrides}
    for key, value in overrides.items():
        setattr(S, key, value)
    try:
        result = S.summarise(S.run(DELTA))
    finally:
        for key, value in original.items():
            setattr(S, key, value)
    return name, result


def main():
    print("filter funnel, NIFTY 246 sessions\n")
    counts = funnel()
    previous = None
    for label, value in counts.items():
        drop = "" if previous is None else f"  (-{previous - value})"
        print(f"  {label:<16}{value:>5}{drop}")
        previous = value

    print(f"\nsensitivity at delta {DELTA}\n")
    header = f"{'variant':<28}{'n':>5}{'win%':>7}{'totR':>9}{'avgR':>8}{'PF':>7}"
    print(header)
    print("-" * len(header))
    cases = [
        variant("as specified"),
        variant("ADX >= 15", MIN_ADX=15.0),
        variant("ADX >= 0 (no filter)", MIN_ADX=0.0),
        variant("time stop 5 bars", TIME_STOP_BARS=5),
        variant("no partial, full runner", PARTIAL_FRACTION=0.0),
        variant("partial 25%", PARTIAL_FRACTION=0.25),
        variant("premium stop 20%", PREMIUM_STOP=0.20),
    ]
    for name, result in cases:
        if not result:
            print(f"{name:<28}   no trades")
            continue
        print(f"{name:<28}{result['n']:>5}{result['win']:>7.1f}{result['totR']:>9.1f}"
              f"{result['avgR']:>8.3f}{result['pf']:>7.2f}")


if __name__ == "__main__":
    main()
