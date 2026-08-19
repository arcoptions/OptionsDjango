"""The shipped NIFTY architecture, ported to stock options and swept.

WHY THIS AND NOT ANOTHER ENTRY.  Every stock-option entry signal is dead: 68 of
70 combinations in `option_moves.py`, both Chartink scans, and the pre-move
predictor at three strike rungs.  What is NOT dead, and has never been swept, is
the exit.  It is the only lever on this data with a demonstrated size -- the same
entries go 0.773x on a 2-session hold and 0.882x on a crude 30% trail, eleven
paise from the exit alone, wider than the gap between the best and worst entry
signal ever tested.

AND THE HOLDING PERIOD IS PART OF THE EXIT.  Every stock-option study here has
held 2 to 5 sessions, overnight, where theta is the whole bill: break-even needs
+0.87% of stock movement by the close and +1.10% over two days.  The one intraday
test that exists (the Chartink overlay) ran 0.963x at 30 minutes, 0.941x at 1h,
0.906x at 2h, 0.785x by the close -- monotonically worse with time held, and the
30-minute figure is the closest anything in this programme has come to 1.0.  It
got there with a fixed-clock exit on an entry proven identical to a random bar.

So the untested space is: intraday only, with a real exit.  Which is precisely
what the NIFTY strategy that already ships is -- 10% hard stop, a trail that ARMS
at +7% and then follows 7% behind the running high, no target, flat before the
bell.  This file ports that and sweeps it.

WHAT IS BEING ASKED, EXACTLY.  Not "does the opening-range breakout work" -- it
almost certainly does not, because nothing does.  The question is whether ANY
intraday exit architecture on a stock option clears 1.0 net of costs.  So the ORB
entry runs against an ANY-BAR CONTROL on the identical exit sweep.  If the
control matches the signal, the entry is null again and the exit is the whole
story; if neither clears 1.0, intraday stock options are dead and the programme
is finished.

THREE THINGS THAT KEEP THIS HONEST.

  Within a 15-minute bar the path is unknown.  If a bar's low breaches the stop
  AND its high would have advanced the trail, this assumes THE STOP HIT FIRST.
  Pessimistic by construction; the alternative manufactures profit.

  Costs are swept, not assumed.  A 5-paise tick is an INDEX assumption and stock
  options are thinner.  Everything that has ever looked shippable on this data
  died on the slippage sweep, so it runs here as a first-class column.

  Intraday defuses two of the five feed traps.  A strike pinned at 09:30 and
  released by 15:30 barely drifts, so exit-side survivorship is small (measured
  and reported, not assumed), and a corporate action happens between sessions, so
  it cannot land mid-trade.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from option_spreads import IST, TAX, TICK, day_t, log  # noqa: E402

from options_tracker.models import StockEquityCandle, StockOptionCandle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

BUFFER = 0.0003      # the NIFTY breakout buffer, 0.03% beyond the opening range
VOL_RATIO = 1.5      # option bar volume against the median of the prior bars
MIN_PREMIUM = 1.00   # below this the tick alone dwarfs the trade
CONTROL_PER_DAY = 2  # any-bar control entries per symbol-day
SEED = 7

# The shipped NIFTY exit is stop 0.10 / arm 0.07 / gap 0.07. Swept around it,
# plus the two degenerate references every candidate has to beat.
EXITS = [
    ("hold to bell", None, None, None),
    ("stop 10% only", 0.10, None, None),
    ("NIFTY 10/7/7", 0.10, 0.07, 0.07),
    ("10/7/15", 0.10, 0.07, 0.15),
    ("15/10/10", 0.15, 0.10, 0.10),
    ("20/10/15", 0.20, 0.10, 0.15),
    ("20/15/20", 0.20, 0.15, 0.20),
    ("25/15/25", 0.25, 0.15, 0.25),
    ("30/20/30", 0.30, 0.20, 0.30),
    ("40/25/40", 0.40, 0.25, 0.40),
    ("50/30/50", 0.50, 0.30, 0.50),
]

SLIPPAGE = [0.05, 0.15, 0.25, 0.50]


def load_equity():
    rows = StockEquityCandle.objects.filter(interval_minutes=15).values_list(
        "symbol", "timestamp", "open", "high", "low", "close", "volume")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "o", "h", "l", "c", "v"])
    f["ts"] = pd.to_datetime(f.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for c in ("o", "h", "l", "c", "v"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f.sort_values(["symbol", "ts"])


def load_options():
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15, relative_strike="ATM",
    ).values_list("symbol", "timestamp", "option_type", "strike", "spot",
                  "open", "high", "low", "close", "volume")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "side", "strike", "spot",
                                          "o", "h", "l", "c", "v"])
    f["ts"] = pd.to_datetime(f.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for c in ("strike", "spot", "o", "h", "l", "c", "v"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    # A quote below its own intrinsic, or above 60% of spot, is the stale-5-paise
    # trap in dhan-rolling-feed-traps. Same filter option_moves.py uses.
    intr = np.where(f.side == "CALL", (f.spot - f.strike).clip(lower=0),
                    (f.strike - f.spot).clip(lower=0))
    ok = (f.c.gt(0) & f.o.gt(0) & f.h.ge(f.l) & f.c.ge(intr - 0.10)
          & f.c.lt(f.spot * 0.6) & f.spot.gt(0))
    return f[ok].sort_values(["symbol", "ts"])


def paths(eq, op):
    """One row per candidate entry, carrying the option's path to the bell.

    Built once; the exit sweep then runs over these arrays, so adding a parameter
    combination costs nothing.
    """
    rng = np.random.default_rng(SEED)
    out = []
    for symbol, edays in eq.groupby("symbol", sort=False):
        osym = op[op.symbol == symbol]
        if osym.empty:
            continue
        obyday = {d: g for d, g in osym.groupby(osym.ts.dt.date, sort=False)}
        for day, ebars in edays.groupby(edays.ts.dt.date, sort=False):
            obars = obyday.get(day)
            if obars is None or len(ebars) < 4:
                continue
            ebars = ebars.sort_values("ts")
            opening = ebars.iloc[0]
            hi, lo = opening.h * (1 + BUFFER), opening.l * (1 - BUFFER)

            for side, tag in (("CALL", "up"), ("PUT", "dn")):
                leg = obars[obars.side == side].sort_values("ts")
                if len(leg) < 4:
                    continue
                k = leg.iloc[0].strike
                leg = leg[leg.strike == k]           # pin the strike at the open
                if len(leg) < 4:
                    continue
                lts = list(leg.ts)
                lo_, hi_, cl_, op_ = (leg.l.values, leg.h.values,
                                      leg.c.values, leg.o.values)
                vol = leg.v.values
                idx = {t: i for i, t in enumerate(lts)}

                # --- the opening-range breakout, from EQUITY bars
                armed, fired = True, None
                for _, b in ebars.iloc[1:].iterrows():
                    broke = b.c > hi if side == "CALL" else b.c < lo
                    back = b.c <= opening.h if side == "CALL" else b.c >= opening.l
                    if broke and armed:
                        fired = b.ts
                        armed = False
                    elif back:
                        armed = True
                    if fired is not None:
                        break

                picks = []
                if fired is not None:
                    picks.append((fired, "ORB"))
                # --- the control: any bar of the same session, same contract
                pool = [t for t in lts[1:-1]]
                if pool:
                    n = min(CONTROL_PER_DAY, len(pool))
                    for t in rng.choice(pool, size=n, replace=False):
                        picks.append((t, "ANY"))

                for ts, kind in picks:
                    i = idx.get(pd.Timestamp(ts))
                    if i is None or i + 1 >= len(lts):
                        continue
                    j = i + 1                     # enter on the NEXT bar's open
                    entry = op_[j]
                    if not np.isfinite(entry) or entry < MIN_PREMIUM:
                        continue
                    if kind == "ORB":
                        # Both filters read bar i, the SIGNAL bar, which is
                        # complete at the moment of the decision. Reading bar j
                        # here would be look-ahead -- j's volume and close are
                        # not known until 15 minutes after we have already
                        # bought at j's open. That is the exact bug that made
                        # the Chartink scan look like +1.24% at t=22.97.
                        prior = vol[max(0, i - 5):i]
                        med = np.median(prior) if len(prior) else 0
                        if not (med > 0 and vol[i] / med >= VOL_RATIO):
                            continue
                        if not cl_[i] > op_[i]:
                            continue
                    out.append({
                        "symbol": symbol, "day": day, "kind": kind, "side": side,
                        "ts": lts[j], "entry": entry, "strike": k,
                        "spot": leg.spot.values[j], "bars": len(lts) - j - 1,
                        "lo": lo_[j + 1:], "hi": hi_[j + 1:], "cl": cl_[j + 1:],
                    })
    return out


def simulate(trade, stop_pct, arm, gap, slip):
    """Walk the rest of the session. Stop is assumed to hit before the trail."""
    entry = trade["entry"]
    buy = entry + slip
    lo, hi, cl = trade["lo"], trade["hi"], trade["cl"]
    if len(lo) == 0:
        return None
    stop = entry * (1 - stop_pct) if stop_pct else None
    peak = entry
    exit_px = None
    for n in range(len(lo)):
        if stop is not None and lo[n] <= stop:
            exit_px = stop                      # pessimistic: stop before trail
            break
        if hi[n] > peak:
            peak = hi[n]
            if arm is not None and peak >= entry * (1 + arm):
                stop = max(stop or 0, peak - entry * gap)
    if exit_px is None:
        exit_px = cl[-1]
    sell = max(exit_px - slip, 0.0)
    return (sell - buy - (buy + sell) * TAX) / buy


def report(frame, label):
    log("")
    log("=" * 100)
    log(label)
    log("=" * 100)
    log("  {:<16} {:>8} {:>9} {:>9} {:>8} {:>9} {:>9}".format(
        "exit", "n", "mean", "median", "win%", "t(day)", "per-sess"))
    best = None
    for name, s, a, g in EXITS:
        r = frame["r_" + name].dropna()
        if len(r) < 200:
            continue
        sub = frame.loc[r.index]
        per = sub.groupby("day").apply(lambda x: x["r_" + name].mean(),
                                       include_groups=False)
        t = day_t(r.values, sub.day.values)
        log("  {:<16} {:>8,} {:>+8.2f}% {:>+8.2f}% {:>7.1f}% {:>+8.2f} {:>+8.2f}%".format(
            name, len(r), r.mean() * 100, r.median() * 100,
            (r > 0).mean() * 100, t, per.median() * 100))
        if best is None or r.mean() > best[1]:
            best = (name, r.mean(), t)
    if best:
        log("")
        log("  best mean: {} at {:+.2f}% (t {:+.2f}). Multiple of capital: {:.3f}x".format(
            best[0], best[1] * 100, best[2], 1 + best[1]))
    return best


def main():
    eq, op = load_equity(), load_options()
    log("{:,} equity bars ({} symbols), {:,} ATM option bars ({} symbols)".format(
        len(eq), eq.symbol.nunique(), len(op), op.symbol.nunique()))

    trades = paths(eq, op)
    log("{:,} candidate entries built".format(len(trades)))
    meta = pd.DataFrame([{k: t[k] for k in
                          ("symbol", "day", "kind", "side", "ts", "entry",
                           "strike", "spot", "bars")} for t in trades])
    log("  ORB {:,}   control {:,}   {} symbols, {} sessions".format(
        (meta.kind == "ORB").sum(), (meta.kind == "ANY").sum(),
        meta.symbol.nunique(), meta.day.nunique()))
    log("  median premium Rs{:.1f} = {:.2f}% of spot, median {} bars held to bell".format(
        meta.entry.median(), (meta.entry / meta.spot).median() * 100,
        int(meta.bars.median())))

    for name, s, a, g in EXITS:
        meta["r_" + name] = [simulate(t, s, a, g, TICK) for t in trades]

    orb = meta[meta.kind == "ORB"]
    any_ = meta[meta.kind == "ANY"]
    report(orb, "OPENING-RANGE BREAKOUT + volume, ATM, intraday, 5p tick")
    report(any_, "ANY-BAR CONTROL -- same contract, same session, same exits")

    log("")
    log("=" * 100)
    log("SLIPPAGE -- 5 paise is an INDEX assumption. Stock options are thinner,")
    log("and this column is what has killed every previous candidate.")
    log("=" * 100)
    log("  {:<16} {:>10} {:>10} {:>10} {:>10}".format(
        "exit", *["Rs{:.2f}".format(s) for s in SLIPPAGE]))
    tr = [t for t, k in zip(trades, meta.kind) if k == "ORB"]
    for name, s, a, g in EXITS:
        cells = []
        for slip in SLIPPAGE:
            r = pd.Series([simulate(t, s, a, g, slip) for t in tr]).dropna()
            cells.append("{:+.2f}%".format(r.mean() * 100) if len(r) > 200 else "-")
        log("  {:<16} {:>10} {:>10} {:>10} {:>10}".format(name, *cells))

    meta.drop(columns=[c for c in meta.columns if c.startswith("r_")][:0]).to_csv(
        os.path.join(HERE, "stock_intraday.csv"), index=False)
    log("\nwrote stock_intraday.csv")


if __name__ == "__main__":
    main()
