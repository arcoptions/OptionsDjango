import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.utils import timezone

from options_tracker.dynamic_strategy import available_session_dates, nifty_expiry_sessions
from options_tracker.strategy_backtest import (
    backtest_strategy,
    nifty_put_strategy_config,
    trade_metrics,
)


def normal_day_config():
    return replace(
        nifty_put_strategy_config(max_trades_per_day=3),
        option_types=("CALL", "PUT"),
        minimum_spot_move_percent=0.15,
        volume_ratio=1.5,
        end_time=time(15, 9),
        entry_windows=(
            (time(9, 30), time(10, 59)),
            (time(13, 30), time(14, 29)),
            (time(14, 30), time(15, 9)),
        ),
    )


def _window(trade):
    clock = datetime.fromisoformat(trade["signal_at"]).time()
    if clock < time(13, 30):
        return "MORNING"
    if clock < time(14, 30):
        return "AFTERNOON"
    return "CLOSING"


def _group_metrics(trades, classifier):
    groups = defaultdict(list)
    for trade in trades:
        groups[classifier(trade)].append(trade)
    return {
        str(name): trade_metrics(rows)
        for name, rows in sorted(groups.items())
    }


class Command(BaseCommand):
    help = "Research the symmetric three-window strategy on normal NIFTY sessions."

    def handle(self, *args, **options):
        config = normal_day_config()
        trades = backtest_strategy("NIFTY", 1, config)
        session_dates = available_session_dates()
        completed_dates = [day for day in session_dates if day < timezone.localdate()]
        expiry_dates = nifty_expiry_sessions(completed_dates)
        normal_dates = [day for day in completed_dates if day not in expiry_dates]
        split_at = max(int(len(completed_dates) * 0.7), 1)
        validation_start = completed_dates[split_at]
        training_dates = {day.isoformat() for day in normal_dates if day < validation_start}
        validation_dates = {day.isoformat() for day in normal_dates if day >= validation_start}
        normal_trades = [
            trade
            for trade in trades
            if trade["date"] in training_dates | validation_dates
        ]
        training = [trade for trade in normal_trades if trade["date"] in training_dates]
        validation = [trade for trade in normal_trades if trade["date"] in validation_dates]
        daily_counts = Counter(trade["date"] for trade in normal_trades)

        report = {
            "configuration": {
                "directions": config.option_types,
                "windows": [
                    [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")]
                    for start, end in config.entry_windows
                ],
                "stop_percent": config.stop_percent * 100,
                "target_r": config.reward_risk,
                "minimum_spot_move_percent": config.minimum_spot_move_percent,
                "minimum_volume_ratio": config.volume_ratio,
                "maximum_trades_per_day": config.max_trades_per_day,
            },
            "coverage": {
                "normal_sessions": len(normal_dates),
                "training_sessions": len(training_dates),
                "validation_sessions": len(validation_dates),
                "validation_starts": validation_start.isoformat(),
            },
            "training": trade_metrics(training),
            "validation": trade_metrics(validation),
            "all": trade_metrics(normal_trades),
            "daily_frequency": {
                "zero_trade_days": len(normal_dates) - len(daily_counts),
                "one_trade_days": sum(count == 1 for count in daily_counts.values()),
                "two_trade_days": sum(count == 2 for count in daily_counts.values()),
                "three_trade_days": sum(count == 3 for count in daily_counts.values()),
            },
            "by_window": {
                "training": _group_metrics(training, _window),
                "validation": _group_metrics(validation, _window),
            },
            "by_window_and_side": {
                "training": _group_metrics(
                    training, lambda trade: f'{_window(trade)}:{trade["option_type"]}',
                ),
                "validation": _group_metrics(
                    validation, lambda trade: f'{_window(trade)}:{trade["option_type"]}',
                ),
            },
            "training_features": {
                "volume_ratio": _group_metrics(
                    training,
                    lambda trade: (
                        "1.0-1.49" if trade["volume_ratio"] < 1.5
                        else "1.5-1.99" if trade["volume_ratio"] < 2
                        else ">=2.0"
                    ),
                ),
                "spot_move_percent": _group_metrics(
                    training,
                    lambda trade: (
                        "0.10-0.14" if trade["spot_move_percent"] < 0.15
                        else "0.15-0.24" if trade["spot_move_percent"] < 0.25
                        else ">=0.25"
                    ),
                ),
            },
        }
        self.stdout.write(json.dumps(report, indent=2, default=str))