import json
from datetime import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from options_tracker.capital_pnl import (
    NIFTY_LOT_SIZE,
    cash_ledger,
    cash_metrics,
)
from options_tracker.dynamic_strategy import available_session_dates, nifty_expiry_sessions
from options_tracker.dual_strategy_research import (
    ExitScenario,
    collect_expiry_variant_candidates,
    expiry_closing_variants,
    simulate_exit_scenario,
)
from options_tracker.management.commands.research_normal_day_strategy import (
    normal_day_config,
)
from options_tracker.strategy_backtest import backtest_strategy


VALIDATION_START = "2026-04-29"


def _normal_raw_trades():
    session_dates = available_session_dates()
    completed_dates = [day for day in session_dates if day < timezone.localdate()]
    expiry_dates = nifty_expiry_sessions(completed_dates)
    eligible_dates = {day.isoformat() for day in completed_dates if day not in expiry_dates}
    trades = backtest_strategy("NIFTY", 1, normal_day_config())
    return [
        {
            "date": trade["date"],
            "strategy": "NORMAL_DAY",
            "entry_at": trade["signal_at"],
            "exit_at": trade["exit_at"],
            "option_type": trade["option_type"],
            "strike": trade["strike"],
            "entry": trade["entry"],
            "unit_exit": round(
                trade["entry"]
                + trade["realized_r"] * (trade["entry"] - trade["stop_loss"]),
                4,
            ),
            "unit_stop_risk": trade["entry"] - trade["stop_loss"],
            "outcome": trade["outcome"],
        }
        for trade in trades
        if trade["date"] in eligible_dates
    ]


def _expiry_raw_trades():
    variant = "P20_50_ATM_OTM2"
    config = expiry_closing_variants()[variant]
    scenario = ExitScenario("SL75_T5X", 75, "MULTIPLE", 5, 0.2)
    eligible = [
        candidate
        for candidate in collect_expiry_variant_candidates(config)
        if time(14, 30) <= candidate["timestamp"].time() < time(14, 45)
    ]
    trades = [
        trade
        for candidate in eligible
        if (trade := simulate_exit_scenario(candidate, config, scenario, True))
    ]
    return [
        {
            "date": trade["date"],
            "strategy": "EXPIRY_DAY",
            "entry_at": trade["entry_at"],
            "exit_at": trade["exit_at"],
            "option_type": trade["option_type"],
            "strike": trade["strike"],
            "entry": trade["entry"],
            "unit_exit": round(
                trade["entry"] * (1 + trade["premium_return_percent"] / 100),
                4,
            ),
            "unit_stop_risk": trade["entry"] * 0.75,
            "outcome": trade["outcome"],
        }
        for trade in trades
    ]


def _report(
    raw_trades,
    capital,
    max_position,
    lot_size,
    policy,
    risk_cap=None,
    fixed_lots=None,
):
    trades, skipped = cash_ledger(
        raw_trades, max_position, lot_size, policy, risk_cap, fixed_lots,
    )
    training = [trade for trade in trades if trade["date"] < VALIDATION_START]
    validation = [trade for trade in trades if trade["date"] >= VALIDATION_START]
    return {
        "sizing": {
            "policy": policy,
            "risk_cap": risk_cap,
            "fixed_lots": fixed_lots,
        },
        "all": cash_metrics(trades, capital, len(raw_trades)),
        "training": cash_metrics(
            training,
            capital,
            sum(trade["date"] < VALIDATION_START for trade in raw_trades),
        ),
        "validation": cash_metrics(
            validation,
            capital,
            sum(trade["date"] >= VALIDATION_START for trade in raw_trades),
        ),
        "skipped": [
            {
                "date": trade["date"],
                "strategy": trade["strategy"],
                "entry": trade["entry"],
                "minimum_lot_cost": round(trade["entry"] * lot_size, 2),
            }
            for trade in skipped
        ],
        "trades": trades,
    }


class Command(BaseCommand):
    help = "Convert the two paper strategies into a lot-sized cash P&L report."

    def add_arguments(self, parser):
        parser.add_argument("--capital", type=float, default=100000)
        parser.add_argument("--max-position", type=float, default=10000)
        parser.add_argument("--lot-size", type=int, default=NIFTY_LOT_SIZE)
        parser.add_argument("--fixed-lots", type=int)
        parser.add_argument("--compact", action="store_true")

    def handle(self, *args, **options):
        capital = options["capital"]
        max_position = options["max_position"]
        lot_size = options["lot_size"]
        normal_raw = _normal_raw_trades()
        expiry_raw = _expiry_raw_trades()
        if options["fixed_lots"]:
            fixed_lots = max(options["fixed_lots"], 1)
            normal_fixed = _report(
                normal_raw,
                capital,
                max_position,
                lot_size,
                "FIXED_LOTS",
                fixed_lots=fixed_lots,
            )
            expiry_fixed = _report(
                expiry_raw,
                capital,
                max_position,
                lot_size,
                "FIXED_LOTS",
                fixed_lots=fixed_lots,
            )
            combined_trades = [*normal_fixed["trades"], *expiry_fixed["trades"]]
            report = {
                "assumptions": {
                    "starting_capital": capital,
                    "fixed_lots": fixed_lots,
                    "lot_size": lot_size,
                    "quantity_per_trade": fixed_lots * lot_size,
                    "position_cap_applied": False,
                    "charges_and_slippage": "same estimates as the capital-capped report",
                },
                "normal_day": {
                    key: value for key, value in normal_fixed.items() if key != "trades"
                },
                "expiry_day": {
                    key: value for key, value in expiry_fixed.items() if key != "trades"
                },
                "combined": cash_metrics(
                    combined_trades,
                    capital,
                    len(normal_raw) + len(expiry_raw),
                ),
            }
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        normal = _report(
            normal_raw, capital, max_position, lot_size, "MAX_BUDGET",
        )
        expiry_one_lot = _report(
            expiry_raw, capital, max_position, lot_size, "ONE_LOT",
        )
        expiry_max_budget = _report(
            expiry_raw, capital, max_position, lot_size, "MAX_BUDGET",
        )
        expiry_risk_cap = _report(
            expiry_raw, capital, max_position, lot_size, "RISK_CAP", 1000,
        )
        recommended_trades = [
            *normal["trades"],
            *expiry_one_lot["trades"],
        ]
        report = {
            "assumptions": {
                "starting_capital": capital,
                "maximum_position": max_position,
                "nifty_lot_size": lot_size,
                "normal_sizing": "maximum whole lots under the position cap",
                "expiry_recommended_sizing": "one lot because a 75% stop makes full ₹10,000 deployment high risk",
                "entry_exit_slippage": "already included by the strategy simulators",
                "estimated_costs": {
                    "brokerage": "₹20 per executed order, two orders per trade",
                    "stt": "0.1% through March 2026; 0.15% from April 2026",
                    "exchange_transaction": "0.03503% of turnover",
                    "sebi": "₹10 per crore of turnover",
                    "gst": "18% of brokerage, exchange, and SEBI charges",
                    "stamp_duty": "0.003% of buy premium",
                },
            },
            "normal_max_budget": {
                key: value for key, value in normal.items() if key != "trades"
            },
            "expiry_one_lot": {
                key: value for key, value in expiry_one_lot.items() if key != "trades"
            },
            "expiry_max_budget_stress": {
                key: value for key, value in expiry_max_budget.items() if key != "trades"
            },
            "expiry_₹1000_risk_cap": {
                key: value for key, value in expiry_risk_cap.items() if key != "trades"
            },
            "combined_normal_max_plus_expiry_one_lot": cash_metrics(
                recommended_trades,
                capital,
                len(normal_raw) + len(expiry_raw),
            ),
        }
        if options["compact"]:
            report = {
                "assumptions": report["assumptions"],
                "normal_max_budget": {
                    "all": report["normal_max_budget"]["all"],
                    "training": report["normal_max_budget"]["training"],
                    "validation": report["normal_max_budget"]["validation"],
                    "skipped": report["normal_max_budget"]["skipped"],
                },
                "expiry_one_lot": {
                    "all": report["expiry_one_lot"]["all"],
                    "training": report["expiry_one_lot"]["training"],
                    "validation": report["expiry_one_lot"]["validation"],
                    "skipped": report["expiry_one_lot"]["skipped"],
                },
                "expiry_max_budget_stress": {
                    "all": report["expiry_max_budget_stress"]["all"],
                    "validation": report["expiry_max_budget_stress"]["validation"],
                },
                "expiry_₹1000_risk_cap": {
                    "all": report["expiry_₹1000_risk_cap"]["all"],
                    "validation": report["expiry_₹1000_risk_cap"]["validation"],
                    "skipped": report["expiry_₹1000_risk_cap"]["skipped"],
                },
                "combined_normal_max_plus_expiry_one_lot": report[
                    "combined_normal_max_plus_expiry_one_lot"
                ],
            }
        self.stdout.write(json.dumps(report, indent=2, default=str))