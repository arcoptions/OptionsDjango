"""Can a breakout be seen coming? Signals, and what happened after each trigger.

Two families, kept separate on purpose because they answer different questions:

  ANTICIPATION  fires while the stock is still coiling -- squeeze on, narrowing
                range, volume drying up. This is "find it before the breakout".
  CONFIRMATION  fires when price has already cleared a level. This is the
                conventional breakout scan, included so the two can be compared
                on identical measurement.

Three things make this measurement honest, and all three have killed a result in
this project before:

1.  NO LOOK-AHEAD. Every signal is computed from data up to day t's close; the
    position is entered at day t+1's OPEN. Rolling extremes are shifted so the
    level a breakout must clear was knowable before the bar that cleared it.
    A previous option-side test returned +6.57% (t +5.09) purely from reading
    the close of the bar it bought the open of.

2.  MARKET-RELATIVE IS THE HEADLINE. Raw forward return is reported, but the
    number that decides anything is the excess over the SAME DAY's
    cross-sectional mean across the whole universe. A signal that mostly fires
    on strong market days would otherwise be rewarded for beta it did not
    predict. This also absorbs most of the universe's survivorship tilt: the
    189 names are today's F&O list, so they over-represent stocks that did well,
    but the control is drawn from that same tilted universe.

3.  DAY-CLUSTERED SIGNIFICANCE. Stocks move together, so 5,000 signals on 300
    days are nowhere near 5,000 independent observations. t is computed across
    daily means. A sub-Rs5 option result in this project survived a naive t and
    died at day-clustered t -3.83.

Writes research/breakout_results.csv and prints the report tables.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PANEL = Path(__file__).resolve().parent / "daily_panel.csv"
OUT = Path(__file__).resolve().parent / "breakout_results.csv"

HORIZONS = (1, 3, 5, 10, 20)
BREAKOUT_MOVE = 0.10      # what counts as "a breakout" in the label
BREAKOUT_ATR = 3.0        # ...or the same move in units of the stock's own ATR
BREAKOUT_WINDOW = 10      # ...within this many sessions of entry

# Delivery equity, both sides. STT at 0.10% each way is the bulk of it; the rest
# is stamp duty, exchange charges, GST and a spread allowance for liquid F&O
# names. Nothing like the 1.64% that ended the option programme.
ROUND_TRIP = 0.0030


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Everything the signals need, for ONE symbol, in time order."""
    out = df.copy()
    close, high, low = out["close"], out["high"], out["low"]

    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    out["atr14"] = _wilder(true_range, 14)
    out["atr5"] = _wilder(true_range, 5)
    out["atr20"] = _wilder(true_range, 20)
    out["rng"] = high - low

    mid = close.rolling(20).mean()
    dev = close.rolling(20).std(ddof=0)
    out["bb_up"], out["bb_lo"], out["bb_mid"] = mid + 2 * dev, mid - 2 * dev, mid
    out["bb_width"] = (out["bb_up"] - out["bb_lo"]) / mid

    kc_width = 1.5 * _wilder(true_range, 20)
    out["kc_up"], out["kc_lo"] = mid + kc_width, mid - kc_width

    out["vol_sma20"] = out["volume"].rolling(20).mean()
    out["vol_ratio"] = out["volume"] / out["vol_sma20"]

    # Prior extremes -- shifted, so today's own high can never be the level
    # today is asked to clear.
    for window in (10, 20, 55, 200):
        out[f"hh{window}"] = high.rolling(window).max().shift(1)
        out[f"ll{window}"] = low.rolling(window).min().shift(1)

    out["bbw_pct"] = out["bb_width"].rolling(120, min_periods=60).rank(pct=True)
    out["ret1"] = close.pct_change()
    out["ema10"] = close.ewm(span=10, adjust=False).mean()
    out["above_ema10"] = close > out["ema10"]

    # Context for the conditioned setups below. These use today's own high/low
    # deliberately -- where price sits inside a base it has already made is
    # knowable at the close, unlike the level a breakout has to clear.
    span_hi = high.rolling(20).max()
    span_lo = low.rolling(20).min()
    out["pos20"] = (close - span_lo) / (span_hi - span_lo).replace(0, np.nan)
    out["sma100"] = close.rolling(100).mean()
    out["ret60"] = close.pct_change(60)
    out["near_200high"] = close / high.rolling(200, min_periods=100).max()

    # Accumulation: volume traded on up days against volume on down days.
    up_vol = out["volume"].where(out["ret1"] > 0, 0.0).rolling(20).sum()
    down_vol = out["volume"].where(out["ret1"] < 0, 0.0).rolling(20).sum()
    out["ud_vol"] = up_vol / down_vol.replace(0, np.nan)

    # Volatility contraction pattern: each five-session block tighter than the
    # last, on falling volume.
    out["rng5"] = (high.rolling(5).max() - low.rolling(5).min()) / close
    out["vol5"] = out["volume"].rolling(5).mean()
    out["contracting"] = ((out["rng5"] < out["rng5"].shift(5))
                          & (out["rng5"].shift(5) < out["rng5"].shift(10))
                          & (out["vol5"] < out["vol5"].shift(5)))
    return out


# --------------------------------------------------------------------------- #
# Signals.  Each returns a boolean Series aligned to the frame.
# --------------------------------------------------------------------------- #
# ---- family: anticipation (still coiling, nothing has broken yet) ---------- #


def sig_squeeze_on(d):
    """TTM squeeze active: Bollinger Bands inside Keltner Channels."""
    return (d["bb_lo"] > d["kc_lo"]) & (d["bb_up"] < d["kc_up"])


def sig_squeeze_day6(d):
    """Squeeze active for 6+ consecutive sessions -- a *long* coil."""
    on = sig_squeeze_on(d)
    return on & on.shift(1, fill_value=False) & on.shift(2, fill_value=False) \
        & on.shift(3, fill_value=False) & on.shift(4, fill_value=False) \
        & on.shift(5, fill_value=False)


def sig_nr7(d):
    """Narrowest range of the last 7 sessions."""
    return d["rng"] == d["rng"].rolling(7).min()


def sig_inside_bar(d):
    """Range wholly inside the prior session's range."""
    return (d["high"] <= d["high"].shift(1)) & (d["low"] >= d["low"].shift(1))


def sig_bbw_squeeze(d):
    """Bollinger bandwidth in the bottom decile of its own 6-month history."""
    return d["bbw_pct"] <= 0.10


def sig_atr_contraction(d):
    """Short-term ATR collapsed to under 65% of the 20-day -- volatility drying."""
    return (d["atr5"] / d["atr20"]) < 0.65


def sig_volume_dryup(d):
    """Quiet tape: volume under 60% of normal while the range is also tight."""
    return (d["vol_ratio"] < 0.60) & (d["rng"] < d["rng"].rolling(20).median())


def sig_coil3(d):
    """Three consecutive narrowing ranges."""
    rng = d["rng"]
    return (rng < rng.shift(1)) & (rng.shift(1) < rng.shift(2)) \
        & (rng.shift(2) < rng.shift(3))


# ---- family: confirmation (the level is already gone) --------------------- #


def sig_donchian20(d):
    return d["close"] > d["hh20"]


def sig_donchian55(d):
    return d["close"] > d["hh55"]


def sig_high_200(d):
    return d["close"] > d["hh200"]


def sig_darvas(d):
    """10-day box break confirmed by 1.5x volume."""
    return (d["close"] > d["hh10"]) & (d["vol_ratio"] > 1.5)


def sig_squeeze_fire(d):
    """The classic TTM trigger: squeeze releases and price leaves the top band."""
    on = sig_squeeze_on(d)
    return on.shift(1, fill_value=False) & ~on & (d["close"] > d["bb_up"])


def sig_vol_expansion(d):
    """20-day break on double normal volume."""
    return (d["close"] > d["hh20"]) & (d["vol_ratio"] > 2.0)


def sig_pocket_pivot(d):
    """Up day on volume exceeding every down-day volume of the last 10."""
    down_vol = d["volume"].where(d["ret1"] < 0)
    return (d["ret1"] > 0) & (d["volume"] > down_vol.rolling(10, min_periods=3).max())


# ---- combinations: coil, then break -------------------------------------- #


def sig_squeeze_then_break(d):
    """Squeeze was on within the last 5 days AND price clears the 20-day high.

    This is the honest version of "find it before the breakout, act on the
    break": the coil is the filter, the break is the trigger.
    """
    on = sig_squeeze_on(d)
    coiled = on.shift(1, fill_value=False).rolling(5, min_periods=1).max().astype(bool)
    return coiled & (d["close"] > d["hh20"])


def sig_nr7_then_break(d):
    nr = sig_nr7(d)
    recent = nr.shift(1, fill_value=False).rolling(5, min_periods=1).max().astype(bool)
    return recent & (d["close"] > d["hh20"])


def sig_dryup_then_break(d):
    dry = sig_volume_dryup(d)
    recent = dry.shift(1, fill_value=False).rolling(5, min_periods=1).max().astype(bool)
    return recent & (d["close"] > d["hh20"]) & (d["vol_ratio"] > 1.5)


ANTICIPATION = {
    "squeeze_on": sig_squeeze_on,
    "squeeze_6day": sig_squeeze_day6,
    "nr7": sig_nr7,
    "inside_bar": sig_inside_bar,
    "bbw_bottom_decile": sig_bbw_squeeze,
    "atr_contraction": sig_atr_contraction,
    "volume_dryup": sig_volume_dryup,
    "coil_3bar": sig_coil3,
}

CONFIRMATION = {
    "donchian20": sig_donchian20,
    "donchian55": sig_donchian55,
    "high_200d": sig_high_200,
    "darvas_box": sig_darvas,
    "squeeze_fire": sig_squeeze_fire,
    "vol_expansion": sig_vol_expansion,
    "pocket_pivot": sig_pocket_pivot,
}

COMBINATION = {
    "squeeze_then_break": sig_squeeze_then_break,
    "nr7_then_break": sig_nr7_then_break,
    "dryup_then_break": sig_dryup_then_break,
}


# ---- conditioned anticipation: a coil, but only where one is worth having --- #
# Applied after the per-symbol frames are stacked, because two of them need the
# cross-section of that day. Bare "squeeze on" fires on 45 names a session and
# says nothing about direction; these ask where the coil sits.


def sig_squeeze_near_high(d):
    """Coiled in the top quarter of its own 20-day base."""
    return d["squeeze_on"] & (d["pos20"] >= 0.75)


def sig_squeeze_uptrend(d):
    """Coiled while above the 100-day average."""
    return d["squeeze_on"] & (d["close"] > d["sma100"])


def sig_squeeze_rs_top(d):
    """Coiled, and among the strongest fifth of the universe over 60 sessions."""
    return d["squeeze_on"] & (d["rs60_rank"] >= 0.80)


def sig_squeeze_accum(d):
    """Coiled on accumulation: up-day volume 1.2x down-day volume."""
    return d["squeeze_on"] & (d["ud_vol"] >= 1.2)


def sig_vcp(d):
    """Volatility contraction pattern within 10% of the 200-day high."""
    return d["contracting"] & (d["near_200high"] >= 0.90)


def sig_vcp_rs(d):
    """VCP with cross-sectional relative strength on top."""
    return sig_vcp(d) & (d["rs60_rank"] >= 0.80)


def sig_coil_at_high(d):
    """NR7 pressed right against the 200-day high."""
    return d["nr7"] & (d["near_200high"] >= 0.97)


def sig_full_setup(d):
    """Everything at once: coiled, near the high of its base, in an uptrend,
    strong against the cross-section, on accumulation."""
    return (d["squeeze_on"] & (d["pos20"] >= 0.70) & (d["close"] > d["sma100"])
            & (d["rs60_rank"] >= 0.70) & (d["ud_vol"] >= 1.0))


CONDITIONED = {
    "squeeze_near_high": sig_squeeze_near_high,
    "squeeze_uptrend": sig_squeeze_uptrend,
    "squeeze_rs_top": sig_squeeze_rs_top,
    "squeeze_accum": sig_squeeze_accum,
    "vcp": sig_vcp,
    "vcp_rs": sig_vcp_rs,
    "coil_at_high": sig_coil_at_high,
    "full_setup": sig_full_setup,
}


# ---- deconfounds --------------------------------------------------------- #
# squeeze_rs_top is the only candidate that survives, and it has two ingredients.
# These separate them. If plain relative strength does as well, the coil is
# decoration and the finding is 60-day momentum, which is not news and is not
# what was asked for.


def sig_rs_top(d):
    """Relative strength alone -- no squeeze condition at all."""
    return d["rs60_rank"] >= 0.80


def sig_rs_top_unsqueezed(d):
    """Strong, and explicitly NOT coiled. The direct control for the coil."""
    return (d["rs60_rank"] >= 0.80) & ~d["squeeze_on"]


def sig_rs_bottom(d):
    """The weak fifth. A sanity check that the rank means something."""
    return d["rs60_rank"] <= 0.20


def sig_squeeze_rs_mid(d):
    """Coiled but unremarkable. Isolates the coil from the strength."""
    return d["squeeze_on"] & (d["rs60_rank"] >= 0.40) & (d["rs60_rank"] < 0.60)


DECONFOUND = {
    "rs_top_only": sig_rs_top,
    "rs_top_NOT_coiled": sig_rs_top_unsqueezed,
    "rs_bottom_only": sig_rs_bottom,
    "squeeze_rs_middle": sig_squeeze_rs_mid,
}
CONDITIONED_ALL = {**CONDITIONED, **DECONFOUND}

ALL_SIGNALS = {**ANTICIPATION, **CONFIRMATION, **COMBINATION}
FAMILY = ({k: "anticipation" for k in ANTICIPATION}
          | {k: "confirmation" for k in CONFIRMATION}
          | {k: "combination" for k in COMBINATION}
          | {k: "conditioned" for k in CONDITIONED}
          | {k: "DECONFOUND" for k in DECONFOUND})


# --------------------------------------------------------------------------- #
# Panel construction
# --------------------------------------------------------------------------- #


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Indicators, signals and forward returns for every stock-day."""
    frames = []
    for symbol, group in panel.groupby("symbol", sort=False):
        d = add_indicators(group.sort_values("date").reset_index(drop=True))

        # Entry is the NEXT session's open. Everything forward is measured from
        # there, so a signal read off today's close can never be filled at it.
        d["entry_price"] = d["open"].shift(-1)
        d["entry_date"] = d["date"].shift(-1)

        # Hold h sessions: in at the open of t+1, out at the close of t+h.
        for horizon in HORIZONS:
            d[f"fwd{horizon}"] = d["close"].shift(-horizon) / d["entry_price"] - 1.0

        # Best excursion inside the breakout window, from the same entry.
        fwd_high = d["high"].shift(-1).rolling(BREAKOUT_WINDOW, min_periods=1).max()
        d["max_fwd"] = fwd_high.shift(-(BREAKOUT_WINDOW - 1)) / d["entry_price"] - 1.0

        # How far the stock normally travels, in its own units. A +10% label is
        # a volatility label as much as a direction one, so a coiled stock is
        # penalised for being coiled unless the bar is scaled to its own ATR.
        d["atr_pct"] = d["atr20"] / d["close"]

        for name, fn in ALL_SIGNALS.items():
            d[name] = fn(d).fillna(False).astype(bool)

        d["symbol"] = symbol
        frames.append(d)

    full = pd.concat(frames, ignore_index=True)

    # Cross-sectional strength, then the setups that need it. Ranked within the
    # session, so it is a comparison against what else was tradeable that day.
    full["rs60_rank"] = full.groupby("date")["ret60"].rank(pct=True)
    for name, fn in CONDITIONED_ALL.items():
        full[name] = fn(full).fillna(False).astype(bool)

    # Eligibility: indicators warmed up and the forward window actually exists.
    full["eligible"] = (
        full["entry_price"].notna()
        & full["atr20"].notna()
        & full["hh20"].notna()
        & full[f"fwd{max(HORIZONS)}"].notna()
        & full["max_fwd"].notna()
    )

    # Market-relative: excess over the same day's cross-sectional mean, computed
    # on eligible rows only so a partly-warmed day cannot skew its own baseline.
    elig = full[full["eligible"]]
    for horizon in HORIZONS:
        col = f"fwd{horizon}"
        market = elig.groupby("entry_date")[col].transform("mean")
        full.loc[elig.index, f"ex{horizon}"] = elig[col] - market
    market_max = elig.groupby("entry_date")["max_fwd"].transform("mean")
    full.loc[elig.index, "ex_max"] = elig["max_fwd"] - market_max

    # Two labels, because they disagree and the disagreement is the finding.
    #   is_breakout   a fixed +10%, the way a screener would define it
    #   is_vol_break  the same move in units of the stock's own ATR, so a quiet
    #                 stock is not disqualified for being quiet
    full["is_breakout"] = full["max_fwd"] >= BREAKOUT_MOVE
    full["is_vol_break"] = full["max_fwd"] >= BREAKOUT_ATR * full["atr_pct"]

    # Volatility decile WITHIN each day, so the matched control below compares a
    # signal against other stocks of similar volatility on the same date. A day
    # too thin to cut into ten gets no decile and simply sits out that control.
    def _decile(s: pd.Series) -> pd.Series:
        if s.notna().sum() < 20:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(s.rank(method="first"), 10, labels=False)

    full.loc[elig.index, "vol_decile"] = (
        elig.groupby("entry_date")["atr_pct"].transform(_decile))
    return full


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #


def day_clustered_t(values: pd.Series, days: pd.Series) -> float:
    """t-statistic across daily means, not across trades.

    Stocks co-move, so treating every signal as independent overstates n by
    roughly the average number of names firing per day.
    """
    daily = pd.DataFrame({"v": values, "d": days}).dropna().groupby("d")["v"].mean()
    if len(daily) < 5 or daily.std(ddof=1) == 0:
        return np.nan
    return daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))


def overlap_safe_t(values: pd.Series, days: pd.Series, stride: int) -> float:
    """Day-clustered t on NON-OVERLAPPING samples.

    Clustering by day removes the cross-sectional correlation between stocks.
    It does nothing about serial overlap: a 10-day forward return measured every
    session shares nine tenths of its window with the session before it, so the
    daily means are heavily autocorrelated and t is inflated by roughly the
    square root of the horizon. Sampling every `stride`-th day fixes that. Each
    of the `stride` possible starting offsets gives a valid estimate, so all of
    them are averaged rather than one being picked arbitrarily.
    """
    daily = pd.DataFrame({"v": values, "d": days}).dropna().groupby("d")["v"].mean()
    if len(daily) < 5 * stride:
        return np.nan
    estimates = []
    for offset in range(stride):
        sample = daily.iloc[offset::stride]
        if len(sample) < 5 or sample.std(ddof=1) == 0:
            continue
        estimates.append(sample.mean() / (sample.std(ddof=1) / np.sqrt(len(sample))))
    return float(np.mean(estimates)) if estimates else np.nan


def evaluate(full: pd.DataFrame) -> pd.DataFrame:
    elig = full[full["eligible"]].copy()
    base_rate = elig["is_breakout"].mean()
    base_vol = elig["is_vol_break"].mean()

    # Expected breakout rate for a stock of this volatility on this day. Cells
    # are (entry_date x volatility decile), so the comparison a signal faces is
    # other names of similar volatility trading the same session -- not the
    # whole universe, which would reward or punish it for being quiet.
    cells = elig.dropna(subset=["vol_decile"]).groupby(["entry_date", "vol_decile"])
    elig["cell_rate"] = cells["is_breakout"].transform("mean")
    elig["cell_vol_rate"] = cells["is_vol_break"].transform("mean")

    midpoint = elig["entry_date"].quantile(0.5)
    rows = []

    def row(name, family, mask):
        hit = elig[mask]
        if len(hit) < 50:
            return None
        expected = hit["cell_rate"].mean()
        record = {
            "signal": name,
            "family": family,
            "n": len(hit),
            "per_day": len(hit) / hit["entry_date"].nunique(),
            "breakout_rate": hit["is_breakout"].mean(),
            "lift": hit["is_breakout"].mean() / base_rate,
            "lift_matched": (hit["is_breakout"].mean() / expected
                             if expected else np.nan),
            # Is the matched lift real? Same de-overlapped test, applied to the
            # per-signal edge over its own volatility-and-day-matched cell.
            "lift_t": overlap_safe_t(hit["is_breakout"].astype(float)
                                     - hit["cell_rate"],
                                     hit["entry_date"], BREAKOUT_WINDOW),
            "vol_break_rate": hit["is_vol_break"].mean(),
            "vol_lift": hit["is_vol_break"].mean() / base_vol,
            "vol_lift_matched": (hit["is_vol_break"].mean()
                                 / hit["cell_vol_rate"].mean()),
            "vol_lift_t": overlap_safe_t(hit["is_vol_break"].astype(float)
                                         - hit["cell_vol_rate"],
                                         hit["entry_date"], BREAKOUT_WINDOW),
            "recall": hit["is_breakout"].sum() / elig["is_breakout"].sum(),
            "atr_pct": hit["atr_pct"].mean(),
        }
        for horizon in HORIZONS:
            record[f"raw{horizon}"] = hit[f"fwd{horizon}"].mean()
            record[f"ex{horizon}"] = hit[f"ex{horizon}"].mean()
            record[f"med{horizon}"] = hit[f"ex{horizon}"].median()
            record[f"t{horizon}"] = day_clustered_t(hit[f"ex{horizon}"],
                                                    hit["entry_date"])
            record[f"tn{horizon}"] = overlap_safe_t(hit[f"ex{horizon}"],
                                                    hit["entry_date"], horizon)
        first, second = hit[hit["entry_date"] <= midpoint], \
            hit[hit["entry_date"] > midpoint]
        for half, part in (("h1", first), ("h2", second)):
            record[f"{half}_n"] = len(part)
            record[f"{half}_ex10"] = part["ex10"].mean() if len(part) else np.nan
            record[f"{half}_t10"] = day_clustered_t(part["ex10"],
                                                    part["entry_date"])
            record[f"{half}_lift"] = ((part["is_breakout"].mean()
                                       / part["cell_rate"].mean())
                                      if len(part) else np.nan)
        record["ex_max"] = hit["ex_max"].mean()
        record["win10"] = (hit["fwd10"] > 0).mean()
        return record

    rows.append(row("ALL BARS (control)", "control",
                    pd.Series(True, index=elig.index)))
    for name, family in FAMILY.items():
        got = row(name, family, elig[name])
        if got:
            rows.append(got)
    return pd.DataFrame([r for r in rows if r])


if __name__ == "__main__":
    panel = pd.read_csv(PANEL, parse_dates=["date"])
    print(f"panel: {panel['symbol'].nunique()} symbols, "
          f"{panel['date'].nunique()} sessions, {len(panel):,} stock-days\n")

    full = build_features(panel)
    elig = full[full["eligible"]]
    print(f"eligible stock-days: {len(elig):,}  "
          f"({elig['entry_date'].nunique()} entry dates, "
          f"{elig['entry_date'].min().date()} -> {elig['entry_date'].max().date()})")
    print(f"base rate  +{BREAKOUT_MOVE:.0%} within {BREAKOUT_WINDOW} sessions : "
          f"{elig['is_breakout'].mean():.2%}")
    print(f"base rate  {BREAKOUT_ATR:.0f}x ATR within {BREAKOUT_WINDOW} sessions: "
          f"{elig['is_vol_break'].mean():.2%}\n")

    results = evaluate(full)
    results.to_csv(OUT, index=False)
    pd.set_option("display.width", 240)

    print("=" * 116)
    print("1. DOES THE SIGNAL FIND A BREAKOUT COMING?")
    print("   lift = vs the whole universe.  matched = vs same-day stocks of the "
          "same volatility decile.")
    print("=" * 116)
    head = (f"{'signal':<21}{'family':<14}{'n':>7}{'/day':>6}{'ATR%':>7}"
            f"{'+10% hit':>10}{'lift':>7}{'matched':>9}{'t':>6}"
            f"{'3xATR hit':>11}{'lift':>7}{'matched':>9}{'t':>6}{'recall':>8}")
    print(head)
    for _, r in results.iterrows():
        print(f"{r['signal']:<21}{r['family']:<14}{int(r['n']):>7}"
              f"{r['per_day']:>6.1f}{r['atr_pct'] * 100:>7.2f}"
              f"{r['breakout_rate'] * 100:>9.1f}%{r['lift']:>7.2f}"
              f"{r['lift_matched']:>9.2f}{r['lift_t']:>6.1f}"
              f"{r['vol_break_rate'] * 100:>10.1f}%{r['vol_lift']:>7.2f}"
              f"{r['vol_lift_matched']:>9.2f}{r['vol_lift_t']:>6.1f}"
              f"{r['recall'] * 100:>7.1f}%")

    print("\n" + "=" * 116)
    print("2. WHAT HAPPENED AFTER THE TRIGGER   (mean %, market-relative in "
          "brackets: excess over the same day's universe)")
    print("=" * 116)
    head = f"{'signal':<21}{'family':<14}{'n':>7}"
    for horizon in HORIZONS:
        head += f"{'+' + str(horizon) + 'd':>18}"
    print(head)
    for _, r in results.iterrows():
        line = f"{r['signal']:<21}{r['family']:<14}{int(r['n']):>7}"
        for horizon in HORIZONS:
            cell = (f"{r[f'raw{horizon}'] * 100:+.2f} "
                    f"({r[f'ex{horizon}'] * 100:+.2f})")
            line += f"{cell:>18}"
        print(line)

    print("\n" + "=" * 116)
    print("3. IS ANY OF IT SIGNIFICANT?   t on the market-relative return. "
          "'clustered' groups by day; 'honest' also")
    print("   de-overlaps, because a 10-day return sampled daily reuses 9/10 of "
          "its window. Control's t is float dust.")
    print("=" * 116)
    head = (f"{'signal':<21}{'family':<14}{'win10':>7}"
            f"{'  clustered t':>15}{'':>10}{'  honest t':>13}{'':>9}"
            f"{'  OOS ex10 h1':>15}{'h2':>9}")
    print(head)
    print(f"{'':<42}{'+5d':>8}{'+10d':>7}{'+20d':>10}"
          f"{'+5d':>8}{'+10d':>7}{'+20d':>9}")
    for _, r in results.iterrows():
        line = f"{r['signal']:<21}{r['family']:<14}{r['win10'] * 100:>6.1f}%"
        for horizon in (5, 10, 20):
            line += f"{r[f't{horizon}']:>8.1f}" if horizon == 5 else \
                f"{r[f't{horizon}']:>7.1f}" if horizon == 10 else \
                f"{r[f't{horizon}']:>10.1f}"
        for horizon in (5, 10, 20):
            value = r[f"tn{horizon}"]
            cell = "   n/a" if pd.isna(value) else f"{value:.1f}"
            line += f"{cell:>8}" if horizon == 5 else \
                f"{cell:>7}" if horizon == 10 else f"{cell:>9}"
        line += (f"{r['h1_ex10'] * 100:>+13.2f}{r['h2_ex10'] * 100:>+9.2f}")
        print(line)

    print("\n" + "=" * 116)
    print("4. IS RELATIVE STRENGTH MONOTONE?   a real factor pays across the "
          "ranking; a lucky top decile does not")
    print("=" * 116)
    elig = elig.copy()
    elig["rs_decile"] = elig.groupby("entry_date")["rs60_rank"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False)
        if s.notna().sum() >= 20 else pd.Series(np.nan, index=s.index))
    print(f"{'RS decile':<12}{'n':>8}{'+10% hit':>10}{'ex+5d':>9}{'ex+10d':>9}"
          f"{'ex+20d':>9}{'t+10d':>8}{'t+20d':>8}")
    for decile in range(10):
        part = elig[elig["rs_decile"] == decile]
        if part.empty:
            continue
        edge = "(weak)" if decile == 0 else "(strong)" if decile == 9 else ""
        label = f"{decile + 1} {edge}"
        print(f"{label:<12}{len(part):>8}{part['is_breakout'].mean() * 100:>9.1f}%"
              f"{part['ex5'].mean() * 100:>+9.2f}{part['ex10'].mean() * 100:>+9.2f}"
              f"{part['ex20'].mean() * 100:>+9.2f}"
              f"{day_clustered_t(part['ex10'], part['entry_date']):>8.1f}"
              f"{day_clustered_t(part['ex20'], part['entry_date']):>8.1f}")

    print("\n" + "=" * 116)
    print("5. WOULD IT SURVIVE BEING TRADED?   top-N by relative strength, "
          "equal weight, rebalanced every 10 sessions,")
    print(f"   non-overlapping, charged {ROUND_TRIP:.2%} a round trip "
          "(STT 0.20 + stamp/exchange/GST 0.02 + spread ~0.08)")
    print("=" * 116)
    dates = sorted(elig["entry_date"].unique())[::10]
    universe = elig[elig["entry_date"].isin(dates)]
    bench = universe.groupby("entry_date")["fwd10"].mean()
    print(f"{'basket':<14}{'periods':>9}{'gross/pd':>10}{'net/pd':>9}"
          f"{'vs equal-wt':>13}{'net t':>8}{'win pds':>9}{'best':>8}{'worst':>8}")
    for top_n in (5, 10, 20, 30, 50):
        picks = (universe.sort_values("rs60_rank", ascending=False)
                 .groupby("entry_date").head(top_n))
        gross = picks.groupby("entry_date")["fwd10"].mean()
        net = gross - ROUND_TRIP
        excess = net - bench.reindex(net.index)
        t_stat = net.mean() / (net.std(ddof=1) / np.sqrt(len(net)))
        print(f"top {top_n:<10}{len(net):>9}{gross.mean() * 100:>+9.2f}%"
              f"{net.mean() * 100:>+8.2f}%{excess.mean() * 100:>+12.2f}%"
              f"{t_stat:>8.1f}{(net > 0).mean() * 100:>8.0f}%"
              f"{net.max() * 100:>+8.1f}{net.min() * 100:>+8.1f}")
    net_bench = bench - ROUND_TRIP
    print(f"{'equal-weight':<14}{len(bench):>9}{bench.mean() * 100:>+9.2f}%"
          f"{net_bench.mean() * 100:>+8.2f}%{0.0:>+12.2f}%"
          f"{net_bench.mean() / (net_bench.std(ddof=1) / np.sqrt(len(bench))):>8.1f}"
          f"{(net_bench > 0).mean() * 100:>8.0f}%"
          f"{net_bench.max() * 100:>+8.1f}{net_bench.min() * 100:>+8.1f}")

    print(f"\nwritten {OUT}")
