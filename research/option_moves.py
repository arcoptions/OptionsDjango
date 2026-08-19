"""Does the "options 2-3X easily" thesis survive measurement?

The claim under test is the one that started this: UNIONBANK 190 CE ran 0.70 to
5.00, and 1.40 to 3.30, more than once -- so multi-bagger moves in stock options
must be catchable with a decent rule.

Two things have to be true for that to be a strategy rather than an observation:

  1. The moves have to be common enough to aim at. Measured here as a BASE RATE:
     from a random entry bar, how often does the option double before the
     holding window ends?

  2. Something observable at entry has to raise that base rate. A rule that
     fires on 5% of bars and catches doublers 5% of the time is not a rule.

Getting the instrument right matters more than anything else here. Dhan's
rolling feed is ATM-RELATIVE: ask for "ATM" and each bar returns whatever strike
was at the money at that moment, and the strike changes every five bars or so.
Buying "ATM" on Monday and selling "ATM" on Friday is therefore not a trade --
it silently swaps contracts underneath you and manufactures returns. Every
series here is rebuilt as a FIXED strike inside a single expiry cycle, which is
the only thing a person can actually hold.
"""
import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd
from scipy.special import erfc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import StockOptionCandle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

BARS_PER_DAY = 25          # 15-minute bars in an NSE session
HOLDS = [("1d", 25), ("2d", 50), ("5d", 125)]
MULTIPLES = [1.5, 2.0, 3.0]

# One-way friction on a stock option. The tick is 5 paise, so the quoted spread
# alone is 7% of a 70-paisa premium; brokerage and taxes are small beside it.
# Measured from the account: charges ran 0.14% of turnover per side.
TICK = 0.05
RATE = 0.065


def bs_price(spot, strike, years, vol, is_call):
    """Black-Scholes, used only as a plausibility yardstick for a quote."""
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    years = np.clip(np.asarray(years, dtype=float), 1 / 365 / 8, None)
    vol = np.clip(np.asarray(vol, dtype=float), 1e-4, None)
    root = vol * np.sqrt(years)
    d1 = (np.log(spot / strike) + (RATE + 0.5 * vol ** 2) * years) / root
    d2 = d1 - root
    normal = lambda x: 0.5 * erfc(-x / np.sqrt(2))
    call = spot * normal(d1) - strike * np.exp(-RATE * years) * normal(d2)
    put = strike * np.exp(-RATE * years) * normal(-d2) - spot * normal(-d1)
    return np.where(is_call, call, put)


def monthly_expiry(year, month):
    """NSE stock options expire on the last Thursday of the month."""
    day = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - dt.timedelta(days=1)
    while last.weekday() != 3:
        last -= dt.timedelta(days=1)
    return last


def cycle_of(day):
    """Which monthly contract a date belongs to, given expiryCode=1."""
    expiry = monthly_expiry(day.year, day.month)
    if day > expiry:
        nxt = dt.date(day.year + (day.month == 12), day.month % 12 + 1, 1)
        return nxt.year * 100 + nxt.month
    return day.year * 100 + day.month


def load():
    rows = StockOptionCandle.objects.exclude(relative_strike__startswith="K").values_list(
        "symbol", "timestamp", "option_type", "relative_strike", "strike", "spot",
        "open", "high", "low", "close", "volume", "oi", "implied_volatility",
    )
    frame = pd.DataFrame(rows, columns=[
        "symbol", "ts", "option_type", "rel", "strike", "spot",
        "open", "high", "low", "close", "volume", "oi", "iv",
    ])
    if frame.empty:
        return frame
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for column in ("strike", "spot", "open", "high", "low", "close", "iv"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)

    # Drop the small tail of unusable quotes: a price below intrinsic, a zero
    # premium, or a bar the feed never priced at all.
    intrinsic = np.where(frame["option_type"] == "CALL",
                         (frame["spot"] - frame["strike"]).clip(lower=0),
                         (frame["strike"] - frame["spot"]).clip(lower=0))
    sane = (
        frame["close"].gt(0) & frame["open"].gt(0) & frame["high"].ge(frame["low"])
        & frame["close"].ge(intrinsic - 0.10)
        & frame["close"].lt(frame["spot"] * 0.6)
        & frame["spot"].gt(0)
    )
    frame = frame[sane].copy()
    frame["cycle"] = frame["ts"].dt.date.map(cycle_of)
    frame["expiry"] = frame["cycle"].map(
        lambda c: monthly_expiry(c // 100, c % 100)
    )
    frame["dte"] = (pd.to_datetime(frame["expiry"]) - frame["ts"].dt.normalize()).dt.days
    frame["moneyness"] = np.where(
        frame["option_type"] == "CALL",
        (frame["spot"] / frame["strike"] - 1) * 100,
        (frame["strike"] / frame["spot"] - 1) * 100,
    )

    # A freshly rolled contract that has not traded yet is quoted at a nominal
    # five paise, and the feed happily serves it: TRENT 4700 CALL was shown at
    # 0.05 with spot 4673 and thirty days to run, when it was plainly worth
    # about a hundred and fifty rupees. Those quotes are not merely noisy, they
    # manufacture thousand-bagger returns two bars later when real trading
    # starts. Dhan gives us its own IV on the same bar, so price the option from
    # that and throw away quotes that disagree with it by more than 3x either
    # way -- the feed contradicting itself is the tell.
    theoretical = bs_price(
        frame["spot"], frame["strike"], frame["dte"].clip(lower=0) / 365,
        frame["iv"] / 100, frame["option_type"].eq("CALL"),
    )
    frame["fair"] = theoretical
    usable = frame["iv"].between(1, 250) & (theoretical > 0.20)
    consistent = frame["close"].between(theoretical * 0.33, theoretical * 3.0)
    frame = frame[usable & consistent].copy()

    # One row per contract-bar: the same strike arrives under several relative
    # labels as spot drifts, and they are the same contract.
    frame = frame.sort_values("volume", ascending=False).drop_duplicates(
        ["symbol", "option_type", "strike", "cycle", "ts"]
    )
    return frame.sort_values(["symbol", "option_type", "strike", "cycle", "ts"]).reset_index(drop=True)


def forward_stats(frame):
    """Max favourable multiple and end multiple per contract series."""
    out = []
    keys = ["symbol", "option_type", "strike", "cycle"]
    for _, series in frame.groupby(keys, sort=False):
        if len(series) < 6:
            continue
        series = series.reset_index(drop=True)
        entry = series["open"].shift(-1)           # tradeable: next bar's open
        record = series[["symbol", "option_type", "strike", "cycle", "ts", "dte",
                         "moneyness", "iv", "oi", "volume", "spot", "close", "fair"]].copy()
        record["entry"] = entry
        for label, ahead in HOLDS:
            # min_periods=ahead so the window is always the full holding period;
            # a truncated window at the end of a cycle would understate MFE and
            # is not the same trade.
            top = (series["high"].shift(-1).rolling(ahead, min_periods=ahead)
                   .max().shift(-(ahead - 1)))
            end = series["close"].shift(-ahead)
            record[f"mfe_{label}"] = top / entry
            record[f"end_{label}"] = end / entry
        out.append(record)
    if not out:
        return pd.DataFrame()
    result = pd.concat(out, ignore_index=True)
    return result[result["entry"].gt(0)].copy()


def base_rates(frame):
    print(f"\n{'=' * 88}\nBASE RATE -- from a random bar, how often does the option run?")
    print(f"{len(frame):,} tradeable entry bars, {frame['symbol'].nunique()} symbols, "
          f"{frame['cycle'].nunique()} expiry cycles\n")
    print(f"{'HOLD':>6} {'n':>8}" + "".join(f"{'>=' + str(m) + 'x':>9}" for m in MULTIPLES)
          + f"{'median':>9}{'mean end':>10}{'>=2x end':>10}")
    for label, _ in HOLDS:
        column = frame[f"mfe_{label}"].dropna()
        end = frame[f"end_{label}"].dropna()
        if column.empty:
            continue
        line = f"{label:>6} {len(column):>8,}"
        for multiple in MULTIPLES:
            line += f"{(column >= multiple).mean() * 100:8.2f}%"
        line += f"{column.median():9.2f}{end.mean():10.2f}{(end >= 2).mean() * 100:9.2f}%"
        print(line)
    print("\n  MFE is the best price the option printed while held -- the ceiling a")
    print("  perfectly-timed exit could have caught. 'end' is what you get if you")
    print("  simply hold to the horizon. The gap between them is the exit problem.")


def by_bucket(frame, column, bins, labels, title, hold="2d"):
    print(f"\n{'-' * 88}\n{title}")
    cut = pd.cut(frame[column], bins=bins, labels=labels)
    grouped = frame.groupby(cut, observed=True)
    print(f"{'BUCKET':>16} {'n':>8}" + "".join(f"{'>=' + str(m) + 'x':>9}" for m in MULTIPLES)
          + f"{'mean end':>10}{'median end':>12}")
    for name, group in grouped:
        mfe = group[f"mfe_{hold}"].dropna()
        end = group[f"end_{hold}"].dropna()
        if len(mfe) < 200:
            continue
        line = f"{str(name):>16} {len(mfe):>8,}"
        for multiple in MULTIPLES:
            line += f"{(mfe >= multiple).mean() * 100:8.2f}%"
        line += f"{end.mean():10.2f}{end.median():12.2f}"
        print(line)


def cost_reality(frame, hold="2d"):
    """What the tick alone does to a doubling strategy."""
    print(f"\n{'=' * 88}\nWHAT THE SPREAD DOES (hold {hold})")
    print("A stock option trades in 5-paise ticks. Crossing the spread once costs")
    print("half a tick if you are lucky and a full tick if you are not; here it is")
    print("charged as one full tick in and one out, plus 0.28% of turnover in taxes.\n")
    print(f"{'PREMIUM AT ENTRY':>18} {'n':>8} {'tick as %':>11} {'raw >=2x':>10} "
          f"{'net >=2x':>10} {'mean net':>10}")
    bands = [(0.05, 1), (1, 2), (2, 5), (5, 10), (10, 25), (25, 1e9)]
    for low, high in bands:
        group = frame[frame["entry"].between(low, high, inclusive="left")]
        mfe = group[f"mfe_{hold}"].dropna()
        end = group[f"end_{hold}"].dropna()
        if len(mfe) < 200:
            continue
        entry = group.loc[mfe.index, "entry"]
        # Pay a tick up on entry, receive a tick down on exit, then taxes.
        paid = entry + TICK
        got_mfe = (mfe * entry - TICK) * (1 - 0.0028)
        got_end = (end * group.loc[end.index, "entry"] - TICK) * (1 - 0.0028)
        net_mfe = got_mfe / paid
        net_end = got_end / (group.loc[end.index, "entry"] + TICK)
        label = f"{low:g}-{high:g}" if high < 1e9 else f"{low:g}+"
        print(f"{label:>18} {len(mfe):>8,} {TICK / entry.median() * 100:10.1f}% "
              f"{(mfe >= 2).mean() * 100:9.2f}% {(net_mfe >= 2).mean() * 100:9.2f}% "
              f"{net_end.mean():10.2f}")


def main():
    print("loading rolling option bars...")
    frame = load()
    if frame.empty:
        print("no rolling option data yet")
        return
    print(f"{len(frame):,} clean contract-bars, {frame['symbol'].nunique()} symbols, "
          f"{frame['ts'].min():%d %b %Y} -> {frame['ts'].max():%d %b %Y}")

    stats = forward_stats(frame)
    if stats.empty:
        print("not enough per-contract history yet")
        return
    stats.to_parquet(os.path.join(HERE, "option_moves.parquet"))

    base_rates(stats)
    calls = stats[stats["option_type"] == "CALL"]

    by_bucket(stats, "entry", [0, 1, 2, 5, 10, 25, 1e9],
              ["<1", "1-2", "2-5", "5-10", "10-25", "25+"],
              "By premium at entry (2-day hold) -- cheap options move in multiples")
    by_bucket(stats, "moneyness", [-100, -10, -5, -2, 0, 2, 5, 100],
              ["<-10 OTM", "-10..-5", "-5..-2", "-2..0", "0..2", "2..5", ">5 ITM"],
              "By moneyness (2-day hold) -- negative is out of the money")
    by_bucket(stats, "dte", [0, 3, 7, 14, 21, 60], ["0-3", "3-7", "7-14", "14-21", "21+"],
              "By days to expiry (2-day hold)")
    by_bucket(calls, "iv", [0, 25, 35, 45, 60, 1000],
              ["<25", "25-35", "35-45", "45-60", "60+"],
              "Calls by implied volatility at entry (2-day hold)")

    cost_reality(stats)

    print(f"\n{'=' * 88}\nTHE THESIS, STATED AS A NUMBER")
    two = stats["mfe_2d"].dropna()
    print(f"  A randomly chosen stock option, held two days, touches 2x "
          f"{(two >= 2).mean() * 100:.2f}% of the time.")
    print(f"  Held to the end of those two days it is worth "
          f"{stats['end_2d'].dropna().mean():.2f}x on average.")
    cheap = stats[stats["entry"] < 2]["mfe_2d"].dropna()
    print(f"  Restricted to options under Rs 2 -- the UNIONBANK case -- the 2x rate "
          f"is {(cheap >= 2).mean() * 100:.2f}%.")


if __name__ == "__main__":
    main()
