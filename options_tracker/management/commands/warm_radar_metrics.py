import json
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError

from options_tracker.radar_workspace import refresh_table_metrics_cache


class Command(BaseCommand):
    help = "Refresh the shared Trading Workspace display-metrics cache."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Refresh every 45 seconds.",
        )

    def handle(self, *args, **options):
        while True:
            try:
                result = refresh_table_metrics_cache()
            except DatabaseError as exc:
                if not options["loop"]:
                    raise
                self.stderr.write(
                    self.style.ERROR(f"Radar metrics refresh failed: {exc}")
                )
            else:
                self.stdout.write(json.dumps(result, sort_keys=True))
            if not options["loop"]:
                return
            time.sleep(45)
