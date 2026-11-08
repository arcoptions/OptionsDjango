from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .operations_health import operations_snapshot


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
