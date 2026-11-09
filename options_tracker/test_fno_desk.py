from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .fno_intelligence import iv_percentile_rows
from .models import IndexOISnapshot, IndexOptionCandle, StockScoreSnapshot


class FnoDeskTests(TestCase):
    def setUp(self):
        cache.clear()
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

    def test_desk_selects_only_latest_index_and_score_rows(self):
        now = timezone.now()
        old_index = IndexOISnapshot.objects.create(
            underlying="NIFTY",
            underlying_price=24000,
        )
        IndexOISnapshot.objects.filter(pk=old_index.pk).update(
            created_at=now - timedelta(hours=1),
        )
        latest_index = IndexOISnapshot.objects.create(
            underlying="NIFTY",
            underlying_price=25000,
        )
        StockScoreSnapshot.objects.create(
            symbol="RELIANCE",
            score_type="FNO",
            version="v1",
            score=10,
            source_at=now - timedelta(hours=1),
        )
        latest_score = StockScoreSnapshot.objects.create(
            symbol="RELIANCE",
            score_type="FNO",
            version="v2",
            score=90,
            source_at=now,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("fno_desk"))

        self.assertEqual(list(response.context["indexes"]), [latest_index])
        self.assertEqual(list(response.context["scores"]), [latest_score])

    def test_iv_percentile_reuses_result_until_source_timestamp_changes(self):
        now = timezone.now()
        for index in range(20):
            IndexOptionCandle.objects.create(
                underlying="NIFTY",
                relative_strike="ATM",
                option_type="CALL",
                timestamp=now - timedelta(minutes=index),
                implied_volatility=20 - index,
            )
            IndexOptionCandle.objects.create(
                underlying="NIFTY",
                relative_strike="ATM",
                option_type="PUT",
                timestamp=now - timedelta(hours=1, minutes=index),
                implied_volatility=99,
            )

        with self.assertNumQueries(2):
            first = iv_percentile_rows(["NIFTY"])
        with self.assertNumQueries(1):
            second = iv_percentile_rows(["NIFTY"])

        self.assertEqual(first, second)
        self.assertEqual(first[0]["observations"], 20)
