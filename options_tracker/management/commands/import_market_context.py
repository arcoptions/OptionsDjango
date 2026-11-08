import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from options_tracker.models import (
    EconomicEvent,
    MarketContextCategory,
    MarketContextSnapshot,
)
from options_tracker.provider_evidence import FUTURE_TOLERANCE


def _timestamp(value, label):
    timestamp = parse_datetime(str(value or ""))
    if timestamp is None:
        raise CommandError(f"{label} must be an ISO 8601 timestamp")
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp)
    return timestamp


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise CommandError("Market context payload must be a JSON object")
    context_rows = []
    for row in payload.get("context", []):
        category = str(row.get("category") or "")
        if category not in MarketContextCategory.values:
            raise CommandError(f"Unsupported market context category: {category!r}")
        try:
            value = float(row["value"])
        except (KeyError, TypeError, ValueError) as error:
            raise CommandError("Market context values must be numeric") from error
        key = str(row.get("key") or "").strip()
        source_ref = str(row.get("source_ref") or "").strip()
        if not key or not source_ref:
            raise CommandError("Market context key and source_ref are required")
        source_at = _timestamp(row.get("source_at"), "source_at")
        if source_at > timezone.now() + FUTURE_TOLERANCE:
            raise CommandError("source_at cannot be more than five minutes in the future")
        context_rows.append({
            "category": category,
            "key": key,
            "value": value,
            "unit": str(row.get("unit") or ""),
            "source_ref": source_ref,
            "source_at": source_at,
            "payload": row.get("payload") or {},
        })
    events = []
    for row in payload.get("events", []):
        importance = str(row.get("importance") or "").upper()
        if importance not in {"LOW", "MEDIUM", "HIGH"}:
            raise CommandError(f"Unsupported event importance: {importance!r}")
        title = str(row.get("title") or "").strip()
        source_ref = str(row.get("source_ref") or "").strip()
        if not title or not source_ref:
            raise CommandError("Event title and source_ref are required")
        events.append({
            "title": title,
            "event_type": str(row.get("event_type") or "ECONOMIC"),
            "starts_at": _timestamp(row.get("starts_at"), "starts_at"),
            "importance": importance,
            "source_ref": source_ref,
            "payload": row.get("payload") or {},
        })
    return context_rows, events


class Command(BaseCommand):
    help = "Validate or import provider-attributed global, macro, flow, sector, and calendar evidence."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, required=True)
        parser.add_argument("--provider", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        path = options["file"]
        if not path.is_file():
            raise CommandError(f"Market context file does not exist: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CommandError(f"Market context file is invalid JSON: {error}") from error
        provider = str(options["provider"] or "").strip()
        if not provider:
            raise CommandError("Provider name is required")
        context_rows, events = validate_payload(payload)
        if options["apply"]:
            for row in context_rows:
                MarketContextSnapshot.objects.update_or_create(
                    category=row["category"],
                    key=row["key"],
                    provider=provider,
                    source_at=row["source_at"],
                    defaults={
                        **row,
                        "provider": provider,
                        "received_at": timezone.now(),
                    },
                )
            for row in events:
                EconomicEvent.objects.update_or_create(
                    provider=provider,
                    source_ref=row["source_ref"],
                    starts_at=row["starts_at"],
                    defaults={
                        **row,
                        "provider": provider,
                        "received_at": timezone.now(),
                    },
                )
        self.stdout.write(json.dumps({
            "mode": "apply" if options["apply"] else "dry-run",
            "provider": provider,
            "context_rows": len(context_rows),
            "events": len(events),
        }))
