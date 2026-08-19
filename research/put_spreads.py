"""The other credit spread, and the test that separates premium from direction.

`SPREAD_REPORT.md` closed with "bull put spreads remain untestable on this cache:
about 2,070 PUT bars against 1.76 million CALL bars."  That was wrong.  1,965 PUT
bars is what sits inside the Sep-2025..May-2026 CALENDAR HOLE in the put
download; the cache as a whole holds 592,658 ATM and 306,379 ATM+1 put bars, and
309,451 SAME-INSTANT pairs across 113 symbols.  A count taken inside a gap was
generalised to the whole file.

So the mirror is testable, and it matters, because the short call spread's
verdict rested on a direction argument.  It lost 19-28% of risk, and its monthly
P&L correlated -0.73 with the spot move -- i.e. it lost because Indian stocks
rose over the sample, not obviously because selling premium is a bad trade.  That
leaves one question genuinely open: IS THERE PREMIUM TO SELL AT ALL, once you are
not making a direction bet?

  BULL PUT SPREAD (credit, bullish):  sell the ATM+1 put (the higher strike),
      buy the ATM put (the lower one).  Max loss = strike gap - credit.
      This is the short call spread's mirror: opposite delta, same theta sign.

Run alone it will look good, and that will mean nothing -- it is long delta over
a rising sample, the same confound read backwards.  The test that does mean
something is the PAIRED one at the bottom: take only the instants where BOTH
spreads could be opened on the same symbol, and hold them together.  The deltas
roughly cancel; what is left is the premium.  If the combination still loses,
selling defined-risk premium on stock options loses regardless of direction, and
the entire family is closed -- not just the bearish half of it.

Everything side-dependent is flipped, and there are exactly three such places:
which leg is short, the intrinsic formula (a put is worth max(strike - spot, 0)),
and which way spot has to run for the exit quote to vanish.  The survivorship
repair from the call study is kept in full -- a missing exit is valued at
intrinsic, never dropped -- because it is the same feed and the same trap, only
pointing the other way.  So is `drop_rebased_strikes`: splits break the put
imputation exactly as badly, and 1% of rows carried most of the loss until it was
applied.

A WARNING ABOUT THE PAIRED TEST, because the first version of it was wrong.  Short
C(k_low)/long C(k_high) plus short P(k_high)/long P(k_low) is short a synthetic
forward at k_low and long one at k_high -- a BOX SPREAD.  Put-call parity makes it
worth exactly the strike gap at every instant, with no delta, no theta and no
vega, and the data agrees: correlation with the spot move is -0.010.  It is
therefore not a delta-neutral premium seller and cannot answer "is there premium
to sell".  What it does measure, cleanly and with no model, is round-trip
friction, since the structure has a known arbitrage-free value.  A real
delta-neutral premium test needs four strikes (an iron condor); this cache has
two, so it cannot be built here at all.
"""

import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from option_spreads import (  # noqa: E402  -- runs django.setup() on import
    HORIZONS, IST, MIN_LONG, MIN_RISK, TAX, TICK,
    day_t, drop_rebased_strikes, find_expiries, log, table,
)

from options_tracker.models import StockOptionCandle, TrackedStock  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load(option_type):
    rows = StockOptionCandle.objects.filter(
        interval_minutes=15, option_type=option_type,
        relative_strike__in=["ATM", "ATM+1"],
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


def run_symbol(frame, expiries):
    """Bull put spreads: short the ATM+1 put, long the ATM put.

    Mirrors `option_spreads.run_symbol` line for line except at the three points
    where the side matters -- which strike is sold, intrinsic being
    max(strike - spot, 0), and therefore which direction deletes an exit quote.
    """
    wide_open = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = wide_open.index
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
        k_low, k_high = float(leg["ATM"]), float(leg["ATM+1"])
        if k_high <= k_low:
            continue
        gap = k_high - k_low
        try:
            # SHORT the higher strike, LONG the lower one -- the flip.
            short_in, long_in = wide_open.at[ts, k_high], wide_open.at[ts, k_low]
            v_short, v_long = wide_vol.at[ts, k_high], wide_vol.at[ts, k_low]
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
            "ts": ts, "day": ts.date(), "k_low": k_low, "k_high": k_high, "gap": gap,
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
            s_out, l_out = wide_close.at[out_ts, k_high], wide_close.at[out_ts, k_low]
            quoted = np.isfinite(s_out) and np.isfinite(l_out)
            if not quoted:
                # Same trap, opposite direction: for a bull put spread the exit
                # goes missing when spot falls away from the strikes, which is
                # the LOSING side here. A put is worth max(strike - spot, 0).
                if not np.isfinite(spot_out):
                    continue
                if not np.isfinite(s_out):
                    s_out = max(k_high - spot_out, 0.0)
                if not np.isfinite(l_out):
                    l_out = max(k_low - spot_out, 0.0)
            debit = min(max((s_out + TICK) - (l_out - TICK), 0.0), gap)
            turnover = (short_in + long_in + s_out + l_out) * TAX
            rec["i_" + label] = credit - debit - turnover
            if quoted:
                rec[label] = rec["i_" + label]
        out.append(rec)
    return out


def build(frame, expiries, runner):
    all_rows = []
    for symbol in sorted(frame.symbol.unique()):
        rows = runner(frame[frame.symbol == symbol], expiries)
        for row in rows:
            row["symbol"] = symbol
        all_rows.extend(rows)
    return pd.DataFrame(all_rows)


def clean_up(spreads, strike_col="k_low"):
    kept = spreads[
        (spreads.long_in >= MIN_LONG) & (spreads.risk >= MIN_RISK)
        & (spreads.v_short > 0) & (spreads.v_long > 0)
    ].copy()
    return drop_rebased_strikes(kept, strike_col)


def main():
    puts = load("PUT")
    log("loaded {:,} ATM/ATM+1 PUT bars, {} symbols, {} -> {}".format(
        len(puts), puts.symbol.nunique(), puts.ts.min().date(), puts.ts.max().date()))

    # Expiries are read off the CALL series: it is the denser and continuous one,
    # and expiry dates are a property of the contract, not of the side.
    calls = load("CALL")
    expiries = find_expiries(calls)
    log("{} expiries detected from the call series, {} -> {}".format(
        len(expiries), expiries[0], expiries[-1]))

    spreads = build(puts, expiries, run_symbol)
    if spreads.empty:
        log("no put spreads could be built.")
        return
    spreads["month"] = pd.to_datetime(spreads.ts).dt.to_period("M").astype(str)
    log("\n{:,} raw bull put spreads, {} symbols, {} sessions, {} -> {}".format(
        len(spreads), spreads.symbol.nunique(), spreads.day.nunique(),
        spreads.day.min(), spreads.day.max()))
    log("median gap Rs{:.1f}, credit Rs{:.2f} ({:.0f}% of gap), risk Rs{:.2f}".format(
        spreads.gap.median(), spreads.credit.median(),
        (spreads.credit / spreads.gap).median() * 100, spreads.risk.median()))

    clean = clean_up(spreads)
    log("\nquality filter -> {:,} of {:,} survive ({:.0f}%)".format(
        len(clean), len(spreads), 100 * len(clean) / len(spreads)))

    log("\n  {:<6} {:>10} {:>12} {:>12}".format("exit", "quoted%", "n quoted", "n imputed"))
    for label, _ in HORIZONS:
        q, i = clean[label].notna().sum(), clean["i_" + label].notna().sum()
        log("  {:<6} {:>9.1f}% {:>12,d} {:>12,d}".format(label, 100 * q / max(i, 1), q, i))

    table(clean, "BULL PUT SPREAD -- quoted exits only",
          "  Sell ATM+1 put, buy ATM put. Survivorship-bitten, shown for comparison.")
    summary = table(clean, "BULL PUT SPREAD -- missing legs imputed at intrinsic",
                    "  The headline. Long delta over a rising sample, so a LOSS here"
                    " cannot be blamed on direction.", prefix="i_")

    # -- the confound, stated rather than hidden ---------------------------
    log("\n" + "-" * 92)
    log("BY MONTH at 5 days. A bull put spread is LONG delta. If P&L tracks the")
    log("market rising, it is the same direction bet the call spread was, reversed.")
    log("-" * 92)
    log("  {:<9} {:>10} {:>11} {:>9} {:>8}".format("month", "ROI/risk", "spot move", "win%", "n"))
    months = []
    for month, g in clean.groupby("month"):
        v = g.dropna(subset=["i_5d"])
        if len(v) < 100:
            continue
        move = ((v["spot_5d"] / v.spot - 1) * 100).median()
        roi = v["i_5d"].sum() / v.risk.sum() * 100
        months.append({"month": month, "roi": roi, "move": move, "n": len(v)})
        log("  {:<9} {:>9.2f}% {:>+10.2f}% {:>8.1f}% {:>8d}".format(
            month, roi, move, (v["i_5d"] > 0).mean() * 100, len(v)))
    if len(months) >= 3:
        frame = pd.DataFrame(months)
        log("\n  correlation(monthly ROI, monthly spot move) = {:+.2f}".format(
            frame.roi.corr(frame.move)))

    # -- the test that is not a direction bet ------------------------------
    log("\n" + "=" * 92)
    log("BOX CHECK -- both spreads on the same symbol at the same instant.")
    log("This is NOT a delta-neutral premium test, though it looks like one. Short")
    log("C(k_low)/long C(k_high) plus short P(k_high)/long P(k_low) is short a forward")
    log("at k_low and long one at k_high: a BOX, worth exactly the gap at every instant")
    log("by put-call parity, with no delta, theta or vega. Its value is knowing the")
    log("fair price with no model -- so what it measures is pure round-trip friction.")
    log("=" * 92)
    import option_spreads

    call_spreads = build(calls, expiries, option_spreads.run_symbol)
    call_clean = clean_up(call_spreads, "k1")
    call_clean["month"] = pd.to_datetime(call_clean.ts).dt.to_period("M").astype(str)
    log("call side: {:,} clean spreads; put side: {:,}".format(len(call_clean), len(clean)))

    keys = ["symbol", "ts"]
    both = call_clean.merge(clean, on=keys, suffixes=("_c", "_p"))
    both = both[both.gap_c == both.gap_p]
    log("both openable at the same instant, same gap: {:,} paired entries, {} symbols,"
        " {} -> {}".format(len(both), both.symbol.nunique(),
                           both.day_c.min(), both.day_c.max()))
    if both.empty:
        log("no overlap -- the two downloads do not share a calendar.")
        return

    short = both.gap_c - (both.credit_c + both.credit_p)
    log("\n  fair value of the box = the gap, Rs{:.2f} median. Credits sum to Rs{:.2f}.".format(
        both.gap_c.median(), (both.credit_c + both.credit_p).median()))
    log("  entry shortfall vs arbitrage-free: median Rs{:+.2f} ({:.2f}% of the gap).".format(
        short.median(), (short / both.gap_c).median() * 100))

    log("\n  {:<6} {:>11} {:>11} {:>12} {:>10} {:>8} {:>9}".format(
        "exit", "call ROI", "put ROI", "BOX", "win%", "t(day)", "n"))
    paired_rows = []
    for label, _ in HORIZONS:
        cc, pc = "i_" + label + "_c", "i_" + label + "_p"
        v = both.dropna(subset=[cc, pc])
        if len(v) < 50:
            continue
        pnl = v[cc] + v[pc]
        risk = v.risk_c + v.risk_p
        roi = pnl.sum() / risk.sum() * 100
        row = {
            "exit": label,
            "call": v[cc].sum() / v.risk_c.sum() * 100,
            "put": v[pc].sum() / v.risk_p.sum() * 100,
            "combined": roi, "win": (pnl > 0).mean() * 100,
            "t": day_t(pnl.values, v.day_c.values), "n": len(v),
            "spot_corr": pnl.corr((v["spot_" + label + "_c"] / v.spot_c - 1) * 100),
        }
        paired_rows.append(row)
        log("  {:<6} {:>10.2f}% {:>10.2f}% {:>11.2f}% {:>9.1f}% {:>8} {:>9,d}".format(
            label, row["call"], row["put"], roi, row["win"],
            "n/a" if not np.isfinite(row["t"]) else "{:+.2f}".format(row["t"]), len(v)))

    log("\n  corr(box P&L, spot move): {}".format(", ".join(
        "{} {:+.3f}".format(r["exit"], r["spot_corr"]) for r in paired_rows)))
    log("  Near zero at every horizon, which is the box confirming itself. The BOX")
    log("  column is therefore a friction measurement, not evidence about premium.")
    log("  The two verdicts that DO stand are the call and put columns, separately:")
    log("  opposite direction bets, both losing, which no direction story explains.")

    pd.DataFrame(summary).to_csv(os.path.join(HERE, "putspread_summary.csv"), index=False)
    pd.DataFrame(months).to_csv(os.path.join(HERE, "putspread_months.csv"), index=False)
    pd.DataFrame(paired_rows).to_csv(os.path.join(HERE, "putspread_paired.csv"), index=False)
    log("\nwrote putspread_summary.csv, putspread_months.csv, putspread_paired.csv")


if __name__ == "__main__":
    main()
