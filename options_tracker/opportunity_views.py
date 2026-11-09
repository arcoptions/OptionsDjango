from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import OpportunityStatus, UnifiedOpportunity
from .opportunities import derivative_plan_issues, transition_opportunity


MANUAL_DECISION_STATUSES = {
    OpportunityStatus.VALIDATED,
    OpportunityStatus.PLANNED,
    OpportunityStatus.APPROVED,
    OpportunityStatus.REJECTED,
}


@login_required(login_url="admin:login")
@require_GET
def opportunity_inbox(request):
    selected_status = str(request.GET.get("status") or "").strip().upper()
    opportunities = UnifiedOpportunity.objects.select_related("instrument").prefetch_related(
        "revisions",
        "status_events__actor",
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


def _plan_from_post(request):
    targets = [
        value.strip()
        for value in request.POST.get("targets", "").split(",")
        if value.strip()
    ]
    return {
        "entry": request.POST.get("entry", "").strip(),
        "stop_loss": request.POST.get("stop_loss", "").strip(),
        "targets": targets,
        "quantity": request.POST.get("quantity", "").strip(),
        "estimated_charges": request.POST.get("estimated_charges", "").strip(),
        "risk_amount": request.POST.get("risk_amount", "").strip(),
        "invalidation": request.POST.get("invalidation", "").strip(),
    }


@staff_member_required(login_url="admin:login")
@require_POST
def opportunity_transition(request, opportunity_id):
    opportunity = get_object_or_404(UnifiedOpportunity, pk=opportunity_id)
    target = request.POST.get("status", "").strip().upper()
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A decision reason is required.")
        return redirect("opportunity_inbox")
    if len(reason) > 500:
        messages.error(request, "Decision reason must be 500 characters or fewer.")
        return redirect("opportunity_inbox")
    if target not in MANUAL_DECISION_STATUSES:
        messages.error(request, "Execution is not available from the Opportunity Inbox.")
        return redirect("opportunity_inbox")

    try:
        with transaction.atomic():
            if target == OpportunityStatus.PLANNED:
                if opportunity.status != OpportunityStatus.VALIDATED:
                    raise ValueError(
                        f"Invalid opportunity transition: {opportunity.status} -> {target}"
                    )
                proposed_plan = _plan_from_post(request)
                original_plan = opportunity.plan
                opportunity.plan = proposed_plan
                issues = derivative_plan_issues(opportunity)
                opportunity.plan = original_plan
                if issues:
                    raise ValueError("Risk plan is incomplete: " + ", ".join(issues))
                opportunity.plan = proposed_plan
                opportunity.save(update_fields=["plan", "updated_at"])
            transition_opportunity(
                opportunity,
                target,
                reason=reason,
                actor=request.user,
                context={"channel": "OPPORTUNITY_INBOX"},
            )
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Opportunity moved to {target}.")
    return redirect("opportunity_inbox")
