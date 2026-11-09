import datetime as dt
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import TrackedStock


class DownloadStockDataCommandTests(TestCase):
    def setUp(self):
        TrackedStock.objects.create(
            symbol="TEST",
            security_id="1",
            priority=1,
        )

    @patch("options_tracker.management.commands.download_stock_data.stock_data.download_equity")
    def test_exact_day_lookback_bounds_equity_pilot(self, download_equity):
        download_equity.return_value = (10, 0)

        call_command(
            "download_stock_data",
            "--feed=equity",
            "--days=10",
            "--limit=1",
            stdout=StringIO(),
        )

        stocks, start, end, interval = download_equity.call_args.args
        self.assertEqual([stock.symbol for stock in stocks], ["TEST"])
        self.assertEqual(end, dt.date.today())
        self.assertEqual(start, end - dt.timedelta(days=10))
        self.assertEqual(interval, 15)

    def test_negative_day_lookback_is_rejected(self):
        with self.assertRaisesRegex(CommandError, "--days cannot be negative"):
            call_command(
                "download_stock_data",
                "--feed=equity",
                "--days=-1",
                stdout=StringIO(),
            )
