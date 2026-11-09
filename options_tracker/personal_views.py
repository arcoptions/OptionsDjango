from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .personal_desk import personal_desk_context


@staff_member_required(login_url="admin:login")
@require_GET
def personal_desk(request):
    return render(
        request,
        "options_tracker/personal_desk.html",
        {
            "title": "Personal Trading Desk",
            **personal_desk_context(user=request.user),
        },
    )
