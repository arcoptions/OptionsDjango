"""Does the motion signal clear the bar that the deep-OTM null set?

THE BAR, STATED BEFORE THE TEST RATHER THAN AFTER IT.  `otm_exits.py` measured
the unconditional rate on 6,366 real pinned contracts: every exit rule loses in
every slice on both sides, and the best net-per-session anywhere is 0.75x.  So an
entry signal is not being asked to "add value" -- it has to carry the median
trade from ~0.70x to 1.0x, a 43% improvement, before it makes one rupee.  Nothing
in this programme has moved a median more than single digits.  That is the
number this file is trying to beat, and it is written down first so the result
cannot be graded on a curve afterwards.

WHY THIS SIGNAL AND NOT ANOTHER.  `premove_direction.py` established two things
that fit together awkwardly and are both real: the model predicts MOTION at a
2.7x lift over base rate and survives every deconfound, and it predicts SIDE at
AUC 0.510, which is a coin.  A CE-only rule therefore throws away half its own
signal, and indeed the CE failed at ATM, ATM+1 and ATM+2 alike.  The construction
the evidence actually implies is BOTH LEGS -- and until this cache existed there
were no real far-OTM put contracts to test it on.  `premove_straddle.py` got as
close as the rolling feed allowed (ATM only, n=68, -27.2% against a -33.7%
control) and could not reach the strikes the brief is about.

THREE THINGS ARE HELD FIXED SO THE SIGNAL IS THE ONLY VARIABLE.

  THE MODEL NEVER SEES THE WINDOW.  Training stops at the deep-OTM window's first
  session minus a five-day embargo -- 2026-05-20 -- and every scored day is after
  it.  That is one clean out-of-sample block rather than the interleaved folds
  `premove_straddle.py` used, and it is the honest arrangement here because the
  cache itself is a single contiguous six weeks.

  THE COMPARISON IS AGAINST NON-SIGNAL DAYS, NOT AGAINST ZERO.  Buying premium
  loses on this cache before any signal is applied.  A rule that loses 20% where
  the control loses 30% is the signal working; it is also still a losing trade.
  Both readings are printed and neither is allowed to stand in for the other.

  THE SLICE IS HELD CONSTANT.  Signal and control are compared inside the same
  premium band, because premium alone moves the net result 0.30x -> 0.70x through
  friction.  A signal that merely selects expensive options would otherwise look
  like an edge.

WHAT A PAIR TRADE MEANS HERE.  Two independent orders, one call and one put, each
exited by its own rule -- not a basket unwound together.  That is what a person
would actually place, and it is also the only version daily bars can price
honestly.  The pair's return is cost-weighted across the two legs, each already
net of its own premium-band friction.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import (PREM_BANDS, RULES, charge, clustered_t,  # noqa: E402
                       load_spreads, log)

FEATURES = os.path.join(HERE, "premove_features.parquet")
TRADES = os.path.join(HERE, "otm_exits.parquet")

EMBARGO = 5          # sessions dropped between training and the window
HORIZON = 5          # the label's horizon, in sessions
MIN_PREM = 2.50      # below this friction is 16-40% and swamps everything
TOPS = [0.10, 0.05, 0.02]
PER_DAY_N = 3        # the live workflow: rank the universe each morning, take N
# Only the rules worth carrying into a comparison table: the measured best
# (trail 30%), the fixed hold as a floor, and the two limit orders a person
# would actually leave resting.
HEADLINE = ["hold to 10d", "trail 30% of peak", "limit 2x", "limit 3x",
            "half at 2x, trail 40%"]


# --------------------------------------------------------------------------
# the signal
# --------------------------------------------------------------------------
DROP = {"symbol", "day", "o", "h", "l", "c", "v", "contaminated",
        "up_max", "dn_max", "fwd_close", "up5", "up10", "up20",
        "dn5", "dn10", "dn20", "dollar_vol"}


def score_window(first_day):
    """P(10% move either way within 5 sessions), for every day of the cache.

    One block, one fit. Everything the model learns from ends `EMBARGO` sessions
    before the first day it is asked about, so no leakage path exists -- not
    through the label's own 5-day horizon, and not through a fold boundary.
    """
    f = pd.read_parquet(FEATURES).sort_values(["day", "symbol"])
    if "contaminated" in f:
        f = f[~f["contaminated"].astype(bool)]
    cols = [c for c in f.columns
            if c not in DROP and pd.api.types.is_numeric_dtype(f[c])]

    days = sorted(f["day"].unique())
    cut = [d for d in days if d < first_day]
    if len(cut) <= EMBARGO:
        raise SystemExit("not enough history before the cache window")
    train_end = cut[-EMBARGO]

    f["mover"] = ((f["up10"] == 1) | (f["dn10"] == 1)).astype(float)
    f.loc[f["up10"].isna(), "mover"] = np.nan
    tr = f[f["day"] < train_end].dropna(subset=["mover"])
    te = f[f["day"] >= first_day].copy()
    log("training on {:,} stock-days to {} ; scoring {:,} from {}".format(
        len(tr), train_end, len(te), first_day))
    log("base rate in training: {:.1%} of stock-days see a 10% move in {}d".format(
        tr["mover"].mean(), HORIZON))

    m = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.15,
        random_state=7)
    m.fit(tr[cols], tr["mover"])
    te["p"] = m.predict_proba(te[cols])[:, 1]

    # Did it still work out here? A signal that lost its lift in the window is
    # not worth pricing, and a null would then be about the signal rather than
    # about options -- a distinction worth keeping straight.
    lab = te.dropna(subset=["up10"]).copy()
    if len(lab):
        lab["mover"] = ((lab["up10"] == 1) | (lab["dn10"] == 1)).astype(float)
        base = lab["mover"].mean()
        for top in TOPS:
            hi = lab[lab["p"] >= lab["p"].quantile(1 - top)]
            log("  top {:.0%}: {:.1%} movers vs {:.1%} base = {:.2f}x lift  (n {:,})"
                .format(top, hi["mover"].mean(), base,
                        hi["mover"].mean() / base if base else float("nan"), len(hi)))
    return te[["symbol", "day", "p"]]


def mark(scored):
    """Attach every signal definition as its own boolean column."""
    s = scored.copy()
    for top in TOPS:
        s["top{:.0f}".format(top * 100)] = s["p"] >= s["p"].quantile(1 - top)
    # Cross-sectional rather than global: on a quiet day the live workflow still
    # trades its best three names, and on a wild day it does not trade thirty.
    rank = s.groupby("day")["p"].rank(ascending=False, method="first")
    s["daily{}".format(PER_DAY_N)] = rank <= PER_DAY_N
    return s


# --------------------------------------------------------------------------
# pricing it
# --------------------------------------------------------------------------
def net_columns(t, spreads):
    """Each trade's net multiple under each headline rule, friction on its own premium."""
    s = charge(t["entry"].to_numpy(float), spreads)
    out = t[["symbol", "day", "kind", "otm", "entry", "sid"]].copy()
    for name in HEADLINE:
        out["net::" + name] = t[name].to_numpy(float) * (1 - s / 2) / (1 + s / 2)
    out["spread"] = s
    return out


def pick_legs(t, target_otm=0.05):
    """One call and one put per (symbol, session): the tradeable pair.

    Nearest to `target_otm` among contracts that printed and cost at least
    MIN_PREM, so the choice is made on information available at entry and never
    on what the contract went on to do.
    """
    v = t[t["entry"] >= MIN_PREM].copy()
    v["gap"] = (v["otm"] - target_otm).abs()
    v = v.sort_values("gap")
    return v.groupby(["symbol", "day", "kind"], as_index=False).first()


def line(label, b, rule, base=None):
    """One row of a comparison table, led by the per-session median."""
    col = "net::" + rule
    if len(b) < 25:
        print("    {:<28} n {:>5,}  -- too thin".format(label, len(b)))
        return None
    net = b[col]
    per_sess = pd.DataFrame({"v": net, "d": b["day"]}).groupby("d").v.median()
    med = per_sess.median()
    print("    {:<28} {:>6,} {:>8} {:>9.2f} {:>9.2f}x {:>10.2f}x {:>7.1f}% {:>8.2f} {:>9}"
          .format(label, len(b), b["day"].nunique(), b["entry"].median(), med,
                  net.mean(), (net > 1).mean() * 100, clustered_t(net, b["day"]),
                  "" if base is None else "{:+.2f}x".format(med - base)))
    return med


def compare(t, scored, rule, title, kinds=("CE", "PE")):
    """Signal against control, inside one premium regime, on one exit rule."""
    v = t[t["kind"].isin(kinds)].merge(scored, on=["symbol", "day"], how="inner")
    v = v[v["entry"] >= MIN_PREM]
    print()
    print("=" * 118)
    print("{}   exit = {}   premium >= Rs{:.2f}   n {:,} trades, {} sessions".format(
        title, rule, MIN_PREM, len(v), v["day"].nunique()))
    print("  the bar is 1.00x per session; anything below it is a loss however it "
          "compares to the control")
    print("=" * 118)
    print("    {:<28} {:>6} {:>8} {:>9} {:>10} {:>11} {:>8} {:>8} {:>9}".format(
        "slice", "n", "sess", "med prem", "NET/sess", "net pooled", "win%",
        "clust t", "vs rest"))
    for col in ["top10", "top5", "top2", "daily{}".format(PER_DAY_N)]:
        rest = line("no signal ({})".format(col), v[~v[col]], rule)
        line("SIGNAL {}".format(col), v[v[col]], rule, base=rest)
    return v


def pair_line(label, sub, base=None):
    """One row of the pair table. Returns the per-session median, or None if thin."""
    if len(sub) < 25:
        print("    {:<28} n {:>5,}  -- too thin".format(label, len(sub)))
        return None
    per = pd.DataFrame({"v": sub["net"], "d": sub["day"]}).groupby("d").v.median()
    med = per.median()
    print("    {:<28} {:>6,} {:>8} {:>9.2f} {:>9.2f}x {:>10.2f}x {:>7.1f}% {:>8.2f} {:>9}"
          .format(label, len(sub), sub["day"].nunique(), sub["cost"].median(), med,
                  sub["net"].mean(), (sub["net"] > 1).mean() * 100,
                  clustered_t(sub["net"], sub["day"]),
                  "" if base is None else "{:+.2f}x".format(med - base)))
    return med


def pair_table(t, scored, rule):
    """Both legs, which is what an AUC-0.510 side prediction actually implies."""
    legs = pick_legs(t).merge(scored, on=["symbol", "day"], how="inner")
    col = "net::" + rule
    # A pair needs both legs to exist and both to have printed.
    w = legs.pivot_table(index=["symbol", "day"], columns="kind",
                         values=["entry", col], aggfunc="first").dropna()
    if not len(w):
        log("no complete pairs")
        return
    ce_c, pe_c = w[("entry", "CE")], w[("entry", "PE")]
    ce_r, pe_r = w[(col, "CE")], w[(col, "PE")]
    pair = (ce_c * ce_r + pe_c * pe_r) / (ce_c + pe_c)
    p = pd.DataFrame({"net": pair, "cost": ce_c + pe_c}).reset_index()
    p = p.merge(scored, on=["symbol", "day"], how="left")

    print()
    print("=" * 118)
    print("PAIR (call + put, each exited on its own)   exit = {}   {:,} pairs, {} sessions"
          .format(rule, len(p), p["day"].nunique()))
    print("  no side is predicted, so no side is chosen; each leg pays its own friction")
    print("=" * 118)
    print("    {:<28} {:>6} {:>8} {:>9} {:>10} {:>11} {:>8} {:>8} {:>9}".format(
        "slice", "n", "sess", "med cost", "NET/sess", "net pooled", "win%",
        "clust t", "vs rest"))
    for c in ["top10", "top5", "top2", "daily{}".format(PER_DAY_N)]:
        rest = pair_line("no signal ({})".format(c), p[~p[c]])
        pair_line("SIGNAL {}".format(c), p[p[c]], base=rest)


def main():
    t = pd.read_parquet(TRADES)
    spreads = load_spreads()
    first = min(t["day"])
    log("{:,} trades on the cache, {} .. {}".format(len(t), first, max(t["day"])))

    scored = mark(score_window(first))
    n = t.merge(scored[["symbol", "day"]], on=["symbol", "day"], how="inner")
    log("{:,} of {:,} trades fall on a scored stock-day ({:.0%})".format(
        len(n), len(t), len(n) / len(t)))

    priced = net_columns(t, spreads)

    for rule in ["trail 30% of peak", "limit 2x", "hold to 10d"]:
        compare(priced, scored, rule, "CALLS ONLY", kinds=("CE",))
        compare(priced, scored, rule, "PUTS ONLY", kinds=("PE",))
        pair_table(priced, scored, rule)

    priced.merge(scored, on=["symbol", "day"], how="left").to_parquet(
        os.path.join(HERE, "otm_signal.parquet"), index=False)
    log("wrote research/otm_signal.parquet")


if __name__ == "__main__":
    main()
