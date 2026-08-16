"""Build the fast per-session npz cache for an underlying.

Layout: research/cache/<UNDERLYING>/<date>.npz (NIFTY also lives flat in
research/cache/ from the first build, which common.load still reads).
    strikes (nS,)  minute (nM,)  spot (nM,)
    o/h/l/c/v/oi/iv  each (2, nS, nM)  axis0 = 0:CALL 1:PUT
"""
import os
import sys
from collections import defaultdict

import django
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.models import IndexOptionCandle

IST_OFFSET = 330  # minutes; the DB stores UTC and 03:45 UTC == 09:15 IST
FIELDS = ("open", "high", "low", "close", "volume", "oi", "implied_volatility")
KEYS = ("o", "h", "l", "c", "v", "oi", "iv")


def build(underlying, expiry_code=1):
    target = os.path.join(ROOT, "research", "cache", underlying)
    os.makedirs(target, exist_ok=True)
    rows = (
        IndexOptionCandle.objects.filter(
            underlying=underlying, expiry_code=expiry_code, interval_minutes=1
        )
        .values("timestamp", "strike", "option_type", "spot", *FIELDS)
        .iterator(chunk_size=50_000)
    )
    sessions = defaultdict(list)
    for row in rows:
        stamp = row["timestamp"]
        minute = stamp.hour * 60 + stamp.minute + IST_OFFSET
        date = stamp.date()
        if minute >= 24 * 60:  # rolled past midnight UTC -> next IST day
            minute -= 24 * 60
        sessions[date.isoformat()].append((minute, row))

    written = 0
    for date, entries in sorted(sessions.items()):
        strikes = sorted({float(row["strike"] or 0) for _, row in entries if row["strike"]})
        minutes = sorted({minute for minute, _ in entries})
        if not strikes or len(minutes) < 60:
            continue
        strike_index = {value: index for index, value in enumerate(strikes)}
        minute_index = {value: index for index, value in enumerate(minutes)}
        shape = (2, len(strikes), len(minutes))
        arrays = {key: np.full(shape, np.nan, dtype=np.float32) for key in KEYS}
        spot = np.full(len(minutes), np.nan, dtype=np.float64)
        for minute, row in entries:
            strike = float(row["strike"] or 0)
            if strike not in strike_index:
                continue
            side = 0 if row["option_type"] == "CALL" else 1
            si = strike_index[strike]
            mi = minute_index[minute]
            for key, field in zip(KEYS, FIELDS):
                value = row[field]
                if value is not None:
                    arrays[key][side, si, mi] = float(value)
            if row["spot"]:
                spot[mi] = float(row["spot"])
        np.savez_compressed(
            os.path.join(target, f"{date}.npz"),
            strikes=np.array(strikes, dtype=np.float64),
            minute=np.array([value - 555 for value in minutes], dtype=np.int32),
            spot=spot,
            **arrays,
        )
        written += 1
    print(f"{underlying}: wrote {written} sessions to {target}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "SENSEX")
