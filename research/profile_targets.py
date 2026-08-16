"""Volume-profile levels as targets, scored the same way every other level was.

The earlier level study left one gap. Targets are worth a great deal in principle
-- selling half at the exact high turns Rs 256,748 into Rs 822,610 and *cuts*
drawdown -- but every level available from price alone was either informative and
too close (the opening range, 0.6R away) or far enough to matter and pure noise
(round numbers, beating shuffled distances 34% of the time). Volume-profile
levels were the named suspect for the middle ground.

There is a structural wrinkle. This strategy enters on momentum, so at the fill
price is usually leaving the developing value area, not approaching it: the
developing VAH sits *behind* a call, which makes it useless as an upside target.
So the profile has to be asked for forward levels too --

  prior session VAH / POC / VAL   the profile a trader has drawn before the open
  value-area extensions           VAH + k*(VAH-VAL) on a break, the measured move
  naked POC                       a prior point of control price never came back to

The extensions matter most, because their distance scales with the day's own
value-area width rather than with our stop. That is the one property every level
tested so far lacked.

Scoring is deliberately identical to level_targets/target_ceiling: distance in R
via delta, percent reached, half out at the target with the runner trailing, and
each level raced against its own distances shuffled between trades.
"""
import os
import sys
from datetime import datetime

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config

import breadth as B
import common as C
import volume_profile as V
from exit_lab import run
from level_targets import CAPITAL, OPEN_MINUTE, book, target_r

SEED = 20260815
SHUFFLES = 400
NAKED_LOOKBACK = 20  # sessions; older points of control stop being talked about


def minute_of(stamp):
    """`signal_at` is written back out as an ISO string by the backtester."""
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    return stamp.hour * 60 + stamp.minute - OPEN_MINUTE


def build(dates):
    """Completed profile and traded range per session, in date order."""
    table = {}
    for date in dates:
        try:
            spot, turnover = V.session_turnover(date)
        except (OSError, KeyError):
            continue
        levels = V.poc_value_area(*V.profile(spot, turnover))
        good = np.isfinite(spot) & (spot > 0)
        if not levels or not good.any():
            continue
        table[date] = {"levels": levels, "low": float(spot[good].min()),
                       "high": float(spot[good].max())}
    return table


def naked_pocs(history, dates, upto):
    """Prior points of control that no later session has traded through."""
    index = dates.index(upto)
    out = []
    for offset in range(max(0, index - NAKED_LOOKBACK), index):
        day = dates[offset]
        if day not in history:
            continue
        poc = history[day]["levels"]["poc"]
        touched = any(history[later]["low"] <= poc <= history[later]["high"]
                      for later in dates[offset + 1:index] if later in history)
        if not touched:
            out.append(poc)
    return out


def targets_for(trade, history, dates):
    """Volume-profile targets ahead of the trade, by name."""
    date, spot = trade["date"], trade["entry_spot"]
    call = trade["option_type"] == "CALL"
    ahead = lambda level: (level > spot) if call else (level < spot)
    found = {}

    index = dates.index(date) if date in dates else -1
    if index > 0:
        for offset in range(index - 1, -1, -1):
            if dates[offset] in history:
                prior = history[dates[offset]]["levels"]
                for name, key in (("prior POC", "poc"), ("prior VAH", "vah"),
                                  ("prior VAL", "val")):
                    if ahead(prior[key]):
                        found[name] = prior[key]
                break

    # The developing profile is built only from minutes already printed when the
    # signal fired, so nothing here is known before it could have been known.
    live = V.developing(date, minute_of(trade["signal_at"]))
    if live:
        width = max(live["vah"] - live["val"], V.BIN)
        edge = live["vah"] if call else live["val"]
        for key, name in (("poc", "dev POC"), ("vah", "dev VAH"), ("val", "dev VAL")):
            if ahead(live[key]):
                found[name] = live[key]
        # The measured move off the value area. Its size is set by how wide the
        # day's own value has built, which is the property a fixed R multiple and
        # every price-only level both lack.
        for factor in (0.5, 1.0, 1.5):
            found[f"VA ext {factor}x"] = edge + (factor * width if call else -factor * width)
        found["VA width from entry"] = spot + (width if call else -width)

    if index >= 0:
        pocs = [p for p in naked_pocs(history, dates, date) if ahead(p)]
        if pocs:
            found["naked POC"] = min(pocs, key=lambda p: abs(p - spot))

    structural = {k: v for k, v in found.items() if not k.startswith("VA ")}
    if structural:
        found["nearest profile level"] = min(structural.values(),
                                             key=lambda v: abs(v - spot))
    return found


NAMES = ["prior POC", "prior VAH", "prior VAL", "dev POC", "dev VAH", "dev VAL",
         "naked POC", "nearest profile level", "VA width from entry",
         "VA ext 0.5x", "VA ext 1.0x", "VA ext 1.5x"]


def main():
    dates = [d for d in C.session_dates() if d in set(B.stock_dates())]
    history = build(dates)
    print(f"{len(history)} sessions profiled", flush=True)

    trades = run(nifty_trail_config(), trail_gap=0.7, record=True)
    usable = [t for t in trades if t["date"] in history]
    print(f"{len(usable)} of {len(trades)} trades on profiled sessions; "
          f"capital Rs {CAPITAL:,}\n", flush=True)

    plans = {name: {} for name in NAMES}
    distances = {name: [] for name in NAMES}
    for trade in usable:
        found = targets_for(trade, history, dates)
        for name, level in found.items():
            if name not in plans:
                continue
            multiple = target_r(trade, level)
            if multiple is None or multiple <= 0.05:
                continue
            plans[name][id(trade)] = multiple
            distances[name].append(multiple)

    print(f"  {'target':<24}{'trades':>8}{'median R':>10}{'p25':>7}{'p75':>7}"
          f"{'reached':>9}")
    for name in NAMES:
        values = np.array(distances[name])
        if not len(values):
            continue
        reached = [t["mfe_r"] >= plans[name][id(t)]
                   for t in usable if id(t) in plans[name]]
        print(f"  {name:<24}{len(values):>8}{np.median(values):>10.2f}"
              f"{np.percentile(values, 25):>7.2f}{np.percentile(values, 75):>7.2f}"
              f"{100 * np.mean(reached):>8.0f}%")

    base = book(usable, {})
    print(f"\n  half out at the target, runner trails as usual")
    print(f"  {'rule':<24}{'n':>5}{'splits':>8}{'win%':>8}{'net Rs':>11}"
          f"{'vs trail':>11}{'maxDD':>10}")
    print(f"  {'trail only':<24}{base['n']:>5}{base['splits']:>8}"
          f"{base['win']:>8.1f}{base['net']:>11,.0f}{0:>11,.0f}{base['dd']:>10,.0f}")
    for name in NAMES:
        if not plans[name]:
            continue
        result = book(usable, plans[name])
        print(f"  {name:<24}{result['n']:>5}{result['splits']:>8}"
              f"{result['win']:>8.1f}{result['net']:>11,.0f}"
              f"{result['net'] - base['net']:>+11,.0f}{result['dd']:>10,.0f}")

    print(f"\n  each level against its own distances, shuffled between trades")
    print(f"  {'level':<24}{'real net':>11}{'shuffled':>11}{'p05':>10}{'p95':>10}"
          f"{'better than':>13}")
    rng = np.random.default_rng(SEED)
    for name in NAMES:
        if len(plans[name]) < 8:
            continue
        real = book(usable, plans[name])["net"]
        keys = list(plans[name])
        values = np.array([plans[name][key] for key in keys])
        draws = np.array([book(usable, dict(zip(keys, values[rng.permutation(len(values))])))["net"]
                          for _ in range(SHUFFLES)])
        print(f"  {name:<24}{real:>11,.0f}{np.median(draws):>11,.0f}"
              f"{np.percentile(draws, 5):>10,.0f}{np.percentile(draws, 95):>10,.0f}"
              f"{100 * (real > draws).mean():>12.0f}%", flush=True)


if __name__ == "__main__":
    main()
