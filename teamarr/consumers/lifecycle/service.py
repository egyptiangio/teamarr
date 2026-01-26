"""Channel lifecycle service.

Full channel lifecycle management with Dispatcharr integration.
Handles channel creation, deletion, settings sync, and EPG association.
"""

import json
import logging
import threading
from datetime import datetime
from sqlite3 import Connection
from typing import Any

from teamarr.core import Event
from teamarr.consumers.event_epg import is_event_postponed, POSTPONED_LABEL
from teamarr.templates import ContextBuilder, TemplateResolver

from .dynamic_resolver import DynamicResolver
from .timing import ChannelLifecycleManager
from .types import (
    ChannelCreationResult,
    CreateTiming,
    DeleteTiming,
    StreamProcessResult,
    generate_event_tvg_id,
)

logger = logging.getLogger(__name__)


class ChannelLifecycleService:
    """Full channel lifecycle management with Dispatcharr integration.

    Handles:
    - Channel creation from matched streams
    - Channel deletion based on timing
    - Settings sync (name, number, streams, logo, profiles)
    - EPG association after refresh
    - Duplicate handling (consolidate, separate, ignore)
    - Exception keyword handling

    Usage:
        from teamarr.dispatcharr import DispatcharrClient, ChannelManager, EPGManager, LogoManager
        from teamarr.database import get_db

        with DispatcharrClient(url, username, password) as client:
            service = ChannelLifecycleService(
                db_factory=get_db,
                channel_manager=ChannelManager(client),
                logo_manager=LogoManager(client),
                epg_manager=EPGManager(client),
                create_timing='same_day',
                delete_timing='day_after',
            )

            # Process matched streams
            result = service.process_matched_streams(matches, group_config)

            # Delete expired channels
            result = service.process_scheduled_deletions()
    """

    def __init__(
        self,
        db_factory: Any,
        sports_service: Any,
        channel_manager: Any = None,
        logo_manager: Any = None,
        epg_manager: Any = None,
        create_timing: CreateTiming = "same_day",
        delete_timing: DeleteTiming = "day_after",
        default_duration_hours: float = 3.0,
        sport_durations: dict[str, float] | None = None,
        timezone: str = "America/New_York",
        include_final_events: bool = False,
    ):
        """Initialize the lifecycle service.

        Args:
            db_factory: Factory function that returns a database connection
            sports_service: SportsDataService for template variable resolution (required)
            channel_manager: ChannelManager instance for Dispatcharr operations
            logo_manager: LogoManager instance for logo operations
            epg_manager: EPGManager instance for EPG operations
            create_timing: When to create channels
            delete_timing: When to delete channels
            default_duration_hours: Default event duration
            sport_durations: Per-sport duration mapping (basketball, football, etc.)
            timezone: User timezone for timing calculations
            include_final_events: Whether to include completed/final events in EPG

        Raises:
            ValueError: If sports_service is not provided
        """
        if sports_service is None:
            raise ValueError("sports_service is required for template variable resolution")

        self._db_factory = db_factory
        self._sports_service = sports_service
        self._channel_manager = channel_manager
        self._logo_manager = logo_manager
        self._epg_manager = epg_manager
        self._timezone = timezone

        # Timing manager for create/delete decisions
        self._timing_manager = ChannelLifecycleManager(
            create_timing=create_timing,
            delete_timing=delete_timing,
            default_duration_hours=default_duration_hours,
            sport_durations=sport_durations,
            include_final_events=include_final_events,
        )

        # Thread lock for Dispatcharr operations
        self._dispatcharr_lock = threading.Lock()

        # Cache exception keywords
        self._exception_keywords: list | None = None

        # Pending profile changes for bulk application
        # Structure: {profile_id: {"add": set(channel_ids), "remove": set(channel_ids)}}
        self._pending_profile_changes: dict[int, dict[str, set[int]]] = {}

        # Template engine
        self._context_builder = ContextBuilder(sports_service)
        self._resolver = TemplateResolver()

        # Dynamic group/profile resolver
        self._dynamic_resolver = DynamicResolver()

    @property
    def dispatcharr_enabled(self) -> bool:
        """Check if Dispatcharr integration is enabled."""
        return self._channel_manager is not None

    def clear_caches(self) -> None:
        """Clear all Dispatcharr caches.

        Should be called at the start of EPG generation to ensure fresh data.
        """
        if self._channel_manager:
            self._channel_manager.clear_cache()
        if self._logo_manager:
            self._logo_manager.clear_cache()
        self._exception_keywords = None
        self._pending_profile_changes = {}

    def _collect_profile_change(
        self,
        profile_id: int,
        channel_id: int,
        action: str,
    ) -> None:
        """Collect a profile change for bulk application later.

        Args:
            profile_id: Profile ID to modify
            channel_id: Channel ID to add/remove
            action: "add" or "remove"
        """
        if profile_id not in self._pending_profile_changes:
            self._pending_profile_changes[profile_id] = {"add": set(), "remove": set()}
        self._pending_profile_changes[profile_id][action].add(channel_id)

    def _apply_pending_profile_changes(self) -> dict:
        """Apply all pending profile changes using bulk API.

        Returns:
            Dict with stats: {profiles_updated, channels_added, channels_removed, errors}
        """
        if not self._pending_profile_changes or not self._channel_manager:
            return {"profiles_updated": 0, "channels_added": 0, "channels_removed": 0}

        stats = {"profiles_updated": 0, "channels_added": 0, "channels_removed": 0, "errors": []}

        with self._dispatcharr_lock:
            for profile_id, changes in self._pending_profile_changes.items():
                add_ids = list(changes["add"])
                remove_ids = list(changes["remove"])

                if not add_ids and not remove_ids:
                    continue

                try:
                    result = self._channel_manager.bulk_update_profile_channels(
                        profile_id=profile_id,
                        add_channel_ids=add_ids if add_ids else None,
                        remove_channel_ids=remove_ids if remove_ids else None,
                    )
                    if result.success:
                        stats["profiles_updated"] += 1
                        stats["channels_added"] += len(add_ids)
                        stats["channels_removed"] += len(remove_ids)
                        logger.debug(
                            f"Bulk profile update for profile {profile_id}: "
                            f"+{len(add_ids)} -{len(remove_ids)} channels"
                        )
                    else:
                        stats["errors"].append(f"Profile {profile_id}: {result.error}")
                        logger.warning("[LIFECYCLE] Bulk profile update failed for profile %d: %s", profile_id, result.error)
                except Exception as e:
                    stats["errors"].append(f"Profile {profile_id}: {e}")
                    logger.warning("[LIFECYCLE] Bulk profile update error for profile %d: %s", profile_id, e)

        # Clear pending changes after applying
        self._pending_profile_changes = {}

        if stats["profiles_updated"] > 0:
            logger.info(
                f"Bulk profile updates: {stats['profiles_updated']} profiles, "
                f"+{stats['channels_added']} -{stats['channels_removed']} channel assignments"
            )

        return stats

    def _get_exception_keywords(self, conn: Connection) -> list:
        """Get exception keywords with caching."""
        if self._exception_keywords is None:
            from teamarr.database.channels import get_exception_keywords

            self._exception_keywords = get_exception_keywords(conn)
        return self._exception_keywords

    def _check_exception_keyword(
        self,
        stream_name: str,
        conn: Connection,
    ) -> tuple[str | None, str | None]:
        """Check if stream name matches any exception keyword.

        Returns:
            Tuple of (matched_keyword, behavior) or (None, None)
        """
        from teamarr.database.channels import check_exception_keyword

        keywords = self._get_exception_keywords(conn)
        return check_exception_keyword(stream_name, keywords)

    def process_matched_streams(
        self,
        matched_streams: list[dict],
        group_config: dict,
        template: dict | None = None,
    ) -> StreamProcessResult:
        """Process matched streams and create/update channels as needed.

        Handles all three duplicate modes:
        - consolidate: All streams for same event → one channel
        - separate: Each stream → its own channel
        - ignore: First stream wins, skip duplicates

        Args:
            matched_streams: List of dicts with 'stream', 'event' keys
            group_config: Event EPG group configuration
            template: Optional template for channel naming

        Returns:
            StreamProcessResult with created, existing, skipped, errors
        """
        from teamarr.database.channels import (
            find_existing_channel,
            log_channel_history,
        )

        result = StreamProcessResult()

        # Clear logo cache at start of batch to avoid stale references
        # Logos may have been deleted/changed in Dispatcharr since last run
        if self._logo_manager:
            self._logo_manager.clear_cache()

        try:
            with self._db_factory() as conn:
                # Initialize dynamic resolver for this batch
                self._dynamic_resolver.initialize(self._db_factory, conn)

                # Get group settings
                group_id = group_config.get("id")
                duplicate_mode = group_config.get("duplicate_event_handling", "consolidate")

                # Channel group settings - now supports dynamic modes
                static_channel_group_id = group_config.get("channel_group_id")
                channel_group_mode = group_config.get("channel_group_mode", "static")

                # Parse profile IDs from group config (may contain wildcards like "{sport}", "{league}")
                # Returns None if not configured, [] if explicitly empty, [1,2,...] if set
                raw_profile_ids = group_config.get("channel_profile_ids")

                # Fallback to default profiles from settings ONLY if group hasn't configured
                # (raw value is None/missing). If group explicitly set [] for no profiles, use that.
                if raw_profile_ids is None:
                    from teamarr.database.settings import get_dispatcharr_settings

                    dispatcharr_settings = get_dispatcharr_settings(conn)
                    raw_profile_ids = dispatcharr_settings.default_channel_profile_ids

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

                    event_id = event.id
                    event_provider = getattr(event, "provider", "espn")
                    stream_name = stream.get("name", "")
                    stream_id = stream.get("id")

                    # UFC segment support: extract segment info if present
                    segment = matched.get("segment")  # e.g., "prelims", "main_card"
                    segment_display = matched.get("segment_display", "")
                    segment_start = matched.get("segment_start")  # Segment-specific start time
                    # For channel lookup/creation, use segment-aware event_id
                    # This treats each segment as a separate "sub-event"
                    effective_event_id = f"{event_id}-{segment}" if segment else event_id

                    # Check if event should be excluded based on timing
                    logger.debug(
                        "[LIFECYCLE] Checking stream '%s' for event %s (status=%s)",
                        stream_name[:40],
                        event_id,
                        event.status.state if event.status else "N/A",
                    )
                    excluded_reason = self._timing_manager.categorize_event_timing(event)
                    if excluded_reason:
                        result.excluded.append(
                            {
                                "stream": stream_name,
                                "stream_id": stream_id,
                                "event_id": event_id,
                                "event_name": event.short_name or event.name,
                                "reason": excluded_reason.value,
                                "reason_display": {
                                    "event_past": "Event already ended",
                                    "event_final": "Event is final",
                                    "before_create_window": "Before create window",
                                }.get(excluded_reason.value, excluded_reason.value),
                            }
                        )
                        continue

                    # Check exception keyword
                    matched_keyword, keyword_behavior = self._check_exception_keyword(
                        stream_name, conn
                    )

                    # V1 Parity: If behavior is 'ignore', skip stream entirely
                    # This must happen BEFORE any channel lookup/creation
                    if keyword_behavior == "ignore":
                        logger.debug(
                            f"Skipping stream '{stream_name}': "
                            f"keyword '{matched_keyword}' set to ignore"
                        )
                        result.skipped.append(
                            {
                                "stream": stream_name,
                                "stream_id": stream_id,
                                "event_id": event_id,
                                "reason": f"Exception keyword '{matched_keyword}' set to ignore",
                            }
                        )
                        continue

                    # Determine effective duplicate mode
                    effective_mode = keyword_behavior if keyword_behavior else duplicate_mode

                    # Find existing channel based on mode
                    # Use effective_event_id for segment-aware lookup
                    existing = find_existing_channel(
                        conn=conn,
                        group_id=group_id,
                        event_id=effective_event_id,
                        event_provider=event_provider,
                        exception_keyword=matched_keyword,
                        stream_id=stream_id,
                        mode=effective_mode,
                    )

                    if existing:
                        # Handle based on effective mode
                        channel_result = self._handle_existing_channel(
                            conn=conn,
                            existing=existing,
                            stream=stream,
                            event=event,
                            effective_mode=effective_mode,
                            matched_keyword=matched_keyword,
                            group_config=group_config,
                            template=template,
                        )
                        # None means Dispatcharr channel missing - fall through to create new
                        if channel_result is not None:
                            result.merge(channel_result)
                            continue

                    # Check if we should create based on timing
                    decision = self._timing_manager.should_create_channel(
                        event,
                        stream_exists=True,
                    )

                    if not decision.should_act:
                        logger.debug(
                            f"Skipping channel creation for '{stream_name}': {decision.reason}"
                        )
                        result.skipped.append(
                            {
                                "stream": stream_name,
                                "event_id": event_id,
                                "reason": decision.reason,
                            }
                        )
                        continue

                    # Cross-group overlap handling for multi-league groups
                    # Multi-league groups are processed LAST, so single-league channels exist
                    leagues = group_config.get("leagues", [])
                    is_multi_league = len(leagues) > 1
                    overlap_handling = group_config.get("overlap_handling", "add_stream")

                    if is_multi_league and overlap_handling != "create_all":
                        cross_group_result = self._handle_cross_group_overlap(
                            conn=conn,
                            event=event,
                            stream=stream,
                            group_id=group_id,
                            matched_keyword=matched_keyword,
                            overlap_handling=overlap_handling,
                            group_config=group_config,
                            template=template,
                            segment=segment,
                        )

                        if cross_group_result is not None:
                            # Stream was handled (added to existing or skipped)
                            result.merge(cross_group_result)
                            continue
                        # cross_group_result is None means: no existing channel found
                        # and not add_only mode, so fall through to create new channel

                    # Resolve dynamic channel group and profiles for this event
                    event_sport = getattr(event, "sport", None)
                    event_league = getattr(event, "league", None)

                    resolved_channel_group_id = self._dynamic_resolver.resolve_channel_group(
                        mode=channel_group_mode,
                        static_group_id=static_channel_group_id,
                        event_sport=event_sport,
                        event_league=event_league,
                    )

                    resolved_channel_profile_ids = self._dynamic_resolver.resolve_channel_profiles(
                        profile_ids=raw_profile_ids,
                        event_sport=event_sport,
                        event_league=event_league,
                    )

                    # Create new channel
                    channel_result = self._create_channel(
                        conn=conn,
                        event=event,
                        stream=stream,
                        group_config=group_config,
                        template=template,
                        matched_keyword=matched_keyword,
                        channel_group_id=resolved_channel_group_id,
                        channel_profile_ids=resolved_channel_profile_ids,
                        segment=segment,
                        segment_display=segment_display,
                        segment_start=segment_start,
                    )

                    if channel_result.success:
                        logger.info(
                            "[CHANNEL_CREATE] id=%s (#%s) stream='%s' event=%s status=%s",
                            channel_result.dispatcharr_channel_id,
                            channel_result.channel_number,
                            stream_name[:40],
                            event_id,
                            event.status.state if event.status else "N/A",
                        )
                        result.created.append(
                            {
                                "stream": stream_name,
                                "event_id": event_id,
                                "channel_id": channel_result.channel_id,
                                "dispatcharr_channel_id": channel_result.dispatcharr_channel_id,
                                "channel_number": channel_result.channel_number,
                                "tvg_id": channel_result.tvg_id,
                            }
                        )

                        # Log history
                        log_channel_history(
                            conn=conn,
                            managed_channel_id=channel_result.channel_id,
                            change_type="created",
                            change_source="epg_generation",
                            notes=f"Created from stream '{stream_name}'",
                        )
                    else:
                        logger.warning(
                            f"Failed to create channel for '{stream_name}': {channel_result.error}"
                        )
                        result.errors.append(
                            {
                                "stream": stream_name,
                                "event_id": event_id,
                                "error": channel_result.error,
                            }
                        )

                # Apply all pending profile changes in bulk
                self._apply_pending_profile_changes()

        except Exception as e:
            logger.exception("Error processing matched streams")
            result.errors.append({"error": str(e)})
            # Still try to apply pending profile changes even on error
            try:
                self._apply_pending_profile_changes()
            except Exception as profile_err:
                logger.debug("[LIFECYCLE] Failed to apply pending profile changes after error: %s", profile_err)

        return result

    def _handle_cross_group_overlap(
        self,
        conn: Connection,
        event: Event,
        stream: dict,
        group_id: int,
        matched_keyword: str | None,
        overlap_handling: str,
        group_config: dict,
        template: dict | None,
        segment: str | None = None,
    ) -> StreamProcessResult | None:
        """Handle cross-group overlap for multi-league groups.

        Multi-league groups are processed LAST, so single-league channels
        should already exist. This method checks for existing channels in
        OTHER groups and handles based on overlap_handling mode:

        - add_stream: Add to existing OR create new (returns None to create)
        - add_only: Add to existing OR skip (never create new)
        - skip: Skip if existing found, create if not (returns None to create)
        - create_all: Not called (handled by caller)

        Args:
            conn: Database connection
            event: Event to check
            stream: Stream data
            group_id: Current group ID (to exclude from search)
            matched_keyword: Exception keyword if matched
            overlap_handling: One of add_stream, add_only, skip
            group_config: Full group configuration
            template: Template configuration
            segment: UFC card segment (e.g., "prelims", "main_card")

        Returns:
            StreamProcessResult if stream was handled (added/skipped)
            None if no existing channel found and should create new
        """
        from teamarr.database.channels import (
            add_stream_to_channel,
            compute_stream_priority_from_rules,
            find_any_channel_for_event,
            get_next_stream_priority,
            get_ordered_stream_ids,
            log_channel_history,
            stream_exists_on_channel,
        )

        stream_name = stream.get("name", "")
        stream_id = stream.get("id")
        # Use segment-aware event_id for UFC segments
        event_id = f"{event.id}-{segment}" if segment else event.id
        event_provider = getattr(event, "provider", "espn")

        # First try to find channel with matching keyword (or no keyword)
        existing = find_any_channel_for_event(
            conn=conn,
            event_id=event_id,
            event_provider=event_provider,
            exclude_group_id=group_id,
            exception_keyword=matched_keyword,  # None for non-keyword streams
        )

        # If no exact match, fall back to any channel for the event
        # This allows non-keyword streams to use keyword channels as last resort
        if not existing:
            existing = find_any_channel_for_event(
                conn=conn,
                event_id=event_id,
                event_provider=event_provider,
                exclude_group_id=group_id,
                any_keyword=True,
            )

        if existing:
            # Found existing channel in another group
            if overlap_handling == "skip":
                # Skip mode: don't add stream, don't create channel
                result = StreamProcessResult()
                result.skipped.append(
                    {
                        "stream": stream_name,
                        "event_id": event_id,
                        "reason": "event_owned_by_other_group",
                        "existing_channel_id": existing.id,
                        "existing_group_id": existing.event_epg_group_id,
                    }
                )
                logger.debug(
                    f"Skipped '{stream_name}' - event owned by group {existing.event_epg_group_id}"
                )
                return result
            else:
                # add_stream or add_only: add stream to existing channel
                result = StreamProcessResult()

                if stream_exists_on_channel(conn, existing.id, stream_id):
                    result.existing.append(
                        {
                            "stream": stream_name,
                            "channel_id": existing.id,
                            "channel_number": existing.channel_number,
                            "action": "already_present",
                        }
                    )
                    return result

                # Add stream to existing channel
                # Compute priority from ordering rules (or use sequential if no rules)
                m3u_account_name = group_config.get("m3u_account_name")
                priority = compute_stream_priority_from_rules(
                    conn, stream_name, m3u_account_name, group_id
                )
                if priority is None:
                    priority = get_next_stream_priority(conn, existing.id)
                add_stream_to_channel(
                    conn=conn,
                    managed_channel_id=existing.id,
                    dispatcharr_stream_id=stream_id,
                    source_group_id=group_id,
                    source_group_type="cross_group",
                    stream_name=stream_name,
                    m3u_account_id=group_config.get("m3u_account_id"),
                    m3u_account_name=m3u_account_name,
                    priority=priority,
                )

                # Sync with Dispatcharr - use ordered stream list to respect rules
                if self._channel_manager and existing.dispatcharr_channel_id:
                    with self._dispatcharr_lock:
                        # Get streams in priority order from DB
                        ordered_streams = get_ordered_stream_ids(conn, existing.id)
                        self._channel_manager.update_channel(
                            existing.dispatcharr_channel_id,
                            {"streams": tuple(ordered_streams)},
                        )

                log_channel_history(
                    conn=conn,
                    managed_channel_id=existing.id,
                    change_type="stream_added",
                    change_source="cross_group_enforcement",
                    notes=f"Added '{stream_name}' from multi-league group {group_id}",
                )

                result.existing.append(
                    {
                        "stream": stream_name,
                        "channel_id": existing.id,
                        "channel_number": existing.channel_number,
                        "action": "added_cross_group",
                        "source_group_id": group_id,
                    }
                )

                logger.debug(
                    f"Added '{stream_name}' to existing channel #{existing.channel_number} "
                    f"(cross-group from {group_id})"
                )
                return result

        else:
            # No existing channel found in other groups
            if overlap_handling == "add_only":
                # add_only mode: don't create new channel, skip stream
                result = StreamProcessResult()
                result.skipped.append(
                    {
                        "stream": stream_name,
                        "event_id": event_id,
                        "reason": "no_existing_channel_for_add_only",
                    }
                )
                logger.debug(
                    f"Skipped '{stream_name}' - add_only mode and no existing channel"
                )
                return result
            else:
                # add_stream or skip mode with no existing channel: create new
                # Return None to signal caller should create channel
                return None

    def _handle_existing_channel(
        self,
        conn: Connection,
        existing: Any,
        stream: dict,
        event: Event,
        effective_mode: str,
        matched_keyword: str | None,
        group_config: dict,
        template: dict | None,
    ) -> StreamProcessResult | None:
        """Handle an existing channel based on duplicate mode.

        Returns:
            StreamProcessResult if channel was handled successfully
            None if Dispatcharr channel is missing and caller should create new
        """
        from teamarr.database.channels import (
            add_stream_to_channel,
            compute_stream_priority_from_rules,
            get_next_stream_priority,
            get_ordered_stream_ids,
            log_channel_history,
            mark_channel_deleted,
            stream_exists_on_channel,
        )

        result = StreamProcessResult()
        stream_name = stream.get("name", "")
        stream_id = stream.get("id")

        # Verify channel exists in Dispatcharr
        # If missing, mark as deleted and return None to signal caller to create new
        if self._channel_manager and existing.dispatcharr_channel_id:
            with self._dispatcharr_lock:
                disp_channel = self._channel_manager.get_channel(existing.dispatcharr_channel_id)
                if not disp_channel:
                    # Channel missing from Dispatcharr - mark old record deleted
                    # Return None to signal caller should create new channel
                    logger.warning(
                        f"Channel {existing.dispatcharr_channel_id} missing from "
                        f"Dispatcharr, marking deleted and will create new: {existing.channel_name}"
                    )
                    mark_channel_deleted(
                        conn, existing.id,
                        reason=f"Missing from Dispatcharr (ID {existing.dispatcharr_channel_id})"
                    )
                    log_channel_history(
                        conn=conn,
                        managed_channel_id=existing.id,
                        change_type="deleted",
                        change_source="lifecycle",
                        notes="Channel missing from Dispatcharr, marked for cleanup",
                    )
                    # Return None to signal caller to create new channel
                    return None

        if effective_mode == "ignore":
            # Skip - don't add stream, but still sync settings
            result.existing.append(
                {
                    "stream": stream_name,
                    "channel_id": existing.dispatcharr_channel_id,
                    "channel_number": existing.channel_number,
                    "action": "ignored",
                }
            )
            # Still sync channel settings even for ignored duplicates
            settings_result = self._sync_channel_settings(
                conn=conn,
                existing=existing,
                stream=stream,
                event=event,
                group_config=group_config,
                template=template,
            )
            result.merge(settings_result)
            return result

        if effective_mode == "consolidate":
            # Add stream to existing channel if not already present
            if not stream_exists_on_channel(conn, existing.id, stream_id):
                # Compute priority from ordering rules (or use sequential if no rules)
                m3u_account_name = stream.get("m3u_account_name") or group_config.get("m3u_account_name")
                source_group_id = group_config.get("id")
                priority = compute_stream_priority_from_rules(
                    conn, stream_name, m3u_account_name, source_group_id
                )
                if priority is None:
                    priority = get_next_stream_priority(conn, existing.id)

                # Add to DB
                add_stream_to_channel(
                    conn=conn,
                    managed_channel_id=existing.id,
                    dispatcharr_stream_id=stream_id,
                    stream_name=stream_name,
                    priority=priority,
                    exception_keyword=matched_keyword,
                    m3u_account_id=stream.get("m3u_account_id"),
                    m3u_account_name=m3u_account_name,
                    source_group_id=source_group_id,
                )

                # Sync with Dispatcharr - use ordered stream list to respect rules
                if self._channel_manager:
                    with self._dispatcharr_lock:
                        # Get streams in priority order from DB
                        ordered_streams = get_ordered_stream_ids(conn, existing.id)
                        self._channel_manager.update_channel(
                            existing.dispatcharr_channel_id,
                            {"streams": ordered_streams},
                        )

                # Log history
                log_channel_history(
                    conn=conn,
                    managed_channel_id=existing.id,
                    change_type="stream_added",
                    change_source="epg_generation",
                    notes=f"Added stream '{stream_name}' (consolidate mode)",
                )

                result.streams_added.append(
                    {
                        "stream": stream_name,
                        "channel_id": existing.dispatcharr_channel_id,
                        "channel_name": existing.channel_name,
                    }
                )

            result.existing.append(
                {
                    "stream": stream_name,
                    "channel_id": existing.dispatcharr_channel_id,
                    "channel_number": existing.channel_number,
                    "action": "consolidated",
                }
            )

        else:  # separate mode - channel found for this stream
            result.existing.append(
                {
                    "stream": stream_name,
                    "channel_id": existing.dispatcharr_channel_id,
                    "channel_number": existing.channel_number,
                    "action": "separate_exists",
                }
            )

        # Sync channel settings
        settings_result = self._sync_channel_settings(
            conn=conn,
            existing=existing,
            stream=stream,
            event=event,
            group_config=group_config,
            template=template,
        )
        result.merge(settings_result)

        return result

    def _create_channel(
        self,
        conn: Connection,
        event: Event,
        stream: dict,
        group_config: dict,
        template: dict | None,
        matched_keyword: str | None,
        channel_group_id: int | None,
        channel_profile_ids: list[int],
        segment: str | None = None,
        segment_display: str = "",
        segment_start: datetime | None = None,
    ) -> ChannelCreationResult:
        """Create a new channel in DB and Dispatcharr.

        Args:
            segment: UFC card segment code (e.g., "prelims", "main_card")
            segment_display: Display name for segment (e.g., "Prelims")
            segment_start: Segment-specific start time (for UFC segments)
        """
        from teamarr.database.channels import (
            add_stream_to_channel,
            create_managed_channel,
        )

        event_id = event.id
        event_provider = getattr(event, "provider", "espn")
        stream_name = stream.get("name", "")
        stream_id = stream.get("id")
        group_id = group_config.get("id")

        # For segments, use segment-aware event_id for DB storage
        effective_event_id = f"{event_id}-{segment}" if segment else event_id

        # Generate tvg_id with segment suffix
        tvg_id = generate_event_tvg_id(event_id, event_provider, segment)

        # Generate channel name, appending segment display if present
        channel_name = self._generate_channel_name(event, template, matched_keyword)
        if segment_display:
            channel_name = f"{channel_name} - {segment_display}"

        # Get channel number - use group's start number if configured
        group_start_number = group_config.get("channel_start_number")
        channel_number = self._get_next_channel_number(conn, group_id, group_start_number)
        if not channel_number:
            return ChannelCreationResult(
                success=False,
                error="Could not allocate channel number",
            )

        # Calculate delete time
        delete_time = self._timing_manager.calculate_delete_time(event)

        # Resolve logo URL from template (supports template variables including {exception_keyword})
        logo_url = self._resolve_logo_url(event, template, matched_keyword)

        # Create in Dispatcharr
        dispatcharr_channel_id = None
        dispatcharr_uuid = None
        dispatcharr_logo_id = None

        if self._channel_manager:
            with self._dispatcharr_lock:
                # Upload logo if specified
                if logo_url and self._logo_manager:
                    logo_result = self._logo_manager.upload(
                        name=f"{channel_name} Logo",
                        url=logo_url,
                    )
                    if logo_result.success and logo_result.logo:
                        dispatcharr_logo_id = logo_result.logo.get("id")

                # Create channel with channel_profile_ids
                # Dispatcharr profile semantics (as of commit 6b873be):
                #   [] = NO profiles (explicit)
                #   [0] = ALL profiles (sentinel)
                #   [1, 2, ...] = specific profile IDs
                #
                # Logic:
                #   None = not configured → default to [0] (all profiles, backwards compat)
                #   [] = explicitly no profiles → send [] (no profiles)
                #   [1, 2, ...] = specific profiles → send those
                effective_profile_ids = channel_profile_ids if channel_profile_ids is not None else [0]
                logger.debug(
                    f"Channel '{channel_name}' profile assignment: "
                    f"configured={channel_profile_ids}, effective={effective_profile_ids}"
                )
                create_result = self._channel_manager.create_channel(
                    name=channel_name,
                    channel_number=channel_number,
                    stream_ids=[stream_id],
                    tvg_id=tvg_id,
                    channel_group_id=channel_group_id,
                    logo_id=dispatcharr_logo_id,
                    channel_profile_ids=effective_profile_ids,
                )

                if not create_result.success:
                    return ChannelCreationResult(
                        success=False,
                        error=create_result.error or "Failed to create channel in Dispatcharr",
                    )

                if create_result.channel:
                    dispatcharr_channel_id = create_result.channel.get("id")
                    dispatcharr_uuid = create_result.channel.get("uuid")

        # Create in DB - with rollback protection for Dispatcharr orphans
        try:
            managed_channel_id = create_managed_channel(
                conn=conn,
                event_epg_group_id=group_id,
                event_id=effective_event_id,  # Segment-aware event ID for UFC segments
                event_provider=event_provider,
                tvg_id=tvg_id,
                channel_name=channel_name,
                channel_number=channel_number,
                logo_url=logo_url,
                dispatcharr_channel_id=dispatcharr_channel_id,
                dispatcharr_uuid=dispatcharr_uuid,
                dispatcharr_logo_id=dispatcharr_logo_id,
                channel_group_id=channel_group_id,
                channel_profile_ids=channel_profile_ids,
                primary_stream_id=stream_id,
                exception_keyword=matched_keyword,
                home_team=event.home_team.name if event.home_team else None,
                away_team=event.away_team.name if event.away_team else None,
                # Use segment-specific start time for UFC segments, otherwise event start
                event_date=(segment_start or event.start_time).isoformat() if (segment_start or event.start_time) else None,
                event_name=event.name,
                league=event.league,
                sport=event.sport,
                # V1 Parity: Include venue and broadcast
                venue=event.venue.name if event.venue else None,
                broadcast=", ".join(event.broadcasts) if event.broadcasts else None,
                scheduled_delete_at=delete_time.isoformat() if delete_time else None,
                sync_status="in_sync" if dispatcharr_channel_id else "pending",
            )

            # Add stream to managed_channel_streams
            # Use default priority - final ordering happens after all matching complete
            add_stream_to_channel(
                conn=conn,
                managed_channel_id=managed_channel_id,
                dispatcharr_stream_id=stream_id,
                stream_name=stream_name,
                priority=0,
                exception_keyword=matched_keyword,
                m3u_account_id=stream.get("m3u_account_id"),
                m3u_account_name=group_config.get("m3u_account_name"),
                source_group_id=group_id,
            )

            # Commit immediately so next channel number query sees this channel
            conn.commit()

        except Exception as e:
            # DB insert failed - clean up the Dispatcharr channel to prevent orphans
            logger.error("[LIFECYCLE] DB insert failed for channel '%s': %s", channel_name, e)
            if dispatcharr_channel_id and self._channel_manager:
                try:
                    with self._dispatcharr_lock:
                        self._channel_manager.delete_channel(dispatcharr_channel_id)
                    logger.info(
                        f"Cleaned up Dispatcharr channel {dispatcharr_channel_id} after DB failure"
                    )
                except Exception as cleanup_err:
                    logger.warning("[LIFECYCLE] Failed to cleanup Dispatcharr channel: %s", cleanup_err)

            return ChannelCreationResult(
                success=False,
                error=f"DB insert failed: {e}",
            )

        return ChannelCreationResult(
            success=True,
            channel_id=managed_channel_id,
            dispatcharr_channel_id=dispatcharr_channel_id,
            channel_number=channel_number,
            tvg_id=tvg_id,
        )

    def _generate_channel_name(
        self,
        event: Event,
        template,
        exception_keyword: str | None,
    ) -> str:
        """Generate channel name for an event.

        Uses full template engine (141 variables) when service is available.
        Otherwise falls back to default "Away @ Home" format.

        Supports {exception_keyword} variable in templates. If the template
        includes {exception_keyword}, the value is substituted directly.
        If not included and a keyword is present, it's auto-appended as
        "(Keyword)" to maintain backward compatibility.

        Also prepends "Postponed: " to the channel name if the event is
        postponed and the prepend_postponed_label setting is enabled.

        Args:
            event: Event data
            template: Can be dict, EventTemplateConfig dataclass, or None
            exception_keyword: Optional keyword for naming
        """
        # Get channel name format from template or use default
        name_format = None
        if template:
            # Handle both dict and dataclass template types
            if hasattr(template, "channel_name_format"):
                # EventTemplateConfig dataclass
                name_format = template.channel_name_format
            elif hasattr(template, "get"):
                # Dict with event_channel_name
                name_format = template.get("event_channel_name")

        # Build extra variables for template resolution
        # Always include exception_keyword - resolves to "" if None (graceful disappear)
        extra_vars = {
            "exception_keyword": exception_keyword.title() if exception_keyword else "",
        }

        if name_format:
            # Check if template uses {exception_keyword} - if so, don't auto-append
            template_uses_keyword = "{exception_keyword}" in name_format

            # Resolve using full template engine with extra variables
            # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
            base_name = self._resolve_template(name_format, event, extra_vars)

            # Clean up empty wrappers when {exception_keyword} resolves to ""
            # e.g., "Team A @ Team B ()" → "Team A @ Team B"
            base_name = self._clean_empty_wrappers(base_name)

            # Auto-append keyword only if template didn't use {exception_keyword}
            if exception_keyword and not template_uses_keyword:
                base_name = f"{base_name} ({exception_keyword.title()})"
        else:
            # Default format: "Away @ Home"
            home_name = event.home_team.short_name if event.home_team else "Home"
            away_name = event.away_team.short_name if event.away_team else "Away"
            base_name = f"{away_name} @ {home_name}"

            # Append keyword if present (default format doesn't support template vars)
            if exception_keyword:
                base_name = f"{base_name} ({exception_keyword.title()})"

        # Prepend "POSTPONED | " if event is postponed and setting is enabled
        if is_event_postponed(event):
            from teamarr.database.settings import get_epg_settings

            with self._db_factory() as conn:
                epg_settings = get_epg_settings(conn)
                if epg_settings.prepend_postponed_label:
                    base_name = f"{POSTPONED_LABEL}{base_name}"

        return base_name

    def _clean_empty_wrappers(self, text: str) -> str:
        """Clean up empty wrappers left when variables resolve to empty string.

        Removes:
        - Empty parentheses: () []
        - Trailing separators: " - ", " | ", " : "
        - Multiple consecutive spaces
        - Leading/trailing whitespace

        Examples:
            "Team A @ Team B ()" → "Team A @ Team B"
            "Team A @ Team B []" → "Team A @ Team B"
            "Team A @ Team B - " → "Team A @ Team B"
            "Team A  @  Team B" → "Team A @ Team B"
        """
        import re

        # Remove empty parentheses and brackets (with optional surrounding space)
        text = re.sub(r"\s*\(\s*\)", "", text)
        text = re.sub(r"\s*\[\s*\]", "", text)

        # Remove trailing separators
        text = re.sub(r"\s*[-|:]\s*$", "", text)

        # Collapse multiple spaces into one
        text = re.sub(r"\s{2,}", " ", text)

        return text.strip()

    def _resolve_logo_url(
        self,
        event: Event,
        template,
        exception_keyword: str | None = None,
    ) -> str | None:
        """Resolve logo URL from template.

        Uses full template engine for variable resolution.
        No fallback to team logo - if no template, returns None.

        Args:
            event: Event data
            template: Can be dict, EventTemplateConfig dataclass, or None
            exception_keyword: Optional keyword for {exception_keyword} variable
        """
        logo_url = None
        if template:
            # Handle both dict and dataclass template types
            if hasattr(template, "event_channel_logo_url"):
                # EventTemplateConfig dataclass
                logo_url = template.event_channel_logo_url
            elif hasattr(template, "get"):
                # Dict with event_channel_logo_url
                logo_url = template.get("event_channel_logo_url")

        if logo_url:
            # Resolve template variables if present
            # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
            if "{" in logo_url:
                extra_vars = {
                    "exception_keyword": exception_keyword.title() if exception_keyword else "",
                }
                return self._resolve_template(logo_url, event, extra_vars)
            return logo_url

        return None

    def _resolve_template(
        self,
        template_str: str,
        event: Event,
        extra_variables: dict[str, str] | None = None,
    ) -> str:
        """Resolve template string using full template engine.

        Supports all 141+ template variables plus optional extra variables.

        Args:
            template_str: Template string with {variable} placeholders
            event: Event to extract context from
            extra_variables: Optional dict of additional variables to resolve
                (e.g., {"exception_keyword": "Spanish"})

        Returns:
            Resolved string with variables replaced
        """
        # Handle extra variables first (simple replacement)
        if extra_variables:
            for var_name, value in extra_variables.items():
                template_str = template_str.replace(f"{{{var_name}}}", value)

        context = self._context_builder.build_for_event(
            event=event,
            team_id=event.home_team.id if event.home_team else "",
            league=event.league,
        )
        return self._resolver.resolve(template_str, context)

    def _get_next_channel_number(
        self,
        conn: Connection,
        group_id: int,
        group_start_number: int | None = None,
    ) -> int | None:
        """Get next available channel number for a group.

        Uses the channel_numbers module for AUTO/MANUAL mode support
        with range validation and 10-block intervals.

        Args:
            conn: Database connection
            group_id: Event EPG group ID
            group_start_number: Starting channel number from group config (unused, read from DB)

        Returns:
            Next available channel number as int, or None if range exhausted
        """
        from teamarr.database.channel_numbers import get_next_channel_number

        next_num = get_next_channel_number(conn, group_id, auto_assign=True)
        if next_num is None:
            logger.warning("[LIFECYCLE] Could not allocate channel number for group %d", group_id)
            return None
        return next_num

    def _sync_channel_settings(
        self,
        conn: Connection,
        existing: Any,
        stream: dict,
        event: Event,
        group_config: dict,
        template: dict | None,
    ) -> StreamProcessResult:
        """Sync channel settings from group/template to Dispatcharr.

        V1 Parity: Syncs all 8 channel properties:
        | Source              | Dispatcharr Field    | Handling                    |
        |---------------------|---------------------|-----------------------------|
        | template            | name                | Template variable resolution|
        | managed_channels    | channel_number      | DB is source of truth       |
        | group               | channel_group_id    | Simple compare              |
        | current_stream      | streams             | M3U ID lookup               |
        | group               | channel_profile_ids | Add/remove via profile API  |
        | template            | logo_id             | Upload/update if different  |
        | event_id            | tvg_id              | Ensures EPG matching        |
        """
        from teamarr.database.channels import (
            log_channel_history,
            update_managed_channel,
        )

        result = StreamProcessResult()

        if not self._channel_manager:
            return result

        try:
            with self._dispatcharr_lock:
                current_channel = self._channel_manager.get_channel(existing.dispatcharr_channel_id)
                if not current_channel:
                    return result

            update_data = {}
            db_updates = {}
            changes_made = []
            group_id = group_config.get("id")

            # 1. Check channel name (template resolution) - V1 parity
            matched_keyword = getattr(existing, "exception_keyword", None)
            expected_name = self._generate_channel_name(event, template, matched_keyword)
            if expected_name != current_channel.name:
                update_data["name"] = expected_name
                db_updates["channel_name"] = expected_name
                changes_made.append(f"name: {current_channel.name} → {expected_name}")

            # 2. Check channel number - Teamarr DB is source of truth
            # Handle channel numbers that may be floats as strings (e.g., "8121.0")
            expected_number = int(float(existing.channel_number)) if existing.channel_number else None
            current_number = (
                int(float(current_channel.channel_number)) if current_channel.channel_number else None
            )
            if expected_number and expected_number != current_number:
                update_data["channel_number"] = expected_number
                changes_made.append(f"number: {current_number} → {expected_number}")

            # 3. Check channel_group_id (supports dynamic sport/league resolution)
            channel_group_mode = group_config.get("channel_group_mode", "static")
            static_group_id = group_config.get("channel_group_id")
            event_sport = getattr(event, "sport", None)
            event_league = getattr(event, "league", None)

            # Resolve dynamic group ID (creates group in Dispatcharr if needed)
            new_group_id = self._dynamic_resolver.resolve_channel_group(
                mode=channel_group_mode,
                static_group_id=static_group_id,
                event_sport=event_sport,
                event_league=event_league,
            )

            old_group_id = current_channel.channel_group_id
            if new_group_id != old_group_id:
                update_data["channel_group_id"] = new_group_id
                changes_made.append(f"channel_group_id: {old_group_id} → {new_group_id}")

            # 4. Check streams (M3U ID sync) - V1 parity
            stream_id = stream.get("id") if stream else None
            if stream_id:
                # streams is already tuple[int, ...] of stream IDs
                ch_streams = current_channel.streams
                current_stream_ids = list(ch_streams) if ch_streams else []
                if stream_id not in current_stream_ids:
                    # Stream changed - update to use current stream
                    # Note: For consolidate mode, this adds streams; for separate, this replaces
                    new_streams = current_stream_ids + [stream_id]
                    update_data["streams"] = new_streams
                    db_updates["dispatcharr_stream_id"] = stream_id
                    changes_made.append(f"streams: added {stream_id}")

            # Note: Stream ordering is applied as a final step after all matching
            # See generation.py Step 3b - this ensures all streams from all groups
            # are considered together when computing final order

            # 5. Check tvg_id
            expected_tvg_id = existing.tvg_id
            if expected_tvg_id != current_channel.tvg_id:
                update_data["tvg_id"] = expected_tvg_id
                changes_made.append(f"tvg_id: {current_channel.tvg_id} → {expected_tvg_id}")

            # 6b. Recalculate scheduled_delete_at based on current settings
            expected_delete_time = self._timing_manager.calculate_delete_time(event)
            if expected_delete_time:
                expected_delete_str = expected_delete_time.isoformat()
                stored_delete_str = getattr(existing, "scheduled_delete_at", None)
                # Compare as strings (both should be ISO format)
                if stored_delete_str:
                    stored_delete_str = str(stored_delete_str)
                if expected_delete_str != stored_delete_str:
                    db_updates["scheduled_delete_at"] = expected_delete_str
                    changes_made.append("scheduled_delete_at updated")

            # Apply Dispatcharr updates
            if update_data:
                with self._dispatcharr_lock:
                    self._channel_manager.update_channel(
                        existing.dispatcharr_channel_id,
                        update_data,
                    )

            # Apply DB updates
            if db_updates:
                update_managed_channel(conn, existing.id, db_updates)

            # 7. Sync channel_profile_ids (supports dynamic {sport}/{league} resolution)
            # Dispatcharr profile semantics (commit 6b873be):
            #   [] = NO profiles
            #   [0] = ALL profiles (sentinel)
            #   [1, 2, ...] = specific profile IDs
            raw_group_profiles = group_config.get("channel_profile_ids")
            stored_profile_ids = self._parse_profile_ids(
                getattr(existing, "channel_profile_ids", None)
            )

            # Resolve dynamic profile IDs (expands "{sport}" and "{league}" wildcards)
            if raw_group_profiles is not None:
                resolved_profile_ids = self._dynamic_resolver.resolve_channel_profiles(
                    profile_ids=raw_group_profiles,
                    event_sport=event_sport,
                    event_league=event_league,
                )
                effective_profile_ids = resolved_profile_ids if resolved_profile_ids else []
            else:
                # None (not configured) → default to [0] (all profiles)
                effective_profile_ids = [0]

            logger.debug(
                f"Channel '{existing.channel_name}' profile sync: "
                f"raw={raw_group_profiles}, resolved={effective_profile_ids}, "
                f"stored={stored_profile_ids}"
            )

            # Check if profiles changed
            if effective_profile_ids != stored_profile_ids:
                logger.info(
                    f"Channel '{existing.channel_name}' profiles changed: "
                    f"{stored_profile_ids} → {effective_profile_ids}"
                )
                # For sentinel values ([0] or []), PATCH the channel directly
                # This lets Dispatcharr handle the "all profiles" or "no profiles" logic
                is_sentinel = effective_profile_ids in ([0], [])

                if is_sentinel:
                    # PATCH channel_profile_ids directly with sentinel
                    with self._dispatcharr_lock:
                        self._channel_manager.update_channel(
                            existing.dispatcharr_channel_id,
                            {"channel_profile_ids": effective_profile_ids},
                        )
                    if effective_profile_ids == [0]:
                        changes_made.append("profiles: all profiles")
                    else:
                        changes_made.append("profiles: no profiles")
                else:
                    # Specific profile IDs - collect for bulk application
                    profiles_to_add = set(effective_profile_ids) - set(stored_profile_ids)
                    profiles_to_remove = set(stored_profile_ids) - set(effective_profile_ids)

                    channel_id = existing.dispatcharr_channel_id
                    for profile_id in profiles_to_remove:
                        self._collect_profile_change(profile_id, channel_id, "remove")
                        changes_made.append(f"queued remove from profile {profile_id}")

                    for profile_id in profiles_to_add:
                        self._collect_profile_change(profile_id, channel_id, "add")
                        changes_made.append(f"queued add to profile {profile_id}")

                # Update stored profile IDs in DB
                update_managed_channel(
                    conn, existing.id, {"channel_profile_ids": json.dumps(effective_profile_ids)}
                )

            # 8. Sync logo - handles both updates and removals
            logo_url = self._resolve_logo_url(event, template, matched_keyword)
            current_logo_id = getattr(existing, "dispatcharr_logo_id", None)
            stored_logo_url = getattr(existing, "logo_url", None)

            if logo_url and self._logo_manager:
                # Logo is set - check if needs update
                # Also trigger if logo_id is missing (initial upload may have failed)
                needs_logo_update = logo_url != stored_logo_url or not current_logo_id
                if needs_logo_update:
                    reason = "URL changed" if logo_url != stored_logo_url else "missing logo_id"
                    logger.debug(
                        "[LIFECYCLE] Logo sync for '%s': %s (stored=%s, new=%s, logo_id=%s)",
                        existing.channel_name, reason, stored_logo_url, logo_url, current_logo_id
                    )
                    with self._dispatcharr_lock:
                        # Upload new logo
                        logo_result = self._logo_manager.upload(
                            name=f"{existing.channel_name} Logo",
                            url=logo_url,
                        )
                        if logo_result.success and logo_result.logo:
                            new_logo_id = logo_result.logo.get("id")
                            # Update channel with new logo
                            self._channel_manager.update_channel(
                                existing.dispatcharr_channel_id,
                                {"logo_id": new_logo_id},
                            )
                            # Update DB
                            update_managed_channel(
                                conn,
                                existing.id,
                                {
                                    "logo_url": logo_url,
                                    "dispatcharr_logo_id": new_logo_id,
                                },
                            )
                            changes_made.append("logo updated")

                            # Delete old logo if it existed
                            if current_logo_id:
                                try:
                                    self._logo_manager.delete(current_logo_id)
                                except Exception:
                                    pass  # Ignore logo deletion failures

            elif stored_logo_url and self._logo_manager:
                # Logo was removed from template - clear it
                with self._dispatcharr_lock:
                    # Remove logo from Dispatcharr channel
                    self._channel_manager.update_channel(
                        existing.dispatcharr_channel_id,
                        {"logo_id": None},
                    )
                    # Update DB
                    update_managed_channel(
                        conn,
                        existing.id,
                        {
                            "logo_url": None,
                            "dispatcharr_logo_id": None,
                        },
                    )
                    changes_made.append("logo removed")

                    # Delete old logo from Dispatcharr
                    if current_logo_id:
                        try:
                            self._logo_manager.delete(current_logo_id)
                        except Exception:
                            pass  # Ignore logo deletion failures

            # Log changes if any
            if changes_made:
                result.settings_updated.append(
                    {
                        "channel_id": existing.dispatcharr_channel_id,
                        "channel_name": existing.channel_name,
                        "changes": changes_made,
                    }
                )

                # Log to history
                log_channel_history(
                    conn=conn,
                    managed_channel_id=existing.id,
                    change_type="synced",
                    change_source="epg_generation",
                    notes=f"Settings synced: {', '.join(changes_made)}",
                )

        except Exception as e:
            logger.debug("[LIFECYCLE] Error syncing settings for channel %s: %s", existing.channel_name, e)

        return result

    def _remove_stream_from_dispatcharr_channel(
        self,
        dispatcharr_channel_id: int,
        stream_id: int,
    ) -> bool:
        """Remove a stream from a Dispatcharr channel's stream list.

        Args:
            dispatcharr_channel_id: The Dispatcharr channel ID
            stream_id: The stream ID to remove

        Returns:
            True if the stream was removed, False otherwise
        """
        if not self._channel_manager:
            return False

        with self._dispatcharr_lock:
            current = self._channel_manager.get_channel(dispatcharr_channel_id)
            if not current:
                return False

            # streams is tuple[int, ...] of IDs
            current_ids = list(current.streams) if current.streams else []
            if stream_id not in current_ids:
                return False

            current_ids.remove(stream_id)
            self._channel_manager.update_channel(
                dispatcharr_channel_id,
                {"streams": current_ids},
            )
            return True

    def delete_managed_channel(
        self,
        conn: Connection,
        managed_channel_id: int,
        reason: str = "scheduled",
    ) -> bool:
        """Delete a managed channel from Dispatcharr and mark as deleted in DB.

        V1 Parity: Also deletes the channel's logo from Dispatcharr.

        Args:
            conn: Database connection
            managed_channel_id: Managed channel ID
            reason: Deletion reason

        Returns:
            True if deleted successfully
        """
        from teamarr.database.channels import (
            get_managed_channel,
            log_channel_history,
            mark_channel_deleted,
        )

        channel = get_managed_channel(conn, managed_channel_id)
        if not channel:
            return False

        # Delete logo from Dispatcharr (V1 parity)
        logo_id = getattr(channel, "dispatcharr_logo_id", None)
        if self._logo_manager and logo_id:
            try:
                with self._dispatcharr_lock:
                    self._logo_manager.delete(logo_id)
            except Exception as e:
                logger.debug("[LIFECYCLE] Failed to delete logo %s: %s", logo_id, e)

        # Delete channel from Dispatcharr
        if self._channel_manager and channel.dispatcharr_channel_id:
            with self._dispatcharr_lock:
                result = self._channel_manager.delete_channel(channel.dispatcharr_channel_id)
                if not result.success:
                    logger.warning(
                        f"Failed to delete channel {channel.dispatcharr_channel_id} "
                        f"from Dispatcharr: {result.error}"
                    )

        # Mark as deleted in DB
        mark_channel_deleted(conn, managed_channel_id, reason)

        # Log history
        log_channel_history(
            conn=conn,
            managed_channel_id=managed_channel_id,
            change_type="deleted",
            change_source="lifecycle",
            notes=f"Deleted: {reason}",
        )

        logger.info("[LIFECYCLE] Deleted channel '%s' (%s)", channel.channel_name, reason)
        return True

    def process_scheduled_deletions(self) -> StreamProcessResult:
        """Process all channels past their scheduled delete time.

        First recalculates scheduled_delete_at for all active channels based on
        current settings (handles settings changes), then deletes any that are past due.

        Returns:
            StreamProcessResult with deleted channels
        """
        from teamarr.database.channels import (
            get_channels_pending_deletion,
        )

        result = StreamProcessResult()

        try:
            with self._db_factory() as conn:
                # Step 1: Recalculate scheduled_delete_at for all active channels
                # This handles settings changes (e.g., day_after -> 6_hours_after)
                self._recalculate_deletion_times(conn)

                # Step 2: Get channels that are now past their delete time
                channels = get_channels_pending_deletion(conn)

                for channel in channels:
                    success = self.delete_managed_channel(
                        conn,
                        channel.id,
                        reason="scheduled_delete",
                    )

                    if success:
                        result.deleted.append(
                            {
                                "channel_id": channel.id,
                                "channel_name": channel.channel_name,
                                "tvg_id": channel.tvg_id,
                            }
                        )
                    else:
                        result.errors.append(
                            {
                                "channel_id": channel.id,
                                "channel_name": channel.channel_name,
                                "error": "Failed to delete",
                            }
                        )

        except Exception as e:
            logger.exception("Error processing scheduled deletions")
            result.errors.append({"error": str(e)})

        if result.deleted:
            logger.info("[LIFECYCLE] Deleted %d expired channels", len(result.deleted))

        return result

    def _recalculate_deletion_times(self, conn) -> int:
        """Recalculate scheduled_delete_at for all active channels.

        This handles settings changes (e.g., day_after -> 6_hours_after) by
        recalculating deletion times based on current settings.

        Args:
            conn: Database connection

        Returns:
            Number of channels updated
        """
        from datetime import datetime, timedelta

        from dateutil import parser

        from teamarr.database.channels import get_all_managed_channels, update_managed_channel
        from teamarr.utilities.sports import get_sport_duration
        from teamarr.utilities.tz import to_user_tz

        channels = get_all_managed_channels(conn, include_deleted=False)
        updated_count = 0

        # Get delete timing setting
        delete_timing = self._timing_manager.delete_timing
        sport_durations = self._timing_manager.sport_durations
        default_duration = self._timing_manager.default_duration_hours

        for channel in channels:
            # Skip channels without event_date (can't calculate delete time)
            if not channel.event_date:
                continue

            try:
                # Parse event date
                event_start = parser.parse(str(channel.event_date))
                event_start = to_user_tz(event_start)

                # Calculate event end time using sport-specific duration
                sport = channel.sport or "other"
                duration_hours = get_sport_duration(sport, sport_durations, default_duration)
                event_end = event_start + timedelta(hours=duration_hours)

                # Calculate delete threshold based on timing setting
                end_date = event_end.date()
                day_end = datetime.combine(
                    end_date,
                    datetime.max.time(),
                ).replace(tzinfo=event_end.tzinfo)

                timing_map = {
                    "6_hours_after": event_end + timedelta(hours=6),
                    "same_day": day_end,
                    "day_after": day_end + timedelta(days=1),
                    "2_days_after": day_end + timedelta(days=2),
                    "3_days_after": day_end + timedelta(days=3),
                    "1_week_after": day_end + timedelta(days=7),
                }

                expected_delete_time = timing_map.get(delete_timing)
                if not expected_delete_time:
                    continue

                expected_delete_str = expected_delete_time.isoformat()
                stored_delete_str = str(channel.scheduled_delete_at) if channel.scheduled_delete_at else None

                # Update if different
                if expected_delete_str != stored_delete_str:
                    update_managed_channel(conn, channel.id, {"scheduled_delete_at": expected_delete_str})
                    updated_count += 1
                    logger.debug(
                        f"Updated scheduled_delete_at for '{channel.channel_name}': "
                        f"{stored_delete_str} -> {expected_delete_str}"
                    )

            except Exception as e:
                logger.debug("[LIFECYCLE] Error recalculating delete time for channel %d: %s", channel.id, e)
                continue

        if updated_count > 0:
            logger.info("[LIFECYCLE] Recalculated scheduled_delete_at for %d channels", updated_count)

        return updated_count

    def associate_epg_with_channels(self, epg_source_id: int | None = None) -> dict:
        """Associate EPG data with managed channels after EPG refresh.

        Looks up EPGData by tvg_id and calls set_channel_epg to link them.

        Args:
            epg_source_id: Optional EPG source ID (uses default from settings if not provided)

        Returns:
            Dict with success/error counts
        """
        from teamarr.database.channels import get_all_managed_channels

        if not self._channel_manager or not self._epg_manager:
            return {"error": "Dispatcharr not configured"}

        result = {"associated": 0, "not_found": 0, "errors": 0}

        with self._db_factory() as conn:
            # Get all active managed channels
            channels = get_all_managed_channels(conn, include_deleted=False)

            if not channels:
                return result

            # Build EPG data lookup from Dispatcharr (via ChannelManager)
            epg_lookup = self._channel_manager.build_epg_lookup(epg_source_id)

            for channel in channels:
                if not channel.dispatcharr_channel_id or not channel.tvg_id:
                    continue

                # Look up EPG data by tvg_id
                epg_data = epg_lookup.get(channel.tvg_id)

                if not epg_data:
                    result["not_found"] += 1
                    continue

                # Associate EPG with channel
                epg_data_id = epg_data.get("id")
                if not epg_data_id:
                    result["not_found"] += 1
                    continue

                try:
                    with self._dispatcharr_lock:
                        self._channel_manager.set_channel_epg(
                            channel.dispatcharr_channel_id,
                            epg_data_id,
                        )
                    result["associated"] += 1
                except Exception as e:
                    logger.debug("[LIFECYCLE] Failed to associate EPG for channel %s: %s", channel.channel_name, e)
                    result["errors"] += 1

        if result["associated"]:
            logger.info("[LIFECYCLE] Associated EPG data with %d channels", result['associated'])

        return result

    def cleanup_deleted_streams(
        self,
        group_id: int,
        current_streams: dict[int, dict],
    ) -> StreamProcessResult:
        """Clean up channels for streams that no longer exist or have changed content.

        V1 Parity: Runs regardless of delete_timing because missing streams
        should trigger immediate deletion.

        Also detects content changes via fingerprint comparison - if a stream's
        name changed (indicating different content), it's removed from the channel.

        Args:
            group_id: Event EPG group ID
            current_streams: Dict mapping stream_id -> stream_data with 'name' field

        Returns:
            StreamProcessResult with deleted channels and errors
        """
        from teamarr.consumers.stream_match_cache import compute_fingerprint
        from teamarr.database.channels import (
            get_channel_streams,
            get_managed_channels_for_group,
            log_channel_history,
            remove_stream_from_channel,
        )

        result = StreamProcessResult()
        current_ids_set = set(current_streams.keys())

        try:
            with self._db_factory() as conn:
                # Get all active channels for the group
                channels = get_managed_channels_for_group(conn, group_id)

                for channel in channels:
                    # Get streams associated with this channel
                    streams = get_channel_streams(conn, channel.id)

                    if not streams:
                        # Legacy fallback: check primary_stream_id
                        primary_id = getattr(channel, "primary_stream_id", None)
                        if primary_id and primary_id not in current_ids_set:
                            success = self.delete_managed_channel(
                                conn,
                                channel.id,
                                reason="primary stream removed",
                            )
                            if success:
                                result.deleted.append(
                                    {
                                        "channel_id": channel.dispatcharr_channel_id,
                                        "channel_number": channel.channel_number,
                                        "channel_name": channel.channel_name,
                                        "reason": "primary stream no longer exists",
                                    }
                                )
                        continue

                    # Categorize streams: valid, missing, or content-changed
                    valid_streams = []
                    missing_streams = []
                    changed_streams = []

                    for s in streams:
                        stream_id = getattr(s, "dispatcharr_stream_id", None)
                        stored_name = getattr(s, "stream_name", None)

                        if not stream_id:
                            continue

                        if stream_id not in current_ids_set:
                            # Stream no longer in M3U
                            missing_streams.append(s)
                        else:
                            # Stream exists - check if content changed via fingerprint
                            current_stream = current_streams.get(stream_id, {})
                            current_name = current_stream.get("name", "")

                            if stored_name and current_name and stored_name != current_name:
                                # Content changed - fingerprint would differ
                                stored_fp = compute_fingerprint(group_id, stream_id, stored_name)
                                current_fp = compute_fingerprint(group_id, stream_id, current_name)

                                if stored_fp != current_fp:
                                    changed_streams.append({
                                        "stream": s,
                                        "old_name": stored_name,
                                        "new_name": current_name,
                                    })
                                    continue

                            valid_streams.append(s)

                    # Combine missing and changed streams for removal
                    streams_to_remove = missing_streams + [c["stream"] for c in changed_streams]

                    if not valid_streams and streams_to_remove:
                        # All streams gone or changed - delete channel
                        success = self.delete_managed_channel(
                            conn,
                            channel.id,
                            reason="all streams removed or changed",
                        )
                        if success:
                            result.deleted.append(
                                {
                                    "channel_id": channel.dispatcharr_channel_id,
                                    "channel_number": channel.channel_number,
                                    "channel_name": channel.channel_name,
                                    "reason": "all streams no longer exist or content changed",
                                }
                            )
                        else:
                            result.errors.append(
                                {
                                    "channel_id": channel.dispatcharr_channel_id,
                                    "error": "Failed to delete channel",
                                }
                            )

                    elif streams_to_remove:
                        # Some streams gone/changed - remove them from channel
                        for s in missing_streams:
                            stream_id = getattr(s, "dispatcharr_stream_id", None)
                            if stream_id:
                                remove_stream_from_channel(conn, channel.id, stream_id)
                                self._remove_stream_from_dispatcharr_channel(
                                    channel.dispatcharr_channel_id,
                                    stream_id,
                                )
                                log_channel_history(
                                    conn=conn,
                                    managed_channel_id=channel.id,
                                    change_type="stream_removed",
                                    change_source="lifecycle",
                                    notes=f"Stream {stream_id} no longer exists in M3U",
                                )

                        for changed in changed_streams:
                            s = changed["stream"]
                            stream_id = getattr(s, "dispatcharr_stream_id", None)
                            if stream_id:
                                remove_stream_from_channel(conn, channel.id, stream_id)
                                self._remove_stream_from_dispatcharr_channel(
                                    channel.dispatcharr_channel_id,
                                    stream_id,
                                )
                                log_channel_history(
                                    conn=conn,
                                    managed_channel_id=channel.id,
                                    change_type="stream_removed",
                                    change_source="lifecycle",
                                    notes=f"Stream {stream_id} content changed: '{changed['old_name']}' -> '{changed['new_name']}'",
                                )
                                logger.debug(
                                    f"Removed stream {stream_id} from channel "
                                    f"'{channel.channel_name}': content changed"
                                )

                        result.streams_removed.append(
                            {
                                "channel_id": channel.dispatcharr_channel_id,
                                "channel_name": channel.channel_name,
                                "streams_removed": len(streams_to_remove),
                                "missing": len(missing_streams),
                                "content_changed": len(changed_streams),
                            }
                        )

        except Exception as e:
            logger.exception(f"Error cleaning up deleted streams for group {group_id}")
            result.errors.append({"error": str(e)})

        if result.deleted:
            logger.info("[LIFECYCLE] Deleted %d channels with missing/changed streams", len(result.deleted))

        return result

    def reassign_group_channels(self, group_id: int) -> dict:
        """Reassign ALL channels in a group to their correct range.

        V1 Parity: Called during EPG generation when AUTO sort order changes
        or MANUAL start changes. Compacts channels to fill gaps.

        Args:
            group_id: Event EPG group ID

        Returns:
            Dict with reassigned, already_correct, and errors
        """
        from teamarr.database.channel_numbers import get_group_channel_range
        from teamarr.database.channels import (
            get_managed_channels_for_group,
            log_channel_history,
            update_managed_channel,
        )
        from teamarr.database.groups import get_group

        result = {
            "reassigned": [],
            "already_correct": [],
            "errors": [],
        }

        try:
            with self._db_factory() as conn:
                group = get_group(conn, group_id)
                if not group:
                    result["errors"].append({"error": f"Group {group_id} not found"})
                    return result

                # Get expected range for this group
                range_start, range_end = get_group_channel_range(conn, group_id)
                if range_start is None:
                    # No range configured - skip
                    return result

                # Get and sort active channels by current number
                channels = get_managed_channels_for_group(conn, group_id)
                if not channels:
                    return result

                # Sort by current channel number to maintain relative order
                sorted_channels = sorted(
                    channels, key=lambda c: int(float(c.channel_number)) if c.channel_number else 9999
                )

                # Reassign to compact range
                next_number = range_start
                for channel in sorted_channels:
                    current_number = int(float(channel.channel_number)) if channel.channel_number else None

                    if current_number == next_number:
                        # Already at correct position
                        result["already_correct"].append(
                            {
                                "channel_id": channel.dispatcharr_channel_id,
                                "channel_number": current_number,
                            }
                        )
                        next_number += 1
                        continue

                    # Check for overflow
                    if range_end and next_number > range_end:
                        result["errors"].append(
                            {
                                "channel_id": channel.dispatcharr_channel_id,
                                "channel_name": channel.channel_name,
                                "error": f"Range exhausted (max {range_end})",
                            }
                        )
                        continue

                    # Update Dispatcharr
                    if self._channel_manager:
                        with self._dispatcharr_lock:
                            self._channel_manager.update_channel(
                                channel.dispatcharr_channel_id,
                                {"channel_number": next_number},
                            )

                    # Update DB
                    update_managed_channel(conn, channel.id, {"channel_number": next_number})

                    # Log history
                    log_channel_history(
                        conn=conn,
                        managed_channel_id=channel.id,
                        change_type="modified",
                        change_source="lifecycle",
                        notes=f"Channel number: {current_number} → {next_number}",
                    )

                    result["reassigned"].append(
                        {
                            "channel_id": channel.dispatcharr_channel_id,
                            "channel_name": channel.channel_name,
                            "old_number": current_number,
                            "new_number": next_number,
                        }
                    )

                    next_number += 1

        except Exception as e:
            logger.exception(f"Error reassigning channels for group {group_id}")
            result["errors"].append({"error": str(e)})

        if result["reassigned"]:
            logger.info("[LIFECYCLE] Reassigned %d channels in group %d", len(result['reassigned']), group_id)

        return result

    def reassign_all_auto_groups(self) -> dict:
        """Reassign ALL AUTO groups to their correct ranges.

        V1 Parity: Called at the start of EPG generation to ensure
        all AUTO groups have correct non-overlapping ranges based on
        current channel counts and sort order.

        This handles the case where:
        - Group A (sort=1) expands and needs more channels
        - Group B (sort=2) has channels in Group A's expanded range
        - Group B's channels must move to make room

        Returns:
            Dict with total stats across all groups
        """
        from teamarr.database.channel_numbers import (
            _calculate_blocks_needed,
            _get_total_stream_count,
            get_global_channel_range,
        )
        from teamarr.database.channels import (
            get_managed_channels_for_group,
            log_channel_history,
            update_managed_channel,
        )

        result = {
            "groups_processed": 0,
            "channels_reassigned": 0,
            "errors": [],
        }

        try:
            with self._db_factory() as conn:
                # Get global channel range
                range_start, range_end = get_global_channel_range(conn)

                # Get all AUTO groups sorted by sort_order
                auto_groups = conn.execute(
                    """SELECT id, name, sort_order
                       FROM event_epg_groups
                       WHERE channel_assignment_mode = 'auto'
                         AND parent_group_id IS NULL
                         AND enabled = 1
                       ORDER BY sort_order ASC"""
                ).fetchall()

                if not auto_groups:
                    return result

                # Calculate ideal ranges for each group based on raw stream count
                group_ranges = []
                current_start = range_start
                for grp in auto_groups:
                    group_id = grp["id"]
                    # Use total_stream_count (raw M3U) for range reservation
                    total_streams = _get_total_stream_count(conn, group_id)
                    blocks_needed = _calculate_blocks_needed(total_streams)
                    group_end = current_start + (blocks_needed * 10) - 1

                    group_ranges.append(
                        {
                            "id": group_id,
                            "name": grp["name"],
                            "ideal_start": current_start,
                            "ideal_end": group_end,
                            "stream_count": total_streams,
                        }
                    )

                    current_start = group_end + 1

                # Process each group and reassign channels if needed
                for grp_range in group_ranges:
                    group_id = grp_range["id"]
                    ideal_start = grp_range["ideal_start"]

                    # Get channels for this group sorted by current number
                    channels = get_managed_channels_for_group(conn, group_id)
                    if not channels:
                        continue

                    sorted_channels = sorted(
                        channels, key=lambda c: int(float(c.channel_number)) if c.channel_number else 9999
                    )

                    # Reassign to ideal range
                    next_number = ideal_start
                    for channel in sorted_channels:
                        current_num = (
                            int(float(channel.channel_number)) if channel.channel_number else None
                        )

                        if current_num == next_number:
                            next_number += 1
                            continue

                        # Need to reassign
                        if self._channel_manager:
                            with self._dispatcharr_lock:
                                self._channel_manager.update_channel(
                                    channel.dispatcharr_channel_id,
                                    {"channel_number": next_number},
                                )

                        update_managed_channel(conn, channel.id, {"channel_number": next_number})

                        log_channel_history(
                            conn=conn,
                            managed_channel_id=channel.id,
                            change_type="modified",
                            change_source="lifecycle",
                            notes=f"Channel number: {current_num} → {next_number}",
                        )

                        result["channels_reassigned"] += 1
                        next_number += 1

                    result["groups_processed"] += 1

        except Exception as e:
            logger.exception("Error in global AUTO group reassignment")
            result["errors"].append({"error": str(e)})

        if result["channels_reassigned"]:
            logger.info(
                f"Global reassignment: {result['channels_reassigned']} channels "
                f"across {result['groups_processed']} groups"
            )

        return result

    def cleanup_orphan_dispatcharr_channels(self) -> dict:
        """Clean up orphan channels in Dispatcharr.

        V1 Parity: Runs every EPG generation to find and delete orphan channels.

        Orphan channels are Dispatcharr channels with teamarr-event-* tvg_id
        that aren't tracked (or are tracked as deleted) in our DB.

        These can occur when:
        - Dispatcharr delete API call failed but DB was marked deleted
        - Same event got a new channel, old one wasn't cleaned up
        - Manual intervention or bugs

        Returns:
            Dict with 'deleted' count and 'errors' list
        """
        from teamarr.database.channels import get_all_managed_channels

        result = {"deleted": 0, "errors": []}

        if not self._channel_manager:
            return result

        try:
            with self._db_factory() as conn:
                # Get all teamarr channels from Dispatcharr
                with self._dispatcharr_lock:
                    all_dispatcharr = self._channel_manager.get_channels()

                teamarr_channels = [
                    c for c in all_dispatcharr if (c.tvg_id or "").startswith("teamarr-event-")
                ]

                if not teamarr_channels:
                    return result

                # Get active DB channels (by dispatcharr_channel_id and UUID)
                db_channels = get_all_managed_channels(conn, include_deleted=False)
                active_ids = {
                    c.dispatcharr_channel_id for c in db_channels if c.dispatcharr_channel_id
                }
                active_uuids = {c.dispatcharr_uuid for c in db_channels if c.dispatcharr_uuid}

                # Find orphans
                orphans = [
                    c
                    for c in teamarr_channels
                    if c.id not in active_ids and (not c.uuid or c.uuid not in active_uuids)
                ]

                if not orphans:
                    return result

                logger.info("[LIFECYCLE] Found %d orphan Dispatcharr channel(s) to clean up", len(orphans))

                for orphan in orphans:
                    try:
                        with self._dispatcharr_lock:
                            delete_result = self._channel_manager.delete_channel(orphan.id)

                        is_success = delete_result.success
                        is_not_found = "not found" in str(delete_result.error or "").lower()
                        if is_success or is_not_found:
                            result["deleted"] += 1
                            logger.debug(
                                f"Deleted orphan channel #{orphan.channel_number} - {orphan.name}"
                            )
                        else:
                            result["errors"].append(
                                {
                                    "channel_id": orphan.id,
                                    "channel_name": orphan.name,
                                    "error": delete_result.error,
                                }
                            )
                    except Exception as e:
                        result["errors"].append(
                            {
                                "channel_id": orphan.id,
                                "channel_name": orphan.name,
                                "error": str(e),
                            }
                        )

        except Exception as e:
            logger.exception("Error cleaning up orphan Dispatcharr channels")
            result["errors"].append({"error": str(e)})

        if result["deleted"] > 0:
            logger.info("[LIFECYCLE] Cleaned up %d orphan Dispatcharr channels", result['deleted'])

        return result

    def cleanup_disabled_groups(self) -> dict:
        """Clean up channels from disabled event groups.

        When a group is DISABLED, channels are cleaned up at the next EPG
        generation rather than immediately. This allows users to re-enable
        the group without losing channels.

        V1 Parity: Matches cleanup_disabled_groups() from channel_lifecycle.py

        Returns:
            Dict with 'deleted' and 'errors' lists
        """
        from teamarr.database.channels import (
            get_managed_channels_for_group,
            mark_channel_deleted,
        )
        from teamarr.database.groups import get_all_groups

        result: dict = {"deleted": [], "errors": []}

        try:
            with self._db_factory() as conn:
                # Get ALL groups including disabled
                all_groups = get_all_groups(conn, include_disabled=True)

                # Filter to disabled groups only
                disabled_groups = [g for g in all_groups if not g.enabled]

                if not disabled_groups:
                    return result

                logger.info(
                    f"Checking {len(disabled_groups)} disabled group(s) for channel cleanup..."
                )

                for group in disabled_groups:
                    group_id = group.id
                    group_name = group.name

                    # Get channels for this disabled group
                    channels = get_managed_channels_for_group(
                        conn, group_id, include_deleted=False
                    )

                    for channel in channels:
                        try:
                            # Delete from Dispatcharr
                            if self._channel_manager and channel.dispatcharr_channel_id:
                                with self._dispatcharr_lock:
                                    self._channel_manager.delete_channel(
                                        channel.dispatcharr_channel_id
                                    )

                            # Mark as deleted in DB
                            mark_channel_deleted(
                                conn, channel.id, reason=f"Group '{group_name}' disabled"
                            )

                            result["deleted"].append(
                                {
                                    "group": group_name,
                                    "channel_number": channel.channel_number,
                                    "channel_name": channel.channel_name,
                                }
                            )
                        except Exception as e:
                            result["errors"].append(
                                {
                                    "group": group_name,
                                    "channel_id": channel.dispatcharr_channel_id,
                                    "error": str(e),
                                }
                            )

                conn.commit()

        except Exception as e:
            logger.exception("Error cleaning up disabled groups")
            result["errors"].append({"error": str(e)})

        if result["deleted"]:
            logger.info(
                f"Cleaned up {len(result['deleted'])} channel(s) from disabled groups"
            )

        return result

    def _parse_profile_ids(self, raw: Any) -> list[int]:
        """Parse channel profile IDs from various formats."""
        if not raw:
            return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return []
        if isinstance(raw, list):
            return [int(x) for x in raw if x]
        return []
