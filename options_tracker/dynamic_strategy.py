from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median

from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import IndexOptionCandle
from .strategy_backtest import _completed_spot_bars


NIFTY_TUESDAY_EXPIRY_START = date(2025, 9, 1)


@dataclass(frozen=True)
class TradeWindow:
    name: str
    start: time
    signal_end: time
    exit_time: time


@dataclass(frozen=True)
class DynamicStrategyConfig:
    windows: tuple[TradeWindow, ...] = (
        TradeWindow("MORNING", time(9, 30), time(10, 54), time(11, 0)),
        TradeWindow("AFTERNOON", time(13, 30), time(14, 24), time(14, 30)),
        TradeWindow("CLOSING", time(14, 30), time(15, 9), time(15, 20)),
    )
    fast_ema_period: int = 3
    slow_ema_period: int = 8
    atr_period: int = 6
    breakout_lookback: int = 3
    breakout_buffer_atr: float = 0.05
    minimum_body_fraction: float = 0.35
    minimum_range_atr: float = 0.75
    minimum_close_location: float = 0.65
    expiry_minimum_range_atr: float = 1.0
    premium_lookback: int = 5
    premium_min: float = 20
    premium_max: float = 300
    expiry_premium_min: float = 5
    expiry_premium_max: float = 150
    minimum_otm_distance: int = -1
    maximum_otm_distance: int = 1
    expiry_minimum_otm_distance: int = 0
    expiry_maximum_otm_distance: int = 2
    minimum_option_breakout_percent: float = 0.1
    maximum_option_breakout_percent: float | None = None
    expiry_option_breakout_percent: float = 0.5
    expiry_maximum_breakout_percent: float | None = None
    minimum_volume_ratio: float = 1.2
    expiry_minimum_volume_ratio: float = 1.5
    expiry_minimum_spot_move_percent: float = 0.03
    entry_slippage_percent: float = 0.5
    minimum_stop_percent: float = 8
    maximum_stop_percent: float = 18
    expiry_minimum_stop_percent: float = 12
    expiry_maximum_stop_percent: float = 25
    reward_risk: float = 2
    spot_setup_mode: str = "local_breakout"


def trade_window(clock, config):
    return next(
        (
            window
            for window in config.windows
            if window.start <= clock <= window.signal_end
        ),
        None,
    )


def nifty_expiry_sessions(session_dates):
    sessions_by_week = defaultdict(list)
    for session_date in sorted(set(session_dates)):
        sessions_by_week[session_date.isocalendar()[:2]].append(session_date)

    expiry_dates = set()
    for sessions in sessions_by_week.values():
        monday = sessions[0] - timedelta(days=sessions[0].weekday())
        expiry_weekday = 1 if monday >= NIFTY_TUESDAY_EXPIRY_START else 3
        scheduled_expiry = monday + timedelta(days=expiry_weekday)
        eligible = [session for session in sessions if session <= scheduled_expiry]
        if eligible:
            expiry_dates.add(max(eligible))
    return expiry_dates


def _ema(values, period):
    average = values[0]
    multiplier = 2 / (period + 1)
    for value in values[1:]:
        average += (value - average) * multiplier
    return average


def _true_range(bar, previous_close):
    return max(
        bar["high"] - bar["low"],
        abs(bar["high"] - previous_close),
        abs(bar["low"] - previous_close),
    )


def price_action_setup(bars, config, is_expiry_day=False):
    if config.spot_setup_mode == "window_structure":
        return window_structure_setup(bars, config, is_expiry_day)
    required_bars = max(
        config.slow_ema_period + 2,
        config.atr_period + 1,
        config.breakout_lookback + 1,
    )
    if len(bars) < required_bars:
        return None

    current = bars[-1]
    window = trade_window(current["timestamp"].time(), config)
    if not window:
        return None

    previous = bars[:-1]
    closes = [bar["close"] for bar in bars]
    fast_ema = _ema(closes, config.fast_ema_period)
    slow_ema = _ema(closes, config.slow_ema_period)
    previous_slow_ema = _ema(closes[:-1], config.slow_ema_period)
    true_ranges = [
        _true_range(bar, prior["close"])
        for prior, bar in zip(previous, bars[1:])
    ]
    atr = sum(true_ranges[-config.atr_period - 1:-1]) / config.atr_period
    current_range = current["high"] - current["low"]
    if atr <= 0 or current_range <= 0:
        return None

    body_fraction = abs(current["close"] - current["open"]) / current_range
    range_atr = _true_range(current, previous[-1]["close"]) / atr
    close_location = (current["close"] - current["low"]) / current_range
    prior_range = previous[-config.breakout_lookback:]
    prior_high = max(bar["high"] for bar in prior_range)
    prior_low = min(bar["low"] for bar in prior_range)
    range_threshold = (
        config.expiry_minimum_range_atr
        if is_expiry_day
        else config.minimum_range_atr
    )

    call_setup = all((
        current["close"] > prior_high + atr * config.breakout_buffer_atr,
        fast_ema > slow_ema,
        slow_ema > previous_slow_ema,
        current["close"] > current["open"],
        close_location >= config.minimum_close_location,
        body_fraction >= config.minimum_body_fraction,
        range_atr >= range_threshold,
    ))
    put_setup = all((
        current["close"] < prior_low - atr * config.breakout_buffer_atr,
        fast_ema < slow_ema,
        slow_ema < previous_slow_ema,
        current["close"] < current["open"],
        close_location <= 1 - config.minimum_close_location,
        body_fraction >= config.minimum_body_fraction,
        range_atr >= range_threshold,
    ))
    if not call_setup and not put_setup:
        return None

    option_type = "CALL" if call_setup else "PUT"
    trend_strength = abs(fast_ema - slow_ema) / atr
    breakout_distance = (
        (current["close"] - prior_high) / atr
        if call_setup
        else (prior_low - current["close"]) / atr
    )
    score = min(
        100,
        round(
            35
            + min(trend_strength, 1) * 20
            + min(breakout_distance, 1) * 20
            + min(body_fraction, 1) * 15
            + min(range_atr / 2, 1) * 10
        ),
    )
    return {
        "timestamp": current["timestamp"],
        "window": window.name,
        "option_type": option_type,
        "score": score,
        "atr": atr,
        "range_atr": range_atr,
        "body_fraction": body_fraction,
        "trend_strength": trend_strength,
        "breakout_distance_atr": breakout_distance,
    }


def window_structure_setup(bars, config, is_expiry_day=False):
    if len(bars) < 4:
        return None
    current = bars[-1]
    window = trade_window(current["timestamp"].time(), config)
    if not window:
        return None

    reference_periods = {
        "MORNING": (time(9, 19), time(9, 29)),
        "AFTERNOON": (time(12, 34), time(13, 29)),
        "CLOSING": (time(13, 34), time(14, 29)),
    }
    reference_start, reference_end = reference_periods[window.name]
    reference = [
        bar
        for bar in bars[:-1]
        if reference_start <= bar["timestamp"].time() <= reference_end
    ]
    if len(reference) < 3:
        return None

    previous = bars[:-1]
    true_ranges = [
        _true_range(bar, prior["close"])
        for prior, bar in zip(previous, bars[1:])
    ]
    atr_values = true_ranges[-min(config.atr_period, len(true_ranges)) - 1:-1]
    if not atr_values:
        return None
    atr = sum(atr_values) / len(atr_values)
    current_range = current["high"] - current["low"]
    if atr <= 0 or current_range <= 0:
        return None

    closes = [bar["close"] for bar in bars]
    fast_ema = _ema(closes, config.fast_ema_period)
    slow_ema = _ema(closes, config.slow_ema_period)
    previous_fast_ema = _ema(closes[:-1], config.fast_ema_period)
    previous_slow_ema = _ema(closes[:-1], config.slow_ema_period)
    body_fraction = abs(current["close"] - current["open"]) / current_range
    range_atr = _true_range(current, previous[-1]["close"]) / atr
    close_location = (current["close"] - current["low"]) / current_range
    reference_high = max(bar["high"] for bar in reference)
    reference_low = min(bar["low"] for bar in reference)
    buffer = atr * config.breakout_buffer_atr
    range_threshold = (
        config.expiry_minimum_range_atr
        if is_expiry_day
        else config.minimum_range_atr
    )
    quality = (
        body_fraction >= config.minimum_body_fraction
        and range_atr >= range_threshold
    )
    call_breakout = all((
        quality,
        previous[-1]["close"] <= reference_high + buffer,
        current["close"] > reference_high + buffer,
        current["close"] > current["open"],
        close_location >= config.minimum_close_location,
        fast_ema > slow_ema,
        slow_ema > previous_slow_ema,
    ))
    put_breakout = all((
        quality,
        previous[-1]["close"] >= reference_low - buffer,
        current["close"] < reference_low - buffer,
        current["close"] < current["open"],
        close_location <= 1 - config.minimum_close_location,
        fast_ema < slow_ema,
        slow_ema < previous_slow_ema,
    ))
    call_reversal = all((
        quality,
        current["low"] < reference_low - buffer,
        current["close"] > reference_low,
        current["close"] > current["open"],
        close_location >= config.minimum_close_location,
        fast_ema > previous_fast_ema,
    ))
    put_reversal = all((
        quality,
        current["high"] > reference_high + buffer,
        current["close"] < reference_high,
        current["close"] < current["open"],
        close_location <= 1 - config.minimum_close_location,
        fast_ema < previous_fast_ema,
    ))
    signals = (
        (call_breakout, "CALL", "BREAKOUT", reference_high),
        (put_breakout, "PUT", "BREAKOUT", reference_low),
        (call_reversal, "CALL", "FAILED_BREAK", reference_low),
        (put_reversal, "PUT", "FAILED_BREAK", reference_high),
    )
    matched = next((signal for signal in signals if signal[0]), None)
    if not matched:
        return None

    _, option_type, setup_type, boundary = matched
    trend_strength = abs(fast_ema - slow_ema) / atr
    boundary_distance = abs(current["close"] - boundary) / atr
    score = min(
        100,
        round(
            40
            + min(trend_strength, 1) * 15
            + min(boundary_distance, 1) * 20
            + min(body_fraction, 1) * 15
            + min(range_atr / 2, 1) * 10
        ),
    )
    return {
        "timestamp": current["timestamp"],
        "window": window.name,
        "option_type": option_type,
        "setup_type": setup_type,
        "score": score,
        "atr": atr,
        "range_atr": range_atr,
        "body_fraction": body_fraction,
        "trend_strength": trend_strength,
        "breakout_distance_atr": boundary_distance,
    }


def _number(value):
    return float(value or 0)


def _relative_index(relative_strike):
    if relative_strike == "ATM":
        return 0
    try:
        return int(relative_strike[3:])
    except (TypeError, ValueError):
        return 99


def _otm_distance(relative_strike, option_type):
    relative_index = _relative_index(relative_strike)
    return relative_index if option_type == "CALL" else -relative_index


def _continuous_rows(stream, signal_at, lookback):
    timestamps = [
        signal_at - timedelta(minutes=offset)
        for offset in range(lookback, -1, -1)
    ]
    return [stream.get(timestamp) for timestamp in timestamps]


def select_option_candidate(contracts, setup, config, is_expiry_day=False):
    signal_at = setup["timestamp"]
    closing_expiry = is_expiry_day and setup["window"] == "CLOSING"
    candidates = []
    for (strike, option_type), stream in contracts.items():
        if option_type != setup["option_type"]:
            continue
        rows = _continuous_rows(stream, signal_at, config.premium_lookback)
        if any(row is None for row in rows):
            continue
        prior, current = rows[:-1], rows[-1]
        next_row = stream.get(signal_at + timedelta(minutes=1))
        if next_row is None:
            continue

        otm_distance = _otm_distance(current["relative_strike"], option_type)
        if closing_expiry:
            if not config.expiry_minimum_otm_distance <= otm_distance <= config.expiry_maximum_otm_distance:
                continue
            premium_min, premium_max = config.expiry_premium_min, config.expiry_premium_max
            breakout_required = config.expiry_option_breakout_percent
            breakout_maximum = config.expiry_maximum_breakout_percent
            volume_required = config.expiry_minimum_volume_ratio
        else:
            if not config.minimum_otm_distance <= otm_distance <= config.maximum_otm_distance:
                continue
            premium_min, premium_max = config.premium_min, config.premium_max
            breakout_required = config.minimum_option_breakout_percent
            breakout_maximum = config.maximum_option_breakout_percent
            volume_required = config.minimum_volume_ratio

        premium = _number(current["close"])
        prior_high = max(_number(row["high"]) for row in prior)
        prior_close = _number(prior[-1]["close"])
        positive_volumes = [_number(row["volume"]) for row in prior if _number(row["volume"]) > 0]
        baseline_volume = median(positive_volumes) if positive_volumes else 0
        volume_ratio = _number(current["volume"]) / baseline_volume if baseline_volume else 0
        breakout_percent = (premium / prior_high - 1) * 100 if prior_high else 0
        if not all((
            premium_min <= premium <= premium_max,
            premium > _number(current["open"]),
            prior_close > 0,
            breakout_percent >= breakout_required,
            volume_ratio >= volume_required,
        )):
            continue
        if breakout_maximum is not None and breakout_percent >= breakout_maximum:
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
        premium_momentum = (premium / prior_close - 1) * 100
        score = (
            setup["score"]
            + min(volume_ratio, 3) * 5
            + min(max(breakout_percent, 0), 5) * 2
            + (4 if oi_change_percent > 0 else 2)
            + (4 if iv_change > 0 else 0)
        )
        preferred_distance = 1 if closing_expiry else 0
        score -= abs(otm_distance - preferred_distance) * 3
        candidates.append({
            **setup,
            "strike": strike,
            "relative_strike": current["relative_strike"],
            "signal_close": premium,
            "option_score": round(score, 1),
            "volume_ratio": round(volume_ratio, 2),
            "breakout_percent": round(breakout_percent, 2),
            "premium_momentum_percent": round(premium_momentum, 2),
            "oi_change_percent": round(oi_change_percent, 2),
            "iv_change": round(iv_change, 2),
            "otm_distance": otm_distance,
            "prior_rows": prior,
            "current_row": current,
            "next_row": next_row,
            "stream": stream,
        })
    return max(candidates, key=lambda candidate: candidate["option_score"], default=None)


def build_session_context(rows):
    contracts = defaultdict(dict)
    spot_rows = {}
    for source in rows:
        row = dict(source)
        local_timestamp = row.get("local_timestamp") or timezone.localtime(row["timestamp"])
        row["local_timestamp"] = local_timestamp
        spot = _number(row.get("spot"))
        if spot:
            spot_rows[local_timestamp] = spot
        strike = row.get("strike")
        if strike is not None:
            contracts[(_number(strike), row["option_type"])][local_timestamp] = row

    bars = _completed_spot_bars(spot_rows, 5) if spot_rows else []
    return contracts, bars


def context_candidates(contracts, bars, config, is_expiry_day=False):
    candidates = []
    used_windows = set()
    for index in range(len(bars)):
        setup = price_action_setup(bars[:index + 1], config, is_expiry_day)
        if not setup or setup["window"] in used_windows:
            continue
        candidate = select_option_candidate(contracts, setup, config, is_expiry_day)
        if not candidate:
            continue
        entry = max(_number(candidate["next_row"]["open"]), candidate["signal_close"])
        entry *= 1 + config.entry_slippage_percent / 100
        if _number(candidate["next_row"]["high"]) < entry:
            continue
        candidates.append(candidate)
        used_windows.add(setup["window"])
    return candidates


def session_candidates(rows, config, is_expiry_day=False):
    contracts, bars = build_session_context(rows)
    return context_candidates(contracts, bars, config, is_expiry_day)


def simulate_trade(candidate, config, is_expiry_day=False):
    current = candidate["current_row"]
    next_row = candidate["next_row"]
    entry = max(_number(next_row["open"]), candidate["signal_close"])
    entry *= 1 + config.entry_slippage_percent / 100
    if _number(next_row["high"]) < entry:
        return None

    closing_expiry = is_expiry_day and candidate["window"] == "CLOSING"
    minimum_stop = (
        config.expiry_minimum_stop_percent
        if closing_expiry
        else config.minimum_stop_percent
    ) / 100
    maximum_stop = (
        config.expiry_maximum_stop_percent
        if closing_expiry
        else config.maximum_stop_percent
    ) / 100
    structure_low = min(
        _number(row["low"])
        for row in [*candidate["prior_rows"][-2:], current]
        if _number(row["low"]) > 0
    )
    structure_risk = max((entry - structure_low * 0.995) / entry, 0)
    risk_percent = min(max(structure_risk, minimum_stop), maximum_stop)
    stop = entry * (1 - risk_percent)
    risk = entry - stop
    target = entry + risk * config.reward_risk
    window = next(window for window in config.windows if window.name == candidate["window"])
    future_rows = [
        row
        for timestamp, row in sorted(candidate["stream"].items())
        if next_row["local_timestamp"] <= timestamp
        and timestamp.time() <= window.exit_time
    ]
    if not future_rows:
        return None

    outcome = "TIME_EXIT"
    exit_price = _number(future_rows[-1]["close"]) * 0.995
    exit_at = future_rows[-1]["local_timestamp"]
    for row in future_rows:
        if _number(row["low"]) <= stop:
            outcome, exit_price, exit_at = "STOP", stop * 0.995, row["local_timestamp"]
            break
        if _number(row["high"]) >= target:
            outcome, exit_price, exit_at = "TARGET", target * 0.995, row["local_timestamp"]
            break

    window_high = max(_number(row["high"]) for row in future_rows)
    window_low = min(_number(row["low"]) for row in future_rows)
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
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "risk_percent": round(risk_percent * 100, 1),
        "outcome": outcome,
        "realized_r": round((exit_price - entry) / risk, 2),
        "window_mfe_r": round((window_high - entry) / risk, 2),
        "window_mae_r": round((entry - window_low) / risk, 2),
        "window_max_multiple": round(window_high / entry, 2),
        "is_expiry_day": is_expiry_day,
    }


def backtest_session(rows, session_date, config, is_expiry_day=False):
    trades = []
    for candidate in session_candidates(rows, config, is_expiry_day):
        trade = simulate_trade(candidate, config, is_expiry_day)
        if trade:
            trades.append(trade)
    return trades


def available_session_dates(underlying="NIFTY", expiry_code=1):
    return list(IndexOptionCandle.objects.filter(
        underlying=underlying,
        expiry_code=expiry_code,
        interval_minutes=1,
    ).annotate(
        session_date=TruncDate("timestamp"),
    ).values_list(
        "session_date", flat=True,
    ).order_by("session_date").distinct())


def backtest_dynamic_strategy(underlying="NIFTY", expiry_code=1, config=None):
    config = config or DynamicStrategyConfig()
    session_dates = available_session_dates(underlying, expiry_code)
    expiry_dates = nifty_expiry_sessions(session_dates) if underlying == "NIFTY" else set()
    query = IndexOptionCandle.objects.filter(
        underlying=underlying,
        expiry_code=expiry_code,
        interval_minutes=1,
    ).values(
        "timestamp", "strike", "relative_strike", "option_type", "open", "high",
        "low", "close", "volume", "oi", "implied_volatility", "spot",
    ).order_by("timestamp")

    trades = []
    completed_sessions = []
    current_date = None
    session_rows = []
    for row in query.iterator(chunk_size=20000):
        local_timestamp = timezone.localtime(row["timestamp"])
        session_date = local_timestamp.date()
        if current_date is not None and session_date != current_date:
            if session_rows[-1]["local_timestamp"].time() >= time(15, 20):
                trades.extend(backtest_session(
                    session_rows, current_date, config, current_date in expiry_dates,
                ))
                completed_sessions.append(current_date)
            session_rows = []
        row["local_timestamp"] = local_timestamp
        session_rows.append(row)
        current_date = session_date
    if session_rows and session_rows[-1]["local_timestamp"].time() >= time(15, 20):
        trades.extend(backtest_session(
            session_rows, current_date, config, current_date in expiry_dates,
        ))
        completed_sessions.append(current_date)
    return trades, completed_sessions


def dynamic_trade_metrics(trades, session_dates=()):
    ordered = sorted(trades, key=lambda trade: trade["entry_at"])
    wins = [trade for trade in ordered if trade["realized_r"] > 0]
    gross_profit = sum(max(trade["realized_r"], 0) for trade in ordered)
    gross_loss = abs(sum(min(trade["realized_r"], 0) for trade in ordered))
    equity = peak = maximum_drawdown = 0
    for trade in ordered:
        equity += trade["realized_r"]
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    sessions = len(set(session_dates))
    dates_with_three = len({
        trade_date
        for trade_date in {trade["date"] for trade in ordered}
        if sum(trade["date"] == trade_date for trade in ordered) == 3
    })
    return {
        "sessions": sessions,
        "trades": len(ordered),
        "trades_per_session": round(len(ordered) / sessions, 2) if sessions else 0,
        "three_trade_days": dates_with_three,
        "calls": sum(trade["option_type"] == "CALL" for trade in ordered),
        "puts": sum(trade["option_type"] == "PUT" for trade in ordered),
        "targets": sum(trade["outcome"] == "TARGET" for trade in ordered),
        "stops": sum(trade["outcome"] == "STOP" for trade in ordered),
        "time_exits": sum(trade["outcome"] == "TIME_EXIT" for trade in ordered),
        "win_rate": round(len(wins) / len(ordered) * 100, 1) if ordered else 0,
        "total_r": round(sum(trade["realized_r"] for trade in ordered), 2),
        "average_r": round(sum(trade["realized_r"] for trade in ordered) / len(ordered), 2) if ordered else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "maximum_drawdown_r": round(maximum_drawdown, 2),
        "expiry_trades": sum(trade["is_expiry_day"] for trade in ordered),
        "window_2x": sum(trade["window_max_multiple"] >= 2 for trade in ordered),
        "window_3x": sum(trade["window_max_multiple"] >= 3 for trade in ordered),
        "window_5x": sum(trade["window_max_multiple"] >= 5 for trade in ordered),
    }