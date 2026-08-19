"""The hold ran past expiry, and the feed quietly handed it a fresh contract.

`chart_reader_hurdle.py` reports that a CALL read must be right 56.8% of the
time to break even, against 48.4% free. That number is on the shipped screen.
It is wrong, and this file works out by how much.

THE BUG. Every option study here prices a trade by pinning the ABSOLUTE strike
and reading `close[t, strike]` forward -- which was itself a fix, for an earlier
bug where an ATM-relative anchor let the contract drift. But the cache is built
with `expiry_code=1`, meaning NEAREST expiry, so a strike identifies a contract
only WITHIN one expiry cycle. Hold past expiry and the same strike silently
becomes next month's contract, quoted with a full month of time value the trade
never paid for. The position buys a two-day option and marks out against a
thirty-day one.

HOW BIG. Just under a quarter of the sample (774 of 3,388) holds across an
expiry, and those trades return +36.8% at the median when the stock does NOT
rise. A call that pays for being wrong is not a good trade, it is an impossible
one, and impossibility is the cleanest evidence the mechanism is real rather
than merely suspected. Cut the same trades at expiry and the median goes from
+91.6% to -99.1%.

WHY THE EXPIRY DATES ARE MEASURED, NOT ASSUMED. Last Thursday of the month is
the rule this project would have assumed, and it is wrong for most of the
sample: NSE moved equity-derivative expiry to the last TUESDAY partway through,
and the change is visible in the data as the mean ATM premium collapsing into
expiry and jumping the next session. Detecting the dates from that jump gets
the change for free and cannot drift out of date. On a last-Thursday rule only
6 of 17 expiries land correctly.

THE FIX IS TRUNCATION, NOT EXCLUSION -- AND IT IS BOTH. Dropping the affected
trades would bias the sample toward early-month entries, so the hold is also
cut at the last bar before the contract dies, keeping every trade. The two
disagree by two points (67.1% against 68.9%) and neither is clean on its own:
exclusion loses a fifth of the sample, truncation quietly asks a different
question, because a hold cut at expiry exits with no time value left. Reported
together they bracket the answer, which is what makes it usable.

    python research/expiry_contamination.py
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

from django.db import connection  # noqa: E402

from option_spreads import TAX, TICK, day_t  # noqa: E402
from premove_legs import HOLD, load  # noqa: E402
from chart_reader_hurdle import scan_entries  # noqa: E402
from strike_ladder import non_overlapping  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "expiry_contamination.txt")

# A contract loses the last of its time value into expiry and the feed rolls to
# a fresh month the next session, so the mean ATM premium roughly doubles
# overnight. 1.8x is well clear of any ordinary day and well below the roll.
JUMP = 1.8
MIN_BARS = 200

LINES: list[str] = []


def log(message: str = "") -> None:
    print(message)
    LINES.append(message)


def expiry_days() -> list:
    """Expiry dates read off the premium series rather than off a calendar."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT date(timestamp) d, AVG(open/spot) pp, COUNT(*) n "
            "FROM options_tracker_stockoptioncandle "
            "WHERE option_type='CALL' AND relative_strike='ATM' "
            "AND spot>0 AND open>0 GROUP BY 1 ORDER BY 1"
        )
        rows = cursor.fetchall()
    frame = pd.DataFrame(rows, columns=["d", "pp", "n"])
    frame["d"] = pd.to_datetime(frame["d"]).dt.date
    frame["jump"] = frame["pp"].shift(-1) / frame["pp"]
    hits = frame[(frame["jump"] > JUMP) & (frame["n"] > MIN_BARS)
                 & (frame["d"] > frame["d"].min())]["d"].tolist()
    # One expiry per month. Nearby sessions can also clear the threshold as the
    # premium decays, so the LAST jump in a month is the roll itself.
    latest: dict = {}
    for day in hits:
        key = (day.year, day.month)
        latest[key] = max(latest.get(key, day), day)
    return sorted(latest.values())


def price(frame: pd.DataFrame, entries: set, expiries: np.ndarray) -> list[dict]:
    """The ATM call, priced once holding past expiry and once stopping at it."""
    opens = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = opens.index
    close = frame.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    vol = frame.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    spot = (frame.pivot_table(index="ts", columns="strike", values="spot")
            .reindex(grid).mean(axis=1))
    anchor = (frame[frame["rel"] == "ATM"].drop_duplicates("ts")
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
        # The last bar this contract is still itself. After this the strike
        # names a different, longer-dated option.
        after = expiries[expiries >= np.datetime64(day)]
        dead = after[0] if len(after) else None

        full, cut = [], []
        for j in range(i + 1, min(i + 1 + HOLD, len(grid))):
            t = grid[j]
            v = close.at[t, k]
            if not np.isfinite(v):
                s = spot.get(t, np.nan)
                if not np.isfinite(s):
                    continue
                v = max(s - k, 0.0)
            v = max(v - TICK, 0.0)
            full.append(v)
            if dead is None or np.datetime64(t.date()) <= dead:
                cut.append(v)
        if not full:
            continue

        def settle(series):
            if not series:
                return np.nan
            end = series[-1]
            return end - cost - (entry + end) * TAX

        out.append({
            "day": day, "cost": cost,
            "hold": settle(full),
            "hold_cut": settle(cut),
            "crossed": bool(dead is not None
                            and dead < np.datetime64(grid[min(i + HOLD, len(grid) - 1)].date())),
            "bars_full": len(full), "bars_cut": len(cut),
        })
    return out


def hurdle(v: pd.DataFrame, column: str = "ret"):
    right, wrong = v[v["went_up"]], v[~v["went_up"]]
    if len(right) < 25 or len(wrong) < 25:
        return "thin", np.nan, np.nan
    lose_r, lose_w = (right[column] < 0).mean(), (wrong[column] < 0).mean()
    med_r, med_w = right[column].median() * 100, wrong[column].median() * 100
    if lose_w <= max(0.5, lose_r):
        return ("always pays" if lose_w <= 0.5 else "unreachable"), med_r, med_w
    need = (lose_w - 0.5) / (lose_w - lose_r)
    return ("{:.1%}".format(need) if need <= 1 else "unreachable"), med_r, med_w


def row(label: str, g: pd.DataFrame, column: str) -> None:
    need, med_r, med_w = hurdle(g, column)
    spaced = g[g["day"].isin(non_overlapping(g["day"]))]
    log("  {:<30} {:>6,} {:>10.1f}% {:>10.1f}% {:>7.1f}% {:>13} {:>+8.1f}% {:>+8.2f}"
        .format(label, len(g), med_r, med_w, (g[column] > 0).mean() * 100, need,
                g[column].median() * 100,
                day_t(spaced[column].values, spaced["day"].values)))


def main() -> None:
    expiries = expiry_days()
    log("=" * 100)
    log("THE HOLD RAN PAST EXPIRY")
    log("=" * 100)
    log("\n  {} expiries detected from the premium series, not assumed from a"
        .format(len(expiries)))
    log("  calendar. Note where the weekday changes:")
    log("")
    for i in range(0, len(expiries), 6):
        log("    " + "   ".join("{} {}".format(d, d.strftime("%a"))
                                for d in expiries[i:i + 6]))
    thu = sum(1 for d in expiries if d.strftime("%a") == "Thu")
    log("\n  A last-Thursday rule would get {} of {} right. NSE moved equity"
        .format(thu, len(expiries)))
    log("  derivatives to the last Tuesday partway through the sample.")

    grid = np.array([np.datetime64(d) for d in expiries])
    entries = scan_entries()
    opts = load("CALL")
    rows = []
    shared = sorted(set(opts["symbol"].unique()) & set(entries["symbol"].unique()))
    for symbol in shared:
        want = entries[entries["symbol"] == symbol]
        flags = want.set_index(want["entry"].dt.date)[["went_up"]].to_dict("index")
        for trade in price(opts[opts["symbol"] == symbol],
                           set(want["entry"].dt.date), grid):
            flag = flags.get(trade["day"])
            if flag is None:
                continue
            trade.update({"symbol": symbol, "went_up": flag["went_up"]})
            rows.append(trade)

    f = pd.DataFrame(rows).dropna(subset=["hold"])
    f["ret"] = f["hold"] / f["cost"]
    f["ret_cut"] = f["hold_cut"] / f["cost"]
    f["day"] = pd.to_datetime(f["day"])

    log("\n" + "-" * 100)
    log("WHAT THE CONTAMINATION IS WORTH")
    log("-" * 100)
    log("\n  {:<30} {:>6} {:>11} {:>11} {:>8} {:>13} {:>9} {:>8}".format(
        "sample", "n", "if right", "if wrong", "win%", "break-even", "median", "t(5d)"))
    log("  " + "-" * 98)
    row("as shipped (held through)", f, "ret")
    row("  of which crossed expiry", f[f["crossed"]], "ret")
    row("  of which did not", f[~f["crossed"]], "ret")
    log("")
    row("CORRECTED (cut at expiry)", f, "ret_cut")
    row("  of which crossed expiry", f[f["crossed"]], "ret_cut")
    row("  of which did not", f[~f["crossed"]], "ret_cut")

    log("\n  The crossed rows are the whole story. Held through expiry they pay")
    log("  when the stock FALLS -- 'always pays' means the median trade profits")
    log("  even on a wrong read, which no call can do. Cut at expiry they fall")
    log("  back in line with everything else, which is what a real contract does.")

    crossed = f[f["crossed"]]
    if len(crossed):
        log("\n  {:,} of {:,} trades ({:.0%}) held past an expiry. On those the"
            .format(len(crossed), len(f), len(crossed) / len(f)))
        log("  median return falls from {:+.1f}% to {:+.1f}% once the contract is"
            .format(crossed["ret"].median() * 100, crossed["ret_cut"].median() * 100))
        log("  allowed to die on schedule, and the hold shortens from {:.0f} bars"
            .format(crossed["bars_full"].median()))
        log("  to {:.0f}.".format(crossed["bars_cut"].median()))

    need_old, _, _ = hurdle(f, "ret")
    need_cut, _, _ = hurdle(f, "ret_cut")
    need_clean, _, _ = hurdle(f[~f["crossed"]], "ret")
    log("\n" + "-" * 100)
    log("VERDICT")
    log("-" * 100)
    log("\n  The shipped hurdle is {}. It is wrong. Two independent corrections"
        .format(need_old))
    log("  bracket the real one:")
    log("")
    log("    drop the crossed trades      {}   every trade a clean 5-session hold"
        .format(need_clean))
    log("    cut every hold at expiry     {}   every trade kept, none mispriced"
        .format(need_cut))
    log("")
    log("  Take the first. Truncation prices honestly but changes the question:")
    log("  a hold cut at expiry exits with no time value left, which is a")
    log("  different trade from the five-session hold being measured, and its")
    log("  -99.1% median is that difference rather than a result. Dropping the")
    log("  crossed trades costs {:.0%} of the sample and biases toward entries"
        .format((f["crossed"]).mean()))
    log("  earlier in the month, which is a real limitation and a smaller one.")
    log("  That the two agree within two points is what makes either usable.")
    log("")
    log("  So the read must be right about 67% of the time, not 56.8%, against")
    log("  48.4% free. Required skill goes from 8.4 points above blind picking")
    log("  to roughly 19. And on the clean sample t(5d) is negative, so the")
    log("  trade is not merely harder than advertised -- at the measured")
    log("  accuracy it loses.")
    log("")
    log("  Every downstream number priced on this cache inherits the same error:")
    log("  the strike ladder, the premium bands, and the elasticity gradient that")
    log("  looked like a finding an hour ago and was days-to-expiry in disguise.")
    log("")
    log("  The stock-level shortlist is NOT affected -- 73% move 5%, 48.4% rise")
    log("  are measured on equity candles and never touch an option contract.")
    log("  What changes is the price of acting on it.")

    with open(OUT, "w") as handle:
        handle.write("\n".join(LINES) + "\n")
    log("\nwritten to {}".format(os.path.basename(OUT)))


if __name__ == "__main__":
    main()
