import requests
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from .ai_research import generate_research
from .models import AIResearchSnapshot


@staff_member_required(login_url="admin:login")
def research_assistant(request):
    status = 200
    if request.method == "POST":
        try:
            generate_research(
                user=request.user,
                question=request.POST.get("question", ""),
                symbol=request.POST.get("symbol", ""),
            )
        except ValueError as error:
            messages.error(request, str(error))
            status = 400
        except requests.RequestException as error:
            messages.error(request, f"Azure OpenAI request failed: {error}")
            status = 502
    snapshots = AIResearchSnapshot.objects.filter(user=request.user)[:20]
    return render(
        request,
        "options_tracker/research_assistant.html",
        {"snapshots": snapshots},
        status=status,
    )
