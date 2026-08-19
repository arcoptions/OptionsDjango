"""The steelman: expiry-day gamma, tested properly.

Every diagnostic so far points the same way. The contracts that ran 3x or more
were not multi-day swing trades -- their median age at the low was TWO DAYS TO
EXPIRY and their median moneyness was zero. They are expiry-day at-the-money
options, where gamma is enormous and time value is nearly gone. That is the real
shape of "UNIONBANK 190 CE went 0.70 to 5.00", and it is a different trade from
the one the fourteen entry rules were testing.

So this tests it on its own terms:

  - only DTE 0 and 1, only within 1.5% of the money, only liquid contracts
  - broken out by TIME OF DAY, because on expiry day the clock is the variable
    that matters and a 09:30 entry is a different instrument from a 14:30 one
  - with a directional filter, because buying a call at 09:30 on a day the stock
    is already down is not the trade anyone means
  - net of the tick both ways, as everywhere else

If a positive expectancy exists anywhere in this dataset, it is here.
"""
import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from option_strategy import build, simulate  # noqa: E402
from options_tracker.models import StockEquityCandle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def since_open(symbols):
    """How far the stock has travelled from today's open, at every bar."""
    rows = StockEquityCandle.objects.filter(
        symbol__in=list(symbols), interval_minutes=15
    ).values_list("symbol", "timestamp", "open", "close")
    frame = pd.DataFrame(rows, columns=["symbol", "ts", "open", "close"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    frame["open"] = frame["open"].astype(float)
    frame["close"] = frame["close"].astype(float)
    frame["day"] = frame["ts"].dt.date
    frame = frame.sort_values(["symbol", "ts"])
    day_open = frame.groupby(["symbol", "day"])["open"].transform("first")
    frame["from_open"] = (frame["close"] / day_open - 1) * 100
    return frame[["symbol", "ts", "from_open"]]


def table(name, frame, mask, results, minimum=100):
    """Expectancy by entry time of day, under several exits."""
    print(f"\n{'-' * 104}\n{name}   ({int(mask.sum()):,} candidate bars)")
    print(f"{'ENTRY SLOT':>12} {'n':>7}" + "".join(f"{label:>17}" for label in results))
    slots = frame.loc[mask, "ts"].dt.strftime("%H:%M")
    for slot in sorted(slots.unique()):
        rows = mask & (frame["ts"].dt.strftime("%H:%M") == slot).to_numpy()
        line, printed = f"{slot:>12} {int(rows.sum()):7,}", False
        for label, result in results.items():
            net = result["net"][rows]
            net = net[np.isfinite(net)]
            if len(net) < minimum:
                line += f"{'-':>17}"
                continue
            printed = True
            line += f"   {net.mean():6.3f} ({(net > 1).mean() * 100:4.1f}%)"
        if printed:
            print(line)
    line, any_row = f"{'ALL DAY':>12} {int(mask.sum()):7,}", False
    for label, result in results.items():
        net = result["net"][mask]
        net = net[np.isfinite(net)]
        if len(net) < minimum:
            line += f"{'-':>17}"
            continue
        any_row = True
        line += f"   {net.mean():6.3f} ({(net > 1).mean() * 100:4.1f}%)"
    if any_row:
        print(f"{'=' * 104}\n{line}")


def main():
    frame = build()
    if frame.empty:
        print("no data")
        return
    stock = since_open(set(frame["symbol"].unique()))
    frame = frame.merge(stock, on=["symbol", "ts"], how="left")

    print(f"{len(frame):,} contract-bars in the panel")

    # Expiry-day exits are short by construction: a 50-bar hold is meaningless
    # on a contract that expires this afternoon.
    results = {
        "hold to close": simulate(frame, None, 0.0, None, max_bars=25),
        "2x / stop 0.5x": simulate(frame, 2.0, 0.50, None, max_bars=25),
        "trail 30%": simulate(frame, None, 0.40, 0.70, max_bars=25),
    }

    calls = frame["option_type"].eq("CALL").to_numpy()
    liquid = (frame["volume"].gt(0) & frame["oi"].gt(0)).to_numpy()
    expiry_day = frame["dte"].le(1).to_numpy()
    atm = frame["moneyness"].abs().le(1.5).to_numpy()
    with_stock = (frame["from_open"] > 0.3).to_numpy()
    against = (frame["from_open"] < -0.3).to_numpy()

    print(f"\n{'=' * 104}\nEXPIRY-DAY ATM OPTIONS, BY ENTRY TIME OF DAY")
    print("Each cell is mean net multiple after the tick, with the win rate beside it.")
    print("Anything at or above 1.000 makes money; everything below hands it over.")

    table("all expiry-day ATM calls", frame, expiry_day & atm & calls & liquid, results)
    table("expiry-day ATM calls, stock up >0.3% from open",
          frame, expiry_day & atm & calls & liquid & with_stock, results)
    table("expiry-day ATM calls, stock DOWN >0.3% from open (the fade)",
          frame, expiry_day & atm & calls & liquid & against, results)
    table("expiry-day ATM calls, breakout on the stock",
          frame, expiry_day & atm & calls & liquid
          & frame["breakout"].fillna(False).to_numpy(), results)

    otm = frame["moneyness"].between(-4, -1.5).to_numpy()
    table("expiry-day OTM 1.5-4% calls (the lottery ticket)",
          frame, expiry_day & otm & calls & liquid, results)

    print(f"\n{'=' * 104}\nTHE SAME TRADES, ONE DAY EARLIER (DTE 2-4) FOR CONTRAST")
    week = frame["dte"].between(2, 4).to_numpy()
    table("DTE 2-4 ATM calls", frame, week & atm & calls & liquid, results)

    print(f"\n{'=' * 104}\nHOW OFTEN THE 2x ACTUALLY ARRIVES ON EXPIRY DAY")
    hold = results["hold to close"]
    for label, mask in [("expiry ATM calls", expiry_day & atm & calls & liquid),
                        ("expiry OTM calls", expiry_day & otm & calls & liquid),
                        ("DTE 2-4 ATM calls", week & atm & calls & liquid)]:
        gross = hold["gross"][mask]
        gross = gross[np.isfinite(gross)]
        net = hold["net"][mask]
        net = net[np.isfinite(net)]
        if len(gross) < 100:
            continue
        print(f"  {label:22s} n={len(gross):6,}  reaches 2x {(gross >= 2).mean() * 100:5.2f}%  "
              f"goes to zero-ish (<0.2x) {(gross < 0.2).mean() * 100:5.2f}%  "
              f"net expectancy {net.mean():.3f}")

    print(f"\n{'=' * 104}")
    print("READ: the 2x rate on expiry-day ATM calls is many times the all-bars base")
    print("rate -- the user's observation is correct, the moves are there. The column")
    print("that decides whether it is a strategy is the last one.")


if __name__ == "__main__":
    main()
