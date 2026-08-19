"""Is "further out, conditional on a predicted move" real, or the strike list again?

THE CLAIM UNDER TEST.  On the run-1 cache, cutting SIGNAL top-5% calls by
moneyness gave a clean monotone gross gradient -- 0.623 / 0.698 / 0.825 / 1.081 /
1.279 -- with +12-25% OTM netting 1.174x.  That is the only cell in this
programme that has ever cleared 1.0 after friction, and it is an interaction
rather than a main effect: UNCONDITIONALLY further out is monotonically WORSE
(that is [[deep-otm-unconditional-null]]), so the claim is specifically that a
predicted move flips the sign of the moneyness gradient.  Which is not absurd --
a 10% move is worth far more to a strike 15% out than to one 3% out -- and is
exactly the kind of clean, large, monotone result this programme has already had
manufactured for it twice.

SO IT IS GUILTY UNTIL IT PASSES FOUR CHECKS, all printed whether they pass or
fail, none of them optional:

  1. PER-SESSION MEDIAN, not the pooled mean.  1.174x pooled came with a 0.77x
     median and a 34.6% win rate.  That is a lottery, and a lottery that needs
     one 20x trade to carry 57 sessions is not a strategy at Rs1L -- the same
     reason [[nifty-trail-gap-exit-finding]] killed risk-dialling.

  2. COMPOSITION STABILITY.  On run 1 the far-OTM share of the signal sample fell
     54.1% -> 28.0% across the window, which is the signature of an as-of-today
     strike list and bites hardest in precisely the band that looks good.  The
     as-of-date top-up is what settles this; if the share still collapses, the
     cell is an artifact and nothing else here matters.

  3. THE SAME CUT ON PUTS.  A real interaction should show up on both sides,
     because the signal predicts MOTION and not direction.  Calls-only is what a
     six-week up-drift produces.

  4. LEAVE-ONE-SESSION-OUT.  Drop the single best session and re-read.  If the
     cell dies, one day is carrying it.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import charge, clustered_t, load_spreads, log  # noqa: E402

SIG = os.path.join(HERE, "otm_signal.parquet")
BANDS = [(-0.02, 0.02), (0.02, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.25)]
RULE = "net::trail 30% of peak"
MIN_PREM = 2.50


def gross_of(v, spreads):
    """Undo the net so gross and net can be read side by side."""
    s = charge(v["entry"].to_numpy(float), spreads)
    return v[RULE].to_numpy(float) * (1 + s / 2) / (1 - s / 2)


def gradient(v, title):
    print()
    print("=" * 118)
    print(title)
    print("=" * 118)
    print("  {:<16} {:>7} {:>6} {:>9} {:>9} {:>10} {:>8} {:>8} {:>8} {:>9}".format(
        "OTM band", "n", "sess", "med prem", "GROSS", "NET/sess", "net avg",
        "win%", ">=2x%", "clust t"))
    for lo, hi in BANDS:
        b = v[(v["otm"] >= lo) & (v["otm"] < hi)]
        if len(b) < 25:
            print("  {:>+5.0%}..{:<+5.0%}   n {:>5,}  -- too thin".format(lo, hi, len(b)))
            continue
        net = b[RULE]
        per = pd.DataFrame({"v": net, "d": b["day"]}).groupby("d").v.median()
        print("  {:>+5.0%}..{:<+5.0%} {:>9,} {:>6} {:>9.2f} {:>8.3f}x {:>9.3f}x {:>9.3f}x "
              "{:>7.1f}% {:>7.1f}% {:>9.2f}".format(
                  lo, hi, len(b), b["day"].nunique(), b["entry"].median(),
                  np.mean(b["gross"]), per.median(), net.mean(),
                  (net > 1).mean() * 100, (b["gross"] >= 2).mean() * 100,
                  clustered_t(net, b["day"])))


MIN_WK = 20      # a week with 1 trade in it has no share worth reading


def composition(v, title):
    """Check 2, with a minimum weekly count -- WITHOUT which it lies both ways.

    The signal's top-5% threshold is global, so the first weeks of the window
    carry one or two names each. Reading a "far-OTM share" off n=1 gave 0.0% for
    calls (verdict: stable) and 25.0% for puts (verdict: collapses) from single
    trades, in both cases overriding what the populated weeks plainly showed.
    Weeks below `MIN_WK` are printed but excluded from the verdict.
    """
    print()
    print("  {} -- far-OTM share by week (check 2)".format(title))
    w = v.copy()
    w["wk"] = pd.to_datetime(w["day"]).dt.to_period("W").astype(str).str[:10]
    p = w.groupby("wk").agg(n=("otm", "size"), far=("otm", lambda s: (s >= 0.12).mean()))
    for wk, r in p.iterrows():
        print("    {}  n {:>6,}   share >=+12% otm {:>6.1%}{}".format(
            wk, int(r["n"]), r["far"], "   (thin -- excluded)" if r["n"] < MIN_WK else ""))
    p = p[p["n"] >= MIN_WK]
    if len(p) >= 4:
        head, tail = p["far"].iloc[:2].mean(), p["far"].iloc[-2:].mean()
        print("    {:.1%} at the start vs {:.1%} at the end, over {} populated weeks  ->  {}"
              .format(head, tail, len(p),
                      "COLLAPSES -- the cell is composition, not edge"
                      if tail < head * 0.6 else "stable -- composition is not the explanation"))
    else:
        print("    only {} weeks clear n>={} -- check 2 cannot be read".format(len(p), MIN_WK))


def loo(v, title):
    """Check 4: does one session carry it?"""
    b = v[(v["otm"] >= 0.12) & (v["otm"] < 0.25)]
    if len(b) < 25:
        print("\n  {} -- +12-25% band too thin for leave-one-out".format(title))
        return
    per = pd.DataFrame({"v": b[RULE], "d": b["day"]}).groupby("d").v.mean()
    tot = b[RULE].mean()
    worst = per.idxmax()
    drop = b[b["day"] != worst]
    print("\n  {} -- leave-one-session-out on +12-25% (check 4)".format(title))
    print("    pooled net {:.3f}x over {} sessions; best session {} at {:.2f}x"
          .format(tot, b["day"].nunique(), worst, per.max()))
    print("    without it: {:.3f}x   ->  {}".format(
        drop[RULE].mean(),
        "one day was carrying it" if drop[RULE].mean() < 1.0 <= tot else "survives the drop"))


def main():
    spreads = load_spreads()
    d = pd.read_parquet(SIG)
    d = d[d["entry"] >= MIN_PREM].copy()
    d["gross"] = gross_of(d, spreads)
    log("{:,} priced trades, {} sessions, {} .. {}".format(
        len(d), d["day"].nunique(), d["day"].min(), d["day"].max()))

    for kind, word in (("CE", "CALLS"), ("PE", "PUTS")):
        k = d[d["kind"] == kind]
        sig = k[k["top5"].astype(bool)]
        gradient(k[~k["top5"].astype(bool)], "{} -- NO SIGNAL (the control gradient)".format(word))
        gradient(sig, "{} -- SIGNAL top 5% (check 1: read NET/sess, not net avg)".format(word))
        composition(sig, "{} signal".format(word))
        loo(sig, "{} signal".format(word))


if __name__ == "__main__":
    main()
