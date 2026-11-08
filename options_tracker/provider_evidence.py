"""Common provenance and freshness metadata for external evidence."""

from datetime import timedelta


FUTURE_TOLERANCE = timedelta(minutes=5)


def evidence_metadata(
    *,
    provider,
    source_ref,
    source_at,
    received_at,
    now,
    stale_after_seconds,
):
    issues = []
    if not str(provider or "").strip():
        issues.append("provider missing")
    if not str(source_ref or "").strip():
        issues.append("source reference missing")
    if source_at > now + FUTURE_TOLERANCE:
        issues.append("source timestamp is in the future")
    if source_at > received_at + FUTURE_TOLERANCE:
        issues.append("source timestamp follows receipt time")
    age_seconds = max(0, int((now - source_at).total_seconds()))
    stale = bool(issues) or age_seconds > stale_after_seconds
    return {
        "provider": provider,
        "source_ref": source_ref,
        "source_at": source_at,
        "received_at": received_at,
        "age_seconds": age_seconds,
        "stale": stale,
        "valid": not issues,
        "issues": issues,
    }
