"""What Rs1 lakh actually does, running the signal as a portfolio.

WHY THE TABLES SO FAR DO NOT ANSWER THIS.  Everything up to here reports a
per-trade multiple, which quietly assumes you can take every trade at equal
weight.  You cannot.  A stock option trades in a LOT -- 725 shares is the median
here -- so one position costs Rs20,351 at the median and Rs70,875 at the 90th
percentile.  At Rs1L that is one or two positions, not twenty, and a strategy
whose profit arrives on three sessions out of thirty-nine needs to be holding
something on those three sessions.  The lot constraint and the concentration
finding interact, and only a capital-aware simulation shows how.

WHAT IS MODELLED HONESTLY.  Whole lots only.  Capital is LOCKED from entry until
that position's own exit, so a day with no free cash takes no trade no matter how
strong the signal -- which is the mechanism that makes lumpy strategies worse than
their per-trade statistics look.  Entry at the signal session's close, exit at the
close the rule chose, both net of the premium-band round trip.

WHAT IS STILL OPTIMISTIC, and it is not a short list.  Fills are assumed at the
close plus half the median spread, and the median is not what you get when you are
buying a Rs10 option in size on a day the stock is moving.  The exit is a trail
evaluated on CLOSES, so it never claims an intraday high it could not have seen.
There is no slippage beyond the spread, no rejected orders, and no impact -- and
these contracts trade 5,625 lots a day at the far end, so a real Rs1L order is a
visible fraction of the book.  Six weeks, one regime, one market.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import charge, load_spreads, log  # noqa: E402

RULE = "trail 30% of peak"
MIN_PREM = 2.50


def build():
    """Signal + exit timing + lot size, one row per candidate trade."""
    t = pd.read_parquet(os.path.join(HERE, "otm_exits.parquet"))
    s = pd.read_parquet(os.path.join(HERE, "otm_signal.parquet"))
    lots = (pd.read_parquet(os.path.join(HERE, "deep_otm.parquet"),
                            columns=["sid", "lot"]).drop_duplicates("sid"))
    sig = s[["symbol", "day", "top10", "top5", "top2", "daily3"]].drop_duplicates(
        ["symbol", "day"])
    c = (t[(t["kind"] == "CE") & (t["entry"] >= MIN_PREM)]
         .merge(sig, on=["symbol", "day"], how="inner")
         .merge(lots, on="sid", how="left"))
    sp = load_spreads()
    f = charge(c["entry"].to_numpy(float), sp)
    c["net"] = c[RULE].to_numpy(float) * (1 - f / 2) / (1 + f / 2)
    c["held"] = c["days::" + RULE].fillna(10).astype(int)
    c["ticket"] = c["lot"] * c["entry"]
    return c.dropna(subset=["net", "lot"])


def run(c, capital, col, band, max_pos, per_pos_frac, label, entry_days=None,
        quiet=False):
    """Walk the sessions in order, respecting cash and whole lots.

    `entry_days`, when given, is the set of sessions on which a NEW position may
    be opened. It suppresses entries only -- the calendar itself is left whole.

    THAT DISTINCTION IS THE WHOLE POINT AND IS EASY TO GET WRONG.  The obvious
    way to ask "what if late July had not happened" is to delete those sessions
    from `days`. Doing so renumbers every index, so `exit_i` -- computed as an
    offset into this list -- silently points at the wrong session for every
    position still open across the gap, and positions entered before the cut exit
    early with someone else's return. Suppressing entries keeps holding periods,
    cash locking and exits exactly as they were and changes only the decision
    under test.
    """
    lo, hi = band
    v = c[c[col].astype(bool) & (c["otm"] >= lo) & (c["otm"] < hi)].copy()
    days = sorted(c["day"].unique())
    idx = {d: i for i, d in enumerate(days)}

    cash, equity, open_pos, trades, skipped = capital, capital, [], [], 0
    curve, peak, mdd = [], capital, 0.0
    for i, d in enumerate(days):
        for p in [p for p in open_pos if p["exit_i"] <= i]:
            cash += p["cost"] * p["net"]
            trades.append(p)
        open_pos = [p for p in open_pos if p["exit_i"] > i]

        # Strongest signal first, and one contract per symbol -- two strikes on
        # the same name is one bet wearing two hats.
        cand = (v[v["day"] == d].sort_values("entry")
                .drop_duplicates("symbol", keep="first"))
        if entry_days is not None and d not in entry_days:
            cand = cand.iloc[0:0]
        for _, r in cand.iterrows():
            if len(open_pos) >= max_pos:
                break
            budget = min(capital * per_pos_frac, cash)
            n_lots = int(budget // r["ticket"])
            if n_lots < 1:
                skipped += 1
                continue
            cost = n_lots * r["ticket"]
            cash -= cost
            open_pos.append({"cost": cost, "net": r["net"], "symbol": r["symbol"],
                             "day": d, "entry": r["entry"], "otm": r["otm"],
                             "exit_i": min(i + max(int(r["held"]), 1), len(days) - 1)})
        equity = cash + sum(p["cost"] for p in open_pos)   # holdings at cost
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
        curve.append(equity)

    for p in open_pos:
        cash += p["cost"] * p["net"]
        trades.append(p)
    final = cash
    if not trades:
        if not quiet:
            print("  {:<34} -- no position was ever affordable".format(label))
        return None
    tr = pd.DataFrame(trades)
    tr["pnl"] = tr["cost"] * (tr["net"] - 1)
    if not quiet:
        print("  {:<34} {:>10,.0f} {:>+9.1%} {:>7,} {:>8.1%} {:>9,.0f} {:>9.1%} {:>8,}".format(
            label, final, final / capital - 1, len(tr), (tr["net"] > 1).mean(),
            tr["cost"].median(), mdd, skipped))
    return {"final": final, "ret": final / capital - 1, "n": len(tr), "mdd": mdd}


def main():
    c = build()
    log("{:,} candidate call-trades, {} sessions, median ticket Rs{:,.0f}".format(
        len(c), c["day"].nunique(), c["ticket"].median()))

    for capital in (100_000, 500_000):
        print()
        print("=" * 118)
        print("Rs{:,} capital -- signal calls, exit = {}, whole lots, cash locked while held"
              .format(capital, RULE))
        print("=" * 118)
        print("  {:<34} {:>10} {:>9} {:>7} {:>8} {:>9} {:>9} {:>8}".format(
            "construction", "final", "return", "trades", "win%", "med cost", "max DD",
            "skipped"))
        for col in ("top10", "top5", "top2", "daily3"):
            for band, bl in [((-1.0, 1.0), "any strike"), ((0.08, 0.25), "+8-25% OTM")]:
                run(c, capital, col, band, max_pos=5, per_pos_frac=0.25,
                    label="{:<8} {:<12} 5 pos".format(col, bl))
        print("  " + "-" * 114)
        print("  the control: same machinery, NO signal -- every call in the band")
        c2 = c.copy()
        c2["all"] = True
        for band, bl in [((-1.0, 1.0), "any strike"), ((0.08, 0.25), "+8-25% OTM")]:
            run(c2, capital, "all", band, max_pos=5, per_pos_frac=0.25,
                label="{:<8} {:<12} 5 pos".format("none", bl))


HOT = [dt.date(2026, 7, 24), dt.date(2026, 7, 27), dt.date(2026, 7, 29)]


def periods(c, capital=100_000, col="top2"):
    """Is this a strategy, or is it three sessions?

    The single most important table in the file, and the reason the headline
    above must not be read on its own.

    TWO CONSTRUCTIONS, AND USING THE WRONG ONE COST THIS RESULT ITS NUMBERS.
    A contiguous window (a half) is honestly simulated by SUBSETTING: capital is
    deployed fresh, the calendar inside the window is unbroken, and nothing is
    renumbered. Cutting three sessions out of the MIDDLE is not the same object.
    Subsetting there compresses the calendar, and `exit_i` is an offset into it,
    so every position open across the cut frees its cash early -- while still
    booking the full multi-day return it was credited with. The degenerate case
    makes the error obvious: subset to the three hot sessions alone and the
    calendar is three rows long, so ten-day holds all exit by row 3 and Rs1L
    recycles into EIGHT trades on three days. That is where the published
    +345.2% came from, and it is not a thing that could have happened.

    So: subset for contiguous windows, suppress entries for a middle cut.
    """
    days = sorted(c["day"].unique())
    half = len(days) // 2
    hot = set(d for d in HOT if d in set(days))
    head = "  {:<36} {:>10} {:>9} {:>7} {:>8} {:>9} {:>9} {:>8}".format(
        "period", "final", "return", "trades", "win%", "med cost", "max DD", "skipped")

    print()
    print("=" * 118)
    print("IS IT A STRATEGY OR IS IT THREE DAYS?   Rs{:,}, {}, 5 positions at 25%, {} sessions"
          .format(capital, col, len(days)))
    print("=" * 118)
    print(head)
    run(c, capital, col, (-1.0, 1.0), 5, 0.25, "  everything")
    for lbl, w in [("first half", days[:half]), ("second half", days[half:])]:
        # contiguous -> fresh capital over an unbroken calendar
        run(c[c["day"].isin(set(w))].copy(), capital, col, (-1.0, 1.0), 5, 0.25,
            "  {} (from {})".format(lbl, w[0]))
    for lbl, allowed in [("EXCEPT the {} hot sessions".format(len(hot)), set(days) - hot),
                         ("ONLY those {} sessions".format(len(hot)), hot)]:
        # middle cut -> suppress entries, leave the calendar and every hold alone
        run(c, capital, col, (-1.0, 1.0), 5, 0.25, "  " + lbl, entry_days=allowed)

    print("  " + "-" * 114)
    print("  superseded, kept so the earlier figures are traceable: the same two rows")
    print("  computed by SUBSETTING, which compresses the calendar and frees locked cash early")
    for lbl, allowed in [("EXCEPT the hot sessions", set(days) - hot),
                         ("ONLY those sessions", hot)]:
        run(c[c["day"].isin(allowed)].copy(), capital, col, (-1.0, 1.0), 5, 0.25,
            "  {} [subset]".format(lbl))

    # The knob sweep, run twice. Parameter robustness is not time robustness.
    print()
    print("  the knob sweep -- 1-8 positions x 15-100% sizing, with and without those sessions")
    for lbl, allowed in [("all sessions", set(days)), ("hot sessions removed", set(days) - hot)]:
        wins = tot = 0
        for mp in (1, 2, 3, 5, 8):
            for frac in (0.15, 0.25, 0.50, 1.00):
                r = run(c, capital, col, (-1.0, 1.0), mp, frac, "",
                        entry_days=allowed, quiet=True)
                tot += 1
                wins += 1 if (r and r["ret"] > 0) else 0
        print("    {:<26} {:>2} of {:>2} settings profitable".format(lbl, wins, tot))


if __name__ == "__main__":
    main()
    periods(build())
