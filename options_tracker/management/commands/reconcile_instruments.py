"""Map existing radar instruments into the neutral canonical identity table."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from options_tracker.instrument_identity import register_instrument
from options_tracker.radar_models import RadarInstrument


class Command(BaseCommand):
    help = "Report or apply canonical mappings for existing RadarInstrument rows."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 5000:
            raise CommandError("--limit must be between 1 and 5000")
        total = RadarInstrument.objects.count()
        unresolved = RadarInstrument.objects.filter(canonical_instrument__isnull=True)
        unresolved_count = unresolved.count()
        rows = list(unresolved.order_by("pk")[:limit])
        applied = 0
        with transaction.atomic():
            for row in rows:
                if options["apply"]:
                    instrument = register_instrument(
                        canonical_symbol=row.symbol,
                        display_name=row.name,
                        kind=row.kind,
                        exchange_segment=row.segment,
                        security_id=row.security_id,
                        underlying_symbol=row.symbol if row.kind == "OPTION" else "",
                        expiry_date=row.expiry,
                        strike=row.strike,
                        option_type=row.side,
                        lot_size=row.lot_size,
                        aliases=(("RADAR", row.symbol), ("DHAN", row.security_id)),
                    )
                    row.canonical_instrument = instrument
                    row.save(update_fields=["canonical_instrument"])
                    applied += 1
            if not options["apply"]:
                transaction.set_rollback(True)

        result = {
            "mode": "apply" if options["apply"] else "dry-run",
            "radar_instruments": total,
            "already_mapped": total - unresolved_count,
            "batch_size": len(rows),
            "applied": applied,
            "remaining": unresolved_count - applied,
        }
        self.stdout.write(json.dumps(result))
