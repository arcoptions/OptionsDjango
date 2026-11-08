import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .command_centre import command_centre_context
from .market_context import market_context_evidence
from .models import EconomicEvent, MarketContextSnapshot


class MarketContextTests(TestCase):
    def payload(self, now):
        return {
            "context": [{
                "category": "GLOBAL_INDEX",
                "key": "S&P 500 Futures",
                "value": 0.8,
                "unit": "%",
                "source_ref": "provider-record-1",
                "source_at": now.isoformat(),
            }],
            "events": [{
                "title": "India CPI",
                "event_type": "ECONOMIC",
                "starts_at": (now + timedelta(days=1)).isoformat(),
                "importance": "HIGH",
                "source_ref": "provider-event-1",
            }],
        }

    def run_import(self, payload, *args):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            call_command(
                "import_market_context",
                "--file",
                str(path),
                "--provider",
                "APPROVED_TEST",
                *args,
            )

    def test_import_is_dry_run_by_default_and_idempotent_on_apply(self):
        now = timezone.now()
        self.run_import(self.payload(now))
        self.assertEqual(MarketContextSnapshot.objects.count(), 0)

        self.run_import(self.payload(now), "--apply")
        self.run_import(self.payload(now), "--apply")

        self.assertEqual(MarketContextSnapshot.objects.count(), 1)
        self.assertEqual(EconomicEvent.objects.count(), 1)

    def test_import_rejects_unknown_categories(self):
        now = timezone.now()
        payload = self.payload(now)
        payload["context"][0]["category"] = "EXECUTE_ORDER"
        with self.assertRaises(CommandError):
            self.run_import(payload)

    def test_context_retains_provider_freshness_and_calendar_evidence(self):
        now = timezone.now()
        self.run_import(self.payload(now), "--apply")

        evidence = market_context_evidence(now)
        command = command_centre_context(now)

        self.assertEqual(
            evidence["groups"]["GLOBAL_INDEX"][0]["provider"],
            "APPROVED_TEST",
        )
        self.assertFalse(evidence["groups"]["GLOBAL_INDEX"][0]["stale"])
        self.assertEqual(len(command["market_context"]["events"]), 1)
