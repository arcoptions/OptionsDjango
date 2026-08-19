"""The real bid-ask curve at deep-OTM stock strikes.

WHY THIS MATTERS MORE THAN IT LOOKS.  Every friction number in this programme
descends from an index assumption.  `INTRADAY_REPORT.md` says it plainly: "a Rs3
stock option is not quoted five paise wide, and five paise is an INDEX
assumption imported wholesale."  Nothing has ever measured the real thing at the
strikes this brief is about, and the historical endpoint cannot -- it serves
OHLCV and OI only.  The option chain does carry `top_bid_price` and
`top_ask_price`, and they survive the close, so the curve is capturable.

WHAT IT CHANGES.  [[stock-option-friction-ceiling]] closed the intraday
stock-option programme on the grounds that a +1.02% gross edge cannot pay 1.64%
of friction.  That arithmetic is specific to a regime where the edge is about
one percent.  A trade aiming at 2-5x is targeting 100-400%, so even a 15%
round-trip spread is a rounding error against the target.  Friction stops being
the binding constraint out here and HIT RATE becomes it.  This file measures how
big the friction actually is so that claim rests on data rather than on a hunch
-- and so the base-rate work has a real number to subtract.

HAL, sampled first, gives the shape: 0.7% of mid at the money, 2.6% at +2%,
4.6-7.2% around +4-5%, 12.6% at +6%, 34% at +7%, and thin noisy quotes past
+10%.  The question this file answers across the universe is where that curve
stops being payable, because that -- not theta -- is what bounds how far out the
strategy can reach.

RATE LIMIT.  /v2/optionchain is one call every three seconds and is a separate
bucket from the charts endpoints. 181 symbols is about ten minutes.
"""
import datetime as dt
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django  # noqa: E402

django.setup()

from options_tracker.models import TrackedStock  # noqa: E402
from options_tracker.stock_data import _headers  # noqa: E402

CHAIN = "https://api.dhan.co/v2/optionchain"
EXPIRY = "2026-08-25"
GAP = 3.3
OUT = "research/spread_curve.csv"
BANDS = [(-0.02, 0.02), (0.02, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.20)]
PREM_BANDS = [(0.05, 0.5), (0.5, 1.0), (1.0, 2.5), (2.5, 5.0), (5.0, 10.0),
              (10.0, 25.0), (25.0, 1e9)]


def log(msg):
    print("[{:%H:%M:%S}] {}".format(dt.datetime.now(), msg), flush=True)


def chain(sid, head):
    for delay in (GAP, 2 * GAP, 4 * GAP):
        r = requests.post(CHAIN, json={"UnderlyingScrip": int(sid),
                                       "UnderlyingSeg": "NSE_FNO",
                                       "Expiry": EXPIRY}, headers=head, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 805) or r.status_code >= 500:
            time.sleep(delay)
            continue
        return None
    return None


def capture():
    head = _headers()
    stocks = list(TrackedStock.objects.filter(is_active=True)
                  .values_list("symbol", "security_id"))
    log("{} tracked symbols, expiry {}".format(len(stocks), EXPIRY))

    rows = []
    for i, (symbol, sid) in enumerate(stocks, 1):
        if not sid:
            continue
        j = chain(sid, head)
        time.sleep(GAP)
        if not j:
            continue
        data = j.get("data") or {}
        oc, spot = data.get("oc") or {}, data.get("last_price")
        if not oc or not spot:
            continue
        for k, legs in oc.items():
            strike = float(k)
            for tag, leg in (("CE", legs.get("ce") or {}), ("PE", legs.get("pe") or {})):
                bid, ask = leg.get("top_bid_price"), leg.get("top_ask_price")
                ltp, vol = leg.get("last_price"), leg.get("volume")
                if not bid or not ask or ask <= bid:
                    continue
                otm = strike / spot - 1.0 if tag == "CE" else 1.0 - strike / spot
                rows.append({"symbol": symbol, "kind": tag, "strike": strike,
                             "spot": spot, "otm": otm, "bid": bid, "ask": ask,
                             "mid": (bid + ask) / 2, "ltp": ltp, "volume": vol or 0,
                             "oi": leg.get("oi") or 0,
                             "spread_pct": (ask - bid) / ((ask + bid) / 2) * 100})
        if i % 25 == 0:
            log("  {}/{} symbols, {:,} quotes".format(i, len(stocks), len(rows)))
    return pd.DataFrame(rows)


def by_premium(d):
    """Friction against the PREMIUM, which is what actually governs it.

    Moneyness is the wrong axis and the HAL example shows why: its 5,000 call
    sat 7.8% out of the money and still cost Rs22.20, because the stock is
    Rs4,637.  The +5..+8% band's MEDIAN premium across the universe is Rs1.51.
    Those are not the same instrument and averaging them together hides the only
    thing a buyer can control.  With a Rs0.05 tick, one tick is 12.5% of a
    Rs0.40 option and 0.3% of a Rs17 one -- so the tick alone sets a floor that
    no strategy can trade its way out of.
    """
    print()
    print("=" * 96)
    print("FRICTION BY PREMIUM LEVEL -- the axis that actually governs it")
    print("  'tick' is one Rs0.05 increment as a share of premium: the hard floor.")
    print("=" * 96)
    print("    {:<14} {:>8} {:>10} {:>11} {:>11} {:>9} {:>10}".format(
        "premium", "quotes", "med otm", "med spread", "75th pct", "tick", "med OI"))
    for lo, hi in PREM_BANDS:
        b = d[(d.mid >= lo) & (d.mid < hi)]
        if len(b) < 20:
            continue
        label = "Rs{:g}-{:g}".format(lo, hi) if hi < 1e6 else "Rs{:g}+".format(lo)
        print("    {:<14} {:>8,} {:>9.1%} {:>10.1f}% {:>10.1f}% {:>8.1%} {:>10,.0f}".format(
            label, len(b), b.otm.median(), b.spread_pct.median(),
            b.spread_pct.quantile(0.75), (0.05 / b.mid).median(), b.oi.median()))


def main():
    # Re-analysing a capture should not re-spend ten minutes of quota.
    if os.path.exists(OUT) and "--refetch" not in sys.argv:
        d = pd.read_csv(OUT)
        log("reusing {} -- {:,} quotes, {} symbols (pass --refetch to recapture)".format(
            OUT, len(d), d.symbol.nunique()))
    else:
        d = capture()
        if d.empty:
            log("no quotes captured")
            return
        d.to_csv(OUT, index=False)
        log("wrote {} -- {:,} quotes, {} symbols".format(OUT, len(d), d.symbol.nunique()))

    for kind, word in (("CE", "CALLS"), ("PE", "PUTS")):
        print()
        print("=" * 96)
        print("{} -- round-trip friction by moneyness".format(word))
        print("  A buyer crosses half the spread in and half out, so the spread IS")
        print("  the round trip. Compare against the multiple the trade targets.")
        print("=" * 96)
        print("    {:<14} {:>8} {:>10} {:>11} {:>11} {:>11} {:>10}".format(
            "OTM band", "quotes", "med prem", "med spread", "75th pct", "90th pct", "med OI"))
        v = d[d.kind == kind]
        for lo, hi in BANDS:
            b = v[(v.otm >= lo) & (v.otm < hi)]
            if len(b) < 20:
                continue
            print("    {:>+5.0%}..{:<+5.0%} {:>8,} {:>10.2f} {:>10.1f}% {:>10.1f}% "
                  "{:>10.1f}% {:>10,.0f}".format(
                      lo, hi, len(b), b.mid.median(), b.spread_pct.median(),
                      b.spread_pct.quantile(0.75), b.spread_pct.quantile(0.90),
                      b.oi.median()))

    print()
    print("=" * 96)
    print("WHERE DOES THE SPREAD STOP BEING PAYABLE?")
    print("  Share of quotes whose round trip exceeds a given fraction of premium.")
    print("=" * 96)
    v = d[d.kind == "CE"]
    print("    {:<14} {:>10} {:>10} {:>10} {:>10}".format(
        "OTM band", ">5%", ">10%", ">20%", ">50%"))
    for lo, hi in BANDS:
        b = v[(v.otm >= lo) & (v.otm < hi)]
        if len(b) < 20:
            continue
        print("    {:>+5.0%}..{:<+5.0%} {:>9.0%} {:>10.0%} {:>10.0%} {:>10.0%}".format(
            lo, hi, (b.spread_pct > 5).mean(), (b.spread_pct > 10).mean(),
            (b.spread_pct > 20).mean(), (b.spread_pct > 50).mean()))

    by_premium(d[d.kind == "CE"])


if __name__ == "__main__":
    main()
