"""Bounded derivatives context derived from stored option evidence."""

from .models import IndexOptionCandle


MAX_IV_HISTORY_ROWS = 10_000
MIN_IV_PERCENTILE_ROWS = 20


def iv_percentile_rows(underlyings):
    results = []
    for underlying in sorted(set(underlyings)):
        rows = list(
            IndexOptionCandle.objects.filter(
                underlying=underlying,
                implied_volatility__gt=0,
            ).order_by("-timestamp").values(
                "timestamp",
                "implied_volatility",
                "relative_strike",
                "option_type",
            )[:MAX_IV_HISTORY_ROWS]
        )
        if not rows:
            continue
        current = float(rows[0]["implied_volatility"])
        values = [float(row["implied_volatility"]) for row in rows]
        percentile = None
        if len(values) >= MIN_IV_PERCENTILE_ROWS:
            percentile = (
                sum(value <= current for value in values)
                / len(values)
                * 100
            )
        results.append({
            "underlying": underlying,
            "current_iv": current,
            "percentile": percentile,
            "observations": len(values),
            "relative_strike": rows[0]["relative_strike"],
            "option_type": rows[0]["option_type"],
            "source_at": rows[0]["timestamp"],
            "history_start": rows[-1]["timestamp"],
            "history_capped": len(rows) == MAX_IV_HISTORY_ROWS,
        })
    return results
