"""The first signal that beats its null: a volume spike, priced as a bought call.

WHAT SURVIVED THE SCREEN, and why it is a different kind of finding.
`breakeven_screen.py` measured fourteen signals against a same-instant
cross-section, with a null row -- every eligible bar, no signal -- included so
that scores are read as differences rather than against zero. Two results:

  * DIRECTION IS WORTH NOTHING. The null scores +0.019% and the fourteen signals
    score +0.014% to +0.031%. Nothing beats doing nothing. The mean is dead.
  * MOTION IS WORTH A LOT, AND ONLY IN THE TAIL. Against a 1.00x null, a 3x
    volume spike on an up bar lifts P(+1% in 30 min) to **2.71x** and P(+2%) to
    **5.51x**, t(day) +9.19.

Those two facts are consistent, and the reason is convexity. A call is not a bet
on the mean; its loss is capped at the premium and its gain is not. A signal that
leaves the mean at zero while doubling the odds of a 1% move is worthless to a
stock trader and valuable to a call buyer. That is the entire thesis under test
here, and it is the only thesis this programme has produced that has not already
been falsified.

THE ASYMMETRY QUESTION THAT DECIDES IT.  A volume spike could simply be
volatility, lifting both tails equally. That does NOT automatically kill the
trade -- capped downside means a symmetric fattening still favours the buyer --
but it changes the size of the edge enormously, so it is measured here rather
than assumed, both tails, side by side.

THE MISTAKE THIS FILE REFUSES TO REPEAT.  The previous configuration returned
+2.86% and it was entirely a fill assumption: with no hard stop the only exit was
a trail that arms at +7%, so every exit was booked AT the trail level and
therefore at or above entry. Priced at the bar close it was -3.99%, at the bar
low -10.17%. Here every triggered exit fills at the WORSE of the trigger level
and the bar's close, and the bar-low worst case is reported alongside it. A
result that does not survive both columns is not a result.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

MIN_PREM = 2.50
MAX_TOLL = 0.03
SPIKE = 3.0
WIN_FROM, WIN_TO = "09:15", "14:30"


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def cheap_names():
    q = pd.read_csv("research/spread_curve.csv")
    q = q[(q["kind"] == "CE") & (q["otm"].abs() <= 0.03) &
          (q["mid"] >= MIN_PREM) & (q["spread_pct"] > 0)]
    t = q.groupby("symbol")["spread_pct"].median() / 100.0
    return t[t <= MAX_TOLL]


def equity_signals(names):
    e = pd.read_parquet("research/equity_15m.parquet")
    e = e[e["symbol"].isin(names)].sort_values(["symbol", "ts"]).reset_index(drop=True)
    e["t"] = e["ts"].dt.strftime("%H:%M")
    g = e.groupby(["symbol", "day"], sort=False)
    e["bar"] = g.cumcount()
    e["v_mean"] = g["volume"].transform(lambda s: s.expanding().mean())
    e["ret1"] = g["close"].pct_change()
    win = (e["t"] > WIN_FROM) & (e["t"] <= WIN_TO) & (e["bar"] >= 1)
    e["sig_up"] = win & (e["volume"] >= SPIKE * e["v_mean"]) & (e["ret1"] > 0)
    e["sig_dn"] = win & (e["volume"] >= SPIKE * e["v_mean"]) & (e["ret1"] < 0)
    e["eligible"] = win
    return e


def tails(e):
    """Both tails of the same signal, so the asymmetry is visible not assumed."""
    g = e.groupby(["symbol", "day"], sort=False)
    fwd = (g["close"].shift(-2) / e["close"] - 1)
    ok = e["eligible"] & fwd.notna()
    print()
    print("=" * 96)
    print("BOTH TAILS OF THE SPIKE -- 30-minute forward move, cheap-toll names only")
    print("=" * 96)
    print("  {:<26} {:>8} {:>11} {:>11} {:>11} {:>11}".format(
        "", "fires", "P(+1%)", "P(-1%)", "P(+2%)", "P(-2%)"))
    for lbl, m in (("(null) every bar", e["eligible"]),
                   ("volume spike, bar up", e["sig_up"]),
                   ("volume spike, bar down", e["sig_dn"])):
        s = m & ok
        if not s.any():
            continue
        print("  {:<26} {:>8,} {:>10.2%} {:>10.2%} {:>10.2%} {:>10.2%}".format(
            lbl, int(s.sum()), (fwd[s] > 0.01).mean(), (fwd[s] < -0.01).mean(),
            (fwd[s] > 0.02).mean(), (fwd[s] < -0.02).mean()))
    b = e["eligible"] & ok
    u = e["sig_up"] & ok
    if u.any() and b.any():
        lu = (fwd[u] > 0.01).mean() / max((fwd[b] > 0.01).mean(), 1e-9)
        ld = (fwd[u] < -0.01).mean() / max((fwd[b] < -0.01).mean(), 1e-9)
        print()
        print("  right-tail lift {:.2f}x vs left-tail lift {:.2f}x -- ratio {:.2f}".format(
            lu, ld, lu / ld if ld else np.nan))
        print("  {}".format(
            "one-sided: the spike predicts UP motion specifically" if lu / ld > 1.15 else
            "near-symmetric: this is volatility, and a call owns it only because\n"
            "  the downside is capped at the premium"))


def run_trade(path, i0, stop, arm, gap, worst=False):
    """Honest exits. A triggered stop fills at the WORSE of level and bar close."""
    if i0 + 1 >= len(path):
        return None
    entry = float(path["open"].iloc[i0 + 1])
    if entry < MIN_PREM:
        return None
    hard, high = entry * (1 - stop), entry
    for j in range(i0 + 1, len(path)):
        b = path.iloc[j]
        lo, hi, cl = float(b["low"]), float(b["high"]), float(b["close"])
        if lo <= hard:
            px = lo if worst else min(hard, cl)
            return entry, px, j - i0, "stop"
        high = max(high, hi)
        if high >= entry * (1 + arm):
            hard = max(hard, high - entry * gap)
        if j == len(path) - 1:
            return entry, cl, j - i0, "eod"
    return None


def backtest(panel_idx, keys, sig, toll, stop, arm, gap, worst=False):
    rows = []
    for r in sig.itertuples():
        ks = keys.get((r.symbol, r.day))
        if not ks:
            continue
        k = min(ks, key=lambda x: abs(x - r.spot))
        p = panel_idx[(r.symbol, r.day, k)]
        m = p.index[p["ts"] == r.ts]
        if not len(m):
            continue
        res = run_trade(p, int(m[0]), stop, arm, gap, worst)
        if res is None:
            continue
        entry, px, held, why = res
        f = float(toll.get(r.symbol, MAX_TOLL))
        net = (px / entry) * (1 - f / 2) / (1 + f / 2)
        rows.append({"symbol": r.symbol, "day": r.day, "net": net,
                     "held": held, "why": why})
    return pd.DataFrame(rows)


def summarise(t, label):
    if not len(t) or len(t) < 20:
        print("  {:<40} {:>6} too few".format(label, len(t)))
        return
    r = t["net"] - 1
    day = t.groupby("day")["net"].mean() - 1
    ts = day.mean() / (day.std(ddof=1) / np.sqrt(len(day))) if len(day) > 2 else np.nan
    print("  {:<40} {:>6,} {:>7.1%} {:>9.2%} {:>9.2%} {:>8.2f} {:>7.1f}".format(
        label, len(t), (t["net"] > 1).mean(), r.mean(), r.median(), ts, t["held"].mean()))


HEAD = "  {:<40} {:>6} {:>7} {:>9} {:>9} {:>8} {:>7}".format(
    "construction", "trades", "win%", "mean", "median", "t(day)", "bars")


def main():
    toll = cheap_names()
    log("{} cheap-toll names, median toll {:.2%}".format(len(toll), toll.median()))
    e = equity_signals(set(toll.index))
    log("{:,} spike-up signals, {:,} spike-down, {:,} eligible bars".format(
        int(e["sig_up"].sum()), int(e["sig_dn"].sum()), int(e["eligible"].sum())))
    tails(e)

    panel = pd.read_parquet("research/call_panel_15m.parquet")
    panel = panel[panel["symbol"].isin(set(toll.index))]
    idx = {k: g.sort_values("ts").reset_index(drop=True)
           for k, g in panel.groupby(["symbol", "day", "strike"], sort=False)}
    keys = {}
    for (s, d, k) in idx:
        keys.setdefault((s, d), []).append(k)
    log("panel: {:,} contract-bars on the shortlist".format(len(panel)))

    def mk(mask):
        s = e[mask][["symbol", "day", "ts", "close"]].rename(columns={"close": "spot"})
        return s.drop_duplicates(["symbol", "day"], keep="first")

    up, dn = mk(e["sig_up"]), mk(e["sig_dn"])
    rng = np.random.default_rng(0)
    el = e[e["eligible"]]
    ctl = (el.groupby(["symbol", "day"], sort=False)
           .apply(lambda g: g.iloc[rng.integers(len(g))], include_groups=False)
           .reset_index()[["symbol", "day", "ts", "close"]]
           .rename(columns={"close": "spot"}))
    ctl = ctl.merge(up[["symbol", "day"]], on=["symbol", "day"], how="inner")

    print()
    print("=" * 96)
    print("THE SPIKE, PRICED AS A BOUGHT ATM CALL -- exits fill at the worse of level and close")
    print("=" * 96)
    print(HEAD)
    for stop, arm, gap in [(0.20, 0.15, 0.15), (0.30, 0.20, 0.20), (0.35, 0.25, 0.25)]:
        t = backtest(idx, keys, up, toll, stop, arm, gap)
        summarise(t, "spike up, stop {:.0%} arm {:.0%} gap {:.0%}".format(stop, arm, gap))
    print()
    t = backtest(idx, keys, up, toll, 0.30, 0.20, 0.20)
    summarise(t, "  the same, filled at the bar LOW (worst)")
    tw = backtest(idx, keys, up, toll, 0.30, 0.20, 0.20, worst=True)
    summarise(tw, "  spike up, worst-case fills")
    print()
    summarise(backtest(idx, keys, ctl, toll, 0.30, 0.20, 0.20),
              "  CONTROL: random bar, same symbol-days")
    summarise(backtest(idx, keys, dn, toll, 0.30, 0.20, 0.20),
              "  MIRROR: spike DOWN, still buying the call")

    print()
    print("  split-half")
    days = sorted(up["day"].unique())
    h = len(days) // 2
    for lbl, w in (("first half", set(days[:h])), ("second half", set(days[h:]))):
        summarise(t[t["day"].isin(w)], "  {} (from {})".format(lbl, sorted(w)[0]))
    t.to_parquet("research/spike_call_trades.parquet", index=False)


if __name__ == "__main__":
    main()
