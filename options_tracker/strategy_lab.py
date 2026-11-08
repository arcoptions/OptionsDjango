import hashlib
import json
import os

from django.db import transaction

from .models import StockEquityCandle, StrategyDefinition, StrategyRunManifest


TEMPLATES = {
    "EQUITY_MOMENTUM": {
        "lookback": (2, 252),
        "minimum_move_pct": (0, 50),
        "minimum_volume_ratio": (0, 20),
    },
    "OPENING_RANGE": {
        "opening_minutes": (5, 60),
        "breakout_pct": (0, 10),
        "stop_pct": (0.1, 25),
    },
}
COST_FIELDS = {
    "brokerage_per_order": (0, 10000),
    "tax_bps": (0, 1000),
    "slippage_bps": (0, 1000),
    "spread_bps": (0, 1000),
}


def _validate_numeric_map(values, schema, label):
    if not isinstance(values, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(values) - set(schema)
    missing = set(schema) - set(values)
    if unknown:
        raise ValueError(f"Unsupported {label} fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing {label} fields: {', '.join(sorted(missing))}")
    normalized = {}
    for field, (minimum, maximum) in schema.items():
        try:
            value = float(values[field])
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} field {field} must be numeric") from error
        if not minimum <= value <= maximum:
            raise ValueError(
                f"{label} field {field} must be between {minimum} and {maximum}",
            )
        normalized[field] = value
    return normalized


def validate_strategy(template, parameters, cost_model):
    if template not in TEMPLATES:
        raise ValueError(f"Unsupported strategy template: {template!r}")
    return (
        _validate_numeric_map(parameters, TEMPLATES[template], "parameter"),
        _validate_numeric_map(cost_model, COST_FIELDS, "cost"),
    )


@transaction.atomic
def create_definition(*, owner, name, template, symbol, interval_minutes, parameters, cost_model):
    parameters, cost_model = validate_strategy(template, parameters, cost_model)
    if interval_minutes not in {1, 5, 15, 30, 60, 1440}:
        raise ValueError("Unsupported strategy interval")
    return StrategyDefinition.objects.create(
        owner=owner,
        name=name.strip(),
        template=template,
        symbol=symbol.strip().upper(),
        interval_minutes=interval_minutes,
        parameters=parameters,
        cost_model=cost_model,
    )


def create_manifest(definition, data_start, data_end):
    if data_start >= data_end:
        raise ValueError("Manifest data start must precede data end")
    digest = hashlib.sha256()
    rows = StockEquityCandle.objects.filter(
        symbol=definition.symbol,
        interval_minutes=definition.interval_minutes,
        timestamp__gte=data_start,
        timestamp__lte=data_end,
    ).order_by("timestamp").values_list(
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    count = 0
    for row in rows.iterator(chunk_size=1000):
        digest.update("|".join(str(value) for value in row).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    if not count:
        raise ValueError("No stored candles exist for the requested manifest range")
    definition_snapshot = {
        "name": definition.name,
        "template": definition.template,
        "symbol": definition.symbol,
        "interval_minutes": definition.interval_minutes,
        "parameters": definition.parameters,
        "version": definition.version,
    }
    data_hash = digest.hexdigest()
    code_version = os.getenv("ARC_DEPLOYED_COMMIT", "local-uncommitted")
    payload = {
        "definition": definition_snapshot,
        "cost_model": definition.cost_model,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "data_rows": count,
        "data_hash": data_hash,
        "code_version": code_version,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    manifest, _ = StrategyRunManifest.objects.get_or_create(
        manifest_hash=manifest_hash,
        defaults={
            "definition": definition,
            "definition_snapshot": definition_snapshot,
            "cost_snapshot": definition.cost_model,
            "data_start": data_start,
            "data_end": data_end,
            "data_rows": count,
            "data_hash": data_hash,
            "code_version": code_version,
        },
    )
    return manifest
