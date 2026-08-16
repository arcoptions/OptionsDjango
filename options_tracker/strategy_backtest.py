from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from statistics import median

from django.utils import timezone

from .models import IndexOptionCandle


ENTRY_START = time(9, 25)
TIME_EXIT = time(15, 20)
MARKET_OPEN = time(9, 15)
NIFTY_PUT_RESEARCH_SUMMARY = {
    "data_start": "2025-08-18",
    "data_end": "2026-08-14",
    "sessions": 246,
    "trades": 48,
    "targets": 29,
    "stops": 19,
    "win_rate": 60.4,
    "total_r": 17.25,
    "average_r": 0.36,
    "profit_factor": 1.91,
    "max_drawdown_r": 5.0,
    "second_trade_days": 2,
    "third_trade_days": 0,
}


@dataclass(frozen=True)
class StrategyConfig:
    lookback: int
    start_time: time
    end_time: time
    volume_ratio: float
    stop_percent: float
    reward_risk: float
    max_distance: int
    premium_min: float = 10
    premium_max: float = 200
    minimum_breakout_percent: float = 0.2
    minimum_spot_move_percent: float = 0
    confirmation_bars: int = 1
    require_oi_rise: bool = False
    require_iv_rise: bool = False
    signal_mode: str = "breakout"
    spot_trend_minutes: int = 5
    require_opening_range_break: bool = False
    opening_range_minutes: int = 15
    opening_range_buffer_percent: float = 0
    moneyness: str = "ANY"
    retest_tolerance_percent: float = 0.5
    spot_setup: str = "trend"
    spot_retest_tolerance_percent: float = 0.03
    spot_setup_timeout_minutes: int = 30
    spot_confirmation_bars: int = 2
    option_types: tuple[str, ...] = ("CALL", "PUT")
    max_trades_per_day: int = 1
    reentry_cooldown_minutes: int = 10
    daily_loss_limit_r: float = 2
    confirmation_interval_minutes: int = 1
    require_confirmation_candle_direction: bool = False
    context_intervals: tuple[int, ...] = ()
    entry_windows: tuple[tuple[time, time], ...] = ()
    # When set, the fixed reward_risk target is replaced by a stop that trails
    # this many R behind the running high once the trade is that far in profit.
    trail_gap_r: float | None = None


def nifty_put_strategy_config(max_trades_per_day=2):
    return StrategyConfig(
        lookback=5,
        start_time=time(9, 30),
        end_time=time(13, 0),
        volume_ratio=1,
        stop_percent=0.10,
        reward_risk=1.25,
        max_distance=0,
        premium_min=50,
        premium_max=250,
        minimum_breakout_percent=0,
        minimum_spot_move_percent=0.10,
        confirmation_bars=1,
        signal_mode="spot",
        spot_trend_minutes=5,
        opening_range_minutes=15,
        opening_range_buffer_percent=0.03,
        moneyness="ATM",
        spot_setup="opening_breakout",
        spot_confirmation_bars=1,
        option_types=("PUT",),
        max_trades_per_day=max_trades_per_day,
        reentry_cooldown_minutes=10,
        daily_loss_limit_r=2,
        confirmation_interval_minutes=1,
        context_intervals=(5,),
        entry_windows=((time(9, 30), time(10, 0)), (time(11, 30), time(13, 0))),
    )


def _number(value):
    return float(value or 0)


def load_contract_rows(underlying, expiry_code, max_distance=None):
    query = IndexOptionCandle.objects.filter(
        underlying=underlying,
        expiry_code=expiry_code,
        interval_minutes=1,
    )
    eligible_contracts = None
    if max_distance is not None:
        relative_strikes = ["ATM"]
        for distance in range(1, max_distance + 1):
            relative_strikes.extend((f"ATM-{distance}", f"ATM+{distance}"))
        eligible_contracts = set()
        for timestamp, strike, option_type in query.filter(
            relative_strike__in=relative_strikes,
        ).values_list("timestamp", "strike", "option_type").iterator(chunk_size=10000):
            local_timestamp = timezone.localtime(timestamp)
            eligible_contracts.add((local_timestamp.date(), strike, option_type))

    rows = query.values(
        "timestamp", "strike", "relative_strike", "option_type", "open", "high", "low", "close",
        "volume", "oi", "implied_volatility", "spot",
    ).order_by("timestamp")
    contracts = defaultdict(list)
    for row in rows.iterator(chunk_size=10000):
        local_timestamp = timezone.localtime(row["timestamp"])
        contract_key = (local_timestamp.date(), row["strike"], row["option_type"])
        if eligible_contracts is not None and contract_key not in eligible_contracts:
            continue
        row["local_timestamp"] = local_timestamp
        contracts[contract_key].append(row)
    return contracts


def _distance(relative_strike):
    return abs(_relative_index(relative_strike))


def _relative_index(relative_strike):
    if relative_strike == "ATM":
        return 0
    try:
        return int(relative_strike[3:])
    except (TypeError, ValueError):
        return 99


def _is_continuous(rows):
    return all(
        current["local_timestamp"] - previous["local_timestamp"] == timedelta(minutes=1)
        for previous, current in zip(rows, rows[1:])
    )


def _matches_moneyness(relative_strike, option_type, config):
    relative_index = _relative_index(relative_strike)
    if config.moneyness == "ATM":
        return relative_index == 0
    if config.moneyness == "ATM_ITM":
        return relative_index <= 0 if option_type == "CALL" else relative_index >= 0
    return True


def _entry_window_index(clock, config):
    windows = config.entry_windows or ((config.start_time, config.end_time),)
    return next(
        (index for index, (start, end) in enumerate(windows) if start <= clock <= end),
        None,
    )


def _spot_context(contracts, opening_range_minutes):
    spot_by_date = defaultdict(dict)
    for rows in contracts.values():
        for row in rows:
            spot = _number(row["spot"])
            if spot:
                spot_by_date[row["local_timestamp"].date()][row["local_timestamp"]] = spot

    opening_ranges = {}
    for session_date, spot_rows in spot_by_date.items():
        opening_end = datetime.combine(
            session_date, MARKET_OPEN, tzinfo=next(iter(spot_rows)).tzinfo,
        ) + timedelta(minutes=opening_range_minutes)
        opening_values = [
            spot for timestamp, spot in spot_rows.items()
            if MARKET_OPEN <= timestamp.time() < opening_end.time()
        ]
        if opening_values:
            opening_ranges[session_date] = (min(opening_values), max(opening_values))
    return spot_by_date, opening_ranges


def _opening_retest_setups(spot_by_date, opening_ranges, config):
    setups = defaultdict(lambda: defaultdict(set))
    breakout_buffer = config.opening_range_buffer_percent / 100
    retest_tolerance = config.spot_retest_tolerance_percent / 100
    timeout = timedelta(minutes=config.spot_setup_timeout_minutes)
    for session_date, spot_rows in spot_by_date.items():
        opening_range = opening_ranges.get(session_date)
        if not opening_range:
            continue
        opening_low, opening_high = opening_range
        states = {"CALL": None, "PUT": None}
        for timestamp, spot in sorted(spot_rows.items()):
            if timestamp.time() < config.start_time:
                continue
            for option_type in ("CALL", "PUT"):
                boundary = opening_high if option_type == "CALL" else opening_low
                breakout = (
                    boundary * (1 + breakout_buffer)
                    if option_type == "CALL"
                    else boundary * (1 - breakout_buffer)
                )
                within_retest = (
                    boundary * (1 - retest_tolerance)
                    <= spot
                    <= boundary * (1 + retest_tolerance)
                )
                beyond_breakout = spot > breakout if option_type == "CALL" else spot < breakout
                too_far_inside = (
                    spot < boundary * (1 - retest_tolerance)
                    if option_type == "CALL"
                    else spot > boundary * (1 + retest_tolerance)
                )
                state = states[option_type]
                if state and timestamp - state[1] > timeout:
                    state = None
                if state is None:
                    if beyond_breakout:
                        states[option_type] = ("BROKEN", timestamp)
                    continue
                if state[0] == "BROKEN":
                    if within_retest and timestamp > state[1]:
                        states[option_type] = ("RETESTED", state[1], timestamp)
                    elif too_far_inside:
                        states[option_type] = None
                    continue
                if beyond_breakout and timestamp > state[2]:
                    setups[session_date][timestamp].add(option_type)
                    states[option_type] = None
                elif too_far_inside:
                    states[option_type] = None
    return setups


def _completed_spot_bars(spot_rows, interval_minutes):
    session_open = datetime.combine(
        next(iter(spot_rows)).date(), MARKET_OPEN, tzinfo=next(iter(spot_rows)).tzinfo,
    )
    buckets = defaultdict(list)
    for timestamp, spot in sorted(spot_rows.items()):
        minute_offset = int((timestamp - session_open).total_seconds() // 60)
        if minute_offset >= 0:
            buckets[minute_offset // interval_minutes].append((timestamp, spot))

    bars = []
    for bucket, values in sorted(buckets.items()):
        expected_start = session_open + timedelta(minutes=bucket * interval_minutes)
        expected_end = expected_start + timedelta(minutes=interval_minutes - 1)
        if (
            len(values) != interval_minutes
            or values[0][0] != expected_start
            or values[-1][0] != expected_end
        ):
            continue
        spots = [spot for _, spot in values]
        bars.append({
            "timestamp": expected_end,
            "open": spots[0],
            "high": max(spots),
            "low": min(spots),
            "close": spots[-1],
        })
    return bars


def _context_aligned(context_bars, timestamp, option_type):
    for bars in context_bars.values():
        completed = next(
            (bar for bar in reversed(bars) if bar["timestamp"] <= timestamp),
            None,
        )
        if not completed:
            return False
        aligned = (
            completed["close"] > completed["open"]
            if option_type == "CALL"
            else completed["close"] < completed["open"]
        )
        if not aligned:
            return False
    return True


def _opening_breakout_setups(spot_by_date, opening_ranges, config):
    setups = defaultdict(lambda: defaultdict(set))
    breakout_buffer = config.opening_range_buffer_percent / 100
    for session_date, spot_rows in spot_by_date.items():
        opening_range = opening_ranges.get(session_date)
        if not opening_range:
            continue
        opening_low, opening_high = opening_range
        confirmations = {"CALL": 0, "PUT": 0}
        armed = {"CALL": True, "PUT": True}
        active_window = None
        bars = _completed_spot_bars(spot_rows, config.confirmation_interval_minutes)
        context_bars = {
            interval: _completed_spot_bars(spot_rows, interval)
            for interval in config.context_intervals
        }
        for bar in bars:
            timestamp, spot = bar["timestamp"], bar["close"]
            window_index = _entry_window_index(timestamp.time(), config)
            if window_index is None:
                for option_type in ("CALL", "PUT"):
                    boundary = opening_high if option_type == "CALL" else opening_low
                    reset = spot <= boundary if option_type == "CALL" else spot >= boundary
                    if not armed[option_type] and reset:
                        armed[option_type] = True
                        confirmations[option_type] = 0
                active_window = None
                continue
            if window_index != active_window:
                confirmations = {"CALL": 0, "PUT": 0}
                active_window = window_index
            for option_type in ("CALL", "PUT"):
                boundary = opening_high if option_type == "CALL" else opening_low
                breakout = (
                    boundary * (1 + breakout_buffer)
                    if option_type == "CALL"
                    else boundary * (1 - breakout_buffer)
                )
                reset = spot <= boundary if option_type == "CALL" else spot >= boundary
                if not armed[option_type]:
                    if reset:
                        armed[option_type] = True
                        confirmations[option_type] = 0
                    continue
                direction_aligned = (
                    bar["close"] > bar["open"]
                    if option_type == "CALL"
                    else bar["close"] < bar["open"]
                )
                beyond_breakout = (
                    spot > breakout if option_type == "CALL" else spot < breakout
                ) and (
                    direction_aligned or not config.require_confirmation_candle_direction
                ) and _context_aligned(context_bars, timestamp, option_type)
                confirmations[option_type] = confirmations[option_type] + 1 if beyond_breakout else 0
                if confirmations[option_type] >= config.spot_confirmation_bars:
                    setups[session_date][timestamp].add(option_type)
                    armed[option_type] = False
    return setups


def _spot_setups(spot_by_date, opening_ranges, config):
    if config.spot_setup == "opening_retest":
        return _opening_retest_setups(spot_by_date, opening_ranges, config)
    if config.spot_setup == "opening_breakout":
        return _opening_breakout_setups(spot_by_date, opening_ranges, config)
    return defaultdict(lambda: defaultdict(set))


def spot_setup_timestamps(spot_rows, config):
    if not spot_rows:
        return {}
    session_date = next(iter(spot_rows)).date()
    opening_end = datetime.combine(
        session_date, MARKET_OPEN, tzinfo=next(iter(spot_rows)).tzinfo,
    ) + timedelta(minutes=config.opening_range_minutes)
    opening_values = [
        spot for timestamp, spot in spot_rows.items()
        if MARKET_OPEN <= timestamp.time() < opening_end.time()
    ]
    if len(opening_values) < config.opening_range_minutes:
        return {}
    opening_ranges = {session_date: (min(opening_values), max(opening_values))}
    setups = _spot_setups({session_date: spot_rows}, opening_ranges, config)
    return dict(setups[session_date])


def _candidate(
    contract_key, rows, index, config, spot_by_date, opening_ranges, spot_setups,
):
    current = rows[index]
    clock = current["local_timestamp"].time()
    premium = _number(current["close"])
    if _entry_window_index(clock, config) is None:
        return None
    if current["option_type"] not in config.option_types:
        return None
    if not (config.premium_min <= premium <= config.premium_max):
        return None
    if _distance(current["relative_strike"]) > config.max_distance:
        return None
    if not _matches_moneyness(current["relative_strike"], current["option_type"], config):
        return None

    confirmation_start = index - config.confirmation_bars + 1
    prior = rows[confirmation_start - config.lookback:confirmation_start]
    confirmation = rows[confirmation_start:index + 1]
    if len(prior) != config.lookback or len(confirmation) != config.confirmation_bars:
        return None
    if not _is_continuous([*prior, *confirmation, rows[index + 1]]):
        return None
    prior_high = max(_number(row["high"]) for row in prior)
    volumes = [_number(row["volume"]) for row in prior if _number(row["volume"]) > 0]
    baseline_volume = median(volumes) if volumes else 0
    volume_ratio = max(_number(row["volume"]) for row in confirmation) / baseline_volume if baseline_volume else 0
    spot = _number(current["spot"])
    session_spots = spot_by_date[current["local_timestamp"].date()]
    trend_timestamp = current["local_timestamp"] - timedelta(minutes=config.spot_trend_minutes)
    prior_spot = session_spots.get(trend_timestamp, 0)
    option_type = current["option_type"]
    spot_aligned = spot > prior_spot if option_type == "CALL" else spot < prior_spot
    breakout_percent = (premium / prior_high - 1) * 100 if prior_high else 0
    spot_move_percent = abs(spot / prior_spot - 1) * 100 if prior_spot else 0
    confirmed = prior_spot and all(
        _number(row["close"]) >= prior_high * (1 + config.minimum_breakout_percent / 100)
        for row in confirmation
    )
    option_breakout_required = config.signal_mode != "spot"
    if option_breakout_required and (
        not confirmed or breakout_percent < config.minimum_breakout_percent
    ):
        return None
    if volume_ratio < config.volume_ratio or not spot_aligned:
        return None
    if spot_move_percent < config.minimum_spot_move_percent:
        return None
    if config.spot_setup != "trend" and option_type not in spot_setups[
        current["local_timestamp"].date()
    ].get(current["local_timestamp"], set()):
        return None
    if config.require_opening_range_break:
        opening_range = opening_ranges.get(current["local_timestamp"].date())
        if not opening_range:
            return None
        opening_low, opening_high = opening_range
        opening_buffer = config.opening_range_buffer_percent / 100
        range_aligned = (
            spot > opening_high * (1 + opening_buffer)
            if option_type == "CALL"
            else spot < opening_low * (1 - opening_buffer)
        )
        if not range_aligned:
            return None
    if config.signal_mode == "retest":
        if len(confirmation) < 2:
            return None
        tolerance = config.retest_tolerance_percent / 100
        retest_low = _number(current["low"])
        if not prior_high * (1 - tolerance) <= retest_low <= prior_high * (1 + tolerance):
            return None
    if config.require_oi_rise and _number(current["oi"]) <= median(_number(row["oi"]) for row in prior):
        return None
    if config.require_iv_rise and _number(current["implied_volatility"]) <= median(
        _number(row["implied_volatility"]) for row in prior
    ):
        return None
    if premium <= _number(current["open"]):
        return None

    return {
        "date": current["local_timestamp"].date().isoformat(),
        "signal_at": current["local_timestamp"],
        "strike": _number(contract_key[1]),
        "option_type": option_type,
        "relative_strike": current["relative_strike"],
        "volume_ratio": volume_ratio,
        "breakout_percent": breakout_percent,
        "spot_move_percent": spot_move_percent,
        "signal_close": premium,
        "next_index": index + 1,
    }


def _simulate(candidate, rows, config):
    next_row = rows[candidate["next_index"]]
    entry = round(max(_number(next_row["open"]), candidate["signal_close"]) * 1.005, 2)
    if _number(next_row["high"]) < entry:
        return None
    stop = round(entry * (1 - config.stop_percent), 2)
    risk = entry - stop
    if risk <= 0:
        return None
    initial_stop = stop
    target = round(entry + risk * config.reward_risk, 2)
    runner_target = round(entry + risk * 3, 2)
    trailing = config.trail_gap_r
    high_water = entry
    outcome = "TIME_EXIT"
    exit_price = None
    exit_at = None
    for future in rows[candidate["next_index"]:]:
        if future["local_timestamp"].time() > TIME_EXIT:
            break
        low, high = _number(future["low"]), _number(future["high"])
        if low <= stop:
            outcome = "TRAIL_EXIT" if stop > entry else "STOP"
            exit_price, exit_at = stop, future["local_timestamp"]
            break
        if not trailing and high >= target:
            outcome, exit_price, exit_at = "TARGET", target, future["local_timestamp"]
            break
        if trailing:
            # Give the move room until it is trail_gap_r in profit, then follow
            # the running high that far behind it.
            high_water = max(high_water, high)
            if high_water - entry >= risk * trailing:
                stop = max(stop, round(high_water - risk * trailing, 2))
    if exit_price is None:
        eligible = [row for row in rows[candidate["next_index"]:] if row["local_timestamp"].time() <= TIME_EXIT]
        if not eligible:
            return None
        exit_at = eligible[-1]["local_timestamp"]
        exit_price = _number(eligible[-1]["close"]) * 0.995
    return {
        **candidate,
        "signal_at": candidate["signal_at"].isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry": entry,
        "stop_loss": initial_stop,
        "exit_stop": stop,
        "target": target,
        "runner_target": runner_target,
        "outcome": outcome,
        "realized_r": round((exit_price - entry) / risk, 2),
    }


def _execute_signals(signals_by_date, config):
    trades = []
    for trade_date in sorted(signals_by_date):
        daily = signals_by_date[trade_date]
        daily_trades = []
        available_at = None
        daily_r = 0
        for signal_at in sorted({candidate["signal_at"] for candidate in daily}):
            if len(daily_trades) >= config.max_trades_per_day:
                break
            if daily_r <= -config.daily_loss_limit_r:
                break
            if available_at and signal_at < available_at:
                continue
            simultaneous = sorted(
                (candidate for candidate in daily if candidate["signal_at"] == signal_at),
                key=lambda candidate: (
                    candidate["volume_ratio"] * candidate["breakout_percent"],
                    -_distance(candidate["relative_strike"]),
                ),
                reverse=True,
            )
            for selected in simultaneous:
                rows = selected["rows"]
                trade = _simulate(
                    {key: value for key, value in selected.items() if key != "rows"},
                    rows,
                    config,
                )
                if not trade:
                    continue
                trades.append(trade)
                daily_trades.append(trade)
                daily_r += trade["realized_r"]
                available_at = datetime.fromisoformat(trade["exit_at"]) + timedelta(
                    minutes=config.reentry_cooldown_minutes,
                )
                break
    return trades


def backtest_strategy(underlying, expiry_code, config, contracts=None):
    signals_by_date = defaultdict(list)
    contracts = contracts or load_contract_rows(underlying, expiry_code)
    spot_by_date, opening_ranges = _spot_context(contracts, config.opening_range_minutes)
    spot_setups = _spot_setups(spot_by_date, opening_ranges, config)
    for contract_key, rows in contracts.items():
        if not contract_key[1]:
            continue
        first_index = config.lookback + config.confirmation_bars - 1
        for index in range(first_index, len(rows) - 1):
            candidate = _candidate(
                contract_key, rows, index, config, spot_by_date, opening_ranges,
                spot_setups,
            )
            if candidate:
                candidate["rows"] = rows
                signals_by_date[candidate["date"]].append(candidate)
    return _execute_signals(signals_by_date, config)


def trade_metrics(trades):
    wins = [trade for trade in trades if trade["outcome"] == "TARGET"]
    stops = [trade for trade in trades if trade["outcome"] == "STOP"]
    time_exits = [trade for trade in trades if trade["outcome"] == "TIME_EXIT"]
    total_r = sum(trade["realized_r"] for trade in trades)
    gross_profit = sum(max(trade["realized_r"], 0) for trade in trades)
    gross_loss = abs(sum(min(trade["realized_r"], 0) for trade in trades))
    equity = drawdown = peak = 0
    for trade in trades:
        equity += trade["realized_r"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(trades),
        "targets": len(wins),
        "stops": len(stops),
        "time_exits": len(time_exits),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_r": round(total_r, 2),
        "average_r": round(total_r / len(trades), 2) if trades else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "max_drawdown_r": round(drawdown, 2),
    }


def chronological_split(trades, session_dates, validation_days=6):
    validation_dates = {date.isoformat() for date in sorted(session_dates)[-validation_days:]}
    return (
        [trade for trade in trades if trade["date"] not in validation_dates],
        [trade for trade in trades if trade["date"] in validation_dates],
    )