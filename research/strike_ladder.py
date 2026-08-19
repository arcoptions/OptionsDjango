"""Does a cheaper strike lower the accuracy the chart read has to clear?

The shipped model asks the user for 56.8% correct CALL reads against 48.4%
free, measured at the ATM strike. Every rupee of that hurdle is premium: the
read has to pay for the contract before it pays anything to the user. So the
obvious lever is a cheaper contract. A 5% move that doubles an ATM call could
treble one two strikes out, and a bigger multiple when right means fewer
correct reads are needed to break even.

The counter-argument is equally obvious and this project has been burned by it
repeatedly: cheap options decay faster, their friction is set by premium rather
than moneyness, and below about fifty paise the tick alone decides the outcome.
ATM+2 here has a Rs10.40 median premium with 7.0% of bars under Rs0.50, so both
effects are live and only the net matters.

WHAT MAKES THIS COMPARABLE. Each rung is priced on the SAME entries -- same
symbol, same session, same five-day hold -- and the tables are then cut to the
entries where every rung produced a fill. Otherwise a rung that quietly drops
its illiquid names would look better for having traded less.

WHY THE HURDLE IS THE RIGHT MEASURE HERE. Break-even accuracy is built on loss
probability, so a capped payoff can flatter it: a target truncates the right
tail while losers still run to zero, which reads as improvement. These are
hold-to-end trades with an uncapped upside, which is the case the measure is
valid for.

    python research/strike_ladder.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from option_spreads import TAX, TICK, day_t  # noqa: E402
from premove_legs import HOLD, load  # noqa: E402
from chart_reader_hurdle import scan_entries  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "strike_ladder.txt")
RUNGS = ("ATM", "ATM+1", "ATM+2")

LINES: list[str] = []


def log(message: str = "") -> None:
    print(message)
    LINES.append(message)


def price_rung(frame: pd.DataFrame, entries: set, rung: str) -> list[dict]:
    """One contract per entry, anchored on `rung` instead of always on ATM.

    This is `premove_legs.run_symbol` with the anchor lifted out. The strike is
    still absolute once chosen, so the contract stays pinned for the whole hold
    even as spot moves it to a different rung -- that fix is what makes any of
    these numbers real, and it must not be undone by anchoring per bar.
    """
    opens = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = opens.index
    close = frame.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    vol = frame.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    spot = (frame.pivot_table(index="ts", columns="strike", values="spot")
            .reindex(grid).mean(axis=1))
    anchor = (frame[frame["rel"] == rung].drop_duplicates("ts")
              .set_index("ts")["strike"].reindex(grid))

    days = pd.Series(grid.date, index=grid)
    first_bar = {d: g.index[0] for d, g in days.groupby(days)}
    pos = {t: i for i, t in enumerate(grid)}

    out = []
    for day in sorted(entries):
        ts = first_bar.get(day)
        if ts is None:
            continue
        k = anchor.get(ts, np.nan)
        if not np.isfinite(k) or k not in opens.columns:
            continue
        entry = opens.at[ts, k]
        if not np.isfinite(entry) or entry < 1.0 or not vol.at[ts, k] > 0:
            continue
        cost = entry + TICK
        i = pos[ts]
        values = []
        for j in range(i + 1, min(i + 1 + HOLD, len(grid))):
            t = grid[j]
            v = close.at[t, k]
            if not np.isfinite(v):
                s = spot.get(t, np.nan)
                if not np.isfinite(s):
                    continue
                # A contract with no quote is worth its intrinsic value, not
                # nothing -- assuming zero is how the losing side goes missing.
                v = max(s - k, 0.0)
            values.append(max(v - TICK, 0.0))
        if not values:
            continue
        values = np.array(values)
        fees = (entry + values[-1]) * TAX
        out.append({"day": day, "strike": k, "cost": cost,
                    "spot": spot.get(ts, np.nan),
                    "hold": values[-1] - cost - fees})
    return out


def hurdle(v: pd.DataFrame) -> tuple[str, float, float, float]:
    """Accuracy needed to break even, plus what each side actually pays."""
    right, wrong = v[v["went_up"]], v[~v["went_up"]]
    if len(right) < 25 or len(wrong) < 25:
        return "thin", np.nan, np.nan, np.nan
    med_r = right["ret"].median() * 100
    med_w = wrong["ret"].median() * 100
    lose_r, lose_w = (right["ret"] < 0).mean(), (wrong["ret"] < 0).mean()
    if lose_w <= max(0.5, lose_r):
        return "always pays" if lose_w <= 0.5 else "unreachable", med_r, med_w, np.nan
    need = (lose_w - 0.5) / (lose_w - lose_r)
    return ("{:.1%}".format(need) if need <= 1 else "unreachable"), med_r, med_w, need


def main() -> None:
    entries = scan_entries()
    opts = load("CALL")
    log("=" * 78)
    log("DOES A CHEAPER STRIKE LOWER THE HURDLE?")
    log("=" * 78)
    log("\n{:,} CALL bars, {} symbols. Entry at the next session's open, held"
        .format(len(opts), opts["symbol"].nunique()))
    log("{} bars (~5 sessions). Cost includes the tick and statutory charges."
        .format(HOLD))

    frames = {}
    for rung in RUNGS:
        rows = []
        shared = sorted(set(opts["symbol"].unique()) & set(entries["symbol"].unique()))
        for symbol in shared:
            want = entries[entries["symbol"] == symbol]
            flags = want.set_index(want["entry"].dt.date)[["went_up"]].to_dict("index")
            for trade in price_rung(opts[opts["symbol"] == symbol],
                                    set(want["entry"].dt.date), rung):
                flag = flags.get(trade["day"])
                if flag is None:
                    continue
                trade.update({"symbol": symbol, "went_up": flag["went_up"]})
                rows.append(trade)
        f = pd.DataFrame(rows).dropna(subset=["hold"])
        f["ret"] = f["hold"] / f["cost"]
        f["day"] = pd.to_datetime(f["day"])
        frames[rung] = f
        log("  {:<6} {:,} trades priced".format(rung, len(f)))

    log("\n" + "-" * 78)
    log("ALL TRADES AT EACH RUNG (different samples -- see the matched table below)")
    log("-" * 78)
    log("\n  {:<7} {:>7} {:>9} {:>11} {:>11} {:>7} {:>12}".format(
        "rung", "trades", "premium", "if right", "if wrong", "win%", "break-even"))
    log("  " + "-" * 70)
    for rung in RUNGS:
        f = frames[rung]
        need, med_r, med_w, _ = hurdle(f)
        log("  {:<7} {:>7,} {:>8.2f} {:>10.1f}% {:>10.1f}% {:>6.1f}% {:>12}".format(
            rung, len(f), f["cost"].median(), med_r, med_w,
            (f["ret"] > 0).mean() * 100, need))

    # The comparison only means something on entries every rung could actually
    # fill; otherwise a rung looks good for having skipped its hardest names.
    keys = None
    for rung in RUNGS:
        k = set(zip(frames[rung]["symbol"], frames[rung]["day"]))
        keys = k if keys is None else keys & k
    log("\n" + "-" * 78)
    log("MATCHED: the {:,} entries where every rung filled".format(len(keys)))
    log("-" * 78)
    log("\n  {:<7} {:>9} {:>11} {:>11} {:>7} {:>12} {:>9}".format(
        "rung", "premium", "if right", "if wrong", "win%", "break-even", "day-t"))
    log("  " + "-" * 70)
    matched = {}
    for rung in RUNGS:
        f = frames[rung]
        m = f[[(s, d) in keys for s, d in zip(f["symbol"], f["day"])]].copy()
        matched[rung] = m
        need, med_r, med_w, _ = hurdle(m)
        # Two arrays, not a frame and a column name -- the earlier call passed
        # the wrong shape, the bare except swallowed it, and every t read nan.
        t = day_t(m["ret"].values, m["day"].values)
        log("  {:<7} {:>8.2f} {:>10.1f}% {:>10.1f}% {:>6.1f}% {:>12} {:>+9.2f}".format(
            rung, m["cost"].median(), med_r, med_w,
            (m["ret"] > 0).mean() * 100, need, t))

    log("\n  'if right' is the median return when the stock did rise 5%; 'if wrong'")
    log("  when it did not. 'break-even' is the share of reads that must be right")
    log("  for the median trade to stop losing. Blind picking gives 48.4%.")

    base = matched[RUNGS[0]]
    _, _, _, need_atm = hurdle(base)
    log("\n" + "-" * 78)
    log("VERDICT")
    log("-" * 78)
    best, best_need = None, need_atm
    for rung in RUNGS[1:]:
        _, _, _, need = hurdle(matched[rung])
        if need == need and need_atm == need_atm and need < best_need:
            best, best_need = rung, need
    if best is None:
        log("\n  No rung beats ATM. The cheaper contract's bigger multiple when")
        log("  right does not cover what it gives up when the move is slow or")
        log("  partial, so the shipped model keeps the ATM strike.")
    else:
        log("\n  {} lowers the hurdle from {:.1%} to {:.1%}, worth {:.1f} points of"
            .format(best, need_atm, best_need, (need_atm - best_need) * 100))
        log("  skill the read no longer has to supply. Check the day-t above")
        log("  before acting on it -- a lower hurdle on a negative t is a")
        log("  payoff-shape artefact, not an edge.")

    with open(OUT, "w") as handle:
        handle.write("\n".join(LINES) + "\n")
    log("\nwritten to {}".format(os.path.basename(OUT)))


if __name__ == "__main__":
    main()
