"""Post-deployment activation checks for ARC web and worker ownership."""

import json

from django.contrib.staticfiles import finders
from django.core.checks import run_checks
from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from options_tracker.operations_health import operations_snapshot


CRITICAL_ROUTES = (
    "home",
    "action_list",
    "options_tracker",
    "radar",
    "operations_health",
    "dhan_orders",
)
CRITICAL_ASSETS = ("options_tracker/radar_workspace.css",)


class Command(BaseCommand):
    help = "Print a secret-free ARC activation report and optionally fail when critical checks are unhealthy."

    def add_arguments(self, parser):
        parser.add_argument("--require-healthy", action="store_true")
        parser.add_argument("--allow-legacy-auto", action="store_true")

    def handle(self, *args, **options):
        snapshot = operations_snapshot()
        checks = []

        django_errors = run_checks()
        checks.append({
            "name": "django_system_checks",
            "healthy": not django_errors,
            "detail": [str(error) for error in django_errors],
        })

        route_errors = []
        for route_name in CRITICAL_ROUTES:
            try:
                reverse(route_name)
            except NoReverseMatch as error:
                route_errors.append(f"{route_name}: {error}")
        checks.append({
            "name": "critical_routes",
            "healthy": not route_errors,
            "detail": route_errors or list(CRITICAL_ROUTES),
        })

        missing_assets = [asset for asset in CRITICAL_ASSETS if finders.find(asset) is None]
        checks.append({
            "name": "critical_assets",
            "healthy": not missing_assets,
            "detail": missing_assets or list(CRITICAL_ASSETS),
        })

        execution = snapshot["execution"]
        execution_healthy = (
            not execution["configuration_error"]
            and (execution["mode"] != "legacy_auto" or options["allow_legacy_auto"])
        )
        checks.append({
            "name": "execution_policy",
            "healthy": execution_healthy,
            "detail": {
                "mode": execution["mode"],
                "automatic_entries": execution["automatic_entries"],
                "configuration_error": execution["configuration_error"],
            },
        })

        deployment = snapshot["deployment"]
        deployment_healthy = bool(
            deployment["deployed"]
            and deployment["vm"]
            and deployment["in_sync"]
            and not deployment["stale"]
        )
        checks.append({
            "name": "deployment_sync",
            "healthy": deployment_healthy,
            "detail": {
                "app_service": deployment["deployed_short"],
                "vm": deployment["vm_short"],
                "vm_heartbeat_age_seconds": deployment["age_seconds"],
            },
        })

        checks.append({
            "name": "radar_workers_and_quotes",
            "healthy": snapshot["radar"]["healthy"],
            "detail": snapshot["radar"],
        })
        unhealthy_providers = [
            provider["name"] for provider in snapshot["providers"] if not provider["healthy"]
        ]
        checks.append({
            "name": "provider_freshness",
            "healthy": not unhealthy_providers,
            "detail": unhealthy_providers,
        })

        result = {
            "healthy": all(check["healthy"] for check in checks),
            "checks": checks,
            "generated_at": snapshot["generated_at"].isoformat(),
        }
        self.stdout.write(json.dumps(result, default=str))
        if options["require_healthy"] and not result["healthy"]:
            raise CommandError("ARC activation checks failed. Review the JSON report; no secrets are included.")
