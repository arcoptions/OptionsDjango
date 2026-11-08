"""Safe configurable scanner evaluation over fresh stored radar quotes."""

import operator
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone

from .models import (
    ScannerMatch,
    ScannerRun,
    StockFundamentalSnapshot,
    StockScoreSnapshot,
)
from .radar import Policy, fresh
from .radar_models import RadarQuote


FUNDAMENTAL_MAX_AGE = timedelta(days=120)
FIELDS = {
    "last": lambda quote, investment: quote.last,
    "change_pct": lambda quote, investment: (
        (quote.last - quote.previous_close) / quote.previous_close * 100
        if quote.previous_close else None
    ),
    "gap_pct": lambda quote, investment: (
        (quote.day_open - quote.previous_close) / quote.previous_close * 100
        if quote.day_open is not None and quote.previous_close else None
    ),
    "volume_ratio": lambda quote, investment: (
        quote.volume / quote.previous_volume if quote.previous_volume else None
    ),
    "oi": lambda quote, investment: quote.oi,
    "investment_score": lambda quote, investment: (
        investment["score"].score if investment else None
    ),
    "investment_confidence": lambda quote, investment: (
        investment["score"].confidence if investment else None
    ),
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


def _investment_evidence(now):
    scores = {}
    for score in StockScoreSnapshot.objects.filter(
        score_type="INVESTMENT",
        score__isnull=False,
        source_at__lte=now,
    ).order_by("symbol", "-source_at", "-pk"):
        scores.setdefault(score.symbol, score)
    fundamentals = {}
    for fundamental in StockFundamentalSnapshot.objects.filter(
        symbol__in=scores,
        source_at__lte=now,
        source_at__gte=now - FUNDAMENTAL_MAX_AGE,
    ).order_by("symbol", "-source_at", "-pk"):
        fundamentals.setdefault(fundamental.symbol, fundamental)
    return {
        symbol: {"score": score, "fundamental": fundamentals[symbol]}
        for symbol, score in scores.items()
        if symbol in fundamentals
    }


def evaluate_scanner(definition, now=None):
    now = now or timezone.now()
    conditions = validate_conditions(definition.conditions)
    needs_investment = any(
        condition["field"].startswith("investment_")
        for condition in conditions
    )
    investments = _investment_evidence(now) if needs_investment else {}
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
            investment = investments.get(quote.instrument.symbol)
            for condition in conditions:
                field = condition["field"]
                actual = FIELDS[field](quote, investment)
                state = _condition_state(
                    actual,
                    condition,
                    definition.approaching_threshold_pct,
                )
                states.append(state)
                source_at = quote.source_at
                provider = "DHAN"
                if field.startswith("investment_") and investment:
                    source_at = investment["fundamental"].source_at
                    provider = investment["fundamental"].provider
                evidence[field] = {
                    "actual": actual,
                    "operator": condition["operator"],
                    "target": condition["value"],
                    "state": state,
                    "source_at": source_at.isoformat(),
                    "provider": provider,
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
                    "next_delivery_at": now,
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
                    match.next_delivery_at = now
                    match.last_delivery_error = ""
                    update_fields.extend([
                        "last_alerted_at",
                        "acknowledged_at",
                        "next_delivery_at",
                        "last_delivery_error",
                    ])
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
