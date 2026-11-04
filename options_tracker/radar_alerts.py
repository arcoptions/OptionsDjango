"""Persistent, opt-in Telegram outbox for paper radar events."""

from datetime import timedelta
import logging

from django.db import transaction
from django.utils import timezone

from . import telegram_bot
from .models import RadarAlert
from .radar import HOLDING, S, executable_entry_quote


LOGGER = logging.getLogger(__name__)


def deliver_alerts(now=None, limit: int = 50) -> dict:
    selected_at = now or timezone.now()
    result = {"sent": 0, "failed": 0, "cancelled": 0}
    for alert_id in RadarAlert.objects.filter(
        sent_at__isnull=True, cancelled_at__isnull=True, next_attempt_at__lte=selected_at,
    ).order_by("pk").values_list("pk", flat=True)[:limit]:
        current = now or timezone.now()
        with transaction.atomic():
            alert = RadarAlert.objects.select_for_update().select_related("opportunity").get(pk=alert_id)
            if alert.sent_at or alert.cancelled_at or alert.next_attempt_at > current:
                continue
            item = alert.opportunity
            expired = alert.state in [S.ARMED, S.TRIGGERED, S.ELIGIBLE] and item.valid_until <= current
            rejection = ""
            if alert.state == S.ELIGIBLE:
                try:
                    executable_entry_quote(item, current)
                except ValueError as error:
                    rejection = str(error)
            state_matches = item.state in HOLDING if alert.state == "DATA_STALE" else alert.state == item.state
            if not state_matches or expired or rejection:
                alert.cancelled_at = current
                alert.last_error = rejection or "Superseded or expired alert."
                alert.save()
                result["cancelled"] += 1
                continue
            alert.attempts += 1
            alert.next_attempt_at = current+timedelta(seconds=60)
            alert.save()
        # Never hold a SQLite write transaction or position lock across HTTP.
        try:
            ok, error = telegram_bot.send(alert.text)
        except ValueError as error:
            RadarAlert.objects.filter(pk=alert.pk).update(last_error=f"Invalid Telegram response: {error}")
            raise
        if ok:
            RadarAlert.objects.filter(pk=alert.pk).update(sent_at=current, last_error="")
            result["sent"] += 1
        else:
            message = error or "Telegram delivery failed without a reason."
            RadarAlert.objects.filter(pk=alert.pk).update(
                last_error=message,
                next_attempt_at=current+timedelta(seconds=min(300, 5*2**min(alert.attempts, 6))),
            )
            LOGGER.error("Radar alert #%s delivery failed: %s", alert.pk, message)
            result["failed"] += 1
    return result
