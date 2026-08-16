from dataclasses import dataclass, replace
from datetime import time, timedelta
from statistics import median

from django.utils import timezone

from .dynamic_strategy import (
    DynamicStrategyConfig,
    _continuous_rows,
    _number,
    _otm_distance,
    available_session_dates,
    build_session_context,
    context_candidates,
    nifty_expiry_sessions,
)
from .models import IndexOptionCandle


NORMAL_ACCOUNT_RISK_PERCENT = 0.50
EXPIRY_ACCOUNT_RISK_PERCENT = 0.20


@dataclass(frozen=True)
class ExitScenario:
    name: str
    stop_percent: float
    target_mode: str
    target_value: float
    account_risk_percent: float
    partial_at_multiple: float | None = None
    partial_fraction: float = 0
    breakeven_after_partial: bool = False


def normal_strategy_config():
    return replace(
        DynamicStrategyConfig(),
        maximum_option_breakout_percent=1,
        spot_setup_mode="window_structure",
    )


def expiry_strategy_config():
    return replace(
        DynamicStrategyConfig(),
        maximum_option_breakout_percent=1,
        expiry_premium_min=2,
        expiry_premium_max=50,
        expiry_minimum_otm_distance=0,
        expiry_maximum_otm_distance=3,
        spot_setup_mode="window_structure",
    )


def expiry_closing_variants():
    base = expiry_strategy_config()
    premium_bands = (
        ("P2_10", 2, 10),
        ("P5_20", 5, 20),
        ("P10_25", 10, 25),
        ("P20_50", 20, 50),
    )
    distance_bands = (
        ("ATM_OTM2", 0, 2),
        ("OTM1_3", 1, 3),
        ("OTM2_5", 2, 5),
    )
    return {
        f"{premium_name}_{distance_name}": replace(
            base,
            expiry_premium_min=premium_min,
            expiry_premium_max=premium_max,
            expiry_minimum_otm_distance=distance_min,
            expiry_maximum_otm_distance=distance_max,
        )
        for premium_name, premium_min, premium_max in premium_bands
        for distance_name, distance_min, distance_max in distance_bands
    }


def normal_exit_scenarios():
    return tuple(
        ExitScenario(
            name=f"SL{stop:g}_T{reward:g}R",
            stop_percent=stop,
            target_mode="R",
            target_value=reward,
            account_risk_percent=NORMAL_ACCOUNT_RISK_PERCENT,
        )
        for stop in (8, 10, 12, 15)
        for reward in (1.5, 2, 2.5, 3)
    )


def expiry_early_exit_scenarios():
    return tuple(
        ExitScenario(
            name=f"SL{stop:g}_T{reward:g}R",
            stop_percent=stop,
            target_mode="R",
            target_value=reward,
            account_risk_percent=EXPIRY_ACCOUNT_RISK_PERCENT,
        )
        for stop in (10, 15, 20)
        for reward in (1.5, 2, 3)
    )


def expiry_hero_exit_scenarios():
    fixed = [
        ExitScenario(
            name=f"SL{stop:g}_T{target:g}X",
            stop_percent=stop,
            target_mode="MULTIPLE",
            target_value=target,
            account_risk_percent=EXPIRY_ACCOUNT_RISK_PERCENT,
        )
        for stop in (25, 40, 50, 75, 100)
        for target in (2, 3, 5)
    ]
    runners = [
        ExitScenario(
            name=f"SL{stop:g}_HALF2X_RUN5X",
            stop_percent=stop,
            target_mode="MULTIPLE",
            target_value=5,
            account_risk_percent=EXPIRY_ACCOUNT_RISK_PERCENT,
            partial_at_multiple=2,
            partial_fraction=0.5,
            breakeven_after_partial=True,
        )
        for stop in (50, 75, 100)
    ]
    return tuple([*fixed, *runners])


def expiry_option_led_candidate(contracts, bars, config):
    candidates_by_time = {}
    for (strike, option_type), stream in contracts.items():
        for signal_at, current in sorted(stream.items()):
            if not time(14, 30) <= signal_at.time() <= time(15, 9):
                continue
            rows = _continuous_rows(stream, signal_at, config.premium_lookback)
            if any(row is None for row in rows):
                continue
            prior, current = rows[:-1], rows[-1]
            next_row = stream.get(signal_at + timedelta(minutes=1))
            if not next_row:
                continue

            otm_distance = _otm_distance(current["relative_strike"], option_type)
            if not config.expiry_minimum_otm_distance <= otm_distance <= config.expiry_maximum_otm_distance:
                continue
            premium = _number(current["close"])
            if not config.expiry_premium_min <= premium <= config.expiry_premium_max:
                continue

            prior_high = max(_number(row["high"]) for row in prior)
            prior_close = _number(prior[-1]["close"])
            positive_volumes = [
                _number(row["volume"])
                for row in prior
                if _number(row["volume"]) > 0
            ]
            baseline_volume = median(positive_volumes) if positive_volumes else 0
            volume_ratio = _number(current["volume"]) / baseline_volume if baseline_volume else 0
            breakout_percent = (premium / prior_high - 1) * 100 if prior_high else 0
            spot = _number(current.get("spot"))
            prior_spot_row = stream.get(signal_at - timedelta(minutes=5))
            prior_spot = _number(prior_spot_row.get("spot")) if prior_spot_row else 0
            spot_move_percent = abs(spot / prior_spot - 1) * 100 if prior_spot else 0
            spot_aligned = spot > prior_spot if option_type == "CALL" else spot < prior_spot
            context_bar = next(
                (bar for bar in reversed(bars) if bar["timestamp"] <= signal_at),
                None,
            )
            context_aligned = bool(context_bar and (
                context_bar["close"] > context_bar["open"]
                if option_type == "CALL"
                else context_bar["close"] < context_bar["open"]
            ))
            if not all((
                prior_close > 0,
                premium > _number(current["open"]),
                breakout_percent >= config.expiry_option_breakout_percent,
                volume_ratio >= config.expiry_minimum_volume_ratio,
                spot_move_percent >= config.expiry_minimum_spot_move_percent,
                spot_aligned,
                context_aligned,
            )):
                continue

            entry = max(_number(next_row["open"]), premium)
            entry *= 1 + config.entry_slippage_percent / 100
            if _number(next_row["high"]) < entry:
                continue
            prior_oi_values = [_number(row["oi"]) for row in prior if _number(row["oi"]) > 0]
            prior_iv_values = [
                _number(row["implied_volatility"])
                for row in prior
                if _number(row["implied_volatility"]) > 0
            ]
            prior_oi = median(prior_oi_values) if prior_oi_values else 0
            prior_iv = median(prior_iv_values) if prior_iv_values else 0
            oi_change_percent = (
                (_number(current["oi"]) / prior_oi - 1) * 100
                if prior_oi and _number(current["oi"])
                else 0
            )
            iv_change = (
                _number(current["implied_volatility"]) - prior_iv
                if prior_iv and _number(current["implied_volatility"])
                else 0
            )
            score = (
                50
                + min(volume_ratio, 4) * 6
                + min(breakout_percent, 20) * 0.8
                + min(spot_move_percent / 0.1, 3) * 4
                + (3 if oi_change_percent > 0 else 0)
                + (3 if iv_change > 0 else 0)
                - abs(otm_distance - 1) * 2
            )
            candidate = {
                "timestamp": signal_at,
                "window": "CLOSING",
                "option_type": option_type,
                "setup_type": "EXPIRY_OPTION_ACCELERATION",
                "score": round(score),
                "strike": strike,
                "relative_strike": current["relative_strike"],
                "signal_close": premium,
                "option_score": round(score, 1),
                "volume_ratio": round(volume_ratio, 2),
                "breakout_percent": round(breakout_percent, 2),
                "premium_momentum_percent": round((premium / prior_close - 1) * 100, 2),
                "spot_move_percent": round(spot_move_percent, 2),
                "oi_change_percent": round(oi_change_percent, 2),
                "iv_change": round(iv_change, 2),
                "otm_distance": otm_distance,
                "prior_rows": prior,
                "current_row": current,
                "next_row": next_row,
                "stream": stream,
            }
            candidates_by_time.setdefault(signal_at, []).append(candidate)
    if not candidates_by_time:
        return None
    first_signal_at = min(candidates_by_time)
    return max(
        candidates_by_time[first_signal_at],
        key=lambda candidate: candidate["option_score"],
    )


def expiry_session_opportunity(contracts, config):
    best = None
    for (strike, option_type), stream in contracts.items():
        for signal_at, current in sorted(stream.items()):
            if not time(14, 30) <= signal_at.time() <= time(15, 9):
                continue
            otm_distance = _otm_distance(current["relative_strike"], option_type)
            premium = _number(current["close"])
            if not (0 <= otm_distance <= 5 and 2 <= premium <= 50):
                continue
            next_row = stream.get(signal_at + timedelta(minutes=1))
            if not next_row:
                continue
            entry = max(_number(next_row["open"]), premium)
            entry *= 1 + config.entry_slippage_percent / 100
            if _number(next_row["high"]) < entry:
                continue
            future = [
                row
                for timestamp, row in sorted(stream.items())
                if next_row["local_timestamp"] <= timestamp
                and timestamp.time() <= time(15, 20)
            ]
            if not future:
                continue
            multiple = max(_number(row["high"]) for row in future) / entry
            opportunity = {
                "date": signal_at.date().isoformat(),
                "signal_at": signal_at.isoformat(),
                "option_type": option_type,
                "strike": strike,
                "relative_strike": current["relative_strike"],
                "entry": round(entry, 2),
                "maximum_multiple": round(multiple, 2),
            }
            if best is None or opportunity["maximum_multiple"] > best["maximum_multiple"]:
                best = opportunity
    return best or {"maximum_multiple": 0}


def simulate_exit_scenario(candidate, config, scenario, is_expiry_day=False):
    next_row = candidate["next_row"]
    entry = max(_number(next_row["open"]), candidate["signal_close"])
    entry *= 1 + config.entry_slippage_percent / 100
    if _number(next_row["high"]) < entry:
        return None

    stop_fraction = scenario.stop_percent / 100
    stop = max(entry * (1 - stop_fraction), 0)
    risk = entry * stop_fraction
    target = (
        entry + risk * scenario.target_value
        if scenario.target_mode == "R"
        else entry * scenario.target_value
    )
    window = next(window for window in config.windows if window.name == candidate["window"])
    future_rows = [
        row
        for timestamp, row in sorted(candidate["stream"].items())
        if next_row["local_timestamp"] <= timestamp
        and timestamp.time() <= window.exit_time
    ]
    if not future_rows:
        return None

    remaining = 1.0
    proceeds = 0.0
    partial_hit = False
    active_stop = stop
    outcome = "TIME_EXIT"
    exit_at = future_rows[-1]["local_timestamp"]
    for row in future_rows:
        low, high = _number(row["low"]), _number(row["high"])
        if active_stop > 0 and low <= active_stop:
            proceeds += remaining * active_stop * 0.995
            outcome = "PARTIAL_STOP" if partial_hit else "STOP"
            exit_at = row["local_timestamp"]
            remaining = 0
            break

        if scenario.partial_at_multiple and not partial_hit:
            partial_target = entry * scenario.partial_at_multiple
            if high >= partial_target:
                proceeds += scenario.partial_fraction * partial_target * 0.995
                remaining -= scenario.partial_fraction
                partial_hit = True
                outcome = "PARTIAL"
                if scenario.breakeven_after_partial:
                    active_stop = entry

        if high >= target:
            proceeds += remaining * target * 0.995
            outcome = "RUNNER_TARGET" if partial_hit else "TARGET"
            exit_at = row["local_timestamp"]
            remaining = 0
            break

    if remaining:
        proceeds += remaining * _number(future_rows[-1]["close"]) * 0.995
        outcome = "PARTIAL_TIME_EXIT" if partial_hit else "TIME_EXIT"

    premium_return = proceeds / entry - 1
    account_allocation_percent = scenario.account_risk_percent / stop_fraction
    account_return_percent = account_allocation_percent * premium_return
    public_candidate = {
        key: value
        for key, value in candidate.items()
        if key not in {"prior_rows", "current_row", "next_row", "stream"}
    }
    return {
        **public_candidate,
        "date": candidate["timestamp"].date().isoformat(),
        "signal_at": candidate["timestamp"].isoformat(),
        "entry_at": next_row["local_timestamp"].isoformat(),
        "exit_at": exit_at.isoformat(),
        "scenario": scenario.name,
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "premium_stop_percent": scenario.stop_percent,
        "account_risk_percent": scenario.account_risk_percent,
        "account_allocation_percent": round(account_allocation_percent, 3),
        "premium_return_percent": round(premium_return * 100, 2),
        "account_return_percent": round(account_return_percent, 4),
        "realized_r": round((proceeds - entry) / risk, 2),
        "outcome": outcome,
        "is_expiry_day": is_expiry_day,
    }


def scenario_metrics(trades, session_dates=()):
    ordered = sorted(trades, key=lambda trade: trade["entry_at"])
    returns = [trade["account_return_percent"] for trade in ordered]
    gross_profit = sum(max(value, 0) for value in returns)
    gross_loss = abs(sum(min(value, 0) for value in returns))
    equity = peak = maximum_drawdown = 0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    sessions = len(set(session_dates))
    trade_dates = {trade["date"] for trade in ordered}
    return {
        "sessions": sessions,
        "trades": len(ordered),
        "trades_per_session": round(len(ordered) / sessions, 2) if sessions else 0,
        "three_trade_days": sum(
            sum(trade["date"] == trade_date for trade in ordered) == 3
            for trade_date in trade_dates
        ),
        "calls": sum(trade["option_type"] == "CALL" for trade in ordered),
        "puts": sum(trade["option_type"] == "PUT" for trade in ordered),
        "wins": sum(value > 0 for value in returns),
        "stops": sum(trade["outcome"] in {"STOP", "PARTIAL_STOP"} for trade in ordered),
        "win_rate": round(sum(value > 0 for value in returns) / len(returns) * 100, 1) if returns else 0,
        "total_r": round(sum(trade["realized_r"] for trade in ordered), 2),
        "average_r": round(sum(trade["realized_r"] for trade in ordered) / len(ordered), 2) if ordered else 0,
        "total_account_return_percent": round(sum(returns), 2),
        "average_account_return_percent": round(sum(returns) / len(returns), 3) if returns else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "maximum_drawdown_percent": round(maximum_drawdown, 2),
        "maximum_allocation_percent": max(
            (trade["account_allocation_percent"] for trade in ordered),
            default=0,
        ),
    }


def collect_dual_strategy_candidates(underlying="NIFTY", expiry_code=1):
    session_dates = available_session_dates(underlying, expiry_code)
    scheduled_expiry_dates = (
        nifty_expiry_sessions(session_dates)
        if underlying == "NIFTY"
        else set()
    )
    normal_config = normal_strategy_config()
    expiry_config = expiry_strategy_config()
    closing_variants = expiry_closing_variants()
    query = IndexOptionCandle.objects.filter(
        underlying=underlying,
        expiry_code=expiry_code,
        interval_minutes=1,
    ).values(
        "timestamp", "strike", "relative_strike", "option_type", "open", "high",
        "low", "close", "volume", "oi", "implied_volatility", "spot",
    ).order_by("timestamp")

    candidates = {
        "normal": [],
        "expiry_early": [],
        "expiry_closing": {name: [] for name in closing_variants},
        "expiry_opportunities": [],
    }
    completed_sessions = []
    current_date = None
    session_rows = []

    def process_session(session_date, rows):
        if not rows or rows[-1]["local_timestamp"].time() < time(15, 20):
            return
        contracts, bars = build_session_context(rows)
        is_expiry_day = session_date in scheduled_expiry_dates
        if not is_expiry_day:
            candidates["normal"].extend(
                context_candidates(contracts, bars, normal_config, False)
            )
        else:
            early = context_candidates(contracts, bars, expiry_config, True)
            candidates["expiry_early"].extend(
                candidate for candidate in early if candidate["window"] != "CLOSING"
            )
            for name, config in closing_variants.items():
                closing = expiry_option_led_candidate(contracts, bars, config)
                if closing:
                    candidates["expiry_closing"][name].append(closing)
            opportunity = expiry_session_opportunity(contracts, expiry_config)
            opportunity["date"] = session_date.isoformat()
            candidates["expiry_opportunities"].append(opportunity)
        completed_sessions.append(session_date)

    for row in query.iterator(chunk_size=20000):
        local_timestamp = timezone.localtime(row["timestamp"])
        session_date = local_timestamp.date()
        if current_date is not None and session_date != current_date:
            process_session(current_date, session_rows)
            session_rows = []
        row["local_timestamp"] = local_timestamp
        session_rows.append(row)
        current_date = session_date
    if current_date is not None:
        process_session(current_date, session_rows)
    return candidates, completed_sessions, scheduled_expiry_dates


def collect_expiry_variant_candidates(
    config,
    underlying="NIFTY",
    expiry_code=1,
):
    session_dates = available_session_dates(underlying, expiry_code)
    scheduled_expiry_dates = (
        nifty_expiry_sessions(session_dates)
        if underlying == "NIFTY"
        else set()
    )
    query = IndexOptionCandle.objects.filter(
        underlying=underlying,
        expiry_code=expiry_code,
        interval_minutes=1,
        timestamp__date__in=scheduled_expiry_dates,
    ).values(
        "timestamp", "strike", "relative_strike", "option_type", "open", "high",
        "low", "close", "volume", "oi", "implied_volatility", "spot",
    ).order_by("timestamp")

    candidates = []
    current_date = None
    session_rows = []

    def process_session(rows):
        if not rows or rows[-1]["local_timestamp"].time() < time(15, 20):
            return
        contracts, bars = build_session_context(rows)
        candidate = expiry_option_led_candidate(contracts, bars, config)
        if candidate:
            candidates.append(candidate)

    for row in query.iterator(chunk_size=20000):
        local_timestamp = timezone.localtime(row["timestamp"])
        session_date = local_timestamp.date()
        if current_date is not None and session_date != current_date:
            process_session(session_rows)
            session_rows = []
        row["local_timestamp"] = local_timestamp
        session_rows.append(row)
        current_date = session_date
    process_session(session_rows)
    return candidates