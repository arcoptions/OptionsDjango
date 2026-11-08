"""Read-only operational health assembled from existing runtime records."""

import json
from datetime import datetime, timedelta

from django.utils import timezone

from .deployment import running_commit, short, sync_status
from .execution_policy import execution_policy
from .live_dashboard import engine_snapshot
from .models import AppSetting, IndexOISnapshot, MarketContextSnapshot
from .radar_assistant import worker_health
from .radar_models import RadarQuote, RadarWorker
from .services import _token_expiry, _token_is_spent, get_dhan_credentials


def _age_seconds(timestamp, now):
    if timestamp is None:
        return None
    return max(0, int((now - timestamp).total_seconds()))


def _freshness_row(name, timestamp, stale_after, now, detail):
    age = _age_seconds(timestamp, now)
    return {
        "name": name,
        "updated_at": timestamp,
        "age_seconds": age,
        "status": "CURRENT" if age is not None and age <= stale_after else "STALE / MISSING",
        "healthy": age is not None and age <= stale_after,
        "detail": detail,
    }


def _telegram_status():
    raw = AppSetting.objects.filter(key="telegram_tracker_status").values_list("value", flat=True).first()
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}, None
    heartbeat = payload.get("heartbeat_at")
    try:
        timestamp = datetime.fromisoformat(heartbeat) if heartbeat else None
    except (TypeError, ValueError):
        timestamp = None
    if timestamp and timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp)
    return payload, timestamp


def operations_snapshot(now=None):
    now = now or timezone.now()
    deployment = sync_status()
    engine = engine_snapshot(now)
    radar = worker_health(now)
    policy = execution_policy()
    current_commit = running_commit()

    latest_oi = IndexOISnapshot.objects.order_by("-created_at").first()
    latest_quote_at = RadarQuote.objects.order_by("-received_at").values_list("received_at", flat=True).first()
    latest_context = MarketContextSnapshot.objects.order_by("-source_at").first()
    telegram, telegram_at = _telegram_status()
    access_token, client_id = get_dhan_credentials()
    token_expiry = _token_expiry(access_token) if access_token else None
    token_healthy = bool(access_token and client_id and not _token_is_spent(access_token))

    providers = [
        {
            "name": "Dhan credentials",
            "updated_at": token_expiry,
            "age_seconds": None,
            "status": "READY" if token_healthy else "EXPIRED / MISSING",
            "healthy": token_healthy,
            "detail": "Client ID and unexpired token are present. Token value is never displayed.",
        },
        _freshness_row(
            "Index OI and market ticker",
            latest_oi.created_at if latest_oi else None,
            300,
            now,
            latest_oi.underlying if latest_oi else "No stored index snapshot",
        ),
        _freshness_row(
            "Radar market quotes",
            latest_quote_at,
            30,
            now,
            f"{radar['fresh']} of {radar['universe']} active equity quotes currently satisfy freshness policy",
        ),
        _freshness_row(
            "Telegram tracker",
            telegram_at,
            120,
            now,
            str(telegram.get("state") or telegram.get("status") or "No heartbeat"),
        ),
        _freshness_row(
            "Global, macro, flow, and sector context",
            latest_context.source_at if latest_context else None,
            24 * 60 * 60,
            now,
            (
                f"{latest_context.provider}: {latest_context.category} / {latest_context.key}"
                if latest_context else "No imported provider context"
            ),
        ),
    ]

    workers = []
    for worker in RadarWorker.objects.order_by("name"):
        age = _age_seconds(worker.heartbeat_at, now)
        alive = worker.lease_until > now and age is not None and age <= 120
        workers.append({
            "name": worker.name,
            "owner": worker.owner,
            "state": worker.state if alive else "OFFLINE / STALE",
            "heartbeat_at": worker.heartbeat_at,
            "age_seconds": age,
            "alive": alive,
            "detail": worker.detail,
        })

    return {
        "generated_at": now,
        "execution": {
            "mode": policy.mode.value,
            "automatic_entries": policy.allows_automatic_entries,
            "position_management": policy.allows_position_management,
            "configuration_error": policy.configuration_error,
        },
        "engine": engine,
        "deployment": {
            **deployment,
            "running": current_commit,
            "running_short": short(current_commit),
        },
        "providers": providers,
        "workers": workers,
        "radar": radar,
        "healthy_provider_count": sum(row["healthy"] for row in providers),
        "provider_count": len(providers),
    }
