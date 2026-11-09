from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    DhanOrderEvent,
    ScannerDefinition,
    ScannerMatch,
    RadarNotificationRoute,
    StockEquityCandle,
    StockFundamentalSnapshot,
    StockScoreSnapshot,
)
from .scanner_alerts import deliver_scanner_alerts
from .radar_models import RadarInstrument, RadarQuote
from .scanner_engine import evaluate_scanner, validate_conditions


class ScannerEngineTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="scanner-operator",
            password="test-password",
            is_staff=True,
        )
        self.instrument = RadarInstrument.objects.create(
            security_id="scanner-1",
            segment="NSE_EQ",
            symbol="TEST",
            kind="EQUITY",
        )

    def quote(self, last, previous_close=100):
        now = timezone.now()
        return RadarQuote.objects.create(
            instrument=self.instrument,
            source_at=now,
            received_at=now,
            last=last,
            previous_close=previous_close,
            volume=2000,
            previous_volume=1000,
        )

    def test_only_whitelisted_fields_and_operators_are_accepted(self):
        self.assertEqual(validate_conditions([{
            "field": "change_pct",
            "operator": "gte",
            "value": 2,
        }])[0]["value"], 2)
        self.assertEqual(validate_conditions([{
            "field": "gap_pct",
            "operator": "lte",
            "value": -1,
        }])[0]["field"], "gap_pct")
        with self.assertRaises(ValueError):
            validate_conditions([{"field": "__class__", "operator": "gte", "value": 1}])
        with self.assertRaises(ValueError):
            validate_conditions([{"field": "last", "operator": "exec", "value": 1}])

    def test_evaluation_distinguishes_approaching_and_confirmed_and_deduplicates(self):
        definition = ScannerDefinition.objects.create(
            name="Two percent move",
            conditions=[{"field": "change_pct", "operator": "gte", "value": 2}],
            approaching_threshold_pct=25,
        )
        self.quote(101.6)
        evaluate_scanner(definition)
        self.assertTrue(ScannerMatch.objects.filter(state="APPROACHING").exists())

        RadarQuote.objects.all().delete()
        self.quote(102.5)
        confirmed_run_at = timezone.now()
        evaluate_scanner(definition, now=confirmed_run_at)
        second_run_at = confirmed_run_at + timedelta(seconds=1)
        evaluate_scanner(definition, now=second_run_at)
        self.assertEqual(ScannerMatch.objects.filter(state="CONFIRMED").count(), 1)
        match = ScannerMatch.objects.get()
        self.assertEqual(match.last_alerted_at, confirmed_run_at)
        self.assertEqual(DhanOrderEvent.objects.count(), 0)

        cooldown_run_at = second_run_at + timedelta(minutes=31)
        RadarQuote.objects.update(
            source_at=cooldown_run_at,
            received_at=cooldown_run_at,
        )
        evaluate_scanner(definition, now=cooldown_run_at)
        match.refresh_from_db()
        self.assertEqual(match.last_alerted_at, cooldown_run_at)

    def test_builder_requires_staff_and_csrf_protected_post(self):
        url = reverse("scanner_builder")
        self.assertEqual(self.client.get(url).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url, {
            "name": "Blocked scanner",
            "field": "last",
            "operator": "gte",
            "value": "100",
        }).status_code, 403)

        self.client.force_login(self.staff)
        response = self.client.post(url, {
            "name": "Price scanner",
            "field": "last",
            "operator": "gte",
            "value": "100",
            "approaching_threshold_pct": "5",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ScannerDefinition.objects.filter(name="Price scanner").exists())

        response = self.client.get(url)
        self.assertContains(response, 'class="scanner-table-wrap"')
        self.assertContains(response, ".scanner-evidence")

    def test_fundamental_scanner_requires_current_provider_evidence(self):
        definition = ScannerDefinition.objects.create(
            name="Fundamental quality",
            conditions=[
                {"field": "investment_score", "operator": "gte", "value": 70},
                {"field": "investment_confidence", "operator": "gte", "value": 0.8},
            ],
        )
        self.quote(101)
        now = timezone.now()
        StockFundamentalSnapshot.objects.create(
            symbol="TEST",
            provider="APPROVED_TEST",
            source_at=now - timedelta(days=10),
        )
        StockScoreSnapshot.objects.create(
            symbol="TEST",
            score_type="INVESTMENT",
            version="ARC-STOCK-1",
            score=82,
            confidence=1,
            components={"return_on_equity": 82},
            missing_data=[],
            source_at=now - timedelta(days=10),
        )

        evaluate_scanner(definition, now=now)

        match = ScannerMatch.objects.get()
        self.assertEqual(match.state, "CONFIRMED")
        self.assertEqual(
            match.evidence["investment_score"]["provider"],
            "APPROVED_TEST",
        )
        self.assertEqual(DhanOrderEvent.objects.count(), 0)

        StockFundamentalSnapshot.objects.update(
            source_at=now - timedelta(days=121),
        )
        evaluate_scanner(definition, now=now + timedelta(seconds=1))
        match.refresh_from_db()
        self.assertFalse(match.is_active)

    def test_match_acknowledgement_is_post_only_and_staff_protected(self):
        definition = ScannerDefinition.objects.create(
            name="Acknowledgement scanner",
            conditions=[{"field": "last", "operator": "gte", "value": 100}],
        )
        self.quote(101)
        evaluate_scanner(definition)
        match = ScannerMatch.objects.get()
        url = reverse("scanner_match_acknowledge", args=[match.pk])

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        match.refresh_from_db()
        self.assertIsNotNone(match.acknowledged_at)

    @patch(
        "options_tracker.management.commands.run_scanners.deliver_scanner_alerts",
        return_value={"sent": 0, "failed": 0},
    )
    def test_scanner_command_runs_once_and_rejects_unsafe_intervals(self, _delivery):
        output = StringIO()

        call_command("run_scanners", stdout=output)

        self.assertIn('"scanners": 0', output.getvalue())
        with self.assertRaises(CommandError):
            call_command("run_scanners", interval_seconds=1, stdout=StringIO())

    def test_historical_scanner_reads_persisted_equity_bars_without_alerting(self):
        definition = ScannerDefinition.objects.create(
            name="Persisted close scanner",
            conditions=[{"field": "last", "operator": "gte", "value": 100}],
        )
        prior_session = timezone.now() - timedelta(days=1)
        current_session = timezone.now()
        StockEquityCandle.objects.create(
            symbol="TEST",
            interval_minutes=15,
            timestamp=prior_session,
            open=98,
            high=100,
            low=97,
            close=99,
            volume=1000,
        )
        StockEquityCandle.objects.create(
            symbol="TEST",
            interval_minutes=15,
            timestamp=current_session,
            open=100,
            high=103,
            low=99,
            close=102,
            volume=2000,
        )

        run = evaluate_scanner(definition, source="STOCK_HISTORY")

        self.assertEqual(run.scanned, 1)
        self.assertEqual(run.confirmed, 1)
        match = ScannerMatch.objects.get(definition=definition, symbol="TEST")
        self.assertEqual(match.evidence["last"]["provider"], "DHAN_HISTORY")
        self.assertGreater(match.next_delivery_at, timezone.now() + timedelta(days=3000))

    @patch("options_tracker.scanner_alerts.telegram_bot.send")
    def test_scanner_telegram_delivery_is_private_advisory_and_deduplicated(
        self,
        send,
    ):
        send.return_value = (True, "")
        RadarNotificationRoute.objects.create(
            user=self.staff,
            telegram_chat_id="private-chat",
        )
        definition = ScannerDefinition.objects.create(
            name="Private scanner",
            owner=self.staff,
            conditions=[{"field": "last", "operator": "gte", "value": 100}],
            cooldown_minutes=30,
        )
        self.quote(101)
        now = timezone.now()
        evaluate_scanner(definition, now=now)

        self.assertEqual(deliver_scanner_alerts(now=now), {"sent": 1, "failed": 0})
        self.assertEqual(deliver_scanner_alerts(now=now), {"sent": 0, "failed": 0})
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["chat_id"], "private-chat")
        self.assertIn("cannot place a broker order", send.call_args.args[0])
        self.assertEqual(DhanOrderEvent.objects.count(), 0)
