import os
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone

from .models import AppSetting, IndexOISnapshot, IndexOptionCandle, IndexOptionStrikeSnapshot
from .services import classify_regime, get_dhan_credentials, get_oi_interval_seconds


DHAN_OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"
DHAN_EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
DHAN_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"
DHAN_ROLLING_OPTION_URL = "https://api.dhan.co/v2/charts/rollingoption"
DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
INDEX_CONFIG = {
    "NIFTY": {"security_id": 13, "option_segment": "NSE_FNO", "strike_step": 50},
    "SENSEX": {"security_id": 51, "option_segment": "BSE_FNO", "strike_step": 100},
}


def _headers():
    access_token, client_id = get_dhan_credentials()
    if not access_token or not client_id:
        raise RuntimeError("Dhan credentials are not configured.")
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": access_token,
        "client-id": client_id,
    }


def _decimal(value, default="0"):
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _active_expiry(underlying, config):
    cache_key = f"dhan_index_expiry_{underlying.lower()}"
    cached = AppSetting.objects.filter(key=cache_key).values_list("value", flat=True).first()
    today = timezone.localdate()
    if cached:
        try:
            cached_date = datetime.strptime(cached, "%Y-%m-%d").date()
            if cached_date >= today:
                return cached_date
        except ValueError:
            pass

    response = requests.post(
        DHAN_EXPIRY_LIST_URL,
        json={"UnderlyingScrip": config["security_id"], "UnderlyingSeg": "IDX_I"},
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    expiries = [datetime.strptime(value, "%Y-%m-%d").date() for value in response.json().get("data", [])]
    active = min((expiry for expiry in expiries if expiry >= today), default=None)
    if not active:
        raise RuntimeError(f"No active Dhan expiry found for {underlying}.")
    AppSetting.objects.update_or_create(key=cache_key, defaults={"value": active.isoformat()})
    return active


def _buildup(price_change, oi_change):
    if price_change > 0 and oi_change > 0:
        return "LONG_BUILDUP"
    if price_change > 0 and oi_change < 0:
        return "SHORT_COVERING"
    if price_change < 0 and oi_change > 0:
        return "SHORT_BUILDUP"
    if price_change < 0 and oi_change < 0:
        return "LONG_UNWINDING"
    return "NEUTRAL"


def _max_pain(strike_rows):
    strikes = sorted({row["strike"] for row in strike_rows})
    if not strikes:
        return None
    oi_by_contract = {(row["strike"], row["option_type"]): row["oi"] for row in strike_rows}
    pain = {}
    for settlement in strikes:
        pain[settlement] = sum(
            oi_by_contract.get((strike, "CE"), 0) * max(settlement - strike, 0)
            + oi_by_contract.get((strike, "PE"), 0) * max(strike - settlement, 0)
            for strike in strikes
        )
    return min(pain, key=pain.get)


def _market_prices(quote, fallback_price, fallback_previous_close=Decimal("0")):
    price = _decimal(quote.get("last_price"), fallback_price)
    previous_close = _decimal((quote.get("ohlc") or {}).get("close"), fallback_previous_close)
    return (
        price if price > 0 else fallback_price,
        previous_close if previous_close > 0 else fallback_previous_close,
    )


def _fetch_quotes(config, strike_rows, atm_strike):
    nearby = [row for row in strike_rows if abs(row["strike"] - atm_strike) <= config["strike_step"] * 5]
    security_ids = [int(row["security_id"]) for row in nearby if row["security_id"]]
    payload = {"IDX_I": [config["security_id"]]}
    if security_ids:
        payload[config["option_segment"]] = security_ids
    response = requests.post(
        DHAN_QUOTE_URL,
        json=payload,
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    return (
        data.get("IDX_I", {}).get(str(config["security_id"]), {}),
        data.get(config["option_segment"], {}),
    )


@transaction.atomic
def collect_index_option_chain(underlying):
    underlying = underlying.upper()
    config = INDEX_CONFIG[underlying]
    expiry = _active_expiry(underlying, config)
    response = requests.post(
        DHAN_OPTION_CHAIN_URL,
        json={
            "UnderlyingScrip": config["security_id"],
            "UnderlyingSeg": "IDX_I",
            "Expiry": expiry.isoformat(),
        },
        headers=_headers(),
        timeout=45,
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    underlying_price = _decimal(data.get("last_price"))
    atm_strike = (
        (underlying_price / config["strike_step"]).quantize(Decimal("1")) * config["strike_step"]
    )

    previous_snapshot = IndexOISnapshot.objects.filter(
        underlying=underlying,
        expiry_date=expiry,
        created_at__date=timezone.localdate(),
    ).prefetch_related("strikes").first()
    previous_rows = {}
    if previous_snapshot:
        previous_rows = {
            (row.strike, row.option_type): row
            for row in previous_snapshot.strikes.all()
        }

    strike_rows = []
    for strike_value, sides in data.get("oc", {}).items():
        strike = _decimal(strike_value)
        for side_key, option_type in (("ce", "CE"), ("pe", "PE")):
            option = sides.get(side_key)
            if not option:
                continue
            previous = previous_rows.get((strike, option_type))
            last_price = _decimal(option.get("last_price"))
            oi = int(option.get("oi") or 0)
            prior_price = previous.last_price if previous else _decimal(option.get("previous_close_price"))
            prior_oi = previous.oi if previous else int(option.get("previous_oi") or 0)
            greeks = option.get("greeks") or {}
            strike_rows.append({
                "strike": strike,
                "option_type": option_type,
                "security_id": str(option.get("security_id") or ""),
                "last_price": last_price,
                "price_change": last_price - prior_price,
                "average_price": _decimal(option.get("average_price")),
                "previous_close": _decimal(option.get("previous_close_price")),
                "oi": oi,
                "previous_oi": int(option.get("previous_oi") or 0),
                "oi_change": oi - prior_oi,
                "volume": int(option.get("volume") or 0),
                "previous_volume": int(option.get("previous_volume") or 0),
                "implied_volatility": float(option.get("implied_volatility") or 0),
                "delta": float(greeks.get("delta") or 0),
                "theta": float(greeks.get("theta") or 0),
                "gamma": float(greeks.get("gamma") or 0),
                "vega": float(greeks.get("vega") or 0),
                "top_bid_price": _decimal(option.get("top_bid_price")),
                "top_bid_quantity": int(option.get("top_bid_quantity") or 0),
                "top_ask_price": _decimal(option.get("top_ask_price")),
                "top_ask_quantity": int(option.get("top_ask_quantity") or 0),
                "buy_quantity": 0,
                "sell_quantity": 0,
                "depth": {},
                "is_atm": strike == atm_strike,
            })

    index_quote, quotes_by_security = _fetch_quotes(config, strike_rows, atm_strike)
    underlying_price, previous_spot_close = _market_prices(index_quote, underlying_price)
    atm_strike = (
        (underlying_price / config["strike_step"]).quantize(Decimal("1")) * config["strike_step"]
    )
    for row in strike_rows:
        quote = quotes_by_security.get(row["security_id"], {})
        if quote:
            row["last_price"], row["previous_close"] = _market_prices(
                quote, row["last_price"], row["previous_close"],
            )
            prior_price = previous_rows.get((row["strike"], row["option_type"]))
            prior_price = prior_price.last_price if prior_price else row["previous_close"]
            row["price_change"] = row["last_price"] - prior_price
            row["buy_quantity"] = int(quote.get("buy_quantity") or 0)
            row["sell_quantity"] = int(quote.get("sell_quantity") or 0)
            row["depth"] = quote.get("depth") or {}
        row["is_atm"] = row["strike"] == atm_strike
        row["buildup"] = _buildup(row["price_change"], row["oi_change"])

    nearest_strikes = sorted(
        {row["strike"] for row in strike_rows}, key=lambda strike: abs(strike - atm_strike)
    )[:11]
    dashboard_rows = [row for row in strike_rows if row["strike"] in nearest_strikes]
    calls = [row for row in dashboard_rows if row["option_type"] == "CE"]
    puts = [row for row in dashboard_rows if row["option_type"] == "PE"]
    call_oi = sum(row["oi"] for row in calls)
    put_oi = sum(row["oi"] for row in puts)
    pcr = round(put_oi / call_oi, 4) if call_oi else 0
    support = max(puts, key=lambda row: row["oi"])["strike"] if puts else None
    resistance = max(calls, key=lambda row: row["oi"])["strike"] if calls else None
    underlying_change = underlying_price - previous_spot_close if previous_spot_close else 0
    pcr_change = pcr - previous_snapshot.pcr if previous_snapshot else 0

    snapshot = IndexOISnapshot.objects.create(
        underlying=underlying,
        expiry_date=expiry,
        underlying_price=underlying_price,
        underlying_change=underlying_change,
        atm_strike=atm_strike,
        call_oi=call_oi,
        put_oi=put_oi,
        call_oi_change=sum(row["oi_change"] for row in calls),
        put_oi_change=sum(row["oi_change"] for row in puts),
        call_volume=sum(row["volume"] for row in calls),
        put_volume=sum(row["volume"] for row in puts),
        pcr=pcr,
        pcr_change=pcr_change,
        max_pain=_max_pain(dashboard_rows),
        support_strike=support,
        resistance_strike=resistance,
        regime=classify_regime(put_oi, call_oi),
        interval_seconds=get_oi_interval_seconds(),
    )
    IndexOptionStrikeSnapshot.objects.bulk_create(
        [IndexOptionStrikeSnapshot(snapshot=snapshot, **row) for row in strike_rows],
        batch_size=500,
    )
    return snapshot


def collect_all_index_option_chains():
    snapshots = []
    for position, underlying in enumerate(INDEX_CONFIG):
        if position:
            time.sleep(3.1)
        snapshots.append(collect_index_option_chain(underlying))
    return snapshots


def backfill_rolling_option_history(underlying, from_date, to_date, interval=1, expiry_code=1):
    underlying = underlying.upper()
    config = INDEX_CONFIG[underlying]
    created = 0
    for relative_index in range(-10, 11):
        relative_strike = "ATM" if relative_index == 0 else f"ATM{relative_index:+d}"
        for option_type in ("CALL", "PUT"):
            payload = {
                "exchangeSegment": config["option_segment"],
                "interval": str(interval),
                "securityId": config["security_id"],
                "instrument": "OPTIDX",
                "expiryFlag": "WEEK",
                "expiryCode": expiry_code,
                "strike": relative_strike,
                "drvOptionType": option_type,
                "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
            }
            response = requests.post(DHAN_ROLLING_OPTION_URL, json=payload, headers=_headers(), timeout=60)
            if not response.ok:
                raise RuntimeError(
                    f"Dhan rolling-option request failed ({response.status_code}): {response.text[:500]}"
                )
            time.sleep(3.1)
            side = (response.json().get("data") or {}).get("ce" if option_type == "CALL" else "pe") or {}
            timestamps = side.get("timestamp") or []
            fields = {name: side.get(name) or [] for name in ("strike", "spot", "open", "high", "low", "close", "volume", "oi", "iv")}
            rows = []
            seen_timestamps = set()
            for index, epoch in enumerate(timestamps):
                if epoch in seen_timestamps:
                    continue
                seen_timestamps.add(epoch)
                value = lambda name: fields[name][index] if index < len(fields[name]) else None
                rows.append(IndexOptionCandle(
                    underlying=underlying,
                    expiry_code=expiry_code,
                    relative_strike=relative_strike,
                    option_type=option_type,
                    interval_minutes=interval,
                    timestamp=datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone()),
                    strike=value("strike"), spot=value("spot"), open=value("open"), high=value("high"),
                    low=value("low"), close=value("close"), volume=value("volume") or 0,
                    oi=value("oi") or 0, implied_volatility=value("iv") or 0,
                ))
            IndexOptionCandle.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)
            created += len(rows)
    return created


def backfill_fixed_option_history(underlying, session_date, interval=1):
    underlying = underlying.upper()
    config = INDEX_CONFIG[underlying]
    snapshot = IndexOISnapshot.objects.filter(
        underlying=underlying,
        created_at__date=session_date,
    ).prefetch_related("strikes").first()
    if not snapshot:
        raise RuntimeError(f"No saved {underlying} option-chain snapshot for {session_date}.")

    contracts = {}
    for row in snapshot.strikes.all():
        if row.security_id:
            contracts[(row.strike, row.option_type)] = row.security_id
    if not contracts:
        raise RuntimeError(f"No saved Dhan contract IDs for {underlying} on {session_date}.")

    created = 0
    for (strike, option_type), security_id in contracts.items():
        payload = {
            "securityId": security_id,
            "exchangeSegment": config["option_segment"],
            "instrument": "OPTIDX",
            "interval": str(interval),
            "oi": True,
            "fromDate": f"{session_date.isoformat()} 09:15:00",
            "toDate": f"{session_date.isoformat()} 15:30:00",
        }
        response = requests.post(DHAN_INTRADAY_URL, json=payload, headers=_headers(), timeout=60)
        if not response.ok:
            raise RuntimeError(
                f"Dhan intraday request failed ({response.status_code}): {response.text[:500]}"
            )
        data = response.json()
        timestamps = data.get("timestamp") or []
        fields = {
            name: data.get(name) or []
            for name in ("open", "high", "low", "close", "volume", "open_interest")
        }
        relative_index = int((strike - snapshot.atm_strike) / config["strike_step"]) if snapshot.atm_strike else 0
        relative_strike = "ATM" if relative_index == 0 else f"ATM{relative_index:+d}"
        rows = []
        for index, epoch in enumerate(timestamps):
            value = lambda name: fields[name][index] if index < len(fields[name]) else None
            rows.append(IndexOptionCandle(
                underlying=underlying,
                relative_strike=relative_strike,
                option_type="CALL" if option_type == "CE" else "PUT",
                interval_minutes=interval,
                timestamp=datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone()),
                strike=strike,
                spot=None,
                open=value("open"), high=value("high"), low=value("low"), close=value("close"),
                volume=value("volume") or 0, oi=value("open_interest") or 0,
                implied_volatility=0,
            ))
        IndexOptionCandle.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)
        created += len(rows)
        time.sleep(0.25)
    return created