"""Charge the sell side at the price it actually sells at.

WHY THIS IS NOT A DETAIL.  `otm_exits.py` prices a round trip as
`gross * (1 - s/2) / (1 + s/2)` with a SINGLE `s` looked up on the ENTRY
premium.  That is right for the buy and wrong for the sell, because the spread
here is governed by premium and the premium moves: an option bought at Rs12 and
trailed out at Rs4.8 is bought in the Rs10-25 bucket at 6.8% and sold in the
Rs2.5-5 bucket at 9.9%.  The error is not symmetric across the distribution
either -- two thirds of these trades lose, so most exits happen DOWN the friction
ladder where it is steeper, while the handful of 3x winners exit up it where it
is flat.  A single entry-keyed rate therefore flatters the losers and taxes the
winners, which is exactly backwards for a strategy whose whole case rests on a
fat right tail.

WHAT IT CANNOT FIX.  The sell-side rate is still a MEDIAN quote for the bucket,
and the 75th percentile is far worse (66.7% at Rs1-2.5).  A trailing exit fires
on the way down, when the book is thinnest, so even this is optimistic.  It moves
the number in the honest direction; it does not make it conservative.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import RULES, charge, clustered_t, load_spreads, log  # noqa: E402

TRADES = os.path.join(HERE, "otm_exits.parquet")
MIN_PREM = 2.50


def net_two_sided(entry, gross, spreads):
    """Buy at mid + half the entry spread, sell at mid - half the EXIT spread."""
    entry = np.asarray(entry, float)
    gross = np.asarray(gross, float)
    s_in = charge(entry, spreads)
    s_out = charge(entry * gross, spreads)
    return gross * (1 - s_out / 2) / (1 + s_in / 2)


def net_one_sided(entry, gross, spreads):
    s = charge(entry, spreads)
    return gross * (1 - s / 2) / (1 + s / 2)


def block(t, title, rules):
    print()
    print("=" * 112)
    print(title)
    print("=" * 112)
    print("  {:<24} {:>7} {:>9} {:>10} {:>11} {:>11} {:>9} {:>9}".format(
        "exit rule", "n", "GROSS", "net(entry)", "net(2-sided)", "cost of the",
        "med exit", "clust t"))
    print("  {:<24} {:>7} {:>9} {:>10} {:>11} {:>11} {:>9} {:>9}".format(
        "", "", "", "per sess", "per sess", "correction", "premium", "2-sided"))
    for name in rules:
        g = t[name].to_numpy(float)
        ok = np.isfinite(g)
        if ok.sum() < 200:
            continue
        sub = t[ok]
        g = g[ok]
        one = net_one_sided(sub["entry"], g, SPREADS)
        two = net_two_sided(sub["entry"], g, SPREADS)
        f = pd.DataFrame({"one": one, "two": two, "g": g, "d": sub["day"].to_numpy()})
        per = f.groupby("d").median()
        print("  {:<24} {:>7,} {:>8.3f}x {:>9.3f}x {:>10.3f}x {:>10.3f}x {:>9.2f} {:>9.2f}"
              .format(name, len(sub), per["g"].median(), per["one"].median(),
                      per["two"].median(), per["two"].median() - per["one"].median(),
                      np.median(sub["entry"].to_numpy(float) * g),
                      clustered_t(two, sub["day"])))


SPREADS = load_spreads()
t = pd.read_parquet(TRADES)
t = t[t["entry"] >= MIN_PREM].copy()
log("{:,} trades at premium >= Rs{:.2f}, {} sessions".format(
    len(t), MIN_PREM, t["day"].nunique()))

names = [n for n, _ in RULES]
block(t[t["kind"] == "CE"], "CALLS -- what the round trip really costs", names)
block(t[t["kind"] == "PE"], "PUTS -- what the round trip really costs", names)

# Where does the correction land? If it is concentrated in the losers, the tail
# the brief cares about is untouched and the null merely deepens; if it eats the
# winners too, the ceiling is lower than reported.
print()
print("=" * 112)
print("WHERE THE CORRECTION LANDS -- calls, trail 30% of peak")
print("=" * 112)
c = t[t["kind"] == "CE"].copy()
g = c["trail 30% of peak"].to_numpy(float)
c = c[np.isfinite(g)].copy()
c["g"] = g[np.isfinite(g)]
c["one"] = net_one_sided(c["entry"], c["g"], SPREADS)
c["two"] = net_two_sided(c["entry"], c["g"], SPREADS)
c["exit_px"] = c["entry"] * c["g"]
print("  {:<20} {:>8} {:>10} {:>10} {:>10} {:>11} {:>11}".format(
    "gross outcome", "n", "share", "med entry", "med exit", "net(entry)", "net(2-sided)"))
for lab, m in [("wipeout <0.25x", c["g"] < 0.25), ("0.25-0.5x", (c["g"] >= 0.25) & (c["g"] < 0.5)),
               ("0.5-1x", (c["g"] >= 0.5) & (c["g"] < 1.0)), ("1-2x", (c["g"] >= 1.0) & (c["g"] < 2.0)),
               ("2-3x", (c["g"] >= 2.0) & (c["g"] < 3.0)), (">=3x", c["g"] >= 3.0)]:
    v = c[m]
    if not len(v):
        continue
    print("  {:<20} {:>8,} {:>9.1f}% {:>10.2f} {:>10.2f} {:>10.3f}x {:>10.3f}x".format(
        lab, len(v), len(v) / len(c) * 100, v["entry"].median(), v["exit_px"].median(),
        v["one"].mean(), v["two"].mean()))
print("  {:<20} {:>8,} {:>9.1f}% {:>10.2f} {:>10.2f} {:>10.3f}x {:>10.3f}x".format(
    "ALL (pooled mean)", len(c), 100.0, c["entry"].median(), c["exit_px"].median(),
    c["one"].mean(), c["two"].mean()))
