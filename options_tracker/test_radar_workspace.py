"""Numerical, isolation and feed regressions for the paper stock workspace."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone as dt_timezone
import json
import struct
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.templatetags.static import static
from django.urls import reverse

from .capital_pnl import estimate_option_charges
from .models import (
    IndexOISnapshot, RadarBar, RadarInstrument, RadarOptionPoint, RadarQuote,
    RadarWatch, RadarWorkspace,
)
from .radar import S, acknowledge
from .radar_feed import CandleCollector, Tick, decode_packets, subscriptions
from .radar_workspace import (
    StockData, aggregate_chart, indicator_chart, historical_chart, market_context, opportunity_payload, option_candidates,
    portfolio, preferences, review_session, session_start, table_metrics, workspace_payload,
)
from .test_radar import NOW, RadarTestCase


class WorkspaceTests(RadarTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("workspace-staff", is_staff=True)
        self.other = get_user_model().objects.create_user("workspace-other", is_staff=True)
        self.client.force_login(self.user)

    def day_bars(self, day, count=360, volume=100, opening=100, complete=True):
        rows = [RadarBar(
            instrument=self.equity, timestamp=session_start(day)+timedelta(minutes=i),
            open=opening, high=opening+1, low=opening-1, close=opening,
            volume=volume, complete=complete, source="TEST_SYNTHETIC",
        ) for i in range(count)]
        RadarBar.objects.bulk_create(rows)
        return rows

    def history(self, current_volume=200):
        rows = []
        day = NOW.date()-timedelta(days=1)
        sessions = 0
        while sessions < 5:
            if day.weekday() < 5:
                rows.extend(self.day_bars(day, volume=100))
                sessions += 1
            day -= timedelta(days=1)
        rows.extend(self.day_bars(NOW.date(), count=110, volume=current_volume))
        return sorted(rows, key=lambda row: row.timestamp)

    def post_json(self, name, data):
        return self.client.post(reverse(name), json.dumps(data), content_type="application/json")

    def test_dashboard_uses_shared_arc_shell_and_keeps_paper_workspace_hooks(self):
        response = self.client.get(reverse("radar"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="arc-workspace-page"')
        self.assertContains(response, 'class="brand-mark">ARC')
        self.assertContains(response, 'class="radar-workspace-shell"')
        self.assertContains(response, "Trading Workspace | ARC Trading Journal")
        self.assertContains(response, "Exception queue")
        self.assertContains(response, "Eligibility and action")
        self.assertContains(response, "Strategy lanes")
        self.assertContains(response, "Opening-range momentum")
        self.assertContains(response, "CAS context")
        self.assertContains(response, "Live NIFTY execution is locked")
        self.assertContains(response, 'id="workspace"')
        self.assertContains(response, 'id="exception-queue"')
        self.assertContains(response, 'id="primary-decision-action"')
        self.assertContains(response, 'id="health-freshness"')
        self.assertContains(response, 'id="selected-stock"')
        self.assertContains(response, 'id="csrf-form"')
        self.assertContains(response, 'id="workspace-data"')
        self.assertContains(response, static("options_tracker/radar_workspace.css"))
        self.assertContains(response, static("options_tracker/radar_workspace.js"))

    def test_closed_session_lookup_uses_indexable_timestamp_bounds(self):
        self.day_bars(NOW.date(), count=10)
        after_close = NOW.replace(hour=18, minute=0)

        with CaptureQueriesContext(connection) as queries:
            session = review_session(after_close)

        self.assertEqual(session["day"], NOW.date().isoformat())
        self.assertFalse(session["open"])
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        self.assertNotIn("django_datetime_cast_time", sql)
        self.assertIn("timestamp", sql)

    def test_exact_rvol_vwap_gap_atr_and_sql_table_agree(self):
        rows = self.history()
        data = StockData(self.equity, self.underlying, rows, NOW).metrics()
        values = data["metrics"]
        self.assertEqual(values["volume"]["value"], 22000)
        self.assertEqual(values["rvol"]["value"], 2)
        self.assertEqual(data["baseline_sessions"], 5)
        self.assertEqual(values["vwap"]["value"], 100)
        self.assertAlmostEqual(values["vwap_distance"]["value"], .1)
        self.assertAlmostEqual(values["change_pct"]["value"], .1)
        self.assertEqual(values["gap"]["value"], 0)
        self.assertEqual(values["atr"]["value"], 2)
        self.assertAlmostEqual(values["range_atr"]["value"], 1)
        table = table_metrics([self.equity], {self.equity.pk: self.underlying}, NOW)["TEST"]
        self.assertEqual(table["rvol"], values["rvol"]["value"])
        self.assertEqual(table["change_pct"], values["change_pct"]["value"])

    def test_rvol_never_compares_partial_day_with_full_day(self):
        rows = self.history()
        # A huge future half-session must not affect same-time volume.
        self.day_bars(NOW.date()-timedelta(days=8), count=360, volume=500)
        # Only five most recent baseline prefixes are eligible here; sixth is incomplete.
        RadarBar.objects.filter(instrument=self.equity, timestamp=session_start(NOW.date()-timedelta(days=8))).delete()
        data = StockData(self.equity, self.underlying, rows, NOW).metrics()
        self.assertEqual(data["metrics"]["rvol"]["value"], 2)
        self.assertEqual(table_metrics([self.equity], {self.equity.pk: self.underlying}, NOW)["TEST"]["rvol"], 2)

    def test_table_uses_bounded_indexed_windows_and_excludes_out_of_session_volume(self):
        self.history()
        for day in [NOW.date(), NOW.date()-timedelta(days=1)]:
            for offset in [-1, 375]:
                RadarBar.objects.create(
                    instrument=self.equity, timestamp=session_start(day)+timedelta(minutes=offset),
                    open=1, high=1, low=1, close=1, volume=10**9, complete=True,
                )
        with CaptureQueriesContext(connection) as queries:
            result = table_metrics([self.equity], {self.equity.pk: self.underlying}, NOW)["TEST"]
        self.assertEqual(result["rvol"], 2)
        self.assertAlmostEqual(result["change_pct"], .1)
        self.assertLessEqual(len(queries), 43)
        if connection.vendor == "sqlite":
            self.assertFalse(any("django_datetime_cast" in query["sql"] for query in queries))

    def test_buffered_quotes_preserve_latest_depth_baseline_and_all_candle_ticks(self):
        collector = CandleCollector(buffer_quotes=True)
        for seconds, last, volume in [(0, 100, 1000), (1, 105, 1010), (2, 98, 1020), (3, 102, 1030)]:
            at = NOW+timedelta(seconds=seconds)
            collector.ingest(self.equity, Tick("NSE_EQ", "123", at, at, last, volume, bid=last-1, ask=last+1))
        with CaptureQueriesContext(connection) as queries:
            collector.flush(NOW+timedelta(seconds=3))
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.last, 102)
        self.assertEqual(self.underlying.bid, 101)
        self.assertEqual(self.underlying.volume, 1030)
        self.assertEqual(self.underlying.source_at, NOW+timedelta(seconds=3))
        self.assertEqual(self.underlying.previous_last, 100.1)
        bucket = collector.pending[self.equity.pk]
        self.assertEqual((bucket["high"], bucket["low"], bucket["close"], bucket["volume"]), (105, 98, 102, 30))
        self.assertLessEqual(len(queries), 3)
        at = NOW+timedelta(seconds=4)
        collector.ingest(self.equity, Tick("NSE_EQ", "123", at, at, 103, 1040))
        collector.flush(at-timedelta(milliseconds=800))
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.last, 102)
        collector.flush(at)
        self.underlying.refresh_from_db()
        self.assertEqual(self.underlying.last, 103)

    def test_gap_in_current_prefix_makes_vwap_and_rvol_unavailable(self):
        rows = self.history()
        rows.pop(next(i for i, row in enumerate(rows) if row.timestamp == session_start(NOW.date())+timedelta(minutes=1)))
        data = StockData(self.equity, self.underlying, rows, NOW).metrics()
        self.assertIsNone(data["metrics"]["vwap"]["value"])
        self.assertIsNone(data["metrics"]["rvol"]["value"])
        self.assertFalse(data["coverage"]["prefix_complete"])

    def test_incomplete_prefix_and_less_than_five_baselines_do_not_fabricate_rvol(self):
        rows = self.history()
        for row in rows:
            if row.timestamp == session_start(NOW.date()-timedelta(days=1)):
                row.complete = False
        data = StockData(self.equity, self.underlying, rows, NOW).metrics()
        self.assertEqual(data["baseline_sessions"], 4)
        self.assertIsNone(data["metrics"]["rvol"]["value"])
        self.assertIsNone(data["metrics"]["previous_close"]["value"])
        self.assertIsNone(data["metrics"]["change_pct"]["value"])

    def test_vwap_is_volume_weighted_typical_price(self):
        now = NOW.replace(hour=9, minute=17)
        rows = self.day_bars(now.date(), count=2)
        rows[1].open, rows[1].high, rows[1].low, rows[1].close, rows[1].volume = 110, 113, 108, 112, 300
        data = StockData(self.equity, self.underlying, rows, now).metrics()
        self.assertAlmostEqual(data["metrics"]["vwap"]["value"], (100*100+111*300)/400)

    def test_old_prior_session_and_future_quote_are_not_current_metrics(self):
        rows = self.day_bars(NOW.date()-timedelta(days=8))
        self.underlying.source_at = NOW+timedelta(seconds=1)
        data = StockData(self.equity, self.underlying, rows, NOW).metrics()
        self.assertIsNone(data["metrics"]["last"]["value"])
        self.assertIsNone(data["metrics"]["previous_close"]["value"])

    def test_chart_requires_every_minute_for_1_5_15_and_keeps_gaps(self):
        rows = self.day_bars(NOW.date(), count=30)
        self.assertEqual(len(aggregate_chart(rows, 1)), 30)
        self.assertEqual(len(aggregate_chart(rows, 5)), 6)
        self.assertEqual(len(aggregate_chart(rows, 15)), 2)
        self.assertEqual(aggregate_chart(rows, 15)[0]["volume"], 1500)
        rows[6].complete = False
        self.assertEqual(len(aggregate_chart(rows, 5)), 5)
        self.assertEqual(len(aggregate_chart(rows, 15)), 1)
        self.assertEqual(aggregate_chart(rows, 15)[0]["time"], (session_start(NOW.date())+timedelta(minutes=15)).isoformat())
        self.assertEqual(aggregate_chart(rows[:4], 5), [])

    def test_watch_add_notes_remove_are_isolated_and_not_broker_actions(self):
        data = {"instrument_id": self.equity.pk, "action": "save", "notes": "<script>personal</script>", "watched_level": 103.5}
        self.assertEqual(self.post_json("radar_watch", data).status_code, 200)
        watch = RadarWatch.objects.get(user=self.user)
        self.assertEqual(watch.notes, data["notes"])
        self.assertEqual(watch.watched_level, 103.5)
        self.client.force_login(self.other)
        self.post_json("radar_watch", {"instrument_id": self.equity.pk, "action": "remove"})
        self.assertEqual(RadarWatch.objects.filter(user=self.user).count(), 1)
        self.assertEqual(workspace_payload(self.other, {"view": "watchlist"}, NOW)["rows"], [])
        self.client.force_login(self.user)
        self.assertEqual(len(workspace_payload(self.user, {"view": "watchlist"}, NOW)["rows"]), 1)
        self.post_json("radar_watch", {"instrument_id": self.equity.pk, "action": "add"})
        self.assertEqual(RadarWatch.objects.get(user=self.user).notes, data["notes"])
        self.post_json("radar_watch", {"instrument_id": self.equity.pk, "action": "remove"})
        self.assertFalse(RadarWatch.objects.filter(user=self.user).exists())

    def test_watch_invalid_inputs_are_explicit_errors(self):
        for extra in [{"notes": "x"*2001}, {"watched_level": float("nan")}, {"watched_level": -1},
                      {"watched_level": True}, {"notes": []}, {"action": "trade"}, {"instrument_id": True}]:
            response = self.post_json("radar_watch", {"instrument_id": self.equity.pk, "action": "save", **extra})
            self.assertEqual(response.status_code, 400, extra)
            self.assertIn("error", response.json())
        self.assertEqual(self.client.post(reverse("radar_watch"), "[1]", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(reverse("radar_watch"), "{", content_type="application/json").status_code, 400)
        self.assertEqual(self.post_json("radar_watch", {"instrument_id": self.option.pk, "action": "add"}).status_code, 404)

    def test_staff_post_and_csrf_required_for_watch_and_preferences(self):
        for name in ["radar_watch", "radar_preferences"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
            client = Client(enforce_csrf_checks=True)
            client.force_login(self.user)
            self.assertEqual(client.post(reverse(name), "{}", content_type="application/json").status_code, 403)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(reverse("radar_data")).status_code, 302)
        self.assertEqual(self.post_json("radar_watch", {"instrument_id": self.equity.pk, "action": "add"}).status_code, 302)

    def test_preferences_persist_all_controls_and_remain_private(self):
        state = preferences({
            "view": "watchlist", "symbol": "test", "q": "Test", "sort": "rvol", "strategy": "S1",
            "state": "ARMED", "interval": "15", "contract": str(self.option.pk), "overlays": "levels,vwap",
            "layout": "focus",
        })
        self.assertEqual(self.post_json("radar_preferences", state).status_code, 200)
        self.assertEqual(RadarWorkspace.objects.get(user=self.user).preferences, state)
        self.assertEqual(workspace_payload(self.user, {}, NOW)["state"]["interval"], "15")
        self.assertEqual(workspace_payload(self.other, {}, NOW)["state"]["interval"], "5")
        for invalid in [{"interval": "7"}, {"period": "FOREVER"}, {"chart_date": "2026-99-03"}, {"chart_date": "2099-01-01"},
                        {"chart_date": "20261103"}, {"overlays": "forecast"}, {"view": "orders"}, {"sort": "unknown"},
                        {"strategy": "S9"}, {"state": "BUY"}, {"contract": "NaN"}, {"q": "x"*81},
                        {"layout": "wide"}]:
            self.assertEqual(self.post_json("radar_preferences", invalid).status_code, 400)
        self.assertEqual(self.post_json("radar_preferences", {"interval": 5}).status_code, 400)
        self.assertEqual(self.post_json("radar_preferences", {"unexpected": "field"}).status_code, 400)

    @patch("options_tracker.radar_views.timezone.now", return_value=NOW)
    @patch("options_tracker.services.get_dhan_credentials")
    @patch("options_tracker.radar.scan")
    @patch("options_tracker.telegram_bot.send")
    def test_get_is_read_only_no_broker_scan_delivery_or_preference_mutation(self, send, scan, credentials, _clock):
        item = self.eligible(created_at=NOW)
        response = self.client.get(reverse("radar_data"), {"symbol": "TEST", "contract": str(self.option.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json()["selected"]["symbol"], "TEST")
        item.refresh_from_db()
        self.assertEqual(item.state, S.ELIGIBLE)
        self.assertFalse(RadarWorkspace.objects.exists())
        self.assertFalse(RadarWatch.objects.exists())
        send.assert_not_called()
        scan.assert_not_called()
        credentials.assert_not_called()

    def test_candidate_selection_does_not_change_pinned_contract(self):
        item = self.eligible(created_at=NOW)
        put = RadarInstrument.objects.get(side="PE")
        RadarWatch.objects.create(user=self.user, instrument=self.equity)
        data = workspace_payload(self.user, {"symbol": "TEST", "contract": str(put.pk)}, NOW)
        self.assertEqual(data["state"]["contract"], str(put.pk))
        item.refresh_from_db()
        self.assertEqual(item.contract_id, self.option.pk)
        self.assertEqual(data["selected"]["oi_context"]["full_chain_pcr"], None)
        self.assertIn("not a full option chain", data["selected"]["oi_context"]["reason"])
        self.assertTrue(all(v is None for v in data["selected"]["candidates"][0]["greeks"].values()))

    def test_future_and_stale_quotes_block_actions_and_candidates(self):
        item = self.eligible(created_at=NOW)
        self.quote.source_at = NOW+timedelta(seconds=1)
        self.quote.save()
        data = opportunity_payload(item, NOW)
        self.assertFalse(data["can_acknowledge"])
        self.assertTrue(data["action_block"])
        candidates = option_candidates(self.equity, 100, [item], NOW)
        self.assertIn("future-dated", next(c for c in candidates if c["id"] == self.option.pk)["rejection"])
        self.quote.source_at = NOW-timedelta(seconds=16)
        self.quote.save()
        self.assertFalse(opportunity_payload(item, NOW)["can_acknowledge"])

    def test_portfolio_uses_costs_and_groups_versions(self):
        item = self.eligible(created_at=NOW)
        held = acknowledge(item.pk, "take", NOW)
        data = opportunity_payload(held, NOW)
        expected = (self.quote.bid-held.entry_price)*held.quantity-estimate_option_charges(held.entry_price, self.quote.bid, held.quantity, NOW.date())
        self.assertAlmostEqual(data["net_unrealised"], expected)
        self.assertGreater(data["planned_stop_risk"], (held.entry_price-data["emergency_stop"])*held.quantity)
        held.state, held.closed_at, held.net_pnl = S.EXITED, NOW, -100
        held.save()
        self.opportunity(key="other", state=S.EXITED, version="experimental-v2", net_pnl=200, closed_at=NOW, created_at=NOW)
        report = portfolio(NOW)
        self.assertEqual(report["net_realised"], 100)
        self.assertEqual(len(report["performance"]), 2)
        self.assertEqual(report["performance"][0]["win_rate"], 0)
        self.assertEqual(report["performance"][1]["win_rate"], 100)

    def test_missing_bid_depth_never_creates_unrealised_pnl(self):
        item = self.eligible(created_at=NOW)
        held = acknowledge(item.pk, "take", NOW)
        self.quote.bid_qty = 0
        self.quote.save()
        data = opportunity_payload(held, NOW)
        self.assertIsNone(data["net_unrealised"])
        self.assertIsNone(data["executable_bid"])
        self.quote.bid_qty, self.quote.ask = 500, self.quote.bid-.1
        self.quote.save()
        self.assertIsNone(opportunity_payload(held, NOW)["executable_bid"])

    def test_benchmark_requires_fresh_current_and_aligned_previous_session(self):
        self.history()
        previous = IndexOISnapshot.objects.create(underlying="NIFTY", underlying_price=100)
        current = IndexOISnapshot.objects.create(underlying="NIFTY", underlying_price=102)
        IndexOISnapshot.objects.filter(pk=previous.pk).update(created_at=NOW.replace(day=2, hour=15, minute=35))
        IndexOISnapshot.objects.filter(pk=current.pk).update(created_at=NOW)
        context = market_context(NOW)
        self.assertAlmostEqual(context["NIFTY"]["change_pct"], 2)
        data = workspace_payload(self.user, {}, NOW)
        self.assertAlmostEqual(data["selected"]["metrics"]["market_strength"]["value"], -1.9)
        IndexOISnapshot.objects.filter(pk=previous.pk).update(created_at=NOW.replace(day=1, hour=15, minute=35))
        self.assertIsNone(workspace_payload(self.user, {}, NOW)["selected"]["metrics"]["market_strength"]["value"])
        self.assertIsNone(market_context(NOW+timedelta(seconds=16))["NIFTY"]["change_pct"])

    def test_stock_filters_never_show_an_unrelated_selected_chart(self):
        self.opportunity(created_at=NOW)
        for query in [{"view": "radar"}, {"strategy": "S1", "state": "TRIGGERED"}, {"q": "test"}]:
            data = workspace_payload(self.user, query, NOW)
            self.assertEqual(len(data["rows"]), 1)
            self.assertEqual(data["selected"]["symbol"], "TEST")
        self.assertEqual(workspace_payload(self.user, {"view": "positions"}, NOW)["rows"], [])
        self.assertEqual(workspace_payload(self.user, {"q": "NOTFOUND"}, NOW)["rows"], [])
        data = workspace_payload(self.user, {"q": "NOTFOUND", "symbol": "TEST"}, NOW)
        self.assertIsNone(data["selected"])
        self.assertEqual(data["state"]["symbol"], "")
        self.assertEqual(len(data["all_rows"]), 1)

    def test_chart_history_ranges_and_dates_use_recorded_bars_only(self):
        self.history()
        current = workspace_payload(self.user, {"symbol": "TEST"}, NOW)["selected"]
        history = workspace_payload(self.user, {"symbol": "TEST", "period": "5D"}, NOW)["selected"]
        self.assertEqual(len(current["chart"]), 22)
        self.assertGreater(len(history["chart"]), len(current["chart"]))
        self.assertLess(history["chart"][0]["time"][:10], NOW.date().isoformat())
        old = workspace_payload(self.user, {"chart_date": "2026-11-02", "interval": "25"}, NOW)["selected"]
        self.assertEqual(len(old["chart"]), 14)
        self.assertTrue(all(b["time"][:10] == "2026-11-02" for b in old["chart"]))
        missing = workspace_payload(self.user, {"chart_date": "2025-01-01"}, NOW)["selected"]
        self.assertEqual(missing["chart"], [])
        self.assertIsNone(missing["chart_range"]["first_available"])
        with self.assertRaisesMessage(ValueError, "future"):
            workspace_payload(self.user, {"chart_date": "2026-11-04"}, NOW)

    def test_daily_weekly_monthly_aggregate_only_complete_recorded_sessions(self):
        rows = self.history()
        daily = historical_chart(rows, "D", "EQUITY")
        self.assertEqual(len(daily), 5)
        self.assertEqual(daily[-1]["volume"], 36000)
        self.assertIsNone(daily[-1]["rsi"])
        weekly = historical_chart(rows, "W", "EQUITY")
        self.assertEqual(sum(b["observed_sessions"] for b in weekly), 5)
        monthly = historical_chart(rows, "M", "EQUITY")
        self.assertEqual(sum(b["volume"] for b in monthly), 180000)
        rows[0].complete = False
        self.assertEqual(len(historical_chart(rows, "D", "EQUITY")), 4)

    def test_cached_display_inputs_do_not_cache_quote_freshness_or_actions(self):
        from django.core.cache import cache
        cache.clear()
        self.history()
        first = table_metrics([self.equity], {self.equity.pk: self.underlying}, NOW, cached=True)
        self.underlying.last = 102
        with CaptureQueriesContext(connection) as queries:
            second = table_metrics([self.equity], {self.equity.pk: self.underlying}, NOW, cached=True)
        self.assertEqual(len(queries), 0)
        self.assertNotEqual(first["TEST"]["change_pct"], second["TEST"]["change_pct"])
        self.underlying.source_at = NOW+timedelta(seconds=1)
        self.assertIsNone(table_metrics([self.equity], {self.equity.pk: self.underlying}, NOW, cached=True)["TEST"]["change_pct"])
        cache.clear()

    def test_midnight_review_preserves_complete_session_metrics_and_near_money_options(self):
        self.history()
        start = session_start(NOW.date())+timedelta(minutes=110)
        RadarBar.objects.bulk_create([RadarBar(
            instrument=self.equity, timestamp=start+timedelta(minutes=i), open=100, high=101,
            low=99, close=100, volume=200, complete=True, source="TEST_SYNTHETIC",
        ) for i in range(250)])
        RadarInstrument.objects.create(kind="OPTION", symbol="TEST", segment="NSE_FNO",
                                       security_id="899", expiry=self.option.expiry, strike=50, side="CE", lot_size=100, active=True)
        midnight = NOW.replace(day=4, hour=1, minute=3)
        data = workspace_payload(self.user, {"symbol": "TEST", "interval": "15"}, midnight)
        self.assertTrue(data["session"]["review"])
        self.assertEqual(data["session"]["day"], "2026-11-03")
        self.assertEqual(data["rows"][0]["display_status"], "SESSION REVIEW")
        self.assertEqual(data["rows"][0]["display_price"], 100)
        self.assertEqual(data["rows"][0]["change_pct"], 0)
        self.assertEqual(data["rows"][0]["rvol"], 2)
        stock = data["selected"]
        self.assertEqual(stock["metrics"]["volume"]["value"], 72000)
        self.assertEqual(stock["metrics"]["last"]["value"], 100)
        self.assertEqual(stock["chart_range"]["to"], "2026-11-03")
        self.assertFalse(stock["quote"]["fresh"])
        self.assertEqual(stock["candidates"][0]["strike"], 100)
        self.assertEqual(stock["decision"]["status"], "SESSION REVIEW - NO LIVE ENTRY")
        self.assertEqual(stock["option_reference"]["basis"], "Recorded session close")

    def test_missing_underlying_reference_never_ranks_lowest_strikes_as_near_money(self):
        self.assertEqual(option_candidates(self.equity, None, [], NOW), [])

    def test_weekend_and_preopen_review_use_observed_session_not_a_fictional_day(self):
        self.history()
        for day, hour in [(4, 8), (7, 12), (8, 12)]:
            with self.subTest(day=day, hour=hour):
                data = workspace_payload(self.user, {"symbol": "TEST"}, NOW.replace(day=day, hour=hour))
                self.assertTrue(data["session"]["review"])
                self.assertEqual(data["session"]["day"], "2026-11-03")
                self.assertEqual(data["selected"]["chart_range"]["to"], "2026-11-03")
                self.assertFalse(data["selected"]["quote"]["fresh"])
                self.assertEqual(data["selected"]["decision"]["status"], "SESSION REVIEW - NO LIVE ENTRY")

    def test_empty_overnight_history_has_no_fictional_session_or_near_money_reference(self):
        RadarQuote.objects.all().delete()
        data = workspace_payload(self.user, {"symbol": "TEST"}, NOW.replace(day=4, hour=1))
        self.assertIsNone(data["session"]["day"])
        self.assertIsNone(data["selected"]["metrics"]["last"]["value"])
        self.assertEqual(data["selected"]["candidates"], [])

    def test_incomplete_last_session_review_does_not_fabricate_metrics(self):
        self.history()
        midnight = NOW.replace(day=4, hour=1)
        data = workspace_payload(self.user, {"symbol": "TEST"}, midnight)
        self.assertEqual(data["rows"][0]["display_status"], "INCOMPLETE SESSION")
        self.assertIsNone(data["selected"]["metrics"]["last"]["value"])
        self.assertIsNone(data["rows"][0]["display_price"])
        self.assertFalse(data["selected"]["quote"]["fresh"])

    def test_watchlist_expands_option_feed_without_waiting_for_strategy(self):
        self.assertEqual(len(subscriptions(NOW)), 1)
        RadarWatch.objects.create(user=self.user, instrument=self.equity)
        self.assertEqual(len(subscriptions(NOW)), 3)

    def test_packet_oi_and_ohlc_are_actual_fields_not_synthetic_prior_close(self):
        packet = bytearray(162)
        struct.pack_into("<BHBI", packet, 0, 8, 162, 2, 900)
        struct.pack_into("<fHI", packet, 8, 10.0, 100, int(NOW.replace(tzinfo=dt_timezone.utc).timestamp()))
        struct.pack_into("<I", packet, 22, 10000)
        struct.pack_into("<I", packet, 34, 123456)
        struct.pack_into("<ffff", packet, 46, 9.0, 8.5, 11.0, 8.0)
        struct.pack_into("<IIHHff", packet, 62, 500, 600, 2, 3, 9.95, 10.05)
        tick = decode_packets(bytes(packet), NOW)[0]
        self.assertEqual((tick.oi, tick.day_open, tick.day_high, tick.day_low), (123456, 9, 11, 8))
        self.assertIsNone(tick.previous_close)

    def test_option_history_is_sampled_immutable_and_preserves_receipt_time(self):
        collector = CandleCollector()
        tick = Tick("NSE_FNO", self.option.security_id, NOW, NOW, 10, 10000, 9.9, 10.1, 500, 500, oi=1234)
        for seconds in [0, 5, 14, 15, 20, 30]:
            collector.ingest(self.option, replace(tick, source_at=NOW+timedelta(seconds=seconds), received_at=NOW+timedelta(seconds=seconds), last=10+seconds/100))
        self.assertEqual(RadarOptionPoint.objects.count(), 3)
        self.assertEqual(RadarOptionPoint.objects.first().last, 10)
        self.assertEqual(RadarOptionPoint.objects.first().oi, 1234)
        self.assertEqual(RadarOptionPoint.objects.first().received_at, NOW)
        self.assertEqual(len(workspace_payload(self.user, {"contract": str(self.option.pk)}, NOW+timedelta(seconds=30))["selected"]["option_history"]), 3)
        restarted = CandleCollector()
        restarted.ingest(self.option, replace(tick, source_at=NOW+timedelta(seconds=35), received_at=NOW+timedelta(seconds=35)))
        self.assertEqual(RadarOptionPoint.objects.count(), 3)

    def test_broker_day_range_takes_precedence_over_observed_prefix(self):
        rows = self.history()
        self.underlying.day_high, self.underlying.day_low = 105, 95
        data = StockData(self.equity, self.underlying, rows, NOW).metrics()["metrics"]
        self.assertEqual(data["high"]["value"], 105)
        self.assertEqual(data["low"]["value"], 95)

    def test_expired_contract_remains_in_audit_not_live_candidates_or_marks(self):
        item = self.eligible(created_at=NOW)
        held = acknowledge(item.pk, "take", NOW)
        self.option.expiry = NOW.date()-timedelta(days=1)
        self.option.save()
        held.contract = self.option
        self.assertNotIn(self.option.pk, [c["id"] for c in option_candidates(self.equity, 100, [held], NOW)])
        data = opportunity_payload(held, NOW)
        self.assertIsNone(data["net_unrealised"])
        self.assertIsNone(data["executable_bid"])

    def test_sorting_and_filters_use_numeric_values_not_display_strings(self):
        self.history()
        second = RadarInstrument.objects.create(symbol="SECOND", name="Second", security_id="200", segment="NSE_EQ", kind="EQUITY", refreshed_at=NOW)
        second_quote = RadarQuote.objects.create(instrument=second, last=90, source_at=NOW, received_at=NOW)
        old_equity = self.equity
        self.equity = second
        self.history(current_volume=900)
        self.equity = old_equity
        data = workspace_payload(self.user, {"sort": "rvol"}, NOW)
        self.assertEqual([r["symbol"] for r in data["rows"]], ["SECOND", "TEST"])
        data = workspace_payload(self.user, {"sort": "change"}, NOW)
        self.assertEqual([r["symbol"] for r in data["rows"]], ["TEST", "SECOND"])
        second_quote.source_at = NOW+timedelta(seconds=1)
        second_quote.save()
        self.assertIsNone(workspace_payload(self.user, {}, NOW)["rows"][0]["change_pct"])

    def test_future_and_unreceived_option_samples_are_excluded(self):
        for source, receipt in [(NOW, NOW), (NOW+timedelta(seconds=1), NOW+timedelta(seconds=1)), (NOW, NOW+timedelta(seconds=1))]:
            # Unique source timestamps per contract; receipt can still be in the future.
            if source == NOW and receipt > NOW:
                source -= timedelta(seconds=1)
            RadarOptionPoint.objects.create(instrument=self.option, timestamp=source, received_at=receipt, last=10, bid=9.9, ask=10.1, volume=100, oi=1000)
        data = workspace_payload(self.user, {"contract": str(self.option.pk)}, NOW)
        self.assertEqual(len(data["selected"]["option_history"]), 1)

    def indicator_rows(self, closes):
        start = session_start(NOW.date())
        return [RadarBar(
            instrument=self.equity, timestamp=start+timedelta(minutes=i),
            open=price, high=price+.1, low=price-.1, close=price,
            volume=100+i, complete=True,
        ) for i, price in enumerate(closes)]

    def test_wilder_rsi_known_reference_and_recursive_smoothing(self):
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
        chart = indicator_chart(self.indicator_rows(closes), 1)
        self.assertTrue(all(b["rsi"] is None for b in chart[:14]))
        self.assertAlmostEqual(chart[14]["rsi"], 70.46413502109705)
        self.assertAlmostEqual(chart[15]["rsi"], 66.24961855355505)
        self.assertTrue(all(b["rsi_ma"] is None for b in chart))

    def test_rsi_sma_is_nine_rsi_values_not_nine_prices(self):
        closes = [100+i*.2+(.8 if i % 3 == 0 else 0) for i in range(30)]
        chart = indicator_chart(self.indicator_rows(closes), 1)
        self.assertIsNone(chart[21]["rsi_ma"])
        self.assertAlmostEqual(chart[22]["rsi_ma"], sum(b["rsi"] for b in chart[14:23])/9)
        self.assertAlmostEqual(chart[-1]["rsi_ma"], sum(b["rsi"] for b in chart[-9:])/9)
        # Appending later bars cannot change an earlier RSI.
        self.assertEqual(chart[:23], indicator_chart(self.indicator_rows(closes[:23]), 1))

    def test_rsi_extremes_and_flat_seed_are_explicit(self):
        for closes, expected in [
            ([100+i for i in range(23)], 100),
            ([100-i for i in range(23)], 0),
            ([100]*23, 50),
        ]:
            chart = indicator_chart(self.indicator_rows(closes), 1)
            self.assertEqual(chart[14]["rsi"], expected)
            self.assertEqual(chart[22]["rsi_ma"], expected)

    def test_rsi_restarts_at_missing_incomplete_and_session_boundaries(self):
        for incomplete in [True, False]:
            rows = self.indicator_rows([100+i for i in range(46)])
            if incomplete:
                rows[20].complete = False
            else:
                del rows[20]
            chart = indicator_chart(rows, 1)
            self.assertEqual(chart[19]["rsi"], 100)
            self.assertTrue(all(b["rsi"] is None for b in chart if rows[0].timestamp+timedelta(minutes=21) <= datetime.fromisoformat(b["time"]) < rows[0].timestamp+timedelta(minutes=35)))
            self.assertEqual(next(b["rsi"] for b in chart if b["time"] == (rows[0].timestamp+timedelta(minutes=35)).isoformat()), 100)
        previous = self.indicator_rows([100+i for i in range(20)])
        for row in previous:
            row.timestamp -= timedelta(days=1)
        chart = indicator_chart(previous+self.indicator_rows([100+i for i in range(15)]), 1)
        self.assertIsNone(chart[20]["rsi"])
        self.assertEqual(chart[-1]["rsi"], 100)

    def test_rsi_uses_selected_aggregation_not_minute_rsi_average(self):
        rows = self.indicator_rows([100+i*.1+(1 if i % 7 == 0 else 0) for i in range(115)])
        for interval in [1, 5, 15]:
            chart = indicator_chart(rows, interval)
            self.assertEqual(len(chart), len(rows)//interval)
            self.assertTrue(all(b["rsi"] is None for b in chart[:14]))
            if len(chart) >= 15:
                changes = [b["close"]-a["close"] for a, b in zip(chart[:14], chart[1:15])]
                gain, loss = sum(max(c, 0) for c in changes)/14, sum(max(-c, 0) for c in changes)/14
                expected = 100 if loss == 0 else 100-100/(1+gain/loss)
                self.assertAlmostEqual(chart[14]["rsi"], expected)

    def test_option_candles_and_volume_are_collected_not_inferred_from_samples(self):
        collector = CandleCollector()
        offsets = [5, 30, 55, 60, 85, 110, 120, 145, 170]
        for index, seconds in enumerate(offsets):
            at = NOW+timedelta(seconds=seconds)
            collector.ingest(self.option, Tick("NSE_FNO", self.option.security_id, at, at,
                                             10+index/10, 10000+index*10, 9.9, 10.1, 500, 500))
        collector.flush(NOW+timedelta(minutes=3))
        rows = list(RadarBar.objects.filter(instrument=self.option))
        self.assertEqual(len(rows), 3)
        self.assertFalse(rows[0].complete)
        self.assertTrue(rows[1].complete)
        self.assertEqual((rows[1].open, rows[1].high, rows[1].low, rows[1].close, rows[1].volume),
                         (10.3, 10.5, 10.3, 10.5, 30))
        data = workspace_payload(self.user, {"contract": str(self.option.pk), "interval": "1"}, NOW+timedelta(minutes=3))["selected"]
        self.assertEqual(len(data["option_chart"]), 2)
        self.assertEqual(data["option_chart_source"], ["DHAN_STREAM"])
        self.assertTrue(all(b["rsi"] is None for b in data["option_chart"]))

    def test_option_rsi_uses_selected_contract_and_never_uses_premium_samples(self):
        self.history()
        put = RadarInstrument.objects.get(side="PE")
        for instrument, sign in [(self.option, 1), (put, -1)]:
            rows = self.indicator_rows([100+sign*i for i in range(23)])
            for row in rows:
                row.instrument = instrument
            RadarBar.objects.bulk_create(rows)
        for instrument, expected in [(self.option, 100), (put, 0)]:
            data = workspace_payload(self.user, {"contract": str(instrument.pk), "interval": "1"}, NOW)["selected"]
            self.assertEqual(data["option_chart"][-1]["rsi"], expected)
            self.assertEqual(data["option_chart"][-1]["rsi_ma"], expected)
            self.assertEqual(data["option_history"], [])
        RadarBar.objects.filter(instrument=self.option).delete()
        RadarOptionPoint.objects.create(instrument=self.option, timestamp=NOW, received_at=NOW,
                                       last=10, bid=9.9, ask=10.1, volume=100, oi=1000)
        data = workspace_payload(self.user, {"contract": str(self.option.pk)}, NOW)["selected"]
        self.assertEqual(data["option_chart"], [])
        self.assertEqual(len(data["option_history"]), 1)

    def test_forming_option_minute_cannot_enter_rsi(self):
        rows = self.indicator_rows([100+i for i in range(15)])
        now = rows[-1].timestamp+timedelta(seconds=30)
        RadarQuote.objects.filter(pk=self.underlying.pk).update(source_at=now, received_at=now)
        for row in rows:
            row.instrument = self.option
        RadarBar.objects.bulk_create(rows)
        data = workspace_payload(self.user, {"contract": str(self.option.pk), "interval": "1"}, now)["selected"]
        self.assertEqual(len(data["option_chart"]), 14)
        self.assertTrue(all(b["rsi"] is None for b in data["option_chart"]))

    def test_option_gap_disconnect_and_volume_reset_invalidate_candles(self):
        for failure in ["gap", "disconnect", "volume_reset"]:
            with self.subTest(failure=failure):
                RadarBar.objects.filter(instrument=self.option).delete()
                RadarQuote.objects.filter(instrument=self.option).delete()
                collector = CandleCollector()
                offsets = [5, 30, 55, 60, 85, 110]
                for index, seconds in enumerate(offsets):
                    if failure == "gap" and seconds == 85:
                        continue
                    if failure == "disconnect" and seconds == 85:
                        collector.disconnect()
                    at = NOW+timedelta(seconds=seconds)
                    volume = 100 if failure == "volume_reset" and seconds >= 85 else 10000+index*10
                    collector.ingest(self.option, Tick("NSE_FNO", self.option.security_id, at, at,
                                                     10, volume, 9.9, 10.1, 500, 500))
                collector.flush(NOW+timedelta(minutes=2))
                self.assertFalse(RadarBar.objects.get(instrument=self.option, timestamp=NOW+timedelta(minutes=1)).complete)

    @patch("options_tracker.radar_views.timezone.now", return_value=NOW)
    def test_acknowledgement_preserves_stock_but_never_redirects_external(self, _clock):
        item = self.eligible(created_at=NOW)
        response = self.client.post(reverse("radar_acknowledge", args=[item.pk]), {"action": "skip", "symbol": "TEST", "next": "https://evil.example"})
        self.assertEqual(response.url, reverse("radar")+"?symbol=TEST&view=all&q=&strategy=&state=")
