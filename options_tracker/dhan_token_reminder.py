"""Production-only reminder for a missing or stale daily Dhan UI token."""

import os
from datetime import time
from zoneinfo import ZoneInfo

from django.utils import timezone

from . import telegram_bot
from .models import AppSetting
from .services import _token_is_spent


IST = ZoneInfo("Asia/Kolkata")
REMINDER_START = time(8, 30)
QUIET_START = time(20, 0)
TOKEN_KEY = "dhan_access_token"
SENT_DATE_KEY = "dhan_token_reminder_sent_date"


def reminder_state(*, now=None):
    now = (now or timezone.now()).astimezone(IST)
    environment = os.getenv("ARC_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    if environment != "production":
        return {"state": "SKIPPED_NON_PRODUCTION", "send": False}
    if now.time() < REMINDER_START or now.time() >= QUIET_START:
        return {"state": "QUIET_HOURS", "send": False}

    token_setting = AppSetting.objects.filter(key=TOKEN_KEY).first()
    token = (token_setting.value if token_setting else "").strip()
    updated_today = bool(
        token_setting and token_setting.updated_at.astimezone(IST).date() == now.date()
    )
    if token and updated_today and not _token_is_spent(token):
        return {"state": "CURRENT", "send": False}

    sent_date = AppSetting.objects.filter(key=SENT_DATE_KEY).values_list(
        "value", flat=True
    ).first()
    if sent_date == now.date().isoformat():
        return {"state": "ALREADY_SENT", "send": False}
    return {
        "state": "MISSING" if not token else "STALE_OR_EXPIRED",
        "send": True,
        "date": now.date().isoformat(),
    }


def send_reminder(*, now=None):
    state = reminder_state(now=now)
    if not state["send"]:
        return state
    ok, error = telegram_bot.send(
        "<b>ARC Dhan token reminder</b>\n"
        "Add and validate today's Dhan token in ARC Apps UI before trading. "
        "The token value is never included in this message."
    )
    if not ok:
        return {**state, "state": "DELIVERY_FAILED", "error": error}
    AppSetting.objects.update_or_create(
        key=SENT_DATE_KEY,
        defaults={"value": state["date"]},
    )
    return {**state, "state": "SENT"}
