"""Authorization and HTTP-method regression tests for legacy mutation routes."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Direction, TipSignal


class LegacyMutationAccessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="reader",
            password="test-password",
        )
        self.staff = user_model.objects.create_user(
            username="operator",
            password="test-password",
            is_staff=True,
        )
        self.signal = TipSignal.objects.create(
            option_symbol="NIFTY 25000 CE",
            direction=Direction.CE,
            stop_loss=100,
        )

    def test_anonymous_and_non_staff_users_cannot_mutate_legacy_routes(self):
        requests = (
            (reverse("options_tracker"), {"source_name": "Manual"}),
            (reverse("option_live_prices"), {}),
            (reverse("option_edit", args=[self.signal.pk]), {}),
            (reverse("option_delete", args=[self.signal.pk]), {}),
            (reverse("scanners"), {"action": "sync_legacy"}),
            (reverse("telegram_feed"), {"action": "sync_legacy"}),
            (reverse("index_oi"), {"interval_seconds": "60"}),
            (reverse("trade_journal"), {"action": "close", "trade_id": "1"}),
            (reverse("archive"), {"action": "run"}),
            (reverse("nifty_live"), {"action": "arm"}),
        )

        for url, payload in requests:
            with self.subTest(role="anonymous", url=url):
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin/login/", response.url)

        self.client.force_login(self.user)
        for url, payload in requests:
            with self.subTest(role="non-staff", url=url):
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin/login/", response.url)

    def test_read_only_application_pages_require_an_authenticated_session(self):
        for name in (
            "home",
            "action_list",
            "options_tracker",
            "scanners",
            "telegram_feed",
            "index_oi",
            "trade_journal",
            "archive",
            "stock_options",
            "daily_shortlist",
            "swing_alerts",
            "research_report",
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin/login/", response.url)

        self.client.force_login(self.user)
        for name in (
            "action_list",
            "options_tracker",
            "scanners",
            "telegram_feed",
            "index_oi",
            "trade_journal",
            "archive",
            "stock_options",
            "daily_shortlist",
            "swing_alerts",
            "research_report",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_staff_mutations_reject_missing_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)

        response = csrf_client.post(reverse("archive"), {"action": "run"})

        self.assertEqual(response.status_code, 403)

    @patch("options_tracker.views.sync_chartink_from_legacy")
    def test_scanner_sync_requires_post_and_staff(self, sync):
        sync.return_value = (0, "OK")
        self.client.force_login(self.staff)

        get_response = self.client.get(f"{reverse('scanners')}?sync=1")
        post_response = self.client.post(reverse("scanners"), {"action": "sync_legacy"})

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 302)
        sync.assert_called_once_with(limit=300)

    @patch("options_tracker.views.archive_old_signals")
    def test_archive_run_requires_post_and_staff(self, archive_old):
        archive_old.return_value = 0
        self.client.force_login(self.staff)

        get_response = self.client.get(f"{reverse('archive')}?run=1")
        post_response = self.client.post(reverse("archive"), {"action": "run"})

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 302)
        archive_old.assert_called_once_with()

    def test_nifty_status_requires_staff(self):
        anonymous_page = self.client.get(reverse("nifty_live"))
        anonymous = self.client.get(reverse("nifty_live_status_api"))
        self.client.force_login(self.user)
        non_staff_page = self.client.get(reverse("nifty_live"))
        non_staff = self.client.get(reverse("nifty_live_status_api"))
        self.client.force_login(self.staff)
        staff_page = self.client.get(reverse("nifty_live"))
        staff = self.client.get(reverse("nifty_live_status_api"))

        self.assertEqual(anonymous_page.status_code, 302)
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(non_staff_page.status_code, 302)
        self.assertEqual(non_staff.status_code, 302)
        self.assertEqual(staff_page.status_code, 200)
        self.assertEqual(staff.status_code, 200)
