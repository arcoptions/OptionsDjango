import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone


def healthy_snapshot():
    return {
        "generated_at": timezone.now(),
        "execution": {
            "mode": "manual",
            "automatic_entries": False,
            "configuration_error": None,
        },
        "deployment": {
            "deployed": "a" * 40,
            "deployed_short": "aaaaaaa",
            "vm": "a" * 40,
            "vm_short": "aaaaaaa",
            "in_sync": True,
            "stale": False,
            "age_seconds": 30,
        },
        "radar": {"healthy": True},
        "providers": [{"name": "Dhan credentials", "healthy": True}],
    }


class ActivationSmokeTests(TestCase):
    @patch("options_tracker.management.commands.arc_activation_smoke.operations_snapshot")
    def test_healthy_activation_report_passes(self, snapshot):
        snapshot.return_value = healthy_snapshot()
        output = StringIO()

        call_command("arc_activation_smoke", "--require-healthy", stdout=output)

        report = json.loads(output.getvalue())
        self.assertTrue(report["healthy"])
        self.assertTrue(all(check["healthy"] for check in report["checks"]))

    @patch("options_tracker.management.commands.arc_activation_smoke.operations_snapshot")
    def test_unhealthy_activation_report_fails_closed(self, snapshot):
        payload = healthy_snapshot()
        payload["deployment"]["in_sync"] = False
        snapshot.return_value = payload

        with self.assertRaises(CommandError):
            call_command("arc_activation_smoke", "--require-healthy", stdout=StringIO())

    @patch("options_tracker.management.commands.arc_activation_smoke.operations_snapshot")
    def test_legacy_auto_requires_explicit_smoke_override(self, snapshot):
        payload = healthy_snapshot()
        payload["execution"]["mode"] = "legacy_auto"
        payload["execution"]["automatic_entries"] = True
        snapshot.return_value = payload

        with self.assertRaises(CommandError):
            call_command("arc_activation_smoke", "--require-healthy", stdout=StringIO())

        output = StringIO()
        call_command(
            "arc_activation_smoke",
            "--require-healthy",
            "--allow-legacy-auto",
            stdout=output,
        )
        self.assertTrue(json.loads(output.getvalue())["healthy"])
