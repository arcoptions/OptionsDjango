"""Can we get what we actually need from Dhan: constituent bars and real volume?

Three things are unknown and decide whether the breadth idea is worth building:

  1. does the instrument master carry the NIFTY 50 constituents with usable
     NSE_EQ security ids
  2. how far back /v2/charts/intraday will serve 1-minute equity candles, and
     how many minutes come back per session
  3. whether the index future is reachable the same way, which would replace the
     option-volume VWAP proxy with a real one

Nothing here writes to the database. It makes a handful of read-only requests so
the answer is measured rather than assumed. The token is read from a file so it
never lands in a command line or a log.
"""
import csv
import io
import os
import sys
import time
from datetime import date, timedelta

import requests

TOKEN_FILE = os.environ.get(
    "DHAN_TOKEN_FILE",
    os.path.expanduser("~/Downloads/Dhan Temp Token.txt"),
)
CLIENT_ID = "1111860593"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

# The heavyweights, by index weight. Roughly 60% of NIFTY between them, which is
# where a breadth signal would get most of its information.
HEAVYWEIGHTS = [
    "HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "BHARTIARTL", "TCS",
    "LT", "ITC", "AXISBANK", "SBIN", "KOTAKBANK", "HINDUNILVR",
]


def token():
    with open(TOKEN_FILE) as handle:
        return handle.read().strip()


def headers():
    return {"access-token": token(), "client-id": CLIENT_ID,
            "Content-Type": "application/json"}


def equity_master():
    """{symbol: security_id} for NSE cash equities."""
    response = requests.get(MASTER_URL, timeout=120)
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.text))
    table = {}
    for row in rows:
        if row.get("INSTRUMENT") != "EQUITY":
            continue
        if row.get("EXCH_ID") != "NSE" or row.get("SEGMENT") != "E":
            continue
        symbol = (row.get("UNDERLYING_SYMBOL") or row.get("SYMBOL_NAME") or "").strip()
        if symbol and symbol not in table:
            table[symbol] = row.get("SECURITY_ID", "").strip()
    return table


def intraday(security_id, segment, instrument, start, end, interval="1"):
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": segment,
        "instrument": instrument,
        "interval": interval,
        "oi": False,
        "fromDate": f"{start} 09:15:00",
        "toDate": f"{end} 15:30:00",
    }
    response = requests.post(INTRADAY_URL, json=payload, headers=headers(), timeout=90)
    if not response.ok:
        return {"error": f"{response.status_code} {response.text[:200]}"}
    return response.json()


def describe(data):
    if "error" in data:
        return data["error"]
    stamps = data.get("timestamp") or []
    if not stamps:
        return "no candles"
    import datetime as dt
    first = dt.datetime.fromtimestamp(stamps[0])
    last = dt.datetime.fromtimestamp(stamps[-1])
    days = len({dt.datetime.fromtimestamp(s).date() for s in stamps})
    volume = data.get("volume") or []
    total = sum(v for v in volume if v)
    return (f"{len(stamps):>7,} candles  {days:>4} sessions  "
            f"{first:%Y-%m-%d}..{last:%Y-%m-%d}  vol {total:,.0f}")


def main():
    print("1. instrument master")
    master = equity_master()
    print(f"   {len(master):,} NSE equities")
    found = {s: master.get(s) for s in HEAVYWEIGHTS}
    missing = [s for s, i in found.items() if not i]
    print(f"   heavyweights resolved: {len(HEAVYWEIGHTS) - len(missing)}/{len(HEAVYWEIGHTS)}"
          + (f"   missing {missing}" if missing else ""))

    today = date.today()
    print("\n2. how far back 1-minute equity candles go")
    security = found.get("RELIANCE")
    for months in (1, 3, 6, 12, 24):
        start = today - timedelta(days=30 * months)
        data = intraday(security, "NSE_EQ", "EQUITY", start.isoformat(), today.isoformat())
        print(f"   RELIANCE, {months:>2} month window: {describe(data)}")
        time.sleep(1.0)

    print("\n3. one request per heavyweight, one month, to time a full backfill")
    start = (today - timedelta(days=30)).isoformat()
    began = time.time()
    for symbol in HEAVYWEIGHTS[:4]:
        data = intraday(found[symbol], "NSE_EQ", "EQUITY", start, today.isoformat())
        print(f"   {symbol:<12}{describe(data)}")
        time.sleep(1.0)
    print(f"   {(time.time() - began) / 4:.1f}s per symbol-month")


if __name__ == "__main__":
    main()
