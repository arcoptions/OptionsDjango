import json
from io import StringIO
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    StockCorporateAction,
    StockEquityCandle,
    StockFinancialResult,
    StockFundamentalSnapshot,
    StockNewsItem,
    StockScoreSnapshot,
    TrackedStock,
)
from .stock_intelligence import (
    automatic_trade_levels,
    investment_score,
    pattern_labels,
    event_timeline,
    stock_intelligence_context,
    stock_personality,
    timeframe_agreement,
)


class StockIntelligenceTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="stock-intelligence-reader",
            password="test-password",
        )
        self.client.force_login(user)
        TrackedStock.objects.create(symbol="TEST", priority=1)

    def test_missing_fundamentals_lower_score_confidence_and_score(self):
        partial = StockFundamentalSnapshot.objects.create(
            symbol="TEST",
            provider="APPROVED",
            source_at=timezone.now(),
            return_on_equity=20,
        )

        score = investment_score(partial)

        self.assertEqual(score["confidence"], 0.2)
        self.assertLess(score["score"], score["components"]["return_on_equity"])
        self.assertIn("debt_to_equity", score["missing_data"])

    def test_context_calculates_technical_evidence_from_stored_bars(self):
        now = timezone.now()
        for index in range(60):
            close = 100 + index
            StockEquityCandle.objects.create(
                symbol="TEST",
                interval_minutes=1440,
                timestamp=now - timedelta(days=60 - index),
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                volume=1000 + index * 10,
            )
        StockEquityCandle.objects.filter(
            symbol="TEST",
            timestamp=now - timedelta(days=1),
        ).update(close=200, high=201, volume=5000)

        context = stock_intelligence_context("TEST", now)

        self.assertEqual(context["technical"]["bar_count"], 60)
        self.assertGreater(context["technical"]["momentum20"], 0)
        self.assertGreater(context["scores"][1]["preview"]["confidence"], 0.9)
        self.assertEqual(context["personality"]["label"], "TRENDING")
        self.assertEqual(len(context["timeframes"]), 3)
        self.assertEqual(context["patterns"][0]["label"], "CONFIRMED BREAKOUT")
        self.assertEqual(
            context["timeframe_agreement"]["label"],
            "INSUFFICIENT TIMEFRAME HISTORY",
        )
        self.assertTrue(context["trade_levels"]["available"])
        self.assertEqual(context["trade_levels"]["direction"], "LONG")
        self.assertEqual(context["trade_levels"]["reward_risk"], 2.0)

    def test_personality_fails_closed_without_sufficient_history(self):
        personality = stock_personality({
            "bar_count": 5,
            "momentum20": None,
            "volatility20": None,
        })

        self.assertEqual(personality["label"], "INSUFFICIENT HISTORY")
        self.assertEqual(personality["confidence"], 0)

    def test_stock_page_renders_normalized_fundamentals_results_news_and_actions(self):
        now = timezone.now()
        StockFundamentalSnapshot.objects.create(
            symbol="TEST",
            provider="APPROVED",
            source_at=now,
            pe_ratio=20,
            return_on_equity=18,
        )
        StockFinancialResult.objects.create(
            symbol="TEST",
            provider="APPROVED",
            period_end=timezone.localdate(),
            period_type="QUARTER",
            net_profit=100,
            source_at=now,
        )
        StockNewsItem.objects.create(
            symbol="TEST",
            provider="APPROVED",
            source_ref="news-1",
            headline="Test company reports results",
            published_at=now,
        )
        StockCorporateAction.objects.create(
            symbol="TEST",
            provider="APPROVED",
            source_ref="action-1",
            action_type="DIVIDEND",
            ex_date=timezone.localdate(),
            description="Dividend announced",
            source_at=now,
        )

        response = self.client.get(reverse("stock_option_detail", args=["TEST"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fundamentals")
        self.assertContains(response, "Test company reports results")
        self.assertContains(response, "Dividend announced")
        self.assertContains(response, "Missing-data penalty")
        self.assertContains(response, "Multi-timeframe candlesticks")
        self.assertContains(response, "Attributed event timeline")

    def test_event_timeline_calculates_observed_one_and_five_session_reactions(self):
        start = timezone.make_aware(
            datetime.combine(timezone.localdate()-timedelta(days=10), time(9)),
        )
        for index in range(7):
            StockEquityCandle.objects.create(
                symbol="TEST",
                interval_minutes=1440,
                timestamp=start + timedelta(days=index),
                open=100 + index,
                high=101 + index,
                low=99 + index,
                close=100 + index,
                volume=1000,
            )
        event_at = start + timedelta(days=2, hours=12)
        StockNewsItem.objects.create(
            symbol="TEST",
            provider="APPROVED",
            source_ref="reaction-news",
            headline="Attributed test event",
            published_at=event_at,
        )

        timeline = event_timeline("TEST")

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["reaction"]["baseline_close"], 101)
        self.assertEqual(timeline[0]["reaction"]["one_session_percent"], 0.99)
        self.assertEqual(timeline[0]["reaction"]["five_session_percent"], 4.95)
        self.assertEqual(timeline[0]["reaction"]["observed_sessions"], 5)

    def test_pattern_labels_fail_closed_with_missing_history(self):
        self.assertEqual(
            pattern_labels("MISSING")[0]["label"],
            "INSUFFICIENT PATTERN HISTORY",
        )

    def test_timeframe_agreement_requires_all_tracked_timeframes(self):
        rows = [
            {"label": "15 minute", "state": "BULLISH"},
            {"label": "1 hour", "state": "BULLISH"},
            {"label": "Daily", "state": "BULLISH"},
        ]
        self.assertEqual(
            timeframe_agreement(rows)["label"],
            "BULLISH AGREEMENT",
        )
        rows[-1]["state"] = "INSUFFICIENT"
        self.assertEqual(
            timeframe_agreement(rows)["label"],
            "INSUFFICIENT TIMEFRAME HISTORY",
        )

    def test_automatic_trade_levels_fail_closed_without_structure(self):
        levels = automatic_trade_levels({
            "latest": 100,
            "sma20": None,
            "sma50": None,
            "support": None,
            "resistance": None,
            "atr20": None,
        })

        self.assertFalse(levels["available"])
        self.assertIn("At least 50 closes", levels["reason"])

    def test_automatic_trade_levels_support_bearish_structure(self):
        levels = automatic_trade_levels({
            "latest": 90,
            "sma20": 95,
            "sma50": 100,
            "support": 85,
            "resistance": 98,
            "atr20": 2,
        })

        self.assertTrue(levels["available"])
        self.assertEqual(levels["direction"], "SHORT")
        self.assertEqual(levels["entry"], 90)
        self.assertEqual(levels["stop"], 93)
        self.assertEqual(levels["target"], 84)

    def test_score_refresh_is_dry_run_by_default_and_idempotent_when_applied(self):
        output = StringIO()
        call_command("refresh_stock_scores", "--symbols", "TEST", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["mode"], "dry-run")
        self.assertEqual(StockScoreSnapshot.objects.count(), 0)

        call_command("refresh_stock_scores", "--symbols", "TEST", "--apply", stdout=StringIO())
        call_command("refresh_stock_scores", "--symbols", "TEST", "--apply", stdout=StringIO())

        self.assertEqual(StockScoreSnapshot.objects.count(), 3)
