"""Chartink trigger -> stock -> CE.  The two-stage test.

The workflow this is meant to price is: a Chartink scan names a stock, we buy
its call, we exit with a gain.  So the question splits in two and the second
half is only worth asking if the first half survives.

STAGE 1 asks whether the trigger moves the STOCK.  Two corrections carry over
from `chartink_study.py` and neither is optional:

  Entry price.  Chartink stamps a trigger with the candle's START, not its
  close.  A 09:15 15-minute trigger describes 09:15-09:30 and is not knowable
  until 09:30.  Entry is the OPEN OF THE BAR AFTER THE SIGNAL CANDLE CLOSES.

  Market drift.  A scan that fires on strength fires on strong days, so its
  raw return is mostly the market's.  For every trigger we also compute what
  the average cached stock did over the identical clock window and report the
  difference.  That EXCESS number is the only one that means anything.

STAGE 2 buys the at-the-money call at that same bar and holds it to a set of
exits.  One trap governs this half: the Dhan rolling feed is ATM-RELATIVE, so
`relative_strike='ATM'` re-centres as spot moves and the "ATM series" is not a
position anyone can hold.  We therefore read the strike at entry and follow
that FIXED strike forward, which is what a real order does.

Costs are a 5-paise tick crossed each way plus 0.28% turnover tax, the same
charge used in the base-rate study, so the numbers are comparable.
"""

import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import (  # noqa: E402
    StockEquityCandle,
    StockOptionCandle,
    TrackedStock,
)

DOWNLOADS = os.path.expanduser("~/Downloads")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# name: (files, 15-minute bars spanned by the signal candle)
SCANS = {
    "ARC15MIN": (["Backtest arc15min.csv", "Backtest arc15min (1).csv"], 1),
    "NARC1HR": (["Backtest narc1hr.csv"], 4),
}

# Bars ahead on the 15-minute grid.  25 bars is one full session.
HORIZONS = [("30m", 2), ("1h", 4), ("2h", 8), ("EOD", 25), ("2d", 50)]

TICK = 0.05        # crossed each way
TAX = 0.0028       # round-trip turnover charge
TRAIL = 0.30       # give back 30% off the peak -- best exit found in the base study
MAX_HOLD = 50      # bars; two sessions, matching the base-rate study


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- triggers

def load_triggers():
    """Both scans, one frame, with the entry bar already resolved."""
    out = []
    for scan, (files, span) in SCANS.items():
        for fn in files:
            path = os.path.join(DOWNLOADS, fn)
            frame = pd.read_csv(path)
            frame.columns = [c.strip().strip('"').lstrip("﻿") for c in frame.columns]
            frame["signal_ts"] = pd.to_datetime(
                frame["Date"].str.strip(), format="%d-%m-%Y %I:%M %p"
            )
            frame["scan"] = scan
            frame["symbol"] = frame["Symbol"].str.strip()
            # The signal candle closes span*15 minutes after it is stamped.
            frame["entry_ts"] = frame["signal_ts"] + pd.Timedelta(minutes=15 * span)
            out.append(frame[["scan", "symbol", "signal_ts", "entry_ts"]])
    trig = pd.concat(out, ignore_index=True)
    trig = trig.drop_duplicates(subset=["scan", "symbol", "signal_ts"])
    return trig.sort_values("entry_ts").reset_index(drop=True)


# ---------------------------------------------------------------- equity

def load_equity(start, end):
    rows = StockEquityCandle.objects.filter(
        interval_minutes=15, timestamp__gte=start, timestamp__lte=end
    ).values_list("symbol", "timestamp", "open", "close")
    frame = pd.DataFrame(list(rows), columns=["symbol", "ts", "open", "close"])
    frame["ts"] = pd.to_datetime(frame.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for col in ("open", "close"):
        frame[col] = frame[col].astype(float)
    return frame


def stage_one(trig, eq):
    """Forward stock returns, raw and net of what the average stock did."""
    opens = eq.pivot_table(index="ts", columns="symbol", values="open")
    closes = eq.pivot_table(index="ts", columns="symbol", values="close")
    grid = opens.index
    pos = {t: i for i, t in enumerate(grid)}

    # Universe control: for every bar and horizon, the mean forward return of
    # every stock in the cache.  Same clock window, so drift cancels.
    control = {}
    for label, bars in HORIZONS:
        fwd = closes.shift(-bars) / opens - 1.0
        control[label] = fwd.mean(axis=1)

    recs = []
    for row in trig.itertuples():
        # First bar at or after the signal candle's close.
        idx = grid.searchsorted(row.entry_ts)
        if idx >= len(grid):
            continue
        bar = grid[idx]
        # Do not reach across a day boundary for an entry.
        if bar.date() != row.entry_ts.date():
            continue
        if row.symbol not in opens.columns:
            continue
        entry = opens.at[bar, row.symbol]
        if not np.isfinite(entry) or entry <= 0:
            continue
        rec = {
            "scan": row.scan,
            "symbol": row.symbol,
            "day": bar.date(),
            "entry_ts": bar,
            "entry": entry,
        }
        i = pos[bar]
        for label, bars in HORIZONS:
            j = i + bars
            if j >= len(grid):
                rec[label] = np.nan
                rec[label + "_x"] = np.nan
                continue
            exitp = closes.iat[j, closes.columns.get_loc(row.symbol)]
            raw = exitp / entry - 1.0 if np.isfinite(exitp) else np.nan
            rec[label] = raw
            rec[label + "_x"] = raw - control[label].iloc[i]
        recs.append(rec)
    return pd.DataFrame(recs)


def day_clustered_t(series, days):
    """One bet per day, not one per overlapping trigger."""
    frame = pd.DataFrame({"v": series, "d": days}).dropna()
    if frame.empty:
        return np.nan, 0
    daily = frame.groupby("d")["v"].mean()
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return np.nan, len(daily)
    return daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))), len(daily)


# ---------------------------------------------------------------- options

def load_options(start, end, symbols):
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15,
        option_type="CALL",
        timestamp__gte=start,
        timestamp__lte=end,
        symbol__in=list(symbols),
    ).values_list("symbol", "timestamp", "strike", "spot", "open", "high", "close")
    frame = pd.DataFrame(
        list(rows), columns=["symbol", "ts", "strike", "spot", "open", "high", "close"]
    )
    if frame.empty:
        return frame
    frame["ts"] = pd.to_datetime(frame.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for col in ("strike", "spot", "open", "high", "close"):
        frame[col] = frame[col].astype(float)
    # A strike can appear twice at one stamp when the feed re-centres onto it
    # from both sides; keep the first.
    return frame.drop_duplicates(subset=["symbol", "ts", "strike"])


def find_expiries(opts):
    """The days the front month dies, read off the data.

    The cache stores `expiry_code=1` -- front month -- but never the expiry
    DATE, so a fixed strike followed across a rollover walks out of the dying
    contract and into next month's at a fraction of the price.  That is not a
    trade, it is a bookkeeping error, and it printed a 228x on KALYANKJIL
    before this guard existed.

    An expiry is visible without any calendar: the median at-the-money premium
    decays to nearly nothing, then the next session opens a whole month richer.
    """
    near = opts[(opts.spot > 0) & ((opts.strike - opts.spot).abs() / opts.spot < 0.01)].copy()
    near["day"] = near.ts.dt.date
    ratio = near.groupby("day").apply(
        lambda x: (x.close / x.spot).median(), include_groups=False
    ).sort_index()
    days = list(ratio.index)
    out = set()
    for i in range(len(days) - 1):
        here, nxt = ratio.iloc[i], ratio.iloc[i + 1]
        if here > 0 and nxt / here > 2.5:
            out.add(days[i])
    return sorted(out)


def net_multiple(entry, exitp):
    """What a rupee becomes, after crossing the tick both ways and tax."""
    paid = entry + TICK
    got = exitp - TICK
    if paid <= 0:
        return np.nan
    return max(got, 0.0) / paid * (1.0 - TAX)


def stage_two(stock_rows, opts, expiries):
    """Buy the ATM call at the entry bar, follow that FIXED strike out."""
    by_symbol = {s: g for s, g in opts.groupby("symbol")}
    recs = []
    for row in stock_rows.itertuples():
        g = by_symbol.get(row.symbol)
        if g is None:
            continue
        at_entry = g[g.ts == row.entry_ts]
        if at_entry.empty:
            continue
        # The strike the feed calls at-the-money at this instant.
        pick = at_entry.iloc[(at_entry.strike - at_entry.spot).abs().argsort().iloc[0]]
        strike = float(pick.strike)
        prem = float(pick.open)
        if not np.isfinite(prem) or prem <= 0:
            continue

        # From here on it is ONE contract, not a rolling ATM series -- and it
        # dies at the front-month expiry, so the hold stops there whatever the
        # horizon says.
        dead = next((e for e in expiries if e >= row.day), None)
        series = g[(g.strike == strike) & (g.ts >= row.entry_ts)]
        if dead is not None:
            series = series[series.ts.dt.date <= dead]
        series = series.sort_values("ts").head(MAX_HOLD + 1)
        if len(series) < 2:
            continue
        path = series.iloc[1:]

        rec = {
            "scan": row.scan,
            "symbol": row.symbol,
            "day": row.day,
            "entry_ts": row.entry_ts,
            "strike": strike,
            "spot": float(pick.spot),
            "premium": prem,
            "moneyness": (float(pick.spot) - strike) / float(pick.spot) * 100.0,
            "bars": len(path),
        }

        # Exit A -- hold to a fixed horizon.
        for label, bars in HORIZONS:
            if len(path) >= bars:
                rec["hold_" + label] = net_multiple(prem, float(path.iloc[bars - 1].close))
            else:
                rec["hold_" + label] = np.nan

        # Exit B -- 30% trail off the running peak, the best exit in the base study.
        peak = prem
        out = float(path.iloc[-1].close)
        for bar in path.itertuples():
            peak = max(peak, bar.high)
            if bar.close <= peak * (1.0 - TRAIL):
                out = float(bar.close)
                break
        rec["trail30"] = net_multiple(prem, out)
        rec["peak_mult"] = peak / (prem + TICK)
        recs.append(rec)
    return pd.DataFrame(recs)


# ---------------------------------------------------------------- report

def pct(x):
    return "n/a" if not np.isfinite(x) else "{:+.3f}%".format(x * 100)


def _out(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def main():
    trig = load_triggers()
    log("triggers loaded: {} rows, {} -> {}".format(
        len(trig), trig.signal_ts.min().date(), trig.signal_ts.max().date()))
    for scan, g in trig.groupby("scan"):
        log("  {:<10} {:4d} triggers  {:3d} symbols  {:2d} days".format(
            scan, len(g), g.symbol.nunique(), g.signal_ts.dt.date.nunique()))

    start = trig.entry_ts.min() - dt.timedelta(days=3)
    end = trig.entry_ts.max() + dt.timedelta(days=12)
    start_utc = start.tz_localize(IST).astimezone(dt.timezone.utc)
    end_utc = end.tz_localize(IST).astimezone(dt.timezone.utc)

    eq = load_equity(start_utc, end_utc)
    log("equity bars: {} over {} symbols".format(len(eq), eq.symbol.nunique()))

    log("\n" + "=" * 78)
    log("STAGE 1 -- does the trigger move the STOCK?")
    log("=" * 78)
    s1 = stage_one(trig, eq)
    log("{} of {} triggers priced (rest: symbol not cached, or no bar after signal)".format(
        len(s1), len(trig)))

    stage1_rows = []
    for scan, g in list(s1.groupby("scan")) + [("BOTH", s1)]:
        log("\n{}  n={}  days={}".format(scan, len(g), g.day.nunique()))
        log("  {:<6} {:>11} {:>11} {:>9} {:>8} {:>7}".format(
            "hz", "raw", "excess", "t(excess)", "win%", "n"))
        for label, _ in HORIZONS:
            raw = g[label].mean()
            exc = g[label + "_x"].mean()
            t, nd = day_clustered_t(g[label + "_x"], g["day"])
            win = (g[label + "_x"] > 0).mean() * 100
            n = g[label + "_x"].notna().sum()
            stage1_rows.append({
                "scan": scan, "horizon": label, "raw": raw, "excess": exc,
                "t": t, "win": win, "n": int(n), "days": g.day.nunique(),
            })
            log("  {:<6} {:>11} {:>11} {:>9} {:>7.1f}% {:>7d}".format(
                label, pct(raw), pct(exc), "n/a" if not np.isfinite(t) else "{:+.2f}".format(t),
                win, int(n)))
    pd.DataFrame(stage1_rows).to_csv(_out("chartink_ce_stage1.csv"), index=False)

    log("\n" + "=" * 78)
    log("STAGE 2 -- buy the ATM CALL on the same bar")
    log("=" * 78)
    opts = load_options(start_utc, end_utc, set(s1.symbol))
    log("option bars: {} rows, {} symbols".format(len(opts), opts.symbol.nunique() if len(opts) else 0))
    expiries = find_expiries(opts)
    log("front-month expiries detected in window: {}".format(
        ", ".join(str(e) for e in expiries) or "none"))
    s2 = stage_two(s1, opts, expiries)
    if s2.empty:
        log("no trigger could be matched to an option bar.")
        return
    log("{} of {} stock triggers had a call quoted at the entry bar".format(len(s2), len(s1)))
    log("median premium Rs{:.2f}   median |moneyness| {:.2f}%".format(
        s2.premium.median(), s2.moneyness.abs().median()))

    lots = dict(TrackedStock.objects.values_list("symbol", "lot_size"))
    s2["lot"] = s2.symbol.map(lots)

    stage2_rows = []
    for scan, g in list(s2.groupby("scan")) + [("BOTH", s2)]:
        log("\n{}  n={}  days={}".format(scan, len(g), g.day.nunique()))
        log("  {:<12} {:>9} {:>9} {:>8} {:>8} {:>7}".format(
            "exit", "mean x", "median x", "win%", "t(day)", "n"))
        cols = [("hold_" + l, l) for l, _ in HORIZONS] + [("trail30", "trail 30%")]
        for col, label in cols:
            v = g[col].dropna()
            if v.empty:
                continue
            t, _ = day_clustered_t(g[col] - 1.0, g["day"])
            stage2_rows.append({
                "scan": scan, "exit": label, "mean": v.mean(), "median": v.median(),
                "win": (v > 1).mean() * 100, "t": t, "n": len(v),
            })
            log("  {:<12} {:>9.3f} {:>9.3f} {:>7.1f}% {:>8} {:>7d}".format(
                label, v.mean(), v.median(), (v > 1).mean() * 100,
                "n/a" if not np.isfinite(t) else "{:+.2f}".format(t), len(v)))
    pd.DataFrame(stage2_rows).to_csv(_out("chartink_ce_stage2.csv"), index=False)

    # Robustness: the feed prints stale 5-paise quotes on thin contracts, and a
    # 5-paise entry manufactures a huge multiple out of one tick.  Nobody gets
    # filled there.  Re-run the same table on entries above a rupee.
    liquid = s2[s2.premium >= 1.0]
    log("\nSame table, entries >= Rs1 only ({} of {} trades kept)".format(len(liquid), len(s2)))
    log("  {:<12} {:>9} {:>9} {:>8} {:>8} {:>7}".format(
        "exit", "mean x", "median x", "win%", "t(day)", "n"))
    for col, label in [("hold_" + l, l) for l, _ in HORIZONS] + [("trail30", "trail 30%")]:
        v = liquid[col].dropna()
        if v.empty:
            continue
        t, _ = day_clustered_t(liquid[col] - 1.0, liquid["day"])
        log("  {:<12} {:>9.3f} {:>9.3f} {:>7.1f}% {:>8} {:>7d}".format(
            label, v.mean(), v.median(), (v > 1).mean() * 100,
            "n/a" if not np.isfinite(t) else "{:+.2f}".format(t), len(v)))

    log("\n" + "-" * 78)
    log("RUPEES -- what the CE workflow actually pays")
    log("-" * 78)
    sized = s2.dropna(subset=["lot"]).copy()
    sized["cost"] = (sized.premium + TICK) * sized.lot
    money_rows = []
    for col, label in [("hold_EOD", "exit same day"), ("hold_2d", "hold 2 sessions"),
                       ("trail30", "30% trail")]:
        v = sized.dropna(subset=[col])
        if v.empty:
            continue
        pnl = (v[col] - 1.0) * v.cost
        money_rows.append({
            "exit": label, "trades": len(v), "deployed": v.cost.sum(), "pnl": pnl.sum(),
            "pct": pnl.sum() / v.cost.sum() * 100, "mean": pnl.mean(),
            "median": pnl.median(), "worst": pnl.min(),
        })
        log("  {:<16} {:4d} trades  deployed Rs{:>12,.0f}  P&L Rs{:>+12,.0f}  ({:+.1f}%)".format(
            label, len(v), v.cost.sum(), pnl.sum(), pnl.sum() / v.cost.sum() * 100))
        log("  {:<16} per trade: mean Rs{:>+9,.0f}   median Rs{:>+9,.0f}   worst Rs{:>+9,.0f}".format(
            "", pnl.mean(), pnl.median(), pnl.min()))
    pd.DataFrame(money_rows).to_csv(_out("chartink_ce_money.csv"), index=False)

    # The hurdle: how far the stock must travel before the call pays for its own
    # decay, against how far the trigger actually takes it.
    liq = s2[s2.premium >= 1.0]
    med_prem, med_spot = liq.premium.median(), liq.spot.median()
    hurdle_rows = []
    by_exit = {r["exit"]: r["mean"] for r in stage2_rows if r["scan"] == "BOTH"}
    for label, _ in HORIZONS:
        mult = by_exit.get(label)
        if mult is None:
            continue
        need = (1.0 - mult) * med_prem / 0.5 / med_spot   # delta ~0.5 at the money
        moves = s1[label].dropna()
        hurdle_rows.append({
            "horizon": label, "need_pct": need * 100,
            "median_delivered_pct": moves.median() * 100,
            "cleared_pct": (moves > need).mean() * 100, "n": len(moves),
        })
    pd.DataFrame(hurdle_rows).to_csv(_out("chartink_ce_hurdle.csv"), index=False)
    log("\nBreak-even hurdle (median Rs{:.2f} premium on a Rs{:.0f} stock):".format(
        med_prem, med_spot))
    for row in hurdle_rows:
        log("  {:<5} need {:+.2f}%   delivered {:+.2f}%   cleared by {:.1f}%".format(
            row["horizon"], row["need_pct"], row["median_delivered_pct"], row["cleared_pct"]))

    s2.to_csv(_out("chartink_options_trades.csv"), index=False)
    log("\ntrades + 4 summary CSVs written to research/")


if __name__ == "__main__":
    main()
