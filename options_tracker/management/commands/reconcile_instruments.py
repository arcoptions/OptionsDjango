"""Map existing radar instruments into the neutral canonical identity table."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from options_tracker.instrument_identity import canonical_kind
from options_tracker.models import CanonicalInstrument, InstrumentAlias
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


def _instrument_values(row):
    return {
        "canonical_symbol": row.symbol.strip().upper(),
        "display_name": row.name.strip(),
        "kind": canonical_kind(row.kind),
        "underlying_symbol": row.symbol.strip().upper() if row.kind == "OPTION" else "",
        "expiry_date": row.expiry,
        "strike": row.strike,
        "option_type": row.side.strip().upper(),
        "lot_size": max(1, int(row.lot_size or 1)),
        "active": True,
    }


def _alias_keys(row):
    segment = row.segment.strip().upper()
    return (
        ("RADAR", radar_alias(row).strip().upper(), segment),
        ("DHAN_SECURITY_ID", row.security_id.strip().upper(), segment),
    )


def reconcile_batch(rows):
    segments = {row.segment.strip().upper() for row in rows}
    security_ids = {row.security_id.strip() for row in rows}
    existing_instruments = {
        (instrument.exchange_segment, instrument.security_id): instrument
        for instrument in CanonicalInstrument.objects.filter(
            exchange_segment__in=segments,
            security_id__in=security_ids,
        )
    }
    alias_keys = [key for row in rows for key in _alias_keys(row)]
    existing_aliases = {
        (alias.provider, alias.value, alias.exchange_segment): alias
        for alias in InstrumentAlias.objects.select_for_update().filter(
            provider__in={key[0] for key in alias_keys},
            value__in={key[1] for key in alias_keys},
            exchange_segment__in=segments,
        )
    }

    accepted = []
    errors = []
    claimed_aliases = {}
    for row in rows:
        identity = (row.segment.strip().upper(), row.security_id.strip())
        instrument = existing_instruments.get(identity)
        conflict = None
        for alias_key in _alias_keys(row):
            existing_alias = existing_aliases.get(alias_key)
            if existing_alias and (
                instrument is None or existing_alias.instrument_id != instrument.pk
            ):
                conflict = (
                    f"Alias {alias_key[0]}:{alias_key[1]} already maps to "
                    f"instrument {existing_alias.instrument_id}"
                )
                break
            claimed_identity = claimed_aliases.get(alias_key)
            if claimed_identity and claimed_identity != identity:
                conflict = (
                    f"Alias {alias_key[0]}:{alias_key[1]} is claimed by "
                    f"{claimed_identity[0]}:{claimed_identity[1]} in this batch"
                )
                break
        if conflict:
            errors.append({
                "radar_instrument": row.pk,
                "security_id": row.security_id,
                "error": conflict,
            })
            continue
        accepted.append(row)
        for alias_key in _alias_keys(row):
            claimed_aliases[alias_key] = identity

    missing = []
    for row in accepted:
        identity = (row.segment.strip().upper(), row.security_id.strip())
        if identity not in existing_instruments:
            missing.append(CanonicalInstrument(
                exchange_segment=identity[0],
                security_id=identity[1],
                **_instrument_values(row),
            ))
    CanonicalInstrument.objects.bulk_create(missing)

    accepted_ids = {row.security_id.strip() for row in accepted}
    instruments = {
        (instrument.exchange_segment, instrument.security_id): instrument
        for instrument in CanonicalInstrument.objects.filter(
            exchange_segment__in=segments,
            security_id__in=accepted_ids,
        )
    }
    for row in accepted:
        instrument = instruments[(row.segment.strip().upper(), row.security_id.strip())]
        for field, value in _instrument_values(row).items():
            setattr(instrument, field, value)
    CanonicalInstrument.objects.bulk_update(
        list(instruments.values()),
        [
            "canonical_symbol",
            "display_name",
            "kind",
            "underlying_symbol",
            "expiry_date",
            "strike",
            "option_type",
            "lot_size",
            "active",
        ],
    )

    aliases_to_create = []
    for row in accepted:
        instrument = instruments[(row.segment.strip().upper(), row.security_id.strip())]
        for provider, value, segment in _alias_keys(row):
            if (provider, value, segment) not in existing_aliases:
                aliases_to_create.append(InstrumentAlias(
                    instrument=instrument,
                    provider=provider,
                    value=value,
                    exchange_segment=segment,
                ))
        row.canonical_instrument = instrument
    InstrumentAlias.objects.bulk_create(aliases_to_create)
    RadarInstrument.objects.bulk_update(accepted, ["canonical_instrument"])
    return len(accepted), errors


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
                    batch_applied, new_errors = reconcile_batch(rows)
                    applied += batch_applied
                    errors.extend(new_errors)
                    batch_errors = len(new_errors)
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
