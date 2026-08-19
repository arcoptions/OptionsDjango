"""Is the 504 about the dates, or about how hard we were pushing?

The first probe looked conclusive -- 45 and 20-day windows timing out at exactly
30.0 seconds while 10-day windows answered in 0.3.  Then the cliff probe ran the
SAME 20-day window on the SAME symbol and it came back in 4.3 seconds with 350
bars.  Identical request, opposite outcome.  So the 30-second wall is real but
what hits it is not the window size; latency on this endpoint swings between
0.1s and 30s+ for the same call.

Which leaves the one difference between the probes and the real run: the probes
are sequential and the downloader is four-wide.  If the server serialises per
client, four in flight makes every request roughly four times slower, and the
dense stretch of history -- which is exactly the barren middle band -- is the
part sitting close enough to the wall to tip over.

That is the hypothesis this measures: the same windows, sequential vs four-wide.
"""

import datetime as dt
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker import stock_data as sd  # noqa: E402
from options_tracker.models import TrackedStock  # noqa: E402

from probe_504 import probe  # noqa: E402

START = dt.date(2025, 8, 17)          # the middle of the 96%-failure band
SPAN = 45                             # what the downloader actually used
SYMBOLS = ["NATIONALUM", "RELIANCE", "TATASTEEL", "TATAMOTORS", "SBIN",
           "ICICIBANK", "INFY", "HINDALCO"]


def run(jobs, workers):
    clock = time.time()
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda a: probe(*a), jobs))
    wall = time.time() - clock
    fails = [r for _, r in results if r.startswith("HTTP") or r.startswith("network")]
    times = [t for t, _ in results]
    return {
        "workers": workers, "n": len(jobs), "fails": len(fails), "wall": wall,
        "median": sorted(times)[len(times) // 2], "worst": max(times),
    }


def main():
    equities = sd.equity_ids(sd.load_master())
    jobs = []
    for symbol in SYMBOLS:
        stock = TrackedStock.objects.filter(symbol=symbol).first()
        sec = (stock.security_id if stock else None) or equities.get(symbol)
        if sec:
            jobs.append((sec, "ATM+1", START, START + dt.timedelta(days=SPAN - 1)))
    sd.log("{} windows of {} days from {}, in the failure band".format(
        len(jobs), SPAN, START))

    print("\n  {:>8} {:>6} {:>7} {:>9} {:>9} {:>9}".format(
        "workers", "n", "failed", "fail%", "median s", "wall s"))
    print("  " + "-" * 56)
    for workers in (1, 4):
        r = run(jobs, workers)
        print("  {:>8} {:>6} {:>7} {:>8.0f}% {:>9.1f} {:>9.1f}".format(
            r["workers"], r["n"], r["fails"], 100 * r["fails"] / r["n"],
            r["median"], r["wall"]))
        time.sleep(5)   # let the server settle between the two regimes


if __name__ == "__main__":
    main()
