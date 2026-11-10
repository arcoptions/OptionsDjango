"""History ingestion must not fabricate minutes, live quotes or completed jobs."""

from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
import requests

from .models import DownloadJob, RadarBar, RadarQuote, RadarWorker, StockEquityCandle
from .radar import Candle
from .radar_feed import acquire_worker
from .radar_feed import CandleCollector, Tick, decode_packets, fetch_quote_snapshot
from .radar_history import (
    HistoryClient, credentials, download_instrument, parse_history, reuse_equity_cache,
    store_bars, window_covered,
)
from .radar_workspace import session_start
from .test_radar import NOW, RadarTestCase


def response_data(stamps):
    return {
        "timestamp": [stamp.timestamp() for stamp in stamps],
        "open": [100]*len(stamps), "high": [101]*len(stamps),
        "low": [99]*len(stamps), "close": [100.5]*len(stamps),
        "volume": [100]*len(stamps),
    }


class StreamTimingTests(RadarTestCase):
    def run_stream(
        self,
        receive,
        scan,
        *,
        collect_only=False,
        decoded_ticks=None,
        fallback_ticks=None,
    ):
        from .management.commands.radar_run import Command
        module = "options_tracker.management.commands.radar_run"
        socket, collector, beat = Mock(), Mock(), Mock()
        fallback = Mock(return_value=fallback_ticks)
        socket.recv.side_effect = receive
        with patch.multiple(
            module, credentials=Mock(return_value=("test-token", "test-client")),
            subscriptions=Mock(return_value=[self.equity]), CandleCollector=Mock(return_value=collector),
            scan=scan, heartbeat=beat, decode_packets=Mock(return_value=decoded_ticks or []),
            fetch_quote_snapshot=fallback,
        ), patch(module+".timezone.now", side_effect=lambda: self.clock[0]), patch(
            module+".websocket.create_connection", return_value=socket,
        ) as connect, patch(module+".time.sleep"):
            Command().stream("test-owner", collect_only=collect_only)
        return connect, beat, fallback, collector

    def test_collection_only_never_scans(self):
        self.clock = [NOW]
        scanner = Mock()
        connect, beat, _, _ = self.run_stream([b"packet", KeyboardInterrupt], scanner, collect_only=True)
        scanner.assert_not_called()
        self.assertEqual(connect.call_count, 1)
        self.assertIn("COLLECT_ONLY", beat.call_args.args[2])

    def test_slow_scans_wait_after_completion_instead_of_starving_socket_reads(self):
        self.clock = [NOW]
        calls = 0

        def receive():
            nonlocal calls
            calls += 1
            if calls > 10:
                raise KeyboardInterrupt
            self.clock[0] += timedelta(seconds=1)
            return b"packet"

        def scan(_now):
            self.clock[0] += timedelta(seconds=6)
            return {}

        scanner = Mock(side_effect=scan)
        connect, beat, _, _ = self.run_stream(receive, scanner)
        self.assertEqual(scanner.call_count, 2)
        self.assertEqual(connect.call_count, 1)
        self.assertTrue(all(call.args[1] == "WAITING_FOR_DATA" for call in beat.call_args_list))

    def test_stalled_open_market_socket_reconnects_instead_of_claiming_streaming(self):
        self.clock = [NOW]
        calls = 0

        def receive():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise KeyboardInterrupt
            self.clock[0] += timedelta(seconds=41)
            return b"packet"

        connect, beat, _, _ = self.run_stream(receive, Mock(return_value={}), collect_only=True)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(beat.call_args_list[0].args[1], "RECONNECTING")
        self.assertIn("No market packets", beat.call_args_list[0].args[2])
        self.assertIn("COLLECT_ONLY", beat.call_args_list[0].args[2])

    def test_silent_socket_uses_rate_safe_rest_quote_fallback(self):
        self.clock = [NOW]
        calls = 0

        def receive():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise KeyboardInterrupt
            self.clock[0] += timedelta(seconds=31)
            return b"packet"

        tick = Tick(
            "NSE_EQ",
            self.equity.security_id,
            NOW + timedelta(seconds=31),
            NOW + timedelta(seconds=31),
            101,
            1000,
        )
        connect, beat, fallback, collector = self.run_stream(
            receive,
            Mock(return_value={}),
            collect_only=True,
            fallback_ticks=[tick],
        )

        self.assertEqual(connect.call_count, 1)
        fallback.assert_called_once()
        collector.ingest.assert_called_once()
        self.assertIn("REST_QUOTE_FALLBACK", beat.call_args.args[2])

    def test_stale_quotes_do_not_make_a_receiving_socket_look_stalled(self):
        self.clock = [NOW]
        calls = 0

        def receive():
            nonlocal calls
            calls += 1
            if calls > 2:
                raise KeyboardInterrupt
            self.clock[0] += timedelta(seconds=31)
            return b"packet"

        tick = Mock(source_at=NOW, received_at=NOW, segment="NSE_EQ", security_id=self.equity.security_id)
        connect, beat, _, _ = self.run_stream(
            receive,
            Mock(return_value={}),
            collect_only=True,
            decoded_ticks=[tick],
        )
        self.assertEqual(connect.call_count, 1)
        self.assertNotIn("RECONNECTING", [call.args[1] for call in beat.call_args_list])


class RadarHistoryTests(RadarTestCase):
    @patch("options_tracker.radar_feed.requests.post")
    def test_quote_fallback_parses_snapshot_and_obeys_shared_interval(self, post):
        post.return_value.status_code = 200
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {
            "status": "success",
            "data": {
                "NSE_EQ": {
                    self.equity.security_id: {
                        "last_price": 101.5,
                        "last_trade_time": NOW.strftime("%d/%m/%Y %H:%M:%S"),
                        "volume": 1234,
                        "depth": {
                            "buy": [{"price": 101.4, "quantity": 20}],
                            "sell": [{"price": 101.6, "quantity": 25}],
                        },
                        "ohlc": {
                            "open": 100,
                            "high": 102,
                            "low": 99,
                            "close": 100.5,
                        },
                    },
                },
            },
        }

        ticks = fetch_quote_snapshot(
            [self.equity],
            "test-token",
            "test-client",
            NOW,
        )
        second = fetch_quote_snapshot(
            [self.equity],
            "test-token",
            "test-client",
            NOW,
        )

        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0].last, 101.5)
        self.assertEqual(ticks[0].volume, 1234)
        self.assertEqual(ticks[0].bid, 101.4)
        self.assertEqual(ticks[0].ask, 101.6)
        self.assertIsNone(second)
        self.assertEqual(post.call_count, 1)

    def test_parser_excludes_forming_future_and_nonregular_candles(self):
        start = session_start(NOW.date())
        data = response_data([start-timedelta(minutes=1), start, NOW-timedelta(minutes=1), NOW, NOW+timedelta(minutes=1)])
        bars = parse_history(data, NOW.date(), NOW.date(), NOW)
        self.assertEqual([b.timestamp for b in bars], [start, NOW-timedelta(minutes=1)])
        self.assertEqual(bars[0].volume, 100)

    def test_parser_rejects_malformed_arrays_and_invalid_bars(self):
        data = response_data([session_start(NOW.date())])
        for key, value in [("volume", []), ("high", [98]), ("open", [float("nan")]),
                           ("volume", [-1]), ("volume", [1.5]), ("timestamp", ["bad"])]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                parse_history({**data, key: value}, NOW.date(), NOW.date(), NOW)
        with self.assertRaises(ValueError):
            parse_history({}, NOW.date(), NOW.date(), NOW)
        with self.assertRaises(ValueError):
            parse_history(response_data([session_start(NOW.date())]*2), NOW.date(), NOW.date(), NOW)

    def test_cache_reuses_only_true_minutes_never_coarse_or_rolling_options(self):
        stamp = session_start(NOW.date())
        for interval in [1, 5, 15, 1440]:
            StockEquityCandle.objects.create(symbol="TEST", interval_minutes=interval, timestamp=stamp,
                                            open=100, high=101, low=99, close=100.5, volume=100)
        result = reuse_equity_cache(self.equity, NOW.date(), NOW.date(), NOW)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(RadarBar.objects.get().source, "DHAN_CACHE_1M")
        self.assertEqual(reuse_equity_cache(self.option, NOW.date(), NOW.date(), NOW)["inserted"], 0)
        self.assertEqual(reuse_equity_cache(self.equity, NOW.date(), NOW.date(), NOW)["retained"], 1)

    def test_retains_immutable_live_bar_and_reports_conflict(self):
        stamp = session_start(NOW.date())
        original = RadarBar.objects.create(instrument=self.equity, timestamp=stamp, open=100, high=101, low=99,
                                           close=100, volume=10, complete=False, source="DHAN_STREAM")
        with self.assertLogs("options_tracker.radar_history", level="WARNING"):
            result = store_bars(self.equity, [Candle(stamp, 100, 101, 99, 100.5, 100)], "DHAN_HISTORY_1M")
        self.assertEqual(result, {"inserted": 0, "retained": 1, "conflicts": 1})
        original.refresh_from_db()
        self.assertFalse(original.complete)
        self.assertEqual(original.close, 100)

    @patch("options_tracker.radar_history.get_dhan_credentials", return_value=("", ""))
    def test_missing_auth_is_explicit(self, _credentials):
        with self.assertRaisesRegex(ValueError, "missing or expired"):
            credentials()

    @patch("options_tracker.radar_history._token_is_spent", return_value=True)
    @patch("options_tracker.radar_history.get_dhan_credentials", return_value=("example-test-token", "test-client"))
    def test_expired_auth_stops_before_requests(self, _credentials, _expired):
        client = HistoryClient()
        client.session = Mock()
        with self.assertRaisesRegex(ValueError, "missing or expired"):
            client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        client.session.post.assert_not_called()
        client.close()

    def history_client(self, responses):
        client = HistoryClient()
        client.session.close()
        client.session = Mock()
        client.session.post.side_effect = responses
        return client

    def reply(self, status=200, data=None, headers=None):
        reply = Mock(status_code=status, ok=200 <= status < 300, headers=headers or {})
        reply.json.return_value = data if data is not None else response_data([session_start(NOW.date())])
        return reply

    @patch("options_tracker.radar_history.credentials", return_value=("test-secret", "test-client"))
    @patch("options_tracker.radar_history.clock.sleep")
    @patch("options_tracker.radar_history.clock.monotonic", return_value=0)
    def test_rate_limit_retry_after_and_exact_payload(self, _monotonic, sleep, _credentials):
        client = self.history_client([self.reply(429, headers={"Retry-After": "7"}), self.reply()])
        bars = client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        self.assertEqual(len(bars), 1)
        self.assertEqual(client.requests, 2)
        self.assertEqual(sleep.call_args_list[-1].args[0], 7)
        payload = client.session.post.call_args.kwargs["json"]
        self.assertEqual(payload["interval"], "1")
        self.assertEqual(payload["instrument"], "EQUITY")
        self.assertTrue(payload["fromDate"].endswith("09:00:00"))
        self.assertTrue(payload["toDate"].endswith("15:40:00"))
        client.close()

    @patch("options_tracker.radar_history.credentials", return_value=("test-secret", "test-client"))
    @patch("options_tracker.radar_history.clock.sleep")
    @patch("options_tracker.radar_history.clock.monotonic", return_value=0)
    def test_sequential_requests_are_at_least_1_point_1_seconds_apart(self, _monotonic, sleep, _credentials):
        client = self.history_client([self.reply(), self.reply()])
        client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        self.assertEqual(sleep.call_args_list[-1].args[0], 1.1)
        client.close()

    @patch("options_tracker.radar_history.credentials", return_value=("test-secret", "test-client"))
    @patch("options_tracker.radar_history.clock.sleep")
    def test_auth_failure_and_bad_json_are_not_retried_or_logged(self, _sleep, _credentials):
        for reply, message in [(self.reply(401), "authentication"), (self.reply(403), "entitlement"), (self.reply(400), "HTTP 400")]:
            client = self.history_client([reply])
            with self.assertRaisesRegex(ValueError, message) as caught:
                client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertEqual(client.session.post.call_count, 1)
            client.close()
        reply = self.reply()
        reply.json.side_effect = ValueError("body containing test-secret")
        client = self.history_client([reply])
        with self.assertRaisesRegex(ValueError, "invalid JSON") as caught:
            client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        self.assertNotIn("test-secret", str(caught.exception))
        client.close()

    @patch("options_tracker.radar_history.credentials", return_value=("test-secret", "test-client"))
    @patch("options_tracker.radar_history.clock.sleep")
    def test_retry_exhaustion_stops_without_success_fallback(self, _sleep, _credentials):
        client = self.history_client([self.reply(429)]*4)
        with self.assertRaisesRegex(ValueError, "after four attempts"):
            client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        self.assertEqual(client.requests, 4)
        client.close()

    def test_download_is_resumable_and_never_updates_live_quotes(self):
        start = NOW.date()-timedelta(days=7)
        end = NOW.date()-timedelta(days=1)
        client = Mock(requests=1)
        client.fetch.return_value = [Candle(session_start(start), 100, 101, 99, 100, 10)]
        result = download_instrument(self.equity, start, end, NOW, client)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(DownloadJob.objects.get().status, "DONE")
        quote_count = RadarQuote.objects.count()
        result = download_instrument(self.equity, start, end, NOW, client)
        self.assertEqual(result["checkpoints"], 1)
        self.assertEqual(client.fetch.call_count, 1)
        self.assertEqual(RadarQuote.objects.count(), quote_count)

    def test_current_window_rechecks_missing_minutes_and_empty_is_failed(self):
        client = Mock(requests=1)
        client.fetch.return_value = [Candle(session_start(NOW.date()), 100, 101, 99, 100, 10)]
        download_instrument(self.equity, NOW.date(), NOW.date(), NOW, client)
        client.fetch.return_value = []
        with self.assertRaisesRegex(ValueError, "no complete"):
            download_instrument(self.equity, NOW.date(), NOW.date(), NOW, client)
        self.assertEqual(client.fetch.call_count, 2)
        self.assertEqual(DownloadJob.objects.get().status, "FAILED")

    def test_complete_cache_skips_network_but_gap_does_not(self):
        start = session_start(NOW.date())
        RadarBar.objects.bulk_create([RadarBar(instrument=self.equity, timestamp=start+timedelta(minutes=i),
                                              open=100, high=101, low=99, close=100, volume=10, complete=True)
                                      for i in range(110)])
        self.assertTrue(window_covered(self.equity, NOW.date(), NOW.date(), NOW))
        client = Mock(requests=0)
        download_instrument(self.equity, NOW.date(), NOW.date(), NOW, client)
        client.fetch.assert_not_called()
        RadarBar.objects.filter(instrument=self.equity, timestamp=start).delete()
        self.assertFalse(window_covered(self.equity, NOW.date(), NOW.date(), NOW))

    def test_expired_options_are_never_requested(self):
        self.option.expiry = NOW.date()-timedelta(days=1)
        client = Mock()
        with self.assertRaisesRegex(ValueError, "Expired"):
            download_instrument(self.option, NOW.date(), NOW.date(), NOW, client)
        client.fetch.assert_not_called()

    @patch("options_tracker.management.commands.radar_backfill.credentials")
    @patch("options_tracker.radar_history.HistoryClient.fetch")
    def test_cache_only_command_never_accesses_broker_or_credentials(self, fetch, auth):
        output = StringIO()
        call_command("radar_backfill", cache_only=True, symbols=["TEST"], days=14, stdout=output)
        self.assertIn("PAPER DATA ONLY", output.getvalue())
        fetch.assert_not_called()
        auth.assert_not_called()
        self.assertFalse(DownloadJob.objects.exists())

    def test_invalid_days_and_symbols_fail_before_fetch(self):
        for kwargs in [{"days": 0}, {"days": 41}, {"symbols": ["NOTREGISTERED"]}]:
            with self.assertRaises(CommandError):
                call_command("radar_backfill", cache_only=True, stdout=StringIO(), **kwargs)

    @patch("options_tracker.management.commands.radar_backfill.credentials", side_effect=ValueError("Dhan credentials are missing or expired."))
    def test_auth_failure_is_persisted_as_failed_health_not_completed(self, _auth):
        with self.assertRaisesRegex(CommandError, "missing or expired"):
            call_command("radar_backfill", symbols=["TEST"], stdout=StringIO())
        worker = RadarWorker.objects.get(name="radar-history")
        self.assertEqual(worker.state, "FAILED")
        self.assertIn("expired", worker.detail)
        self.assertFalse(DownloadJob.objects.exists())

    @patch("options_tracker.radar_history.credentials", return_value=("test-secret", "test-client"))
    @patch("options_tracker.radar_history.clock.sleep")
    def test_fixed_contract_payload_uses_security_identity_not_rolling_strike(self, _sleep, _auth):
        client = self.history_client([self.reply()])
        client.fetch(self.option, NOW.date(), NOW.date(), NOW)
        payload = client.session.post.call_args.kwargs["json"]
        self.assertEqual(payload["securityId"], self.option.security_id)
        self.assertEqual(payload["exchangeSegment"], "NSE_FNO")
        self.assertEqual(payload["instrument"], "OPTSTK")
        self.assertNotIn("relativeStrike", payload)
        client.close()

    @patch("options_tracker.radar_history.HistoryClient.fetch")
    def test_second_downloader_cannot_run_with_active_lease(self, fetch):
        acquire_worker(timezone.now(), name="radar-history")
        with self.assertRaisesRegex(CommandError, "lease"):
            call_command("radar_backfill", cache_only=True, symbols=["TEST"], stdout=StringIO())
        fetch.assert_not_called()

    @patch("options_tracker.radar_history.credentials", return_value=("test-secret", "test-client"))
    @patch("options_tracker.radar_history.clock.sleep")
    def test_network_exception_never_exposes_credentials(self, _sleep, _auth):
        client = self.history_client([requests.RequestException("test-secret")] * 4)
        with self.assertRaisesRegex(ValueError, "network request failed") as caught:
            client.fetch(self.equity, NOW.date(), NOW.date(), NOW)
        self.assertNotIn("test-secret", str(caught.exception))
        client.close()

    @patch("options_tracker.management.commands.radar_run.credentials", side_effect=ValueError("Dhan credentials are missing or expired."))
    @patch("options_tracker.management.commands.radar_run.websocket.create_connection")
    def test_live_feed_fails_before_connecting_with_expired_auth(self, connect, _auth):
        with self.assertRaisesRegex(CommandError, "missing or expired"):
            call_command("radar_run", stream=True, stdout=StringIO())
        connect.assert_not_called()

    def test_observed_live_ltt_is_ist_wall_clock_not_history_epoch(self):
        import struct
        from datetime import datetime, timezone as dt_timezone
        from zoneinfo import ZoneInfo
        at = datetime(2026, 11, 3, 12, 37, 4, tzinfo=ZoneInfo("Asia/Kolkata"))
        raw_epoch = int(at.replace(tzinfo=dt_timezone.utc).timestamp())
        packet = bytearray(162)
        struct.pack_into("<BHBI", packet, 0, 8, 162, 1, 123)
        struct.pack_into("<fHI", packet, 8, 100.0, 10, raw_epoch)
        tick = decode_packets(bytes(packet), at+timedelta(seconds=1))[0]
        self.assertEqual(tick.source_at, at)
        collector = CandleCollector()
        collector.ingest(self.equity, tick)
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.source_at, at)
        # REST history timestamps remain real Unix epochs, not feed wall epochs.
        candles = parse_history(response_data([at.replace(second=0)]), at.date(), at.date(), at+timedelta(minutes=1))
        self.assertEqual(candles[0].timestamp, at.replace(second=0))
