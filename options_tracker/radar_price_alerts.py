"""Personal observed crossings: no strategy changes, implied fills or broker orders."""

from datetime import timedelta
from html import escape
import logging

from django.db import transaction
from django.utils import timezone

from . import telegram_bot
from .models import RadarNotificationRoute, RadarPriceAlert, RadarQuote
from .radar import Policy, fresh
from .radar_sessions import continuous_timestamp


LOGGER = logging.getLogger(__name__)


def alert_payload(alert, now):
    status = ("CANCELLED" if alert.cancelled_at else "DELIVERED" if alert.sent_at else
              "EXPIRED" if alert.expires_at <= now else "TRIGGERED" if alert.triggered_at else "WATCHING")
    return {
        "id": alert.pk, "instrument_id": alert.instrument_id, "symbol": alert.instrument.symbol,
        "direction": alert.direction, "level": alert.level, "status": status,
        "expires_at": alert.expires_at.isoformat(),
        "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
        "trigger_price": alert.trigger_price, "last_error": alert.last_error,
    }


def process_price_alerts(now=None, limit=50):
    now = now or timezone.now()
    result = {"triggered": 0, "sent": 0, "failed": 0}
    market_open = continuous_timestamp(now)
    pending = RadarPriceAlert.objects.filter(
        cancelled_at__isnull=True, sent_at__isnull=True, expires_at__gt=now,
        user__is_active=True, user__is_staff=True,
    )
    if not market_open:
        pending = pending.filter(triggered_at__isnull=False)
    ids = list(pending.order_by("checked_at", "created_at", "pk").values_list("pk", flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            item = RadarPriceAlert.objects.select_for_update().select_related("instrument").get(pk=pk)
            if item.cancelled_at or item.sent_at or item.expires_at <= now:
                continue
            item.checked_at = now
            item.save(update_fields=["checked_at"])
            if item.triggered_at is None:
                quote = RadarQuote.objects.filter(instrument=item.instrument, instrument__active=True).first()
                if not fresh(quote, now, Policy()) or quote is None:
                    continue
                if item.previous_at and quote.source_at <= item.previous_at:
                    continue
                contiguous = (item.previous_at is not None and
                              0 < (quote.source_at-item.previous_at).total_seconds() <= 30)
                crossed = contiguous and item.previous_price is not None and (
                    item.previous_price < item.level <= quote.last if item.direction == "ABOVE"
                    else item.previous_price > item.level >= quote.last
                )
                item.previous_price, item.previous_at = quote.last, quote.source_at
                if crossed:
                    item.triggered_at, item.trigger_price = now, quote.last
                    result["triggered"] += 1
                item.save(update_fields=["previous_price", "previous_at", "triggered_at", "trigger_price"])
            if item.triggered_at is None or item.next_attempt_at > now:
                continue
            route = RadarNotificationRoute.objects.filter(user=item.user).first()
            if route is None:
                item.last_error = "Private Telegram route missing; reconnect your notifications."
                item.next_attempt_at = now+timedelta(minutes=1)
                item.save(update_fields=["last_error", "next_attempt_at"])
                LOGGER.error("Price alert #%s has no private notification route.", item.pk)
                result["failed"] += 1
                continue
            # Claim before network I/O; no database lock is held during Telegram calls.
            item.attempts += 1
            item.next_attempt_at = now+timedelta(seconds=60)
            item.save(update_fields=["attempts", "next_attempt_at"])
            chat_id = route.telegram_chat_id
        text = (f"<b>ARC custom price alert #{item.pk}</b>\n{escape(item.instrument.symbol)} crossed "
                f"{item.direction.lower()} {item.level:g}; observed LTP {item.trigger_price:g}.\n"
                f"Observed {timezone.localtime(item.triggered_at):%d %b %H:%M:%S %Z}.\n"
                "Observed quote crossing, not every exchange tick. ALERT ONLY: not a strategy entry, fill or broker order.")
        ok, error = telegram_bot.send(text, chat_id=chat_id)
        if ok:
            RadarPriceAlert.objects.filter(pk=pk).update(sent_at=now, last_error="")
            result["sent"] += 1
        else:
            RadarPriceAlert.objects.filter(pk=pk).update(
                last_error=error or "Telegram delivery failed without a reason.",
                next_attempt_at=now+timedelta(seconds=min(300, 5*2**min(item.attempts, 6))),
            )
            LOGGER.error("Price alert #%s delivery failed: %s", pk, error)
            result["failed"] += 1
    return result
