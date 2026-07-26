"""Cache invalidation for the storefront API.

Django owns the data; the FastAPI service caches it in Redis for five minutes.
Without this, an admin who changes a price sees no effect on the site for up to
five minutes and has no way to tell whether the edit worked — so Django clears
the keys it just invalidated.

A Redis outage must never block an admin save, so every failure here is logged
and swallowed: the cache expires on its own regardless.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Must match app/core/cache.py in the API.
CATALOGUE_PATTERNS = ("qs:regions*", "qs:countries*", "qs:country*")
CONTENT_PATTERNS = ("qs:landing*",)


def _client():
    try:
        import redis
    except ImportError:  # pragma: no cover - redis is an optional admin dep
        return None
    url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    try:
        return redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache invalidation unavailable: %s", exc)
        return None


def invalidate(*patterns: str) -> int:
    client = _client()
    if client is None:
        return 0
    removed = 0
    try:
        for pattern in patterns:
            # SCAN, never KEYS: this runs against the production keyspace.
            for key in client.scan_iter(match=pattern, count=500):
                removed += client.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache invalidation failed: %s", exc)
    return removed


def invalidate_catalogue() -> int:
    return invalidate(*CATALOGUE_PATTERNS)


def invalidate_content() -> int:
    return invalidate(*CONTENT_PATTERNS)
