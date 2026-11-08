from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DhanOrderEvent, ScannerDefinition, ScannerMatch
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
