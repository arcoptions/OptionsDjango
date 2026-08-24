"""The traded universe, carried with the app instead of living on one laptop.

    python manage.py seed_reference_data --dump      # write the fixture from this database
    python manage.py seed_reference_data             # load the fixture into this database

WHY THIS EXISTS. `TrackedStock` is harvested from broker P&L files
(`ingest_broker_pnl`), and those files are on the laptop. Every other host --
the App Service serving /shortlist/, the VM placing the orders -- had no way to
obtain the universe at all, so production sat at zero rows and the page 404'd
on an empty table. The other two tables /shortlist/ needs are recomputable and
are NOT shipped:

    manage.py download_stock_data --feed master     # stamp security ids and lots
    manage.py download_stock_data --feed equity --months 10
    manage.py build_daily_shortlist --rebuild-history 400

That is deliberate. 1.8 million candles are a download, not a fixture, and
backfilled picks are recomputed from them by a model that may change. Only the
list of names -- which nothing outside a broker statement can reconstruct --
travels in the repository.

MATCHED ON SYMBOL, NOT PRIMARY KEY. Two databases assign ids independently, so
loading by id either collides or silently renumbers. `symbol` is the unique
natural key and is what the rest of the app joins on.
"""
import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from options_tracker.models import TrackedStock

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "tracked_stocks.json"

# Everything except the id and the auto_now stamp. Decimals are written as
# strings: json floats would quietly re-round turnover and realised P&L.
FIELDS = (
    "symbol", "security_id", "lot_size", "strike_step", "sector",
    "market_cap_band", "contracts_traded", "realised_pnl", "turnover",
    "brokers", "is_active", "priority",
)


class Command(BaseCommand):
    help = "Load (or dump) the TrackedStock universe the shortlist is built from."

    def add_arguments(self, parser):
        parser.add_argument("--dump", action="store_true",
                            help="Write the fixture from this database instead of reading it.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change and write nothing.")
        parser.add_argument("--fixture", default="",
                            help="Read or write somewhere other than the tracked copy.")

    def handle(self, *args, **options):
        path = Path(options["fixture"]) if options["fixture"] else FIXTURE
        if options["dump"]:
            self._dump(path, options["dry_run"])
        else:
            self._load(path, options["dry_run"])

    # -- out ----------------------------------------------------------------

    def _dump(self, path, dry_run):
        rows = [self._as_json(stock)
                for stock in TrackedStock.objects.order_by("symbol")]
        if not rows:
            raise CommandError("this database has no TrackedStock rows to dump")
        if dry_run:
            self.stdout.write("would write {} stock(s) to {}".format(len(rows), path))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            "wrote {} stock(s) to {} -- commit it".format(len(rows), path)))

    def _as_json(self, stock):
        out = {}
        for field in FIELDS:
            value = getattr(stock, field)
            out[field] = str(value) if isinstance(value, Decimal) else value
        return out

    # -- in -----------------------------------------------------------------

    def _load(self, path, dry_run):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CommandError(
                "no fixture at {} -- run --dump on the machine that has the "
                "broker P&L loaded ({})".format(path, exc))

        held = {stock.symbol: stock for stock in TrackedStock.objects.all()}
        created = [row for row in rows if row["symbol"] not in held]
        changed = [row for row in rows
                   if row["symbol"] in held and self._differs(row, held[row["symbol"]])]

        if dry_run:
            self.stdout.write("{} to create, {} to update, {} already current".format(
                len(created), len(changed), len(rows) - len(created) - len(changed)))
            return

        with transaction.atomic():
            for row in rows:
                symbol = row["symbol"]
                TrackedStock.objects.update_or_create(
                    symbol=symbol,
                    defaults={key: self._as_field(key, value)
                              for key, value in row.items() if key != "symbol"},
                )
        self.stdout.write(self.style.SUCCESS(
            "{} stock(s) seeded ({} new, {} updated)".format(
                len(rows), len(created), len(changed))))
        # Naming the next steps here because this command is the first thing
        # run on a fresh host, and on its own it leaves /shortlist/ still empty.
        self.stdout.write("")
        self.stdout.write("next, on a host with a Dhan token:")
        self.stdout.write("  manage.py download_stock_data --feed master")
        self.stdout.write("  manage.py download_stock_data --feed equity --months 10")
        self.stdout.write("  manage.py build_daily_shortlist --rebuild-history 400")

    def _as_field(self, key, value):
        if key in ("strike_step", "realised_pnl", "turnover") and value is not None:
            return Decimal(str(value))
        return value

    def _differs(self, row, stock):
        return any(str(getattr(stock, key)) != str(value)
                   for key, value in row.items() if key != "symbol")
