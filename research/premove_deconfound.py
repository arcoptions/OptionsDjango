"""The 2.7x lift, taken apart. Most of it is a tautology, and one piece is not.

`premove_predict.py` found a real out-of-sample lift -- 2.73x on a +10% touch,
1.93x on a -10% touch, consistent across three date-split folds.  Before any of
that becomes a strategy it has to survive the objection written all over the
univariate table:

    atr_pct   2.04x      rvol20   1.82x      iv_rank   1.66x

Those are the three strongest features and all three are the same thing.  A
volatile stock is more likely to travel 10% in a week.  That is not a prediction,
it is a definition -- and worse, it is the one property of a stock the option
market prices EXACTLY, through implied volatility.  Being right that a stock is
volatile earns nothing when the premium already charges for it.  If the whole 2.7x
is a volatility read, the finding is worthless to an option buyer even though it
is perfectly true about the stock.

So four questions, in the order that can kill the result fastest:

  1. CONDITIONAL LIFT.  Inside a single volatility decile -- where every stock is
     equally volatile -- does the model still separate?  This is the test that
     matters most.  If lift collapses to 1.0x within buckets, the model knows
     only volatility.

  2. STRIPPED MODEL.  Refit with every volatility feature deleted (ATR, realised
     vol, IV, range ranks).  What is left is levels, momentum, volume, breadth.
     Whatever lift survives is the part that is not the tautology.

  3. DIRECTION.  Among days that DID produce a big move either way, does the model
     pick the correct SIDE better than the 50/50 a coin gives?  A model that
     predicts "something will happen" is a straddle signal, and this cache
     already showed short premium loses and long premium loses -- so a
     directionless mover-detector is not tradeable in either form.

  4. IS THE MODEL JUST LONG BETA?  93% of its top-decile picks were CE.  If the
     signal fires on the same days for every stock at once, it is a market-timing
     call wearing a stock-picking costume, and 189 positions is one position.

Question 3 is the one the user's request actually turns on: "our stocks predictor
should buy CE or PE based on the current data".  That needs SIDE, not motion.
"""

import datetime as dt
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")

HORIZON, FOLDS, TOP = 5, 3, 0.10

DROP = {
    "symbol", "day", "o", "h", "l", "c", "v", "contaminated",
    "up_max", "dn_max", "fwd_close", "iv_call", "iv_put", "oi_call", "oi_put",
    "up5", "up10", "up20", "dn5", "dn10", "dn20", "dollar_vol", "oi", "iv",
}

# Everything that is a restatement of "this stock moves a lot".
VOL = {"atr_pct", "atr_rank", "rvol20", "rvol_rank", "rng_rank", "nr7",
       "iv_rank", "iv_chg5", "iv_vs_rv", "skew"}


def log(m):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), m), flush=True)


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


def fit_predict(frame, cols, label):
    """Walk the folds, return the test rows with a probability attached."""
    out = []
    for k, train_days, test_days in folds(frame.day.unique()):
        tr = frame[frame.day.isin(train_days)].dropna(subset=[label])
        te = frame[frame.day.isin(test_days)].dropna(subset=[label])
        if len(tr) < 2000 or len(te) < 500:
            continue
        model = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=200, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, random_state=7)
        model.fit(tr[cols], tr[label])
        keep = ["symbol", "day", "up_max", "dn_max", "rvol20", "atr_pct",
                "up10", "dn10"]
        if label not in keep:
            keep.append(label)
        chunk = te[keep].copy()
        chunk["p"], chunk["fold"] = model.predict_proba(te[cols])[:, 1], k
        out.append(chunk)
    return pd.concat(out) if out else pd.DataFrame()


def top_lift(scored, label, frac=TOP):
    pick = scored.groupby("fold").p.transform(lambda x: x >= x.quantile(1 - frac))
    base, hit = scored[label].mean(), scored[label][pick].mean()
    return base, hit, hit / base if base else np.nan


def main():
    frame = pd.read_parquet(FEATURES).sort_values(["day", "symbol"]).reset_index(drop=True)
    cols = [c for c in frame.columns
            if c not in DROP and pd.api.types.is_numeric_dtype(frame[c])]
    plain = [c for c in cols if c not in VOL]
    log("{:,} stock-days. {} features, {} of them non-volatility.".format(
        len(frame), len(cols), len(plain)))

    results = {}
    for label in ("up10", "dn10"):
        results[label] = {"full": fit_predict(frame, cols, label),
                          "plain": fit_predict(frame, plain, label)}

    # -- 1. lift INSIDE a volatility bucket --------------------------------
    log("")
    log("=" * 96)
    log("1. CONDITIONAL LIFT. Every stock-day is sorted into a realised-volatility")
    log("   quintile, and the model's top 10% is scored WITHIN each quintile only.")
    log("   If the model knows only volatility, these all collapse to 1.00x.")
    log("=" * 96)
    for label, name in [("up10", "+10% touch"), ("dn10", "-10% touch")]:
        scored = results[label]["full"].dropna(subset=["rvol20"]).copy()
        scored["bucket"] = pd.qcut(scored.rvol20, 5, labels=False)
        log("")
        log("  {}   {:<8} {:>10} {:>10} {:>9} {:>9}".format(
            name, "vol", "base", "top 10%", "LIFT", "n"))
        for b, g in scored.groupby("bucket"):
            pick = g.groupby("fold").p.transform(lambda x: x >= x.quantile(1 - TOP))
            base, hit = g[label].mean(), g[label][pick].mean()
            log("  {:<12} {:<8} {:>9.2f}% {:>9.2f}% {:>8.2f}x {:>9,d}".format(
                "", "Q{} {}".format(b + 1, ["lowest", "", "mid", "", "highest"][b]),
                base * 100, hit * 100, hit / base if base else np.nan, len(g)))

    # -- 2. what survives with volatility deleted ---------------------------
    log("")
    log("=" * 96)
    log("2. STRIPPED MODEL. Refit with ATR, realised vol, IV and range ranks removed.")
    log("=" * 96)
    log("  {:<14} {:>12} {:>12} {:>12} {:>12}".format(
        "label", "full lift", "stripped", "base", "n"))
    for label in ("up10", "dn10"):
        b1, h1, l1 = top_lift(results[label]["full"], label)
        b2, h2, l2 = top_lift(results[label]["plain"], label)
        log("  {:<14} {:>11.2f}x {:>11.2f}x {:>11.2f}% {:>12,d}".format(
            label, l1, l2, b1 * 100, len(results[label]["full"])))

    # -- 3. does it know the SIDE, not just the motion? ---------------------
    log("")
    log("=" * 96)
    log("3. DIRECTION. Restricted to days that DID move 10% one way or the other,")
    log("   and to the days where exactly ONE side happened, so there is a right")
    log("   answer. Coin-flip is 50%. This is the number the CE-or-PE product needs.")
    log("=" * 96)
    up, dn = results["up10"]["full"], results["dn10"]["full"]
    both = up.merge(dn[["symbol", "day", "fold", "p"]],
                    on=["symbol", "day", "fold"], suffixes=("_up", "_dn"))
    moved = both[(both.up10 == 1) ^ (both.dn10 == 1)].copy()
    moved["truth"] = np.where(moved.up10 == 1, "CE", "PE")
    moved["call"] = np.where(moved.p_up >= moved.p_dn, "CE", "PE")
    moved["conf"] = (moved.p_up - moved.p_dn).abs()
    log("  {:,} stock-days moved 10% one way only. {:.1f}% of them were up moves,".format(
        len(moved), (moved.truth == "CE").mean() * 100))
    log("  so always saying CE already scores that much. THAT is the hurdle, not 50%.")
    log("")
    log("  {:<22} {:>10} {:>12} {:>13} {:>10}".format(
        "confidence slice", "n", "correct", "always-CE", "edge"))
    always = (moved.truth == "CE").mean()
    for name, q in [("all movers", 0.0), ("top 50%", 0.50),
                    ("top 25%", 0.75), ("top 10%", 0.90)]:
        cut = moved[moved.conf >= moved.conf.quantile(q)]
        if len(cut) < 40:
            continue
        acc = (cut.call == cut.truth).mean()
        naive = (cut.truth == "CE").mean()
        log("  {:<22} {:>10,d} {:>11.1f}% {:>12.1f}% {:>9.1f}pp".format(
            name, len(cut), acc * 100, naive * 100, (acc - naive) * 100))

    # -- 4. is the signal one bet or many? ----------------------------------
    log("")
    log("=" * 96)
    log("4. CONCENTRATION. If the top decile bunches into a handful of sessions, the")
    log("   'strategy' is a market call held in 189 pieces, and its real sample size")
    log("   is the number of DAYS, not the number of trades.")
    log("=" * 96)
    pick = up[up.groupby("fold").p.transform(lambda x: x >= x.quantile(1 - TOP))]
    per_day = pick.groupby("day").size()
    sessions = up.day.nunique()
    log("  top-decile CE signals: {:,} trades over {} of {} test sessions".format(
        len(pick), len(per_day), sessions))
    log("  per session: median {:.0f}, 90th pct {:.0f}, max {:.0f} of 189 stocks".format(
        per_day.median(), per_day.quantile(0.9), per_day.max()))
    share = per_day.sort_values(ascending=False)
    log("  the busiest 10% of signal days carry {:.0f}% of all signals".format(
        100 * share.head(max(len(share) // 10, 1)).sum() / share.sum()))
    day_hit = pick.groupby("day")["up10"].mean()
    log("  hit rate by session: median {:.0f}%, and {:.0f}% of sessions went 0-for-all"
        .format(day_hit.median() * 100, (day_hit == 0).mean() * 100))

    both.to_csv(os.path.join(HERE, "premove_deconfound.csv"), index=False)
    log("")
    log("wrote premove_deconfound.csv")


if __name__ == "__main__":
    main()
