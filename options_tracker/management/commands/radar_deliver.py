"""Opt-in alert delivery in a worker independent of live data collection."""

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from options_tracker.models import RadarWorker
from options_tracker.radar_alerts import deliver_alerts
from options_tracker.radar_feed import acquire_worker, heartbeat
from options_tracker.radar_price_alerts import process_price_alerts


class Command(BaseCommand):
    help = "Explicitly send PAPER radar Telegram alerts: --once or --loop. No Dhan connection or orders."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        owner = None
        try:
            owner = acquire_worker(timezone.now(), name="paper-alerts")
            while True:
                # One HTTP request per heartbeat bounds lease and shutdown delay.
                heartbeat(owner, "DELIVERING", "Checking paper alert outbox.", timezone.now(), name="paper-alerts")
                result = deliver_alerts(limit=1)
                result["price_alerts"] = process_price_alerts(limit=50)
                state = "DEGRADED" if result["failed"] or result["price_alerts"]["failed"] else "IDLE"
                heartbeat(owner, state, json.dumps(result), timezone.now(), name="paper-alerts")
                close_old_connections()
                if options["once"]:
                    self.stdout.write(json.dumps(result))
                    return
                time.sleep(2)
        except KeyboardInterrupt:
            self.stdout.write("Paper alert delivery stopped.")
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        finally:
            if owner:
                RadarWorker.objects.filter(name="paper-alerts", owner=owner).update(
                    lease_until=timezone.now(), state="STOPPED",
                )
