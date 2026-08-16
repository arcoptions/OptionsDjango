import json
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from options_tracker.live_engine import tick


class Command(BaseCommand):
    help = (
        "Run the finalised NIFTY trail strategy against the live Dhan feed: "
        "detect the signal, place the order, trail the stop, square off at 15:20."
    )

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one tick and exit.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Evaluate and log everything, but send no orders to Dhan.",
        )
        parser.add_argument(
            "--interval", type=int, default=15, help="Seconds between ticks while the market is open.",
        )

    def handle(self, *args, **options):
        while True:
            try:
                status = tick(dry_run=options["dry_run"])
            except Exception as error:  # a bad tick must never kill the loop
                self.stderr.write(f"tick failed: {error}")
                status = {"state": "ERROR"}

            state = status.get("state", "?")
            if options["once"]:
                self.stdout.write(json.dumps(status, indent=2, default=str))
            else:
                notes = "; ".join(status.get("notes") or []) or "-"
                self.stdout.write(f"[{status.get('at', '')}] {state}: {notes}")
                for reason in status.get("rejections") or []:
                    self.stdout.write(f"    rejected: {reason}")

            close_old_connections()
            if options["once"]:
                break
            time.sleep(options["interval"] if state == "RUNNING" else 60)
