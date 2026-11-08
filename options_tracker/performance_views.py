from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .performance_analytics import performance_context


@staff_member_required(login_url="admin:login")
@require_GET
def performance_dashboard(request):
    context = performance_context()
    return render(request, "options_tracker/performance_dashboard.html", {
        "title": "Performance",
        **context,
        "breakdowns": [
            ("Broker by broker", context["broker_by_broker"]),
            ("Broker by underlying", context["broker_by_underlying"]),
            ("Broker by instrument kind", context["broker_by_kind"]),
            ("Paper radar by strategy", context["paper_by_strategy"]),
            ("Paper radar by symbol", context["paper_by_symbol"]),
        ],
    })
