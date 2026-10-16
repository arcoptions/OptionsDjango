import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .models import AppSetting, Direction, TipSignal
from .services import refresh_dhan_option_prices


class DhanLtpThrottleTests(TestCase):
    def make_signal(self, **overrides):
        values = {
            "option_symbol": "NIFTY 24000 CE",
            "direction": Direction.CE,
            "entry_price": Decimal("50"),
            "stop_loss": Decimal("25"),
            "target_1": Decimal("75"),
            "security_id": "123",
            "exchange_segment": "NSE_FNO",
        }
        values.update(overrides)
        return TipSignal.objects.create(**values)

    @patch("options_tracker.services.requests.post")
    def test_429_starts_shared_cooldown(self, post):
        signal = self.make_signal()
        post.return_value.status_code = 429
        post.return_value.headers = {}

        with patch.dict(
            os.environ,
            {"DHAN_ACCESS_TOKEN": "token", "DHAN_CLIENT_ID": "client"},
        ):
            first = refresh_dhan_option_prices([signal], force=True)
            second = refresh_dhan_option_prices([signal], force=True)

        self.assertTrue(first["rate_limited"])
        self.assertEqual(first["retry_after_seconds"], 120)
        self.assertNotIn("https://", first["error"])
        self.assertTrue(second["rate_limited"])
        self.assertEqual(post.call_count, 1)

    @patch("options_tracker.services.requests.post")
    def test_concurrent_refresh_uses_existing_request(self, post):
        signal = self.make_signal()
        AppSetting.objects.create(
            key="dhan_ltp_request_state",
            value=json.dumps({
                "in_flight_until": (
                    timezone.now() + timedelta(seconds=20)
                ).isoformat(),
            }),
        )

        with patch.dict(
            os.environ,
            {"DHAN_ACCESS_TOKEN": "token", "DHAN_CLIENT_ID": "client"},
        ):
            result = refresh_dhan_option_prices([signal], force=True)

        self.assertTrue(result["refreshing"])
        post.assert_not_called()

    @patch("options_tracker.services.requests.post")
    def test_duplicate_security_ids_are_sent_once(self, post):
        first = self.make_signal()
        second = self.make_signal(option_symbol="NIFTY duplicate")
        post.return_value.status_code = 200
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {
            "data": {"NSE_FNO": {"123": {"last_price": 60}}},
        }

        with patch.dict(
            os.environ,
            {"DHAN_ACCESS_TOKEN": "token", "DHAN_CLIENT_ID": "client"},
        ):
            refresh_dhan_option_prices([first, second], force=True)

        self.assertEqual(post.call_args.kwargs["json"], {"NSE_FNO": [123]})
