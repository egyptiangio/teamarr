"""Channel lifecycle management for event-based EPG.

Handles creation and deletion timing for event channels.
Channels are created before events and deleted after.

Create timing options:
- stream_available: Create immediately when stream exists
- same_day: Create on the day of the event
- day_before: Create 1 day before event
- 2_days_before, 3_days_before, 1_week_before

Delete timing options:
- stream_removed: Delete only when stream disappears
- same_day: Delete at 23:59 of event END date
- day_after: Delete at 23:59 of day after event ends
- 2_days_after, 3_days_after, 1_week_after

EPG Association Flow:
1. Generate consistent tvg_id: teamarr-event-{event_id}
2. Create channel in Dispatcharr with this tvg_id
3. Generate XMLTV with matching channel id
4. After EPG refresh, look up EPGData by tvg_id
5. Call set_channel_epg(channel_id, epg_data_id) to associate
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlite3 import Connection
from typing import Any, Literal

from teamarr.core import Event
from teamarr.utilities.time_blocks import crosses_midnight
from teamarr.utilities.tz import now_user, to_user_tz

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


CreateTiming = Literal[
    "stream_available",
    "same_day",
    "day_before",
    "2_days_before",
    "3_days_before",
    "1_week_before",
    "manual",
]

DeleteTiming = Literal[
    "stream_removed",
    "same_day",
    "day_after",
    "2_days_after",
    "3_days_after",
    "1_week_after",
    "manual",
]

DuplicateMode = Literal["consolidate", "separate", "ignore"]


@dataclass
class LifecycleDecision:
    """Result of a lifecycle check."""

    should_act: bool
    reason: str
    threshold_time: datetime | None = None


@dataclass
class ChannelCreationResult:
    """Result of channel creation."""

    success: bool
    channel_id: int | None = None
    dispatcharr_channel_id: int | None = None
    channel_number: str | None = None
    tvg_id: str | None = None
    error: str | None = None


@dataclass
class StreamProcessResult:
    """Result of processing matched streams."""

    created: list[dict] = field(default_factory=list)
    existing: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    streams_added: list[dict] = field(default_factory=list)
    logo_updated: list[dict] = field(default_factory=list)
    settings_updated: list[dict] = field(default_factory=list)
    deleted: list[dict] = field(default_factory=list)

    def merge(self, other: "StreamProcessResult") -> None:
        """Merge another result into this one."""
        self.created.extend(other.created)
        self.existing.extend(other.existing)
        self.skipped.extend(other.skipped)
        self.errors.extend(other.errors)
        self.streams_added.extend(other.streams_added)
        self.logo_updated.extend(other.logo_updated)
        self.settings_updated.extend(other.settings_updated)
        self.deleted.extend(other.deleted)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "created": self.created,
            "existing": self.existing,
            "skipped": self.skipped,
            "errors": self.errors,
            "streams_added": self.streams_added,
            "logo_updated": self.logo_updated,
            "settings_updated": self.settings_updated,
            "deleted": self.deleted,
            "summary": {
                "created_count": len(self.created),
                "existing_count": len(self.existing),
                "skipped_count": len(self.skipped),
                "error_count": len(self.errors),
            },
        }


def generate_event_tvg_id(event_id: str, provider: str = "espn") -> str:
    """Generate consistent tvg_id for an event.

    This tvg_id is used:
    1. In XMLTV <channel id="..."> and <programme channel="...">
    2. When creating channels in Dispatcharr
    3. To look up EPGData for channel-EPG association

    Args:
        event_id: Provider event ID (e.g., "401547679")
        provider: Provider name (default: espn)

    Returns:
        Formatted tvg_id (e.g., "teamarr-event-401547679")
    """
    return f"teamarr-event-{event_id}"


# =============================================================================
# TIMING DECISIONS
# =============================================================================


class ChannelLifecycleManager:
    """Manages event channel creation and deletion timing.

    Usage:
        manager = ChannelLifecycleManager(
            create_timing='same_day',
            delete_timing='day_after',
            default_duration_hours=3.0,
        )

        # Check if channel should be created
        decision = manager.should_create_channel(event)
        if decision.should_act:
            create_channel(event)

        # Check if channel should be deleted
        decision = manager.should_delete_channel(event)
        if decision.should_act:
            delete_channel(event)
    """

    def __init__(
        self,
        create_timing: CreateTiming = "same_day",
        delete_timing: DeleteTiming = "day_after",
        default_duration_hours: float = 3.0,
    ):
        self.create_timing = create_timing
        self.delete_timing = delete_timing
        self.default_duration_hours = default_duration_hours

    def should_create_channel(
        self,
        event: Event,
        stream_exists: bool = False,
    ) -> LifecycleDecision:
        """Determine if a channel should be created for this event.

        Args:
            event: The event to check
            stream_exists: Whether a matching stream currently exists

        Returns:
            LifecycleDecision with should_act and reason
        """
        if self.create_timing == "manual":
            return LifecycleDecision(False, "Manual creation only")

        if self.create_timing == "stream_available":
            if stream_exists:
                return LifecycleDecision(True, "Stream available")
            return LifecycleDecision(False, "Waiting for stream")

        # Calculate create threshold
        create_threshold = self._calculate_create_threshold(event)
        now = now_user()

        # Check if we're past delete threshold (prevents create-then-delete)
        delete_threshold = self._calculate_delete_threshold(event)
        if delete_threshold and now >= delete_threshold:
            return LifecycleDecision(
                False,
                f"Past delete threshold ({delete_threshold.strftime('%m/%d %I:%M %p')})",
                delete_threshold,
            )

        if now >= create_threshold:
            return LifecycleDecision(
                True,
                f"Create threshold reached ({create_threshold.strftime('%m/%d %I:%M %p')})",
                create_threshold,
            )

        return LifecycleDecision(
            False,
            f"Before create threshold ({create_threshold.strftime('%m/%d %I:%M %p')})",
            create_threshold,
        )

    def should_delete_channel(
        self,
        event: Event,
        stream_exists: bool = True,
    ) -> LifecycleDecision:
        """Determine if a channel should be deleted for this event.

        Args:
            event: The event to check
            stream_exists: Whether a matching stream currently exists

        Returns:
            LifecycleDecision with should_act and reason
        """
        if self.delete_timing == "manual":
            return LifecycleDecision(False, "Manual deletion only")

        if self.delete_timing == "stream_removed":
            if not stream_exists:
                return LifecycleDecision(True, "Stream removed")
            return LifecycleDecision(False, "Stream still exists")

        # Calculate delete threshold
        delete_threshold = self._calculate_delete_threshold(event)
        if not delete_threshold:
            return LifecycleDecision(False, "Could not calculate delete time")

        now = now_user()

        if now >= delete_threshold:
            return LifecycleDecision(
                True,
                f"Delete threshold reached ({delete_threshold.strftime('%m/%d %I:%M %p')})",
                delete_threshold,
            )

        return LifecycleDecision(
            False,
            f"Before delete threshold ({delete_threshold.strftime('%m/%d %I:%M %p')})",
            delete_threshold,
        )

    def _calculate_create_threshold(self, event: Event) -> datetime:
        """Calculate when channel should be created."""
        event_start = to_user_tz(event.start_time)

        # Start of event day (midnight)
        day_start = event_start.replace(hour=0, minute=0, second=0, microsecond=0)

        timing_map = {
            "same_day": day_start,
            "day_before": day_start - timedelta(days=1),
            "2_days_before": day_start - timedelta(days=2),
            "3_days_before": day_start - timedelta(days=3),
            "1_week_before": day_start - timedelta(days=7),
        }

        return timing_map.get(self.create_timing, day_start)

    def _calculate_delete_threshold(self, event: Event) -> datetime | None:
        """Calculate when channel should be deleted.

        Uses event END date for midnight-crossing games.
        """
        event_start = to_user_tz(event.start_time)
        event_end = event_start + timedelta(hours=self.default_duration_hours)

        # Use END date (important for midnight-crossing games)
        end_date = event_end.date()

        # End of day (23:59:59)
        day_end = datetime.combine(
            end_date,
            datetime.max.time(),
        ).replace(tzinfo=event_end.tzinfo)

        timing_map = {
            "same_day": day_end,
            "day_after": day_end + timedelta(days=1),
            "2_days_after": day_end + timedelta(days=2),
            "3_days_after": day_end + timedelta(days=3),
            "1_week_after": day_end + timedelta(days=7),
        }

        return timing_map.get(self.delete_timing)

    def calculate_delete_time(self, event: Event) -> datetime | None:
        """Calculate scheduled delete time for an event."""
        return self._calculate_delete_threshold(event)

    def get_event_end_time(self, event: Event) -> datetime:
        """Calculate estimated event end time."""
        return to_user_tz(event.start_time) + timedelta(hours=self.default_duration_hours)

    def event_crosses_midnight(self, event: Event) -> bool:
        """Check if event crosses midnight."""
        start = to_user_tz(event.start_time)
        end = self.get_event_end_time(event)
        return crosses_midnight(start, end)


# =============================================================================
# CHANNEL LIFECYCLE SERVICE
# =============================================================================


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
            channel_manager: ChannelManager instance for Dispatcharr operations
            logo_manager: LogoManager instance for logo operations
            epg_manager: EPGManager instance for EPG operations
            create_timing: When to create channels
            delete_timing: When to delete channels
            default_duration_hours: Default event duration
            timezone: User timezone for timing calculations
        """
        self._db_factory = db_factory
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
        conn = self._db_factory()

        try:
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
                    result.errors.append({
                        "stream": stream.get("name", "Unknown"),
                        "error": "No event data",
                    })
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
                    result.skipped.append({
                        "stream": stream_name,
                        "event_id": event_id,
                        "reason": decision.reason,
                    })
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
                    result.created.append({
                        "stream": stream_name,
                        "event_id": event_id,
                        "channel_id": channel_result.channel_id,
                        "dispatcharr_channel_id": channel_result.dispatcharr_channel_id,
                        "channel_number": channel_result.channel_number,
                        "tvg_id": channel_result.tvg_id,
                    })

                    # Log history
                    log_channel_history(
                        conn=conn,
                        managed_channel_id=channel_result.channel_id,
                        change_type="created",
                        change_source="epg_generation",
                        notes=f"Created from stream '{stream_name}'",
                    )
                else:
                    result.errors.append({
                        "stream": stream_name,
                        "event_id": event_id,
                        "error": channel_result.error,
                    })

            conn.commit()
        except Exception as e:
            logger.exception("Error processing matched streams")
            result.errors.append({"error": str(e)})
        finally:
            conn.close()

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
            result.existing.append({
                "stream": stream_name,
                "channel_id": existing.dispatcharr_channel_id,
                "channel_number": existing.channel_number,
                "action": "ignored",
            })
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
                        current = self._channel_manager.get(existing.dispatcharr_channel_id)
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

                result.streams_added.append({
                    "stream": stream_name,
                    "channel_id": existing.dispatcharr_channel_id,
                    "channel_name": existing.channel_name,
                })

            result.existing.append({
                "stream": stream_name,
                "channel_id": existing.dispatcharr_channel_id,
                "channel_number": existing.channel_number,
                "action": "consolidated",
            })

        else:  # separate mode - channel found for this stream
            result.existing.append({
                "stream": stream_name,
                "channel_id": existing.dispatcharr_channel_id,
                "channel_number": existing.channel_number,
                "action": "separate_exists",
            })

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

        # Get channel number
        channel_number = self._get_next_channel_number(conn, group_id)
        if not channel_number:
            return ChannelCreationResult(
                success=False,
                error="Could not allocate channel number",
            )

        # Calculate delete time
        delete_time = self._timing_manager.calculate_delete_time(event)

        # Get logo URL from template
        logo_url = None
        if template:
            logo_url = template.get("channel_logo_url")
            # TODO: resolve template variables in logo_url

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
            sync_status="synced" if dispatcharr_channel_id else "pending",
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
        """Generate channel name for an event."""
        # TODO: Use template engine for channel name resolution

        # Default format: "Away @ Home"
        home_name = event.home_team.short_name if event.home_team else "Home"
        away_name = event.away_team.short_name if event.away_team else "Away"

        base_name = f"{away_name} @ {home_name}"

        # Append keyword if present
        if exception_keyword:
            return f"{base_name} ({exception_keyword.title()})"

        return base_name

    def _get_next_channel_number(self, conn: Connection, group_id: int) -> str | None:
        """Get next available channel number for a group."""
        # TODO: Implement proper channel number allocation
        # For now, return a placeholder
        cursor = conn.execute(
            """SELECT COALESCE(MAX(CAST(channel_number AS INTEGER)), 5000) + 1
               FROM managed_channels
               WHERE event_epg_group_id = ? AND deleted_at IS NULL""",
            (group_id,),
        )
        row = cursor.fetchone()
        return str(row[0]) if row else None

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
                current_channel = self._channel_manager.get(existing.dispatcharr_channel_id)
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

                result.settings_updated.append({
                    "channel_id": existing.dispatcharr_channel_id,
                    "channel_name": existing.channel_name,
                    "changes": update_data,
                })

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
                result = self._channel_manager.delete_channel(
                    channel.dispatcharr_channel_id
                )
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
        conn = self._db_factory()

        try:
            channels = get_channels_pending_deletion(conn)

            for channel in channels:
                success = self.delete_managed_channel(
                    conn,
                    channel.id,
                    reason="scheduled_delete",
                )

                if success:
                    result.deleted.append({
                        "channel_id": channel.id,
                        "channel_name": channel.channel_name,
                        "tvg_id": channel.tvg_id,
                    })
                else:
                    result.errors.append({
                        "channel_id": channel.id,
                        "channel_name": channel.channel_name,
                        "error": "Failed to delete",
                    })

            conn.commit()
        except Exception as e:
            logger.exception("Error processing scheduled deletions")
            result.errors.append({"error": str(e)})
        finally:
            conn.close()

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

        conn = self._db_factory()
        result = {"associated": 0, "not_found": 0, "errors": 0}

        try:
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
                    logger.debug(
                        f"Failed to associate EPG for channel {channel.channel_name}: {e}"
                    )
                    result["errors"] += 1

        finally:
            conn.close()

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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_lifecycle_settings(conn: Connection) -> dict:
    """Get global channel lifecycle settings from the settings table.

    Returns:
        Dict with create_timing, delete_timing, duplicate_handling settings
    """
    cursor = conn.execute(
        """SELECT channel_create_timing, channel_delete_timing,
                  default_duplicate_event_handling
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if row:
        return {
            "create_timing": row["channel_create_timing"] or "same_day",
            "delete_timing": row["channel_delete_timing"] or "day_after",
            "duplicate_handling": row["default_duplicate_event_handling"] or "consolidate",
        }

    return {
        "create_timing": "same_day",
        "delete_timing": "day_after",
        "duplicate_handling": "consolidate",
    }


def create_lifecycle_service(
    db_factory: Any,
    dispatcharr_client: Any = None,
) -> ChannelLifecycleService:
    """Create a ChannelLifecycleService with optional Dispatcharr integration.

    Args:
        db_factory: Factory function returning database connection
        dispatcharr_client: Optional DispatcharrClient instance

    Returns:
        Configured ChannelLifecycleService
    """
    from teamarr.database.channels import get_dispatcharr_settings

    conn = db_factory()
    try:
        settings = get_dispatcharr_settings(conn)
        lifecycle = get_lifecycle_settings(conn)
    finally:
        conn.close()

    channel_manager = None
    logo_manager = None
    epg_manager = None

    if dispatcharr_client and settings.get("enabled"):
        from teamarr.dispatcharr import ChannelManager, EPGManager, LogoManager

        channel_manager = ChannelManager(dispatcharr_client)
        logo_manager = LogoManager(dispatcharr_client)
        epg_manager = EPGManager(dispatcharr_client)

    return ChannelLifecycleService(
        db_factory=db_factory,
        channel_manager=channel_manager,
        logo_manager=logo_manager,
        epg_manager=epg_manager,
        create_timing=lifecycle["create_timing"],
        delete_timing=lifecycle["delete_timing"],
    )
