"""Map existing radar instruments into the neutral canonical identity table."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

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
        parser.add_argument("--batches", type=int, default=1)
        parser.add_argument("--after-pk", type=int, default=0)
        parser.add_argument("--kind", choices=["EQUITY", "OPTION"])

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 5000:
            raise CommandError("--limit must be between 1 and 5000")
        batches = options["batches"]
        if batches < 1 or batches > 100:
            raise CommandError("--batches must be between 1 and 100")
        after_pk = options["after_pk"]
        if after_pk < 0:
            raise CommandError("--after-pk cannot be negative")
        total = RadarInstrument.objects.count()
        scoped = RadarInstrument.objects.all()
        if options["kind"]:
            scoped = scoped.filter(kind=options["kind"])
        scoped_count = scoped.count()
        unresolved = scoped.filter(canonical_instrument__isnull=True)
        unresolved_count = unresolved.count()
        applied = 0
        errors = []
        examined = 0
        cursor = after_pk
        batch_reports = []
        for batch_number in range(1, batches + 1):
            rows = list(unresolved.filter(pk__gt=cursor).order_by("pk")[:limit])
            if not rows:
                break
            batch_applied = 0
            batch_errors = 0
            if options["apply"]:
                with transaction.atomic():
                    for row in rows:
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
                        except (IntegrityError, ValueError) as error:
                            errors.append({
                                "radar_instrument": row.pk,
                                "security_id": row.security_id,
                                "error": str(error),
                            })
                            batch_errors += 1
                            continue
                        row.canonical_instrument = instrument
                        row.save(update_fields=["canonical_instrument"])
                        applied += 1
                        batch_applied += 1
            examined += len(rows)
            cursor = rows[-1].pk
            batch_reports.append({
                "batch": batch_number,
                "examined": len(rows),
                "applied": batch_applied,
                "errors": batch_errors,
                "last_pk": cursor,
            })
            if len(rows) < limit:
                break

        remaining = unresolved.count() if options["apply"] else unresolved_count
        more_after_cursor = unresolved.filter(pk__gt=cursor).exists()
        result = {
            "mode": "apply" if options["apply"] else "dry-run",
            "radar_instruments": total,
            "kind": options["kind"] or "ALL",
            "scoped_instruments": scoped_count,
            "already_mapped": scoped_count - unresolved_count,
            "batch_limit": limit,
            "requested_batches": batches,
            "completed_batches": len(batch_reports),
            "batch_size": examined,
            "applied": applied,
            "remaining": remaining,
            "started_after_pk": after_pk,
            "next_after_pk": cursor if more_after_cursor else None,
            "batches": batch_reports,
            "errors": errors,
        }
        self.stdout.write(json.dumps(result))
