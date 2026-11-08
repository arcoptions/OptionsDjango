from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import BrokerPnlEntry
from .performance_analytics import performance_context, performance_metrics
from .radar_models import RadarOpportunity


class PerformanceAnalyticsTests(TestCase):
    def test_metrics_calculate_win_rate_expectancy_profit_factor_and_drawdown(self):
        metrics = performance_metrics([100, -40, 20, -10], ordered=True)

        self.assertEqual(metrics["win_rate"], 50)
        self.assertEqual(metrics["expectancy"], Decimal("17.5"))
        self.assertEqual(metrics["profit_factor"], Decimal("2.4"))
        self.assertEqual(metrics["maximum_drawdown"], Decimal("40"))

    def test_context_keeps_broker_and_paper_results_separate(self):
        BrokerPnlEntry.objects.create(
            broker="DHAN",
            raw_symbol="TEST",
            underlying="TEST",
            instrument_kind="EQUITY",
            realised_pnl=Decimal("100"),
        )
        RadarOpportunity.objects.create(
            key="performance-paper",
            symbol="TEST",
            strategy="S1",
            version="v1",
            side="CE",
            state=RadarOpportunity.State.EXITED,
            setup_at=timezone.now(),
            valid_until=timezone.now(),
            trigger=100,
            stop=90,
            atr=5,
            reason="Test",
            closed_at=timezone.now(),
            net_pnl=-25,
        )

        context = performance_context()

        self.assertEqual(context["broker"]["net_pnl"], Decimal("100"))
        self.assertEqual(context["paper"]["net_pnl"], Decimal("-25"))
        self.assertEqual(context["broker_by_broker"][0]["label"], "DHAN")

    def test_dashboard_requires_staff_and_discloses_data_limitations(self):
        user = get_user_model().objects.create_user(
            username="performance-user",
            password="test-password",
            is_staff=True,
        )
        url = reverse("performance_dashboard")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(user)

        response = self.client.get(url)

        self.assertContains(response, "Unified performance")
        self.assertContains(response, "do not contain trade timestamps")
