"""Positional options-buying backtester for NSE F&O stocks.

The system is deliberately split into three layers that know nothing about each
other, so a hundred stocks and four signal engines are the same amount of work:

    signal engine  ->  a boolean Series, one flag per daily bar
    risk model     ->  ATR-derived stop / target / breakeven trigger
    execution      ->  a bar-by-bar walk that resolves the exit

Indicators and signals are fully vectorised. Execution is not, and cannot be:
a stop that moves to breakeven part-way through a trade is path-dependent, and
any "vectorised" version of it is either wrong or a loop in disguise. The loop
here runs over *signal bars only* -- typically a few dozen per stock per decade
-- so it costs nothing next to the indicator maths that is vectorised.

Indicators are implemented natively rather than via pandas_ta on purpose.
pandas_ta returns version-dependent column names (``BBU_20_2.0`` vs ``BBU_20_2``)
and 0.3.14b fails outright on numpy >= 2.0. A backtester's indicators have to be
pinned and exact, so the ~40 lines below are worth more than the dependency. The
pandas_ta equivalent is named in each docstring if you would rather swap it.

Run directly for a demo on synthetic data:

    python research/positional_options_backtest.py
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass
class Config:
    """Every number the system uses. Nothing is hard-coded below this block."""

    # Risk model, all in units of the underlying's 14-day ATR.
    atr_period: int = 14
    stop_atr: float = 1.5
    target_atr: float = 3.0
    breakeven_atr: float = 1.0

    # Time-based exit on the underlying.
    ema_exit_period: int = 10

    # Signal-engine parameters.
    bb_period: int = 20
    bb_std: float = 2.0
    kc_period: int = 20
    kc_mult: float = 1.5
    darvas_box: int = 10
    volume_sma: int = 20
    volume_mult: float = 1.5
    nr_period: int = 7
    donchian_period: int = 20

    # Execution.
    #   next_open    -- signal fires on today's close, we buy tomorrow's open.
    #   signal_close -- buy the close that generated the signal.
    # next_open is the default because signal_close assumes you can transact at
    # a price you only learn at the moment the bar ends. On a daily positional
    # system that difference is a whole day of drift, not a rounding error.
    entry_on: str = "next_open"

    # When a bar's range covers both the stop and the target we cannot know
    # which came first without intraday data. True resolves it as the stop,
    # which is the assumption that cannot flatter the result.
    pessimistic_intrabar: bool = True

    # Options proxy. delta 0.50 is the ATM assumption from the brief.
    delta: float = 0.50
    lot_size: int = 1

    # Per-day premium decay charged against the option proxy, as a fraction of
    # the *entry* premium. 0.0 reproduces the brief exactly. See the note in
    # `_option_pnl` before you leave it at zero.
    theta_per_day: float = 0.0
    entry_premium: float | None = None

    # NSE physical-delivery blackout: close everything this many calendar days
    # before the monthly expiry, and take no new entries inside the window.
    expiry_buffer_days: int = 4


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing -- an EMA with alpha = 1/period, not 2/(period+1)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """Max of the three classic gaps. pandas_ta: ``ta.true_range``."""
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR. pandas_ta: ``ta.atr(h, l, c, length=period)``."""
    return _wilder(true_range(df), period)


def bollinger(close: pd.Series, period: int, num_std: float):
    """Upper/lower Bollinger Bands. pandas_ta: ``ta.bbands``.

    Population standard deviation (ddof=0) to match the textbook definition;
    pandas defaults to the sample deviation and the two disagree enough at
    period 20 to move a squeeze on and off.
    """
    mid = close.rolling(period).mean()
    dev = close.rolling(period).std(ddof=0)
    return mid + num_std * dev, mid, mid - num_std * dev


def keltner(df: pd.DataFrame, period: int, mult: float):
    """Keltner Channels around an SMA, width set by ATR. pandas_ta: ``ta.kc``.

    Carter's original TTM Squeeze uses a simple moving average and an ATR of the
    same length for both channels, which is what keeps the two comparable.
    """
    mid = df["close"].rolling(period).mean()
    width = mult * atr(df, period)
    return mid + width, mid, mid - width


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise the input frame: datetime index, sorted, numeric OHLCV."""
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"])
        out = out.set_index("date")
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()

    required = ["open", "high", "low", "close", "volume"]
    out.columns = [str(column).strip().lower() for column in out.columns]
    missing = [column for column in required if column not in out.columns]
    if missing:
        raise ValueError(f"missing required column(s): {missing}")
    return out[required].astype(float)


# --------------------------------------------------------------------------- #
# Signal engines
# --------------------------------------------------------------------------- #
#
# Each returns a boolean Series aligned to `df`, True on the bar whose *close*
# generated the signal. Every rolling extreme is shifted by one bar: the level
# a breakout has to clear must be known before the bar that clears it, or the
# engine is reading its own answer off the chart.


def signal_ttm_squeeze(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Squeeze releases upward.

    Squeeze is ON while both Bollinger Bands sit inside the Keltner Channels --
    realised volatility compressed below its own recent average. The trade is
    the release: the squeeze was on yesterday, is off today, and the close is
    outside the upper band, so the expansion has a direction.
    """
    upper_bb, _, lower_bb = bollinger(df["close"], cfg.bb_period, cfg.bb_std)
    upper_kc, _, lower_kc = keltner(df, cfg.kc_period, cfg.kc_mult)

    squeeze_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    fired_off = squeeze_on.shift(1, fill_value=False) & ~squeeze_on
    return (fired_off & (df["close"] > upper_bb)).astype(bool)


def signal_darvas_box(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Close above the 10-day box high, confirmed by volume.

    The box high is the highest high of the *previous* ten bars, so today's own
    high cannot define the level today breaks.
    """
    box_high = df["high"].rolling(cfg.darvas_box).max().shift(1)
    volume_ok = df["volume"] > cfg.volume_mult * df["volume"].rolling(
        cfg.volume_sma
    ).mean()
    return ((df["close"] > box_high) & volume_ok).astype(bool)


def signal_nr7(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Break of the high of the narrowest-range day in seven.

    An NR7 bar is a coiled spring: the level that matters is that bar's high,
    and it stays live until price takes it out or a newer NR7 replaces it. The
    signal is the bar whose high crosses it -- never the NR7 bar itself.
    """
    bar_range = df["high"] - df["low"]
    is_nr7 = bar_range == bar_range.rolling(cfg.nr_period).min()

    # The most recent NR7 bar's high, carried forward from the bar *after* it.
    level = df["high"].where(is_nr7).ffill().shift(1)
    nr7_bar = is_nr7.shift(1, fill_value=False)  # do not trigger on the NR7 bar

    crossed = df["high"] > level
    # Only the first cross of a given level trades; the rest is the same idea
    # firing every day while price holds above it. A newly formed NR7 re-arms
    # the signal even if price never came back below the old level.
    fresh_level = level != level.shift(1)
    first_cross = crossed & (~crossed.shift(1, fill_value=False) | fresh_level)
    return (first_cross & ~nr7_bar & level.notna()).astype(bool)


def signal_donchian(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Close above the 20-day Donchian high."""
    channel_high = df["high"].rolling(cfg.donchian_period).max().shift(1)
    return (df["close"] > channel_high).astype(bool)


SIGNAL_ENGINES = {
    "ttm_squeeze": signal_ttm_squeeze,
    "darvas_box": signal_darvas_box,
    "nr7": signal_nr7,
    "donchian": signal_donchian,
}


# --------------------------------------------------------------------------- #
# NSE monthly expiry
# --------------------------------------------------------------------------- #


def last_thursday(year: int, month: int) -> date:
    """The last Thursday of a calendar month.

    NSE has moved its F&O expiry weekday more than once recently, and a holiday
    pulls expiry back to the previous session. Both are one edit to this
    function -- check the current circular and an NSE holiday list before
    trusting the blackout dates on live money.
    """
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - calendar.THURSDAY) % 7)


def expiry_blackout(index: pd.DatetimeIndex, buffer_days: int) -> pd.Series:
    """True on bars inside the physical-delivery blackout window.

    Stock F&O settles by physical delivery, so an in-the-money long option held
    into expiry turns into a delivery obligation and the margin against it
    escalates over the final week. The window runs from `buffer_days` before
    that month's expiry up to expiry itself, and is closed again afterwards --
    the day after expiry belongs to the next month's contract.
    """
    flags = np.zeros(len(index), dtype=bool)
    for (year, month), positions in _month_groups(index).items():
        expiry = last_thursday(year, month)
        opens = expiry - timedelta(days=buffer_days)
        for position in positions:
            bar = index[position].date()
            flags[position] = opens <= bar <= expiry
    return pd.Series(flags, index=index)


def _month_groups(index: pd.DatetimeIndex) -> dict[tuple[int, int], list[int]]:
    groups: dict[tuple[int, int], list[int]] = {}
    for position, stamp in enumerate(index):
        groups.setdefault((stamp.year, stamp.month), []).append(position)
    return groups


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _option_pnl(entry: float, exit_price: float, held_days: int, cfg: Config) -> float:
    """Underlying move converted to option P&L by a constant delta.

    Worth being blunt about what this does and does not model. Delta 0.50 is
    flat, but a real ATM option's delta climbs toward 1.0 as a winner runs and
    decays toward 0 as a loser fades -- so this *understates* trend winners and
    *overstates* losers, which flatters nothing and penalises nothing in an
    obvious direction. Theta is the real omission: on a positional hold of two
    to six weeks it is the largest single cost in the trade and it is charged
    every calendar day regardless of whether the thesis is working. Set
    `theta_per_day` and `entry_premium` to price it; left at zero this returns
    exactly the brief's proxy.
    """
    gross = (exit_price - entry) * cfg.delta
    if cfg.theta_per_day and cfg.entry_premium:
        gross -= cfg.entry_premium * cfg.theta_per_day * held_days
    return gross


def _resolve_trade(bars: dict, entry_index: int, atr_at_signal: float,
                   cfg: Config) -> dict:
    """Walk a single trade forward until something closes it.

    Exit precedence inside one bar, in the order the market would deliver them:
    intrabar stop, intrabar target, then the close-based rules. A gap through
    either level fills at the open, not at the level -- pretending otherwise
    hands the backtest money no broker would.
    """
    open_, high, low, close = bars["open"], bars["high"], bars["low"], bars["close"]
    ema, blackout = bars["ema"], bars["blackout"]
    n = len(close)

    entry_price = open_[entry_index] if cfg.entry_on == "next_open" else close[entry_index]
    stop = entry_price - cfg.stop_atr * atr_at_signal
    target = entry_price + cfg.target_atr * atr_at_signal
    breakeven_trigger = entry_price + cfg.breakeven_atr * atr_at_signal

    # Entering at the open leaves the rest of that bar tradeable; entering at
    # the close does not, so the first bar that can resolve the trade differs.
    first = entry_index if cfg.entry_on == "next_open" else entry_index + 1
    breakeven_armed = False

    for position in range(first, n):
        active_stop = entry_price if breakeven_armed else stop

        if low[position] <= active_stop:
            fill = min(open_[position], active_stop)  # gap-down honesty
            reason = "Trailing (BE)" if breakeven_armed else "SL"
            return _close_trade(entry_index, entry_price, position, fill, reason,
                                bars, cfg)

        hit_target = high[position] >= target
        if hit_target and not (cfg.pessimistic_intrabar and low[position] <= active_stop):
            fill = max(open_[position], target)  # gap-up honesty
            return _close_trade(entry_index, entry_price, position, fill, "TP",
                                bars, cfg)

        if blackout[position]:
            return _close_trade(entry_index, entry_price, position, close[position],
                                "Expiry", bars, cfg)

        if close[position] < ema[position]:
            return _close_trade(entry_index, entry_price, position, close[position],
                                "EMA Exit", bars, cfg)

        # Arm the breakeven stop only from the *next* bar. Within the bar that
        # triggered it we cannot know whether the high preceded the low, and
        # arming immediately would let the same bar both trigger and stop out.
        if not breakeven_armed and high[position] >= breakeven_trigger:
            breakeven_armed = True

    # Ran out of data with the position open. Marked, not silently dropped.
    return _close_trade(entry_index, entry_price, n - 1, close[n - 1], "Open at EOD",
                        bars, cfg)


def _close_trade(entry_index, entry_price, exit_index, exit_price, reason,
                 bars, cfg) -> dict:
    dates = bars["dates"]
    held = (dates[exit_index] - dates[entry_index]).days
    stock_pnl = exit_price - entry_price
    option_pnl = _option_pnl(entry_price, exit_price, held, cfg)
    return {
        "Entry Date": dates[entry_index],
        "Entry Price": round(entry_price, 2),
        "Exit Date": dates[exit_index],
        "Exit Price": round(exit_price, 2),
        "Exit Reason": reason,
        "Held Days": held,
        "Stock PnL": round(stock_pnl, 2),
        "Option PnL proxy": round(option_pnl, 2),
        "Option PnL (lot)": round(option_pnl * cfg.lot_size, 2),
        "_exit_index": exit_index,
    }


def backtest(df: pd.DataFrame, strategy: str = "ttm_squeeze",
             cfg: Config | None = None) -> pd.DataFrame:
    """Run one signal engine over one stock and return its trade log."""
    cfg = cfg or Config()
    if strategy not in SIGNAL_ENGINES:
        raise KeyError(f"unknown strategy {strategy!r}; have {list(SIGNAL_ENGINES)}")

    data = prepare(df)
    warmup = max(cfg.atr_period, cfg.bb_period, cfg.kc_period, cfg.donchian_period,
                 cfg.volume_sma, cfg.ema_exit_period) + 2
    if len(data) <= warmup:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    signals = SIGNAL_ENGINES[strategy](data, cfg)
    atr_series = atr(data, cfg.atr_period)
    blackout = expiry_blackout(data.index, cfg.expiry_buffer_days)

    # No new risk inside the delivery window. Note this is tested on the bar we
    # *enter*, not the bar that signalled: with next-open entry the two differ,
    # and testing the signal bar lets a trade open on the first blackout day and
    # force-close the same afternoon for nothing but costs.
    entry_blackout = (
        blackout.shift(-1, fill_value=True) if cfg.entry_on == "next_open" else blackout
    )
    tradeable = signals & ~entry_blackout & atr_series.notna() & (atr_series > 0)

    bars = {
        "open": data["open"].to_numpy(),
        "high": data["high"].to_numpy(),
        "low": data["low"].to_numpy(),
        "close": data["close"].to_numpy(),
        "ema": data["close"].ewm(span=cfg.ema_exit_period, adjust=False).mean().to_numpy(),
        "blackout": blackout.to_numpy(),
        "dates": data.index.to_pydatetime(),
    }
    atr_values = atr_series.to_numpy()
    n = len(data)
    offset = 1 if cfg.entry_on == "next_open" else 0

    trades, next_free = [], warmup
    for signal_index in np.flatnonzero(tradeable.to_numpy()):
        entry_index = signal_index + offset
        # One position at a time; a signal that fires while the previous trade
        # is still open is information we already acted on.
        if entry_index >= n or entry_index < next_free:
            continue
        trade = _resolve_trade(bars, entry_index, atr_values[signal_index], cfg)
        next_free = trade.pop("_exit_index") + 1
        trades.append(trade)

    return pd.DataFrame(trades, columns=TRADE_COLUMNS) if trades else pd.DataFrame(
        columns=TRADE_COLUMNS
    )


TRADE_COLUMNS = [
    "Entry Date", "Entry Price", "Exit Date", "Exit Price", "Exit Reason",
    "Held Days", "Stock PnL", "Option PnL proxy", "Option PnL (lot)",
]


def run_universe(data: dict[str, pd.DataFrame], strategy: str = "ttm_squeeze",
                 cfg: Config | None = None) -> pd.DataFrame:
    """Backtest a whole shortlist. Returns one trade log with a Symbol column."""
    logs = []
    for symbol, frame in data.items():
        try:
            log = backtest(frame, strategy=strategy, cfg=cfg)
        except (ValueError, KeyError) as error:
            print(f"  {symbol}: skipped -- {error}")
            continue
        if not log.empty:
            log.insert(0, "Symbol", symbol)
            logs.append(log)
    if not logs:
        return pd.DataFrame(columns=["Symbol", *TRADE_COLUMNS])
    return pd.concat(logs, ignore_index=True).sort_values("Entry Date")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def metrics(log: pd.DataFrame, column: str = "Option PnL proxy") -> dict:
    """Headline statistics on the traded instrument, not on the underlying.

    Scratches are counted separately rather than lumped in with losers. A stop
    that moves to breakeven produces exits at *exactly* zero by construction --
    in this system they are a sixth of all trades -- and folding them into the
    losing bucket drags the average loser toward zero and sends profit factor
    to infinity. They are neither wins nor losses; they are the risk manager
    working.
    """
    if log.empty:
        return {"Total Trades": 0}

    pnl = log[column]
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    scratches = len(pnl) - len(wins) - len(losses)
    win_rate = len(wins) / len(pnl)
    average_win = wins.mean() if len(wins) else 0.0
    average_loss = losses.mean() if len(losses) else 0.0

    return {
        "Total Trades": len(pnl),
        "Win Rate (%)": round(win_rate * 100, 2),
        "Scratches": scratches,
        "Average Winning Trade": round(average_win, 2),
        "Average Losing Trade": round(average_loss, 2),
        # Mean P&L per trade. Identical to the textbook
        # (win% x avg win) + (loss% x avg loss) when nothing scratches, and
        # still correct when things do.
        "Expectancy": round(pnl.mean(), 2),
        "Total PnL": round(pnl.sum(), 2),
        "Profit Factor": (
            round(wins.sum() / abs(losses.sum()), 2) if len(losses) else float("inf")
        ),
        "Average Hold (days)": round(log["Held Days"].mean(), 1),
    }


def summarise(log: pd.DataFrame, label: str = "TTM Squeeze") -> dict:
    """Print the trade log's headline numbers and its exit breakdown."""
    stats = metrics(log)
    print(f"\n{'=' * 62}\n{label}\n{'=' * 62}")
    if not stats["Total Trades"]:
        print("  no trades")
        return stats

    for key, value in stats.items():
        print(f"  {key:<24}{value:>14}")

    print(f"\n  {'exit reason':<24}{'n':>6}{'avg option PnL':>18}")
    breakdown = log.groupby("Exit Reason")["Option PnL proxy"].agg(["count", "mean"])
    for reason, row in breakdown.sort_values("count", ascending=False).iterrows():
        print(f"  {reason:<24}{int(row['count']):>6}{row['mean']:>18.2f}")
    return stats


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


def _synthetic(days: int = 1500, seed: int = 7) -> pd.DataFrame:
    """A trending-with-vol-cycles series, so the squeeze has something to find."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2019-01-01", periods=days)

    # Volatility cycles between calm and active so squeezes form and release.
    cycle = 0.008 + 0.010 * (np.sin(np.arange(days) / 55.0) ** 2)
    drift = 0.0004
    close = 1000 * np.exp(np.cumsum(rng.normal(drift, 1.0, days) * cycle))

    spread = close * cycle * 0.9
    open_ = close + rng.normal(0, 1, days) * spread * 0.4
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1, days)) * spread
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1, days)) * spread
    volume = rng.lognormal(13, 0.35, days)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


if __name__ == "__main__":
    # Replace with your own daily OHLCV frame:  df = pd.read_csv(...)
    df = _synthetic()
    config = Config()

    log = backtest(df, strategy="ttm_squeeze", cfg=config)
    summarise(log, "TTM Squeeze  (default)")

    if not log.empty:
        print("\n  trade log (last 10)")
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(log.tail(10).to_string(index=False))

    # The other three engines on the same data and the same execution model,
    # so the only thing that differs between these lines is the entry rule.
    print(f"\n{'=' * 62}\nEngine comparison, identical risk and exit model\n{'=' * 62}")
    print(f"  {'engine':<16}{'n':>5}{'win%':>8}{'expectancy':>13}{'total':>12}")
    for name in SIGNAL_ENGINES:
        stats = metrics(backtest(df, strategy=name, cfg=config))
        if not stats["Total Trades"]:
            print(f"  {name:<16}{0:>5}")
            continue
        print(
            f"  {name:<16}{stats['Total Trades']:>5}{stats['Win Rate (%)']:>8}"
            f"{stats['Expectancy']:>13}{stats['Total PnL']:>12}"
        )
