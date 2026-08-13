from django.core.management.base import BaseCommand

from options_tracker.jump_detector import refresh_historical_jump_report


class Command(BaseCommand):
    help = "Precompute and persist jump-detector reports outside web requests."

    def handle(self, *args, **options):
        for underlying in ("NIFTY", "SENSEX"):
            report = refresh_historical_jump_report(underlying)
            self.stdout.write(f"{underlying}: {report['event_count']} events")