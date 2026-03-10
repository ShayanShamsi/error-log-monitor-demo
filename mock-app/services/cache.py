"""In-process cache service."""

import time

_cache: dict[str, tuple] = {}  # key -> (value, expires_at)


def set(key: str, value, ttl_seconds: int = 300) -> None:
    """Store a value in cache with TTL."""
    _cache[key] = (value, time.time() + ttl_seconds)


def get(key: str):
    """Retrieve cached value; returns None if missing or expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        # BUG: expired entries are never removed — memory leak
        # Should call `del _cache[key]` here
        return None
    return value


def get_stats() -> dict:
    """Return cache statistics."""
    now = time.time()
    live = sum(1 for _, (_, exp) in _cache.items() if exp > now)
    # BUG: total includes expired (leaked) entries — misleading metric
    return {
        "total_keys": len(_cache),
        "live_keys": live,
        "expired_leaked": len(_cache) - live,
    }


def invalidate_user(user_id: str) -> int:
    """Remove all cache entries for a given user. Returns count removed."""
    # BUG: mutating dict while iterating — RuntimeError in Python 3
    count = 0
    for key in _cache:
        if key.startswith(f"user:{user_id}:"):
            del _cache[key]
            count += 1
    return count
