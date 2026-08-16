"""Per-minute causal feature matrix for the spike study.

Every feature at minute t uses only data available up to and including t.
Direction-dependent features are emitted signed for CALL (up) and are simply
negated for the PUT (down) view, so one matrix serves both directions.
"""
import numpy as np

import common as C

SPOT_FEATURES = [
    "ret1", "ret3", "ret5", "ret15",
    "rv15", "squeeze", "range15_pct", "range_ratio",
    "day_pos", "dist_high15", "dist_low15", "dist_open", "minute",
    "streak", "accel",
]
CHAIN_FEATURES = [
    "vol_surge", "vol_imbalance", "straddle_chg5", "straddle_level",
    "atm_iv", "iv_chg5", "oi_ce_chg5", "oi_pe_chg5", "pcr_oi",
    "basis", "basis_chg3", "atm_vol_share",
]
FEATURES = SPOT_FEATURES + CHAIN_FEATURES


def _ffill(values):
    valid = ~np.isnan(values)
    if not valid.any():
        return values
    index = np.where(valid, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    filled = values[index]
    filled[: np.argmax(valid)] = values[valid][0]
    return filled


def _pct_change(values, lag):
    shifted = np.roll(values, lag)
    shifted[:lag] = np.nan
    return (values / shifted - 1) * 100


def _rolling(values, length, function):
    result = np.full(len(values), np.nan)
    for index in range(length, len(values)):
        result[index] = function(values[index - length + 1 : index + 1])
    return result


def build(date):
    """Return (minutes, spot, feature_matrix, feature_names) for one session."""
    session = C.load(date)
    spot = _ffill(session["spot"].astype(np.float64))
    minute = session["minute"].astype(np.float64)
    strikes = session["strikes"].astype(np.float64)
    count = len(spot)
    if count < 120 or np.isnan(spot).any():
        return None

    close = np.nan_to_num(session["c"].astype(np.float64))
    volume = np.nan_to_num(session["v"].astype(np.float64))
    open_interest = np.nan_to_num(session["oi"].astype(np.float64))
    implied_vol = session["iv"].astype(np.float64)

    # --- chain aggregates -------------------------------------------------
    atm = np.abs(strikes[:, None] - spot[None, :]).argmin(axis=0)
    rows = np.arange(count)
    call_atm = close[C.CALL, atm, rows]
    put_atm = close[C.PUT, atm, rows]
    straddle = call_atm + put_atm
    # put-call parity: forward = strike + call - put. Basis vs spot leads spot.
    basis = strikes[atm] + call_atm - put_atm - spot

    call_volume = volume[C.CALL].sum(axis=0)
    put_volume = volume[C.PUT].sum(axis=0)
    total_volume = call_volume + put_volume
    atm_volume = volume[:, atm, rows].sum(axis=0)

    near = np.abs(strikes[:, None] - spot[None, :]) <= 3.5 * 50
    call_oi = (open_interest[C.CALL] * near).sum(axis=0)
    put_oi = (open_interest[C.PUT] * near).sum(axis=0)

    atm_iv = np.nanmean(
        np.stack([implied_vol[C.CALL, atm, rows], implied_vol[C.PUT, atm, rows]]), axis=0
    )
    atm_iv = _ffill(atm_iv)

    # --- spot dynamics ----------------------------------------------------
    ret1 = _pct_change(spot, 1)
    step = np.nan_to_num(ret1)
    rv15 = _rolling(step, 15, np.std)
    rv60 = _rolling(step, 60, np.std)
    high15 = _rolling(spot, 15, np.max)
    low15 = _rolling(spot, 15, np.min)
    high60 = _rolling(spot, 60, np.max)
    low60 = _rolling(spot, 60, np.min)
    day_high = np.maximum.accumulate(spot)
    day_low = np.minimum.accumulate(spot)
    sign = np.sign(step)
    streak = np.zeros(count)
    for index in range(1, count):
        streak[index] = streak[index - 1] + sign[index] if sign[index] == sign[index - 1] else sign[index]

    volume_baseline = C.rolling_median(total_volume, 30)
    columns = {
        "ret1": ret1,
        "ret3": _pct_change(spot, 3),
        "ret5": _pct_change(spot, 5),
        "ret15": _pct_change(spot, 15),
        "rv15": rv15,
        "squeeze": rv15 / np.where(rv60 > 0, rv60, np.nan),
        "range15_pct": (high15 - low15) / spot * 100,
        "range_ratio": (high15 - low15) / np.where(high60 > low60, high60 - low60, np.nan),
        "day_pos": (spot - day_low) / np.where(day_high > day_low, day_high - day_low, np.nan),
        "dist_high15": (spot - high15) / spot * 100,
        "dist_low15": (spot - low15) / spot * 100,
        "dist_open": (spot / spot[0] - 1) * 100,
        "minute": minute,
        "streak": streak,
        "accel": _pct_change(spot, 3) - _pct_change(spot, 15) * (3 / 15),
        "vol_surge": total_volume / np.where(volume_baseline > 0, volume_baseline, np.nan),
        "vol_imbalance": (call_volume - put_volume) / np.where(total_volume > 0, total_volume, np.nan),
        "straddle_chg5": _pct_change(np.where(straddle > 0, straddle, np.nan), 5),
        "straddle_level": straddle / spot * 100,
        "atm_iv": atm_iv,
        "iv_chg5": atm_iv - np.roll(atm_iv, 5),
        "oi_ce_chg5": _pct_change(np.where(call_oi > 0, call_oi, np.nan), 5),
        "oi_pe_chg5": _pct_change(np.where(put_oi > 0, put_oi, np.nan), 5),
        "pcr_oi": put_oi / np.where(call_oi > 0, call_oi, np.nan),
        "basis": basis / spot * 10000,
        "basis_chg3": (basis - np.roll(basis, 3)) / spot * 10000,
        "atm_vol_share": atm_volume / np.where(total_volume > 0, total_volume, np.nan),
    }
    columns["iv_chg5"][:5] = np.nan
    columns["basis_chg3"][:3] = np.nan
    matrix = np.column_stack([columns[name] for name in FEATURES])
    return minute, spot, matrix, FEATURES


# Features whose sign flips when looking for a downside spike instead of upside.
SIGNED = {
    "ret1", "ret3", "ret5", "ret15", "dist_open", "streak", "accel",
    "vol_imbalance", "basis", "basis_chg3",
}
MIRRORED = {"day_pos": lambda x: 1 - x, "dist_high15": None, "dist_low15": None}


def directional(matrix, names, direction):
    """Return a copy of the matrix oriented so 'higher = more bullish for this side'."""
    if direction == "UP":
        return matrix
    out = matrix.copy()
    for index, name in enumerate(names):
        if name in SIGNED:
            out[:, index] = -out[:, index]
    out[:, names.index("day_pos")] = 1 - out[:, names.index("day_pos")]
    high = names.index("dist_high15")
    low = names.index("dist_low15")
    out[:, [high, low]] = -out[:, [low, high]]
    return out
