"""Scheduled, rate-limited repair of absent minutes with immutable conflict audit."""

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone
from options_tracker.radar_history import accept_incomplete_repairs


class Command(BaseCommand):
    help = "Explicit history reconciliation --once or --loop (15-minute cadence in weekday market hours). No orders."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--loop", action="store_true")
        parser.add_argument("--symbols", nargs="+")
        parser.add_argument("--accept-incomplete", action="store_true", help="Replace incomplete canonical bars only after preserving original/proposed audit versions.")

    def handle(self, *args, **options):
        try:
            while True:
                local = timezone.localtime()
                if options["once"] or (local.weekday() < 5 and 9*60+15 <= local.hour*60+local.minute <= 15*60+45):
                    call_command("radar_backfill", days=1, symbols=options["symbols"])
                    if options["accept_incomplete"]:
                        self.stdout.write(f"Audited incomplete repairs accepted: {accept_incomplete_repairs(timezone.now(), options['symbols'])}")
                    if options["once"]:
                        return
                    time.sleep(900)
                else:
                    time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write("History reconciliation stopped.")
