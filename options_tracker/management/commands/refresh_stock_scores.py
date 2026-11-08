import json

from django.core.management.base import BaseCommand

from options_tracker.models import TrackedStock
from options_tracker.stock_intelligence import persist_stock_scores, stock_intelligence_context


class Command(BaseCommand):
    help = "Preview or persist versioned investment, swing, and F&O scores."

    def add_arguments(self, parser):
        parser.add_argument("--symbols", nargs="+")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        symbols = options["symbols"] or list(
            TrackedStock.objects.filter(is_active=True)
            .order_by("priority")
            .values_list("symbol", flat=True)
        )
        result = []
        for symbol in symbols:
            context = stock_intelligence_context(symbol)
            if options["apply"]:
                persist_stock_scores(symbol, context["generated_at"])
            result.append({
                "symbol": symbol,
                "scores": {
                    row["type"]: {
                        "score": row["preview"]["score"],
                        "confidence": row["preview"]["confidence"],
                        "missing_data": row["preview"]["missing_data"],
                    }
                    for row in context["scores"]
                },
            })
        self.stdout.write(json.dumps({
            "mode": "apply" if options["apply"] else "dry-run",
            "symbols": len(symbols),
            "results": result,
        }))
