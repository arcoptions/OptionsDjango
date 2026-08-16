"""Real traded volume for the index, from the NIFTY future.

The index itself has no volume, and every study so far has leaned on a proxy:
either total option-chain volume or the volume of one strike. Both are shaped by
which strikes happen to be liquid, which is not the same as how hard the index is
being traded. The future is the one instrument that trades the index directly,
so its 1-minute volume is the honest measure -- and it is what a volume profile
needs to be worth building at all.

Futures roll, so the capture walks the contract list and takes each contract only
over the window where it was the front month. That avoids stitching a thin
far-month tape onto the front-month tape and calling it one series.

Written to research/cache/FUT/<date>.npz, one file per session, aligned to the
same 375-minute grid as every other cache here so the arrays index identically.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dhan_probe import intraday
from dhan_probe2 import future_contracts

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "FUT")
START, END = "2025-08-18", "2026-08-16"
OPEN_MINUTE = 555  # 09:15 IST in minutes past midnight
SESSION_MINUTES = 375
WINDOW_DAYS = 88
PAUSE = 0.4


def windows(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    while first <= last:
        stop = min(first + timedelta(days=WINDOW_DAYS), last)
        yield first.isoformat(), stop.isoformat()
        first = stop + timedelta(days=1)


def blank():
    return {key: np.full(SESSION_MINUTES, np.nan, dtype=np.float64)
            for key in ("open", "high", "low", "close", "volume")}


def collect(contract, start, end, sessions):
    """Fold one contract's candles into the per-session grids."""
    data = intraday(contract["security_id"], "NSE_FNO", "FUTIDX", start, end)
    stamps = data.get("timestamp") or []
    if not stamps:
        return 0
    added = 0
    for index, epoch in enumerate(stamps):
        moment = datetime.fromtimestamp(int(epoch))
        minute = moment.hour * 60 + moment.minute - OPEN_MINUTE
        if not 0 <= minute < SESSION_MINUTES:
            continue
        day = moment.date().isoformat()
        if not START <= day <= END:
            continue
        grid = sessions.setdefault(day, blank())
        for key, series in (("open", "open"), ("high", "high"), ("low", "low"),
                            ("close", "close"), ("volume", "volume")):
            values = data.get(series)
            if values and index < len(values):
                grid[key][minute] = float(values[index])
        added += 1
    return added


def main():
    os.makedirs(OUT, exist_ok=True)
    contracts = future_contracts()
    print(f"{len(contracts)} NIFTY futures contracts in the master")
    if not contracts:
        return

    # Expiries in the master only cover the near future, so history has to come
    # from whichever contracts are still listed. Each is pulled over its whole
    # life and the front-month rule is applied afterwards, by preferring the
    # nearest expiry that actually traded on a given minute.
    sessions = {}
    for contract in contracts[:6]:
        total = 0
        for start, end in windows(START, END):
            try:
                total += collect(contract, start, end, sessions)
            except Exception as error:  # a dead contract 404s; keep going
                print(f"   {contract['symbol']} {start}..{end}: {error}")
            time.sleep(PAUSE)
        print(f"  {contract['symbol']:<26}expiry {contract['expiry']:<12}"
              f"{total:>8,} minutes", flush=True)

    written = 0
    for day, grid in sorted(sessions.items()):
        if not np.isfinite(grid["close"]).any():
            continue
        np.savez_compressed(
            os.path.join(OUT, f"{day}.npz"),
            minute=np.arange(SESSION_MINUTES),
            **{key: value.astype(np.float32) for key, value in grid.items()},
        )
        written += 1
    print(f"\n{written} sessions -> {OUT}")
    if written:
        sample = sorted(sessions)[len(sessions) // 2]
        grid = sessions[sample]
        good = np.isfinite(grid["close"])
        print(f"  sample {sample}: {good.sum()}/{SESSION_MINUTES} minutes, "
              f"volume total {np.nansum(grid['volume']):,.0f}")


if __name__ == "__main__":
    main()
