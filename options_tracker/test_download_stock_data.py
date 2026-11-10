import datetime as dt
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from . import stock_data
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

    @patch("options_tracker.stock_data.requests.get")
    def test_master_download_and_rows_are_streamed(self, get):
        response = Mock()
        response.iter_content.return_value = [
            b"EXCH_ID,SEGMENT,INSTRUMENT,UNDERLYING_SYMBOL,SECURITY_ID\n",
            b"NSE,E,EQUITY,TEST,1\n",
        ]
        get.return_value = response

        with TemporaryDirectory() as directory, patch.object(
            stock_data,
            "MASTER_CACHE",
            str(Path(directory) / "master.csv"),
        ):
            rows = stock_data.load_master(refresh=True)
            self.assertNotIsInstance(rows, list)
            self.assertEqual(list(rows)[0]["SECURITY_ID"], "1")

        get.assert_called_once_with(stock_data.MASTER_URL, timeout=300, stream=True)
        response.close.assert_called_once_with()

    @patch("options_tracker.stock_data.load_master")
    def test_master_sync_resolves_current_dhan_symbol_alias(self, load_master):
        load_master.return_value = [
            {
                "EXCH_ID": "NSE",
                "SEGMENT": "E",
                "INSTRUMENT": "EQUITY",
                "UNDERLYING_SYMBOL": "ICICILIFE",
                "SECURITY_ID": "18652",
            },
            {
                "EXCH_ID": "NSE",
                "INSTRUMENT": "OPTSTK",
                "UNDERLYING_SYMBOL": "ICICILIFE",
                "SECURITY_ID": "500",
                "STRIKE_PRICE": "500",
                "OPTION_TYPE": "CE",
                "SM_EXPIRY_DATE": "2026-11-26",
                "LOT_SIZE": "925",
                "DISPLAY_NAME": "ICICILIFE 500 CE",
            },
            {
                "EXCH_ID": "NSE",
                "INSTRUMENT": "OPTSTK",
                "UNDERLYING_SYMBOL": "ICICILIFE",
                "SECURITY_ID": "501",
                "STRIKE_PRICE": "510",
                "OPTION_TYPE": "CE",
                "SM_EXPIRY_DATE": "2026-11-26",
                "LOT_SIZE": "925",
                "DISPLAY_NAME": "ICICILIFE 510 CE",
            },
        ]
        stock = TrackedStock.objects.get(symbol="TEST")
        stock.symbol = "ICICIPRULI"
        stock.save(update_fields=["symbol"])

        updated, unmatched = stock_data.sync_master_metadata()
        stock.refresh_from_db()

        self.assertEqual(updated, 1)
        self.assertEqual(unmatched, [])
        self.assertEqual(stock.symbol, "ICICIPRULI")
        self.assertEqual(stock.security_id, "18652")
        self.assertEqual(stock.lot_size, 925)
        self.assertEqual(stock.strike_step, 10)
