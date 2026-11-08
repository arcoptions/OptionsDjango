from datetime import timedelta

from django.utils import timezone

from .models import EconomicEvent, MarketContextCategory, MarketContextSnapshot


STALE_AFTER = {
    MarketContextCategory.GLOBAL_INDEX: 6 * 60 * 60,
    MarketContextCategory.MACRO: 24 * 60 * 60,
    MarketContextCategory.FLOW: 24 * 60 * 60,
    MarketContextCategory.SECTOR: 6 * 60 * 60,
}


def market_context_evidence(now=None):
    now = now or timezone.now()
    latest = {}
    for snapshot in MarketContextSnapshot.objects.order_by(
        "category",
        "key",
        "-source_at",
    ):
        latest.setdefault((snapshot.category, snapshot.key), snapshot)
    groups = {category: [] for category in MarketContextCategory.values}
    for snapshot in latest.values():
        age_seconds = max(0, int((now - snapshot.source_at).total_seconds()))
        groups[snapshot.category].append({
            "key": snapshot.key,
            "value": snapshot.value,
            "unit": snapshot.unit,
            "provider": snapshot.provider,
            "source_ref": snapshot.source_ref,
            "source_at": snapshot.source_at,
            "age_seconds": age_seconds,
            "stale": age_seconds > STALE_AFTER[snapshot.category],
        })
    events = EconomicEvent.objects.filter(
        starts_at__gte=now,
        starts_at__lte=now + timedelta(days=7),
    )[:30]
    return {
        "groups": groups,
        "events": events,
        "latest_at": max(
            (snapshot.source_at for snapshot in latest.values()),
            default=None,
        ),
    }
