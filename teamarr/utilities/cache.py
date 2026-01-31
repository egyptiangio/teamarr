"""Cache implementations with TTL support.

Two cache backends available:
- TTLCache: In-memory, fast, resets on restart
- PersistentTTLCache: Hybrid in-memory + SQLite persistence

The PersistentTTLCache uses a "load on startup, operate in memory, flush
periodically" pattern for optimal performance:
- All reads/writes hit fast in-memory cache (no lock contention)
- Background thread flushes dirty entries to SQLite every few minutes
- Survives restarts by loading from SQLite on initialization

TTL recommendations:
- Team stats: 4 hours (changes infrequently)
- Team schedules: 8 hours (games added/removed rarely)
- Events/scoreboard: 30 min today, 8 hours past/future
"""

import atexit
import json
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
    last_accessed: datetime


class TTLCache:
    """Thread-safe in-memory cache with TTL and size limit.

    Features:
    - Time-based expiration (TTL)
    - Maximum size limit with LRU eviction
    - Thread-safe operations
    - Automatic cleanup of expired entries

    Usage:
        cache = TTLCache(default_ttl_seconds=3600, max_size=10000)
        cache.set("key", value)
        result = cache.get("key")  # Returns None if expired
    """

    # Default max size (0 = unlimited)
    DEFAULT_MAX_SIZE = 10000

    def __init__(
        self,
        default_ttl_seconds: int = 3600,
        max_size: int = DEFAULT_MAX_SIZE,
    ):
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._max_size = max_size
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """Get value if exists and not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if datetime.now() > entry.expires_at:
                del self._cache[key]
                self._misses += 1
                return None
            # Update last accessed time for LRU
            entry.last_accessed = datetime.now()
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Set value with optional custom TTL."""
        ttl = timedelta(seconds=ttl_seconds) if ttl_seconds else self._default_ttl
        now = datetime.now()
        expires_at = now + ttl

        with self._lock:
            # Evict if at max size and key is new
            if self._max_size > 0 and key not in self._cache:
                self._evict_if_needed()

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=expires_at,
                last_accessed=now,
            )

    def _evict_if_needed(self) -> None:
        """Evict entries if cache is at max size. Called with lock held."""
        if self._max_size <= 0:
            return

        # First, remove expired entries
        now = datetime.now()
        expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
        for key in expired_keys:
            del self._cache[key]

        # If still at/over max, evict least recently used
        while len(self._cache) >= self._max_size:
            if not self._cache:
                break
            # Find LRU entry
            lru_key = min(self._cache.keys(), key=lambda k: self._cache[k].last_accessed)
            del self._cache[lru_key]

    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cached values."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = datetime.now()
        removed = 0
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
            for key in expired_keys:
                del self._cache[key]
                removed += 1
        return removed

    @property
    def size(self) -> int:
        """Current number of entries (including possibly expired)."""
        return len(self._cache)

    @property
    def max_size(self) -> int:
        """Maximum cache size (0 = unlimited)."""
        return self._max_size

    def stats(self) -> dict:
        """Get cache statistics."""
        now = datetime.now()
        with self._lock:
            total = len(self._cache)
            expired = sum(1 for v in self._cache.values() if now > v.expires_at)
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0
            return {
                "total_entries": total,
                "active_entries": total - expired,
                "expired_entries": expired,
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 3),
            }

    def get_all_entries(self) -> dict[str, tuple[Any, datetime]]:
        """Get all cache entries with their expiration times.

        Returns dict of key -> (value, expires_at) for serialization.
        Only returns non-expired entries.
        """
        now = datetime.now()
        with self._lock:
            return {
                k: (v.value, v.expires_at) for k, v in self._cache.items() if v.expires_at > now
            }

    def set_with_expiry(self, key: str, value: Any, expires_at: datetime) -> None:
        """Set value with explicit expiration time.

        Used when loading from persistent storage.
        """
        now = datetime.now()
        if expires_at <= now:
            return  # Already expired, don't load

        with self._lock:
            if self._max_size > 0 and key not in self._cache:
                self._evict_if_needed()

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=expires_at,
                last_accessed=now,
            )


class PersistentTTLCache:
    """Hybrid in-memory + SQLite cache with background persistence.

    Optimized for high-concurrency workloads (100+ parallel workers):
    - All reads/writes use fast in-memory TTLCache (no SQLite contention)
    - Background thread flushes dirty entries to SQLite periodically
    - On startup, loads existing cache from SQLite
    - On shutdown, final flush ensures persistence

    Usage:
        cache = PersistentTTLCache(flush_interval_seconds=120)
        cache.set("key", value)
        result = cache.get("key")
        # Cache auto-flushes in background
        # Call cache.flush() for immediate persistence
    """

    # Default flush interval (2 minutes)
    DEFAULT_FLUSH_INTERVAL = 120
    # Default max size for memory cache (prevents runaway memory)
    DEFAULT_MAX_SIZE = 50000

    def __init__(
        self,
        default_ttl_seconds: int = 3600,
        flush_interval_seconds: int = DEFAULT_FLUSH_INTERVAL,
        max_size: int = DEFAULT_MAX_SIZE,
    ):
        self._memory_cache = TTLCache(
            default_ttl_seconds=default_ttl_seconds,
            max_size=max_size,
        )
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._flush_interval = flush_interval_seconds

        # Track dirty keys that need to be flushed
        self._dirty_keys: set[str] = set()
        self._deleted_keys: set[str] = set()
        self._dirty_lock = threading.Lock()
        self._flush_lock = threading.Lock()

        # Background flush thread
        self._flush_timer: threading.Timer | None = None
        self._shutdown = False

        # Load from SQLite on startup
        self._load_from_sqlite()

        # Start background flush thread
        self._schedule_flush()

        # Register shutdown handler
        atexit.register(self._shutdown_flush)

    def _load_from_sqlite(self) -> None:
        """Load non-expired entries from SQLite into memory."""
        from teamarr.database.connection import get_db

        now = datetime.now()
        loaded = 0
        expired = 0

        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT cache_key, data_json, expires_at FROM service_cache"
                ).fetchall()

            for row in rows:
                try:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if expires_at > now:
                        value = json.loads(row["data_json"])
                        self._memory_cache.set_with_expiry(row["cache_key"], value, expires_at)
                        loaded += 1
                    else:
                        expired += 1
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("[CACHE] Failed to load cache entry: %s", e)

            if loaded > 0 or expired > 0:
                logger.info(
                    "[CACHE] Loaded %d entries from SQLite (skipped %d expired)", loaded, expired
                )
        except Exception as e:
            logger.warning("[CACHE] Failed to load cache from SQLite: %s", e)

    def _schedule_flush(self) -> None:
        """Schedule the next background flush."""
        if self._shutdown:
            return

        self._flush_timer = threading.Timer(self._flush_interval, self._background_flush)
        self._flush_timer.daemon = True
        self._flush_timer.name = "CacheFlush"
        self._flush_timer.start()

    def _background_flush(self) -> None:
        """Background flush handler."""
        if self._shutdown:
            return

        try:
            self.flush()
        except Exception as e:
            logger.error("[CACHE] Background flush failed: %s", e)
        finally:
            self._schedule_flush()

    def _shutdown_flush(self) -> None:
        """Final flush on shutdown."""
        self._shutdown = True
        if self._flush_timer:
            self._flush_timer.cancel()

        try:
            self.flush()
        except Exception as e:
            logger.error("[CACHE] Shutdown flush failed: %s", e)

    def get(self, key: str) -> Any | None:
        """Get value if exists and not expired."""
        return self._memory_cache.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Set value with optional custom TTL."""
        self._memory_cache.set(key, value, ttl_seconds)

        # Mark as dirty for next flush
        with self._dirty_lock:
            self._dirty_keys.add(key)
            self._deleted_keys.discard(key)

    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        self._memory_cache.delete(key)

        # Mark for deletion in SQLite
        with self._dirty_lock:
            self._deleted_keys.add(key)
            self._dirty_keys.discard(key)

    def clear(self) -> None:
        """Clear all cached values."""
        from teamarr.database.connection import get_db

        self._memory_cache.clear()

        with self._dirty_lock:
            self._dirty_keys.clear()
            self._deleted_keys.clear()

        # Clear SQLite immediately
        try:
            with get_db() as conn:
                conn.execute("DELETE FROM service_cache")
            logger.debug("[CACHE] Cleared service cache")
        except Exception as e:
            logger.error("[CACHE] Failed to clear SQLite cache: %s", e)

    def cleanup_expired(self) -> int:
        """Remove expired entries from memory and SQLite."""
        from teamarr.database.connection import get_db

        # Clean memory
        removed = self._memory_cache.cleanup_expired()

        # Clean SQLite
        try:
            now = datetime.now().isoformat()
            with get_db() as conn:
                cursor = conn.execute("DELETE FROM service_cache WHERE expires_at < ?", (now,))
                removed += cursor.rowcount
        except Exception as e:
            logger.error("[CACHE] Failed to cleanup SQLite expired entries: %s", e)

        return removed

    def flush(self) -> int:
        """Flush dirty entries to SQLite.

        Returns number of entries written.
        Call this after EPG generation for immediate persistence.
        """
        from teamarr.database.connection import get_db

        with self._flush_lock:
            # Atomically grab dirty/deleted keys
            with self._dirty_lock:
                dirty_keys = self._dirty_keys.copy()
                deleted_keys = self._deleted_keys.copy()
                self._dirty_keys.clear()
                self._deleted_keys.clear()

            if not dirty_keys and not deleted_keys:
                return 0

            # Get current values for dirty keys
            entries = self._memory_cache.get_all_entries()
            to_write = {k: entries[k] for k in dirty_keys if k in entries}

            written = 0
            deleted = 0

            try:
                with get_db() as conn:
                    # Delete removed keys
                    for key in deleted_keys:
                        conn.execute("DELETE FROM service_cache WHERE cache_key = ?", (key,))
                        deleted += 1

                    # Upsert dirty keys
                    now = datetime.now().isoformat()
                    for key, (value, expires_at) in to_write.items():
                        try:
                            data_json = json.dumps(value, default=str)
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO service_cache
                                (cache_key, data_json, expires_at, created_at)
                                VALUES (?, ?, ?, ?)
                                """,
                                (key, data_json, expires_at.isoformat(), now),
                            )
                            written += 1
                        except (TypeError, ValueError) as e:
                            logger.warning("[CACHE] Failed to serialize key %s: %s", key, e)

                if written > 0 or deleted > 0:
                    logger.debug("[CACHE] Flush: %d written, %d deleted", written, deleted)

            except Exception as e:
                logger.error("[CACHE] Flush failed: %s", e)
                # Put keys back for retry on next flush
                with self._dirty_lock:
                    self._dirty_keys.update(dirty_keys)
                    self._deleted_keys.update(deleted_keys)

            return written

    @property
    def size(self) -> int:
        """Current number of entries in memory."""
        return self._memory_cache.size

    def stats(self) -> dict:
        """Get cache statistics."""
        base_stats = self._memory_cache.stats()

        with self._dirty_lock:
            pending_writes = len(self._dirty_keys)
            pending_deletes = len(self._deleted_keys)

        base_stats.update(
            {
                "persistent": True,
                "pending_writes": pending_writes,
                "pending_deletes": pending_deletes,
                "flush_interval_seconds": self._flush_interval,
            }
        )

        return base_stats


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

    Tiered caching - past events are final (long TTL), today needs fresh data.

    Past:       30 days (final scores don't change)
    Today:      30 minutes (flex times, live scores)
    Tomorrow:   4 hours (flex scheduling possible)
    Days 2-7:   8 hours (mostly stable)
    Days 8+:    8 hours (playoffs may appear)
    """
    from datetime import date

    today = date.today()
    days_from_today = (target_date - today).days

    if days_from_today < 0:  # Past
        return 30 * 24 * 60 * 60  # 30 days
    elif days_from_today == 0:  # Today
        return 30 * 60  # 30 minutes
    elif days_from_today == 1:  # Tomorrow
        return 4 * 60 * 60  # 4 hours
    else:  # Days 2+
        return 8 * 60 * 60  # 8 hours
