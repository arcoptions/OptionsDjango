"""Shared loaders for the spike research scripts.

Cache layout (research/cache/<date>.npz):
    strikes (nS,)  minute (nM,)  spot (nM,)
    o/h/l/c/v/oi/iv  each (2, nS, nM)  with axis0 = 0:CALL 1:PUT
"""
import glob
import os

import numpy as np

CACHE = os.path.join(os.path.dirname(__file__), "cache")
CALL, PUT = 0, 1


def session_dates():
    return sorted(
        os.path.basename(path)[:-4] for path in glob.glob(os.path.join(CACHE, "*.npz"))
    )


def load(date):
    return np.load(os.path.join(CACHE, f"{date}.npz"))


NIFTY_TUESDAY_EXPIRY_START = "2025-09-01"


def expiry_dates(dates):
    """Last available session on or before the scheduled weekly expiry."""
    import datetime as dt

    by_week = {}
    for text in dates:
        day = dt.date.fromisoformat(text)
        by_week.setdefault(day.isocalendar()[:2], []).append(day)
    result = set()
    for days in by_week.values():
        monday = days[0] - dt.timedelta(days=days[0].weekday())
        weekday = 1 if monday >= dt.date.fromisoformat(NIFTY_TUESDAY_EXPIRY_START) else 3
        scheduled = monday + dt.timedelta(days=weekday)
        eligible = [day for day in days if day <= scheduled]
        if eligible:
            result.add(max(eligible).isoformat())
    return result


def forward_extremes(values, horizon):
    """Running max/min of values[t+1 .. t+horizon]; NaN where the window is short."""
    count = len(values)
    highest = np.full(count, np.nan)
    lowest = np.full(count, np.nan)
    for index in range(count - 1):
        window = values[index + 1 : index + 1 + horizon]
        if len(window) < horizon:
            break
        highest[index] = window.max()
        lowest[index] = window.min()
    return highest, lowest


def rolling_median(values, length):
    count = len(values)
    result = np.full(count, np.nan)
    for index in range(length, count):
        result[index] = np.median(values[index - length : index])
    return result
