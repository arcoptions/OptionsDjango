from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import ScannerDefinition, ScannerMatch
from .scanner_engine import FIELDS, OPERATORS, validate_conditions


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET", "POST"])
def scanner_builder(request):
    if request.method == "POST":
        condition = {
            "field": request.POST.get("field"),
            "operator": request.POST.get("operator"),
            "value": request.POST.get("value"),
        }
        try:
            name = str(request.POST.get("name") or "").strip()
            if not name:
                raise ValueError("Scanner name is required")
            conditions = validate_conditions([condition])
            approaching = float(request.POST.get("approaching_threshold_pct") or 10)
            if not 0 <= approaching <= 100:
                raise ValueError("Approaching threshold must be between 0 and 100")
            cooldown = int(request.POST.get("cooldown_minutes") or 30)
            if not 1 <= cooldown <= 1440:
                raise ValueError("Alert cooldown must be between 1 and 1440 minutes")
            ScannerDefinition.objects.create(
                name=name,
                owner=request.user,
                conditions=conditions,
                approaching_threshold_pct=approaching,
                cooldown_minutes=cooldown,
            )
        except (ValueError, IntegrityError) as error:
            messages.error(request, f"Scanner was not saved: {error}")
        else:
            messages.success(request, "Scanner definition saved. Evaluation remains advisory.")
        return redirect("scanner_builder")
    return render(request, "options_tracker/scanner_builder.html", {
        "title": "Scanner Builder",
        "definitions": ScannerDefinition.objects.prefetch_related("runs")[:100],
        "matches": ScannerMatch.objects.filter(is_active=True).select_related(
            "definition",
        )[:100],
        "fields": sorted(FIELDS),
        "operators": sorted(OPERATORS),
    })


@staff_member_required(login_url="admin:login")
@require_POST
def acknowledge_match(request, match_id):
    match = get_object_or_404(ScannerMatch, pk=match_id, is_active=True)
    match.acknowledged_at = timezone.now()
    match.save(update_fields=["acknowledged_at"])
    messages.success(request, f"{match.symbol} scanner match acknowledged.")
    return redirect("scanner_builder")
