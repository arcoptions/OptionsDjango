"""Move this host onto the commit App Service is serving, and restart the engine.

    python manage.py sync_to_deployed_commit --dry-run
    python manage.py sync_to_deployed_commit

Runs on the trading VM, on a five-minute timer. It follows
`AppSetting['deployed_commit']` rather than origin/master, because App Service is
what applies the migrations: a commit is only safe to run once the schema behind
it is in place. See `options_tracker/deployment.py`.

WHY IT DECIDES BEFORE IT TOUCHES ANYTHING. The check-out and the restart are one
action, not two. A tree updated at 11:00 and restarted at 15:45 spends the
session with new code on disk and an old process on top of it -- and any module
imported lazily inside a function would come off the new tree mid-trade. So the
safety question is asked first, and a run that cannot restart changes nothing at
all. It simply comes back in five minutes.

SAFE MEANS THE MARKET IS SHUT OR THE BOOK IS FLAT. A restart costs about
thirteen seconds, measured: on 19 August 2026 the engine evaluated the 14:00 bar
at 14:01:51 and the 14:01 bar at 14:02:04, missing nothing. Thirteen seconds is
cheap with nothing open and unacceptable with money on the table, because the
trail is what the whole strategy earns and it is only enforced tick by tick.
"""
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from options_tracker.deployment import (
    VM_PENDING_KEY,
    deployed_commit,
    git_head,
    record_vm_commit,
    short,
)
from options_tracker.live_engine import load_state
from options_tracker.models import AppSetting
from options_tracker.services import is_dhan_market_open

# What the dashboard reads to say an update is waiting on the close.
PENDING_KEY = VM_PENDING_KEY

DEFAULT_RESTART = "sudo -n systemctl restart nifty-live.service"


class Command(BaseCommand):
    help = "Check out the commit App Service deployed and restart the live engine."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would happen and change nothing.")
        parser.add_argument("--restart-command", default=DEFAULT_RESTART,
                            help="How to restart the engine once the tree has moved.")
        parser.add_argument("--force", action="store_true",
                            help="Update mid-session even with a position open. Rarely right.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        target = deployed_commit()
        here = git_head()
        if not here:
            raise CommandError(
                "{} is not a git checkout; this command belongs on the VM.".format(settings.BASE_DIR))
        # Report in before deciding anything. Every run writes the row, so its
        # timestamp is the dashboard's proof the timer is still alive -- and a
        # run that then refuses to move has still been seen.
        if not dry_run:
            record_vm_commit(here)
        if not target:
            self.stdout.write("no deployed commit recorded yet -- staying on {}".format(short(here)))
            return
        if target == here:
            self._clear_pending(dry_run)
            self.stdout.write("already on the deployed commit {}".format(short(here)))
            return

        safe, reason = self._restart_is_safe(options["force"])
        self.stdout.write("deployed {} != running {} ({})".format(
            short(target), short(here), reason))
        if not safe:
            self._record_pending(target, dry_run)
            return
        if dry_run:
            self.stdout.write("would check out {} and restart the engine".format(short(target)))
            return

        self._checkout(target, here)
        self._restart(options["restart_command"])
        self._clear_pending(dry_run)
        # The tree moved under us; the row written at the top of this run named
        # the commit we were leaving.
        record_vm_commit(target)

    # -- decisions ----------------------------------------------------------

    def _restart_is_safe(self, force):
        if not is_dhan_market_open():
            return True, "market closed"
        if not load_state().get("position"):
            return True, "market open, no position"
        if force:
            return True, "position open, forced"
        return False, "a position is open"

    def _record_pending(self, target, dry_run):
        self.stdout.write(self.style.WARNING(
            "deferring: {} will be applied once the book is flat".format(short(target))))
        if not dry_run:
            AppSetting.objects.update_or_create(
                key=PENDING_KEY, defaults={"value": target})

    def _clear_pending(self, dry_run=False):
        if not dry_run:
            AppSetting.objects.filter(key=PENDING_KEY).delete()

    # -- the move -----------------------------------------------------------

    def _checkout(self, target, here):
        if self._git("status", "--porcelain").strip():
            # Someone is mid-something on the box. Moving the tree under them
            # would destroy it, and an engine one commit behind is survivable.
            raise CommandError("the working tree has uncommitted changes; refusing to move it")

        self._git("fetch", "origin", "--prune")
        if self._git("cat-file", "-t", target + "^{commit}", check=False).strip() != "commit":
            raise CommandError(
                "{} is not in this repository even after fetching -- is the deploy "
                "marker ahead of what was pushed?".format(short(target)))

        needs_install = self._requirements_changed(here, target)
        # -B rather than a detached checkout, so the box stays on master and a
        # plain `git pull` by hand still does the obvious thing afterwards.
        self._git("checkout", "-B", "master", target)
        self.stdout.write(self.style.SUCCESS("checked out {}".format(short(target))))
        if needs_install:
            self.stdout.write("requirements.txt changed -- installing")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                           cwd=str(settings.BASE_DIR), check=True)

    def _requirements_changed(self, here, target):
        """Only reinstall when the file actually moved; pip is slow and noisy."""
        before = self._git("rev-parse", f"{here}:requirements.txt", check=False).strip()
        after = self._git("rev-parse", f"{target}:requirements.txt", check=False).strip()
        return bool(after) and before != after

    def _restart(self, command):
        # Deliberately not `migrate`: App Service owns the schema, and two hosts
        # migrating the same database is how you get a half-applied one.
        self.stdout.write("restarting the engine: {}".format(command))
        subprocess.run(command.split(), check=True, timeout=120)
        self.stdout.write(self.style.SUCCESS("engine restarted"))

    def _git(self, *args, check=True):
        finished = subprocess.run(
            ["git", *args], cwd=str(settings.BASE_DIR),
            capture_output=True, text=True, timeout=120,
        )
        if check and finished.returncode != 0:
            raise CommandError("git {} failed: {}".format(
                " ".join(args), finished.stderr.strip() or finished.stdout.strip()))
        return finished.stdout
