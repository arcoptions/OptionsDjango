from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .command_centre import (
    command_centre_context,
    derive_regime,
    market_session_context,
)
from .models import (
    EconomicEvent,
    IndexOISnapshot,
    StockFundamentalSnapshot,
    StockScoreSnapshot,
)
from .radar_models import RadarInstrument, RadarQuote, RadarSector


class CommandCentreTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="command-centre-operator",
            password="test-password",
            is_staff=True,
        )

    def test_regime_requires_index_and_breadth_confirmation(self):
        self.assertEqual(derive_regime(1.2, 0.8)["label"], "BULLISH / RISK-ON")
        self.assertEqual(derive_regime(-1.2, -0.8)["label"], "BEARISH / RISK-OFF")
        self.assertEqual(derive_regime(0.1, 0.1)["label"], "SIDEWAYS / RANGE")
        self.assertEqual(derive_regime(1.0, -0.5)["label"], "MIXED / DIVERGENT")
        self.assertEqual(derive_regime(None, 0.5)["label"], "INSUFFICIENT DATA")

    def test_market_session_context_classifies_five_windows(self):
        zone = ZoneInfo("Asia/Kolkata")
        cases = [
            ((2026, 11, 9, 8, 30), "PRE_MARKET"),
            ((2026, 11, 9, 9, 15), "OPENING_DRIVE"),
            ((2026, 11, 9, 11, 0), "MIDDAY"),
            ((2026, 11, 9, 14, 30), "POWER_HOUR"),
            ((2026, 11, 9, 16, 0), "AFTER_HOURS"),
        ]

        for parts, expected in cases:
            with self.subTest(expected=expected):
                context = market_session_context(datetime(*parts, tzinfo=zone))
                self.assertEqual(context["key"], expected)
                self.assertEqual(
                    [row["key"] for row in context["sessions"] if row["active"]],
                    [expected],
                )

    def test_market_session_context_closes_weekends_and_verified_holidays(self):
        zone = ZoneInfo("Asia/Kolkata")
        weekend = market_session_context(
            datetime(2026, 11, 8, 11, 0, tzinfo=zone),
        )
        self.assertEqual(weekend["key"], "AFTER_HOURS")
        self.assertEqual(weekend["calendar_state"], "WEEKEND")
        self.assertFalse(weekend["market_open"])

        holiday_at = datetime(2026, 11, 9, 9, 0, tzinfo=zone)
        EconomicEvent.objects.create(
            title="NSE trading holiday",
            event_type="MARKET_HOLIDAY",
            starts_at=holiday_at,
            importance="HIGH",
            provider="APPROVED_TEST",
            source_ref="holiday-2026-11-09",
        )
        holiday = market_session_context(
            datetime(2026, 11, 9, 11, 0, tzinfo=zone),
        )
        self.assertEqual(holiday["key"], "AFTER_HOURS")
        self.assertEqual(holiday["calendar_state"], "VERIFIED_HOLIDAY")
        self.assertEqual(
            holiday["calendar_source"],
            "APPROVED_TEST: NSE trading holiday",
        )
        self.assertFalse(holiday["market_open"])

    def test_context_combines_fresh_index_and_equity_breadth(self):
        now = timezone.now()
        snapshot = IndexOISnapshot.objects.create(
            underlying="NIFTY",
            underlying_price=202,
            underlying_change=2,
            pcr=1.1,
        )
        IndexOISnapshot.objects.filter(pk=snapshot.pk).update(created_at=now)
        instrument = RadarInstrument.objects.create(
            security_id="1",
            segment="NSE_EQ",
            symbol="TEST",
            kind="EQUITY",
        )
        RadarQuote.objects.create(
            instrument=instrument,
            source_at=now,
            received_at=now,
            last=102,
            previous_close=100,
        )

        context = command_centre_context(now)

        self.assertEqual(context["indexes"][0]["change_percent"], 1.0)
        self.assertEqual(context["breadth"]["advances"], 1)
        self.assertEqual(context["regime"]["label"], "BULLISH / RISK-ON")
        self.assertTrue(context["scenarios"][0]["active"])

    def test_stale_quotes_do_not_enter_breadth(self):
        now = timezone.now()
        instrument = RadarInstrument.objects.create(
            security_id="2",
            segment="NSE_EQ",
            symbol="STALE",
            kind="EQUITY",
        )
        RadarQuote.objects.create(
            instrument=instrument,
            source_at=now - timedelta(minutes=1),
            received_at=now - timedelta(minutes=1),
            last=102,
            previous_close=100,
        )

        self.assertEqual(command_centre_context(now)["breadth"]["total"], 0)

    def test_breadth_reports_coverage_gaps_distribution_and_industries(self):
        now = timezone.now()
        quote_rows = [
            ("ADVANCE", 103, 100, 102, 99, 104, "Banking"),
            ("DECLINE", 97, 100, 98, 96, 101, "Technology"),
            ("FLAT", 100, 100, 100, 100, 100, "Banking"),
        ]
        for security_id, (
            symbol,
            last,
            previous_close,
            day_open,
            day_low,
            day_high,
            industry,
        ) in enumerate(quote_rows, start=10):
            instrument = RadarInstrument.objects.create(
                security_id=str(security_id),
                segment="NSE_EQ",
                symbol=symbol,
                kind="EQUITY",
            )
            RadarQuote.objects.create(
                instrument=instrument,
                source_at=now,
                received_at=now,
                last=last,
                previous_close=previous_close,
                day_open=day_open,
                day_low=day_low,
                day_high=day_high,
            )
            RadarSector.objects.create(
                instrument=instrument,
                industry=industry,
                source_url=f"https://example.test/{symbol}",
            )
        RadarInstrument.objects.create(
            security_id="99",
            segment="NSE_EQ",
            symbol="NO_QUOTE",
            kind="EQUITY",
        )

        breadth = command_centre_context(now)["breadth"]

        self.assertEqual(breadth["total"], 3)
        self.assertEqual(breadth["active_universe"], 4)
        self.assertEqual(breadth["coverage_percent"], 75.0)
        self.assertEqual(breadth["advance_percent"], 33.3)
        self.assertEqual(breadth["decline_percent"], 33.3)
        self.assertEqual(breadth["unchanged_percent"], 33.3)
        self.assertEqual(breadth["advance_decline_ratio"], 1.0)
        self.assertEqual(
            breadth["gaps"],
            {
                "threshold_percent": 0.1,
                "up": 1,
                "down": 1,
                "filled": 2,
                "unfilled": 0,
            },
        )
        self.assertEqual(
            [row["count"] for row in breadth["price_distribution"]],
            [1, 0, 0, 1, 0, 1],
        )
        self.assertEqual(breadth["sectors"][0]["name"], "Banking")
        self.assertEqual(breadth["sectors"][0]["total"], 2)
        self.assertEqual(len(breadth["unavailable"]), 4)

    def test_context_ranks_only_current_evidence_backed_investments(self):
        now = timezone.now()
        for symbol, score, confidence, age_days in [
            ("CURRENT", 82, 1, 10),
            ("SECOND", 74, 0.8, 20),
            ("LOWCOVERAGE", 95, 0.4, 5),
            ("STALE", 99, 1, 121),
        ]:
            source_at = now - timedelta(days=age_days)
            StockFundamentalSnapshot.objects.create(
                symbol=symbol,
                provider="APPROVED_TEST",
                source_at=source_at,
            )
            StockScoreSnapshot.objects.create(
                symbol=symbol,
                score_type="INVESTMENT",
                version="ARC-STOCK-1",
                score=score,
                confidence=confidence,
                components={"return_on_equity": score},
                missing_data=[],
                source_at=source_at,
            )

        rows = command_centre_context(now)["investment_opportunities"]

        self.assertEqual([row["symbol"] for row in rows], ["CURRENT", "SECOND"])
        self.assertEqual(rows[0]["provider"], "APPROVED_TEST")
        self.assertEqual(rows[0]["evidence"], ["Return On Equity 82.0"])
        self.assertEqual(rows[0]["detail_url"], reverse("stock_option_detail", args=["CURRENT"]))

    def test_home_requires_login_and_renders_command_centre(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "9 AM Command Centre")
        self.assertContains(response, "Market session")
        self.assertContains(response, "Ranked opportunities")
        self.assertContains(response, "Ranked long-term investment research")
        self.assertContains(response, "Personal trading desk")
