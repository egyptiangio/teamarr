"""Channel lifecycle service.

Full channel lifecycle management with Dispatcharr integration.
Handles channel creation, deletion, settings sync, and EPG association.
"""

import json
import logging
import threading
from sqlite3 import Connection
from typing import Any

from teamarr.core import Event
from teamarr.templates import ContextBuilder, TemplateResolver

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
        timezone: str = "America/New_York",
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
            timezone: User timezone for timing calculations

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
        )

        # Thread lock for Dispatcharr operations
        self._dispatcharr_lock = threading.Lock()

        # Cache exception keywords
        self._exception_keywords: list | None = None

        # Template engine
        self._context_builder = ContextBuilder(sports_service)
        self._resolver = TemplateResolver()

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

        try:
            with self._db_factory() as conn:
                # Get group settings
                group_id = group_config.get("id")
                duplicate_mode = group_config.get("duplicate_event_handling", "consolidate")
                channel_group_id = group_config.get("channel_group_id")
                stream_profile_id = group_config.get("stream_profile_id")
                channel_profile_ids = self._parse_profile_ids(
                    group_config.get("channel_profile_ids")
                )

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

                    # Check exception keyword
                    matched_keyword, keyword_behavior = self._check_exception_keyword(
                        stream_name, conn
                    )

                    # Determine effective duplicate mode
                    effective_mode = keyword_behavior if keyword_behavior else duplicate_mode

                    # Find existing channel based on mode
                    existing = find_existing_channel(
                        conn=conn,
                        group_id=group_id,
                        event_id=event_id,
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
                        result.merge(channel_result)
                        continue

                    # Check if we should create based on timing
                    decision = self._timing_manager.should_create_channel(
                        event,
                        stream_exists=True,
                    )

                    if not decision.should_act:
                        result.skipped.append(
                            {
                                "stream": stream_name,
                                "event_id": event_id,
                                "reason": decision.reason,
                            }
                        )
                        continue

                    # Create new channel
                    channel_result = self._create_channel(
                        conn=conn,
                        event=event,
                        stream=stream,
                        group_config=group_config,
                        template=template,
                        matched_keyword=matched_keyword,
                        channel_group_id=channel_group_id,
                        stream_profile_id=stream_profile_id,
                        channel_profile_ids=channel_profile_ids,
                    )

                    if channel_result.success:
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
                        result.errors.append(
                            {
                                "stream": stream_name,
                                "event_id": event_id,
                                "error": channel_result.error,
                            }
                        )

        except Exception as e:
            logger.exception("Error processing matched streams")
            result.errors.append({"error": str(e)})

        return result

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
    ) -> StreamProcessResult:
        """Handle an existing channel based on duplicate mode."""
        from teamarr.database.channels import (
            add_stream_to_channel,
            get_next_stream_priority,
            log_channel_history,
            stream_exists_on_channel,
        )

        result = StreamProcessResult()
        stream_name = stream.get("name", "")
        stream_id = stream.get("id")

        if effective_mode == "ignore":
            # Skip - don't add stream
            result.existing.append(
                {
                    "stream": stream_name,
                    "channel_id": existing.dispatcharr_channel_id,
                    "channel_number": existing.channel_number,
                    "action": "ignored",
                }
            )
            return result

        if effective_mode == "consolidate":
            # Add stream to existing channel if not already present
            if not stream_exists_on_channel(conn, existing.id, stream_id):
                # Add to DB
                priority = get_next_stream_priority(conn, existing.id)
                add_stream_to_channel(
                    conn=conn,
                    managed_channel_id=existing.id,
                    dispatcharr_stream_id=stream_id,
                    stream_name=stream_name,
                    priority=priority,
                    exception_keyword=matched_keyword,
                    m3u_account_id=stream.get("m3u_account_id"),
                )

                # Add to Dispatcharr
                if self._channel_manager:
                    with self._dispatcharr_lock:
                        current = self._channel_manager.get_channel(existing.dispatcharr_channel_id)
                        if current:
                            current_streams = [s.id for s in current.streams]
                            if stream_id not in current_streams:
                                current_streams.append(stream_id)
                                self._channel_manager.update_channel(
                                    existing.dispatcharr_channel_id,
                                    {"streams": current_streams},
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
        stream_profile_id: int | None,
        channel_profile_ids: list[int],
    ) -> ChannelCreationResult:
        """Create a new channel in DB and Dispatcharr."""
        from teamarr.database.channels import (
            add_stream_to_channel,
            create_managed_channel,
        )

        event_id = event.id
        event_provider = getattr(event, "provider", "espn")
        stream_name = stream.get("name", "")
        stream_id = stream.get("id")
        group_id = group_config.get("id")

        # Generate tvg_id
        tvg_id = generate_event_tvg_id(event_id, event_provider)

        # Generate channel name
        channel_name = self._generate_channel_name(event, template, matched_keyword)

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

        # Resolve logo URL from template (supports template variables)
        logo_url = self._resolve_logo_url(event, template)

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

                # Create channel
                create_result = self._channel_manager.create_channel(
                    name=channel_name,
                    channel_number=int(channel_number),
                    stream_ids=[stream_id],
                    tvg_id=tvg_id,
                    channel_group_id=channel_group_id,
                    logo_id=dispatcharr_logo_id,
                    stream_profile_id=stream_profile_id,
                )

                if not create_result.success:
                    return ChannelCreationResult(
                        success=False,
                        error=create_result.error or "Failed to create channel in Dispatcharr",
                    )

                if create_result.channel:
                    dispatcharr_channel_id = create_result.channel.get("id")
                    dispatcharr_uuid = create_result.channel.get("uuid")

                    # Add to channel profiles
                    for profile_id in channel_profile_ids:
                        self._channel_manager.add_to_profile(
                            profile_id,
                            dispatcharr_channel_id,
                        )

        # Create in DB
        managed_channel_id = create_managed_channel(
            conn=conn,
            event_epg_group_id=group_id,
            event_id=event_id,
            event_provider=event_provider,
            tvg_id=tvg_id,
            channel_name=channel_name,
            channel_number=channel_number,
            logo_url=logo_url,
            dispatcharr_channel_id=dispatcharr_channel_id,
            dispatcharr_uuid=dispatcharr_uuid,
            dispatcharr_logo_id=dispatcharr_logo_id,
            channel_group_id=channel_group_id,
            stream_profile_id=stream_profile_id,
            channel_profile_ids=channel_profile_ids,
            primary_stream_id=stream_id,
            exception_keyword=matched_keyword,
            home_team=event.home_team.name if event.home_team else None,
            away_team=event.away_team.name if event.away_team else None,
            event_date=event.start_time.isoformat() if event.start_time else None,
            event_name=event.name,
            league=event.league,
            sport=event.sport,
            scheduled_delete_at=delete_time.isoformat() if delete_time else None,
            sync_status="in_sync" if dispatcharr_channel_id else "pending",
        )

        # Add stream to managed_channel_streams
        add_stream_to_channel(
            conn=conn,
            managed_channel_id=managed_channel_id,
            dispatcharr_stream_id=stream_id,
            stream_name=stream_name,
            priority=0,
            exception_keyword=matched_keyword,
            m3u_account_id=stream.get("m3u_account_id"),
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
        template: dict | None,
        exception_keyword: str | None,
    ) -> str:
        """Generate channel name for an event.

        Uses full template engine (141 variables) when service is available.
        Otherwise falls back to default "Away @ Home" format.
        """
        # Get channel name format from template or use default
        name_format = None
        if template:
            name_format = template.get("event_channel_name")

        if name_format:
            # Resolve using full template engine
            base_name = self._resolve_template(name_format, event)
        else:
            # Default format: "Away @ Home"
            home_name = event.home_team.short_name if event.home_team else "Home"
            away_name = event.away_team.short_name if event.away_team else "Away"
            base_name = f"{away_name} @ {home_name}"

        # Append keyword if present
        if exception_keyword:
            return f"{base_name} ({exception_keyword.title()})"

        return base_name

    def _resolve_logo_url(
        self,
        event: Event,
        template: dict | None,
    ) -> str | None:
        """Resolve logo URL from template.

        Uses full template engine for variable resolution.
        Falls back to home team logo if no template.
        """
        logo_url = None
        if template:
            logo_url = template.get("event_channel_logo_url")

        if logo_url and "{" in logo_url:
            # Has template variables - resolve them
            resolved = self._resolve_template(logo_url, event)

            # Check if resolution succeeded (no unresolved placeholders)
            if "{" not in resolved:
                return resolved

        if logo_url:
            # Static URL - use as-is
            return logo_url

        # Fallback to home team logo
        if event.home_team and event.home_team.logo_url:
            return event.home_team.logo_url

        return None

    def _resolve_template(self, template_str: str, event: Event) -> str:
        """Resolve template string using full template engine.

        Supports all 141 template variables.

        Args:
            template_str: Template string with {variable} placeholders
            event: Event to extract context from

        Returns:
            Resolved string with variables replaced
        """
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
    ) -> str | None:
        """Get next available channel number for a group.

        Uses the channel_numbers module for AUTO/MANUAL mode support
        with range validation and 10-block intervals.

        Args:
            conn: Database connection
            group_id: Event EPG group ID
            group_start_number: Starting channel number from group config (unused, read from DB)

        Returns:
            Next available channel number as string, or None if range exhausted
        """
        from teamarr.database.channel_numbers import get_next_channel_number

        next_num = get_next_channel_number(conn, group_id, auto_assign=True)
        if next_num is None:
            logger.warning(f"Could not allocate channel number for group {group_id}")
            return None
        return str(next_num)

    def _sync_channel_settings(
        self,
        conn: Connection,
        existing: Any,
        stream: dict,
        event: Event,
        group_config: dict,
        template: dict | None,
    ) -> StreamProcessResult:
        """Sync channel settings from group/template to Dispatcharr."""
        result = StreamProcessResult()

        if not self._channel_manager:
            return result

        try:
            with self._dispatcharr_lock:
                current_channel = self._channel_manager.get_channel(existing.dispatcharr_channel_id)
                if not current_channel:
                    return result

            update_data = {}

            # Check channel_group_id
            group_channel_group_id = group_config.get("channel_group_id")
            if group_channel_group_id != current_channel.channel_group_id:
                update_data["channel_group_id"] = group_channel_group_id

            # Check stream_profile_id
            group_stream_profile_id = group_config.get("stream_profile_id")
            if group_stream_profile_id != current_channel.stream_profile_id:
                update_data["stream_profile_id"] = group_stream_profile_id

            # Check tvg_id
            expected_tvg_id = existing.tvg_id
            if expected_tvg_id != current_channel.tvg_id:
                update_data["tvg_id"] = expected_tvg_id

            if update_data:
                with self._dispatcharr_lock:
                    self._channel_manager.update_channel(
                        existing.dispatcharr_channel_id,
                        update_data,
                    )

                result.settings_updated.append(
                    {
                        "channel_id": existing.dispatcharr_channel_id,
                        "channel_name": existing.channel_name,
                        "changes": update_data,
                    }
                )

        except Exception as e:
            logger.debug(f"Error syncing settings for channel {existing.channel_name}: {e}")

        return result

    def delete_managed_channel(
        self,
        conn: Connection,
        managed_channel_id: int,
        reason: str = "scheduled",
    ) -> bool:
        """Delete a managed channel from Dispatcharr and mark as deleted in DB.

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

        # Delete from Dispatcharr
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

        logger.info(f"Deleted channel '{channel.channel_name}' ({reason})")
        return True

    def process_scheduled_deletions(self) -> StreamProcessResult:
        """Process all channels past their scheduled delete time.

        Returns:
            StreamProcessResult with deleted channels
        """
        from teamarr.database.channels import get_channels_pending_deletion

        result = StreamProcessResult()

        try:
            with self._db_factory() as conn:
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
            logger.info(f"Deleted {len(result.deleted)} expired channels")

        return result

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

            # Build EPG data lookup from Dispatcharr
            epg_lookup = self._epg_manager.build_epg_lookup(epg_source_id)

            for channel in channels:
                if not channel.dispatcharr_channel_id or not channel.tvg_id:
                    continue

                # Look up EPG data by tvg_id
                epg_data = epg_lookup.get(channel.tvg_id)

                if not epg_data:
                    result["not_found"] += 1
                    continue

                # Associate EPG with channel
                try:
                    with self._dispatcharr_lock:
                        self._channel_manager.set_channel_epg(
                            channel.dispatcharr_channel_id,
                            epg_data.id,
                        )
                    result["associated"] += 1
                except Exception as e:
                    logger.debug(f"Failed to associate EPG for channel {channel.channel_name}: {e}")
                    result["errors"] += 1

        if result["associated"]:
            logger.info(f"Associated EPG data with {result['associated']} channels")

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
