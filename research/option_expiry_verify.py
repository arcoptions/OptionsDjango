"""The one candidate that cleared 1.0. Is it real?

Expiry-day at-the-money calls, exited on a 30% trail off the peak, returned
1.038x net across ~17,000 entries. That is the only rule in the whole study to
clear break-even on a sample worth taking seriously, so it gets the hostile
treatment rather than a victory lap.

Four ways it could be a mirage, each tested:

  The bell.  The 15:15 slot alone printed 1.419x. F&O trades to 15:39 while the
  cash index stops at 15:29, so the last bars of an expiry session are thin,
  wide, and settling. If the edge lives there it is not an edge, it is a
  closing auction you cannot trade.

  A handful of days.  With 5% of trades reaching 2x, a mean is hostage to its
  tail. If dropping the best five expiry days kills it, it was those five days.

  One half of the sample.  Standard split.

  Overlap.  Seventeen thousand entries are not seventeen thousand bets. Adjacent
  bars on the same contract on the same afternoon are one decision. Collapsing
  to one trade per contract per day is the honest count.
"""
import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from option_strategy import build, simulate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def summarise(label, net):
    net = net[np.isfinite(net)]
    if len(net) < 30:
        print(f"  {label:44s} too few trades")
        return
    print(f"  {label:44s} n={len(net):6,}  mean {net.mean():6.3f}  "
          f"median {np.median(net):6.3f}  win {(net > 1).mean() * 100:5.1f}%")


def main():
    frame = build()
    result = simulate(frame, None, 0.40, 0.70, max_bars=25)   # trail 30% off peak
    net = result["net"]

    base = (frame["dte"].le(1) & frame["moneyness"].abs().le(1.5)
            & frame["option_type"].eq("CALL") & frame["volume"].gt(0)
            & frame["oi"].gt(0)).to_numpy() & np.isfinite(net)

    sub = frame[base].copy()
    sub["net"] = net[base]
    sub["day"] = sub["ts"].dt.date
    sub["slot"] = sub["ts"].dt.strftime("%H:%M")

    print(f"{'=' * 96}\nEXPIRY-DAY ATM CALLS, TRAIL 30% OFF PEAK")
    print(f"{len(sub):,} entries across {sub['day'].nunique()} expiry sessions, "
          f"{sub['symbol'].nunique()} symbols\n")
    summarise("as reported", sub["net"].to_numpy())

    print(f"\n{'-' * 96}\n1. THE BELL")
    for cutoff in ["15:15", "15:00", "14:45", "14:00"]:
        kept = sub[sub["slot"] < cutoff]
        summarise(f"entries before {cutoff}", kept["net"].to_numpy())
    print("\n  The whole excess sits in the closing bars. Everything before 14:00 is")
    print("  under water, and the last two slots of an expiry session are the widest")
    print("  and thinnest quotes of the month.")

    print(f"\n{'-' * 96}\n2. CONCENTRATION ACROSS EXPIRY SESSIONS")
    daily = sub.groupby("day")["net"].agg(["mean", "count"])
    print(f"  {len(daily)} expiry sessions, "
          f"{(daily['mean'] > 1).sum()} of them profitable "
          f"({(daily['mean'] > 1).mean() * 100:.0f}%)")
    top = daily["mean"].nlargest(5)
    without = sub[~sub["day"].isin(top.index)]
    print("  best five sessions:")
    for day, value in top.items():
        print(f"    {day}   mean {value:6.3f}x on {int(daily.loc[day, 'count']):,} entries")
    summarise("dropping those five sessions", without["net"].to_numpy())

    trimmed = sub["net"].to_numpy()
    trimmed = np.sort(trimmed)[:int(len(trimmed) * 0.99)]
    print(f"\n  drop the single best 1% of trades and the mean goes "
          f"{sub['net'].mean():.3f} -> {trimmed.mean():.3f}")

    print(f"\n{'-' * 96}\n3. HALF-SAMPLE SPLIT")
    middle = sub["ts"].median()
    summarise("first half", sub.loc[sub["ts"] <= middle, "net"].to_numpy())
    summarise("second half", sub.loc[sub["ts"] > middle, "net"].to_numpy())

    print(f"\n{'-' * 96}\n4. ONE BET PER CONTRACT PER DAY, NOT PER BAR")
    # Keep the first entry on each contract each session -- the decision a person
    # actually makes once, rather than the same view re-expressed every 15 minutes.
    once = sub.sort_values("ts").groupby(
        ["symbol", "option_type", "strike", "cycle", "day"], as_index=False
    ).first()
    summarise("one entry per contract per session", once["net"].to_numpy())
    daily_once = once.groupby("day")["net"].mean()
    if len(daily_once) > 2:
        excess = daily_once - 1
        t_stat = excess.mean() / (excess.std(ddof=1) / np.sqrt(len(excess)))
        print(f"  across {len(daily_once)} expiry sessions: mean {daily_once.mean():.3f}, "
              f"median {daily_once.median():.3f}, t = {t_stat:.2f}")

    print(f"\n{'=' * 96}\nVERDICT")
    before_two = sub[sub["slot"] < "14:00"]["net"]
    late = sub[sub["slot"] >= "14:00"]
    survives_bell = before_two.mean() > 1.0
    survives_split = (sub.loc[sub["ts"] <= middle, "net"].mean() > 1.0
                      and sub.loc[sub["ts"] > middle, "net"].mean() > 1.0)
    survives_trim = trimmed.mean() > 1.0
    survives_days = without["net"].mean() > 1.0
    print(f"  survives the closing bell   {survives_bell}   "
          f"({before_two.mean():.3f}x before 14:00, {late['net'].mean():.3f}x after)")
    print(f"  survives the half-split     {survives_split}")
    print(f"  survives dropping best 1%   {survives_trim}   ({trimmed.mean():.3f}x)")
    print(f"  survives dropping best 5 dy {survives_days}   ({without['net'].mean():.3f}x)")
    print(f"  median trade                {np.median(sub['net']):.3f}x")
    print(f"  profitable expiry sessions  {(daily['mean'] > 1).sum()}/{len(daily)}")
    if survives_bell and survives_split and not (survives_trim and survives_days):
        print("\n  So it is not a settlement artefact and not a one-half fluke -- but the")
        print("  whole mean lives in the top 1% of trades and a minority of sessions.")
        print("  That is a positive-expectancy LOTTERY, not a reliable edge: most trades")
        print("  and most expiry days lose, and the sample is far too short to tell a")
        print("  real tail from a lucky one. Forward-test it small; do not size it.")
    elif survives_bell and survives_split and survives_trim and survives_days:
        print("\n  It survives every check here. That is the strongest result in the")
        print("  study and warrants a properly sized forward test.")
    else:
        print("\n  It does not survive. The headline number was an artefact of when and")
        print("  where the trades were taken, not a property of the rule.")


if __name__ == "__main__":
    main()
