"""Two questions about *when* the shipped strategy is allowed to trade.

  expiry days   The docstring says they are excluded. Nothing in StrategyConfig
                excludes them, so the first job is to check whether that claim is
                true of the code or only of an old test. Then measure what expiry
                sessions actually contributed under the shipped rules -- not the
                generic first-touch race in `expiry_anatomy.py`, which asked a
                different question (any strike, any minute, fixed multiples).

  the late run  The window already runs to 15:09, so late entries are not banned.
                The question is whether they pay, and whether the Rs 100 floor is
                quietly throwing away the cheap late premium that a buyer wants.

Both are answered from the band pickle written by `finalise.py`, so this costs no
contract load. Section 4 needs fresh backtests and says so before it runs.
"""
import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime, time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

import common as C
from options_tracker.nifty_trail_strategy import sized_ledger

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "cache", "finalise_bands.pkl")
SHIPPED = (100, 1000)
WITH_CHEAP = (50, 250)


def naive(text):
    return datetime.fromisoformat(text).replace(tzinfo=None)


def pts(trade):
    return trade["realized_r"] * (trade["entry"] - trade["stop_loss"])


def summarise(trades):
    """Rupees need the sized ledger; points and win rate do not."""
    if not trades:
        return {"n": 0, "win": 0.0, "pts": 0.0, "net": 0.0, "total_pts": 0.0}
    ledger, _skipped, _dd = sized_ledger(trades)
    wins = sum(1 for t in trades if t["realized_r"] > 0)
    return {
        "n": len(trades),
        "win": 100 * wins / len(trades),
        "pts": float(np.mean([pts(t) for t in trades])),
        "total_pts": float(np.sum([pts(t) for t in trades])),
        "net": sum(row["net_pnl"] for row in ledger),
    }


def row(label, trades, width=34):
    result = summarise(trades)
    print(f"  {label:<{width}}{result['n']:>5}{result['win']:>8.1f}"
          f"{result['pts']:>11.2f}{result['total_pts']:>11.1f}"
          f"{result['net']:>12,.0f}")
    return result


def header(width=34):
    print(f"  {'':<{width}}{'n':>5}{'win%':>8}{'pts/trade':>11}{'pts':>11}"
          f"{'net Rs':>12}")


def bucket_of(stamp):
    """Half-hour bucket, except the tail which is one 14:30-15:09 block."""
    minutes = stamp.hour * 60 + stamp.minute
    if minutes >= 14 * 60 + 30:
        return "14:30-15:09"
    start = minutes - minutes % 30
    return f"{start // 60:02d}:{start % 60:02d}-{(start + 30) // 60:02d}:{(start + 30) % 60:02d}"


def main():
    with open(CACHE, "rb") as handle:
        kept = pickle.load(handle)
    shipped = kept[SHIPPED]
    cheap = kept[WITH_CHEAP]

    dates = C.session_dates()
    expiries = C.expiry_dates(dates)

    print("=" * 92)
    print("1. ARE EXPIRY DAYS ACTUALLY EXCLUDED?")
    print("=" * 92)
    print(f"  {len(dates)} sessions in the cache, {len(expiries)} of them expiry"
          " sessions.")
    traded_days = {t["date"] for t in shipped}
    on_expiry = sorted(traded_days & expiries)
    print(f"  The shipped config traded on {len(traded_days)} distinct days, of"
          f" which {len(on_expiry)} were expiry days.")
    if on_expiry:
        print("  -> NOT excluded. StrategyConfig has no expiry field; the"
              " exclusion was never coded.")
        print(f"     Expiry sessions traded: {', '.join(on_expiry)}")
    else:
        print("  -> Excluded in effect. No expiry-day signal ever passed the"
              " filters.")

    print("\n" + "=" * 92)
    print("2. WHAT DID EXPIRY DAYS CONTRIBUTE, UNDER THE SHIPPED RULES?")
    print("=" * 92)
    print("  Points are premium points per contract; net is the compounded"
          " ledger for that subset alone.\n")
    header()
    for label, keep in (("all 246 sessions", lambda d: True),
                        ("normal sessions only", lambda d: d not in expiries),
                        ("expiry sessions only", lambda d: d in expiries)):
        row(label, [t for t in shipped if keep(t["date"])])

    print("\n  Same split on the wider Rs 50-250 band, which has more trades to"
          " look at:\n")
    header()
    for label, keep in (("all 246 sessions", lambda d: True),
                        ("normal sessions only", lambda d: d not in expiries),
                        ("expiry sessions only", lambda d: d in expiries)):
        row(label, [t for t in cheap if keep(t["date"])])

    expiry_trades = [t for t in cheap if t["date"] in expiries]
    if expiry_trades:
        print("\n  Every expiry-day trade the Rs 50-250 band took, in order:\n")
        print(f"  {'date':<13}{'time':>7}{'type':>6}{'entry':>9}{'exit':>9}"
              f"{'R':>8}{'points':>9}  reason")
        for trade in sorted(expiry_trades, key=lambda t: t["signal_at"]):
            risk = trade["entry"] - trade["stop_loss"]
            exit_price = trade["entry"] + trade["realized_r"] * risk
            stamp = naive(trade["signal_at"])
            print(f"  {trade['date']:<13}{stamp.strftime('%H:%M'):>7}"
                  f"{trade['option_type'][:4]:>6}{trade['entry']:>9.2f}"
                  f"{exit_price:>9.2f}{trade['realized_r']:>8.2f}"
                  f"{pts(trade):>9.1f}  {trade.get('exit_reason', '?')}")

    print("\n" + "=" * 92)
    print("3. THE LATE SESSION: DOES 14:30-15:09 PAY?")
    print("=" * 92)
    print("  The window already runs to 15:09, so these trades are being taken"
          " today.\n")
    for name, trades in (("shipped Rs 100+", shipped), ("Rs 50-250", cheap)):
        print(f"  {name}:")
        header()
        buckets = defaultdict(list)
        for trade in trades:
            buckets[bucket_of(naive(trade["signal_at"]))].append(trade)
        for key in sorted(buckets):
            row(key, buckets[key])
        print()

    print("  How long do trades need? Minutes from entry to exit, and how many"
          " ran into the 15:20 bell:\n")
    print(f"  {'entry bucket':<16}{'n':>5}{'med mins':>10}{'timed out':>12}"
          f"{'stopped':>10}{'trailed':>10}")
    buckets = defaultdict(list)
    for trade in shipped:
        buckets[bucket_of(naive(trade["signal_at"]))].append(trade)
    for key in sorted(buckets):
        group = buckets[key]
        held = [(naive(t["exit_at"]) - naive(t["signal_at"])).total_seconds() / 60
                for t in group]
        reasons = [t.get("exit_reason", "?") for t in group]
        print(f"  {key:<16}{len(group):>5}{np.median(held):>10.0f}"
              f"{sum(1 for r in reasons if 'time' in r.lower()):>12}"
              f"{sum(1 for r in reasons if 'stop' in r.lower()):>10}"
              f"{sum(1 for r in reasons if 'trail' in r.lower()):>10}")

    print("\n" + "=" * 92)
    print("4. IS THE Rs 100 FLOOR THROWING AWAY CHEAP LATE PREMIUM?")
    print("=" * 92)
    print("  The claim to test: premium is lower late in the day, so the floor"
          " bites harder there.\n")
    print(f"  {'entry bucket':<16}{'n (50-250)':>12}{'med premium':>14}"
          f"{'below 100':>12}{'their pts':>12}{'their win%':>12}")
    buckets = defaultdict(list)
    for trade in cheap:
        buckets[bucket_of(naive(trade["signal_at"]))].append(trade)
    for key in sorted(buckets):
        group = buckets[key]
        below = [t for t in group if t["entry"] < 100]
        share = f"{len(below)}"
        wins = (100 * sum(1 for t in below if t["realized_r"] > 0) / len(below)
                if below else float("nan"))
        mean_pts = float(np.mean([pts(t) for t in below])) if below else float("nan")
        print(f"  {key:<16}{len(group):>12}"
              f"{np.median([t['entry'] for t in group]):>14.0f}{share:>12}"
              f"{mean_pts:>12.2f}{wins:>11.1f}%")

    print("\n  And the same question the other way round -- is a cheap contract"
          " cheap because\n  the day is nearly over, or because the option was"
          " always far from the money?\n")
    print(f"  {'':<24}{'n':>5}{'med premium':>14}{'med minutes held':>19}")
    for label, subset in (
            ("below Rs 100", [t for t in cheap if t["entry"] < 100]),
            ("Rs 100 and above", [t for t in cheap if t["entry"] >= 100])):
        if not subset:
            continue
        held = [(naive(t["exit_at"]) - naive(t["signal_at"])).total_seconds() / 60
                for t in subset]
        print(f"  {label:<24}{len(subset):>5}"
              f"{np.median([t['entry'] for t in subset]):>14.0f}"
              f"{np.median(held):>19.0f}")


if __name__ == "__main__":
    main()
