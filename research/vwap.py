"""VWAP for an index that prints no volume, and a check that it is honest.

NIFTY has no traded quantity of its own, so a VWAP has to be built from
something that does trade. Two sources exist here and they are not equally
available:

  constituents   49 stocks, 1-minute close and quantity, all 246 sessions.
                 Their summed rupee turnover is a defensible measure of how hard
                 the index traded, because the index *is* those stocks.
  the future     real single-instrument volume, but only 56 sessions. Dhan's
                 instrument master lists live contracts only, so once a contract
                 expires its history stops being reachable. Three contracts are
                 listed, which is about three months of tape.

So the future cannot drive a strategy over our sample. What it can do is settle
whether the constituent proxy is trustworthy, by comparing the two on the 56
sessions where both exist. If they agree the proxy is used across all 246 with a
measured error bar rather than a hopeful assumption.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breadth as B
import common as C
import indicators as I

FUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "FUT")

_SYNTHETIC = {}
_FUTURES = {}


def turnover(date):
    """Summed constituent rupee turnover per minute."""
    data = B.load_stocks(date)
    close = np.asarray(data["close"], dtype=np.float64)
    volume = np.asarray(data["volume"], dtype=np.float64)
    return np.nansum(np.where(np.isfinite(close) & np.isfinite(volume),
                              close * volume, 0.0), axis=0)


def synthetic(date, spot=None):
    """Session VWAP of the index, weighted by constituent turnover."""
    if date in _SYNTHETIC:
        return _SYNTHETIC[date]
    if spot is None:
        spot = np.asarray(C.load(date)["spot"], dtype=float)
    try:
        weights = turnover(date)
    except (OSError, KeyError):
        _SYNTHETIC[date] = None
        return None
    value = I.vwap(spot, weights)
    _SYNTHETIC[date] = value
    return value


def futures_dates():
    import glob
    return sorted(os.path.basename(path)[:-4]
                  for path in glob.glob(os.path.join(FUT, "*.npz")))


def futures(date):
    """Session VWAP of the front-month future, from its own traded volume."""
    if date in _FUTURES:
        return _FUTURES[date]
    path = os.path.join(FUT, f"{date}.npz")
    if not os.path.exists(path):
        _FUTURES[date] = None
        return None
    raw = np.load(path)
    close = np.asarray(raw["close"], dtype=float)
    volume = np.asarray(raw["volume"], dtype=float)
    # Volume arrives cumulative-per-candle from Dhan, i.e. already per-minute.
    value = I.vwap(close, volume)
    _FUTURES[date] = value
    return value


def main():
    overlap = [date for date in futures_dates() if date in set(C.session_dates())]
    print(f"futures sessions {len(futures_dates())}, "
          f"option sessions {len(C.session_dates())}, overlap {len(overlap)}\n")
    if not overlap:
        print("no overlap; cannot validate")
        return

    print("Does the constituent-turnover VWAP agree with the real futures VWAP?")
    print("Basis is futures minus index, which is a real thing (cost of carry),")
    print("so the level differs by design. What matters is whether they move")
    print("together and whether 'above VWAP' means the same thing on both.\n")

    gaps, correlations, agreements, spreads = [], [], [], []
    for date in overlap:
        spot = np.asarray(C.load(date)["spot"], dtype=float)
        mine = synthetic(date, spot)
        theirs = futures(date)
        if mine is None or theirs is None:
            continue
        raw = np.load(os.path.join(FUT, f"{date}.npz"))
        future_close = np.asarray(raw["close"], dtype=float)
        length = min(len(mine), len(theirs), len(spot), len(future_close))
        if length < 100:
            continue
        mine, theirs = mine[:length], theirs[:length]
        spot, future_close = spot[:length], future_close[:length]
        good = (np.isfinite(mine) & np.isfinite(theirs)
                & np.isfinite(spot) & np.isfinite(future_close))
        if good.sum() < 100:
            continue
        basis = float(np.median(future_close[good] - spot[good]))
        spreads.append(basis)
        # Compare like with like: strip the basis before measuring the gap.
        gaps.append(float(np.median(np.abs((theirs[good] - basis) - mine[good]))))
        correlations.append(float(np.corrcoef(theirs[good] - basis, mine[good])[0, 1]))
        # The only use the strategies make of VWAP is the side price is on.
        agreements.append(float(((spot[good] > mine[good])
                                 == (future_close[good] > theirs[good])).mean()))

    print(f"  sessions compared              {len(gaps)}")
    print(f"  median futures basis           {np.median(spreads):>7.1f} index points")
    print(f"  median |gap| after basis       {np.median(gaps):>7.2f} index points")
    print(f"  median correlation             {np.median(correlations):>7.4f}")
    print(f"  'price above VWAP' agrees      {100 * np.median(agreements):>7.1f}% of minutes")
    print(f"  worst session agreement        {100 * min(agreements):>7.1f}%")
    print()
    verdict = ("usable across all 246 sessions"
               if np.median(agreements) > 0.90
               else "NOT reliable; strategies using it must be read with care")
    print(f"  verdict: the constituent VWAP is {verdict}")


if __name__ == "__main__":
    main()
