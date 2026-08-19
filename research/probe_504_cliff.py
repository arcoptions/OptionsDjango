"""Where exactly does the rolling endpoint give up?

probe_504 established the mechanism: the failures are a hard 30-second gateway
timeout, not missing data.  A 45-day window over a DENSE period times out; the
same 45 days over a sparse period answers in under a second.  The offset is
irrelevant -- plain ATM times out over the same dates too.

So the barren middle band is barren because it has MORE data, not less.

This finds the largest window that still answers, on the densest stretch, so the
downloader can be re-tuned once instead of guessed at.
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

from probe_504 import probe  # noqa: E402

START = dt.date(2025, 8, 17)
SPANS = [10, 12, 14, 16, 18, 20]
# Three names, because one stock's density is not the market's.
SYMBOLS = ["NATIONALUM", "RELIANCE", "TATASTEEL"]


def main():
    equities = sd.equity_ids(sd.load_master())
    print("\n  {:<14} {:>5} {:>8}   {}".format("symbol", "span", "secs", "result"))
    print("  " + "-" * 66)
    for symbol in SYMBOLS:
        stock = TrackedStock.objects.filter(symbol=symbol).first()
        sec = (stock.security_id if stock else None) or equities.get(symbol)
        if not sec:
            print("  {:<14} no security id".format(symbol))
            continue
        for span in SPANS:
            took, result = probe(sec, "ATM+1", START, START + dt.timedelta(days=span - 1))
            print("  {:<14} {:>4}d {:>8.1f}   {}".format(symbol, span, took, result))
            if took > 25:
                print("  {:<14} -> cliff at {}d, stopping".format("", span))
                break
            time.sleep(1.0)


if __name__ == "__main__":
    main()
