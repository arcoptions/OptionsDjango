"""Build an evidence-backed historical market-breadth snapshot."""

import json

from django.core.management.base import BaseCommand

from options_tracker.market_breadth import build_market_breadth_snapshot


class Command(BaseCommand):
    help = "Preview or persist F&O-universe breadth from complete one-minute sessions."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        summary, snapshot, created = build_market_breadth_snapshot(
            apply=options["apply"],
        )
        self.stdout.write(
            json.dumps(
                {
                    "mode": "apply" if options["apply"] else "dry-run",
                    "snapshot_id": snapshot.pk if snapshot else None,
                    "created": created,
                    **summary,
                    "source_at": (
                        summary["source_at"].isoformat()
                        if summary["source_at"]
                        else None
                    ),
                },
                sort_keys=True,
            )
        )
