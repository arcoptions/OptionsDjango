from datetime import date, datetime, timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from .dynamic_strategy import (
    DynamicStrategyConfig,
    nifty_expiry_sessions,
    price_action_setup,
    select_option_candidate,
    simulate_trade,
    window_structure_setup,
)
from .capital_pnl import estimate_option_charges, size_trade
from .dual_strategy_research import (
    ExitScenario,
    expiry_option_led_candidate,
    simulate_exit_scenario,
)


class DynamicStrategyTests(SimpleTestCase):
    def _bars(self, direction, end_at=None):
        end_at = end_at or timezone.make_aware(datetime(2026, 8, 14, 9, 34))
        closes = [100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7, 100.8]
        closes.append(102 if direction == "CALL" else 98)
        if direction == "PUT":
            closes[:-1] = [100 - (value - 100) for value in closes[:-1]]
        return [
            {
                "timestamp": end_at - timedelta(minutes=5 * (len(closes) - index - 1)),
                "open": close - 0.2 if direction == "CALL" else close + 0.2,
                "high": close + (0.1 if direction == "CALL" else 0.25),
                "low": close - (0.25 if direction == "CALL" else 0.1),
                "close": close,
            }
            for index, close in enumerate(closes)
        ]

    def test_price_action_setup_is_bidirectional(self):
        config = DynamicStrategyConfig()

        call_setup = price_action_setup(self._bars("CALL"), config)
        put_setup = price_action_setup(self._bars("PUT"), config)

        self.assertEqual(call_setup["option_type"], "CALL")
        self.assertEqual(put_setup["option_type"], "PUT")

    def test_price_action_setup_only_triggers_in_configured_windows(self):
        config = DynamicStrategyConfig()
        end_at = timezone.make_aware(datetime(2026, 8, 14, 12, 0))

        self.assertIsNone(price_action_setup(self._bars("CALL", end_at), config))

    def test_window_structure_can_break_opening_range_in_either_direction(self):
        config = DynamicStrategyConfig(spot_setup_mode="window_structure")
        start = timezone.make_aware(datetime(2026, 8, 14, 9, 19))
        reference = [
            {
                "timestamp": start + timedelta(minutes=5 * index),
                "open": 100,
                "high": 100.2,
                "low": 99.8,
                "close": 100,
            }
            for index in range(3)
        ]
        call_bar = {
            "timestamp": timezone.make_aware(datetime(2026, 8, 14, 9, 34)),
            "open": 100,
            "high": 101.2,
            "low": 99.9,
            "close": 101.1,
        }
        put_bar = {
            "timestamp": call_bar["timestamp"],
            "open": 100,
            "high": 100.1,
            "low": 98.8,
            "close": 98.9,
        }

        call_setup = window_structure_setup([*reference, call_bar], config)
        put_setup = window_structure_setup([*reference, put_bar], config)

        self.assertEqual(call_setup["option_type"], "CALL")
        self.assertEqual(put_setup["option_type"], "PUT")

    def test_expiry_sessions_follow_schedule_and_shift_for_holiday(self):
        sessions = {
            date(2025, 8, 25), date(2025, 8, 26), date(2025, 8, 28),
            date(2026, 8, 10), date(2026, 8, 11),
            date(2026, 8, 17),
        }

        expiry_dates = nifty_expiry_sessions(sessions)

        self.assertIn(date(2025, 8, 28), expiry_dates)
        self.assertIn(date(2026, 8, 11), expiry_dates)
        self.assertIn(date(2026, 8, 17), expiry_dates)

    def test_closing_expiry_prefers_confirmed_otm_contract(self):
        config = DynamicStrategyConfig()
        signal_at = timezone.make_aware(datetime(2026, 8, 11, 14, 34))
        setup = {
            "timestamp": signal_at,
            "window": "CLOSING",
            "option_type": "CALL",
            "score": 70,
        }
        contracts = {
            (24500, "CALL"): self._contract_stream(signal_at, "ATM", 80),
            (24550, "CALL"): self._contract_stream(signal_at, "ATM+1", 40),
        }

        candidate = select_option_candidate(contracts, setup, config, is_expiry_day=True)

        self.assertEqual(candidate["strike"], 24550)
        self.assertEqual(candidate["otm_distance"], 1)

    def test_simulation_is_stop_first_and_tracks_window_excursion(self):
        config = DynamicStrategyConfig()
        signal_at = timezone.make_aware(datetime(2026, 8, 14, 9, 34))
        setup = {
            "timestamp": signal_at,
            "window": "MORNING",
            "option_type": "CALL",
            "score": 70,
        }
        contracts = {(24500, "CALL"): self._contract_stream(signal_at, "ATM", 100)}
        candidate = select_option_candidate(contracts, setup, config)
        future = candidate["stream"][signal_at + timedelta(minutes=1)]
        future["high"] = 140
        future["low"] = 80

        trade = simulate_trade(candidate, config)

        self.assertEqual(trade["outcome"], "STOP")
        self.assertLess(trade["realized_r"], -1)
        self.assertGreater(trade["window_mfe_r"], 1)

    def test_scenario_keeps_account_risk_small_with_wide_expiry_stop(self):
        config = DynamicStrategyConfig()
        signal_at = timezone.make_aware(datetime(2026, 8, 11, 14, 34))
        setup = {
            "timestamp": signal_at,
            "window": "CLOSING",
            "option_type": "CALL",
            "score": 70,
        }
        candidate = select_option_candidate(
            {(24500, "CALL"): self._contract_stream(signal_at, "ATM", 20)},
            setup,
            config,
            is_expiry_day=True,
        )
        future = candidate["stream"][signal_at + timedelta(minutes=1)]
        future["high"] = 80
        future["low"] = 5
        scenario = ExitScenario("WIDE", 50, "MULTIPLE", 3, 0.2)

        trade = simulate_exit_scenario(candidate, config, scenario, True)

        self.assertEqual(trade["outcome"], "STOP")
        self.assertEqual(trade["account_allocation_percent"], 0.4)
        self.assertLess(trade["account_return_percent"], -0.2)

    def test_runner_takes_half_at_two_x_and_moves_stop_to_entry(self):
        config = DynamicStrategyConfig()
        signal_at = timezone.make_aware(datetime(2026, 8, 11, 14, 34))
        setup = {
            "timestamp": signal_at,
            "window": "CLOSING",
            "option_type": "PUT",
            "score": 70,
        }
        stream = self._contract_stream(signal_at, "ATM", 10)
        second_at = signal_at + timedelta(minutes=2)
        stream[second_at] = {
            **stream[signal_at + timedelta(minutes=1)],
            "local_timestamp": second_at,
            "open": 20,
            "high": 22,
            "low": 10,
            "close": 11,
        }
        candidate = select_option_candidate(
            {(24500, "PUT"): stream}, setup, config, is_expiry_day=True,
        )
        first = candidate["stream"][signal_at + timedelta(minutes=1)]
        first["high"] = 25
        first["low"] = 10
        scenario = ExitScenario(
            "RUNNER", 50, "MULTIPLE", 5, 0.2,
            partial_at_multiple=2, partial_fraction=0.5,
            breakeven_after_partial=True,
        )

        trade = simulate_exit_scenario(candidate, config, scenario, True)

        self.assertEqual(trade["outcome"], "PARTIAL_STOP")
        self.assertGreater(trade["premium_return_percent"], 40)

    def test_expiry_option_led_signal_uses_minute_price_and_completed_context(self):
        config = DynamicStrategyConfig(
            expiry_premium_min=2,
            expiry_premium_max=50,
            expiry_maximum_otm_distance=3,
        )
        signal_at = timezone.make_aware(datetime(2026, 8, 11, 14, 34))
        stream = self._contract_stream(signal_at, "ATM-1", 10)
        for timestamp, row in stream.items():
            row["spot"] = 24500 - (timestamp - signal_at).total_seconds() / 60 * 5
        bars = [{
            "timestamp": timezone.make_aware(datetime(2026, 8, 11, 14, 29)),
            "open": 24520,
            "high": 24525,
            "low": 24490,
            "close": 24500,
        }]

        candidate = expiry_option_led_candidate(
            {(24450, "PUT"): stream}, bars, config,
        )

        self.assertEqual(candidate["option_type"], "PUT")
        self.assertEqual(candidate["setup_type"], "EXPIRY_OPTION_ACCELERATION")

    def test_max_budget_sizing_uses_only_whole_lots(self):
        sizing = size_trade(100, 10, 10000, lot_size=65)

        self.assertEqual(sizing["lots"], 1)
        self.assertEqual(sizing["quantity"], 65)
        self.assertEqual(sizing["deployed"], 6500)
        self.assertEqual(sizing["stop_risk"], 650)

    def test_risk_cap_skips_unaffordable_minimum_lot(self):
        sizing = size_trade(
            30, 22.5, 10000, lot_size=65,
            policy="RISK_CAP", risk_cap=1000,
        )

        self.assertEqual(sizing["lots"], 0)

    def test_fixed_lot_sizing_ignores_position_cap(self):
        sizing = size_trade(
            200, 20, 10000, lot_size=65,
            policy="FIXED_LOTS", fixed_lots=10,
        )

        self.assertEqual(sizing["lots"], 10)
        self.assertEqual(sizing["quantity"], 650)
        self.assertEqual(sizing["deployed"], 130000)
        self.assertEqual(sizing["stop_risk"], 13000)

    def test_estimated_option_charges_include_round_trip_costs(self):
        charges = estimate_option_charges(100, 120, 65)

        self.assertGreater(charges, 50)

    def test_option_stt_increase_is_applied_by_trade_date(self):
        old_charges = estimate_option_charges(100, 120, 65, "2026-03-31")
        new_charges = estimate_option_charges(100, 120, 65, "2026-04-01")

        self.assertGreater(new_charges, old_charges)

    def _contract_stream(self, signal_at, relative_strike, base_price):
        stream = {}
        for offset in range(-5, 2):
            timestamp = signal_at + timedelta(minutes=offset)
            close = base_price if offset < 0 else base_price * 1.1
            stream[timestamp] = {
                "local_timestamp": timestamp,
                "relative_strike": relative_strike,
                "open": close * 0.98,
                "high": close * (1 if offset < 0 else 1.02),
                "low": close * 0.95,
                "close": close,
                "volume": 100 if offset < 0 else 200,
                "oi": 1000 + offset * 10,
                "implied_volatility": 20 + offset / 10,
                "spot": 24500,
            }
        return stream