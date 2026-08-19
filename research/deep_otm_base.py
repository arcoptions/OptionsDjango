"""The true base rate of 2x/5x/10x, measured on real deep-OTM contracts.

WHY THIS NUMBER DOES NOT EXIST YET.  Every multiple reported in this programme
came off the rolling ATM+-3 feed, and that feed has two defects that this cache
does not:

  1. IT CANNOT SEE THE TRADE.  ATM+2 is ~2.6% out of the money on a Rs50 ladder.
     The HAL contract that went Rs22.20 -> Rs199.00 was 7.8% out.  A study that
     cannot reach the strike cannot rule the strike in or out.

  2. ITS MISSING DATA IS THE OUTCOME.  The feed is ATM-relative, so a strike
     stops being quoted exactly when spot walks away from it -- and unquoted bars
     were imputed at intrinsic, which is ZERO for an OTM call.  Measured on
     `premove_otm.csv`: quote coverage correlates with the size of the
     underlying move at Spearman -0.436, mean |move| falling monotonically from
     6.85% in the worst-quoted bucket to 3.18% in the best.  So filtering on
     quote quality filters on the answer.  There is no clean subset.

This cache has neither defect.  Strikes are ABSOLUTE and pinned, quotes come
from the contract's own tape, and a zero-volume day is a real zero-volume day
rather than an imputation.  What it buys in cleanliness it pays for in span:
roughly six weeks, one regime.  So this file deliberately reports base rates and
liquidity -- quantities a single regime can support -- and does not try to
settle a strategy on six weeks of one market.

THE TWO HONEST DIFFICULTIES, stated up front rather than buried.

  ENTRY IS AT THE CLOSE and the multiple is measured against the running HIGH.
  A high is a touch, not a fill.  Every multiple here is therefore an upper
  bound in the same way the 8.96x on HAL was: real, printed, and not necessarily
  obtainable.  The first-touch exit column is the honest counterpart.

  NO BID/ASK.  The historical endpoint serves OHLCV and OI only.  A Rs3 stock
  option is not quoted five paise wide, and five paise is an INDEX assumption
  imported wholesale.  Nothing here is called net of friction; the spread has to
  be measured off the live chain separately before any of these become returns.

THE THIRD DIFFICULTY, WHICH ALREADY BIT ONCE.  This file is only as clean as the
cache behind it, and specifically it must not be run against a cache that is
still downloading.  Its first run -- on the first 1,500 contracts of a 15,280
contract pull -- reported that a +8-12% OTM call reaches 2x within ten sessions
63% of the time and that the MEDIAN such trade returns 1.28x.  The finished cache
says 26.5% and 0.61x.  Same band, same code, same contracts.

The cause is the download ORDER, not the strike band, which is what I first
blamed.  `fetch_deep_otm.py` sorts its worklist by distance from today's spot so
that a partial run is still usable -- and that is exactly what makes a prefix of
it a biased sample.  The nearest strikes to today's price are, run backwards, the
strikes the underlying WALKED TOWARDS.  Their moneyness converges to zero at the
end of the window by construction (for K ~ S_today, moneyness on day D is
S_today/S_D - 1), which is what the bias check below detects.  It fires correctly
on the partial cache and passes correctly on the complete one, so read it as a
partial-sample detector rather than a band check.
"""
import datetime as dt
import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.models import StockEquityCandle  # noqa: E402

HORIZONS = [3, 5, 10]
MULTS = [2, 3, 5, 10]
# Buckets on strike/spot - 1, signed so a call and a put both read "how far out
# of the money", positive = further out.
BANDS = [(-0.02, 0.02), (0.02, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.20)]


def load_cache():
    parts = sorted(glob.glob("research/deep_otm.part*.parquet"))
    if os.path.exists("research/deep_otm.parquet"):
        d = pd.read_parquet("research/deep_otm.parquet")
    elif parts:
        d = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        print("  (using {} partial shards -- download still running)".format(len(parts)))
    else:
        raise SystemExit("no deep_otm data on disk yet")
    d["day"] = pd.to_datetime(d["ts"]).dt.date
    return d.sort_values(["sid", "day"])


def daily_spot():
    """Last close per symbol per session, from the equity feed."""
    rows = StockEquityCandle.objects.values_list("symbol", "timestamp", "close")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "spot"])
    f["day"] = pd.to_datetime(f["ts"], utc=True).dt.tz_convert(
        "Asia/Kolkata").dt.tz_localize(None).dt.date
    f["spot"] = pd.to_numeric(f["spot"], errors="coerce")
    return (f.sort_values("ts").groupby(["symbol", "day"], as_index=False)
            .spot.last())


def build_trades(d, spot):
    """One candidate trade per (contract, session) with real volume.

    Entry at that session's close; forward maxima taken from later sessions of
    the SAME contract, so nothing is stitched across strikes.
    """
    d = d.merge(spot, on=["symbol", "day"], how="left")
    d = d[np.isfinite(d["spot"]) & (d["spot"] > 0) & (d["close"] > 0)].copy()

    # Signed moneyness: how far OUT of the money, whichever side we are on.
    d["otm"] = np.where(d["kind"] == "CE",
                        d["strike"] / d["spot"] - 1.0,
                        1.0 - d["strike"] / d["spot"])
    d["dte"] = (pd.to_datetime(d["expiry"]).dt.date - d["day"]).apply(lambda x: x.days)

    g = d.groupby("sid", sort=False)
    for h in HORIZONS:
        # Highest HIGH over the next h sessions of this contract. Reversed
        # rolling max is the readable way to get a forward window; shift(-1)
        # first so the entry session's own high cannot count as an exit.
        d["mfe{}".format(h)] = g["high"].transform(
            lambda s, h=h: s.shift(-1)[::-1].rolling(h, min_periods=1).max()[::-1]
        ) / d["close"]
        d["hold{}".format(h)] = g["close"].shift(-h) / d["close"]
    return d[d["volume"] > 0].copy()


def table(name, v):
    if len(v) < 200:
        print("    {:<26} n {:>6,}  -- too thin".format(name, len(v)))
        return
    print("    {:<26} n {:>6,}   median premium Rs{:>7.2f}   {:>5.2f}% of spot".format(
        name, len(v), v["close"].median(), (v["close"] / v["spot"]).median() * 100))
    for h in HORIZONS:
        col = v["mfe{}".format(h)].dropna()
        if not len(col):
            continue
        hits = "  ".join("{}x {:>5.1f}%".format(m, (col >= m).mean() * 100) for m in MULTS)
        hold = v["hold{}".format(h)].dropna()
        print("        {:>2}d   {}   median MFE {:>5.2f}x   median hold {:>5.2f}x".format(
            h, hits, col.median(), hold.median() if len(hold) else float("nan")))


def main():
    d = load_cache()
    print("=" * 100)
    print("DEEP-OTM CACHE -- coverage")
    print("=" * 100)
    print("  {:,} bars, {:,} contracts, {} symbols, {} .. {}".format(
        len(d), d["sid"].nunique(), d["symbol"].nunique(), d["day"].min(), d["day"].max()))
    print("  expiries: {}".format(", ".join(sorted(d["expiry"].unique()))))
    print("  sessions per contract: median {:.0f}".format(d.groupby("sid").size().median()))

    spot = daily_spot()
    t = build_trades(d, spot)
    print("  {:,} contract-sessions with real volume (of {:,} bars = {:.0%})".format(
        len(t), len(d), len(t) / len(d)))

    # ---- the standing bias check. If the sample's moneyness converges to zero
    # as the window ends, the strike list was selected on today's spot and every
    # tail number below is manufactured. This is not a hypothetical: it is what
    # the first version of this cache did.
    print()
    print("=" * 100)
    print("BIAS CHECK -- is the sample's moneyness converging to ATM by construction?")
    print("  A healthy cache holds its far-OTM share roughly flat across the window.")
    print("=" * 100)
    ce = t[t["kind"] == "CE"].copy()
    ce["wk"] = pd.to_datetime(ce["day"]).dt.to_period("W").astype(str).str[:10]
    prof = ce.groupby("wk").agg(n=("otm", "size"), mean_otm=("otm", "mean"),
                                far=("otm", lambda s: (s > 0.05).mean()))
    for wk, r in prof.iterrows():
        print("    {}  n {:>7,}   mean otm {:>+6.2%}   share >+5% otm {:>5.1%}".format(
            wk, int(r["n"]), r["mean_otm"], r["far"]))
    if len(prof) >= 4:
        head, tail = prof["far"].iloc[:2].mean(), prof["far"].iloc[-2:].mean()
        verdict = ("LOOKS BIASED -- far-OTM share collapses; do not trust the tails below"
                   if tail < head * 0.5 else "no collapse -- proceed")
        print("    far-OTM share {:.1%} at the start vs {:.1%} at the end  ->  {}".format(
            head, tail, verdict))

    print()
    print("=" * 100)
    print("LIQUIDITY BY MONEYNESS -- can these strikes actually be traded?")
    print("=" * 100)
    print("    {:<14} {:>9} {:>12} {:>12} {:>14}".format(
        "OTM band", "bars", "with volume", "median vol", "median OI"))
    dd = d.merge(spot, on=["symbol", "day"], how="left")
    dd["otm"] = np.where(dd["kind"] == "CE", dd["strike"] / dd["spot"] - 1.0,
                         1.0 - dd["strike"] / dd["spot"])
    for lo, hi in BANDS:
        v = dd[(dd["otm"] >= lo) & (dd["otm"] < hi)]
        if not len(v):
            continue
        print("    {:>+5.0%}..{:<+5.0%} {:>9,} {:>11.0%} {:>12,.0f} {:>14,.0f}".format(
            lo, hi, len(v), (v["volume"] > 0).mean(), v["volume"].median(),
            v["oi"].median()))

    for kind, word in (("CE", "CALLS"), ("PE", "PUTS")):
        print()
        print("=" * 100)
        print("{} -- unconditional base rate of reaching a multiple".format(word))
        print("  entry at the session close; multiple measured against the running HIGH")
        print("=" * 100)
        k = t[t["kind"] == kind]
        for lo, hi in BANDS:
            table("{:>+.0%} .. {:<+.0%}".format(lo, hi),
                  k[(k["otm"] >= lo) & (k["otm"] < hi)])

    print()
    print("=" * 100)
    print("DAYS TO EXPIRY -- does the tail need time, or does it need cheapness?")
    print("=" * 100)
    k = t[(t["kind"] == "CE") & (t["otm"] >= 0.05)]
    for lo, hi in [(0, 7), (7, 14), (14, 21), (21, 35), (35, 70)]:
        table("dte {:>2}-{:<2}".format(lo, hi), k[(k["dte"] >= lo) & (k["dte"] < hi)])


if __name__ == "__main__":
    main()
