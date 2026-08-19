"""Does the frozen side-picker convert into option money -- and does the exit matter?

WHAT THIS SETTLES.  `side_picker.py` found the first thing in this programme to
survive a frozen out-of-sample test: inside the top IV-rank decile, stocks BELOW
their EMAs gap up at 1.92x the base rate while stocks ABOVE them gap down at
1.87x.  That is measured on the UNDERLYING.  Every previous underlying signal in
this programme died when it was priced against real option premiums, because IV
already charged for the move.  So the rule is worth exactly nothing until it is
run through real contracts, which is what this file does.

It answers three questions at once, and they are separable:

  1. THE RULE.  Do rule-selected calls beat non-rule calls on the same feed?
  2. THE EXIT.  `premove_otm.csv` already carries first-touch exits at 1.5x, 2x
     and 3x alongside the 5-session hold.  The user's point was that HAL ran
     Rs22 -> Rs199 -> Rs121, so WHEN you leave decides most of the outcome.
     Nothing in this programme has reported the exit column.
  3. THE MARK.  This is the one that may invalidate the other two.

ON THE MARK, WHICH IS NOT A FOOTNOTE.  The rolling feed is ATM-relative, so a
pinned strike stops being quoted once spot walks away, and `premove_otm.py`
imputes every missing bar at intrinsic.  For a call that has gone OTM, intrinsic
is ZERO -- so a contract that still has real time value is marked at nothing the
moment its quote disappears.  The median trade here is quoted on only 29% of its
held bars.  The original note claims "a loss measured this way is robust"; that
is true for the ITM case it was written about and BACKWARDS for the OTM case,
where the mark overstates the loss.  Since almost every result in this programme
is a measured loss, this is a live threat to all of them.

So every table below is repeated on the well-quoted subset.  If the losses shrink
as quote coverage rises, the nulls are partly an artifact of the mark and the
honest read is that this feed cannot price these trades at all.

WHAT ACTUALLY HAPPENED, recorded because the prediction was wrong and the reason
matters more than the prediction did.  The losses did not shrink as coverage
rose -- they GREW, monotonically, from +11.5% at 0-20% coverage to -29.6% at
60-80%.  The hypothesis above has the mechanism backwards.  `quoted` is not a
data-quality variable at all: it is an OUTCOME variable.  The feed is
ATM-relative, so a strike loses its quote exactly when spot walks away from it,
which is to say exactly when the underlying made a big move.  Low coverage
therefore SELECTS the big movers, and the +11.5% in that bucket is look-ahead,
not conservatism.

The consequence is worse than the artifact it was checking for.  Neither subset
is clean: the poorly-quoted rows are selected for having moved, and the
well-quoted rows are selected for having sat still.  The missing-data mechanism
IS the outcome, so no filter on this feed can separate them.  That is a
structural limit on every stock-option number this programme has produced, and
it is one more reason the pinned-strike deep-OTM cache -- real quotes, absolute
strikes, no imputation -- is the only thing that can settle the question.
"""

DISCIPLINE.  The rule's parameters (iv_rank >= 0.70, split on ema_stack) were
frozen in `side_picker.py` on the first 60% of feature days.  The same cut is
applied here and every number is scored on test days only.  Nothing is tuned.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from option_spreads import day_t  # noqa: E402

FEATURES = "research/premove_features.parquet"
IV_CUT = 0.70      # frozen in side_picker.py, not swept here
SPLIT = 0.60       # same train/test cut as side_picker.py
EXITS = [("hold 5 sessions", "hold"), ("first touch 1.5x", "t1.5"),
         ("first touch 2x", "t2"), ("first touch 3x", "t3")]


def load_features():
    """iv_rank and ema_stack per (symbol, signal day), plus the frozen cut date."""
    f = pd.read_parquet(FEATURES)[["symbol", "day", "iv_rank", "ema_stack"]].copy()
    days = np.sort(f["day"].unique())
    cut = days[int(len(days) * SPLIT)]
    # The option row is stamped with the ENTRY day, which is the session AFTER
    # the features were observed. Join on that, or the rule reads tomorrow's
    # feature into today's trade.
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    f["entry"] = f["day"].map(nxt)
    return f.dropna(subset=["entry"]), cut


def tag(trades, feats, cut):
    # The CSV carries `day` as text and the parquet as datetime.date; merging
    # them straight through silently matches nothing.
    trades = trades.copy()
    trades["day"] = pd.to_datetime(trades["day"]).dt.date
    d = trades.merge(feats, left_on=["symbol", "day"], right_on=["symbol", "entry"],
                     how="inner", suffixes=("", "_f"))
    d = d[d["day"] >= cut].copy()          # test days only
    d["hi_iv"] = d["iv_rank"] >= IV_CUT
    d["below"] = d["ema_stack"] <= 0
    return d


def block(name, v, label_cost="cost"):
    """One row per exit rule. Money-weighted and per-session, because they
    disagree whenever a handful of trades carry the column."""
    if len(v) < 60:
        print("    {:<32} n {:>5,}  -- too thin to report".format(name, len(v)))
        return
    head = "    {:<32} n {:>5,}   prem {:>5.2f}% spot   quoted {:>3.0f}%".format(
        name, len(v), (v.cost / v.spot).median() * 100, v.quoted.mean() * 100)
    print(head)
    for lab, col in EXITS:
        pooled = v[col].sum() / v[label_cost].sum() * 100
        persess = (v.groupby("day").apply(
            lambda x: x[col].sum() / x.cost.sum() * 100, include_groups=False))
        win = (v[col] > 0).mean() * 100
        t = day_t(v[col].values, v.day.values)
        print("        {:<20} pooled {:>+7.1f}%   per-sess med {:>+7.1f}%   "
              "win {:>4.1f}%   t {:>+5.2f}".format(lab, pooled, persess.median(), win, t))
    mult = (v.best + v.cost) / v.cost
    print("        {:<20} >=2x {:>4.1f}%   >=5x {:>4.1f}%   >=10x {:>4.1f}%   "
          "median MFE {:>+6.1f}%".format(
              "perfect exit (MFE)", (mult >= 2).mean() * 100, (mult >= 5).mean() * 100,
              (mult >= 10).mean() * 100, (v.best / v.cost).median() * 100))


def main():
    feats, cut = load_features()
    ce = pd.read_csv("research/premove_otm.csv")
    print("=" * 104)
    print("PRICING THE FROZEN SIDE-PICKER ON REAL CALLS")
    print("  rule frozen on days < {}; every number below is on days >= {} only".format(cut, cut))
    print("=" * 104)

    d = tag(ce, feats, cut)
    print("\n  {:,} call trades on test days, {} symbols, {} sessions, {} rungs".format(
        len(d), d.symbol.nunique(), d.day.nunique(), d.rung.nunique()))

    for rung in ["ATM", "ATM+1", "ATM+2"]:
        r = d[d.rung == rung]
        if not len(r):
            continue
        print("\n" + "-" * 104)
        print("  RUNG {}".format(rung))
        print("-" * 104)
        block("all calls (control)", r)
        block("RULE: high IV + below EMAs", r[r.hi_iv & r.below])
        block("high IV + above EMAs (wrong side)", r[r.hi_iv & ~r.below])
        block("low IV (control)", r[~r.hi_iv])

    # ---- the mark. If losses shrink as quote coverage rises, the feed is the
    # problem rather than the trade.
    print("\n" + "=" * 104)
    print("IS THE LOSS REAL, OR IS IT THE MARK?")
    print("  An unquoted OTM call is marked at intrinsic = ZERO. If that is driving")
    print("  the result, the loss must shrink as real quote coverage rises.")
    print("=" * 104)
    for lo, hi in [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.01)]:
        v = d[(d.quoted >= lo) & (d.quoted < hi)]
        if len(v) < 60:
            continue
        pooled = v.hold.sum() / v.cost.sum() * 100
        t2 = v["t2"].sum() / v.cost.sum() * 100
        mult = (v.best + v.cost) / v.cost
        print("    quoted {:>3.0f}-{:<3.0f}%  n {:>6,}   hold {:>+7.1f}%   2x-exit {:>+7.1f}%"
              "   >=2x {:>4.1f}%   >=5x {:>4.1f}%".format(
                  lo * 100, hi * 100, len(v), pooled, t2,
                  (mult >= 2).mean() * 100, (mult >= 5).mean() * 100))

    print("\n  Same cut, but only trades quoted on at least 60% of their bars:")
    wq = d[d.quoted >= 0.60]
    for rung in ["ATM", "ATM+1", "ATM+2"]:
        r = wq[wq.rung == rung]
        if len(r) < 60:
            continue
        print("\n  RUNG {} -- well quoted only".format(rung))
        block("all calls (control)", r)
        block("RULE: high IV + below EMAs", r[r.hi_iv & r.below])

    # ---- the put side, thinner but it is the half the brief asks for
    pe_path = "research/premove_pe.csv"
    if os.path.exists(pe_path):
        pe = pd.read_csv(pe_path)
        p = tag(pe, feats, cut)
        print("\n" + "=" * 104)
        print("THE PUT SIDE (ATM only, thinner feed)")
        print("  The rule inverts here: high IV + ABOVE EMAs is the PE candidate.")
        print("=" * 104)
        if len(p):
            print("\n  {:,} put trades on test days, {} sessions".format(len(p), p.day.nunique()))
            block("all puts (control)", p)
            block("RULE: high IV + above EMAs", p[p.hi_iv & ~p.below])
            block("high IV + below EMAs (wrong side)", p[p.hi_iv & p.below])


if __name__ == "__main__":
    main()
