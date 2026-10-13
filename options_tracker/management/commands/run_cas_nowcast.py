"""Run the Dhan Closing Auction Session recorder and Nifty nowcaster."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections
from django.utils import timezone

from options_tracker.cas_nowcast import (
    CasIndexPacket,
    CasInstrumentPacket,
    DhanCasClient,
    JsonlCasRecorder,
    NiftyCloseNowcaster,
    cas_connection_state,
    resolve_nifty_security_ids,
)
from options_tracker.models import AppSetting, CasAuctionEvent
from options_tracker.services import get_dhan_credentials


class Command(BaseCommand):
    help = (
        "Record Dhan's CAS stream and publish a conservative live Nifty close nowcast. "
        "This command never places orders."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--security-ids",
            help="Comma-separated NSE equity security IDs; defaults to current Nifty constituents.",
        )
        parser.add_argument(
            "--output-dir",
            type=Path,
            help="JSONL/output directory (default: NIFTY_CAS_OUTPUT_DIR or research/cas).",
        )
        parser.add_argument(
            "--refresh-master",
            action="store_true",
            help="Refresh Dhan's instrument master before resolving constituents.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Open one connection and fail if it closes; intended for diagnostics.",
        )
        parser.add_argument(
            "--reconnect-delay",
            type=float,
            default=5.0,
            help="Seconds to wait before reconnecting when Dhan closes the feed.",
        )
        parser.add_argument(
            "--controlled",
            action="store_true",
            help="Connect only while the dashboard collector switch is enabled.",
        )

    def handle(self, *args, **options) -> None:
        access_token, client_id = get_dhan_credentials()
        if not access_token or not client_id:
            raise CommandError("Dhan credentials are not configured")

        explicit_ids = [
            value.strip()
            for value in (options["security_ids"] or "").split(",")
            if value.strip()
        ]
        if explicit_ids:
            security_ids, missing, symbol_source = explicit_ids, [], "command_line"
        else:
            try:
                security_ids, missing, symbol_source = resolve_nifty_security_ids(
                    refresh_master=options["refresh_master"]
                )
            except (RuntimeError, OSError, ValueError) as error:
                raise CommandError(str(error)) from error

        output_dir = options["output_dir"]
        if output_dir is None:
            configured = os.getenv("NIFTY_CAS_OUTPUT_DIR", "research/cas")
            output_dir = Path(configured)
        else:
            output_dir = Path(output_dir)
        if not output_dir.is_absolute():
            output_dir = settings.BASE_DIR / output_dir

        session_date = timezone.localdate().isoformat()
        recorder = JsonlCasRecorder(output_dir, session_date)
        nowcaster = NiftyCloseNowcaster()
        last_heartbeat_at = 0.0
        self.stdout.write(
            self.style.SUCCESS(
                f"CAS observe-only feed: {len(security_ids)} instruments; "
                f"symbols={symbol_source}; output={output_dir}"
            )
        )
        if missing:
            self.stderr.write(
                self.style.WARNING(f"Unresolved Nifty symbols: {', '.join(missing)}")
            )

        def collector_enabled() -> bool:
            if not options["controlled"]:
                return True
            value = (
                AppSetting.objects.filter(key="nifty_cas_enabled")
                .values_list("value", flat=True)
                .first()
                or ""
            )
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        def update_status(state: str, message: str = "", *, force: bool = False) -> None:
            nonlocal last_heartbeat_at
            now = time.monotonic()
            if not force and now - last_heartbeat_at < 5:
                return
            AppSetting.objects.update_or_create(
                key="nifty_cas_collector_status",
                defaults={"value": json.dumps({
                    "state": state,
                    "message": message,
                    "at": timezone.localtime().isoformat(),
                    "controlled": options["controlled"],
                }, separators=(",", ":"))},
            )
            last_heartbeat_at = now

        def persist_event(
            event_type: str,
            observed_at,
            *,
            security_id: str = "",
            index_name: str = "",
            payload: dict,
        ) -> None:
            try:
                CasAuctionEvent.objects.create(
                    session_date=timezone.localdate(observed_at),
                    observed_at=observed_at,
                    event_type=event_type,
                    security_id=security_id,
                    index_name=index_name,
                    payload_json=payload,
                )
            except DatabaseError as error:
                close_old_connections()
                recorder.record_error(f"Database persistence failed: {error}", observed_at)
                self.stderr.write(f"[{observed_at:%H:%M:%S}] database persistence failed: {error}")

        def on_packet(
            packet: CasInstrumentPacket | CasIndexPacket,
            observed_at,
            raw_data: bytes,
        ) -> None:
            recorder.record_packet(packet, observed_at, raw_data)
            if isinstance(packet, CasInstrumentPacket):
                persist_event(
                    CasAuctionEvent.EventType.INSTRUMENT,
                    observed_at,
                    security_id=str(packet.security_id),
                    payload=asdict(packet),
                )
                nowcaster.update_instrument(packet)
                update_status("LIVE", "Receiving CAS instrument packets")
                return
            persist_event(
                CasAuctionEvent.EventType.INDEX,
                observed_at,
                index_name=packet.index_name,
                payload=asdict(packet),
            )
            if "NIFTY 50" not in packet.index_name.upper():
                return
            snapshot = nowcaster.update_index(packet, observed_at)
            recorder.publish_nowcast(snapshot)
            persist_event(
                CasAuctionEvent.EventType.NOWCAST,
                observed_at,
                index_name=packet.index_name,
                payload=snapshot,
            )
            AppSetting.objects.update_or_create(
                key="nifty_cas_nowcast",
                defaults={"value": json.dumps(snapshot, separators=(",", ":"))},
            )
            update_status("LIVE", "Receiving Nifty indicative-close packets")
            self.stdout.write(
                f"[{observed_at:%H:%M:%S}] indicative={snapshot['indicative_close']:.2f} "
                f"baseline={snapshot['predicted_close']:.2f} "
                f"experimental={snapshot['experimental_momentum_close']:.2f} "
                f"constituents={snapshot['constituents_seen']}"
            )

        def on_error(message: str, observed_at) -> None:
            recorder.record_error(message, observed_at)
            persist_event(
                CasAuctionEvent.EventType.ERROR,
                observed_at,
                payload={"message": message},
            )
            update_status("ERROR", message, force=True)
            self.stderr.write(f"[{observed_at:%H:%M:%S}] {message}")

        client = DhanCasClient(
            access_token=access_token,
            client_id=client_id,
            security_ids=security_ids,
            on_packet=on_packet,
            on_error=on_error,
            should_continue=collector_enabled,
        )
        while True:
            if not collector_enabled():
                update_status(
                    "STOPPED",
                    "Waiting for Start collector on the CAS dashboard",
                    force=True,
                )
                if options["once"]:
                    raise CommandError("CAS collector is disabled")
                time.sleep(max(1.0, options["reconnect_delay"]))
                close_old_connections()
                continue
            if options["controlled"]:
                window_state, window_message = cas_connection_state(timezone.now())
                if window_state != "OPEN":
                    update_status(window_state, window_message, force=True)
                    if options["once"]:
                        raise CommandError(window_message)
                    time.sleep(max(1.0, options["reconnect_delay"]))
                    close_old_connections()
                    continue
            update_status("CONNECTING", "Opening Dhan CAS feed", force=True)
            try:
                client.run()
            except KeyboardInterrupt:
                self.stdout.write("Stopping CAS feed")
                client.close()
                break
            except Exception as error:
                on_error(f"CAS connection failed: {error}", timezone.localtime())
                if options["once"]:
                    raise CommandError(str(error)) from error
            finally:
                client.close()

            if options["once"]:
                raise CommandError(
                    "Dhan closed the CAS feed. Outside the auction window this is expected; "
                    "run without --once to reconnect automatically."
                )
            delay = max(1.0, options["reconnect_delay"])
            if collector_enabled():
                update_status(
                    "RECONNECTING",
                    f"CAS feed disconnected; retrying in {delay:g} seconds",
                    force=True,
                )
                self.stderr.write(f"CAS feed disconnected; reconnecting in {delay:g} seconds")
            else:
                update_status("STOPPED", "Collector stopped from dashboard", force=True)
            time.sleep(delay)
            close_old_connections()
