import json
import time

from django.core.management.base import BaseCommand, CommandError

from options_tracker.dhan_token_reminder import reminder_state, send_reminder


class Command(BaseCommand):
    help = "Preview or send the production-only daily Dhan token reminder."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Send the reminder when it is due. Preview is the default.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Check every five minutes. Requires --apply to send.",
        )

    def handle(self, *args, **options):
        while True:
            result = send_reminder() if options["apply"] else reminder_state()
            self.stdout.write(json.dumps(result, sort_keys=True))
            if result["state"] == "DELIVERY_FAILED" and not options["loop"]:
                raise CommandError(
                    f"Dhan token reminder delivery failed: {result['error']}"
                )
            if not options["loop"]:
                return
            time.sleep(300)
