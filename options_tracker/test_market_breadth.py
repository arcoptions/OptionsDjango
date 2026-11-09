from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .indicator_math import ema_latest, wilder_rsi_latest
from .market_breadth import (
    BREADTH_VERSION,
    DailyEvidence,
    build_market_breadth_snapshot,
    complete_daily_evidence,
    latest_market_breadth_snapshot,
    summarize_breadth_evidence,
)
from .radar_models import MarketBreadthSnapshot
from .radar_sessions import session_minutes


class IndicatorMathTests(SimpleTestCase):
    def test_ema_matches_existing_recursive_definition(self):
        self.assertEqual(ema_latest([1.0, 2.0], 3), 1.5)
        self.assertEqual(ema_latest([1.0, 2.0, 3.0], 3), 2.25)

    def test_wilder_rsi_handles_rising_and_flat_series(self):
        self.assertEqual(wilder_rsi_latest([float(value) for value in range(15)]), 100.0)
        self.assertEqual(wilder_rsi_latest([10.0] * 15), 50.0)
        self.assertIsNone(wilder_rsi_latest([10.0] * 14))


class MarketBreadthCalculationTests(SimpleTestCase):
    def _daily_evidence(self, count, *, rising=True, volume_surge=True):
        start = date(2026, 1, 1)
        evidence = []
        for index in range(count):
            close = 100 + index if rising else 400 - index
            volume = 100
            if volume_surge and index == count - 1:
                volume = 200
            evidence.append(
                DailyEvidence(
                    day=start + timedelta(days=index),
                    open=close - 0.5,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=volume,
                    source_at=timezone.make_aware(
                        datetime.combine(
                            start + timedelta(days=index),
                            datetime.min.time(),
                        )
                    ).replace(hour=15, minute=14),
                )
            )
        return evidence

    def test_complete_daily_evidence_excludes_incomplete_session(self):
        day = date(2026, 11, 9)
        start = timezone.make_aware(datetime(2026, 11, 9, 9, 15))
        rows = [
            {
                "timestamp": start + timedelta(minutes=index),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10,
                "complete": True,
            }
            for index in range(session_minutes(day))
        ]

        self.assertEqual(len(complete_daily_evidence(rows)), 1)
        rows[-1]["complete"] = False
        self.assertEqual(complete_daily_evidence(rows), [])

    def test_summary_is_deterministic_and_reports_qualified_metrics(self):
        rising = self._daily_evidence(252, rising=True)
        falling = self._daily_evidence(252, rising=False)

        first = summarize_breadth_evidence({2: falling, 1: rising}, 2)
        second = summarize_breadth_evidence({1: rising, 2: falling}, 2)

        self.assertEqual(first["input_hash"], second["input_hash"])
        self.assertEqual(first["metrics"]["ema"]["200"]["percent"], 100.0)
        self.assertEqual(first["metrics"]["ema"]["200"]["above"], 1)
        self.assertEqual(first["metrics"]["rsi"]["overbought"], 1)
        self.assertEqual(first["metrics"]["rsi"]["oversold"], 1)
        self.assertEqual(first["metrics"]["volume"]["surging"], 2)
        self.assertEqual(first["metrics"]["high_low_52_week"]["new_highs"], 1)
        self.assertEqual(first["metrics"]["high_low_52_week"]["new_lows"], 1)
        self.assertEqual(first["unavailable"], [])

    def test_summary_retains_explicit_insufficient_coverage(self):
        summary = summarize_breadth_evidence(
            {1: self._daily_evidence(20), 2: []},
            2,
        )

        self.assertFalse(summary["metrics"]["ema"]["20"]["available"])
        self.assertEqual(summary["metrics"]["ema"]["20"]["percent"], 50.0)
        self.assertEqual(len(summary["unavailable"]), 4)


class MarketBreadthPersistenceTests(TestCase):
    def setUp(self):
        self.now = timezone.make_aware(datetime(2026, 11, 9, 15, 14))
        self.evidence = {
            1: [
                DailyEvidence(
                    day=self.now.date(),
                    open=100,
                    high=102,
                    low=99,
                    close=101,
                    volume=100,
                    source_at=self.now,
                )
            ]
        }

    @patch("options_tracker.market_breadth.collect_breadth_evidence")
    def test_apply_is_idempotent_for_same_input_hash(self, collect):
        collect.return_value = (self.evidence, 1)

        _, first, first_created = build_market_breadth_snapshot(apply=True)
        _, second, second_created = build_market_breadth_snapshot(apply=True)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MarketBreadthSnapshot.objects.count(), 1)

    def test_latest_snapshot_rejects_stale_evidence(self):
        MarketBreadthSnapshot.objects.create(
            universe="FNO",
            version=BREADTH_VERSION,
            input_hash="a" * 64,
            source_at=self.now - timedelta(days=5),
            active_instruments=1,
            covered_instruments=1,
        )

        self.assertIsNone(latest_market_breadth_snapshot(self.now))
