import requests
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from .ai_research import generate_research
from .azure_openai import provider_readiness
from .models import AIResearchSnapshot


SUGGESTED_RESEARCH_QUESTIONS = (
    "What current evidence supports the market regime, and what could invalidate it?",
    "Which stored opportunity has the strongest evidence, and which risks remain unresolved?",
    "Where is ARC's current evidence missing, stale, invalid, or conflicting?",
)


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
        {
            "provider_status": provider_readiness(),
            "snapshots": snapshots,
            "suggested_questions": SUGGESTED_RESEARCH_QUESTIONS,
        },
        status=status,
    )
