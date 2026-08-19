"""The near-expiry short call spread, put under the hardest test I can build.

The horizon sweep turned up one clean gradient, and it is the gradient option
theory predicts rather than one I went looking for.  A short ATM/ATM+1 call
spread held five days returns, by days-to-expiry at entry:

    0-7 days   +33.28% of risk, 82.7% win, n=2,045
    8-14 days  +11.50%          67.1%      n=12,853
    15-21 days  -0.44%          40.2%      n=12,897
    22+ days    -4.34%          29.4%      n=15,317

Monotonic, in the right direction, with the cost drag flat at ~4% throughout.
That is what accelerating theta looks like.  It is also what a subtle bug looks
like, and this file went looking for the bug.  It found three.

  1. `groupby.first()` is COLUMN-WISE, not first-row: it takes the first
     non-null value of each column independently.  Every "one trade per
     symbol-expiry" row was a composite of different bars, entry legs from one
     and exits from another.
  2. The expiry-day clock exits were not required to fall after the entry, so a
     15:00 entry could be handed an exit priced at 10:00 that same morning.
  3. SURVIVORSHIP, which is the one that matters.  The cache holds ATM and
     ATM+1 RELATIVE TO A MOVING SPOT, so a strike pinned at entry is only
     quoted while spot stays near it.  An exit that fails to price is not
     missing at random -- it is missing because the market ran away from the
     strike, and for a SHORT call spread that is the losing direction.  Only
     about a fifth of near-expiry entries had both legs still quoted at the
     bell, and dropping the rest flatters every headline.

Three and two are fixed outright.  One is repaired rather than fixed: at the
bell an option IS its intrinsic value, so the spread's closing debit is exactly
max(0, min(spot - k1, gap)) and can be imputed from spot with no quote at all.
Intraday that is a floor rather than an equality, but the floor binds hardest
precisely where the trade is lost -- spot far above both strikes, spread pinned
at max loss -- which is the region the missing quotes live in.  Every exit below
is therefore reported twice: QUOTED (both legs really traded, the flattered
number) and IMPUTED (missing legs valued at intrinsic, the honest one).

Also still standing, and not fixable here: Indian stock options are PHYSICALLY
SETTLED.  A short call left in the money at expiry is a delivery obligation, not
a cash debit, and STT on exercise is charged on intrinsic value, which a flat
0.28% turnover model does not capture at all.  That is why the exit this file
argues for squares off before the bell.
"""

import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import TrackedStock  # noqa: E402

from option_spreads import (  # noqa: E402
    MIN_LONG, MIN_RISK, TAX, TICK, day_t, drop_rebased_strikes, find_expiries,
    load_calls, log,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "spread_nearexpiry_raw.pkl")

CLOCKS = ("10:00", "12:00", "14:00", "15:00")
EXITS = [("hold5", "5 sessions"), ("preexpiry", "close day before expiry"),
         ("exp_10:00", "expiry day 10:00"), ("exp_12:00", "expiry day 12:00"),
         ("exp_14:00", "expiry day 14:00"), ("exp_15:00", "expiry day 15:00"),
         ("toexpiry", "ride into the bell")]
HEADLINE = "exp_14:00"


def rebuild(frame, expiries):
    """Every bar where both legs are quoted becomes one spread entry.

    Each exit is priced twice -- once insisting both legs really traded, once
    valuing a vanished leg at intrinsic -- so the survivorship filter can be
    measured instead of assumed away.
    """
    out = []
    for symbol, sub in frame.groupby("symbol"):
        wide_open = sub.pivot_table(index="ts", columns="strike", values="open")
        grid = wide_open.index
        # Reindexed onto the same grid: a pivot drops rows that are all-NaN, and
        # a mismatched index would turn a missing quote into a KeyError.
        wide_close = sub.pivot_table(
            index="ts", columns="strike", values="close").reindex(grid)
        wide_vol = sub.pivot_table(
            index="ts", columns="strike", values="volume").reindex(grid)
        spot_at = sub.pivot_table(
            index="ts", columns="strike", values="spot").reindex(grid).mean(axis=1)
        legs = sub.pivot_table(index="ts", columns="rel", values="strike")
        if "ATM" not in legs.columns or "ATM+1" not in legs.columns:
            continue
        legs = legs.dropna()
        pos = {t: i for i, t in enumerate(grid)}
        by_day = {}
        for i, t in enumerate(grid):
            by_day.setdefault(t.date(), []).append(i)

        for ts, leg in legs.iterrows():
            k1, k2 = float(leg["ATM"]), float(leg["ATM+1"])
            if k2 <= k1 or k1 not in wide_close.columns or k2 not in wide_close.columns:
                continue
            gap = k2 - k1
            try:
                s_in, l_in = wide_open.at[ts, k1], wide_open.at[ts, k2]
                v_s, v_l = wide_vol.at[ts, k1], wide_vol.at[ts, k2]
            except KeyError:
                continue
            if not (np.isfinite(s_in) and np.isfinite(l_in)):
                continue
            credit = (s_in - TICK) - (l_in + TICK)
            if credit <= 0 or credit >= gap:
                continue
            if l_in < MIN_LONG or (gap - credit) < MIN_RISK:
                continue
            if not (np.isfinite(v_s) and v_s > 0 and np.isfinite(v_l) and v_l > 0):
                continue
            dead = next((e for e in expiries if e >= ts.date()), None)
            if dead is None:
                continue

            def priced(out_ts, impute):
                s_o, l_o = wide_close.at[out_ts, k1], wide_close.at[out_ts, k2]
                if not (np.isfinite(s_o) and np.isfinite(l_o)):
                    if not impute:
                        return np.nan, np.nan, np.nan
                    sp = spot_at.get(out_ts, np.nan)
                    if not np.isfinite(sp):
                        return np.nan, np.nan, np.nan
                    if not np.isfinite(s_o):
                        s_o = max(sp - k1, 0.0)
                    if not np.isfinite(l_o):
                        l_o = max(sp - k2, 0.0)
                debit = min(max((s_o + TICK) - (l_o - TICK), 0.0), gap)
                return credit - debit - ((s_in + l_in + s_o + l_o) * TAX), s_o, l_o

            def book(rec, label, out_ts):
                rec[label], _, _ = priced(out_ts, False)
                rec["i_" + label], s_o, l_o = priced(out_ts, True)
                # Kept for drop_rebased_strikes: a split re-bases spot but not
                # the recorded strike, and the intrinsic above would then
                # subtract two different price scales.
                rec["spot_" + label] = spot_at.get(out_ts, np.nan)
                return s_o, l_o

            rec = {
                "symbol": symbol, "ts": ts, "day": ts.date(), "expiry": dead,
                "dte": (dead - ts.date()).days, "k1": k1, "k2": k2, "gap": gap,
                "credit": credit, "risk": gap - credit, "spot": spot_at.get(ts, np.nan),
                "v_short": v_s, "v_long": v_l, "s_in": s_in, "l_in": l_in,
            }
            for label, _ in EXITS:
                rec[label] = rec["i_" + label] = rec["spot_" + label] = np.nan

            # (a) the fixed 5-session hold, capped at expiry
            j = pos[ts] + 125
            if j < len(grid) and grid[j].date() <= dead:
                book(rec, "hold5", grid[j])
            # (b) close out at the last bar BEFORE expiry day -- never delivers
            before = [d for d in by_day if ts.date() < d < dead]
            if before:
                book(rec, "preexpiry", grid[by_day[max(before)][-1]])
            # (c) ride it all the way to the final bar of expiry day
            if dead in by_day:
                bell = by_day[dead][-1]
                rec["exp_spot"] = spot_at.get(grid[bell], np.nan)
                if grid[bell] > ts:
                    book(rec, "toexpiry", grid[bell])
                # (d) square off DURING expiry day. The last session holds the
                # most theta of the contract's life, and squaring off before the
                # bell takes that without ever facing delivery. This is the
                # version a retail account can actually trade.
                for clock in CLOCKS:
                    hit = [i for i in by_day[dead]
                           if grid[i] > ts and grid[i].strftime("%H:%M") <= clock]
                    if not hit:
                        continue
                    s_o, l_o = book(rec, "exp_" + clock, grid[hit[-1]])
                    if "exp_" + clock == HEADLINE:   # raw, so slippage re-prices
                        rec["s_out"], rec["l_out"] = s_o, l_o
            out.append(rec)
    return pd.DataFrame(out)


def show(frame, prefix, title):
    log("\n" + "=" * 94)
    log(title)
    log("=" * 94)
    log("  {:<24} {:>10} {:>9} {:>10} {:>9} {:>10}".format(
        "exit", "ROI/risk", "win%", "p5 ROI", "t(day)", "n"))
    for col, label in EXITS:
        v = frame.dropna(subset=[prefix + col])
        if v.empty:
            continue
        t = day_t(v[prefix + col].values, v.day.values)
        log("  {:<24} {:>+9.2f}% {:>8.1f}% {:>+9.1f}% {:>9} {:>10,d}".format(
            label, v[prefix + col].sum() / v.risk.sum() * 100,
            (v[prefix + col] > 0).mean() * 100,
            (v[prefix + col] / v.risk).quantile(0.05) * 100,
            "n/a" if not np.isfinite(t) else "{:+.2f}".format(t), len(v)))


def main():
    if "--cached" in sys.argv and os.path.exists(CACHE):
        trades = pd.read_pickle(CACHE)
        log("reusing {:,} cached spread entries".format(len(trades)))
    else:
        frame = load_calls()
        expiries = find_expiries(frame)
        log("loaded {:,} bars; {} expiries".format(len(frame), len(expiries)))
        trades = rebuild(frame, expiries)
        trades.to_pickle(CACHE)
        log("built {:,} spread entries".format(len(trades)))

    near = trades[trades.dte <= 7].copy()
    before = near[["i_" + c for c, _ in EXITS]].notna().sum().sum()
    near = drop_rebased_strikes(near, "k1", EXITS)
    after = near[["i_" + c for c, _ in EXITS]].notna().sum().sum()
    log("\ncorporate actions: {:,} of {:,} imputed exits blanked ({:.2f}%) -- a split"
        " re-bases spot but not the recorded strike.".format(
            before - after, before, 100 * (before - after) / max(before, 1)))
    log("\n{:,} entries at 0-7 days to expiry".format(len(near)))
    log("  across {} symbols, {} expiry cycles, {} symbol-expiry pairs".format(
        near.symbol.nunique(), near.expiry.nunique(),
        near.groupby(["symbol", "expiry"]).ngroups))
    log("  median entry volume: short leg {:,.0f}, long leg {:,.0f}".format(
        near.v_short.median(), near.v_long.median()))

    # -- the filter hiding inside the data ---------------------------------
    log("\n" + "-" * 94)
    log("SURVIVORSHIP -- read this before anything below it.")
    log("A strike pinned at entry is only quoted while spot stays near it, so an exit")
    log("that fails to price is missing BECAUSE the market ran away from the strike.")
    log("Moneyness is spot at the bell in units of the strike gap above the short")
    log("strike: 0 means it finished at the short strike, >1 means both legs are ITM")
    log("and the spread closed at its maximum loss.")
    log("-" * 94)
    got = near.dropna(subset=["exp_spot"]).copy()
    got["money"] = (got.exp_spot - got.k1) / got.gap
    log("  {:<24} {:>9} {:>11} {:>14} {:>15}".format(
        "exit", "quoted%", "n quoted", "money|quoted", "money|dropped"))
    for col, label in EXITS:
        v, d = got.dropna(subset=[col]), got[got[col].isna()]
        if v.empty:
            continue
        log("  {:<24} {:>8.1f}% {:>11,d} {:>+14.2f} {:>15}".format(
            label, 100 * len(v) / len(got), len(v), v.money.median(),
            "n/a" if d.empty else "{:+.2f}".format(d.money.median())))
    log("\n  Where 'money|dropped' sits above 'money|quoted', the missing exits are the")
    log("  losers. Imputing them at intrinsic is what the second table below does.")

    show(near, "", "0-7 DTE -- QUOTED ONLY (both legs really traded). Flattered.")
    show(near, "i_", "0-7 DTE -- MISSING LEGS IMPUTED AT INTRINSIC. The honest one.")

    # -- delivery exposure -------------------------------------------------
    log("\n" + "-" * 94)
    log("DELIVERY EXPOSURE -- these are physically settled. A short call left in the")
    log("money at expiry is an obligation to deliver shares, not a cash debit. Measured")
    log("on every entry whose expiry-day bell exists: whether OUR cache quoted the")
    log("strike has nothing to do with whether the exchange exercises it.")
    log("-" * 94)
    if not got.empty:
        short_itm = (got.money > 0).mean() * 100
        both_itm = (got.money > 1).mean() * 100
        log("  median spot at the bell Rs{:,.0f} vs short strike Rs{:,.0f} "
            "(gap to long strike Rs{:.1f})".format(
                got.exp_spot.median(), got.k1.median(), got.gap.median()))
        log("  spot finishes above the SHORT strike {:.1f}% of the time".format(short_itm))
        log("  spot finishes above the LONG strike too {:.1f}% -- both legs exercise "
            "and the deliveries net".format(both_itm))
        log("  the dangerous band, short ITM with the long expiring worthless: "
            "{:.1f}%".format(short_itm - both_itm))
        log("  where the bell lands, in units of the strike gap above the short strike:")
        for q in (0.05, 0.25, 0.50, 0.75, 0.95):
            log("      p{:<3.0f} {:>+8.2f} gaps".format(q * 100, got.money.quantile(q)))
        log("  squaring off before the bell avoids all of it, by construction.")

    # -- one bet per symbol-expiry ----------------------------------------
    log("\n" + "-" * 94)
    log("ONE ENTRY PER SYMBOL PER EXPIRY -- the overlapping bars are one bet, not many.")
    log("head(1), not first(): groupby.first() is column-wise and would stitch one")
    log("bar's entry onto another bar's exit.")
    log("-" * 94)
    solo = near.sort_values("ts").groupby(["symbol", "expiry"], as_index=False).head(1)
    show(solo, "i_", "0-7 DTE, imputed, one trade per symbol-expiry")

    # -- cycle by cycle ----------------------------------------------------
    log("\n" + "-" * 94)
    log("BY EXPIRY CYCLE -- squared off on expiry day at 14:00, one trade per")
    log("symbol-expiry. Two good months would show here as two good months.")
    log("-" * 94)
    log("  {:<12} {:>12} {:>12} {:>9} {:>9}".format(
        "expiry", "imputed", "quoted only", "win%", "n"))
    rows = []
    for expiry, g in solo.groupby("expiry"):
        v = g.dropna(subset=["i_" + HEADLINE])
        if len(v) < 10:
            continue
        r = v["i_" + HEADLINE].sum() / v.risk.sum() * 100
        q = g.dropna(subset=[HEADLINE])
        qr = q[HEADLINE].sum() / q.risk.sum() * 100 if len(q) else np.nan
        rows.append({"expiry": str(expiry), "roi": r, "quoted": qr,
                     "win": (v["i_" + HEADLINE] > 0).mean() * 100, "n": len(v)})
        log("  {:<12} {:>+11.2f}% {:>+11.2f}% {:>8.1f}% {:>9,d}".format(
            str(expiry), r, qr, (v["i_" + HEADLINE] > 0).mean() * 100, len(v)))
    cycles = pd.DataFrame(rows)
    if len(cycles) > 1:
        log("\n  cycles profitable: {}/{}   median cycle {:+.2f}%   worst {:+.2f}%".format(
            int((cycles.roi > 0).sum()), len(cycles), cycles.roi.median(), cycles.roi.min()))
        log("  NOTE the gap in the cycle list: Aug 2025 - Jun 2026 is the stretch the")
        log("  download never reached, so this is six consecutive 2025 cycles plus two")
        log("  from 2026, not a clean 18-month run.")

    # -- the assumption most likely to be wrong ----------------------------
    log("\n" + "-" * 94)
    log("SLIPPAGE -- everything above assumes a 5-paise tick crossed on each leg each")
    log("way. That is an INDEX-option assumption. Stock options in expiry week are")
    log("thinner, and half-crossing a wider quote is where results like this usually")
    log("die. Re-priced on the imputed 14:00 exit:")
    log("-" * 94)
    log("  {:<20} {:>11} {:>9} {:>10} {:>9} {:>10}".format(
        "half-spread/leg", "ROI/risk", "win%", "Rs/spread", "t(day)", "openable"))
    base = solo.dropna(subset=["s_out", "l_out", "i_" + HEADLINE]).copy()
    slip = []
    for cost in (0.05, 0.25, 0.50, 1.00, 1.50):
        credit = (base.s_in - cost) - (base.l_in + cost)
        # A spread that no longer opens for a credit is not a trade we would take,
        # so it leaves the sample rather than being counted as a loss.
        v = base[(credit > 0) & (credit < base.gap)]
        credit, risk = credit[v.index], v.gap - credit[v.index]
        debit = ((v.s_out + cost) - (v.l_out - cost)).clip(lower=0).clip(upper=v.gap)
        pnl = credit - debit - ((v.s_in + v.l_in + v.s_out + v.l_out) * TAX)
        t = day_t(pnl.values, v.day.values)
        slip.append({"cost": cost, "roi": pnl.sum() / risk.sum() * 100,
                     "win": (pnl > 0).mean() * 100, "rs": pnl.mean(), "t": t,
                     "n": len(v), "openable": len(v) / len(base) * 100})
        log("  Rs{:<18.2f} {:>+10.2f}% {:>8.1f}% {:>+10.2f} {:>9} {:>9.0f}%".format(
            cost, pnl.sum() / risk.sum() * 100, (pnl > 0).mean() * 100, pnl.mean(),
            "n/a" if not np.isfinite(t) else "{:+.2f}".format(t),
            len(v) / len(base) * 100))
    log("\n  Median credit Rs{:.2f} against Rs{:.2f} of risk, so each extra 25 paise of".format(
        base.credit.median(), base.risk.median()))
    log("  half-spread costs Rs1.00 across the four crossings. 'openable' is the share")
    log("  of entries still worth a credit at that spread -- the rest simply vanish.")

    # -- rupees ------------------------------------------------------------
    lots = dict(TrackedStock.objects.values_list("symbol", "lot_size"))
    solo = solo.copy()
    solo["lot"] = solo.symbol.map(lots)
    sized = solo.dropna(subset=["lot", "i_" + HEADLINE])
    if not sized.empty:
        pnl, risk = sized["i_" + HEADLINE] * sized.lot, sized.risk * sized.lot
        log("\n" + "-" * 94)
        log("RUPEES -- one spread per symbol per expiry, squared off expiry day 14:00,")
        log("imputed, at a 5-paise tick. Divide through by the slippage table above.")
        log("-" * 94)
        log("  {:,} spreads   max risk Rs{:,.0f}   P&L Rs{:+,.0f} ({:+.2f}% of risk)".format(
            len(sized), risk.sum(), pnl.sum(), pnl.sum() / risk.sum() * 100))
        log("  per spread: mean Rs{:+,.0f}, median Rs{:+,.0f}, worst Rs{:+,.0f}".format(
            pnl.mean(), pnl.median(), pnl.min()))
        log("  margin blocked per spread ~ Rs{:,.0f} median, Rs{:,.0f} at the 90th "
            "percentile".format(risk.median(), risk.quantile(0.9)))

    solo.to_csv(os.path.join(HERE, "spread_nearexpiry_trades.csv"), index=False)
    cycles.to_csv(os.path.join(HERE, "spread_cycles.csv"), index=False)
    pd.DataFrame(slip).to_csv(os.path.join(HERE, "spread_slippage.csv"), index=False)
    log("\nwritten: spread_nearexpiry_trades.csv, spread_cycles.csv, spread_slippage.csv")


if __name__ == "__main__":
    main()
