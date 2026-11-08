"""Small cache-backed guards for scoped integration endpoints."""

import hashlib

from django.core.cache import cache


def rate_limit_exceeded(*, scope, identity, limit, window_seconds):
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    key = f"arc-rate:{scope}:{digest}"
    if cache.add(key, 1, timeout=window_seconds):
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        return False
    return count > limit
