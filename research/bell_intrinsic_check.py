"""Is an option really worth its intrinsic at the closing bell?

The near-expiry spread study turns entirely on one repair.  Roughly four out of
five exits could not be priced from quotes, because the cache holds ATM and
ATM+1 RELATIVE TO A MOVING SPOT and a strike pinned at entry stops being quoted
once spot walks away from it.  Dropping those exits flatters a short call spread
enormously -- they are missing precisely when the market ran up, which is the
losing direction -- so they are imputed at intrinsic instead.

That repair is only as good as its premise: at expiry an option has no time value
left, so its price IS max(spot - strike, 0).  The premise is textbook, but it is
also the load-bearing assumption behind a result that reversed a headline from
+15% to -17%, and it is checkable.  Wherever a strike WAS still quoted at the
bell, the quote and the intrinsic can be compared directly.

If they agree there, the rule is sound where it was applied blind.
"""

import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from option_spreads import find_expiries, load_calls, log  # noqa: E402


def main():
    frame = load_calls()
    expiries = set(find_expiries(frame))
    log("loaded {:,} bars, {} expiries".format(len(frame), len(expiries)))

    frame["date"] = frame.ts.dt.date
    bell = frame[frame.date.isin(expiries)].copy()
    # The last bar of expiry day, per symbol -- options run to 15:39 while the
    # index stops at 15:29, so take the symbol's own last timestamp.
    last = bell.groupby(["symbol", "date"]).ts.transform("max")
    bell = bell[(bell.ts == last) & bell.spot.notna() & bell.close.notna()]
    bell["intrinsic"] = (bell.spot - bell.strike).clip(lower=0)
    bell["err"] = bell.close - bell.intrinsic
    log("{:,} strike-quotes at the closing bell of an expiry day, {} symbols".format(
        len(bell), bell.symbol.nunique()))

    log("\n" + "-" * 78)
    log("QUOTED CLOSE MINUS INTRINSIC, at the bell")
    log("-" * 78)
    log("  median Rs{:+.2f}   mean Rs{:+.2f}   p5 Rs{:+.2f}   p95 Rs{:+.2f}".format(
        bell.err.median(), bell.err.mean(), bell.err.quantile(0.05),
        bell.err.quantile(0.95)))
    log("  within Rs0.05: {:.1f}%    within Rs0.50: {:.1f}%    within Rs2: {:.1f}%".format(
        (bell.err.abs() <= 0.05).mean() * 100, (bell.err.abs() <= 0.50).mean() * 100,
        (bell.err.abs() <= 2.0).mean() * 100))

    # The imputation only ever fires on strikes spot has walked AWAY from, so
    # the error near the money is the least relevant part of this table.
    log("\n  by distance from the money (strike vs spot, in %):")
    log("  {:<16} {:>10} {:>10} {:>10} {:>10}".format(
        "moneyness", "median err", "mean err", "median Rs", "n"))
    bell["m"] = (bell.spot / bell.strike - 1) * 100
    bands = [(-99, -5, "deep OTM <-5%"), (-5, -1, "OTM -5..-1%"),
             (-1, 1, "at the money"), (1, 5, "ITM 1..5%"), (5, 99, "deep ITM >5%")]
    for lo, hi, name in bands:
        v = bell[(bell.m > lo) & (bell.m <= hi)]
        if v.empty:
            continue
        log("  {:<16} {:>+10.2f} {:>+10.2f} {:>10.2f} {:>10,d}".format(
            name, v.err.median(), v.err.mean(), v.close.median(), len(v)))

    log("\n  A short spread is only hurt by an UNDERSTATED close, which would make its")
    log("  buy-back look cheaper than it was. Closes below intrinsic: {:.1f}% of quotes,"
        .format((bell.err < -0.05).mean() * 100))
    log("  median Rs{:+.2f} where it happens.".format(
        bell[bell.err < -0.05].err.median() if (bell.err < -0.05).any() else np.nan))


if __name__ == "__main__":
    main()
