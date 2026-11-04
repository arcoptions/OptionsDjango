from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta, timezone
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import websocket
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from .cas_nowcast import (
    CAS_INDEX_PACKET_SIZE,
    CAS_PACKET_SIZE,
    CasFeedDisconnect,
    CasIndexPacket,
    CasInstrumentPacket,
    DhanCasClient,
    NiftyCloseNowcaster,
    cas_connection_state,
    decode_cas_packet,
    parse_cas_index_packet,
    parse_cas_instrument_packet,
    reconnect_delay_seconds,
)
from .models import AppSetting, CasAuctionEvent, DhanOrderEvent


class DhanCasClientTests(SimpleTestCase):
    def setUp(self):
        self.on_packet = Mock()
        self.on_error = Mock()
        self.app = Mock()
        self.client = DhanCasClient(
            "test-token", "test-client", ["1333"], self.on_packet, self.on_error,
        )

    def test_uses_documented_cas_url_and_subscription(self):
        self.assertEqual(parse_qs(urlsplit(self.client.url).query), {
            "token": ["test-token"], "clientId": ["test-client"], "authType": ["2"],
        })
        self.client._on_open(self.app)
        self.assertEqual(json.loads(self.app.send.call_args.args[0]), {
            "RequestCode": 23,
            "InstrumentCount": 1,
            "InstrumentList": [{"ExchangeSegment": "NSE_EQ", "SecurityId": "1333"}],
        })

    def test_reports_server_disconnect_reasons_without_counting_market_data(self):
        for reason_code in (800, 804, 805, 806, 807, 808, 809, 810, 999):
            with self.subTest(reason_code=reason_code):
                payload = struct.pack("<BHBIh", 50, 10, 0, 0, reason_code)
                self.client._on_data(self.app, payload, websocket.ABNF.OPCODE_BINARY, True)
                self.assertIsInstance(self.client.last_error, CasFeedDisconnect)
                self.assertIn(str(reason_code), self.on_error.call_args.args[0])
                self.assertFalse(self.client.received_packet)
        self.on_packet.assert_not_called()
        self.assertEqual(self.app.close.call_count, 9)

    def test_generic_socket_error_preserves_server_reason(self):
        payload = struct.pack("<BHBIh", 50, 10, 0, 0, 807)
        self.client._on_data(self.app, payload, websocket.ABNF.OPCODE_BINARY, True)
        self.client._on_error(self.app, RuntimeError("Connection to remote host was lost."))
        self.assertEqual(self.client.last_error.reason_code, 807)
        self.on_error.assert_called_once()

    def test_connection_limit_disconnect_uses_long_cooldown(self):
        self.assertEqual(reconnect_delay_seconds(
            CasFeedDisconnect(805), received_packet=False, configured_delay=5,
        ), 120)

    def test_delivers_valid_index_packet(self):
        payload = struct.pack("<HBB21sd", 33, 42, 0, b"NIFTY 50", 23157.15)
        self.client._on_data(self.app, payload, websocket.ABNF.OPCODE_BINARY, True)
        self.assertTrue(self.client.received_packet)
        self.assertAlmostEqual(self.on_packet.call_args.args[0].indicative_close, 23157.15)
        self.on_error.assert_not_called()


class CasPacketDecoderTests(SimpleTestCase):
    def test_decodes_documented_instrument_packet_offsets(self):
        payload = bytearray(CAS_PACKET_SIZE)
        struct.pack_into("<BHBI", payload, 0, 41, 128, 1, 1333)
        struct.pack_into("<fii", payload, 8, 1450.25, 120_000, 90_000)
        for index in range(5):
            struct.pack_into(
                "<iihhff",
                payload,
                20 + index * 20,
                100 + index,
                80 + index,
                4 + index,
                3 + index,
                1450.0 - index,
                1450.5 + index,
            )
        struct.pack_into("<if", payload, 120, 30_000, 1442.75)

        packet = parse_cas_instrument_packet(bytes(payload))

        self.assertEqual(packet.security_id, 1333)
        self.assertAlmostEqual(packet.indicative_equilibrium_price, 1450.25)
        self.assertEqual(packet.total_buy_quantity, 120_000)
        self.assertEqual(packet.depth[4].ask_orders, 7)
        self.assertAlmostEqual(packet.reference_price, 1442.75)

    def test_decodes_special_index_packet_order(self):
        payload = bytearray(CAS_INDEX_PACKET_SIZE)
        struct.pack_into("<HBB21sd", payload, 0, 33, 42, 0, b"NIFTY 50", 25123.45)

        packet = parse_cas_index_packet(bytes(payload))

        self.assertEqual(packet.message_length, 33)
        self.assertEqual(packet.response_code, 42)
        self.assertEqual(packet.index_name, "NIFTY 50")
        self.assertAlmostEqual(packet.indicative_close, 25123.45)
        self.assertIsInstance(decode_cas_packet(bytes(payload)), CasIndexPacket)

    def test_rejects_undocumented_packet_size(self):
        with self.assertRaisesRegex(ValueError, "Unsupported CAS packet length"):
            decode_cas_packet(b"\0" * 12)


class NiftyCloseNowcasterTests(SimpleTestCase):
    def test_uses_latest_indicative_close_as_untrained_prediction(self):
        nowcaster = NiftyCloseNowcaster()
        start = datetime(2026, 10, 13, 9, 55, tzinfo=timezone.utc)
        values = [25000.0, 25001.0, 25002.0]
        snapshot = None
        for index, value in enumerate(values):
            snapshot = nowcaster.update_index(
                CasIndexPacket(33, 42, 0, "NIFTY 50", value),
                start + timedelta(seconds=index * 10),
            )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["predicted_close"], 25002.0)
        self.assertEqual(snapshot["method"], "indicative_persistence_untrained")
        self.assertGreater(snapshot["experimental_momentum_close"], 25002.0)

    def test_reports_constituent_buy_sell_pressure_without_using_unknown_sign(self):
        nowcaster = NiftyCloseNowcaster()
        nowcaster.update_instrument(
            CasInstrumentPacket(41, 128, 1, 1333, 100.0, 75, 25, (), 50, 99.0)
        )
        observed_at = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
        snapshot = nowcaster.update_index(
            CasIndexPacket(33, 42, 0, "NIFTY 50", 25000.0),
            observed_at,
        )

        self.assertEqual(snapshot["constituent_pressure"], 0.5)
        self.assertEqual(snapshot["constituents_seen"], 1)

    def test_controlled_collector_arms_before_exchange_window(self):
        state, message = cas_connection_state(
            datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc)
        )

        self.assertEqual(state, "ARMED")
        self.assertIn("3:14 PM", message)

    def test_controlled_collector_opens_during_cas_window(self):
        state, _message = cas_connection_state(
            datetime(2026, 10, 13, 9, 50, tzinfo=timezone.utc)
        )

        self.assertEqual(state, "OPEN")

    def test_reconnect_waits_past_dhan_stale_socket_retention(self):
        delay = reconnect_delay_seconds(
            RuntimeError("connection lost"),
            received_packet=False,
            configured_delay=5,
        )

        self.assertEqual(delay, 45)

    def test_rate_limit_uses_long_cooldown(self):
        delay = reconnect_delay_seconds(
            RuntimeError("Handshake status 429 Too Many Requests"),
            received_packet=False,
            configured_delay=5,
        )

        self.assertEqual(delay, 120)

    def test_post_packet_disconnect_uses_normal_backoff(self):
        delay = reconnect_delay_seconds(
            RuntimeError("connection lost"),
            received_packet=True,
            configured_delay=5,
        )

        self.assertEqual(delay, 10)


class CasDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cas-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_dashboard_is_a_separate_staff_page(self):
        response = self.client.get(reverse("cas_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NIFTY Closing Auction")
        self.assertContains(response, "Start CAS collector")

    def test_start_button_enables_deployment_managed_collector(self):
        response = self.client.post(reverse("cas_dashboard"), {"action": "start"})

        self.assertRedirects(response, reverse("cas_dashboard"))
        self.assertEqual(AppSetting.objects.get(key="nifty_cas_enabled").value, "1")
        event = DhanOrderEvent.objects.get(status="CAS_SWITCH")
        self.assertEqual(event.payload_json["action"], "start")
        self.assertEqual(event.payload_json["actor"], "cas-admin")

    def test_status_api_reports_saved_tuning_data(self):
        AppSetting.objects.create(
            key="nifty_cas_nowcast",
            value=json.dumps({
                "indicative_close": 25100.25,
                "predicted_close": 25100.25,
                "constituents_seen": 50,
            }),
        )
        CasAuctionEvent.objects.create(
            session_date=dj_timezone.localdate(),
            observed_at=dj_timezone.now(),
            event_type=CasAuctionEvent.EventType.NOWCAST,
            index_name="NIFTY 50",
            payload_json={"predicted_close": 25100.25},
        )

        payload = self.client.get(reverse("cas_status_api")).json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["event_count"], 1)
        self.assertEqual(payload["nowcast_count"], 1)
        self.assertEqual(payload["nowcast"]["constituents_seen"], 50)

    def test_non_staff_user_cannot_start_collector(self):
        self.client.logout()
        user = get_user_model().objects.create_user(
            username="cas-viewer",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("cas_dashboard"), {"action": "start"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AppSetting.objects.filter(key="nifty_cas_enabled").exists())

    def test_collector_reloads_credentials_before_each_connection(self):
        command_module = "options_tracker.management.commands.run_cas_nowcast"
        with (
            TemporaryDirectory() as output_dir,
            patch(f"{command_module}.get_dhan_credentials", side_effect=[
                ("startup-token", "test-client"),
                ("first-token", "test-client"),
                ("renewed-token", "test-client"),
            ]),
            patch(f"{command_module}.DhanCasClient") as client_class,
            patch(f"{command_module}.time.sleep"),
        ):
            client_class.return_value.run.side_effect = [None, KeyboardInterrupt]
            client_class.return_value.last_error = None
            client_class.return_value.received_packet = False
            call_command(
                "run_cas_nowcast",
                security_ids="1333",
                output_dir=output_dir,
                stdout=StringIO(),
                stderr=StringIO(),
            )

        self.assertEqual(
            [call.kwargs["access_token"] for call in client_class.call_args_list],
            ["first-token", "renewed-token"],
        )
