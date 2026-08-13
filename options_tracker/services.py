import os
import re
import sqlite3
import uuid
import csv
import io
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta

import requests
from django.db.models import Q
from django.utils import timezone

from .models import AppSetting, DhanOrderEvent, Direction, OptionOutcome, SignalStatus, TipSignal, TradeExecution, TradeState, TradeStyle


DHAN_INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_LTP_URL = "https://api.dhan.co/v2/marketfeed/ltp"
DHAN_SYMBOL_ALIASES = {"NG": "NATURALGAS", "UNITSDSPR": "UNITDSPR"}
DHAN_SEGMENTS = {("NSE", "D"): "NSE_FNO", ("BSE", "D"): "BSE_FNO", ("MCX", "M"): "MCX_COMM"}
MONTH_NUMBERS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7, "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9,
    "OCT": 10, "NOV": 11, "DEC": 12,
}
_dhan_master_cache = {"loaded_at": None, "contracts": None}


def get_dhan_credentials():
    token_setting = AppSetting.objects.filter(key="dhan_access_token").first()
    client_setting = AppSetting.objects.filter(key="dhan_client_id").first()
    access_token = str(token_setting.value if token_setting else os.getenv("DHAN_ACCESS_TOKEN", "")).strip()
    client_id = str(client_setting.value if client_setting else os.getenv("DHAN_CLIENT_ID", "")).strip()
    return access_token, client_id


def validate_dhan_credentials(access_token, client_id):
    response = requests.post(
        DHAN_LTP_URL,
        json={"IDX_I": [13]},
        headers=_dhan_headers(access_token, client_id),
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") == "failure" or "data" not in data:
        raise ValueError(data.get("remarks", {}).get("message") or "Dhan rejected these credentials.")
    return data


def parse_tip_text(raw_text):
    text = str(raw_text or "").strip().upper()
    text = re.sub(r"[#*_`]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    ignored_words = {
        "NEW", "TRADE", "BUY", "SELL", "SAFE", "RISKY", "HEROZERO", "HERO", "ZERO",
        "STOCK", "OPTION", "INDEX", "CALL", "FUTURE", "FUTURES", "INTRADAY", "SWING",
        "YESTERDAY", "TODAY", "TOMORROW", "MINI", "BETWEEN", "NEAR", "AROUND", "HEDGE",
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUNE", "JUL", "JULY", "AUG", "SEP",
        "SEPT", "OCT", "NOV", "DEC", "EXPIRY",
    }
    contract_match = re.search(r"\b(\d{2,6})\s*(CE|PE)\b", text)
    direction = contract_match.group(2) if contract_match else None
    strike = contract_match.group(1) if contract_match else ""
    prefix = text[:contract_match.start()] if contract_match else text
    candidates = re.findall(r"\b[A-Z][A-Z0-9&.-]{1,24}\b", prefix)
    candidates = [word.strip(".-") for word in candidates if word.strip(".-") not in ignored_words]
    symbol = candidates[-1] if candidates else ""

    if not contract_match:
        buy_match = re.search(r"\bBUY\s+([A-Z][A-Z0-9&.-]{1,24})\b", text)
        symbol = buy_match.group(1).strip(".-") if buy_match else ""
        if symbol in ignored_words:
            before_buy_match = re.search(r"\b([A-Z][A-Z0-9&.-]{1,24})\s*-?\s*BUY\s+(?:BETWEEN\s+)?", text)
            symbol = before_buy_match.group(1).strip(".-") if before_buy_match else ""
        direction = Direction.EQ if symbol else None

    if symbol in {"AT", "AUG", "JUL", "JULY", "EXPIRY", "CMP", "LTP"}:
        symbol = ""

    def find_decimal(pattern):
        match = re.search(pattern, text)
        if not match:
            return None
        try:
            return Decimal(match.group(1))
        except Exception:
            return None

    entry = find_decimal(r"BUY[^\n\r]{0,80}?\b(?:AT|@|CMP)\s*(\d+(?:\.\d+)?)")
    if entry is None:
        entry = find_decimal(r"(?:ENTRY|ABOVE)\s*(?:AT|[:@\- ])\s*(\d+(?:\.\d+)?)")
    if entry is None:
        entry = find_decimal(r"\bAT\s*(\d+(?:\.\d+)?)")
    if entry is None:
        entry = find_decimal(r"\b(?:CMP|LTP)\s*(?:[:@\- ])*\s*(\d+(?:\.\d+)?)")
    if entry is None:
        entry = find_decimal(r"\bRANGE\s*(?:[:@\- ])*\s*(\d+(?:\.\d+)?)")
    sl = find_decimal(r"(?:SL|STOP ?LOSS)\s*(?:AT|NEAR|[:@\- ])*\s*(\d+(?:\.\d+)?)")

    targets = []
    target_match = re.search(
        r"(?:TARGETS?|TGTS?|T1)\s*(?:[:@\- ])*\s*"
        r"(\d+(?:\.\d+)?(?:\s*(?:\.{2,}|[-,/=])\s*\d+(?:\.\d+)?){0,2})",
        text,
    )
    if target_match:
        for value in re.findall(r"\d+(?:\.\d+)?", target_match.group(1)):
            decimal_value = Decimal(value)
            if decimal_value not in targets:
                targets.append(decimal_value)
            if len(targets) == 3:
                break

    symbol = f"{symbol} {strike} {direction}" if symbol and strike and direction else symbol

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "t1": targets[0] if targets else None,
        "t2": targets[1] if len(targets) > 1 else None,
        "t3": targets[2] if len(targets) > 2 else None,
    }


def score_signal(signal):
    base = 45
    tags = []

    if signal.entry_price and signal.stop_loss:
        risk = abs(signal.entry_price - signal.stop_loss)
        if risk > 0:
            rr = Decimal("0")
            if signal.target_1:
                rr = (signal.target_1 - signal.entry_price) / risk if signal.direction in {Direction.CE, Direction.EQ} else (signal.entry_price - signal.target_1) / risk
            if rr >= Decimal("2.0"):
                base += 20
                tags.append("RR>=2")
            elif rr >= Decimal("1.3"):
                base += 12
                tags.append("RR>=1.3")
            else:
                tags.append("Low RR")

    if signal.trade_style == TradeStyle.SWING:
        base += 8
        tags.append("Swing Weight")
    else:
        base += 5
        tags.append("Intraday Weight")

    if signal.target_2:
        base += 8
        tags.append("T2 Present")
    if signal.target_3:
        base += 8
        tags.append("T3 Present")

    score = max(0, min(100, int(base)))

    if score >= 75:
        recommendation = "STRONG GO"
    elif score >= 55:
        recommendation = "CAUTION"
    else:
        recommendation = "AVOID"

    return score, recommendation, ", ".join(tags)


def risk_guard():
    now = timezone.localtime()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    today_count = TradeExecution.objects.filter(opened_at__range=(start, end)).count()
    open_count = TradeExecution.objects.filter(state=TradeState.OPEN).count()

    if today_count >= 5:
        return False, "Daily trade limit reached (5)."
    if open_count >= 5:
        return False, "Concurrent open trade limit reached (5)."
    return True, "OK"


def get_oi_interval_seconds():
    setting, _ = AppSetting.objects.get_or_create(key="oi_interval_seconds", defaults={"value": "30"})
    try:
        v = int(setting.value)
    except Exception:
        v = 30
    if v not in (30, 60, 90, 120):
        v = 30
    return v


def set_oi_interval_seconds(seconds):
    value = str(int(seconds)) if int(seconds) in (30, 60, 90, 120) else "30"
    AppSetting.objects.update_or_create(key="oi_interval_seconds", defaults={"value": value})


def classify_regime(put_oi, call_oi):
    if call_oi <= 0:
        return "Neutral"
    pcr = put_oi / call_oi
    if pcr > 1.15:
        return "Bullish Bias"
    if pcr < 0.85:
        return "Bearish Bias"
    return "Neutral / Range"


def archive_old_signals():
    now = timezone.localdate()
    expiry_q = Q(expiry_date__isnull=False, expiry_date__lt=now.replace(day=1))
    close_q = Q(status=SignalStatus.CLOSED, executions__closed_at__date__lt=now.replace(day=1))
    return TipSignal.objects.filter(expiry_q | close_q).exclude(status=SignalStatus.ARCHIVED).update(status=SignalStatus.ARCHIVED)


def _dhan_headers(access_token, client_id=None):
    headers = {"Accept": "application/json", "Content-Type": "application/json", "access-token": access_token}
    if client_id is None:
        _, client_id = get_dhan_credentials()
    if client_id:
        headers["client-id"] = client_id
    return headers


def _load_dhan_contracts():
    loaded_at = _dhan_master_cache["loaded_at"]
    if loaded_at and timezone.now() - loaded_at < timedelta(hours=12):
        return _dhan_master_cache["contracts"]

    response = requests.get(DHAN_INSTRUMENT_MASTER_URL, timeout=60)
    response.raise_for_status()
    contracts = {}
    for row in csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))):
        segment = DHAN_SEGMENTS.get((row.get("EXCH_ID"), row.get("SEGMENT")))
        option_type = row.get("OPTION_TYPE")
        if not segment or option_type not in {Direction.CE, Direction.PE}:
            continue
        try:
            strike = Decimal(row["STRIKE_PRICE"]).normalize()
            expiry = datetime.strptime(row["SM_EXPIRY_DATE"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        key = (row.get("UNDERLYING_SYMBOL", "").upper(), strike, option_type)
        contracts.setdefault(key, []).append(
            {
                "security_id": row.get("SECURITY_ID", ""),
                "exchange_segment": segment,
                "expiry": expiry,
                "display_name": row.get("DISPLAY_NAME", ""),
            }
        )
    _dhan_master_cache.update(loaded_at=timezone.now(), contracts=contracts)
    return contracts


def _expiry_month_hint(raw_text):
    text = str(raw_text or "").upper()
    matches = []
    for word, month in MONTH_NUMBERS.items():
        match = re.search(rf"\b{word}\b", text)
        if match:
            matches.append((match.start(), month))
    return min(matches)[1] if matches else None


def _expected_dhan_segment(option_symbol):
    match = re.fullmatch(r"(.+)\s+\d+(?:\.\d+)?\s+(?:CE|PE)", str(option_symbol or "").upper())
    if not match:
        return "NSE_FNO"
    underlying = DHAN_SYMBOL_ALIASES.get(match.group(1), match.group(1))
    return "BSE_FNO" if underlying == "SENSEX" else "NSE_FNO"


def resolve_dhan_instruments(signals):
    contracts = _load_dhan_contracts()
    today = timezone.localdate()
    resolved = 0
    for signal in signals:
        match = re.fullmatch(r"(.+)\s+(\d+(?:\.\d+)?)\s+(CE|PE)", signal.option_symbol.upper())
        if not match:
            continue
        underlying = DHAN_SYMBOL_ALIASES.get(match.group(1), match.group(1))
        key = (underlying, Decimal(match.group(2)).normalize(), match.group(3))
        expected_segment = _expected_dhan_segment(signal.option_symbol)
        choices = [
            contract
            for contract in contracts.get(key, [])
            if contract["expiry"] >= today and contract["exchange_segment"] == expected_segment
        ]
        month_hint = _expiry_month_hint(signal.raw_text)
        if month_hint and underlying != "SENSEX":
            month_choices = [contract for contract in choices if contract["expiry"].month == month_hint]
            if month_choices:
                choices = month_choices
        if not choices:
            signal.security_id = ""
            signal.exchange_segment = ""
            signal.dhan_display_name = ""
            signal.expiry_date = None
            signal.live_price = None
            signal.quote_updated_at = None
            signal.outcome_status = OptionOutcome.UNRESOLVED
            signal.outcome_at = None
            signal.save(update_fields=[
                "security_id", "exchange_segment", "dhan_display_name", "expiry_date",
                "live_price", "quote_updated_at", "outcome_status", "outcome_at",
            ])
            continue
        contract = min(choices, key=lambda item: item["expiry"])
        reset_placeholder_outcome = signal.live_price is not None and signal.live_price <= 0
        signal.security_id = contract["security_id"]
        signal.exchange_segment = contract["exchange_segment"]
        signal.dhan_display_name = contract["display_name"]
        signal.expiry_date = contract["expiry"]
        signal.live_price = None
        signal.quote_updated_at = None
        if signal.outcome_status == OptionOutcome.UNRESOLVED or reset_placeholder_outcome:
            signal.outcome_status = OptionOutcome.TRACKING
            signal.outcome_at = None
        signal.save(update_fields=[
            "security_id", "exchange_segment", "dhan_display_name", "expiry_date",
            "live_price", "quote_updated_at", "outcome_status", "outcome_at",
        ])
        resolved += 1
    return resolved


def refresh_dhan_option_prices(signals, force=False):
    signals = list(signals)
    if not signals:
        return {"updated": 0, "error": ""}

    unresolved = [
        signal
        for signal in signals
        if not signal.security_id or signal.exchange_segment != _expected_dhan_segment(signal.option_symbol)
    ]
    if unresolved:
        try:
            resolve_dhan_instruments(unresolved)
        except requests.RequestException as exc:
            return {"updated": 0, "error": f"Instrument lookup failed: {exc}"}

    stale_before = timezone.now() - timedelta(seconds=10)
    resolved_signals = [signal for signal in signals if signal.security_id]
    if not force and resolved_signals and all(
        signal.quote_updated_at and signal.quote_updated_at >= stale_before for signal in resolved_signals
    ):
        return {"updated": 0, "error": ""}

    access_token, client_id = get_dhan_credentials()
    if not access_token or not client_id:
        return {"updated": 0, "error": "Dhan credentials are not configured."}

    payload = {}
    by_security_id = {}
    for signal in signals:
        if not signal.security_id or not signal.exchange_segment:
            continue
        payload.setdefault(signal.exchange_segment, []).append(int(signal.security_id))
        by_security_id.setdefault((signal.exchange_segment, signal.security_id), []).append(signal)
    if not payload:
        return {"updated": 0, "error": "No Dhan instruments could be resolved."}

    try:
        response = requests.post(DHAN_LTP_URL, json=payload, headers=_dhan_headers(access_token), timeout=20)
        response.raise_for_status()
        response_data = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"updated": 0, "error": f"Dhan quote request failed: {exc}"}

    now = timezone.now()
    updated = 0
    for segment, prices in response_data.get("data", {}).items():
        for security_id, quote in prices.items():
            try:
                live_price = Decimal(str(quote["last_price"]))
            except (KeyError, ValueError):
                continue
            if live_price <= 0:
                continue
            for signal in by_security_id.get((segment, str(security_id)), []):
                signal.live_price = live_price
                signal.quote_updated_at = now
                if signal.outcome_status == OptionOutcome.TRACKING:
                    if live_price <= signal.stop_loss:
                        signal.outcome_status = OptionOutcome.STOP_LOSS
                        signal.outcome_at = now
                    elif signal.target_1 is not None and live_price >= signal.target_1:
                        signal.outcome_status = OptionOutcome.TARGET_1
                        signal.outcome_at = now
                signal.save(update_fields=["live_price", "quote_updated_at", "outcome_status", "outcome_at"])
                updated += 1
    return {"updated": updated, "error": ""}


def _legacy_db_path():
    return Path(__file__).resolve().parents[2] / "arc_trading.db"


def sync_chartink_from_legacy(limit=300):
    db_path = _legacy_db_path()
    if not db_path.exists():
        return 0, "Legacy DB not found"

    from .models import ChartinkTrigger

    created = 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source_name, parsed_symbol, source_target_1, event_time, raw_text
            FROM signal_events
            WHERE UPPER(COALESCE(source_type, '')) IN ('SCANNER', 'CHARTINK')
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        for row in rows:
            source_ref, scanner_name, symbol, tprice, event_time, notes = row
            if not symbol:
                continue
            exists = ChartinkTrigger.objects.filter(
                scanner_name=str(scanner_name or "Chartink"),
                symbol=str(symbol),
                trigger_time=event_time,
            ).exists()
            if exists:
                continue
            ChartinkTrigger.objects.create(
                scanner_name=str(scanner_name or "Chartink"),
                symbol=str(symbol),
                trigger_price=Decimal(str(tprice or "0") or "0") if str(tprice or "").strip() else None,
                trigger_time=event_time,
                notes=str(notes or ""),
                status="MONITORING",
            )
            created += 1
    except Exception as exc:
        return created, f"Sync error: {exc}"
    finally:
        conn.close()

    return created, "OK"


def sync_telegram_from_legacy(limit=300, trade_style=TradeStyle.INTRADAY):
    db_path = _legacy_db_path()
    if not db_path.exists():
        return 0, "Legacy DB not found"

    created = 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source_name, raw_text, parsed_symbol, parsed_trade_type,
                   source_sl, source_target_1, source_target_2, event_time
            FROM signal_events
            WHERE UPPER(COALESCE(source_type, '')) = 'TELEGRAM'
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        for row in rows:
            (
                source_ref,
                source_name,
                raw_text,
                parsed_symbol,
                parsed_trade_type,
                sl,
                t1,
                t2,
                event_time,
            ) = row

            text = str(raw_text or "")
            parsed = parse_tip_text(text)
            symbol = str(parsed_symbol or parsed.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            direction = parsed.get("direction")
            if not direction:
                direction = Direction.PE if "PE" in symbol or " PUT" in text.upper() else Direction.CE

            stop_loss = parsed.get("sl") or (Decimal(str(sl)) if str(sl or "").strip() else None)
            if stop_loss is None:
                continue

            exists = TipSignal.objects.filter(source_type="TELEGRAM", source_ref=str(source_ref)).exists()
            if exists:
                continue

            signal = TipSignal(
                source_type="TELEGRAM",
                source_name=str(source_name or "Telegram"),
                source_ref=str(source_ref),
                raw_text=text,
                option_symbol=symbol,
                direction=direction,
                trade_style=trade_style,
                entry_price=parsed.get("entry"),
                stop_loss=stop_loss,
                target_1=parsed.get("t1") or (Decimal(str(t1)) if str(t1 or "").strip() else None),
                target_2=parsed.get("t2") or (Decimal(str(t2)) if str(t2 or "").strip() else None),
                target_3=parsed.get("t3"),
                tip_time=event_time,
                status=SignalStatus.CANDIDATE,
            )
            signal.score, signal.recommendation, signal.reason_tags = score_signal(signal)
            signal.save()
            created += 1
    except Exception as exc:
        return created, f"Sync error: {exc}"
    finally:
        conn.close()

    return created, "OK"


def sync_index_oi_from_legacy():
    db_path = _legacy_db_path()
    if not db_path.exists():
        return 0, "Legacy DB not found"

    from .models import IndexOISnapshot

    interval = get_oi_interval_seconds()
    inserted = 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        for label, probe in (("NIFTY", "%NIFTY%"), ("SENSEX", "%SENSEX%")):
            cur.execute(
                """
                SELECT call_oi, put_oi
                FROM oi_snapshots
                WHERE UPPER(underlying) LIKE ?
                AND timestamp = (
                    SELECT MAX(timestamp) FROM oi_snapshots WHERE UPPER(underlying) LIKE ?
                )
                """,
                (probe, probe),
            )
            rows = cur.fetchall()
            if not rows:
                continue
            call_oi = int(sum(float(r[0] or 0) for r in rows))
            put_oi = int(sum(float(r[1] or 0) for r in rows))
            pcr = round((put_oi / call_oi), 3) if call_oi else 0.0
            regime = classify_regime(put_oi, call_oi)
            IndexOISnapshot.objects.create(
                underlying=label,
                call_oi=call_oi,
                put_oi=put_oi,
                pcr=pcr,
                regime=regime,
                interval_seconds=interval,
            )
            inserted += 1
    except Exception as exc:
        return inserted, f"Sync error: {exc}"
    finally:
        conn.close()

    return inserted, "OK"


def place_super_order(signal, quantity):
    access_token, dhan_client_id = get_dhan_credentials()
    if not access_token or not dhan_client_id:
        return {"ok": False, "error": "Missing DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID env vars."}

    correlation_id = f"arc-{uuid.uuid4().hex[:12]}"
    security_id = str(signal.security_id or os.getenv("DHAN_SECURITY_ID_FALLBACK", "")).strip()
    if not security_id or security_id == "0":
        return {"ok": False, "error": "Missing security ID on signal. Add valid security_id before placing trade."}

    payload = {
        "dhanClientId": dhan_client_id,
        "correlationId": correlation_id,
        "transactionType": "BUY" if signal.direction == Direction.CE else "BUY",
        "exchangeSegment": "NSE_FNO",
        "productType": "INTRADAY" if signal.trade_style == TradeStyle.INTRADAY else "MARGIN",
        "orderType": "LIMIT" if signal.entry_price else "MARKET",
        "securityId": security_id,
        "quantity": int(quantity),
        "price": float(signal.entry_price or 0),
        "targetPrice": float(signal.target_1 or signal.entry_price or 0),
        "stopLossPrice": float(signal.stop_loss),
        "trailingJump": 0,
    }

    try:
        response = requests.post(
            "https://api.dhan.co/v2/super/orders",
            json=payload,
            headers=_dhan_headers(access_token),
            timeout=20,
        )
        payload_json = response.json() if response.content else {}
    except Exception as exc:
        payload_json = {"error": str(exc)}
        DhanOrderEvent.objects.create(status="FAILED", correlation_id=correlation_id, payload_json=payload_json)
        return {"ok": False, "error": str(exc), "correlation_id": correlation_id}

    ok = response.status_code < 300 and payload_json.get("orderId")
    status = payload_json.get("orderStatus", "FAILED")
    DhanOrderEvent.objects.create(
        order_id=str(payload_json.get("orderId", "")),
        correlation_id=correlation_id,
        status=status,
        payload_json={"request": payload, "response": payload_json, "http": response.status_code},
    )

    if not ok:
        return {
            "ok": False,
            "error": payload_json.get("errorMessage") or f"Dhan API failed with status {response.status_code}",
            "correlation_id": correlation_id,
        }

    return {
        "ok": True,
        "order_id": str(payload_json.get("orderId")),
        "status": status,
        "correlation_id": correlation_id,
    }
