"""Pull stock and stock-option history from Dhan.

Three feeds, because no single endpoint gives everything:

  equity   /v2/charts/intraday on the cash symbol -- 15-minute bars, 1.5 years.
           This is what the Chartink rules get re-run on.

  rolling  /v2/charts/rollingoption with instrument=OPTSTK -- ATM-3..ATM+3 only,
           but it rolls the expiry for you and reaches back a year and a half,
           and it carries IV, OI and spot on every bar. The statistical backbone.

  ladder   /v2/charts/intraday against a specific contract security id -- any
           strike, so this is what can answer "does a 60-paisa call really go
           to five rupees". The catch is that Dhan's scrip master lists only
           live expiries, so the ladder exists for the current cycles and not
           for anything already expired.

Every unit of work is recorded in DownloadJob, so re-running skips what landed
and a killed run resumes. Requests are threaded four ways; eight trips the 429.
"""
import csv
import datetime as dt
import io
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.utils import timezone

from .models import DownloadJob, StockEquityCandle, StockOptionCandle, TrackedStock
from .services import get_dhan_credentials

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
ROLLING_URL = "https://api.dhan.co/v2/charts/rollingoption"

MASTER_CACHE = os.path.join(os.path.dirname(__file__), "data", "scrip_master.csv")

# Measured, not guessed: 4 workers ran 16 requests clean, 8 workers drew six 429s.
WORKERS = 4
# 45 is the largest window the endpoint ACCEPTS, but not the largest it answers.
# The failures are a hard 30-second gateway timeout (HTTP 504, HTML body), and
# they cluster where the data is DENSEST rather than thinnest: 45d over the busy
# band failed 71% sequentially and 100% four-wide, while the same dates at 10-20d
# answered 18/18. Retrying does not help -- a 504 here is deterministic, and four
# to six retries still fail -- so window size is the only lever there is.
ROLLING_WINDOW_DAYS = 15
INTRADAY_WINDOW_DAYS = 85  # the documented intraday cap is 90
RELATIVE_STRIKES = ["ATM", "ATM+1", "ATM-1", "ATM+2", "ATM-2", "ATM+3", "ATM-3"]

_local = threading.local()
_print_lock = threading.Lock()


def _session():
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _headers():
    access_token, client_id = get_dhan_credentials()
    if not access_token:
        path = os.path.expanduser(os.environ.get("DHAN_TOKEN_FILE", "~/Downloads/Dhan Temp Token.txt"))
        if os.path.exists(path):
            access_token = open(path).read().strip()
            client_id = client_id or "1111860593"
    if not access_token or not client_id:
        raise RuntimeError("Dhan credentials are not configured.")
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": access_token,
        "client-id": client_id,
    }


def log(message):
    with _print_lock:
        print(f"[{dt.datetime.now():%H:%M:%S}] {message}", flush=True)


def _post(url, payload, attempts=6):
    """One request, backing off through 429s rather than dropping the window."""
    delay = 2.0
    for attempt in range(attempts):
        try:
            response = _session().post(url, json=payload, headers=_headers(), timeout=120)
        except requests.RequestException as error:
            if attempt == attempts - 1:
                return None, f"network: {error}"
            time.sleep(delay)
            delay *= 1.7
            continue
        if response.status_code == 429:
            time.sleep(delay)
            delay *= 1.7
            continue
        if response.ok:
            return response.json(), None
        return None, f"HTTP {response.status_code}: {response.text[:200]}"
    return None, "rate limited after retries"


# --------------------------------------------------------------------------
# instrument master


def load_master(refresh=False):
    os.makedirs(os.path.dirname(MASTER_CACHE), exist_ok=True)
    stale = (
        not os.path.exists(MASTER_CACHE)
        or time.time() - os.path.getmtime(MASTER_CACHE) > 12 * 3600
    )
    if refresh or stale:
        log("fetching Dhan scrip master")
        response = requests.get(MASTER_URL, timeout=300)
        response.raise_for_status()
        with open(MASTER_CACHE, "w", encoding="utf-8", newline="") as handle:
            handle.write(response.text)
    with open(MASTER_CACHE, encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def equity_ids(rows):
    table = {}
    for row in rows:
        if row.get("EXCH_ID") == "NSE" and row.get("SEGMENT") == "E" and row.get("INSTRUMENT") == "EQUITY":
            symbol = (row.get("UNDERLYING_SYMBOL") or "").strip()
            if symbol:
                table[symbol] = row["SECURITY_ID"]
    return table


def option_contracts(rows):
    """{symbol: [contract dicts]} for live NSE stock options."""
    table = defaultdict(list)
    for row in rows:
        if row.get("INSTRUMENT") != "OPTSTK" or row.get("EXCH_ID") != "NSE":
            continue
        try:
            strike = float(row.get("STRIKE_PRICE") or 0)
        except ValueError:
            continue
        if strike <= 0:
            continue
        table[(row.get("UNDERLYING_SYMBOL") or "").strip()].append({
            "security_id": row["SECURITY_ID"],
            "strike": strike,
            "option_type": row.get("OPTION_TYPE"),
            "expiry": row.get("SM_EXPIRY_DATE"),
            "lot_size": float(row.get("LOT_SIZE") or 0),
            "display": row.get("DISPLAY_NAME", ""),
        })
    return table


def sync_master_metadata():
    """Stamp security id, lot size and strike step onto the tracked stocks."""
    rows = load_master()
    equities = equity_ids(rows)
    contracts = option_contracts(rows)
    updated, unmatched = 0, []
    for stock in TrackedStock.objects.filter(is_active=True):
        security_id = equities.get(stock.symbol, "")
        legs = contracts.get(stock.symbol, [])
        if not security_id and not legs:
            unmatched.append(stock.symbol)
            continue
        stock.security_id = security_id
        if legs:
            stock.lot_size = int(legs[0]["lot_size"] or 0)
            nearest = min(leg["expiry"] for leg in legs)
            strikes = sorted({leg["strike"] for leg in legs if leg["expiry"] == nearest})
            gaps = sorted({round(b - a, 2) for a, b in zip(strikes, strikes[1:])})
            if gaps:
                stock.strike_step = gaps[len(gaps) // 2]
        stock.save(update_fields=["security_id", "lot_size", "strike_step"])
        updated += 1
    return updated, unmatched


# --------------------------------------------------------------------------
# windows and job bookkeeping


def windows(start, end, span):
    cursor = start
    while cursor <= end:
        stop = min(cursor + dt.timedelta(days=span - 1), end)
        yield cursor, stop
        cursor = stop + dt.timedelta(days=1)


def _done_keys(kind):
    return {
        (row["symbol"], row["detail"], row["window_from"], row["window_to"])
        for row in DownloadJob.objects.filter(kind=kind, status="DONE").values(
            "symbol", "detail", "window_from", "window_to"
        )
    }


def _record(kind, symbol, detail, start, end, rows, error=""):
    DownloadJob.objects.update_or_create(
        kind=kind, symbol=symbol, detail=detail, window_from=start, window_to=end,
        defaults={"rows": rows, "status": "FAIL" if error else "DONE", "error": error[:500]},
    )


def _stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone())


# --------------------------------------------------------------------------
# feed 1: equity bars


def fetch_equity(symbol, security_id, start, end, interval=15):
    payload = {
        "securityId": str(security_id), "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
        "interval": str(interval), "oi": False,
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
    }
    data, error = _post(INTRADAY_URL, payload)
    if error:
        return 0, error
    stamps = data.get("timestamp") or []
    if not stamps:
        return 0, ""
    value = lambda name, index: (data.get(name) or [None] * len(stamps))[index]
    candles = [
        StockEquityCandle(
            symbol=symbol, interval_minutes=interval, timestamp=_stamp(epoch),
            open=value("open", index), high=value("high", index),
            low=value("low", index), close=value("close", index),
            volume=int(value("volume", index) or 0),
        )
        for index, epoch in enumerate(stamps)
    ]
    StockEquityCandle.objects.bulk_create(candles, ignore_conflicts=True, batch_size=2000)
    return len(candles), ""


# --------------------------------------------------------------------------
# feed 2: rolling ATM-relative options


def fetch_rolling(symbol, security_id, relative, option_type, start, end,
                  interval=15, expiry_code=1):
    payload = {
        "exchangeSegment": "NSE_FNO", "interval": str(interval), "securityId": str(security_id),
        "instrument": "OPTSTK", "expiryFlag": "MONTH", "expiryCode": expiry_code,
        "strike": relative, "drvOptionType": option_type,
        "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
    }
    data, error = _post(ROLLING_URL, payload)
    if error:
        return 0, error
    side = (data.get("data") or {}).get("ce" if option_type == "CALL" else "pe") or {}
    stamps = side.get("timestamp") or []
    if not stamps:
        return 0, ""
    fields = {
        name: side.get(name) or []
        for name in ("strike", "spot", "open", "high", "low", "close", "volume", "oi", "iv")
    }
    value = lambda name, index: fields[name][index] if index < len(fields[name]) else None
    seen, candles = set(), []
    for index, epoch in enumerate(stamps):
        if epoch in seen:
            continue
        seen.add(epoch)
        candles.append(StockOptionCandle(
            symbol=symbol, expiry_code=expiry_code, relative_strike=relative,
            option_type=option_type, interval_minutes=interval, timestamp=_stamp(epoch),
            strike=value("strike", index), spot=value("spot", index),
            open=value("open", index), high=value("high", index),
            low=value("low", index), close=value("close", index),
            volume=int(value("volume", index) or 0), oi=int(value("oi", index) or 0),
            implied_volatility=float(value("iv", index) or 0),
        ))
    StockOptionCandle.objects.bulk_create(candles, ignore_conflicts=True, batch_size=2000)
    return len(candles), ""


# --------------------------------------------------------------------------
# feed 3: the real strike ladder, live expiries only


def fetch_contract(symbol, contract, start, end, interval=15):
    payload = {
        "securityId": str(contract["security_id"]), "exchangeSegment": "NSE_FNO",
        "instrument": "OPTSTK", "interval": str(interval), "oi": True,
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
    }
    data, error = _post(INTRADAY_URL, payload)
    if error:
        return 0, error
    stamps = data.get("timestamp") or []
    if not stamps:
        return 0, ""
    value = lambda name, index: (data.get(name) or [None] * len(stamps))[index]
    label = f"K{contract['strike']:g}"
    candles = [
        StockOptionCandle(
            symbol=symbol, expiry_code=90, relative_strike=label,
            option_type="CALL" if contract["option_type"] == "CE" else "PUT",
            interval_minutes=interval, timestamp=_stamp(epoch),
            strike=contract["strike"], spot=None,
            open=value("open", index), high=value("high", index),
            low=value("low", index), close=value("close", index),
            volume=int(value("volume", index) or 0),
            oi=int(value("open_interest", index) or 0), implied_volatility=0.0,
        )
        for index, epoch in enumerate(stamps)
    ]
    StockOptionCandle.objects.bulk_create(candles, ignore_conflicts=True, batch_size=2000)
    return len(candles), ""


# --------------------------------------------------------------------------
# drivers


def _run(tasks, label):
    """tasks: [(key tuple, callable) ...] -> executes threaded, records each."""
    total_rows, failures = 0, 0
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(job): key for key, job in tasks}
        for future in as_completed(futures):
            kind, symbol, detail, start, end = futures[future]
            try:
                rows, error = future.result()
            except Exception as error:  # noqa: BLE001 - a bad row must not kill the run
                rows, error = 0, str(error)
            _record(kind, symbol, detail, start, end, rows, error)
            total_rows += rows
            done += 1
            if error:
                failures += 1
                if failures <= 12:
                    log(f"  ! {symbol} {detail} {start}: {error[:110]}")
            if done % 200 == 0:
                log(f"  {label}: {done}/{len(tasks)} windows, {total_rows:,} bars, {failures} failed")
    return total_rows, failures


def download_equity(stocks, start, end, interval=15):
    equities = equity_ids(load_master())
    done = _done_keys("equity")
    tasks = []
    for stock in stocks:
        security_id = stock.security_id or equities.get(stock.symbol)
        if not security_id:
            continue
        for window_start, window_end in windows(start, end, INTRADAY_WINDOW_DAYS):
            detail = f"{interval}m"
            if (stock.symbol, detail, window_start, window_end) in done:
                continue
            tasks.append((
                ("equity", stock.symbol, detail, window_start, window_end),
                lambda s=stock.symbol, i=security_id, a=window_start, b=window_end:
                    fetch_equity(s, i, a, b, interval),
            ))
    log(f"equity: {len(tasks)} windows queued across {len(stocks)} stocks")
    return _run(tasks, "equity")


def download_rolling(stocks, start, end, relatives=None, interval=15, expiry_code=1,
                     option_types=("CALL", "PUT")):
    """Rolling ATM-relative option bars.

    `option_types` exists because moneyness is side-dependent: ATM+2 is an
    out-of-the-money CALL but a deep in-the-money PUT. A run aimed at cheap OTM
    contracts wants ATM+n on the call side and ATM-n on the put side, and pulling
    both sides of every offset would spend half the hours on the wrong half.
    """
    equities = equity_ids(load_master())
    done = _done_keys("rolling")
    relatives = relatives or RELATIVE_STRIKES
    tasks = []
    # Strike-major so a truncated run still has ATM for every stock.
    for relative in relatives:
        for option_type in option_types:
            for stock in stocks:
                security_id = stock.security_id or equities.get(stock.symbol)
                if not security_id:
                    continue
                detail = f"{relative}:{option_type}:{expiry_code}"
                for window_start, window_end in windows(start, end, ROLLING_WINDOW_DAYS):
                    if (stock.symbol, detail, window_start, window_end) in done:
                        continue
                    tasks.append((
                        ("rolling", stock.symbol, detail, window_start, window_end),
                        lambda s=stock.symbol, i=security_id, r=relative, t=option_type,
                               a=window_start, b=window_end:
                            fetch_rolling(s, i, r, t, a, b, interval, expiry_code),
                    ))
    log(f"rolling: {len(tasks)} windows queued")
    return _run(tasks, "rolling")


def download_ladder(stocks, start, end, span=6, interval=15, expiries=1):
    """Every strike within `span` steps of the money, for the live expiries."""
    contracts = option_contracts(load_master())
    done = _done_keys("ladder")
    tasks = []
    for stock in stocks:
        legs = contracts.get(stock.symbol) or []
        if not legs:
            continue
        step = float(stock.strike_step or 0)
        spot = _last_spot(stock.symbol)
        if not step or not spot:
            continue
        wanted = sorted({expiry for expiry in (leg["expiry"] for leg in legs)})[:expiries]
        low, high = spot - span * step, spot + span * step
        for leg in legs:
            if leg["expiry"] not in wanted or not (low <= leg["strike"] <= high):
                continue
            detail = f"{leg['expiry']}:K{leg['strike']:g}:{leg['option_type']}"
            for window_start, window_end in windows(start, end, INTRADAY_WINDOW_DAYS):
                if (stock.symbol, detail, window_start, window_end) in done:
                    continue
                tasks.append((
                    ("ladder", stock.symbol, detail, window_start, window_end),
                    lambda s=stock.symbol, c=leg, a=window_start, b=window_end:
                        fetch_contract(s, c, a, b, interval),
                ))
    log(f"ladder: {len(tasks)} windows queued")
    return _run(tasks, "ladder")


def _last_spot(symbol):
    row = (
        StockEquityCandle.objects.filter(symbol=symbol)
        .order_by("-timestamp").values("close").first()
    )
    return float(row["close"]) if row and row["close"] else None
