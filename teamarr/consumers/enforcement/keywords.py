"""Keyword enforcement for stream placement.

Ensures streams are on the correct channel based on exception keywords.
Runs once per EPG generation to fix any misplaced streams.

When to use:
- A stream with keyword "Spanish" should be on the Spanish channel, not main
- A stream without keywords should be on main channel, not a keyword channel
- Keywords may be added/removed after streams were already placed
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class KeywordEnforcementResult:
    """Result of keyword enforcement run."""

    streams_moved: list[dict] = field(default_factory=list)
    streams_correct: int = 0
    channels_emptied: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def moved_count(self) -> int:
        return len(self.streams_moved)

    def to_dict(self) -> dict:
        return {
            "streams_moved": self.streams_moved,
            "streams_correct": self.streams_correct,
            "channels_emptied": self.channels_emptied,
            "errors": self.errors,
            "summary": {
                "moved": self.moved_count,
                "correct": self.streams_correct,
                "emptied": len(self.channels_emptied),
                "errors": len(self.errors),
            },
        }


class KeywordEnforcer:
    """Enforces correct stream placement based on exception keywords.

    Scans all active streams across all channels and ensures each stream
    is on the correct channel:
    - Streams matching a keyword → keyword channel (or main if not exists)
    - Streams not matching any keyword → main channel

    This corrects misplacements that occur when:
    - Keywords are added/removed after streams were placed
    - Stream names change
    - Manual stream additions
    """

    def __init__(
        self,
        db_factory: Any,
        channel_manager: Any = None,
    ):
        """Initialize the enforcer.

        Args:
            db_factory: Factory returning database connection
            channel_manager: Optional ChannelManager for Dispatcharr sync
        """
        self._db_factory = db_factory
        self._channel_manager = channel_manager
        self._dispatcharr_lock = threading.Lock()

    def enforce(self) -> KeywordEnforcementResult:
        """Run keyword enforcement across all channels.

        Scans all streams and moves any that are on the wrong channel.

        Returns:
            KeywordEnforcementResult with move details
        """
        from teamarr.database.channels import (
            add_stream_to_channel,
            check_exception_keyword,
            get_all_managed_channels,
            get_channel_streams,
            get_exception_keywords,
            get_next_stream_priority,
            log_channel_history,
            remove_stream_from_channel,
        )

        result = KeywordEnforcementResult()

        try:
            with self._db_factory() as conn:
                # Load exception keywords
                exception_keywords = get_exception_keywords(conn)

                if not exception_keywords:
                    logger.debug("[KEYWORD] No exception keywords configured, skipping")
                    return result

                # Get all active channels
                channels = get_all_managed_channels(conn, include_deleted=False)

                # Build lookup: (group_id, event_id, provider) → channels by keyword
                channel_lookup: dict[tuple, dict[str | None, Any]] = {}
                for ch in channels:
                    key = (ch.event_epg_group_id, ch.event_id, ch.event_provider)
                    if key not in channel_lookup:
                        channel_lookup[key] = {}
                    # Use None as key for main channel (no keyword)
                    kw = ch.exception_keyword if ch.exception_keyword else None
                    channel_lookup[key][kw] = ch

                # Check each channel's streams
                for channel in channels:
                    streams = get_channel_streams(conn, channel.id, include_removed=False)

                    for stream in streams:
                        stream_name = stream.stream_name or ""

                        # What keyword should this stream have?
                        expected_keyword, behavior = check_exception_keyword(
                            stream_name, exception_keywords
                        )

                        # Normalize: None for no keyword
                        current_keyword = channel.exception_keyword or None
                        expected_keyword = expected_keyword if expected_keyword else None

                        # If behavior is 'ignore', stream shouldn't be here at all
                        if behavior == "ignore":
                            # Remove stream entirely
                            remove_stream_from_channel(
                                conn,
                                channel.id,
                                stream.dispatcharr_stream_id,
                                reason=f"Keyword '{expected_keyword}' behavior is ignore",
                            )
                            result.streams_moved.append(
                                {
                                    "stream": stream_name,
                                    "action": "removed",
                                    "reason": f"Keyword '{expected_keyword}' set to ignore",
                                }
                            )
                            continue

                        # Is stream on correct channel?
                        if current_keyword == expected_keyword:
                            result.streams_correct += 1
                            continue

                        # Find target channel
                        key = (channel.event_epg_group_id, channel.event_id, channel.event_provider)
                        target_channel = None

                        if key in channel_lookup:
                            target_channel = channel_lookup[key].get(expected_keyword)

                            # Fallback to main if keyword channel doesn't exist
                            if not target_channel and expected_keyword:
                                target_channel = channel_lookup[key].get(None)

                        if not target_channel:
                            # Can't move - target doesn't exist
                            result.errors.append(
                                {
                                    "stream": stream_name,
                                    "error": f"No target channel for keyword '{expected_keyword}'",
                                }
                            )
                            continue

                        if target_channel.id == channel.id:
                            # Already on correct channel
                            result.streams_correct += 1
                            continue

                        # Move stream: remove from current, add to target
                        target_name = "main" if not expected_keyword else expected_keyword
                        remove_stream_from_channel(
                            conn,
                            channel.id,
                            stream.dispatcharr_stream_id,
                            reason=f"Moved to {target_name} channel",
                        )

                        # Use sequential priority - final ordering after all matching
                        priority = get_next_stream_priority(conn, target_channel.id)
                        add_stream_to_channel(
                            conn=conn,
                            managed_channel_id=target_channel.id,
                            dispatcharr_stream_id=stream.dispatcharr_stream_id,
                            stream_name=stream_name,
                            priority=priority,
                            source_group_id=stream.source_group_id,
                            source_group_type=stream.source_group_type,
                            exception_keyword=expected_keyword,
                            m3u_account_name=stream.m3u_account_name,
                        )

                        # Sync to Dispatcharr
                        if self._channel_manager:
                            self._move_stream_in_dispatcharr(
                                from_channel_id=channel.dispatcharr_channel_id,
                                to_channel_id=target_channel.dispatcharr_channel_id,
                                stream_id=stream.dispatcharr_stream_id,
                            )

                        # Log history on both channels
                        log_channel_history(
                            conn=conn,
                            managed_channel_id=channel.id,
                            change_type="stream_removed",
                            change_source="keyword_enforcement",
                            notes=f"Moved stream '{stream_name}' to {target_name} channel",
                        )
                        log_channel_history(
                            conn=conn,
                            managed_channel_id=target_channel.id,
                            change_type="stream_added",
                            change_source="keyword_enforcement",
                            notes=f"Received stream '{stream_name}' from keyword enforcement",
                        )

                        result.streams_moved.append(
                            {
                                "stream": stream_name,
                                "from_channel": channel.channel_name,
                                "to_channel": target_channel.channel_name,
                                "from_keyword": current_keyword,
                                "to_keyword": expected_keyword,
                            }
                        )

                conn.commit()

        except Exception as e:
            logger.exception("[KEYWORD_ERROR] %s", e)
            result.errors.append({"error": str(e)})

        if result.moved_count > 0:
            logger.info(
                "[KEYWORD] Moved %d streams, %d correct",
                result.moved_count,
                result.streams_correct,
            )

        return result

    def _move_stream_in_dispatcharr(
        self,
        from_channel_id: int | None,
        to_channel_id: int | None,
        stream_id: int,
    ) -> None:
        """Move stream between channels in Dispatcharr.

        Args:
            from_channel_id: Source channel (to remove from)
            to_channel_id: Target channel (to add to)
            stream_id: Stream ID to move
        """
        if not self._channel_manager:
            return

        try:
            with self._dispatcharr_lock:
                # Remove from source
                if from_channel_id:
                    channel = self._channel_manager.get_channel(from_channel_id)
                    if channel and channel.streams:
                        streams = [s.id for s in channel.streams if s.id != stream_id]
                        self._channel_manager.update_channel(from_channel_id, {"streams": streams})

                # Add to target
                if to_channel_id:
                    channel = self._channel_manager.get_channel(to_channel_id)
                    if channel:
                        streams = [s.id for s in channel.streams] if channel.streams else []
                        if stream_id not in streams:
                            streams.append(stream_id)
                            self._channel_manager.update_channel(
                                to_channel_id, {"streams": streams}
                            )

        except Exception as e:
            logger.warning("[KEYWORD] Failed to move stream in Dispatcharr: %s", e)
