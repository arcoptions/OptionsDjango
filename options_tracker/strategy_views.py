from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .strategy_forms import StrategyDefinitionForm, StrategyManifestForm
from .models import StrategyRunManifest
from .strategy_lab import (
    create_definition,
    create_manifest,
    promote_manifest,
    promotion_assessment,
)


@staff_member_required(login_url="admin:login")
@require_http_methods(["GET", "POST"])
def strategy_lab(request):
    definition_form = StrategyDefinitionForm(prefix="definition")
    manifest_form = StrategyManifestForm(prefix="manifest", owner=request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create_definition":
                definition_form = StrategyDefinitionForm(
                    request.POST,
                    prefix="definition",
                )
                if definition_form.is_valid():
                    definition = create_definition(
                        owner=request.user,
                        **definition_form.cleaned_data,
                    )
                    messages.success(request, f"{definition.name} strategy saved.")
                    return redirect("strategy_lab")
            elif action == "create_manifest":
                manifest_form = StrategyManifestForm(
                    request.POST,
                    prefix="manifest",
                    owner=request.user,
                )
                if manifest_form.is_valid():
                    manifest = create_manifest(**manifest_form.cleaned_data)
                    messages.success(
                        request,
                        f"Immutable manifest {manifest.manifest_hash[:12]} recorded.",
                    )
                    return redirect("strategy_lab")
            elif action == "promote_manifest":
                manifest = get_object_or_404(
                    StrategyRunManifest.objects.select_related("definition"),
                    pk=request.POST.get("manifest_id"),
                    definition__owner=request.user,
                )
                definition = promote_manifest(
                    manifest=manifest,
                    actor=request.user,
                    reason=request.POST.get("reason", ""),
                )
                messages.success(
                    request,
                    f"{definition.name} approved for paper observation only.",
                )
                return redirect("strategy_lab")
            else:
                messages.error(request, "Unknown Strategy Lab action.")
        except IntegrityError:
            messages.error(
                request,
                "Strategy Lab rejected a duplicate definition or manifest.",
            )
        except ValueError as error:
            messages.error(request, f"Strategy Lab rejected the request: {error}")
    definitions = request.user.strategy_definitions.prefetch_related("manifests")
    manifests = [
        manifest
        for definition in definitions
        for manifest in definition.manifests.all()
    ][:100]
    for manifest in manifests:
        manifest.promotion_assessment = promotion_assessment(manifest)
    return render(request, "options_tracker/strategy_lab.html", {
        "title": "Strategy Lab",
        "definition_form": definition_form,
        "manifest_form": manifest_form,
        "definitions": definitions,
        "manifests": manifests,
    })
