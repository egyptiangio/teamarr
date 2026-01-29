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
    match_method: str | None = None
    user_corrected: bool = False


# Sentinel value for failed match cache entries
FAILED_MATCH_EVENT_ID = "__FAILED__"


class StreamMatchCache:
    """Manages stream fingerprint cache for EPG optimization.

    Stores successful stream-to-event matches with static event data.
    Dynamic fields (scores, status) are refreshed from API on each run.

    Features:
    - Match method tracking (alias, pattern, fuzzy, keyword)
    - User-corrected matches (pinned, never auto-purged)
    - Failed match caching (short TTL, user can override)
    """

    # Purge algorithmic entries not seen in this many generations
    PURGE_AFTER_GENERATIONS = 5

    # Purge failed match entries more aggressively
    PURGE_FAILED_AFTER_GENERATIONS = 2

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
            "failed_cached": 0,
            "user_corrections": 0,
        }

    def get(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
        include_failed: bool = False,
    ) -> StreamCacheEntry | None:
        """Look up cached match for a stream.

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name
            include_failed: If True, return failed match cache entries too

        Returns:
            StreamCacheEntry if found, None otherwise
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT event_id, league, cached_event_data, match_method, user_corrected
                FROM stream_match_cache
                WHERE fingerprint = ?
                """,
                (fingerprint,),
            )
            row = cursor.fetchone()

            if row:
                # Skip failed matches unless explicitly requested
                if row["event_id"] == FAILED_MATCH_EVENT_ID and not include_failed:
                    self._stats["misses"] += 1
                    return None

                self._stats["hits"] += 1
                logger.debug(
                    "[STREAM_CACHE_HIT] stream_id=%d event_id=%s", stream_id, row["event_id"]
                )

                # Parse cached_event_data if present
                cached_data = {}
                if row["cached_event_data"]:
                    try:
                        cached_data = json.loads(row["cached_event_data"])
                    except json.JSONDecodeError:
                        cached_data = {}

                return StreamCacheEntry(
                    event_id=row["event_id"],
                    league=row["league"],
                    cached_data=cached_data,
                    match_method=row["match_method"],
                    user_corrected=bool(row["user_corrected"]),
                )

            self._stats["misses"] += 1
            return None

    def is_user_corrected(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
    ) -> bool:
        """Check if stream has a user-corrected match.

        User corrections are "pinned" and should take precedence.
        """
        entry = self.get(group_id, stream_id, stream_name, include_failed=True)
        return entry is not None and entry.user_corrected

    def is_failed_cached(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
    ) -> bool:
        """Check if stream has a cached failed match."""
        entry = self.get(group_id, stream_id, stream_name, include_failed=True)
        return entry is not None and entry.event_id == FAILED_MATCH_EVENT_ID

    def set(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
        event_id: str,
        league: str,
        cached_data: dict[str, Any],
        generation: int,
        match_method: str | None = None,
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
            match_method: How the match was made (alias, pattern, fuzzy, etc.)

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
                         match_method, user_corrected,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (fingerprint)
                    DO UPDATE SET
                        event_id = excluded.event_id,
                        league = excluded.league,
                        cached_event_data = excluded.cached_event_data,
                        last_seen_generation = excluded.last_seen_generation,
                        match_method = excluded.match_method,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_corrected = 0  -- Don't overwrite user corrections
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
                        match_method,
                    ),
                )
                conn.commit()
                self._stats["sets"] += 1
                logger.debug(
                    "[STREAM_CACHE_SET] stream_id=%d event_id=%s method=%s",
                    stream_id,
                    event_id,
                    match_method,
                )
                return True
        except sqlite3.Error as e:
            logger.error("[STREAM_CACHE_ERROR] Set failed: %s", e)
            return False

    def set_failed(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
        generation: int,
    ) -> bool:
        """Cache a failed match attempt.

        Failed matches are cached with a shorter TTL to avoid re-attempting
        expensive matching on every run for streams that never match.

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name
            generation: Current EPG generation counter

        Returns:
            True if cached successfully
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO stream_match_cache
                        (fingerprint, group_id, stream_id, stream_name,
                         event_id, league, cached_event_data, last_seen_generation,
                         match_method, user_corrected,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, '', NULL, ?, 'no_match', 0,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (fingerprint)
                    DO UPDATE SET
                        last_seen_generation = excluded.last_seen_generation,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_corrected = 0  -- Don't overwrite user corrections
                    """,
                    (
                        fingerprint,
                        group_id,
                        stream_id,
                        stream_name,
                        FAILED_MATCH_EVENT_ID,
                        generation,
                    ),
                )
                conn.commit()
                self._stats["failed_cached"] += 1
                logger.debug("[STREAM_CACHE_FAILED] stream_id=%d (no match)", stream_id)
                return True
        except sqlite3.Error as e:
            logger.error("[STREAM_CACHE_ERROR] Set failed match: %s", e)
            return False

    def set_user_correction(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
        event_id: str,
        league: str,
        cached_data: dict[str, Any],
    ) -> bool:
        """Set a user-corrected match (pinned, never auto-purged).

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name
            event_id: Correct event ID
            league: Correct league code
            cached_data: Event data for template vars

        Returns:
            True if saved successfully
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
                         match_method, user_corrected, corrected_at,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'user_corrected', 1, CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (fingerprint)
                    DO UPDATE SET
                        event_id = excluded.event_id,
                        league = excluded.league,
                        cached_event_data = excluded.cached_event_data,
                        match_method = 'user_corrected',
                        user_corrected = 1,
                        corrected_at = CURRENT_TIMESTAMP,
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
                    ),
                )
                conn.commit()
                self._stats["user_corrections"] += 1
                logger.info(
                    "[STREAM_CACHE_CORRECTED] stream_id=%d event_id=%s", stream_id, event_id
                )
                return True
        except sqlite3.Error as e:
            logger.error("[STREAM_CACHE_ERROR] Set user correction: %s", e)
            return False

    def remove_user_correction(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
    ) -> bool:
        """Remove a user correction, allowing re-matching.

        Returns:
            True if removed successfully
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    DELETE FROM stream_match_cache
                    WHERE fingerprint = ? AND user_corrected = 1
                    """,
                    (fingerprint,),
                )
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.warning("[STREAM_CACHE_ERROR] Remove user correction: %s", e)
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
            logger.warning("[STREAM_CACHE_ERROR] Touch failed: %s", e)
            return False

    def purge_stale(self, current_generation: int) -> int:
        """Remove stale entries not seen recently.

        User-corrected entries are never purged (they are pinned).
        Failed matches have a shorter TTL than successful matches.

        Args:
            current_generation: Current EPG generation counter

        Returns:
            Number of entries purged
        """
        purged_total = 0

        try:
            with self._get_connection() as conn:
                # Purge stale failed matches (shorter TTL)
                # Never purge user corrections
                failed_threshold = current_generation - self.PURGE_FAILED_AFTER_GENERATIONS
                if failed_threshold >= 0:
                    cursor = conn.execute(
                        """
                        DELETE FROM stream_match_cache
                        WHERE last_seen_generation < ?
                          AND event_id = ?
                          AND user_corrected = 0
                        """,
                        (failed_threshold, FAILED_MATCH_EVENT_ID),
                    )
                    failed_purged = cursor.rowcount
                    if failed_purged > 0:
                        logger.debug("[STREAM_CACHE_PURGE] Removed %d stale failed", failed_purged)
                    purged_total += failed_purged

                # Purge stale successful matches (normal TTL)
                # Never purge user corrections
                success_threshold = current_generation - self.PURGE_AFTER_GENERATIONS
                if success_threshold >= 0:
                    cursor = conn.execute(
                        """
                        DELETE FROM stream_match_cache
                        WHERE last_seen_generation < ?
                          AND event_id != ?
                          AND user_corrected = 0
                        """,
                        (success_threshold, FAILED_MATCH_EVENT_ID),
                    )
                    success_purged = cursor.rowcount
                    if success_purged > 0:
                        logger.debug(
                            "[STREAM_CACHE_PURGE] Removed %d stale successful", success_purged
                        )
                    purged_total += success_purged

                conn.commit()

                if purged_total > 0:
                    self._stats["purged"] += purged_total
                    logger.info("[STREAM_CACHE_PURGE] Removed %d total stale entries", purged_total)

                return purged_total
        except sqlite3.Error as e:
            logger.warning("[STREAM_CACHE_ERROR] Purge failed: %s", e)
            return 0

    def delete(
        self,
        group_id: int,
        stream_id: int,
        stream_name: str,
    ) -> bool:
        """Delete a single cache entry.

        Use when a cached match is no longer valid (e.g., event became final).

        Args:
            group_id: Event group ID
            stream_id: Stream ID
            stream_name: Exact stream name

        Returns:
            True if entry was deleted
        """
        fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM stream_match_cache WHERE fingerprint = ?",
                    (fingerprint,),
                )
                conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.debug("[STREAM_CACHE_DELETE] stream_id=%d", stream_id)
                return deleted
        except sqlite3.Error as e:
            logger.warning("[STREAM_CACHE_ERROR] Delete failed: %s", e)
            return False

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
                logger.info("[STREAM_CACHE_CLEAR] group=%d entries=%d", group_id, cleared)
                return cleared
        except sqlite3.Error as e:
            logger.warning("[STREAM_CACHE_ERROR] Clear group failed: %s", e)
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
                logger.info("[STREAM_CACHE_CLEAR] All entries cleared: %d", cleared)
                return cleared
        except sqlite3.Error as e:
            logger.warning("[STREAM_CACHE_ERROR] Clear all failed: %s", e)
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
    """Increment and return the new EPG generation counter.

    Uses BEGIN EXCLUSIVE to ensure atomic UPDATE + SELECT.
    This prevents race conditions when multiple processes run EPG generation.
    """
    with get_connection() as conn:
        # Use exclusive transaction to ensure atomicity
        conn.execute("BEGIN EXCLUSIVE")
        try:
            conn.execute(
                """
                UPDATE settings
                SET epg_generation_counter = COALESCE(epg_generation_counter, 0) + 1
                WHERE id = 1
                """
            )
            cursor = conn.execute("SELECT epg_generation_counter FROM settings WHERE id = 1")
            row = cursor.fetchone()
            new_value = row["epg_generation_counter"] if row else 1
            conn.commit()
            logger.debug("[GENERATION] Counter incremented to %d", new_value)
            return new_value
        except Exception:
            conn.rollback()
            raise


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
