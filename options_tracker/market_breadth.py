"""Offline historical breadth snapshots built from complete radar sessions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from statistics import mean

from django.utils import timezone

from .indicator_math import ema_latest, wilder_rsi_latest
from .radar_models import MarketBreadthSnapshot, RadarBar, RadarInstrument
from .radar_sessions import session_minutes


BREADTH_VERSION = "market-breadth-v1"
MIN_COVERAGE_PERCENT = 80.0
SNAPSHOT_MAX_AGE = timedelta(days=4)
VOLUME_SURGE_MULTIPLIER = 1.5


@dataclass(frozen=True)
class DailyEvidence:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source_at: datetime


def complete_daily_evidence(rows: list[dict]) -> list[DailyEvidence]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[timezone.localdate(row["timestamp"])].append(row)
    evidence = []
    for day, group in sorted(grouped.items()):
        group.sort(key=lambda row: row["timestamp"])
        start = timezone.make_aware(datetime.combine(day, datetime.min.time())).replace(
            hour=9,
            minute=15,
        )
        expected = session_minutes(day)
        if len(group) != expected or any(
            not row["complete"]
            or row["timestamp"] != start + timedelta(minutes=index)
            for index, row in enumerate(group)
        ):
            continue
        evidence.append(
            DailyEvidence(
                day=day,
                open=group[0]["open"],
                high=max(row["high"] for row in group),
                low=min(row["low"] for row in group),
                close=group[-1]["close"],
                volume=sum(row["volume"] for row in group),
                source_at=group[-1]["timestamp"],
            )
        )
    return evidence


def _coverage(covered: int, active: int) -> dict:
    percent = round(covered / active * 100, 1) if active else 0.0
    return {
        "covered": covered,
        "active": active,
        "percent": percent,
        "available": bool(active) and percent >= MIN_COVERAGE_PERCENT,
    }


def summarize_breadth_evidence(
    evidence_by_instrument: dict[int, list[DailyEvidence]],
    active_instruments: int,
    universe: str = "FNO",
) -> dict:
    ema_metrics = {}
    for period in (20, 50, 200):
        values = []
        for evidence in evidence_by_instrument.values():
            closes = [row.close for row in evidence]
            average = ema_latest(closes, period) if len(closes) >= period else None
            if average is not None:
                values.append(closes[-1] > average)
        coverage = _coverage(len(values), active_instruments)
        ema_metrics[str(period)] = {
            **coverage,
            "above": sum(values),
            "below_or_equal": len(values) - sum(values),
        }

    rsi_values = [
        value
        for evidence in evidence_by_instrument.values()
        if (value := wilder_rsi_latest([row.close for row in evidence])) is not None
    ]
    rsi_coverage = _coverage(len(rsi_values), active_instruments)
    rsi = {
        **rsi_coverage,
        "oversold": sum(value < 30 for value in rsi_values),
        "neutral": sum(30 <= value <= 70 for value in rsi_values),
        "overbought": sum(value > 70 for value in rsi_values),
    }

    volume_values = []
    for evidence in evidence_by_instrument.values():
        if len(evidence) >= 21:
            baseline = mean(row.volume for row in evidence[-21:-1])
            volume_values.append(
                baseline > 0
                and evidence[-1].volume >= baseline * VOLUME_SURGE_MULTIPLIER
            )
    volume_coverage = _coverage(len(volume_values), active_instruments)
    volume = {
        **volume_coverage,
        "surging": sum(volume_values),
        "multiplier": VOLUME_SURGE_MULTIPLIER,
    }

    high_low_values = []
    for evidence in evidence_by_instrument.values():
        if len(evidence) >= 252:
            current = evidence[-1]
            prior = evidence[-252:-1]
            high_low_values.append(
                (
                    current.high >= max(row.high for row in prior),
                    current.low <= min(row.low for row in prior),
                )
            )
    high_low_coverage = _coverage(len(high_low_values), active_instruments)
    high_low = {
        **high_low_coverage,
        "new_highs": sum(is_high for is_high, _ in high_low_values),
        "new_lows": sum(is_low for _, is_low in high_low_values),
    }

    metrics = {
        "ema": ema_metrics,
        "rsi": rsi,
        "volume": volume,
        "high_low_52_week": high_low,
    }
    labels = {
        "ema": "EMA breadth",
        "rsi": "RSI distribution",
        "volume": "Volume surge",
        "high_low_52_week": "52-week highs and lows",
    }
    unavailable = []
    for key, metric in metrics.items():
        if key == "ema":
            missing = [
                period
                for period, period_metric in metric.items()
                if not period_metric["available"]
            ]
            if missing:
                unavailable.append({
                    "name": labels[key],
                    "reason": (
                        f"Coverage is below {MIN_COVERAGE_PERCENT:.0f}% for "
                        f"EMA {', '.join(missing)}."
                    ),
                })
        elif not metric["available"]:
            unavailable.append({
                "name": labels[key],
                "reason": (
                    f"Complete-history coverage is {metric['percent']:.1f}%; "
                    f"{MIN_COVERAGE_PERCENT:.0f}% is required."
                ),
            })

    hash_rows = [
        {
            "instrument_id": instrument_id,
            "evidence": [
                {
                    **asdict(row),
                    "day": row.day.isoformat(),
                    "source_at": row.source_at.isoformat(),
                }
                for row in evidence
            ],
        }
        for instrument_id, evidence in sorted(evidence_by_instrument.items())
    ]
    input_hash = sha256(
        json.dumps(hash_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    source_at = max(
        (
            evidence[-1].source_at
            for evidence in evidence_by_instrument.values()
            if evidence
        ),
        default=None,
    )
    return {
        "universe": universe,
        "version": BREADTH_VERSION,
        "input_hash": input_hash,
        "source_at": source_at,
        "active_instruments": active_instruments,
        "covered_instruments": sum(bool(rows) for rows in evidence_by_instrument.values()),
        "metrics": metrics,
        "unavailable": unavailable,
    }


def collect_breadth_evidence(as_of: datetime | None = None) -> tuple[dict, int]:
    as_of = as_of or timezone.now()
    instruments = list(
        RadarInstrument.objects.filter(kind="EQUITY", active=True)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    rows_by_instrument = defaultdict(list)
    rows = (
        RadarBar.objects.filter(
            instrument_id__in=instruments,
            timestamp__lte=as_of,
            timestamp__gte=as_of - timedelta(days=400),
        )
        .order_by("instrument_id", "timestamp")
        .values(
            "instrument_id",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "complete",
        )
        .iterator(chunk_size=5000)
    )
    for row in rows:
        rows_by_instrument[row["instrument_id"]].append(row)
    evidence = {
        instrument_id: complete_daily_evidence(rows_by_instrument[instrument_id])
        for instrument_id in instruments
    }
    return evidence, len(instruments)


def build_market_breadth_snapshot(
    *,
    universe: str = "FNO",
    as_of: datetime | None = None,
    apply: bool = False,
) -> tuple[dict, MarketBreadthSnapshot | None, bool]:
    evidence, active_instruments = collect_breadth_evidence(as_of)
    summary = summarize_breadth_evidence(
        evidence,
        active_instruments,
        universe,
    )
    if not apply or summary["source_at"] is None:
        return summary, None, False
    snapshot, created = MarketBreadthSnapshot.objects.get_or_create(
        universe=summary["universe"],
        version=summary["version"],
        input_hash=summary["input_hash"],
        defaults={
            "source_at": summary["source_at"],
            "active_instruments": summary["active_instruments"],
            "covered_instruments": summary["covered_instruments"],
            "metrics": summary["metrics"],
            "unavailable": summary["unavailable"],
        },
    )
    return summary, snapshot, created


def latest_market_breadth_snapshot(
    now: datetime,
    universe: str = "FNO",
) -> MarketBreadthSnapshot | None:
    snapshot = (
        MarketBreadthSnapshot.objects.filter(
            universe=universe,
            version=BREADTH_VERSION,
        )
        .order_by("-source_at", "-pk")
        .first()
    )
    if snapshot is None or now - snapshot.source_at > SNAPSHOT_MAX_AGE:
        return None
    return snapshot
