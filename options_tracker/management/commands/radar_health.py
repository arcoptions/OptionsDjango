"""Read-only, separately executable health and gap checks for external supervisors."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker.radar_assistant import gap_diagnostics, worker_health
from options_tracker.radar_history import credentials
from options_tracker.radar_research import research_report
from options_tracker import telegram_bot


class Command(BaseCommand):
    help = "Print JSON health/gaps/research. --require-healthy exits nonzero on stale workers/data/auth."

    def add_arguments(self, parser):
        parser.add_argument("--require-healthy", action="store_true")
        parser.add_argument("--gaps", action="store_true")
        parser.add_argument("--research", action="store_true")
        parser.add_argument("--symbols", nargs="+")
        parser.add_argument("--require-delivery", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        result = worker_health()
        result["telegram_configured"] = telegram_bot.configured()
        if options["require_delivery"] and (not result["delivery_enabled"] or not result["telegram_configured"]):
            result["healthy"] = False
        try:
            credentials()
            result["credential_preflight"] = "PRESENT_NOT_EXPIRED_NOT_AN_AUTHENTICATED_REQUEST"
        except ValueError as error:
            result["credential_preflight"] = str(error)
            result["healthy"] = False
        if options["gaps"]:
            result["gaps"] = gap_diagnostics(now, options["symbols"])
        if options["research"]:
            result["research"] = research_report(now)
        self.stdout.write(json.dumps(result))
        if options["require_healthy"] and not result["healthy"]:
            raise CommandError("Paper assistant unhealthy. See JSON diagnostics; no secrets are included.")
