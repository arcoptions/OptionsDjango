"""Does the cheap-toll shortlist have any bite to capture?

THE TENSION THIS RESOLVES, and it is a direct threat to the previous result.
`friction_by_symbol.py` found that the pooled 7.3% toll hides a cheap head: 7
underlyings quote at or below NIFTY's 1.63% and 32 sit under 3%. That looks like
a shortlist for porting the NIFTY architecture. But an earlier measurement
(`stock-option-friction-ceiling`) found intraday stock-option buying is +1.02%
GROSS against 1.64% friction, and -- the part that matters here -- **the gross
edge lives where the tick is worst**. The tick is worst on cheap premiums, and
cheap premiums are where spreads are widest. So the two findings make opposite
predictions about the shortlist:

    friction view : cheap toll -> the bite survives -> tradeable
    tick view     : cheap toll -> no gross bite existed -> nothing to keep

Only a per-symbol measurement can say which. If gross edge and toll are
positively related, the shortlist is an artefact and the programme is closed on
a measurement rather than an average. If they are unrelated, the shortlist is
real.

WHY THE ROLLING FEED IS THE RIGHT SOURCE HERE, having been the wrong one before.
The ATM-relative feed was rejected for the scale-in study because a contract
drops out as spot walks away from it, censoring exactly the multi-day moves that
study was hunting. An INTRADAY hold never has that problem: you enter and exit
inside one session, and "whatever is at the money right now" is precisely the
contract you would trade. Same feed, different horizon, opposite verdict.

THE TRAP THAT WOULD MANUFACTURE A RESULT, guarded explicitly below.  Because the
feed is ATM-RELATIVE, the underlying contract CHANGES whenever spot crosses a
strike boundary. Two consecutive rows can be two different options, and
differencing their closes prices a switch between contracts as if it were a
move -- a jump of the full strike interval, arriving preferentially on days the
stock trends, which is to say exactly when the signal fires. Every return here is
therefore computed only across bars whose `strike` is unchanged, and the number
of bars discarded for that reason is reported rather than silently dropped.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.models import StockOptionCandle  # noqa: E402

CACHE = "research/atm_call_15m.parquet"
HOLD = (1, 2, 4)          # bars held, i.e. 15 / 30 / 60 minutes
MIN_PREM = 2.50


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def load():
    if os.path.exists(CACHE):
        return pd.read_parquet(CACHE)
    log("reading ATM CALL bars ...")
    d = pd.DataFrame(list(
        StockOptionCandle.objects.filter(relative_strike="ATM", option_type="CALL")
        .order_by().values("symbol", "timestamp", "strike", "spot",
                           "open", "high", "low", "close", "volume")))
    for c in ("strike", "spot", "open", "high", "low", "close", "volume"):
        d[c] = d[c].astype(float)
    d["ts"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("Asia/Kolkata")
    d = d.drop(columns=["timestamp"])
    d["day"] = d["ts"].dt.date
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    d.to_parquet(CACHE, index=False)
    log("cached {:,} bars, {} symbols".format(len(d), d["symbol"].nunique()))
    return d


def forward(d, k):
    """Gross return of holding k bars, SAME session and SAME contract only."""
    g = d.groupby("symbol", sort=False)
    fwd_c = g["close"].shift(-k)
    same_day = g["day"].shift(-k) == d["day"]
    same_k = g["strike"].shift(-k) == d["strike"]
    # the strike must also be unchanged at every step in between, or the path
    # crossed a boundary and came back
    steady = pd.Series(True, index=d.index)
    for j in range(1, k + 1):
        steady &= (g["strike"].shift(-j) == d["strike"]) & (g["day"].shift(-j) == d["day"])
    ok = same_day & same_k & steady & (d["close"] >= MIN_PREM)
    r = (fwd_c / d["close"] - 1).where(ok)
    return r, ok, (same_day & (~steady)).sum()


def main():
    d = load()
    log("{:,} bars, {} symbols, {} sessions, {} .. {}".format(
        len(d), d["symbol"].nunique(), d["day"].nunique(), d["day"].min(), d["day"].max()))

    toll = pd.read_csv("research/spread_curve.csv")
    toll = toll[(toll["kind"] == "CE") & (toll["otm"].abs() <= 0.03) &
                (toll["mid"] >= MIN_PREM) & (toll["spread_pct"] > 0)]
    toll = (toll.groupby("symbol")["spread_pct"].median() / 100.0).rename("toll")

    print()
    print("=" * 104)
    print("DOES THE CHEAP-TOLL SHORTLIST HAVE ANY GROSS BITE?  ATM calls, intraday only")
    print("=" * 104)

    rows = {}
    for k in HOLD:
        r, ok, dropped = forward(d, k)
        log("hold {:>2} bars ({:>2} min): {:,} usable, {:,} discarded for a rolled strike"
            .format(k, k * 15, int(ok.sum()), int(dropped)))
        x = pd.DataFrame({"symbol": d["symbol"], "day": d["day"], "r": r}).dropna()
        rows[k] = x

    k = 4
    x = rows[k]
    per = x.groupby("symbol")["r"].agg(n="size", gross="mean", med="median")
    per = per[per["n"] >= 500].join(toll, how="inner").dropna()
    log("{} symbols with >=500 usable bars and a measured toll".format(len(per)))

    print()
    print("  the prediction under test: if gross edge tracks the toll, the cheap names")
    print("  have nothing to capture and the shortlist is an artefact")
    per["band"] = pd.cut(per["toll"], [0, 0.02, 0.03, 0.05, 0.08, 1.0],
                         labels=["<=2%", "2-3%", "3-5%", "5-8%", ">8%"])
    print("    {:<8} {:>7} {:>11} {:>11} {:>11}".format(
        "toll", "names", "median toll", "gross/hour", "net/hour"))
    for b, g in per.groupby("band", observed=True):
        print("    {:<8} {:>7} {:>10.2%} {:>10.3%} {:>10.3%}".format(
            str(b), len(g), g["toll"].median(), g["gross"].mean(),
            g["gross"].mean() - g["toll"].median()))

    c = per[["toll", "gross"]].corr(method="spearman").iloc[0, 1]
    print()
    print("    Spearman(toll, gross edge) = {:+.3f}".format(c))
    print("    {}".format(
        "positive -> the edge lives where the toll is worst; the shortlist is an artefact"
        if c > 0.15 else
        "not positive -> toll and edge are independent; the shortlist survives this test"))

    print()
    print("  the shortlist itself -- toll <= 3%, ranked by gross bite per hour held")
    s = per[per["toll"] <= 0.03].sort_values("gross", ascending=False)
    print("    {:<14} {:>8} {:>10} {:>11} {:>11}".format(
        "symbol", "bars", "toll", "gross/hr", "net/hr"))
    for sym, r in s.head(15).iterrows():
        print("    {:<14} {:>8,} {:>9.2%} {:>10.3%} {:>10.3%}".format(
            sym, int(r["n"]), r["toll"], r["gross"], r["gross"] - r["toll"]))
    print("    ...{} of {} shortlist names have gross > their own toll".format(
        int((s["gross"] > s["toll"]).sum()), len(s)))


if __name__ == "__main__":
    main()
