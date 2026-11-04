"""Explicit reference-data refresh, without orders or historical-data requests."""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker.radar_feed import sync_instruments
from options_tracker.stock_data import load_master


class Command(BaseCommand):
    help = "Refresh paper radar F&O instruments from a local detailed Dhan master or explicitly fetch it."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--master", type=Path)
        source.add_argument("--refresh", action="store_true")

    def handle(self, *args, **options):
        try:
            if options["master"]:
                with options["master"].open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
            else:
                rows = load_master(refresh=True)
            result = sync_instruments(rows, timezone.now())
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Paper radar registry updated: {result}"))
