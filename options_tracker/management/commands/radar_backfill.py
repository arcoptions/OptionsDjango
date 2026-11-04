"""Explicit cache reuse and bounded Dhan minute downloads; no scanning or orders."""

from datetime import timedelta
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker.models import RadarInstrument, RadarWorker
from options_tracker.radar_feed import acquire_worker, heartbeat, subscriptions
from options_tracker.radar_history import HistoryClient, credentials, download_instrument


class Command(BaseCommand):
    help = "Reuse stored 1-minute equity bars and backfill all F&O underlyings; optionally watched live options. No quotes/orders/alerts."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--symbols", nargs="+")
        parser.add_argument("--include-watched-options", action="store_true")
        parser.add_argument("--cache-only", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["days"] <= 40:
            raise CommandError("--days must be between 1 and 40.")
        now = timezone.now()
        symbols = {symbol.upper() for symbol in options["symbols"]} if options["symbols"] else None
        equities = list(RadarInstrument.objects.filter(kind="EQUITY", active=True).order_by("symbol"))
        if symbols:
            unknown = symbols-{i.symbol for i in equities}
            if unknown:
                raise CommandError(f"Unknown active F&O symbols: {', '.join(sorted(unknown))}")
            equities = [i for i in equities if i.symbol in symbols]
        if not equities:
            raise CommandError("No F&O equities registered. Run radar_sync --refresh first.")
        instruments = list(equities)
        if options["include_watched_options"]:
            instruments += [i for i in subscriptions(now) if i.kind == "OPTION" and (symbols is None or i.symbol in symbols)]
        owner, client = None, HistoryClient()
        totals = dict(inserted=0, retained=0, conflicts=0, requests=0, checkpoints=0, returned=0)
        try:
            owner = acquire_worker(now, name="radar-history")
            if not options["cache_only"]:
                credentials()
            for index, instrument in enumerate(instruments, 1):
                detail = f"{index}/{len(instruments)} {instrument.symbol} {instrument.segment}:{instrument.security_id}"
                heartbeat(owner, "DOWNLOADING", detail, timezone.now(), len(instruments), name="radar-history")
                # Retries can span more than one normal feed lease.
                RadarWorker.objects.filter(name="radar-history", owner=owner).update(lease_until=timezone.now()+timedelta(minutes=5))
                result = download_instrument(
                    instrument, timezone.localdate(now)-timedelta(days=options["days"]), timezone.localdate(now),
                    now, client, options["cache_only"],
                )
                for key in totals:
                    totals[key] += result[key]
                self.stdout.write(f"{detail}: {json.dumps(result)}")
            heartbeat(owner, "COMPLETE", json.dumps(totals), timezone.now(), len(instruments), name="radar-history")
            self.stdout.write(f"PAPER DATA ONLY: {json.dumps(totals)}")
        except (ValueError, OSError) as error:
            if owner:
                RadarWorker.objects.filter(name="radar-history", owner=owner).update(state="FAILED", detail=str(error), heartbeat_at=timezone.now())
            raise CommandError(str(error)) from error
        finally:
            client.close()
            if owner:
                RadarWorker.objects.filter(name="radar-history", owner=owner).update(lease_until=timezone.now())
