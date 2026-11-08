"""Safe configurable scanner evaluation over fresh stored radar quotes."""

import operator
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone

from .models import ScannerMatch, ScannerRun
from .radar import Policy, fresh
from .radar_models import RadarQuote


FIELDS = {
    "last": lambda quote: quote.last,
    "change_pct": lambda quote: (
        (quote.last - quote.previous_close) / quote.previous_close * 100
        if quote.previous_close else None
    ),
    "volume_ratio": lambda quote: (
        quote.volume / quote.previous_volume if quote.previous_volume else None
    ),
    "oi": lambda quote: quote.oi,
}
OPERATORS = {
    "gte": operator.ge,
    "lte": operator.le,
    "gt": operator.gt,
    "lt": operator.lt,
}


def validate_conditions(conditions):
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("At least one scanner condition is required")
    normalized = []
    for condition in conditions:
        field = str(condition.get("field") or "").strip()
        comparison = str(condition.get("operator") or "").strip()
        if field not in FIELDS:
            raise ValueError(f"Unsupported scanner field: {field!r}")
        if comparison not in OPERATORS:
            raise ValueError(f"Unsupported scanner operator: {comparison!r}")
        try:
            value = float(condition["value"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Scanner condition value must be numeric") from error
        normalized.append({"field": field, "operator": comparison, "value": value})
    return normalized


def _condition_state(actual, condition, approaching_pct):
    if actual is None:
        return "NO_DATA"
    target = condition["value"]
    comparison = condition["operator"]
    if OPERATORS[comparison](actual, target):
        return "CONFIRMED"
    distance = abs(actual - target) / max(abs(target), 1e-9) * 100
    return "APPROACHING" if distance <= approaching_pct else "NO_MATCH"


def evaluate_scanner(definition, now=None):
    now = now or timezone.now()
    conditions = validate_conditions(definition.conditions)
    run = ScannerRun.objects.create(definition=definition, started_at=now)
    scanned = approaching = confirmed = 0
    try:
        definition.matches.update(is_active=False)
        for quote in RadarQuote.objects.select_related("instrument").filter(
            instrument__kind="EQUITY",
            instrument__active=True,
        ):
            if not fresh(quote, now, Policy()):
                continue
            scanned += 1
            evidence = {}
            states = []
            for condition in conditions:
                actual = FIELDS[condition["field"]](quote)
                state = _condition_state(
                    actual,
                    condition,
                    definition.approaching_threshold_pct,
                )
                states.append(state)
                evidence[condition["field"]] = {
                    "actual": actual,
                    "operator": condition["operator"],
                    "target": condition["value"],
                    "state": state,
                    "source_at": quote.source_at.isoformat(),
                }
            state = "CONFIRMED" if all(item == "CONFIRMED" for item in states) else (
                "APPROACHING"
                if all(item in {"CONFIRMED", "APPROACHING"} for item in states)
                else ""
            )
            if not state:
                continue
            match, created = ScannerMatch.objects.get_or_create(
                definition=definition,
                symbol=quote.instrument.symbol,
                defaults={
                    "state": state,
                    "evidence": evidence,
                    "last_seen_at": now,
                    "last_alerted_at": now,
                },
            )
            if not created:
                state_changed = match.state != state
                cooldown_elapsed = (
                    match.last_alerted_at is None
                    or now >= match.last_alerted_at + timedelta(
                        minutes=definition.cooldown_minutes,
                    )
                )
                match.state = state
                match.evidence = evidence
                match.last_seen_at = now
                match.is_active = True
                update_fields = ["state", "evidence", "last_seen_at", "is_active"]
                if state_changed or cooldown_elapsed:
                    match.last_alerted_at = now
                    match.acknowledged_at = None
                    update_fields.extend(["last_alerted_at", "acknowledged_at"])
                match.save(update_fields=update_fields)
            if state == "CONFIRMED":
                confirmed += 1
            else:
                approaching += 1
    except (ArithmeticError, DatabaseError, TypeError, ValueError) as error:
        run.status = "FAILED"
        run.completed_at = timezone.now()
        run.error = str(error)
        run.save(update_fields=["status", "completed_at", "error"])
        raise
    run.status = "COMPLETE"
    run.completed_at = timezone.now()
    run.scanned = scanned
    run.approaching = approaching
    run.confirmed = confirmed
    run.save(update_fields=[
        "status", "completed_at", "scanned", "approaching", "confirmed",
    ])
    return run
