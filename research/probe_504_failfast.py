"""Does failing fast and retrying beat waiting for the gateway to give up?

The throughput probe put a hard number on the current design: 15-day windows,
two workers, 17% still failing and a MEDIAN of 24 seconds a request.  That
median is the tell.  Successful calls come back in one to four seconds; the 24
is the failures dragging it, because `_post` sets a 120-second client timeout
and so sits waiting the full 30 seconds for the gateway to give up.  It then
returns the 504 without retrying, since only 429s and network errors retry.

Both halves of that look wrong given what the other probes found.  Latency on
this endpoint is bimodal -- fast, or dead -- so a client timeout well below the
gateway's 30 seconds costs nothing on the successes and cuts the failures short.
And the 504 is at least partly transient: the same window has both failed and
succeeded across these probes, so a retry is a fresh roll rather than a repeat
of a settled verdict.

This measures the combination against the current behaviour, on the same
windows, and reports the number that actually matters -- windows COMPLETED per
hour, not requests attempted.
"""

import datetime as dt
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import django
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker import stock_data as sd  # noqa: E402
from options_tracker.models import TrackedStock  # noqa: E402

SPAN = 15
STARTS = [dt.date(2025, 8, 17), dt.date(2025, 10, 31), dt.date(2025, 12, 15),
          dt.date(2026, 2, 8), dt.date(2026, 3, 25), dt.date(2026, 5, 10)]
SYMBOLS = ["NATIONALUM", "RELIANCE", "TATASTEEL", "TATAMOTORS", "SBIN", "ICICIBANK"]


def fetch(sec, start, end, timeout, attempts):
    """One window, with a short client timeout and 504s treated as retryable."""
    payload = {
        "exchangeSegment": "NSE_FNO", "interval": "15", "securityId": str(sec),
        "instrument": "OPTSTK", "expiryFlag": "MONTH", "expiryCode": 1,
        "strike": "ATM+1", "drvOptionType": "CALL",
        "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
    }
    tries = 0
    for _ in range(attempts):
        tries += 1
        try:
            r = sd._session().post(
                sd.ROLLING_URL, json=payload, headers=sd._headers(), timeout=timeout)
        except requests.RequestException:
            continue                      # includes our own read timeout
        if r.status_code == 429:
            time.sleep(2.0)
            continue
        if r.ok:
            side = ((r.json().get("data") or {}).get("ce")) or {}
            return True, len(side.get("timestamp") or []), tries
        if r.status_code in (502, 503, 504):
            continue                      # transient gateway, worth another roll
        return False, 0, tries
    return False, 0, tries


def run(jobs, workers, timeout, attempts):
    clock = time.time()
    with ThreadPoolExecutor(workers) as pool:
        out = list(pool.map(lambda a: fetch(a[0], a[1], a[2], timeout, attempts), jobs))
    wall = time.time() - clock
    ok = [r for r in out if r[0]]
    return {"ok": len(ok), "n": len(jobs), "wall": wall,
            "tries": sum(r[2] for r in out) / len(out),
            "rate": 3600 * len(ok) / wall}


def main():
    equities = sd.equity_ids(sd.load_master())
    jobs = []
    for symbol in SYMBOLS:
        stock = TrackedStock.objects.filter(symbol=symbol).first()
        sec = (stock.security_id if stock else None) or equities.get(symbol)
        if sec:
            jobs += [(sec, s, s + dt.timedelta(days=SPAN - 1)) for s in STARTS]
    sd.log("{} windows of {} days across the failure band".format(len(jobs), SPAN))

    print("\n  {:<32} {:>8} {:>8} {:>9} {:>12}".format(
        "setting", "done", "done%", "tries/win", "windows/hr"))
    print("  " + "-" * 74)
    for label, workers, timeout, attempts in [
        ("current: 120s timeout, no retry", 2, 120, 1),
        ("fail fast 10s, 4 tries", 2, 10, 4),
        ("fail fast 10s, 4 tries, 4 wide", 4, 10, 4),
        ("fail fast 6s, 6 tries, 4 wide", 4, 6, 6),
    ]:
        r = run(jobs, workers, timeout, attempts)
        print("  {:<32} {:>8} {:>7.0f}% {:>9.1f} {:>12,.0f}".format(
            label, r["ok"], 100 * r["ok"] / r["n"], r["tries"], r["rate"]))
        time.sleep(5)


if __name__ == "__main__":
    main()
