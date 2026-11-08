from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AppSetting, IndexOISnapshot
from .operations_health import operations_snapshot
from .radar_models import RadarWorker


class OperationsHealthTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="reader", password="test-password")
        self.staff = user_model.objects.create_user(
            username="operator",
            password="test-password",
            is_staff=True,
        )

    def test_operations_page_requires_staff(self):
        url = reverse("operations_health")

        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_snapshot_combines_execution_deployment_provider_and_worker_health(self):
        now = timezone.now()
        IndexOISnapshot.objects.create(underlying="NIFTY", created_at=now)
        RadarWorker.objects.create(
            name="paper-radar",
            owner="vm-worker",
            state="STREAMING",
            heartbeat_at=now,
            lease_until=now + timezone.timedelta(seconds=90),
        )

        snapshot = operations_snapshot(now)

        self.assertEqual(snapshot["execution"]["mode"], "manual")
        self.assertEqual(snapshot["providers"][1]["status"], "CURRENT")
        self.assertEqual(snapshot["workers"][0]["owner"], "vm-worker")
        self.assertIn("deployment", snapshot)

    def test_page_never_displays_dhan_token(self):
        AppSetting.objects.create(key="dhan_access_token", value="sensitive-token-value")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("operations_health"))

        self.assertNotContains(response, "sensitive-token-value")
