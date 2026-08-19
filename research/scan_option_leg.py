"""The option leg: your scale-in scheme, priced on real pinned contracts.

THE SCHEME AS SPECIFIED.  Rs25,000 allocated per trade. Rs13,000 in on the
signal; if the option falls ~25%, add Rs12,000. Stop out when a CLOSE prints at
or below 50% of the blended entry. Take a first profit at +50%/+75%/+100%, sell
part, and trail the rest. Never lose more than half the allocation.

WHY THE DATA IS ONLY SIX WEEKS, AND WHY THAT IS NOT A CHOICE.  The 18-month
15-minute option feed cannot price a multi-day hold. It is an ATM-relative
rolling feed: it stores whichever strikes sit near spot TODAY, so a contract
drops out of coverage as soon as the stock walks away from it -- 42-46% survive
five sessions, and the ones that vanish are exactly the big movers this strategy
is hunting. It also carries no expiry column, so one (symbol, strike) key mixes
several expiries: HAL's 4500 call shows Rs19.80 and Rs32.85 at the SAME
timestamp. Pinned contracts exist only for live expiries -- expired ones return
DH-907 and their ids are published nowhere -- so six weeks is the ceiling.

THE TWO CONSTRAINTS THAT BITE BEFORE ANY EDGE DOES, and both are lot-size, not
market, facts:
  1. Rs13,000 buys one lot of a traded call only 45% of the time (median lot
     Rs15,531). The first tranche is therefore usually NOT half the allocation.
  2. "Exit some quantity and trail the rest" needs at least two lots. At
     Rs25,000 that is uncommon, so the partial-exit half of the scheme mostly
     degenerates into all-or-nothing.
Both are reported rather than assumed away, because a simulation that allows
fractional lots would quietly invent the very flexibility the scheme depends on.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from otm_exits import charge, load_spreads  # noqa: E402

ALLOC = 25_000
FIRST, ADD = 13_000, 12_000
ADD_TRIGGER = -0.25        # add when the option is 25% below the first fill
STOP = 0.50                # exit when a CLOSE is at/below 50% of blended entry
MIN_PREM = 2.50
TRAIL = 0.30               # give back 30% of the peak on the runner


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def build():
    """Signal days joined to the nearest-strike call on that day."""
    o = pd.read_parquet(os.path.join(HERE, "deep_otm.parquet"))
    o["day"] = pd.to_datetime(o["ts"]).dt.date
    o = o[(o["kind"] == "CE")].copy()
    for c in ("close", "high", "low", "strike", "lot", "volume"):
        o[c] = o[c].astype(float)

    eq = pd.read_parquet(os.path.join(HERE, "equity_15m.parquet"))
    spot = (eq.groupby(["symbol", "day"])["close"].last().rename("spot").reset_index())

    f = pd.read_parquet(os.path.join(HERE, "scan_signals.parquet"))
    f["day"] = pd.to_datetime(f["day"]).dt.date
    sig = (f[f["sig_1h_nobb"]][["symbol", "day"]].drop_duplicates())

    ent = o.merge(sig, on=["symbol", "day"], how="inner").merge(spot, on=["symbol", "day"])
    # "a nearby option": the listed strike closest to spot that actually traded
    ent = ent[(ent["volume"] > 0) & (ent["close"] >= MIN_PREM)].copy()
    ent["dist"] = (ent["strike"] / ent["spot"] - 1).abs()
    ent = ent.sort_values("dist").drop_duplicates(["symbol", "day"], keep="first")
    return o, ent


def simulate(o, ent, target, first_budget=FIRST, allow_single_lot=True,
             scale_in=True, stop_on_entry=False):
    """Walk each trade forward on daily closes, whole lots only.

    `scale_in=False` runs the identical trade without the Rs12,000 add, so the
    two can be differenced ON THE SAME PATHS.  That comparison is the only
    honest one available.  Splitting the results by whether the add HAPPENED
    looks far more dramatic -- 1.17x against 0.43x -- and means nothing, because
    "never added" is just "the option never fell 25%", which is the outcome
    wearing a disguise.  You cannot select on it at entry.

    `stop_on_entry=True` measures the stop against the FIRST fill instead of the
    blended average.  Same intent, different arithmetic: averaging down drags
    the blended entry below the first, so a stop at 50% OF THE BLEND sits far
    below 50% of what you originally paid, and the promise to risk half the
    allocation quietly stops holding.
    """
    sp = load_spreads()
    paths = {s: g.sort_values("day") for s, g in o.groupby("sid")}
    out = []
    for _, r in ent.iterrows():
        g = paths.get(r["sid"])
        if g is None:
            continue
        g = g[g["day"] >= r["day"]]
        if len(g) < 2:
            continue
        p0, lot = float(r["close"]), float(r["lot"])
        ticket = p0 * lot
        n = int(first_budget // ticket)
        if n < 1:
            if not allow_single_lot or ticket > ALLOC:
                out.append({"symbol": r["symbol"], "day": r["day"], "skipped": True})
                continue
            n = 1                       # one lot is the smallest tradeable unit
        qty, cost, cash = n * lot, n * ticket, ALLOC - n * ticket
        added, scaled, peak, realised = False, False, p0, 0.0
        exit_reason, held = "expiry", 0
        for _, b in g.iloc[1:].iterrows():
            held += 1
            px = float(b["close"])
            peak = max(peak, px)
            avg = cost / qty
            # 1. scale in on weakness, once
            if scale_in and not added and px <= p0 * (1 + ADD_TRIGGER):
                k = int(min(ADD, cash) // (px * lot))
                if k >= 1:
                    qty += k * lot
                    cost += k * px * lot
                    cash -= k * px * lot
                added = True
                avg = cost / qty
            # 2. hard stop on a CLOSING basis
            if px <= (p0 if stop_on_entry else avg) * STOP:
                realised += qty * px
                qty, exit_reason = 0.0, "stop"
                break
            # 3. first target -> sell half, trail the runner
            if not scaled and px >= avg * (1 + target):
                half = np.floor((qty / lot) / 2) * lot
                if half >= lot:
                    realised += half * px
                    qty -= half
                scaled, peak = True, px
                continue
            # 4. trail whatever is left
            if scaled and px <= peak * (1 - TRAIL):
                realised += qty * px
                qty, exit_reason = 0.0, "trail"
                break
        if qty > 0:
            realised += qty * float(g.iloc[-1]["close"])
        fr = float(charge(np.array([p0]), sp)[0])
        gross = realised / cost
        net = gross * (1 - fr / 2) / (1 + fr / 2)
        out.append({"symbol": r["symbol"], "day": r["day"], "skipped": False,
                    "entry": p0, "lots": n, "capital": cost, "added": added,
                    "scaled": scaled, "gross": gross, "net": net,
                    "pnl": cost * (net - 1), "reason": exit_reason, "held": held})
    return pd.DataFrame(out)


def report(t, label):
    ok = t[~t["skipped"].astype(bool)]
    if not len(ok):
        print("  {:<26} nothing tradeable".format(label))
        return
    pnl = ok["pnl"]
    print("  {:<26} {:>6,} {:>8.1%} {:>10,.0f} {:>10,.0f} {:>8.2f} {:>8.1%} {:>8.1%} {:>8.1%}".format(
        label, len(ok), (ok["net"] > 1).mean(), pnl.sum(), pnl.mean(),
        ok["net"].median(), ok["added"].astype(bool).mean(),
        ok["scaled"].astype(bool).mean(), (ok["reason"] == "stop").mean()))


def paired(o, ent, target=0.50):
    """The scale-in, tested causally: same paths, add vs no-add.

    Run both variants over the identical trade list and difference them trade by
    trade.  Only the rows where the add actually fired can differ, so those are
    the rows the test is about.
    """
    a = simulate(o, ent, target).set_index(["symbol", "day"])
    b = simulate(o, ent, target, scale_in=False).set_index(["symbol", "day"])
    j = a.join(b, lsuffix="_add", rsuffix="_no").dropna(subset=["net_add", "net_no"])
    j = j[~j["skipped_add"].astype(bool) & j["added_add"].astype(bool)]
    d = j["pnl_add"] - j["pnl_no"]
    print()
    print("  THE SCALE-IN, TESTED ON THE SAME PATHS ({:,} trades where the add fired)".format(len(j)))
    print("    with the Rs{:,} add : {:>8,.0f} P&L/trade, {:.1%} win, {:.2f}x median".format(
        ADD, j["pnl_add"].mean(), (j["net_add"] > 1).mean(), j["net_add"].median()))
    print("    without it          : {:>8,.0f} P&L/trade, {:.1%} win, {:.2f}x median".format(
        j["pnl_no"].mean(), (j["net_no"] > 1).mean(), j["net_no"].median()))
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else np.nan
    print("    the add is worth    : {:>+8,.0f} per trade  (t {:+.2f}, better on {:.1%} of them)"
          .format(d.mean(), t, (d > 0).mean()))


def preservation(o, ent):
    """Does the scheme keep its promise to risk at most half the allocation?"""
    print()
    print("  CAPITAL PRESERVATION -- the rule was: lose at most 50% of the Rs{:,}".format(ALLOC))
    for lbl, kw in [("stop vs blended entry (as specified)", {}),
                    ("stop vs the FIRST fill", {"stop_on_entry": True}),
                    ("no scale-in at all", {"scale_in": False})]:
        ok = simulate(o, ent, 0.50, **kw)
        ok = ok[~ok["skipped"].astype(bool)]
        print("    {:<36} worse than -50% of deployed: {:>5.1%}   of the Rs{:,} alloc: {:>5.1%}   mean {:>+6.1%}"
              .format(lbl, (ok["net"] < 0.5).mean(), ALLOC,
                      (ok["pnl"] / ALLOC < -0.5).mean(),
                      ok["pnl"].sum() / ok["capital"].sum()))


def main():
    o, ent = build()
    log("{:,} signal-days matched to a tradeable near-the-money call".format(len(ent)))
    lots = (ent["close"] * ent["lot"])
    print("  the chosen strike sits {:+.2%} from spot (median) -- genuinely 'nearby'".format(
        ent["dist"].median()))
    print("  one lot costs: median Rs{:,.0f}; <=Rs{:,} on {:.1%} of these; <=Rs{:,} on {:.1%}"
          .format(lots.median(), FIRST, (lots <= FIRST).mean(), ALLOC, (lots <= ALLOC).mean()))

    print()
    print("=" * 118)
    print("YOUR SCHEME ON REAL CONTRACTS -- Rs{:,}/trade, add Rs{:,} at {:.0%}, "
          "stop at {:.0%} of blended entry (closing basis)".format(
              ALLOC, ADD, ADD_TRIGGER, STOP))
    print("=" * 118)
    print("  {:<26} {:>6} {:>8} {:>10} {:>10} {:>8} {:>8} {:>8} {:>8}".format(
        "first target", "trades", "win%", "total P&L", "per trade", "med x",
        "added%", "scaled%", "stopped%"))
    for tgt in (0.50, 0.75, 1.00):
        report(simulate(o, ent, tgt), "+{:.0%} then trail {:.0%}".format(tgt, TRAIL))

    paired(o, ent)
    preservation(o, ent)

    print()
    print("  " + "-" * 114)
    print("  the control: same contracts, same scheme, but entered on EVERY session")
    print("  (if the signal is doing work, the rows above must beat these)")
    allday = o[(o["volume"] > 0) & (o["close"] >= MIN_PREM)].copy()
    eq = pd.read_parquet(os.path.join(HERE, "equity_15m.parquet"))
    spot = eq.groupby(["symbol", "day"])["close"].last().rename("spot").reset_index()
    allday = allday.merge(spot, on=["symbol", "day"])
    allday["dist"] = (allday["strike"] / allday["spot"] - 1).abs()
    ctl = (allday.sort_values("dist").drop_duplicates(["symbol", "day"], keep="first")
           .sample(n=min(4000, len(allday)), random_state=0))
    for tgt in (0.50, 0.75, 1.00):
        report(simulate(o, ctl, tgt), "control +{:.0%}".format(tgt))


if __name__ == "__main__":
    main()
