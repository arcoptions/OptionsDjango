"""Do the constituents tell us anything the index price does not already say?

This is the test that decides whether capturing fifty stocks was worth it, and
it has one trap that has to be avoided. Breadth is mechanically correlated with
the index -- the index *is* the weighted sum -- so any breadth feature will look
predictive on its own for the same reason a thermometer predicts summer. The
only honest question is incremental: holding index momentum fixed, does knowing
how the move was distributed across stocks change what happens next?

So every feature is scored twice. Once raw, against the same forward move, and
once inside buckets of index momentum, where the mechanical correlation is held
constant and only the extra information can show up.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breadth as B
import common as C

WINDOW = 15  # minutes of trailing constituent behaviour
HORIZON = 15  # minutes of forward index move being predicted
WARMUP, TAIL = 30, 30


def auc(positive, negative):
    """Rank-biserial AUC; 0.5 is no information."""
    positive = positive[np.isfinite(positive)]
    negative = negative[np.isfinite(negative)]
    if len(positive) < 20 or len(negative) < 20:
        return np.nan
    joined = np.concatenate([positive, negative])
    ranks = joined.argsort().argsort().astype(np.float64) + 1
    total = ranks[:len(positive)].sum()
    return (total - len(positive) * (len(positive) + 1) / 2) / (
        len(positive) * len(negative))


def collect(dates, symbols, weights):
    sectors = np.array([B.SECTORS.get(s, "OTHER") for s in symbols])
    rows = {name: [] for name in
            ("participation", "concentration", "dispersion", "impulse",
             "sector_gap", "index_move")}
    forward = []
    for date in dates:
        try:
            names, stock_r, index_r, _volume = B.session_matrix(date)
        except (OSError, KeyError):
            continue
        if names != symbols:
            continue
        table = B.features(stock_r, index_r, weights, sectors, WINDOW)
        count = len(index_r)
        ahead = np.full(count, np.nan)
        for index in range(count - HORIZON):
            ahead[index] = index_r[index + 1:index + 1 + HORIZON].sum()
        usable = slice(WARMUP, count - TAIL)
        for name in rows:
            rows[name].append(table[name][usable])
        forward.append(ahead[usable])
    return ({name: np.concatenate(values) for name, values in rows.items()},
            np.concatenate(forward))


def report(features, forward, label):
    up = forward > 0
    down = forward < 0
    print(f"\n{label}   n = {np.isfinite(forward).sum():,}")
    print(f"  {'feature':<18}{'AUC':>8}{'|edge|':>9}")
    for name, values in features.items():
        score = auc(values[up], values[down])
        print(f"  {name:<18}{score:>8.3f}{abs(score - 0.5):>9.3f}")


def main():
    stock_dates = set(B.stock_dates())
    dates = [d for d in C.session_dates() if d in stock_dates]
    print(f"{len(dates)} sessions with both constituent and option data")
    if len(dates) < 30:
        print("not enough overlap to test")
        return

    symbols, weights, explained = B.fit_weights(dates)
    if weights is None:
        print("weight fit failed")
        return
    print(f"\nfitted weights explain {100 * explained:.1f}% of index minute-return "
          f"variance")
    order = np.argsort(weights)[::-1]
    normalised = weights / weights.sum()
    print("  top 10 by fitted weight: " + ", ".join(
        f"{symbols[i]} {100*normalised[i]:.1f}%" for i in order[:10]))
    print(f"  fitted weights sum to {weights.sum():.3f}")

    features, forward = collect(dates, symbols, weights)
    report(features, forward, "raw AUC for direction of the next 15 minutes")

    print("\nincremental test: same AUC inside buckets of index momentum")
    momentum = features["index_move"]
    edges = np.nanquantile(momentum, [0.2, 0.4, 0.6, 0.8])
    buckets = np.digitize(momentum, edges)
    for bucket in range(5):
        mask = buckets == bucket
        if mask.sum() < 500:
            continue
        subset = {name: values[mask] for name, values in features.items()
                  if name != "index_move"}
        report(subset, forward[mask],
               f"  index momentum bucket {bucket + 1}/5 "
               f"(mean {momentum[mask].mean()*100:+.3f}%)")


if __name__ == "__main__":
    main()
