"""What is measurably different at a turning point, using only causal data.

The chart circles are hindsight labels, so the honest question is not "what does
a top look like" but "at the moment a top is confirmed, is anything in the
option chain different from an ordinary bar". This compares the 27 causal
features at confirmation bars against every other bar of the same sessions, and
scores each one by how far apart the two distributions sit.

Separation is reported as the rank-biserial statistic (AUC): 0.50 means the
feature cannot tell the two apart at all, and anything under about 0.55 is
noise at this sample size.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C
import features as F
from swing_trade import K, signals

BAR = 5
MINIMUM_SWING = 50


def auc(positive, negative):
    """P(a random positive ranks above a random negative)."""
    both = np.concatenate([positive, negative])
    order = both.argsort().argsort() + 1.0
    rank_sum = order[: len(positive)].sum()
    return (rank_sum - len(positive) * (len(positive) + 1) / 2) / (
        len(positive) * len(negative)
    )


def main():
    dates = C.session_dates()
    tops, bottoms, ordinary = [], [], []
    for date in dates:
        built = F.build(date)
        if not built:
            continue
        _minutes, _spot, matrix, names = built
        marked = set()
        for signal in signals(date, MINIMUM_SWING):
            row = signal["row"]
            if row >= len(matrix):
                continue
            marked.add(row)
            (tops if signal["kind"] == "H" else bottoms).append(matrix[row])
        for row in range(BAR * (K + 1), min(330, len(matrix))):
            if row not in marked:
                ordinary.append(matrix[row])

    tops = np.array(tops)
    bottoms = np.array(bottoms)
    ordinary = np.array(ordinary)
    print(f"{len(tops)} confirmed tops, {len(bottoms)} confirmed bottoms, "
          f"{len(ordinary)} ordinary bars\n")

    rows = []
    for column, name in enumerate(names):
        base = ordinary[:, column]
        base = base[~np.isnan(base)]
        scores = []
        for group in (tops, bottoms):
            values = group[:, column]
            values = values[~np.isnan(values)]
            scores.append(auc(values, base) if len(values) > 20 else 0.5)
        rows.append((name, scores[0], scores[1], max(abs(s - 0.5) for s in scores)))

    rows.sort(key=lambda item: -item[3])
    header = f"{'feature':<18}{'AUC top':>10}{'AUC bottom':>13}{'separation':>12}"
    print(header)
    print("-" * len(header))
    for name, top, bottom, spread in rows:
        flag = "  *" if spread >= 0.05 else ""
        print(f"{name:<18}{top:>10.3f}{bottom:>13.3f}{spread:>12.3f}{flag}")

    print("\n* marks a feature that separates by 5 points of AUC or more.")
    print("Anything without a star is indistinguishable from an ordinary bar.")


if __name__ == "__main__":
    main()
