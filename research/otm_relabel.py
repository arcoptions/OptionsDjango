"""Train on what actually pays -- the option's own outcome -- and close the entry question.

WHY THIS IS THE LAST ENTRY-SIDE IDEA.  Every signal tried so far predicts a STOCK
event: "this name moves 10% within five sessions".  That label is not the trade.
It ignores what the option cost, how far out the strike was, how much time was
left, and how fast the premium decays while the move is being waited for -- all
of which are known at entry and all of which decide whether a correct call on the
stock turns into money.  `deep-otm-signal-three-days` showed the stock label gets
within +0.089x of the bar and stops.  This asks whether the option label closes
the remaining gap, which is the strongest remaining version of "find the right
entry".

WHY IT IS ALSO THE MOST DANGEROUS.  The cache is 57 sessions and three of them
carry the entire profit.  A model trained on option outcomes inside that window
can reach the target by learning "late July", which is not a strategy, it is a
date.  So the protocol is deliberately hostile:

  WALK FORWARD, NEVER ONE BLOCK.  Retrain every 5 sessions on everything strictly
  before the test fold, so no fold is scored by a model that has seen its future.

  EMBARGO LONGER THAN THE TRADE.  Trades run up to 10 sessions, so a 10-session
  embargo sits between train and test.  Without it a trade entered on the last
  training day is still open on the first test day and the label leaks directly.

  THE DROP-THE-WEEK GUARD IS PART OF THE RESULT, not a footnote.  Any headline is
  reported twice, with and without 2026-07-24/27/29.  A number that only exists
  with them is reported as not existing.

  ONE CONTRACT PER SYMBOL-SESSION.  Ten strikes on one name is one bet, and
  letting the model pick all ten inflates n tenfold while adding no information.
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import charge, load_spreads, log  # noqa: E402

RULE = "trail 30% of peak"
EMBARGO = 10          # must exceed the maximum hold, or the label leaks
REFIT_EVERY = 5
MIN_TRAIN = 15        # sessions before the first fold is scored
MIN_PREM = 2.50
HOT = {pd.Timestamp(x).date() for x in ("2026-07-24", "2026-07-27", "2026-07-29")}


def features():
    """Contract-level facts known at entry, joined to the stock-level ones."""
    t = pd.read_parquet(os.path.join(HERE, "otm_exits.parquet"))
    t = t[(t["kind"] == "CE") & (t["entry"] >= MIN_PREM)].copy()

    d = pd.read_parquet(os.path.join(HERE, "deep_otm.parquet"))
    d["day"] = pd.to_datetime(d["ts"]).dt.date
    d = d[["sid", "day", "volume", "oi", "lot", "close", "high", "low"]]
    d["range"] = (d["high"] - d["low"]) / d["close"].replace(0, np.nan)
    # The contract's own recent behaviour, strictly backward-looking.
    d = d.sort_values(["sid", "day"])
    g = d.groupby("sid", sort=False)
    d["oi_chg"] = g["oi"].pct_change()
    d["vol_5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=1).mean())
    d["prem_5"] = g["close"].transform(lambda s: s.pct_change(5))
    d["range_5"] = g["range"].transform(lambda s: s.rolling(5, min_periods=1).mean())
    # Prefixed, because `premove_features` already has an `oi` -- the STOCK's
    # open interest. Merging both unprefixed silently suffixed each to oi_x/oi_y
    # and the feature list then referred to a column that no longer existed.
    # Left as distinct names rather than dropping one: the contract's own OI and
    # the name's aggregate OI are different facts and the model should see both.
    d = d.rename(columns={c: "c_" + c for c in
                          ["volume", "oi", "oi_chg", "vol_5", "prem_5", "range_5"]})
    t = t.merge(d[["sid", "day", "lot", "c_volume", "c_oi", "c_oi_chg", "c_vol_5",
                   "c_prem_5", "c_range_5"]], on=["sid", "day"], how="left")

    f = pd.read_parquet(os.path.join(HERE, "premove_features.parquet"))
    if "contaminated" in f:
        f = f[~f["contaminated"].astype(bool)]
    drop = {"symbol", "day", "o", "h", "l", "c", "v", "contaminated", "up_max",
            "dn_max", "fwd_close", "up5", "up10", "up20", "dn5", "dn10", "dn20",
            "dollar_vol"}
    keep_label = ["up10"]      # carried through as a LABEL, never as a feature
    cols = [c for c in f.columns if c not in drop and pd.api.types.is_numeric_dtype(f[c])]
    f["day"] = pd.to_datetime(f["day"]).dt.date
    t["day"] = pd.to_datetime(t["day"]).dt.date
    t = t.merge(f[["symbol", "day"] + cols + keep_label], on=["symbol", "day"],
                how="inner")

    sp = load_spreads()
    s = charge(t["entry"].to_numpy(float), sp)
    t["net"] = t[RULE].to_numpy(float) * (1 - s / 2) / (1 + s / 2)
    t["ticket"] = t["lot"] * t["entry"]
    t["logprem"] = np.log(t["entry"])
    own = ["otm", "dte", "entry", "logprem", "ticket", "c_volume", "c_oi",
           "c_oi_chg", "c_vol_5", "c_prem_5", "c_range_5"]
    feats = [c for c in cols + own if c in t.columns]
    missing = set(cols + own) - set(feats)
    if missing:
        log("dropped {} feature(s) not present after the merge: {}".format(
            len(missing), sorted(missing)))
    return t.dropna(subset=["net"]), feats


def walk(t, cols, label_fn, name):
    """Expanding-window walk-forward with an embargo longer than the trade."""
    t = t.sort_values("day").copy()
    t["y"] = label_fn(t)
    days = sorted(t["day"].unique())
    out = []
    for k in range(MIN_TRAIN, len(days), REFIT_EVERY):
        test_days = days[k:k + REFIT_EVERY]
        train_end = days[max(0, k - EMBARGO)]
        tr = t[t["day"] < train_end]
        te = t[t["day"].isin(test_days)]
        # Say WHY a fold is dropped. Four of nine folds vanished silently on the
        # first run, and a walk-forward that quietly scores half its window is
        # indistinguishable from one that picked its window.
        if len(tr) < 2000 or not len(te) or tr["y"].nunique() < 2:
            log("  fold {} ({}..{}): skipped -- train {:,} rows to {}, test {:,} rows"
                .format(k, test_days[0], test_days[-1], len(tr), train_end, len(te)))
            continue
        m = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=100, l2_regularization=1.0, early_stopping=True,
            validation_fraction=0.15, random_state=7)
        m.fit(tr[cols], tr["y"])
        te = te.copy()
        te["p"] = m.predict_proba(te[cols])[:, 1]
        out.append(te)
    if not out:
        log("{}: no fold was trainable".format(name))
        return None
    r = pd.concat(out, ignore_index=True)
    # One contract per symbol-session: the model's own top pick for that name.
    r = r.sort_values("p", ascending=False).drop_duplicates(["symbol", "day"])
    log("{}: {:,} scored trades over {} sessions, {} folds".format(
        name, len(r), r["day"].nunique(), len(out)))
    return r


def discrimination(r, name):
    """Can the score ORDER trades at all? The bucket table can hide this.

    A model whose top decile merely matches its own average is not a weak signal,
    it is no signal, and the distinction decides whether the answer is "train it
    harder" or "stop". AUC is computed PER FOLD and per session as well as pooled:
    a pooled AUC can be lifted purely by the model knowing which SESSIONS are good
    (a market call, unusable at 09:15 when the names must be picked), while the
    within-session AUC is the only part that ranks one contract against another.
    """
    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score
    print()
    print("  discrimination -- {}".format(name))
    if r["y"].nunique() > 1:
        print("    pooled AUC                {:.3f}".format(roc_auc_score(r["y"], r["p"])))
    within = [roc_auc_score(g["y"], g["p"])
              for _, g in r.groupby("day") if g["y"].nunique() > 1 and len(g) >= 20]
    if within:
        print("    within-session AUC        {:.3f}   (median of {} sessions)".format(
            float(np.median(within)), len(within)))
    rho, pv = spearmanr(r["p"], r["net"])
    print("    Spearman(score, net)      {:+.3f}   p = {:.2f}".format(rho, pv))
    per = r.groupby("day").apply(
        lambda g: spearmanr(g["p"], g["net"])[0] if len(g) >= 20 else np.nan,
        include_groups=False).dropna()
    if len(per):
        print("    within-session Spearman   {:+.3f}   positive on {} of {} sessions".format(
            per.median(), int((per > 0).sum()), len(per)))


def show(r, name):
    print()
    print("=" * 110)
    print("{}   exit = {}   walk-forward, {}-session embargo".format(name, RULE, EMBARGO))
    print("=" * 110)
    print("  {:<22} {:>7} {:>7} {:>10} {:>11} {:>8} {:>9} {:>12}".format(
        "selection", "n", "sess", "NET/sess", "net pooled", "win%", ">=2x%", "sess mean>1"))
    for lab, sub in [("all scored", r)] + [
            ("top {:.0%}".format(q), r[r["p"] >= r["p"].quantile(1 - q)])
            for q in (0.20, 0.10, 0.05)]:
        for tag, s2 in [("", sub), ("  (drop 3 days)", sub[~sub["day"].isin(HOT)])]:
            if len(s2) < 25:
                continue
            per = s2.groupby("day")["net"].mean()
            print("  {:<22} {:>7,} {:>7} {:>9.3f}x {:>10.3f}x {:>7.1f}% {:>8.1f}% {:>11}".format(
                lab + tag, len(s2), s2["day"].nunique(),
                s2.groupby("day")["net"].median().median(), s2["net"].mean(),
                (s2["net"] > 1).mean() * 100, (s2["net"] >= 2).mean() * 100,
                "{} of {}".format(int((per > 1).sum()), len(per))))


def main():
    t, cols = features()
    log("{:,} call-trades with {} features, {} sessions".format(
        len(t), len(cols), t["day"].nunique()))
    # POSITIVE CONTROL, run first and on purpose.  An AUC of 0.52 on the option
    # label is only evidence if this same harness can learn something learnable
    # with the same features, folds, embargo and dedup.  The stock-motion label
    # is known-learnable -- `otm_signal.py` got a 2.36-3.12x lift out of it -- so
    # if the control also prints ~0.52 the finding is about this file, not about
    # options.  Reported whatever it says.
    labels = [(lambda x: (x["up10"] == 1).astype(int) if "up10" in x else None,
               "CONTROL = the stock moves +10% in 5d (known learnable)"),
              (lambda x: (x["net"] > 1.0).astype(int), "LABEL = the trade made money"),
              (lambda x: (x["net"] >= 2.0).astype(int), "LABEL = the trade doubled")]
    for fn, name in labels:
        if fn(t) is None:
            log("{}: label column absent -- control cannot run".format(name))
            continue
        r = walk(t, cols, fn, name)
        if r is not None:
            show(r, name)
            discrimination(r, name)


if __name__ == "__main__":
    main()
