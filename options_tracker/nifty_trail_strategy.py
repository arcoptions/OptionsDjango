"""NIFTY intraday option-buying strategy, trailing-exit variant.

Same entry logic as the normal-day research config -- the part that the
zero-skill control says carries real information -- with the fixed 1.25R target
replaced by a stop that trails 0.7R behind the running high, and the entry
premium floored at Rs 100.

Expiry days are NOT banned, and an earlier version of this docstring wrongly said
they were: StrategyConfig has no expiry field and never had one. What actually
removes almost all of them is the Rs 100 floor. Median ATM premium on an expiry
session is Rs 51 in the morning falling to Rs 19 by 14:30, against Rs 123 to
Rs 114 on a normal session, so most expiry signals are sub-Rs 100 and are filtered
out on cost grounds -- 15 of the 17 expiry-day trades the old Rs 50-250 band took
were below Rs 100. Two expiry trades cleared the floor in 246 sessions and both
won. That is the right mechanism to rely on: expiry-day directional buying really
is weaker (a momentum entry wins 34-52% there against 51-62% on normal days) but
the floor already prices that in, and it does so for a reason that survives a
bid-ask rather than by banning a date. See `research/clock.py`.

Measured on 2025-08-18 .. 2026-08-14 (246 sessions, 51 trades):

    win rate           66.7%
    net on Rs 1,00,000 +38,626  (+38.6%)
    premium points     466.1 captured, 9.14 per trade
    maximum drawdown   Rs 5,114 (5.1%)
    after a Rs 2 round-trip bid-ask, Rs 28,208 still stands -- 73% of the
    mid-price result, against 22% for the Rs 50-250 band this replaces

Two parameters were changed on 2026-08-16 after the whole grid was re-run
against one contract load (`research/finalise.py`):

  trail 0.5R -> 0.7R    Worth Rs 21,221 -> Rs 40,341 on the old band. The exit
                        was always the larger lever; see `research/exit_lab.py`.
  premium 50-250 -> 100+ The floor is the finding and the cap was noise. Sub-Rs
                        100 contracts are not losers -- 20 of them won 70% and
                        booked Rs 5,645 -- but they capture only 1.73 points a
                        trade, so a Rs 2 round-trip bid-ask takes more than the
                        whole edge. Above Rs 100 the same signal captures 9.14
                        points. The cap went because the bands either side of it
                        disagreed at random (Rs 100-200 Rs 31,651, Rs 100-250
                        Rs 24,291, Rs 100-300 Rs 32,723), which is what a
                        parameter fitted to noise looks like; and because the
                        7 trades above Rs 250 captured 26 points each. Rs 1,000
                        is a sentinel, not a ceiling: the dearest contract ever
                        bought cost Rs 333.

The band is also the only one of eight whose second half beat its first
(Rs 13,605 -> Rs 16,742). Every other band roughly halved, the shipped
Rs 50-250 worst of all (Rs 24,232 -> Rs 8,473). The most recent 123 sessions
alone: 42 trades, 69.0% win, Rs 29,646, maximum drawdown Rs 4,538.

Expect about five trades a month and expect that to be lumpy -- March 2026 gave
17 and August 2025 through October 2025 gave none at all. A quiet month is the
strategy working as measured, not a fault.

Things that were tested and did NOT work, so they are deliberately absent:

  * OI walls as support/resistance. Buying CE as spot falls onto the largest
    put-OI strike wins 47-52% with negative mean points -- slightly worse than
    a random entry. Walls get broken more often than they hold.
  * Expiry-day buying as a strategy of its own. Expiry sessions move 186 median
    points against 179 on normal days -- no bigger -- while theta guts the
    position: the median worst case for an option bought at 13:00 on expiry is
    0.27x entry. A 1.5x-before-0.6x race wins 39.5% on expiry against 58.5% on a
    normal day. This is why the Rs 100 floor thinning expiry days is welcome; it
    is not a reason to ban the date outright.
  * The last hour as a trigger in its own right. Losing: -Rs 48,974 entering at
    14:30, -Rs 20,362 at 15:00. The mechanism is the 15:20 square-off. 18% of
    14:30-15:05 entries are still open at the bell and get closed flat, against
    0% in every earlier window, so the trail -- which is where this strategy's
    whole edge lives -- never gets to run. Premium is also barely cheaper late
    (Rs 114 median against Rs 123 at the open), so the extra leverage that would
    justify the worse odds is not there either.
  * Wider stops. At a fixed 2% risk budget a wider stop forces a smaller
    position, so more points captured means less money: 10% stop makes
    Rs 19,244, 15% makes Rs 9,468, 20% makes Rs 3,933, 30% loses Rs 4,371.
  * A trend gate on the 20 EMA, RSI in any form, opening-range breakouts,
    straddles and strangles, and eleven other published setups. See
    `research/TEST_REGISTER.md`.

The profit edge is weaker evidence than the win-rate edge. Bid-ask is the
single largest sensitivity in the whole study and the only quantity that has
been modelled rather than measured -- which is why the band was chosen for how
it degrades under a spread rather than for its headline rupees.
"""
from dataclasses import replace
from datetime import time
from math import floor

from .capital_pnl import NIFTY_LOT_SIZE, estimate_option_charges
from .strategy_backtest import backtest_strategy, nifty_put_strategy_config


STARTING_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.02
MAX_CASH_FRACTION = 0.40

# Not a ceiling anyone expects to hit: the dearest contract bought in 246
# sessions cost Rs 333. It exists because the config field is required.
NO_PREMIUM_CAP = 1_000


def nifty_trail_config():
    return replace(
        nifty_put_strategy_config(max_trades_per_day=3),
        option_types=("CALL", "PUT"),
        minimum_spot_move_percent=0.15,
        volume_ratio=1.5,
        stop_percent=0.10,
        trail_gap_r=0.7,
        premium_min=100,
        premium_max=NO_PREMIUM_CAP,
        end_time=time(15, 9),
        # One continuous window. The original three-window split (morning,
        # afternoon, closing) skipped 11:00-13:30 entirely; letting the signal
        # fire there adds 16 trades, more profit and *less* drawdown, and the
        # expanded set survives the same random-entry control (p = 0.006).
        entry_windows=((time(9, 30), time(15, 9)),),
    )


def sized_ledger(
    trades,
    starting_capital=STARTING_CAPITAL,
    risk_per_trade=RISK_PER_TRADE,
    max_cash_fraction=MAX_CASH_FRACTION,
    lot_size=NIFTY_LOT_SIZE,
):
    """Compound the account trade by trade, sizing off equity actually on hand.

    Lots are the smaller of what the risk budget allows and what the cash
    fraction allows, so the ledger can never deploy more than the account holds.
    """
    equity = peak = starting_capital
    drawdown = 0.0
    ledger = []
    skipped = []
    for trade in sorted(trades, key=lambda item: item["signal_at"]):
        entry = trade["entry"]
        unit_risk = entry - trade["stop_loss"]
        if unit_risk <= 0:
            skipped.append(trade)
            continue
        risk_lots = floor(equity * risk_per_trade / (unit_risk * lot_size))
        cash_lots = floor(equity * max_cash_fraction / (entry * lot_size))
        lots = max(0, min(risk_lots, cash_lots))
        if not lots:
            skipped.append(trade)
            continue
        quantity = lots * lot_size
        exit_price = entry + trade["realized_r"] * unit_risk
        charges = estimate_option_charges(
            entry, max(exit_price, 0), quantity, trade["date"]
        )
        gross = (exit_price - entry) * quantity
        net = gross - charges
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        ledger.append(
            {
                **trade,
                "lots": lots,
                "quantity": quantity,
                "deployed": round(entry * quantity, 2),
                "stop_risk": round(unit_risk * quantity, 2),
                "gross_pnl": round(gross, 2),
                "charges": charges,
                "net_pnl": round(net, 2),
                "equity": round(equity, 2),
            }
        )
    return ledger, skipped, round(drawdown, 2)


def strategy_report(starting_capital=STARTING_CAPITAL, **sizing):
    trades = backtest_strategy("NIFTY", 1, nifty_trail_config())
    ledger, skipped, drawdown = sized_ledger(
        trades, starting_capital=starting_capital, **sizing
    )
    if not ledger:
        return {"executed_trades": 0, "signals": len(trades)}
    wins = [row for row in ledger if row["net_pnl"] > 0]
    net = sum(row["net_pnl"] for row in ledger)
    streak = longest_loss_streak = 0
    for row in ledger:
        streak = streak + 1 if row["net_pnl"] <= 0 else 0
        longest_loss_streak = max(longest_loss_streak, streak)
    return {
        "signals": len(trades),
        "executed_trades": len(ledger),
        "skipped_signals": len(skipped),
        "win_rate": round(len(wins) / len(ledger) * 100, 1),
        "net_pnl": round(net, 2),
        "starting_capital": starting_capital,
        "ending_capital": round(starting_capital + net, 2),
        "return_percent": round(net / starting_capital * 100, 2),
        "maximum_drawdown": drawdown,
        "maximum_drawdown_percent": round(drawdown / starting_capital * 100, 2),
        "maximum_deployed": max(row["deployed"] for row in ledger),
        "maximum_stop_risk": max(row["stop_risk"] for row in ledger),
        "longest_losing_streak": longest_loss_streak,
        "average_net_pnl": round(net / len(ledger), 2),
        "best_trade": round(max(row["net_pnl"] for row in ledger), 2),
        "worst_trade": round(min(row["net_pnl"] for row in ledger), 2),
    }
