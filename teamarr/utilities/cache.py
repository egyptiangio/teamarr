"""In-memory cache with TTL support.

Simple cache for API responses. Provides significant performance improvement
by avoiding redundant API calls for the same data.

TTL recommendations:
- Team stats: 6 hours (changes infrequently)
- Team schedules: 1 hour (games added/removed rarely)
- Events/scoreboard: 5 minutes (live score updates)
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached value with expiration."""

    value: Any
    expires_at: datetime


class TTLCache:
    """Thread-safe in-memory cache with TTL.

    Usage:
        cache = TTLCache(default_ttl_seconds=3600)
        cache.set("key", value)
        result = cache.get("key")  # Returns None if expired
    """

    def __init__(self, default_ttl_seconds: int = 3600):
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Get value if exists and not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if datetime.now() > entry.expires_at:
                del self._cache[key]
                return None
            return entry.value

    def set(
        self, key: str, value: Any, ttl_seconds: int | None = None
    ) -> None:
        """Set value with optional custom TTL."""
        ttl = timedelta(seconds=ttl_seconds) if ttl_seconds else self._default_ttl
        expires_at = datetime.now() + ttl
        with self._lock:
            self._cache[key] = CacheEntry(value=value, expires_at=expires_at)

    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cached values."""
        with self._lock:
            self._cache.clear()

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = datetime.now()
        removed = 0
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items() if now > v.expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
                removed += 1
        return removed

    @property
    def size(self) -> int:
        """Current number of entries (including possibly expired)."""
        return len(self._cache)

    def stats(self) -> dict:
        """Get cache statistics."""
        now = datetime.now()
        with self._lock:
            total = len(self._cache)
            expired = sum(1 for v in self._cache.values() if now > v.expires_at)
            return {
                "total_entries": total,
                "active_entries": total - expired,
                "expired_entries": expired,
            }


# Cache TTL constants (seconds)
# Optimized for typical EPG regeneration patterns (hourly to 24hr)
CACHE_TTL_TEAM_STATS = 4 * 60 * 60  # 4 hours - record/standings change infrequently
CACHE_TTL_SCHEDULE = 8 * 60 * 60  # 8 hours - team schedules rarely change
CACHE_TTL_EVENTS = 8 * 60 * 60  # 8 hours - scoreboard (league events list)
CACHE_TTL_SINGLE_EVENT = 30 * 60  # 30 minutes - individual event (scores, odds)
CACHE_TTL_TEAM_INFO = 24 * 60 * 60  # 24 hours - static team data


def make_cache_key(*parts: str) -> str:
    """Create a cache key from parts."""
    return ":".join(str(p) for p in parts)


def get_events_cache_ttl(target_date) -> int:
    """Get cache TTL for events based on date proximity.

    Tiered caching - past events are final, today needs fresh data.

    Past:       8 hours (final, but allow eventual refresh)
    Today:      30 minutes (flex times, live scores)
    Tomorrow:   4 hours (flex scheduling possible)
    Days 2-7:   8 hours (mostly stable)
    Days 8+:    8 hours (playoffs may appear)
    """
    from datetime import date

    today = date.today()
    days_from_today = (target_date - today).days

    if days_from_today < 0:  # Past
        return 8 * 60 * 60  # 8 hours
    elif days_from_today == 0:  # Today
        return 30 * 60  # 30 minutes
    elif days_from_today == 1:  # Tomorrow
        return 4 * 60 * 60  # 4 hours
    else:  # Days 2+
        return 8 * 60 * 60  # 8 hours
