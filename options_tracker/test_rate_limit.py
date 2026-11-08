import os
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class IntegrationRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_telegram_ingest_limits_repeated_requests_by_remote_address(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_INGEST_TOKEN": "scoped-test-token",
                "TELEGRAM_INGEST_RATE_LIMIT": "2",
            },
        ):
            responses = [
                self.client.post(
                    reverse("telegram_ingest_api"),
                    data="{}",
                    content_type="application/json",
                    HTTP_X_TELEGRAM_INGEST_TOKEN="wrong",
                    REMOTE_ADDR="192.0.2.10",
                )
                for _ in range(3)
            ]

        self.assertEqual(responses[0].status_code, 401)
        self.assertEqual(responses[1].status_code, 401)
        self.assertEqual(responses[2].status_code, 429)
        self.assertEqual(responses[2]["Retry-After"], "60")
