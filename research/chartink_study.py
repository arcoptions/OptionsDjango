"""What happened after each Chartink trigger, measured against a control.

The two scans are taken as ground truth from their own backtest exports rather
than re-implemented from the screenshots: the export says exactly which symbol
fired at exactly which candle, so there is no risk of my reading of the rule
quietly differing from Chartink's.

Two things make the raw forward return a liar, and both are corrected here:

  Entry price.  Chartink stamps a trigger with the candle's START, not its
  close -- both exports contain 09:15 triggers, and no candle has finished at
  the opening bell. So a 15-minute trigger stamped 09:15 describes the
  09:15-09:30 candle and is only knowable at 09:30; an hourly one stamped 09:15
  describes 09:15-10:15 and is only knowable at 10:15. Entering at the stamped
  bar would mean buying with the signal candle's own move already in hand,
  which is pure look-ahead and inflates an hourly breakout scan by well over a
  percent. Entry here is the OPEN OF THE BAR AFTER THE SIGNAL CANDLE CLOSES.

  Market drift.  A breakout scan fires when stocks are breaking out, which is
  when the whole market is moving. So the forward return after a trigger is
  partly just "the market went up in the next fifteen minutes". For every
  trigger we therefore also compute what the average stock in the same 172-name
  universe did over the identical clock window, and report the difference.
  That EXCESS number is the only one that says anything about the scan.

The two exports also cover different date ranges (the 15-min one is seven
trading days in August, the 1-hour one is twenty-three from mid-July), so the
head-to-head is additionally restricted to the overlapping window.
"""
import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import StockEquityCandle  # noqa: E402

DOWNLOADS = os.path.expanduser("~/Downloads")
HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

SCANS = {
    # name: (export, 15-minute bars spanned by the signal candle)
    "ARC15MIN": ("Backtest arc15min.csv", 1),
    "NARC1HR": ("Backtest narc1hr.csv", 4),
}

# Bars ahead on the 15-minute grid; 25 bars is one full session.
HORIZONS = [("15m", 1), ("30m", 2), ("1h", 4), ("2h", 8), ("EOD", 25), ("2d", 50), ("5d", 125)]


def load_triggers(filename):
    frame = pd.read_csv(os.path.join(DOWNLOADS, filename))
    frame.columns = [c.strip().strip('"').lstrip("﻿") for c in frame.columns]
    frame["when"] = pd.to_datetime(frame["Date"], format="%d-%m-%Y %I:%M %p")
    frame["Symbol"] = frame["Symbol"].str.strip().str.upper()
    return frame[["when", "Symbol", "Marketcapname", "Sector"]].dropna()


def load_panel(symbols):
    """One aligned price panel: {field: DataFrame indexed by time, columns symbols}."""
    rows = StockEquityCandle.objects.filter(
        symbol__in=list(symbols), interval_minutes=15
    ).values_list("symbol", "timestamp", "open", "high", "low", "close")
    frame = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].astype(float)
    panel = {
        field: frame.pivot_table(index="ts", columns="symbol", values=field, aggfunc="last")
        for field in ("open", "high", "low", "close")
    }
    return panel, frame["symbol"].nunique(), len(frame)


def forward_tables(panel):
    """Per horizon: forward return, MFE and MAE for every (bar, symbol) cell.

    Vectorised across the whole panel, so the universe control at a given
    timestamp is just the mean of that row.
    """
    entry = panel["open"]
    tables = {}
    for label, ahead in HORIZONS:
        exit_close = panel["close"].shift(-ahead)
        top = panel["high"].rolling(ahead + 1, min_periods=1).max().shift(-ahead)
        bottom = panel["low"].rolling(ahead + 1, min_periods=1).min().shift(-ahead)
        tables[label] = {
            "ret": (exit_close / entry - 1) * 100,
            "mfe": (top / entry - 1) * 100,
            "mae": (bottom / entry - 1) * 100,
        }
    return tables


def study(name, filename, signal_bars, panel, tables):
    triggers = load_triggers(filename)
    index = panel["open"].index
    columns = set(panel["open"].columns)

    records, missing = [], 0
    for _, trigger in triggers.iterrows():
        symbol = trigger["Symbol"]
        if symbol not in columns:
            missing += 1
            continue
        # The stamp is the signal candle's start; step past its close to the
        # first bar a person could actually have bought.
        stamped = index.searchsorted(trigger["when"], side="left")
        position = stamped + signal_bars
        if position >= len(index) or stamped >= len(index):
            missing += 1
            continue
        stamp = index[position]
        # A trigger that lands more than an hour from any bar we hold is a hole
        # in our data, not a trade.
        if abs((index[stamped] - trigger["when"]).total_seconds()) > 3600:
            missing += 1
            continue
        if not np.isfinite(panel["open"].at[stamp, symbol]):
            missing += 1
            continue

        signal_open = panel["open"].at[index[stamped], symbol]
        entry = float(panel["open"].at[stamp, symbol])
        record = {"scan": name, "symbol": symbol, "when": trigger["when"], "bar": stamp,
                  "sector": trigger["Sector"], "cap": trigger["Marketcapname"], "entry": entry,
                  "signal_move": (entry / float(signal_open) - 1) * 100
                                 if np.isfinite(signal_open) and signal_open else np.nan}
        for label, _ in HORIZONS:
            table = tables[label]
            value = table["ret"].at[stamp, symbol]
            control = table["ret"].loc[stamp].mean()
            record[f"ret_{label}"] = value
            record[f"ctl_{label}"] = control
            record[f"exc_{label}"] = value - control
            record[f"mfe_{label}"] = table["mfe"].at[stamp, symbol]
            record[f"mae_{label}"] = table["mae"].at[stamp, symbol]
            record[f"mfe_ctl_{label}"] = table["mfe"].loc[stamp].mean()
        records.append(record)

    return pd.DataFrame(records), missing


def describe(frame, name, missing):
    print(f"\n{'=' * 92}\n{name}   {len(frame)} usable triggers  ({missing} unmatched)")
    if frame.empty:
        return
    print(f"span {frame['when'].min():%d %b %Y} -> {frame['when'].max():%d %b %Y}   "
          f"{frame['symbol'].nunique()} symbols   {frame['when'].dt.date.nunique()} trading days")
    print(f"\n{'HORIZON':>8} {'RAW%':>8} {'UNIVERSE%':>10} {'EXCESS%':>9} {'t-stat':>7} "
          f"{'WIN%':>7} {'BEAT%':>7} {'MFE%':>7} {'MAE%':>7} {'n':>5}")
    for label, _ in HORIZONS:
        raw = frame[f"ret_{label}"].dropna()
        excess = frame[f"exc_{label}"].dropna()
        if excess.empty:
            continue
        t_stat = excess.mean() / (excess.std(ddof=1) / np.sqrt(len(excess))) if excess.std(ddof=1) else 0
        print(f"{label:>8} {raw.mean():8.3f} {frame[f'ctl_{label}'].mean():10.3f} "
              f"{excess.mean():9.3f} {t_stat:7.2f} {(raw > 0).mean() * 100:6.1f}% "
              f"{(excess > 0).mean() * 100:6.1f}% {frame[f'mfe_{label}'].mean():7.3f} "
              f"{frame[f'mae_{label}'].mean():7.3f} {len(excess):5d}")


def overlap_compare(frames):
    windows = [(f["when"].min(), f["when"].max()) for f in frames.values() if not f.empty]
    start = max(w[0] for w in windows)
    end = min(w[1] for w in windows)
    print(f"\n{'=' * 92}\nHEAD TO HEAD on the overlapping window only "
          f"({start:%d %b %Y} -> {end:%d %b %Y})")
    clipped = {
        name: frame[(frame["when"] >= start) & (frame["when"] <= end)]
        for name, frame in frames.items()
    }
    for name, frame in clipped.items():
        print(f"  {name}: {len(frame)} triggers")
    print(f"\n{'HORIZON':>8}" + "".join(f"{name:>32}" for name in clipped))
    for label, _ in HORIZONS:
        line = f"{label:>8}"
        for frame in clipped.values():
            excess = frame[f"exc_{label}"].dropna()
            if excess.empty:
                line += f"{'-':>32}"
            else:
                line += (f"  raw {frame[f'ret_{label}'].mean():6.2f}%"
                         f"  excess {excess.mean():6.2f}%  beat {(excess > 0).mean() * 100:5.1f}%")
        print(line)
    return clipped


def main():
    triggers = {name: load_triggers(f) for name, (f, _) in SCANS.items()}
    symbols = set().union(*(t["Symbol"] for t in triggers.values()))
    print(f"{len(symbols)} distinct symbols across both scans")

    panel, have, bars = load_panel(symbols)
    print(f"{have}/{len(symbols)} have 15-minute history locally ({bars:,} bars)")
    print("building forward-return tables...")
    tables = forward_tables(panel)

    frames = {}
    for name, (filename, signal_bars) in SCANS.items():
        frame, missing = study(name, filename, signal_bars, panel, tables)
        frames[name] = frame
        describe(frame, name, missing)
        if not frame.empty:
            move = frame["signal_move"].dropna()
            print(f"  the signal candle itself moved {move.mean():+.3f}% on average "
                  f"({(move > 0).mean() * 100:.1f}% of the time upward) -- this is the part "
                  f"that is NOT tradeable")
        frame.to_csv(os.path.join(HERE, f"chartink_{name}.csv"), index=False)

    clipped = overlap_compare(frames)

    print(f"\n{'=' * 92}\nIS THE EXCESS CONCENTRATED OR BROAD?")
    for name, frame in frames.items():
        if frame.empty:
            continue
        excess = frame["exc_1h"].dropna()
        by_day = frame.groupby(frame["when"].dt.date)["exc_1h"].mean().dropna()
        print(f"\n{name}: 1h excess by trading day  "
              f"({(by_day > 0).sum()}/{len(by_day)} days positive)")
        for day, value in by_day.items():
            bar = "#" * min(int(abs(value) * 20), 40)
            print(f"  {day}  {value:7.3f}%  {'' if value >= 0 else '-'}{bar}")
        if len(excess) > 4:
            print(f"  best single trigger {excess.max():.2f}%, worst {excess.min():.2f}%, "
                  f"top 5 contribute {excess.nlargest(5).sum() / len(excess):.3f}% of the "
                  f"{excess.mean():.3f}% mean")


if __name__ == "__main__":
    main()
