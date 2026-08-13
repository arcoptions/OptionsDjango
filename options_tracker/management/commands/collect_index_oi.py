import json
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from options_tracker.index_oi_services import collect_all_index_option_chains
from options_tracker.models import AppSetting
from options_tracker.services import get_oi_interval_seconds, is_dhan_market_open


class Command(BaseCommand):
    help = "Continuously collect NIFTY and SENSEX Dhan option-chain and depth snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Collect one snapshot for each index and exit.")

    def handle(self, *args, **options):
        while True:
            market_open = is_dhan_market_open()
            if market_open:
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
                if not market_open:
                    self.stdout.write("Dhan market is closed; no live snapshot collected.")
                break
            time.sleep(get_oi_interval_seconds() if market_open else 60)