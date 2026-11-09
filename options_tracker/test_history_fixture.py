import datetime as dt
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .models import DownloadJob, StockEquityCandle, StockOptionCandle, TrackedStock


class HistoryFixtureImportTests(TestCase):
    def setUp(self):
        TrackedStock.objects.create(symbol="RELIANCE", security_id="2885", priority=1)
        self.fixture = {
            "schema_version": 1,
            "provider": "DHAN",
            "captured_at": "2026-11-09T12:00:00+05:30",
            "window": {"from": "2026-11-01", "to": "2026-11-09"},
            "equity": [{
                "symbol": "RELIANCE",
                "interval_minutes": 15,
                "timestamp": "2026-11-09T10:00:00+05:30",
                "open": "1450.00",
                "high": "1460.00",
                "low": "1445.00",
                "close": "1458.00",
                "volume": 12500.0,
            }],
            "options": [{
                "symbol": "RELIANCE",
                "expiry_code": 1,
                "relative_strike": "ATM",
                "option_type": "CALL",
                "interval_minutes": 15,
                "timestamp": "2026-11-09T10:00:00+05:30",
                "strike": "1460.00",
                "spot": "1458.00",
                "open": "24.00",
                "high": "27.00",
                "low": "23.00",
                "close": "26.50",
                "volume": 5000,
                "oi": 120000,
                "implied_volatility": "18.25",
            }],
        }

    def _write_fixture(self, payload=None):
        directory = TemporaryDirectory()
        path = Path(directory.name) / "history.json"
        path.write_text(json.dumps(payload or self.fixture), encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_dry_run_validates_without_writing(self):
        stdout = StringIO()

        call_command("import_stock_history_fixture", self._write_fixture(), stdout=stdout)

        self.assertIn("DRY RUN", stdout.getvalue())
        self.assertEqual(StockEquityCandle.objects.count(), 0)
        self.assertEqual(StockOptionCandle.objects.count(), 0)
        self.assertEqual(DownloadJob.objects.count(), 0)

    def test_apply_is_idempotent_and_records_hash_evidence(self):
        path = self._write_fixture()

        call_command("import_stock_history_fixture", path, "--apply", stdout=StringIO())
        call_command("import_stock_history_fixture", path, "--apply", stdout=StringIO())

        self.assertEqual(StockEquityCandle.objects.count(), 1)
        self.assertEqual(StockOptionCandle.objects.count(), 1)
        jobs = list(DownloadJob.objects.order_by("kind"))
        self.assertEqual([job.kind for job in jobs], ["equity_import", "option_import"])
        self.assertTrue(all("sha256:" in job.detail for job in jobs))
        self.assertTrue(all(job.status == "DONE" for job in jobs))

    def test_fixture_rejects_credential_fields(self):
        self.fixture["accessToken"] = "must-not-import"

        with self.assertRaisesRegex(CommandError, "forbidden credential field"):
            call_command(
                "import_stock_history_fixture",
                self._write_fixture(),
                stdout=StringIO(),
            )

    def test_fixture_rejects_unknown_or_inactive_symbols(self):
        self.fixture["equity"][0]["symbol"] = "UNKNOWN"

        with self.assertRaisesRegex(CommandError, "not an active tracked stock"):
            call_command(
                "import_stock_history_fixture",
                self._write_fixture(),
                stdout=StringIO(),
            )

    def test_fixture_rejects_inconsistent_ohlc(self):
        self.fixture["equity"][0]["low"] = "1500.00"

        with self.assertRaisesRegex(CommandError, "inconsistent OHLC bounds"):
            call_command(
                "import_stock_history_fixture",
                self._write_fixture(),
                stdout=StringIO(),
            )

    def test_fixture_rejects_duplicate_candle_identity(self):
        self.fixture["equity"].append(dict(self.fixture["equity"][0]))

        with self.assertRaisesRegex(CommandError, "duplicates an equity candle identity"):
            call_command(
                "import_stock_history_fixture",
                self._write_fixture(),
                stdout=StringIO(),
            )

    @patch("options_tracker.stock_data.get_rolling_bars")
    @patch("options_tracker.stock_data.get_equity_bars")
    def test_capture_is_read_only_and_produces_importable_fixture(
        self,
        get_equity_bars,
        get_rolling_bars,
    ):
        timestamp = timezone.now() - dt.timedelta(minutes=15)
        get_equity_bars.return_value = ([{
            "symbol": "RELIANCE",
            "interval_minutes": 15,
            "timestamp": timestamp,
            "open": "1450",
            "high": "1460",
            "low": "1445",
            "close": "1458",
            "volume": 12500,
        }], "")
        get_rolling_bars.side_effect = [
            ([{
                "symbol": "RELIANCE",
                "expiry_code": 1,
                "relative_strike": "ATM",
                "option_type": option_type,
                "interval_minutes": 15,
                "timestamp": timestamp,
                "strike": "1460",
                "spot": "1458",
                "open": "24",
                "high": "27",
                "low": "23",
                "close": "26.5",
                "volume": 5000,
                "oi": 120000,
                "implied_volatility": 18.25,
            }], "")
            for option_type in ("CALL", "PUT")
        ]
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "captured.json"

        call_command(
            "capture_stock_history_fixture",
            path,
            "--days=10",
            "--limit=1",
            stdout=StringIO(),
        )
        call_command("import_stock_history_fixture", path, stdout=StringIO())

        self.assertTrue(path.exists())
        self.assertEqual(StockEquityCandle.objects.count(), 0)
        self.assertEqual(StockOptionCandle.objects.count(), 0)
        self.assertEqual(DownloadJob.objects.count(), 0)

    @patch("options_tracker.stock_data.get_equity_bars")
    def test_capture_does_not_write_partial_file_after_provider_failure(
        self,
        get_equity_bars,
    ):
        get_equity_bars.return_value = ([], "HTTP 503")
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "captured.json"

        with self.assertRaisesRegex(CommandError, "Equity capture failed"):
            call_command(
                "capture_stock_history_fixture",
                path,
                "--limit=1",
                stdout=StringIO(),
            )

        self.assertFalse(path.exists())

    @patch("options_tracker.stock_data.get_rolling_bars")
    @patch("options_tracker.stock_data.get_equity_bars")
    def test_capture_accepts_explicit_stocks_without_database_rows(
        self,
        get_equity_bars,
        get_rolling_bars,
    ):
        TrackedStock.objects.all().delete()
        timestamp = timezone.now() - dt.timedelta(minutes=15)
        get_equity_bars.return_value = ([{
            "symbol": "RELIANCE",
            "interval_minutes": 15,
            "timestamp": timestamp,
            "open": "1450",
            "high": "1460",
            "low": "1445",
            "close": "1458",
            "volume": 12500,
        }], "")
        get_rolling_bars.return_value = ([{
            "symbol": "RELIANCE",
            "expiry_code": 1,
            "relative_strike": "ATM",
            "option_type": "CALL",
            "interval_minutes": 15,
            "timestamp": timestamp,
            "strike": "1460",
            "spot": "1458",
            "open": "24",
            "high": "27",
            "low": "23",
            "close": "26.5",
            "volume": 5000,
            "oi": 120000,
            "implied_volatility": 18.25,
        }], "")
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "captured.json"

        call_command(
            "capture_stock_history_fixture",
            path,
            "--stock=RELIANCE:2885",
            stdout=StringIO(),
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["equity"][0]["symbol"], "RELIANCE")
        self.assertEqual(len(payload["options"]), 2)

    def test_capture_rejects_invalid_explicit_stock(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "captured.json"

        with self.assertRaisesRegex(CommandError, "SYMBOL:SECURITY_ID"):
            call_command(
                "capture_stock_history_fixture",
                path,
                "--stock=RELIANCE",
                stdout=StringIO(),
            )

    @patch("options_tracker.stock_data.get_rolling_bars")
    @patch("options_tracker.stock_data.get_equity_bars")
    def test_equity_only_capture_does_not_call_rolling_option_provider(
        self,
        get_equity_bars,
        get_rolling_bars,
    ):
        timestamp = timezone.now() - dt.timedelta(minutes=15)
        get_equity_bars.return_value = ([{
            "symbol": "RELIANCE",
            "interval_minutes": 15,
            "timestamp": timestamp,
            "open": "1450",
            "high": "1460",
            "low": "1445",
            "close": "1458",
            "volume": 12500,
        }], "")
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "captured.json"

        call_command(
            "capture_stock_history_fixture",
            path,
            "--limit=1",
            "--equity-only",
            stdout=StringIO(),
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["equity"]), 1)
        self.assertEqual(payload["options"], [])
        get_rolling_bars.assert_not_called()
