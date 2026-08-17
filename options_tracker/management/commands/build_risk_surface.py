"""Measure what each risk knob is actually worth, and cache it for the dashboard.

The dashboard lets the account be re-tuned live. That is only defensible if the
consequence of a change is on screen next to the control, so this command
produces the evidence the panel shows.

Two artifacts, because the knobs split cleanly into two kinds.

**Sizing knobs** -- capital, risk per trade, cash fraction, lot cap -- do not
change which trades happen. They only change how big each one is, so
`sized_ledger` can replay the same trade list at any setting in microseconds.
Those need no sweep at all: the dashboard recomputes them exactly, in the
request. All this command has to do is dump the trade list.

**Strategy knobs** -- stop, trail, premium floor, volume ratio, spot move, trade
count, loss limit -- change which trades exist and how they end, so each value
needs a real backtest. Contracts are loaded once and shared across every run,
which is the only reason a sweep of this size is affordable: the database read
is most of the two minutes a single `report_trail_strategy` takes.

The sweep is one-at-a-time from the shipped config, not a grid. A grid over
seven parameters would be tens of thousands of runs and would mostly measure
overfitting; the panel's job is to answer "what does moving *this* cost?", which
is exactly a one-at-a-time question.

The result is committed so production never recomputes it. Re-run it when the
strategy config changes or the history is extended.
"""
import json
from dataclasses import replace
from pathlib import Path
from time import monotonic

from django.core.management.base import BaseCommand

from ...nifty_trail_strategy import (
    MAX_CASH_FRACTION,
    RISK_PER_TRADE,
    STARTING_CAPITAL,
    nifty_trail_config,
    sized_ledger,
)
from ...strategy_backtest import backtest_strategy, load_contract_rows

ARTIFACT = Path(__file__).resolve().parents[2] / "data" / "risk_surface.json"

# Values swept per parameter. Each list contains the shipped value, so the panel
# always has an exact anchor to compare against rather than an interpolation.
SWEEPS = {
    "stop_percent": [0.06, 0.08, 0.10, 0.125, 0.15, 0.20, 0.25, 0.30],
    "trail_gap_r": [0.3, 0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.5],
    "premium_min": [50, 75, 100, 125, 150, 200, 250],
    "volume_ratio": [1.0, 1.25, 1.5, 1.75, 2.0, 2.5],
    "minimum_spot_move_percent": [0.05, 0.10, 0.15, 0.20, 0.25],
    "max_trades_per_day": [1, 2, 3, 4, 5],
    "daily_loss_limit_r": [1.0, 1.5, 2.0, 3.0, 4.0],
}


def _summarise(trades):
    """The same figures `strategy_report` prints, at default sizing."""
    ledger, skipped, drawdown = sized_ledger(trades)
    if not ledger:
        return {
            "signals": len(trades), "trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
            "max_drawdown": 0.0, "return_percent": 0.0, "average_net_pnl": 0.0,
        }
    net = sum(row["net_pnl"] for row in ledger)
    wins = [row for row in ledger if row["net_pnl"] > 0]
    return {
        "signals": len(trades),
        "trades": len(ledger),
        "skipped": len(skipped),
        "net_pnl": round(net, 2),
        "win_rate": round(len(wins) / len(ledger) * 100, 1),
        "max_drawdown": drawdown,
        "return_percent": round(net / STARTING_CAPITAL * 100, 2),
        "average_net_pnl": round(net / len(ledger), 2),
    }


class Command(BaseCommand):
    help = "Sweep each strategy risk parameter and cache the measured result."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only", nargs="*", default=None,
            help="Sweep only these parameters (default: all).",
        )

    def handle(self, *args, **options):
        started = monotonic()
        base = nifty_trail_config()

        self.stdout.write("loading contracts once for the whole sweep...")
        contracts = load_contract_rows("NIFTY", 1)
        self.stdout.write(f"  {len(contracts)} contracts in {monotonic() - started:.1f}s")

        baseline_trades = backtest_strategy("NIFTY", 1, base, contracts=contracts)
        baseline = _summarise(baseline_trades)
        self.stdout.write(
            f"baseline: {baseline['trades']} trades, Rs {baseline['net_pnl']:,.0f}, "
            f"{baseline['win_rate']}% win"
        )

        wanted = options["only"] or list(SWEEPS)
        surface = {}
        for name in wanted:
            values = SWEEPS[name]
            shipped = getattr(base, name)
            points = []
            for value in values:
                mark = monotonic()
                if value == shipped:
                    result = dict(baseline)
                else:
                    trades = backtest_strategy(
                        "NIFTY", 1, replace(base, **{name: value}), contracts=contracts,
                    )
                    result = _summarise(trades)
                points.append({"value": value, "shipped": value == shipped, **result})
                self.stdout.write(
                    f"  {name}={value}: {result['trades']} trades, "
                    f"Rs {result['net_pnl']:,.0f}, {result['win_rate']}% win, "
                    f"DD Rs {result['max_drawdown']:,.0f}  ({monotonic() - mark:.1f}s)"
                )
            surface[name] = {"shipped": shipped, "points": points}

        # The trade list is what makes the sizing knobs exact rather than
        # interpolated. Only the fields `sized_ledger` reads are kept.
        ledger_input = [
            {
                "date": trade["date"],
                "signal_at": trade["signal_at"],
                "exit_at": trade["exit_at"],
                "entry": trade["entry"],
                "stop_loss": trade["stop_loss"],
                "realized_r": trade["realized_r"],
                "outcome": trade["outcome"],
                "option_type": trade["option_type"],
                "strike": trade["strike"],
            }
            for trade in baseline_trades
        ]

        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(json.dumps({
            "baseline": baseline,
            "baseline_sizing": {
                "capital": STARTING_CAPITAL,
                "risk_per_trade": RISK_PER_TRADE,
                "max_cash_fraction": MAX_CASH_FRACTION,
            },
            # Sessions *tested*, not sessions that produced a trade. The
            # dashboard quotes this as the size of the evidence, and on a
            # strategy that trades roughly one day in five the difference
            # between the two is a factor of six.
            "sessions": len({key[0] for key in contracts}),
            "sessions_with_trades": len({trade["date"] for trade in baseline_trades}),
            "surface": surface,
            "trades": ledger_input,
        }, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(
            f"wrote {ARTIFACT} in {monotonic() - started:.0f}s"
        ))
