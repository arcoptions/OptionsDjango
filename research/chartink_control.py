"""Is the Chartink entry bad, or is buying calls bad?

Stage 2 showed the CE workflow losing at every horizon.  That alone does not
convict the scan, because buying at-the-money stock calls loses money from a
RANDOM bar too -- 0.77x over two sessions, measured over 460k contract-bars.
If the trigger lands on the same number as a coin flip, the scan is neutral and
the loss is just the option's carrying cost.

So: for every symbol-day that produced a trigger, price the same trade from
EVERY OTHER bar of that same session.  Same stock, same day, same contract,
same exits, same costs.  The only difference is when you pressed the button.
"""

import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chartink_options import (  # noqa: E402
    HORIZONS, IST, MAX_HOLD, TICK, TRAIL,
    day_clustered_t, find_expiries, load_equity, load_options, load_triggers,
    log, net_multiple, stage_one,
)


def price_from(g, entry_ts, dead):
    """One ATM call bought at `entry_ts`, held on a fixed strike."""
    at_entry = g[g.ts == entry_ts]
    if at_entry.empty:
        return None
    pick = at_entry.iloc[(at_entry.strike - at_entry.spot).abs().argsort().iloc[0]]
    prem = float(pick.open)
    if not np.isfinite(prem) or prem < 1.0:
        return None
    series = g[(g.strike == float(pick.strike)) & (g.ts >= entry_ts)]
    if dead is not None:
        series = series[series.ts.dt.date <= dead]
    series = series.sort_values("ts").head(MAX_HOLD + 1)
    if len(series) < 2:
        return None
    path = series.iloc[1:]

    rec = {"premium": prem}
    for label, bars in HORIZONS:
        rec["hold_" + label] = (
            net_multiple(prem, float(path.iloc[bars - 1].close)) if len(path) >= bars else np.nan
        )
    peak, out = prem, float(path.iloc[-1].close)
    for bar in path.itertuples():
        peak = max(peak, bar.high)
        if bar.close <= peak * (1.0 - TRAIL):
            out = float(bar.close)
            break
    rec["trail30"] = net_multiple(prem, out)
    return rec


def main():
    trig = load_triggers()
    start = trig.entry_ts.min() - dt.timedelta(days=3)
    end = trig.entry_ts.max() + dt.timedelta(days=12)
    su = start.tz_localize(IST).astimezone(dt.timezone.utc)
    eu = end.tz_localize(IST).astimezone(dt.timezone.utc)

    eq = load_equity(su, eu)
    s1 = stage_one(trig, eq)
    opts = load_options(su, eu, set(s1.symbol))
    expiries = find_expiries(opts)

    fired = set(zip(s1.symbol, s1.day, s1.entry_ts))
    pairs = sorted(set(zip(s1.symbol, s1.day)))
    log("pricing every bar of {} triggered symbol-days...".format(len(pairs)))

    by_symbol = {s: g for s, g in opts.groupby("symbol")}
    rows = []
    for symbol, day in pairs:
        g = by_symbol.get(symbol)
        if g is None:
            continue
        dead = next((e for e in expiries if e >= day), None)
        session = g[g.ts.dt.date == day]
        for stamp in sorted(session.ts.unique()):
            rec = price_from(g, stamp, dead)
            if rec is None:
                continue
            rec.update({
                "symbol": symbol, "day": day, "ts": stamp,
                "fired": (symbol, day, stamp) in fired,
            })
            rows.append(rec)
    frame = pd.DataFrame(rows)

    hit = frame[frame.fired]
    miss = frame[~frame.fired]
    log("\n{} priced entries on {} symbol-days: {} on a trigger bar, {} on every other bar".format(
        len(frame), len(pairs), len(hit), len(miss)))

    log("\n" + "=" * 82)
    log("TRIGGER BAR vs EVERY OTHER BAR OF THE SAME SESSION")
    log("=" * 82)
    log("  {:<12} {:>10} {:>10} {:>10} {:>9} {:>8}".format(
        "exit", "trigger", "control", "difference", "t(day)", "n"))
    out_rows = []
    for col, label in [("hold_" + l, l) for l, _ in HORIZONS] + [("trail30", "trail 30%")]:
        a, b = hit[col].dropna(), miss[col].dropna()
        if a.empty or b.empty:
            continue
        # Pair each trigger against its own session's average, then cluster by day.
        base = miss.groupby(["symbol", "day"])[col].mean()
        paired = hit.dropna(subset=[col]).join(
            base.rename("base"), on=["symbol", "day"]
        ).dropna(subset=["base"])
        diff = paired[col] - paired["base"]
        t, _ = day_clustered_t(diff, paired["day"])
        out_rows.append({
            "exit": label, "trigger": a.mean(), "control": b.mean(),
            "difference": diff.mean(), "t": t, "n": len(diff),
            "control_win": (b > 1).mean() * 100, "control_n": len(b),
        })
        log("  {:<12} {:>10.3f} {:>10.3f} {:>+10.3f} {:>9} {:>8d}".format(
            label, a.mean(), b.mean(), diff.mean(),
            "n/a" if not np.isfinite(t) else "{:+.2f}".format(t), len(diff)))

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chartink_ce_control.csv")
    pd.DataFrame(out_rows).to_csv(path, index=False)
    log("\nwritten to {}".format(path))

    log("\nWhat the control says about buying ATM calls at all:")
    for col, label in [("hold_" + l, l) for l, _ in HORIZONS] + [("trail30", "trail 30%")]:
        b = miss[col].dropna()
        if b.empty:
            continue
        log("  random bar, {:<10} {:.3f}x   win {:.1f}%   n={}".format(
            label, b.mean(), (b > 1).mean() * 100, len(b)))


if __name__ == "__main__":
    main()
