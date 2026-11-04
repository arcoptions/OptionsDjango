from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Direction, TipSignal
from .stock_views import _coverage


class OptionsNavigationTests(TestCase):
    def setUp(self):
        self.signal = TipSignal.objects.create(
            option_symbol="RELIANCE 1500 CE",
            direction=Direction.CE,
            stop_loss=10,
        )

    def test_sidebar_exposes_options_and_equities(self):
        response = self.client.get(reverse("options_tracker"))

        self.assertContains(response, 'href="/options/?tab=options"')
        self.assertContains(response, 'href="/options/?tab=equities"')

    def test_equities_sidebar_item_is_active_on_equities_tab(self):
        response = self.client.get(f"{reverse('options_tracker')}?tab=equities")

        self.assertContains(
            response,
            '<a class="nav-item active" href="/options/?tab=equities">Equities</a>',
            html=True,
        )

    def test_option_edit_get_uses_the_dashboard_theme(self):
        response = self.client.get(reverse("option_edit", args=[self.signal.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "options_tracker/option_edit.html")
        self.assertContains(response, "ARC")
        self.assertContains(response, "Edit tracked option")
        self.assertContains(response, self.signal.option_symbol)
        self.assertContains(
            response,
            '<a class="nav-item active" href="/options/?tab=options">Options</a>',
            html=True,
        )

    def test_tracker_paginates_large_tabs_and_preserves_filters(self):
        TipSignal.objects.bulk_create([
            TipSignal(
                option_symbol=f"TEST {index} CE",
                direction=Direction.CE,
                stop_loss=10,
                source_name="Bulk source",
            )
            for index in range(101)
        ])

        response = self.client.get(f"{reverse('options_tracker')}?tab=options&source=Bulk+source")

        self.assertEqual(len(response.context["items"]), 100)
        self.assertTrue(response.context["page_obj"].has_next())
        self.assertContains(response, "tab=options&amp;source=Bulk+source&amp;page=2")

    def test_option_edit_post_returns_to_the_matching_tracker_tab(self):
        response = self.client.post(reverse("option_edit", args=[self.signal.pk]), {
            "source_name": "Manual",
            "option_symbol": "RELIANCE 1510 CE",
            "entry_price": "12.50",
            "stop_loss": "9.50",
            "target_1": "15.00",
            "target_2": "",
            "target_3": "",
            "expiry_date": "",
            "outcome_status": "TRACKING",
        })

        self.assertRedirects(response, "/options/?tab=options", fetch_redirect_response=False)
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.option_symbol, "RELIANCE 1510 CE")


class StockCoverageCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_coverage_aggregate_is_reused_between_page_loads(self):
        expected = ({"RELIANCE": {"bars": 10}}, {"RELIANCE": {"bars": 20}}, {"equity": 10, "options": 20})
        with patch("options_tracker.stock_views._calculate_coverage", return_value=expected) as calculate:
            self.assertEqual(_coverage(), expected)
            self.assertEqual(_coverage(), expected)

        calculate.assert_called_once_with()
