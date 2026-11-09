"""Bounded derivatives context derived from stored option evidence."""

from django.core.cache import cache

from .models import IndexOptionCandle


MAX_IV_HISTORY_ROWS = 10_000
MIN_IV_PERCENTILE_ROWS = 20
IV_PERCENTILE_CACHE_SECONDS = 60


def iv_percentile_rows(underlyings):
    results = []
    for underlying in sorted(set(underlyings)):
        queryset = IndexOptionCandle.objects.filter(
            underlying=underlying,
            implied_volatility__gt=0,
        ).order_by("-timestamp")
        latest_timestamp = queryset.values_list("timestamp", flat=True).first()
        if latest_timestamp is None:
            continue
        cache_key = (
            f"arc:iv-percentile:v1:{underlying}:"
            f"{latest_timestamp.timestamp()}"
        )
        cached = cache.get(cache_key)
        if cached is not None:
            results.append(cached)
            continue
        rows = list(
            queryset.values(
                "timestamp",
                "implied_volatility",
                "relative_strike",
                "option_type",
            )[:MAX_IV_HISTORY_ROWS]
        )
        current = float(rows[0]["implied_volatility"])
        values = [float(row["implied_volatility"]) for row in rows]
        percentile = None
        if len(values) >= MIN_IV_PERCENTILE_ROWS:
            percentile = (
                sum(value <= current for value in values)
                / len(values)
                * 100
            )
        result = {
            "underlying": underlying,
            "current_iv": current,
            "percentile": percentile,
            "observations": len(values),
            "relative_strike": rows[0]["relative_strike"],
            "option_type": rows[0]["option_type"],
            "source_at": rows[0]["timestamp"],
            "history_start": rows[-1]["timestamp"],
            "history_capped": len(rows) == MAX_IV_HISTORY_ROWS,
        }
        cache.set(cache_key, result, IV_PERCENTILE_CACHE_SECONDS)
        results.append(result)
    return results
