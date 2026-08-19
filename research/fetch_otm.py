"""Widen the option cache off the money -- on the side where "off" means cheap.

Everything measured so far came from `relative_strike='ATM'`, which in practice
means 87% of bars sit within 1% of the money.  Every verdict in this project --
buying is 0.77x, the Chartink overlay loses, defined-risk spreads lose -- is
therefore a verdict about AT-THE-MONEY options and says nothing about the thing
the original question was actually about: buy a cheap out-of-the-money contract
and catch a 3x.  That question has never been tested because the data to test it
has never existed.

MONEYNESS IS SIDE-DEPENDENT, which is the whole shape of this run.  ATM+2 is an
out-of-the-money CALL and a deep in-the-money PUT.  So "cheap and out of the
money" means ATM+n on the call side and ATM-n on the put side, and pulling both
sides of every offset would spend half the hours on expensive contracts nobody
asked about.

  ATM+2, ATM+3  CALL   cheap OTM calls
  ATM-1, ATM-2  PUT    cheap OTM puts

Ordered nearest-first and interleaved between the two sides, because
`download_rolling` is strike-major: a run killed halfway leaves both sides
covered at the near offsets rather than one side covered deeply.

WHAT THIS RUN FIXES.  The previous attempt died at 1,600 of 29,484 windows.  The
failures were 973 identical HTTP 504s -- a hard 30-second GATEWAY timeout with an
HTML body, not "no data for this range" -- and they clustered where the data was
DENSEST.  A 45-day window over the busy band failed 71% of the time; the same
dates at 10-20 days answered 18 out of 18.  Retrying does not help: a 504 here is
deterministic and four to six retries still fail.  `ROLLING_WINDOW_DAYS` is now
15, which is the entire fix.  Nothing else in `_post` changed -- a shorter client
timeout was measured and made completion WORSE, because it kills legitimately
slow successes.

The bookkeeping in DownloadJob is keyed on the window boundaries, so shrinking
the window means none of the old 45-day rows count as done.  That is intentional
here (these offsets are empty anyway) but it does mean this cannot be mixed with
a resumed 45-day run.

WHAT THIS STILL CANNOT REACH.  ATM+/-3 is only about +/-4% on a typical 2.5-point
strike ladder.  The genuinely cheap tail -- 70-paise contracts, the ones that
actually 10x -- is further out than the rolling feed goes at all, and needs the
ladder feed with real contract security ids.  This run answers "does 2-4% out of
the money behave differently from at the money", not "do lottery tickets pay".
"""

import datetime as dt
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker import stock_data as sd  # noqa: E402
from options_tracker.models import TrackedStock  # noqa: E402

# (offset, side) in the order they matter. Nearest offsets first and the two
# sides interleaved, so being cut short degrades gracefully in both directions.
LEGS = [
    ("ATM+2", "CALL"),
    ("ATM-1", "PUT"),
    ("ATM+3", "CALL"),
    ("ATM-2", "PUT"),
]

END = dt.date(2026, 8, 17)
START = END - dt.timedelta(days=545)


def main():
    stocks = list(TrackedStock.objects.filter(is_active=True))
    spans = len(list(sd.windows(START, END, sd.ROLLING_WINDOW_DAYS)))
    sd.log("otm: {} stocks, {} -> {}, {}-day windows ({} per leg)".format(
        len(stocks), START, END, sd.ROLLING_WINDOW_DAYS, spans))
    sd.log("otm: {} legs queued -- {}".format(
        len(LEGS), ", ".join(f"{rel} {side}" for rel, side in LEGS)))

    total_rows, total_failed = 0, 0
    for index, (relative, side) in enumerate(LEGS, 1):
        sd.log(f"otm: leg {index}/{len(LEGS)} -- {relative} {side}")
        rows, failures = sd.download_rolling(
            stocks, START, END, relatives=[relative], interval=15,
            expiry_code=1, option_types=(side,),
        )
        total_rows += rows
        total_failed += failures
        sd.log(f"otm: leg {index}/{len(LEGS)} done -- {rows:,} bars, {failures} failed")

    sd.log(f"otm: ALL DONE -- {total_rows:,} bars, {total_failed} failed windows")


if __name__ == "__main__":
    main()
