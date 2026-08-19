"""Defined-risk call spreads: the one lead the buy-side study left open.

Every buy-side result in this project says the same thing -- a long at-the-money
call returns about 0.76x over two sessions, measured twice on independent
samples.  That number is an argument for being on the OTHER side of it, and the
only responsible way to take that side is with the risk defined.  A naked short
call is unbounded and is not on the table.

So: the VERTICAL CALL SPREAD, built from the two strikes the cache actually has
at the same instant.

  SHORT spread (credit, bearish/neutral):  sell the ATM call, buy the ATM+1.
      Collect a credit.  Max loss = strike gap - credit.  Capped, both ends.
  LONG spread (debit, bullish):  the exact mirror, paying the costs again.

Three traps from the rest of the study apply here and all three are guarded:

  Re-centring.  `relative_strike` is ATM-RELATIVE and moves with spot, so the
  labels only pick the strikes AT ENTRY.  Both strikes are then pinned and
  followed as fixed contracts, which is what a real order does.

  Rollover.  `expiry_code=1` is front month with no expiry DATE stored, so a
  fixed strike followed across a rollover silently becomes next month's
  contract.  Holds are capped at the expiry detected from the data.

  Costs.  A spread crosses FOUR tick boundaries, not two -- both legs in and
  both legs out.  That is 20 paise a round trip before tax, and on a credit of
  a few rupees it is the whole argument.

And one trap that is specific to spreads.  A stale five-paise quote on the LONG
leg fabricates a credit out of nothing: we would be booking protection we never
actually bought.  Every headline below is therefore reported twice, raw and
filtered, so the reader can see how much of the result survives insisting that
both legs actually traded.

And one that is specific to imputing.  A split or demerger RE-BASES `spot` but
not the recorded `strike`, so `max(spot - strike, 0)` straddles two price scales
and returns nonsense -- and it lands almost entirely on imputed rows, because the
corporate action is precisely what stops the old strike being quoted.  1.0% of
rows carried 69% of the loss before this was caught.  See `MAX_DRIFT`.
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

from options_tracker.models import StockOptionCandle, TrackedStock  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TICK = 0.05      # crossed on EVERY leg, EVERY way -- four crossings a round trip
TAX = 0.0028     # round-trip turnover charge
BARS_PER_SESSION = 25
HORIZONS = [("2h", 8), ("EOD", 25), ("2d", 50), ("5d", 125)]

MIN_LONG = 0.50   # a long leg cheaper than this is a stale quote, not protection
MIN_RISK = 1.00   # below this the return-on-risk denominator is meaningless

# A SPLIT OR DEMERGER RE-BASES `spot` BUT NOT THE RECORDED `strike`, so intrinsic
# gets computed across two different price scales and returns garbage -- and it
# bites only imputed rows, because the corporate action is exactly what makes the
# old strike stop being quoted. The fingerprint: a pinned ATM strike sits on spot
# at entry (median distance 0.00) and 73% away from it at exit, which no five
# sessions of price movement can do. NESTLEIND 0.490, SIEMENS 0.532, LTM 0.530 --
# split and demerger ratios, not price moves.
#
# 1.0% of rows carried 69% of the total loss before this was caught. The cut is
# taken on the STRIKE'S DISTANCE FROM SPOT, never on how far spot moved: spot
# movement is the outcome, and filtering on it removes adverse results and
# manufactures a positive. Rows the exchange still quoted -- provably real
# contracts -- reach 0.025 at the 99.9th percentile, so 0.30 is loose by an order
# of magnitude. Results plateau from 0.30 to 0.15, which is what a clean filter
# looks like: once the actions are gone there is nothing left to remove.
MAX_DRIFT = 0.30


def drop_rebased_strikes(spreads, strike_col="k1", labels=None):
    """Blank the horizons whose exit spot is on a different price scale.

    Per horizon, not per row: a 2-hour exit can be clean on a day whose 5-day
    exit lands the far side of a split.

    `labels` defaults to HORIZONS but takes any (name, _) list, so studies with
    their own exit set -- spread_near_expiry's expiry-day clocks, say -- get the
    same guard instead of quietly skipping it.
    """
    spreads = spreads.copy()
    missing = 0
    for label, _ in (HORIZONS if labels is None else labels):
        spot_out = "spot_" + label
        if spot_out not in spreads.columns:
            missing += 1
            continue
        drift = (spreads[spot_out] - spreads[strike_col]).abs() / spreads[spot_out]
        bad = drift > MAX_DRIFT
        for col in (label, "i_" + label, "gross_" + label):
            if col in spreads.columns:
                spreads.loc[bad, col] = np.nan
    if missing:
        # Silence here would look exactly like a clean sample, which is the one
        # failure mode this guard exists to prevent.
        raise KeyError(
            "drop_rebased_strikes: {} exit label(s) have no spot_<label> column,"
            " so the corporate-action guard would be a no-op".format(missing))
    return spreads



def log(msg):
    print(msg, flush=True)


def load_calls():
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15, option_type="CALL", relative_strike__in=["ATM", "ATM+1"]
    ).values_list("symbol", "timestamp", "strike", "spot", "relative_strike",
                  "open", "close", "volume")
    frame = pd.DataFrame(
        list(rows),
        columns=["symbol", "ts", "strike", "spot", "rel", "open", "close", "volume"],
    )
    frame["ts"] = pd.to_datetime(frame.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for col in ("strike", "spot", "open", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.drop_duplicates(subset=["symbol", "ts", "strike"])


def find_expiries(frame):
    """Front-month death days, read off the data (see chartink_options.py)."""
    near = frame[(frame.spot > 0) & ((frame.strike - frame.spot).abs() / frame.spot < 0.01)]
    ratio = near.groupby(near.ts.dt.date).apply(
        lambda x: (x.close / x.spot).median(), include_groups=False
    ).sort_index()
    days = list(ratio.index)
    return sorted(
        days[i] for i in range(len(days) - 1)
        if ratio.iloc[i] > 0 and ratio.iloc[i + 1] / ratio.iloc[i] > 2.5
    )


def run_symbol(frame, expiries):
    """Every bar where both legs are quoted becomes one spread entry."""
    wide_open = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = wide_open.index
    # Reindexed onto the same grid: a pivot drops all-NaN rows, and a mismatched
    # index would turn a missing quote into a KeyError instead of a NaN.
    wide_close = frame.pivot_table(
        index="ts", columns="strike", values="close").reindex(grid)
    wide_vol = frame.pivot_table(
        index="ts", columns="strike", values="volume").reindex(grid)
    wide_spot = frame.pivot_table(
        index="ts", columns="strike", values="spot").reindex(grid).mean(axis=1)

    legs = frame.pivot_table(index="ts", columns="rel", values="strike")
    if "ATM" not in legs.columns or "ATM+1" not in legs.columns:
        return []
    legs = legs.dropna()
    if legs.empty:
        return []

    exp_by_day = {
        day: next((e for e in expiries if e >= day), None)
        for day in {t.date() for t in legs.index}
    }
    pos = {t: i for i, t in enumerate(grid)}
    out = []
    for ts, leg in legs.iterrows():
        k1, k2 = float(leg["ATM"]), float(leg["ATM+1"])
        if k2 <= k1:
            continue
        gap = k2 - k1
        try:
            short_in, long_in = wide_open.at[ts, k1], wide_open.at[ts, k2]
            v_short, v_long = wide_vol.at[ts, k1], wide_vol.at[ts, k2]
        except KeyError:
            continue
        if not (np.isfinite(short_in) and np.isfinite(long_in)):
            continue
        credit = (short_in - TICK) - (long_in + TICK)
        if credit <= 0 or credit >= gap:
            continue

        dead = exp_by_day.get(ts.date())
        i = pos[ts]
        rec = {
            "ts": ts, "day": ts.date(), "k1": k1, "k2": k2, "gap": gap,
            "credit": credit, "risk": gap - credit,
            "spot": wide_spot.get(ts, np.nan), "long_in": long_in,
            "v_short": v_short if np.isfinite(v_short) else 0,
            "v_long": v_long if np.isfinite(v_long) else 0,
            "dte": (dead - ts.date()).days if dead else np.nan,
        }
        for label, bars in HORIZONS:
            j = i + bars
            rec["days_" + label] = np.nan
            rec[label] = rec["i_" + label] = np.nan
            if j >= len(grid):
                continue
            out_ts = grid[j]
            if dead is not None and out_ts.date() > dead:
                continue
            rec["days_" + label] = (out_ts.date() - ts.date()).days
            rec["spot_" + label] = spot_out = wide_spot.get(out_ts, np.nan)
            s_out, l_out = wide_close.at[out_ts, k1], wide_close.at[out_ts, k2]
            quoted = np.isfinite(s_out) and np.isfinite(l_out)
            if not quoted:
                # A pinned strike stops being quoted once spot walks away from
                # it, so these exits go missing in the losing direction. Value
                # what is missing at intrinsic rather than dropping the trade.
                if not np.isfinite(spot_out):
                    continue
                if not np.isfinite(s_out):
                    s_out = max(spot_out - k1, 0.0)
                if not np.isfinite(l_out):
                    l_out = max(spot_out - k2, 0.0)
            debit = min(max((s_out + TICK) - (l_out - TICK), 0.0), gap)
            turnover = (short_in + long_in + s_out + l_out) * TAX
            rec["i_" + label] = credit - debit - turnover
            if quoted:
                rec[label] = rec["i_" + label]
                # The same trade with no tick and no tax, so the two can be told apart.
                rec["gross_" + label] = (short_in - long_in) - min(
                    max(s_out - l_out, 0.0), gap)
        out.append(rec)
    return out


def day_t(values, days):
    """Overlapping entries on one morning are one bet wearing many hats."""
    daily = pd.DataFrame({"v": values, "d": days}).groupby("d")["v"].mean()
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return np.nan
    return daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))


def table(spreads, title, note="", prefix=""):
    log("\n" + "=" * 92)
    log(title)
    if note:
        log(note)
    log("=" * 92)
    log("  {:<5} {:>10} {:>10} {:>8} {:>9} {:>9} {:>8} {:>8}".format(
        "exit", "P&L/sprd", "ROI/risk", "win%", "p10 ROI", "t(day)", "cal.days", "n"))
    rows = []
    for label, _ in HORIZONS:
        col = prefix + label
        v = spreads.dropna(subset=[col])
        if v.empty:
            continue
        # Aggregate, not a mean of ratios: one tiny-risk trade must not dominate.
        roi = v[col].sum() / v.risk.sum() * 100
        p10 = (v[col] / v.risk).quantile(0.10) * 100
        t = day_t(v[col].values, v.day.values)
        rows.append({"exit": label, "prefix": prefix or "quoted", "pnl": v[col].mean(),
                     "roi": roi, "win": (v[col] > 0).mean() * 100, "p10": p10, "t": t,
                     "days": v["days_" + label].median(), "n": len(v)})
        log("  {:<5} {:>+10.3f} {:>9.2f}% {:>7.1f}% {:>8.1f}% {:>9} {:>8.0f} {:>8d}".format(
            label, v[col].mean(), roi, (v[col] > 0).mean() * 100, p10,
            "n/a" if not np.isfinite(t) else "{:+.2f}".format(t),
            rows[-1]["days"], len(v)))
    return rows


def main():
    frame = load_calls()
    log("loaded {:,} ATM/ATM+1 call bars, {} symbols".format(len(frame), frame.symbol.nunique()))
    expiries = find_expiries(frame)
    log("{} expiries detected, {} -> {}".format(len(expiries), expiries[0], expiries[-1]))

    all_rows = []
    symbols = sorted(frame.symbol.unique())
    for n, symbol in enumerate(symbols, 1):
        rows = run_symbol(frame[frame.symbol == symbol], expiries)
        for row in rows:
            row["symbol"] = symbol
        all_rows.extend(rows)
        if n % 50 == 0:
            log("  {}/{} symbols, {:,} spreads".format(n, len(symbols), len(all_rows)))
    spreads = pd.DataFrame(all_rows)
    if spreads.empty:
        log("no spreads could be built.")
        return

    lots = dict(TrackedStock.objects.values_list("symbol", "lot_size"))
    spreads["lot"] = spreads.symbol.map(lots)
    spreads["month"] = pd.to_datetime(spreads.ts).dt.to_period("M").astype(str)

    log("\n{:,} raw spread entries, {} symbols, {} sessions, {} -> {}".format(
        len(spreads), spreads.symbol.nunique(), spreads.day.nunique(),
        spreads.day.min(), spreads.day.max()))
    log("median gap Rs{:.1f}, credit Rs{:.2f} ({:.0f}% of gap), risk Rs{:.2f}, "
        "spot Rs{:,.0f}".format(
            spreads.gap.median(), spreads.credit.median(),
            (spreads.credit / spreads.gap).median() * 100,
            spreads.risk.median(), spreads.spot.median()))

    table(spreads, "SHORT CALL SPREAD -- RAW, no quality filter",
          "  Includes entries whose long leg may be a stale quote. Not the headline.")

    # -- the filter that matters ------------------------------------------
    clean = spreads[
        (spreads.long_in >= MIN_LONG) & (spreads.risk >= MIN_RISK)
        & (spreads.v_short > 0) & (spreads.v_long > 0)
    ].copy()
    log("\nquality filter: long leg >= Rs{:.2f}, risk >= Rs{:.2f}, both legs traded"
        " -> {:,} of {:,} entries survive ({:.0f}%)".format(
            MIN_LONG, MIN_RISK, len(clean), len(spreads), 100 * len(clean) / len(spreads)))

    before = clean["i_5d"].notna().sum()
    clean = drop_rebased_strikes(clean, "k1")
    log("corporate actions: {:,} of {:,} 5-day exits blanked ({:.2f}%) -- a split"
        " re-bases spot but not the recorded strike, so intrinsic straddles two"
        " price scales.".format(
            before - clean["i_5d"].notna().sum(), before,
            100 * (before - clean["i_5d"].notna().sum()) / max(before, 1)))

    summary = table(clean, "SHORT CALL SPREAD -- both legs actually traded",
                    "  Sell ATM call, buy ATM+1 call. NOT the headline: see below.")

    # -- the filter hiding inside that table -------------------------------
    log("\n" + "-" * 92)
    log("SURVIVORSHIP -- why the table above cannot be read at face value.")
    log("`relative_strike` is ATM-RELATIVE, so a strike pinned at entry is only quoted")
    log("while spot stays near it. An exit that fails to price is missing BECAUSE the")
    log("market ran away from the strike -- and for a SHORT call spread, spot running up")
    log("is the losing direction. Below, how much of the sample that quietly removes:")
    log("-" * 92)
    log("  {:<6} {:>10} {:>12} {:>12}".format("exit", "quoted%", "n quoted", "n imputed"))
    for label, _ in HORIZONS:
        q, i = clean[label].notna().sum(), clean["i_" + label].notna().sum()
        log("  {:<6} {:>9.1f}% {:>12,d} {:>12,d}".format(
            label, 100 * q / max(i, 1), q, i))
    log("\n  The repair: at expiry an option IS its intrinsic, so a vanished leg is worth")
    log("  max(spot - strike, 0) with no quote at all. Checked against the strikes that")
    log("  WERE still quoted at the bell, that rule lands within one tick (median")
    log("  Rs+0.05) -- see bell_intrinsic_check.py.")
    imputed = table(clean, "SHORT CALL SPREAD -- MISSING LEGS IMPUTED AT INTRINSIC",
                    "  This is the headline. Same trades, none of them dropped.",
                    prefix="i_")

    # -- is it theta, or is it just a short bet in a flat market? ----------
    log("\n" + "-" * 92)
    log("BY MONTH, at 5 days -- the horizon where the pooled number turns positive.")
    log("A short call spread is bearish; if the P&L just tracks the market falling,")
    log("it is a direction bet, not an edge.")
    log("-" * 92)
    log("  {:<9} {:>10} {:>11} {:>9} {:>8}".format("month", "ROI/risk", "spot move", "win%", "n"))
    by_month = []
    for month, g in clean.groupby("month"):
        v = g.dropna(subset=["i_5d"])
        if len(v) < 100:
            continue
        move = ((v["spot_5d"] / v.spot - 1) * 100).median()
        roi = v["i_5d"].sum() / v.risk.sum() * 100
        by_month.append({"month": month, "roi": roi, "move": move,
                         "win": (v["i_5d"] > 0).mean() * 100, "n": len(v)})
        log("  {:<9} {:>9.2f}% {:>+10.2f}% {:>8.1f}% {:>8d}".format(
            month, roi, move, (v["i_5d"] > 0).mean() * 100, len(v)))
    months = pd.DataFrame(by_month)
    if len(months) > 2:
        log("\n  months profitable: {}/{}    correlation(ROI, spot move) = {:+.2f}".format(
            int((months.roi > 0).sum()), len(months), months.roi.corr(months.move)))

    # -- how much of the loss is the broker's, and how much is the trade's? --
    log("\n" + "-" * 92)
    log("WHERE THE MONEY GOES -- gross is the same trade with no tick and no tax.")
    log("If gross is positive and net is not, the structure works and the costs eat it.")
    log("-" * 92)
    log("  {:<5} {:>11} {:>11} {:>11} {:>9}".format(
        "exit", "gross ROI", "net ROI", "cost drag", "n"))
    for label, _ in HORIZONS:
        v = clean.dropna(subset=[label, "gross_" + label])
        if v.empty:
            continue
        gross = v["gross_" + label].sum() / v.risk.sum() * 100
        net = v[label].sum() / v.risk.sum() * 100
        log("  {:<5} {:>+10.2f}% {:>+10.2f}% {:>+10.2f}% {:>9,d}".format(
            label, gross, net, net - gross, len(v)))

    # -- theta should pay more the closer expiry gets -----------------------
    log("\n" + "-" * 92)
    log("BY DAYS TO EXPIRY AT ENTRY, held 5 days -- theta accelerates into expiry,")
    log("so a real short-premium edge should get STRONGER as the contract dies.")
    log("This was the finding that sent the study near-expiry; note what the two")
    log("columns do to it once the dropped exits are put back.")
    log("-" * 92)
    log("  {:<12} {:>11} {:>11} {:>9} {:>9} {:>10}".format(
        "dte", "quoted ROI", "imputed", "quoted n", "imp. n", "quoted%"))
    buckets = [(0, 7, "0-7 days"), (8, 14, "8-14 days"),
               (15, 21, "15-21 days"), (22, 60, "22+ days")]
    for lo, hi, name in buckets:
        b = clean[(clean.dte >= lo) & (clean.dte <= hi)]
        v, w = b.dropna(subset=["5d"]), b.dropna(subset=["i_5d"])
        if len(w) < 100:
            continue
        log("  {:<12} {:>+10.2f}% {:>+10.2f}% {:>9,d} {:>9,d} {:>9.1f}%".format(
            name, v["5d"].sum() / v.risk.sum() * 100 if len(v) else np.nan,
            w["i_5d"].sum() / w.risk.sum() * 100, len(v), len(w),
            100 * len(v) / len(w)))

    # -- one entry per symbol-week: no overlapping holds -------------------
    log("\n" + "-" * 92)
    log("NON-OVERLAPPING -- one entry per symbol per week, so no two holds share a")
    log("path. Repeated at four entry times, because 'the first bar of the week' is")
    log("always Monday's open and that is its own bias.")
    log("-" * 92)
    log("  {:<10} {:>11} {:>11} {:>9} {:>9} {:>8}".format(
        "entry", "2d ROI", "5d ROI", "5d win%", "5d t(day)", "n"))
    weekly = clean.copy()
    weekly["week"] = pd.to_datetime(weekly.ts).dt.to_period("W").astype(str)
    weekly["clock"] = pd.to_datetime(weekly.ts).dt.strftime("%H:%M")
    for when in ["09:45", "11:15", "13:00", "14:30"]:
        pick = weekly[weekly.clock == when].sort_values("ts").groupby(
            ["symbol", "week"], as_index=False).head(1)
        v = pick.dropna(subset=["i_5d"])
        if len(v) < 50:
            continue
        two = pick.dropna(subset=["i_2d"])
        t = day_t(v["i_5d"].values, v.day.values)
        log("  {:<10} {:>+10.2f}% {:>+10.2f}% {:>8.1f}% {:>9} {:>8,d}".format(
            when, two["i_2d"].sum() / two.risk.sum() * 100,
            v["i_5d"].sum() / v.risk.sum() * 100, (v["i_5d"] > 0).mean() * 100,
            "n/a" if not np.isfinite(t) else "{:+.2f}".format(t), len(v)))

    # -- rupees ------------------------------------------------------------
    log("\n" + "-" * 92)
    log("RUPEES -- at real lot sizes. Note this weights by lot, so cheap high-lot")
    log("names dominate; the per-spread table above weights every trade equally.")
    log("-" * 92)
    sized = clean.dropna(subset=["lot"])
    money = []
    for label, _ in HORIZONS:
        v = sized.dropna(subset=["i_" + label])
        if v.empty:
            continue
        pnl, risk = v["i_" + label] * v.lot, v.risk * v.lot
        money.append({"exit": label, "trades": len(v), "risk": risk.sum(), "pnl": pnl.sum(),
                      "pct": pnl.sum() / risk.sum() * 100, "mean": pnl.mean(),
                      "worst": pnl.min()})
        log("  {:<5} {:6d} spreads   risk Rs{:>13,.0f}   P&L Rs{:>+12,.0f} ({:+.2f}%)   "
            "worst Rs{:>+9,.0f}".format(
                label, len(v), risk.sum(), pnl.sum(), pnl.sum() / risk.sum() * 100, pnl.min()))

    log("\nLONG call spread (buy ATM, sell ATM+1) -- the mirror, paying costs again:")
    for label, _ in HORIZONS:
        v = clean.dropna(subset=["i_" + label])
        if v.empty:
            continue
        mirror = -v["i_" + label] - (TICK * 4)
        log("  {:<5} P&L/spread {:>+8.3f}   win {:>5.1f}%   n={:,}".format(
            label, mirror.mean(), (mirror > 0).mean() * 100, len(v)))

    here = os.path.dirname(os.path.abspath(__file__))
    pd.DataFrame(summary + imputed).to_csv(
        os.path.join(here, "spread_summary.csv"), index=False)
    pd.DataFrame(money).to_csv(os.path.join(here, "spread_money.csv"), index=False)
    months.to_csv(os.path.join(here, "spread_months.csv"), index=False)
    clean.to_csv(os.path.join(here, "spread_trades.csv"), index=False)
    log("\nwritten: spread_summary.csv, spread_money.csv, spread_months.csv, spread_trades.csv")


if __name__ == "__main__":
    main()
