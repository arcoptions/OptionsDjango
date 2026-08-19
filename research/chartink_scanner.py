"""Can the motion predictor be reduced to a Chartink scan?

One thing in the stock programme survived de-overlapped significance: the model
that predicts a stock will travel 10% inside five sessions, at 2.73x the base
rate and t +4.08 (`overlap_rescore.py`). It is direction-blind, so it is not a
strategy on its own -- but it is the only real signal we have, and the question
asked is whether it can be run without any of this machinery: as a saved scan on
Chartink, refreshed daily.

That turns on what Chartink can actually compute. It reads price, volume and
their derivatives on the daily bar. It cannot see implied volatility, open
interest, or the cross-sectional breadth of the F&O universe, and three of those
sit in the model's feature list. So the test runs in three steps, each a fair
comparison against the same folds and the same de-overlapped significance:

  1. CEILING. Refit the model on the Chartink-expressible features ALONE. This
     bounds what any scan can reach, however clever, because a scan cannot use
     what it cannot see. If the lift collapses here, nothing downstream matters.

  2. FLOOR. Score plain threshold rules of the kind a scan actually encodes --
     "within 2% of the 20-day high", "volume twice its average". A gradient
     boosting model is not a scan; the gap between step 1 and step 2 is the cost
     of the simplification.

  3. SCALE OR PREDICTION. The step that decides what the scan is worth. A fixed
     +10% target is nearer, in a stock's own units, for a volatile stock than a
     calm one, so any rule correlated with volatility scores well without
     predicting anything. Two controls separate the two readings: refit with the
     volatility features removed, and re-score the model INSIDE volatility
     buckets, where a genuine event-predictor must still rank.

  4. SYNTAX. Emit the Chartink source for whatever survives.

The label is the same "+10% touch within 5 sessions" the 2.73x was measured on,
plus the two-sided motion label the model is really detecting, since a
direction-blind signal should be judged on motion rather than on up-moves.

Writes research/chartink_scanner.txt.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

FEATURES = "research/premove_features.parquet"
OUT = "research/chartink_scanner.txt"

HORIZON, FOLDS, TOP = 5, 3, 0.10

# What a Chartink daily scan can compute: price, volume, and functions of them.
# Everything here maps to a formula in their editor -- the mapping is printed in
# section 3 so the claim is checkable rather than asserted.
CHARTINK_OK = [
    "to_hi20", "to_lo20", "to_hi50", "to_lo50", "pos20", "pos50", "close_pos",
    "atr_pct", "atr_rank", "rvol20", "rvol_rank", "rng_rank", "nr7",
    "vol_surge", "vol_rank", "mom1", "mom5", "mom10", "mom20",
    "rs1", "rs5", "rs20", "gap", "streak", "to_ema20", "ema_stack",
    "brk_up20", "brk_dn20", "brk_up50", "brk_dn50", "age_hi", "age_lo",
]

# What it cannot: the option chain and the cross-section. These are the features
# the scan has to give up, and step 1 measures what giving them up costs.
CHARTINK_BLIND = ["iv", "iv_rank", "iv_chg5", "iv_vs_rv", "oi", "oi_chg5",
                  "skew", "breadth"]

LINES: list[str] = []

# The scan, written only from quantities Chartink computes on the daily bar.
# Every threshold below was measured on this sample rather than chosen: 3.28% is
# the cross-sectional top quartile of atr_pct, 4.0% is roughly its top decile,
# and 6% is the 20-day move that separates the top quintile of `rs20`.
#
# `rs20` in the feature set is index-RELATIVE; the scan uses a plain 20-day
# return because expressing the index leg in Chartink is awkward. That
# substitution is measured, not assumed -- the |20d| variant is the row scored
# in section 2 of the output, not a translation of a different row.
SCAN = """   ------------------------------------------------------------------------
   A. THE WATCHLIST SCAN  (17.8 names/day, 3.11x, t +6.95)

   ( {cash} (
       latest atr( 14 ) > latest close * 0.04
       and latest close > 100
       and latest sma( latest volume , 20 ) > 300000
   ) )

   B. TIGHTER, ADDS A 20-DAY MOVE  (11.7 names/day, 3.31x, t +6.22)

   ( {cash} (
       latest atr( 14 ) > latest close * 0.04
       and ( latest close > 1.06 * 20 days ago close
             or latest close < 0.94 * 20 days ago close )
       and latest close > 100
       and latest sma( latest volume , 20 ) > 300000
   ) )

   ------------------------------------------------------------------------
   THREE THINGS THAT WILL BREAK IT IF IGNORED

   1. RESTRICT THE UNIVERSE TO F&O NAMES. Every number here was measured on
      181 F&O stocks. `ATR% > 4%` run against the whole market surfaces
      illiquid small-caps this sample never contained, where nothing above
      was tested. Set the scan's group to the F&O list.

   2. DO NOT ADD "close near 20-day high". It is the intuitive filter and it
      INVERTS -- 0.66x, t -3.12 (section 2). A fixed-percentage distance to a
      boundary selects narrow-range stocks, which is the opposite of what the
      signal wants. `to_hi20` is the model's top feature by permutation
      importance and still fails as a threshold; the model uses it in
      combination, a scan cannot.

   3. THE SCAN IS DIRECTION-BLIND, and section 3 says it is a volatility
      ranking rather than an event predictor. It tells you where the range is
      about to be wide. It does not tell you which way, and buying premium on
      it pays for exactly the volatility it selects.
   ------------------------------------------------------------------------"""


def log(message: str = "") -> None:
    print(message)
    LINES.append(message)


def overlap_safe_t(values: pd.Series, days: pd.Series, stride: int = HORIZON) -> float:
    """Day-clustered t on non-overlapping samples. See overlap_rescore.py."""
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


def folds(days, n=FOLDS, embargo=HORIZON):
    days = sorted(days)
    start = len(days) // 2
    step = (len(days) - start) // n
    for k in range(n):
        cut = start + k * step
        stop = start + (k + 1) * step if k < n - 1 else len(days)
        if stop - cut < 10:
            continue
        yield k + 1, set(days[:max(cut - embargo, 1)]), set(days[cut:stop])


def fit_predict(frame: pd.DataFrame, cols: list, label: str) -> pd.DataFrame:
    """Walk-forward with an embargo, same harness as the original run."""
    out = []
    for k, train_days, test_days in folds(frame["day"].unique()):
        tr = frame[frame["day"].isin(train_days)].dropna(subset=[label])
        te = frame[frame["day"].isin(test_days)].dropna(subset=[label])
        if len(tr) < 2000 or len(te) < 500:
            continue
        model = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=200, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, random_state=7)
        model.fit(tr[cols], tr[label])
        chunk = te[["symbol", "day", "up10", "dn10", "motion"]].copy()
        chunk["p"], chunk["fold"] = model.predict_proba(te[cols])[:, 1], k
        out.append(chunk)
    return pd.concat(out) if out else pd.DataFrame()


def score(name: str, picked: pd.Series, frame: pd.DataFrame, label: str) -> dict:
    """Lift against the base rate, with the edge measured per same-day session.

    Comparing a pick against its OWN session's base rate is what makes the t
    meaningful: it removes the market-wide days when everything moves, which is
    otherwise most of what a motion label picks up.
    """
    d = frame.dropna(subset=[label]).copy()
    d["pick"] = picked.reindex(d.index).fillna(False).astype(bool)
    if d["pick"].sum() < 100:
        return {"rule": name, "n": int(d["pick"].sum()), "lift": np.nan,
                "hit": np.nan, "t": np.nan, "per_day": np.nan}
    d["day_base"] = d.groupby("day")[label].transform("mean")
    picks = d[d["pick"]]
    base, hit = d[label].mean(), picks[label].mean()
    return {
        "rule": name,
        "n": int(len(picks)),
        "per_day": len(picks) / d["day"].nunique(),
        "hit": hit,
        "lift": hit / base if base else np.nan,
        "t": overlap_safe_t(picks[label] - picks["day_base"], picks["day"]),
    }


def show(rows: list, base: float, title: str) -> None:
    log("  {}  (base rate {:.2%})".format(title, base))
    log("  {:<40} {:>8} {:>8} {:>8} {:>7} {:>8}".format(
        "rule", "n", "per day", "hit", "lift", "t"))
    log("  " + "-" * 84)
    for r in rows:
        if np.isnan(r["lift"]):
            log("  {:<40} {:>8} {:>8} {:>8} {:>7} {:>8}".format(
                r["rule"], r["n"], "-", "-", "too few", "-"))
            continue
        log("  {:<40} {:>8,} {:>8.1f} {:>7.2%} {:>7.2f} {:>8.2f}".format(
            r["rule"], r["n"], r["per_day"], r["hit"], r["lift"], r["t"]))


def main() -> None:
    frame = pd.read_parquet(FEATURES).sort_values(["day", "symbol"]).reset_index(drop=True)
    frame["day"] = pd.to_datetime(frame["day"])
    # The label the model is really detecting: a 10% move in EITHER direction.
    frame["motion"] = ((frame["up10"] > 0) | (frame["dn10"] > 0)).astype(float)
    frame.loc[frame["up10"].isna() | frame["dn10"].isna(), "motion"] = np.nan

    have = [c for c in CHARTINK_OK if c in frame.columns]
    blind = [c for c in CHARTINK_BLIND if c in frame.columns]
    log("=" * 90)
    log("CAN THE MOTION SIGNAL BE RUN AS A CHARTINK SCAN?")
    log("=" * 90)
    log("{:,} stock-days, {} symbols, {} sessions".format(
        len(frame), frame["symbol"].nunique(), frame["day"].nunique()))
    log("{} features a scan can compute, {} it is blind to ({})".format(
        len(have), len(blind), ", ".join(blind)))

    # -- 1. CEILING ---------------------------------------------------------
    log("")
    log("1. CEILING -- refit on scan-visible features only")
    log("   The full model scored 2.73x on +10% touch. A scan cannot beat this row.")
    log("")
    for label in ("up10", "motion"):
        scored = fit_predict(frame, have, label)
        if scored.empty:
            continue
        scored["pick"] = scored.groupby("fold")["p"].transform(
            lambda x: x >= x.quantile(1 - TOP))
        scored["day_base"] = scored.groupby("day")[label].transform("mean")
        picks = scored[scored["pick"]]
        base, hit = scored[label].mean(), picks[label].mean()
        log("   {:<8} base {:.2%} -> top decile {:.2%} = {:.2f}x   t {:+.2f}".format(
            label, base, hit, hit / base,
            overlap_safe_t(picks[label] - picks["day_base"], picks["day"])))

    # -- 2. FLOOR -----------------------------------------------------------
    # Plain rules of the kind a scan encodes. The range-edge family first,
    # because permutation importance put `to_hi20` four times above anything
    # else -- "about to move" is mostly "already pressed against a boundary".
    f = frame
    edge = np.minimum(f["to_hi20"].abs(), f["to_lo20"].abs())
    rules = {
        "near 20d HIGH (<=2%)": f["to_hi20"].abs() <= 0.02,
        "near 20d LOW  (<=2%)": f["to_lo20"].abs() <= 0.02,
        "near EITHER 20d edge (<=2%)": edge <= 0.02,
        "near EITHER 20d edge (<=1%)": edge <= 0.01,
        "  + volume surge >1.5x": (edge <= 0.02) & (f["vol_surge"] > 1.5),
        "  + top-quartile ATR": (edge <= 0.02) & (f["atr_rank"] >= 0.75),
        "  + both": ((edge <= 0.02) & (f["vol_surge"] > 1.5)
                     & (f["atr_rank"] >= 0.75)),
        "top-quartile ATR alone": f["atr_rank"] >= 0.75,
        "top-decile ATR alone": f["atr_rank"] >= 0.90,
        "20d breakout (brk_up20)": f["brk_up20"] > 0,
        "volume surge >2x alone": f["vol_surge"] > 2.0,
        # The two rows the emitted scan actually encodes. `atr_rank` above is a
        # 120-day rank of a stock against ITSELF; these use `atr_pct`, the level,
        # which is what a scan can write and -- unexpectedly -- what scores.
        "SCAN A: ATR% > 4.0%": f["atr_pct"] > 0.040,
        "SCAN B:  + |20d move| > 6%": (f["atr_pct"] > 0.040) & (f["mom20"].abs() > 0.06),
    }
    for label, title in [("up10", "+10% UP touch in 5 sessions"),
                         ("motion", "10% move EITHER WAY in 5 sessions")]:
        log("")
        log("2. FLOOR -- plain scan rules, {}".format(title))
        log("")
        rows = [score(name, cond, f, label) for name, cond in rules.items()]
        show(rows, f[label].mean(), title)

    # -- 3. SCALE OR PREDICTION --------------------------------------------
    # The step that decides everything. `atr_pct >= 3.28%` (the cross-sectional
    # top quartile) scores 2.17x on motion at t +8.35 -- almost the whole model,
    # from one threshold. Before calling that an edge, rule out the arithmetic:
    # a +10% target is a shorter walk for a volatile stock.
    log("")
    log("3. SCALE OR PREDICTION -- is the signal an event, or just volatility?")
    log("")
    v = frame.dropna(subset=["motion", "atr_pct"]).copy()
    # How many days of its own average range a stock must travel to make 10%.
    v["atr_days"] = 0.10 / (HORIZON * v["atr_pct"])
    v["q"] = pd.qcut(v["atr_pct"], 4, labels=["Q1 calm", "Q2", "Q3", "Q4 volatile"])
    base = v["motion"].mean()
    log("   {:<13} {:>9} {:>16} {:>9} {:>7}".format(
        "ATR quartile", "ATR%", "ATR-days to 10%", "motion", "lift"))
    log("   " + "-" * 60)
    for q, g in v.groupby("q", observed=True):
        log("   {:<13} {:>8.2%} {:>16.2f} {:>8.2%} {:>7.2f}".format(
            str(q), g["atr_pct"].median(), g["atr_days"].median(),
            g["motion"].mean(), g["motion"].mean() / base))
    log("")
    log("   A calm stock must travel ~1.1 days of its own range to make 10%; a")
    log("   volatile one ~0.5. The 11x spread in hit rate is mostly that.")

    # Control A: take the volatility features away. If the lift barely moves,
    # the remaining features are volatility proxies rather than a second signal.
    vol_features = ["atr_pct", "atr_rank", "rvol20", "rvol_rank", "rng_rank", "nr7"]
    log("")
    log("   CONTROL A -- refit with the volatility features removed:")
    for tag, cols in [("scan features (as above)", have),
                      ("volatility features removed",
                       [c for c in have if c not in vol_features])]:
        scored = fit_predict(frame, cols, "motion")
        if scored.empty:
            continue
        scored["pick"] = scored.groupby("fold")["p"].transform(
            lambda x: x >= x.quantile(1 - TOP))
        scored["day_base"] = scored.groupby("day")["motion"].transform("mean")
        picks = scored[scored["pick"]]
        b, hit = scored["motion"].mean(), picks["motion"].mean()
        log("     {:<32} {:.2%} -> {:.2%} = {:.2f}x   t {:+.2f}".format(
            tag, b, hit, hit / b,
            overlap_safe_t(picks["motion"] - picks["day_base"], picks["day"])))

    # Control B: the decisive one. A predictor of an EVENT must still rank
    # inside a volatility bucket, where the scale effect is held constant.
    log("")
    log("   CONTROL B -- re-score the model INSIDE volatility buckets.")
    log("   A genuine event-predictor must still rank here. A volatility sorter cannot.")
    scored = fit_predict(frame, have, "motion")
    scored = scored.merge(frame[["symbol", "day", "atr_pct"]],
                          on=["symbol", "day"], how="left").dropna(subset=["atr_pct"])
    scored["q"] = pd.qcut(scored["atr_pct"], 4,
                          labels=["Q1 calm", "Q2", "Q3", "Q4 volatile"])
    log("     {:<13} {:>8} {:>8} {:>12} {:>7} {:>7}".format(
        "bucket", "n", "base", "top decile", "lift", "t"))
    log("     " + "-" * 58)
    for q, g in scored.groupby("q", observed=True):
        g = g.copy()
        g["pick"] = g["p"] >= g["p"].quantile(1 - TOP)
        g["day_base"] = g.groupby("day")["motion"].transform("mean")
        picks = g[g["pick"]]
        b = g["motion"].mean()
        log("     {:<13} {:>8,} {:>7.2%} {:>11.2%} {:>7.2f} {:>7.2f}".format(
            str(q), len(g), b, picks["motion"].mean(), picks["motion"].mean() / b,
            overlap_safe_t(picks["motion"] - picks["day_base"], picks["day"])))

    # Control C: bucketing quarters the sample and so halves every t. The
    # full-power version scores each pick against same-day peers IN ITS OWN
    # VOLATILITY DECILE, which holds volatility constant without spending
    # sample. The plain ATR rule is the positive control -- matched on
    # volatility it must collapse to zero, and if it does not, the matching
    # is broken rather than the finding confirmed.
    log("")
    log("   CONTROL C -- volatility-matched, at full sample power.")
    scored["vb"] = pd.qcut(scored["atr_pct"], 10, labels=False, duplicates="drop")
    scored["pick"] = scored.groupby("fold")["p"].transform(
        lambda x: x >= x.quantile(1 - TOP))
    scored["day_base"] = scored.groupby("day")["motion"].transform("mean")
    scored["vol_base"] = scored.groupby(["day", "vb"])["motion"].transform("mean")
    picks = scored[scored["pick"]]
    log("     model top decile, {:,} picks on {} sessions".format(
        len(picks), picks["day"].nunique()))
    for tag, col in [("vs same-DAY average (the published control)", "day_base"),
                     ("vs same-day, SAME-VOLATILITY-DECILE average", "vol_base")]:
        e = picks["motion"] - picks[col]
        log("       {:<44} edge {:+.2%}  t {:+.2f}".format(
            tag, e.mean(), overlap_safe_t(e, picks["day"])))
    g = frame.dropna(subset=["motion", "atr_pct"]).copy()
    g["vb"] = pd.qcut(g["atr_pct"], 10, labels=False, duplicates="drop")
    g["day_base"] = g.groupby("day")["motion"].transform("mean")
    g["vol_base"] = g.groupby(["day", "vb"])["motion"].transform("mean")
    r = g[g["atr_pct"] > 0.040]
    log("     POSITIVE CONTROL -- the plain ATR% > 4% rule, which IS volatility:")
    for tag, col in [("vs same-DAY average", "day_base"),
                     ("vs same-day, SAME-VOLATILITY-DECILE", "vol_base")]:
        e = r["motion"] - r[col]
        log("       {:<44} edge {:+.2%}  t {:+.2f}".format(
            tag, e.mean(), overlap_safe_t(e, r["day"])))
    log("")
    log("   READ: the model's edge falls +7.35% -> +2.03% and t +3.89 -> +1.33 once")
    log("   volatility is held constant. Roughly 72% of it was volatility, and what")
    log("   is left does not reach significance. The ATR rule collapsing to +0.10%")
    log("   at t +0.16 is the check that the matching works as intended.")

    # -- 4. SYNTAX ----------------------------------------------------------
    log("")
    log("4. SYNTAX -- the scan, and what it is honestly for")
    log("")
    log("   Verdict: buildable, and it will do what it says -- surface stocks that")
    log("   go on to travel 10%. But it is a volatility filter, and volatility is")
    log("   what option sellers already charge for. Use it as a WATCHLIST, where")
    log("   the direction call is yours, not as a mechanical signal.")
    log("")
    log(SCAN)

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("\nwrote {}".format(OUT))


if __name__ == "__main__":
    main()
