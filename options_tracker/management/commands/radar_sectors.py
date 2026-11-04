"""Import attributable official industry classifications without guessing symbols."""

import csv
from io import StringIO
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
import requests

from options_tracker.models import RadarInstrument, RadarSector


SOURCE = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"


class Command(BaseCommand):
    help = "Refresh official Nifty 500 industry metadata for matching F&O equities; unmatched names remain unavailable."

    def add_arguments(self, parser):
        parser.add_argument("--input", type=Path, help="Local copy of the official CSV when the source is unavailable.")

    def handle(self, *args, **options):
        try:
            if options["input"]:
                text = options["input"].read_text(encoding="utf-8-sig")
            else:
                response = requests.get(SOURCE, timeout=30)
                response.raise_for_status()
                text = response.content.decode("utf-8-sig")
            reader = csv.DictReader(StringIO(text))
            if not {"Symbol", "Industry"} <= set(reader.fieldnames or []):
                raise ValueError("Official CSV requires Symbol and Industry columns; existing mappings unchanged.")
            records = {}
            for row in reader:
                symbol, industry = row["Symbol"].strip().upper(), row["Industry"].strip()
                if not symbol or not industry or len(industry) > 100 or symbol in records:
                    raise ValueError("Empty, duplicate or invalid classification row; existing mappings unchanged.")
                records[symbol] = industry
            equities = list(RadarInstrument.objects.filter(kind="EQUITY", active=True))
            matched = [item for item in equities if item.symbol in records]
            if not matched:
                raise ValueError("No active equities matched this source; existing mappings unchanged.")
            now = timezone.now()
            with transaction.atomic():
                RadarSector.objects.bulk_create(
                    [RadarSector(instrument=item, industry=records[item.symbol], source_url=SOURCE, verified_at=now) for item in matched],
                    update_conflicts=True, unique_fields=["instrument"], update_fields=["industry", "source_url", "verified_at"],
                )
            self.stdout.write(f"Official industry mappings: {len(matched)}/{len(equities)} active equities. Unmatched remain unavailable.")
        except requests.RequestException as error:
            raise CommandError(f"Official sector source unavailable ({type(error).__name__}); existing mappings retained. Supply --input with the official CSV.") from None
        except (ValueError, OSError, UnicodeError) as error:
            raise CommandError(str(error)) from error
