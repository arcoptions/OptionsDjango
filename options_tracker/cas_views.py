"""Dashboard and status API for the Nifty Closing Auction Session collector."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import AppSetting, CasAuctionEvent, DhanOrderEvent

ENABLED_KEY = "nifty_cas_enabled"
STATUS_KEY = "nifty_cas_collector_status"
NOWCAST_KEY = "nifty_cas_nowcast"
STALE_AFTER = timedelta(seconds=20)


def _setting_json(key: str) -> tuple[dict[str, object], object | None]:
    row = AppSetting.objects.filter(key=key).first()
    if not row:
        return {}, None
    try:
        value = json.loads(str(row.value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"state": "ERROR", "message": f"Invalid {key} payload"}, row.updated_at
    return value if isinstance(value, dict) else {}, row.updated_at


def cas_dashboard_state() -> dict[str, object]:
    """Return the persisted collector and forecast state for page/API rendering."""
    enabled_value = (
        AppSetting.objects.filter(key=ENABLED_KEY).values_list("value", flat=True).first()
        or ""
    )
    enabled = str(enabled_value).strip().lower() in {"1", "true", "yes", "on"}
    status, status_at = _setting_json(STATUS_KEY)
    nowcast, nowcast_at = _setting_json(NOWCAST_KEY)
    stale = not status_at or timezone.now() - status_at > STALE_AFTER
    return {
        "enabled": enabled,
        "status": status,
        "status_at": status_at,
        "stale": stale,
        "nowcast": nowcast,
        "nowcast_at": nowcast_at,
        "event_count": CasAuctionEvent.objects.filter(
            session_date=timezone.localdate()
        ).count(),
        "nowcast_count": CasAuctionEvent.objects.filter(
            session_date=timezone.localdate(),
            event_type=CasAuctionEvent.EventType.NOWCAST,
        ).count(),
    }


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET", "POST"])
def cas_dashboard(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action not in {"start", "stop"}:
            messages.error(request, "Unknown CAS collector action.")
            return redirect(reverse("cas_dashboard"))
        enabled = action == "start"
        AppSetting.objects.update_or_create(
            key=ENABLED_KEY,
            defaults={"value": "1" if enabled else "0"},
        )
        DhanOrderEvent.objects.create(
            order_id="",
            correlation_id="",
            status="CAS_SWITCH",
            payload_json={
                "at": timezone.localtime().isoformat(),
                "action": action,
                "actor": request.user.get_username(),
            },
        )
        messages.success(
            request,
            "CAS collector requested. The deployment daemon will connect to Dhan."
            if enabled
            else "CAS collector stopped. Saved auction data is unchanged.",
        )
        return redirect(reverse("cas_dashboard"))

    state = cas_dashboard_state()
    recent_events = CasAuctionEvent.objects.filter(
        session_date=timezone.localdate()
    ).only(
        "event_type", "security_id", "index_name", "observed_at", "payload_json"
    )[:50]
    return render(request, "options_tracker/cas_dashboard.html", {
        "title": "NIFTY Closing Auction",
        "recent_events": recent_events,
        **state,
    })


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET"])
def cas_status_api(request):
    return JsonResponse({"ok": True, **cas_dashboard_state()})
