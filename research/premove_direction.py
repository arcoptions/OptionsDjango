"""It predicts MOTION, not DIRECTION. Three tests to be sure, and what that leaves.

The deconfounding run survived its two hardest challenges and failed the one that
decides the product:

  conditional lift   1.6-2.8x inside EVERY volatility quintile -- so it is not
                     just reading "this stock is volatile"
  stripped model     1.71x up / 1.67x down with ATR, realised vol, IV and range
                     ranks deleted outright -- so it is not only volatility
  DIRECTION          60.0% correct against 61.4% for always-say-CE. Minus 1.3pp.

A motion detector is not a CE-or-PE picker.  But the direction test as run has a
real flaw and it has to be closed before the verdict stands: P(up 10%) has a
5.04% base rate and P(down 10%) has 2.95%, so comparing the two raw probabilities
tilts to CE mechanically, and the model called CE 90% of the time.  Three ways
of asking the same question, each fixing a different objection:

  A. NORMALISED.  Compare p_up/base_up against p_dn/base_dn, so each side is
     measured against its own frequency instead of against the other's.

  B. TRAINED ON THE QUESTION.  Fit a model on the moved days only, target "was it
     up".  Given that a big move is coming, call the side.  Nothing about base
     rates can distort a model asked the question directly.  If its AUC sits at
     0.50 out of sample, direction is not there and no amount of reframing puts
     it there.

  C. WHAT IS THE SIGNAL, THEN?  Permutation importance on the stripped model, so
     the thing that does work has a name rather than being a black box.

And then the part that decides whether a motion detector is worth anything at all.
Motion with no direction is a STRADDLE, and a straddle is only profitable if the
move beats what the option cost.  The option cost is implied volatility, quoted
on the same stock at the same instant.  So:

  D. IS THE MOTION ALREADY PRICED?  On signal days, compare the realised 5-day
     move against the move IV implied at entry.  If IV already knew, the 2.7x
     lift is a true statement about the stock that pays nothing.

Nothing here is a backtest of an option position -- that comes after, on the OTM
cache, and only if D says there is anything left to buy.
"""

import datetime as dt
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")
HORIZON, FOLDS, TOP = 5, 3, 0.10

DROP = {"symbol", "day", "o", "h", "l", "c", "v", "contaminated",
        "up_max", "dn_max", "fwd_close", "iv_call", "iv_put", "oi_call", "oi_put",
        "up5", "up10", "up20", "dn5", "dn10", "dn20", "dollar_vol", "oi", "iv"}
VOL = {"atr_pct", "atr_rank", "rvol20", "rvol_rank", "rng_rank", "nr7",
       "iv_rank", "iv_chg5", "iv_vs_rv", "skew"}


def log(m):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), m), flush=True)


def folds(days, n=FOLDS, embargo=HORIZON):
    days = sorted(days)
    start = len(days) // 2
    step = (len(days) - start) // n
    for k in range(n):
        cut, stop = start + k * step, (start + (k + 1) * step if k < n - 1 else len(days))
        if stop - cut < 10:
            continue
        yield k + 1, set(days[:max(cut - embargo, 1)]), set(days[cut:stop])


def model():
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.15,
        random_state=7)


def fit_predict(frame, cols, label, carry=()):
    out = []
    for k, train_days, test_days in folds(frame.day.unique()):
        tr = frame[frame.day.isin(train_days)].dropna(subset=[label])
        te = frame[frame.day.isin(test_days)].dropna(subset=[label])
        if len(tr) < 500 or len(te) < 200 or tr[label].nunique() < 2:
            continue
        m = model()
        m.fit(tr[cols], tr[label])
        keep = list(dict.fromkeys(["symbol", "day", label] + list(carry)))
        chunk = te[keep].copy()
        chunk["p"], chunk["fold"] = m.predict_proba(te[cols])[:, 1], k
        chunk["base"] = tr[label].mean()
        out.append(chunk)
    return pd.concat(out) if out else pd.DataFrame()


def main():
    frame = pd.read_parquet(FEATURES).sort_values(["day", "symbol"]).reset_index(drop=True)
    cols = [c for c in frame.columns
            if c not in DROP and pd.api.types.is_numeric_dtype(frame[c])]
    plain = [c for c in cols if c not in VOL]

    carry = ["up_max", "dn_max", "fwd_close", "up10", "dn10", "iv", "rvol20", "c"]
    up = fit_predict(frame, cols, "up10", carry)
    dn = fit_predict(frame, cols, "dn10", carry)
    both = up.merge(dn[["symbol", "day", "fold", "p", "base"]],
                    on=["symbol", "day", "fold"], suffixes=("_up", "_dn"))

    # -- A. normalise each side against its own base rate -------------------
    log("=" * 96)
    log("A. DIRECTION, NORMALISED. Each probability divided by its own training base")
    log("   rate, so the 5.04%-vs-2.95% asymmetry cannot tilt the call toward CE.")
    log("=" * 96)
    moved = both[(both.up10 == 1) ^ (both.dn10 == 1)].copy()
    moved["truth"] = np.where(moved.up10 == 1, "CE", "PE")
    moved["lift_up"] = moved.p_up / moved.base_up
    moved["lift_dn"] = moved.p_dn / moved.base_dn
    moved["call"] = np.where(moved.lift_up >= moved.lift_dn, "CE", "PE")
    moved["conf"] = (moved.lift_up - moved.lift_dn).abs()
    naive = (moved.truth == "CE").mean()
    log("  {:,} single-direction movers, {:.1f}% of them up. Model says CE {:.1f}% of"
        " the time now (was 90% unnormalised).".format(
            len(moved), naive * 100, (moved.call == "CE").mean() * 100))
    log("")
    log("  {:<20} {:>9} {:>11} {:>13} {:>10}".format(
        "confidence", "n", "correct", "always-CE", "edge"))
    for name, q in [("all movers", 0.0), ("top 50%", 0.5), ("top 25%", 0.75),
                    ("top 10%", 0.9)]:
        cut = moved[moved.conf >= moved.conf.quantile(q)]
        if len(cut) < 40:
            continue
        acc, nv = (cut.call == cut.truth).mean(), (cut.truth == "CE").mean()
        log("  {:<20} {:>9,d} {:>10.1f}% {:>12.1f}% {:>9.1f}pp".format(
            name, len(cut), acc * 100, nv * 100, (acc - nv) * 100))

    # -- B. ask the question directly ---------------------------------------
    log("")
    log("=" * 96)
    log("B. A MODEL TRAINED ON THE QUESTION. Fit only on days that moved 10% one way,")
    log("   target 'was it up'. No base-rate asymmetry left to blame. AUC 0.50 = blind.")
    log("=" * 96)
    sub = frame[((frame.up10 == 1) ^ (frame.dn10 == 1))].copy()
    sub["was_up"] = (sub.up10 == 1).astype(float)
    log("  training sample: {:,} movers across {} sessions".format(
        len(sub), sub.day.nunique()))
    side = fit_predict(sub, cols, "was_up", ["up_max", "dn_max"])
    if len(side):
        log("")
        log("  {:<8} {:>9} {:>10} {:>12} {:>12}".format(
            "fold", "n test", "AUC", "accuracy", "always-CE"))
        for k, g in side.groupby("fold"):
            auc = roc_auc_score(g.was_up, g.p) if g.was_up.nunique() > 1 else np.nan
            acc = ((g.p >= 0.5).astype(float) == g.was_up).mean()
            log("  {:<8} {:>9,d} {:>10.3f} {:>11.1f}% {:>11.1f}%".format(
                k, len(g), auc, acc * 100, g.was_up.mean() * 100))
        auc = roc_auc_score(side.was_up, side.p)
        acc = ((side.p >= 0.5).astype(float) == side.was_up).mean()
        log("  {:<8} {:>9,d} {:>10.3f} {:>11.1f}% {:>11.1f}%   <- pooled".format(
            "ALL", len(side), auc, acc * 100, side.was_up.mean() * 100))

    # -- C. name the signal --------------------------------------------------
    log("")
    log("=" * 96)
    log("C. WHAT THE STRIPPED MODEL ACTUALLY USES (permutation importance, drop in")
    log("   AUC when one feature is shuffled). Volatility features are not in it.")
    log("=" * 96)
    days = sorted(frame.day.unique())
    cut = days[len(days) * 2 // 3]
    tr = frame[frame.day < cut].dropna(subset=["up10"])
    te = frame[frame.day > days[min(days.index(cut) + HORIZON, len(days) - 1)]] \
        .dropna(subset=["up10"])
    m = model()
    m.fit(tr[plain], tr.up10)
    imp = permutation_importance(m, te[plain], te.up10, n_repeats=5,
                                 random_state=7, scoring="roc_auc")
    order = np.argsort(imp.importances_mean)[::-1]
    log("  {:<16} {:>14} {:>12}".format("feature", "AUC drop", "sd"))
    for i in order[:12]:
        log("  {:<16} {:>13.4f} {:>12.4f}".format(
            plain[i], imp.importances_mean[i], imp.importances_std[i]))

    # -- D. is the motion already priced? ------------------------------------
    log("")
    log("=" * 96)
    log("D. IS THE MOTION PRICED? A directionless mover-detector is a STRADDLE signal.")
    log("   A straddle pays |move| and costs roughly what IV implies. IV is quoted on")
    log("   the same stock at the same instant, so this is the whole economics.")
    log("=" * 96)
    priced = both.dropna(subset=["iv", "fwd_close"]).copy()
    priced["implied"] = priced.iv / 100 * np.sqrt(HORIZON / 252)     # 5-day 1sd
    priced["realised"] = priced.fwd_close.abs()
    # E|N(0,s)| = s*sqrt(2/pi): the move a fairly-priced straddle needs to break even.
    priced["fair"] = priced.implied * np.sqrt(2 / np.pi)
    priced["excess"] = priced.realised - priced.fair
    priced["conf"] = np.maximum(priced.p_up / priced.base_up,
                                priced.p_dn / priced.base_dn)
    log("  {:,} test rows carry an ATM IV quote (median IV {:.1f}).".format(
        len(priced), priced.iv.median()))
    log("")
    log("  {:<20} {:>9} {:>11} {:>11} {:>12} {:>11}".format(
        "signal slice", "n", "IV implies", "realised", "excess", "ratio"))
    for name, q in [("everything", 0.0), ("top 25%", 0.75), ("top 10%", 0.90),
                    ("top 2%", 0.98)]:
        g = priced[priced.conf >= priced.conf.quantile(q)]
        if len(g) < 100:
            continue
        log("  {:<20} {:>9,d} {:>10.2f}% {:>10.2f}% {:>11.2f}pp {:>10.2f}x".format(
            name, len(g), g.fair.mean() * 100, g.realised.mean() * 100,
            g.excess.mean() * 100, g.realised.mean() / g.fair.mean()))
    log("")
    log("  Ratio above 1.00x means the stock moved MORE than the option was priced")
    log("  for -- gross of spread, brokerage and the bid-ask on two legs.")

    priced.to_csv(os.path.join(HERE, "premove_direction.csv"), index=False)
    log("")
    log("wrote premove_direction.csv")


if __name__ == "__main__":
    main()
