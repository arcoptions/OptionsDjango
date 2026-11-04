"""Import explicit true one-minute equity bars for radar warmup and replay."""

import csv
from datetime import datetime, time, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from options_tracker.models import RadarBar, RadarInstrument
from options_tracker.radar import Candle, validate_candle


class Command(BaseCommand):
    help = "Import start-stamped aware one-minute CSV: symbol,ts,open,high,low,close,volume. Existing bars are immutable."

    def add_arguments(self, parser):
        parser.add_argument("--input", type=Path, required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        imported = 0
        try:
            with options["input"].open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    stamp = datetime.fromisoformat(row["ts"])
                    bar = Candle(stamp, *(float(row[key]) for key in ["open", "high", "low", "close"]), int(row["volume"]))
                    validate_candle(bar)
                    local = timezone.localtime(stamp)
                    if local.weekday() >= 5 or not time(9, 15) <= local.time() < time(15, 30):
                        raise ValueError("Only regular-session weekday bars may be imported.")
                    if stamp+timedelta(minutes=1) > timezone.now():
                        raise ValueError("Cannot import future or forming bars.")
                    instrument = RadarInstrument.objects.get(symbol=row["symbol"].upper(), kind="EQUITY", active=True)
                    values = dict(open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume,
                                  complete=True, source="IMPORTED_OHLC")
                    existing, created = RadarBar.objects.get_or_create(instrument=instrument, timestamp=stamp, defaults=values)
                    if not created and any(getattr(existing, key) != values[key] for key in ["open", "high", "low", "close", "volume", "complete"]):
                        raise ValueError("Import conflicts with an immutable existing bar.")
                    imported += int(created)
        except (ValueError, KeyError, OSError, RadarInstrument.DoesNotExist) as error:
            raise CommandError(f"Import rolled back: {error}") from error
        self.stdout.write(f"Imported {imported} minute bars. Historical bars do not create historical entry alerts.")
