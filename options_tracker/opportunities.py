"""Canonical cross-source opportunity lifecycle and evidence revisions."""

import hashlib
import json

from django.db import transaction
from django.db.models import Max

from .models import (
    OpportunityRevision,
    OpportunityStatus,
    OpportunityStatusEvent,
    UnifiedOpportunity,
)


ALLOWED_TRANSITIONS = {
    OpportunityStatus.NEW: {OpportunityStatus.VALIDATED, OpportunityStatus.REJECTED},
    OpportunityStatus.VALIDATED: {OpportunityStatus.PLANNED, OpportunityStatus.REJECTED},
    OpportunityStatus.PLANNED: {OpportunityStatus.APPROVED, OpportunityStatus.REJECTED},
    OpportunityStatus.APPROVED: {OpportunityStatus.EXECUTED, OpportunityStatus.REJECTED},
    OpportunityStatus.EXECUTED: {OpportunityStatus.CLOSED},
    OpportunityStatus.CLOSED: set(),
    OpportunityStatus.REJECTED: set(),
}
DERIVATIVE_DIRECTIONS = {"CE", "PE"}
DERIVATIVE_PLAN_FIELDS = {
    "entry",
    "stop_loss",
    "targets",
    "quantity",
    "estimated_charges",
    "risk_amount",
    "invalidation",
}


def _digest(payload):
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _json_safe(payload):
    return json.loads(json.dumps(payload, default=str))


def opportunity_fingerprint(*, source_type, source_ref, strategy, direction="", timeframe=""):
    return _digest({
        "source_type": str(source_type).strip().upper(),
        "source_ref": str(source_ref).strip(),
        "strategy": str(strategy).strip().upper(),
        "direction": str(direction).strip().upper(),
        "timeframe": str(timeframe).strip().upper(),
    })


def derivative_plan_issues(opportunity):
    if opportunity.direction not in DERIVATIVE_DIRECTIONS:
        return []
    plan = opportunity.plan if isinstance(opportunity.plan, dict) else {}
    issues = [
        f"missing {field}"
        for field in sorted(DERIVATIVE_PLAN_FIELDS - set(plan))
    ]
    targets = plan.get("targets")
    if "targets" in plan and (
        not isinstance(targets, list)
        or not targets
        or any(target in (None, "") for target in targets)
    ):
        issues.append("targets must contain at least one level")
    numeric_positive = ("entry", "stop_loss", "quantity", "risk_amount")
    for field in numeric_positive:
        if field in plan:
            try:
                valid = float(plan[field]) > 0
            except (TypeError, ValueError):
                valid = False
            if not valid:
                issues.append(f"{field} must be positive")
    if "estimated_charges" in plan:
        try:
            valid = float(plan["estimated_charges"]) >= 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            issues.append("estimated_charges must not be negative")
    if not str(plan.get("invalidation") or "").strip():
        issues.append("invalidation must explain the failure condition")
    if not opportunity.rationale.strip():
        issues.append("rationale is required")
    return sorted(set(issues))


@transaction.atomic
def upsert_opportunity(
    *,
    source_type,
    source_ref,
    strategy,
    evidence,
    direction="",
    timeframe="",
    instrument=None,
    source_name="",
    score=0,
    confidence=None,
    rationale="",
    plan=None,
    next_action="",
    source_at=None,
    expires_at=None,
):
    if not str(source_ref or "").strip():
        raise ValueError("source_ref is required for opportunity provenance")
    fingerprint = opportunity_fingerprint(
        source_type=source_type,
        source_ref=source_ref,
        strategy=strategy,
        direction=direction,
        timeframe=timeframe,
    )
    opportunity, created = UnifiedOpportunity.objects.update_or_create(
        fingerprint=fingerprint,
        defaults={
            "instrument": instrument,
            "source_type": str(source_type).strip().upper(),
            "source_ref": str(source_ref).strip(),
            "source_name": str(source_name or "").strip(),
            "strategy": str(strategy).strip().upper(),
            "direction": str(direction or "").strip().upper(),
            "timeframe": str(timeframe or "").strip().upper(),
            "score": float(score or 0),
            "confidence": confidence,
            "rationale": str(rationale or "").strip(),
            "plan": _json_safe(plan or {}),
            "next_action": str(next_action or "").strip(),
            "source_at": source_at,
            "expires_at": expires_at,
        },
    )
    evidence_hash = _digest(evidence)
    if not OpportunityRevision.objects.filter(
        opportunity=opportunity,
        evidence_fingerprint=evidence_hash,
    ).exists():
        version = (
            OpportunityRevision.objects.filter(opportunity=opportunity)
            .aggregate(maximum=Max("version"))["maximum"]
            or 0
        ) + 1
        OpportunityRevision.objects.create(
            opportunity=opportunity,
            version=version,
            evidence_fingerprint=evidence_hash,
            evidence=_json_safe(evidence),
            source_at=source_at,
        )
    if created:
        OpportunityStatusEvent.objects.create(
            opportunity=opportunity,
            from_status="",
            to_status=OpportunityStatus.NEW,
            reason="Opportunity created from source evidence.",
        )
    return opportunity


@transaction.atomic
def transition_opportunity(opportunity, to_status, *, reason, actor=None, context=None):
    target = str(to_status or "").strip().upper()
    if target not in OpportunityStatus.values:
        raise ValueError(f"Unknown opportunity status: {to_status!r}")
    if target not in ALLOWED_TRANSITIONS[opportunity.status]:
        raise ValueError(f"Invalid opportunity transition: {opportunity.status} -> {target}")
    if target in {OpportunityStatus.PLANNED, OpportunityStatus.APPROVED}:
        issues = derivative_plan_issues(opportunity)
        if issues:
            raise ValueError(
                "Derivative recommendation contract is incomplete: "
                + ", ".join(issues),
            )
    if target in {OpportunityStatus.APPROVED, OpportunityStatus.REJECTED} and actor is None:
        raise ValueError(f"An authenticated actor is required for {target}")
    previous = opportunity.status
    opportunity.status = target
    opportunity.save(update_fields=["status", "updated_at"])
    OpportunityStatusEvent.objects.create(
        opportunity=opportunity,
        from_status=previous,
        to_status=target,
        reason=str(reason or "").strip(),
        actor=actor,
        context=context or {},
    )
    return opportunity
