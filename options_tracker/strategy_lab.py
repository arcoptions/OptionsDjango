import hashlib
import json
import os
from collections import deque

from django.db import transaction
from django.utils import timezone

from .deployment import running_commit
from .models import (
    StockEquityCandle,
    StrategyDefinition,
    StrategyRunManifest,
    StrategyStatus,
)


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
MAX_BACKTEST_ROWS = 250000
BACKTEST_NOTIONAL = 100000
PROMOTION_MINIMUM_TRADES = 20
PROMOTION_MINIMUM_VALIDATION_TRADES = 5
PROMOTION_MAXIMUM_DRAWDOWN_PCT = 25


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


def promotion_assessment(manifest):
    result = manifest.result_snapshot
    validation = manifest.validation_snapshot.get("validation", {})
    checks = [
        {
            "label": f"At least {PROMOTION_MINIMUM_TRADES} total trades",
            "passed": result.get("trades", 0) >= PROMOTION_MINIMUM_TRADES,
        },
        {
            "label": (
                f"At least {PROMOTION_MINIMUM_VALIDATION_TRADES} validation trades"
            ),
            "passed": (
                validation.get("trades", 0)
                >= PROMOTION_MINIMUM_VALIDATION_TRADES
            ),
        },
        {
            "label": "Positive validation expectancy",
            "passed": (validation.get("expectancy_pct") or 0) > 0,
        },
        {
            "label": (
                "Validation drawdown does not exceed "
                f"{PROMOTION_MAXIMUM_DRAWDOWN_PCT}%"
            ),
            "passed": (
                validation.get("max_drawdown_pct", 100)
                <= PROMOTION_MAXIMUM_DRAWDOWN_PCT
            ),
        },
        {
            "label": "Chronological validation is complete",
            "passed": manifest.validation_snapshot.get("status") == "COMPLETE",
        },
    ]
    return {
        "eligible": all(check["passed"] for check in checks),
        "checks": checks,
    }


@transaction.atomic
def promote_manifest(*, manifest, actor, reason):
    if manifest.definition.owner_id != actor.pk:
        raise ValueError("Only the strategy owner can promote this manifest.")
    reason = reason.strip()
    if len(reason) < 20:
        raise ValueError("Promotion reason must contain at least 20 characters.")
    assessment = promotion_assessment(manifest)
    if not assessment["eligible"]:
        raise ValueError("Manifest does not meet the paper-promotion evidence gates.")
    definition = manifest.definition
    definition.status = StrategyStatus.PAPER_APPROVED
    definition.promotion_manifest = manifest
    definition.promotion_reason = reason
    definition.promoted_by = actor
    definition.promoted_at = timezone.now()
    definition.save(update_fields=[
        "status",
        "promotion_manifest",
        "promotion_reason",
        "promoted_by",
        "promoted_at",
        "updated_at",
    ])
    return definition


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


def _trade_return(entry, exit_price, cost_model):
    variable_bps = (
        cost_model["tax_bps"]
        + 2 * cost_model["slippage_bps"]
        + cost_model["spread_bps"]
    )
    fixed_cost_pct = 2 * cost_model["brokerage_per_order"] / BACKTEST_NOTIONAL * 100
    return (exit_price / entry - 1) * 100 - variable_bps / 100 - fixed_cost_pct


def _metrics(returns):
    if not returns:
        return {
            "status": "INSUFFICIENT_DATA",
            "trades": 0,
            "win_rate": None,
            "expectancy_pct": None,
            "profit_factor": None,
            "net_return_pct": 0,
            "max_drawdown_pct": 0,
        }
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "status": "COMPLETE",
        "trades": len(returns),
        "win_rate": round(len(wins) / len(returns) * 100, 2),
        "expectancy_pct": round(sum(returns) / len(returns), 4),
        "profit_factor": round(gross_profit / gross_loss, 4)
        if gross_loss else None,
        "net_return_pct": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
    }


def _momentum_returns(rows, parameters, cost_model):
    lookback = int(parameters["lookback"])
    closes = deque(maxlen=lookback)
    volumes = deque(maxlen=lookback)
    pending = False
    position = None
    returns = []
    for row in rows:
        _, open_price, _, _, close_price, volume = row
        open_price = float(open_price)
        close_price = float(close_price)
        volume = float(volume or 0)
        if pending and position is None:
            position = {"entry": open_price, "held": 0}
            pending = False
        if position is not None:
            position["held"] += 1
            if position["held"] >= lookback:
                returns.append(
                    _trade_return(position["entry"], close_price, cost_model),
                )
                position = None
        if position is None and len(closes) == lookback and len(volumes) == lookback:
            past_close = closes[0]
            move_pct = (close_price / past_close - 1) * 100 if past_close else 0
            average_volume = sum(volumes) / len(volumes) if volumes else 0
            volume_ratio = volume / average_volume if average_volume else 0
            pending = (
                move_pct >= parameters["minimum_move_pct"]
                and volume_ratio >= parameters["minimum_volume_ratio"]
            )
        closes.append(close_price)
        volumes.append(volume)
    if position is not None:
        returns.append(_trade_return(position["entry"], float(rows[-1][4]), cost_model))
    return returns


def _opening_range_returns(rows, parameters, cost_model):
    days = {}
    for row in rows:
        day = timezone.localtime(row[0]).date()
        days.setdefault(day, []).append(row)
    returns = []
    opening_minutes = int(parameters["opening_minutes"])
    for day_rows in days.values():
        if len(day_rows) < 3:
            continue
        first_at = day_rows[0][0]
        opening = [
            row
            for row in day_rows
            if (row[0] - first_at).total_seconds() < opening_minutes * 60
        ]
        if not opening or len(opening) >= len(day_rows):
            continue
        range_high = max(float(row[2]) for row in opening)
        trigger = range_high * (1 + parameters["breakout_pct"] / 100)
        entry = None
        stop = None
        for index in range(len(opening), len(day_rows)):
            row = day_rows[index]
            if entry is None and float(row[4]) >= trigger and index + 1 < len(day_rows):
                entry = float(day_rows[index + 1][1])
                stop = entry * (1 - parameters["stop_pct"] / 100)
                continue
            if entry is not None and float(row[3]) <= stop:
                returns.append(_trade_return(entry, stop, cost_model))
                entry = None
                break
        if entry is not None:
            returns.append(_trade_return(entry, float(day_rows[-1][4]), cost_model))
    return returns


def backtest_snapshot(template, parameters, cost_model, rows):
    if template == "EQUITY_MOMENTUM":
        returns = _momentum_returns(rows, parameters, cost_model)
    elif template == "OPENING_RANGE":
        returns = _opening_range_returns(rows, parameters, cost_model)
    else:
        raise ValueError(f"Unsupported strategy template: {template!r}")
    return _metrics(returns)


def _validation_snapshot(definition, rows):
    split = int(len(rows) * 0.7)
    if split < 2 or len(rows) - split < 2:
        return {
            "status": "INSUFFICIENT_DATA",
            "split": "70/30 chronological",
            "training_rows": split,
            "validation_rows": len(rows) - split,
            "training": _metrics([]),
            "validation": _metrics([]),
        }
    training = backtest_snapshot(
        definition.template,
        definition.parameters,
        definition.cost_model,
        rows[:split],
    )
    validation = backtest_snapshot(
        definition.template,
        definition.parameters,
        definition.cost_model,
        rows[split:],
    )
    return {
        "status": (
            "COMPLETE"
            if training["status"] == "COMPLETE"
            and validation["status"] == "COMPLETE"
            else "INSUFFICIENT_DATA"
        ),
        "split": "70/30 chronological",
        "training_rows": split,
        "validation_rows": len(rows) - split,
        "training": training,
        "validation": validation,
    }


def create_manifest(definition, data_start, data_end):
    if data_start >= data_end:
        raise ValueError("Manifest data start must precede data end")
    digest = hashlib.sha256()
    rows = list(StockEquityCandle.objects.filter(
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
    )[:MAX_BACKTEST_ROWS + 1])
    count = len(rows)
    if count > MAX_BACKTEST_ROWS:
        raise ValueError(
            f"Manifest exceeds the {MAX_BACKTEST_ROWS}-row backtest limit",
        )
    for row in rows:
        digest.update("|".join(str(value) for value in row).encode("utf-8"))
        digest.update(b"\n")
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
    code_version = (
        running_commit()
        or os.getenv("ARC_DEPLOYED_COMMIT", "").strip()
        or "local-uncommitted"
    )
    result_snapshot = backtest_snapshot(
        definition.template,
        definition.parameters,
        definition.cost_model,
        rows,
    )
    validation_snapshot = _validation_snapshot(definition, rows)
    payload = {
        "definition": definition_snapshot,
        "cost_model": definition.cost_model,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "data_rows": count,
        "data_hash": data_hash,
        "code_version": code_version,
        "result_snapshot": result_snapshot,
        "validation_snapshot": validation_snapshot,
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
            "result_snapshot": result_snapshot,
            "validation_snapshot": validation_snapshot,
        },
    )
    return manifest
