import json
from collections import defaultdict

from django.core.management.base import BaseCommand

from options_tracker.dynamic_strategy import (
    DynamicStrategyConfig,
    backtest_dynamic_strategy,
    dynamic_trade_metrics,
)


def _group_metrics(trades, key, session_dates):
    groups = defaultdict(list)
    for trade in trades:
        groups[str(trade[key])].append(trade)
    return {
        name: dynamic_trade_metrics(group, session_dates)
        for name, group in sorted(groups.items())
    }


def _feature_metrics(trades):
    gross_profit = sum(max(trade["realized_r"], 0) for trade in trades)
    gross_loss = abs(sum(min(trade["realized_r"], 0) for trade in trades))
    return {
        "trades": len(trades),
        "targets": sum(trade["outcome"] == "TARGET" for trade in trades),
        "stops": sum(trade["outcome"] == "STOP" for trade in trades),
        "time_exits": sum(trade["outcome"] == "TIME_EXIT" for trade in trades),
        "total_r": round(sum(trade["realized_r"] for trade in trades), 2),
        "average_r": round(sum(trade["realized_r"] for trade in trades) / len(trades), 2) if trades else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "three_x_excursions": sum(trade["window_max_multiple"] >= 3 for trade in trades),
    }


def _binned_metrics(trades, classifier):
    groups = defaultdict(list)
    for trade in trades:
        groups[classifier(trade)].append(trade)
    return {
        name: _feature_metrics(group)
        for name, group in sorted(groups.items())
    }


def _training_feature_bins(trades):
    return {
        "window_and_side": _binned_metrics(
            trades, lambda trade: f'{trade["window"]}:{trade["option_type"]}',
        ),
        "range_atr": _binned_metrics(
            trades,
            lambda trade: (
                "<1.00" if trade["range_atr"] < 1
                else "1.00-1.24" if trade["range_atr"] < 1.25
                else ">=1.25"
            ),
        ),
        "body_fraction": _binned_metrics(
            trades,
            lambda trade: (
                "<0.50" if trade["body_fraction"] < 0.5
                else "0.50-0.69" if trade["body_fraction"] < 0.7
                else ">=0.70"
            ),
        ),
        "trend_strength": _binned_metrics(
            trades,
            lambda trade: (
                "<0.25" if trade["trend_strength"] < 0.25
                else "0.25-0.49" if trade["trend_strength"] < 0.5
                else ">=0.50"
            ),
        ),
        "volume_ratio": _binned_metrics(
            trades,
            lambda trade: (
                "1.20-1.49" if trade["volume_ratio"] < 1.5
                else "1.50-1.99" if trade["volume_ratio"] < 2
                else ">=2.00"
            ),
        ),
        "option_breakout_percent": _binned_metrics(
            trades,
            lambda trade: (
                "<1" if trade["breakout_percent"] < 1
                else "1-2.99" if trade["breakout_percent"] < 3
                else "3-6.99" if trade["breakout_percent"] < 7
                else ">=7"
            ),
        ),
        "iv_change": _binned_metrics(
            trades, lambda trade: "positive" if trade["iv_change"] > 0 else "flat_or_negative",
        ),
        "oi_change": _binned_metrics(
            trades,
            lambda trade: "positive" if trade["oi_change_percent"] > 0 else "flat_or_negative",
        ),
        "otm_distance": _binned_metrics(
            trades, lambda trade: str(trade["otm_distance"]),
        ),
        "signal_quarter_hour": _binned_metrics(
            trades,
            lambda trade: (
                f'{trade["timestamp"].hour:02d}:'
                f'{(trade["timestamp"].minute // 15) * 15:02d}'
            ),
        ),
    }


class Command(BaseCommand):
    help = "Research the isolated bidirectional three-window NIFTY strategy."

    def add_arguments(self, parser):
        parser.add_argument("--underlying", default="NIFTY")
        parser.add_argument("--expiry-code", type=int, default=1)
        parser.add_argument("--validation-percent", type=int, default=30)

    def handle(self, *args, **options):
        config = DynamicStrategyConfig()
        trades, session_dates = backtest_dynamic_strategy(
            underlying=options["underlying"].upper(),
            expiry_code=options["expiry_code"],
            config=config,
        )
        validation_percent = min(max(options["validation_percent"], 10), 50)
        split_at = max(int(len(session_dates) * (1 - validation_percent / 100)), 1)
        training_dates = set(session_dates[:split_at])
        validation_dates = set(session_dates[split_at:])
        training_trades = [
            trade for trade in trades if trade["date"] in {day.isoformat() for day in training_dates}
        ]
        validation_trades = [
            trade for trade in trades if trade["date"] in {day.isoformat() for day in validation_dates}
        ]
        expiry_trades = [trade for trade in trades if trade["is_expiry_day"]]
        closing_expiry = [
            trade
            for trade in expiry_trades
            if trade["window"] == "CLOSING"
        ]
        report = {
            "configuration": {
                "windows": [
                    {
                        "name": window.name,
                        "start": window.start.isoformat(timespec="minutes"),
                        "signal_end": window.signal_end.isoformat(timespec="minutes"),
                        "exit": window.exit_time.isoformat(timespec="minutes"),
                    }
                    for window in config.windows
                ],
                "reward_risk": config.reward_risk,
                "entry_slippage_percent": config.entry_slippage_percent,
                "volume_ratio": config.minimum_volume_ratio,
                "expiry_volume_ratio": config.expiry_minimum_volume_ratio,
            },
            "coverage": {
                "first_session": session_dates[0].isoformat() if session_dates else None,
                "last_session": session_dates[-1].isoformat() if session_dates else None,
                "completed_sessions": len(session_dates),
                "training_sessions": len(training_dates),
                "validation_sessions": len(validation_dates),
                "validation_starts": min(validation_dates).isoformat() if validation_dates else None,
            },
            "all": dynamic_trade_metrics(trades, session_dates),
            "training": dynamic_trade_metrics(training_trades, training_dates),
            "validation": dynamic_trade_metrics(validation_trades, validation_dates),
            "training_feature_bins": _training_feature_bins(training_trades),
            "by_window": _group_metrics(trades, "window", session_dates),
            "by_side": _group_metrics(trades, "option_type", session_dates),
            "expiry": dynamic_trade_metrics(expiry_trades, session_dates),
            "closing_expiry": dynamic_trade_metrics(closing_expiry, session_dates),
            "largest_expiry_excursions": [
                {
                    key: trade[key]
                    for key in (
                        "date", "signal_at", "window", "option_type", "strike",
                        "relative_strike", "entry", "outcome", "realized_r",
                        "window_max_multiple", "volume_ratio", "breakout_percent",
                    )
                }
                for trade in sorted(
                    closing_expiry,
                    key=lambda trade: trade["window_max_multiple"],
                    reverse=True,
                )[:20]
            ],
        }
        self.stdout.write(json.dumps(report, indent=2, default=str))