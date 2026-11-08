from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .execution_policy import execution_policy
from .fno_intelligence import iv_percentile_rows
from .models import (
    IndexOISnapshot,
    InstrumentKind,
    OpportunityStatus,
    StockScoreSnapshot,
    TradeExecution,
    TradeState,
    UnifiedOpportunity,
)
from .radar_models import RadarOpportunity


@require_GET
def fno_desk(request):
    indexes = {}
    for snapshot in IndexOISnapshot.objects.order_by("underlying", "-created_at"):
        indexes.setdefault(snapshot.underlying, snapshot)
    scores = {}
    for score in StockScoreSnapshot.objects.filter(
        score_type="FNO",
    ).order_by("symbol", "-source_at"):
        scores.setdefault(score.symbol, score)
    opportunities = UnifiedOpportunity.objects.select_related("instrument").filter(
        Q(instrument__kind__in=[InstrumentKind.OPTION, InstrumentKind.FUTURE])
        | Q(direction__in=["CE", "PE"]),
    ).exclude(
        status__in=[OpportunityStatus.CLOSED, OpportunityStatus.REJECTED],
    )[:30]
    index_rows = list(indexes.values())
    return render(request, "options_tracker/fno_desk.html", {
        "title": "F&O Desk",
        "indexes": index_rows,
        "iv_rows": iv_percentile_rows(
            snapshot.underlying for snapshot in index_rows
        ),
        "radar_opportunities": RadarOpportunity.objects.select_related(
            "contract",
        ).exclude(
            state__in=[
                RadarOpportunity.State.EXITED,
                RadarOpportunity.State.REJECTED,
                RadarOpportunity.State.EXPIRED,
                RadarOpportunity.State.INVALIDATED,
                RadarOpportunity.State.SKIPPED,
            ],
        )[:30],
        "opportunities": opportunities,
        "scores": sorted(
            scores.values(),
            key=lambda score: score.score if score.score is not None else -1,
            reverse=True,
        )[:30],
        "open_trades": TradeExecution.objects.select_related("signal").filter(
            state=TradeState.OPEN,
        )[:30],
        "execution": execution_policy(),
    })
