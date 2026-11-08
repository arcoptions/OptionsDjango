"""Staff-only paper radar dashboard and CSRF-protected acknowledgement."""

import json
from datetime import date, timedelta
from math import isfinite
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .api_contract import versioned_json
from .models import RadarInstrument, RadarOpportunity, RadarWatch, RadarWorkspace, RadarPriceAlert, RadarNotificationRoute, RadarQuote
from .radar import S, STRATEGIES, Policy, fresh, acknowledge
from .radar_price_alerts import alert_payload
from .radar_workspace import preferences, live_workspace_payload
from .action_workspace import operating_workspace_context


@staff_member_required
@require_GET
def dashboard(request):
    try:
        payload = live_workspace_payload(request.user, request.GET.dict())
    except ValueError as error:
        return HttpResponseBadRequest(str(error))
    return render(request, "options_tracker/radar.html", {
        "workspace": payload,
        "strategies": STRATEGIES,
        "states": S.choices,
        **operating_workspace_context(),
    })


@staff_member_required
@require_GET
def workspace_data(request):
    try:
        payload = live_workspace_payload(request.user, request.GET.dict())
    except ValueError as error:
        return versioned_json({"error": str(error)}, status=400)
    response = versioned_json(payload)
    response["Cache-Control"] = "no-store"
    return response


@staff_member_required
@require_POST
def price_alert(request):
    try:
        body = json_object(request)
        if body.get("action") == "cancel":
            if set(body) != {"action", "id"} or type(body["id"]) is not int:
                raise ValueError("An integer alert id is required.")
            item = get_object_or_404(RadarPriceAlert, pk=body["id"], user=request.user)
            if item.sent_at:
                raise ValueError("Delivered alerts cannot be cancelled.")
            item.cancelled_at = timezone.now()
            item.save(update_fields=["cancelled_at"])
            return JsonResponse({"alert": alert_payload(item, timezone.now())})
        if set(body) != {"action", "instrument_id", "direction", "level", "minutes"} or body["action"] != "create":
            raise ValueError("Expected action, instrument_id, direction, level and minutes.")
        if type(body["instrument_id"]) is not int:
            raise ValueError("An integer equity instrument id is required.")
        if body["direction"] not in ["ABOVE", "BELOW"]:
            raise ValueError("Direction must be ABOVE or BELOW.")
        level, minutes = body["level"], body["minutes"]
        if type(level) not in [int, float] or not isfinite(level) or level <= 0:
            raise ValueError("Alert level must be a positive finite number.")
        if type(minutes) is not int or minutes not in [15, 60, 1440, 4320, 10080]:
            raise ValueError("Choose an expiry of 15/60 minutes, 1/3/7 days.")
        if not RadarNotificationRoute.objects.filter(user=request.user).exists():
            raise ValueError("Connect this user's private Telegram route before creating alerts.")
        now = timezone.now()
        if RadarPriceAlert.objects.filter(user=request.user, cancelled_at__isnull=True, sent_at__isnull=True, expires_at__gt=now).count() >= 50:
            raise ValueError("Maximum 50 active personal price alerts. Cancel old alerts first.")
        instrument = get_object_or_404(RadarInstrument, pk=body["instrument_id"], kind="EQUITY", active=True)
        quote = RadarQuote.objects.filter(instrument=instrument).first()
        current = fresh(quote, now, Policy())
        item = RadarPriceAlert.objects.create(
            user=request.user, instrument=instrument, direction=body["direction"], level=level,
            expires_at=now+timedelta(minutes=minutes),
            previous_price=quote.last if current and quote else None,
            previous_at=quote.source_at if current and quote else None,
        )
        return JsonResponse({"alert": alert_payload(item, now)}, status=201)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)


def json_object(request):
    try:
        value = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Invalid JSON request body.") from error
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


@staff_member_required
@require_POST
def workspace_preferences(request):
    try:
        raw = json_object(request)
        if set(raw)-set(preferences({})):
            raise ValueError("Unknown workspace preference.")
        if any(not isinstance(value, str) for value in raw.values()):
            raise ValueError("Workspace preferences must be strings.")
        state = preferences(raw)
        if state["chart_date"]:
            chart_date = date.fromisoformat(state["chart_date"])
            if chart_date.isoformat() != state["chart_date"] or chart_date > timezone.localdate():
                raise ValueError("Chart date must be YYYY-MM-DD and cannot be in the future.")
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    RadarWorkspace.objects.update_or_create(user=request.user, defaults={"preferences": state})
    return JsonResponse({"state": state})


@staff_member_required
@require_POST
def workspace_watch(request):
    try:
        body = json_object(request)
        if set(body)-{"instrument_id", "action", "notes", "watched_level"}:
            raise ValueError("Unknown watchlist field.")
        if not isinstance(body.get("instrument_id"), int) or isinstance(body["instrument_id"], bool):
            raise ValueError("An integer instrument_id is required.")
        if body.get("action") not in {"add", "remove", "save"}:
            raise ValueError("Watch action must be add, remove or save.")
        notes = body.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError("Notes must be text of at most 2000 characters.")
        level = body.get("watched_level")
        if level is not None and (isinstance(level, bool) or not isinstance(level, (int, float)) or not isfinite(level) or level <= 0):
            raise ValueError("Watched level must be a positive finite number or null.")
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    instrument = get_object_or_404(RadarInstrument, pk=body["instrument_id"], kind="EQUITY", active=True)
    if body["action"] == "remove":
        RadarWatch.objects.filter(user=request.user, instrument=instrument).delete()
    elif body["action"] == "add":
        RadarWatch.objects.get_or_create(user=request.user, instrument=instrument)
    else:
        RadarWatch.objects.update_or_create(
            user=request.user, instrument=instrument, defaults={"notes": notes, "watched_level": level},
        )
    return JsonResponse({"watched": body["action"] != "remove", "symbol": instrument.symbol})


@staff_member_required
@require_POST
def acknowledge_view(request, opportunity_id):
    get_object_or_404(RadarOpportunity, pk=opportunity_id)
    action = request.POST.get("action", "")
    if action not in ["take", "skip"]:
        return HttpResponseBadRequest("Action must be take or skip.")
    try:
        item = acknowledge(opportunity_id, action, timezone.now())
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"PAPER opportunity #{item.pk}: {item.get_state_display()}. No broker order placed.")
    symbol = request.POST.get("symbol", "").strip().upper()
    if symbol and RadarInstrument.objects.filter(symbol=symbol, kind="EQUITY").exists():
        return redirect(f"{reverse('radar')}?{urlencode({'symbol': symbol, 'view': 'all', 'q': '', 'strategy': '', 'state': ''})}")
    return redirect("radar")
