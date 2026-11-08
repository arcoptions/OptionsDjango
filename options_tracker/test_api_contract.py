from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .api_contract import API_VERSION
from .models import IndexOISnapshot


class PollingApiContractTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="api-contract-operator",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    @patch(
        "options_tracker.views.fetch_dhan_nifty_ltp",
        side_effect=RuntimeError("offline"),
    )
    def test_market_ticker_exposes_contract_version(self, _fetch):
        IndexOISnapshot.objects.create(
            underlying="NIFTY",
            underlying_price=25000,
            underlying_change=100,
        )

        response = self.client.get(reverse("market_ticker_api"))

        self.assertEqual(response.json()["api_version"], API_VERSION)
        self.assertEqual(response["X-ARC-API-Version"], API_VERSION)

    def test_cas_status_exposes_contract_version(self):
        response = self.client.get(reverse("cas_status_api"))

        self.assertEqual(response.json()["api_version"], API_VERSION)
        self.assertEqual(response["X-ARC-API-Version"], API_VERSION)

    @patch("options_tracker.views.trade_rows", return_value=[])
    @patch(
        "options_tracker.views.today_summary",
        return_value={"net_pnl": 0, "observed": 0},
    )
    @patch("options_tracker.views.engine_snapshot")
    def test_nifty_status_exposes_contract_version(
        self,
        engine_snapshot,
        _summary,
        _rows,
    ):
        engine_snapshot.return_value = {
            "state": "IDLE",
            "dry_run": True,
            "execution_mode": "manual",
            "automatic_entries": False,
            "position_management_live": True,
            "configuration_error": "",
            "stale": False,
            "age_seconds": 0,
            "at": None,
            "notes": [],
            "rejections": [],
            "session": {},
            "position": None,
            "trades_today": 0,
            "realized_r": 0,
        }

        response = self.client.get(reverse("nifty_live_status_api"))

        self.assertEqual(response.json()["api_version"], API_VERSION)
        self.assertEqual(response["X-ARC-API-Version"], API_VERSION)
