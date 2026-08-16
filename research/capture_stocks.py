"""Capture 1-minute constituent bars so the research can see inside the index.

Everything tested so far has read either the index price or the index option
chain, and the anatomy study showed the chain is blind at turning points. The
constituents are the first genuinely new information: NIFTY is a weighted sum of
fifty stocks, so it cannot move unless they move, which puts them causally
upstream of every signal we have been trying to find.

Dhan serves 90 days of 1-minute equity candles per request, so each symbol needs
a chain of windows to cover the option cache. Output mirrors the existing cache
layout, one npz per session:

    research/cache/STOCKS/<date>.npz
        symbols (nK,)  minute (nM,)  close (nK, nM)  volume (nK, nM)

Minutes are indexed from 09:15 exactly as the option cache is, so a stock row
lines up with a spot row without any date arithmetic at read time.
"""
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dhan_probe import equity_master, intraday

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "STOCKS")
START, END = "2025-08-18", "2026-08-14"
OPEN_MINUTE = 555  # 09:15 IST in minutes past midnight
SESSION_MINUTES = 375
WINDOW_DAYS = 88
PAUSE = 0.35

# NIFTY 50. Any symbol the master cannot resolve is reported and skipped rather
# than silently dropped -- a missing heavyweight would bias every breadth number.
CONSTITUENTS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

# The index future, for a real traded VWAP and a real basis. Resolved at runtime
# from the master so the nearest contract is always the one used.
FUTURE_SYMBOL = "NIFTY"


def windows(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    cursor = first
    while cursor <= last:
        stop = min(cursor + timedelta(days=WINDOW_DAYS), last)
        out.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return out


def fetch_symbol(security_id, segment, instrument):
    """{session_date: {minute_index: (close, volume)}} across the whole span."""
    series = defaultdict(dict)
    for start, stop in windows(START, END):
        data = intraday(security_id, segment, instrument, start, stop)
        if "error" in data:
            print(f"      {start}..{stop}  {data['error']}")
            time.sleep(PAUSE)
            continue
        stamps = data.get("timestamp") or []
        closes = data.get("close") or []
        volumes = data.get("volume") or []
        for index, epoch in enumerate(stamps):
            moment = datetime.fromtimestamp(epoch)
            minute = moment.hour * 60 + moment.minute - OPEN_MINUTE
            if not 0 <= minute < SESSION_MINUTES:
                continue
            close = closes[index] if index < len(closes) else None
            volume = volumes[index] if index < len(volumes) else 0
            if close:
                series[moment.date().isoformat()][minute] = (float(close),
                                                             float(volume or 0))
        time.sleep(PAUSE)
    return series


def main():
    os.makedirs(OUT, exist_ok=True)
    master = equity_master()
    resolved, missing = {}, []
    for symbol in CONSTITUENTS:
        if master.get(symbol):
            resolved[symbol] = master[symbol]
        else:
            missing.append(symbol)
    print(f"resolved {len(resolved)}/{len(CONSTITUENTS)} constituents")
    if missing:
        print(f"unresolved (skipped): {missing}")

    collected = {}
    for position, (symbol, security_id) in enumerate(sorted(resolved.items()), 1):
        print(f"  [{position:>2}/{len(resolved)}] {symbol}")
        collected[symbol] = fetch_symbol(security_id, "NSE_EQ", "EQUITY")

    symbols = sorted(collected)
    sessions = sorted({d for series in collected.values() for d in series})
    print(f"\n{len(sessions)} sessions, {len(symbols)} symbols -> {OUT}")

    minute_axis = np.arange(SESSION_MINUTES, dtype=np.int16)
    for session in sessions:
        close = np.full((len(symbols), SESSION_MINUTES), np.nan, dtype=np.float32)
        volume = np.zeros((len(symbols), SESSION_MINUTES), dtype=np.float32)
        for row, symbol in enumerate(symbols):
            for minute, (price, traded) in collected[symbol].get(session, {}).items():
                close[row, minute] = price
                volume[row, minute] = traded
        np.savez_compressed(os.path.join(OUT, f"{session}.npz"),
                            symbols=np.array(symbols), minute=minute_axis,
                            close=close, volume=volume)
    covered = np.mean([
        np.isfinite(np.load(os.path.join(OUT, f"{s}.npz"))["close"]).mean()
        for s in sessions[:20]
    ])
    print(f"first 20 sessions are {100 * covered:.1f}% populated")


if __name__ == "__main__":
    main()
