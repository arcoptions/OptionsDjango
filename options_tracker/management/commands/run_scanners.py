import json
import time

from django.core.management.base import BaseCommand, CommandError

from options_tracker.models import ScannerDefinition
from options_tracker.scanner_alerts import deliver_scanner_alerts
from options_tracker.scanner_engine import evaluate_scanner


class Command(BaseCommand):
    help = "Evaluate enabled advisory scanner definitions against fresh stored quotes."

    def add_arguments(self, parser):
        parser.add_argument("--daemon", action="store_true")
        parser.add_argument("--interval-seconds", type=int, default=60)
        parser.add_argument(
            "--source",
            choices=["radar", "stock-history"],
            default="radar",
        )

    def _run_once(self, source):
        results = []
        for definition in ScannerDefinition.objects.filter(enabled=True):
            run = evaluate_scanner(definition, source=source)
            results.append({
                "scanner": definition.name,
                "scanned": run.scanned,
                "approaching": run.approaching,
                "confirmed": run.confirmed,
            })
        delivery = (
            deliver_scanner_alerts()
            if source == "RADAR"
            else {"sent": 0, "failed": 0, "skipped": "historical source"}
        )
        return {
            "scanners": len(results),
            "results": results,
            "delivery": delivery,
        }

    def handle(self, *args, **options):
        interval = options["interval_seconds"]
        source = options["source"].replace("-", "_").upper()
        if not 15 <= interval <= 3600:
            raise CommandError("Scanner interval must be between 15 and 3600 seconds.")
        while True:
            self.stdout.write(json.dumps(self._run_once(source)))
            if not options["daemon"]:
                return
            time.sleep(interval)
