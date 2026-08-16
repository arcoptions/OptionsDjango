"""Breadth as a filter on the strategy we actually run.

The statistical test asks whether constituents carry information at all. This
asks the only question that pays: at the 64 moments the shipped strategy fired,
was the index move being made by the whole market or by a handful of stocks, and
does that separate the winners from the losers?

Sixty-four trades cannot settle anything on its own, so the split is reported
with its trade counts in plain sight and treated as a lead to be confirmed on a
larger sample, not as a filter to switch on.
"""
import os
import sys
from datetime import datetime

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config, sized_ledger
from options_tracker.strategy_backtest import backtest_strategy

import breadth as B
import common as C

WINDOW = 15
OPEN_MINUTE = 555


def signal_minute(stamp):
    moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
    return moment.hour * 60 + moment.minute - OPEN_MINUTE


def main():
    stock_dates = set(B.stock_dates())
    dates = [d for d in C.session_dates() if d in stock_dates]
    symbols, weights, explained = B.fit_weights(dates)
    if weights is None:
        print("weight fit failed")
        return
    sectors = np.array([B.SECTORS.get(s, "OTHER") for s in symbols])
    print(f"weights explain {100 * explained:.1f}% of index minute variance, "
          f"{len(dates)} usable sessions\n")

    trades = backtest_strategy("NIFTY", 1, nifty_trail_config())
    ledger, _skipped, _drawdown = sized_ledger(trades)

    cache = {}
    rows = []
    for trade in ledger:
        date = trade["date"]
        if date not in stock_dates:
            continue
        if date not in cache:
            try:
                names, stock_r, index_r, _volume = B.session_matrix(date)
            except (OSError, KeyError):
                cache[date] = None
            else:
                cache[date] = (None if names != symbols else
                               B.features(stock_r, index_r, weights, sectors, WINDOW))
        table = cache[date]
        if table is None:
            continue
        minute = signal_minute(trade["signal_at"])
        if not 0 <= minute < len(table["participation"]):
            continue
        direction = 1.0 if trade.get("option_type") == "CALL" else -1.0
        rows.append({
            "won": trade["net_pnl"] > 0,
            "net": trade["net_pnl"],
            # Signed so a call and a put are directly comparable: how much index
            # weight was moving the way the trade needed it to.
            "aligned": (table["participation"][minute] if direction > 0
                        else 1.0 - table["participation"][minute]),
            "concentration": table["concentration"][minute],
            "dispersion": table["dispersion"][minute],
            "sector_gap": abs(table["sector_gap"][minute]),
        })

    if not rows:
        print("no trades landed on sessions with constituent data")
        return
    won = [r for r in rows if r["won"]]
    lost = [r for r in rows if not r["won"]]
    print(f"{len(rows)} trades matched   {len(won)} winners  {len(lost)} losers\n")
    print(f"  {'feature':<16}{'winners':>10}{'losers':>10}{'gap':>9}")
    for name in ("aligned", "concentration", "dispersion", "sector_gap"):
        a = np.mean([r[name] for r in won])
        b = np.mean([r[name] for r in lost])
        print(f"  {name:<16}{a:>10.3f}{b:>10.3f}{a - b:>+9.3f}")

    print(f"\n  {'filter':<30}{'n':>5}{'win%':>8}{'net Rs':>11}")
    for name, test in (
        ("no filter", lambda r: True),
        ("aligned weight > 0.40", lambda r: r["aligned"] > 0.40),
        ("aligned weight > 0.50", lambda r: r["aligned"] > 0.50),
        ("concentration < median", None),
        ("dispersion > median", None),
    ):
        if test is None:
            key = "concentration" if "concentration" in name else "dispersion"
            middle = np.median([r[key] for r in rows])
            test = ((lambda r, k=key, m=middle: r[k] < m) if key == "concentration"
                    else (lambda r, k=key, m=middle: r[k] > m))
        subset = [r for r in rows if test(r)]
        if not subset:
            print(f"  {name:<30}{0:>5}")
            continue
        wins = sum(1 for r in subset if r["won"])
        print(f"  {name:<30}{len(subset):>5}{100 * wins / len(subset):>8.1f}"
              f"{sum(r['net'] for r in subset):>11,.0f}")


if __name__ == "__main__":
    main()
