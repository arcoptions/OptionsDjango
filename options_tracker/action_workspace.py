"""Consolidated, evidence-aware action queue and workspace status."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .cas_views import cas_dashboard_state
from .models import (
    AppSetting,
    ChartinkTrigger,
    ChatMessage,
    DailyPick,
    Direction,
    PickSource,
    RadarInstrument,
    SignalStatus,
    SwingSignal,
    SwingStatus,
    TipSignal,
    TriggerStatus,
)
ACTION_LIST_SIGNAL_STATUSES = (
    SignalStatus.NEW,
    SignalStatus.CANDIDATE,
    SignalStatus.ACTIVE,
)
BUY_READY_RECOMMENDATIONS = {"STRONG GO", "BUY", "LONG"}
ACTION_LIST_PRIORITY = {
    "BUY": 5,
    "MANAGE": 4,
    "TRIGGERED": 3,
    "WATCH": 2,
    "REVIEW": 1,
}


def tip_action(signal: TipSignal, today) -> tuple[str, bool, str]:
    if signal.status == SignalStatus.ACTIVE:
        return "MANAGE", False, "Existing active position; manage it rather than buying again."

    levels = (signal.entry_price, signal.stop_loss, signal.target_1)
    if any(value is None or value <= 0 for value in levels):
        return "WATCH", False, "Entry, stop and first target must be present before action."

    if signal.direction in {Direction.CE, Direction.PE}:
        if signal.expiry_date is None:
            return "WATCH", False, "Option expiry is not verified."
        if signal.expiry_date < today:
            return "WATCH", False, "Option contract has expired."

    recommendation = (signal.recommendation or "").strip().upper()
    if signal.score < 75 or recommendation not in BUY_READY_RECOMMENDATIONS:
        return "WATCH", False, "Tracker evidence does not meet the buy-ready gate."

    return "BUY", True, "Current signal with a complete entry, stop and target plan."


def _underlying(option_symbol: str) -> str:
    return re.sub(
        r"\s+\d+(?:\.\d+)?\s+(?:CE|PE)$",
        "",
        option_symbol.strip(),
        flags=re.IGNORECASE,
    ).upper()


def _chart_url(symbol: str, radar_symbols: set[str]) -> str:
    underlying = _underlying(symbol)
    if underlying not in radar_symbols:
        return ""
    return f"{reverse('radar')}?{urlencode({'symbol': underlying})}#selected-stock"


def action_list_context() -> dict[str, object]:
    today = timezone.localdate()
    action_rows: list[dict[str, object]] = []
    radar_symbols = set(
        RadarInstrument.objects.filter(kind="EQUITY", active=True).values_list("symbol", flat=True)
    )

    signals = TipSignal.objects.filter(
        status__in=ACTION_LIST_SIGNAL_STATUSES,
    ).filter(
        Q(direction=Direction.EQ)
        | Q(expiry_date__isnull=True)
        | Q(expiry_date__gte=today)
    ).order_by("-score", "-tip_time")[:50]
    for signal in signals:
        if not signal.option_symbol:
            continue
        action, is_buy, action_reason = tip_action(signal, today)
        action_rows.append({
            "id": signal.pk,
            "symbol": signal.option_symbol,
            "underlying": _underlying(signal.option_symbol),
            "type": "TipSignal" if signal.direction in {Direction.CE, Direction.PE} else "Equity",
            "source": signal.source_name or "Manual",
            "source_type": "Option tracker" if signal.direction in {Direction.CE, Direction.PE} else "Equity tracker",
            "source_key": "tracker",
            "score": signal.score,
            "action": action,
            "action_reason": action_reason,
            "is_buy": is_buy,
            "direction": signal.direction,
            "entry": signal.entry_price,
            "stop": signal.stop_loss,
            "target": signal.target_1,
            "status": signal.status,
            "reason": signal.reason_tags or signal.raw_text[:120],
            "timestamp": signal.tip_time,
            "recommendation": signal.recommendation or "Review",
            "detail_url": reverse("option_edit", args=[signal.pk]),
            "chart_url": _chart_url(signal.option_symbol, radar_symbols),
        })

    for trigger in ChartinkTrigger.objects.filter(status=TriggerStatus.MONITORING).order_by("-trigger_time")[:25]:
        crossed = bool(
            trigger.live_price is not None
            and trigger.trigger_price is not None
            and trigger.live_price >= trigger.trigger_price
        )
        action_rows.append({
            "id": trigger.pk,
            "symbol": trigger.symbol,
            "underlying": trigger.symbol.upper(),
            "type": "Chartink",
            "source": trigger.scanner_name,
            "source_type": "Scanner",
            "source_key": "scanner",
            "score": 55,
            "action": "TRIGGERED" if crossed else "WATCH",
            "action_reason": (
                f"Price {trigger.live_price} crossed scanner level {trigger.trigger_price}; "
                "add a stop and target before buying."
                if crossed else "Scanner is monitoring for its configured level."
            ),
            "is_buy": False,
            "direction": "EQ",
            "entry": trigger.live_price,
            "stop": None,
            "target": None,
            "status": "CANDIDATE",
            "reason": trigger.notes or "Scanner trigger is active.",
            "timestamp": trigger.trigger_time,
            "recommendation": "Watch",
            "detail_url": reverse("scanners"),
            "chart_url": _chart_url(trigger.symbol, radar_symbols),
        })

    for chat in ChatMessage.objects.filter(source_category="TIPS").order_by("-created_at")[:15]:
        if chat.linked_signal_id:
            continue
        text = (chat.normalized_text or chat.raw_text or "").strip()
        if not text:
            continue
        action_rows.append({
            "id": chat.pk,
            "symbol": "Telegram",
            "underlying": "",
            "type": "Telegram",
            "source": chat.source_name or "Telegram",
            "source_type": "Chat",
            "source_key": "telegram",
            "score": 50,
            "action": "REVIEW",
            "action_reason": "Unparsed Telegram message; verify and promote it before action.",
            "is_buy": False,
            "direction": "EQ",
            "entry": None,
            "stop": None,
            "target": None,
            "status": "NEW",
            "reason": text[:150],
            "timestamp": chat.created_at,
            "recommendation": "Review",
            "detail_url": reverse("telegram_feed"),
            "chart_url": "",
        })

    deduped: dict[tuple[object, object, object], dict[str, object]] = {}
    for row in action_rows:
        key = (row["symbol"], row["source"], row["type"])
        existing = deduped.get(key)
        if existing is None or row["timestamp"] > existing["timestamp"]:
            deduped[key] = row

    rows = sorted(
        deduped.values(),
        key=lambda row: (
            ACTION_LIST_PRIORITY[str(row["action"])],
            row["score"],
            row["timestamp"],
        ),
        reverse=True,
    )[:40]

    return {
        "rows": rows,
        "buy_count": sum(1 for row in rows if row["is_buy"]),
        "manage_count": sum(1 for row in rows if row["action"] == "MANAGE"),
        "triggered_count": sum(1 for row in rows if row["action"] == "TRIGGERED"),
        "watch_count": sum(1 for row in rows if row["action"] in {"WATCH", "REVIEW"}),
        "source_count": len({row["source"] for row in rows}),
    }


def strategy_lanes() -> list[dict[str, object]]:
    today = timezone.localdate()
    swing_day = (
        SwingSignal.objects.filter(source=PickSource.LIVE)
        .order_by("-day")
        .values_list("day", flat=True)
        .first()
    )
    shortlist_day = (
        DailyPick.objects.filter(source=PickSource.LIVE)
        .order_by("-day")
        .values_list("day", flat=True)
        .first()
    )
    return [
        {
            "name": "NIFTY intraday options",
            "stage": "EXECUTION BOUNDARY",
            "tone": "live",
            "summary": "Only lane with broker-order controls. Review status and risk before arming.",
            "status": (
                "ARMED"
                if str(
                    AppSetting.objects.filter(key="nifty_live_enabled")
                    .values_list("value", flat=True)
                    .first()
                    or ""
                ).lower() in {"1", "true", "yes", "on"}
                else "DISARMED"
            ),
            "url": reverse("nifty_live"),
        },
        {
            "name": "Strength Dips",
            "stage": "PAPER / MONITOR",
            "tone": "paper",
            "summary": "Stock-only live paper book. The option translation remains research-only.",
            "status": (
                f"{SwingSignal.objects.filter(source=PickSource.LIVE, status__in=[SwingStatus.PENDING, SwingStatus.OPEN]).count()} active"
                if swing_day
                else "No live record"
            ),
            "fresh": bool(swing_day and (today - swing_day).days <= 4),
            "url": reverse("swing_alerts"),
        },
        {
            "name": "Daily movement shortlist",
            "stage": "DISCOVERY ONLY",
            "tone": "research",
            "summary": "Use for names to inspect, never as a buy instruction or validated edge.",
            "status": (
                f"{DailyPick.objects.filter(source=PickSource.LIVE, day=shortlist_day).count()} names"
                if shortlist_day
                else "No live record"
            ),
            "fresh": bool(shortlist_day and (today - shortlist_day).days <= 3),
            "url": reverse("daily_shortlist"),
        },
        {
            "name": "Opening-range momentum",
            "stage": "RESEARCH WATCH",
            "tone": "research",
            "summary": "Short-sample observation only. A confirmed stock watch still requires fresh option and risk checks.",
            "status": "Prospective evidence",
            "url": f"{reverse('radar')}#assistant-research",
        },
        {
            "name": "S1 / S2 / S3 and retired models",
            "stage": "AUDIT / ARCHIVE",
            "tone": "retired",
            "summary": "Negative or insufficient after-cost evidence. Visible for audit, excluded from BUY.",
            "status": "Not promoted",
            "url": reverse("research_report"),
        },
    ]


def operating_workspace_context(
    action: dict[str, object] | None = None,
    cas: dict[str, object] | None = None,
) -> dict[str, object]:
    action = action or action_list_context()
    cas = cas or cas_dashboard_state()
    cas_healthy = bool(
        cas["enabled"]
        and not cas["stale"]
        and cas["event_count"]
        and cas["nowcast_count"]
    )
    return {
        **action,
        "strategy_lanes": strategy_lanes(),
        "cas_summary": {
            **cas,
            "healthy": cas_healthy,
            "decision_use": "CONTEXT ONLY" if cas_healthy else "NOT USED",
            "url": reverse("cas_dashboard"),
        },
    }
