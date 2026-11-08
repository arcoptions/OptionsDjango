import json

from django.core.management.base import BaseCommand

from options_tracker.models import ScannerDefinition
from options_tracker.scanner_alerts import deliver_scanner_alerts
from options_tracker.scanner_engine import evaluate_scanner


class Command(BaseCommand):
    help = "Evaluate enabled advisory scanner definitions against fresh stored quotes."

    def handle(self, *args, **options):
        results = []
        for definition in ScannerDefinition.objects.filter(enabled=True):
            run = evaluate_scanner(definition)
            results.append({
                "scanner": definition.name,
                "scanned": run.scanned,
                "approaching": run.approaching,
                "confirmed": run.confirmed,
            })
        delivery = deliver_scanner_alerts()
        self.stdout.write(json.dumps({
            "scanners": len(results),
            "results": results,
            "delivery": delivery,
        }))
