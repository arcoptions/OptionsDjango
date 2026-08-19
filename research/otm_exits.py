"""Which EXIT converts a deep-OTM move into money?

THE HALF OF THE BRIEF NOBODY HAS MEASURED.  The user's framing was "you just
have to find the right entry and right exit", and every study in this programme
so far has spent itself on the entry.  Their own example is the argument: the
HAL 5000 CE ran Rs22.20 -> Rs199.00 -> Rs121.85.  Hold-to-horizon collects the
Rs121.85.  The move was 9.0x and the fixed hold banks 5.5x, so on that contract
the exit rule is worth more than a 60% swing in the entry hit rate.  This is also
what [[nifty-trail-gap-exit-finding]] found on the index, where the exit turned
out to be the largest single lever measured anywhere in this programme.

WHAT IS AND IS NOT FILLABLE ON DAILY BARS.  This distinction is the whole
integrity of the file, so it is drawn explicitly rather than assumed:

  A LIMIT SELL AT Nx IS FILLABLE and is not survivorship.  A resting limit order
  executes when the tape trades through it, so `high >= N * entry` is exactly the
  condition for a fill at N.  If the contract gaps straight past the limit the
  fill is still booked at N, never at the better price.  What this does NOT
  capture is size: a limit at 3x on a contract quoted Rs3 may fill for two lots
  and not twenty.  That is a liquidity question, answered by the OI/volume table
  in `deep_otm_base.py`, not a logic error here.

  A TRAILING STOP ON INTRADAY HIGHS IS NOT FILLABLE, because a daily bar does not
  say whether the high came before or after the low.  Every stop in this file is
  therefore evaluated on CLOSES only -- look at the close, decide, exit at that
  same close.  That is implementable by anyone with an end-of-day routine and it
  cannot peek at the intraday path.

  WHEN A TARGET AND A STOP COULD BOTH FIRE ON ONE DAY, THE STOP WINS.  Same
  reason: the ordering is unknowable, so the rule takes the worse branch.  This
  biases every combination rule DOWNWARDS, which is the correct direction for a
  study trying not to fool itself.

FRICTION IS APPLIED, NOT WAVED AT.  `deep_otm_base.py` deliberately reports gross
because the historical endpoint carries no bid/ask.  `spread_curve.py` measures
the real curve off the live chain, and it is steep: ~0.7% of mid at the money,
2.6% at +2%, 4.6-7.2% at +4-5%, 12.6% at +6%, 34% at +7% on the first symbol
sampled.  So here the buyer pays half the spread in and half out, and SPREAD is
set per moneyness band from that measurement rather than from an index habit.
Both gross and net are printed; the gap between them is the honest cost of
reaching further out.

READ THE PER-SESSION MEDIAN, NOT THE POOLED MEAN.  Option winners cluster --
one market-wide rally pays hundreds of calls at once -- so a pooled mean treats
thousands of trades as independent when they sit in a few dozen sessions.  That
statistic has manufactured a strategy twice already in this programme
([[stock-option-spread-null]], [[premove-motion-not-direction]]).  Every table
below therefore leads with the per-session median and a day-clustered t.

THIS FILE IS ONLY AS CLEAN AS THE CACHE UNDER IT.  If the strike list was built
against today's spot rather than as-of-date, the tails are manufactured -- see
[[otm-strike-selection-bias]].  Run `deep_otm_base.py` first and read its bias
check before believing anything here.
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

HOLD = 10                 # sessions; the cache spans ~6 weeks so this is the ceiling
MIN_PREMIUM = 0.25        # below this the Rs0.05 tick is a fifth of the trade
BANDS = [(-0.02, 0.02), (0.02, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.20)]
PREM_BANDS = [(0.05, 0.5), (0.5, 1.0), (1.0, 2.5), (2.5, 5.0), (5.0, 10.0),
              (10.0, 25.0), (25.0, 1e9)]
# Only two expiries fall in this window, so DTE is badly confounded with the
# calendar and the buckets must be read against the fortnight control in
# `deep_otm_base.py`, not on their own.
DTE_BANDS = [(7, 14), (14, 21), (21, 35), (35, 70)]

# Round-trip spread as a fraction of premium, keyed to PREMIUM rather than
# moneyness, because that is the axis the measurement says governs it: across
# 4,229 live call quotes the median round trip runs 40.0% / 29.5% / 16.0% / 9.9%
# / 8.7% / 6.8% / 7.3% down the premium ladder, and only 4.9% -> 52.2% across the
# moneyness ladder because moneyness is a proxy for premium and a poor one. HAL's
# 5,000 call was 7.8% out of the money and cost Rs22.20 -- deep OTM by moneyness,
# Rs10-25 bucket by premium, ~7% round trip. The same 7.8% strike on a Rs200
# stock costs Rs0.80 and pays 29.5%. Fallbacks apply only if the capture is absent.
SPREAD_FALLBACK = {(0.05, 0.5): 0.400, (0.5, 1.0): 0.295, (1.0, 2.5): 0.160,
                   (2.5, 5.0): 0.099, (5.0, 10.0): 0.087, (10.0, 25.0): 0.068,
                   (25.0, 1e9): 0.073}


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


# --------------------------------------------------------------------------
# the exit rules
# --------------------------------------------------------------------------
def simulate(o, h, l, c, i, rule, hold=HOLD):
    """Walk one trade forward and return (exit_price, sessions_held).

    Entry is `c[i]`, the close of the signal session. `rule` is a dict with any
    of: target (limit sell multiple), stop (close-based, fraction of entry),
    trail (close-based give-back from the best close so far), scale (fraction
    sold at `target`, remainder run under `trail`).
    """
    entry = c[i]
    end = min(i + hold, len(c) - 1)
    if end <= i:
        return entry, 0

    target = rule.get("target")
    stop = rule.get("stop")
    trail = rule.get("trail")
    scale = rule.get("scale")

    peak = entry
    banked, remaining = 0.0, 1.0

    for j in range(i + 1, end + 1):
        # --- stops first, on CLOSES, and they win any same-day tie. A daily bar
        # cannot order the high against the low, so the rule takes the worse
        # branch rather than pretending to know.
        if stop is not None and c[j] <= entry * (1.0 - stop):
            return banked + remaining * c[j], j - i
        if trail is not None and peak > entry and c[j] <= peak * (1.0 - trail):
            return banked + remaining * c[j], j - i

        # --- limit sell: fillable when the tape trades through it, booked AT
        # the limit even on a gap through, never at the better price.
        if target is not None and h[j] >= entry * target:
            fill = entry * target
            if scale is None:
                return banked + remaining * fill, j - i
            banked += remaining * scale * fill      # sell part, run the rest
            remaining *= (1.0 - scale)
            target = None                           # only scales out once

        peak = max(peak, c[j])

    return banked + remaining * c[end], end - i


RULES = [
    ("hold to 10d",            {}),
    ("limit 2x",               {"target": 2.0}),
    ("limit 3x",               {"target": 3.0}),
    ("limit 5x",               {"target": 5.0}),
    ("stop -50% only",         {"stop": 0.50}),
    ("limit 2x + stop -50%",   {"target": 2.0, "stop": 0.50}),
    ("limit 3x + stop -50%",   {"target": 3.0, "stop": 0.50}),
    ("trail 30% of peak",      {"trail": 0.30}),
    ("trail 50% of peak",      {"trail": 0.50}),
    ("trail 50% + stop -50%",  {"trail": 0.50, "stop": 0.50}),
    # The HAL shape directly: bank half the position at 2x, let the rest run on a
    # trail. Rs22 -> Rs199 -> Rs122 is precisely the path a fixed hold wastes.
    ("half at 2x, trail 40%",  {"target": 2.0, "scale": 0.5, "trail": 0.40}),
    ("half at 3x, trail 40%",  {"target": 3.0, "scale": 0.5, "trail": 0.40}),
]


# --------------------------------------------------------------------------
def load_cache():
    parts = sorted(glob.glob("research/deep_otm.part*.parquet"))
    if os.path.exists("research/deep_otm.parquet"):
        d = pd.read_parquet("research/deep_otm.parquet")
    elif parts:
        d = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        log("using {} partial shards -- download still running".format(len(parts)))
    else:
        raise SystemExit("no deep_otm data on disk yet")
    d["day"] = pd.to_datetime(d["ts"]).dt.date
    return d.sort_values(["sid", "day"]).reset_index(drop=True)


def daily_spot():
    rows = StockEquityCandle.objects.values_list("symbol", "timestamp", "close")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "spot"])
    f["day"] = pd.to_datetime(f["ts"], utc=True).dt.tz_convert(
        "Asia/Kolkata").dt.tz_localize(None).dt.date
    f["spot"] = pd.to_numeric(f["spot"], errors="coerce")
    return f.sort_values("ts").groupby(["symbol", "day"], as_index=False).spot.last()


def load_spreads():
    """Real round-trip spread by PREMIUM bucket, from the live chain if captured."""
    if not os.path.exists("research/spread_curve.csv"):
        log("spread_curve.csv not present -- using fallback frictions")
        return dict(SPREAD_FALLBACK)
    q = pd.read_csv("research/spread_curve.csv")
    q = q[q.kind == "CE"]
    out = {}
    for lo, hi in PREM_BANDS:
        b = q[(q.mid >= lo) & (q.mid < hi)]
        out[(lo, hi)] = (b.spread_pct.median() / 100.0 if len(b) >= 20
                         else SPREAD_FALLBACK[(lo, hi)])
    log("frictions from {:,} live call quotes: {}".format(
        len(q), "  ".join("Rs{:g}+ {:.0%}".format(k[0], v) for k, v in out.items())))
    return out


def charge(entry, spreads):
    """Per-trade round trip, looked up on the trade's OWN premium.

    A band average would let a Rs17 ATM call subsidise a Rs0.40 lottery ticket
    sitting in the same moneyness bucket, and the whole point of the measurement
    is that those two are not the same instrument.
    """
    s = np.full(len(entry), SPREAD_FALLBACK[(25.0, 1e9)])
    for (lo, hi), v in spreads.items():
        s = np.where((entry >= lo) & (entry < hi), v, s)
    return s


def build(d, spot):
    d = d.merge(spot, on=["symbol", "day"], how="left")
    d = d[np.isfinite(d["spot"]) & (d["spot"] > 0) & (d["close"] > 0)].copy()
    d["otm"] = np.where(d["kind"] == "CE", d["strike"] / d["spot"] - 1.0,
                        1.0 - d["strike"] / d["spot"])
    d["dte"] = (pd.to_datetime(d["expiry"]).dt.date - d["day"]).apply(lambda x: x.days)
    return d.sort_values(["sid", "day"]).reset_index(drop=True)


def run_rules(d):
    """Every (contract, session) with real volume, run through every exit rule."""
    recs = []
    for sid, g in d.groupby("sid", sort=False):
        o = g["open"].to_numpy(float)
        h = g["high"].to_numpy(float)
        l = g["low"].to_numpy(float)
        c = g["close"].to_numpy(float)
        vol = g["volume"].to_numpy(float)
        days = g["day"].to_numpy()
        otm = g["otm"].to_numpy(float)
        kind = g["kind"].iloc[0]
        sym = g["symbol"].iloc[0]
        dte = g["dte"].to_numpy(float)
        for i in range(len(c) - 1):
            if vol[i] <= 0 or c[i] < MIN_PREMIUM:
                continue
            rec = {"sid": sid, "symbol": sym, "kind": kind, "day": days[i],
                   "otm": otm[i], "entry": c[i], "dte": dte[i]}
            for name, rule in RULES:
                px, held = simulate(o, h, l, c, i, rule)
                rec[name] = px / c[i]
                # Per rule, not shared: getting out EARLY is most of what a trail
                # is for, and a single shared column cannot show that.
                rec["days::" + name] = held
            recs.append(rec)
    return pd.DataFrame(recs)


def clustered_t(v, day, base=1.0):
    """t on per-session means, because option winners cluster inside a session."""
    s = pd.DataFrame({"v": v, "d": day}).groupby("d").v.mean() - base
    if len(s) < 3 or s.std(ddof=1) == 0:
        return float("nan")
    return s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))


def slice_report(b, spreads, label, out):
    """One table: every exit rule on one slice of trades, net of per-trade friction."""
    if len(b) < 300:
        return
    s = charge(b["entry"].to_numpy(float), spreads)
    print()
    print("=" * 104)
    print("{}   n {:,}   {} sessions   {} symbols   median premium Rs{:.2f}   "
          "median round trip {:.1%}".format(
              label, len(b), b["day"].nunique(), b["symbol"].nunique(),
              b["entry"].median(), float(np.median(s))))
    print("=" * 104)
    print("    {:<24} {:>9} {:>11} {:>10} {:>9} {:>10} {:>9}".format(
        "exit rule", "gross", "NET/sess", "net pooled", "win%", "clust t", "med days"))
    for name, _ in RULES:
        g = b[name].to_numpy(float)
        # Buy at mid + half the spread, sell at mid - half.
        net = pd.Series(g * (1 - s / 2) / (1 + s / 2), index=b.index)
        per_sess = pd.DataFrame({"v": net, "d": b["day"]}).groupby("d").v.median()
        print("    {:<24} {:>8.2f}x {:>10.2f}x {:>9.2f}x {:>8.1f}% {:>9.2f} {:>9.0f}".format(
            name, np.median(g), per_sess.median(), net.mean(),
            (net > 1).mean() * 100, clustered_t(net, b["day"]),
            b["days::" + name].median()))
        out.append({"slice": label, "rule": name, "net_sess": per_sess.median(),
                    "net_pooled": net.mean(), "n": len(b)})


def report(t, spreads, kind, word):
    """Every rule, cut both ways: by moneyness and by premium level."""
    out = []
    v = t[t["kind"] == kind]
    for lo, hi in BANDS:
        slice_report(v[(v["otm"] >= lo) & (v["otm"] < hi)], spreads,
                     "{}  {:+.0%}..{:+.0%} OTM".format(word, lo, hi), out)
    for lo, hi in PREM_BANDS:
        lab = "Rs{:g}-{:g}".format(lo, hi) if hi < 1e6 else "Rs{:g}+".format(lo)
        slice_report(v[(v["entry"] >= lo) & (v["entry"] < hi)], spreads,
                     "{}  premium {}".format(word, lab), out)
    # Days to expiry, which the base-rate table says is the sharpest axis of the
    # three: holding the fortnight constant, 21-35 DTE roughly DOUBLES the 2x
    # rate of 35-70 DTE (44.0% vs 24.2%, 44.4% vs 24.5%, 31.1% vs 15.2%). That is
    # options arithmetic rather than an edge -- less absolute time value, more
    # gamma -- and it cuts both ways, because the median hold moves the other
    # direction (0.52x vs 0.62x). Which of the two wins depends entirely on the
    # exit rule, so it has to be measured here rather than argued.
    for lo, hi in DTE_BANDS:
        slice_report(v[(v["dte"] >= lo) & (v["dte"] < hi)], spreads,
                     "{}  dte {}-{}".format(word, lo, hi), out)
    return out


def exit_lever(rows, word):
    """The one question this file exists to answer: is the exit worth anything?"""
    if not rows:
        return
    d = pd.DataFrame(rows)
    print()
    print("=" * 104)
    print("{} -- HOW MUCH IS THE EXIT WORTH?  best rule minus hold-to-horizon, "
          "per-session median".format(word))
    print("=" * 104)
    print("    {:<26} {:>10} {:>26} {:>12} {:>10}".format(
        "slice", "hold", "best rule", "best", "lever"))
    for label, g in d.groupby("slice", sort=False):
        held = g[g["rule"] == "hold to 10d"]["net_sess"]
        if not len(held):
            continue
        base = held.iloc[0]
        best = g.loc[g["net_sess"].idxmax()]
        print("    {:<26} {:>9.2f}x {:>26} {:>11.2f}x {:>+9.2f}x".format(
            label.replace(word + "  ", ""), base, best["rule"],
            best["net_sess"], best["net_sess"] - base))


def main():
    d = load_cache()
    spot = daily_spot()
    spreads = load_spreads()
    d = build(d, spot)
    log("{:,} bars, {:,} contracts, {} symbols, {} .. {}".format(
        len(d), d["sid"].nunique(), d["symbol"].nunique(), d["day"].min(), d["day"].max()))

    t = run_rules(d)
    log("{:,} candidate trades (volume > 0, premium >= Rs{:.0f})".format(len(t), MIN_PREMIUM))

    print()
    print("=" * 104)
    print("EXIT RULES ON PINNED DEEP-OTM CONTRACTS")
    print("  Entry at the session close. Limit sells fill on the daily high; every")
    print("  stop and trail is evaluated on CLOSES only and wins any same-day tie.")
    print("=" * 104)
    for kind, word in (("CE", "CALLS"), ("PE", "PUTS")):
        exit_lever(report(t, spreads, kind, word), word)

    t.to_parquet("research/otm_exits.parquet", index=False)
    log("wrote research/otm_exits.parquet")


if __name__ == "__main__":
    main()
