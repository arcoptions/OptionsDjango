"""Load the broker P&L statements and rebuild the tracked-stock universe."""
from django.core.management.base import BaseCommand

from options_tracker.models import BrokerPeriodSummary, TrackedStock
from options_tracker.pnl_ingest import ingest, rebuild_universe


class Command(BaseCommand):
    help = "Parse the Zerodha/Sahi/Dhan P&L files and derive the unique stock list."

    def add_arguments(self, parser):
        parser.add_argument("--downloads", default=None, help="Folder holding the statements.")

    def handle(self, *args, **options):
        from options_tracker.pnl_ingest import DOWNLOADS

        folder = options["downloads"] or DOWNLOADS
        self.stdout.write(f"Reading statements from {folder}")
        rows, periods, missing = ingest(downloads=folder)
        for name in missing:
            self.stdout.write(self.style.WARNING(f"  missing: {name}"))

        count = rebuild_universe()
        self.stdout.write("")
        self.stdout.write(f"{rows} contract rows across {periods} statement periods")
        self.stdout.write(self.style.SUCCESS(f"{count} unique stocks tracked"))

        gross = charges = unrealised = 0
        self.stdout.write("")
        self.stdout.write(f"{'BROKER':10s} {'PERIOD':22s} {'GROSS':>14s} {'CHARGES':>11s} {'NET':>14s}")
        for period in BrokerPeriodSummary.objects.all():
            gross += period.gross_realised
            charges += period.charges
            unrealised += period.unrealised
            self.stdout.write(
                f"{period.broker:10s} {period.period_label:22s} "
                f"{period.gross_realised:14,.0f} {period.charges:11,.0f} {period.net_realised:14,.0f}"
            )
        self.stdout.write("-" * 76)
        self.stdout.write(
            f"{'TOTAL':10s} {'':22s} {gross:14,.0f} {charges:11,.0f} {gross - charges:14,.0f}"
        )
        self.stdout.write(f"open positions: {unrealised:,.0f}")
        self.stdout.write(
            self.style.ERROR(f"hole to fill: {gross - charges + unrealised:,.0f}")
        )

        self.stdout.write("")
        self.stdout.write("Top 10 by turnover:")
        for stock in TrackedStock.objects.filter(is_active=True).order_by("priority")[:10]:
            self.stdout.write(
                f"  {stock.priority:3d}. {stock.symbol:14s} turnover {stock.turnover:12,.0f} "
                f"pnl {stock.realised_pnl:11,.0f}  [{stock.brokers}]"
            )
