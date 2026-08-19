"""Why does the middle of the OTM download 504?

The ledger says something odd: 973 of 1,802 ATM+1 windows failed, but not at
random.  The oldest five windows (Feb-Jul 2025) succeeded 100%, the newest one
(Aug 2026) succeeded 100%, and everything BETWEEN them failed at ~96%.  All
fourteen windows for a given stock are requested within seconds of each other,
so this is not a rate limit or a wall-clock artefact -- it is something about
the dates themselves.

The error body is a raw HTML gateway page, not a JSON API error, which points at
an upstream TIMEOUT rather than "no data for that range".  If that is right the
window is simply too wide for the server to answer in time, and cutting
ROLLING_WINDOW_DAYS recovers the whole barren band for free.

This probes that, one request at a time, timing each:

  1. reproduce a known failure at 45 days
  2. the same dates at 20, 10 and 5 days
  3. the same dates at ATM instead of ATM+1  -- is it the offset?
  4. a known-GOOD window at 45 days          -- how long does a success take?
"""

import datetime as dt
import os
import sys
import time

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker import stock_data as sd  # noqa: E402
from options_tracker.models import TrackedStock  # noqa: E402

BAD = (dt.date(2025, 8, 17), dt.date(2025, 9, 30))     # 96% failure band
GOOD = (dt.date(2025, 2, 18), dt.date(2025, 4, 3))     # 0% failure band


def probe(security_id, relative, start, end):
    """One raw request. Returns (seconds, bar count or the error)."""
    payload = {
        "exchangeSegment": "NSE_FNO", "interval": "15", "securityId": str(security_id),
        "instrument": "OPTSTK", "expiryFlag": "MONTH", "expiryCode": 1,
        "strike": relative, "drvOptionType": "CALL",
        "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
    }
    clock = time.time()
    # attempts=1: we want the raw verdict, not the retry loop's summary.
    data, error = sd._post(sd.ROLLING_URL, payload, attempts=1)
    took = time.time() - clock
    if error:
        return took, error.split("\n")[0][:60]
    stamps = ((data.get("data") or {}).get("ce") or {}).get("timestamp") or []
    return took, "{} bars".format(len(stamps))


def main():
    stock = TrackedStock.objects.filter(symbol="NATIONALUM").first()
    equities = sd.equity_ids(sd.load_master())
    sec = stock.security_id or equities.get("NATIONALUM")
    sd.log("probing NATIONALUM (securityId {})".format(sec))

    trials = []
    trials.append(("reproduce  45d ATM+1 BAD ", "ATM+1", BAD[0], BAD[1]))
    for span in (20, 10, 5):
        trials.append((
            "narrow     {:>2}d ATM+1 BAD ".format(span),
            "ATM+1", BAD[0], BAD[0] + dt.timedelta(days=span - 1),
        ))
    trials.append(("offset     45d ATM   BAD ", "ATM", BAD[0], BAD[1]))
    trials.append(("control    45d ATM+1 GOOD", "ATM+1", GOOD[0], GOOD[1]))
    trials.append(("control    20d ATM+1 GOOD", "ATM+1", GOOD[0], GOOD[0] + dt.timedelta(days=19)))

    print("\n  {:<26} {:<12} {:>8}   {}".format("trial", "dates", "secs", "result"))
    print("  " + "-" * 76)
    for label, relative, start, end in trials:
        took, result = probe(sec, relative, start, end)
        print("  {:<26} {} {:>8.1f}   {}".format(
            label, start.strftime("%Y-%m-%d"), took, result))
        time.sleep(1.5)   # the API is shared; do not hammer it while diagnosing


if __name__ == "__main__":
    main()
