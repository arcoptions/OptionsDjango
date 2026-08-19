"""Can anything see a 5-10% move coming? Measured against the base rate, honestly.

The features are built.  This file is the part that can only ever say yes or no,
and the whole design is aimed at making "no" the easy answer to reach.

WHAT WOULD COUNT AS A YES.  Not accuracy, not AUC, not "the model found
patterns".  A quarter of all stock-days already precede a 5% up-touch, so a
signal that fires on a quarter of days and is right a quarter of the time has
found precisely nothing.  The only number that matters is LIFT: the hit rate
inside the signal divided by the hit rate of the whole sample, on data the model
never saw.  Below about 1.3x nothing survives an option's spread and theta, and
the buying study already showed the option leg costs more than that.

THREE WAYS THIS TEST COULD LIE, all of them closed here:

  1. Random splits.  Stock-days are not independent: the entire market rises and
     falls together, so a randomly held-out day sits between two training days
     from the same session and the model recovers it from its neighbours.  Splits
     here are strictly by DATE, train always earlier than test.

  2. Label leakage at the seam.  The last 5 training days carry labels drawn from
     the first 5 test days.  So an EMBARGO of `HORIZON` sessions is cut out
     between train and test.  Without it the fold boundary quietly leaks and the
     first fold looks best, which is the fingerprint.

  3. One lucky fold.  Feb-2025 to Aug-2026 covers one particular market.  Three
     expanding folds are run and reported separately; a result that appears in
     one fold and not the others is a regime, not an edge.

THE MODEL PICKS THE SIDE.  Two probabilities per day, P(up 10%) and P(down 10%),
from ONE feature set with no directional tuning.  Fitting a bull model and a bear
model separately and reporting the better one is how a null becomes a strategy.

Everything here is measured on the STOCK.  Even a real 1.4x lift on the stock has
to survive the option leg afterwards -- entry spread, theta over the hold, and
the IV the market charges precisely when a move looks likely -- and that is a
separate file.  A stock-level null needs no option pricing to be a null.
"""

import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")

HORIZON = 5      # must match premove_features.HORIZON -- also the embargo length
FOLDS = 3
TOP = 0.10       # the tradeable slice: the strongest 10% of signals

DROP = {
    "symbol", "day", "o", "h", "l", "c", "v", "contaminated",
    "up_max", "dn_max", "fwd_close", "iv_call", "iv_put", "oi_call", "oi_put",
    "up5", "up10", "up20", "dn5", "dn10", "dn20", "dollar_vol", "oi", "iv",
}


def log(message):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), message), flush=True)


def feature_names(frame):
    return [c for c in frame.columns
            if c not in DROP and pd.api.types.is_numeric_dtype(frame[c])]


# ---------------------------------------------------------------------------
# part 1: does any single feature separate the outcome at all?


def univariate(frame, cols, label, base):
    """Decile lift per feature. A feature with no top-or-bottom decile above
    1.2x is not going to become one inside a model."""
    rows = []
    valid = frame.dropna(subset=[label])
    for col in cols:
        sub = valid[[col, label]].dropna()
        if len(sub) < 5000 or sub[col].nunique() < 10:
            continue
        try:
            bins = pd.qcut(sub[col], 10, labels=False, duplicates="drop")
        except ValueError:
            continue
        hit = sub.groupby(bins)[label].mean()
        rows.append({
            "feature": col,
            "lo": hit.iloc[0] / base, "hi": hit.iloc[-1] / base,
            "spread": abs(hit.iloc[-1] - hit.iloc[0]) / base,
            "best": max(hit.max() / base, 1.0),
        })
    out = pd.DataFrame(rows).sort_values("spread", ascending=False)
    return out


# ---------------------------------------------------------------------------
# part 2: out-of-sample, date-split, embargoed


def folds(days, n=FOLDS, embargo=HORIZON):
    """Expanding train, contiguous test, `embargo` sessions cut between them."""
    days = sorted(days)
    start = len(days) // 2                      # first half is always training
    step = (len(days) - start) // n
    for k in range(n):
        cut = start + k * step
        stop = start + (k + 1) * step if k < n - 1 else len(days)
        train = days[:max(cut - embargo, 1)]
        test = days[cut:stop]
        if len(test) < 10:
            continue
        yield k + 1, set(train), set(test), days[cut], days[stop - 1]


def run_label(frame, cols, label, tag):
    base_all = frame[label].mean()
    log("")
    log("=" * 94)
    log("{}   base rate {:.2f}%   ({:,} labelled stock-days)".format(
        tag, base_all * 100, int(frame[label].notna().sum())))
    log("=" * 94)
    log("  {:<6} {:<24} {:>7} {:>10} {:>10} {:>9} {:>8}".format(
        "fold", "test window", "AUC", "base", "top 10%", "LIFT", "n"))

    scored, summaries = [], []
    for k, train_days, test_days, first, last in folds(frame.day.unique()):
        tr = frame[frame.day.isin(train_days)].dropna(subset=[label])
        te = frame[frame.day.isin(test_days)].dropna(subset=[label])
        if len(tr) < 2000 or len(te) < 500:
            continue
        model = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=200, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, random_state=7,
        )
        model.fit(tr[cols], tr[label])
        p = model.predict_proba(te[cols])[:, 1]

        base = te[label].mean()
        cutoff = np.quantile(p, 1 - TOP)
        picked = te[label].values[p >= cutoff]
        hit = picked.mean() if len(picked) else np.nan
        auc = roc_auc_score(te[label], p) if te[label].nunique() > 1 else np.nan
        row = {"label": label, "fold": k, "auc": auc, "base": base,
               "top": hit, "lift": hit / base if base else np.nan,
               "n_test": len(te), "n_picked": len(picked),
               "from": str(first), "to": str(last)}
        summaries.append(row)
        out = te[["symbol", "day", "up_max", "dn_max", label]].copy()
        out["p"], out["fold"] = p, k
        scored.append(out)
        log("  {:<6} {:<24} {:>7.3f} {:>9.2f}% {:>9.2f}% {:>8.2f}x {:>8,d}".format(
            k, "{} -> {}".format(first, last), auc, base * 100,
            hit * 100, row["lift"], len(te)))

    if summaries:
        combined = pd.concat(scored)
        pooled_base = combined[label].mean()
        pooled_hit = combined[label][combined.groupby("fold").p.transform(
            lambda x: x >= x.quantile(1 - TOP))].mean()
        log("  {:<6} {:<24} {:>7} {:>9.2f}% {:>9.2f}% {:>8.2f}x {:>8,d}".format(
            "ALL", "pooled", "",
            pooled_base * 100, pooled_hit * 100, pooled_hit / pooled_base,
            len(combined)))
    return summaries, (pd.concat(scored) if scored else pd.DataFrame())


def main():
    frame = pd.read_parquet(FEATURES)
    frame = frame.sort_values(["day", "symbol"]).reset_index(drop=True)
    cols = feature_names(frame)
    log("{:,} stock-days, {} symbols, {} features, {} -> {}".format(
        len(frame), frame.symbol.nunique(), len(cols),
        frame.day.min(), frame.day.max()))
    log("features: {}".format(", ".join(cols)))

    # -- univariate screen ------------------------------------------------
    for label, tag in [("up10", "UP 10%"), ("dn10", "DOWN 10%")]:
        base = frame[label].mean()
        uni = univariate(frame, cols, label, base)
        log("")
        log("-" * 94)
        log("SINGLE-FEATURE DECILE LIFT, {} (base {:.2f}%). Top 12 by decile spread."
            .format(tag, base * 100))
        log("  Lift is hit rate / base rate. 1.00x is the feature knowing nothing.")
        log("-" * 94)
        log("  {:<14} {:>12} {:>12} {:>12}".format(
            "feature", "low decile", "high decile", "spread"))
        for _, r in uni.head(12).iterrows():
            log("  {:<14} {:>11.2f}x {:>11.2f}x {:>11.2f}x".format(
                r.feature, r.lo, r.hi, r.spread))
        uni.to_csv(os.path.join(HERE, "premove_univariate_{}.csv".format(label)),
                   index=False)

    # -- out of sample ------------------------------------------------------
    all_rows, all_scores = [], {}
    for label, tag in [("up5", "PREDICTING A +5% TOUCH IN 5 SESSIONS"),
                       ("up10", "PREDICTING A +10% TOUCH IN 5 SESSIONS"),
                       ("dn5", "PREDICTING A -5% TOUCH IN 5 SESSIONS"),
                       ("dn10", "PREDICTING A -10% TOUCH IN 5 SESSIONS")]:
        rows, scored = run_label(frame, cols, label, tag)
        all_rows.extend(rows)
        all_scores[label] = scored

    # -- the side-picker: one feature set, two probabilities, take the winner
    log("")
    log("=" * 94)
    log("THE SIDE-PICKER. On each test day take max(P(up10), P(dn10)) and trade")
    log("that side. This is the actual product: CE, PE, or stand aside.")
    log("=" * 94)
    up, dn = all_scores.get("up10"), all_scores.get("dn10")
    if len(up) and len(dn):
        both = up.merge(dn[["symbol", "day", "fold", "p", "dn10"]],
                        on=["symbol", "day", "fold"], suffixes=("_up", "_dn"))
        both["side"] = np.where(both.p_up >= both.p_dn, "CE", "PE")
        both["conf"] = both[["p_up", "p_dn"]].max(axis=1)
        both["won"] = np.where(both.side == "CE", both.up10, both.dn10)
        base = np.where(both.side == "CE", up.up10.mean(), dn.dn10.mean())
        log("  {:<20} {:>10} {:>10} {:>9} {:>9} {:>9}".format(
            "confidence slice", "n", "hit%", "base%", "LIFT", "CE share"))
        for name, q in [("all signals", 0.0), ("top 25%", 0.75),
                        ("top 10%", 0.90), ("top 2%", 0.98)]:
            cut = both[both.conf >= both.conf.quantile(q)]
            if len(cut) < 50:
                continue
            b = np.where(cut.side == "CE", up.up10.mean(), dn.dn10.mean()).mean()
            hit = cut.won.mean()
            log("  {:<20} {:>10,d} {:>9.2f}% {:>8.2f}% {:>8.2f}x {:>8.0f}%".format(
                name, len(cut), hit * 100, b * 100, hit / b,
                (cut.side == "CE").mean() * 100))
        # What the winning side actually paid, in stock terms.
        top = both[both.conf >= both.conf.quantile(0.90)]
        move = np.where(top.side == "CE", top.up_max, -top.dn_max)
        log("")
        log("  Top 10% by confidence: median favourable excursion {:+.2f}%, mean {:+.2f}%."
            .format(np.nanmedian(move) * 100, np.nanmean(move) * 100))
        allmove = np.where(both.side == "CE", both.up_max, -both.dn_max)
        log("  Everything else for scale:  median {:+.2f}%, mean {:+.2f}%.".format(
            np.nanmedian(allmove) * 100, np.nanmean(allmove) * 100))
        both.to_csv(os.path.join(HERE, "premove_sidepicker.csv"), index=False)

    pd.DataFrame(all_rows).to_csv(os.path.join(HERE, "premove_oos.csv"), index=False)
    log("")
    log("wrote premove_oos.csv, premove_sidepicker.csv, premove_univariate_*.csv")


if __name__ == "__main__":
    main()
