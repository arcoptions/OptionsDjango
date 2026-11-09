"""Download stock and stock-option history from Dhan.

    python manage.py download_stock_data --feed equity
    python manage.py download_stock_data --feed rolling --months 18
    python manage.py download_stock_data --feed ladder

Safe to re-run: finished windows are skipped, so a killed job resumes.
"""
import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from options_tracker import stock_data
from options_tracker.models import StockEquityCandle, StockOptionCandle, TrackedStock


class Command(BaseCommand):
    help = "Pull equity bars, ATM-relative option bars and the live strike ladder."

    def add_arguments(self, parser):
        parser.add_argument("--feed", default="all",
                            choices=["all", "equity", "rolling", "ladder", "master"])
        parser.add_argument("--months", type=int, default=18)
        parser.add_argument("--days", type=int, default=0,
                            help="Use an exact bounded lookback instead of --months.")
        parser.add_argument("--limit", type=int, default=0, help="Only the top N stocks by turnover.")
        parser.add_argument("--interval", type=int, default=15)
        parser.add_argument("--relatives", default="", help="Comma list, e.g. ATM,ATM+1,ATM-1")
        parser.add_argument("--ladder-span", type=int, default=6)

    def handle(self, *args, **options):
        stocks = list(TrackedStock.objects.filter(is_active=True).order_by("priority"))
        if options["limit"]:
            stocks = stocks[: options["limit"]]

        if options["days"] < 0:
            raise CommandError("--days cannot be negative")
        if not options["days"] and options["months"] < 1:
            raise CommandError("--months must be at least 1")
        end = dt.date.today()
        lookback_days = options["days"] or int(options["months"] * 30.5)
        start = end - dt.timedelta(days=lookback_days)
        feed = options["feed"]

        if feed in ("all", "master"):
            updated, unmatched = stock_data.sync_master_metadata()
            self.stdout.write(f"master: {updated} stocks stamped with security id / lot / step")
            if unmatched:
                self.stdout.write(self.style.WARNING(
                    f"  no Dhan match for {len(unmatched)}: {', '.join(unmatched[:15])}"
                ))
            stocks = list(TrackedStock.objects.filter(is_active=True).order_by("priority"))
            if options["limit"]:
                stocks = stocks[: options["limit"]]

        if feed in ("all", "equity"):
            rows, failed = stock_data.download_equity(stocks, start, end, options["interval"])
            self.stdout.write(self.style.SUCCESS(f"equity: {rows:,} bars ({failed} windows failed)"))

        if feed in ("all", "rolling"):
            relatives = [r.strip() for r in options["relatives"].split(",") if r.strip()] or None
            rows, failed = stock_data.download_rolling(
                stocks, start, end, relatives, options["interval"]
            )
            self.stdout.write(self.style.SUCCESS(f"rolling: {rows:,} bars ({failed} windows failed)"))

        if feed in ("all", "ladder"):
            rows, failed = stock_data.download_ladder(
                stocks, end - dt.timedelta(days=120), end,
                span=options["ladder_span"], interval=options["interval"],
            )
            self.stdout.write(self.style.SUCCESS(f"ladder: {rows:,} bars ({failed} windows failed)"))

        self.stdout.write("")
        self.stdout.write(f"equity bars in db : {StockEquityCandle.objects.count():,}")
        self.stdout.write(f"option bars in db : {StockOptionCandle.objects.count():,}")
