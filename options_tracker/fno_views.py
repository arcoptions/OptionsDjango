from django.db.models import F, OuterRef, Q, Subquery
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
    latest_index_pk = IndexOISnapshot.objects.filter(
        underlying=OuterRef("underlying"),
    ).order_by("-created_at", "-pk").values("pk")[:1]
    index_rows = list(
        IndexOISnapshot.objects.filter(pk=Subquery(latest_index_pk)).order_by(
            "underlying",
        )
    )
    latest_score_pk = StockScoreSnapshot.objects.filter(
        score_type="FNO",
        symbol=OuterRef("symbol"),
    ).order_by("-source_at", "-pk").values("pk")[:1]
    score_rows = StockScoreSnapshot.objects.filter(
        score_type="FNO",
        pk=Subquery(latest_score_pk),
    )
    opportunities = UnifiedOpportunity.objects.select_related("instrument").filter(
        Q(instrument__kind__in=[InstrumentKind.OPTION, InstrumentKind.FUTURE])
        | Q(direction__in=["CE", "PE"]),
    ).exclude(
        status__in=[OpportunityStatus.CLOSED, OpportunityStatus.REJECTED],
    )[:30]
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
        "scores": score_rows.order_by(
            F("score").desc(nulls_last=True),
        )[:30],
        "open_trades": TradeExecution.objects.select_related("signal").filter(
            state=TradeState.OPEN,
        )[:30],
        "execution": execution_policy(),
    })
