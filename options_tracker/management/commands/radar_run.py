"""A single supervised, paper-only market worker with an exclusive database lease."""

from datetime import time as clock_time, timedelta
import json
import logging
import time
from urllib.parse import urlencode

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
import websocket

from options_tracker.models import RadarWorker
from options_tracker.radar import scan
from options_tracker.radar_feed import (
    CandleCollector, TickBuffer, acquire_worker, decode_packets, heartbeat, subscriptions,
)
from options_tracker.radar_history import credentials


LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Paper radar: --once evaluates stored data; --stream connects to Dhan. Never places orders."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--stream", action="store_true")
        parser.add_argument("--collect-only", action="store_true", help="Separate feed collection from radar_evaluate.")

    def handle(self, *_args, **options):
        owner = None
        try:
            if options["stream"]:
                credentials()
            if options["collect_only"] and not options["stream"]:
                raise CommandError("--collect-only requires --stream.")
            if not options["collect_only"] and RadarWorker.objects.filter(
                name="radar-evaluator", lease_until__gt=timezone.now(),
            ).exists():
                raise CommandError("Separate evaluator is active. Use --stream --collect-only to avoid duplicate decisions.")
            owner = acquire_worker(timezone.now())
            if options["once"]:
                result = scan(timezone.now())
                heartbeat(owner, "IDLE", json.dumps(result), timezone.now())
                self.stdout.write(f"PAPER ONLY: {result}")
            else:
                self.stream(owner, collect_only=options["collect_only"])
        except (ValueError, OSError, ConnectionError) as error:
            raise CommandError(str(error)) from error
        finally:
            if owner:
                RadarWorker.objects.filter(name="paper-radar", owner=owner).update(
                    lease_until=timezone.now(), state="STOPPED",
                )

    def stream(self, owner, *, collect_only=False):
        collector = CandleCollector(buffer_quotes=True)
        delay = 2
        while True:
            sock = None
            token = client_id = ""
            try:
                try:
                    token, client_id = credentials()
                except ValueError as error:
                    raise CommandError(str(error)) from error
                items = subscriptions(timezone.now())
                if not items:
                    raise CommandError("Radar universe is empty. Run radar_sync first.")
                # websocket-client exceptions can contain the URL; never log it with credentials.
                sock = websocket.create_connection(
                    "wss://api-feed.dhan.co?"+urlencode({"version": 2, "token": token, "clientId": client_id, "authType": 2}),
                    timeout=5,
                )
                active = {}
                ticks_waiting = TickBuffer()
                connected_at = timezone.now()
                last_fresh_at = None
                last_packet_at = None
                refresh_at = connected_at
                scan_at = timezone.now()
                while True:
                    now = timezone.now()
                    if now >= refresh_at:
                        wanted = {(item.segment, item.security_id): item for item in subscriptions(now)}
                        for code, keys in [(22, active.keys()-wanted.keys()), (21, wanted.keys()-active.keys())]:
                            entries = [{"ExchangeSegment": segment, "SecurityId": security_id} for segment, security_id in keys]
                            for start in range(0, len(entries), 100):
                                chunk = entries[start:start+100]
                                sock.send(json.dumps({"RequestCode": code, "InstrumentCount": len(chunk), "InstrumentList": chunk}))
                        active = wanted
                        refresh_at = now+timedelta(seconds=15)
                    try:
                        data = sock.recv()
                    except websocket.WebSocketTimeoutException:
                        data = None
                    if data == b"" or data == "":
                        raise ConnectionError("Feed connection closed.")
                    if data is not None and not isinstance(data, bytes):
                        raise ValueError("Unexpected text response from market feed; verify account data entitlement.")
                    ticks = decode_packets(data, timezone.now()) if isinstance(data, bytes) else []
                    if ticks:
                        last_packet_at = timezone.now()
                    for tick in ticks_waiting.ready(ticks, timezone.now()):
                        instrument = active.get((tick.segment, tick.security_id))
                        if instrument:
                            accepted_at = timezone.now()
                            collector.ingest(instrument, tick, accepted_at=accepted_at)
                            if all(0 <= (accepted_at-stamp).total_seconds() <= 15 for stamp in (tick.source_at, tick.received_at)):
                                last_fresh_at = max(last_fresh_at or tick.source_at, tick.source_at)
                    now = timezone.now()
                    collector.flush(now)
                    local = timezone.localtime(now)
                    if (local.weekday() < 5 and clock_time(9, 15) <= local.time() < clock_time(15, 30)
                            and now-(last_packet_at or connected_at) >= timedelta(seconds=30)):
                        raise ConnectionError("No market packets for 30 seconds; discarding stalled connection.")
                    if now >= scan_at:
                        result = {"mode": "COLLECT_ONLY"} if collect_only else scan(now)
                        finished_at = timezone.now()
                        state = "STREAMING" if last_fresh_at and finished_at-last_fresh_at <= timedelta(seconds=15) else "WAITING_FOR_DATA"
                        heartbeat(owner, state, json.dumps(result), finished_at, len(active))
                        scan_at = finished_at+timedelta(seconds=5)
                        delay = 2
            except (websocket.WebSocketException, OSError, ConnectionError, ValueError) as error:
                collector.disconnect()
                detail = str(error)
                for secret in (token, client_id):
                    if secret:
                        detail = detail.replace(secret, "[redacted]")
                mode = " Mode=COLLECT_ONLY." if collect_only else ""
                message = f"Feed interrupted ({type(error).__name__}): {detail[:500]}. Reconnecting; fresh quotes required.{mode}"
                LOGGER.warning(message)
                heartbeat(owner, "RECONNECTING", message, timezone.now())
                time.sleep(delay)
                delay = min(delay*2, 30)
            except KeyboardInterrupt:
                collector.disconnect()
                self.stdout.write("Paper radar stopped.")
                return
            finally:
                if sock is not None:
                    sock.close()
