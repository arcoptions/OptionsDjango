"""How much deep-OTM history can we actually address?

The chain gives us real security ids for every strike from -45% to +35% OTM, and
`chain_probe.py` proved the intraday endpoint honours them: HAL-Aug2026-5000-CE
(id 97484) returned 562 real bars and the Rs22.20 -> Rs199.00 move the user
pointed at, 8.96x, verified against the tape.

That settles that the trades exist and that we can see them going forward.  It
does not settle whether we can BACKTEST them, which needs history on contracts
that have since expired.  The scrip master holds live contracts only -- three
monthly expiries, nothing before 2026-08-25 -- so the ids for a year of expired
contracts are not published anywhere we can read.

Two questions decide the shape of everything downstream:

  A. Does the API serve history for an EXPIRED contract if we happen to know its
     id?  If yes, the id list is the only obstacle and it is solvable.  If no,
     no amount of id archaeology helps and a historical deep-OTM backtest is
     off the table.

  B. How far back does a LIVE contract go?  Every live contract is a free
     sample of real deep-OTM history.  Three expiries x ~190 stocks x ~74
     strikes is a large cache if the lookback is months rather than days.

The answers determine whether the deep-OTM strategy is testable on history, or
whether it has to be built forward from a capture that starts today.
"""
import datetime as dt
import os
import sys

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.stock_data import _headers  # noqa: E402

INTRADAY = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL = "https://api.dhan.co/v2/charts/historical"


def daily(sid, seg, instr, frm, to):
    r = requests.post(HISTORICAL, headers=_headers(), timeout=30, json={
        "securityId": str(sid), "exchangeSegment": seg, "instrument": instr,
        "expiryCode": 0, "oi": False, "fromDate": frm, "toDate": to})
    if r.status_code != 200:
        return None, "HTTP {} {}".format(r.status_code, r.text[:160])
    j = r.json()
    if not j.get("close"):
        return None, "empty"
    return j, "{} bars".format(len(j["close"]))


def span(j):
    ts = j["timestamp"]
    lo = dt.datetime.fromtimestamp(ts[0], dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
    hi = dt.datetime.fromtimestamp(ts[-1], dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
    return lo.date(), hi.date()


def main():
    print("=" * 90)
    print("A. DOES AN EXPIRED CONTRACT STILL SERVE HISTORY?")
    print("   Testing NIFTY ids captured in an old chain snapshot -- long expired.")
    print("=" * 90)
    from options_tracker.models import IndexOptionStrikeSnapshot as S
    old = list(S.objects.exclude(security_id="").values(
        "security_id", "strike", "option_type")[:6])
    for row in old[:4]:
        j, msg = daily(row["security_id"], "NSE_FNO", "OPTIDX", "2023-01-01", "2026-08-18")
        extra = "  span {} .. {}".format(*span(j)) if j else ""
        print("   NIFTY {:>9} {} id {:<8} -> {}{}".format(
            str(row["strike"]), row["option_type"], row["security_id"], msg, extra))

    print()
    print("=" * 90)
    print("B. HOW FAR BACK DOES A LIVE CONTRACT GO?")
    print("   Every live contract is free deep-OTM history. Depth decides how much.")
    print("=" * 90)
    d = pd.read_csv("research/scrip_master.csv", low_memory=False)
    o = d[(d["SEM_INSTRUMENT_NAME"] == "OPTSTK") & (d["SEM_EXM_EXCH_ID"] == "NSE")]
    hal = o[o["SEM_TRADING_SYMBOL"].astype(str).str.startswith("HAL-")]
    hal = hal[(hal.SEM_STRIKE_PRICE == 5000) & (hal.SEM_OPTION_TYPE == "CE")]
    for _, r in hal.iterrows():
        j, msg = daily(r.SEM_SMST_SECURITY_ID, "NSE_FNO", "OPTSTK", "2025-08-01", "2026-08-18")
        extra = "  span {} .. {}".format(*span(j)) if j else ""
        print("   {:<26} id {:<8} -> {}{}".format(r.SEM_TRADING_SYMBOL, r.SEM_SMST_SECURITY_ID, msg, extra))

    print()
    print("=" * 90)
    print("C. HOW BIG IS THE ADDRESSABLE UNIVERSE RIGHT NOW?")
    print("=" * 90)
    tracked = set(pd.read_sql("select symbol from options_tracker_trackedstock where is_active=1",
                              __import__("django.db", fromlist=["connection"]).connection)["symbol"])
    o = o.copy()
    o["base"] = o["SEM_TRADING_SYMBOL"].astype(str).str.split("-").str[0]
    mine = o[o["base"].isin(tracked)]
    print("   tracked symbols with listed options: {} of {}".format(mine["base"].nunique(), len(tracked)))
    print("   total live NSE stock option contracts for them: {:,}".format(len(mine)))
    for exp, grp in mine.groupby(mine["SEM_EXPIRY_DATE"].astype(str).str[:10]):
        print("     {}  {:>6,} contracts   {:>4} symbols   median strikes/symbol {:.0f}".format(
            exp, len(grp), grp["base"].nunique(), grp.groupby("base").size().median()))


if __name__ == "__main__":
    main()
