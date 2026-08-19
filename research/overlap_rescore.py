"""Re-score the two multi-day findings for serial-overlap inflation.

`research/breakout_study.py` turned up a defect in this project's standard
significance test. Day-clustering collapses each date to a single observation,
which removes the correlation between stocks -- but consecutive dates are still
nearly the same measurement. A 10-day forward return sampled every session
shares nine tenths of its window with the session before it, so the daily means
are autocorrelated and t is inflated by roughly the square root of the horizon.
On the breakout study that took t +4.6 down to +1.4.

Two earlier findings used multi-day forward windows sampled daily and were never
given a significance number at all -- they rested on "every fold positive" and
"frozen out-of-sample", which are the right controls for leakage but say nothing
about how much of the effect could be luck:

  1. MOTION. The 2.73x lift on a +10% touch within 5 sessions
     (`premove_predict.py` -> `premove_deconfound.py`). Horizon 5.

  2. DIRECTION. High IV rank plus EMA side, frozen on data through 2026-01-09:
     below-EMA names gap up 17.6% against 5.8% down, a 3.06 ratio, while
     above-EMA names invert to 0.79 (`side_picker.py`). Horizon 10.

Both are re-scored here three ways: pooled (wrong, treats every stock-day as
independent), day-clustered (the project's old standard), and de-overlapped
(sampling every h-th day, averaging over all h starting offsets so the phase is
not chosen arbitrarily). The gap between the second and third columns is the
inflation.

Nothing here re-fits a model. The predictions are read from the CSVs the
original runs wrote, so this measures those exact findings rather than a
re-derivation of them.

Writes research/overlap_rescore.txt.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DECONFOUND = "research/premove_deconfound.csv"
FEATURES = "research/premove_features.parquet"
OUT = "research/overlap_rescore.txt"

TOP = 0.10             # the predictor's top decile, as originally scored
MOTION_HORIZON = 5     # "+10% touch within 5 sessions"

GAP_HORIZON = 10       # "largest single-day move within 10 sessions"
GAP_THRESH = 0.06
IV_CUT = 0.70
SPLIT = 0.60           # the frozen train/test boundary, unchanged

LINES: list[str] = []


def log(message: str = "") -> None:
    print(message)
    LINES.append(message)


def naive_t(values: pd.Series) -> float:
    """Every observation treated as independent. Included to show the damage."""
    v = values.dropna()
    if len(v) < 3 or v.std(ddof=1) == 0:
        return np.nan
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def clustered_t(values: pd.Series, days: pd.Series) -> float:
    """One observation per session. Fixes stocks co-moving, not much else."""
    daily = pd.DataFrame({"v": values, "d": days}).dropna().groupby("d")["v"].mean()
    return naive_t(daily)


def overlap_safe_t(values: pd.Series, days: pd.Series, stride: int) -> float:
    """Day-clustered t on NON-OVERLAPPING samples.

    Sampling every `stride`-th session removes the serial overlap. Each of the
    `stride` possible starting offsets gives a valid estimate, so all of them
    are averaged rather than one being picked arbitrarily.
    """
    daily = pd.DataFrame({"v": values, "d": days}).dropna().groupby("d")["v"].mean()
    if len(daily) < 5 * stride:
        return np.nan
    estimates = []
    for offset in range(stride):
        sample = daily.iloc[offset::stride]
        if len(sample) < 5 or sample.std(ddof=1) == 0:
            continue
        estimates.append(sample.mean() / (sample.std(ddof=1) / np.sqrt(len(sample))))
    return float(np.mean(estimates)) if estimates else np.nan


def three_ways(name: str, values: pd.Series, days: pd.Series, stride: int) -> dict:
    """The same edge scored pooled, day-clustered and de-overlapped."""
    row = {
        "measure": name,
        "n": int(values.notna().sum()),
        "sessions": int(days[values.notna()].nunique()),
        "edge": float(values.mean()),
        "t_pooled": naive_t(values),
        "t_clustered": clustered_t(values, days),
        "t_deoverlap": overlap_safe_t(values, days, stride),
    }
    row["inflation"] = (row["t_clustered"] / row["t_deoverlap"]
                        if row["t_deoverlap"] not in (0, np.nan) and
                        not np.isnan(row["t_deoverlap"]) else np.nan)
    return row


def show(rows: list[dict]) -> None:
    log("  {:<44} {:>7} {:>6} {:>9} {:>9} {:>10} {:>11} {:>7}".format(
        "measure", "n", "days", "edge", "t pooled", "t clust", "t DE-OVERLAP",
        "ratio"))
    log("  " + "-" * 108)
    for r in rows:
        log("  {:<44} {:>7,} {:>6} {:>+8.2%} {:>9.2f} {:>9.2f} {:>10.2f}  {:>7}".format(
            r["measure"], r["n"], r["sessions"], r["edge"], r["t_pooled"],
            r["t_clustered"], r["t_deoverlap"],
            "-" if np.isnan(r["inflation"]) else "{:.1f}x".format(r["inflation"])))


# ----------------------------------------------------------------------------
# 1. MOTION -- the 2.73x lift
# ----------------------------------------------------------------------------
def motion() -> list[dict]:
    scored = pd.read_csv(DECONFOUND, parse_dates=["day"])

    rows = []
    for label, prob, name in [("up10", "p_up", "+10% touch"),
                              ("dn10", "p_dn", "-10% touch")]:
        d = scored.dropna(subset=[label, prob]).copy()

        # Top decile within each fold, exactly as the original scoring did.
        d["pick"] = d.groupby("fold")[prob].transform(
            lambda x: x >= x.quantile(1 - TOP))

        base = d[label].mean()
        hit = d.loc[d["pick"], label].mean()
        log("  {}: base {:.2%}, top decile {:.2%}, lift {:.2f}x on {:,} picks"
            .format(name, base, hit, hit / base, int(d["pick"].sum())))

        # The edge as a per-pick quantity: did this pick beat the base rate of
        # its OWN session? Comparing against the same day removes the market
        # regime, so what is left is the selection itself.
        d["day_base"] = d.groupby("day")[label].transform("mean")
        picks = d[d["pick"]].copy()
        picks["excess"] = picks[label] - picks["day_base"]
        rows.append(three_ways("{} top-decile vs same-day base".format(name),
                               picks["excess"], picks["day"], MOTION_HORIZON))

        # And the control: the other 90% must come out negative by construction,
        # so this row is a check that the arithmetic is right, not a result.
        rest = d[~d["pick"]].copy()
        rest["excess"] = rest[label] - rest["day_base"]
        rows.append(three_ways("  ...bottom 90% (mirror, must be negative)",
                               rest["excess"], rest["day"], MOTION_HORIZON))
    return rows


# ----------------------------------------------------------------------------
# 2. DIRECTION -- the 3.06 vs 0.79 rule
# ----------------------------------------------------------------------------
def gap_label(d: pd.DataFrame) -> pd.DataFrame:
    """Largest single-day up and down move in the next GAP_HORIZON sessions."""
    g = d.groupby("symbol")
    up = dn = None
    for k in range(1, GAP_HORIZON + 1):
        step = g["c"].shift(-k) / g["c"].shift(-(k - 1)) - 1
        up = step if up is None else np.maximum(up, step)
        dn = step if dn is None else np.minimum(dn, step)
    d["up_gap"], d["dn_gap"] = up, dn
    return d.dropna(subset=["up_gap", "dn_gap"])


def direction() -> list[dict]:
    frame = pd.read_parquet(FEATURES).sort_values(["day", "symbol"])
    # The parquet stores `day` as a date object; make it a Timestamp so the
    # offset slicing below orders correctly and prints consistently.
    frame["day"] = pd.to_datetime(frame["day"])
    frame = gap_label(frame.reset_index(drop=True))

    days = np.sort(frame["day"].unique())
    cut = days[int(len(days) * SPLIT)]
    test = frame[frame["day"] > cut].copy()
    log("  frozen boundary {}, test window {} -> {} ({} sessions)".format(
        pd.Timestamp(cut).date(), test["day"].min().date(),
        test["day"].max().date(), test["day"].nunique()))

    test["up_hit"] = (test["up_gap"] >= GAP_THRESH).astype(float)
    test["dn_hit"] = (test["dn_gap"] <= -GAP_THRESH).astype(float)
    log("  base rates on test: up {:.1%}, down {:.1%}".format(
        test["up_hit"].mean(), test["dn_hit"].mean()))

    high_iv = test[test["iv_rank"] >= IV_CUT]
    ce = high_iv[high_iv["ema_stack"] <= 0].copy()   # below EMAs -> call side
    pe = high_iv[high_iv["ema_stack"] > 0].copy()    # above EMAs -> put side
    log("  high-IV names: {:,} below EMAs (CE), {:,} above (PE)".format(
        len(ce), len(pe)))
    log("  reported ratios: CE up {:.1%} vs dn {:.1%} = {:.2f}   "
        "PE up {:.1%} vs dn {:.1%} = {:.2f}".format(
            ce["up_hit"].mean(), ce["dn_hit"].mean(),
            ce["up_hit"].mean() / ce["dn_hit"].mean(),
            pe["up_hit"].mean(), pe["dn_hit"].mean(),
            pe["up_hit"].mean() / pe["dn_hit"].mean()))

    rows = []

    # The claim in its own terms: inside the CE bucket, up gaps beat down gaps.
    # A ratio has no standard error, so it is measured as the difference the
    # ratio is built from -- one number per name, which does have one.
    ce["edge"] = ce["up_hit"] - ce["dn_hit"]
    rows.append(three_ways("CE bucket: up minus down", ce["edge"], ce["day"],
                           GAP_HORIZON))
    pe["edge"] = pe["dn_hit"] - pe["up_hit"]
    rows.append(three_ways("PE bucket: down minus up", pe["edge"], pe["day"],
                           GAP_HORIZON))

    # The harder test, and the one that matters. The two buckets sit on the same
    # days, so the difference BETWEEN them is free of any market-wide drift --
    # which the two rows above are not, in a window that rose.
    ce_day = ce.groupby("day")["up_hit"].mean()
    pe_day = pe.groupby("day")["up_hit"].mean()
    spread = (ce_day - pe_day).dropna()
    spread_days = pd.Series(spread.index, index=spread.index)
    rows.append(three_ways("CE-minus-PE up rate (same-day, drift-free)",
                           spread, spread_days, GAP_HORIZON))

    # And a placebo: the same split on LOW-IV names, where the rule claims
    # nothing. If this scores as well as the real thing, the EMA split is doing
    # the work and the IV filter is decoration.
    low_iv = test[test["iv_rank"] < IV_CUT]
    lo_ce = low_iv[low_iv["ema_stack"] <= 0]
    lo_pe = low_iv[low_iv["ema_stack"] > 0]
    lo_spread = (lo_ce.groupby("day")["up_hit"].mean()
                 - lo_pe.groupby("day")["up_hit"].mean()).dropna()
    rows.append(three_ways("  ...same split on LOW IV (placebo)", lo_spread,
                           pd.Series(lo_spread.index, index=lo_spread.index),
                           GAP_HORIZON))
    return rows


def main() -> None:
    log("=" * 112)
    log("SERIAL-OVERLAP RE-SCORE")
    log("Multi-day forward windows sampled daily inflate t by about sqrt(horizon).")
    log("The right-hand column is the honest one. 'ratio' is how much the old")
    log("day-clustered figure overstated it.")
    log("=" * 112)

    log("")
    log("1. MOTION -- the 2.73x lift, horizon {} sessions".format(MOTION_HORIZON))
    log("")
    motion_rows = motion()
    log("")
    show(motion_rows)

    log("")
    log("2. DIRECTION -- the frozen 3.06 vs 0.79 rule, horizon {} sessions"
        .format(GAP_HORIZON))
    log("")
    direction_rows = direction()
    log("")
    show(direction_rows)

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("\nwrote {}".format(OUT))


if __name__ == "__main__":
    main()
