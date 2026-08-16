import json

from django.core.management.base import BaseCommand

from options_tracker.dual_strategy_research import (
    collect_dual_strategy_candidates,
    expiry_early_exit_scenarios,
    expiry_hero_exit_scenarios,
    expiry_closing_variants,
    expiry_strategy_config,
    normal_exit_scenarios,
    normal_strategy_config,
    scenario_metrics,
    simulate_exit_scenario,
)


def _evaluate(candidates, config, scenarios, training_dates, validation_dates, is_expiry_day):
    reports = []
    training_iso = {day.isoformat() for day in training_dates}
    validation_iso = {day.isoformat() for day in validation_dates}
    for scenario in scenarios:
        trades = [
            trade
            for candidate in candidates
            if (trade := simulate_exit_scenario(candidate, config, scenario, is_expiry_day))
        ]
        training = [trade for trade in trades if trade["date"] in training_iso]
        validation = [trade for trade in trades if trade["date"] in validation_iso]
        reports.append({
            "scenario": scenario.name,
            "training": scenario_metrics(training, training_dates),
            "validation": scenario_metrics(validation, validation_dates),
            "all": scenario_metrics(trades, [*training_dates, *validation_dates]),
        })
    return reports


def _rank(reports, minimum_trades, limit=None):
    eligible = [
        report
        for report in reports
        if report["training"]["trades"] >= minimum_trades
    ]
    ranked = sorted(
        eligible,
        key=lambda report: (
            report["training"]["profit_factor"] or 0,
            report["training"]["total_account_return_percent"],
            -report["training"]["maximum_drawdown_percent"],
        ),
        reverse=True,
    )
    return ranked[:limit] if limit else ranked


def _compact_metrics(metrics):
    return {
        key: metrics[key]
        for key in (
            "trades", "calls", "puts", "wins", "stops", "win_rate",
            "total_r", "total_account_return_percent", "profit_factor",
            "maximum_drawdown_percent", "maximum_allocation_percent",
        )
    }


def _compact_report(report):
    compact = {
        "scenario": report["scenario"],
        "training": _compact_metrics(report["training"]),
        "validation": _compact_metrics(report["validation"]),
        "all": _compact_metrics(report["all"]),
    }
    if "variant" in report:
        compact["variant"] = report["variant"]
    return compact


class Command(BaseCommand):
    help = "Compare independent normal-day and expiry-day strategy scenarios."

    def add_arguments(self, parser):
        parser.add_argument("--underlying", default="NIFTY")
        parser.add_argument("--expiry-code", type=int, default=1)
        parser.add_argument("--validation-percent", type=int, default=30)
        parser.add_argument("--compact", action="store_true")

    def handle(self, *args, **options):
        candidates, session_dates, expiry_dates = collect_dual_strategy_candidates(
            options["underlying"].upper(), options["expiry_code"],
        )
        validation_percent = min(max(options["validation_percent"], 10), 50)
        split_at = max(int(len(session_dates) * (1 - validation_percent / 100)), 1)
        training_dates = session_dates[:split_at]
        validation_dates = session_dates[split_at:]

        normal_reports = _evaluate(
            candidates["normal"], normal_strategy_config(), normal_exit_scenarios(),
            training_dates, validation_dates, False,
        )
        early_expiry_reports = _evaluate(
            candidates["expiry_early"], expiry_strategy_config(),
            expiry_early_exit_scenarios(), training_dates, validation_dates, True,
        )
        closing_reports = []
        variant_configs = expiry_closing_variants()
        for variant, variant_candidates in candidates["expiry_closing"].items():
            for report in _evaluate(
                variant_candidates, variant_configs[variant], expiry_hero_exit_scenarios(),
                training_dates, validation_dates, True,
            ):
                report["variant"] = variant
                closing_reports.append(report)

        report = {
            "coverage": {
                "first_session": session_dates[0].isoformat() if session_dates else None,
                "last_session": session_dates[-1].isoformat() if session_dates else None,
                "sessions": len(session_dates),
                "scheduled_expiry_sessions": len(set(session_dates) & expiry_dates),
                "training_sessions": len(training_dates),
                "validation_sessions": len(validation_dates),
                "validation_starts": validation_dates[0].isoformat() if validation_dates else None,
            },
            "risk_model": {
                "normal_account_risk_percent": 0.5,
                "expiry_account_risk_percent": 0.2,
                "note": "Wider premium stops reduce position allocation so account risk stays fixed.",
            },
            "scenario_counts": {
                "normal": len(normal_reports),
                "expiry_early": len(early_expiry_reports),
                "expiry_closing": len(closing_reports),
            },
            "candidate_counts": {
                "normal": len(candidates["normal"]),
                "expiry_early": len(candidates["expiry_early"]),
                "expiry_closing_by_variant": {
                    name: len(rows)
                    for name, rows in candidates["expiry_closing"].items()
                },
            },
            "expiry_opportunity_upper_bound": {
                "eligible_sessions": len(candidates["expiry_opportunities"]),
                "sessions_with_2x": sum(
                    row["maximum_multiple"] >= 2
                    for row in candidates["expiry_opportunities"]
                ),
                "sessions_with_3x": sum(
                    row["maximum_multiple"] >= 3
                    for row in candidates["expiry_opportunities"]
                ),
                "sessions_with_5x": sum(
                    row["maximum_multiple"] >= 5
                    for row in candidates["expiry_opportunities"]
                ),
                "top_sessions": sorted(
                    candidates["expiry_opportunities"],
                    key=lambda row: row["maximum_multiple"],
                    reverse=True,
                )[:15],
                "note": "Oracle upper bound: best executable contract/minute is selected with future knowledge.",
            },
            "normal_ranked_on_training": _rank(normal_reports, minimum_trades=25),
            "expiry_early_ranked_on_training": _rank(
                early_expiry_reports, minimum_trades=8,
            ),
            "expiry_closing_ranked_on_training": _rank(
                closing_reports, minimum_trades=5, limit=20,
            ),
        }
        if options["compact"]:
            report = {
                "coverage": report["coverage"],
                "risk_model": report["risk_model"],
                "scenario_counts": report["scenario_counts"],
                "candidate_counts": report["candidate_counts"],
                "expiry_opportunity_upper_bound": {
                    **report["expiry_opportunity_upper_bound"],
                    "top_sessions": report["expiry_opportunity_upper_bound"]["top_sessions"][:5],
                },
                "normal_top": [
                    _compact_report(row)
                    for row in report["normal_ranked_on_training"][:5]
                ],
                "expiry_early_top": [
                    _compact_report(row)
                    for row in report["expiry_early_ranked_on_training"][:5]
                ],
                "expiry_closing_top": [
                    _compact_report(row)
                    for row in report["expiry_closing_ranked_on_training"][:10]
                ],
            }
        self.stdout.write(json.dumps(report, indent=2, default=str))