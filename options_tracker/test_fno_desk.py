from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import IndexOISnapshot, IndexOptionCandle


class FnoDeskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fno-researcher",
            password="test-password",
        )

    def test_desk_requires_login_and_renders_index_evidence(self):
        IndexOISnapshot.objects.create(
            underlying="NIFTY",
            underlying_price=25000,
            pcr=1.1,
            support_strike=24900,
            resistance_strike=25100,
            regime="BALANCED",
        )
        url = reverse("fno_desk")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)

        response = self.client.get(url)

        self.assertContains(response, "F&amp;O research desk", html=True)
        self.assertContains(response, "25000")
        self.assertContains(response, "manual execution boundary")

    def test_desk_calculates_bounded_iv_percentile_from_stored_evidence(self):
        IndexOISnapshot.objects.create(underlying="NIFTY")
        now = timezone.now()
        for index in range(20):
            IndexOptionCandle.objects.create(
                underlying="NIFTY",
                relative_strike="ATM",
                option_type="CALL",
                timestamp=now - timedelta(minutes=index),
                implied_volatility=20 - index,
            )
        self.client.force_login(self.user)

        response = self.client.get(reverse("fno_desk"))

        self.assertContains(response, "Stored implied-volatility context")
        self.assertContains(response, "100.0%")
        self.assertContains(response, "20")
