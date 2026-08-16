"""Zero-skill controls for the two strategies that survived, and the rupee ledger.

The control asks the only question that matters about a 25-trade result: does the
entry rule do anything, or is the exit machinery doing all the work? So it keeps
the machinery identical -- same stop construction from the 15-minute ATR, same
partial at +1R, same swing trail, same 3-bar time stop, same level-fail and
time exits -- and replaces the entry with a random bar and a random direction.

Anything the strategy earns above that band is what the breakout-and-retest
signal is worth. Anything below it is the exits.

The ledger then converts R into rupees on the live account and cost model,
because 25 trades a year at 1R each is a different proposition from 250.
"""
import os
import sys
from math import floor

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges

import common as C
import online_s1_breakout as S
import online_s2_spread as S2
import regime as R

START, RISK, CASH = 100_000.0, 0.02, 0.40
ITERATIONS = 2000
SEED = 20260814


def build_pool(target_delta, dates=None):
    """Every random-entry trade the exit machinery would have produced.

    One candidate per 15-minute bar per direction, with the trigger and stop
    built by exactly the same rules the real signal uses, so the only difference
    from the strategy is which bar was chosen and which way.
    """
    dates = dates or C.session_dates()
    table = R.regime(dates)
    calendar = R.expiry_calendar(dates)
    pool = []
    for date in dates:
        daily = table.get(date)
        if not daily or daily["bars_available"] < 50:
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
        high, low, close = S.bars15(spot)
        if len(high) < 8:
            continue
        band = S.atr15(high, low, close)
        for index in range(S.OPENING_RANGE_BARS, len(close) - 1):
            if index * S.BAR >= S.LAST_ENTRY:
                break
            width = band[index] if not np.isnan(band[index]) else 0.0
            if width <= 0:
                continue
            for bullish in (True, False):
                trigger = high[index] if bullish else low[index]
                stop_level = low[index] if bullish else high[index]
                distance = min(max(abs(trigger - stop_level), 0.25 * width), width)
                stop = trigger - distance if bullish else trigger + distance
                level = (max(high[:2].max(), daily["prev_high"]) if bullish
                         else min(low[:2].min(), daily["prev_low"]))
                signal = {"bar": index, "bullish": bullish, "trigger": trigger,
                          "stop_spot": stop, "level": level}
                trade = S.run_trade(session, spot, anchor, signal, target_delta, days)
                if trade:
                    pool.append({**trade, "date": date})
    return pool


def control_band(pool, size, statistic):
    """Distribution of a statistic over random draws of `size` trades."""
    rng = np.random.default_rng(SEED)
    values = np.empty(ITERATIONS)
    for index in range(ITERATIONS):
        draw = rng.integers(0, len(pool), size)
        values[index] = statistic([pool[i] for i in draw])
    return values


def mean_r(trades):
    return float(np.mean([t["premium_r"] for t in trades]))


def win_rate(trades):
    return 100.0 * float(np.mean([t["premium_r"] > 0 for t in trades]))


def ledger(trades, partial=True):
    """Compounded rupee outcome on the live account and cost model."""
    equity = peak = START
    drawdown = gross_total = charge_total = 0.0
    wins = count = 0
    for trade in sorted(trades, key=lambda item: (item["date"], item["exit_row"])):
        entry, unit_risk = trade["entry"], trade["unit_risk"]
        if unit_risk <= 0 or entry <= 0:
            continue
        lots = min(
            floor(equity * RISK / (unit_risk * NIFTY_LOT_SIZE)),
            floor(equity * CASH / (entry * NIFTY_LOT_SIZE)),
        )
        if lots < 1:
            continue
        quantity = lots * NIFTY_LOT_SIZE
        exit_price = max(entry + trade["premium_pnl"], 0.0)
        charges = estimate_option_charges(entry, exit_price, quantity, trade["date"])
        if partial and S.PARTIAL_FRACTION > 0:
            charges += 40.0 * 1.18  # the scale-out is a second sell ticket
        gross = trade["premium_pnl"] * quantity
        equity += gross - charges
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        gross_total += gross
        charge_total += charges
        wins += gross - charges > 0
        count += 1
    return {"n": count, "win": 100 * wins / count if count else 0.0,
            "gross": gross_total, "charges": charge_total,
            "net": equity - START, "dd": drawdown}


def main():
    dates = C.session_dates()

    print("zero-skill control: random bar, random direction, identical exits\n")
    header = (f"{'strategy':<28}{'n':>5}{'avgR':>8}{'ctrl med':>10}"
              f"{'ctrl 95th':>11}{'pct':>7}{'win%':>7}{'ctrl win':>10}")
    print(header)
    print("-" * len(header))

    rows = []
    for target_delta in (0.55, 0.65):
        live = S.run(target_delta, dates)
        pool = build_pool(target_delta, dates)
        band = control_band(pool, len(live), mean_r)
        wins = control_band(pool, len(live), win_rate)
        observed = mean_r(live)
        percentile = 100.0 * float((band < observed).mean())
        rows.append((f"S1 outright delta {target_delta:.2f}", live))
        print(f"{'S1 outright delta %.2f' % target_delta:<28}{len(live):>5}"
              f"{observed:>8.3f}{np.median(band):>10.3f}"
              f"{np.percentile(band, 95):>11.3f}{percentile:>7.1f}"
              f"{win_rate(live):>7.1f}{np.median(wins):>10.1f}")
        print(f"    pool of {len(pool)} random-entry trades, "
              f"control avgR {band.mean():.3f}")

    spread = S2.run(dates)
    rows.append(("S2 debit spread 0.65/0.30", spread))

    print("\nrupees on a Rs 1,00,000 account, 2% risk, 40% cash cap, real charges\n")
    header = (f"{'strategy':<28}{'n':>5}{'win%':>7}{'gross':>11}{'charges':>10}"
              f"{'net':>11}{'ret%':>8}{'maxDD':>10}")
    print(header)
    print("-" * len(header))
    for name, trades in rows:
        book = ledger(trades)
        if not book["n"]:
            print(f"{name:<28}   no sized trades")
            continue
        print(f"{name:<28}{book['n']:>5}{book['win']:>7.1f}{book['gross']:>11,.0f}"
              f"{book['charges']:>10,.0f}{book['net']:>11,.0f}"
              f"{100*book['net']/START:>8.1f}{book['dd']:>10,.0f}")
    print(f"{'shipped nifty_trail (bench)':<28}{64:>5}{75.0:>7.1f}"
          f"{'':>11}{'':>10}{21221:>11,.0f}{21.2:>8.1f}{5213:>10,.0f}")


if __name__ == "__main__":
    main()
