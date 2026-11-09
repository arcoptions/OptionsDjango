"""Map existing radar instruments into the neutral canonical identity table."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from options_tracker.instrument_identity import register_instrument
from options_tracker.radar_models import RadarInstrument


def radar_alias(row):
    if row.kind == "OPTION":
        return ":".join([
            row.symbol,
            row.expiry.isoformat() if row.expiry else "",
            format(row.strike, ".2f") if row.strike is not None else "",
            row.side,
        ])
    return row.symbol


class Command(BaseCommand):
    help = "Report or apply canonical mappings for existing RadarInstrument rows."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--kind", choices=["EQUITY", "OPTION"])

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 5000:
            raise CommandError("--limit must be between 1 and 5000")
        total = RadarInstrument.objects.count()
        scoped = RadarInstrument.objects.all()
        if options["kind"]:
            scoped = scoped.filter(kind=options["kind"])
        scoped_count = scoped.count()
        unresolved = scoped.filter(canonical_instrument__isnull=True)
        unresolved_count = unresolved.count()
        rows = list(unresolved.order_by("pk")[:limit])
        applied = 0
        errors = []
        with transaction.atomic():
            for row in rows:
                if options["apply"]:
                    try:
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
                            aliases=(
                                ("RADAR", radar_alias(row)),
                                ("DHAN_SECURITY_ID", row.security_id),
                            ),
                        )
                    except ValueError as error:
                        errors.append({
                            "radar_instrument": row.pk,
                            "security_id": row.security_id,
                            "error": str(error),
                        })
                        continue
                    row.canonical_instrument = instrument
                    row.save(update_fields=["canonical_instrument"])
                    applied += 1
            if not options["apply"]:
                transaction.set_rollback(True)

        result = {
            "mode": "apply" if options["apply"] else "dry-run",
            "radar_instruments": total,
            "kind": options["kind"] or "ALL",
            "scoped_instruments": scoped_count,
            "already_mapped": scoped_count - unresolved_count,
            "batch_size": len(rows),
            "applied": applied,
            "remaining": unresolved_count - applied,
            "errors": errors,
        }
        self.stdout.write(json.dumps(result))
