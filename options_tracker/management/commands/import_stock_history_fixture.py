"""Import a validated, credential-free stock history fixture."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from options_tracker.history_fixture import (
    FixtureValidationError,
    import_fixture,
    load_fixture,
)


class Command(BaseCommand):
    help = "Validate and optionally import sanitized Dhan stock-history JSON."

    def add_arguments(self, parser):
        parser.add_argument("fixture", type=Path)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist validated rows. The default is a read-only dry run.",
        )

    def handle(self, *args, **options):
        try:
            fixture = load_fixture(options["fixture"])
            result = import_fixture(fixture, apply=options["apply"])
        except FixtureValidationError as error:
            raise CommandError(str(error)) from error

        mode = "APPLIED" if result.applied else "DRY RUN"
        self.stdout.write(f"{mode}: sha256:{fixture.source_hash}")
        self.stdout.write(
            f"equity rows: {result.equity_rows:,} "
            f"(inserted {result.inserted_equity_rows:,})"
        )
        self.stdout.write(
            f"option rows: {result.option_rows:,} "
            f"(inserted {result.inserted_option_rows:,})"
        )
        if not result.applied:
            self.stdout.write(self.style.WARNING("No database rows were changed."))
