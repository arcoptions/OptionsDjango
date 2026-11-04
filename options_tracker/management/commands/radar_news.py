import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker.models import RadarWorker
from options_tracker.radar_feed import acquire_worker, heartbeat
from options_tracker.radar_news import collect_public_news


class Command(BaseCommand):
    help = "Collect public BeatTheStreet text/captions once per minute; no Telegram user credentials needed."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        owner = None
        try:
            owner = acquire_worker(timezone.now(), name="radar-news")
            while True:
                heartbeat(owner, "FETCHING", "Public text/captions only.", timezone.now(), name="radar-news")
                result = collect_public_news(timezone.now())
                heartbeat(owner, "CURRENT", json.dumps(result), timezone.now(), name="radar-news")
                if options["once"]:
                    self.stdout.write(json.dumps(result))
                    return
                for _ in range(3):
                    time.sleep(20)
                    heartbeat(owner, "CURRENT", json.dumps(result), timezone.now(), name="radar-news")
        except KeyboardInterrupt:
            self.stdout.write("Public news collection stopped.")
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        finally:
            if owner:
                RadarWorker.objects.filter(name="radar-news", owner=owner).update(state="STOPPED", lease_until=timezone.now())
