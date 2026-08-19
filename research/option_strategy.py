"""Can any rule beat simply not buying the option?

`option_moves.py` established the base case: a stock option bought at a random
bar and held two days comes back worth 0.78x. Decay is the house edge and the
house is the seller. So the bar a strategy has to clear is not "does it win
sometimes" -- it is EXPECTANCY ABOVE 1.0 NET OF THE TICK.

Three things here are deliberate, because each is a way a backtest normally
flatters itself:

  The path is walked, not read off a max.  A target and a stop are checked bar
  by bar in the order the market printed them. Where a single bar's high and low
  would both trigger, the STOP is taken first -- a 15-minute bar cannot tell you
  which came first, and assuming the good one is how backtests lie.

  Costs are charged at the tick.  One full 5-paise tick crossed going in, another
  coming out, plus 0.28% of turnover in taxes. On a 70-paisa option that tick is
  7% a side, which is why cheap options look wonderful in a spreadsheet and
  terrible in a statement.

  Significance is clustered by day.  Overlapping entries on the same name in the
  same hour are one bet wearing many hats. A t-stat across 300,000 such trades
  is meaningless; the one reported here is computed across trading days, which
  is roughly the number of independent decisions actually made.
"""
import datetime as dt
import os
import sys

import django
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")
django.setup()

from option_moves import load  # noqa: E402
from options_tracker.models import StockEquityCandle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

TICK = 0.05
TAX = 0.0028
MAX_BARS = 50            # two sessions
CHUNK = 40_000
KEYS = ["symbol", "option_type", "strike", "cycle"]


def equity_features(symbols):
    """Stock-side context for every 15-minute bar, computed causally."""
    rows = StockEquityCandle.objects.filter(
        symbol__in=list(symbols), interval_minutes=15
    ).values_list("symbol", "timestamp", "open", "high", "low", "close", "volume")
    frame = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].astype(float)
    frame = frame.sort_values(["symbol", "ts"])

    group = frame.groupby("symbol", sort=False)
    frame["ret_4"] = group["close"].pct_change(4) * 100
    frame["ret_25"] = group["close"].pct_change(25) * 100
    frame["ema20"] = group["close"].transform(lambda s: s.ewm(span=20, adjust=False).mean())
    frame["ema50"] = group["close"].transform(lambda s: s.ewm(span=50, adjust=False).mean())
    # shift(1) on every window that would otherwise end on the bar being judged.
    frame["hi25"] = group["high"].transform(lambda s: s.rolling(25, min_periods=10).max().shift(1))
    frame["lo25"] = group["low"].transform(lambda s: s.rolling(25, min_periods=10).min().shift(1))
    frame["volume_ma"] = group["volume"].transform(
        lambda s: s.rolling(25, min_periods=10).mean().shift(1)
    )
    frame["vol_surge"] = frame["volume"] / frame["volume_ma"].replace(0, np.nan)
    frame["above_ema20"] = (frame["close"] / frame["ema20"] - 1) * 100
    frame["breakout"] = frame["close"] > frame["hi25"]
    frame["breakdown"] = frame["close"] < frame["lo25"]
    keep = ["symbol", "ts", "ret_4", "ret_25", "above_ema20", "vol_surge",
            "breakout", "breakdown", "ema20", "ema50"]
    return frame[keep]


def option_features(frame):
    """Option-side context, again strictly backward-looking."""
    frame = frame.sort_values(KEYS + ["ts"]).copy()
    group = frame.groupby(KEYS, sort=False)
    frame["oi_chg_25"] = group["oi"].pct_change(25) * 100
    frame["prem_chg_4"] = group["close"].pct_change(4) * 100
    frame["iv_rank"] = group["iv"].transform(
        lambda s: s.rolling(200, min_periods=50).rank(pct=True) * 100
    )
    return frame


def build():
    options = load()
    if options.empty:
        return options
    options = option_features(options)
    stock = equity_features(set(options["symbol"].unique()))
    merged = options.merge(stock, on=["symbol", "ts"], how="inner")
    return merged.sort_values(KEYS + ["ts"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# the exit simulator, vectorised over every bar at once


def simulate(frame, target, stop, trail=None, max_bars=MAX_BARS):
    """Outcome of entering at EVERY bar's next open, walked forward bar by bar.

    Returns arrays aligned to `frame`: net multiple, gross multiple, bars held,
    and why the trade ended. Doing the whole frame in one pass means each signal
    below is just a boolean mask over a result that is already computed, rather
    than its own re-run of the walk.
    """
    n = len(frame)
    gid = frame.groupby(KEYS, sort=False).ngroup().to_numpy()

    def pad(values, fill):
        return np.concatenate([values, np.full(max_bars + 1, fill, dtype=values.dtype)])

    high_all = pad(frame["high"].to_numpy(float), np.nan)
    low_all = pad(frame["low"].to_numpy(float), np.nan)
    close_all = pad(frame["close"].to_numpy(float), np.nan)
    open_all = pad(frame["open"].to_numpy(float), np.nan)
    gid_all = pad(gid, -1)

    net = np.full(n, np.nan)
    gross = np.full(n, np.nan)
    held = np.zeros(n, dtype=np.int16)
    reason = np.zeros(n, dtype=np.int8)      # 0 time, 1 target, 2 stop
    entry_out = np.full(n, np.nan)

    for start in range(0, n, CHUNK):
        end = min(start + CHUNK, n)
        rows = slice(start + 1, end + 1)     # windows begin at the bar AFTER the signal
        highs = sliding_window_view(high_all, max_bars)[rows]
        lows = sliding_window_view(low_all, max_bars)[rows]
        closes = sliding_window_view(close_all, max_bars)[rows]
        opens = sliding_window_view(open_all, max_bars)[rows]
        groups = sliding_window_view(gid_all, max_bars)[rows]

        here = gid[start:end]
        same = groups == here[:, None]       # a contiguous prefix, since sorted by contract
        entry = np.where(same[:, 0], opens[:, 0], np.nan)
        alive = np.isfinite(entry) & (entry > 0)
        if not alive.any():
            continue

        stop_level = np.repeat((entry * stop)[:, None], max_bars, axis=1)
        if trail is not None:
            # Peak so far, including the entry price itself, so a trade that
            # never rises cannot trail below its own fixed stop.
            running = np.where(same, highs, -np.inf)
            peak = np.maximum(np.maximum.accumulate(running, axis=1), entry[:, None])
            stop_level = np.maximum(stop_level, peak * trail)

        hit_stop = same & (lows <= stop_level)
        if target:
            hit_target = same & (highs >= (entry * target)[:, None])
        else:
            hit_target = np.zeros_like(hit_stop)

        any_stop, any_target = hit_stop.any(axis=1), hit_target.any(axis=1)
        first_stop = np.argmax(hit_stop, axis=1)
        first_target = np.argmax(hit_target, axis=1)
        last = same.sum(axis=1) - 1          # final in-contract bar inside the window

        # Stop wins ties: inside one bar we cannot know which price came first.
        take_stop = any_stop & (~any_target | (first_stop <= first_target))
        take_target = any_target & ~take_stop
        take_time = ~take_stop & ~take_target & (last >= 0)

        rows_index = np.arange(end - start)
        price = np.where(take_time, closes[rows_index, np.clip(last, 0, None)], np.nan)
        price = np.where(take_stop, stop_level[rows_index, np.clip(first_stop, 0, None)], price)
        if target:
            price = np.where(take_target, entry * target, price)

        bars = np.where(take_stop, first_stop + 1,
                        np.where(take_target, first_target + 1, last + 1))
        keep = alive & (take_stop | take_target | take_time) & np.isfinite(price)

        paid = entry + TICK
        got = np.clip(price - TICK, 0, None)
        net[start:end] = np.where(keep, got * (1 - TAX) / paid, np.nan)
        gross[start:end] = np.where(keep, price / entry, np.nan)
        held[start:end] = np.where(keep, bars, 0)
        reason[start:end] = np.where(take_stop, 2, np.where(take_target, 1, 0))
        entry_out[start:end] = np.where(keep, entry, np.nan)

    return {"net": net, "gross": gross, "bars": held, "reason": reason, "entry": entry_out}


def day_t_stat(net, days):
    """t across trading days, not across overlapping trades."""
    frame = pd.DataFrame({"net": net, "day": days}).dropna()
    if frame.empty:
        return np.nan, 0
    daily = frame.groupby("day")["net"].mean() - 1
    if len(daily) < 3 or daily.std(ddof=1) == 0:
        return np.nan, len(daily)
    return daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))), len(daily)


def main():
    print("building the option+stock panel...")
    frame = build()
    if frame.empty:
        print("no data yet")
        return
    print(f"{len(frame):,} contract-bars with stock context, {frame['symbol'].nunique()} symbols, "
          f"{frame['ts'].min():%d %b %Y} -> {frame['ts'].max():%d %b %Y}")

    days = frame["ts"].dt.date.to_numpy()
    half = frame["ts"].median()
    calls = frame["option_type"].eq("CALL")
    puts = frame["option_type"].eq("PUT")
    liquid = frame["volume"].gt(0) & frame["oi"].gt(0)

    signals = {
        "baseline: any liquid bar": liquid,
        "call, stock 25-bar breakout": calls & liquid & frame["breakout"].fillna(False),
        "call, breakout + volume surge": calls & liquid & frame["breakout"].fillna(False)
                                         & frame["vol_surge"].gt(1.5),
        "call, stock +1% in an hour": calls & liquid & frame["ret_4"].gt(1.0),
        "call, stock +3% in a day": calls & liquid & frame["ret_25"].gt(3.0),
        "call, OI +20% over a day": calls & liquid & frame["oi_chg_25"].gt(20),
        "call, OI up + premium up": calls & liquid & frame["oi_chg_25"].gt(20)
                                    & frame["prem_chg_4"].gt(0),
        "call, low IV rank + breakout": calls & liquid & frame["iv_rank"].lt(30)
                                        & frame["breakout"].fillna(False),
        "call, OTM 2-6% + momentum": calls & liquid & frame["moneyness"].between(-6, -2)
                                     & frame["ret_4"].gt(0.5),
        "call, under Rs 3 + momentum": calls & liquid & frame["close"].lt(3)
                                       & frame["ret_4"].gt(0.5),
        "call, 14+ DTE + breakout": calls & liquid & frame["dte"].gt(14)
                                    & frame["breakout"].fillna(False),
        "call, EMA20 pullback in trend": calls & liquid & frame["ema20"].gt(frame["ema50"])
                                         & frame["above_ema20"].between(-1, 0.2),
        "put, stock 25-bar breakdown": puts & liquid & frame["breakdown"].fillna(False),
        "put, stock -1% in an hour": puts & liquid & frame["ret_4"].lt(-1.0),
    }
    signals = {name: mask.fillna(False).to_numpy() for name, mask in signals.items()}

    exits = [
        ("hold 2 sessions", None, 0.0, None),
        ("target 1.5x, stop 0.6x", 1.5, 0.60, None),
        ("target 2x, stop 0.6x", 2.0, 0.60, None),
        ("target 3x, stop 0.7x", 3.0, 0.70, None),
        ("trail 30% off peak", None, 0.50, 0.70),
    ]

    everything, rows = {}, []
    for exit_name, target, stop, trail in exits:
        result = simulate(frame, target, stop, trail)
        net_all, reason_all = result["net"], result["reason"]
        print(f"\n{'=' * 116}\nEXIT: {exit_name}   (max hold 2 sessions, tick + tax charged)")
        print(f"{'SIGNAL':31s} {'trades':>8} {'net avg':>8} {'median':>8} {'win%':>7} "
              f"{'>=2x':>7} {'hit T':>7} {'hit S':>7} {'bars':>6} {'day t':>7}")
        for signal_name, mask in signals.items():
            taken = mask & np.isfinite(net_all)
            if taken.sum() < 50:
                continue
            net = net_all[taken]
            t_stat, n_days = day_t_stat(net, days[taken])
            print(f"{signal_name:31s} {taken.sum():8,} {net.mean():8.3f} "
                  f"{np.median(net):8.3f} {(net > 1).mean() * 100:6.1f}% "
                  f"{(net >= 2).mean() * 100:6.2f}% "
                  f"{(reason_all[taken] == 1).mean() * 100:6.1f}% "
                  f"{(reason_all[taken] == 2).mean() * 100:6.1f}% "
                  f"{result['bars'][taken].mean():6.1f} {t_stat:7.2f}")
            everything[(exit_name, signal_name)] = net
            early = taken & (frame["ts"] <= half).to_numpy()
            late = taken & (frame["ts"] > half).to_numpy()
            rows.append({
                "exit": exit_name, "signal": signal_name, "trades": int(taken.sum()),
                "net_mean": net.mean(), "net_median": float(np.median(net)),
                "win_rate": (net > 1).mean(), "day_t": t_stat, "days": n_days,
                "first_half": net_all[early].mean() if early.sum() > 30 else np.nan,
                "second_half": net_all[late].mean() if late.sum() > 30 else np.nan,
            })

    table = pd.DataFrame(rows).sort_values("net_mean", ascending=False)
    table.to_csv(os.path.join(HERE, "option_strategy.csv"), index=False)

    print(f"\n{'=' * 116}\nRANKED BY NET EXPECTANCY, WITH AN OUT-OF-SAMPLE SPLIT")
    print("A rule that only works in one half of the sample is a description of that half.\n")
    print(f"{'EXIT':26s} {'SIGNAL':31s} {'n':>8} {'net':>7} {'day t':>7} "
          f"{'1st half':>9} {'2nd half':>9}")
    for _, row in table.head(16).iterrows():
        print(f"{row['exit']:26s} {row['signal']:31s} {row['trades']:8,} "
              f"{row['net_mean']:7.3f} {row['day_t']:7.2f} "
              f"{row['first_half']:9.3f} {row['second_half']:9.3f}")

    winners = table[(table["net_mean"] > 1.0)]
    both = winners[(winners["first_half"] > 1.0) & (winners["second_half"] > 1.0)]
    print(f"\n{len(winners)} of {len(table)} signal/exit pairs have net expectancy above 1.0")
    print(f"{len(both)} of those hold up in BOTH halves of the sample")
    if both.empty:
        print("\nNothing survives. Every entry rule tested here loses money once the")
        print("tick is paid, or wins in one half of the sample and gives it back in")
        print("the other. The 2-3x moves are real; buying them at random times is not")
        print("a strategy, and none of these entry filters finds them ahead of time.")


if __name__ == "__main__":
    main()
