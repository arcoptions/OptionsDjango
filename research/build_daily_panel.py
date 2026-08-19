"""Build a daily OHLCV panel from the 15-minute equity cache.

The stock-option programme measured everything on option prices. This builds the
underlying panel instead, because the question changed: not "what is a move
worth to an option buyer" (answered, it is worth less than the toll) but "can a
breakout be seen coming in the stock". At stock-level friction of roughly 10
basis points rather than the option's 164, a much smaller edge is still real.

Source is the local SQLite cache -- 189 F&O symbols, 15-minute bars, Feb 2025 to
Aug 2026. Timestamps in that table are UTC (03:45 is the 09:15 IST open), so
they are shifted to IST before being grouped into sessions, otherwise every bar
after 18:30 IST would land on the wrong day. There are none that late, but the
shift is what makes that statement checkable rather than lucky.

Writes research/daily_panel.csv.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DB = Path(__file__).resolve().parent.parent / "db.sqlite3"
OUT = Path(__file__).resolve().parent / "daily_panel.csv"
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)


def build() -> pd.DataFrame:
    with sqlite3.connect(DB) as conn:
        raw = pd.read_sql(
            """
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM options_tracker_stockequitycandle
            WHERE interval_minutes = 15
              AND close IS NOT NULL AND close > 0
            """,
            conn,
        )

    raw["timestamp"] = pd.to_datetime(raw["timestamp"], format="mixed") + IST_OFFSET
    for column in ("open", "high", "low", "close"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = raw.dropna(subset=["open", "high", "low", "close"])
    raw["date"] = raw["timestamp"].dt.normalize()
    raw = raw.sort_values(["symbol", "timestamp"])

    # Session OHLC from the intraday bars. first/last rather than open/close
    # columns wholesale, because a symbol that missed the 09:15 bar should take
    # its open from the first bar it actually traded, not from a NaN.
    daily = raw.groupby(["symbol", "date"]).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        bars=("close", "size"),
    ).reset_index()

    # A session with a handful of bars is a partial feed, not a real day, and it
    # produces fake narrow ranges -- which is exactly what an NR7 or a squeeze
    # detector would light up on. Drop them.
    full = daily["bars"].median()
    daily = daily[daily["bars"] >= full * 0.6].copy()

    daily = daily[daily["high"] >= daily["low"]]
    return daily.sort_values(["symbol", "date"]).reset_index(drop=True)


if __name__ == "__main__":
    panel = build()
    panel.to_csv(OUT, index=False)
    sessions = panel["date"].nunique()
    print(f"symbols        {panel['symbol'].nunique()}")
    print(f"sessions       {sessions}")
    print(f"stock-days     {len(panel):,}")
    print(f"range          {panel['date'].min().date()} -> "
          f"{panel['date'].max().date()}")
    print(f"median bars/dy {panel['bars'].median():.0f}")
    print(f"written        {OUT}")
