"""Paper radar safety, data, strategy, delivery and web integration tests."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
import csv
from pathlib import Path
import struct
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import (
    ChatMessage, RadarAlert, RadarBar, RadarInstrument, RadarOpportunity, RadarQuote,
)
from .radar import (
    Candle, Policy, S, Setup, acknowledge, atr, detect_setups, evaluate_entry,
    five_minute_bars, manage_positions, news_context, scan, sizing,
)
from .radar_alerts import deliver_alerts
from .radar_feed import (
    CandleCollector, Tick, acquire_worker, decode_packets, heartbeat,
    subscriptions, sync_instruments,
)


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 3, 11, 5, tzinfo=IST)


def master_rows():
    return [
        {"EXCH_ID": "NSE", "SEGMENT": "E", "INSTRUMENT": "EQUITY",
         "UNDERLYING_SYMBOL": "TEST", "SECURITY_ID": "123", "DISPLAY_NAME": "Test Ltd"},
        *[{"EXCH_ID": "NSE", "SEGMENT": "D", "INSTRUMENT": "OPTSTK",
           "UNDERLYING_SYMBOL": "TEST", "SECURITY_ID": str(900+i), "DISPLAY_NAME": "Test option",
           "SM_EXPIRY_DATE": "2026-11-26", "STRIKE_PRICE": "100", "LOT_SIZE": "100", "OPTION_TYPE": side}
          for i, side in enumerate(["CE", "PE"])],
    ]


def bars():
    start = NOW.replace(hour=9, minute=15)
    return [Candle(start+timedelta(minutes=5*i), 100, 101, 99, 100, 1000) for i in range(22)]


class RadarTestCase(TestCase):
    def setUp(self):
        sync_instruments(master_rows(), NOW)
        self.equity = RadarInstrument.objects.get(kind="EQUITY")
        self.option = RadarInstrument.objects.get(kind="OPTION", side="CE")
        self.underlying = RadarQuote.objects.create(
            instrument=self.equity, source_at=NOW, received_at=NOW, last=100.1, volume=10_000,
        )
        self.quote = RadarQuote.objects.create(
            instrument=self.option, source_at=NOW, received_at=NOW, last=10,
            bid=9.95, ask=10.05, bid_qty=500, ask_qty=500, volume=10_000,
            previous_last=9.9, previous_volume=9000, previous_at=NOW-timedelta(seconds=30),
        )

    def opportunity(self, **overrides):
        values = {
            "key": "TEST:S1:unique", "symbol": "TEST", "strategy": "S1",
            "version": "experimental-v1", "side": "CE", "state": S.TRIGGERED,
            "setup_at": NOW-timedelta(minutes=5), "trigger_at": NOW,
            "valid_until": NOW+timedelta(minutes=2), "trigger": 100, "stop": 98,
            "atr": 2, "reason": "test", "policy": asdict(Policy()),
            "created_at": NOW,
        }
        values.update(overrides)
        return RadarOpportunity.objects.create(**values)

    def eligible(self, **overrides):
        item = self.opportunity(**overrides)
        evaluate_entry(item, NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.ELIGIBLE)
        return item


class StrategyTests(TestCase):
    def test_prior_session_warmup_allows_opening_reclaim(self):
        data = bars()[:3]
        data[1] = replace(data[1], high=101.5, low=99.8, close=100.5)
        data[2] = replace(data[2], open=100.5, high=101.8, low=100, close=101.6)
        previous = [
            Candle(NOW.replace(day=2, hour=9, minute=15)+timedelta(minutes=5*i), 101, 102, 100, 101, 1000)
            for i in range(72)
        ]
        opening = NOW.replace(hour=9, minute=30)
        self.assertTrue(any(s.strategy == "S1" and s.triggered for s in detect_setups(data, previous, opening)))

    def test_s2_compression_breakout_and_no_future_access(self):
        data = bars()
        data[-1] = replace(data[-1], high=101.2, close=101.15, volume=1600)
        found = [s for s in detect_setups(data, [], NOW) if s.strategy == "S2" and s.side == "CE"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].triggered)
        self.assertFalse(any(s.triggered for s in detect_setups(data, [], NOW-timedelta(minutes=1))))

    def test_bear_breakout_is_mirrored(self):
        data = bars()
        data[-1] = replace(data[-1], low=98.8, close=98.85, volume=1600)
        found = [s for s in detect_setups(data, [], NOW) if s.strategy == "S2" and s.side == "PE"]
        self.assertTrue(found[0].triggered)
        self.assertGreater(found[0].stop, found[0].trigger)

    def test_breakout_without_volume_is_not_trigger(self):
        data = bars()
        data[-1] = replace(data[-1], high=101.2, close=101.15)
        self.assertFalse(any(s.triggered for s in detect_setups(data, [], NOW)))

    def test_gap_cannot_be_ignored(self):
        data = bars()
        del data[10]
        self.assertEqual(detect_setups(data, [], NOW), [])

    def test_stale_completed_bar_does_not_create_late_signal(self):
        self.assertEqual(detect_setups(bars(), [], NOW+timedelta(seconds=31)), [])

    def test_atr_true_range_and_warmup(self):
        self.assertEqual(atr(bars()), 2)
        self.assertEqual(atr(bars()[:14]), 0)

    def test_s1_needs_whole_previous_session_and_following_break(self):
        data = bars()
        data[-2] = replace(data[-2], high=101.5, low=99.8, close=100.5)
        data[-1] = replace(data[-1], open=100.5, high=101.8, low=100, close=101.6)
        previous = [
            Candle(NOW.replace(day=2, hour=9, minute=15)+timedelta(minutes=5*i), 101, 102, 100, 101, 1000)
            for i in range(72)
        ]
        found = [s for s in detect_setups(data, previous, NOW) if s.strategy == "S1"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].triggered)
        self.assertEqual([s for s in detect_setups(data, previous[:-1], NOW) if s.strategy == "S1"], [])

    def test_s1_ambiguous_trigger_and_invalidation_rejects(self):
        data = bars()
        data[-2] = replace(data[-2], high=101.5, low=99.8, close=100.5)
        data[-1] = replace(data[-1], open=100.5, high=101.8, low=99.7, close=101.6)
        previous = [Candle(NOW.replace(day=2, hour=9, minute=15)+timedelta(minutes=5*i), 101, 102, 100, 101, 1000) for i in range(72)]
        self.assertFalse(any(s.strategy == "S1" and s.triggered for s in detect_setups(data, previous, NOW)))

    def test_s3_pullback(self):
        data = bars()
        data = [replace(b, open=97+i*.2, high=98+i*.2, low=96+i*.2, close=97+i*.2) for i, b in enumerate(data)]
        mean = sum(b.close for b in data[-21:-1])/20
        data[-2] = replace(data[-2], open=mean+.2, low=mean, high=mean+.5, close=mean+.3)
        data[-1] = replace(data[-1], open=mean+.4, low=mean+.1, high=mean+1, close=mean+.8, volume=2000)
        found = [s for s in detect_setups(data, [], NOW) if s.strategy == "S3" and s.side == "CE"]
        self.assertTrue(found[0].triggered)

    def test_aggregation_requires_all_five_minutes(self):
        source = [Candle(NOW+timedelta(minutes=i), 100, 101, 99, 100, 10) for i in range(5)]
        self.assertEqual(len(five_minute_bars(source)), 1)
        self.assertEqual(five_minute_bars(source[:-1]), [])
        self.assertEqual(five_minute_bars(source+[source[0]]), [])


class RegistryTests(RadarTestCase):
    def test_entire_option_universe_not_existing_tracked_stock_subset(self):
        self.assertEqual(RadarInstrument.objects.filter(kind="EQUITY", active=True).count(), 1)
        self.assertEqual(RadarInstrument.objects.filter(kind="OPTION", active=True).count(), 2)

    def test_expired_contracts_archived(self):
        extra = master_rows()[1].copy()
        extra.update(SECURITY_ID="777", SM_EXPIRY_DATE="2026-11-02")
        sync_instruments(master_rows()+[extra], NOW)
        self.assertFalse(RadarInstrument.objects.filter(security_id="777", active=True).exists())

    def test_empty_master_cannot_deactivate_universe(self):
        with self.assertRaises(ValueError):
            sync_instruments([], NOW)
        self.equity.refresh_from_db()
        self.assertTrue(self.equity.active)

    def test_reused_security_id_cannot_mutate_contract_history(self):
        rows = master_rows()
        rows[1]["STRIKE_PRICE"] = "110"
        with self.assertRaisesMessage(ValueError, "identity changed"):
            sync_instruments(rows, NOW)
        self.option.refresh_from_db()
        self.assertEqual(self.option.strike, 100)

    def test_coordinated_unused_contract_strike_adjustment_is_applied(self):
        rows = master_rows()
        for row in rows[1:]:
            row["STRIKE_PRICE"] = "89.75"
        sync_instruments(rows, NOW)
        self.assertEqual(
            set(RadarInstrument.objects.filter(kind="OPTION").values_list("strike", flat=True)),
            {89.75},
        )

    def test_used_contract_blocks_coordinated_strike_adjustment(self):
        self.opportunity(contract=self.option)
        rows = master_rows()
        for row in rows[1:]:
            row["STRIKE_PRICE"] = "89.75"
        with self.assertRaisesMessage(ValueError, "identity changed"):
            sync_instruments(rows, NOW)
        self.option.refresh_from_db()
        self.assertEqual(self.option.strike, 100)

    def test_stale_master_missing_equity_rolls_back(self):
        with self.assertRaises(ValueError):
            sync_instruments(master_rows()[1:], NOW)
        self.assertTrue(RadarInstrument.objects.filter(pk=self.equity.pk, active=True).exists())

    def test_option_subscriptions_include_watches_and_pinned_inactive_positions(self):
        self.assertEqual(len(subscriptions(NOW)), 1)
        item = self.opportunity()
        self.assertEqual(len(subscriptions(NOW)), 3)
        self.option.active = False
        self.option.save()
        item.contract, item.state = self.option, S.TAKEN
        item.save()
        self.assertIn(self.option.pk, [i.pk for i in subscriptions(NOW)])

    def test_expired_held_contract_is_never_subscribed_live(self):
        item = self.opportunity(state=S.TAKEN, contract=self.option)
        self.option.expiry = NOW.date()-timedelta(days=1)
        self.option.save()
        self.assertNotIn(item.contract_id, [i.pk for i in subscriptions(NOW)])


class LifecycleTests(RadarTestCase):
    def test_expired_position_requires_offline_resolution_not_a_reused_id_quote(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        self.option.expiry = NOW.date()-timedelta(days=1)
        self.option.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXIT_PENDING)
        self.assertIsNone(item.exit_price)
        self.assertIn("offline", item.exit_reason)

    def test_registry_staleness_blocks_new_entry(self):
        item = self.opportunity()
        self.equity.refreshed_at = NOW-timedelta(hours=25)
        self.equity.save()
        evaluate_entry(item, NOW)
        self.assertEqual(item.state, S.REJECTED)
        self.assertIn("registry is stale", item.reason)

    def test_near_expiry_is_not_selected(self):
        self.option.expiry = NOW.date()+timedelta(days=2)
        self.option.save()
        item = self.opportunity()
        evaluate_entry(item, NOW)
        self.assertEqual(item.state, S.REJECTED)

    def test_daily_loss_gate_blocks_fresh_entry(self):
        self.opportunity(key="closed", state=S.EXITED, closed_at=NOW, net_pnl=-2000)
        item = self.opportunity()
        quantity, reason = sizing(item, self.quote, Policy(), NOW)
        self.assertEqual(quantity, 0)
        self.assertIn("daily-loss", reason)

    def test_lifetime_losses_constrain_cash_even_after_daily_reset(self):
        self.opportunity(key="closed", state=S.EXITED, closed_at=NOW-timedelta(days=1), net_pnl=-100_000)
        quantity, _ = sizing(self.opportunity(), self.quote, Policy(), NOW)
        self.assertEqual(quantity, 0)

    def test_trailing_stop_uses_executable_bid(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        self.quote.bid, self.quote.ask = 11.6, 11.7
        self.quote.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.TAKEN)
        self.quote.bid, self.quote.ask = 10.4, 10.5
        self.quote.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXITED)
        self.assertEqual(item.exit_price, 10.4)
        self.assertIn("trailing", item.exit_reason)
        self.assertIn("exit_quote", item.evidence)

    def test_time_exit_without_quote_is_pending_not_a_marked_fill(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        manage_positions(NOW.replace(hour=15, minute=10))
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXIT_PENDING)
        self.assertIsNone(item.exit_price)

    def test_decision_audit_retains_rejection_after_recovery(self):
        item = self.opportunity()
        self.quote.previous_volume = self.quote.volume
        self.quote.save()
        evaluate_entry(item, NOW)
        self.quote.previous_volume -= 1
        self.quote.save()
        evaluate_entry(item, NOW)
        self.assertEqual(list(item.decisions.values_list("state", flat=True)), [S.ELIGIBLE, S.REJECTED])

    def test_full_minute_scan_creates_real_unmocked_breakout(self):
        data = bars()
        data[-1] = replace(data[-1], high=101.2, close=101.15, volume=1600)
        self.underlying.last = 101.15
        self.underlying.save()
        for bar in data:
            for minute in range(5):
                RadarBar.objects.create(
                    instrument=self.equity, timestamp=bar.timestamp+timedelta(minutes=minute),
                    open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                    volume=bar.volume//5, complete=True, source="IMPORTED_OHLC",
                )
        scan(NOW)
        item = RadarOpportunity.objects.get(strategy="S2", side="CE")
        self.assertEqual(item.state, S.ELIGIBLE)
        acknowledge(item.pk, "take", NOW)
        self.quote.bid, self.quote.ask = 7, 7.05
        self.quote.save()
        scan(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXITED)
        self.assertEqual(list(item.decisions.values_list("state", flat=True)), [
            S.EXITED, S.TAKEN, S.ELIGIBLE, S.TRIGGERED, S.ARMED,
        ])

    def test_eligibility_take_exit_and_costs(self):
        item = self.eligible()
        taken = acknowledge(item.pk, "take", NOW)
        self.assertEqual(taken.state, S.TAKEN)
        self.assertEqual(taken.entry_price, self.quote.ask)
        self.assertEqual(taken.quantity % self.option.lot_size, 0)
        self.quote.bid, self.quote.ask = 7, 7.05
        self.quote.save()
        manage_positions(NOW)
        taken.refresh_from_db()
        self.assertEqual(taken.state, S.EXITED)
        self.assertEqual(taken.exit_price, 7)
        self.assertGreater(taken.charges, 0)
        self.assertLess(taken.net_pnl, (7-10.05)*taken.quantity)
        self.assertEqual(RadarAlert.objects.filter(opportunity=taken, state=S.EXITED).count(), 1)

    def test_stale_or_future_quote_cannot_qualify(self):
        for stamp in [NOW-timedelta(seconds=16), NOW+timedelta(seconds=1)]:
            item = self.opportunity(key=stamp.isoformat())
            self.quote.source_at = stamp
            self.quote.save()
            evaluate_entry(item, NOW)
            self.assertEqual(item.state, S.REJECTED)

    def test_wide_spread_not_option_momentum(self):
        item = self.opportunity()
        self.quote.ask = 12
        self.quote.save()
        evaluate_entry(item, NOW)
        self.assertEqual(item.state, S.REJECTED)

    def test_no_acceleration_is_explicit_rejection(self):
        item = self.opportunity()
        self.quote.previous_last = 10
        self.quote.save()
        evaluate_entry(item, NOW)
        self.assertIn("acceleration", item.reason)
        self.assertEqual(item.state, S.REJECTED)

    def test_zero_volume_increase_not_acceleration(self):
        item = self.opportunity()
        self.quote.previous_volume = self.quote.volume
        self.quote.save()
        evaluate_entry(item, NOW)
        self.assertEqual(item.state, S.REJECTED)

    def test_one_lot_unaffordable_is_not_forced(self):
        self.quote.ask = 1000
        self.quote.bid = 999
        self.quote.save()
        item = self.opportunity()
        quantity, reason = sizing(item, self.quote, Policy(), NOW)
        self.assertEqual(quantity, 0)
        self.assertIn("whole lot", reason)

    def test_take_rechecks_ask_limit(self):
        item = self.eligible()
        self.quote.bid, self.quote.ask = 10.1, 10.2
        self.quote.save()
        with self.assertRaisesMessage(ValueError, "limit"):
            acknowledge(item.pk, "take", NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.ELIGIBLE)

    def test_take_rechecks_acceleration_not_just_a_cheaper_ask(self):
        item = self.eligible()
        self.quote.last, self.quote.bid, self.quote.ask = 9.8, 9.8, 9.9
        self.quote.save()
        with self.assertRaisesMessage(ValueError, "acceleration"):
            acknowledge(item.pk, "take", NOW)

    def test_wide_spread_does_not_hide_an_executable_losing_exit(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        self.quote.bid, self.quote.ask = 7, 10
        self.quote.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXITED)
        self.assertEqual(item.exit_price, 7)

    def test_take_rechecks_underlying_and_age(self):
        item = self.eligible()
        with self.assertRaises(ValueError):
            acknowledge(item.pk, "take", NOW+timedelta(seconds=16))
        self.underlying.last = 101
        self.underlying.save()
        with self.assertRaisesMessage(ValueError, "extended"):
            acknowledge(item.pk, "take", NOW)

    def test_acknowledgement_is_idempotent_by_refusal(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        with self.assertRaises(ValueError):
            acknowledge(item.pk, "take", NOW)

    def test_same_stock_strategies_do_not_double_enter(self):
        first = self.eligible()
        acknowledge(first.pk, "take", NOW)
        second = self.opportunity(key="different", strategy="S2")
        evaluate_entry(second, NOW)
        self.assertEqual(second.state, S.REJECTED)
        self.assertIn("already holds", second.reason)

    def test_skip_never_becomes_a_position(self):
        item = self.eligible()
        acknowledge(item.pk, "skip", NOW)
        scan(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.SKIPPED)
        self.assertIsNone(item.entry_price)

    def test_stop_with_stale_option_latches_exit_pending(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        self.underlying.last = 97
        self.underlying.save()
        self.quote.source_at = NOW-timedelta(seconds=60)
        self.quote.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXIT_PENDING)
        self.assertIsNone(item.exit_price)
        self.underlying.last = 100.1
        self.underlying.save()
        self.quote.source_at, self.quote.bid = NOW, 8
        self.quote.ask = 8.05
        self.quote.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXITED)
        self.assertEqual(item.exit_price, 8)

    def test_insufficient_exit_depth_does_not_fake_a_full_fill(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        self.quote.bid_qty = 1
        self.quote.bid, self.quote.ask = 7, 7.05
        self.quote.save()
        manage_positions(NOW)
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXIT_PENDING)

    def test_expired_opportunity_cannot_be_taken(self):
        item = self.eligible()
        with self.assertRaises(ValueError):
            acknowledge(item.pk, "take", NOW+timedelta(minutes=3))
        scan(NOW+timedelta(minutes=3))
        item.refresh_from_db()
        self.assertEqual(item.state, S.EXPIRED)

    def test_old_eligible_alert_cancelled_after_take(self):
        item = self.eligible()
        acknowledge(item.pk, "take", NOW)
        self.assertTrue(RadarAlert.objects.get(opportunity=item, state=S.ELIGIBLE).cancelled_at)

    def test_news_exact_symbol_dedup_and_no_catchup_lookahead(self):
        fields = {"source_name": "BeatTheStreet News", "source_category": "NEWS",
                  "raw_text": "TEST wins a contract", "telegram_message_at": NOW}
        ChatMessage.objects.create(**fields, created_at=NOW)
        ChatMessage.objects.create(**fields, created_at=NOW)
        # auto_now_add must be explicitly updated to replay the receipt timestamp.
        ChatMessage.objects.all().update(created_at=NOW)
        ChatMessage.objects.create(**{**fields, "raw_text": "TESTING is not TESTCO"})
        context = news_context("TEST", NOW)
        self.assertEqual(len(context), 1)
        self.assertEqual(news_context("TEST", NOW-timedelta(seconds=1)), [])


class DeliveryTests(RadarTestCase):
    def test_delivery_does_not_hold_its_claim_transaction_across_network(self):
        self.eligible()
        depth = len(connection.savepoint_ids)
        def send(_text):
            self.assertEqual(len(connection.savepoint_ids), depth)
            return True, None
        with patch("options_tracker.radar_alerts.telegram_bot.send", side_effect=send):
            self.assertEqual(deliver_alerts(NOW)["sent"], 1)

    @patch("options_tracker.radar_alerts.telegram_bot.send", return_value=(True, None))
    def test_claimed_message_waits_until_lease_expires(self, send):
        self.eligible()
        RadarAlert.objects.update(next_attempt_at=NOW+timedelta(seconds=60))
        self.assertEqual(deliver_alerts(NOW)["sent"], 0)
        send.assert_not_called()
        RadarQuote.objects.update(source_at=NOW+timedelta(seconds=61), received_at=NOW+timedelta(seconds=61))
        self.assertEqual(deliver_alerts(NOW+timedelta(seconds=61))["sent"], 1)

    @patch("options_tracker.radar_alerts.telegram_bot.send")
    def test_dead_feed_cannot_send_a_stale_eligible_alert(self, send):
        self.eligible()
        self.assertEqual(deliver_alerts(NOW+timedelta(seconds=16))["cancelled"], 1)
        send.assert_not_called()

    @patch("options_tracker.radar_alerts.telegram_bot.send", return_value=(True, None))
    def test_delivery_no_repeat_after_success(self, send):
        self.eligible()
        self.assertEqual(deliver_alerts(NOW)["sent"], 1)
        self.assertEqual(deliver_alerts(NOW)["sent"], 0)
        send.assert_called_once()

    @patch("options_tracker.radar_alerts.telegram_bot.send", return_value=(False, "not configured"))
    def test_delivery_error_persistent_and_backed_off(self, send):
        self.eligible()
        self.assertEqual(deliver_alerts(NOW)["failed"], 1)
        alert = RadarAlert.objects.get(state=S.ELIGIBLE)
        self.assertIn("not configured", alert.last_error)
        self.assertGreater(alert.next_attempt_at, NOW)
        deliver_alerts(NOW)
        send.assert_called_once()

    @patch("options_tracker.radar_alerts.telegram_bot.send")
    def test_expired_entry_alert_not_delivered(self, send):
        self.eligible()
        self.assertEqual(deliver_alerts(NOW+timedelta(minutes=3))["cancelled"], 1)
        send.assert_not_called()


class FeedTests(RadarTestCase):
    def test_delivery_and_market_workers_have_independent_leases(self):
        market = acquire_worker(NOW)
        delivery = acquire_worker(NOW, name="paper-alerts")
        self.assertNotEqual(market, delivery)
        heartbeat(delivery, "IDLE", "test", NOW, name="paper-alerts")
        heartbeat(market, "STREAMING", "test", NOW)

    def test_same_second_depth_updates_are_not_discarded(self):
        collector = CandleCollector()
        collector.ingest(self.equity, self.tick(NOW, bid=99, ask=100))
        collector.ingest(self.equity, self.tick(NOW, bid=99.5, ask=100))
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.bid, 99.5)

    def test_forming_minute_not_flushed_early(self):
        collector = CandleCollector()
        collector.ingest(self.equity, self.tick(NOW))
        collector.flush(NOW+timedelta(seconds=59))
        self.assertFalse(RadarBar.objects.exists())

    def test_unknown_packet_skipped_and_disconnect_packet_errors(self):
        self.assertEqual(decode_packets(struct.pack("<BHBI", 5, 12, 2, 900)+struct.pack("<I", 123), NOW), [])
        with self.assertRaisesMessage(ConnectionError, "805"):
            decode_packets(struct.pack("<BHBIH", 50, 10, 2, 900, 805), NOW)
    def tick(self, at, **values):
        return Tick("NSE_EQ", "123", at, at, values.pop("last", 100), values.pop("volume", 1000), **values)

    def test_binary_full_packet_and_multiple_packets(self):
        packet = bytearray(162)
        struct.pack_into("<BHBI", packet, 0, 8, 162, 2, 900)
        struct.pack_into("<fHI", packet, 8, 10.0, 100, int(NOW.replace(tzinfo=dt_timezone.utc).timestamp()))
        struct.pack_into("<I", packet, 22, 10000)
        struct.pack_into("<IIHHff", packet, 62, 500, 600, 2, 3, 9.95, 10.05)
        ticks = decode_packets(bytes(packet)*2, NOW)
        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks[0].segment, "NSE_FNO")
        self.assertEqual(ticks[0].volume, 10000)
        self.assertEqual(ticks[0].source_at, NOW)
        self.assertAlmostEqual(ticks[0].ask, 10.05, places=4)

    def test_invalid_price_packet_is_logged_and_does_not_drop_valid_packets(self):
        invalid = bytearray(162)
        struct.pack_into("<BHBI", invalid, 0, 8, 162, 1, 123)
        valid = bytearray(162)
        struct.pack_into("<BHBI", valid, 0, 8, 162, 1, 456)
        struct.pack_into("<fHI", valid, 8, 100.0, 10, int(NOW.replace(tzinfo=dt_timezone.utc).timestamp()))
        with self.assertLogs("options_tracker.radar_feed", level="WARNING") as logs:
            ticks = decode_packets(bytes(invalid)+bytes(valid), NOW)
        self.assertEqual([tick.security_id for tick in ticks], ["456"])
        self.assertIn("NSE_EQ:123", logs.output[0])

    def test_unsupported_packet_size_is_logged_and_does_not_drop_valid_packets(self):
        unsupported = struct.pack("<BHBI", 8, 12, 1, 123)+struct.pack("<I", 0)
        valid = bytearray(162)
        struct.pack_into("<BHBI", valid, 0, 8, 162, 1, 456)
        struct.pack_into("<fHI", valid, 8, 100.0, 10, int(NOW.replace(tzinfo=dt_timezone.utc).timestamp()))
        with self.assertLogs("options_tracker.radar_feed", level="WARNING") as logs:
            ticks = decode_packets(unsupported+bytes(valid), NOW)
        self.assertEqual([tick.security_id for tick in ticks], ["456"])
        self.assertIn("code 8 and length 12", logs.output[0])

    def test_malformed_frame_remainder_is_logged_without_dropping_decoded_ticks(self):
        valid = bytearray(162)
        struct.pack_into("<BHBI", valid, 0, 8, 162, 1, 456)
        struct.pack_into("<fHI", valid, 8, 100.0, 10, int(NOW.replace(tzinfo=dt_timezone.utc).timestamp()))
        with self.assertLogs("options_tracker.radar_feed", level="WARNING") as logs:
            ticks = decode_packets(bytes(valid)+b"\x08\x00", NOW)
            self.assertEqual(decode_packets(struct.pack("<BHBI", 8, 900, 2, 900), NOW), [])
        self.assertEqual([tick.security_id for tick in ticks], ["456"])
        self.assertIn("truncated header", logs.output[0])
        self.assertIn("packet length 900", logs.output[1])

    def test_first_minute_is_incomplete_then_continuous_minutes_complete(self):
        collector = CandleCollector()
        start = NOW
        for i in range(0, 121, 10):
            collector.ingest(self.equity, self.tick(start+timedelta(seconds=i), volume=10_000+i, last=100+i/100))
        first, second = list(RadarBar.objects.order_by("timestamp"))
        self.assertFalse(first.complete)
        self.assertTrue(second.complete)
        self.assertEqual(second.volume, 60)
        self.assertLessEqual(second.low, second.close)

    def test_disconnect_invalidates_partial_candle(self):
        collector = CandleCollector()
        for i in [0, 20, 40, 60, 80]:
            collector.ingest(self.equity, self.tick(NOW+timedelta(seconds=i), volume=10_000+i))
        collector.disconnect()
        collector.ingest(self.equity, self.tick(NOW+timedelta(seconds=100), volume=10_100))
        collector.flush(NOW+timedelta(seconds=120))
        self.assertFalse(RadarBar.objects.order_by("-timestamp").first().complete)

    def test_volume_reset_invalidates_minute(self):
        collector = CandleCollector()
        for i in [0, 20, 40, 60, 80]:
            collector.ingest(self.equity, self.tick(NOW+timedelta(seconds=i), volume=10_000+i))
        collector.ingest(self.equity, self.tick(NOW+timedelta(seconds=100), volume=1))
        collector.flush(NOW+timedelta(seconds=120))
        self.assertFalse(RadarBar.objects.order_by("-timestamp").first().complete)

    def test_late_ticks_do_not_overwrite_latest_quote(self):
        collector = CandleCollector()
        collector.ingest(self.equity, self.tick(NOW, last=100))
        collector.ingest(self.equity, self.tick(NOW-timedelta(seconds=1), last=99))
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.last, 100)

    def test_stale_tick_is_rejected(self):
        collector = CandleCollector()
        tick = self.tick(NOW-timedelta(seconds=30))
        collector.ingest(self.equity, replace(tick, received_at=NOW, last=99))
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.last, 100.1)

    def test_worker_exclusive_and_lease_recovery(self):
        owner = acquire_worker(NOW)
        with self.assertRaises(ValueError):
            acquire_worker(NOW, "second")
        heartbeat(owner, "STREAMING", "test", NOW)
        second = acquire_worker(NOW+timedelta(seconds=91), "second")
        self.assertEqual(second, "second")
        with self.assertRaises(ValueError):
            heartbeat(owner, "STREAMING", "stale worker", NOW+timedelta(seconds=92))


class WebAndCommandTests(RadarTestCase):
    @patch("options_tracker.management.commands.radar_deliver.timezone.now", return_value=NOW)
    @patch("options_tracker.radar_alerts.telegram_bot.send", return_value=(True, None))
    def test_explicit_delivery_command_sends_one_pending_paper_alert(self, send, _clock):
        self.eligible()
        output = StringIO()
        call_command("radar_deliver", once=True, stdout=output)
        self.assertIn('"sent": 1', output.getvalue())
        send.assert_called_once()

    def staff(self):
        user = get_user_model().objects.create_user("radar-admin", password="test-only", is_staff=True)
        self.client.force_login(user)

    def test_dashboard_requires_staff(self):
        self.assertEqual(self.client.get(reverse("radar")).status_code, 302)
        self.staff()
        response = self.client.get(reverse("radar"))
        self.assertContains(response, "Trading Workspace")
        self.assertContains(response, "Create private paper alert")
        self.assertContains(response, "No opportunities recorded")

    @patch("options_tracker.radar_views.timezone.now", return_value=NOW)
    def test_post_required_and_csrf_enforced(self, _clock):
        self.staff()
        item = self.eligible()
        url = reverse("radar_acknowledge", args=[item.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(get_user_model().objects.get(username="radar-admin"))
        self.assertEqual(client.post(url, {"action": "take"}).status_code, 403)
        self.assertEqual(self.client.post(url, {"action": "take"}).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.state, S.TAKEN)

    def test_dashboard_escapes_news_and_reason(self):
        self.staff()
        self.opportunity(reason="<script>bad()</script>")
        self.assertContains(self.client.get(reverse("radar")), "&lt;script&gt;")

    @patch("options_tracker.radar_views.timezone.now", return_value=NOW)
    def test_dashboard_shows_eligible_and_acknowledges(self, _clock):
        self.staff()
        item = self.eligible()
        self.assertContains(self.client.get(reverse("radar")), "Take PAPER position")
        self.assertEqual(self.client.post(reverse("radar_acknowledge", args=[item.pk]), {"action": "skip"}).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.state, S.SKIPPED)

    @patch("options_tracker.management.commands.radar_run.timezone.now", return_value=NOW)
    @patch("options_tracker.radar_history.get_dhan_credentials")
    def test_once_never_fetches_credentials_or_places_orders(self, credentials, _clock):
        output = StringIO()
        call_command("radar_run", once=True, stdout=output)
        self.assertIn("PAPER ONLY", output.getvalue())
        credentials.assert_not_called()

    @patch("options_tracker.management.commands.radar_import_bars.timezone.now", return_value=NOW+timedelta(days=1))
    def test_import_true_minute_bars_and_reject_conflicts(self, _clock):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            with path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["symbol", "ts", "open", "high", "low", "close", "volume"])
                writer.writerow(["TEST", NOW.isoformat(), 100, 101, 99, 100, 1000])
            call_command("radar_import_bars", input=path, stdout=StringIO())
            call_command("radar_import_bars", input=path, stdout=StringIO())
            self.assertEqual(RadarBar.objects.count(), 1)
            content = path.read_text().replace(",1000", ",2000")
            path.write_text(content)
            with self.assertRaises(CommandError):
                call_command("radar_import_bars", input=path, stdout=StringIO())

    @patch("options_tracker.radar.detect_setups", return_value=[])
    def test_scan_loads_all_instrument_bars_with_one_bounded_query(self, _detect):
        for index in range(5):
            RadarInstrument.objects.create(
                security_id=f"extra-{index}", segment="NSE_EQ", symbol=f"EXTRA{index}",
                kind="EQUITY", refreshed_at=NOW,
            )

        with CaptureQueriesContext(connection) as queries:
            scan(NOW)

        bar_queries = [query for query in queries if 'FROM "options_tracker_radarbar"' in query["sql"]]
        self.assertEqual(len(bar_queries), 2)

    @patch("options_tracker.radar.detect_setups", return_value=[])
    def test_scan_reports_progress_at_least_every_five_instruments(self, _detect):
        for index in range(11):
            instrument = RadarInstrument.objects.create(
                security_id=f"progress-{index}", segment="NSE_EQ", symbol=f"PROGRESS{index}",
                kind="EQUITY", refreshed_at=NOW,
            )
            RadarBar.objects.create(
                instrument=instrument, timestamp=NOW-timedelta(minutes=2),
                open=100, high=101, low=99, close=100, volume=100,
                complete=True, source="IMPORTED_OHLC",
            )
        progress_calls = []

        scan(NOW, progress=lambda: progress_calls.append(True))

        self.assertGreaterEqual(len(progress_calls), 3)

    @patch("options_tracker.radar.detect_setups")
    def test_scan_persists_unique_strategy_event_and_replay_does_not_duplicate(self, detect):
        for minute in range(5):
            RadarBar.objects.create(
                instrument=self.equity, timestamp=NOW-timedelta(minutes=5-minute),
                open=100, high=101, low=99, close=100, volume=100,
                complete=True, source="IMPORTED_OHLC",
            )
        detect.return_value = [Setup("S2", "CE", NOW-timedelta(minutes=5), 100, 98, 2, True, "test")]
        self.assertEqual(scan(NOW)["new_setups"], 1)
        self.assertEqual(scan(NOW)["new_setups"], 0)
        self.assertEqual(RadarOpportunity.objects.count(), 1)
        self.assertEqual(RadarOpportunity.objects.get().state, S.ELIGIBLE)
        self.assertEqual(RadarAlert.objects.filter(state=S.ELIGIBLE).count(), 1)
