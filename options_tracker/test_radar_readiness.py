"""Custom alert isolation, source imports and operating checks."""

from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from .models import RadarNotificationRoute, RadarPriceAlert, RadarSector
from .radar_price_alerts import process_price_alerts
from .radar_news import PublicNewsParser, collect_public_news
from .test_radar import NOW, RadarTestCase


class PriceAlertTests(RadarTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("alert-owner", is_staff=True)
        self.other = get_user_model().objects.create_user("alert-other", is_staff=True)
        RadarNotificationRoute.objects.create(user=self.user, telegram_chat_id="private-test-chat")
        self.client.force_login(self.user)

    def alert(self, **kwargs):
        return RadarPriceAlert.objects.create(
            user=self.user, instrument=self.equity, level=101, direction="ABOVE",
            expires_at=NOW+timedelta(hours=1), created_at=NOW, next_attempt_at=NOW,
            **kwargs,
        )

    def update_quote(self, now, price):
        self.underlying.last = price
        self.underlying.source_at = self.underlying.received_at = now
        self.underlying.save()

    def post(self, **data):
        with patch("options_tracker.radar_views.timezone.now", return_value=NOW):
            return self.client.post(reverse("radar_price_alert"), json.dumps(data), content_type="application/json")

    def test_create_and_cancel_are_personal_and_do_not_create_trades(self):
        response = self.post(action="create", instrument_id=self.equity.pk, direction="ABOVE", level=101, minutes=1440)
        self.assertEqual(response.status_code, 201)
        item = RadarPriceAlert.objects.get()
        self.assertEqual(item.user, self.user)
        self.assertEqual(item.previous_price, self.underlying.last)
        self.assertEqual(item.expires_at, NOW+timedelta(days=1))
        self.client.force_login(self.other)
        self.assertEqual(self.post(action="cancel", id=item.pk).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.post(action="cancel", id=item.pk).status_code, 200)
        item.refresh_from_db()
        self.assertIsNotNone(item.cancelled_at)

    def test_api_rejects_bad_inputs_and_unconnected_users(self):
        valid = dict(action="create", instrument_id=self.equity.pk, direction="ABOVE", level=101, minutes=60)
        for extra in [dict(level=True), dict(level=-1), dict(level=float("nan")), dict(direction="BUY"),
                      dict(minutes=0), dict(minutes=True), dict(instrument_id=True), dict(secret="extra")]:
            with self.subTest(extra=extra):
                self.assertEqual(self.post(**{**valid, **extra}).status_code, 400)
        self.client.force_login(self.other)
        self.assertEqual(self.post(**valid).status_code, 400)

    def test_permission_and_csrf_protected(self):
        self.client.logout()
        self.assertEqual(self.post(action="cancel", id=1).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("radar_price_alert"), "{}",
                                     content_type="application/json").status_code, 403)
        self.assertEqual(client.get(reverse("radar_price_alert")).status_code, 405)

    def test_bounded_polling_rotates_past_stale_old_alerts(self):
        first = self.alert()
        second = self.alert()
        self.underlying.source_at = NOW-timedelta(minutes=2)
        self.underlying.save()
        process_price_alerts(NOW, limit=1)
        process_price_alerts(NOW+timedelta(seconds=1), limit=1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.checked_at, NOW)
        self.assertEqual(second.checked_at, NOW+timedelta(seconds=1))

    @patch("options_tracker.radar_price_alerts.telegram_bot.send", return_value=(True, None))
    def test_already_triggered_delivery_can_retry_after_market_close(self, send):
        closed = NOW.replace(hour=16)
        item = self.alert(triggered_at=NOW, trigger_price=102)
        item.expires_at = closed+timedelta(hours=1)
        item.save()
        self.assertEqual(process_price_alerts(closed)["sent"], 1)
        send.assert_called_once()

    @patch("options_tracker.radar_price_alerts.telegram_bot.send", return_value=(True, None))
    def test_crosses_once_with_private_route_and_retries_do_not_duplicate_success(self, send):
        item = self.alert()
        self.update_quote(NOW, 100)
        self.assertEqual(process_price_alerts(NOW)["triggered"], 0)
        self.update_quote(NOW+timedelta(seconds=5), 102)
        result = process_price_alerts(NOW+timedelta(seconds=5))
        self.assertEqual(result, {"triggered": 1, "sent": 1, "failed": 0})
        process_price_alerts(NOW+timedelta(seconds=6))
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["chat_id"], "private-test-chat")
        item.refresh_from_db()
        self.assertEqual(item.trigger_price, 102)
        self.assertIn("ALERT ONLY", send.call_args.args[0])

    @patch("options_tracker.radar_price_alerts.telegram_bot.send", return_value=(True, None))
    def test_first_quote_stale_gap_and_future_never_infer_crossings(self, send):
        item = self.alert()
        self.update_quote(NOW, 102)
        process_price_alerts(NOW)
        self.update_quote(NOW+timedelta(seconds=60), 100)
        process_price_alerts(NOW+timedelta(seconds=60))
        self.update_quote(NOW+timedelta(seconds=120), 102)
        process_price_alerts(NOW+timedelta(seconds=120))
        process_price_alerts(NOW+timedelta(seconds=140))
        self.update_quote(NOW+timedelta(seconds=150), 105)
        process_price_alerts(NOW+timedelta(seconds=149))
        send.assert_not_called()
        item.refresh_from_db()
        self.assertIsNone(item.triggered_at)

    @patch("options_tracker.radar_price_alerts.telegram_bot.send", side_effect=[(False, "network: Timeout"), (True, None)])
    def test_delivery_failure_is_visible_and_retry_does_not_require_new_cross(self, send):
        item = self.alert(previous_price=100, previous_at=NOW-timedelta(seconds=1))
        self.update_quote(NOW, 102)
        self.assertEqual(process_price_alerts(NOW)["failed"], 1)
        item.refresh_from_db()
        self.assertIn("Timeout", item.last_error)
        self.assertEqual(process_price_alerts(NOW+timedelta(seconds=1))["sent"], 0)
        self.assertEqual(process_price_alerts(NOW+timedelta(seconds=11))["sent"], 1)
        self.assertEqual(send.call_count, 2)

    @patch("options_tracker.radar_price_alerts.telegram_bot.send", return_value=(True, None))
    def test_below_crossing_and_closed_market_expiry_cancellation(self, send):
        item = self.alert(previous_price=102, previous_at=NOW-timedelta(seconds=1))
        item.direction = "BELOW"
        item.save()
        self.update_quote(NOW, 100)
        self.assertEqual(process_price_alerts(NOW)["sent"], 1)
        for fields in [dict(cancelled_at=NOW), dict(expires_at=NOW), dict()]:
            other = self.alert()
            for key, value in fields.items():
                setattr(other, key, value)
            other.save()
        process_price_alerts(NOW.replace(hour=17))
        self.assertEqual(send.call_count, 1)


class ReadinessTests(RadarTestCase):
    def test_reference_identity_validation_does_not_query_each_contract(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .radar_feed import sync_instruments
        from .test_radar import master_rows
        master = master_rows()
        equity = next(row for row in master if row["INSTRUMENT"] == "EQUITY")
        option = next(row for row in master if row["INSTRUMENT"] == "OPTSTK")
        rows = [equity]+[{**option, "SECURITY_ID": str(5000+i), "STRIKE_PRICE": str(100+i)} for i in range(40)]
        sync_instruments(rows, NOW)
        with CaptureQueriesContext(connection) as queries:
            sync_instruments(rows, NOW)
        self.assertLessEqual(len(queries), 8)

    def test_effective_cash_and_option_sessions_have_distinct_closes(self):
        from .radar_sessions import continuous_timestamp, session_minutes
        self.assertEqual(session_minutes(NOW.date().replace(month=7, day=31)), 375)
        self.assertEqual(session_minutes(NOW.date().replace(month=8, day=3)), 360)
        self.assertEqual(session_minutes(NOW.date(), "OPTION"), 385)
        cash_close = NOW.replace(hour=15, minute=15)
        self.assertFalse(continuous_timestamp(cash_close))
        self.assertTrue(continuous_timestamp(cash_close, "OPTION"))
        self.assertTrue(continuous_timestamp(NOW.replace(hour=15, minute=39), "OPTION"))
        self.assertFalse(continuous_timestamp(NOW.replace(hour=15, minute=40), "OPTION"))

    def test_closed_cash_session_has_no_phantom_auction_gaps_or_auction_close(self):
        from .models import RadarBar
        from .radar_assistant import gap_diagnostics
        from .radar_workspace import StockData, session_start
        rows = []
        for day in [NOW.date()-timedelta(days=1), NOW.date()]:
            rows.extend(RadarBar(
                instrument=self.equity, timestamp=session_start(day)+timedelta(minutes=i),
                open=100, high=101, low=99, close=100, volume=100, complete=True, source="TEST",
            ) for i in range(360))
            rows.append(RadarBar(
                instrument=self.equity, timestamp=session_start(day)+timedelta(minutes=374),
                open=500, high=500, low=500, close=500, volume=10000, complete=True, source="TEST_AUCTION",
            ))
        RadarBar.objects.bulk_create(rows)
        closed = NOW.replace(hour=16)
        result = gap_diagnostics(closed)
        self.assertEqual(result["expected_minutes"], 360)
        self.assertEqual(result["affected"], [])
        metrics = StockData(self.equity, self.underlying, rows, closed).metrics()["metrics"]
        self.assertEqual(metrics["previous_close"]["value"], 100)
        self.assertEqual(metrics["volume"]["value"], 36000)
        self.assertEqual(metrics["vwap"]["value"], 100)

    def test_history_parser_retains_derivatives_but_not_cash_auction_minutes(self):
        from .radar_history import parse_history
        stamps = [NOW.replace(hour=15, minute=minute) for minute in [14, 15, 29, 39]]
        data = {"timestamp": [stamp.timestamp() for stamp in stamps],
                "open": [100]*4, "high": [101]*4, "low": [99]*4, "close": [100]*4, "volume": [100]*4}
        closed = NOW.replace(hour=16)
        self.assertEqual(len(parse_history(data, NOW.date(), NOW.date(), closed)), 1)
        self.assertEqual(len(parse_history(data, NOW.date(), NOW.date(), closed, "OPTION")), 4)

    def test_stock_chart_excludes_auction_but_option_chart_includes_extended_session(self):
        from .models import RadarBar
        from .radar_workspace import aggregate_chart
        row = RadarBar(instrument=self.equity, timestamp=NOW.replace(hour=15, minute=35),
                       open=100, high=101, low=99, close=100, volume=100, complete=True)
        self.assertEqual(aggregate_chart([row], 1), [])
        self.assertEqual(len(aggregate_chart([row], 1, "OPTION")), 1)

    def test_live_health_uses_clock_after_reading_worker_records(self):
        from .models import RadarWorker
        from .radar_assistant import worker_health
        timestamp = NOW+timedelta(seconds=3)
        for name in ["paper-radar", "radar-evaluator", "paper-alerts"]:
            RadarWorker.objects.create(name=name, owner="test", state="IDLE", heartbeat_at=timestamp,
                                       lease_until=timestamp+timedelta(seconds=90))
        with patch("options_tracker.radar_assistant.timezone.now", return_value=timestamp):
            self.assertTrue(worker_health()["delivery_enabled"])
            self.assertTrue(worker_health(NOW)["delivery_enabled"])
            self.assertFalse(worker_health(NOW-timedelta(seconds=31))["delivery_enabled"])

    def test_public_news_parser_keeps_nested_text_timestamp_and_source_identity(self):
        parser = PublicNewsParser()
        parser.feed('<div data-post="BeatTheStreetnews/12"><div class="tgme_widget_message_text js-message_text">TEST <b>results</b><br>Caption &amp; text</div><a><time datetime="2026-11-03T05:30:00+00:00">11:00</time></a></div>')
        self.assertEqual(len(parser.posts), 1)
        self.assertEqual("".join(parser.posts[0]["text"]), "TEST results\nCaption & text")
        self.assertEqual(parser.posts[0]["id"], 12)

    @patch("options_tracker.radar_news.requests.get")
    def test_public_news_ingestion_idempotent_attributed_and_not_a_trade(self, get):
        from .models import ChatMessage, RadarOpportunity
        get.return_value.text = '<div data-post="BeatTheStreetnews/12"><div class="tgme_widget_message_text">TEST results</div><time datetime="2026-11-03T05:30:00+00:00">11:00</time></div>'
        self.assertEqual(collect_public_news(NOW)["inserted"], 1)
        self.assertEqual(collect_public_news(NOW)["inserted"], 0)
        row = ChatMessage.objects.get()
        self.assertEqual(row.source_category, "NEWS")
        self.assertFalse(row.is_tip_candidate)
        self.assertEqual(json.loads(row.raw_payload)["mode"], "PUBLIC_TEXT_CAPTION_ONLY")
        self.assertFalse(RadarOpportunity.objects.exists())
        get.return_value.text = "<html>Access denied</html>"
        with self.assertRaises(ValueError):
            collect_public_news(NOW)

    @patch("options_tracker.radar_news.requests.get")
    def test_public_news_rejects_bad_time_and_ignores_future_or_old_posts(self, get):
        from .models import ChatMessage
        template = '<div data-post="BeatTheStreetnews/12"><div class="tgme_widget_message_text">TEST news</div><time datetime="{stamp}">time</time></div>'
        for stamp in ["2026-11-04T05:30:00+00:00", "2026-11-01T05:30:00+00:00"]:
            get.return_value.text = template.format(stamp=stamp)
            self.assertEqual(collect_public_news(NOW)["inserted"], 0)
        for stamp in ["bad timestamp", "2026-11-03T05:30:00"]:
            get.return_value.text = template.format(stamp=stamp)
            with self.assertRaises(ValueError):
                collect_public_news(NOW)
        self.assertFalse(ChatMessage.objects.exists())

    def test_sector_import_exact_symbols_and_bad_file_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"official.csv"
            path.write_text("Symbol,Industry\nTEST,Financial Services\nUNKNOWN,Utilities\n", encoding="utf-8")
            call_command("radar_sectors", input=path, stdout=StringIO())
            sector = RadarSector.objects.get()
            self.assertEqual(sector.industry, "Financial Services")
            self.assertIn("nseindia.com", sector.source_url)
            path.write_text("Symbol,Industry\nTEST,Changed\nTEST,Duplicate\n", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("radar_sectors", input=path, stdout=StringIO())
            sector.refresh_from_db()
            self.assertEqual(sector.industry, "Financial Services")

    def test_supervisor_plan_is_read_only_and_never_starts_real_orders(self):
        output = StringIO()
        with patch("options_tracker.management.commands.radar_supervise.subprocess.Popen") as popen:
            call_command("radar_supervise", check=True, stdout=output)
        popen.assert_not_called()
        self.assertIn("--collect-only", output.getvalue())
        self.assertIn("--accept-incomplete", output.getvalue())
        self.assertNotIn("execute", output.getvalue())

    def test_supervisor_restarts_exited_and_hung_owned_children_after_lease_backoff(self):
        target = "options_tracker.management.commands.radar_supervise"
        for hung in [False, True]:
            with self.subTest(hung=hung), tempfile.TemporaryDirectory() as directory:
                clock, launched = [0], []
                first = Mock(pid=101, returncode=None if hung else 1)
                second = Mock(pid=102, returncode=None)
                first.poll.side_effect = lambda: first.returncode
                second.poll.side_effect = lambda: second.returncode
                first.terminate.side_effect = lambda: setattr(first, "returncode", -15)
                second.terminate.side_effect = lambda: setattr(second, "returncode", -15)

                def spawn(*args, **kwargs):
                    launched.append(clock[0])
                    return first if len(launched) == 1 else second

                def sleep(seconds):
                    clock[0] += seconds
                    if clock[0] >= (320 if hung else 130):
                        raise KeyboardInterrupt

                with patch(target+".worker_specs", return_value={"delivery": ["radar_deliver", "--loop"]}), patch(
                    target+".timezone.now", return_value=NOW+timedelta(days=4),
                ), patch(target+".time.monotonic", side_effect=lambda: clock[0]), patch(
                    target+".time.sleep", side_effect=sleep,
                ), patch(target+".subprocess.Popen", side_effect=spawn):
                    call_command("radar_supervise", log_dir=Path(directory), stdout=StringIO(), stderr=StringIO())
                self.assertEqual(len(launched), 2)
                self.assertGreaterEqual(launched[1]-launched[0], 95)
                if hung:
                    first.terminate.assert_called_once()
                else:
                    first.terminate.assert_not_called()
                second.terminate.assert_called_once()

    @patch("options_tracker.management.commands.radar_monitor.upcoming_credential_expiry", return_value=None)
    def test_monitor_failure_throttle_and_recovery(self, _expiry):
        from .management.commands.radar_monitor import monitor_once
        target = "options_tracker.management.commands.radar_monitor"
        with patch(target+".problems", return_value=["collector offline"]), patch(
            target+".telegram_bot.send", return_value=(True, None),
        ) as send:
            self.assertFalse(monitor_once(NOW)["healthy"])
            self.assertFalse(monitor_once(NOW+timedelta(seconds=20))["notified"])
            self.assertFalse(monitor_once(NOW+timedelta(minutes=16))["notified"])
            self.assertEqual(send.call_count, 1)
        with patch(target+".problems", return_value=[]), patch(target+".telegram_bot.send", return_value=(True, None)) as send:
            self.assertTrue(monitor_once(NOW+timedelta(minutes=17))["notified"])
            self.assertIn("recovered", send.call_args.args[0])

    @patch("options_tracker.management.commands.radar_monitor.upcoming_credential_expiry", return_value=None)
    def test_monitor_does_not_page_for_public_news_advisory(self, _expiry):
        from .management.commands.radar_monitor import monitor_once
        target = "options_tracker.management.commands.radar_monitor"
        with patch(target+".problems", return_value=[]), patch(
            target+".advisories", return_value=["Public BeatTheStreet collector offline/stale"],
        ), patch(target+".telegram_bot.send") as send:
            result = monitor_once(NOW)
        self.assertTrue(result["healthy"])
        self.assertFalse(result["notified"])
        self.assertEqual(result["advisories"], ["Public BeatTheStreet collector offline/stale"])
        send.assert_not_called()

    @patch("options_tracker.management.commands.radar_monitor.upcoming_credential_expiry", return_value=None)
    def test_monitor_failed_delivery_is_not_reported_as_success(self, _expiry):
        from .management.commands.radar_monitor import monitor_once
        target = "options_tracker.management.commands.radar_monitor"
        with patch(target+".problems", return_value=["collector offline"]), patch(
            target+".telegram_bot.send", return_value=(False, "network: Timeout"),
        ), self.assertRaises(ValueError):
            monitor_once(NOW)

    def test_credential_expiry_reminder_is_private_and_sent_once_per_expiry(self):
        from .management.commands.radar_monitor import monitor_once
        target = "options_tracker.management.commands.radar_monitor"
        expiry = NOW+timedelta(hours=20)
        with patch(target+".problems", return_value=[]), patch(
            target+".upcoming_credential_expiry", return_value=expiry,
        ), patch(target+".telegram_bot.send", return_value=(True, None)) as send:
            self.assertTrue(monitor_once(NOW)["notified"])
            self.assertFalse(monitor_once(NOW+timedelta(minutes=1))["notified"])
            self.assertEqual(send.call_count, 1)
            self.assertIn("renewal reminder", send.call_args.args[0])
