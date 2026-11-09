"""Read-only personal trading desk assembled from existing ARC records."""

from django.urls import reverse
from django.utils import timezone

from .models import (
    BrokerPeriodSummary,
    OpportunityStatus,
    TradeExecution,
    TradeState,
    UnifiedOpportunity,
)
from .radar_models import RadarPriceAlert


def personal_desk_context(*, user, now=None):
    now = now or timezone.now()
    active_alerts = RadarPriceAlert.objects.select_related("instrument").filter(
        user=user,
        cancelled_at__isnull=True,
        expires_at__gt=now,
    )
    planned_setups = UnifiedOpportunity.objects.select_related("instrument").filter(
        status__in=[OpportunityStatus.PLANNED, OpportunityStatus.APPROVED],
    )[:8]
    open_trades = TradeExecution.objects.select_related("signal").filter(
        state=TradeState.OPEN,
    )[:8]
    latest_statement = BrokerPeriodSummary.objects.order_by("-period_to", "-pk").first()
    return {
        "active_alerts": active_alerts[:8],
        "active_alert_count": active_alerts.count(),
        "triggered_alert_count": active_alerts.filter(triggered_at__isnull=False).count(),
        "planned_setups": planned_setups,
        "planned_setup_count": UnifiedOpportunity.objects.filter(
            status__in=[OpportunityStatus.PLANNED, OpportunityStatus.APPROVED],
        ).count(),
        "open_trades": open_trades,
        "open_trade_count": TradeExecution.objects.filter(state=TradeState.OPEN).count(),
        "latest_statement": latest_statement,
        "destinations": (
            ("Opportunity Inbox", reverse("opportunity_inbox"), "Validate and approve manual setups."),
            ("Trading Workspace", reverse("radar"), "Manage watches and personal price alerts."),
            ("Trade Journal", reverse("trade_journal"), "Record decisions, emotions, and evidence."),
            ("Performance", reverse("performance_dashboard"), "Review broker and paper results separately."),
        ),
    }
