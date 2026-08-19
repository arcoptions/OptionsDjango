"""Does "you find the movers, I read the chart" pay?

The user's proposal, and it is a fair one: I supply a daily shortlist of stocks
likely to travel 5-10% in either direction, they read the chart to choose the
side, and they buy the option. Direction is the part every model here has failed
at, so handing it to a human is a reasonable response to the evidence rather
than a way around it.

WHAT THIS MEASURES, AND WHY IT IS THE RIGHT TEST. Their chart-reading skill is
not in the data and cannot be simulated. What CAN be measured is the payoff
table it has to work against:

  * buy the CALL on names that went on to rise 5% -- a PERFECT up-call
  * buy the CALL on names that did not -- a wrong call
  * the same pair for puts

The first bucket is the ceiling. No chart reader beats "knew the direction in
advance", so if that bucket does not pay, no amount of skill rescues the trade.
If it does pay, the gap between the two buckets gives the accuracy needed to
break even, which is a number the user can judge themselves against.

THE TRAP THIS IS BUILT TO AVOID. `sum(pnl)/sum(cost)` has manufactured a
strategy twice in this project already. Every figure below is reported per
SESSION as well as pooled, and the two are compared explicitly, because option
winners cluster -- one market-wide rally pays hundreds of calls at once.

THE ENTRY POPULATION IS THE SCAN, NOT THE MODEL. `atr_pct > 4%` is what the
Chartink scan encodes and what the user would actually run. Using the model
here would price a shortlist they cannot reproduce.

THE HURDLE MOVED ON 2026-08-19, from 56.8% to 68.9%, and the reason was a
pricing bug rather than a change of method. `premove_legs.run_symbol` pinned the
STRIKE but not the CONTRACT, on a nearest-expiry cache: a five-session hold that
ran past a roll was marked out against next month's option at the same strike,
carrying a month of time value the trade never paid for. It is now cut at the
roll and every option figure below is the corrected one. Reading the old .txt
beside this one will disagree everywhere; the old one is wrong. The shipped
constant is 67.1%, which drops the crossing trades instead of truncating them --
the two disagree by two points and that bracket is what makes either usable.
See `expiry_contamination.py`.

Writes research/chart_reader_hurdle.txt.
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

from premove_legs import load, run_symbol  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = os.path.join(HERE, "premove_features.parquet")
OUT = os.path.join(HERE, "chart_reader_hurdle.txt")
CACHE = os.path.join(HERE, "chart_reader_trades.parquet")

ATR_CUT = 0.040     # the scan, as emitted in chartink_scanner.py
MOVE = 0.05         # "moved 5%" -- the user's own target

LINES: list[str] = []

# P(the stock rises 5%) over the whole ENTRY population, set once in main().
# Every "against the X% a coin gives" in the report reads from here, because
# quoting the trade-level rate in one section and the entry-level rate in
# another puts two different free rates in one document.
FREE_UP = float("nan")


def log(message: str = "") -> None:
    print(message)
    LINES.append(message)


def scan_entries() -> pd.DataFrame:
    """Scan rows, with the outcome attached and the entry set to the NEXT session.

    The scan reads the close of day D, so the earliest tradeable entry is the
    open of D+1. The label runs from D's close, which leaves the option about
    four of the five labelled sessions -- the honest version of the trade, not
    the one that enters at a price it could not have got.
    """
    frame = pd.read_parquet(FEATURES).sort_values(["symbol", "day"])
    frame["day"] = pd.to_datetime(frame["day"])
    sessions = np.array(sorted(frame["day"].unique()))
    nxt = dict(zip(sessions[:-1], sessions[1:]))
    frame["entry"] = frame["day"].map(nxt)

    hit = frame[(frame["atr_pct"] > ATR_CUT)].dropna(
        subset=["up5", "dn5", "entry", "up_max", "dn_max"]).copy()
    hit["went_up"] = (hit["up5"] > 0)
    hit["went_dn"] = (hit["dn5"] > 0)
    return hit


def price(side: str, entries: pd.DataFrame) -> pd.DataFrame:
    """Buy one ATM contract per symbol-entry, reusing the pinned-strike loader."""
    opts = load(side)
    log("  {:,} {} bars, {} symbols".format(len(opts), side, opts["symbol"].nunique()))
    rows = []
    shared = sorted(set(opts["symbol"].unique()) & set(entries["symbol"].unique()))
    for symbol in shared:
        want = entries[entries["symbol"] == symbol]
        by_date = {d.date(): d for d in want["entry"]}
        flags = want.set_index(want["entry"].dt.date)[
            ["went_up", "went_dn"]].to_dict("index")
        for trade in run_symbol(opts[opts["symbol"] == symbol],
                                set(by_date), side):
            f = flags.get(trade["day"])
            if f is None:
                continue
            trade.update({"symbol": symbol, "side": side,
                          "went_up": f["went_up"], "went_dn": f["went_dn"]})
            rows.append(trade)
    return pd.DataFrame(rows)


def bucket(trades: pd.DataFrame, name: str) -> dict | None:
    """Pooled AND per-session, side by side, because they disagree on this data."""
    v = trades.dropna(subset=["hold"])
    if len(v) < 40:
        log("  {:<34} only {} trades -- not reported".format(name, len(v)))
        return None
    v = v.copy()
    v["ret"] = v["hold"] / v["cost"]
    v["ret_best"] = v["best"] / v["cost"]
    per_day = v.groupby("day")["ret"].mean()
    row = {
        "name": name, "n": len(v),
        "pooled": v["hold"].sum() / v["cost"].sum() * 100,
        "mean": v["ret"].mean() * 100,
        "median": v["ret"].median() * 100,
        "session_median": per_day.median() * 100,
        "win": (v["hold"] > 0).mean() * 100,
        "best": v["ret_best"].median() * 100,
    }
    log("  {:<34} {:>6,} {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>7.1f}% {:>9.1f}%".format(
        name, row["n"], row["pooled"], row["mean"], row["median"],
        row["session_median"], row["win"], row["best"]))
    return row


def main() -> None:
    log("=" * 108)
    log("DOES 'YOU FIND THE MOVERS, I PICK THE SIDE' PAY?")
    log("=" * 108)
    entries = scan_entries()
    global FREE_UP
    FREE_UP = float(entries["went_up"].mean())
    log("scan = ATR% > {:.0%}; {:,} signals, {} symbols, {} sessions".format(
        ATR_CUT, len(entries), entries["symbol"].nunique(),
        entries["day"].nunique()))
    log("of these, {:.1%} rose {:.0%}+ and {:.1%} fell {:.0%}+ within 5 sessions".format(
        entries["went_up"].mean(), MOVE, entries["went_dn"].mean(), MOVE))

    frames = []
    for side in ("CALL", "PUT"):
        log("")
        log("loading {}...".format(side))
        frames.append(price(side, entries))
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    if trades.empty:
        log("no trades priced -- option cache does not cover the scan names")
        return

    log("")
    log("{:,} option trades priced".format(len(trades)))
    log("")
    log("  {:<34} {:>6} {:>10} {:>10} {:>10} {:>10} {:>8} {:>10}".format(
        "bucket", "n", "pooled", "mean", "median", "SESS med", "win%", "best exit"))
    log("  " + "-" * 104)

    calls = trades[trades["side"] == "CALL"]
    puts = trades[trades["side"] == "PUT"]
    rows = {}
    rows["call_right"] = bucket(calls[calls["went_up"]], "CALL, stock DID rise 5% (perfect)")
    rows["call_wrong"] = bucket(calls[~calls["went_up"]], "CALL, stock did not")
    rows["call_all"] = bucket(calls, "CALL, every scan name (no skill)")
    log("")
    rows["put_right"] = bucket(puts[puts["went_dn"]], "PUT, stock DID fall 5% (perfect)")
    rows["put_wrong"] = bucket(puts[~puts["went_dn"]], "PUT, stock did not")
    rows["put_all"] = bucket(puts, "PUT, every scan name (no skill)")

    # The hurdle. NOT a weighted average of the two buckets' medians -- medians
    # do not combine linearly, and doing it that way gives 43.3% against a true
    # 56.8%, i.e. it flatters the trade into looking free. The median of the
    # MIXED population turns positive exactly when its loss probability falls
    # below one half, and that does combine linearly:
    #
    #     p * P(lose | right) + (1 - p) * P(lose | wrong) = 0.5
    #
    # A resample confirms the closed form and prints what each accuracy pays.
    log("")
    log("THE HURDLE -- how often the read must be right just to break even")
    log("")
    rng = np.random.default_rng(7)
    for tag, right, wrong, natural in [
            ("CALL", calls[calls["went_up"]], calls[~calls["went_up"]],
             entries["went_up"].mean()),
            ("PUT", puts[puts["went_dn"]], puts[~puts["went_dn"]],
             entries["went_dn"].mean())]:
        r = right.dropna(subset=["hold"]).copy()
        w = wrong.dropna(subset=["hold"]).copy()
        if len(r) < 40 or len(w) < 40:
            continue
        r["ret"] = r["hold"] / r["cost"]
        w["ret"] = w["hold"] / w["cost"]
        lose_r, lose_w = (r["ret"] < 0).mean(), (w["ret"] < 0).mean()
        if lose_w <= 0.5:
            log("  {}: loses less than half the time even when wrong".format(tag))
            continue
        need = (lose_w - 0.5) / (lose_w - lose_r)
        if need > 1:
            log("  {}  is UNREACHABLE: it needs {:.1%} of reads correct, i.e. more"
                .format(tag, need))
            log("     than all of them. Even a perfect read has a {:+.1%} median."
                .format(r["ret"].median()))
        else:
            log("  {}  needs {:.1%} of reads correct; picking at random gives {:.1%}"
                .format(tag, need, natural))
        log("     (loses {:.1%} of the time when right, {:.1%} when wrong)"
            .format(lose_r, lose_w))
        R, W = r["ret"].values, w["ret"].values
        log("     {:>9} {:>9} {:>9} {:>7}".format("accuracy", "median", "mean", "win%"))
        # `need` can exceed 1 on a leg that loses even when right, so the grid is
        # clamped rather than assumed to contain a reachable break-even.
        grid = sorted({0.40, round(natural, 3), 0.55, 0.60, 0.70}
                      | ({round(need, 3)} if need <= 1 else set()))
        for acc in grid:
            n = len(R) + len(W)
            k = int(n * acc)
            meds, means, wins = [], [], []
            for _ in range(200):
                s = np.concatenate([rng.choice(R, k, replace=True),
                                    rng.choice(W, n - k, replace=True)])
                meds.append(np.median(s))
                means.append(s.mean())
                wins.append((s > 0).mean())
            note = ""
            if abs(acc - round(natural, 3)) < 1e-9:
                note = "  <- random pick"
            elif abs(acc - round(need, 3)) < 1e-9:
                note = "  <- break-even"
            log("     {:>9.1%} {:>9.1%} {:>9.1%} {:>7.1%}{}".format(
                acc, np.mean(meds), np.mean(means), np.mean(wins), note))
        log("")
    log("  The mean column stays positive at every accuracy INCLUDING random,")
    log("  which is the fat-tail trap this project has hit twice. Read the")
    log("  median: it is the trade you will actually experience most weeks.")

    regime(calls, entries)

    trades.to_parquet(CACHE, index=False)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("\nwrote {}".format(OUT))


def hurdle_of(calls: pd.DataFrame) -> tuple[str, str, int]:
    """The break-even accuracy for one slice, already formatted.

    Three outcomes have to stay distinct or the table lies: a real hurdle, a
    hurdle above 100% (no read is good enough), and too few trades to say
    anything. The last is a gap in the option cache, not a verdict.
    """
    v = calls.dropna(subset=["hold"]).copy()
    v["ret"] = v["hold"] / v["cost"]
    r, w = v[v["went_up"]], v[~v["went_up"]]
    if len(r) < 25 or len(w) < 25:
        return "thin", "n/a", len(v)
    med_right = "{:+.1f}%".format(r["ret"].median() * 100)
    lose_r, lose_w = (r["ret"] < 0).mean(), (w["ret"] < 0).mean()
    if lose_w <= 0.5:
        return "always pays", med_right, len(v)
    if lose_w <= lose_r:
        return "unreachable", med_right, len(v)
    need = (lose_w - 0.5) / (lose_w - lose_r)
    return ("{:.1%}".format(need) if need <= 1 else "unreachable"), med_right, len(v)


def regime(calls: pd.DataFrame, entries: pd.DataFrame) -> None:
    """Is one hurdle number even the right shape of answer?

    56.8% is an average over 18 months, and an average is only useful if the
    thing is stable. It is not. Splitting by month asks whether the user is
    being handed a skill target or a market-timing bet in disguise.
    """
    log("")
    log("=" * 108)
    log("IS THE HURDLE STABLE? (the same calculation, month by month)")
    log("=" * 108)
    v = calls.dropna(subset=["hold"]).copy()
    v["ret"] = v["hold"] / v["cost"]
    v["month"] = pd.to_datetime(v["day"]).dt.to_period("M")
    log("")
    log("  {:<10} {:>7} {:>14} {:>18}".format(
        "month", "trades", "break-even", "median if PERFECT"))
    log("  " + "-" * 52)
    scored, unreachable, perfect_loses = [], 0, []
    for month, chunk in v.groupby("month"):
        need, med_right, n = hurdle_of(chunk)
        log("  {:<10} {:>7,} {:>14} {:>18}".format(str(month), n, need, med_right))
        if need == "unreachable":
            unreachable += 1
        if need.endswith("%"):
            scored.append(float(need.rstrip("%")))
        if med_right.startswith("-"):
            perfect_loses.append(str(month))
    log("")
    # Narrated from the table above rather than typed beside it. An earlier
    # version quoted "18% to unreachable" from a run whose best month was an
    # expiry artefact; once holds were cut at the roll the floor moved to 46%
    # and the sentence was left contradicting its own table.
    log("  'thin' is a hole in the option cache, not a verdict. Of the {} months"
        .format(len(scored) + unreachable))
    log("  that can be scored the hurdle runs {:.0f}% to {}, and in {}".format(
        min(scored), "unreachable" if unreachable else "{:.0f}%".format(max(scored)),
        ", ".join(perfect_loses) if perfect_loses else "no month"))
    log("  even a perfect read lost money."
        if perfect_loses else "  a perfect read always paid.")

    log("")
    log("  Splitting the same data by how the broad tape behaved makes the")
    log("  mechanism explicit:")
    log("")
    up_rate = v.groupby("day")["went_up"].mean()
    med = up_rate.median()
    for tag, days in [
            ("days when MORE scan names rose than usual", up_rate[up_rate > med].index),
            ("days when FEWER rose", up_rate[up_rate <= med].index)]:
        need, med_right, n = hurdle_of(v[v["day"].isin(set(days))])
        log("  {:<44} {:>6,} trades  break-even {}".format(tag, n, need))
    log("")
    log("  Read that as: the read has to be far better on exactly the days it is")
    log("  hardest to be right. Most of what this trade wins or loses is the")
    log("  market's direction, not the individual chart's.")
    log("")
    log("  BUT THAT SPLIT IS NOT A RULE. It is cut on how the trades THEMSELVES")
    log("  turned out, so it explains the mechanism and cannot be applied in")
    log("  advance. The tradeable version is below.")

    tradeable_breadth(v)


def tradeable_breadth(calls: pd.DataFrame) -> None:
    """Can the regime be called BEFORE the trade, using only prior closes?

    The outcome-split above is hindsight. The honest question is whether
    breadth known at the previous close -- what fraction of the universe is
    up over five sessions -- picks the good regimes. If it does, "sit out the
    weak tape" is a rule; if it does not, the regime risk is simply borne.
    """
    log("")
    log("=" * 108)
    log("CAN THE REGIME BE CALLED IN ADVANCE? (breadth at the prior close)")
    log("=" * 108)
    frame = pd.read_parquet(FEATURES).sort_values(["symbol", "day"])
    frame["day"] = pd.to_datetime(frame["day"])
    frame["r5"] = frame.groupby("symbol")["c"].pct_change(5)
    breadth = frame.groupby("day")["r5"].apply(
        lambda s: (s > 0).mean()).rename("breadth").reset_index()

    v = calls.copy()
    v["day"] = pd.to_datetime(v["day"])
    v = v.merge(breadth, on="day", how="left").dropna(subset=["breadth"])
    v["q"] = pd.qcut(v["breadth"], 4, labels=["weakest", "weak", "ok", "strongest"],
                     duplicates="drop")
    log("")
    log("  {:<11} {:>7} {:>13} {:>18}".format(
        "prior breadth", "trades", "break-even", "median if PERFECT"))
    log("  " + "-" * 52)
    buckets = {}
    for q, chunk in v.groupby("q", observed=True):
        need, med_right, n = hurdle_of(chunk)
        buckets[str(q)] = need
        log("  {:<11} {:>7,} {:>13} {:>18}".format(str(q), n, need, med_right))

    def pct(name):
        s = buckets.get(name, "")
        return float(s.rstrip("%")) if s.endswith("%") else float("inf")

    monotone = (pct("weakest") > pct("weak") > pct("ok") > pct("strongest"))
    log("")
    if monotone:
        log("  Monotone -- the hurdle falls at every step from weakest to")
        log("  strongest, so this IS a dose-response. It was not one before the")
        log("  expiry correction, and a dose-response is the check a lucky")
        log("  bucket usually fails. The top quartile:")
    else:
        log("  Not monotone -- a middle bucket is WORSE than the weakest, so this")
        log("  is not a dose-response and only one bucket is really speaking:")
    log("")
    top = v["breadth"] > v["breadth"].quantile(0.75)
    stats = {}
    for tag, chunk in [("top quartile", v[top]), ("the rest", v[~top])]:
        per_day = chunk.groupby("day")["ret"].mean()
        t = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day)))
        stats[tag] = (len(per_day), t)
        log("    {:<14} {:>5,} trades over {:>3} days   median {:+6.1f}%   day-t {:+.2f}"
            .format(tag, len(chunk), len(per_day), chunk["ret"].median() * 100, t))
    mid = v["day"].median()
    log("")
    log("    split in half by date, top-quartile edge over the rest:")
    halves = []
    for tag, chunk in [("first half", v[v["day"] <= mid]), ("second half", v[v["day"] > mid])]:
        a = chunk[chunk["breadth"] > v["breadth"].quantile(0.75)]
        b = chunk[chunk["breadth"] <= v["breadth"].quantile(0.75)]
        if len(a) > 40 and len(b) > 40:
            gap = (a["ret"].median() - b["ret"].median()) * 100
            halves.append(gap)
            log("      {:<12} {:+.1f}pp".format(tag, gap))
    log("")
    # Narrated from the three checks above. Before the expiry correction the
    # top quartile failed all three -- a lower t than the bucket it beat and an
    # edge decaying +94pp -> +21pp -- and the text said so. On priced-honestly
    # contracts it passes two of them, so the text has to say THAT instead. It
    # still does not rescue the trade, and the reason is the level, not the
    # stability: the best quartile needs more skill than a coin supplies.
    days_top = stats["top quartile"][0]
    holds = (stats["top quartile"][1] > stats["the rest"][1]
             and len(halves) == 2 and halves[1] >= halves[0])
    if holds:
        log("  This survives the two checks that killed it before the expiry")
        log("  correction: its day-t is now ABOVE the bucket it should beat, and")
        log("  the edge GROWS across the two halves instead of decaying. What")
        log("  still sinks it is the level, not the stability -- the best")
        log("  quartile needs {} against the {:.1f}% a coin gives, so it makes"
            .format(buckets.get("strongest", "n/a"), FREE_UP * 100))
        log("  the trade less bad rather than positive, on only {} days."
            .format(days_top))
    else:
        log("  {} days, a day-t BELOW the bucket it is supposed to beat, and an"
            .format(days_top))
        log("  edge that decays across the sample. There is no in-advance filter.")
    log("  Either way the regime risk has to be borne, not timed -- which is the")
    log("  real reason this is a market bet.")

    exits(calls)


def exits(calls: pd.DataFrame) -> None:
    """Does a target exit lower the hurdle? It was the biggest NIFTY lever.

    It appears to, spectacularly, and the appearance is an artifact of the
    hurdle itself. A first-touch target truncates the right tail while losers
    still run to zero, so the median rises even as expectancy falls. The
    break-even-accuracy framework is built on loss PROBABILITY and is therefore
    only valid for exits that leave the upside uncapped.
    """
    log("")
    log("=" * 108)
    log("DOES THE EXIT LOWER THE HURDLE? (no -- it breaks the measure)")
    log("=" * 108)
    v = calls.dropna(subset=["hold"]).copy()
    v["day"] = pd.to_datetime(v["day"])
    log("")
    log("  {:<18} {:>11} {:>10} {:>10} {:>8} {:>9}".format(
        "exit rule", "break-even", "median", "mean", "win%", "day-t"))
    log("  " + "-" * 70)
    capped = {}
    for label, col in [("hold to day 5", "hold"), ("first touch 1.5x", "t1.5"),
                       ("first touch 2x", "t2"), ("first touch 3x", "t3")]:
        s = v.dropna(subset=[col]).copy()
        s["ret"] = s[col] / s["cost"]
        need, _, _ = hurdle_of(s.assign(hold=s[col]))
        per_day = s.groupby("day")["ret"].mean()
        t = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day)))
        capped[col] = (need, (s["ret"] > 0).mean() * 100, t)
        log("  {:<18} {:>11} {:>9.1f}% {:>9.1f}% {:>7.1f}% {:>+9.2f}".format(
            label, need, s["ret"].median() * 100, s["ret"].mean() * 100,
            (s["ret"] > 0).mean() * 100, t))

    need15, win15, t15 = capped["t1.5"]
    free = FREE_UP * 100
    log("")
    # Computed, because this paragraph was written around numbers that moved a
    # long way once holds were cut at expiry. The 1.5x row used to read 31%
    # against a +48.1% median -- loud enough to call a money machine. Priced on
    # contracts that stay themselves it is 47.8% and the mean turns negative.
    # Same lesson, different evidence, so the evidence has to be re-derived.
    log("  Read the 1.5x row across: the hurdle falls to {} against the {:.1f}% a"
        .format(need15, free))
    log("  coin gives, and {:.1f}% of trades finish positive -- yet its day-t is"
        .format(win15))
    log("  {:+.2f}. The target caps every winner at +50% while the losers still run"
        .format(t15))
    log("  to -100%, so the COUNT of winners and the AVERAGE point opposite ways.")
    log("  Do not use the hurdle on a capped payoff: it is built on the")
    log("  probability of losing and cannot see a truncated right tail.")

    log("")
    ret = v["hold"] / v["cost"]
    log("  And the hold column's own {:+.1f}% mean does not survive either. It is"
        .format(ret.mean() * 100))
    log("  a 5-day return sampled every day, so its windows overlap:")
    log("")
    per_day = v.assign(ret=ret).groupby("day")["ret"].mean().sort_index()
    vals = per_day.values
    naive = vals.mean() / (vals.std(ddof=1) / np.sqrt(len(vals)))
    safe = [s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
            for s in (vals[off::5] for off in range(5)) if len(s) > 5]
    log("    naive day-clustered t   {:+.2f}   ({} days)".format(naive, len(vals)))
    log("    overlap-safe t          {:+.2f}   (~{} independent 5-day blocks)".format(
        np.mean(safe), len(vals) // 5))
    top1 = ret.nlargest(max(1, len(ret) // 100)).sum() / len(ret) * 100
    log("    top 1% of trades carry {:+.1f}pp of that mean; the median is {:.1f}%"
        .format(top1, ret.median() * 100))
    log("    and only {:.1f}% of days are positive.".format((per_day > 0).mean() * 100))
    log("")
    # The conclusion is derived rather than asserted, because the strength of
    # the evidence for it changed: before holds were cut at expiry the naive t
    # was +3.19 and the argument had to lean on de-overlapping to reach "not
    # significant". Priced honestly the day-clustered t is already negative.
    log("  So no-skill call buying on the scan is NOT quietly profitable. The")
    if naive > 0:
        log("  positive mean is payoff shape, and once the overlap is removed it is")
        log("  not significant. Consistent with the buying null, not against it.")
    else:
        log("  mean is pure payoff shape: the day-clustered t is already {:+.2f}"
            .format(naive))
        log("  BEFORE any overlap correction, so the de-overlapping below is not")
        log("  even load-bearing. Agrees with the buying null outright.")


if __name__ == "__main__":
    main()
