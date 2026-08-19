"""Does the put verdict survive the repaired cache?

`chart_reader_hurdle.py` concluded that puts need 122.3% of reads correct --
no read is good enough -- and that conclusion shipped to the user. It was
measured on a cache with a known Sep 2025 - May 2026 hole, and the ATM-1 leg
now landing fills exactly that gap. A verdict that only holds on the half of
the sample that happened to download is not a verdict, so this re-runs it and
splits by month to see whether the answer was ever coverage-driven.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from premove_legs import load, run_symbol  # noqa: E402
from chart_reader_hurdle import scan_entries  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    entries = scan_entries()
    opts = load("PUT")
    print("{:,} PUT bars, {} symbols".format(len(opts), opts["symbol"].nunique()))

    rows = []
    shared = sorted(set(opts["symbol"].unique()) & set(entries["symbol"].unique()))
    for symbol in shared:
        want = entries[entries["symbol"] == symbol]
        flags = want.set_index(want["entry"].dt.date)[["went_dn"]].to_dict("index")
        for trade in run_symbol(opts[opts["symbol"] == symbol],
                                set(want["entry"].dt.date), "PUT"):
            f = flags.get(trade["day"])
            if f is None:
                continue
            trade.update({"symbol": symbol, "went_dn": f["went_dn"]})
            rows.append(trade)
    t = pd.DataFrame(rows).dropna(subset=["hold"])
    t["ret"] = t["hold"] / t["cost"]
    t["day"] = pd.to_datetime(t["day"])
    t["month"] = t["day"].dt.to_period("M")
    print("{:,} put trades priced, {} symbols, {} sessions".format(
        len(t), t["symbol"].nunique(), t["day"].nunique()))

    def hurdle(v):
        r, w = v[v["went_dn"]], v[~v["went_dn"]]
        if len(r) < 25 or len(w) < 25:
            return "thin", np.nan
        lr, lw = (r["ret"] < 0).mean(), (w["ret"] < 0).mean()
        if lw <= max(0.5, lr):
            return "unreachable", r["ret"].median() * 100
        need = (lw - 0.5) / (lw - lr)
        return ("{:.1%}".format(need) if need <= 1 else "unreachable"), r["ret"].median() * 100

    need, med = hurdle(t)
    print("\nWHOLE SAMPLE: break-even {}   median if right {:+.1f}%".format(need, med))
    print("  median trade {:+.1f}%   win {:.1f}%   mean {:+.1f}% (fat tail, ignore)".format(
        t["ret"].median() * 100, (t["ret"] > 0).mean() * 100, t["ret"].mean() * 100))

    print("\n  {:<9} {:>7} {:>14} {:>18}".format("month", "trades", "break-even", "median if right"))
    print("  " + "-" * 51)
    for month, chunk in t.groupby("month"):
        need, med = hurdle(chunk)
        print("  {:<9} {:>7,} {:>14} {:>18}".format(
            str(month), len(chunk), need, "n/a" if med != med else "{:+.1f}%".format(med)))

    old = t[t["month"] < "2025-09"]
    new = t[(t["month"] >= "2025-09") & (t["month"] <= "2026-05")]
    print("\n  the months that WERE the hole, scored on their own:")
    for tag, chunk in [("outside the hole", old), ("inside the hole", new)]:
        need, med = hurdle(chunk)
        print("    {:<18} {:>5,} trades  break-even {}".format(tag, len(chunk), need))


if __name__ == "__main__":
    main()
