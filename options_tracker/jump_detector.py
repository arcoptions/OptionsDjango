import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from statistics import median

from django.core.cache import cache
from django.utils import timezone

from .models import AppSetting, IndexOISnapshot, IndexOptionCandle, IndexOptionStrikeSnapshot
from .strategy_backtest import nifty_put_strategy_config, spot_setup_timestamps


EXPIRY_WEEKDAYS = {"NIFTY": 1, "SENSEX": 3}
SIGNAL_TIMES = (time(14, 30), time(14, 55), time(15, 0))
OPENING_START = time(9, 25)
OPENING_END = time(10, 0)


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


def _session_bounds(session_date):
    start = timezone.make_aware(datetime.combine(session_date, time.min))
    return start, start + timedelta(days=1)


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


def _trade_levels(reference_price, recent_support=None):
    entry = round(reference_price * 1.005, 2)
    stop = round(entry * 0.92, 2)
    risk = entry - stop
    return {
        "entry": entry,
        "stop_loss": stop,
        "target_1": round(entry + (risk * 2), 2),
        "target_2": round(entry + (risk * 3), 2),
        "risk_percent": round((risk / entry) * 100, 1),
    }


def _opening_breakout_report(underlying, days=45, session_date=None):
    cutoff = timezone.localdate() - timedelta(days=days)
    cutoff_start, _ = _session_bounds(cutoff)
    query = IndexOptionCandle.objects.filter(
        underlying=underlying, interval_minutes=1, timestamp__gte=cutoff_start,
    )
    if session_date:
        session_start, session_end = _session_bounds(session_date)
        query = query.filter(timestamp__gte=session_start, timestamp__lt=session_end)
    query = query.values(
        "timestamp", "strike", "option_type", "open", "high", "low", "close", "volume", "spot",
    ).order_by("timestamp")

    contracts = defaultdict(list)
    for row in query:
        row["local_timestamp"] = timezone.localtime(row["timestamp"])
        contracts[(row["local_timestamp"].date(), row["strike"], row["option_type"])].append(row)

    signals = []
    for (trade_date, strike, option_type), rows in contracts.items():
        for index in range(5, len(rows) - 1):
            current = rows[index]
            clock = current["local_timestamp"].time()
            premium, spot = _number(current["close"]), _number(current["spot"])
            if not (OPENING_START <= clock <= OPENING_END) or not (10 <= premium <= 200) or not spot:
                continue
            prior = rows[index - 5:index]
            prior_high = max(_number(row["high"]) for row in prior)
            volumes = [_number(row["volume"]) for row in prior if _number(row["volume"]) > 0]
            volume_baseline = median(volumes) if volumes else 0
            volume_ratio = (_number(current["volume"]) / volume_baseline) if volume_baseline else 0
            spot_start = _number(prior[0]["spot"]) or spot
            aligned = spot > spot_start if option_type == "CALL" else spot < spot_start
            if premium <= prior_high * 1.002 or volume_ratio < 1.5 or not aligned:
                continue

            next_row = rows[index + 1]
            levels = _trade_levels(max(_number(next_row["open"]), premium))
            outcome, exit_price, exit_at = "TIME_EXIT", None, None
            for future in rows[index + 1:]:
                if future["local_timestamp"].time() > time(15, 20):
                    break
                if _number(future["low"]) <= levels["stop_loss"]:
                    outcome, exit_price, exit_at = "STOP", levels["stop_loss"], future["local_timestamp"]
                    break
                if _number(future["high"]) >= levels["target_1"]:
                    outcome, exit_price, exit_at = "TARGET_1", levels["target_1"], future["local_timestamp"]
                    break
            if exit_price is None:
                future_rows = [row for row in rows[index + 1:] if row["local_timestamp"].time() <= time(15, 20)]
                if not future_rows:
                    continue
                exit_price = _number(future_rows[-1]["close"]) * 0.995
                exit_at = future_rows[-1]["local_timestamp"]
            risk = levels["entry"] - levels["stop_loss"]
            signals.append({
                "date": trade_date.isoformat(), "signal_at": current["local_timestamp"].isoformat(),
                "exit_at": exit_at.isoformat(),
                "strike": _number(strike), "option_type": option_type, "volume_ratio": round(volume_ratio, 1),
                "outcome": outcome, "realized_r": round((exit_price - levels["entry"]) / risk, 2), **levels,
            })

    trades = []
    for trade_date in sorted({signal["date"] for signal in signals}):
        daily = [signal for signal in signals if signal["date"] == trade_date]
        first_at = min(signal["signal_at"] for signal in daily)
        trades.append(max(
            (signal for signal in daily if signal["signal_at"] == first_at),
            key=lambda signal: signal["volume_ratio"],
        ))
    wins = [trade for trade in trades if trade["outcome"] == "TARGET_1"]
    return {
        "sample_days": len(trades), "wins": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_r": round(sum(trade["realized_r"] for trade in trades), 2),
        "average_r": round(sum(trade["realized_r"] for trade in trades) / len(trades), 2) if trades else 0,
        "validated": len(trades) >= 20 and len(wins) / len(trades) >= 0.45,
        "trades": trades,
    }


def _historical_trade_outcome(current, prior_rows, future_rows):
    reference = max(_number(current["high"]), _number(current["close"]))
    recent_support = min(_number(row["low"]) for row in prior_rows if _number(row["low"]) > 0)
    levels = _trade_levels(reference, recent_support)
    entered = False
    outcome = "NO_ENTRY"
    exit_price = None
    for row in future_rows:
        high, low = _number(row["high"]), _number(row["low"])
        if not entered:
            if high < levels["entry"]:
                continue
            entered = True
        if low <= levels["stop_loss"]:
            outcome, exit_price = "STOP", levels["stop_loss"]
            break
        if high >= levels["target_2"]:
            outcome, exit_price = "TARGET_2", levels["target_2"]
            break
        if high >= levels["target_1"]:
            outcome, exit_price = "TARGET_1", levels["target_1"]
            break
    if entered and exit_price is None:
        outcome = "TIME_EXIT"
        exit_price = _number(future_rows[-1]["close"])
    risk = levels["entry"] - levels["stop_loss"]
    return {
        **levels,
        "trade_outcome": outcome,
        "return_percent": round(((exit_price / levels["entry"]) - 1) * 100, 1) if exit_price else 0,
        "realized_r": round((exit_price - levels["entry"]) / risk, 2) if exit_price and risk else 0,
    }


def _historical_events(underlying, days=45, session_date=None):
    cutoff = timezone.localdate() - timedelta(days=days)
    cutoff_start, _ = _session_bounds(cutoff)
    rows = IndexOptionCandle.objects.filter(
        underlying=underlying,
        interval_minutes=1,
        timestamp__gte=cutoff_start,
    )
    if session_date:
        session_start, session_end = _session_bounds(session_date)
        rows = rows.filter(timestamp__gte=session_start, timestamp__lt=session_end)
    rows = rows.values(
        "timestamp", "relative_strike", "option_type", "strike", "spot",
        "close", "high", "low", "volume", "oi", "implied_volatility",
    ).order_by("timestamp")

    contracts = defaultdict(list)
    for row in rows:
        local_timestamp = timezone.localtime(row["timestamp"])
        if local_timestamp.weekday() >= 5:
            continue
        row["local_timestamp"] = local_timestamp
        contracts[(local_timestamp.date(), row["strike"], row["option_type"])].append(row)

    events = []
    for (event_date, strike, option_type), contract_rows in contracts.items():
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
            trade_outcome = _historical_trade_outcome(current, prior_rows, future_rows)
            events.append({
                "date": event_date.isoformat(),
                "signal_time": signal_time.strftime("%H:%M"),
                "relative_strike": current["relative_strike"],
                "option_type": option_type,
                "strike": _number(strike),
                "premium": features["premium"],
                "score": score,
                "evidence": evidence,
                "max_multiple": round(future_high / features["premium"], 2),
                **trade_outcome,
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


def historical_jump_report(underlying, days=45, use_cache=True, session_date=None):
    underlying = underlying.upper()
    if session_date:
        use_cache = False
    cache_key = f"jump-report:{underlying}:{days}:v2"
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
    events = _historical_events(underlying, days=days, session_date=session_date)
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
    entered = [event for event in scored if event["trade_outcome"] != "NO_ENTRY"]
    target_hits = [event for event in entered if event["trade_outcome"].startswith("TARGET")]
    stopped = [event for event in entered if event["trade_outcome"] == "STOP"]
    latest_date = max((event["date"] for event in events), default=None)
    report = {
        "patterns": patterns,
        "segments": _segment_report(events)[:8],
        "event_count": len(events),
        "baseline_hit_rate_3x": round((all_hits / len(events)) * 100, 1) if events else 0,
        "scored_count": len(scored),
        "scored_hit_rate_3x": round((scored_hits / len(scored)) * 100, 1) if scored else 0,
        "scored_lift": round(((scored_hits / len(scored)) - (all_hits / len(events))) * 100, 1) if scored and events else 0,
        "trade_plan": {
            "signals": len(scored), "entries": len(entered), "targets": len(target_hits), "stops": len(stopped),
            "target_before_stop_rate": round((len(target_hits) / len(entered)) * 100, 1) if entered else 0,
            "average_return_percent": round(sum(event["return_percent"] for event in entered) / len(entered), 1) if entered else 0,
            "average_realized_r": round(sum(event["realized_r"] for event in entered) / len(entered), 2) if entered else 0,
        },
        "opening_breakout": _opening_breakout_report(underlying, days=days, session_date=session_date),
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
    cache.set(f"jump-report:{underlying.upper()}:{days}:v2", report, 3600)
    return report


def jump_detector_state(underlying):
    latest = IndexOISnapshot.objects.filter(underlying=underlying).first()
    now = timezone.localtime()
    trading_day = now.weekday() < 5
    if underlying == "NIFTY":
        in_window = (
            time(9, 30) <= now.time() <= time(10, 1)
            or time(11, 30) <= now.time() <= time(13, 1)
        )
        window_label = "OUTSIDE 09:30–10:00 / 11:30–13:00"
    else:
        in_window = time(9, 0) <= now.time() <= time(15, 40)
        window_label = "OUTSIDE 09:00–15:40"
    fresh = bool(latest and timezone.localtime(latest.created_at) >= now - timedelta(minutes=3))
    active = trading_day and in_window and fresh
    if active:
        label = "ACTIVE MARKET HOURS"
    elif not trading_day:
        label = "MARKET CLOSED"
    elif not in_window:
        label = window_label
    else:
        label = "WAITING FOR FRESH SNAPSHOT"
    return {"active": active, "label": label}


def _minute_spot_rows(snapshots, completed_before):
    minute_rows = {}
    for snapshot in sorted(snapshots, key=lambda row: row.created_at):
        local_timestamp = timezone.localtime(snapshot.created_at).replace(second=0, microsecond=0)
        if local_timestamp + timedelta(minutes=1) <= completed_before and snapshot.underlying_price:
            minute_rows[local_timestamp] = _number(snapshot.underlying_price)
    return minute_rows


def _strategy_now():
    return timezone.localtime()


def _contract_minute_rows(snapshots, strike, option_type):
    minute_rows = defaultdict(list)
    rows = IndexOptionStrikeSnapshot.objects.filter(
        snapshot__in=snapshots,
        strike=strike,
        option_type=option_type,
    ).select_related("snapshot").order_by("snapshot__created_at")
    for row in rows:
        timestamp = timezone.localtime(row.snapshot.created_at)
        minute_rows[timestamp.replace(second=0, microsecond=0)].append({
            "timestamp": timestamp,
            "price": _number(row.last_price),
            "volume": row.volume,
        })
    return minute_rows


def live_nifty_put_candidate():
    config = nifty_put_strategy_config()
    now = _strategy_now()
    today = now.date()
    session_start, session_end = _session_bounds(today)
    snapshots = list(IndexOISnapshot.objects.filter(
        underlying="NIFTY",
        created_at__gte=session_start,
        created_at__lt=session_end,
    ).order_by("created_at"))
    if not snapshots:
        return None

    latest = snapshots[-1]
    latest_at = timezone.localtime(latest.created_at)
    if latest_at < now - timedelta(minutes=3):
        return None
    spot_rows = _minute_spot_rows(snapshots, now)
    setups = spot_setup_timestamps(spot_rows, config)
    setup_times = sorted(
        timestamp for timestamp, option_types in setups.items()
        if "PUT" in option_types
    )[:config.max_trades_per_day]
    if not setup_times:
        return None

    setup_at = setup_times[-1]
    setup_available_at = setup_at + timedelta(minutes=1)
    if setup_available_at > now or now - setup_available_at > timedelta(minutes=1):
        return None
    setup_snapshot = max(
        (
            snapshot for snapshot in snapshots
            if timezone.localtime(snapshot.created_at).replace(second=0, microsecond=0) == setup_at
        ),
        key=lambda snapshot: snapshot.created_at,
        default=None,
    )
    if not setup_snapshot or setup_snapshot.atm_strike is None:
        return None
    if latest.atm_strike is None or abs(latest.atm_strike - setup_snapshot.atm_strike) > Decimal("50"):
        return None

    row = latest.strikes.filter(
        option_type="PE",
        strike=setup_snapshot.atm_strike,
    ).first()
    if not row:
        return None

    prior_spot = spot_rows.get(setup_at - timedelta(minutes=config.spot_trend_minutes), 0)
    setup_spot = spot_rows.get(setup_at, 0)
    spot_move_percent = ((prior_spot - setup_spot) / prior_spot * 100) if prior_spot else 0
    contract_minutes = _contract_minute_rows(
        snapshots, setup_snapshot.atm_strike, "PE",
    )
    setup_contract_rows = contract_minutes.get(setup_at, [])
    previous_contract_rows = contract_minutes.get(setup_at - timedelta(minutes=1), [])
    setup_price = setup_contract_rows[-1]["price"] if setup_contract_rows else 0
    opening_price = (
        setup_contract_rows[0]["price"]
        if len(setup_contract_rows) > 1
        else previous_contract_rows[-1]["price"] if previous_contract_rows else 0
    )
    minute_closes = {
        timestamp: values[-1]
        for timestamp, values in contract_minutes.items()
        if values
    }
    minute_volumes = {}
    for timestamp, values in minute_closes.items():
        previous = minute_closes.get(timestamp - timedelta(minutes=1))
        if previous:
            minute_volumes[timestamp] = max(values["volume"] - previous["volume"], 0)
    prior_volumes = [
        minute_volumes.get(setup_at - timedelta(minutes=offset), 0)
        for offset in range(1, config.lookback + 1)
    ]
    positive_prior_volumes = [volume for volume in prior_volumes if volume > 0]
    baseline_volume = median(positive_prior_volumes) if positive_prior_volumes else 0
    setup_volume = minute_volumes.get(setup_at, 0)
    volume_ratio = setup_volume / baseline_volume if baseline_volume else 0

    bid, ask, premium = _number(row.top_bid_price), _number(row.top_ask_price), _number(row.last_price)
    spread_percent = ((ask - bid) / ask * 100) if ask > 0 and bid > 0 and ask >= bid else 100
    total_depth = row.buy_quantity + row.sell_quantity
    depth_imbalance = ((row.buy_quantity - row.sell_quantity) / total_depth) if total_depth else 0
    rejection_reasons = []
    if spot_move_percent < config.minimum_spot_move_percent:
        rejection_reasons.append("five-minute spot decline below 0.10%")
    if not opening_price or setup_price <= opening_price:
        rejection_reasons.append("ATM PE premium did not rise on the setup minute")
    if volume_ratio < config.volume_ratio:
        rejection_reasons.append("minute volume below recent median")
    if not (config.premium_min <= premium <= config.premium_max):
        rejection_reasons.append("premium outside ₹50–₹250")
    if bid <= 0 or ask <= 0:
        rejection_reasons.append("missing two-sided quote")
    elif spread_percent > 4:
        rejection_reasons.append("spread above 4%")
    if row.top_bid_quantity <= 0 or row.top_ask_quantity <= 0:
        rejection_reasons.append("insufficient top-level depth")
    if total_depth and depth_imbalance < -0.35:
        rejection_reasons.append("sell depth dominates")
    if not (0.15 <= abs(row.delta) <= 0.70):
        rejection_reasons.append("delta outside 0.15–0.70")

    entry = round(max(ask, premium) * 1.005, 2)
    stop_loss = round(entry * (1 - config.stop_percent), 2)
    risk = entry - stop_loss
    target = round(entry + risk * config.reward_risk, 2)
    return {
        "strike": row.strike,
        "option_type": "PE",
        "relative_strike": "ATM",
        "premium": row.last_price,
        "score": 80 if not rejection_reasons else 45,
        "evidence": [
            "NIFTY broke the 09:15–09:29 low",
            "completed 5-minute candle is bearish",
            "five-minute spot momentum is at least 0.10%",
            "ATM PE price and minute volume confirm",
        ],
        "oi_change": row.oi_change,
        "iv": row.implied_volatility,
        "volume": row.volume,
        "bid": row.top_bid_price,
        "ask": row.top_ask_price,
        "spread_percent": round(spread_percent, 1),
        "delta": row.delta,
        "gamma": row.gamma,
        "theta": row.theta,
        "vega": row.vega,
        "depth_imbalance": round(depth_imbalance * 100, 1),
        "trade_ready": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        "entry": entry,
        "stop_loss": stop_loss,
        "target_1": target,
        "target_2": target,
        "risk_percent": round(config.stop_percent * 100, 1),
        "entry_rule": "Paper signal: enter only near the ask after the completed setup minute.",
        "exit_time": "15:20",
        "setup_at": setup_at.strftime("%H:%M"),
        "setup_number": setup_times.index(setup_at) + 1,
        "spot_move_percent": round(spot_move_percent, 2),
        "volume_ratio": round(volume_ratio, 2),
        "paper_only": True,
    }


def live_jump_candidates(underlying, limit=8):
    if underlying == "NIFTY":
        candidate = live_nifty_put_candidate()
        return [candidate] if candidate else []
    latest = IndexOISnapshot.objects.filter(underlying=underlying).prefetch_related("strikes").first()
    if not latest or latest.atm_strike is None:
        return []
    session_start, session_end = _session_bounds(timezone.localdate(latest.created_at))
    recent_snapshots = list(IndexOISnapshot.objects.filter(
        underlying=underlying, expiry_date=latest.expiry_date, created_at__lt=latest.created_at,
        created_at__gte=session_start,
    ).prefetch_related("strikes")[:5])
    previous = recent_snapshots[0] if recent_snapshots else None
    previous_rows = {(row.strike, row.option_type): row for row in previous.strikes.all()} if previous else {}
    recent_prices = defaultdict(list)
    recent_volumes = defaultdict(list)
    for snapshot in recent_snapshots:
        for strike_row in snapshot.strikes.all():
            recent_prices[(strike_row.strike, strike_row.option_type)].append(strike_row.last_price)
            recent_volumes[(strike_row.strike, strike_row.option_type)].append(strike_row.volume)
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

    candidates = []
    for row, features, volume_delta in candidate_features:
        contract_volumes = recent_volumes[(row.strike, row.option_type)]
        prior_volume_delta = max(contract_volumes[0] - contract_volumes[1], 0) if len(contract_volumes) >= 2 else 0
        features["volume_surge"] = (volume_delta / prior_volume_delta) if prior_volume_delta else 0
        score, evidence = _feature_score(features, underlying)
        relative_steps = int((row.strike - latest.atm_strike) / strike_step)
        bid, ask = _number(row.top_bid_price), _number(row.top_ask_price)
        spread_percent = ((ask - bid) / ask * 100) if ask > 0 and bid > 0 and ask >= bid else 100
        history = [_number(price) for price in recent_prices[(row.strike, row.option_type)] if _number(price) > 0]
        reference = max([ask, _number(row.last_price), *history])
        support = min([_number(row.last_price), *history])
        levels = _trade_levels(reference, support)
        total_depth = row.buy_quantity + row.sell_quantity
        depth_imbalance = ((row.buy_quantity - row.sell_quantity) / total_depth) if total_depth else 0
        delta_ok = 0.15 <= abs(row.delta) <= 0.70
        rejection_reasons = []
        if bid <= 0 or ask <= 0:
            rejection_reasons.append("missing two-sided quote")
        elif spread_percent > 4:
            rejection_reasons.append("spread above 4%")
        if row.top_bid_quantity <= 0 or row.top_ask_quantity <= 0:
            rejection_reasons.append("insufficient top-level depth")
        if total_depth and depth_imbalance < -0.35:
            rejection_reasons.append("sell depth dominates")
        if not delta_ok:
            rejection_reasons.append("delta outside 0.15–0.70")
        if features["favorable_spot_move"] <= 0:
            rejection_reasons.append("spot direction unconfirmed")
        if features["volume_surge"] < 1.5:
            rejection_reasons.append("volume confirmation missing")
        signal_clock = timezone.localtime(latest.created_at).time()
        breakout_confirmed = bool(history and _number(row.last_price) > max(history) * 1.002)
        if not (OPENING_START <= signal_clock <= OPENING_END):
            rejection_reasons.append("outside validated 09:25–10:00 window")
        if not breakout_confirmed:
            rejection_reasons.append("five-snapshot high not broken")
        trade_ready = not rejection_reasons and len(history) >= 2
        if spread_percent <= 2:
            score = min(score + 5, 100)
            evidence.append("tight spread")
        if depth_imbalance >= 0.20:
            score = min(score + 5, 100)
            evidence.append("buy depth leads")
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
            "bid": row.top_bid_price,
            "ask": row.top_ask_price,
            "spread_percent": round(spread_percent, 1),
            "delta": row.delta,
            "gamma": row.gamma,
            "theta": row.theta,
            "vega": row.vega,
            "depth_imbalance": round(depth_imbalance * 100, 1),
            "trade_ready": trade_ready,
            "rejection_reasons": rejection_reasons,
            "entry_rule": "Buy only at or above trigger; use a limit order near the ask.",
            "exit_time": "15:20",
            **levels,
        })
    ranked = sorted(candidates, key=lambda candidate: (candidate["trade_ready"], candidate["score"]), reverse=True)
    selected = []
    for option_type in ("CE", "PE"):
        best = next((candidate for candidate in ranked if candidate["option_type"] == option_type), None)
        if best:
            selected.append(best)
    return sorted(selected, key=lambda candidate: (candidate["trade_ready"], candidate["score"]), reverse=True)[:limit]