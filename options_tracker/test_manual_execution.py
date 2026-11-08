"""Regression tests for the reviewed manual Dhan order path."""

import os
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    DhanOrderEvent,
    Direction,
    SignalStatus,
    TipSignal,
    TradeExecution,
    TradeExecutionMode,
)


class ManualExecutionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="operator",
            password="test-password",
            is_staff=True,
        )
        self.signal = TipSignal.objects.create(
            source_type="ENGINE",
            source_name="nifty_trail",
            option_symbol="NIFTY 24500 CE",
            security_id="44556",
            direction=Direction.CE,
            entry_price=Decimal("120"),
            stop_loss=Decimal("108"),
            target_1=Decimal("150"),
            status=SignalStatus.NEW,
        )
        self.url = reverse("dhan_orders")

    def test_anonymous_users_cannot_reach_broker_orders(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    @patch("options_tracker.views.get_dhan_credentials", return_value=("token", "12345678"))
    @patch("options_tracker.views.place_super_order")
    def test_review_displays_the_exact_order_without_submitting(self, place_order, _credentials):
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {
                "action": "preview",
                "signal_id": self.signal.id,
                "quantity": 65,
                "journal_reason": "Validated breakout with bounded risk.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm Live Dhan Order")
        self.assertContains(response, "780.00")
        self.assertContains(response, "...5678")
        place_order.assert_not_called()

    @patch("options_tracker.views.get_dhan_credentials", return_value=("token", "12345678"))
    @patch("options_tracker.views.place_super_order")
    def test_confirmed_review_places_and_audits_the_order(self, place_order, _credentials):
        place_order.return_value = {
            "ok": True,
            "order_id": "ORDER-1",
            "correlation_id": "arc-test",
        }
        self.client.force_login(self.user)
        preview = self.client.post(
            self.url,
            {
                "action": "preview",
                "signal_id": self.signal.id,
                "quantity": 65,
                "journal_reason": "Validated breakout with bounded risk.",
            },
        )
        token = preview.context["order_preview"]["token"]

        response = self.client.post(
            self.url,
            {
                "action": "confirm",
                "order_token": token,
                "confirm_live_order": "1",
            },
        )

        self.assertRedirects(response, self.url)
        place_order.assert_called_once_with(self.signal, 65)
        execution = TradeExecution.objects.get(dhan_order_id="ORDER-1")
        self.assertEqual(execution.quantity, 65)
        self.assertEqual(execution.execution_mode, TradeExecutionMode.MANUAL)
        event = DhanOrderEvent.objects.get(status="MANUAL_CONFIRM")
        self.assertEqual(event.payload_json["actor"], "operator")
        self.assertEqual(event.payload_json["max_loss"], 780.0)

    @patch("options_tracker.views.get_dhan_credentials", return_value=("token", "12345678"))
    @patch("options_tracker.views.place_super_order")
    def test_confirmation_checkbox_is_required_server_side(self, place_order, _credentials):
        self.client.force_login(self.user)
        preview = self.client.post(
            self.url,
            {
                "action": "preview",
                "signal_id": self.signal.id,
                "quantity": 65,
                "journal_reason": "Validated breakout with bounded risk.",
            },
        )
        token = preview.context["order_preview"]["token"]

        response = self.client.post(
            self.url,
            {"action": "confirm", "order_token": token},
        )

        self.assertRedirects(response, self.url)
        place_order.assert_not_called()
        self.assertFalse(TradeExecution.objects.exists())

    @patch("options_tracker.views.get_dhan_credentials", return_value=("token", "12345678"))
    @patch("options_tracker.views.place_super_order")
    def test_changed_terms_require_a_fresh_review(self, place_order, _credentials):
        self.client.force_login(self.user)
        preview = self.client.post(
            self.url,
            {
                "action": "preview",
                "signal_id": self.signal.id,
                "quantity": 65,
                "journal_reason": "Validated breakout with bounded risk.",
            },
        )
        token = preview.context["order_preview"]["token"]
        self.signal.entry_price = Decimal("125")
        self.signal.save(update_fields=["entry_price"])

        response = self.client.post(
            self.url,
            {
                "action": "confirm",
                "order_token": token,
                "confirm_live_order": "1",
            },
            follow=True,
        )

        self.assertContains(response, "signal terms changed after review")
        place_order.assert_not_called()

    @patch("options_tracker.views.get_dhan_credentials", return_value=("token", "12345678"))
    @patch("options_tracker.views.place_super_order")
    def test_preview_mode_blocks_confirmed_manual_order(self, place_order, _credentials):
        self.client.force_login(self.user)
        with patch.dict(os.environ, {"ARC_EXECUTION_MODE": "preview"}):
            preview = self.client.post(
                self.url,
                {
                    "action": "preview",
                    "signal_id": self.signal.id,
                    "quantity": 65,
                    "journal_reason": "Validated breakout with bounded risk.",
                },
            )
            response = self.client.post(
                self.url,
                {
                    "action": "confirm",
                    "order_token": preview.context["order_preview"]["token"],
                    "confirm_live_order": "1",
                },
                follow=True,
            )

        self.assertContains(response, "Broker orders are blocked in preview mode")
        place_order.assert_not_called()
        self.assertFalse(DhanOrderEvent.objects.exists())
        self.assertFalse(TradeExecution.objects.exists())
