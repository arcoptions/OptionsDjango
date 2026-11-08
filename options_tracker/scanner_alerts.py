"""Private advisory delivery for versioned scanner matches."""

from datetime import timedelta
from html import escape
import logging

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from . import telegram_bot
from .models import RadarNotificationRoute, ScannerMatch


LOGGER = logging.getLogger(__name__)


def _message(match):
    evidence = []
    for field, item in sorted(match.evidence.items()):
        actual = item.get("actual")
        target = item.get("target")
        comparison = item.get("operator")
        provider = item.get("provider")
        evidence.append(
            f"{escape(field)} {escape(str(actual))} "
            f"{escape(str(comparison))} {escape(str(target))}"
            f" ({escape(str(provider))})",
        )
    return (
        f"<b>ARC {escape(match.state.lower())} scanner alert</b>\n"
        f"{escape(match.definition.name)}: {escape(match.symbol)}\n"
        f"{'<br>'.join(evidence)}\n"
        "ALERT ONLY: review the evidence before creating any trade plan. "
        "This alert cannot place a broker order."
    )


def deliver_scanner_alerts(now=None, limit=100):
    now = now or timezone.now()
    result = {"sent": 0, "failed": 0}
    pending = ScannerMatch.objects.filter(
        is_active=True,
        acknowledged_at__isnull=True,
        last_alerted_at__isnull=False,
        next_delivery_at__lte=now,
        definition__owner__is_active=True,
        definition__owner__is_staff=True,
    ).filter(
        Q(telegram_sent_at__isnull=True)
        | Q(telegram_sent_at__lt=F("last_alerted_at")),
    )
    ids = list(
        pending.order_by("next_delivery_at", "pk").values_list("pk", flat=True)[:limit],
    )
    for match_id in ids:
        with transaction.atomic():
            match = ScannerMatch.objects.select_for_update().select_related(
                "definition__owner",
            ).get(pk=match_id)
            if (
                not match.is_active
                or match.acknowledged_at is not None
                or match.last_alerted_at is None
                or (
                    match.telegram_sent_at is not None
                    and match.telegram_sent_at >= match.last_alerted_at
                )
            ):
                continue
            route = RadarNotificationRoute.objects.filter(
                user=match.definition.owner,
            ).first()
            if route is None:
                match.last_delivery_error = (
                    "Private Telegram route missing; reconnect notifications."
                )
                match.next_delivery_at = now + timedelta(minutes=5)
                match.save(update_fields=["last_delivery_error", "next_delivery_at"])
                result["failed"] += 1
                continue
            match.delivery_attempts += 1
            match.next_delivery_at = now + timedelta(seconds=60)
            match.save(update_fields=["delivery_attempts", "next_delivery_at"])
            chat_id = route.telegram_chat_id
            alert_version = match.last_alerted_at
            text = _message(match)
        ok, error = telegram_bot.send(text, chat_id=chat_id)
        if ok:
            ScannerMatch.objects.filter(pk=match_id).update(
                telegram_sent_at=alert_version,
                last_delivery_error="",
            )
            result["sent"] += 1
        else:
            retry_seconds = min(300, 5 * 2 ** min(match.delivery_attempts, 6))
            ScannerMatch.objects.filter(pk=match_id).update(
                last_delivery_error=error or "Telegram delivery failed without a reason.",
                next_delivery_at=now + timedelta(seconds=retry_seconds),
            )
            LOGGER.error("Scanner match #%s delivery failed: %s", match_id, error)
            result["failed"] += 1
    return result
