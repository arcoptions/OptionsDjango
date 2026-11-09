"""Independent evaluation owns paper decisions, ranking and research, never collection."""

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from options_tracker.models import RadarWorker
from options_tracker.radar import scan
from options_tracker.radar_assistant import persist_shortlist
from options_tracker.radar_feed import acquire_worker, heartbeat
from options_tracker.radar_research import capture_observations, register_protocol, update_observations


class Command(BaseCommand):
    help = "Separate paper evaluator: --once or --loop. No broker connection, delivery or orders."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true")
        mode.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        owner = None
        try:
            collector = RadarWorker.objects.filter(name="paper-radar").first()
            if collector and collector.lease_until > timezone.now() and "COLLECT_ONLY" not in collector.detail:
                raise CommandError("Active combined radar worker found. Restart it with --collect-only before running a separate evaluator.")
            owner = acquire_worker(timezone.now(), name="radar-evaluator")
            protocol = register_protocol(timezone.now())
            research_at = None
            while True:
                now = timezone.now()
                heartbeat(owner, "EVALUATING", "Evaluating paper opportunities.", now, name="radar-evaluator")

                def report_progress():
                    heartbeat(
                        owner,
                        "EVALUATING",
                        "Evaluating paper opportunities.",
                        timezone.now(),
                        name="radar-evaluator",
                    )

                result = scan(now, assistant=True, progress=report_progress)
                report_progress()
                captured_at = timezone.now()
                persist_shortlist(captured_at)
                report_progress()
                capture_observations(protocol, captured_at)
                report_progress()
                if research_at is None or captured_at >= research_at:
                    update_observations(protocol, captured_at)
                    research_at = timezone.now()+timezone.timedelta(minutes=1)
                    report_progress()
                heartbeat(owner, "IDLE", json.dumps(result), timezone.now(), name="radar-evaluator")
                if options["once"]:
                    self.stdout.write(json.dumps(result))
                    return
                close_old_connections()
                time.sleep(5)
        except KeyboardInterrupt:
            self.stdout.write("Paper evaluator stopped.")
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        finally:
            if owner:
                RadarWorker.objects.filter(name="radar-evaluator", owner=owner).update(
                    state="STOPPED", lease_until=timezone.now(),
                )
