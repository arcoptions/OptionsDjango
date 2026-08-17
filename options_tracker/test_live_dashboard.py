"""The tuning layer and the dashboard that drives it.

Two guarantees carry most of the weight here and are worth naming, because both
are the kind of thing that fails silently and expensively.

The first is that nothing set in a browser can move the numbers the strategy was
validated on: `nifty_trail_config()` must keep returning the shipped values no
matter what the live layer is set to.

The second is that the sizing replay is a replay. The Risk tab tells the user
that what it shows at a given capital and risk fraction is what would actually
have happened, not a projection, so it has to land on the published figure
exactly -- Rs 38,625.64 over 51 trades.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from . import live_config, live_dashboard, live_engine
from .models import AppSetting, DhanOrderEvent, Direction, TipSignal, TradeExecution
from .nifty_trail_strategy import nifty_trail_config


class ClearsConfigCache(TestCase):
    """`live_strategy_config` is cached for the tick loop; tests must not share it."""

    def setUp(self):
        live_config.live_strategy_config.cache_clear()

    def tearDown(self):
        live_config.live_strategy_config.cache_clear()


class LiveConfigTests(ClearsConfigCache):
    def test_clamps_only_what_the_arithmetic_cannot_take(self):
        # A 0% stop makes the unit risk zero and the position size infinite.
        self.assertEqual(live_config.clamp("stop_percent", 0), 0.02)
        self.assertEqual(live_config.clamp("risk_per_trade", 5), 0.10)

    def test_allows_settings_the_backtest_never_measured(self):
        settings, notes = live_config.save_live_settings({"stop_percent": 0.45})
        self.assertEqual(settings["stop_percent"], 0.45)
        self.assertTrue(any("outside" in note for note in notes))

    def test_says_nothing_when_everything_is_validated(self):
        self.assertEqual(live_config.warnings_for(live_config.defaults()), [])

    def test_a_change_reaches_the_engine(self):
        live_config.save_live_settings({"stop_percent": 0.12, "trail_gap_r": 0.9})
        config = live_config.live_strategy_config()
        self.assertEqual(config.stop_percent, 0.12)
        self.assertEqual(config.trail_gap_r, 0.9)

    def test_a_change_never_reaches_the_backtest(self):
        # If this fails, a slider in a browser can silently move the numbers the
        # strategy was validated on, and the research lineage is gone.
        live_config.save_live_settings({"stop_percent": 0.30, "premium_min": 25})
        research = nifty_trail_config()
        self.assertEqual(research.stop_percent, 0.10)
        self.assertEqual(research.premium_min, 100)

    def test_every_change_is_logged(self):
        live_config.save_live_settings({"risk_per_trade": 0.03}, actor="test")
        event = DhanOrderEvent.objects.get(status="RISK_CHANGE")
        self.assertEqual(event.payload_json["by"], "test")
        self.assertEqual(event.payload_json["changed"]["risk_per_trade"]["to"], 0.03)

    def test_saving_the_same_value_logs_nothing(self):
        live_config.save_live_settings(live_config.defaults())
        self.assertEqual(DhanOrderEvent.objects.filter(status="RISK_CHANGE").count(), 0)

    def test_reset_returns_to_the_validated_configuration(self):
        live_config.save_live_settings({"stop_percent": 0.25, "fixed_lots": 4})
        live_config.reset_live_settings()
        self.assertEqual(live_config.live_settings(), live_config.defaults())

    def test_max_trades_per_day_stays_an_integer(self):
        # It is compared against a trade tally, and a number input will happily
        # send "3.0".
        live_config.save_live_settings({"max_trades_per_day": 4})
        self.assertIsInstance(live_config.live_strategy_config().max_trades_per_day, int)

    def test_every_tunable_is_covered_by_help_text(self):
        for spec in live_config.TUNABLES:
            self.assertTrue(spec["help"].strip(), spec["key"])
            self.assertIn(spec["kind"], ("sizing", "strategy"), spec["key"])


class LiveConfigMidTradeTests(ClearsConfigCache):
    """Retuning must not reach into a position that is already running."""

    def test_an_open_position_keeps_the_trail_it_was_opened_with(self):
        position = {
            "entry": 100.0, "initial_stop": 90.0, "stop": 90.0,
            "stop_percent": 0.10, "trail_gap_r": 0.7,
        }
        live_config.save_live_settings({"trail_gap_r": 1.5})
        self.assertEqual(live_engine._trailed_stop(position, 120.0), 113.0)

    def test_the_next_entry_uses_the_new_setting(self):
        live_config.save_live_settings({"trail_gap_r": 1.5})
        fresh = {
            "entry": 100.0, "initial_stop": 90.0, "stop": 90.0,
            "stop_percent": 0.10, "trail_gap_r": 1.5,
        }
        self.assertEqual(live_engine._trailed_stop(fresh, 120.0), 105.0)


class LiveDashboardTests(ClearsConfigCache):
    def _closed_trade(self, entry="100", exit_price=118.0, quantity=65):
        signal = TipSignal.objects.create(
            source_type="ENGINE", source_name="nifty_trail",
            option_symbol="NIFTY 24500 CE", direction=Direction.CE,
            entry_price=Decimal(entry), stop_loss=Decimal("90"),
        )
        TradeExecution.objects.create(
            signal=signal, dhan_order_id="ORD1", quantity=quantity,
            entry_price=Decimal(entry), stop_loss=Decimal("90"),
            state="CLOSED", closed_at=timezone.now(),
        )
        DhanOrderEvent.objects.create(
            order_id="ORD1", correlation_id="", status="EXIT",
            payload_json={"exit_price": exit_price, "reason": "TRAIL", "realized_r": 1.8},
        )

    def test_a_closed_trade_is_reported_net_of_costs(self):
        self._closed_trade()
        row = live_dashboard.trade_rows()[0]
        self.assertEqual(row["kind"], "TRADE")
        self.assertEqual(row["gross_pnl"], 1170.0)
        self.assertGreater(row["charges"], 0)
        self.assertEqual(row["net_pnl"], round(1170.0 - row["charges"], 2))

    def test_observe_only_signals_are_shown_but_never_counted(self):
        # Without this an observe-only day looks like a day the strategy stayed
        # silent, which is exactly backwards.
        DhanOrderEvent.objects.create(
            order_id="", correlation_id="", status="DRY_RUN_ENTRY",
            payload_json={"option": "NIFTY 24500 CE", "entry_limit": 120.0,
                          "stop": 108.0, "quantity": 65, "lots": 1},
        )
        rows = live_dashboard.trade_rows()
        self.assertEqual([row["kind"] for row in rows], ["OBSERVED"])

        results = live_dashboard.performance(rows, 100_000)
        self.assertEqual(results["trades"], 0)
        self.assertEqual(results["net_pnl"], 0.0)
        self.assertEqual(results["observed_signals"], 1)

    def test_slippage_is_measured_against_the_limit(self):
        self._closed_trade(entry="100")
        DhanOrderEvent.objects.create(
            order_id="ORD1", correlation_id="", status="FILL",
            payload_json={"filled_at": 101.5},
        )
        row = live_dashboard.trade_rows()[0]
        self.assertEqual(row["entry"], 101.5)
        self.assertEqual(row["slippage"], 1.5)

    def test_a_silent_engine_is_reported_as_stale(self):
        AppSetting.objects.update_or_create(
            key="nifty_live_status",
            defaults={"value": json.dumps({
                "at": (timezone.localtime() - timedelta(minutes=30)).isoformat(),
                "state": "RUNNING", "dry_run": True,
            })},
        )
        snapshot = live_dashboard.engine_snapshot()
        self.assertTrue(snapshot["stale"])
        self.assertGreater(snapshot["age_seconds"], 1500)

    def test_a_missing_status_is_not_mistaken_for_a_healthy_one(self):
        self.assertTrue(live_dashboard.engine_snapshot()["stale"])

    def test_sizing_replay_reproduces_the_validated_result(self):
        # The page calls this a replay rather than a projection, so it has to
        # land on the published figure to the paisa.
        preview = live_dashboard.sizing_preview(live_config.defaults())
        self.assertEqual(preview["trades"], 51)
        self.assertEqual(preview["net_pnl"], 38625.64)
        self.assertEqual(preview["win_rate"], 66.7)
        self.assertEqual(preview["max_drawdown"], 5114.5)
        self.assertEqual(preview["sessions"], 246)

    def test_the_lot_cap_only_ever_costs_money(self):
        uncapped = live_dashboard.sizing_preview(live_config.defaults())
        capped = live_dashboard.sizing_preview({**live_config.defaults(), "fixed_lots": 1})
        self.assertLess(capped["net_pnl"], uncapped["net_pnl"])
        self.assertLessEqual(capped["average_lots"], 1.0)

    def test_a_capital_too_small_to_trade_reports_zero_not_an_error(self):
        preview = live_dashboard.sizing_preview({**live_config.defaults(), "capital": 20_000})
        self.assertEqual(preview["trades"], 0)
        self.assertEqual(preview["skipped"], 51)
        self.assertEqual(preview["max_drawdown_percent"], 0.0)


class NiftyLiveViewTests(ClearsConfigCache):
    def test_every_tab_renders(self):
        for tab in ("live", "trades", "metrics", "risk"):
            response = self.client.get(f"/nifty-live/?tab={tab}")
            self.assertEqual(response.status_code, 200, tab)

    def test_saving_risk_settings_applies_them(self):
        response = self.client.post("/nifty-live/", {"action": "save", "set_stop_percent": "12"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(live_config.live_settings()["stop_percent"], 0.12)

    def test_a_percent_field_is_read_as_a_percent(self):
        self.client.post("/nifty-live/", {"action": "save", "set_risk_per_trade": "3"})
        self.assertEqual(live_config.live_settings()["risk_per_trade"], 0.03)

    def test_an_untouched_field_is_left_alone(self):
        live_config.save_live_settings({"trail_gap_r": 0.9})
        self.client.post("/nifty-live/", {"action": "save", "set_stop_percent": "12"})
        self.assertEqual(live_config.live_settings()["trail_gap_r"], 0.9)

    def test_preview_shows_the_result_without_saving_it(self):
        response = self.client.post(
            "/nifty-live/", {"action": "preview", "set_risk_per_trade": "3"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(live_config.live_settings()["risk_per_trade"], 0.02)
        self.assertContains(response, "not saved")

    def test_arming_and_disarming_moves_the_kill_switch(self):
        self.client.post("/nifty-live/", {"action": "arm"})
        self.assertTrue(live_engine.engine_enabled())
        self.client.post("/nifty-live/", {"action": "disarm"})
        self.assertFalse(live_engine.engine_enabled())

    def test_the_status_api_answers_without_a_running_engine(self):
        payload = self.client.get("/api/nifty-live/status/").json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["stale"])
