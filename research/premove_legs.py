"""One leg at a time, at full sample size, with the strike tracked properly.

`premove_straddle.py` produced a +6.5pp gap in favour of the signal and it has to
be thrown away.  Two things were wrong with it, one in the data and one mine:

  THE DATA.  The PUT cache has a nine-month hole -- Sep 2025 to May 2026 runs
  0.0-1.0% puts against a 50% share either side of it.  So every straddle it
  could open landed in Jun-Aug 2026, and the 68 signal trades sat in 34 sessions
  of a single quarter.  t = -0.77.  That is one regime, not a result.

  MINE.  The strike was pinned at entry, correctly, but only `relative_strike =
  'ATM'` was loaded.  A strike that starts at the money and drifts one step is
  still quoted -- as ATM+1 -- and reading it as missing forced 99% of exits to
  intrinsic.  The fix is to pivot on the ABSOLUTE strike across every relative
  offset in the cache, which is what this file does.  The tracked window is then
  ATM-1..ATM+2 wide instead of a single step.

The CALL series has no hole, so the CE leg runs the full 18 months at full power.
That is also the leg the original question was about: predict the move, buy the
call.  The PUT leg runs on the months that have puts and is reported separately
with its own sample size attached, never pooled with the call leg.

A THIRD BUG, found 2026-08-19, long after this file's results were used.  The
absolute-strike fix above pins the STRIKE; it does not pin the CONTRACT, because
the cache is nearest-expiry.  A hold running past a roll was marked out against
next month's option at the same strike, carrying a month of time value it never
paid for.  23% of five-session holds, and they returned +36.8% at the median on
trades where the stock FELL.  `expiries()` below detects the rolls and
`run_symbol` now cuts the hold at one BY DEFAULT.  See `expiry_contamination.py`
for the cost: the shipped break-even hurdle moved 56.8% -> 67.1%.

WHAT THE COMPARISON IS.  Not profit against zero -- buying ATM premium loses on
this cache before any signal (0.77x per 2-day hold), so the signal column is
measured against the NON-SIGNAL column and the gap is the whole finding.  A CE
that loses 20% on signal days and 30% otherwise is a working signal on a losing
instrument, and that distinction decides whether a cheaper strike could rescue it.

DIRECTION IS DEAD, so a CE-only trade is knowingly betting on the up side of a
move whose side is unpredictable.  It is run anyway because 61% of the 10% movers
in this sample were up moves, which is drift plus the sample's own bull market --
and if CE cannot beat non-signal CE even with that tailwind, nothing downstream
is worth pricing.
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
from django.db import connection  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")

HOLD = 125
TOP = 0.10
FOLDS, EMBARGO = 3, 5
TARGETS = (1.5, 2.0, 3.0)

DROP = {"symbol", "day", "o", "h", "l", "c", "v", "contaminated",
        "up_max", "dn_max", "fwd_close", "iv_call", "iv_put", "oi_call", "oi_put",
        "up5", "up10", "up20", "dn5", "dn10", "dn20", "dollar_vol", "oi", "iv"}


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


def load(side):
    """Every relative offset, keyed on the ABSOLUTE strike.

    This is the fix. The feed is ATM-relative, so the same contract appears
    under ATM today and ATM+1 next week; keying on the absolute strike keeps the
    pinned contract visible across the whole ATM-1..ATM+2 band instead of losing
    it the moment spot moves one step.
    """
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15, option_type=side,
    ).exclude(relative_strike__startswith="K").values_list(
        "symbol", "timestamp", "relative_strike", "strike", "spot",
        "open", "close", "volume")
    f = pd.DataFrame(list(rows), columns=["symbol", "ts", "rel", "strike", "spot",
                                          "open", "close", "volume"])
    f["ts"] = pd.to_datetime(f.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for c in ("strike", "spot", "open", "close", "volume"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f.drop_duplicates(subset=["symbol", "ts", "strike"])


_EXPIRIES = {}


def expiries(side="CALL"):
    """Front-month death days, read off the premium series.

    `expiry_code=1` is NEAREST expiry with no expiry DATE stored, so a pinned
    strike names one contract only within a cycle. Hold past the roll and the
    same strike is next month's option carrying a month of time value the trade
    never paid for -- which is worth +36.8% at the median on trades where the
    stock FELL, i.e. a call that pays for being wrong. `option_spreads.py` and
    `chartink_options.py` have guarded this since they were written; this file
    did not, and five studies built on it inherited the error. See
    `expiry_contamination.py` for what it cost.

    Detected rather than assumed, because a last-Thursday rule is right 6 times
    in 17: NSE moved equity derivatives to the last TUESDAY partway through the
    sample, and a calendar written today goes stale the next time they move it.
    The premium collapses into expiry and roughly doubles on the roll, so the
    jump is unmistakable at any threshold between about 1.5x and 2.5x.
    """
    if side in _EXPIRIES:
        return _EXPIRIES[side]
    with connection.cursor() as cur:
        cur.execute(
            "SELECT date(timestamp), AVG(open/spot), COUNT(*) "
            "FROM options_tracker_stockoptioncandle "
            "WHERE option_type=%s AND relative_strike='ATM' AND spot>0 AND open>0 "
            "GROUP BY 1 ORDER BY 1", [side])
        rows = cur.fetchall()
    f = pd.DataFrame(rows, columns=["d", "pp", "n"])
    f["d"] = pd.to_datetime(f["d"]).dt.date
    f["jump"] = f["pp"].shift(-1) / f["pp"]
    hits = f[(f["jump"] > 1.8) & (f["n"] > 200) & (f["d"] > f["d"].min())]["d"]
    # One roll a month. The premium decays for days beforehand, so nearby
    # sessions can also clear the threshold -- the LAST jump in a month is the
    # roll itself and the earlier ones are the decay leading into it.
    last = {}
    for day in hits:
        last[(day.year, day.month)] = max(last.get((day.year, day.month), day), day)
    _EXPIRIES[side] = sorted(last.values())
    return _EXPIRIES[side]


def run_symbol(frame, entries, side, deadlines=None):
    """One ATM contract per entry, held HOLD bars or until the contract dies.

    `deadlines` DEFAULTS to the expiries detected for `side`, so a caller gets
    the guard without knowing it needs one -- three studies imported this
    function and inherited the bug precisely because the unguarded path was the
    easy one. Pass an empty list to reproduce the old behaviour; the only
    reason to do that is to measure what it cost, and it now has to be asked
    for out loud.
    """
    if deadlines is None:
        deadlines = expiries(side)
    o = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = o.index
    cl = frame.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    vol = frame.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    spot = (frame.pivot_table(index="ts", columns="strike", values="spot")
            .reindex(grid).mean(axis=1))
    atm = (frame[frame.rel == "ATM"].drop_duplicates("ts")
           .set_index("ts").strike.reindex(grid))

    days = pd.Series(grid.date, index=grid)
    first_bar = {d: g.index[0] for d, g in days.groupby(days)}
    pos = {t: i for i, t in enumerate(grid)}

    out = []
    for day in sorted(entries):
        ts = first_bar.get(day)
        if ts is None:
            continue
        k = atm.get(ts, np.nan)
        if not np.isfinite(k) or k not in o.columns:
            continue
        entry = o.at[ts, k]
        if not np.isfinite(entry) or entry < 1.0 or not vol.at[ts, k] > 0:
            continue
        cost = entry + TICK
        i = pos[ts]
        # The last session on which this strike is still this contract.
        dead = next((e for e in deadlines if e >= day), None) if deadlines else None
        values, quoted = [], 0
        for j in range(i + 1, min(i + 1 + HOLD, len(grid))):
            t = grid[j]
            if dead is not None and t.date() > dead:
                break
            v = cl.at[t, k]
            if np.isfinite(v):
                quoted += 1
            else:
                s = spot.get(t, np.nan)
                if not np.isfinite(s):
                    continue
                v = max(s - k, 0.0) if side == "CALL" else max(k - s, 0.0)
            values.append(max(v - TICK, 0.0))
        if not values:
            continue
        values = np.array(values)
        fees = (entry + values[-1]) * TAX
        rec = {"day": day, "strike": k, "cost": cost, "spot": spot.get(ts, np.nan),
               "hold": values[-1] - cost - fees, "best": values.max() - cost - fees,
               "quoted": quoted / len(values), "bars": len(values),
               # A hold cut short is a different trade from one that ran its
               # course, and the caller must be able to tell them apart.
               "truncated": bool(dead is not None and len(values) < HOLD)}
        for mult in TARGETS:
            hit = (values >= cost * mult).any()
            rec["t{:g}".format(mult)] = (
                (cost * mult if hit else values[-1]) - cost - fees)
            rec["hit{:g}".format(mult)] = hit
        out.append(rec)
    return out


def summarise(trades, name, exits):
    v = trades.dropna(subset=["hold"])
    if len(v) < 40:
        log("  {:<20} only {} trades -- not reported".format(name, len(v)))
        return None
    row = {"slice": name, "n": len(v)}
    cells = []
    for e in exits:
        row[e] = v[e].sum() / v.cost.sum() * 100
        cells.append("{:>10.1f}%".format(row[e]))
    row["t"] = day_t(v.hold.values, v.day.values)
    log("  {:<20} {:>7,d} {} {:>9} {:>9.0f}%".format(
        name, len(v), "".join(cells),
        "n/a" if not np.isfinite(row["t"]) else "{:+.2f}".format(row["t"]),
        v.quoted.mean() * 100))
    return row


def leg(side, scored, label):
    log("")
    log("=" * 100)
    log(label)
    log("=" * 100)
    opts = load(side)
    months = pd.to_datetime(opts.ts).dt.to_period("M")
    log("{:,} {} bars, {} symbols, {} -> {}, offsets {}".format(
        len(opts), side, opts.symbol.nunique(), opts.ts.min().date(),
        opts.ts.max().date(), ", ".join(sorted(opts.rel.unique()))))

    rows = []
    deadlines = expiries(side)
    log("{} expiries detected from the premium series, {} -> {}. Holds are cut"
        .format(len(deadlines), deadlines[0], deadlines[-1]))
    log("there: past the roll a pinned strike is next month's contract.")
    for symbol in sorted(set(opts.symbol.unique()) & set(scored.symbol.unique())):
        want = scored[scored.symbol == symbol]
        flags = want.set_index("entry").signal.to_dict()
        for t in run_symbol(opts[opts.symbol == symbol], set(want.entry), side,
                            deadlines):
            t["symbol"], t["signal"] = symbol, bool(flags.get(t["day"], False))
            rows.append(t)
    trades = pd.DataFrame(rows)
    if trades.empty:
        log("no trades opened.")
        return trades

    span = pd.to_datetime(trades.day)
    log("{:,} trades, {} symbols, {} sessions, {} -> {}. Median premium Rs{:.1f}"
        " ({:.1f}% of spot).".format(
            len(trades), trades.symbol.nunique(), trades.day.nunique(),
            span.min().date(), span.max().date(), trades.cost.median(),
            (trades.cost / trades.spot).median() * 100))
    log("signal trades: {:,} across {} sessions".format(
        int(trades.signal.sum()), trades[trades.signal].day.nunique()))

    exits = ["hold", "t1.5", "t2", "t3", "best"]
    log("")
    log("  {:<20} {:>7} {:>11} {:>11} {:>11} {:>11} {:>11} {:>9} {:>10}".format(
        "slice", "n", "hold 5d", "+50% tgt", "2x tgt", "3x tgt", "best(max)",
        "t(day)", "quoted"))
    summary = []
    for name, sub in [("all entries", trades),
                      ("SIGNAL top 10%", trades[trades.signal]),
                      ("no signal", trades[~trades.signal])]:
        r = summarise(sub, name, exits)
        if r:
            summary.append(r)
    if len(summary) == 3:
        sig = summary[1]
        non = summary[2]
        log("")
        log("  GAP (signal - no signal): " + ", ".join(
            "{} {:+.1f}pp".format(e, sig[e] - non[e]) for e in exits))

    log("")
    log("  {:<20} {:>10} {:>10} {:>10} {:>10} {:>12}".format(
        "tail (best exit)", ">=1.5x", ">=2x", ">=3x", ">=5x", "median"))
    for name, sub in [("SIGNAL top 10%", trades[trades.signal]),
                      ("no signal", trades[~trades.signal])]:
        if len(sub) < 40:
            continue
        mult = (sub.best + sub.cost) / sub.cost
        log("  {:<20} {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>11.2f}x".format(
            name, (mult >= 1.5).mean() * 100, (mult >= 2).mean() * 100,
            (mult >= 3).mean() * 100, (mult >= 5).mean() * 100, mult.median()))
    return trades


def main():
    scored = signal_days()
    sessions = sorted(scored.day.unique())
    nxt = {d: sessions[i + 1] for i, d in enumerate(sessions[:-1])}
    scored["entry"] = scored.day.map(nxt)
    scored = scored.dropna(subset=["entry"])
    log("{:,} scored stock-days, {:,} signals, {} test sessions".format(
        len(scored), int(scored.signal.sum()), scored.day.nunique()))

    calls = leg("CALL", scored,
                "CE LEG -- buy the ATM call the morning after the signal. Full 18 months.")
    puts = leg("PUT", scored,
               "PE LEG -- same trade, put side. The Sep2025-May2026 hole means this is"
               " a SHORTER and more clustered sample; read its session count, not just n.")

    for name, t in [("premove_ce.csv", calls), ("premove_pe.csv", puts)]:
        if len(t):
            t.to_csv(os.path.join(HERE, name), index=False)
    log("\nwrote premove_ce.csv, premove_pe.csv")


if __name__ == "__main__":
    main()
