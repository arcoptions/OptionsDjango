"""A stock-option buying strategy, built from the measurements rather than hoped for.

WHY THIS DESIGN, DERIVED AND NOT GUESSED.  Three numbers fix the shape of any
strategy that can work here, and all three are measured, not assumed:

  1. TOLL. Pooled friction is 7.3%, but that is an average hiding a cheap head:
     7 underlyings quote at or below NIFTY's 1.63% and 32 sit under 3%
     (`friction_by_symbol.py`). Trade only the cheap head.
  2. DECAY. Holding an ATM call costs **-1.1% per hour, gross, before spread**,
     and this is flat across toll bands (Spearman +0.054, `cheap_toll_bite.py`).
     So every extra minute is a fee. Hold minutes, not sessions.
  3. LEVERAGE. An ATM call runs delta ~0.5 on a premium worth ~1.5-2% of spot,
     so its elasticity is roughly 25-30x. A **0.1% move in the stock is a ~2.8%
     move in the option.**

Put together: a 30-minute hold on a cheap-toll name costs about 2% of toll plus
0.55% of decay, so it needs roughly **0.1% of favourable stock movement** to
break even. That is a small number, and it is why this is worth building even
though every multi-day version of it has failed. The multi-day schemes were
asking for +50% while paying nine sessions of decay; this asks for +3% while
paying thirty minutes.

WHAT THIS IS A PORT OF.  The shipped NIFTY rule: opening-range breakout, ATM
strike, hard stop at 10% of premium, trail armed at +7% with a 0.7R gap, flat
before the close, no target. That architecture is not being re-derived here --
it earned 66.7% on NIFTY and the reason it works is now understood (small bite,
short hold, low toll). The question is whether the cheap-toll shortlist gives it
the same conditions.

THE MEASUREMENT TRAP THAT WOULD FAKE THE WHOLE RESULT.  The option feed is
ATM-RELATIVE: the contract behind `relative_strike='ATM'` CHANGES the moment
spot crosses a strike boundary. Differencing consecutive rows therefore prices a
switch between two different options as if it were a move -- a jump of the full
strike interval, arriving preferentially on trending days, which is to say
exactly when a breakout signal fires. It would manufacture precisely the result
this file is looking for. The fix is to pool every relative_strike bucket and key
on the ACTUAL STRIKE, which recovers contiguous full-session paths for one
contract (verified: 26 of 26 bars on HDFCBANK 730/740 CE). Nothing here reads a
price across a strike change.

WHAT REMAINS OPTIMISTIC, stated rather than buried.  Bars are 15 minutes, not the
1 minute the NIFTY engine uses, so the opening range is one bar, the trail can
only be evaluated four times an hour, and both the entry and the stop are
coarser than they would be live -- a 15-minute bar can travel a long way through
a stop before it prints. Fills are at the bar open with the measured round trip
charged, with no impact and no rejections.
"""
import datetime as dt
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

from options_tracker.models import StockOptionCandle  # noqa: E402

PANEL = "research/call_panel_15m.parquet"
MIN_PREM = 2.50
MAX_TOLL = 0.03            # the cheap head, from friction_by_symbol.py
STOP = 0.10                # hard stop at 10% of premium -- NIFTY's number
TRAIL_ARM = 0.07           # arm the trail once the running high is +7%
TRAIL_GAP = 0.07           # then stop = high_water - 7% of entry


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def load_panel():
    """Every CALL bar keyed on its ACTUAL strike, so a contract can be followed."""
    if os.path.exists(PANEL):
        return pd.read_parquet(PANEL)
    log("reading all CALL bars across relative_strike buckets ...")
    d = pd.DataFrame(list(
        StockOptionCandle.objects.filter(option_type="CALL").order_by()
        .values("symbol", "timestamp", "strike", "spot", "open", "high",
                "low", "close", "volume")))
    for c in ("strike", "spot", "open", "high", "low", "close", "volume"):
        d[c] = d[c].astype(float)
    d["ts"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("Asia/Kolkata")
    d = d.drop(columns=["timestamp"])
    d["day"] = d["ts"].dt.date
    # one row per contract-bar: the same strike can arrive under two buckets
    d = (d.sort_values(["symbol", "strike", "ts"])
          .drop_duplicates(["symbol", "strike", "ts"], keep="first")
          .reset_index(drop=True))
    d.to_parquet(PANEL, index=False)
    log("cached {:,} contract-bars, {} symbols".format(len(d), d["symbol"].nunique()))
    return d


def cheap_names():
    q = pd.read_csv("research/spread_curve.csv")
    q = q[(q["kind"] == "CE") & (q["otm"].abs() <= 0.03) &
          (q["mid"] >= MIN_PREM) & (q["spread_pct"] > 0)]
    t = q.groupby("symbol")["spread_pct"].median() / 100.0
    return t[t <= MAX_TOLL]


def orb_signals(eq, names, breakout_buffer=0.0, vol_mult=1.0, last_entry="14:00"):
    """Opening-range breakout on 15-minute equity bars.

    The opening range is the FIRST bar of the session. A signal fires on the
    first later bar that closes above it on above-average volume. One signal per
    symbol-day: the second breakout of the same range is the same idea, already
    priced.
    """
    e = eq[eq["symbol"].isin(names)].copy()
    e["t"] = e["ts"].dt.strftime("%H:%M")
    out = []
    for (sym, day), g in e.groupby(["symbol", "day"], sort=False):
        g = g.sort_values("ts")
        if len(g) < 6:
            continue
        opn = g.iloc[0]
        hi = float(opn["high"]) * (1 + breakout_buffer)
        rest = g.iloc[1:]
        rest = rest[rest["t"] <= last_entry]
        if not len(rest):
            continue
        vmean = float(g["volume"].iloc[:4].mean()) or 1.0
        hit = rest[(rest["close"] > hi) & (rest["volume"] >= vol_mult * vmean)]
        if not len(hit):
            continue
        b = hit.iloc[0]
        out.append({"symbol": sym, "day": day, "ts": b["ts"],
                    "spot": float(b["close"]), "orh": float(opn["high"])})
    return pd.DataFrame(out)


def run_trade(path, i0, stop=STOP, arm=TRAIL_ARM, gap=TRAIL_GAP):
    """NIFTY's exit engine on one contract's intraday path.

    Entry is the NEXT bar's open. The stop is hard from entry; once the running
    high clears `arm`, the stop becomes high_water - gap and only ratchets up.
    Everything is flat at the last bar of the session, without exception.
    """
    if i0 + 1 >= len(path):
        return None
    entry = float(path["open"].iloc[i0 + 1])
    if entry < MIN_PREM:
        return None
    hard, high = entry * (1 - stop), entry
    for j in range(i0 + 1, len(path)):
        b = path.iloc[j]
        lo, hi, cl = float(b["low"]), float(b["high"]), float(b["close"])
        if lo <= hard:                       # stop first: same bar, worst case
            return entry, hard, j - i0, "stop"
        high = max(high, hi)
        if high >= entry * (1 + arm):
            hard = max(hard, high - entry * gap)
        if j == len(path) - 1:
            return entry, cl, j - i0, "eod"
    return None


def backtest(panel, sig, toll, stop=STOP, arm=TRAIL_ARM, gap=TRAIL_GAP, label=""):
    idx = {k: g.sort_values("ts").reset_index(drop=True)
           for k, g in panel.groupby(["symbol", "day", "strike"], sort=False)}
    keys = {}
    for (s, d, k) in idx:
        keys.setdefault((s, d), []).append(k)
    rows = []
    for _, r in sig.iterrows():
        ks = keys.get((r["symbol"], r["day"]))
        if not ks:
            continue
        k = min(ks, key=lambda x: abs(x - r["spot"]))     # the ATM strike
        p = idx[(r["symbol"], r["day"], k)]
        m = p.index[p["ts"] == r["ts"]]
        if not len(m):
            continue
        res = run_trade(p, int(m[0]), stop, arm, gap)
        if res is None:
            continue
        entry, exit_px, held, why = res
        f = float(toll.get(r["symbol"], MAX_TOLL))
        gross = exit_px / entry
        net = gross * (1 - f / 2) / (1 + f / 2)
        rows.append({"symbol": r["symbol"], "day": r["day"], "entry": entry,
                     "net": net, "gross": gross, "held": held, "why": why,
                     "toll": f})
    return pd.DataFrame(rows)


def summarise(t, label):
    if not len(t):
        print("  {:<34} no trades".format(label))
        return None
    r = t["net"] - 1
    day = t.groupby("day")["net"].mean() - 1
    tstat = (day.mean() / (day.std(ddof=1) / np.sqrt(len(day)))) if len(day) > 2 else np.nan
    print("  {:<34} {:>6,} {:>8.1%} {:>9.2%} {:>9.2%} {:>8.2f} {:>7.1f} {:>8.1%}".format(
        label, len(t), (t["net"] > 1).mean(), r.mean(), r.median(), tstat,
        t["held"].mean(), (t["why"] == "stop").mean()))
    return {"n": len(t), "win": (t["net"] > 1).mean(), "mean": r.mean(), "t": tstat}


HEAD = "  {:<34} {:>6} {:>8} {:>9} {:>9} {:>8} {:>7} {:>8}".format(
    "construction", "trades", "win%", "mean", "median", "t(day)", "bars", "stopped")


def main():
    panel = load_panel()
    eq = pd.read_parquet("research/equity_15m.parquet")
    toll = cheap_names()
    log("{} cheap-toll names (<= {:.0%}), median toll {:.2%}".format(
        len(toll), MAX_TOLL, toll.median()))

    sig = orb_signals(eq, set(toll.index))
    log("{:,} opening-range breakouts on {} sessions".format(
        len(sig), sig["day"].nunique()))

    print()
    print("=" * 108)
    print("NIFTY'S ARCHITECTURE ON THE CHEAP-TOLL SHORTLIST -- ATM calls, intraday, flat at the bell")
    print("=" * 108)
    print(HEAD)
    t = backtest(panel, sig, toll)
    summarise(t, "ORB breakout, stop 10% trail 7%")

    # The control that matters: same names, same days, entry at a random bar.
    rng = np.random.default_rng(0)
    ctl = sig.copy()
    pool = eq[eq["symbol"].isin(set(toll.index))].copy()
    pool["t"] = pool["ts"].dt.strftime("%H:%M")
    pool = pool[(pool["t"] > "09:15") & (pool["t"] <= "14:00")]
    pick = (pool.groupby(["symbol", "day"], sort=False)
            .apply(lambda g: g.iloc[rng.integers(len(g))], include_groups=False)
            .reset_index())
    pick = pick.rename(columns={"close": "spot"})
    pick = pick.merge(sig[["symbol", "day"]], on=["symbol", "day"], how="inner")
    c = backtest(panel, pick[["symbol", "day", "ts", "spot"]], toll)
    summarise(c, "  control: random bar, same days")

    print()
    print("  does it survive the exit knobs?")
    for stop, arm, gap in [(0.10, 0.07, 0.07), (0.15, 0.10, 0.10),
                           (0.20, 0.10, 0.15), (0.10, 0.05, 0.05)]:
        t2 = backtest(panel, sig, toll, stop, arm, gap)
        summarise(t2, "  stop {:.0%} arm {:.0%} gap {:.0%}".format(stop, arm, gap))

    print()
    print("  split-half -- the only test that has ever mattered in this programme")
    days = sorted(sig["day"].unique())
    h = len(days) // 2
    for lbl, w in [("first half", set(days[:h])), ("second half", set(days[h:]))]:
        summarise(t[t["day"].isin(w)], "  {} (from {})".format(lbl, sorted(w)[0]))

    t.to_parquet("research/orb_stock_trades.parquet", index=False)
    log("wrote research/orb_stock_trades.parquet")


if __name__ == "__main__":
    main()
