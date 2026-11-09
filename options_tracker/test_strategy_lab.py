import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DhanOrderEvent, StockEquityCandle, StrategyStatus
from .strategy_lab import (
    create_definition,
    create_manifest,
    promote_manifest,
    promotion_assessment,
    validate_strategy,
)


class StrategyLabTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="researcher",
            password="test-password",
            is_staff=True,
        )
        self.start = timezone.now().replace(second=0, microsecond=0)
        StockEquityCandle.objects.create(
            symbol="TEST",
            interval_minutes=15,
            timestamp=self.start,
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=1000,
        )
        self.definition = create_definition(
            owner=self.staff,
            name="Momentum",
            template="EQUITY_MOMENTUM",
            symbol="test",
            interval_minutes=15,
            parameters={
                "lookback": 20,
                "minimum_move_pct": 5,
                "minimum_volume_ratio": 1.5,
            },
            cost_model={
                "brokerage_per_order": 20,
                "tax_bps": 5,
                "slippage_bps": 5,
                "spread_bps": 5,
            },
        )

    def test_strategy_validation_rejects_unknown_fields_and_templates(self):
        with self.assertRaises(ValueError):
            validate_strategy("EXEC_CODE", {}, {})
        with self.assertRaises(ValueError):
            validate_strategy(
                "EQUITY_MOMENTUM",
                {
                    "lookback": 20,
                    "minimum_move_pct": 5,
                    "minimum_volume_ratio": 1,
                    "__class__": 1,
                },
                {
                    "brokerage_per_order": 20,
                    "tax_bps": 5,
                    "slippage_bps": 5,
                    "spread_bps": 5,
                },
            )

    def test_manifest_is_deduplicated_and_immutable(self):
        end = self.start + timedelta(minutes=15)
        first = create_manifest(self.definition, self.start, end)
        second = create_manifest(self.definition, self.start, end)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.data_rows, 1)
        first.code_version = "changed"
        with self.assertRaises(ValidationError):
            first.save()
        with self.assertRaises(ValidationError):
            first.delete()
        self.assertEqual(DhanOrderEvent.objects.count(), 0)

    def test_manifest_hash_changes_when_source_data_changes(self):
        first = create_manifest(
            self.definition,
            self.start,
            self.start + timedelta(minutes=15),
        )
        StockEquityCandle.objects.filter(pk=1).update(close=Decimal("110"))
        second = create_manifest(
            self.definition,
            self.start,
            self.start + timedelta(minutes=15),
        )

        self.assertNotEqual(first.data_hash, second.data_hash)
        self.assertNotEqual(first.manifest_hash, second.manifest_hash)

    @patch("options_tracker.strategy_lab.running_commit", return_value="packaged-sha")
    def test_manifest_prefers_packaged_commit_over_stale_environment(
        self,
        _running_commit,
    ):
        with patch.dict(os.environ, {"ARC_DEPLOYED_COMMIT": "stale-sha"}):
            manifest = create_manifest(
                self.definition,
                self.start,
                self.start + timedelta(minutes=15),
            )

        self.assertEqual(manifest.code_version, "packaged-sha")

    @patch("options_tracker.strategy_lab.running_commit", return_value="")
    def test_manifest_uses_environment_commit_without_package_stamp(
        self,
        _running_commit,
    ):
        with patch.dict(os.environ, {"ARC_DEPLOYED_COMMIT": "environment-sha"}):
            manifest = create_manifest(
                self.definition,
                self.start,
                self.start + timedelta(minutes=15),
            )

        self.assertEqual(manifest.code_version, "environment-sha")

    def test_manifest_stores_backtest_and_chronological_validation_results(self):
        for index in range(1, 61):
            price = Decimal(100 + index)
            StockEquityCandle.objects.create(
                symbol="TEST",
                interval_minutes=15,
                timestamp=self.start + timedelta(minutes=15 * index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 1,
                volume=1000,
            )
        definition = create_definition(
            owner=self.staff,
            name="Fast momentum",
            template="EQUITY_MOMENTUM",
            symbol="TEST",
            interval_minutes=15,
            parameters={
                "lookback": 2,
                "minimum_move_pct": 0,
                "minimum_volume_ratio": 0,
            },
            cost_model={
                "brokerage_per_order": 20,
                "tax_bps": 5,
                "slippage_bps": 5,
                "spread_bps": 5,
            },
        )

        manifest = create_manifest(
            definition,
            self.start,
            self.start + timedelta(minutes=15 * 61),
        )

        self.assertGreater(manifest.result_snapshot["trades"], 0)
        self.assertEqual(
            manifest.validation_snapshot["split"],
            "70/30 chronological",
        )
        self.assertEqual(manifest.validation_snapshot["status"], "COMPLETE")

    def test_strategy_lab_requires_staff_and_renders_library(self):
        url = reverse("strategy_lab")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.get(url)

        self.assertContains(response, "Strategy Lab")
        self.assertContains(response, "Momentum")

    def test_strategy_lab_posts_are_csrf_protected_and_create_manifests(self):
        url = reverse("strategy_lab")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(
            csrf_client.post(url, {"action": "create_manifest"}).status_code,
            403,
        )
        self.client.force_login(self.staff)
        response = self.client.post(url, {
            "action": "create_definition",
            "definition-name": "Opening range",
            "definition-template": "OPENING_RANGE",
            "definition-symbol": "test",
            "definition-interval_minutes": "15",
            "definition-parameters": json.dumps({
                "opening_minutes": 15,
                "breakout_pct": 1,
                "stop_pct": 2,
            }),
            "definition-cost_model": json.dumps({
                "brokerage_per_order": 20,
                "tax_bps": 5,
                "slippage_bps": 5,
                "spread_bps": 5,
            }),
        })
        manifest_response = self.client.post(url, {
            "action": "create_manifest",
            "manifest-definition": self.definition.pk,
            "manifest-data_start": timezone.localtime(self.start).strftime(
                "%Y-%m-%dT%H:%M",
            ),
            "manifest-data_end": (
                timezone.localtime(self.start) + timedelta(minutes=15)
            ).strftime("%Y-%m-%dT%H:%M"),
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(manifest_response.status_code, 302)
        self.assertEqual(self.definition.manifests.count(), 1)

    def test_promotion_requires_passing_immutable_validation_evidence(self):
        manifest = create_manifest(
            self.definition,
            self.start,
            self.start + timedelta(minutes=15),
        )

        self.assertFalse(promotion_assessment(manifest)["eligible"])
        with self.assertRaises(ValueError):
            promote_manifest(
                manifest=manifest,
                actor=self.staff,
                reason="Evidence was reviewed and approved for paper observation.",
            )
        self.definition.refresh_from_db()
        self.assertEqual(self.definition.status, StrategyStatus.DRAFT)

    def test_eligible_manifest_can_only_promote_paper_observation(self):
        manifest = create_manifest(
            self.definition,
            self.start,
            self.start + timedelta(minutes=15),
        )
        manifest.result_snapshot = {
            "trades": 30,
            "max_drawdown_pct": 10,
        }
        manifest.validation_snapshot = {
            "status": "COMPLETE",
            "validation": {
                "trades": 10,
                "expectancy_pct": 1.5,
                "max_drawdown_pct": 12,
            },
        }

        definition = promote_manifest(
            manifest=manifest,
            actor=self.staff,
            reason="Validation expectancy and drawdown support paper observation.",
        )

        self.assertEqual(definition.status, StrategyStatus.PAPER_APPROVED)
        self.assertEqual(definition.promotion_manifest, manifest)
        self.assertEqual(DhanOrderEvent.objects.count(), 0)
