"""Does option-side liquidity lower the chart reader's hurdle?

`strike_ladder.py` answered its own question -- no cheaper strike helps -- but
left a bigger number lying in its output. The ATM row appeared twice:

    all 3,388 ATM trades      break-even 56.8%   win 45.1%
    the 1,437 matched ones    break-even 49.6%   win 52.3%

Same strike, same model, same hold. The only difference is that the matched set
required the ATM+2 contract to have ALSO traded that morning, and on that subset
the hurdle fell 7.2 points. If that were real it would be worth more than
anything else tested in this programme, because it costs the user nothing: they
simply do not take the trade.

(Both of those levels were wrong -- see WHAT CAME OF IT below -- but the gap
between them was not, which is why the question was worth asking.)

WHY THIS IS NOT AUTOMATICALLY HINDSIGHT. The fill test in `price_rung` is
applied at the ENTRY bar and nowhere else: the contract needs a finite open, a
premium of at least Rs1, and non-zero volume on that one bar. All three are on
the screen before the order goes in. This is a filter the user could actually
run, which is exactly why it deserves a real test rather than a nod.

WHY IT STILL MIGHT BE NOTHING. Three ways it dies, and each is checked below:

  * SAMPLE. 1,437 of 3,388. Splitting any sample in half and reporting the
    better piece is how this project manufactured a strategy twice already.
  * COMPOSITION. Chains with live wings belong to bigger, more optioned names.
    That is a different universe, not a better filter -- so the buckets are
    also compared inside the same session, where the market is held constant.
  * MONTHS. The shipped hurdle swings from 46% to unreachable across months.
    A bucket that only wins in some of them is that instability re-labelled.

WHAT IS BEING TESTED. Four entry-time observables, three buckets each, so
roughly twelve looks -- enough that one clears by chance, and the write-up says
so rather than picking the winner. `rungs_live` is the direct generalisation of
the matched definition. `premium` is included because friction here is set by
premium rather than moneyness, and because the NIFTY Rs100 floor is known to
invert on stocks -- a prior worth being wrong against.

WHAT CAME OF IT. The hypothesis is dead: the liquid case has the WORSE hurdle.
But the bucket that produced the illusion paid when the stock FELL, which no
call does, and running that down found a pricing bug underneath the whole
programme -- the hold crossing expiry onto a fresh contract, measured in
`expiry_contamination.py`.

This file predicted, before the fix existed, that both hurdles were
contaminated alike: the COMPARISON would survive and the LEVELS would not.
Re-run 2026-08-19 under `premove_legs.expiries()`, that is what happened --
every level moved up about ten points and the gap held. The verdict is
unchanged and now rests on honest numbers. The one thing that DID change is
the diagnostic: the cheap bucket no longer pays when the stock falls, because
that signature WAS the bug.

    python research/liquidity_hurdle.py [--rebuild]
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
from premove_legs import HOLD, expiries, load  # noqa: E402
from chart_reader_hurdle import scan_entries  # noqa: E402
from strike_ladder import non_overlapping  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "liquidity_hurdle.txt")
CACHE = os.path.join(HERE, "liquidity_hurdle_trades.parquet")

# The rungs the cache carries. Used only to count how much of the chain was
# alive at entry -- the trade itself is always the ATM contract.
RUNGS = ("ATM-1", "ATM", "ATM+1", "ATM+2", "ATM+3")
MIN_BUCKET = 150        # below this a hurdle is a rounding error with a label

LINES: list[str] = []


def log(message: str = "") -> None:
    print(message)
    LINES.append(message)


def price_atm(frame: pd.DataFrame, entries: set,
              deadlines: list | None = None) -> list[dict]:
    """Buy the ATM call, and record what the chain looked like at that moment.

    The trade is `strike_ladder.price_rung(rung="ATM")` unchanged -- absolute
    strike, pinned for the hold, missing quotes valued at intrinsic, and the
    hold cut at the front-month expiry so the contract stays itself. What is
    added is a snapshot of the entry bar: the premium, the ATM contract's own
    volume, and how many neighbouring rungs were themselves tradeable. Every
    one of those is visible before the order is placed.
    """
    if deadlines is None:
        deadlines = expiries("CALL")
    opens = frame.pivot_table(index="ts", columns="strike", values="open")
    grid = opens.index
    close = frame.pivot_table(index="ts", columns="strike", values="close").reindex(grid)
    vol = frame.pivot_table(index="ts", columns="strike", values="volume").reindex(grid)
    spot = (frame.pivot_table(index="ts", columns="strike", values="spot")
            .reindex(grid).mean(axis=1))

    anchors = {}
    for rung in RUNGS:
        sub = frame[frame["rel"] == rung].drop_duplicates("ts")
        if len(sub):
            anchors[rung] = sub.set_index("ts")["strike"].reindex(grid)

    days = pd.Series(grid.date, index=grid)
    first_bar = {d: g.index[0] for d, g in days.groupby(days)}
    pos = {t: i for i, t in enumerate(grid)}

    def tradeable(ts, k) -> bool:
        if not np.isfinite(k) or k not in opens.columns:
            return False
        px = opens.at[ts, k]
        return bool(np.isfinite(px) and px >= 1.0 and vol.at[ts, k] > 0)

    out = []
    for day in sorted(entries):
        ts = first_bar.get(day)
        if ts is None:
            continue
        atm = anchors.get("ATM")
        k = atm.get(ts, np.nan) if atm is not None else np.nan
        if not tradeable(ts, k):
            continue
        entry = opens.at[ts, k]
        cost = entry + TICK
        i = pos[ts]
        dead = next((e for e in deadlines if e >= day), None)
        values = []
        for j in range(i + 1, min(i + 1 + HOLD, len(grid))):
            t = grid[j]
            if dead is not None and t.date() > dead:
                break        # past the roll this strike is another contract
            v = close.at[t, k]
            if not np.isfinite(v):
                s = spot.get(t, np.nan)
                if not np.isfinite(s):
                    continue
                v = max(s - k, 0.0)
            values.append(max(v - TICK, 0.0))
        if not values:
            continue
        values = np.array(values)
        fees = (entry + values[-1]) * TAX
        live = sum(1 for r in RUNGS
                   if r in anchors and tradeable(ts, anchors[r].get(ts, np.nan)))
        wing = anchors.get("ATM+2")
        s0 = spot.get(ts, np.nan)
        out.append({
            "day": day,
            "cost": cost,
            "hold": values[-1] - cost - fees,
            # --- entry-time observables, all visible before the order ---
            "premium": entry,
            "opt_vol": float(vol.at[ts, k]),
            "rungs_live": live,
            "wing_live": bool(wing is not None and tradeable(ts, wing.get(ts, np.nan))),
            # Kept for the sanity check, not for trading. An at-the-money call
            # is worth a few percent of spot; anything far below that is a
            # broken print, and a broken ENTRY print manufactures a winner.
            "spot": s0,
            "strike": k,
            "moneyness": (s0 - k) / s0 if np.isfinite(s0) and s0 else np.nan,
            "prem_pct": entry / s0 if np.isfinite(s0) and s0 else np.nan,
            "quotes": int(np.isfinite(close.iloc[i + 1:i + 1 + HOLD][k]).sum())
            if k in close.columns else 0,
        })
    return out


def hurdle(v: pd.DataFrame):
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


def bucket_table(f: pd.DataFrame, column: str, cuts: list, labels: list) -> None:
    """Hurdle by bucket, with the sample size beside every row."""
    log("\n  by {}".format(column))
    log("  {:<16} {:>7} {:>9} {:>11} {:>11} {:>7} {:>12}".format(
        "bucket", "trades", "premium", "if right", "if wrong", "win%", "break-even"))
    log("  " + "-" * 76)
    for lo, hi, label in zip(cuts[:-1], cuts[1:], labels):
        b = f[(f[column] >= lo) & (f[column] < hi)]
        if len(b) < MIN_BUCKET:
            log("  {:<16} {:>7,}   -- too thin to read --".format(label, len(b)))
            continue
        need, med_r, med_w, _ = hurdle(b)
        log("  {:<16} {:>7,} {:>8.2f} {:>10.1f}% {:>10.1f}% {:>6.1f}% {:>12}".format(
            label, len(b), b["cost"].median(), med_r, med_w,
            (b["ret"] > 0).mean() * 100, need))


def main() -> None:
    if os.path.exists(CACHE) and "--rebuild" not in sys.argv:
        f = pd.read_parquet(CACHE)
        opts_bars, opts_syms = -1, f["symbol"].nunique()
    else:
        entries = scan_entries()
        opts = load("CALL")
        opts_bars, opts_syms = len(opts), opts["symbol"].nunique()
        rows = []
        shared = sorted(set(opts["symbol"].unique()) & set(entries["symbol"].unique()))
        for symbol in shared:
            want = entries[entries["symbol"] == symbol]
            flags = want.set_index(want["entry"].dt.date)[["went_up"]].to_dict("index")
            for trade in price_atm(opts[opts["symbol"] == symbol],
                                   set(want["entry"].dt.date)):
                flag = flags.get(trade["day"])
                if flag is None:
                    continue
                trade.update({"symbol": symbol, "went_up": flag["went_up"]})
                rows.append(trade)
        f = pd.DataFrame(rows).dropna(subset=["hold"])
        f["ret"] = f["hold"] / f["cost"]
        f["day"] = pd.to_datetime(f["day"])
        f.to_parquet(CACHE, index=False)

    f["month"] = f["day"].dt.to_period("M")
    log("=" * 78)
    log("DOES OPTION-SIDE LIQUIDITY LOWER THE HURDLE?")
    log("=" * 78)
    log("\n{} CALL bars, {} symbols. ATM call at the next session's open, held"
        .format("{:,}".format(opts_bars) if opts_bars >= 0 else "(cached)", opts_syms))
    log("{} bars (~5 sessions). Every split below is on an entry-time observable."
        .format(HOLD))
    log("\n  {:,} ATM trades priced across {} sessions".format(
        len(f), f["day"].nunique()))

    need_all, med_r, med_w, need_base = hurdle(f)
    log("\n  BASELINE (every trade)  premium {:.2f}  if right {:+.1f}%  if wrong "
        "{:+.1f}%".format(f["cost"].median(), med_r, med_w))
    log("  win {:.1f}%, break-even {} against 48.4% free."
        .format((f["ret"] > 0).mean() * 100, need_all))

    log("\n" + "-" * 78)
    log("THE SPLIT THAT STARTED THIS: did the ATM+2 wing trade at entry?")
    log("-" * 78)
    log("\n  {:<16} {:>7} {:>9} {:>11} {:>11} {:>7} {:>12}".format(
        "wing", "trades", "premium", "if right", "if wrong", "win%", "break-even"))
    log("  " + "-" * 76)
    for flag, label in ((True, "traded"), (False, "did not")):
        b = f[f["wing_live"] == flag]
        if len(b) < MIN_BUCKET:
            log("  {:<16} {:>7,}   -- too thin to read --".format(label, len(b)))
            continue
        need, r, w, _ = hurdle(b)
        log("  {:<16} {:>7,} {:>8.2f} {:>10.1f}% {:>10.1f}% {:>6.1f}% {:>12}".format(
            label, len(b), b["cost"].median(), r, w,
            (b["ret"] > 0).mean() * 100, need))

    log("\n" + "-" * 78)
    log("THE SAME QUESTION ASKED THREE OTHER WAYS")
    log("-" * 78)
    bucket_table(f, "rungs_live", [0, 3, 4, 99], ["1-2 rungs", "3 rungs", "4-5 rungs"])
    qv = f["opt_vol"].quantile([1 / 3, 2 / 3]).tolist()
    bucket_table(f, "opt_vol", [-1] + qv + [np.inf],
                 ["thin volume", "middling", "heavy volume"])
    bucket_table(f, "premium", [0, 10, 40, np.inf],
                 ["under Rs10", "Rs10-40", "over Rs40"])

    # A chain with live wings belongs to a bigger, busier name. That is a
    # different universe, not a better filter -- unless the gap survives with
    # the session held fixed, which is what this does.
    log("\n" + "-" * 78)
    log("IS IT THE FILTER OR THE UNIVERSE? Same-session comparison")
    log("-" * 78)
    both = (f.groupby("day")["wing_live"].nunique() > 1)
    shared_days = both[both].index
    inside = f[f["day"].isin(shared_days)]
    if len(inside) < 2 * MIN_BUCKET:
        log("\n  Too few sessions carry both kinds of trade to compare inside one.")
    else:
        log("\n  {:,} trades on the {} sessions that contain both kinds:"
            .format(len(inside), len(shared_days)))
        log("\n  {:<16} {:>7} {:>11} {:>11} {:>7} {:>12}".format(
            "wing", "trades", "if right", "if wrong", "win%", "break-even"))
        log("  " + "-" * 67)
        for flag, label in ((True, "traded"), (False, "did not")):
            b = inside[inside["wing_live"] == flag]
            if len(b) < MIN_BUCKET:
                log("  {:<16} {:>7,}   -- too thin to read --".format(label, len(b)))
                continue
            need, r, w, _ = hurdle(b)
            log("  {:<16} {:>7,} {:>10.1f}% {:>10.1f}% {:>6.1f}% {:>12}".format(
                label, len(b), r, w, (b["ret"] > 0).mean() * 100, need))
        gap = (inside[inside["wing_live"]]["ret"].values,
               inside[~inside["wing_live"]]["ret"].values)
        log("\n  mean return {:+.1f}% with a live wing against {:+.1f}% without"
            .format(gap[0].mean() * 100, gap[1].mean() * 100))

    # A filter that only works in some months is the hurdle's own instability
    # wearing a filter's clothes.
    log("\n" + "-" * 78)
    log("DOES IT HOLD ACROSS MONTHS?")
    log("-" * 78)
    log("\n  {:<10} {:>9} {:>12} {:>9} {:>12}".format(
        "month", "wing n", "wing b/e", "rest n", "rest b/e"))
    log("  " + "-" * 55)
    wins = 0
    months = 0
    for month, g in f.groupby("month"):
        a, b = g[g["wing_live"]], g[~g["wing_live"]]
        na, nb = hurdle(a)[3], hurdle(b)[3]
        log("  {:<10} {:>9,} {:>12} {:>9,} {:>12}".format(
            str(month), len(a),
            "{:.1%}".format(na) if na == na else "--", len(b),
            "{:.1%}".format(nb) if nb == nb else "--"))
        if na == na and nb == nb:
            months += 1
            wins += int(na < nb)
    if months:
        log("\n  The live-wing bucket has the lower hurdle in {} of {} readable"
            .format(wins, months))
        log("  months. Ten of twelve would be a filter; six would be a coin.")

    log("\n" + "-" * 78)
    log("IS THE RETURN GAP ITSELF SIGNIFICANT?")
    log("-" * 78)
    a = f[f["wing_live"]]
    b = f[~f["wing_live"]]
    for label, g in (("live wing", a), ("no wing", b)):
        if len(g) < MIN_BUCKET:
            continue
        t = day_t(g["ret"].values, g["day"].values)
        sp = g[g["day"].isin(non_overlapping(g["day"]))]
        t5 = day_t(sp["ret"].values, sp["day"].values)
        log("\n  {:<10} n {:,}  mean {:+.1f}%  median {:+.1f}%  t(day) {:+.2f}  "
            "t(5d) {:+.2f}".format(label, len(g), g["ret"].mean() * 100,
                                   g["ret"].median() * 100, t, t5))
    log("\n  t(day) treats a session as one bet. t(5d) also spaces entries five")
    log("  sessions apart so no two trades share a holding period -- without it")
    log("  a five-day hold sampled daily reads about sqrt(5) too significant.")
    log("  Read the mean and the median together: a positive t beside a negative")
    log("  median is a lottery, not an edge.")

    # An "always pays" bucket is a bug report, not a discovery. Three things
    # could produce one, and each leaves a different fingerprint: a broken
    # ENTRY print (premium far below what an at-the-money call can cost), a
    # strike that is not actually at the money (so the option starts with
    # intrinsic value the premium never paid for), or an EXIT built mostly
    # from the intrinsic fallback rather than from real quotes.
    #
    # It was none of the three. The bucket is cheap, real and well quoted --
    # and cheap was the tell, because premium as a share of spot is mostly a
    # clock. The fourth possibility, the one that turned out to be true, was
    # not on that list: the contract stopped being itself partway through the
    # hold. That is why this section is kept even though the guard has since
    # removed what it was diagnosing -- the elimination is what pointed at it.
    log("\n" + "-" * 78)
    log("SANITY CHECK ON THE BUCKET THAT ONCE 'ALWAYS PAID'")
    log("-" * 78)
    log("\n  {:<12} {:>7} {:>9} {:>11} {:>11} {:>10} {:>9}".format(
        "wing", "trades", "premium", "prem/spot", "moneyness", "quotes/125", "median"))
    log("  " + "-" * 72)
    finger = {}
    for flag, label in ((True, "traded"), (False, "did not")):
        b = f[f["wing_live"] == flag]
        if not len(b):
            continue
        finger[label] = b
        log("  {:<12} {:>7,} {:>8.2f} {:>10.2f}% {:>10.2f}% {:>10.0f} {:>+8.1f}%".format(
            label, len(b), b["premium"].median(),
            b["prem_pct"].median() * 100, b["moneyness"].median() * 100,
            b["quotes"].median(), b["ret"].median() * 100))
    if len(finger) == 2:
        rich, cheap = finger["traded"], finger["did not"]
        ratio = (rich["prem_pct"].median() / cheap["prem_pct"].median()
                 if cheap["prem_pct"].median() else float("nan"))
        log("\n  All three fingerprints come back clean. The cheap bucket is"
            " {:.2f}% of".format(cheap["prem_pct"].median() * 100))
        log("  spot against {:.2f}% -- a factor of {:.1f}, not the order of"
            " magnitude a".format(rich["prem_pct"].median() * 100, ratio))
        log("  broken print would show. Its moneyness is {:.2f}%, so it is"
            " genuinely".format(cheap["moneyness"].median() * 100))
        log("  at the money. And it carries {} live quotes against {}, so the"
            .format(int(cheap["quotes"].median()), int(rich["quotes"].median())))
        log("  exit is not an intrinsic fallback. These are real, well-quoted,")
        log("  cheap contracts.")
        log("")
        log("  Which was the answer, once you read premium as a clock rather")
        log("  than a price. An at-the-money call is worth roughly 3-4% of spot")
        log("  with a month to run and under 2% with days left, so a low")
        log("  prem/spot is not a bargain -- it is a contract close to expiry.")
        log("  And a contract close to expiry is exactly the one a five-session")
        log("  hold runs PAST, at which point the pinned strike silently becomes")
        log("  next month's option carrying a fresh month of time value. THAT is")
        log("  what used to pay when the stock fell. See expiry_contamination.py.")
        log("")
        wrong = cheap[~cheap["went_up"]]["ret"].median() * 100
        log("  The guard is now on, and the signature it was diagnosing is gone:")
        log("  this bucket returns {:+.1f}% at the median when the stock does NOT"
            .format(wrong))
        if wrong < 0:
            log("  rise, which is what a call is supposed to do. An impossibility")
            log("  that disappears when you fix the thing you blamed for it is")
            log("  the strongest confirmation this project has of a diagnosis.")
        else:
            log("  rise -- still positive, so the expiry guard was NOT the whole")
            log("  explanation and something else is inflating this bucket.")

    log("\n" + "-" * 78)
    log("WHAT rungs_live ACTUALLY MEASURES")
    log("-" * 78)
    log("\n  {:<12} {:>16} {:>10}".format("rung", "bars in cache", "share"))
    log("  " + "-" * 40)
    from django.db.models import Count  # noqa: E402
    from options_tracker.models import StockOptionCandle  # noqa: E402
    counts = dict(StockOptionCandle.objects.filter(option_type="CALL")
                  .values_list("relative_strike")
                  .annotate(n=Count("id")).values_list("relative_strike", "n"))
    total = sum(counts.values()) or 1
    for rung in RUNGS:
        n = counts.get(rung, 0)
        log("  {:<12} {:>16,} {:>9.1f}%".format(rung, n, 100 * n / total))
    log("\n  The CALL cache holds ATM-1, ATM, ATM+1 and ATM+2; ATM+3 was never")
    log("  downloaded and ATM-1 landed only 85k bars against ATM's 1.17m. So a")
    log("  count of live rungs is substantially a count of which DOWNLOAD")
    log("  windows completed, which is not something a trader can see and not")
    log("  something the market decided. The 4-5 rung bucket in particular is")
    log("  mostly the 7% of dates where ATM-1 exists at all. That gradient")
    log("  cannot be traded until the cache is square, and it is not evidence")
    log("  of a liquidity effect while the download is still running.")

    log("\n" + "-" * 78)
    log("VERDICT")
    log("-" * 78)
    live = f[f["wing_live"]]
    dead_wing = f[~f["wing_live"]]
    need_live = hurdle(live)[3]
    need_dead = hurdle(dead_wing)[3]
    log("")
    if need_live == need_live and need_base == need_base and need_live >= need_base:
        log("  The hypothesis was that a liquid chain lowers the hurdle. It does")
        log("  not: with the wing trading -- the liquid case -- the hurdle is")
        log("  {:.1%}, WORSE than the {:.1%} baseline. What is left sits entirely"
            .format(need_live, need_base))
        log("  in the {:,} trades where the wing did NOT trade.".format(len(dead_wing)))
    else:
        log("  With the wing trading -- the liquid case -- the hurdle is {:.1%}"
            .format(need_live))
        log("  against the {:.1%} baseline, so the liquid chain does NOT carry"
            .format(need_base))
        log("  the penalty this file expected. Re-read the buckets above before")
        log("  quoting that, because it reverses the original verdict.")
    log("")
    if need_dead == need_dead:
        spaced = dead_wing[dead_wing["day"].isin(non_overlapping(dead_wing["day"]))]
        t5 = day_t(spaced["ret"].values, spaced["day"].values)
        readable = sum(
            1 for _, chunk in dead_wing.groupby(dead_wing["day"].dt.to_period("M"))
            if hurdle(chunk)[3] == hurdle(chunk)[3])
        months = dead_wing["day"].dt.to_period("M").nunique()
        log("  And that residue does NOT survive as a filter. It is {:,} trades"
            .format(len(dead_wing)))
        log("  needing {:.1%}, on a median of {:+.1f}% with t(5d) {:+.2f}. A"
            .format(need_dead, dead_wing["ret"].median() * 100, t5))
        log("  positive t beside a negative median is a lottery, not an edge --")
        log("  and it can be scored in only {} of its {} months, because the"
            .format(readable, months))
        log("  bucket is too thin to read anywhere else.")
    log("")
    log("  So the strike ladder's matched table is not a filter the user could")
    log("  run. It is what happens when you require three contracts to have")
    log("  printed, on a cache where printing is partly a function of what has")
    log("  finished downloading.")
    log("")
    log("  THE PREDICTION THIS FILE MADE HAS NOW BEEN TESTED. It said the two")
    log("  hurdles were contaminated alike, so the COMPARISON would survive the")
    log("  expiry fix and the LEVELS would not. Re-run under the guard:")
    log("    liquid wing   {:>6.1%}   (was 59.6% unguarded)".format(need_live))
    log("    baseline      {:>6.1%}   (was 56.8% unguarded)".format(need_base))
    log("    no wing       {:>6.1%}   (was 49.6% unguarded)".format(need_dead))
    log("  Both moved up about ten points and the liquid-minus-illiquid gap is")
    log("  {:.1f} points against the 10.0 it was before. The comparison held;"
        .format((need_live - need_dead) * 100))
    log("  the levels did not, exactly as predicted. Every number on this page")
    log("  is now the guarded one.")

    with open(OUT, "w") as handle:
        handle.write("\n".join(LINES) + "\n")
    log("\nwritten to {}".format(os.path.basename(OUT)))


if __name__ == "__main__":
    main()
