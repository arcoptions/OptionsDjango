from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .investment_forms import (
    InvestmentDividendForm,
    InvestmentHoldingForm,
    InvestmentIdeaForm,
)
from .models import InvestmentHolding, InvestmentIdea, InvestmentIdeaStatus


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET", "POST"])
def investment_desk(request):
    idea_form = InvestmentIdeaForm(prefix="idea")
    holding_form = InvestmentHoldingForm(prefix="holding", owner=request.user)
    dividend_form = InvestmentDividendForm(prefix="dividend", owner=request.user)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create_idea":
                idea_form = InvestmentIdeaForm(request.POST, prefix="idea")
                if idea_form.is_valid():
                    idea = idea_form.save(commit=False)
                    idea.owner = request.user
                    conflict = InvestmentIdea.objects.filter(
                        owner=request.user,
                        symbol=idea.symbol,
                        status__in=[
                            InvestmentIdeaStatus.WATCHING,
                            InvestmentIdeaStatus.HOLDING,
                        ],
                    ).exists()
                    if conflict and idea.status in {
                        InvestmentIdeaStatus.WATCHING,
                        InvestmentIdeaStatus.HOLDING,
                    }:
                        idea_form.add_error(
                            "symbol",
                            "An active investment thesis already exists for this symbol.",
                        )
                    else:
                        idea.save()
                        messages.success(request, f"{idea.symbol} investment thesis saved.")
                        return redirect("investment_desk")
            elif action == "save_holding":
                idea_id = request.POST.get("holding-idea")
                existing = InvestmentHolding.objects.filter(
                    idea_id=idea_id,
                    idea__owner=request.user,
                ).first()
                holding_form = InvestmentHoldingForm(
                    request.POST,
                    prefix="holding",
                    owner=request.user,
                    instance=existing,
                )
                if holding_form.is_valid():
                    holding = holding_form.save()
                    if holding.idea.status != InvestmentIdeaStatus.HOLDING:
                        holding.idea.status = InvestmentIdeaStatus.HOLDING
                        holding.idea.save(update_fields=["status", "updated_at"])
                    messages.success(request, f"{holding.idea.symbol} holding saved.")
                    return redirect("investment_desk")
            elif action == "record_dividend":
                dividend_form = InvestmentDividendForm(
                    request.POST,
                    prefix="dividend",
                    owner=request.user,
                )
                if dividend_form.is_valid():
                    dividend = dividend_form.save()
                    messages.success(
                        request,
                        f"{dividend.holding.idea.symbol} dividend recorded.",
                    )
                    return redirect("investment_desk")
            else:
                messages.error(request, "Unknown Investment Desk action.")
        except IntegrityError:
            messages.error(
                request,
                "Investment record was not saved because it duplicates an existing record.",
            )

    ideas = list(
        InvestmentIdea.objects.filter(owner=request.user)
        .select_related("instrument")
        .prefetch_related("holding__dividends")
    )
    holdings = [
        idea.holding
        for idea in ideas
        if hasattr(idea, "holding") and idea.holding.closed_on is None
    ]
    total_value = sum(
        (
            holding.quantity
            * (holding.current_price or holding.average_cost)
            for holding in holdings
        ),
        Decimal("0"),
    )
    holding_rows = []
    for holding in holdings:
        value = holding.quantity * (holding.current_price or holding.average_cost)
        cost = holding.quantity * holding.average_cost
        holding_rows.append({
            "holding": holding,
            "value": value,
            "unrealised": value - cost,
            "allocation_pct": value / total_value * 100 if total_value else Decimal("0"),
            "dividends": sum(
                (item.amount_total for item in holding.dividends.all()),
                Decimal("0"),
            ),
        })
    today = timezone.localdate()
    return render(request, "options_tracker/investment_desk.html", {
        "title": "Investment Desk",
        "idea_form": idea_form,
        "holding_form": holding_form,
        "dividend_form": dividend_form,
        "ideas": ideas,
        "holding_rows": holding_rows,
        "total_value": total_value,
        "review_due": [
            idea
            for idea in ideas
            if idea.review_on <= today
            and idea.status in {
                InvestmentIdeaStatus.WATCHING,
                InvestmentIdeaStatus.HOLDING,
            }
        ],
    })
