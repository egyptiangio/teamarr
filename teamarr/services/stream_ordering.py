"""Stream ordering service.

Computes stream priorities based on user-defined rules.
Rules are evaluated in priority order (lowest first); first match wins.
"""

import logging
import re
from dataclasses import dataclass
from sqlite3 import Connection

from teamarr.database.channels.types import ManagedChannelStream
from teamarr.database.settings.types import StreamOrderingRule

logger = logging.getLogger(__name__)

# Default priority for streams that don't match any rule
NO_MATCH_PRIORITY = 999


@dataclass
class StreamWithPriority:
    """A stream with its computed priority."""

    stream: ManagedChannelStream
    computed_priority: int
    matched_rule_type: str | None = None  # Which rule type matched


class StreamOrderingService:
    """Service for computing stream ordering based on rules.

    Rules are evaluated in priority order (lowest number first).
    First matching rule determines the stream's position.
    Non-matching streams get priority 999 (sorted to end).
    """

    def __init__(
        self,
        rules: list[StreamOrderingRule],
        conn: Connection | None = None,
    ):
        """Initialize the service.

        Args:
            rules: List of ordering rules
            conn: Database connection (optional, needed for group name lookups)
        """
        self.rules = sorted(rules, key=lambda r: r.priority)
        self.conn = conn
        self._compiled_regex: dict[str, re.Pattern] = {}
        self._group_name_cache: dict[int, str] = {}

    def compute_priority(
        self,
        stream: ManagedChannelStream,
        source_group_name: str | None = None,
    ) -> int:
        """Compute the priority for a single stream.

        Args:
            stream: The stream to compute priority for
            source_group_name: Optional pre-fetched group name (optimization)

        Returns:
            Priority number (lower = higher priority)
        """
        for rule in self.rules:
            if self._matches(stream, rule, source_group_name):
                return rule.priority
        return NO_MATCH_PRIORITY

    def compute_priority_with_details(
        self,
        stream: ManagedChannelStream,
        source_group_name: str | None = None,
    ) -> StreamWithPriority:
        """Compute priority with details about which rule matched.

        Args:
            stream: The stream to compute priority for
            source_group_name: Optional pre-fetched group name

        Returns:
            StreamWithPriority with computed priority and match info
        """
        for rule in self.rules:
            if self._matches(stream, rule, source_group_name):
                return StreamWithPriority(
                    stream=stream,
                    computed_priority=rule.priority,
                    matched_rule_type=rule.type,
                )
        return StreamWithPriority(
            stream=stream,
            computed_priority=NO_MATCH_PRIORITY,
            matched_rule_type=None,
        )

    def sort_streams(
        self,
        streams: list[ManagedChannelStream],
        source_group_names: dict[int, str] | None = None,
    ) -> list[ManagedChannelStream]:
        """Sort streams by computed priority.

        Args:
            streams: List of streams to sort
            source_group_names: Optional mapping of source_group_id -> group name

        Returns:
            Sorted list of streams (lowest priority first)
        """
        if not self.rules:
            # No rules - preserve existing order by added_at
            return sorted(streams, key=lambda s: (s.priority, s.added_at or 0))

        def sort_key(stream: ManagedChannelStream):
            group_name = None
            if source_group_names and stream.source_group_id:
                group_name = source_group_names.get(stream.source_group_id)
            priority = self.compute_priority(stream, group_name)
            # Secondary sort by added_at for stable ordering within same priority
            return (priority, stream.added_at or 0)

        return sorted(streams, key=sort_key)

    def _matches(
        self,
        stream: ManagedChannelStream,
        rule: StreamOrderingRule,
        source_group_name: str | None = None,
    ) -> bool:
        """Check if a stream matches a rule.

        Args:
            stream: The stream to check
            rule: The rule to match against
            source_group_name: Optional pre-fetched group name

        Returns:
            True if the stream matches the rule
        """
        if rule.type == "m3u":
            return self._match_m3u(stream, rule.value)
        elif rule.type == "group":
            return self._match_group(stream, rule.value, source_group_name)
        elif rule.type == "regex":
            return self._match_regex(stream, rule.value)
        return False

    def _match_m3u(self, stream: ManagedChannelStream, account_name: str) -> bool:
        """Match stream by M3U account name (case-insensitive)."""
        if not stream.m3u_account_name:
            return False
        return stream.m3u_account_name.lower() == account_name.lower()

    def _match_group(
        self,
        stream: ManagedChannelStream,
        group_name: str,
        source_group_name: str | None = None,
    ) -> bool:
        """Match stream by source group name (case-insensitive).

        Args:
            stream: The stream to check
            group_name: The group name to match
            source_group_name: Pre-fetched group name (if available)
        """
        actual_name = source_group_name
        if actual_name is None and stream.source_group_id:
            actual_name = self._get_group_name(stream.source_group_id)
        if not actual_name:
            return False
        return actual_name.lower() == group_name.lower()

    def _match_regex(self, stream: ManagedChannelStream, pattern: str) -> bool:
        """Match stream name by regex pattern (case-insensitive)."""
        if not stream.stream_name:
            return False

        compiled = self._get_compiled_regex(pattern)
        if compiled is None:
            return False

        return bool(compiled.search(stream.stream_name))

    def _get_compiled_regex(self, pattern: str) -> re.Pattern | None:
        """Get or compile a regex pattern (with caching)."""
        if pattern not in self._compiled_regex:
            try:
                self._compiled_regex[pattern] = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[STREAM_ORDER] Invalid regex pattern '%s': %s", pattern, e)
                self._compiled_regex[pattern] = None  # type: ignore
        return self._compiled_regex.get(pattern)

    def _get_group_name(self, group_id: int) -> str | None:
        """Look up group name from database (with caching)."""
        if group_id in self._group_name_cache:
            return self._group_name_cache[group_id]

        if not self.conn:
            return None

        try:
            cursor = self.conn.execute(
                "SELECT name FROM event_epg_groups WHERE id = ?",
                (group_id,),
            )
            row = cursor.fetchone()
            if row:
                self._group_name_cache[group_id] = row["name"]
                return row["name"]
        except Exception as e:
            logger.warning("[STREAM_ORDER] Failed to look up group %d: %s", group_id, e)

        self._group_name_cache[group_id] = None  # type: ignore
        return None


def get_stream_ordering_service(conn: Connection) -> StreamOrderingService:
    """Factory function to create a StreamOrderingService with rules from database.

    Args:
        conn: Database connection

    Returns:
        Configured StreamOrderingService
    """
    from teamarr.database.settings import get_stream_ordering_settings

    settings = get_stream_ordering_settings(conn)
    return StreamOrderingService(rules=settings.rules, conn=conn)
