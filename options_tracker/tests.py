import json
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.db import connection
from django.utils import timezone

from .index_oi_services import _buildup, _market_prices
from .jump_detector import historical_jump_report
from .models import AppSetting, ChatMessage, Direction, IndexOISnapshot, IndexOptionCandle, IndexOptionStrikeSnapshot, OptionOutcome, TipSignal, TradeStyle
from .services import _expiry_month_hint, get_dhan_credentials, refresh_dhan_option_prices, resolve_dhan_instruments, parse_tip_text
from .views import _ingest_single_telegram_message
from .management.commands.track_telegram import Command as TrackTelegramCommand


class TelegramTipParserTests(TestCase):
	def test_extracts_equity_option_underlying_and_multiple_targets(self):
		parsed = parse_tip_text("#Hyundai Aug 2000CE @45-50 SL-20 Target-150, 220, 300+")

		self.assertEqual(parsed["symbol"], "HYUNDAI 2000 CE")
		self.assertEqual(str(parsed["sl"]), "20")
		self.assertEqual([str(parsed[key]) for key in ("t1", "t2", "t3")], ["150", "220", "300"])

	def test_extracts_commodity_and_index_underlyings(self):
		commodity = parse_tip_text("NEW TRADE BUY NATURALGAS 320CE AT 12-14 STOPLOSS-8 TARGET-18-25-30++")
		index = parse_tip_text("Buy Sensex 77700 CE at 140...160 SL 70 Target 211...284....377")

		self.assertEqual(commodity["symbol"], "NATURALGAS 320 CE")
		self.assertEqual(index["symbol"], "SENSEX 77700 CE")

	def test_extracts_cash_equity_tip(self):
		parsed = parse_tip_text("NEW TRADE BUY TVSMOTORS AT 115 SL AT 75 TARGET-146-180")

		self.assertEqual(parsed["symbol"], "TVSMOTORS")
		self.assertEqual(parsed["direction"], "EQ")

	def test_rejects_option_without_identifiable_underlying(self):
		parsed = parse_tip_text("NEW TRADE BUY 500CE AUG AT 23 SELL 510CE JUL AT 6 SL AT 480 TARGET-525-545")

		self.assertEqual(parsed["symbol"], "")

	def test_ignores_holding_period_after_target(self):
		parsed = parse_tip_text("#Hyundai Aug 2000CE @45-50 SL-20 Target-150+ 3-4 weeks holding")

		self.assertEqual(parsed["t1"], 150)
		self.assertIsNone(parsed["t2"])

	def test_ignores_context_words_when_extracting_underlying(self):
		hedge = parse_tip_text("NIFTY Hedge Trade Yesterday 23900PE SL 50 Target 150-200")
		commodity = parse_tip_text("BUY CRUDEOIL MINI 9000CE AT 895 SL 780 TARGET 1100-1200")
		spot = parse_tip_text("SPOT LEVELS FOR SRF - BUY BETWEEN 2860-2830 SL 2750 TARGET 2950-3100-3300")

		self.assertEqual(hedge["symbol"], "NIFTY 23900 PE")
		self.assertEqual(commodity["symbol"], "CRUDEOIL 9000 CE")
		self.assertEqual(spot["symbol"], "SRF")

	def test_extracts_entry_from_standalone_cmp(self):
		nbcc = parse_tip_text("NBCC 95 ce cmp 2.95 range 3-2.95 add more 2.60 - 2 if comes. SL 1.45 clsb. Target 4 - 6+")
		crompton = parse_tip_text("CROMPTON 260 ce cmp 14.10 range 17-14 add more level 10 and 7 if comes. SL 5 clsb. Target 28 - 42")

		self.assertEqual(nbcc["entry"], Decimal("2.95"))
		self.assertEqual(crompton["entry"], Decimal("14.10"))

	def test_uses_range_as_entry_when_cmp_and_at_are_absent(self):
		parsed = parse_tip_text("NIFTY 24000 CE RANGE 50-45 SL 30 TARGET 70-90")

		self.assertEqual(parsed["entry"], Decimal("50"))

	def test_tracker_database_call_closes_stale_connections(self):
		connection.ensure_connection()
		result = TrackTelegramCommand._fresh_database_call(lambda: "saved")

		self.assertEqual(result, "saved")


class DhanOptionTrackingTests(TestCase):
	def make_signal(self, **overrides):
		values = {
			"option_symbol": "NIFTY 24000 CE",
			"direction": Direction.CE,
			"entry_price": Decimal("50"),
			"stop_loss": Decimal("25"),
			"target_1": Decimal("75"),
			"raw_text": "BUY NIFTY 24000 CE AUG SL 25 TARGET 75",
		}
		values.update(overrides)
		return TipSignal.objects.create(**values)

	def test_uses_first_expiry_month_mentioned(self):
		self.assertEqual(_expiry_month_hint("BUY 5200CE AUG SELL 5200CE JULY"), 8)

	def test_database_credentials_override_environment(self):
		AppSetting.objects.create(key="dhan_access_token", value="daily-token")
		with patch.dict(os.environ, {"DHAN_ACCESS_TOKEN": "azure-token", "DHAN_CLIENT_ID": "client"}):
			self.assertEqual(get_dhan_credentials(), ("daily-token", "client"))

	@patch("options_tracker.views.validate_dhan_credentials")
	def test_staff_can_validate_and_rotate_dhan_token(self, validate_credentials):
		user = get_user_model().objects.create_user("operator", password="test", is_staff=True)
		self.client.force_login(user)
		with patch.dict(os.environ, {"DHAN_CLIENT_ID": "client"}):
			response = self.client.post("/settings/dhan/", {"access_token": "fresh-token", "client_id": ""})

		self.assertRedirects(response, "/settings/dhan/")
		validate_credentials.assert_called_once_with("fresh-token", "client")
		self.assertEqual(AppSetting.objects.get(key="dhan_access_token").value, "fresh-token")

	def test_market_ticker_returns_latest_nifty_snapshot(self):
		IndexOISnapshot.objects.create(underlying="NIFTY", underlying_price=Decimal("24800"), underlying_change=Decimal("25"))

		response = self.client.get("/api/market-ticker/")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["price"], 24800.0)
		self.assertEqual(response.json()["change"], 25.0)

	@patch("options_tracker.services._load_dhan_contracts")
	def test_stale_expiry_month_rolls_to_nearest_active_nse_contract(self, load_contracts):
		signal = self.make_signal(
			raw_text="BUY NIFTY 24000 CE JUL SL 25 TARGET 75",
			security_id="bse-july",
			exchange_segment="BSE_FNO",
			live_price=Decimal("0"),
			outcome_status=OptionOutcome.STOP_LOSS,
		)
		load_contracts.return_value = {
			("NIFTY", Decimal("24000"), "CE"): [
				{
					"security_id": "bse-july",
					"exchange_segment": "BSE_FNO",
					"expiry": date(2026, 7, 30),
					"display_name": "NIFTY 30 JUL 24000 CALL",
				},
				{
					"security_id": "nse-august",
					"exchange_segment": "NSE_FNO",
					"expiry": date(2026, 8, 25),
					"display_name": "NIFTY 25 AUG 24000 CALL",
				},
			]
		}

		with patch("options_tracker.services.timezone.localdate", return_value=date(2026, 7, 29)):
			resolve_dhan_instruments([signal])

		signal.refresh_from_db()
		self.assertEqual(signal.security_id, "nse-august")
		self.assertEqual(signal.exchange_segment, "NSE_FNO")
		self.assertEqual(signal.expiry_date, date(2026, 8, 25))
		self.assertIsNone(signal.live_price)
		self.assertEqual(signal.outcome_status, OptionOutcome.TRACKING)

	@patch("options_tracker.services._load_dhan_contracts")
	def test_sensex_uses_nearest_active_bse_weekly_expiry(self, load_contracts):
		signal = self.make_signal(
			option_symbol="SENSEX 77700 CE",
			raw_text="BUY SENSEX 77700 CE AUG SL 25 TARGET 75",
		)
		load_contracts.return_value = {
			("SENSEX", Decimal("77700"), "CE"): [
				{
					"security_id": "bse-current-week",
					"exchange_segment": "BSE_FNO",
					"expiry": date(2026, 7, 30),
					"display_name": "SENSEX 30 JUL 77700 CALL",
				},
				{
					"security_id": "bse-next-week",
					"exchange_segment": "BSE_FNO",
					"expiry": date(2026, 8, 6),
					"display_name": "SENSEX 06 AUG 77700 CALL",
				},
			],
		}

		with patch("options_tracker.services.timezone.localdate", return_value=date(2026, 7, 29)):
			resolve_dhan_instruments([signal])

		signal.refresh_from_db()
		self.assertEqual(signal.security_id, "bse-current-week")
		self.assertEqual(signal.exchange_segment, "BSE_FNO")
		self.assertEqual(signal.expiry_date, date(2026, 7, 30))

	@patch("options_tracker.services._load_dhan_contracts")
	def test_missing_nse_contract_clears_stale_bse_mapping(self, load_contracts):
		signal = self.make_signal(
			security_id="bse-contract",
			exchange_segment="BSE_FNO",
			dhan_display_name="NIFTY BSE CONTRACT",
			expiry_date=date(2026, 7, 30),
			live_price=Decimal("10"),
		)
		load_contracts.return_value = {}

		resolve_dhan_instruments([signal])

		signal.refresh_from_db()
		self.assertEqual(signal.security_id, "")
		self.assertEqual(signal.exchange_segment, "")
		self.assertEqual(signal.dhan_display_name, "")
		self.assertIsNone(signal.expiry_date)
		self.assertIsNone(signal.live_price)
		self.assertEqual(signal.outcome_status, OptionOutcome.UNRESOLVED)

	@patch("options_tracker.services.requests.post")
	def test_refresh_ignores_zero_ltp_placeholder(self, post):
		signal = self.make_signal(security_id="123", exchange_segment="NSE_FNO")
		post.return_value.raise_for_status.return_value = None
		post.return_value.json.return_value = {"data": {"NSE_FNO": {"123": {"last_price": 0}}}}

		with patch.dict(os.environ, {"DHAN_ACCESS_TOKEN": "token", "DHAN_CLIENT_ID": "client"}):
			result = refresh_dhan_option_prices([signal], force=True)

		signal.refresh_from_db()
		self.assertEqual(result["updated"], 0)
		self.assertIsNone(signal.live_price)
		self.assertEqual(signal.outcome_status, OptionOutcome.TRACKING)

	@patch("options_tracker.services.requests.post")
	def test_refresh_records_first_observed_target_hit(self, post):
		signal = self.make_signal(security_id="123", exchange_segment="NSE_FNO")
		post.return_value.raise_for_status.return_value = None
		post.return_value.json.return_value = {"data": {"NSE_FNO": {"123": {"last_price": 80}}}}

		with patch.dict(os.environ, {"DHAN_ACCESS_TOKEN": "token", "DHAN_CLIENT_ID": "client"}):
			result = refresh_dhan_option_prices([signal], force=True)

		signal.refresh_from_db()
		self.assertEqual(result["updated"], 1)
		self.assertEqual(signal.live_price, Decimal("80"))
		self.assertEqual(signal.outcome_status, OptionOutcome.TARGET_1)

	@patch("options_tracker.services.resolve_dhan_instruments")
	def test_refresh_resolves_initially_unresolved_signals(self, resolve_instruments):
		signal = self.make_signal()

		result = refresh_dhan_option_prices([signal])

		resolve_instruments.assert_called_once_with([signal])
		self.assertEqual(result["error"], "Dhan credentials are not configured.")

	@patch("options_tracker.services.resolve_dhan_instruments")
	def test_refresh_repairs_recent_bse_mapping_before_throttling(self, resolve_instruments):
		signal = self.make_signal(
			security_id="bse-contract",
			exchange_segment="BSE_FNO",
			quote_updated_at=timezone.now(),
		)

		refresh_dhan_option_prices([signal])

		resolve_instruments.assert_called_once_with([signal])

	@patch("options_tracker.views.refresh_dhan_option_prices")
	def test_live_endpoint_returns_counts(self, refresh_prices):
		signal = self.make_signal(
			dhan_display_name="NIFTY 25 AUG 24000 CALL",
			expiry_date=date(2026, 8, 25),
		)
		signal.outcome_status = OptionOutcome.TARGET_1
		signal.save(update_fields=["outcome_status"])
		refresh_prices.return_value = {"updated": 1, "error": ""}

		response = self.client.post("/api/options/live/")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["counts"], {"tracked": 1, "target": 1, "stop_loss": 0})
		self.assertEqual(response.json()["rows"][0]["dhan_display_name"], "NIFTY 25 AUG 24000 CALL")
		self.assertEqual(response.json()["rows"][0]["expiry_date"], "2026-08-25")

	def test_tracker_filters_by_exact_source(self):
		self.make_signal(source_name="Source A")
		self.make_signal(source_name="Source B", option_symbol="NIFTY 24100 CE")

		response = self.client.get("/options/?source=Source+A")

		self.assertContains(response, "NIFTY 24000 CE")
		self.assertNotContains(response, "NIFTY 24100 CE")

	def test_edit_clears_stale_dhan_resolution(self):
		signal = self.make_signal(security_id="123", exchange_segment="NSE_FNO", live_price=Decimal("55"))

		response = self.client.post(
			f"/options/{signal.id}/edit/",
			{
				"source_name": "Edited",
				"option_symbol": "NIFTY 24100 CE",
				"entry_price": "60",
				"stop_loss": "30",
				"target_1": "90",
				"target_2": "",
				"target_3": "",
				"expiry_date": "",
				"outcome_status": OptionOutcome.TRACKING,
			},
		)

		self.assertEqual(response.status_code, 302)
		signal.refresh_from_db()
		self.assertEqual(signal.option_symbol, "NIFTY 24100 CE")
		self.assertEqual(signal.security_id, "")
		self.assertIsNone(signal.live_price)

	def test_delete_removes_tracked_option(self):
		signal = self.make_signal()

		response = self.client.post(f"/options/{signal.id}/delete/")

		self.assertEqual(response.status_code, 302)
		self.assertFalse(TipSignal.objects.filter(id=signal.id).exists())


class IndexOIIntelligenceTests(TestCase):
	def tearDown(self):
		cache.clear()
		super().tearDown()

	@patch("options_tracker.jump_detector._historical_events")
	def test_request_report_uses_persisted_data_without_scanning_candles(self, historical_events):
		report = {"patterns": [], "segments": [], "latest_candidates": [], "event_count": 12}
		AppSetting.objects.create(key="jump_report_nifty_45", value=json.dumps(report))

		result = historical_jump_report("NIFTY")

		self.assertEqual(result, report)
		historical_events.assert_not_called()

	def test_classifies_price_and_oi_movement(self):
		self.assertEqual(_buildup(Decimal("10"), 100), "LONG_BUILDUP")
		self.assertEqual(_buildup(Decimal("10"), -100), "SHORT_COVERING")
		self.assertEqual(_buildup(Decimal("-10"), 100), "SHORT_BUILDUP")
		self.assertEqual(_buildup(Decimal("-10"), -100), "LONG_UNWINDING")

	def test_market_prices_use_quote_ltp_and_previous_trading_close(self):
		price, previous_close = _market_prices(
			{"last_price": 77716.48, "ohlc": {"close": 77733.67}},
			Decimal("77000"),
			Decimal("76900"),
		)

		self.assertEqual(price, Decimal("77716.48"))
		self.assertEqual(previous_close, Decimal("77733.67"))

	def test_market_prices_ignore_zero_quote_placeholders(self):
		price, previous_close = _market_prices(
			{"last_price": 0, "ohlc": {"close": 0}},
			Decimal("77716.48"),
			Decimal("77733.67"),
		)

		self.assertEqual(price, Decimal("77716.48"))
		self.assertEqual(previous_close, Decimal("77733.67"))

	def test_dashboard_renders_live_snapshot(self):
		snapshot = IndexOISnapshot.objects.create(
			underlying="SENSEX", expiry_date=date(2026, 7, 30),
			underlying_price=Decimal("77928.15"), atm_strike=Decimal("77900"),
			call_oi=1000, put_oi=1500, pcr=1.5, max_pain=Decimal("77900"),
			support_strike=Decimal("77800"), resistance_strike=Decimal("78000"),
		)
		IndexOptionStrikeSnapshot.objects.create(
			snapshot=snapshot, strike=Decimal("77900"), option_type="CE",
			security_id="123", oi=1000, previous_oi=250, is_atm=True,
			depth={"buy": [{"price": 10, "quantity": 50}], "sell": [{"price": 11, "quantity": 40}]},
		)

		response = self.client.get("/index-oi/?underlying=SENSEX")

		self.assertContains(response, "SENSEX OI Analysis")
		self.assertContains(response, "OI vs price today")
		self.assertContains(response, "ATM market depth")
		self.assertContains(response, "Late-session jump detector")
		self.assertContains(response, 'id="oi-strike-data"')
		self.assertContains(response, "OI Change")
		self.assertEqual(response.context["strike_chart_data"][0]["call_oi"], 1000)
		self.assertEqual(response.context["strike_chart_data"][0]["call_change"], 750)
		self.assertEqual(response.context["latest_changes"]["call_oi_change"], 750)

	@patch("options_tracker.views.historical_jump_report")
	def test_dashboard_uses_bounded_aggregate_history(self, jump_report):
		jump_report.return_value = {"patterns": [], "segments": [], "latest_candidates": []}
		for index in range(245):
			IndexOISnapshot.objects.create(
				underlying="NIFTY", underlying_price=Decimal("24000"),
				call_oi=1000 + index, put_oi=1500 + index, pcr=1.5,
			)

		response = self.client.get("/index-oi/?underlying=NIFTY")

		self.assertEqual(len(response.context["history_data"]), 240)
		self.assertEqual(response.context["history_data"][-1]["call_oi_change"], 239)

	def test_jump_score_does_not_use_future_high(self):
		tuesday = date(2026, 7, 28)
		for minute, close, high, volume, oi, iv, spot in (
			(time(14, 25), 10, 10, 100, 1000, 10, 24000),
			(time(14, 30), 12, 12, 400, 1100, 12, 24030),
			(time(14, 31), 12, 36, 500, 1200, 13, 24040),
		):
			IndexOptionCandle.objects.create(
				underlying="NIFTY", relative_strike="ATM+1", option_type="CALL",
				timestamp=timezone.make_aware(datetime.combine(tuesday, minute)),
				strike=Decimal("24050"), spot=Decimal(spot), open=Decimal(close),
				high=Decimal(high), low=Decimal(close), close=Decimal(close),
				volume=volume, oi=oi, implied_volatility=iv,
			)

		first = historical_jump_report("NIFTY", use_cache=False)["latest_candidates"][0]
		future = IndexOptionCandle.objects.get(timestamp=timezone.make_aware(datetime.combine(tuesday, time(14, 31))))
		future.high = Decimal("120")
		future.save(update_fields=["high"])
		second = historical_jump_report("NIFTY", use_cache=False)["latest_candidates"][0]

		self.assertEqual(first["score"], second["score"])
		self.assertEqual(first["max_multiple"], 3)
		self.assertEqual(second["max_multiple"], 10)


class TelegramWebhookTests(TestCase):
	def test_ingests_native_channel_post(self):
		with patch.dict(os.environ, {"TELEGRAM_INGEST_TOKEN": ""}):
			response = self.client.post(
				"/api/telegram/ingest/",
				data=json.dumps(
					{
						"update_id": 123,
						"channel_post": {
							"chat": {"title": "Options Channel", "type": "channel"},
							"text": "NIFTY 25000 CE SL 100 TARGET 150",
						},
					}
				),
				content_type="application/json",
			)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(ChatMessage.objects.get().source_name, "Options Channel")

	def test_validates_telegram_secret_header(self):
		with patch.dict(os.environ, {"TELEGRAM_INGEST_TOKEN": "webhook-secret"}):
			response = self.client.post(
				"/api/telegram/ingest/",
				data=json.dumps({"message": {"chat": {"first_name": "Test"}, "text": "hello"}}),
				content_type="application/json",
				HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong-secret",
			)

		self.assertEqual(response.status_code, 401)

	def test_raw_logs_deduplicate_by_telegram_message_id(self):
		message = "NIFTY 25000 CE SL 100 TARGET 150"
		first = _ingest_single_telegram_message(
			"Tips",
			message,
			TradeStyle.INTRADAY,
			source_category="TIPS",
			telegram_chat_id=-100123,
			telegram_message_id=10,
			raw_payload='{"id": 10}',
		)
		second = _ingest_single_telegram_message(
			"Tips",
			message,
			TradeStyle.INTRADAY,
			source_category="TIPS",
			telegram_chat_id=-100123,
			telegram_message_id=11,
			raw_payload='{"id": 11}',
		)
		replay = _ingest_single_telegram_message(
			"Tips",
			message,
			TradeStyle.INTRADAY,
			source_category="TIPS",
			telegram_chat_id=-100123,
			telegram_message_id=10,
		)

		self.assertEqual(first["status"], "saved")
		self.assertEqual(second["status"], "saved")
		self.assertEqual(replay["status"], "duplicate")
		self.assertEqual(ChatMessage.objects.count(), 2)

	def test_discussion_message_is_logged_without_signal_parsing(self):
		result = _ingest_single_telegram_message(
			"Discussion",
			"NIFTY 25000 CE SL 100 TARGET 150",
			TradeStyle.INTRADAY,
			source_category="DISCUSSION",
			telegram_chat_id=-100456,
			telegram_message_id=20,
		)

		self.assertEqual(result["status"], "saved")
		self.assertFalse(ChatMessage.objects.get().is_tip_candidate)
		self.assertFalse(TipSignal.objects.exists())

	def test_media_only_message_is_retained_as_raw_log(self):
		result = _ingest_single_telegram_message(
			"News",
			"",
			TradeStyle.INTRADAY,
			source_category="NEWS",
			telegram_chat_id=-100789,
			telegram_message_id=30,
			raw_payload='{"media": {"type": "photo"}}',
		)

		self.assertEqual(result["status"], "saved")
		self.assertEqual(ChatMessage.objects.get().raw_text, "")

	def test_tracker_status_requires_diagnostics_token(self):
		with patch.dict(os.environ, {"TELEGRAM_DIAGNOSTICS_TOKEN": "status-secret"}):
			unauthorized = self.client.get("/api/telegram/status/")
			authorized = self.client.get(
				"/api/telegram/status/",
				HTTP_X_TELEGRAM_DIAGNOSTICS_TOKEN="status-secret",
			)

		self.assertEqual(unauthorized.status_code, 401)
		self.assertEqual(authorized.status_code, 200)
		self.assertEqual(authorized.json()["total"], 0)
