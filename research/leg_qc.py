"""Does a freshly downloaded option leg actually contain what its label says?

Five traps have already invalidated conclusions on this feed, three of them
silently: stale five-paise quotes on contracts that never really traded, a
`relative_strike` label that collapses the whole cache to at-the-money,
corporate actions that re-base spot without re-basing the strike, and premiums
so small that friction rather than direction sets the outcome.

Every one of those is cheap to test and expensive to miss, so this runs before
any leg is analysed rather than after a result looks surprising. It answers one
question -- is this leg safe to draw a conclusion from -- and prints the
evidence rather than a verdict.

    python research/leg_qc.py PUT ATM-1
"""
from __future__ import annotations

import os
import sys

import django
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import StockOptionCandle  # noqa: E402

# A put labelled ATM-1 must sit BELOW spot and a call labelled ATM+1 above it,
# so the expected sign of (strike - spot) is read off the label, not assumed.
RUNG = {"ATM": 0, "ATM+1": 1, "ATM+2": 2, "ATM+3": 3, "ATM-1": -1, "ATM-2": -2}


def main() -> None:
    side = sys.argv[1] if len(sys.argv) > 1 else "PUT"
    rung = sys.argv[2] if len(sys.argv) > 2 else "ATM-1"

    rows = StockOptionCandle.objects.filter(
        option_type=side, relative_strike=rung).values(
        "symbol", "timestamp", "strike", "close", "spot", "volume")
    frame = pd.DataFrame.from_records(rows.iterator(chunk_size=50_000))
    if frame.empty:
        print("no rows for {} {} -- nothing to check".format(side, rung))
        return
    for column in ("strike", "close", "spot"):
        frame[column] = frame[column].astype(float)

    print("=" * 72)
    print("{} {}: {:,} bars, {} symbols".format(
        side, rung, len(frame), frame["symbol"].nunique()))
    print("=" * 72)

    frame["month"] = pd.to_datetime(
        frame["timestamp"], utc=True).dt.tz_convert(None).dt.to_period("M")
    months = frame.groupby("month").size()
    print("\ncoverage: {} months, thinnest {} at {:,} bars".format(
        len(months), str(months.idxmin()), months.min()))
    empty = [str(m) for m, n in months.items() if n < 500]
    print("  months under 500 bars: {}".format(", ".join(empty) if empty else "none"))

    # Trap: a contract that never traded still returns bars, pinned at the tick.
    # Those quotes are unfillable, and they are not spread evenly -- they cluster
    # exactly where the option is worthless, which is where losses live.
    print("\nquote quality")
    print("  bars at or under Rs0.05 (stale):     {:.2%}".format(
        (frame["close"] <= 0.05).mean()))
    print("  bars under Rs0.50 (friction zone):   {:.2%}".format(
        (frame["close"] < 0.50).mean()))
    print("  zero-volume bars:                    {:.2%}".format(
        (frame["volume"] == 0).mean()))
    print("  premium: median Rs{:.2f}, p10 Rs{:.2f}, p90 Rs{:.2f}".format(
        frame["close"].median(), frame["close"].quantile(0.10),
        frame["close"].quantile(0.90)))

    # Trap: the label is written by the downloader, not derived from the data,
    # so it can be wrong in a way nothing downstream would notice.
    priced = frame[frame["spot"] > 0].dropna(subset=["spot"])
    print("\nis the strike where the label says? (n={:,})".format(len(priced)))
    if priced.empty:
        print("  spot is not populated -- placement cannot be verified here")
        return
    rel = (priced["strike"] - priced["spot"]) / priced["spot"]
    print("  (strike-spot)/spot: median {:+.2%}, p10 {:+.2%}, p90 {:+.2%}".format(
        rel.median(), rel.quantile(0.10), rel.quantile(0.90)))
    print("  collapsed to ATM (within +/-0.25% of spot): {:.1%}".format(
        (rel.abs() < 0.0025).mean()))
    want = RUNG.get(rung)
    if want:
        wrong = (rel > 0).mean() if want < 0 else (rel < 0).mean()
        print("  on the WRONG side of spot for {}: {:.1%}".format(rung, wrong))
        print("\n  {}".format(
            "PASS -- the leg is where its label claims"
            if wrong < 0.05 and (rel.abs() < 0.0025).mean() < 0.05
            else "SUSPECT -- placement does not match the label, do not analyse yet"))


if __name__ == "__main__":
    main()
