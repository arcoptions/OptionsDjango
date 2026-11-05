"""Post-close repair of absent minutes with immutable conflict audit."""

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone
from options_tracker.radar_history import accept_incomplete_repairs


class Command(BaseCommand):
    help = "Explicit history reconciliation --once or --loop (one post-close run each weekday). No orders."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--loop", action="store_true")
        parser.add_argument("--symbols", nargs="+")
        parser.add_argument("--accept-incomplete", action="store_true", help="Replace incomplete canonical bars only after preserving original/proposed audit versions.")

    def handle(self, *args, **options):
        reconciled_on = None
        try:
            while True:
                local = timezone.localtime()
                minute = local.hour*60+local.minute
                due = (
                    local.weekday() < 5
                    and 15*60+45 <= minute < 18*60
                    and reconciled_on != local.date()
                )
                if options["once"] or due:
                    call_command("radar_backfill", days=1, symbols=options["symbols"])
                    if options["accept_incomplete"]:
                        self.stdout.write(f"Audited incomplete repairs accepted: {accept_incomplete_repairs(timezone.now(), options['symbols'])}")
                    if options["once"]:
                        return
                    reconciled_on = local.date()
                    time.sleep(60)
                else:
                    time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write("History reconciliation stopped.")
