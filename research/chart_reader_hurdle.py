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
    v["month"] = pd.to_datetime(v["day"]).dt.to_period("M")
    log("")
    log("  {:<10} {:>7} {:>14} {:>18}".format(
        "month", "trades", "break-even", "median if PERFECT"))
    log("  " + "-" * 52)
    for month, chunk in v.groupby("month"):
        need, med_right, n = hurdle_of(chunk)
        log("  {:<10} {:>7,} {:>14} {:>18}".format(str(month), n, need, med_right))
    log("")
    log("  'thin' is a hole in the option cache, not a verdict. Of the 11 months")
    log("  that can be scored the hurdle runs 18% to unreachable, and in 2026-03")
    log("  even a perfect read lost money.")

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


if __name__ == "__main__":
    main()
