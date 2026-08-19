"""Can we reach the strikes where 5-10x actually lives?

THE PROBLEM THIS EXISTS TO SOLVE.  Every stock-option result in this programme
is measured on the rolling ATM+-3 feed, which on a Rs50 strike ladder spans
about +-2% of spot.  The trades the brief is actually about live further out.
Worked example, HAL: on 2026-08-05 spot was 4,637, so the cache's furthest call
was the 4,750 strike at Rs92.  The contract that went Rs22 -> Rs199 that week
was the 5,000 strike -- ATM+7, 7.8% out of the money, a quarter of the price.
The cache cannot see it, so no study run on the cache can rule it in or out.

WHAT THIS PROBES, IN ORDER.
  1. Does /v2/optionchain enumerate stock strikes with real security ids?
  2. How far out does the chain go, and what do the far strikes cost?
  3. Does /v2/charts/intraday accept those ids and return real history?
  4. THE ONE THAT DECIDES THE PROGRAMME: can we address an EXPIRED contract?
     If yes, a year-long deep-OTM backtest is possible.  If no, the only honest
     route is forward capture, and the strategy has to be built on what the
     rolling feed can support plus whatever we start recording today.

Nothing here is a strategy.  It establishes what data exists before anything is
claimed about it, which is the step this programme skipped for the far tail.
"""
import datetime as dt
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.stock_data import _headers  # noqa: E402

CHAIN = "https://api.dhan.co/v2/optionchain"
EXPIRYLIST = "https://api.dhan.co/v2/optionchain/expirylist"
INTRADAY = "https://api.dhan.co/v2/charts/intraday"

SYMBOL = "HAL"
SECURITY_ID = 2303
TARGET_STRIKE = 5000.0

# The optionchain endpoint is rate limited to one call every three seconds.
CHAIN_GAP = 3.2


def post(url, payload, label):
    r = requests.post(url, json=payload, headers=_headers(), timeout=30)
    print("  {:<28} HTTP {}".format(label, r.status_code))
    if r.status_code != 200:
        print("    {}".format(r.text[:400]))
        return None
    return r.json()


def main():
    print("=" * 88)
    print("1. EXPIRY LIST FOR {} (security id {})".format(SYMBOL, SECURITY_ID))
    print("=" * 88)
    expiries = []
    for seg in ("NSE_FNO", "NSE_EQ"):
        data = post(EXPIRYLIST, {"UnderlyingScrip": SECURITY_ID, "UnderlyingSeg": seg},
                    "UnderlyingSeg=" + seg)
        if data and data.get("data"):
            expiries = data["data"]
            print("    -> {} expiries, first five: {}".format(len(expiries), expiries[:5]))
            SEG = seg
            break
        time.sleep(CHAIN_GAP)
    else:
        print("\n  No expiry list. Stock option chain is not reachable this way.")
        return

    time.sleep(CHAIN_GAP)

    print()
    print("=" * 88)
    print("2. THE CHAIN ITSELF -- how far out does it go, and what does it cost?")
    print("=" * 88)
    expiry = expiries[0]
    chain = post(CHAIN, {"UnderlyingScrip": SECURITY_ID, "UnderlyingSeg": SEG,
                         "Expiry": expiry}, "chain " + str(expiry))
    if not chain:
        return

    oc = (chain.get("data") or {}).get("oc") or {}
    spot = (chain.get("data") or {}).get("last_price")
    print("    spot {}   strikes returned {}".format(spot, len(oc)))
    if not oc:
        print("    raw: {}".format(json.dumps(chain)[:600]))
        return

    strikes = sorted(float(k) for k in oc)
    print("    range {} .. {}   step ~{}".format(
        strikes[0], strikes[-1],
        min(b - a for a, b in zip(strikes, strikes[1:])) if len(strikes) > 1 else "n/a"))

    # Does a row carry a security id?  That is the whole question.
    sample = oc[list(oc)[0]]
    print("    keys on a strike row: {}".format(list(sample)))
    ce_keys = list((sample.get("ce") or {}).keys())
    print("    keys on the CE leg:   {}".format(ce_keys))

    print()
    print("    {:>9} {:>10} {:>10} {:>8}  {}".format(
        "strike", "CE ltp", "% of spot", "OTM %", "CE security id"))
    for k in strikes:
        ce = (oc["{:.6f}".format(k)] if "{:.6f}".format(k) in oc else oc[str(k)]).get("ce") or {}
        ltp = ce.get("last_price")
        if ltp is None:
            continue
        sid = ce.get("security_id") or ce.get("securityId") or "-- ABSENT --"
        print("    {:>9.0f} {:>10} {:>10} {:>8}  {}".format(
            k, ltp,
            "{:.2f}%".format(100 * float(ltp) / float(spot)) if spot else "?",
            "{:+.1f}%".format(100 * (k / float(spot) - 1)) if spot else "?",
            sid))

    print()
    print("=" * 88)
    print("3. CAN WE PULL REAL HISTORY FOR A FAR STRIKE?")
    print("   Target: the {} {} CE -- the contract that actually went 9x.".format(SYMBOL, TARGET_STRIKE))
    print("=" * 88)
    row = None
    for k in strikes:
        if abs(k - TARGET_STRIKE) < 0.01:
            row = (oc.get("{:.6f}".format(k)) or oc.get(str(k)) or {}).get("ce") or {}
    sid = (row or {}).get("security_id") or (row or {}).get("securityId")
    if not sid:
        print("  The chain does not expose a security id, so history is not addressable")
        print("  this way. Next probe: the scrip master CSV.")
        return

    payload = {"securityId": str(sid), "exchangeSegment": "NSE_FNO", "instrument": "OPTSTK",
               "interval": "15", "fromDate": "2026-07-20", "toDate": "2026-08-18"}
    bars = post(INTRADAY, payload, "intraday {}".format(sid))
    if not bars or not bars.get("close"):
        print("  No bars returned.")
        return

    ts, op, hi, lo, cl = (bars["timestamp"], bars["open"], bars["high"],
                          bars["low"], bars["close"])
    print("    {} bars".format(len(cl)))
    day = {}
    for i, t in enumerate(ts):
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
        e = day.setdefault(d.date(), [op[i], hi[i], lo[i], cl[i]])
        e[1] = max(e[1], hi[i])
        e[2] = min(e[2], lo[i])
        e[3] = cl[i]
    print()
    print("    {:<12} {:>8} {:>8} {:>8} {:>8}".format("date", "open", "high", "low", "close"))
    for d in sorted(day):
        o, h, l, c = day[d]
        print("    {:<12} {:>8.2f} {:>8.2f} {:>8.2f} {:>8.2f}".format(d, o, h, l, c))

    lows = [day[d][2] for d in sorted(day)]
    highs = [day[d][1] for d in sorted(day)]
    best = max((highs[j] / lows[i], sorted(day)[i], sorted(day)[j])
               for i in range(len(lows)) for j in range(i, len(highs)))
    print()
    print("    Best low->high multiple in the window: {:.2f}x  ({} -> {})".format(*best))


if __name__ == "__main__":
    main()
