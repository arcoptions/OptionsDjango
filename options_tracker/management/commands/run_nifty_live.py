import json
import os
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
        # The daemon is started by startup.sh with no arguments, so observe-only
        # has to be reachable from the environment too. Defaulting the *flag* to
        # off and the env var to on would be the wrong way round: an unset
        # variable must never be the thing that arms real money.
        dry_run = options["dry_run"] or os.getenv("NIFTY_LIVE_DRY_RUN", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if dry_run:
            self.stdout.write("observe-only: signals and quotes are logged, no order is sent")

        while True:
            try:
                status = tick(dry_run=dry_run)
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
