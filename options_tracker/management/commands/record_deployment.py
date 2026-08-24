"""Publish the commit this host is serving, so the VM knows what to follow.

    python manage.py record_deployment
    python manage.py record_deployment --commit 88cb1c0...

Called from `startup.sh` immediately after `migrate` succeeds. The ordering is
the whole point: the marker means "the schema is ready for this commit", not
merely "this commit exists". The VM checks out whatever this row says, so
writing it before the migration would invite the engine to run against a
database that has not caught up yet.

Failure here is not fatal. A deploy that cannot write the marker still serves
the site; the VM simply stays where it is, which is the safe direction to fail.
"""
from django.core.management.base import BaseCommand

from options_tracker.deployment import (
    DEPLOYED_COMMIT_KEY,
    deployed_commit,
    record_deployed_commit,
    running_commit,
    short,
)


class Command(BaseCommand):
    help = "Record this host's commit as the deployed one for the VM to follow."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", default="",
            help="Record this SHA instead of the one stamped into the package.",
        )

    def handle(self, *args, **options):
        commit = options["commit"].strip() or running_commit()
        if not commit:
            # No DEPLOYED_SHA in the package and no .git to ask. Leaving the old
            # marker alone keeps the VM on a commit that at least once worked.
            self.stderr.write(self.style.WARNING(
                f"nothing to record: no {DEPLOYED_COMMIT_KEY} could be determined "
                "(the package carries no DEPLOYED_SHA and this is not a checkout)"))
            return

        previous = deployed_commit()
        record_deployed_commit(commit)
        if previous == commit:
            self.stdout.write(f"deployed commit unchanged at {short(commit)}")
        else:
            self.stdout.write(self.style.SUCCESS(
                "deployed commit {} -> {}".format(short(previous) or "(none)", short(commit))))
