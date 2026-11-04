"""Directional discovery, frozen forward evidence and split-worker regressions."""

from dataclasses import replace
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
import requests

from .models import (
    RadarAlert, RadarAssistantSnapshot, RadarBar, RadarCatalyst, RadarHistoryConflict,
    RadarInstrument, RadarOptionPoint, RadarPriceAlert, RadarQuote, RadarResearchObservation, RadarWorker,
)
from .radar import Candle, Policy, S, option_priority, scan, transition
from .radar_assistant import (
    assistant_payload, build_shortlist, gap_diagnostics, opening_range_watch,
    persist_shortlist, trigger_report, worker_health,
)
from .radar_feed import CandleCollector, Tick, TickBuffer, subscriptions, sync_instruments
from .radar_history import accept_incomplete_repairs, store_bars
from .radar_research import capture_observations, markout, register_protocol, research_report, update_observations
from .test_radar import NOW, RadarTestCase, master_rows


class ScanTransactionTests(TransactionTestCase):
    def test_universe_history_reads_do_not_hold_sqlite_write_transaction(self):
        sync_instruments(master_rows(), NOW)
        original = RadarBar.objects.filter
        states = []

        def history(*args, **kwargs):
            states.append(connection.in_atomic_block)
            return original(*args, **kwargs)

        with patch.object(RadarBar.objects, "filter", side_effect=history):
            scan(NOW, assistant=True)
        self.assertGreaterEqual(len(states), 2)
        self.assertFalse(any(states))


class AssistantTests(RadarTestCase):
    def test_trigger_report_uses_durable_confirmations_and_user_crossings(self):
        user = get_user_model().objects.create_user("trigger-report-user", is_staff=True)
        other = get_user_model().objects.create_user("other-trigger-report-user", is_staff=True)
        opportunity = self.opportunity()
        RadarAlert.objects.create(
            opportunity=opportunity, state=S.TRIGGERED, text="Recorded test trigger",
            created_at=NOW, sent_at=NOW,
        )
        RadarPriceAlert.objects.create(
            user=user, instrument=self.equity, direction="ABOVE", level=101,
            expires_at=NOW+timedelta(days=1), triggered_at=NOW, trigger_price=101.2, sent_at=NOW,
        )
        RadarPriceAlert.objects.create(
            user=other, instrument=self.equity, direction="ABOVE", level=102,
            expires_at=NOW+timedelta(days=1), triggered_at=NOW, trigger_price=102.2,
        )
        report = trigger_report(user, NOW)
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["strategy_count"], 1)
        self.assertEqual(report["personal_price_alert_count"], 1)
        strategy, personal = report["events"]
        self.assertEqual(strategy["kind"], "STRATEGY")
        self.assertEqual(strategy["trigger"], 100)
        self.assertEqual(strategy["stop"], 98)
        self.assertIn("Delivered", strategy["delivery"])
        self.assertEqual(personal["kind"], "PERSONAL_PRICE_ALERT")
        self.assertEqual(personal["trigger"], 101.2)
        self.assertIn("alert, not a strategy entry", personal["reason"])
        self.assertEqual(assistant_payload(NOW, user)["trigger_report"], report)

    def test_clock_lead_is_deferred_without_rewriting_receipt_or_freshness(self):
        buffer = TickBuffer()
        collector = CandleCollector(buffer_quotes=True)
        first = Tick("NSE_EQ", "123", NOW-timedelta(seconds=2), NOW-timedelta(seconds=2), 100, 100)
        self.underlying.source_at = self.underlying.received_at = first.source_at
        self.underlying.save()
        collector.ingest(self.equity, first)
        for second in [0, 1, 30, 59, 60]:
            source = NOW+timedelta(seconds=second)
            received = source-timedelta(milliseconds=1300)
            tick = replace(first, source_at=source, received_at=received, volume=101+second)
            self.assertEqual(buffer.ready([tick], received), [])
            released = buffer.ready([], source)
            self.assertEqual(released, [tick])
            collector.ingest(self.equity, released[0], accepted_at=source)
        minute = RadarBar.objects.get(instrument=self.equity, timestamp=NOW)
        self.assertTrue(minute.complete)
        quote = collector.quotes[self.equity.pk]
        self.assertEqual(quote.received_at, NOW+timedelta(seconds=58, milliseconds=700))
        from .radar import fresh
        self.assertTrue(fresh(quote, NOW+timedelta(seconds=60), Policy()))
        self.assertFalse(fresh(quote, NOW+timedelta(seconds=59, milliseconds=900), Policy()))

    def test_large_future_anomaly_is_not_hidden_by_clock_buffer(self):
        buffer = TickBuffer()
        tick = Tick("NSE_EQ", "123", NOW+timedelta(seconds=16), NOW, 100, 100)
        self.assertEqual(buffer.ready([tick], NOW), [tick])
        CandleCollector().ingest(self.equity, tick, accepted_at=NOW)
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.source_at, NOW)

    def test_clock_wait_buffer_is_bounded_and_does_not_make_old_receipts_fresh(self):
        tick = Tick("NSE_EQ", "123", NOW+timedelta(milliseconds=500), NOW, 100, 100)
        with self.assertRaises(ValueError):
            TickBuffer().ready([tick]*5001, NOW)
        buffer = TickBuffer()
        self.assertEqual(buffer.ready([tick], NOW), [])
        released = buffer.ready([], NOW+timedelta(seconds=16))
        CandleCollector().ingest(self.equity, released[0], accepted_at=NOW+timedelta(seconds=16))
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.source_at, NOW)

    def test_telegram_failure_cannot_persist_bot_credentials(self):
        from . import telegram_bot
        token = "private-test-token"
        with patch.object(telegram_bot, "_setting", return_value=token), patch.object(
            telegram_bot.requests, "post", side_effect=requests.ConnectionError(f"https://api.telegram.org/bot{token}/sendMessage"),
        ):
            _, error = telegram_bot._call("sendMessage")
        self.assertEqual(error, "network: ConnectionError")
        self.assertNotIn(token, error)

    def structure(self, instrument=None):
        instrument = instrument or self.equity
        start = NOW.replace(hour=9, minute=15)
        bearish = instrument.symbol.startswith("BEAR")
        rows = [
            RadarBar(instrument=instrument, timestamp=start+timedelta(minutes=i),
                     open=100, high=100.8, low=99.2, close=100, volume=100, complete=True)
            for i in range(110)
        ]
        rows[-1].high = 100.8 if bearish else 101.1
        rows[-1].low = 98.9 if bearish else 99.2
        rows[-1].close = 99 if bearish else 101
        RadarBar.objects.bulk_create(rows)

    def metrics(self, instruments, _quotes, _now):
        return {i.symbol: {"rvol": 2, "change_pct": -.5 if i.symbol.startswith("BEAR") else .5} for i in instruments}

    def test_opening_range_watch_requires_rvol_relative_strength_vwap_and_breakout(self):
        self.structure()
        rows = [
            Candle(row.timestamp, row.open, row.high, row.low, row.close, row.volume)
            for row in RadarBar.objects.filter(instrument=self.equity).order_by("timestamp")
        ]
        from .radar import five_minute_bars
        bars = five_minute_bars(rows)
        watch = opening_range_watch(bars, change_pct=.5, rvol=1.2, breadth=.2)
        self.assertEqual(len(watch), 1)
        self.assertEqual(watch[0]["side"], "CE")
        self.assertEqual(watch[0]["state"], "OPENING_RANGE_CONFIRMED")
        self.assertEqual(opening_range_watch(bars, .5, 1.19, .2), [])
        self.assertEqual(opening_range_watch(bars, .1, 1.2, .2), [])
        self.assertEqual(opening_range_watch(bars, .5, 1.2, -.2), [])
        early = bars[:4]
        early[-1] = replace(early[-1], high=101.1, close=101)
        self.assertEqual(
            opening_range_watch(early, .5, 1.2, .2)[0]["state"],
            "OPENING_RANGE_CONFIRMED",
        )
        bearish = [replace(bar) for bar in bars]
        bearish[-1] = replace(bearish[-1], low=98.9, close=99)
        self.assertEqual(opening_range_watch(bearish, -.5, 1.2, -.2), [])

    @patch("options_tracker.radar_assistant.table_metrics")
    def test_directional_rank_is_explainable_not_probability_and_no_optional_defaults(self, metrics):
        metrics.side_effect = self.metrics
        self.structure()
        result = build_shortlist(NOW)
        self.assertEqual(len(result["bullish"]), 1)
        self.assertEqual(result["bearish"], [])
        item = result["bullish"][0]
        self.assertEqual(item["score"], 60)
        self.assertEqual(sum(c["points"] for c in item["components"]), 60)
        self.assertIn("not a win probability", item["why"])
        self.assertIn("unavailable", result["coverage"].lower())
        self.assertGreater(item["trigger"], self.underlying.last)
        self.assertLess(item["stop"], self.underlying.last)
        self.assertEqual(item["state"], "STOCK_WATCH")

    @patch("options_tracker.radar_assistant.table_metrics")
    def test_caps_each_side_at_five_and_ties_are_deterministic(self, metrics):
        metrics.side_effect = self.metrics
        for side in ["BULL", "BEAR"]:
            for i in range(7):
                instrument = RadarInstrument.objects.create(
                    symbol=f"{side}{i}", name="Synthetic only", kind="EQUITY",
                    segment="NSE_EQ", security_id=f"{side}{i}", active=True, refreshed_at=NOW,
                )
                RadarQuote.objects.create(instrument=instrument, source_at=NOW, received_at=NOW,
                                          last=99.9 if side == "BEAR" else 100.1, volume=1000)
                self.structure(instrument)
        self.structure()
        result = build_shortlist(NOW)
        self.assertEqual(len(result["bullish"]), 5)
        self.assertEqual(result["bearish"], [], "The market-direction gate must not rank both sides.")
        self.assertEqual([i["symbol"] for i in result["bullish"]], [f"BULL{i}" for i in range(5)])

    @patch("options_tracker.radar_assistant.table_metrics")
    def test_missing_rvol_or_stale_quote_produces_normal_empty_result(self, metrics):
        metrics.return_value = {"TEST": {"rvol": None, "change_pct": 1}}
        self.structure()
        result = build_shortlist(NOW)
        self.assertEqual(result["bullish"], [])
        self.assertIn("No suitable", result["message"])
        self.underlying.source_at = NOW-timedelta(seconds=16)
        self.underlying.save()
        self.assertEqual(build_shortlist(NOW)["excluded"]["stale_or_missing_quote"], 1)

    @patch("options_tracker.radar_assistant.table_metrics")
    def test_gaps_and_extension_block_shortlisting(self, metrics):
        metrics.side_effect = self.metrics
        self.structure()
        RadarBar.objects.filter(timestamp=NOW-timedelta(minutes=2)).update(complete=False)
        self.assertFalse(build_shortlist(NOW)["bullish"])
        RadarBar.objects.all().update(complete=True)
        self.underlying.last = 105
        self.underlying.save()
        self.assertFalse(build_shortlist(NOW)["bullish"])

    @patch("options_tracker.radar_assistant.table_metrics")
    def test_catalyst_requires_publication_verification_validity_and_safe_source(self, metrics):
        metrics.side_effect = self.metrics
        self.structure()
        catalyst = RadarCatalyst.objects.create(
            symbol="TEST", side="CE", summary="Synthetic verified event", source_url="https://example.com/filing",
            published_at=NOW-timedelta(minutes=5), verified_at=NOW, expires_at=NOW+timedelta(hours=1),
        )
        self.assertEqual(build_shortlist(NOW)["bullish"][0]["score"], 70)
        catalyst.verified_at = NOW+timedelta(seconds=1)
        catalyst.save()
        self.assertEqual(build_shortlist(NOW)["bullish"][0]["score"], 60)
        catalyst.verified_at = NOW
        catalyst.source_url = "javascript:alert(1)"
        catalyst.save()
        self.assertEqual(build_shortlist(NOW)["bullish"][0]["score"], 60)

    @patch("options_tracker.radar_assistant.table_metrics")
    def test_snapshot_is_immutable_and_expires_without_page_side_effects(self, metrics):
        metrics.side_effect = self.metrics
        self.structure()
        first = persist_shortlist(NOW)
        self.assertEqual(persist_shortlist(NOW+timedelta(seconds=10)).pk, first.pk)
        self.assertEqual(RadarAssistantSnapshot.objects.count(), 1)
        payload = assistant_payload(NOW+timedelta(seconds=61))
        self.assertFalse(payload["current"])
        self.assertEqual(payload["shortlist"]["bullish"], [])
        self.assertEqual(payload["momentum_evidence"]["candidate"], "opening-range-momentum-v1")
        self.assertEqual(payload["momentum_evidence"]["held_out_bullish"]["expectancy_r"], .102)

    def test_shortlist_expands_only_current_option_subscriptions(self):
        RadarAssistantSnapshot.objects.create(timestamp=NOW, expires_at=NOW+timedelta(seconds=60),
                                             payload={"bullish": [{"symbol": "TEST"}], "bearish": []})
        self.assertIn(self.option.pk, [i.pk for i in subscriptions(NOW)])
        self.assertNotIn(self.option.pk, [i.pk for i in subscriptions(NOW+timedelta(seconds=61))])

    def test_primary_forming_and_confirmation_alerts_do_not_spam_comparators(self):
        primary = self.opportunity(evidence={"assistant": True})
        transition(primary, S.ARMED, "Synthetic forming", NOW)
        self.assertTrue(RadarAlert.objects.filter(opportunity=primary, state=S.ARMED).exists())
        transition(primary, S.TRIGGERED, "Synthetic confirmed", NOW)
        self.assertIsNotNone(RadarAlert.objects.get(opportunity=primary, state=S.ARMED).cancelled_at)
        comparison = self.opportunity(key="comparison", strategy="S2", evidence={"assistant": True})
        transition(comparison, S.ARMED, "Synthetic comparator", NOW)
        transition(comparison, S.ELIGIBLE, "Synthetic comparator", NOW)
        self.assertFalse(comparison.alerts.exists())
        transition(comparison, S.EXIT_PENDING, "Exit required", NOW)
        self.assertTrue(comparison.alerts.exists())

    def test_diagnostics_distinguish_missing_and_incomplete_minutes(self):
        RadarBar.objects.create(instrument=self.equity, timestamp=NOW.replace(hour=9, minute=15),
                                open=100, high=100, low=100, close=100, volume=0, complete=False)
        result = gap_diagnostics(NOW)
        self.assertEqual(result["expected_minutes"], 110)
        self.assertEqual(result["affected"][0]["missing"], 109)
        self.assertEqual(result["affected"][0]["incomplete"], 1)

    def test_conflicting_history_is_audited_idempotently_without_mutating_bar(self):
        bar = Candle(NOW-timedelta(minutes=1), 100, 101, 99, 100, 1000)
        store_bars(self.equity, [bar], "DHAN_HISTORY_1M")
        correction = replace(bar, close=100.5)
        store_bars(self.equity, [correction], "DHAN_HISTORY_1M")
        store_bars(self.equity, [correction], "DHAN_HISTORY_1M")
        self.assertEqual(RadarHistoryConflict.objects.count(), 1)
        self.assertEqual(RadarBar.objects.get().close, 100)
        self.assertEqual(RadarHistoryConflict.objects.get().proposed["close"], 100.5)

    def test_explicit_repair_preserves_original_and_never_replaces_complete_bars(self):
        bar = RadarBar.objects.create(
            instrument=self.equity, timestamp=NOW-timedelta(minutes=1),
            open=100, high=101, low=99, close=100, volume=10, complete=False,
        )
        correction = Candle(bar.timestamp, 100, 102, 99, 101, 1000)
        store_bars(self.equity, [correction], "DHAN_HISTORY_1M")
        self.assertEqual(accept_incomplete_repairs(NOW, ["TEST"]), 1)
        bar.refresh_from_db()
        self.assertTrue(bar.complete)
        self.assertEqual(bar.close, 101)
        audit = RadarHistoryConflict.objects.get()
        self.assertEqual(audit.original["close"], 100)
        self.assertFalse(audit.original["complete"])
        self.assertEqual(audit.accepted_at, NOW)
        self.assertEqual(accept_incomplete_repairs(NOW), 0)
        store_bars(self.equity, [replace(correction, close=100.5)], "DHAN_HISTORY_1M")
        self.assertEqual(accept_incomplete_repairs(NOW), 0)
        bar.refresh_from_db()
        self.assertEqual(bar.close, 101)

    def test_independent_health_requires_evaluator_and_coverage(self):
        for name in ["paper-radar", "radar-evaluator"]:
            RadarWorker.objects.create(name=name, state="STREAMING", heartbeat_at=NOW, lease_until=NOW+timedelta(seconds=90))
        self.assertTrue(worker_health(NOW)["healthy"])
        RadarWorker.objects.update(heartbeat_at=NOW+timedelta(seconds=2))
        self.assertTrue(worker_health(NOW)["healthy"])
        self.assertFalse(worker_health(NOW+timedelta(seconds=31))["healthy"])
        self.underlying.source_at = NOW-timedelta(seconds=20)
        self.underlying.save()
        self.assertFalse(worker_health(NOW)["healthy"])

    def test_health_command_nonzero_and_never_uses_network(self):
        with patch("options_tracker.management.commands.radar_health.credentials", return_value=("private", "private")):
            output = StringIO()
            with self.assertRaises(CommandError):
                call_command("radar_health", require_healthy=True, stdout=output)
            self.assertNotIn("private", output.getvalue())

    def test_split_evaluator_refuses_combined_live_worker(self):
        RadarWorker.objects.create(name="paper-radar", detail="combined", lease_until=timezone.now()+timedelta(seconds=90))
        with self.assertRaises(CommandError):
            call_command("radar_evaluate", once=True, stdout=StringIO())

    def test_combined_worker_refuses_separate_evaluator(self):
        RadarWorker.objects.create(name="radar-evaluator", lease_until=timezone.now()+timedelta(seconds=90))
        with self.assertRaises(CommandError):
            call_command("radar_run", once=True, stdout=StringIO())

    def test_collect_only_requires_stream(self):
        with self.assertRaises(CommandError):
            call_command("radar_run", once=True, collect_only=True, stdout=StringIO())

    def test_separate_evaluator_once_persists_protocol_and_snapshot_without_broker(self):
        with patch("options_tracker.management.commands.radar_evaluate.scan", return_value={"new_setups": 0}) as scan:
            call_command("radar_evaluate", once=True, stdout=StringIO())
        scan.assert_called_once()
        self.assertTrue(scan.call_args.kwargs["assistant"])
        self.assertTrue(RadarAssistantSnapshot.objects.exists())
        self.assertEqual(RadarWorker.objects.get(name="radar-evaluator").state, "STOPPED")
        self.assertEqual(research_report(timezone.now())["status"], "REGISTERED_NOT_STARTED")

    def test_reconcile_acceptance_is_explicit_and_backfill_errors_propagate(self):
        target = "options_tracker.management.commands.radar_reconcile"
        with patch(f"{target}.call_command") as backfill, patch(f"{target}.accept_incomplete_repairs", return_value=0) as repair:
            call_command("radar_reconcile", once=True, symbols=["TEST"], stdout=StringIO())
            repair.assert_not_called()
            call_command("radar_reconcile", once=True, symbols=["TEST"], accept_incomplete=True, stdout=StringIO())
            repair.assert_called_once()
            self.assertEqual(backfill.call_args.kwargs, {"days": 1, "symbols": ["TEST"]})
            backfill.side_effect = CommandError("History unavailable")
            with self.assertRaises(CommandError):
                call_command("radar_reconcile", once=True, stdout=StringIO())

    def test_option_rank_prefers_executable_spread_and_preserves_no_greeks_assumption(self):
        self.quote.instrument = self.option
        worse = RadarQuote(instrument=self.option, source_at=NOW, received_at=NOW, last=10,
                           bid=9.9, ask=10.1, bid_qty=500, ask_qty=500, volume=10000,
                           previous_last=9.9, previous_at=NOW-timedelta(seconds=30), previous_volume=9000)
        self.assertLess(option_priority(self.quote, 100, NOW, Policy()), option_priority(worse, 100, NOW, Policy()))


class ResearchTests(RadarTestCase):
    def test_protocol_freezes_rules_policy_and_next_session(self):
        protocol = register_protocol(NOW)
        self.assertEqual(protocol.specification["primary"], "S1")
        self.assertGreater(protocol.starts_at, NOW)
        self.assertEqual(register_protocol(NOW).pk, protocol.pk)
        with patch("options_tracker.radar_research.VERSION", "changed"):
            with self.assertRaises(ValueError):
                register_protocol(NOW)

    def test_observations_are_once_only_and_missing_reference_never_backfilled(self):
        protocol = register_protocol(NOW)
        item = self.opportunity()
        self.underlying.source_at = NOW-timedelta(seconds=20)
        self.underlying.save()
        self.assertEqual(capture_observations(protocol, NOW), 1)
        observation = RadarResearchObservation.objects.get()
        self.assertIsNone(observation.reference["last"])
        self.assertEqual(observation.reference["cohort"], "WARMUP_NOT_FORWARD")
        self.assertEqual(capture_observations(protocol, NOW+timedelta(seconds=1)), 0)
        self.assertEqual(observation.opportunity_id, item.pk)

    def path(self, captured=NOW):
        start = captured+timedelta(minutes=1)
        RadarBar.objects.bulk_create([
            RadarBar(instrument=self.equity, timestamp=start+timedelta(minutes=i),
                     open=100, high=102, low=99, close=101, volume=100, complete=True)
            for i in range(14)
        ])

    def test_markout_requires_complete_post_capture_path_and_uses_signed_direction(self):
        self.path()
        output = markout("TEST", NOW, NOW+timedelta(minutes=15), 100, 1, 2)
        self.assertAlmostEqual(output["return_pct"], 1)
        self.assertEqual(output["mfe_r"], 1)
        self.assertEqual(output["mae_r"], .5)
        bearish = markout("TEST", NOW, NOW+timedelta(minutes=15), 100, -1, 2)
        self.assertEqual(bearish["mfe_r"], .5)
        self.assertEqual(bearish["mae_r"], 1)
        RadarBar.objects.filter(timestamp=NOW+timedelta(minutes=2)).update(complete=False)
        self.assertFalse(markout("TEST", NOW, NOW+timedelta(minutes=15), 100, 1, 2)["available"])

    def test_no_outcome_before_horizon_and_once_missing_stays_missing(self):
        protocol = register_protocol(NOW)
        self.opportunity()
        capture_observations(protocol, NOW)
        self.assertEqual(update_observations(protocol, NOW+timedelta(minutes=14)), 0)
        self.assertEqual(update_observations(protocol, NOW+timedelta(minutes=15)), 1)
        self.path()
        update_observations(protocol, NOW+timedelta(minutes=15))
        self.assertFalse(RadarResearchObservation.objects.get().outcomes["15"]["available"])

    def test_warmup_never_counted_as_forward_profitability(self):
        protocol = register_protocol(NOW)
        self.opportunity()
        capture_observations(protocol, NOW)
        report = research_report(NOW)
        self.assertEqual(report["status"], "REGISTERED_NOT_STARTED")
        self.assertTrue(all(g["observed"] == 0 for g in report["groups"]))

    def test_forward_report_cost_stress_drawdown_regime_and_sampled_option_excursions(self):
        protocol = register_protocol(NOW)
        captured = protocol.starts_at+timedelta(minutes=10)
        item = self.opportunity(created_at=captured, state=S.EXITED, contract=self.option,
                                quantity=100, entry_price=10, exit_price=9, net_pnl=-120,
                                opened_at=captured, closed_at=captured+timedelta(seconds=30))
        self.underlying.source_at = self.underlying.received_at = captured
        self.underlying.save()
        capture_observations(protocol, captured)
        for offset, bid in [(0, 10), (15, 11), (30, 9)]:
            RadarOptionPoint.objects.create(instrument=self.option, timestamp=captured+timedelta(seconds=offset),
                                           received_at=captured+timedelta(seconds=offset), last=bid,
                                           bid=bid, ask=bid+.1, volume=1000, bid_qty=100)
        report = research_report(captured+timedelta(minutes=1))
        group = report["groups"][0]
        self.assertEqual(group["closed"], 1)
        self.assertEqual(group["expectancy"], -120)
        self.assertEqual(group["max_drawdown"], 120)
        self.assertEqual(group["slippage_stress_net"]["10"], -121.9)
        self.assertEqual(group["regimes"]["UNAVAILABLE"]["observed"], 1)
        self.assertTrue(group["option_excursions"][0]["available"])
        self.assertEqual(item.pk, group["option_excursions"][0]["opportunity_id"])
