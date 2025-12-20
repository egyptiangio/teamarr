"""Stream match cache for EPG generation optimization.

Caches successful stream-to-event matches to avoid expensive matching
on every EPG generation run.

Fingerprint = hash(group_id + stream_id + stream_name)
When stream name changes, fingerprint changes -> fresh match occurs.

Usage:
    cache = StreamMatchCache(get_db)

    # Check cache before matching
    cached = cache.get(group_id, stream_id, stream_name)
    if cached:
        event_id, league, cached_data = cached
        # Use cached match, refresh dynamic fields only
    else:
        # Do full matching
        event = match_stream(stream_name)
        # Cache the result
        cache.set(group_id, stream_id, stream_name, event.id, league, event_data)
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from teamarr.core import Event

logger = logging.getLogger(__name__)


def compute_fingerprint(group_id: int, stream_id: int, stream_name: str) -> str:
    """Compute SHA256 fingerprint for cache lookup.

    Args:
        group_id: Event group ID
        stream_id: Stream ID from provider
        stream_name: Exact stream name

    Returns:
        16-character hex hash
    """
    key = f"{group_id}:{stream_id}:{stream_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class StreamCacheEntry:
    """Cached match result."""

    event_id: str
    league: str
    cached_data: dict[str, Any]


class StreamMatchCache:
    """Manages stream fingerprint cache for EPG optimization.

    Stores successful stream-to-event matches with static event data.
    Dynamic fields (scores, status) are refreshed from API on each run.
    """

    # Purge entries not seen in this many generations
    PURGE_AFTER_GENERATIONS = 5

    def __init__(self, get_connection: Callable):
        """Initialize cache with database connection factory.

        Args:
            get_connection: Function that returns a database connection
        """
        self._get_connection = get_connection
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "purged": 0,
        }

    def get(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
    ) -> StreamCacheEntry | None:
        """Look up cached match for a stream.

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name

        Returns:
            StreamCacheEntry if found, None otherwise
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT event_id, league, cached_event_data
                FROM stream_match_cache
                WHERE fingerprint = ?
                """,
                (fingerprint,),
            )
            row = cursor.fetchone()

            if row:
                self._stats["hits"] += 1
                logger.debug(f"[CACHE HIT] stream_id={stream_id} -> event_id={row['event_id']}")
                return StreamCacheEntry(
                    event_id=row["event_id"],
                    league=row["league"],
                    cached_data=json.loads(row["cached_event_data"]),
                )

            self._stats["misses"] += 1
            return None

    def set(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
        event_id: str,
        league: str,
        cached_data: dict[str, Any],
        generation: int,
    ) -> bool:
        """Cache a successful stream-to-event match.

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name
            event_id: Matched event ID
            league: Detected league code
            cached_data: Dict with static event data for template vars
            generation: Current EPG generation counter

        Returns:
            True if cached successfully
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)
        cached_json = json.dumps(cached_data, default=_json_serializer)

        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO stream_match_cache
                        (fingerprint, group_id, stream_id, stream_name,
                         event_id, league, cached_event_data, last_seen_generation,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (fingerprint)
                    DO UPDATE SET
                        event_id = excluded.event_id,
                        league = excluded.league,
                        cached_event_data = excluded.cached_event_data,
                        last_seen_generation = excluded.last_seen_generation,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        fingerprint,
                        group_id,
                        stream_id,
                        stream_name,
                        event_id,
                        league,
                        cached_json,
                        generation,
                    ),
                )
                conn.commit()
                self._stats["sets"] += 1
                logger.debug(f"[CACHE SET] stream_id={stream_id} -> event_id={event_id}")
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error caching stream match: {e}")
            return False

    def touch(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
        generation: int,
    ) -> bool:
        """Update last_seen_generation for a cached entry.

        Call this when using a cached match to keep it fresh.

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name
            generation: Current EPG generation counter

        Returns:
            True if updated
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE stream_match_cache
                    SET last_seen_generation = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE fingerprint = ?
                    """,
                    (generation, fingerprint),
                )
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.warning(f"[CACHE] touch failed: {e}")
            return False

    def purge_stale(self, current_generation: int) -> int:
        """Remove entries not seen in the last N generations.

        Args:
            current_generation: Current EPG generation counter

        Returns:
            Number of entries purged
        """
        threshold = current_generation - self.PURGE_AFTER_GENERATIONS
        if threshold < 0:
            return 0

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    DELETE FROM stream_match_cache
                    WHERE last_seen_generation < ?
                    """,
                    (threshold,),
                )
                purged = cursor.rowcount
                conn.commit()

                if purged > 0:
                    self._stats["purged"] += purged
                    logger.info(
                        f"[CACHE PURGE] Removed {purged} stale entries (generation < {threshold})"
                    )
                return purged
        except sqlite3.Error as e:
            logger.warning(f"[CACHE] purge_stale failed: {e}")
            return 0

    def clear_group(self, group_id: int) -> int:
        """Clear all cache entries for a specific group.

        Useful when group settings change significantly.

        Args:
            group_id: Event group ID to clear

        Returns:
            Number of entries cleared
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM stream_match_cache WHERE group_id = ?",
                    (group_id,),
                )
                cleared = cursor.rowcount
                conn.commit()
                logger.info(f"[CACHE CLEAR] Cleared {cleared} entries for group {group_id}")
                return cleared
        except sqlite3.Error as e:
            logger.warning(f"[CACHE] clear_group failed: {e}")
            return 0

    def clear_all(self) -> int:
        """Clear entire cache.

        Returns:
            Number of entries cleared
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM stream_match_cache")
                cleared = cursor.rowcount
                conn.commit()
                logger.info(f"[CACHE CLEAR] Cleared entire cache ({cleared} entries)")
                return cleared
        except sqlite3.Error as e:
            logger.warning(f"[CACHE] clear_all failed: {e}")
            return 0

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics for this session."""
        return self._stats.copy()

    def get_size(self) -> int:
        """Get total number of cached entries."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM stream_match_cache")
            return cursor.fetchone()[0]


def get_generation_counter(get_connection: Callable) -> int:
    """Get current EPG generation counter from settings."""
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT epg_generation_counter FROM settings WHERE id = 1")
            row = cursor.fetchone()
            return row["epg_generation_counter"] if row else 0
    except sqlite3.Error:
        return 0


def increment_generation_counter(get_connection: Callable) -> int:
    """Increment and return the new EPG generation counter."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE settings
            SET epg_generation_counter = COALESCE(epg_generation_counter, 0) + 1
            WHERE id = 1
            """
        )
        conn.commit()

        cursor = conn.execute("SELECT epg_generation_counter FROM settings WHERE id = 1")
        row = cursor.fetchone()
        new_value = row["epg_generation_counter"] if row else 1
        logger.debug(f"EPG generation counter: {new_value}")
        return new_value


def event_to_cache_data(event: Event) -> dict[str, Any]:
    """Convert Event to cacheable dict with static fields.

    Dynamic fields (scores, status) should be refreshed on each run
    via the single event endpoint.

    Args:
        event: Event to convert

    Returns:
        Dict suitable for JSON serialization
    """
    return asdict(event)


def _json_serializer(obj: Any) -> Any:
    """JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
