from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .deployment import running_commit
from .operations_health import operations_snapshot
from .release_status import release_dashboard_snapshot


@require_GET
def deployment_health(request):
    return JsonResponse({"status": "ok", "commit": running_commit()})


@staff_member_required(login_url="admin:login")
@require_GET
def operations_health(request):
    return render(
        request,
        "options_tracker/operations_health.html",
        {
            "title": "Operations & Data Health",
            "operations": operations_snapshot(),
        },
    )


@staff_member_required(login_url="admin:login")
@require_GET
def release_dashboard(request):
    return render(
        request,
        "options_tracker/release_dashboard.html",
        {
            "title": "ARC 2.0 Release Dashboard",
            "release_dashboard": release_dashboard_snapshot(),
        },
    )
