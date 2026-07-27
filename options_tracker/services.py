import os
import re
import sqlite3
import uuid
from pathlib import Path
from decimal import Decimal
from datetime import datetime

import requests
from django.db.models import Q
from django.utils import timezone

from .models import AppSetting, DhanOrderEvent, Direction, SignalStatus, TipSignal, TradeExecution, TradeState, TradeStyle


def parse_tip_text(raw_text):
    text = str(raw_text or "").strip().upper()
    symbol_match = re.search(r"\b([A-Z0-9]*\d+[A-Z0-9]*?(?:CE|PE))\b", text)
    symbol = symbol_match.group(1) if symbol_match else ""

    direction = None
    if symbol.endswith("CE"):
        direction = Direction.CE
    elif symbol.endswith("PE"):
        direction = Direction.PE
    elif re.search(r"\bCE\b", text):
        direction = Direction.CE
    elif re.search(r"\bPE\b", text):
        direction = Direction.PE

    def find_decimal(pattern):
        match = re.search(pattern, text)
        if not match:
            return None
        try:
            return Decimal(match.group(1))
        except Exception:
            return None

    entry = find_decimal(r"BUY[^\n\r]{0,80}?\bAT\s*(\d+(?:\.\d+)?)")
    if entry is None:
        entry = find_decimal(r"(?:ENTRY|ABOVE)\s*(?:AT|[:@\- ])\s*(\d+(?:\.\d+)?)")
    if entry is None:
        entry = find_decimal(r"\bAT\s*(\d+(?:\.\d+)?)")
    sl = find_decimal(r"(?:SL|STOP ?LOSS)\s*(?:AT|[:@\- ])\s*(\d+(?:\.\d+)?)")
    t1 = find_decimal(r"(?:T1|TARGET\s*1?)\s*[:@\- ]\s*(\d+(?:\.\d+)?)")
    t2 = find_decimal(r"(?:T2|TARGET\s*2)\s*[:@\- ]\s*(\d+(?:\.\d+)?)")
    t3 = find_decimal(r"(?:T3|TARGET\s*3)\s*[:@\- ]\s*(\d+(?:\.\d+)?)")

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "t3": t3,
    }


def score_signal(signal):
    base = 45
    tags = []

    if signal.entry_price and signal.stop_loss:
        risk = abs(signal.entry_price - signal.stop_loss)
        if risk > 0:
            rr = Decimal("0")
            if signal.target_1:
                rr = (signal.target_1 - signal.entry_price) / risk if signal.direction == Direction.CE else (signal.entry_price - signal.target_1) / risk
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
    setting, _ = AppSetting.objects.get_or_create(key="oi_interval_seconds", defaults={"value": "60"})
    try:
        v = int(setting.value)
    except Exception:
        v = 60
    if v not in (60, 120):
        v = 60
    return v


def set_oi_interval_seconds(seconds):
    value = "120" if int(seconds) == 120 else "60"
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


def _dhan_headers(access_token):
    return {"Content-Type": "application/json", "access-token": access_token}


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
    access_token = os.getenv("DHAN_ACCESS_TOKEN", "")
    dhan_client_id = os.getenv("DHAN_CLIENT_ID", "")
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
