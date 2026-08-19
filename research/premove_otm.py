"""The motion signal, traded up the strike ladder: ATM vs ATM+1 vs ATM+2.

`premove_legs.py` priced the ATM call on signal days and it was a null -- worse
than a non-signal call on every per-session measure, and signal trades paid 3.51%
of spot against 2.43%.  The reading was that IV already prices the move.  One
thread was left explicitly open, and it is the user's actual question: a 10% move
turns a 2.4%-of-spot ATM call into roughly 3x, where a cheaper contract further
out could turn the same move into 8-10x, AND the OTM skew need not price motion
the way ATM does.

So this file runs the identical signal against the identical sessions at three
rungs of the ladder.  The comparison that matters is not ATM+2 against zero -- it
is (a) ATM+2 on signal days against ATM+2 on non-signal days, which is whether
the signal works out there, and (b) the tail, >=2x/3x/5x, which is whether the
cheaper strike converts a given move into the multiple the user is after.

WHAT THIS CAN AND CANNOT REACH.  Even ATM+2 is only ~2.6% out of the money on
this feed's strike ladder, at 1.50% of spot against ATM's 2.38%.  So this tests
the DIRECTION of the cheap-strike effect, not the far tail.  A 0.7%-of-spot
contract needs the ladder feed with real security ids; ATM+-3 does not get there.

TWO TRAPS ARE LIVE HERE AND THEY PULL AGAINST EACH OTHER.

  Exits vanish in the winning direction.  The feed is ATM-relative, so a pinned
  strike stops being quoted once spot walks away -- and for a long call, spot
  walking UP is the win.  Dropping unpriceable exits would delete the winners, so
  every missing bar is imputed at intrinsic.  That understates: it strips time
  value from a leg that is deep ITM precisely because the trade is working.  A
  loss measured this way is robust; a profit is a floor.

  Corporate actions re-base spot but not the strike.  Which makes the intrinsic
  above subtract two different price scales.  The spread study cut this on
  strike-vs-spot distance, and THAT CUT IS UNSAFE HERE: this study deliberately
  selects stocks predicted to move 10%+, and a real 30% run in five sessions is
  exactly the trade we are trying to measure.  Cutting on distance would delete
  the winners a second time.

  So the action is detected on the SPOT SERIES' OWN CONTINUITY instead -- a
  bar-to-bar jump above 30%.  NSE circuit limits are 20% for a day, so no
  15-minute bar moves 30% for market reasons; a jump that size is the contract
  being re-based underneath us.  That test never looks at the strike, the
  direction, or the outcome, so it cannot delete a winner.  Detected dates are
  printed so they can be checked against the actual corporate action.
"""

import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from option_spreads import IST, TAX, TICK, day_t, log  # noqa: E402

from options_tracker.models import StockOptionCandle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")

HOLD = 125          # 5 sessions of 15-minute bars
TOP = 0.10          # the signal slice
FOLDS, EMBARGO = 3, 5
MIN_PREMIUM = 1.00  # below this the 5p tick alone is >5% a side
SPLIT_JUMP = 0.30   # bar-to-bar spot move that no market can produce
RUNGS = ["ATM", "ATM+1", "ATM+2"]

DROP = {"symbol", "day", "o", "h", "l", "c", "v", "contaminated",
        "up_max", "dn_max", "fwd_close", "iv_call", "iv_put", "oi_call", "oi_put",
        "up5", "up10", "up20", "dn5", "dn10", "dn20", "dollar_vol", "oi", "iv"}


# ---------------------------------------------------------------------------
# the signal, refit here so it is provably the out-of-sample one


def folds(days, n=FOLDS, embargo=EMBARGO):
    days = sorted(days)
    start = len(days) // 2
    step = (len(days) - start) // n
    for k in range(n):
        cut, stop = start + k * step, (start + (k + 1) * step if k < n - 1 else len(days))
        if stop - cut < 10:
            continue
        yield set(days[:max(cut - embargo, 1)]), set(days[cut:stop])


def signal_days():
    frame = pd.read_parquet(FEATURES).sort_values(["day", "symbol"])
    cols = [c for c in frame.columns
            if c not in DROP and pd.api.types.is_numeric_dtype(frame[c])]
    frame["mover"] = ((frame.up10 == 1) | (frame.dn10 == 1)).astype(float)
    frame.loc[frame.up10.isna(), "mover"] = np.nan
    out = []
    for train_days, test_days in folds(frame.day.unique()):
        tr = frame[frame.day.isin(train_days)].dropna(subset=["mover"])
        te = frame[frame.day.isin(test_days)]
        if len(tr) < 2000 or len(te) < 500:
            continue
        m = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=200, l2_regularization=1.0, early_stopping=True,
            validation_fraction=0.15, random_state=7)
        m.fit(tr[cols], tr.mover)
        chunk = te[["symbol", "day", "up_max", "dn_max"]].copy()
        chunk["p"] = m.predict_proba(te[cols])[:, 1]
        out.append(chunk)
    scored = pd.concat(out)
    scored["signal"] = scored.p >= scored.p.quantile(1 - TOP)
    return scored


# ---------------------------------------------------------------------------
# the option side


def load():
    """Every CALL offset, keyed on the ABSOLUTE strike.

    The rung is chosen by the feed's own label at the entry bar; the contract is
    then followed by absolute strike across every offset, so a strike bought at
    ATM+2 stays visible as it drifts back through ATM+1 and ATM.
    """
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15, option_type="CALL",
    ).exclude(relative_strike__startswith="K").values_list(
        "symbol", "timestamp", "relative_strike", "strike", "spot",
        "open", "close", "volume")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "rel", "strike", "spot",
                                          "open", "close", "volume"])
    f["ts"] = pd.to_datetime(f.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for c in ("strike", "spot", "open", "close", "volume"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f.drop_duplicates(subset=["symbol", "ts", "strike"])


def rebase_bars(spot):
    """Timestamps at which the spot series itself jumps -- i.e. a corporate action.

    Deliberately blind to the strike and to the trade: it asks only whether this
    stock's own price series is continuous. A >30% step between adjacent bars is
    not reachable through a 20% daily circuit limit.
    """
    step = spot / spot.shift(1) - 1
    return set(spot.index[step.abs() > SPLIT_JUMP])


def run_symbol(frame, entries, rung):
    o = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = o.index
    cl = frame.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    vol = frame.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    spot = (frame.pivot_table(index="ts", columns="strike", values="spot")
            .reindex(grid).mean(axis=1))
    pick = (frame[frame.rel == rung].drop_duplicates("ts")
            .set_index("ts").strike.reindex(grid))
    broken = rebase_bars(spot)

    days = pd.Series(grid.date, index=grid)
    first_bar = {d: g.index[0] for d, g in days.groupby(days)}
    pos = {t: i for i, t in enumerate(grid)}

    out = []
    for day in sorted(entries):
        ts = first_bar.get(day)
        if ts is None:
            continue
        k = pick.get(ts, np.nan)
        if not np.isfinite(k) or k not in o.columns:
            continue
        entry = o.at[ts, k]
        if not np.isfinite(entry) or entry < MIN_PREMIUM or not vol.at[ts, k] > 0:
            continue
        cost = entry + TICK
        i = pos[ts]
        values, quoted, cut = [], 0, False
        for j in range(i + 1, min(i + 1 + HOLD, len(grid))):
            t = grid[j]
            if t in broken:      # the contract was re-based; the path ends here
                cut = True
                break
            v = cl.at[t, k]
            if np.isfinite(v):
                quoted += 1
            else:
                s = spot.get(t, np.nan)
                if not np.isfinite(s):
                    continue
                v = max(s - k, 0.0)
            values.append(max(v - TICK, 0.0))
        if not values or cut:
            continue
        values = np.array(values)
        fees = (entry + values[-1]) * TAX
        rec = {"day": day, "rung": rung, "strike": k, "cost": cost,
               "spot": spot.get(ts, np.nan), "hold": values[-1] - cost - fees,
               "best": values.max() - cost - fees,
               "quoted": quoted / len(values), "bars": len(values)}
        for mult in (1.5, 2.0, 3.0):
            hit = (values >= cost * mult).any()
            rec["t{:g}".format(mult)] = (
                (cost * mult if hit else values[-1]) - cost - fees)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# reporting. Per-SESSION first, because that is the unit that reversed the ATM
# result: sum(pnl)/sum(cost) let a handful of trades carry the whole column.


def sessions(v, col):
    g = v.groupby("day").apply(
        lambda x: x[col].sum() / x.cost.sum() * 100, include_groups=False)
    return g


def report(trades, rung):
    log("")
    log("=" * 102)
    log("RUNG {} -- buy the morning after the signal, hold 5 sessions".format(rung))
    log("=" * 102)
    v = trades[trades.rung == rung]
    if len(v) < 100:
        log("  only {} trades -- not reported".format(len(v)))
        return None
    log("{:,} trades, {} symbols, {} sessions. Median premium Rs{:.1f}"
        " = {:.2f}% of spot. Quoted {:.0f}% of held bars.".format(
            len(v), v.symbol.nunique(), v.day.nunique(), v.cost.median(),
            (v.cost / v.spot).median() * 100, v.quoted.mean() * 100))

    sig, non = v[v.signal], v[~v.signal]
    if len(sig) < 50 or len(non) < 50:
        log("  signal/non-signal split too thin ({} / {})".format(len(sig), len(non)))
        return None
    log("signal: {:,} trades / {} sessions   no signal: {:,} / {}".format(
        len(sig), sig.day.nunique(), len(non), non.day.nunique()))

    log("")
    log("  {:<26} {:>12} {:>12} {:>10}".format("measure", "SIGNAL", "no signal", "gap"))
    row = {"rung": rung, "n_sig": len(sig), "n_non": len(non)}
    checks = [
        ("pooled sum(pnl)/sum(cost)", lambda x: x.hold.sum() / x.cost.sum() * 100),
        ("equal-weighted mean", lambda x: (x.hold / x.cost).mean() * 100),
        ("median trade", lambda x: (x.hold / x.cost).median() * 100),
        ("win rate", lambda x: (x.hold > 0).mean() * 100),
        ("per-session mean", lambda x: sessions(x, "hold").mean()),
        ("per-session median", lambda x: sessions(x, "hold").median()),
        ("positive sessions", lambda x: (sessions(x, "hold") > 0).mean() * 100),
        ("premium as % of spot", lambda x: (x.cost / x.spot).median() * 100),
    ]
    for name, fn in checks:
        a, b = fn(sig), fn(non)
        row[name] = a - b
        log("  {:<26} {:>11.1f}% {:>11.1f}% {:>+9.1f}pp".format(name, a, b, a - b))

    log("")
    log("  {:<26} {:>12} {:>12} {:>10}".format(
        "tail on a perfect exit", "SIGNAL", "no signal", "gap"))
    for label, mult in [(">=2x", 2), (">=3x", 3), (">=5x", 5), (">=10x", 10)]:
        a = ((sig.best + sig.cost) / sig.cost >= mult).mean() * 100
        b = ((non.best + non.cost) / non.cost >= mult).mean() * 100
        row[label] = a - b
        log("  {:<26} {:>11.1f}% {:>11.1f}% {:>+9.1f}pp".format(label, a, b, a - b))
    row["t_sig"] = day_t(sig.hold.values, sig.day.values)
    log("")
    log("  day-clustered t on the signal column: {:+.2f}".format(row["t_sig"]))
    return row


def main():
    scored = signal_days()
    days = sorted(scored.day.unique())
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    scored["entry"] = scored.day.map(nxt)
    scored = scored.dropna(subset=["entry"])
    log("{:,} scored stock-days, {:,} signals, {} test sessions".format(
        len(scored), int(scored.signal.sum()), scored.day.nunique()))

    opts = load()
    log("{:,} CALL bars, {} symbols, {} -> {}, offsets {}".format(
        len(opts), opts.symbol.nunique(), opts.ts.min().date(), opts.ts.max().date(),
        ", ".join(sorted(opts.rel.unique()))))

    rows = []
    universe = sorted(set(opts.symbol.unique()) & set(scored.symbol.unique()))
    for symbol in universe:
        want = scored[scored.symbol == symbol]
        flags = want.set_index("entry").signal.to_dict()
        sub = opts[opts.symbol == symbol]
        for rung in RUNGS:
            for t in run_symbol(sub, set(want.entry), rung):
                t["symbol"], t["signal"] = symbol, bool(flags.get(t["day"], False))
                rows.append(t)
    trades = pd.DataFrame(rows)
    if trades.empty:
        log("no trades opened.")
        return

    summary = [r for r in (report(trades, rung) for rung in RUNGS) if r]
    if len(summary) > 1:
        log("")
        log("=" * 102)
        log("THE LADDER -- does going cheaper convert the same move into a bigger")
        log("multiple, and does the signal survive out there?")
        log("=" * 102)
        log("  {:<8} {:>10} {:>12} {:>12} {:>10} {:>10} {:>10}".format(
            "rung", "n signal", "per-sess med", "pos sess", ">=2x", ">=3x", ">=5x"))
        for r in summary:
            log("  {:<8} {:>10,} {:>+11.1f}pp {:>+11.1f}pp {:>+9.1f}pp"
                " {:>+9.1f}pp {:>+9.1f}pp".format(
                    r["rung"], r["n_sig"], r["per-session median"],
                    r["positive sessions"], r[">=2x"], r[">=3x"], r[">=5x"]))
        log("")
        log("  Every cell is SIGNAL minus NO-SIGNAL at that rung. A working cheap-strike")
        log("  story needs the gaps to grow as the rung goes out. Flat or shrinking means")
        log("  the OTM skew prices the move exactly as ATM does.")

    trades.to_csv(os.path.join(HERE, "premove_otm.csv"), index=False)
    pd.DataFrame(summary).to_csv(os.path.join(HERE, "premove_otm_summary.csv"),
                                 index=False)
    log("\nwrote premove_otm.csv, premove_otm_summary.csv")


if __name__ == "__main__":
    main()
