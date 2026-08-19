"""Page-ready aggregates for the spread study, derived from what it already wrote.

The two spread scripts print their tables and dump raw trades; the `/research/`
page wants small pre-aggregated CSVs it can render without re-reading a 146 MB
trade file on every request. This derives those from the artefacts already on
disk, so it costs seconds rather than the ~20 minutes a re-run of either study
takes, and it cannot disagree with them -- it reads their own output.

Writes `spread_dte.csv` (the theta gradient, quoted vs repaired) and
`spread_exits.csv` (near-expiry exits, quoted vs repaired).
"""

import os
import sys

import django
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

# Imported rather than restated: this file exists to agree with the studies.
from spread_near_expiry import CACHE, EXITS  # noqa: E402

BUCKETS = [(0, 7, "0-7 days"), (8, 14, "8-14 days"),
           (15, 21, "15-21 days"), (22, 60, "22+ days")]
COLS = ["dte", "risk", "day", "5d", "i_5d"]


def log(msg):
    print(msg, flush=True)


def roi(frame, col):
    v = frame.dropna(subset=[col])
    if v.empty:
        return np.nan, 0, np.nan
    # sum/sum, never a mean of ratios: one tiny-risk trade must not dominate.
    return (v[col].sum() / v.risk.sum() * 100, len(v),
            (v[col] > 0).mean() * 100)


def dte_table():
    """The gradient that sent the study near-expiry, and what repair does to it."""
    frame = pd.read_csv(os.path.join(HERE, "spread_trades.csv"), usecols=COLS)
    rows = []
    for lo, hi, name in BUCKETS:
        b = frame[(frame.dte >= lo) & (frame.dte <= hi)]
        q, qn, _ = roi(b, "5d")
        i, inn, win = roi(b, "i_5d")
        if inn < 100:
            continue
        rows.append({"bucket": name, "quoted": q, "imputed": i, "win": win,
                     "quoted_n": qn, "n": inn, "quoted_pct": 100 * qn / inn})
        log("  {:<12} quoted {:>+8.2f}%   imputed {:>+8.2f}%   quoted {:>5.1f}% of {:,}"
            .format(name, q, i, 100 * qn / inn, inn))
    return pd.DataFrame(rows)


def exit_table():
    """Every near-expiry exit priced both ways, on all 0-7 DTE entries."""
    near = pd.read_pickle(CACHE)
    near = near[near.dte <= 7]
    rows = []
    for col, label in EXITS:
        q, qn, _ = roi(near, col)
        i, inn, win = roi(near, "i_" + col)
        if not inn:
            continue
        rows.append({"exit": label, "quoted": q, "imputed": i, "win": win,
                     "quoted_n": qn, "n": inn, "quoted_pct": 100 * qn / max(inn, 1),
                     "p5": (near["i_" + col] / near.risk).quantile(0.05) * 100})
        log("  {:<26} quoted {:>+8.2f}%   imputed {:>+8.2f}%   quoted {:>5.1f}% of {:,}"
            .format(label, q, i, 100 * qn / max(inn, 1), inn))
    return pd.DataFrame(rows)


def main():
    log("BY DAYS TO EXPIRY, held 5 sessions:")
    dte = dte_table()
    log("\nNEAR-EXPIRY EXITS, 0-7 DTE, every entry (not deduplicated):")
    exits = exit_table()

    cycles = pd.read_csv(os.path.join(HERE, "spread_cycles.csv"))
    log("\ncycles: {}/{} profitable imputed, {}/{} quoted-only".format(
        int((cycles.roi > 0).sum()), len(cycles),
        int((cycles.quoted > 0).sum()), len(cycles)))

    dte.to_csv(os.path.join(HERE, "spread_dte.csv"), index=False)
    exits.to_csv(os.path.join(HERE, "spread_exits.csv"), index=False)
    log("written: spread_dte.csv, spread_exits.csv")


if __name__ == "__main__":
    main()
