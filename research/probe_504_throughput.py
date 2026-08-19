"""What would a re-tuned download actually cost in machine hours?

Three probes have now established the mechanism:

  * The 504 is a hard 30-second gateway timeout, not "no data for this range".
  * At 45 days over the dense stretch it fires on 71% of SEQUENTIAL requests and
    100% of four-wide ones.  Concurrency compounds it; it is not the cause.
  * At 10-20 days the same dates answered 18 times out of 18, in a few seconds.

So the fix is a smaller window, and the only open question is the price: a
smaller window means proportionally more requests.  This measures the real
throughput at a safe span across a spread of names and dates, so the re-run can
be costed rather than guessed at.
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

SPAN = 15
# Spread the sample across the whole barren band, not one corner of it.
STARTS = [dt.date(2025, 8, 17), dt.date(2025, 10, 31), dt.date(2025, 12, 15),
          dt.date(2026, 2, 8), dt.date(2026, 3, 25), dt.date(2026, 5, 10)]
SYMBOLS = ["NATIONALUM", "RELIANCE", "TATASTEEL", "TATAMOTORS", "SBIN", "ICICIBANK"]


def main():
    equities = sd.equity_ids(sd.load_master())
    jobs = []
    for symbol in SYMBOLS:
        stock = TrackedStock.objects.filter(symbol=symbol).first()
        sec = (stock.security_id if stock else None) or equities.get(symbol)
        if not sec:
            continue
        for start in STARTS:
            jobs.append((sec, "ATM+1", start, start + dt.timedelta(days=SPAN - 1)))
    sd.log("{} windows of {} days across the whole failure band".format(len(jobs), SPAN))

    print("\n  {:>8} {:>6} {:>7} {:>8} {:>9} {:>9} {:>10}".format(
        "workers", "n", "failed", "fail%", "median s", "wall s", "req/hour"))
    print("  " + "-" * 66)
    for workers in (2, 4):
        clock = time.time()
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(lambda a: probe(*a), jobs))
        wall = time.time() - clock
        fails = sum(1 for _, r in results if r.startswith(("HTTP", "network")))
        times = sorted(t for t, _ in results)
        print("  {:>8} {:>6} {:>7} {:>7.0f}% {:>9.2f} {:>9.1f} {:>10,.0f}".format(
            workers, len(jobs), fails, 100 * fails / len(jobs),
            times[len(times) // 2], wall, 3600 * len(jobs) / wall))
        time.sleep(5)

    # What a full re-run would queue, at this span.
    stocks = TrackedStock.objects.filter(is_active=True).count()
    span_windows = len(list(sd.windows(
        dt.date(2026, 8, 17) - dt.timedelta(days=545), dt.date(2026, 8, 17), SPAN)))
    for label, offsets, types in [
        ("CALL+PUT, 6 offsets", 6, 2), ("CALL only, 6 offsets", 6, 1),
        ("CALL only, 4 offsets", 4, 1), ("CALL only, ATM+/-1", 2, 1),
    ]:
        total = stocks * offsets * types * span_windows
        print("  {:<22} {:>9,} windows".format(label, total))


if __name__ == "__main__":
    main()
