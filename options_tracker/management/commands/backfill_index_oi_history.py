from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from options_tracker.index_oi_services import INDEX_CONFIG, backfill_rolling_option_history


class Command(BaseCommand):
    help = "Backfill Dhan rolling index-option candles with OI, IV, volume, strike and spot."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--interval", type=int, choices=(1, 5, 15, 25, 60), default=1)
        parser.add_argument("--expiry-code", type=int, choices=(1, 2), default=1)
        parser.add_argument("--underlying", choices=tuple(INDEX_CONFIG), action="append")

    def handle(self, *args, **options):
        days = min(max(options["days"], 1), 30)
        to_date = timezone.localdate() + timedelta(days=1)
        from_date = to_date - timedelta(days=days)
        underlyings = options["underlying"] or list(INDEX_CONFIG)
        for underlying in underlyings:
            created = backfill_rolling_option_history(
                underlying,
                from_date,
                to_date,
                interval=options["interval"],
                expiry_code=options["expiry_code"],
            )
            self.stdout.write(self.style.SUCCESS(f"{underlying}: processed {created} historical candles."))