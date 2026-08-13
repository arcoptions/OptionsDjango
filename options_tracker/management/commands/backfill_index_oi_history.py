from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from options_tracker.index_oi_services import (
    INDEX_CONFIG,
    backfill_fixed_option_history,
    backfill_rolling_option_history,
)


class Command(BaseCommand):
    help = "Backfill Dhan rolling index-option candles with OI, IV, volume, strike and spot."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--interval", type=int, choices=(1, 5, 15, 25, 60), default=1)
        parser.add_argument("--expiry-code", type=int, choices=(1, 2), default=1)
        parser.add_argument("--underlying", choices=tuple(INDEX_CONFIG), action="append")
        parser.add_argument("--date", type=date.fromisoformat)

    def handle(self, *args, **options):
        days = min(max(options["days"], 1), 30)
        to_date = timezone.localdate() + timedelta(days=1)
        from_date = to_date - timedelta(days=days)
        underlyings = options["underlying"] or list(INDEX_CONFIG)
        for underlying in underlyings:
            if options["date"]:
                created = backfill_fixed_option_history(
                    underlying,
                    options["date"],
                    interval=options["interval"],
                )
                if not created:
                    self.stdout.write(self.style.WARNING(
                        f"{underlying}: Dhan returned no fixed-contract candles for {options['date']}."
                    ))
                    continue
                self.stdout.write(self.style.SUCCESS(
                    f"{underlying}: processed {created} fixed-contract candles."
                ))
                continue
            created = backfill_rolling_option_history(
                underlying,
                from_date,
                to_date,
                interval=options["interval"],
                expiry_code=options["expiry_code"],
            )
            if not created:
                self.stdout.write(self.style.WARNING(
                    f"{underlying}: Dhan expired-options API returned no candles for "
                    f"{from_date} through {to_date}."
                ))
                continue
            self.stdout.write(self.style.SUCCESS(f"{underlying}: processed {created} historical candles."))