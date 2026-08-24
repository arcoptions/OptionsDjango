"""Copy the tables /research/ renders out of research/ and into the app.

    python manage.py publish_research_tables --check
    python manage.py publish_research_tables

research/ does not travel to the deployed hosts -- it is two hundred scripts,
their working files and a 1.8 GB cache, and the site reads sixteen small CSVs
out of all that. Those sixteen live in `options_tracker/data/research/` and are
tracked with the app, so the deployed package carries the tables and nothing
else.

WHY THIS IS A COMMAND AND NOT A FALLBACK PATH. The obvious alternative is for
the view to read research/ when it is there and the published copy when it is
not. That reads well and hides the one thing worth seeing: the laptop would
render freshly-written numbers while production served whatever was last
committed, and the difference would only ever surface in production. Publishing
by hand costs one command and makes the page show the same bytes everywhere.

Run it after re-running a script that feeds the page, then commit the result.
"""
import filecmp
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from options_tracker.research_views import PAGE_TABLES, RESEARCH


class Command(BaseCommand):
    help = "Publish the CSVs the /research/ page renders from research/ into the app."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true",
                            help="Report what is stale or missing and copy nothing.")
        parser.add_argument("--source", default="",
                            help="Where the research scripts wrote (default: BASE_DIR/research).")

    def handle(self, *args, **options):
        check = options["check"]
        source = Path(options["source"].strip() or (settings.BASE_DIR / "research"))
        copied, stale, current, missing = [], [], [], []

        if not check:
            RESEARCH.mkdir(parents=True, exist_ok=True)

        for name in PAGE_TABLES:
            origin, target = source / name, RESEARCH / name
            if not origin.exists():
                missing.append(name)
                continue
            if target.exists() and filecmp.cmp(str(origin), str(target), shallow=False):
                current.append(name)
                continue
            if check:
                stale.append(name)
                continue
            # copy2 rather than copy, so the published file keeps the mtime of
            # the run that produced it -- that is the only date the page has.
            shutil.copy2(str(origin), str(target))
            copied.append(name)

        for name in copied:
            self.stdout.write(f"published {name}")
        for name in stale:
            self.stdout.write(self.style.WARNING(f"stale     {name}"))
        for name in missing:
            # Not an error: a table nobody has generated on this machine yet
            # renders as an empty section, which is what it should do.
            self.stderr.write(self.style.WARNING(
                f"missing   {name} -- not in {source}, the page will render it empty"))

        self.stdout.write(self.style.SUCCESS(
            "{} of {} tables published, {} already current, {} missing".format(
                len(copied) + len(stale) if check else len(copied),
                len(PAGE_TABLES), len(current), len(missing))))
