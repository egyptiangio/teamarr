"""Cross-group consolidation enforcement.

When multiple groups have channels for the same event, consolidates
streams into one channel based on group priority.

Use case:
- Multi-league group (ESPN+) matches an NHL game
- Single-league group (NHL) also has that game
- Move ESPN+ streams to NHL channel, delete ESPN+ channel
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CrossGroupResult:
    """Result of cross-group consolidation."""

    streams_moved: list[dict] = field(default_factory=list)
    channels_deleted: list[dict] = field(default_factory=list)
    channels_skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def moved_count(self) -> int:
        return len(self.streams_moved)

    @property
    def deleted_count(self) -> int:
        return len(self.channels_deleted)

    def to_dict(self) -> dict:
        return {
            "streams_moved": self.streams_moved,
            "channels_deleted": self.channels_deleted,
            "channels_skipped": self.channels_skipped,
            "errors": self.errors,
            "summary": {
                "moved": self.moved_count,
                "deleted": self.deleted_count,
                "skipped": len(self.channels_skipped),
                "errors": len(self.errors),
            },
        }


class CrossGroupEnforcer:
    """Enforces cross-group stream consolidation.

    Identifies when multiple groups have channels for the same event
    and consolidates them based on priority:
    - Single-league groups take priority over multi-league
    - Parent groups take priority over child groups
    - Earlier-created channels take priority

    The lower-priority channel's streams are moved to the higher-priority
    channel, and the lower-priority channel is deleted.
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

    def enforce(self, multi_league_group_ids: list[int] | None = None) -> CrossGroupResult:
        """Run cross-group consolidation.

        Finds duplicate channels across groups and consolidates them.

        Args:
            multi_league_group_ids: Optional list of multi-league group IDs
                to check. If None, checks all groups with multiple leagues.

        Returns:
            CrossGroupResult with consolidation details
        """
        from teamarr.database.channels import (
            add_stream_to_channel,
            find_any_channel_for_event,
            get_channel_streams,
            get_managed_channels_for_group,
            get_next_stream_priority,
            log_channel_history,
            mark_channel_deleted,
            stream_exists_on_channel,
        )
        from teamarr.database.groups import get_all_groups

        result = CrossGroupResult()

        try:
            with self._db_factory() as conn:
                # Get all groups
                all_groups = get_all_groups(conn, include_disabled=False)

                # Identify single-league vs multi-league groups
                # Multi-league = has multiple leagues in leagues array
                single_league_ids = set()
                multi_league_ids = set()

                for group in all_groups:
                    if len(group.leagues) > 1:
                        multi_league_ids.add(group.id)
                    else:
                        single_league_ids.add(group.id)

                # Filter to specified groups if provided
                if multi_league_group_ids is not None:
                    multi_league_ids = multi_league_ids.intersection(set(multi_league_group_ids))

                if not multi_league_ids:
                    logger.debug("No multi-league groups to check for consolidation")
                    return result

                # For each multi-league group's channels
                for group_id in multi_league_ids:
                    channels = get_managed_channels_for_group(conn, group_id, include_deleted=False)

                    for channel in channels:
                        event_id = channel.event_id
                        event_provider = channel.event_provider

                        if not event_id:
                            continue

                        # Check if a single-league group has this event
                        target_channel = find_any_channel_for_event(
                            conn=conn,
                            event_id=event_id,
                            event_provider=event_provider,
                            exclude_group_id=group_id,
                        )

                        if not target_channel:
                            continue

                        # Only consolidate if target is from single-league group
                        if target_channel.event_epg_group_id not in single_league_ids:
                            result.channels_skipped.append({
                                "channel": channel.channel_name,
                                "reason": "Target is not single-league",
                            })
                            continue

                        # Move streams from multi-league to single-league
                        streams = get_channel_streams(conn, channel.id, include_removed=False)
                        moved_count = 0

                        for stream in streams:
                            if stream_exists_on_channel(
                                conn, target_channel.id, stream.dispatcharr_stream_id
                            ):
                                continue  # Already on target

                            priority = get_next_stream_priority(conn, target_channel.id)
                            add_stream_to_channel(
                                conn=conn,
                                managed_channel_id=target_channel.id,
                                dispatcharr_stream_id=stream.dispatcharr_stream_id,
                                stream_name=stream.stream_name,
                                priority=priority,
                                source_group_id=group_id,
                                source_group_type="cross_group",
                                exception_keyword=stream.exception_keyword,
                            )
                            moved_count += 1

                            result.streams_moved.append({
                                "stream": stream.stream_name,
                                "from_channel": channel.channel_name,
                                "to_channel": target_channel.channel_name,
                            })

                        # Sync to Dispatcharr
                        if self._channel_manager and moved_count > 0:
                            self._sync_streams_to_dispatcharr(
                                from_channel_id=channel.dispatcharr_channel_id,
                                to_channel_id=target_channel.dispatcharr_channel_id,
                                streams=streams,
                            )

                        # Delete the multi-league channel
                        if self._channel_manager and channel.dispatcharr_channel_id:
                            self._delete_channel_in_dispatcharr(channel.dispatcharr_channel_id)

                        mark_channel_deleted(conn, channel.id, reason="Cross-group consolidation")

                        log_channel_history(
                            conn=conn,
                            managed_channel_id=channel.id,
                            change_type="deleted",
                            change_source="cross_group_enforcement",
                            notes=f"Consolidated into '{target_channel.channel_name}'",
                        )

                        if moved_count > 0:
                            log_channel_history(
                                conn=conn,
                                managed_channel_id=target_channel.id,
                                change_type="stream_added",
                                change_source="cross_group_enforcement",
                                notes=f"Received {moved_count} streams from cross-group",
                            )

                        result.channels_deleted.append({
                            "channel": channel.channel_name,
                            "event_id": event_id,
                            "streams_moved": moved_count,
                            "consolidated_into": target_channel.channel_name,
                        })

                conn.commit()

        except Exception as e:
            logger.exception("Error during cross-group consolidation")
            result.errors.append({"error": str(e)})

        if result.deleted_count > 0:
            logger.info(
                f"Cross-group consolidation: deleted {result.deleted_count} channels, "
                f"moved {result.moved_count} streams"
            )

        return result

    def _sync_streams_to_dispatcharr(
        self,
        from_channel_id: int | None,
        to_channel_id: int | None,
        streams: list,
    ) -> None:
        """Move streams between channels in Dispatcharr.

        Args:
            from_channel_id: Source channel
            to_channel_id: Target channel
            streams: List of stream records to move
        """
        if not self._channel_manager or not to_channel_id:
            return

        try:
            with self._dispatcharr_lock:
                channel = self._channel_manager.get_channel(to_channel_id)
                if not channel:
                    return

                current_streams = [s.id for s in channel.streams] if channel.streams else []
                stream_ids = [s.dispatcharr_stream_id for s in streams]

                new_streams = current_streams + [
                    sid for sid in stream_ids if sid not in current_streams
                ]

                if new_streams != current_streams:
                    self._channel_manager.update_channel(to_channel_id, {"streams": new_streams})

        except Exception as e:
            logger.warning(f"Failed to sync streams to Dispatcharr: {e}")

    def _delete_channel_in_dispatcharr(self, channel_id: int) -> None:
        """Delete channel in Dispatcharr.

        Args:
            channel_id: Dispatcharr channel ID
        """
        if not self._channel_manager:
            return

        try:
            with self._dispatcharr_lock:
                self._channel_manager.delete_channel(channel_id)
        except Exception as e:
            logger.warning(f"Failed to delete channel in Dispatcharr: {e}")
