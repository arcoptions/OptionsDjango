import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker.ai_briefing import generate_briefing
from options_tracker.models import AIBriefingSnapshot


class Command(BaseCommand):
    help = "Preview or generate a traceable evidence-grounded ARC daily briefing."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--daemon", action="store_true")
        parser.add_argument("--hour", type=int, default=8)
        parser.add_argument("--minute", type=int, default=45)

    def _generate(self, apply):
        try:
            result = generate_briefing(apply=apply)
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        if apply:
            payload = {
                "mode": "apply",
                "briefing_id": result.pk,
                "provider": result.provider,
                "model": result.model,
                "prompt_hash": result.prompt_hash,
                "evidence_hash": result.evidence_hash,
            }
        else:
            payload = {
                "mode": "dry-run",
                "provider": result["provider"],
                "model": result["model"],
                "prompt_hash": result["prompt_hash"],
                "evidence_hash": result["evidence_hash"],
                "source_count": len(result["evidence"]["sources"]),
            }
        self.stdout.write(json.dumps(payload))

    def handle(self, *args, **options):
        if not 0 <= options["hour"] <= 23 or not 0 <= options["minute"] <= 59:
            raise CommandError("Briefing schedule must use a valid hour and minute.")
        if options["daemon"] and not options["apply"]:
            raise CommandError("Briefing daemon requires --apply.")
        if not options["daemon"]:
            self._generate(options["apply"])
            return
        while True:
            now = timezone.localtime()
            scheduled = now.replace(
                hour=options["hour"],
                minute=options["minute"],
                second=0,
                microsecond=0,
            )
            if (
                now >= scheduled
                and not AIBriefingSnapshot.objects.filter(
                    briefing_date=now.date(),
                ).exists()
            ):
                self._generate(True)
            time.sleep(60)
