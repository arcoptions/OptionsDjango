"""Three questions the strategy table raises but does not answer.

  1. Why does the day-clustered t-stat on the baseline read +0.94 when the mean
     net is 0.773? Either most days are quietly positive and a few are ruinous,
     or a handful of freak days drag the day-average up. The answer changes what
     the t column is worth.

  2. The winners exist -- 0.62% of trades come back at 2x or better. Were they
     recognisable at entry? Condition on the OUTCOME and look back at what was
     observable BEFORE the move. If the doublers look like everything else on
     every measurable dimension, no entry filter can exist, and that is a
     stronger statement than "the fourteen I tried failed".

  3. Buying loses ~22% per two-day trade. The other side of every one of those
     trades is a seller. Does the mirror actually pay, and at what tail?
"""
import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from option_strategy import build, simulate, TICK, TAX  # noqa: E402


def skew_check(frame, net):
    print(f"\n{'=' * 92}\n1. WHY THE DAY t-STAT AND THE MEAN DISAGREE")
    table = pd.DataFrame({"net": net, "day": frame["ts"].dt.date}).dropna()
    daily = table.groupby("day")["net"].agg(["mean", "median", "count"])
    print(f"  {len(daily)} trading days, {len(table):,} trades")
    print(f"  mean over all trades        {table['net'].mean():.3f}")
    print(f"  mean of the daily means     {daily['mean'].mean():.3f}")
    print(f"  MEDIAN of the daily means   {daily['mean'].median():.3f}")
    print(f"  days whose mean beats 1.0   {(daily['mean'] > 1).sum()}/{len(daily)} "
          f"({(daily['mean'] > 1).mean() * 100:.1f}%)")
    top = daily["mean"].nlargest(5)
    rest = daily["mean"].drop(top.index)
    print(f"\n  Drop the five best days and the day-average falls "
          f"{daily['mean'].mean():.3f} -> {rest.mean():.3f}")
    print("  Those five days:")
    for day, value in top.items():
        print(f"    {day}  mean {value:6.2f}x on {int(daily.loc[day, 'count']):,} trades")
    print("\n  So the positive t is five days out of a hundred and thirty, not a")
    print("  quiet edge. The equal-weighted-by-day statistic is the wrong lens on a")
    print("  distribution this skewed; the median day, at "
          f"{daily['mean'].median():.3f}x, is the honest summary.")


def hindsight(frame, net, gross):
    print(f"\n{'=' * 92}\n2. WERE THE WINNERS RECOGNISABLE BEFORE THEY RAN?")
    live = np.isfinite(net)
    sub = frame[live].copy()
    sub["net"] = net[live]
    sub["gross"] = gross[live]
    winners = sub[sub["gross"] >= 2.0]
    losers = sub[sub["gross"] < 2.0]
    print(f"  {len(winners):,} trades doubled ({len(winners) / len(sub) * 100:.2f}%), "
          f"{len(losers):,} did not\n")

    fields = [
        ("premium at entry (Rs)", "close"), ("moneyness %", "moneyness"),
        ("days to expiry", "dte"), ("implied vol", "iv"),
        ("IV rank %", "iv_rank"), ("open interest", "oi"),
        ("OI change over a day %", "oi_chg_25"), ("stock 1h return %", "ret_4"),
        ("stock 1d return %", "ret_25"), ("distance above EMA20 %", "above_ema20"),
        ("volume surge x", "vol_surge"),
    ]
    print(f"{'OBSERVABLE AT ENTRY':26s} {'doublers':>12} {'the rest':>12} {'separation':>12}")
    for label, column in fields:
        a, b = winners[column].dropna(), losers[column].dropna()
        if len(a) < 30 or len(b) < 30:
            continue
        # Cohen's d: how many standard deviations apart the two groups sit.
        pooled = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
        d = (a.median() - b.median()) / pooled if pooled else np.nan
        print(f"{label:26s} {a.median():12.2f} {b.median():12.2f} {d:11.2f} sd")

    print(f"\n  breakout at entry:  doublers {winners['breakout'].mean() * 100:5.1f}%   "
          f"the rest {losers['breakout'].mean() * 100:5.1f}%")
    print(f"  calls:              doublers {winners['option_type'].eq('CALL').mean() * 100:5.1f}%   "
          f"the rest {losers['option_type'].eq('CALL').mean() * 100:5.1f}%")

    print("\n  The one real separation is PREMIUM. Doublers start cheap. That is not")
    print("  an edge, it is arithmetic -- a 0.50 option needs 50 paise to double and")
    print("  a 50-rupee option needs fifty rupees. And 50 paise is ten ticks, so the")
    print("  spread you pay to get in is 10% of the position before anything happens.")

    print(f"\n  Cheap options, sliced (2-session hold, net of costs):")
    print(f"{'PREMIUM':>14} {'trades':>9} {'net avg':>9} {'win%':>7} {'>=2x%':>7} "
          f"{'tick cost':>10}")
    for low, high in [(0.05, 1), (1, 2), (2, 5), (5, 10), (10, 25), (25, 1e9)]:
        band = sub[sub["close"].between(low, high, inclusive="left")]
        if len(band) < 200:
            continue
        label = f"{low:g}-{high:g}" if high < 1e9 else f"{low:g}+"
        print(f"{label:>14} {len(band):9,} {band['net'].mean():9.3f} "
              f"{(band['net'] > 1).mean() * 100:6.1f}% "
              f"{(band['gross'] >= 2).mean() * 100:6.2f}% "
              f"{TICK / band['close'].median() * 100:9.1f}%")


def sell_side(frame, net_buy):
    print(f"\n{'=' * 92}\n3. THE MIRROR: WHAT THE SELLER OF THOSE OPTIONS MADE")
    live = np.isfinite(net_buy)
    entry = frame["open"].shift(-1).to_numpy()[live]
    exit_gross = net_buy[live]
    # Reconstruct the seller's P&L per rupee of premium sold. The seller
    # receives the bid (one tick below the buyer's ask) and buys back at the
    # ask, so the tick works against them too -- it is just much smaller
    # relative to the premium they collected.
    sold = entry - TICK
    bought_back = (exit_gross * (entry + TICK)) / (1 - TAX) + TICK
    profit = (sold - bought_back) * (1 - TAX)
    per_rupee = profit / entry
    print(f"  {live.sum():,} mirrored trades, 2-session hold, no target or stop")
    print(f"  seller's return per rupee of premium sold   {per_rupee.mean() * 100:+.2f}%")
    print(f"  seller wins                                  {(profit > 0).mean() * 100:.1f}% of the time")
    print(f"  worst single trade                           {per_rupee.min() * 100:+.1f}% of premium")
    tail = np.sort(per_rupee)[:max(1, int(len(per_rupee) * 0.01))]
    print(f"  average of the worst 1%                      {tail.mean() * 100:+.1f}% of premium")
    print(f"\n  The edge is real and it is the same 22% seen from the other side. But")
    print(f"  a worst-case of {per_rupee.min() * 100:.0f}% of premium on a naked short is the whole")
    print("  story: collecting Rs 3 a hundred times does not survive one Rs 300 gap,")
    print("  and margin on a naked stock-option short is roughly 15% of contract")
    print("  value, so the return on capital is nothing like the return on premium.")
    print("  This is a hedged-spread finding, not a permission slip to sell naked.")


def case_study(frame):
    """The actual biggest runs in the data -- the UNIONBANK-shaped trades."""
    print(f"\n{'=' * 92}\n4. THE BIGGEST RUNS THAT ACTUALLY HAPPENED")
    keys = ["symbol", "option_type", "strike", "cycle"]
    runs = []
    for key, series in frame.groupby(keys, sort=False):
        if len(series) < 20:
            continue
        low_price = series["close"].cummin()
        multiple = series["close"] / low_price
        best = multiple.max()
        if best < 3:
            continue
        peak = multiple.idxmax()
        trough = series.loc[:peak, "close"].idxmin()
        runs.append({
            "symbol": key[0], "type": key[1], "strike": key[2], "cycle": key[3],
            "from": series.loc[trough, "close"], "to": series.loc[peak, "close"],
            "multiple": best,
            "start": series.loc[trough, "ts"], "end": series.loc[peak, "ts"],
            "bars": series.index.get_loc(peak) - series.index.get_loc(trough)
                    if hasattr(series.index, "get_loc") else np.nan,
            "dte_at_low": series.loc[trough, "dte"],
            "stock_1h_before": series.loc[trough, "ret_4"],
            "moneyness_at_low": series.loc[trough, "moneyness"],
        })
    if not runs:
        print("  none found")
        return
    table = pd.DataFrame(runs).sort_values("multiple", ascending=False)
    print(f"  {len(table)} contract-cycles ran 3x or more off their own low "
          f"({len(table) / frame.groupby(keys).ngroups * 100:.1f}% of all contract-cycles)\n")
    print(f"{'SYMBOL':13s} {'TYPE':5s} {'STRIKE':>9} {'FROM':>8} {'TO':>9} {'X':>7} "
          f"{'DTE':>5} {'OTM%':>7} {'WHEN':>12}")
    for _, row in table.head(20).iterrows():
        print(f"{row['symbol']:13s} {row['type']:5s} {row['strike']:9.1f} "
              f"{row['from']:8.2f} {row['to']:9.2f} {row['multiple']:6.1f}x "
              f"{row['dte_at_low']:5.0f} {row['moneyness_at_low']:7.1f} "
              f"{row['start']:%d %b %Y}")
    print(f"\n  At the moment of the low, these looked like: median {table['dte_at_low'].median():.0f} "
          f"days to expiry, {table['moneyness_at_low'].median():.1f}% moneyness,")
    print(f"  and the stock had moved {table['stock_1h_before'].median():+.2f}% in the prior hour.")
    print("  Compare that with the population medians in section 2. They are the same")
    print("  numbers. The low is only visible as a low afterwards.")
    table.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "option_big_runs.csv"), index=False)


def main():
    frame = build()
    if frame.empty:
        print("no data")
        return
    result = simulate(frame, None, 0.0, None)     # plain 2-session hold
    net, gross = result["net"], result["gross"]
    skew_check(frame, net)
    hindsight(frame, net, gross)
    sell_side(frame, net)
    case_study(frame)


if __name__ == "__main__":
    main()
