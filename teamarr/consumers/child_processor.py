"""Child group stream processor.

Handles adding streams from child groups to parent group's channels.
Child groups don't create their own channels - they add streams to
existing channels owned by their parent group.

Usage:
    processor = ChildStreamProcessor(db_factory, channel_manager)
    result = processor.process_child_streams(
        child_group=child_group,
        parent_group_id=parent_group.id,
        matched_streams=matched_streams,
    )
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChildProcessResult:
    """Result of processing child group streams."""

    streams_added: list[dict] = field(default_factory=list)
    streams_skipped: list[dict] = field(default_factory=list)
    streams_existing: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def added_count(self) -> int:
        return len(self.streams_added)

    @property
    def skipped_count(self) -> int:
        return len(self.streams_skipped)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict:
        return {
            "streams_added": self.streams_added,
            "streams_skipped": self.streams_skipped,
            "streams_existing": self.streams_existing,
            "errors": self.errors,
            "summary": {
                "added": self.added_count,
                "skipped": self.skipped_count,
                "existing": len(self.streams_existing),
                "errors": self.error_count,
            },
        }


class ChildStreamProcessor:
    """Processes child group streams by adding them to parent channels.

    Child groups are M3U sources that add backup/failover streams to
    channels owned by their parent group.

    Flow:
    1. For each matched stream in child group
    2. Check exception keyword → route to keyword channel or main
    3. Find parent's channel for that event
    4. If found, add stream (with source_group_type='child')
    5. If not found, skip (parent channel doesn't exist yet)
    """

    def __init__(
        self,
        db_factory: Any,
        channel_manager: Any = None,
    ):
        """Initialize the processor.

        Args:
            db_factory: Factory function returning database connection
            channel_manager: Optional ChannelManager for Dispatcharr sync
        """
        self._db_factory = db_factory
        self._channel_manager = channel_manager
        self._dispatcharr_lock = threading.Lock()

        # Cache for exception keywords
        self._exception_keywords: list | None = None

    def clear_caches(self) -> None:
        """Clear cached data (call at start of processing run)."""
        self._exception_keywords = None

    def process_child_streams(
        self,
        child_group: dict,
        parent_group_id: int,
        matched_streams: list[dict],
    ) -> ChildProcessResult:
        """Process matched streams from a child group.

        Adds each stream to the parent's existing channel for that event.
        Does NOT create new channels - child groups only add to existing.

        Args:
            child_group: Child group configuration dict
            parent_group_id: Parent group ID
            matched_streams: List of dicts with 'stream' and 'event' keys

        Returns:
            ChildProcessResult with streams added, skipped, errors
        """
        from teamarr.database.channels import (
            add_stream_to_channel,
            check_exception_keyword,
            find_parent_channel_for_event,
            get_exception_keywords,
            get_next_stream_priority,
            log_channel_history,
            stream_exists_on_channel,
        )

        result = ChildProcessResult()
        child_group_id = child_group.get("id")
        child_group_name = child_group.get("name", "Unknown")

        try:
            with self._db_factory() as conn:
                # Load exception keywords (cached per run)
                if self._exception_keywords is None:
                    self._exception_keywords = get_exception_keywords(conn)

                for matched in matched_streams:
                    stream = matched.get("stream", {})
                    event = matched.get("event")

                    if not event:
                        result.errors.append(
                            {
                                "stream": stream.get("name", "Unknown"),
                                "error": "No event data",
                            }
                        )
                        continue

                    stream_name = stream.get("name", "")
                    stream_id = stream.get("id")
                    event_id = event.id
                    event_provider = getattr(event, "provider", "espn")

                    # Check exception keyword for routing
                    matched_keyword, keyword_behavior = check_exception_keyword(
                        stream_name, self._exception_keywords
                    )

                    # Skip if keyword behavior is 'ignore'
                    if keyword_behavior == "ignore":
                        result.streams_skipped.append(
                            {
                                "stream": stream_name,
                                "reason": f"Exception keyword '{matched_keyword}' set to ignore",
                            }
                        )
                        continue

                    # Find parent's channel for this event
                    parent_channel = find_parent_channel_for_event(
                        conn=conn,
                        parent_group_id=parent_group_id,
                        event_id=event_id,
                        event_provider=event_provider,
                        exception_keyword=matched_keyword,
                    )

                    # Fallback: if keyword matched but no keyword channel, try main
                    if not parent_channel and matched_keyword:
                        parent_channel = find_parent_channel_for_event(
                            conn=conn,
                            parent_group_id=parent_group_id,
                            event_id=event_id,
                            event_provider=event_provider,
                            exception_keyword=None,
                        )
                        if parent_channel:
                            logger.debug(
                                "[CHILD] Keyword channel not found for '%s', using main for %s",
                                matched_keyword,
                                event_id,
                            )

                    if not parent_channel:
                        result.streams_skipped.append(
                            {
                                "stream": stream_name,
                                "event_id": event_id,
                                "reason": "No parent channel for event",
                            }
                        )
                        continue

                    # Check if stream already exists on channel
                    if stream_exists_on_channel(conn, parent_channel.id, stream_id):
                        result.streams_existing.append(
                            {
                                "stream": stream_name,
                                "channel_id": parent_channel.dispatcharr_channel_id,
                                "channel_name": parent_channel.channel_name,
                            }
                        )
                        continue

                    # Add stream to database
                    # Use sequential priority - final ordering happens after all matching
                    priority = get_next_stream_priority(conn, parent_channel.id)
                    add_stream_to_channel(
                        conn=conn,
                        managed_channel_id=parent_channel.id,
                        dispatcharr_stream_id=stream_id,
                        stream_name=stream_name,
                        priority=priority,
                        source_group_id=child_group_id,
                        source_group_type="child",
                        exception_keyword=matched_keyword,
                        m3u_account_id=stream.get("m3u_account_id"),
                        m3u_account_name=stream.get("m3u_account_name"),
                    )

                    # Sync to Dispatcharr if configured
                    if self._channel_manager and parent_channel.dispatcharr_channel_id:
                        self._sync_stream_to_dispatcharr(
                            parent_channel.dispatcharr_channel_id,
                            stream_id,
                        )

                    # Log history
                    log_channel_history(
                        conn=conn,
                        managed_channel_id=parent_channel.id,
                        change_type="stream_added",
                        change_source="epg_generation",
                        notes=f"Added stream '{stream_name}' from child group '{child_group_name}'",
                    )

                    result.streams_added.append(
                        {
                            "stream": stream_name,
                            "event_id": event_id,
                            "channel_id": parent_channel.dispatcharr_channel_id,
                            "channel_name": parent_channel.channel_name,
                            "keyword": matched_keyword,
                        }
                    )

                conn.commit()

        except Exception as e:
            logger.exception("[CHILD_ERROR] %s: %s", child_group_name, e)
            result.errors.append({"error": str(e)})

        logger.info(
            "[CHILD] %s: added=%d skipped=%d existing=%d errors=%d",
            child_group_name,
            result.added_count,
            result.skipped_count,
            len(result.streams_existing),
            result.error_count,
        )

        return result

    def _sync_stream_to_dispatcharr(
        self,
        dispatcharr_channel_id: int,
        stream_id: int,
    ) -> bool:
        """Add stream to channel in Dispatcharr.

        Args:
            dispatcharr_channel_id: Dispatcharr channel ID
            stream_id: Stream ID to add

        Returns:
            True if synced successfully
        """
        if not self._channel_manager:
            return False

        try:
            with self._dispatcharr_lock:
                channel = self._channel_manager.get_channel(dispatcharr_channel_id)
                if not channel:
                    logger.warning(
                        "[CHILD] Channel %d not found in Dispatcharr", dispatcharr_channel_id
                    )
                    return False

                current_streams = list(channel.streams) if channel.streams else []

                if stream_id in current_streams:
                    return True  # Already exists

                current_streams.append(stream_id)
                result = self._channel_manager.update_channel(
                    dispatcharr_channel_id,
                    {"streams": current_streams},
                )

                return result.success if hasattr(result, "success") else bool(result)

        except Exception as e:
            logger.warning("[CHILD] Failed to sync stream to Dispatcharr: %s", e)
            return False
