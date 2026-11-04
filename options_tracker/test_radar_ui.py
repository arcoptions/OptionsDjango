"""Real Chromium gestures against an isolated Django live-server database."""

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from time import perf_counter
import re
from unittest.mock import patch
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.db import connections
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from .models import (
    ChartinkTrigger, ChatMessage, Direction, IndexOISnapshot, RadarAssistantSnapshot,
    RadarBar, RadarInstrument, RadarNotificationRoute, RadarOptionPoint,
    RadarPriceAlert, RadarWatch, SignalStatus, TipSignal, TriggerStatus,
)
from .radar import S
from . import test_radar as fixtures
from . import test_radar_workspace as workspace_fixtures


@override_settings(
    DEBUG=True,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class RadarUITests(StaticLiveServerTestCase):
    def setUp(self):
        cache.clear()
        self.clock = patch("options_tracker.radar_views.timezone.now", return_value=fixtures.NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.no_send = patch("options_tracker.telegram_bot.send", side_effect=AssertionError("UI tests must not send Telegram messages"))
        self.no_send.start()
        self.addCleanup(self.no_send.stop)
        fixtures.RadarTestCase.setUp(self)
        self.user = get_user_model().objects.create_user("ui-staff", is_staff=True)
        RadarNotificationRoute.objects.create(user=self.user, telegram_chat_id="test-only-recipient")
        workspace_fixtures.WorkspaceTests.history(self)
        RadarBar.objects.bulk_create([
            RadarBar(instrument=self.option, timestamp=row.timestamp, open=10, high=11, low=9,
                     close=10, volume=100, complete=True, source="TEST_SYNTHETIC")
            for row in RadarBar.objects.filter(instrument=self.equity)
        ])
        self.second = RadarInstrument.objects.create(
            kind="EQUITY", segment="NSE_EQ", security_id="124", symbol="ZETA",
            name="Zeta Synthetic Test", active=True,
        )
        snapshot = IndexOISnapshot.objects.create(underlying="NIFTY", underlying_price=100, underlying_change=1)
        IndexOISnapshot.objects.filter(pk=snapshot.pk).update(created_at=fixtures.NOW)
        self.client.force_login(self.user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.database = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.database.shutdown)
        self.addCleanup(lambda: self.db(connections.close_all))
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(headless=True)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.addCleanup(self.context.close)
        self.context.add_cookies([{
            "name": settings.SESSION_COOKIE_NAME, "value": session_cookie,
            "domain": urlparse(self.live_server_url).hostname, "path": "/",
        }])
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.script_errors = []
        self.page.on("pageerror", lambda error: self.script_errors.append(str(error)))

    day_bars = workspace_fixtures.WorkspaceTests.day_bars
    opportunity = fixtures.RadarTestCase.opportunity
    eligible = fixtures.RadarTestCase.eligible

    def db(self, operation):
        # Playwright owns an asyncio loop on this thread; ORM work stays on a separate synchronous thread.
        return self.database.submit(operation).result()

    def tearDown(self):
        self.assertEqual(self.script_errors, [], "Browser JavaScript errors")
        super().tearDown()

    def open(self, **query):
        self.page.goto(self.live_server_url+reverse("radar")+"?"+urlencode(query), wait_until="networkidle")
        expect(self.page.locator("#stock-heading")).to_contain_text("TEST")
        expect(self.page.locator("#request-error")).to_be_hidden()

    def data_action(self, action):
        with self.page.expect_response(lambda response: urlparse(response.url).path == reverse("radar_data")) as response:
            action()
        self.assertEqual(response.value.status, 200, response.value.text()[:200])
        expect(self.page.locator("#workspace-loading")).to_have_text("")
        expect(self.page.locator("#request-error")).to_be_hidden()

    def open_drawer(self, selector):
        drawer = self.page.locator(selector)
        drawer.evaluate("element => element.open = true")
        expect(drawer).to_have_attribute("open", "")

    def test_discovery_filters_tabs_sort_and_no_unrelated_chart(self):
        self.db(self.opportunity)
        self.open()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(2)
        self.page.locator("#state").select_option("TRIGGERED")
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        self.page.locator("#search").fill("NOTFOUND")
        expect(self.page.locator("#stocks .stock-link")).to_have_count(0)
        expect(self.page.locator("#stock-heading")).to_contain_text("No selected")
        expect(self.page.locator("#stock-chart svg")).to_have_count(0)
        expect(self.page.locator("#price-alert-form [type=submit]")).to_be_disabled()
        self.data_action(lambda: self.page.get_by_role("button", name="All F&O", exact=True).click())
        expect(self.page.locator("#state")).to_have_value("")
        expect(self.page.locator("#search")).to_have_value("")
        expect(self.page.locator("#stocks .stock-link")).to_have_count(2)
        for key in ["change", "rvol", "distance", "symbol"]:
            self.page.locator("#sort").select_option(key)
            expect(self.page.locator("#sort")).to_have_value(key)
        self.page.locator("#strategy").select_option("S3")
        expect(self.page.locator("#stocks .stock-link")).to_have_count(0)
        self.data_action(lambda: self.page.locator("#clear-filters").click())
        expect(self.page.locator("#stocks .stock-link")).to_have_count(2)
        self.page.get_by_role("button", name="Strategy radar", exact=True).click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        self.page.get_by_role("button", name="Positions", exact=True).click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(0)

    def test_consolidated_action_queue_filters_sources_search_and_decisions(self):
        def action_rows():
            TipSignal.objects.create(
                source_type="TELEGRAM", source_name="Reviewed feed",
                option_symbol="TEST 100 CE", direction=Direction.CE,
                entry_price=Decimal("10"), stop_loss=Decimal("8"),
                target_1=Decimal("15"), expiry_date=fixtures.NOW.date()+timedelta(days=1),
                score=80, recommendation="STRONG GO", status=SignalStatus.CANDIDATE,
                tip_time=fixtures.NOW,
            )
            ChartinkTrigger.objects.create(
                scanner_name="Momentum scan", symbol="ZETA",
                trigger_price=Decimal("99"), live_price=Decimal("100"),
                status=TriggerStatus.MONITORING, trigger_time=fixtures.NOW,
            )
            ChatMessage.objects.create(
                source_name="Chartsays", source_category="TIPS",
                raw_text="Watch ALPHA for momentum", normalized_text="Watch ALPHA for momentum",
                is_tip_candidate=True,
            )

        self.db(action_rows)
        self.open()

        cards = self.page.locator("#exception-queue .exception-card:visible")
        expect(cards).to_have_count(3)
        self.page.get_by_role("button", name="Scanners", exact=True).click()
        expect(cards).to_have_count(1)
        expect(cards).to_contain_text("ZETA")
        self.page.locator('[data-source-filter="all"]').click()
        self.page.locator("#action-search").fill("Chartsays")
        expect(cards).to_have_count(1)
        expect(cards).to_contain_text("ALPHA")
        self.page.locator("#action-search").fill("")
        self.page.locator('[data-action-filter="BUY"]').click()
        expect(cards).to_have_count(1)
        expect(cards).to_contain_text("TEST 100 CE")
        cards.first.focus()
        cards.first.press("Enter")
        expect(self.page.locator("#stock-heading")).to_contain_text("TEST")
        self.page.locator('[data-action-filter="ALL"]').click()
        cards.first.focus()
        cards.first.press("ArrowDown")
        self.assertEqual(self.page.evaluate("document.activeElement.classList.contains('exception-card')"), True)
        expect(self.page.locator("#health-exceptions")).to_have_text("3")
        expect(self.page.locator("#health-cas")).not_to_have_text("Loading")

    def test_overnight_review_has_dated_chart_decision_and_non_executable_catalogue(self):
        def finish_session():
            RadarBar.objects.filter(instrument=self.equity, timestamp__date=fixtures.NOW.date()).delete()
            self.day_bars(fixtures.NOW.date(), count=360)
        with patch("options_tracker.radar_views.timezone.now", return_value=fixtures.NOW.replace(day=4, hour=1, minute=3)):
            self.db(finish_session)
            self.open(interval="15")
            expect(self.page.locator("#session-status")).to_contain_text("Market closed")
            expect(self.page.locator("#session-status")).to_contain_text("2026-11-03")
            expect(self.page.locator("#stock-heading")).to_contain_text("REVIEW ONLY")
            expect(self.page.locator("#decision-desk")).to_contain_text("NO LIVE ENTRY")
            expect(self.page.locator("#stocks")).to_contain_text("SESSION REVIEW")
            expect(self.page.locator("#chart-range")).to_contain_text("2026-11-03")
            expect(self.page.locator("#stock-chart svg")).to_have_count(1)
            expect(self.page.locator("#option-readiness")).to_contain_text("No executable option recommendation")
            self.open_drawer("details.contract-drawer")
            self.assertFalse(self.page.locator("#contract-catalogue").evaluate("element=>element.open"))
            self.page.get_by_text("Analytics contract catalogue (not executable recommendations)", exact=True).click()
            expect(self.page.locator("#contracts button").first).to_be_visible()
            expect(self.page.get_by_role("button", name="Take PAPER position", exact=True)).to_have_count(0)
            self.page.set_viewport_size({"width": 390, "height": 844})
            expect(self.page.locator("#decision-desk")).to_contain_text("NO LIVE ENTRY")

    def test_stock_selection_fast_local_filters_and_unsaved_notes_guard(self):
        self.open()
        self.open_drawer("details.market-drawer")
        responses = []
        self.page.on("request", lambda request: responses.append(request.url) if urlparse(request.url).path == reverse("radar_data") else None)
        started = perf_counter()
        self.page.locator("#search").fill("Test Ltd")
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        self.assertLess(perf_counter()-started, 1.5)
        self.assertEqual(responses, [], "Changing discovery filters should not recompute all stocks")
        self.page.locator("#clear-filters").click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(2)
        self.page.locator("#notes").fill("Unsaved test note")
        self.page.locator("#stocks .stock-link").filter(has_text="ZETA").click()
        expect(self.page.locator("#request-error")).to_contain_text("Save")
        expect(self.page.locator("#stock-heading")).to_contain_text("TEST")
        self.page.locator("#notes-form [type=submit]").click()
        expect(self.page.locator("#notes-status")).to_contain_text("Saved")
        self.data_action(lambda: self.page.locator("#stocks .stock-link").filter(has_text="ZETA").click())
        expect(self.page.locator("#stock-heading")).to_contain_text("ZETA")
        expect(self.page.locator("#stock-chart")).to_contain_text("No complete")
        self.data_action(lambda: self.page.locator("#stocks .stock-link").filter(has_text="TEST").click())
        expect(self.page.locator("#stock-chart svg")).to_have_count(1)

    def test_filter_changes_during_an_inflight_chart_request_cancel_the_old_view(self):
        self.open()
        held, failed = [], []
        def delayed_first_request(route):
            if not held:
                held.append(route)
            else:
                route.continue_()
        self.page.route("**"+reverse("radar_data")+"?*", delayed_first_request)
        self.page.on("requestfailed", lambda request: failed.append(request.url))
        with self.page.expect_request(lambda request: urlparse(request.url).path == reverse("radar_data")):
            self.page.locator("#interval").select_option("15")
        expect(self.page.locator("#workspace-loading")).to_contain_text("Loading")
        self.data_action(lambda: self.page.locator("#search").fill("Test Ltd"))
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        expect(self.page.locator("#search")).to_have_value("Test Ltd")
        expect(self.page.locator("#interval")).to_have_value("15")
        expect(self.page.locator("#stock-heading")).to_contain_text("TEST")
        self.assertTrue(failed, "The superseded in-flight chart fetch must be aborted")
        held[0].abort()

    def test_watch_stars_selected_control_notes_reload_and_user_isolation(self):
        self.open()
        self.open_drawer("details.market-drawer")
        self.page.locator("#selected-watch").click()
        expect(self.page.locator("#selected-watch")).to_have_attribute("aria-pressed", "true")
        self.assertTrue(self.db(lambda: RadarWatch.objects.filter(user=self.user, instrument=self.equity).exists()))
        self.page.get_by_role("button", name="Watchlist", exact=True).click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        self.page.locator("#notes").fill("Observe a reclaim; do not chase.")
        self.page.locator("#watched-level").fill("100.50")
        self.page.locator("#notes-form [type=submit]").click()
        expect(self.page.locator("#notes-status")).to_contain_text("Saved")
        self.page.reload(wait_until="networkidle")
        self.open_drawer("details.market-drawer")
        expect(self.page.locator("#notes")).to_have_value("Observe a reclaim; do not chase.")
        expect(self.page.locator("#watched-level")).to_have_value("100.5")
        self.page.locator("#watched-level").fill("")
        with self.page.expect_response(lambda response: response.url.endswith(reverse("radar_watch"))):
            self.page.locator("#notes-form [type=submit]").click()
        expect(self.page.locator("#watched-level")).to_have_value("")
        self.assertIsNone(self.db(lambda: RadarWatch.objects.get(user=self.user, instrument=self.equity).watched_level))
        self.page.locator("#stocks .watch-toggle").click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(0)
        self.data_action(lambda: self.page.locator("#clear-filters").click())
        self.page.locator("#stocks .watch-toggle").first.click()
        expect(self.page.locator("#selected-watch")).to_have_attribute("aria-pressed", "true")
        other = self.db(lambda: get_user_model().objects.create_user("ui-other", is_staff=True))
        self.db(lambda: self.client.force_login(other))
        self.assertEqual(self.db(lambda: self.client.get(reverse("radar_data")).json()["watch_count"]), 0)

    def test_history_selection_changes_visible_candles_without_manual_fit(self):
        self.db(lambda: self.day_bars(fixtures.NOW.date()-timedelta(days=20), opening=90))
        self.db(lambda: self.day_bars(fixtures.NOW.date()-timedelta(days=60), opening=80))
        self.open(interval="15", period="1D")
        candles = self.page.locator('#stock-chart svg g[role="img"]')
        expect(candles).to_have_count(7)
        first_day = candles.first.get_attribute("aria-label")
        self.data_action(lambda: self.page.locator("#period").select_option("5D"))
        expect(candles).to_have_count(103)
        first_week = candles.first.get_attribute("aria-label")
        self.assertNotEqual(first_day, first_week)
        self.data_action(lambda: self.page.locator("#period").select_option("1M"))
        expect(candles).to_have_count(151)
        first_month = candles.first.get_attribute("aria-label")
        self.assertNotEqual(first_week, first_month)
        self.data_action(lambda: self.page.locator("#period").select_option("3M"))
        expect(candles).to_have_count(175)
        self.assertNotEqual(first_month, candles.first.get_attribute("aria-label"))
        self.page.get_by_role("button", name="Stock chart Zoom in", exact=True).click()
        self.data_action(lambda: self.page.locator("#period").select_option("5D"))
        expect(candles).to_have_count(103)
        self.data_action(lambda: self.page.locator("#interval").select_option("5"))
        expect(candles).to_have_count(310)
        self.data_action(lambda: self.page.locator("#interval").select_option("1"))
        expect(candles).to_have_count(600)
        expect(self.page.locator("#stock-chart")).to_contain_text("600-candle display limit")

    def test_longer_history_discloses_when_no_older_recordings_exist(self):
        self.open(interval="15", period="1M")
        candles = self.page.locator('#stock-chart svg g[role="img"]')
        expect(candles).to_have_count(127)
        first = candles.first.get_attribute("aria-label")
        self.data_action(lambda: self.page.locator("#period").select_option("3M"))
        expect(candles).to_have_count(127)
        self.assertEqual(candles.first.get_attribute("aria-label"), first)
        expect(self.page.locator("#chart-range")).to_contain_text("no older data exists")
        expect(self.page.locator("#stock-chart .chart-visible-range")).to_contain_text("minute candles")

    def test_history_all_intervals_dates_option_selection_and_readable_timeline(self):
        self.open()
        self.data_action(lambda: self.page.locator("#period").select_option("5D"))
        self.page.get_by_role("button", name="Stock chart Fit latest recorded window (up to 600 bars)", exact=True).click()
        expect(self.page.locator(".chart-timeline").first).to_contain_text("Nov")
        expect(self.page.locator("#chart-range")).to_contain_text("2026-10-28")
        for interval in ["1", "2", "3", "5", "10", "15", "25", "30", "60", "75", "125", "240", "D", "W", "M"]:
            self.data_action(lambda value=interval: self.page.locator("#interval").select_option(value))
            expect(self.page.locator("#interval")).to_have_value(interval)
            expect(self.page.locator("#stock-chart svg")).to_have_count(1)
        for period in ["1M", "3M", "5D"]:
            self.data_action(lambda value=period: self.page.locator("#period").select_option(value))
            expect(self.page.locator("#chart-range")).to_contain_text("Recorded")
        self.data_action(lambda: self.page.locator("#interval").select_option("5"))
        self.data_action(lambda: self.page.locator("#period").select_option("1D"))
        self.data_action(lambda: self.page.locator("#chart-date").fill("2026-11-02"))
        expect(self.page.locator("#chart-range")).to_contain_text("2026-11-02 to 2026-11-02")
        expect(self.page.locator("#option-chart svg")).to_have_count(1)
        self.data_action(lambda: self.page.locator("#interval").select_option("240"))
        svg_box = self.page.locator("#stock-chart svg").bounding_box()
        self.assertIsNotNone(svg_box)
        for label in self.page.locator("#stock-chart .chart-timeline text").all():
            box = label.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["x"], svg_box["x"], "Single-candle timelines must not clip their date")
        self.data_action(lambda: self.page.locator("#interval").select_option("5"))
        self.data_action(lambda: self.page.locator("#chart-date").fill("2025-01-01"))
        expect(self.page.locator("#stock-chart")).to_contain_text("No complete")
        self.page.locator("#chart-date").fill("2099-01-01")
        expect(self.page.locator("#request-error")).to_contain_text("no later than today")
        expect(self.page.locator("#chart-date")).to_have_value("2025-01-01")
        self.data_action(lambda: self.page.locator("#latest-chart").click())
        expect(self.page.locator("#stock-chart svg")).to_have_count(1)
        put = self.db(lambda: RadarInstrument.objects.get(side="PE"))
        self.data_action(lambda: self.page.locator("#contract").select_option(str(put.pk)))
        expect(self.page.locator("#option-chart")).to_contain_text("No complete option candles")
        self.open_drawer("details.contract-drawer")
        self.page.get_by_text("Analytics contract catalogue (not executable recommendations)",exact=True).click()
        self.data_action(lambda: self.page.locator("#contracts button").first.click())
        expect(self.page.locator("#contract")).not_to_have_value(str(put.pk))
        for overlay in ["vwap", "levels", "strategy", "markers"]:
            self.page.locator(f'[data-overlay="{overlay}"]').uncheck()
            expect(self.page.locator(f'[data-overlay="{overlay}"]')).not_to_be_checked()
            if overlay == "levels":
                expect(self.page.locator("#stock-chart .chart-levels")).to_have_count(0)
            self.page.locator(f'[data-overlay="{overlay}"]').check()
            if overlay == "levels":
                expect(self.page.locator("#stock-chart .chart-levels")).to_have_count(1)
        self.page.get_by_role("button", name="Focus chart", exact=True).click()
        expect(self.page.locator("#workspace")).to_have_class(re.compile("chart-focus"))
        self.page.get_by_role("button", name="Restore workspace", exact=True).click()

    def test_chart_real_zoom_pan_keyboard_wheel_drag_crosshair_and_fit_live(self):
        self.open(interval="1", period="5D")
        controls = self.page.locator("#stock-chart .chart-controls")
        expect(controls).to_contain_text("600/")
        self.page.get_by_role("button", name="Stock chart Zoom in", exact=True).click()
        expect(controls).to_contain_text("450/")
        self.page.get_by_role("button", name="Stock chart Zoom out", exact=True).click()
        self.page.get_by_role("button", name="Stock chart Pan earlier", exact=True).click()
        expect(controls).to_contain_text("fixed view")
        self.page.get_by_role("button", name="Stock chart Pan later", exact=True).click()
        svg = self.page.locator("#stock-chart svg")
        svg.focus()
        svg.press("+")
        svg.press("ArrowLeft")
        expect(controls).to_contain_text("fixed view")
        svg.press("0")
        expect(controls).to_contain_text("600/")
        svg.scroll_into_view_if_needed()
        box = svg.bounding_box()
        self.assertIsNotNone(box)
        self.page.mouse.move(box["x"]+170, box["y"]+100)
        expect(self.page.locator("#chart-detail")).to_contain_text("2026")
        self.page.keyboard.down("Control")
        self.page.mouse.wheel(0, -200)
        self.page.keyboard.up("Control")
        expect(controls).to_contain_text("450/")
        self.page.mouse.down()
        self.page.mouse.move(box["x"]+270, box["y"]+100, steps=8)
        self.page.mouse.up()
        expect(controls).to_contain_text("fixed view")
        self.page.get_by_role("button", name="Stock chart Fit latest recorded window (up to 600 bars)", exact=True).click()
        expect(controls).not_to_contain_text("45/")
        self.page.get_by_role("button", name="Stock chart Follow latest bars", exact=True).click()
        expect(controls).to_contain_text("60/")
        svg.dblclick(position={"x": 180, "y": 100})
        expect(controls).to_contain_text("following latest")

    def test_typed_and_chart_picked_triggers_create_cancel_and_validation(self):
        self.open(interval="1")
        self.page.locator("#alert-level").fill("101")
        self.page.locator("#alert-direction").select_option("ABOVE")
        self.page.locator("#alert-minutes").select_option("60")
        self.page.get_by_role("button", name="Create private alert", exact=True).click()
        expect(self.page.locator("#price-alert-status")).to_contain_text("saved")
        expect(self.page.locator("#price-alert-list")).to_contain_text("WATCHING")
        self.assertEqual(self.db(lambda: RadarPriceAlert.objects.get().level), 101)
        self.page.get_by_role("button", name="Cancel alert", exact=True).click()
        expect(self.page.locator("#price-alert-list")).to_contain_text("CANCELLED")
        self.page.locator("#pick-alert-level").click()
        self.page.locator("#stock-chart svg").click(position={"x": 180, "y": 100})
        expect(self.page.locator("#price-alert-status")).to_contain_text("Level selected")
        self.assertGreater(float(self.page.locator("#alert-level").input_value()), 0)
        self.page.locator("#alert-direction").select_option("BELOW")
        self.page.locator("#alert-minutes").select_option("1440")
        self.page.get_by_role("button", name="Create private alert", exact=True).click()
        expect(self.page.locator("#price-alert-list")).to_contain_text("cross below")
        self.assertEqual(self.db(RadarPriceAlert.objects.count), 2)
        self.page.locator("#alert-level").fill("-1")
        self.page.get_by_role("button", name="Create private alert", exact=True).click()
        self.assertEqual(self.db(RadarPriceAlert.objects.count), 2)

    def test_mutation_failures_and_refresh_failure_never_claim_unsaved_success(self):
        self.open()
        self.page.route("**"+reverse("radar_watch"), lambda route: route.fulfill(
            status=400, content_type="application/json", body='{"error":"Rejected test request"}'))
        self.page.locator("#selected-watch").click()
        expect(self.page.locator("#request-error")).to_contain_text("Rejected test request")
        self.assertFalse(self.db(RadarWatch.objects.exists))
        self.page.unroute("**"+reverse("radar_watch"))
        self.page.route("**"+reverse("radar_data")+"?*", lambda route: route.fulfill(
            status=503, content_type="application/json", body='{"error":"Data temporarily unavailable"}'))
        self.page.locator("#selected-watch").click()
        expect(self.page.locator("#selected-watch")).to_have_attribute("aria-pressed", "true")
        self.page.locator("#alert-level").fill("101")
        self.page.get_by_role("button", name="Create private alert", exact=True).click()
        expect(self.page.locator("#price-alert-status")).to_contain_text("saved")
        self.page.locator("#refresh").click()
        expect(self.page.locator("#request-error")).to_contain_text("Data temporarily unavailable")
        self.assertTrue(self.db(RadarWatch.objects.exists))
        self.assertTrue(self.db(RadarPriceAlert.objects.exists))
        self.page.unroute("**"+reverse("radar_data")+"?*")
        self.data_action(lambda: self.page.locator("#refresh").click())
        self.page.route("**"+reverse("radar_price_alert"), lambda route: route.fulfill(
            status=400, content_type="application/json", body='{"error":"Rejected price test"}'))
        self.page.get_by_role("button", name="Create private alert", exact=True).click()
        expect(self.page.locator("#request-error")).to_contain_text("Rejected price test")
        self.assertEqual(self.db(RadarPriceAlert.objects.count), 1)

    def test_paper_take_and_skip_are_real_forms_and_stale_quotes_block_take(self):
        item = self.db(self.eligible)
        self.open()
        self.page.get_by_role("button", name="Take PAPER position", exact=True).click()
        self.page.wait_for_load_state("networkidle")
        self.db(item.refresh_from_db)
        self.assertEqual(item.state, S.TAKEN)
        expect(self.page.locator("#positions")).to_contain_text("Paper position open")
        self.page.get_by_role("button", name="Positions", exact=True).click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        item.state = S.SKIPPED
        self.db(lambda: item.save(update_fields=["state"]))
        skipped = self.db(lambda: self.eligible(key="TEST:S1:skip"))
        self.page.reload(wait_until="networkidle")
        self.data_action(lambda: self.page.locator("#clear-filters").click())
        self.page.get_by_role("button", name="Skip", exact=True).click()
        self.page.wait_for_load_state("networkidle")
        self.db(skipped.refresh_from_db)
        self.assertEqual(skipped.state, S.SKIPPED)
        stale = self.db(lambda: self.eligible(key="TEST:S1:stale"))
        self.quote.source_at = fixtures.NOW-timedelta(seconds=16)
        self.db(lambda: self.quote.save(update_fields=["source_at"]))
        self.page.reload(wait_until="networkidle")
        expect(self.page.get_by_role("button", name="Take PAPER position", exact=True)).to_be_disabled()
        self.db(stale.refresh_from_db)
        self.assertEqual(stale.state, S.ELIGIBLE)

    def test_disclosures_details_missing_route_auth_and_mobile_controls(self):
        self.db(self.opportunity)
        self.open()
        self.open_drawer("details.assistant-desk")
        self.open_drawer("details.market-drawer")
        for title in ["Data coverage and gaps", "All trigger report", "Recent paper lifecycle and delivery",
                      "Strategy evidence and momentum research", "Metric definitions and coverage"]:
            self.page.get_by_text(title, exact=True).click()
        expect(self.page.locator("#trigger-report")).to_contain_text("S1 CE strategy confirmation")
        expect(self.page.locator("#trigger-report")).to_contain_text("TEST")
        expect(self.page.locator("#trigger-report")).to_contain_text("invalidation 98")
        expect(self.page.locator("#definitions")).to_contain_text("completed current-session minutes")
        expect(self.page.locator("#news")).to_contain_text("not a claim that no news exists")
        expect(self.page.locator("#assistant-research")).to_contain_text("S1")
        expect(self.page.locator("#assistant-research")).to_contain_text("opening-range-momentum-v1")
        expect(self.page.locator("#assistant-research")).to_contain_text("Underlying proxy only")
        self.db(lambda: RadarNotificationRoute.objects.filter(user=self.user).delete())
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#price-alert-status")).to_contain_text("No private Telegram route")
        expect(self.page.get_by_role("button", name="Create private alert", exact=True)).to_be_disabled()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("""() => {
            const chart=document.querySelector('#stock-chart');
            const svg=chart.querySelector('svg');
            return Math.abs(svg.viewBox.baseVal.width-Math.max(320,chart.clientWidth))<2;
        }""")
        timeline = self.page.locator("#stock-chart .chart-timeline text").first.bounding_box()
        self.assertIsNotNone(timeline)
        self.assertGreaterEqual(timeline["height"], 10, "Mobile timeline text must not shrink after resizing")
        for offset in [0, 1]:
            labels = self.page.locator("#stock-chart .chart-timeline text").all()[offset::2]
            boxes = [label.bounding_box() for label in labels]
            for left, right in zip(boxes, boxes[1:]):
                self.assertIsNotNone(left)
                self.assertIsNotNone(right)
                self.assertLessEqual(left["x"]+left["width"], right["x"], "Mobile date/time labels must not overlap")
        self.page.locator("#selected-watch").click()
        expect(self.page.locator("#selected-watch")).to_have_attribute("aria-pressed", "true")
        self.page.get_by_role("button", name="Watchlist", exact=True).click()
        expect(self.page.locator("#stocks .stock-link")).to_have_count(1)
        self.page.locator("#chart-focus").click()
        expect(self.page.locator("#chart-focus")).to_have_attribute("aria-pressed", "true")
        self.context.clear_cookies()
        self.page.goto(self.live_server_url+reverse("radar"))
        self.assertIn("/login/", self.page.url)

    def test_priority_cards_strategy_definitions_news_links_and_sampled_premium_chart(self):
        self.db(lambda: RadarAssistantSnapshot.objects.create(
            timestamp=fixtures.NOW, expires_at=fixtures.NOW+timedelta(minutes=1),
            payload={"bullish": [{
                "symbol": "ZETA", "score": 66, "state": "ARMED", "why": "UI fixture, not a real trade idea",
                "confirmation": "Observed break", "invalidation": "Below test level",
                "expires_at": (fixtures.NOW+timedelta(minutes=1)).isoformat(),
                "source_at": fixtures.NOW.isoformat(),
                "components": [{"name": "Test structure", "points": 20, "detail": "Recorded fixture", "source_url": ""}],
            }], "bearish": [], "message": "UI test snapshot", "coverage": "Synthetic fixtures in test database only"},
        ))
        message = self.db(lambda: ChatMessage.objects.create(
            source_name="BeatTheStreet News", source_category="NEWS", raw_text="TEST: Synthetic UI news fixture",
            telegram_message_id=1, telegram_message_at=fixtures.NOW,
        ))
        self.db(lambda: ChatMessage.objects.filter(pk=message.pk).update(created_at=fixtures.NOW))
        for seconds in [15, 0]:
            self.db(lambda age=seconds: RadarOptionPoint.objects.create(
                instrument=self.option, timestamp=fixtures.NOW-timedelta(seconds=age), received_at=fixtures.NOW,
                last=10, bid=9.9, ask=10.1, volume=100, oi=1000,
            ))
        self.open()
        self.open_drawer("details.assistant-desk")
        self.open_drawer("details.market-drawer")
        self.open_drawer("details.contract-drawer")
        expect(self.page.locator("#premium-chart svg")).to_have_count(1)
        expect(self.page.locator("#news")).to_contain_text("Synthetic UI news fixture")
        link = self.page.get_by_role("link", name="Original Telegram post", exact=True)
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
        self.context.route("https://t.me/BeatTheStreetnews/*", lambda route: route.fulfill(
            status=200, content_type="text/plain", body="UI-test-only source placeholder"))
        with self.page.expect_popup() as popup:
            link.click()
        popup.value.wait_for_load_state()
        expect(popup.value.locator("body")).to_contain_text("UI-test-only source placeholder")
        popup.value.close()
        for rules in self.page.get_by_text("Strategy conditions (definition, not a passed checklist)", exact=True).all():
            rules.click()
        self.page.get_by_text("Priority components & missing sources", exact=True).click()
        expect(self.page.locator("#bullish-shortlist")).to_contain_text("Test structure")
        self.data_action(lambda: self.page.locator("#bullish-shortlist button").click())
        expect(self.page.locator("#stock-heading")).to_contain_text("ZETA")
