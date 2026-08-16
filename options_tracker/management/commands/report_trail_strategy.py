import json

from django.core.management.base import BaseCommand

from options_tracker.nifty_trail_strategy import (
    MAX_CASH_FRACTION,
    RISK_PER_TRADE,
    STARTING_CAPITAL,
    strategy_report,
)


class Command(BaseCommand):
    help = "Report the NIFTY option-buying trailing-exit strategy on a cash account."

    def add_arguments(self, parser):
        parser.add_argument("--capital", type=float, default=STARTING_CAPITAL)
        parser.add_argument("--risk", type=float, default=RISK_PER_TRADE)
        parser.add_argument("--cash-fraction", type=float, default=MAX_CASH_FRACTION)

    def handle(self, *args, **options):
        report = strategy_report(
            starting_capital=options["capital"],
            risk_per_trade=options["risk"],
            max_cash_fraction=options["cash_fraction"],
        )
        self.stdout.write(json.dumps(report, indent=2))
