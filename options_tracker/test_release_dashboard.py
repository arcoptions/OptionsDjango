from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import CanonicalInstrument, InstrumentKind, TrackedStock
from .release_status import release_dashboard_snapshot


class ReleaseDashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="reader", password="reader-password")
        self.staff = user_model.objects.create_user(
            username="operator",
            password="operator-password",
            is_staff=True,
        )

    def test_release_dashboard_requires_staff(self):
        url = reverse("release_dashboard")

        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_release_dashboard_shows_local_and_deployment_status_separately(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("release_dashboard"))

        self.assertContains(response, "Production baseline already deployed")
        self.assertContains(response, "Production deployed")
        self.assertContains(response, "Production release gates")
        self.assertContains(response, "Database activation evidence")
        self.assertContains(response, "Manual")
        self.assertContains(response, "Release Dashboard")

    def test_snapshot_counts_release_and_gate_states(self):
        snapshot = release_dashboard_snapshot()

        self.assertEqual(snapshot["release_counts"], {"complete": 2, "in_progress": 8, "blocked": 0})
        self.assertEqual(snapshot["gate_counts"], {"passed": 6, "remaining": 6, "blocked": 0})
        self.assertEqual(snapshot["automated_tests"], 580)

    def test_snapshot_reports_persisted_activation_evidence(self):
        CanonicalInstrument.objects.create(
            canonical_symbol="TEST",
            kind=InstrumentKind.EQUITY,
            exchange_segment="NSE_EQ",
            security_id="1",
        )
        TrackedStock.objects.create(symbol="TEST")

        evidence = {
            row["name"]: row for row in release_dashboard_snapshot()["activation_evidence"]
        }

        self.assertEqual(evidence["Canonical instruments"]["count"], 1)
        self.assertEqual(evidence["Canonical instruments"]["state"], "Activated")
        self.assertEqual(evidence["Active tracked stocks"]["count"], 1)
        self.assertEqual(evidence["Stock equity candles"]["state"], "Pending activation")
