"""Capture Dhan history to a normalized JSON file without database writes."""

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from options_tracker import stock_data
from options_tracker.models import TrackedStock


@dataclass(frozen=True)
class CaptureStock:
    symbol: str
    security_id: str


class Command(BaseCommand):
    help = "Capture credential-free Dhan history JSON without changing the database."

    def add_arguments(self, parser):
        parser.add_argument("output", type=Path)
        parser.add_argument("--days", type=int, default=10)
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--interval", type=int, default=15)
        parser.add_argument("--relative", default="ATM")
        parser.add_argument(
            "--stock",
            action="append",
            default=[],
            metavar="SYMBOL:SECURITY_ID",
            help=(
                "Capture an explicit stock without reading TrackedStock. Repeat for each "
                "stock; --limit applies only when this option is omitted."
            ),
        )
        parser.add_argument(
            "--equity-only",
            action="store_true",
            help=(
                "Capture equity bars without calling the rolling-option endpoint. "
                "Use only when the provider has explicitly rejected option history."
            ),
        )
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        output = options["output"]
        if output.exists() and not options["force"]:
            raise CommandError(f"Output already exists: {output}")
        if not 1 <= options["days"] <= stock_data.ROLLING_WINDOW_DAYS:
            raise CommandError(
                f"--days must be between 1 and {stock_data.ROLLING_WINDOW_DAYS}"
            )
        if not 1 <= options["limit"] <= 20:
            raise CommandError("--limit must be between 1 and 20")
        if options["interval"] not in {1, 5, 15, 25, 60}:
            raise CommandError("--interval must be one of 1, 5, 15, 25, or 60")
        relative = options["relative"].strip().upper()
        if not re.fullmatch(r"ATM(?:[+-][1-9]\d*)?", relative):
            raise CommandError("--relative must be ATM or an ATM offset such as ATM+1")

        stocks = self._capture_stocks(options["stock"], options["limit"])
        if not options["stock"] and len(stocks) != options["limit"]:
            raise CommandError(
                f"Requested {options['limit']} stocks but only {len(stocks)} active stocks "
                "have Dhan security IDs"
            )

        window_to = timezone.localdate()
        window_from = window_to - dt.timedelta(days=options["days"])
        equity_rows = []
        option_rows = []
        for stock in stocks:
            rows, error = stock_data.get_equity_bars(
                stock.symbol,
                stock.security_id,
                window_from,
                window_to,
                options["interval"],
            )
            if error or not rows:
                raise CommandError(
                    f"Equity capture failed for {stock.symbol}: {error or 'no rows returned'}"
                )
            equity_rows.extend(rows)

            if not options["equity_only"]:
                for option_type in ("CALL", "PUT"):
                    rows, error = stock_data.get_rolling_bars(
                        stock.symbol,
                        stock.security_id,
                        relative,
                        option_type,
                        window_from,
                        window_to,
                        options["interval"],
                    )
                    if error or not rows:
                        raise CommandError(
                            f"{option_type} capture failed for {stock.symbol}: "
                            f"{error or 'no rows returned'}"
                        )
                    option_rows.extend(rows)

        payload = {
            "schema_version": 1,
            "provider": "DHAN",
            "captured_at": timezone.now().isoformat(),
            "window": {
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
            },
            "equity": [self._serialize(row) for row in equity_rows],
            "options": [self._serialize(row) for row in option_rows],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(f"{output.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(output)
        self.stdout.write(
            self.style.SUCCESS(
                f"Captured {len(equity_rows):,} equity and {len(option_rows):,} option "
                f"rows for {len(stocks)} stocks to {output}"
            )
        )

    @staticmethod
    def _serialize(row):
        return {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in row.items()
        }

    @staticmethod
    def _capture_stocks(values, limit):
        if not values:
            return list(
                TrackedStock.objects.filter(is_active=True)
                .exclude(security_id="")
                .order_by("priority")[:limit]
            )
        if len(values) > 20:
            raise CommandError("--stock may be repeated at most 20 times")

        stocks = []
        seen = set()
        for value in values:
            parts = value.strip().split(":", maxsplit=1)
            if len(parts) != 2:
                raise CommandError(
                    f"Invalid --stock {value!r}; expected SYMBOL:SECURITY_ID"
                )
            symbol, security_id = parts[0].strip().upper(), parts[1].strip()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9&.-]{0,39}", symbol):
                raise CommandError(f"Invalid stock symbol in --stock {value!r}")
            if not re.fullmatch(r"\d{1,20}", security_id):
                raise CommandError(f"Invalid Dhan security ID in --stock {value!r}")
            if symbol in seen:
                raise CommandError(f"Duplicate stock symbol in --stock: {symbol}")
            seen.add(symbol)
            stocks.append(CaptureStock(symbol=symbol, security_id=security_id))
        return stocks
