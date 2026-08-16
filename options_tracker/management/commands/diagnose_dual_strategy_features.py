import json
from collections import defaultdict

from django.core.management.base import BaseCommand

from options_tracker.dual_strategy_research import (
    ExitScenario,
    collect_dual_strategy_candidates,
    expiry_closing_variants,
    expiry_hero_exit_scenarios,
    normal_strategy_config,
    scenario_metrics,
    simulate_exit_scenario,
)


def _compact(metrics):
    return {
        key: metrics[key]
        for key in (
            "trades", "calls", "puts", "wins", "stops", "total_r",
            "total_account_return_percent", "profit_factor",
            "maximum_drawdown_percent",
        )
    }


def _grouped(trades, classifier, training_dates, validation_dates):
    training_iso = {day.isoformat() for day in training_dates}
    validation_iso = {day.isoformat() for day in validation_dates}
    groups = defaultdict(list)
    for trade in trades:
        groups[str(classifier(trade))].append(trade)
    report = {}
    for name, rows in sorted(groups.items()):
        training = [row for row in rows if row["date"] in training_iso]
        validation = [row for row in rows if row["date"] in validation_iso]
        if len(training) < 5:
            continue
        report[name] = {
            "training": _compact(scenario_metrics(training, training_dates)),
            "validation": _compact(scenario_metrics(validation, validation_dates)),
        }
    return report


def _simulate(candidates, config, scenario, is_expiry_day):
    return [
        trade
        for candidate in candidates
        if (trade := simulate_exit_scenario(candidate, config, scenario, is_expiry_day))
    ]


def _feature_report(trades, training_dates, validation_dates):
    return {
        "window": _grouped(trades, lambda row: row["window"], training_dates, validation_dates),
        "setup_type": _grouped(
            trades, lambda row: row.get("setup_type", "UNKNOWN"),
            training_dates, validation_dates,
        ),
        "window_setup_side": _grouped(
            trades,
            lambda row: f'{row["window"]}:{row.get("setup_type", "UNKNOWN")}:{row["option_type"]}',
            training_dates,
            validation_dates,
        ),
        "quarter_hour": _grouped(
            trades,
            lambda row: f'{row["timestamp"].hour:02d}:{row["timestamp"].minute // 15 * 15:02d}',
            training_dates,
            validation_dates,
        ),
        "volume_ratio": _grouped(
            trades,
            lambda row: "<1.5" if row["volume_ratio"] < 1.5 else "1.5-2" if row["volume_ratio"] < 2 else ">=2",
            training_dates,
            validation_dates,
        ),
        "breakout_percent": _grouped(
            trades,
            lambda row: "<1" if row["breakout_percent"] < 1 else "1-5" if row["breakout_percent"] < 5 else ">=5",
            training_dates,
            validation_dates,
        ),
        "oi_change": _grouped(
            trades, lambda row: "positive" if row["oi_change_percent"] > 0 else "non_positive",
            training_dates, validation_dates,
        ),
        "iv_change": _grouped(
            trades, lambda row: "positive" if row["iv_change"] > 0 else "non_positive",
            training_dates, validation_dates,
        ),
        "otm_distance": _grouped(
            trades, lambda row: row["otm_distance"], training_dates, validation_dates,
        ),
    }


class Command(BaseCommand):
    help = "Diagnose causal features for the two isolated strategy playbooks."

    def handle(self, *args, **options):
        candidates, session_dates, _ = collect_dual_strategy_candidates()
        split_at = max(int(len(session_dates) * 0.7), 1)
        training_dates = session_dates[:split_at]
        validation_dates = session_dates[split_at:]

        normal_scenario = ExitScenario("SL10_T2.5R", 10, "R", 2.5, 0.5)
        normal_trades = _simulate(
            candidates["normal"], normal_strategy_config(), normal_scenario, False,
        )

        expiry_reports = {}
        configs = expiry_closing_variants()
        for variant in ("P5_20_ATM_OTM2", "P10_25_ATM_OTM2", "P20_50_ATM_OTM2"):
            rows = candidates["expiry_closing"][variant]
            scenario_reports = []
            for scenario in expiry_hero_exit_scenarios():
                trades = _simulate(rows, configs[variant], scenario, True)
                training = [
                    trade for trade in trades
                    if trade["date"] in {day.isoformat() for day in training_dates}
                ]
                validation = [
                    trade for trade in trades
                    if trade["date"] in {day.isoformat() for day in validation_dates}
                ]
                scenario_reports.append({
                    "scenario": scenario.name,
                    "training": _compact(scenario_metrics(training, training_dates)),
                    "validation": _compact(scenario_metrics(validation, validation_dates)),
                })
            expiry_reports[variant] = {
                "top_training_exits": sorted(
                    scenario_reports,
                    key=lambda row: (
                        row["training"]["profit_factor"] or 0,
                        row["training"]["total_r"],
                    ),
                    reverse=True,
                )[:5],
            }

        expiry_diagnostic_scenario = ExitScenario("SL75_T5X", 75, "MULTIPLE", 5, 0.2)
        expiry_diagnostic_trades = _simulate(
            candidates["expiry_closing"]["P20_50_ATM_OTM2"],
            configs["P20_50_ATM_OTM2"],
            expiry_diagnostic_scenario,
            True,
        )
        report = {
            "coverage": {
                "training_ends": training_dates[-1].isoformat(),
                "validation_starts": validation_dates[0].isoformat(),
            },
            "normal_SL10_T2.5R_features": _feature_report(
                normal_trades, training_dates, validation_dates,
            ),
            "expiry_exit_scenarios": expiry_reports,
            "expiry_P20_50_SL75_T5X_features": _feature_report(
                expiry_diagnostic_trades, training_dates, validation_dates,
            ),
        }
        self.stdout.write(json.dumps(report, indent=2, default=str))