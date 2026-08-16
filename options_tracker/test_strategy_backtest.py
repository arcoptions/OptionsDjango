from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from .strategy_backtest import (
    _completed_spot_bars,
    _execute_signals,
    _simulate,
    nifty_put_strategy_config,
    spot_setup_timestamps,
)


class StrategyBacktestTests(SimpleTestCase):
    def _timestamp(self, hour, minute):
        return timezone.make_aware(datetime(2026, 8, 14, hour, minute))

    def test_completed_spot_bars_exclude_incomplete_interval(self):
        start = self._timestamp(9, 15)
        spot_rows = {start + timedelta(minutes=index): 100 + index for index in range(9)}

        bars = _completed_spot_bars(spot_rows, 5)

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["timestamp"], self._timestamp(9, 19))
        self.assertEqual(bars[0]["open"], 100)
        self.assertEqual(bars[0]["close"], 104)

    def test_setup_waits_for_completed_five_minute_bearish_context(self):
        start = self._timestamp(9, 15)
        opening = [100] * 10 + [100, 100.5, 101, 101.5, 102]
        decline = [99.8, 99.7, 99.6, 99.5, 99.4]
        spot_rows = {
            start + timedelta(minutes=index): spot
            for index, spot in enumerate([*opening, *decline])
        }

        setups = spot_setup_timestamps(spot_rows, nifty_put_strategy_config())

        self.assertEqual(setups, {self._timestamp(9, 34): {"PUT"}})

    def test_setup_rearms_only_after_spot_returns_inside_opening_range(self):
        start = self._timestamp(9, 15)
        spot_rows = {start + timedelta(minutes=index): 100 for index in range(15)}
        for minute, spot in ((30, 99.8), (31, 99.7), (32, 99.6), (33, 99.5), (34, 99.4)):
            spot_rows[self._timestamp(9, minute)] = spot
        for timestamp in (
            self._timestamp(10, 30),
            self._timestamp(11, 25),
            self._timestamp(11, 26),
            self._timestamp(11, 27),
            self._timestamp(11, 28),
            self._timestamp(11, 29),
        ):
            spot_rows[timestamp] = 100
        for minute, spot in ((30, 99.8), (31, 99.7), (32, 99.6), (33, 99.5), (34, 99.4)):
            spot_rows[self._timestamp(11, minute)] = spot

        setups = spot_setup_timestamps(spot_rows, nifty_put_strategy_config())

        self.assertEqual(
            setups,
            {
                self._timestamp(9, 34): {"PUT"},
                self._timestamp(11, 34): {"PUT"},
            },
        )

    def test_setup_survives_a_feed_that_is_missing_the_opening_bar(self):
        """The outage: one absent minute used to cost the entire session.

        Dhan's `fromDate` is exclusive, so asking from 09:15 returned 09:16
        onwards and the opening range arrived fourteen minutes long. The old
        guard demanded all fifteen and returned an empty dict, which is
        indistinguishable from a quiet market -- so the live engine reported no
        breakout every day of its life and nobody could tell it was broken.
        """
        start = self._timestamp(9, 16)
        opening = [100] * 9 + [100, 100.5, 101, 101.5, 102]
        decline = [99.8, 99.7, 99.6, 99.5, 99.4]
        spot_rows = {
            start + timedelta(minutes=index): spot
            for index, spot in enumerate(opening)
        }
        for offset, spot in enumerate(decline):
            spot_rows[self._timestamp(9, 30 + offset)] = spot

        setups = spot_setup_timestamps(spot_rows, nifty_put_strategy_config())

        self.assertEqual(setups, {self._timestamp(9, 34): {"PUT"}})

    def test_no_setup_until_the_opening_window_has_actually_closed(self):
        """A range read at 09:20 is five minutes of noise, not an opening range."""
        start = self._timestamp(9, 15)
        spot_rows = {start + timedelta(minutes=index): 100 + index for index in range(6)}

        self.assertEqual(spot_setup_timestamps(spot_rows, nifty_put_strategy_config()), {})

    def test_a_feed_below_the_coverage_floor_is_refused(self):
        """Missing minutes can only narrow the range, which invents breakouts.

        A min and a max taken from a third of the window is a tighter range than
        the real one, so every later bar clears it more easily. Withholding the
        session is the safe direction; the backtest applies no such test, so this
        can only ever skip a trade.
        """
        spot_rows = {self._timestamp(9, 15 + index): 100 for index in (0, 1, 2, 3, 14)}
        for minute, spot in ((30, 99.8), (31, 99.7), (32, 99.6), (33, 99.5), (34, 99.4)):
            spot_rows[self._timestamp(9, minute)] = spot

        self.assertEqual(spot_setup_timestamps(spot_rows, nifty_put_strategy_config()), {})

    def test_simulation_tracks_fixed_strike_and_uses_stop_first(self):
        config = nifty_put_strategy_config()
        candidate = {
            "date": "2026-08-14",
            "signal_at": self._timestamp(9, 34),
            "strike": 24500,
            "option_type": "PUT",
            "relative_strike": "ATM",
            "volume_ratio": 1.5,
            "breakout_percent": 1,
            "spot_move_percent": 0.2,
            "signal_close": 100,
            "next_index": 0,
        }
        rows = [{
            "local_timestamp": self._timestamp(9, 35),
            "open": 100,
            "high": 120,
            "low": 80,
            "close": 100,
        }]

        trade = _simulate(candidate, rows, config)

        self.assertEqual(trade["strike"], 24500)
        self.assertEqual(trade["outcome"], "STOP")
        self.assertEqual(trade["realized_r"], -1)

    def test_sequential_execution_skips_overlap_and_caps_trades(self):
        config = nifty_put_strategy_config(max_trades_per_day=2)
        signals = self._signals_at((9, 30), (9, 35), (9, 56), (11, 40))

        with patch(
            "options_tracker.strategy_backtest._simulate",
            side_effect=self._successful_trade,
        ):
            trades = _execute_signals({"2026-08-14": signals}, config)

        self.assertEqual([trade["signal_at"][11:16] for trade in trades], ["09:30", "09:56"])

    def test_daily_loss_limit_stops_third_entry(self):
        config = replace(
            nifty_put_strategy_config(max_trades_per_day=3),
            reentry_cooldown_minutes=0,
        )
        signals = self._signals_at((9, 30), (9, 40), (9, 50))

        with patch(
            "options_tracker.strategy_backtest._simulate",
            side_effect=self._stopped_trade,
        ):
            trades = _execute_signals({"2026-08-14": signals}, config)

        self.assertEqual(len(trades), 2)
        self.assertEqual(sum(trade["realized_r"] for trade in trades), -2)

    def _signals_at(self, *clocks):
        return [
            {
                "date": "2026-08-14",
                "signal_at": self._timestamp(hour, minute),
                "strike": 24500,
                "option_type": "PUT",
                "relative_strike": "ATM",
                "volume_ratio": 1.5,
                "breakout_percent": 1,
                "spot_move_percent": 0.2,
                "signal_close": 100,
                "next_index": 0,
                "rows": [],
            }
            for hour, minute in clocks
        ]

    def _successful_trade(self, candidate, rows, config):
        signal_at = candidate["signal_at"]
        return {
            **candidate,
            "signal_at": signal_at.isoformat(),
            "exit_at": (signal_at + timedelta(minutes=15)).isoformat(),
            "outcome": "TARGET",
            "realized_r": config.reward_risk,
        }

    def _stopped_trade(self, candidate, rows, config):
        signal_at = candidate["signal_at"]
        return {
            **candidate,
            "signal_at": signal_at.isoformat(),
            "exit_at": signal_at.isoformat(),
            "outcome": "STOP",
            "realized_r": -1,
        }