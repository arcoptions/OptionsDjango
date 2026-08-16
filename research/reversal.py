"""What to do after a stop: turn around, or get back in?

The overnight question was whether to reverse -- stopped out of a call, buy a put
and sell it. The hunt diagnostic already argues against it: after a stop the
index kept moving against us into the close only 28% of the time, and the option
we were just stopped out of traded back above our entry 72% of the time. If that
holds, a reversal is a trade taken into a move that has already finished, and the
profitable follow-up is the opposite one -- buying back what we were shaken out
of.

Both are tested here on the same 18 stop events, with the same 10% stop and 0.7R
trail as the parent strategy, entered on the bar after the stop at the same 0.5%
slippage the entry rule assumes. Three follow-ups:

  reverse             at-the-money option of the opposite type
  re-enter            the very contract we were just stopped out of
  re-enter on reclaim the same contract, but only once the premium trades back
                      through our original entry, which is the confirmation that
                      the shakeout is over rather than a guess that it is

Standalone statistics come first, because a follow-up that does not win on its
own has nothing to contribute to the ledger no matter how it is sized. The
combined ledger then compounds parent and follow-up trades together in time
order, so the follow-ups compete for the same capital.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from datetime import datetime

from options_tracker import strategy_backtest as SB
from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger
from options_tracker.strategy_backtest import backtest_strategy

import common as C
from stop_hunt import ORIGINAL_SIMULATE, make_simulate

TRAIL = 0.7
STOP_PERCENT = 0.10
SLIPPAGE = 1.005
EXIT_MINUTE = 365  # 15:20 IST, the parent strategy's flat-by time
RECLAIM_WINDOW = 30  # minutes to wait for the premium to come back


def minute_of(stamp):
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    return stamp.hour * 60 + stamp.minute - 555


def simulate_leg(data, side, strike_index, start, trail_gap=TRAIL):
    """Buy at the open of `start` and run the parent strategy's exit."""
    opens = data["o"][side, strike_index]
    highs = data["h"][side, strike_index]
    lows = data["l"][side, strike_index]
    closes = data["c"][side, strike_index]
    last = min(EXIT_MINUTE, opens.shape[0] - 1)
    if start > last or not np.isfinite(opens[start]) or opens[start] <= 0:
        return None
    entry = round(float(opens[start]) * SLIPPAGE, 2)
    stop = round(entry * (1 - STOP_PERCENT), 2)
    risk = entry - stop
    if risk <= 0:
        return None
    high_water, exit_price, outcome = entry, None, "TIME_EXIT"
    for index in range(start, last + 1):
        low, high = float(lows[index]), float(highs[index])
        if not np.isfinite(low) or not np.isfinite(high):
            continue
        if low <= stop:
            outcome = "TRAIL_EXIT" if stop > entry else "STOP"
            exit_price = stop
            break
        high_water = max(high_water, high)
        if (high_water - entry) / risk >= trail_gap:
            stop = max(stop, round(high_water - risk * trail_gap, 2))
    if exit_price is None:
        final = closes[last]
        if not np.isfinite(final):
            return None
        exit_price = float(final) * 0.995
    return {"entry": entry, "stop_loss": stop if exit_price == stop else round(entry * (1 - STOP_PERCENT), 2),
            "initial_stop": round(entry * (1 - STOP_PERCENT), 2),
            "exit_price": exit_price, "outcome": outcome,
            "realized_r": round((exit_price - entry) / risk, 3)}


def follow_ups(parent):
    """Build the three candidate follow-up trades for one stopped-out parent."""
    date = parent["date"]
    try:
        data = C.load(date)
    except OSError:
        return {}
    start = minute_of(parent["exit_at"]) + 1
    if start < 0 or start > EXIT_MINUTE:
        return {}
    strikes = np.asarray(data["strikes"], dtype=float)
    spot = np.asarray(data["spot"], dtype=float)
    if start >= len(spot) or not np.isfinite(spot[start]):
        return {}
    call = parent["option_type"] == "CALL"

    out = {}
    opposite = 1 if call else 0
    atm = int(np.argmin(np.abs(strikes - spot[start])))
    leg = simulate_leg(data, opposite, atm, start)
    if leg:
        out["reverse"] = {**leg, "option_type": "PUT" if call else "CALL",
                          "strike": float(strikes[atm])}

    same = 0 if call else 1
    matches = np.where(np.isclose(strikes, float(parent["strike"])))[0]
    if len(matches):
        index = int(matches[0])
        leg = simulate_leg(data, same, index, start)
        if leg:
            out["re-enter"] = {**leg, "option_type": parent["option_type"],
                               "strike": float(parent["strike"])}
        # Reclaim: wait for the premium to trade back through the original entry
        # before paying up for it again.
        highs = data["h"][same, index]
        limit = min(EXIT_MINUTE, len(highs) - 1)
        for minute in range(start, min(start + RECLAIM_WINDOW, limit)):
            if np.isfinite(highs[minute]) and highs[minute] >= parent["entry"]:
                leg = simulate_leg(data, same, index, minute + 1)
                if leg:
                    out["re-enter on reclaim"] = {
                        **leg, "option_type": parent["option_type"],
                        "strike": float(parent["strike"]),
                        "waited": minute + 1 - start}
                break
    return out


def summarise(name, legs):
    if not legs:
        print(f"  {name:<24}  (no trades)")
        return
    values = np.array([leg["realized_r"] for leg in legs])
    wins = (values > 0).mean()
    print(f"  {name:<24}{len(values):>7}{100 * wins:>9.1f}{values.mean():>10.2f}"
          f"{np.median(values):>10.2f}{values.sum():>10.2f}")


def random_control(stopped, draws=400, seed=20260815):
    """The same reversal trades, entered at a random minute on the same day.

    A reversal buys a particular option type on a particular day. Some of that
    day's move would have paid for that option whenever it was bought, so the
    question is not whether the reversal made money but whether *the moment of
    the stop* was the reason. Randomising only the minute holds the day, the
    direction and the strike selection rule fixed and varies nothing else.
    """
    rng = np.random.default_rng(seed)
    cached = []
    for parent in stopped:
        try:
            data = C.load(parent["date"])
        except OSError:
            continue
        strikes = np.asarray(data["strikes"], dtype=float)
        spot = np.asarray(data["spot"], dtype=float)
        side = 1 if parent["option_type"] == "CALL" else 0
        cached.append((data, strikes, spot, side))
    totals = []
    for _ in range(draws):
        total = 0.0
        for data, strikes, spot, side in cached:
            latest = min(EXIT_MINUTE, len(spot) - 1) - 10
            if latest <= 15:
                continue
            for _attempt in range(6):
                start = int(rng.integers(15, latest))
                if not np.isfinite(spot[start]):
                    continue
                atm = int(np.argmin(np.abs(strikes - spot[start])))
                leg = simulate_leg(data, side, atm, start)
                if leg:
                    total += leg["realized_r"]
                    break
        totals.append(total)
    return np.array(totals)


def main():
    SB._simulate = make_simulate(TRAIL)
    try:
        parents = backtest_strategy("NIFTY", 1, nifty_trail_config())
    finally:
        SB._simulate = ORIGINAL_SIMULATE
    stopped = [t for t in parents if t["outcome"] == "STOP"]
    print(f"{len(parents)} parent trades at a {TRAIL}R trail, {len(stopped)} "
          f"stopped out\n", flush=True)

    collected = {"reverse": [], "re-enter": [], "re-enter on reclaim": []}
    origin = {}
    for parent in stopped:
        for name, leg in follow_ups(parent).items():
            collected[name].append({**leg, "date": parent["date"],
                                    "signal_at": parent["exit_at"],
                                    "parent_entry": parent["entry"]})
            if name == "reverse":
                origin[len(collected[name]) - 1] = parent

    print(f"  {'follow-up, on its own':<24}{'n':>7}{'win%':>9}{'mean R':>10}"
          f"{'median':>10}{'total R':>10}")
    for name in ("reverse", "re-enter", "re-enter on reclaim"):
        summarise(name, collected[name])

    waits = [leg["waited"] for leg in collected["re-enter on reclaim"] if "waited" in leg]
    if waits:
        print(f"\n  the reclaim came {np.median(waits):.0f} minutes after the stop "
              f"on median (n={len(waits)} of {len(stopped)} stops reclaimed within "
              f"{RECLAIM_WINDOW} minutes)")

    base_ledger, _skipped, base_dd = sized_ledger(parents)
    base_net = sum(row["net_pnl"] for row in base_ledger)
    print(f"\n  combined ledger at Rs 1,00,000, follow-ups competing for the same "
          f"capital")
    print(f"  {'book':<28}{'n':>6}{'win%':>8}{'net Rs':>11}{'vs parent':>12}"
          f"{'maxDD':>10}")
    print(f"  {'parents only':<28}{len(base_ledger):>6}"
          f"{100 * sum(1 for r in base_ledger if r['net_pnl'] > 0) / len(base_ledger):>8.1f}"
          f"{base_net:>11,.0f}{0:>12,.0f}{base_dd:>10,.0f}")
    for name in ("reverse", "re-enter", "re-enter on reclaim"):
        legs = collected[name]
        if not legs:
            continue
        merged = parents + [{**leg, "stop_loss": leg["initial_stop"]} for leg in legs]
        ledger, _skipped, drawdown = sized_ledger(merged)
        net = sum(row["net_pnl"] for row in ledger)
        wins = sum(1 for row in ledger if row["net_pnl"] > 0)
        print(f"  {'parents + ' + name:<28}{len(ledger):>6}"
              f"{100 * wins / len(ledger):>8.1f}{net:>11,.0f}"
              f"{net - base_net:>+12,.0f}{drawdown:>10,.0f}")

    print(f"\n  zero-skill control: same option type, same day, random entry minute",
          flush=True)
    real = sum(leg["realized_r"] for leg in collected["reverse"])
    draws = random_control(stopped)
    print(f"  reversal total {real:+.2f}R  vs random median {np.median(draws):+.2f}R"
          f"  p05 {np.percentile(draws, 5):+.2f}  p95 {np.percentile(draws, 95):+.2f}")
    print(f"  the real timing beat {100 * (real > draws).mean():.0f}% of "
          f"{len(draws)} random-minute books")

    print(f"\n  does it matter whether the stop was a real move or just decay?")
    print(f"  {'parent stop':<28}{'n':>6}{'win%':>9}{'mean R':>10}{'total R':>10}")
    for label, keep in (("index moved >25 pts against", lambda m: m <= -25),
                        ("index moved 10-25 pts", lambda m: -25 < m <= -10),
                        ("index barely moved (>-10)", lambda m: m > -10)):
        subset = [leg for index, leg in enumerate(collected["reverse"])
                  if index in origin
                  and origin[index]["stop_spot_move"] is not None
                  and np.isfinite(origin[index]["stop_spot_move"])
                  and keep(origin[index]["stop_spot_move"])]
        if not subset:
            print(f"  {label:<28}{0:>6}")
            continue
        values = np.array([leg["realized_r"] for leg in subset])
        print(f"  {label:<28}{len(values):>6}{100 * (values > 0).mean():>9.1f}"
              f"{values.mean():>10.2f}{values.sum():>10.2f}")


if __name__ == "__main__":
    main()
