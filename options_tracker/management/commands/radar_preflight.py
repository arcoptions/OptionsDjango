"""Explicit daily reference refresh and source readiness, never fake authenticated health."""

import json
import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker import telegram_bot
from options_tracker.models import RadarInstrument, RadarSector, RadarWorker
from options_tracker.radar_history import credentials


class Command(BaseCommand):
    help = "Check credentials, reference age, delivery and Telegram news prerequisites; --refresh updates public references."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true")

    def handle(self, *args, **options):
        credentials_ok = True
        try:
            credentials()
        except ValueError:
            credentials_ok = False
        if options["refresh"]:
            call_command("radar_sync", refresh=True)
            call_command("radar_sectors")
        now = timezone.now()
        universe = RadarInstrument.objects.filter(kind="EQUITY", active=True)
        stale = universe.filter(refreshed_at__lt=now-timezone.timedelta(hours=24)).count()
        result = {
            "dhan_credentials_present_unexpired": credentials_ok,
            "universe": universe.count(), "stale_instruments": stale,
            "telegram_delivery_configured": telegram_bot.configured(),
            "news_credentials_present": bool(os.getenv("TELEGRAM_API_ID") and os.getenv("TELEGRAM_API_HASH")
                                             and (os.getenv("TELEGRAM_SESSION") or os.getenv("TELEGRAM_SESSION_STRING"))),
            "official_industry_mappings": RadarSector.objects.filter(instrument__in=universe, verified_at__gte=now-timezone.timedelta(days=30)).count(),
            "public_news_worker_alive": RadarWorker.objects.filter(
                name="radar-news", lease_until__gt=now, heartbeat_at__gte=now-timezone.timedelta(seconds=90),
            ).exists(),
            "note": "Public BeatTheStreet text/caption collection does not require user credentials. Full Telegram history needs a separate authorized user session. Credential presence is not authenticated feed health or profitability.",
        }
        self.stdout.write(json.dumps(result))
        if not credentials_ok or not universe.exists() or stale or not result["telegram_delivery_configured"]:
            raise CommandError("Daily core preflight failed; see readiness output.")
