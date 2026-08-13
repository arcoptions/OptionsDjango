import json
import time
from datetime import time as clock_time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from options_tracker.index_oi_services import collect_all_index_option_chains
from options_tracker.models import AppSetting
from options_tracker.services import get_oi_interval_seconds


class Command(BaseCommand):
    help = "Continuously collect NIFTY and SENSEX Dhan option-chain and depth snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Collect one snapshot for each index and exit.")

    def handle(self, *args, **options):
        first_run = True
        while True:
            now = timezone.localtime()
            market_open = now.weekday() < 5 and clock_time(9, 15) <= now.time() <= clock_time(15, 35)
            if options["once"] or market_open or first_run:
                try:
                    snapshots = collect_all_index_option_chains()
                    status = {
                        "state": "RUNNING",
                        "updated_at": timezone.now().isoformat(),
                        "snapshots": {row.underlying: row.id for row in snapshots},
                    }
                    self.stdout.write(
                        "Collected " + ", ".join(f"{row.underlying} #{row.id}" for row in snapshots)
                    )
                except Exception as error:
                    status = {"state": "ERROR", "updated_at": timezone.now().isoformat(), "error": str(error)}
                    self.stderr.write(str(error))
                AppSetting.objects.update_or_create(
                    key="index_oi_collector_status", defaults={"value": json.dumps(status)}
                )
                close_old_connections()
                if options["once"]:
                    break
                first_run = False
            time.sleep(get_oi_interval_seconds() if market_open else 60)