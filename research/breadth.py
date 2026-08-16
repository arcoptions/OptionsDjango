"""Look inside the index: effective weights, then breadth from the constituents.

NIFTY is a weighted sum, so it cannot move unless its constituents move. That
makes them causally upstream of every signal tried so far, and unlike the option
chain -- which the anatomy study found blind at turning points -- they are a
genuinely new information source rather than a rearrangement of index price.

Weights are not read from a published file. They are solved for: over a trailing
window, find the non-negative w that best explains index minute returns as a
combination of constituent minute returns. That self-calibrates, survives index
reconstitution, and automatically down-weights any symbol we failed to capture.

The features it exports all answer variants of one question -- is this index move
being made by the whole market or by two stocks?
"""
import os
import sys

import numpy as np
from scipy.optimize import nnls

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C

STOCKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "STOCKS")

# Sector map for the divergence feature. Financials and IT are roughly 35% and
# 13% of the index; when they pull against each other the index tends to chop.
SECTORS = {
    "HDFCBANK": "FIN", "ICICIBANK": "FIN", "AXISBANK": "FIN", "KOTAKBANK": "FIN",
    "SBIN": "FIN", "BAJFINANCE": "FIN", "BAJAJFINSV": "FIN", "INDUSINDBK": "FIN",
    "SHRIRAMFIN": "FIN", "HDFCLIFE": "FIN", "SBILIFE": "FIN", "JIOFIN": "FIN",
    "INFY": "IT", "TCS": "IT", "HCLTECH": "IT", "TECHM": "IT", "WIPRO": "IT",
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "COALINDIA": "ENERGY",
    "NTPC": "UTIL", "POWERGRID": "UTIL",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "TATACONSUM": "FMCG",
    "MARUTI": "AUTO", "M&M": "AUTO", "TATAMOTORS": "AUTO", "BAJAJ-AUTO": "AUTO",
    "EICHERMOT": "AUTO", "HEROMOTOCO": "AUTO",
    "SUNPHARMA": "PHARMA", "CIPLA": "PHARMA", "DRREDDY": "PHARMA",
    "APOLLOHOSP": "PHARMA",
    "TATASTEEL": "METAL", "JSWSTEEL": "METAL", "HINDALCO": "METAL",
    "ULTRACEMCO": "CEMENT", "GRASIM": "CEMENT", "ADANIENT": "INFRA",
    "ADANIPORTS": "INFRA", "LT": "INFRA", "BEL": "INFRA",
    "BHARTIARTL": "TELECOM", "TITAN": "CONS", "TRENT": "CONS",
    "ASIANPAINT": "CONS", "ETERNAL": "CONS",
}


def stock_dates():
    import glob
    return sorted(os.path.basename(p)[:-4]
                  for p in glob.glob(os.path.join(STOCKS, "*.npz")))


def load_stocks(date):
    return np.load(os.path.join(STOCKS, f"{date}.npz"))


def _returns(prices):
    """Minute log returns along the last axis, zero where a price is missing."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.diff(np.log(prices), axis=-1)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def session_matrix(date):
    """(symbols, stock minute returns, index minute returns, volume) for a session."""
    stocks = load_stocks(date)
    symbols = [str(s) for s in stocks["symbols"]]
    closes = stocks["close"].astype(np.float64)
    closes = _forward_fill(closes)
    session = C.load(date)
    spot = session["spot"].astype(np.float64)
    width = min(closes.shape[1], len(spot))
    stock_r = _returns(closes[:, :width])
    index_r = _returns(_forward_fill(spot[None, :width]))[0]
    return symbols, stock_r, index_r, stocks["volume"].astype(np.float64)[:, :width]


def _forward_fill(matrix):
    out = np.array(matrix, dtype=np.float64, copy=True)
    for row in range(out.shape[0]):
        series = out[row]
        valid = np.isfinite(series)
        if not valid.any():
            continue
        index = np.where(valid, np.arange(len(series)), 0)
        np.maximum.accumulate(index, out=index)
        out[row] = series[index]
        out[row, : np.argmax(valid)] = series[valid][0]
    return out


def fit_weights(dates, symbols=None):
    """Non-negative weights that best reproduce index returns from constituents.

    Solved once over the whole span. The residual is reported because it is the
    honest check on the whole idea: if the fit cannot explain the index, the
    capture is missing something and every breadth number below is suspect.
    """
    rows, targets = [], []
    reference = symbols
    for date in dates:
        try:
            names, stock_r, index_r, _volume = session_matrix(date)
        except (OSError, KeyError):
            continue
        if reference is None:
            reference = names
        if names != reference:
            continue
        rows.append(stock_r.T)
        targets.append(index_r)
    if not rows:
        return reference, None, None
    design = np.vstack(rows)
    target = np.concatenate(targets)
    keep = np.isfinite(target) & np.isfinite(design).all(axis=1)
    weights, _residual = nnls(design[keep], target[keep])
    predicted = design[keep] @ weights
    explained = 1.0 - np.var(target[keep] - predicted) / np.var(target[keep])
    return reference, weights, explained


def features(stock_r, index_r, weights, sectors, window):
    """Breadth features per minute, each computed from the trailing window only."""
    count = stock_r.shape[1]
    rolled = np.zeros_like(stock_r)
    for index in range(count):
        start = max(0, index - window + 1)
        rolled[:, index] = stock_r[:, start:index + 1].sum(axis=1)
    index_rolled = np.array([index_r[max(0, i - window + 1):i + 1].sum()
                             for i in range(count)])

    weight = weights / weights.sum() if weights.sum() > 0 else weights
    contribution = weight[:, None] * rolled
    total = np.abs(contribution).sum(axis=0)
    total[total == 0] = np.nan

    participation = (weight[:, None] * (rolled > 0)).sum(axis=0)
    concentration = ((contribution / total) ** 2).sum(axis=0)
    dispersion = rolled.std(axis=0)
    impulse = (weight[:, None] * stock_r).sum(axis=0)

    financial = sectors == "FIN"
    technology = sectors == "IT"
    sector_gap = np.zeros(count)
    if financial.any() and technology.any():
        fin = (weight[financial, None] * rolled[financial]).sum(axis=0) / max(
            weight[financial].sum(), 1e-9)
        tech = (weight[technology, None] * rolled[technology]).sum(axis=0) / max(
            weight[technology].sum(), 1e-9)
        sector_gap = fin - tech

    return {
        "participation": participation,
        "concentration": np.nan_to_num(concentration, nan=1.0),
        "dispersion": dispersion,
        "impulse": impulse,
        "sector_gap": sector_gap,
        "index_move": index_rolled,
    }
