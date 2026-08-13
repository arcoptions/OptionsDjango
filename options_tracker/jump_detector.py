import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from statistics import median

from django.core.cache import cache
from django.utils import timezone

from .models import AppSetting, IndexOISnapshot, IndexOptionCandle


EXPIRY_WEEKDAYS = {"NIFTY": 1, "SENSEX": 3}
SIGNAL_TIMES = (time(14, 30), time(14, 55), time(15, 0))


def _empty_report():
    return {
        "patterns": [
            {
                "signal_time": signal_time.strftime("%H:%M"), "samples": 0, "hits_3x": 0,
                "hits_5x": 0, "hits_10x": 0, "hit_rate_3x": 0, "median_multiple": 0,
            }
            for signal_time in SIGNAL_TIMES
        ],
        "segments": [], "event_count": 0, "baseline_hit_rate_3x": 0,
        "scored_count": 0, "scored_hit_rate_3x": 0, "scored_lift": 0,
        "latest_candidates": [], "latest_date": None,
    }


def _number(value):
    return float(value or 0)


def _change(current, previous):
    previous = _number(previous)
    return ((_number(current) - previous) / abs(previous)) if previous else 0.0


def _relative_distance(relative_strike):
    return 0 if relative_strike == "ATM" else abs(int(relative_strike[3:]))


def _feature_score(features, underlying):
    score = 0
    evidence = []
    premium = features["premium"]
    if underlying == "NIFTY":
        if 1 <= premium <= 10:
            score += 35
            evidence.append("₹1–10 historical sweet spot")
        elif premium <= 25:
            score += 12
            evidence.append("low premium")
        if features["signal_time"] == "14:55":
            score += 15
            evidence.append("14:55 NIFTY window")
        elif features["signal_time"] == "14:30":
            score += 8
            evidence.append("14:30 NIFTY window")
        if features["oi_change"] > 0:
            score += 10
            evidence.append("OI rising")
    else:
        if 10 < premium <= 50:
            score += 25
            evidence.append("₹10–50 historical sweet spot")
        elif premium <= 10:
            score += 8
            evidence.append("lottery premium")
        if features["signal_time"] == "15:00":
            score += 20
            evidence.append("15:00 SENSEX window")
        elif features["signal_time"] == "14:55":
            score += 8
            evidence.append("14:55 SENSEX window")
        if features["volume_surge"] >= 3:
            score += 20
            evidence.append("≥3x volume burst")
        elif features["volume_surge"] >= 1.5:
            score += 8
            evidence.append("volume rising")
        if features["iv_change"] <= 0:
            score += 5
            evidence.append("IV compressed")
        if features["option_type"] == "CALL":
            score += 5
            evidence.append("CALL historical edge")

    if features["distance"] <= 2:
        score += 20
        evidence.append("ATM ±2")
    elif features["distance"] <= 5:
        score += 6
        evidence.append("ATM ±3–5")
    if features["premium_momentum"] <= 0:
        score += 10
        evidence.append("premium compressed")
    if features["favorable_spot_move"] > 0:
        score += 5
        evidence.append("spot direction aligned")
    return min(score, 100), evidence


def _row_features(current, prior_rows, signal_time):
    prior = prior_rows[-1] if prior_rows else current
    recent_volumes = [_number(row["volume"]) for row in prior_rows[-10:] if _number(row["volume"]) > 0]
    volume_baseline = median(recent_volumes) if recent_volumes else 0
    option_type = current["option_type"]
    spot_move = _change(current["spot"], prior["spot"])
    return {
        "premium": _number(current["close"]),
        "premium_momentum": _change(current["close"], prior["close"]),
        "oi_change": _change(current["oi"], prior["oi"]),
        "iv_change": _number(current["implied_volatility"]) - _number(prior["implied_volatility"]),
        "volume_surge": (_number(current["volume"]) / volume_baseline) if volume_baseline else 0,
        "favorable_spot_move": spot_move if option_type == "CALL" else -spot_move,
        "signal_time": signal_time.strftime("%H:%M"),
        "distance": _relative_distance(current["relative_strike"]),
        "option_type": option_type,
    }


def _historical_events(underlying, days=45):
    cutoff = timezone.localdate() - timedelta(days=days)
    rows = IndexOptionCandle.objects.filter(
        underlying=underlying,
        interval_minutes=1,
        timestamp__date__gte=cutoff,
        timestamp__time__gte=time(14, 15),
        timestamp__time__lte=time(15, 30),
    ).values(
        "timestamp", "relative_strike", "option_type", "strike", "spot",
        "close", "high", "volume", "oi", "implied_volatility",
    ).order_by("timestamp")

    contracts = defaultdict(list)
    expiry_weekday = EXPIRY_WEEKDAYS[underlying]
    for row in rows:
        local_timestamp = timezone.localtime(row["timestamp"])
        if local_timestamp.weekday() != expiry_weekday:
            continue
        row["local_timestamp"] = local_timestamp
        contracts[(local_timestamp.date(), row["relative_strike"], row["option_type"])].append(row)

    events = []
    for (session_date, relative_strike, option_type), contract_rows in contracts.items():
        for signal_time in SIGNAL_TIMES:
            current = next(
                (row for row in contract_rows if row["local_timestamp"].time().replace(second=0, microsecond=0) == signal_time),
                None,
            )
            if not current or not current["close"] or not (1 <= _number(current["close"]) <= 200):
                continue
            signal_at = current["local_timestamp"]
            prior_rows = [row for row in contract_rows if signal_at - timedelta(minutes=10) <= row["local_timestamp"] < signal_at]
            future_rows = [row for row in contract_rows if signal_at < row["local_timestamp"] <= signal_at + timedelta(minutes=30)]
            if not prior_rows or not future_rows:
                continue
            features = _row_features(current, prior_rows, signal_time)
            score, evidence = _feature_score(features, underlying)
            future_high = max(_number(row["high"]) for row in future_rows)
            events.append({
                "date": session_date.isoformat(),
                "signal_time": signal_time.strftime("%H:%M"),
                "relative_strike": relative_strike,
                "option_type": option_type,
                "strike": _number(current["strike"]),
                "premium": features["premium"],
                "score": score,
                "evidence": evidence,
                "max_multiple": round(future_high / features["premium"], 2),
                **{key: round(value, 4) for key, value in features.items() if key not in {"premium", "signal_time", "option_type"}},
            })
    return events


def _segment_report(events):
    groups = defaultdict(list)
    for event in events:
        premium = event["premium"]
        definitions = {
            "Side": event["option_type"],
            "Strike": (
                "ATM ±2" if event["relative_strike"] == "ATM" or int(event["relative_strike"][3:]) in range(-2, 3)
                else "ATM ±3–5" if abs(int(event["relative_strike"][3:])) <= 5
                else "Beyond ATM ±5"
            ),
            "Premium": "₹1–10" if premium <= 10 else "₹10–25" if premium <= 25 else "₹25–50" if premium <= 50 else "₹50–100" if premium <= 100 else "₹100–200",
            "Momentum": "Positive" if event["premium_momentum"] > 0 else "Negative",
            "Volume": "Burst ≥3x" if event["volume_surge"] >= 3 else "Rising 1.5–3x" if event["volume_surge"] >= 1.5 else "Normal",
            "IV": "Expanding" if event["iv_change"] > 0 else "Falling",
            "OI": "Rising" if event["oi_change"] > 0 else "Falling/flat",
        }
        for feature, value in definitions.items():
            groups[(feature, value)].append(event)
    segments = []
    for (feature, value), rows in groups.items():
        if len(rows) < 10:
            continue
        hits = sum(row["max_multiple"] >= 3 for row in rows)
        segments.append({
            "feature": feature,
            "value": value,
            "samples": len(rows),
            "hits_3x": hits,
            "hit_rate_3x": round((hits / len(rows)) * 100, 1),
        })
    return sorted(segments, key=lambda row: (row["hit_rate_3x"], row["samples"]), reverse=True)


def historical_jump_report(underlying, days=45, use_cache=True):
    underlying = underlying.upper()
    cache_key = f"jump-report:{underlying}:{days}:v1"
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            return cached
        stored = AppSetting.objects.filter(key=f"jump_report_{underlying.lower()}_{days}").values_list("value", flat=True).first()
        if stored:
            try:
                report = json.loads(stored)
            except (TypeError, json.JSONDecodeError):
                report = None
            if report:
                cache.set(cache_key, report, 3600)
                return report
        return _empty_report()
    events = _historical_events(underlying, days=days)
    patterns = []
    for signal_time in (value.strftime("%H:%M") for value in SIGNAL_TIMES):
        slot = [event for event in events if event["signal_time"] == signal_time]
        hits_3x = sum(event["max_multiple"] >= 3 for event in slot)
        patterns.append({
            "signal_time": signal_time,
            "samples": len(slot),
            "hits_3x": hits_3x,
            "hits_5x": sum(event["max_multiple"] >= 5 for event in slot),
            "hits_10x": sum(event["max_multiple"] >= 10 for event in slot),
            "hit_rate_3x": round((hits_3x / len(slot)) * 100, 1) if slot else 0,
            "median_multiple": round(median(event["max_multiple"] for event in slot), 2) if slot else 0,
        })
    scored = [event for event in events if event["score"] >= 55]
    scored_hits = sum(event["max_multiple"] >= 3 for event in scored)
    all_hits = sum(event["max_multiple"] >= 3 for event in events)
    latest_date = max((event["date"] for event in events), default=None)
    report = {
        "patterns": patterns,
        "segments": _segment_report(events)[:8],
        "event_count": len(events),
        "baseline_hit_rate_3x": round((all_hits / len(events)) * 100, 1) if events else 0,
        "scored_count": len(scored),
        "scored_hit_rate_3x": round((scored_hits / len(scored)) * 100, 1) if scored else 0,
        "scored_lift": round(((scored_hits / len(scored)) - (all_hits / len(events))) * 100, 1) if scored and events else 0,
        "latest_candidates": sorted(
            (event for event in events if event["date"] == latest_date),
            key=lambda event: (event["score"], event["max_multiple"]),
            reverse=True,
        )[:10],
        "latest_date": latest_date,
    }
    cache.set(cache_key, report, 600)
    return report


def refresh_historical_jump_report(underlying, days=45):
    report = historical_jump_report(underlying, days=days, use_cache=False)
    AppSetting.objects.update_or_create(
        key=f"jump_report_{underlying.lower()}_{days}",
        defaults={"value": json.dumps(report)},
    )
    cache.set(f"jump-report:{underlying.upper()}:{days}:v1", report, 3600)
    return report


def jump_detector_state(underlying):
    latest = IndexOISnapshot.objects.filter(underlying=underlying).first()
    now = timezone.localtime()
    expiry_day = now.weekday() == EXPIRY_WEEKDAYS[underlying]
    in_window = time(14, 25) <= now.time() <= time(15, 10)
    fresh = bool(latest and timezone.localtime(latest.created_at) >= now - timedelta(minutes=3))
    active = expiry_day and in_window and fresh
    if active:
        label = "ACTIVE EXPIRY WINDOW"
    elif not expiry_day:
        label = "OFF EXPIRY DAY"
    elif not in_window:
        label = "OUTSIDE 14:25–15:10"
    else:
        label = "WAITING FOR FRESH SNAPSHOT"
    return {"active": active, "label": label}


def live_jump_candidates(underlying, limit=8):
    latest = IndexOISnapshot.objects.filter(underlying=underlying).prefetch_related("strikes").first()
    if not latest or latest.atm_strike is None:
        return []
    previous = IndexOISnapshot.objects.filter(
        underlying=underlying, expiry_date=latest.expiry_date, created_at__lt=latest.created_at,
    ).prefetch_related("strikes").first()
    previous_rows = {(row.strike, row.option_type): row for row in previous.strikes.all()} if previous else {}
    strike_step = Decimal("50" if underlying == "NIFTY" else "100")
    candidate_features = []
    for row in latest.strikes.all():
        if abs(row.strike - latest.atm_strike) > strike_step * 5 or not (Decimal("1") <= row.last_price <= Decimal("200")):
            continue
        prior = previous_rows.get((row.strike, row.option_type))
        prior_price = prior.last_price if prior else row.last_price - row.price_change
        prior_oi = prior.oi if prior else row.oi - row.oi_change
        prior_iv = prior.implied_volatility if prior else row.implied_volatility
        spot_move = _change(latest.underlying_price, previous.underlying_price) if previous else 0
        features = {
            "premium": _number(row.last_price),
            "premium_momentum": _change(row.last_price, prior_price),
            "oi_change": _change(row.oi, prior_oi),
            "iv_change": row.implied_volatility - prior_iv,
            "volume_surge": 0,
            "favorable_spot_move": spot_move if row.option_type == "CE" else -spot_move,
            "signal_time": timezone.localtime(latest.created_at).strftime("%H:%M"),
            "distance": abs(int((row.strike - latest.atm_strike) / strike_step)),
            "option_type": "CALL" if row.option_type == "CE" else "PUT",
        }
        volume_delta = max(row.volume - prior.volume, 0) if prior else 0
        candidate_features.append((row, features, volume_delta))

    volume_deltas = [volume_delta for _, _, volume_delta in candidate_features if volume_delta > 0]
    volume_baseline = median(volume_deltas) if volume_deltas else 0
    candidates = []
    for row, features, volume_delta in candidate_features:
        features["volume_surge"] = (volume_delta / volume_baseline) if volume_baseline else 0
        score, evidence = _feature_score(features, underlying)
        relative_steps = int((row.strike - latest.atm_strike) / strike_step)
        candidates.append({
            "strike": row.strike,
            "option_type": row.option_type,
            "relative_strike": "ATM" if relative_steps == 0 else f"ATM{relative_steps:+d}",
            "premium": row.last_price,
            "score": score,
            "evidence": evidence,
            "oi_change": row.oi_change,
            "iv": row.implied_volatility,
            "volume": row.volume,
        })
    return sorted(candidates, key=lambda candidate: candidate["score"], reverse=True)[:limit]