from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import OpportunityStatus, UnifiedOpportunity


@login_required(login_url="admin:login")
@require_GET
def opportunity_inbox(request):
    selected_status = str(request.GET.get("status") or "").strip().upper()
    opportunities = UnifiedOpportunity.objects.select_related("instrument").prefetch_related(
        "revisions",
    )
    if selected_status in OpportunityStatus.values:
        opportunities = opportunities.filter(status=selected_status)
    return render(
        request,
        "options_tracker/opportunity_inbox.html",
        {
            "title": "Opportunity Inbox",
            "opportunities": opportunities[:100],
            "selected_status": selected_status,
            "status_choices": OpportunityStatus.choices,
        },
    )
