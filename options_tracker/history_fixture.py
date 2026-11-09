"""Validate and import normalized, credential-free stock history fixtures."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime

from .models import DownloadJob, StockEquityCandle, StockOptionCandle, TrackedStock

MAX_FIXTURE_BYTES = 50 * 1024 * 1024
MAX_FIXTURE_ROWS = 1_000_000
ALLOWED_INTERVALS = {1, 5, 15, 25, 60, 1440}
SENSITIVE_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "clientid",
    "headers",
    "password",
    "token",
}
TOP_LEVEL_KEYS = {
    "schema_version",
    "provider",
    "captured_at",
    "window",
    "equity",
    "options",
}
EQUITY_KEYS = {
    "symbol",
    "interval_minutes",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
}
OPTION_KEYS = {
    "symbol",
    "expiry_code",
    "relative_strike",
    "option_type",
    "interval_minutes",
    "timestamp",
    "strike",
    "spot",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
    "implied_volatility",
}
RELATIVE_STRIKE_RE = re.compile(r"^(ATM(?:[+-][1-9]\d*)?|K\d+(?:\.\d+)?)$")


class FixtureValidationError(ValueError):
    """The sanitized history fixture is invalid or unsafe."""


@dataclass(frozen=True)
class ValidatedFixture:
    """Validated fixture rows and immutable import evidence."""

    source_hash: str
    captured_at: dt.datetime
    window_from: dt.date
    window_to: dt.date
    equity: tuple[dict[str, Any], ...]
    options: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ImportResult:
    """Counts produced by a dry run or applied fixture import."""

    equity_rows: int
    option_rows: int
    inserted_equity_rows: int
    inserted_option_rows: int
    applied: bool


def _reject_sensitive_keys(value: Any, path: str = "fixture") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in SENSITIVE_KEYS:
                raise FixtureValidationError(f"{path} contains forbidden credential field {key!r}")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def _require_exact_keys(row: dict[str, Any], allowed: set[str], path: str) -> None:
    unexpected = set(row) - allowed
    missing = allowed - set(row)
    if unexpected:
        raise FixtureValidationError(f"{path} contains unexpected fields: {sorted(unexpected)}")
    if missing:
        raise FixtureValidationError(f"{path} is missing fields: {sorted(missing)}")


def _aware_datetime(value: Any, path: str) -> dt.datetime:
    parsed = parse_datetime(str(value))
    if parsed is None or parsed.tzinfo is None:
        raise FixtureValidationError(f"{path} must be an ISO 8601 timestamp with a timezone")
    return parsed


def _date(value: Any, path: str) -> dt.date:
    parsed = parse_date(str(value))
    if parsed is None:
        raise FixtureValidationError(f"{path} must be an ISO 8601 date")
    return parsed


def _decimal(value: Any, path: str, *, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FixtureValidationError(f"{path} must be numeric") from None
    if not parsed.is_finite() or parsed < 0:
        raise FixtureValidationError(f"{path} must be a finite non-negative number")
    return parsed


def _integer(value: Any, path: str, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        raise FixtureValidationError(f"{path} must be an integer")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FixtureValidationError(f"{path} must be an integer") from None
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise FixtureValidationError(f"{path} must be an integer")
    parsed = int(numeric)
    if parsed < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise FixtureValidationError(f"{path} must be a {qualifier} integer")
    return parsed


def _validate_price_bar(row: dict[str, Any], path: str) -> dict[str, Decimal | None]:
    prices = {
        key: _decimal(row[key], f"{path}.{key}", nullable=True)
        for key in ("open", "high", "low", "close")
    }
    populated = [value for value in prices.values() if value is not None]
    if populated and len(populated) != 4:
        raise FixtureValidationError(f"{path} must provide all OHLC values or none")
    if populated:
        low = prices["low"]
        high = prices["high"]
        assert low is not None and high is not None
        if low > min(populated) or high < max(populated):
            raise FixtureValidationError(f"{path} has inconsistent OHLC bounds")
    return prices


def _validate_common(
    row: dict[str, Any],
    path: str,
    active_symbols: set[str],
    window_from: dt.date,
    window_to: dt.date,
    captured_at: dt.datetime,
) -> tuple[str, int, dt.datetime, dict[str, Decimal | None]]:
    symbol = str(row["symbol"]).strip().upper()
    if symbol not in active_symbols:
        raise FixtureValidationError(f"{path}.symbol is not an active tracked stock: {symbol!r}")
    interval = _integer(row["interval_minutes"], f"{path}.interval_minutes", positive=True)
    if interval not in ALLOWED_INTERVALS:
        raise FixtureValidationError(
            f"{path}.interval_minutes must be one of {sorted(ALLOWED_INTERVALS)}"
        )
    timestamp = _aware_datetime(row["timestamp"], f"{path}.timestamp")
    if not window_from <= timestamp.date() <= window_to:
        raise FixtureValidationError(f"{path}.timestamp falls outside the declared window")
    if timestamp > captured_at:
        raise FixtureValidationError(f"{path}.timestamp is later than captured_at")
    return symbol, interval, timestamp, _validate_price_bar(row, path)


def load_fixture(path: Path) -> ValidatedFixture:
    """Load and validate a normalized history fixture without writing data."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise FixtureValidationError(f"Cannot read fixture: {error}") from error
    if size > MAX_FIXTURE_BYTES:
        raise FixtureValidationError(f"Fixture exceeds {MAX_FIXTURE_BYTES} bytes")

    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FixtureValidationError(f"Fixture is not valid UTF-8 JSON: {error}") from error
    if not isinstance(payload, dict):
        raise FixtureValidationError("Fixture root must be an object")
    _reject_sensitive_keys(payload)
    _require_exact_keys(payload, TOP_LEVEL_KEYS, "fixture")
    if payload["schema_version"] != 1:
        raise FixtureValidationError("schema_version must be 1")
    if payload["provider"] != "DHAN":
        raise FixtureValidationError("provider must be DHAN")

    captured_at = _aware_datetime(payload["captured_at"], "fixture.captured_at")
    window = payload["window"]
    if not isinstance(window, dict) or set(window) != {"from", "to"}:
        raise FixtureValidationError("fixture.window must contain only 'from' and 'to'")
    window_from = _date(window["from"], "fixture.window.from")
    window_to = _date(window["to"], "fixture.window.to")
    if window_from > window_to:
        raise FixtureValidationError("fixture.window.from cannot be later than fixture.window.to")

    equity_payload = payload["equity"]
    option_payload = payload["options"]
    if not isinstance(equity_payload, list) or not isinstance(option_payload, list):
        raise FixtureValidationError("fixture.equity and fixture.options must be arrays")
    if len(equity_payload) + len(option_payload) > MAX_FIXTURE_ROWS:
        raise FixtureValidationError(f"Fixture exceeds {MAX_FIXTURE_ROWS} rows")
    if not equity_payload and not option_payload:
        raise FixtureValidationError("Fixture contains no candle rows")

    symbols = {
        str(row.get("symbol", "")).strip().upper()
        for row in equity_payload + option_payload
        if isinstance(row, dict)
    }
    active_symbols = set(
        TrackedStock.objects.filter(symbol__in=symbols, is_active=True).values_list(
            "symbol", flat=True
        )
    )

    equity_rows: list[dict[str, Any]] = []
    equity_keys: set[tuple[str, int, dt.datetime]] = set()
    for index, row in enumerate(equity_payload):
        path_name = f"fixture.equity[{index}]"
        if not isinstance(row, dict):
            raise FixtureValidationError(f"{path_name} must be an object")
        _require_exact_keys(row, EQUITY_KEYS, path_name)
        symbol, interval, timestamp, prices = _validate_common(
            row, path_name, active_symbols, window_from, window_to, captured_at
        )
        identity = (symbol, interval, timestamp)
        if identity in equity_keys:
            raise FixtureValidationError(f"{path_name} duplicates an equity candle identity")
        equity_keys.add(identity)
        equity_rows.append({
            "symbol": symbol,
            "interval_minutes": interval,
            "timestamp": timestamp,
            **prices,
            "volume": _integer(row["volume"], f"{path_name}.volume"),
        })

    option_rows: list[dict[str, Any]] = []
    option_keys: set[tuple[str, int, str, str, int, dt.datetime]] = set()
    for index, row in enumerate(option_payload):
        path_name = f"fixture.options[{index}]"
        if not isinstance(row, dict):
            raise FixtureValidationError(f"{path_name} must be an object")
        _require_exact_keys(row, OPTION_KEYS, path_name)
        symbol, interval, timestamp, prices = _validate_common(
            row, path_name, active_symbols, window_from, window_to, captured_at
        )
        expiry_code = _integer(row["expiry_code"], f"{path_name}.expiry_code", positive=True)
        relative_strike = str(row["relative_strike"]).strip().upper()
        if not RELATIVE_STRIKE_RE.fullmatch(relative_strike):
            raise FixtureValidationError(f"{path_name}.relative_strike is invalid")
        option_type = str(row["option_type"]).strip().upper()
        if option_type not in {"CALL", "PUT"}:
            raise FixtureValidationError(f"{path_name}.option_type must be CALL or PUT")
        identity = (
            symbol,
            expiry_code,
            relative_strike,
            option_type,
            interval,
            timestamp,
        )
        if identity in option_keys:
            raise FixtureValidationError(f"{path_name} duplicates an option candle identity")
        option_keys.add(identity)
        option_rows.append({
            "symbol": symbol,
            "expiry_code": expiry_code,
            "relative_strike": relative_strike,
            "option_type": option_type,
            "interval_minutes": interval,
            "timestamp": timestamp,
            **prices,
            "strike": _decimal(row["strike"], f"{path_name}.strike", nullable=True),
            "spot": _decimal(row["spot"], f"{path_name}.spot", nullable=True),
            "volume": _integer(row["volume"], f"{path_name}.volume"),
            "oi": _integer(row["oi"], f"{path_name}.oi"),
            "implied_volatility": float(
                _decimal(row["implied_volatility"], f"{path_name}.implied_volatility")
            ),
        })

    return ValidatedFixture(
        source_hash=hashlib.sha256(raw).hexdigest(),
        captured_at=captured_at,
        window_from=window_from,
        window_to=window_to,
        equity=tuple(equity_rows),
        options=tuple(option_rows),
    )


def import_fixture(fixture: ValidatedFixture, *, apply: bool = False) -> ImportResult:
    """Import validated candles atomically, or report a dry run."""
    if not apply:
        return ImportResult(
            equity_rows=len(fixture.equity),
            option_rows=len(fixture.options),
            inserted_equity_rows=0,
            inserted_option_rows=0,
            applied=False,
        )

    with transaction.atomic():
        equity_before = StockEquityCandle.objects.count()
        option_before = StockOptionCandle.objects.count()
        StockEquityCandle.objects.bulk_create(
            [StockEquityCandle(**row) for row in fixture.equity],
            ignore_conflicts=True,
            batch_size=2000,
        )
        StockOptionCandle.objects.bulk_create(
            [StockOptionCandle(**row) for row in fixture.options],
            ignore_conflicts=True,
            batch_size=2000,
        )
        equity_inserted = StockEquityCandle.objects.count() - equity_before
        option_inserted = StockOptionCandle.objects.count() - option_before

        grouped: dict[tuple[str, str, int], int] = defaultdict(int)
        for row in fixture.equity:
            grouped[("equity_import", row["symbol"], row["interval_minutes"])] += 1
        for row in fixture.options:
            grouped[("option_import", row["symbol"], row["interval_minutes"])] += 1
        for (kind, symbol, interval), rows in grouped.items():
            DownloadJob.objects.update_or_create(
                kind=kind,
                symbol=symbol,
                detail=f"{interval}m sha256:{fixture.source_hash[:12]}",
                window_from=fixture.window_from,
                window_to=fixture.window_to,
                defaults={"rows": rows, "status": "DONE", "error": ""},
            )

    return ImportResult(
        equity_rows=len(fixture.equity),
        option_rows=len(fixture.options),
        inserted_equity_rows=equity_inserted,
        inserted_option_rows=option_inserted,
        applied=True,
    )
