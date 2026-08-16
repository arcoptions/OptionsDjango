import json
from collections import Counter
from datetime import time

from django.core.management.base import BaseCommand

from options_tracker.dual_strategy_research import (
    collect_dual_strategy_candidates,
    expiry_closing_variants,
    expiry_hero_exit_scenarios,
    scenario_metrics,
    simulate_exit_scenario,
)


def _compact(metrics):
    return {
        key: metrics[key]
        for key in (
            "trades", "calls", "puts", "wins", "stops", "win_rate",
            "total_r", "total_account_return_percent", "profit_factor",
            "maximum_drawdown_percent", "maximum_allocation_percent",
        )
    }


class Command(BaseCommand):
    help = "Research the low-risk NIFTY expiry closing strategy and exit scenarios."

    def handle(self, *args, **options):
        candidates, session_dates, expiry_dates = collect_dual_strategy_candidates()
        split_at = max(int(len(session_dates) * 0.7), 1)
        training_dates = session_dates[:split_at]
        validation_dates = session_dates[split_at:]
        training_iso = {day.isoformat() for day in training_dates}
        validation_iso = {day.isoformat() for day in validation_dates}
        variant = "P20_50_ATM_OTM2"
        config = expiry_closing_variants()[variant]
        eligible = [
            candidate
            for candidate in candidates["expiry_closing"][variant]
            if time(14, 30) <= candidate["timestamp"].time() < time(14, 45)
        ]
        reports = []
        for scenario in expiry_hero_exit_scenarios():
            trades = [
                trade
                for candidate in eligible
                if (trade := simulate_exit_scenario(candidate, config, scenario, True))
            ]
            training = [trade for trade in trades if trade["date"] in training_iso]
            validation = [trade for trade in trades if trade["date"] in validation_iso]
            reports.append({
                "scenario": scenario.name,
                "training": _compact(scenario_metrics(training, training_dates)),
                "validation": _compact(scenario_metrics(validation, validation_dates)),
                "all": _compact(scenario_metrics(trades, session_dates)),
            })
        ranked = sorted(
            reports,
            key=lambda row: (
                row["training"]["profit_factor"] or 0,
                row["training"]["total_r"],
            ),
            reverse=True,
        )
        opportunity_rows = candidates["expiry_opportunities"]
        signal_dates = Counter(candidate["timestamp"].date().isoformat() for candidate in eligible)
        report = {
            "configuration": {
                "premium_range": [20, 50],
                "moneyness": "ATM through OTM2",
                "signal_window": ["14:30", "14:44"],
                "signal": "one-minute premium breakout and volume with completed five-minute spot alignment",
                "account_risk_percent": 0.2,
            },
            "coverage": {
                "completed_sessions": len(session_dates),
                "expiry_sessions": len(set(session_dates) & expiry_dates),
                "training_expiry_sessions": len(set(training_dates) & expiry_dates),
                "validation_expiry_sessions": len(set(validation_dates) & expiry_dates),
                "validation_starts": validation_dates[0].isoformat(),
                "signal_days": len(signal_dates),
            },
            "oracle_opportunity": {
                "sessions_with_2x": sum(row["maximum_multiple"] >= 2 for row in opportunity_rows),
                "sessions_with_3x": sum(row["maximum_multiple"] >= 3 for row in opportunity_rows),
                "sessions_with_5x": sum(row["maximum_multiple"] >= 5 for row in opportunity_rows),
                "expiry_sessions": len(opportunity_rows),
                "warning": "Uses future knowledge to select the best contract and minute; not a tradable result.",
            },
            "scenarios_tested": len(reports),
            "ranked_on_training": ranked,
        }
        self.stdout.write(json.dumps(report, indent=2, default=str))