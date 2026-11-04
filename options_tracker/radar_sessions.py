"""NSE F&O-stock continuous sessions; auction prints are not continuous candles."""

from datetime import date, datetime, time

from django.utils import timezone


CAS_EFFECTIVE = date(2026, 8, 3)
CASH_SCHEDULE_SOURCE = "https://nsearchives.nseindia.com/web/pressrelease/2026-08/PR_cc_03082026_20260803232411.pdf"
DERIVATIVE_SCHEDULE_SOURCE = "https://flattrade.in/kosh/wp-content/uploads/2026/05/FAOP74467.pdf"


def continuous_close(day: date, kind: str = "EQUITY") -> time:
    if day < CAS_EFFECTIVE:
        return time(15, 30)
    return time(15, 15) if kind == "EQUITY" else time(15, 40)


def session_minutes(day: date, kind: str = "EQUITY") -> int:
    close = continuous_close(day, kind)
    return close.hour*60+close.minute-(9*60+15)


def continuous_timestamp(stamp: datetime, kind: str = "EQUITY") -> bool:
    local = timezone.localtime(stamp)
    return local.weekday() < 5 and time(9, 15) <= local.time() < continuous_close(local.date(), kind)
