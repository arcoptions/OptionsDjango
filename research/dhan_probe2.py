"""Second probe: historical depth by chaining 90-day windows, and the future.

The first probe showed 1-minute equity candles exist and that Dhan caps a single
request at 90 days. Two things still decide the build:

  * whether windows placed a year back still return data, or whether intraday
    history is only recent -- this sets how much of our option cache can be
    paired with constituent bars
  * whether the NIFTY future is reachable, which would give a real traded VWAP
    and a real basis instead of the option-volume proxy the research currently
    leans on
"""
import csv
import io
import os
import time
from datetime import date, timedelta

import requests

from dhan_probe import (CLIENT_ID, HEAVYWEIGHTS, MASTER_URL, describe,
                        equity_master, headers, intraday)


def future_contracts():
    """NIFTY index futures in the master, nearest expiry first."""
    response = requests.get(MASTER_URL, timeout=120)
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.text))
    out = []
    for row in rows:
        if row.get("INSTRUMENT") != "FUTIDX":
            continue
        if (row.get("UNDERLYING_SYMBOL") or "").strip() != "NIFTY":
            continue
        out.append({
            "security_id": row.get("SECURITY_ID", "").strip(),
            "expiry": (row.get("SM_EXPIRY_DATE") or "").strip()[:10],
            "symbol": (row.get("DISPLAY_NAME") or "").strip(),
            "segment": row.get("EXCH_ID", "") + "_" + row.get("SEGMENT", ""),
        })
    return sorted(out, key=lambda item: item["expiry"])


def main():
    today = date.today()
    master = equity_master()

    print("historical depth, chained 90-day windows on RELIANCE")
    security = master["RELIANCE"]
    for back in (0, 3, 6, 9, 12, 15, 18, 24):
        end = today - timedelta(days=30 * back)
        start = end - timedelta(days=88)
        data = intraday(security, "NSE_EQ", "EQUITY", start.isoformat(), end.isoformat())
        print(f"   {back:>2} months back ({start}..{end}): {describe(data)}")
        time.sleep(1.0)

    print("\nNIFTY index futures in the master")
    contracts = future_contracts()
    for row in contracts[:4]:
        print(f"   {row['symbol']:<28}{row['expiry']:<12}id {row['security_id']:<10}"
              f"{row['segment']}")
    if not contracts:
        print("   none found")
        return

    nearest = contracts[0]
    print("\n1-minute candles on the nearest future")
    start = (today - timedelta(days=30)).isoformat()
    for segment in ("NSE_FNO", "NSE_FO"):
        data = intraday(nearest["security_id"], segment, "FUTIDX", start, today.isoformat())
        print(f"   segment {segment:<10}{describe(data)}")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
