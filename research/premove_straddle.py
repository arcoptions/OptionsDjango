"""The motion signal, traded as a real straddle at real quoted prices.

`premove_direction.py` closed with one thread deliberately left open.  The signal
predicts MOTION and not direction (AUC 0.510 on the side), so the only honest way
to trade it is a straddle -- and the straddle looked priced: realised 5-day move
over IV-implied was 1.02x overall, 1.06x on the strongest 2% of signals.

That comparison has a real weakness, and it points the RIGHT way for once.  It was
CLOSE-TO-CLOSE.  An option buyer is not forced to hold to day five; they exit at
the best point they can see.  And signal days do carry bigger swings -- mean
favourable excursion +5.30% against +3.46% elsewhere.  So the sqrt(2/pi) IV
comparison may understate what a buyer with a working exit actually collects.

This file replaces the approximation with the thing itself: buy the ATM call and
the ATM put at the open of the session AFTER the signal, at quoted prices, pay
the tick on every leg every way, and hold under several exits.  No IV model, no
lognormal assumption, no fair-value formula -- just what the contracts printed.

THE COMPARISON IS AGAINST NON-SIGNAL DAYS, not against zero.  Buying premium is a
losing trade on this cache before any signal is applied (0.77x per 2-day hold at
ATM), so a straddle that loses 8% on signal days and 15% everywhere else is the
signal WORKING.  The number that matters is the gap between the two columns.

THREE EXITS, and the middle one is the only realistic one:

  hold      value at exactly 5 sessions. The floor.
  best      max over the path of (call + put) at the SAME instant -- not the sum
            of each leg's own high, which is not purchasable. Perfect foresight,
            so it is a ceiling nobody reaches, and it exists to bracket the
            answer: if even this loses, nothing about the exit can save it.
  target    first bar where the pair is worth `TARGET`x cost, else hold to 5d.
            This is implementable: it needs no foresight, only a limit order.

SURVIVORSHIP CUTS THE OTHER WAY HERE, and it must be said plainly.  The rolling
feed is ATM-relative, so a pinned strike stops being quoted once spot walks away
from it -- which for a straddle is exactly when the straddle WINS.  Imputing the
missing quote at intrinsic strips all remaining time value, so this run
UNDERSTATES the straddle.  A loss measured this way is therefore robust; a
profit measured this way would be a floor, not a headline.
"""

import datetime as dt
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

HOLD = 125        # 5 sessions of 15-minute bars
TARGET = 1.50     # the limit order: +50% on the pair
TOP = 0.10        # the signal slice
HORIZON, FOLDS, EMBARGO = 5, 3, 5

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
    """(symbol, day) -> out-of-sample P(big move either way), test folds only."""
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
# the straddle


def load(symbol=None):
    q = StockOptionCandle.objects.filter(interval_minutes=15, relative_strike="ATM")
    if symbol:
        q = q.filter(symbol=symbol)
    rows = q.values_list("symbol", "timestamp", "option_type", "strike", "spot",
                         "open", "close", "volume")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "side", "strike", "spot",
                                          "open", "close", "volume"])
    f["ts"] = pd.to_datetime(f.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for c in ("strike", "spot", "open", "close", "volume"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f.drop_duplicates(subset=["symbol", "ts", "side", "strike"])


def run_symbol(frame, entries):
    """entries: {date} on which to open at the first bar. Returns trade dicts."""
    calls = frame[frame.side == "CALL"]
    puts = frame[frame.side == "PUT"]
    if calls.empty or puts.empty:
        return []
    c_open = calls.pivot_table(index="ts", columns="strike", values="open")
    grid = c_open.index
    c_close = calls.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    p_open = puts.pivot_table(index="ts", columns="strike", values="open").reindex(grid)
    p_close = puts.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    c_vol = calls.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    p_vol = puts.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    spot = (frame.pivot_table(index="ts", columns="strike", values="spot")
            .reindex(grid).mean(axis=1))
    atm = calls.set_index("ts").strike.reindex(grid)

    days = pd.Series(grid.date, index=grid)
    first_bar = {d: g.index[0] for d, g in days.groupby(days)}
    pos = {t: i for i, t in enumerate(grid)}

    out = []
    for day in sorted(entries):
        ts = first_bar.get(day)
        if ts is None:
            continue
        k = atm.get(ts, np.nan)
        if not np.isfinite(k) or k not in c_open.columns or k not in p_open.columns:
            continue
        c_in, p_in = c_open.at[ts, k], p_open.at[ts, k]
        if not (np.isfinite(c_in) and np.isfinite(p_in)) or min(c_in, p_in) < 0.50:
            continue
        if not (c_vol.at[ts, k] > 0 and p_vol.at[ts, k] > 0):
            continue
        cost = (c_in + TICK) + (p_in + TICK)
        i = pos[ts]
        path, quoted_all = [], True
        for j in range(i + 1, min(i + 1 + HOLD, len(grid))):
            t = grid[j]
            cv, pv = c_close.at[t, k], p_close.at[t, k]
            if not (np.isfinite(cv) and np.isfinite(pv)):
                quoted_all = False
                s = spot.get(t, np.nan)
                if not np.isfinite(s):
                    continue
                # Intrinsic understates a straddle: it deletes time value from a
                # leg that is deep ITM precisely because the trade is winning.
                cv = max(s - k, 0.0) if not np.isfinite(cv) else cv
                pv = max(k - s, 0.0) if not np.isfinite(pv) else pv
            path.append(((cv - TICK) + (pv - TICK), j))
        if not path:
            continue
        values = np.array([v for v, _ in path])
        fees = (c_in + p_in + values[-1]) * TAX
        hit = np.argmax(values >= cost * TARGET) if (values >= cost * TARGET).any() else None
        out.append({
            "day": day, "strike": k, "cost": cost, "spot": spot.get(ts, np.nan),
            "hold": values[-1] - cost - fees,
            "best": values.max() - cost - fees,
            "target": (cost * TARGET if hit is not None else values[-1]) - cost - fees,
            "hit_target": hit is not None,
            "bars": len(values), "quoted_all": quoted_all,
        })
    return out


def summarise(trades, name):
    v = trades.dropna(subset=["hold"])
    if len(v) < 50:
        log("  {:<22} too few trades ({})".format(name, len(v)))
        return None
    row = {"slice": name, "n": len(v), "cost": v.cost.median()}
    for exit_name in ("hold", "best", "target"):
        row[exit_name] = v[exit_name].sum() / v.cost.sum() * 100
        row[exit_name + "_win"] = (v[exit_name] > 0).mean() * 100
    row["t"] = day_t(v.hold.values, v.day.values)
    row["target_hit"] = v.hit_target.mean() * 100
    log("  {:<22} {:>7,d} {:>10.1f}% {:>10.1f}% {:>10.1f}% {:>10.1f}% {:>9}".format(
        name, len(v), row["hold"], row["target"], row["best"], row["target_hit"],
        "n/a" if not np.isfinite(row["t"]) else "{:+.2f}".format(row["t"])))
    return row


def main():
    log("scoring the motion signal out of sample")
    scored = signal_days()
    log("{:,} scored stock-days, {:,} in the top {:.0f}% ({} sessions)".format(
        len(scored), int(scored.signal.sum()), TOP * 100, scored.day.nunique()))

    # The signal is known at the CLOSE of day t, so the trade opens on day t+1.
    sessions = sorted(scored.day.unique())
    nxt = {d: sessions[i + 1] for i, d in enumerate(sessions[:-1])}
    scored["entry"] = scored.day.map(nxt)
    scored = scored.dropna(subset=["entry"])

    log("loading ATM option bars")
    opts = load()
    log("{:,} ATM bars, {} symbols, {} -> {}".format(
        len(opts), opts.symbol.nunique(), opts.ts.min().date(), opts.ts.max().date()))

    universe = set(opts.symbol.unique())
    rows = []
    for symbol in sorted(universe & set(scored.symbol.unique())):
        want = scored[scored.symbol == symbol]
        trades = run_symbol(opts[opts.symbol == symbol], set(want.entry))
        flags = want.set_index("entry").signal.to_dict()
        moves = want.set_index("entry")[["up_max", "dn_max"]].to_dict("index")
        for t in trades:
            t["symbol"] = symbol
            t["signal"] = bool(flags.get(t["day"], False))
            t.update(moves.get(t["day"], {}))
            rows.append(t)
    trades = pd.DataFrame(rows)
    if trades.empty:
        log("no straddles could be opened.")
        return
    log("\n{:,} ATM straddles opened, {} symbols, median cost Rs{:.1f} on spot Rs{:.0f}"
        " ({:.1f}% of spot)".format(
            len(trades), trades.symbol.nunique(), trades.cost.median(),
            trades.spot.median(), (trades.cost / trades.spot).median() * 100))
    log("{:.0f}% of trades kept a quote on the pinned strike for the whole 5 sessions;"
        " the rest are imputed at intrinsic, which understates them.".format(
            trades.quoted_all.mean() * 100))

    log("\n" + "=" * 96)
    log("ATM STRADDLE ON THE MOTION SIGNAL. Return on premium paid. The signal column")
    log("must beat the non-signal column -- buying premium loses on this cache before")
    log("any signal is applied, so 'less negative' IS the edge, and zero is not the bar.")
    log("=" * 96)
    log("  {:<22} {:>7} {:>11} {:>11} {:>11} {:>11} {:>9}".format(
        "slice", "n", "hold 5d", "+50% tgt", "best (max)", "tgt hit%", "t(day)"))
    summary = []
    for name, sub in [("all entries", trades),
                      ("SIGNAL (top 10%)", trades[trades.signal]),
                      ("no signal", trades[~trades.signal])]:
        r = summarise(sub, name)
        if r:
            summary.append(r)

    if len(summary) >= 3:
        sig = next(r for r in summary if r["slice"].startswith("SIGNAL"))
        non = next(r for r in summary if r["slice"] == "no signal")
        log("")
        log("  GAP (signal minus no-signal): hold {:+.1f}pp, target {:+.1f}pp,"
            " best {:+.1f}pp".format(sig["hold"] - non["hold"],
                                     sig["target"] - non["target"],
                                     sig["best"] - non["best"]))

    # Does the signal at least concentrate the big winners? A straddle buyer
    # lives on the tail, so the mean can lose while the tail is the whole trade.
    log("\n" + "-" * 96)
    log("THE TAIL. A premium buyer is paid by the few trades that multiply, so the")
    log("mean can be negative and the strategy still work IF the tail is fat enough.")
    log("-" * 96)
    log("  {:<22} {:>10} {:>10} {:>10} {:>10} {:>12}".format(
        "slice", ">=1.5x", ">=2x", ">=3x", ">=5x", "best median"))
    for name, sub in [("SIGNAL (top 10%)", trades[trades.signal]),
                      ("no signal", trades[~trades.signal])]:
        if len(sub) < 50:
            continue
        mult = (sub.best + sub.cost) / sub.cost
        log("  {:<22} {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>11.2f}x".format(
            name, (mult >= 1.5).mean() * 100, (mult >= 2).mean() * 100,
            (mult >= 3).mean() * 100, (mult >= 5).mean() * 100, mult.median()))

    trades.to_csv(os.path.join(HERE, "premove_straddle.csv"), index=False)
    pd.DataFrame(summary).to_csv(os.path.join(HERE, "premove_straddle_summary.csv"),
                                 index=False)
    log("\nwrote premove_straddle.csv, premove_straddle_summary.csv")


if __name__ == "__main__":
    main()
