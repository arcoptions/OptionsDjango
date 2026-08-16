"""Exit-management frontier for the NORMAL_DAY buying strategy.

Entry logic is frozen (normal_day_config). We cache the raw candidate set once,
then re-run trade selection + exit simulation for every stop/target/partial/
trail variant. Train/validation split is temporal so the frontier can be read
without fooling ourselves.
"""
import os
import pickle
import sys
from datetime import timedelta
from statistics import median

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from options_tracker.capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from options_tracker.strategy_backtest import (
    TIME_EXIT,
    _candidate,
    _distance,
    _number,
    _spot_context,
    _spot_setups,
    load_contract_rows,
)
from options_tracker.management.commands.research_normal_day_strategy import (
    normal_day_config,
)

CONFIG = normal_day_config()
CACHE = os.path.join(ROOT, "research", "normal_candidates.pkl")
VALIDATION_START = "2026-04-29"


def collect():
    """Every entry signal the frozen entry logic produces, with its forward bars."""
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as handle:
            return pickle.load(handle)
    contracts = load_contract_rows("NIFTY", 1)
    spot_by_date, opening_ranges = _spot_context(contracts, CONFIG.opening_range_minutes)
    spot_setups = _spot_setups(spot_by_date, opening_ranges, CONFIG)
    signals = {}
    for contract_key, rows in contracts.items():
        if not contract_key[1]:
            continue
        first = CONFIG.lookback + CONFIG.confirmation_bars - 1
        for index in range(first, len(rows) - 1):
            candidate = _candidate(
                contract_key, rows, index, CONFIG, spot_by_date, opening_ranges,
                spot_setups,
            )
            if not candidate:
                continue
            # Only the forward path matters for exits; keep it compact.
            candidate["forward"] = [
                (
                    row["local_timestamp"],
                    _number(row["open"]),
                    _number(row["high"]),
                    _number(row["low"]),
                    _number(row["close"]),
                )
                for row in rows[candidate["next_index"]:]
                if row["local_timestamp"].time() <= TIME_EXIT
            ]
            candidate.pop("next_index")
            signals.setdefault(candidate["date"], []).append(candidate)
    with open(CACHE, "wb") as handle:
        pickle.dump(signals, handle)
    return signals


def simulate(candidate, stop_percent, reward, partial_at=None, partial_frac=0.5,
             breakeven=False, trail_gap=None, runner=None):
    """Exit a long option position under one exit scheme.

    reward     fixed target in R (None = no fixed target)
    partial_at book partial_frac of the position at this R, then optionally
               move the stop to breakeven and let the rest run to `runner` R
    trail_gap  once price trades trail_gap R in profit, trail the stop that far
               below the running high
    """
    forward = candidate["forward"]
    if not forward:
        return None
    entry = round(max(forward[0][1], candidate["signal_close"]) * 1.005, 2)
    if forward[0][2] < entry:
        return None
    stop = round(entry * (1 - stop_percent), 2)
    risk = entry - stop
    if risk <= 0:
        return None

    exit_reward = runner if partial_at else reward
    final_target = entry + risk * exit_reward if exit_reward else None
    partial_price = entry + risk * partial_at if partial_at else None
    booked_r = 0.0
    remaining = 1.0
    high_water = entry
    outcome = "TIME_EXIT"
    exit_price = exit_at = None

    for timestamp, _open, high, low, close in forward:
        # Conservative intrabar order: adverse move resolves before favourable.
        if low <= stop:
            booked_r += remaining * (stop - entry) / risk
            outcome = "STOP" if stop < entry else "BREAKEVEN"
            exit_price, exit_at, remaining = stop, timestamp, 0.0
            break
        if partial_price and remaining == 1.0 and high >= partial_price:
            booked_r += partial_frac * partial_at
            remaining = 1 - partial_frac
            if breakeven:
                stop = max(stop, entry)
        if final_target and high >= final_target:
            booked_r += remaining * (final_target - entry) / risk
            outcome = "TARGET"
            exit_price, exit_at, remaining = final_target, timestamp, 0.0
            break
        if trail_gap:
            high_water = max(high_water, high)
            if high_water - entry >= risk * trail_gap:
                stop = max(stop, round(high_water - risk * trail_gap, 2))

    if remaining > 0:
        timestamp, _open, _high, _low, close = forward[-1]
        price = close * 0.995
        booked_r += remaining * (price - entry) / risk
        exit_price, exit_at = price, timestamp

    charges = estimate_option_charges(
        entry, max(exit_price, 0), NIFTY_LOT_SIZE, candidate["date"]
    )
    cost_r = charges / (risk * NIFTY_LOT_SIZE)
    return {
        "date": candidate["date"],
        "signal_at": candidate["signal_at"],
        "exit_at": exit_at,
        "entry": entry,
        "risk": risk,
        "outcome": outcome,
        "gross_r": round(booked_r, 4),
        "net_r": round(booked_r - cost_r, 4),
        "net_rupees": round(booked_r * risk * NIFTY_LOT_SIZE - charges, 2),
    }


def execute(signals, **scheme):
    """Apply the live trade-selection rules (cap, cooldown, daily loss) per day."""
    trades = []
    for trade_date in sorted(signals):
        daily = signals[trade_date]
        taken = 0
        available_at = None
        daily_r = 0.0
        for signal_at in sorted({item["signal_at"] for item in daily}):
            if taken >= CONFIG.max_trades_per_day or daily_r <= -CONFIG.daily_loss_limit_r:
                break
            if available_at and signal_at < available_at:
                continue
            simultaneous = sorted(
                (item for item in daily if item["signal_at"] == signal_at),
                key=lambda item: (
                    item["volume_ratio"] * item["breakout_percent"],
                    -_distance(item["relative_strike"]),
                ),
                reverse=True,
            )
            for selected in simultaneous:
                trade = simulate(selected, **scheme)
                if not trade:
                    continue
                trades.append(trade)
                taken += 1
                daily_r += trade["net_r"]
                available_at = trade["exit_at"] + timedelta(
                    minutes=CONFIG.reentry_cooldown_minutes
                )
                break
    return trades


def metrics(trades):
    if not trades:
        return None
    wins = [trade for trade in trades if trade["net_r"] > 0]
    gross_profit = sum(trade["net_r"] for trade in trades if trade["net_r"] > 0)
    gross_loss = abs(sum(trade["net_r"] for trade in trades if trade["net_r"] < 0))
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade["net_r"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(trades), 1),
        "total_r": round(sum(trade["net_r"] for trade in trades), 2),
        "avg_r": round(sum(trade["net_r"] for trade in trades) / len(trades), 3),
        "median_r": round(median(trade["net_r"] for trade in trades), 3),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else 99.0,
        "max_dd_r": round(drawdown, 2),
        "rupees": round(sum(trade["net_rupees"] for trade in trades)),
    }


def split(trades):
    train = [trade for trade in trades if trade["date"] < VALIDATION_START]
    validation = [trade for trade in trades if trade["date"] >= VALIDATION_START]
    return metrics(train), metrics(validation), metrics(trades)


def variants():
    for stop in (0.06, 0.08, 0.10, 0.12, 0.15):
        for reward in (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
            yield f"S{int(stop*100)}_T{reward}", {"stop_percent": stop, "reward": reward}
    for stop in (0.08, 0.10, 0.12):
        for partial in (0.5, 0.75, 1.0, 1.25):
            for runner in (2.0, 3.0):
                yield (
                    f"S{int(stop*100)}_P{partial}BE_R{runner}",
                    {"stop_percent": stop, "reward": None, "partial_at": partial,
                     "partial_frac": 0.5, "breakeven": True, "runner": runner},
                )
        for gap in (0.5, 0.75, 1.0, 1.5):
            yield (
                f"S{int(stop*100)}_TRAIL{gap}",
                {"stop_percent": stop, "reward": None, "trail_gap": gap},
            )
            yield (
                f"S{int(stop*100)}_P1BE_TRAIL{gap}",
                {"stop_percent": stop, "reward": None, "partial_at": 1.0,
                 "partial_frac": 0.5, "breakeven": True, "trail_gap": gap},
            )


def main():
    signals = collect()
    total = sum(len(value) for value in signals.values())
    print(f"cached candidates: {total} across {len(signals)} sessions\n")
    rows = []
    for name, scheme in variants():
        train, validation, overall = split(execute(signals, **scheme))
        if not overall:
            continue
        rows.append((name, train, validation, overall))
    rows.sort(key=lambda row: row[3]["total_r"], reverse=True)
    header = (
        f"{'variant':<22}{'n':>4}{'win%':>7}{'totR':>8}{'avgR':>7}{'PF':>6}"
        f"{'ddR':>7}{'rupees':>9}{'valN':>6}{'valWin%':>9}{'valR':>7}"
    )
    print(header)
    print("-" * len(header))
    for name, train, validation, overall in rows:
        validation = validation or {"trades": 0, "win_rate": 0.0, "total_r": 0.0}
        print(
            f"{name:<22}{overall['trades']:>4}{overall['win_rate']:>7}{overall['total_r']:>8}"
            f"{overall['avg_r']:>7}{overall['profit_factor']:>6}{overall['max_dd_r']:>7}"
            f"{overall['rupees']:>9}{validation['trades']:>6}{validation['win_rate']:>9}"
            f"{validation['total_r']:>7}"
        )
    with open(os.path.join(ROOT, "research", "exit_frontier.pkl"), "wb") as handle:
        pickle.dump(rows, handle)


if __name__ == "__main__":
    main()
