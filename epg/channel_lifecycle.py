"""
Channel Lifecycle Manager for Event-based EPG

Handles automatic channel creation and deletion in Dispatcharr
based on matched streams and lifecycle settings.

EPG Association Flow (matching Dispatcharr's internal pattern):
1. Generate consistent tvg_id: teamarr-event-{espn_event_id}
2. Create channel in Dispatcharr with this tvg_id
3. Generate XMLTV with matching channel id
4. After EPG refresh, look up EPGData by tvg_id
5. Call set_channel_epg(channel_id, epg_data_id) to associate
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def generate_event_tvg_id(espn_event_id: str) -> str:
    """
    Generate consistent tvg_id for an event.

    This tvg_id is used:
    1. In XMLTV <channel id="..."> and <programme channel="...">
    2. When creating channels in Dispatcharr
    3. To look up EPGData for channel-EPG association

    Args:
        espn_event_id: ESPN event ID (e.g., "401547679")

    Returns:
        Formatted tvg_id (e.g., "teamarr-event-401547679")
    """
    return f"teamarr-event-{espn_event_id}"


def get_global_lifecycle_settings() -> Dict[str, str]:
    """
    Get global channel lifecycle settings from the settings table.

    Returns:
        Dict with channel_create_timing, channel_delete_timing, default_duplicate_event_handling,
        and reconciliation settings
    """
    try:
        from database import get_connection
        conn = get_connection()
        row = conn.execute("""
            SELECT channel_create_timing, channel_delete_timing,
                   default_duplicate_event_handling,
                   reconcile_on_epg_generation, reconcile_on_startup,
                   auto_fix_orphan_teamarr, auto_fix_orphan_dispatcharr, auto_fix_duplicates
            FROM settings WHERE id = 1
        """).fetchone()
        conn.close()

        if row:
            return {
                'channel_create_timing': row['channel_create_timing'] or 'same_day',
                'channel_delete_timing': row['channel_delete_timing'] or 'same_day',
                'default_duplicate_event_handling': row['default_duplicate_event_handling'] or 'consolidate',
                'reconcile_on_epg_generation': bool(row['reconcile_on_epg_generation']) if row['reconcile_on_epg_generation'] is not None else True,
                'reconcile_on_startup': bool(row['reconcile_on_startup']) if row['reconcile_on_startup'] is not None else True,
                'auto_fix_orphan_teamarr': bool(row['auto_fix_orphan_teamarr']) if row['auto_fix_orphan_teamarr'] is not None else True,
                'auto_fix_orphan_dispatcharr': bool(row['auto_fix_orphan_dispatcharr']) if row['auto_fix_orphan_dispatcharr'] is not None else False,
                'auto_fix_duplicates': bool(row['auto_fix_duplicates']) if row['auto_fix_duplicates'] is not None else False,
            }
    except Exception as e:
        logger.warning(f"Could not get global lifecycle settings: {e}")

    return {
        'channel_create_timing': 'same_day',
        'channel_delete_timing': 'same_day',
        'default_duplicate_event_handling': 'consolidate',
        'reconcile_on_epg_generation': True,
        'reconcile_on_startup': True,
        'auto_fix_orphan_teamarr': True,
        'auto_fix_orphan_dispatcharr': False,
        'auto_fix_duplicates': False,
    }


def normalize_create_timing(timing: str) -> str:
    """Normalize legacy create timing values to new format."""
    mapping = {
        'day_of': 'same_day',
        'week_before': '2_days_before',  # No week option, use 2 days
    }
    return mapping.get(timing, timing)


def normalize_delete_timing(timing: str) -> str:
    """Normalize legacy delete timing values to new format."""
    mapping = {
        'end_of_day': 'same_day',
        'end_of_next_day': 'day_after',
    }
    return mapping.get(timing, timing)


def generate_channel_name(
    event: Dict,
    template: Optional[Dict] = None,
    template_engine = None,
    timezone: str = None,
    exception_keyword: str = None,
    group_info: Dict = None,
    detected_league: str = None,
    detected_sport: str = None
) -> str:
    """
    Generate channel name for an event.

    Uses template's channel_name field if available,
    otherwise falls back to "{away} @ {home}" format.

    Args:
        event: ESPN event data with home_team/away_team
        template: Optional event template with channel_name field
        template_engine: Optional template engine for variable resolution
        timezone: User's timezone for date/time formatting
        exception_keyword: Optional matched exception keyword for sub-consolidation
        group_info: Event EPG group configuration
        detected_league: Per-stream detected league (for multi-sport groups)
        detected_sport: Per-stream detected sport (for multi-sport groups)

    Returns:
        Channel name string
    """
    # If template has channel_name and we have an engine, use it
    if template and template.get('channel_name') and template_engine:
        from epg.event_template_engine import build_event_context

        # Build effective group_info with per-stream overrides for multi-sport
        effective_group_info = dict(group_info) if group_info else {}
        if detected_league:
            effective_group_info['assigned_league'] = detected_league
        if detected_sport:
            effective_group_info['assigned_sport'] = detected_sport

        ctx = build_event_context(event, {}, effective_group_info, timezone, exception_keyword=exception_keyword)
        return template_engine.resolve(template['channel_name'], ctx)

    # Default format: "Away @ Home" (with keyword suffix if present)
    home = event.get('home_team', {})
    away = event.get('away_team', {})

    home_name = home.get('shortDisplayName') or home.get('name', 'Home')
    away_name = away.get('shortDisplayName') or away.get('name', 'Away')

    base_name = f"{away_name} @ {home_name}"

    # Append keyword to default name if present
    if exception_keyword:
        return f"{base_name} ({exception_keyword.title()})"
    return base_name


def should_create_channel(
    event: Dict,
    create_timing: str,
    timezone: str,
    delete_timing: str = None,
    sport: str = None,
    settings: dict = None,
    template: dict = None
) -> Tuple[bool, str]:
    """
    Check if a channel should be created based on event date and timing settings.

    This checks both:
    1. Earliest creation check - channel won't be created before the threshold date
    2. Already-deleted check - channel won't be created if delete time has passed

    The second check prevents the create-then-immediately-delete cycle that occurs
    when a stream still exists but the event has already ended and passed the
    delete threshold.

    Args:
        event: ESPN event data with 'date' field
        create_timing: One of 'stream_available', 'same_day', 'day_before', '2_days_before',
                      '3_days_before', '1_week_before', 'manual'
        timezone: Timezone for date comparison (user's local timezone)
        delete_timing: Optional delete timing to check if we're past the delete threshold
        sport: Sport type for duration calculation (needed for delete time check)
        settings: Optional settings dict with game_duration_{sport} values
        template: Optional template dict with game_duration_mode and game_duration_override

    Returns:
        Tuple of (should_create: bool, reason: str)
    """
    # Normalize legacy values
    create_timing = normalize_create_timing(create_timing)

    # Manual means never auto-create
    if create_timing == 'manual':
        return False, "Manual creation only"

    # stream_available means create immediately when stream exists
    # BUT we still need to check if we're past the delete threshold
    if create_timing == 'stream_available' and not delete_timing:
        return True, "Stream available - immediate creation"

    event_date_str = event.get('date')
    if not event_date_str:
        return False, "No event date"

    try:
        # Parse event date
        if event_date_str.endswith('Z'):
            event_date_str = event_date_str[:-1] + '+00:00'
        event_dt = datetime.fromisoformat(event_date_str)

        # Convert to local timezone for date comparison
        tz = ZoneInfo(timezone)
        event_local = event_dt.astimezone(tz)
        event_date = event_local.date()

        # Get current time in same timezone
        now = datetime.now(tz)
        today = now.date()

        # First, check if we're PAST the delete threshold
        # This prevents creating channels that would immediately be deleted
        if delete_timing and delete_timing not in ('manual', 'stream_removed'):
            delete_time = calculate_delete_time(event, delete_timing, timezone, sport, settings, template)
            if delete_time:
                # Convert delete_time to local timezone for comparison
                delete_time_local = delete_time.astimezone(tz)
                if now >= delete_time_local:
                    return False, f"Past delete threshold ({delete_time_local.strftime('%m/%d %I:%M %p')})"

        # For stream_available with delete_timing check passed, allow creation
        if create_timing == 'stream_available':
            return True, "Stream available - immediate creation"

        # Calculate threshold based on timing (earliest creation date)
        if create_timing == 'same_day' or create_timing == 'day_of':
            threshold_date = event_date
        elif create_timing == 'day_before':
            threshold_date = event_date - timedelta(days=1)
        elif create_timing == '2_days_before':
            threshold_date = event_date - timedelta(days=2)
        elif create_timing == '3_days_before':
            threshold_date = event_date - timedelta(days=3)
        elif create_timing == '1_week_before':
            threshold_date = event_date - timedelta(days=7)
        else:
            # Default to same day
            threshold_date = event_date

        if today >= threshold_date:
            return True, f"Event on {event_date}, threshold {threshold_date}, today {today}"
        else:
            days_until = (threshold_date - today).days
            return False, f"Too early - {days_until} days until creation threshold"

    except Exception as e:
        logger.warning(f"Error checking create timing: {e}")
        return False, f"Error: {e}"


def get_sport_duration_hours(sport: str, settings: dict = None) -> float:
    """
    Get typical duration for a sport in hours.

    Uses settings-configured sport durations when available, falling back
    to hardcoded defaults only when settings are not provided.

    Args:
        sport: Sport name (e.g., 'football', 'basketball')
        settings: Optional settings dict with game_duration_{sport} values

    Returns:
        Duration in hours
    """
    sport_lower = sport.lower() if sport else ''

    # Try to get from settings first
    if settings:
        sport_key = f'game_duration_{sport_lower}'
        if sport_key in settings:
            return float(settings[sport_key])
        # Fall back to global default from settings
        if 'game_duration_default' in settings:
            return float(settings['game_duration_default'])

    # Hardcoded fallbacks (only used when settings not available)
    fallback_durations = {
        'football': 4.0,      # NFL/CFB games ~3-3.5 hours + buffer
        'basketball': 3.0,    # NBA/CBB games ~2-2.5 hours + buffer
        'hockey': 3.0,        # NHL games ~2.5 hours + buffer
        'baseball': 4.0,      # MLB games can go long with extra innings
        'soccer': 2.5,        # 90 min + halftime + stoppage + buffer
    }
    return fallback_durations.get(sport_lower, 3.5)  # Default 3.5 hours


def get_event_duration_hours(sport: str, settings: dict = None, template: dict = None) -> float:
    """
    Get event duration based on template mode and settings.

    Priority:
    1. Template custom override (if mode='custom')
    2. Sport-specific setting from settings (if mode='sport' or default)
    3. Global default from settings
    4. Hardcoded fallbacks

    Args:
        sport: Sport name (e.g., 'football', 'basketball')
        settings: Optional settings dict with game_duration_{sport} values
        template: Optional template dict with game_duration_mode and game_duration_override

    Returns:
        Duration in hours
    """
    # Check template for custom duration
    if template:
        duration_mode = template.get('game_duration_mode', 'sport')
        if duration_mode == 'custom' and template.get('game_duration_override'):
            return float(template['game_duration_override'])
        elif duration_mode == 'default':
            # Use global default from settings
            if settings and 'game_duration_default' in settings:
                return float(settings['game_duration_default'])

    # Use sport-specific duration from settings (default behavior)
    return get_sport_duration_hours(sport, settings)


def calculate_delete_time(
    event: Dict,
    delete_timing: str,
    timezone: str,
    sport: str = None,
    settings: dict = None,
    template: dict = None
) -> Optional[datetime]:
    """
    Calculate when a channel should be deleted based on event and timing setting.

    This is the "latest deletion" time - channel will be deleted by this time
    even if the stream still exists.

    Uses the actual event start time and sport duration to determine if the
    event will cross midnight, then schedules deletion appropriately.

    Args:
        event: ESPN event data with 'date' field
        delete_timing: One of 'stream_removed', 'same_day', 'day_after', '2_days_after',
                      '3_days_after', '1_week_after', 'manual'
        timezone: Timezone for date calculation (user's local timezone)
        sport: Sport type for duration calculation (e.g., 'basketball', 'football')
        settings: Optional settings dict with game_duration_{sport} values
        template: Optional template dict with game_duration_mode and game_duration_override

    Returns:
        Datetime when channel should be deleted (at 23:59 local time), or None for 'manual'/'stream_removed'
    """
    # Normalize legacy values
    delete_timing = normalize_delete_timing(delete_timing)

    if delete_timing in ('manual', 'stream_removed'):
        return None

    event_date_str = event.get('date')
    if not event_date_str:
        return None

    try:
        # Parse event date
        if event_date_str.endswith('Z'):
            event_date_str = event_date_str[:-1] + '+00:00'
        event_dt = datetime.fromisoformat(event_date_str)

        # Convert to local timezone
        tz = ZoneInfo(timezone)
        event_start = event_dt.astimezone(tz)
        event_start_date = event_start.date()

        # Calculate event end time based on template/settings duration
        duration_hours = get_event_duration_hours(sport, settings, template) if sport else 3.5
        event_end = event_start + timedelta(hours=duration_hours)
        event_end_date = event_end.date()

        # Check if event crosses midnight (ends on a different day than it started)
        crosses_midnight = event_end_date > event_start_date

        # Calculate delete date based on when event ENDS
        if delete_timing == 'same_day' or delete_timing == 'end_of_day':
            # Delete at end of the day the event ENDS (not starts)
            delete_date = event_end_date
        elif delete_timing == 'day_after' or delete_timing == 'end_of_next_day':
            # Delete at end of the day AFTER the event ends
            delete_date = event_end_date + timedelta(days=1)
        elif delete_timing == '2_days_after':
            # Delete at end of 2 days after the event ends
            delete_date = event_end_date + timedelta(days=2)
        elif delete_timing == '3_days_after':
            # Delete at end of 3 days after the event ends
            delete_date = event_end_date + timedelta(days=3)
        elif delete_timing == '1_week_after':
            # Delete at end of 1 week after the event ends
            delete_date = event_end_date + timedelta(days=7)
        else:
            return None

        if crosses_midnight:
            logger.debug(
                f"Event starts {event_start.strftime('%m/%d %I:%M %p')}, "
                f"ends ~{event_end.strftime('%m/%d %I:%M %p')} (crosses midnight)"
            )

        # Return as datetime at 23:59:59 on the delete date, converted to UTC
        # This ensures SQLite CURRENT_TIMESTAMP comparisons work correctly
        from datetime import time
        end_of_day = time(23, 59, 59)
        local_delete_time = datetime.combine(delete_date, end_of_day).replace(tzinfo=tz)
        # Convert to UTC for consistent database storage
        return local_delete_time.astimezone(ZoneInfo('UTC'))

    except Exception as e:
        logger.warning(f"Error calculating delete time: {e}")
        return None


class ChannelLifecycleManager:
    """
    Manages channel creation and deletion for event-based EPG.

    Coordinates between:
    - Dispatcharr API (channel CRUD)
    - Local database (managed_channels tracking)
    - Event matching (which streams need channels)

    EPG is injected directly via set-epg API after channel creation.
    """

    def __init__(
        self,
        dispatcharr_url: str,
        dispatcharr_username: str,
        dispatcharr_password: str,
        timezone: str,
        epg_data_id: int = None,
        settings: dict = None
    ):
        """
        Initialize the lifecycle manager.

        Args:
            dispatcharr_url: Dispatcharr base URL
            dispatcharr_username: Dispatcharr username
            dispatcharr_password: Dispatcharr password
            epg_data_id: Teamarr's EPG source ID in Dispatcharr (for direct EPG injection)
            timezone: Default timezone for date calculations
            settings: Full settings dict for sport durations and other config
        """
        from api.dispatcharr_client import ChannelManager

        self.channel_api = ChannelManager(
            dispatcharr_url,
            dispatcharr_username,
            dispatcharr_password
        )
        self.epg_data_id = epg_data_id
        self.timezone = timezone
        self.settings = settings or {}
        # Lock to serialize Dispatcharr channel operations (create/update/delete)
        # Prevents race conditions when multiple groups are processed in parallel
        self._dispatcharr_lock = threading.Lock()

    def delete_managed_channel(
        self,
        channel: Dict,
        reason: str = None
    ) -> Dict[str, Any]:
        """
        SINGLE SOURCE OF TRUTH for deleting a managed channel.

        Handles all aspects of channel deletion:
        1. Delete channel from Dispatcharr
        2. Delete associated logo from Dispatcharr (if present)
        3. Mark channel as deleted in Teamarr database
        4. Log deletion in channel history (V2)

        Args:
            channel: Managed channel dict (must have 'id', 'dispatcharr_channel_id',
                    optionally 'dispatcharr_logo_id', 'channel_name')
            reason: Optional reason for deletion (for logging)

        Returns:
            Dict with:
            - success: bool
            - channel_deleted: bool (Dispatcharr channel removed)
            - logo_deleted: bool (logo removed if present)
            - db_updated: bool (marked deleted in DB)
            - error: str (if failed)
        """
        from database import mark_managed_channel_deleted, log_channel_history, update_managed_channel

        result = {
            'success': False,
            'channel_deleted': False,
            'logo_deleted': False,
            'db_updated': False,
            'error': None
        }

        channel_id = channel.get('id')
        dispatcharr_channel_id = channel.get('dispatcharr_channel_id')
        channel_name = channel.get('channel_name', f'Channel {dispatcharr_channel_id}')
        logo_id = channel.get('dispatcharr_logo_id')

        if not dispatcharr_channel_id:
            result['error'] = 'No dispatcharr_channel_id provided'
            return result

        try:
            # Serialize Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Step 1: Delete channel from Dispatcharr
                delete_result = self.channel_api.delete_channel(dispatcharr_channel_id)

                # Consider success if deleted OR already not found
                if delete_result.get('success') or 'not found' in str(delete_result.get('error', '')).lower():
                    result['channel_deleted'] = True

                    # Step 2: Delete logo if present
                    if logo_id:
                        logo_result = self.channel_api.delete_logo(logo_id)
                        if logo_result.get('status') == 'deleted' or logo_result.get('success'):
                            result['logo_deleted'] = True
                            logger.debug(f"Deleted logo {logo_id} for channel '{channel_name}'")

                    # Step 3: Mark as deleted in database
                    # Pass logo_deleted status: True if deleted, False if failed, None if no logo
                    if channel_id:
                        logo_deleted_status = None  # No logo was present
                        if logo_id:
                            logo_deleted_status = result['logo_deleted']  # True or False based on deletion result

                        if mark_managed_channel_deleted(channel_id, logo_deleted=logo_deleted_status):
                            result['db_updated'] = True

                        # V2: Update delete_reason and log history
                        if reason:
                            update_managed_channel(channel_id, {'delete_reason': reason})

                        log_channel_history(
                            managed_channel_id=channel_id,
                            change_type='deleted',
                            change_source='epg_generation',
                            notes=reason or 'Channel deleted'
                        )

                    result['success'] = True

                    log_msg = f"Deleted channel '{channel_name}'"
                    if reason:
                        log_msg += f" ({reason})"
                    logger.info(log_msg)

                else:
                    result['error'] = delete_result.get('error', 'Unknown error')
                    logger.warning(f"Failed to delete channel '{channel_name}': {result['error']}")

        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Error deleting channel '{channel_name}': {e}")

        return result

    def _update_channel_logo_if_changed(
        self,
        existing: Dict,
        event: Dict,
        stream: Dict,
        group: Dict,
        template: Dict,
        template_engine,
        results: Dict
    ) -> None:
        """
        Check if channel logo needs updating and update if changed.

        Compares the resolved template logo URL with the current logo URL
        in Dispatcharr. If different, uploads new logo and updates the channel.

        Args:
            existing: Existing managed channel record
            event: ESPN event data
            stream: Stream data
            group: Event EPG group
            template: Event template with channel_logo_url
            template_engine: EventTemplateEngine instance
            results: Results dict to append logo_updated entries
        """
        from database import update_managed_channel
        from epg.event_template_engine import build_event_context

        try:
            # Resolve the template logo URL
            logo_ctx = build_event_context(event, stream, group, self.timezone)
            template_logo_url = template.get('channel_logo_url')
            new_logo_url = template_engine.resolve(template_logo_url, logo_ctx) if template_logo_url else None

            channel_name = existing.get('channel_name', 'Unknown')
            dispatcharr_channel_id = existing.get('dispatcharr_channel_id')
            current_logo_id = existing.get('dispatcharr_logo_id')

            # Serialize Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Get current logo URL from Dispatcharr
                current_logo_url = None
                if current_logo_id:
                    current_logo = self.channel_api.get_logo(current_logo_id)
                    if current_logo:
                        current_logo_url = current_logo.get('url')

                # Case 1: Template has no logo but channel has one - delete it
                if not new_logo_url and current_logo_id:
                    # Remove logo from channel
                    update_result = self.channel_api.update_channel(dispatcharr_channel_id, {'logo_id': None})
                    if update_result.get('success'):
                        # Delete the logo from Dispatcharr
                        delete_result = self.channel_api.delete_logo(current_logo_id)
                        if delete_result.get('success'):
                            logger.info(f"Deleted logo for '{channel_name}' (template no longer has logo)")

                        # Update managed_channels record
                        update_managed_channel(existing['id'], {'dispatcharr_logo_id': None, 'logo_url': None})

                        results['logo_updated'].append({
                            'channel_name': channel_name,
                            'channel_id': dispatcharr_channel_id,
                            'old_logo_url': current_logo_url,
                            'new_logo_url': None,
                            'new_logo_id': None,
                            'action': 'deleted'
                        })
                    return

                # Case 2: No logo in template and no logo on channel - nothing to do
                if not new_logo_url:
                    return

                # Case 3: Compare URLs - if same, no update needed
                if current_logo_url == new_logo_url:
                    return

                # Case 4: Upload new logo (or find existing by URL)
                logo_name = f"{channel_name} Logo"
                logo_result = self.channel_api.upload_logo(logo_name, new_logo_url)

                if not logo_result.get('success'):
                    logger.warning(f"Failed to upload logo for '{channel_name}': {logo_result.get('error')}")
                    return

                new_logo_id = logo_result.get('logo_id')

                # Update channel in Dispatcharr with new logo
                update_result = self.channel_api.update_channel(dispatcharr_channel_id, {'logo_id': new_logo_id})

                if not update_result.get('success'):
                    logger.warning(f"Failed to update channel logo for '{channel_name}': {update_result.get('error')}")
                    return

            # Update managed_channels record with new logo_id
            update_managed_channel(existing['id'], {'dispatcharr_logo_id': new_logo_id, 'logo_url': new_logo_url})

            results['logo_updated'].append({
                'channel_name': channel_name,
                'channel_id': dispatcharr_channel_id,
                'old_logo_url': current_logo_url,
                'new_logo_url': new_logo_url,
                'new_logo_id': new_logo_id,
                'action': 'updated'
            })

            logger.info(f"Updated logo for '{channel_name}': {logo_result.get('status')}")

        except Exception as e:
            logger.debug(f"Error updating logo for channel {existing.get('channel_name')}: {e}")

    def _sync_channel_settings(
        self,
        existing: Dict,
        group: Dict,
        results: Dict,
        current_stream: Dict = None,
        event: Dict = None,
        template: Dict = None,
        template_engine = None
    ) -> None:
        """
        Sync channel settings from group/template configuration to Dispatcharr.

        Called for each existing channel during EPG generation to ensure any
        configuration changes are propagated to Dispatcharr.

        SYNCED FIELDS (update this list when adding new fields):
        +---------------------+-------------------+-----------------------------+
        | Source              | Dispatcharr Field | Handling                    |
        +---------------------+-------------------+-----------------------------+
        | template            | name              | Template resolution         |
        | group.channel_start | channel_number    | Range validation/reassign   |
        | group               | channel_group_id  | Simple compare              |
        | group               | stream_profile_id | Simple compare              |
        | current_stream      | streams           | M3U ID lookup               |
        | group               | channel_profile_id| Add/remove via profile API  |
        | template            | logo_id           | Separate method (below)     |
        | espn_event_id       | tvg_id            | Ensures EPG matching works  |
        +---------------------+-------------------+-----------------------------+

        When adding a new synced field:
        1. Add comparison logic in this method
        2. Add to update_data dict (for Dispatcharr channel update)
        3. Update managed_channels DB record if we track it locally
        4. Track changes in results dict for logging/UI feedback
        5. Update the table above

        Args:
            existing: Existing managed channel record from our DB
            group: Event EPG group configuration
            results: Results dict to append updates (name_updated, number_updated, etc.)
            current_stream: Current matched stream from M3U (for stream ID sync)
            event: ESPN event data (for channel name resolution)
            template: Event template (for channel name resolution)
            template_engine: EventTemplateEngine instance (for variable substitution)
        """
        try:
            dispatcharr_channel_id = existing['dispatcharr_channel_id']
            stored_channel_name = existing.get('channel_name', 'Unknown')

            # Serialize Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Get current channel data from Dispatcharr
                current_channel = self.channel_api.get_channel(dispatcharr_channel_id)
                if not current_channel:
                    logger.debug(f"Could not fetch channel {dispatcharr_channel_id} for settings sync")
                    return

            # Build update payload if there are differences
            update_data = {}

            # Check channel name - resolve from template and compare
            if event and template and template_engine:
                # Get exception_keyword from existing channel for proper name resolution
                channel_exception_keyword = existing.get('exception_keyword')

                # For multi-sport groups, use the stored league/sport from when channel was created
                # This ensures template variables resolve correctly even during sync
                stored_league = existing.get('league')
                stored_sport = existing.get('sport')

                new_channel_name = generate_channel_name(
                    event,
                    template=template,
                    template_engine=template_engine,
                    timezone=self.timezone,
                    exception_keyword=channel_exception_keyword,
                    group_info=group,
                    detected_league=stored_league,
                    detected_sport=stored_sport
                )
                current_dispatcharr_name = current_channel.get('name', '')

                if new_channel_name and new_channel_name != current_dispatcharr_name:
                    update_data['name'] = new_channel_name

                    # Also update our managed_channels record
                    from database import update_managed_channel
                    update_managed_channel(existing['id'], {'channel_name': new_channel_name})

                    # Track name change in results
                    if 'name_updated' not in results:
                        results['name_updated'] = []
                    results['name_updated'].append({
                        'channel_id': dispatcharr_channel_id,
                        'old_name': current_dispatcharr_name,
                        'new_name': new_channel_name
                    })
                    logger.info(f"Channel name updated: '{current_dispatcharr_name}' -> '{new_channel_name}'")

            # Check channel number - reassign if outside current group range
            channel_start = group.get('channel_start')
            current_channel_number = existing.get('channel_number')
            current_dispatcharr_number = current_channel.get('channel_number')

            if channel_start and current_channel_number and current_channel_number < channel_start:
                # Channel number is below the new range - need to reassign
                from database import get_next_channel_number, update_managed_channel
                new_channel_number = get_next_channel_number(group['id'])

                if new_channel_number:
                    update_data['channel_number'] = new_channel_number

                    # Update our managed_channels record
                    update_managed_channel(existing['id'], {'channel_number': new_channel_number})

                    # Track in results
                    if 'number_updated' not in results:
                        results['number_updated'] = []
                    results['number_updated'].append({
                        'channel_id': dispatcharr_channel_id,
                        'old_number': current_channel_number,
                        'new_number': new_channel_number
                    })
                    logger.info(
                        f"Channel number reassigned: {current_channel_number} -> {new_channel_number} "
                        f"(group start now {channel_start})"
                    )

            # Check channel_group_id
            group_channel_group_id = group.get('channel_group_id')
            current_channel_group_id = current_channel.get('channel_group_id')
            if group_channel_group_id != current_channel_group_id:
                update_data['channel_group_id'] = group_channel_group_id

            # Check stream_profile_id
            group_stream_profile_id = group.get('stream_profile_id')
            current_stream_profile_id = current_channel.get('stream_profile_id')
            if group_stream_profile_id != current_stream_profile_id:
                update_data['stream_profile_id'] = group_stream_profile_id

            # Check tvg_id - ensure EPG matching is always correct
            espn_event_id = existing.get('espn_event_id')
            if espn_event_id:
                expected_tvg_id = generate_event_tvg_id(espn_event_id)
                current_tvg_id = current_channel.get('tvg_id')
                if expected_tvg_id != current_tvg_id:
                    update_data['tvg_id'] = expected_tvg_id
                    logger.debug(f"Syncing tvg_id: {current_tvg_id} -> {expected_tvg_id}")

            # Check stream assignment - M3U stream IDs can change on refresh
            if current_stream:
                new_stream_id = current_stream.get('id')
                current_streams = current_channel.get('streams', [])

                # Check if the stream is missing or different
                if not current_streams or (len(current_streams) == 1 and current_streams[0] != new_stream_id):
                    update_data['streams'] = [new_stream_id]

                    # Also update our managed_channels record with the new stream ID
                    from database import update_managed_channel
                    update_managed_channel(existing['id'], {'dispatcharr_stream_id': new_stream_id})

            # Check channel_profile_ids (handled separately - profiles maintain channel lists)
            # Both are lists (or empty lists)
            group_profile_ids = set(group.get('channel_profile_ids') or [])
            stored_profile_ids = set(existing.get('channel_profile_ids') or [])

            if group_profile_ids != stored_profile_ids:
                from database import update_managed_channel
                import json

                profiles_to_add = group_profile_ids - stored_profile_ids
                profiles_to_remove = stored_profile_ids - group_profile_ids
                profile_changed = False

                # Serialize Dispatcharr profile operations
                with self._dispatcharr_lock:
                    # Remove from profiles no longer in the list
                    for profile_id in profiles_to_remove:
                        remove_result = self.channel_api.remove_channel_from_profile(
                            profile_id, dispatcharr_channel_id
                        )
                        if remove_result.get('success'):
                            logger.debug(f"Removed channel {dispatcharr_channel_id} from profile {profile_id}")
                            profile_changed = True
                        else:
                            logger.warning(
                                f"Failed to remove channel {dispatcharr_channel_id} from profile {profile_id}: "
                                f"{remove_result.get('error')}"
                            )

                    # Add to new profiles
                    for profile_id in profiles_to_add:
                        add_result = self.channel_api.add_channel_to_profile(
                            profile_id, dispatcharr_channel_id
                        )
                        if add_result.get('success'):
                            logger.debug(f"Added channel {dispatcharr_channel_id} to profile {profile_id}")
                            profile_changed = True
                        else:
                            logger.warning(
                                f"Failed to add channel {dispatcharr_channel_id} to profile {profile_id}: "
                                f"{add_result.get('error')}"
                            )

                # Update our record with the new profile IDs
                if profile_changed:
                    new_profile_ids_json = json.dumps(list(group_profile_ids)) if group_profile_ids else None
                    update_managed_channel(existing['id'], {'channel_profile_ids': new_profile_ids_json})

                    # Track in results
                    if 'profile_updated' not in results:
                        results['profile_updated'] = []
                    results['profile_updated'].append({
                        'channel_name': stored_channel_name,
                        'channel_id': dispatcharr_channel_id,
                        'profiles_added': list(profiles_to_add),
                        'profiles_removed': list(profiles_to_remove)
                    })

            if not update_data:
                return  # No channel property changes needed

            # Update channel in Dispatcharr (serialized)
            with self._dispatcharr_lock:
                update_result = self.channel_api.update_channel(dispatcharr_channel_id, update_data)

            if update_result.get('success'):
                changes = ', '.join(f"{k}={v}" for k, v in update_data.items())
                logger.info(f"Synced settings for '{stored_channel_name}': {changes}")

                # Track in results
                if 'settings_updated' not in results:
                    results['settings_updated'] = []
                results['settings_updated'].append({
                    'channel_name': stored_channel_name,
                    'channel_id': dispatcharr_channel_id,
                    'changes': update_data
                })
            else:
                logger.warning(
                    f"Failed to sync settings for '{stored_channel_name}': "
                    f"{update_result.get('error')}"
                )

        except Exception as e:
            logger.debug(f"Error syncing settings for channel {existing.get('channel_name')}: {e}")

    def process_matched_streams(
        self,
        matched_streams: List[Dict],
        group: Dict,
        template: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Process matched streams and create/update channels as needed.

        Channel Lifecycle V2 behavior:
        - Checks duplicate_event_handling mode: 'ignore', 'consolidate', 'separate'
        - For 'consolidate': adds additional streams to existing channels
        - For 'separate': creates separate channels per stream
        - For 'ignore': skips duplicate streams

        Args:
            matched_streams: List of dicts with 'stream', 'teams', 'event' keys
            group: Event EPG group configuration
            template: Optional event template

        Returns:
            Dict with:
            - created: List of created channel records
            - skipped: List of skipped streams (with reasons)
            - errors: List of error messages
            - existing: List of already-existing channels
            - streams_added: List of streams added to existing channels (consolidate mode)
        """
        from database import (
            get_managed_channel_by_event,
            get_next_channel_number,
            create_managed_channel,
            add_stream_to_channel,
            remove_stream_from_channel,
            find_existing_channel,
            stream_exists_on_channel,
            log_channel_history
        )
        from epg.event_template_engine import EventTemplateEngine
        from database import get_consolidation_exception_keywords
        from utils.keyword_matcher import check_exception_keyword

        # Create template engine for channel name resolution
        template_engine = EventTemplateEngine()

        # Load global exception keywords
        exception_keywords = get_consolidation_exception_keywords()

        results = {
            'created': [],
            'skipped': [],
            'errors': [],
            'existing': [],
            'logo_updated': [],
            'streams_added': []  # V2: tracks consolidate mode additions
        }

        # Get lifecycle settings - always use global settings (no per-group overrides)
        global_settings = get_global_lifecycle_settings()
        channel_start = group.get('channel_start')
        channel_group_id = group.get('channel_group_id')  # Dispatcharr channel group
        stream_profile_id = group.get('stream_profile_id')  # Dispatcharr stream profile
        channel_profile_ids = group.get('channel_profile_ids') or []  # Dispatcharr channel profiles (list)
        create_timing = global_settings['channel_create_timing']
        delete_timing = global_settings['channel_delete_timing']
        sport = group.get('assigned_sport')
        league = group.get('assigned_league')

        # V2: Get duplicate event handling mode
        # Per-group setting takes precedence, otherwise use system default
        duplicate_mode = group.get('duplicate_event_handling')
        if not duplicate_mode:
            duplicate_mode = global_settings.get('default_duplicate_event_handling', 'consolidate')

        # Auto-assign channel_start if not set (using get_next_channel_number triggers auto-assign)
        if not channel_start:
            # Try to auto-assign a channel range
            test_channel = get_next_channel_number(group['id'], auto_assign=True)
            if test_channel:
                channel_start = ((test_channel - 1) // 100) * 100 + 1  # Get the x01 start
                logger.info(f"Auto-assigned channel_start {channel_start} to group {group['id']}")
            else:
                logger.warning(f"Group {group['id']} could not auto-assign channel range (max 9999 exceeded?)")
                results['skipped'] = [
                    {'stream': m['stream']['name'], 'reason': 'Could not auto-assign channel range (max 9999 exceeded)'}
                    for m in matched_streams
                ]
                return results

        for matched in matched_streams:
            stream = matched['stream']
            event = matched['event']
            teams = matched.get('teams', {})

            espn_event_id = event.get('id')
            if not espn_event_id:
                results['errors'].append({
                    'stream': stream['name'],
                    'error': 'No ESPN event ID'
                })
                continue

            # Check for exception keyword match (only relevant when base mode is 'consolidate')
            matched_keyword = None
            effective_mode = duplicate_mode  # Default to group's duplicate mode

            if exception_keywords and duplicate_mode == 'consolidate':
                keyword, exception_behavior = check_exception_keyword(stream.get('name', ''), exception_keywords)
                if keyword:
                    matched_keyword = keyword
                    effective_mode = exception_behavior
                    logger.debug(
                        f"Stream '{stream['name']}' matched exception keyword '{keyword}' "
                        f"→ behavior: {exception_behavior}"
                    )

                    # Remove stream from any non-keyword channel for this event
                    # (handles case where stream was added before keyword was configured)
                    non_keyword_channel = find_existing_channel(
                        group_id=group['id'],
                        event_id=espn_event_id,
                        exception_keyword=None,
                        mode='consolidate'
                    )
                    if non_keyword_channel and stream_exists_on_channel(non_keyword_channel['id'], stream['id']):
                        try:
                            remove_stream_from_channel(non_keyword_channel['id'], stream['id'])
                            # Also remove from Dispatcharr
                            with self._dispatcharr_lock:
                                current_channel = self.channel_api.get_channel(non_keyword_channel['dispatcharr_channel_id'])
                                if current_channel:
                                    current_streams = current_channel.get('streams', [])
                                    if stream['id'] in current_streams:
                                        current_streams.remove(stream['id'])
                                        self.channel_api.update_channel(
                                            non_keyword_channel['dispatcharr_channel_id'],
                                            {'streams': current_streams}
                                        )
                            logger.info(
                                f"Removed stream '{stream['name']}' from non-keyword channel "
                                f"'{non_keyword_channel['channel_name']}' (now using keyword '{keyword}')"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to remove stream from non-keyword channel: {e}")

            # V2: Check for existing channel based on duplicate handling mode
            # For keyword-based consolidation, we need a different lookup strategy
            if matched_keyword and effective_mode == 'consolidate':
                # Find existing channel for this event+keyword combination
                existing = find_existing_channel(
                    group_id=group['id'],
                    event_id=espn_event_id,
                    exception_keyword=matched_keyword,
                    mode='consolidate'
                )
            else:
                existing = find_existing_channel(
                    group_id=group['id'],
                    event_id=espn_event_id,
                    stream_id=stream.get('id') if effective_mode == 'separate' else None,
                    mode=effective_mode
                )

            if existing:
                # Handle based on effective mode (may differ from group default due to keyword)
                if effective_mode == 'ignore':
                    # Skip - don't add stream
                    results['existing'].append({
                        'stream': stream['name'],
                        'channel_id': existing['dispatcharr_channel_id'],
                        'channel_number': existing['channel_number'],
                        'action': 'ignored'
                    })

                elif effective_mode == 'consolidate':
                    # V2: Add stream to existing channel if not already present
                    if not stream_exists_on_channel(existing['id'], stream['id']):
                        try:
                            # Add stream to channel in Dispatcharr
                            with self._dispatcharr_lock:
                                current_channel = self.channel_api.get_channel(existing['dispatcharr_channel_id'])
                                if current_channel:
                                    current_streams = current_channel.get('streams', [])
                                    if stream['id'] not in current_streams:
                                        new_streams = current_streams + [stream['id']]
                                        update_result = self.channel_api.update_channel(
                                            existing['dispatcharr_channel_id'],
                                            {'streams': new_streams}
                                        )
                                        if update_result.get('success'):
                                            # Track in managed_channel_streams
                                            add_stream_to_channel(
                                                managed_channel_id=existing['id'],
                                                dispatcharr_stream_id=stream['id'],
                                                source_group_id=group['id'],
                                                stream_name=stream.get('name'),
                                                source_group_type='parent',
                                                m3u_account_id=stream.get('m3u_account_id'),
                                                m3u_account_name=stream.get('m3u_account_name'),
                                                exception_keyword=matched_keyword
                                            )
                                            # Log history
                                            keyword_note = f" [keyword: {matched_keyword}]" if matched_keyword else ""
                                            log_channel_history(
                                                managed_channel_id=existing['id'],
                                                change_type='stream_added',
                                                change_source='epg_generation',
                                                notes=f"Added stream '{stream.get('name')}' (consolidate mode){keyword_note}"
                                            )
                                            results['streams_added'].append({
                                                'stream': stream['name'],
                                                'channel_id': existing['dispatcharr_channel_id'],
                                                'channel_name': existing['channel_name']
                                            })
                                            logger.debug(
                                                f"Added stream '{stream['name']}' to channel "
                                                f"'{existing['channel_name']}' (consolidate mode)"
                                            )
                        except Exception as e:
                            logger.warning(f"Failed to add stream to channel: {e}")

                    results['existing'].append({
                        'stream': stream['name'],
                        'channel_id': existing['dispatcharr_channel_id'],
                        'channel_number': existing['channel_number'],
                        'action': 'consolidated'
                    })

                else:  # duplicate_mode == 'separate' - channel found for this stream
                    results['existing'].append({
                        'stream': stream['name'],
                        'channel_id': existing['dispatcharr_channel_id'],
                        'channel_number': existing['channel_number'],
                        'action': 'separate_exists'
                    })

                # Sync channel settings (name, group, profile, stream) if they've changed
                self._sync_channel_settings(
                    existing, group, results,
                    current_stream=stream,
                    event=event,
                    template=template,
                    template_engine=template_engine
                )

                # Check if logo needs updating for existing channel
                # Call if template has logo OR channel has logo (to handle deletion)
                if template and (template.get('channel_logo_url') or existing.get('dispatcharr_logo_id')):
                    self._update_channel_logo_if_changed(
                        existing, event, stream, group, template, template_engine, results
                    )

                continue

            # Check if we should create channel based on timing
            # Also pass delete_timing to prevent creating channels that would immediately be deleted
            should_create, reason = should_create_channel(
                event, create_timing, self.timezone,
                delete_timing=delete_timing,
                sport=sport,
                settings=self.settings,
                template=template
            )
            if not should_create:
                results['skipped'].append({
                    'stream': stream['name'],
                    'reason': reason
                })
                continue

            # Get next channel number
            channel_number = get_next_channel_number(group['id'])
            if not channel_number:
                results['errors'].append({
                    'stream': stream['name'],
                    'error': 'Could not allocate channel number'
                })
                continue

            # Get detected league/sport for multi-sport groups
            # detected_league is stored both in teams dict and at top level of matched
            stream_detected_league = matched.get('detected_league') or teams.get('detected_league')
            stream_detected_sport = None
            if stream_detected_league:
                from epg.league_detector import LEAGUE_TO_SPORT
                stream_detected_sport = LEAGUE_TO_SPORT.get(stream_detected_league)

            # Generate channel name using template
            channel_name = generate_channel_name(
                event,
                template=template,
                template_engine=template_engine,
                timezone=self.timezone,
                exception_keyword=matched_keyword,
                group_info=group,
                detected_league=stream_detected_league,
                detected_sport=stream_detected_sport
            )

            # Calculate scheduled delete time (uses template duration if custom)
            # Use detected sport for multi-sport groups, otherwise group's assigned sport
            effective_sport = stream_detected_sport or sport
            effective_league = stream_detected_league or league
            delete_at = calculate_delete_time(event, delete_timing, self.timezone, effective_sport, self.settings, template)

            # Generate tvg_id for channel-EPG association
            # This must match the channel id in the generated XMLTV
            tvg_id = generate_event_tvg_id(espn_event_id)

            # Serialize all Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Generate and upload channel logo if template has channel_logo_url
                logo_id = None
                if template and template.get('channel_logo_url'):
                    from epg.event_template_engine import build_event_context

                    # Build effective group_info with per-stream overrides for multi-sport
                    effective_group_info = dict(group) if group else {}
                    if stream_detected_league:
                        effective_group_info['assigned_league'] = stream_detected_league
                    if stream_detected_sport:
                        effective_group_info['assigned_sport'] = stream_detected_sport

                    logo_ctx = build_event_context(event, stream, effective_group_info, self.timezone)
                    logo_url = template_engine.resolve(template['channel_logo_url'], logo_ctx)

                    if logo_url:
                        logo_name = f"{channel_name} Logo"
                        logo_result = self.channel_api.upload_logo(logo_name, logo_url)
                        if logo_result.get('success'):
                            logo_id = logo_result.get('logo_id')
                            logger.debug(f"Logo for '{channel_name}': {logo_result.get('status')}")
                        else:
                            logger.warning(f"Failed to upload logo for '{channel_name}': {logo_result.get('error')}")

                # Create channel in Dispatcharr with tvg_id
                create_result = self.channel_api.create_channel(
                    name=channel_name,
                    channel_number=channel_number,
                    stream_ids=[stream['id']],
                    tvg_id=tvg_id,
                    channel_group_id=channel_group_id,
                    logo_id=logo_id,
                    stream_profile_id=stream_profile_id
                )

                if not create_result.get('success'):
                    results['errors'].append({
                        'stream': stream['name'],
                        'error': create_result.get('error', 'Unknown error')
                    })
                    continue

                dispatcharr_channel = create_result['channel']
                dispatcharr_channel_id = dispatcharr_channel['id']
                dispatcharr_uuid = dispatcharr_channel.get('uuid')  # Immutable identifier

                # Add to channel profiles if configured (supports multiple)
                added_to_profiles = []
                for profile_id in channel_profile_ids:
                    profile_result = self.channel_api.add_channel_to_profile(
                        profile_id, dispatcharr_channel_id
                    )
                    if not profile_result.get('success'):
                        logger.warning(
                            f"Failed to add channel {dispatcharr_channel_id} to profile {profile_id}: "
                            f"{profile_result.get('error')}"
                        )
                    else:
                        logger.debug(f"Added channel {dispatcharr_channel_id} to profile {profile_id}")
                        added_to_profiles.append(profile_id)

            # Note: EPG association happens AFTER EPG refresh in Dispatcharr
            # See associate_epg_with_channels() method

            # Track in database with V2 extended fields
            try:
                home_team_obj = event.get('home_team', {})
                away_team_obj = event.get('away_team', {})

                home_team = home_team_obj.get('name', '')
                away_team = away_team_obj.get('name', '')
                # ESPN client uses 'abbrev', not 'abbreviation'
                home_team_abbrev = home_team_obj.get('abbrev', '') or home_team_obj.get('abbreviation', '')
                away_team_abbrev = away_team_obj.get('abbrev', '') or away_team_obj.get('abbreviation', '')
                home_team_logo = home_team_obj.get('logo', '')
                away_team_logo = away_team_obj.get('logo', '')

                # Store full UTC datetime (convert to user TZ in display)
                event_date = event.get('date', '') or None

                # Build event name
                event_name = f"{away_team_abbrev or away_team} @ {home_team_abbrev or home_team}"

                # Get venue and broadcast info if available
                # ESPN client returns venue.name (not fullName) and broadcasts as a list
                venue_obj = event.get('venue', {})
                venue = venue_obj.get('name', '') or venue_obj.get('fullName', '') if venue_obj else ''
                broadcasts = event.get('broadcasts', [])
                broadcast = broadcasts[0] if broadcasts else ''

                # Get logo URL from template if present
                logo_url_source = None
                if template and template.get('channel_logo_url'):
                    from epg.event_template_engine import build_event_context

                    # Build effective group_info with per-stream overrides for multi-sport
                    logo_effective_group = dict(group) if group else {}
                    if stream_detected_league:
                        logo_effective_group['assigned_league'] = stream_detected_league
                    if stream_detected_sport:
                        logo_effective_group['assigned_sport'] = stream_detected_sport

                    logo_ctx = build_event_context(event, stream, logo_effective_group, self.timezone)
                    logo_url_source = template_engine.resolve(template['channel_logo_url'], logo_ctx)

                managed_id = create_managed_channel(
                    event_epg_group_id=group['id'],
                    dispatcharr_channel_id=dispatcharr_channel_id,
                    dispatcharr_stream_id=stream['id'],
                    channel_number=channel_number,
                    channel_name=channel_name,
                    tvg_id=tvg_id,
                    espn_event_id=espn_event_id,
                    event_date=event_date,
                    home_team=home_team,
                    away_team=away_team,
                    scheduled_delete_at=delete_at.isoformat() if delete_at else None,
                    dispatcharr_logo_id=logo_id,
                    channel_profile_ids=added_to_profiles if added_to_profiles else None,
                    dispatcharr_uuid=dispatcharr_uuid,
                    # V2 fields
                    primary_stream_id=stream['id'] if effective_mode == 'separate' else None,
                    channel_group_id=channel_group_id,
                    stream_profile_id=stream_profile_id,
                    logo_url=logo_url_source,
                    home_team_abbrev=home_team_abbrev,
                    home_team_logo=home_team_logo,
                    away_team_abbrev=away_team_abbrev,
                    away_team_logo=away_team_logo,
                    event_name=event_name,
                    league=effective_league,  # Use detected league for multi-sport
                    sport=effective_sport,    # Use detected sport for multi-sport
                    venue=venue,
                    broadcast=broadcast,
                    sync_status='created',
                    exception_keyword=matched_keyword  # Store keyword for keyword-based consolidation
                )

                # V2: Also track stream in managed_channel_streams
                add_stream_to_channel(
                    managed_channel_id=managed_id,
                    dispatcharr_stream_id=stream['id'],
                    source_group_id=group['id'],
                    stream_name=stream.get('name'),
                    source_group_type='parent',
                    priority=0,  # Primary stream
                    m3u_account_id=stream.get('m3u_account_id'),
                    m3u_account_name=stream.get('m3u_account_name'),
                    exception_keyword=matched_keyword
                )

                # V2: Log channel creation in history
                creation_notes = f"Channel created for event {event_name}"
                if matched_keyword:
                    creation_notes += f" (keyword: {matched_keyword}, mode: {effective_mode})"
                log_channel_history(
                    managed_channel_id=managed_id,
                    change_type='created',
                    change_source='epg_generation',
                    notes=creation_notes
                )

                results['created'].append({
                    'stream': stream['name'],
                    'channel_id': dispatcharr_channel_id,
                    'channel_number': channel_number,
                    'channel_name': channel_name,
                    'managed_id': managed_id,
                    'logo_id': logo_id,
                    'scheduled_delete_at': delete_at.isoformat() if delete_at else None,
                    'exception_keyword': matched_keyword,
                    'duplicate_mode': effective_mode
                })

                log_msg = f"Created channel {channel_number} '{channel_name}' for stream '{stream['name']}'"
                if matched_keyword:
                    log_msg += f" [keyword: {matched_keyword}]"
                logger.info(log_msg)

            except Exception as e:
                # Channel was created but tracking failed - try to delete it
                logger.error(f"Failed to track channel {dispatcharr_channel_id}: {e}")
                with self._dispatcharr_lock:
                    self.channel_api.delete_channel(dispatcharr_channel_id)
                results['errors'].append({
                    'stream': stream['name'],
                    'error': f'Database error: {e}'
                })

        return results

    def process_child_group_streams(
        self,
        child_group: Dict,
        matched_streams: List[Dict]
    ) -> Dict[str, Any]:
        """
        Process streams from a child group - add them to parent's channels.

        Child groups don't create new channels. Instead, they add their matched
        streams to the parent group's existing channels for the same events.

        This method handles exception keyword matching so that child streams
        go to the correct sub-consolidated channel (if applicable).

        Args:
            child_group: The child event EPG group configuration
            matched_streams: List of matched streams with events

        Returns:
            Dict with streams_added, skipped, and errors
        """
        from database import (
            find_parent_channel_for_event,
            add_stream_to_channel,
            stream_exists_on_channel,
            log_channel_history,
            get_consolidation_exception_keywords
        )
        from utils.keyword_matcher import check_exception_keyword

        results = {
            'streams_added': [],
            'skipped': [],
            'errors': []
        }

        parent_group_id = child_group.get('parent_group_id')
        if not parent_group_id:
            logger.error(f"Child group {child_group['id']} has no parent_group_id")
            return results

        # Load global exception keywords (same as parent uses)
        exception_keywords = get_consolidation_exception_keywords()

        for matched in matched_streams:
            stream = matched['stream']
            event = matched['event']
            event_id = event.get('id')

            if not event_id:
                results['errors'].append({
                    'stream': stream['name'],
                    'error': 'No ESPN event ID'
                })
                continue

            # Check for exception keyword match (child should route to same channel as parent would)
            matched_keyword = None
            if exception_keywords:
                keyword, exception_behavior = check_exception_keyword(stream.get('name', ''), exception_keywords)
                if keyword:
                    # For 'ignore' behavior, skip this stream
                    if exception_behavior == 'ignore':
                        results['skipped'].append({
                            'stream': stream['name'],
                            'reason': f"exception keyword '{keyword}' set to ignore"
                        })
                        continue
                    matched_keyword = keyword
                    logger.debug(
                        f"Child stream '{stream['name']}' matched exception keyword '{keyword}'"
                    )

            # Find parent's channel for this event (with matching keyword if applicable)
            parent_channel = find_parent_channel_for_event(parent_group_id, event_id, matched_keyword)

            if not parent_channel:
                # If keyword matched but no keyword channel exists, try main channel
                if matched_keyword:
                    parent_channel = find_parent_channel_for_event(parent_group_id, event_id, None)

                if not parent_channel:
                    logger.debug(
                        f"No parent channel for event {event_id} "
                        f"(keyword: {matched_keyword}) - skipping stream '{stream['name']}'"
                    )
                    results['skipped'].append({
                        'stream': stream['name'],
                        'reason': 'no parent channel for event'
                    })
                    continue

            # Check if stream already attached
            if stream_exists_on_channel(parent_channel['id'], stream['id']):
                continue

            # Add stream to parent channel in Dispatcharr
            try:
                current_channel = self.channel_api.get_channel(parent_channel['dispatcharr_channel_id'])
                if current_channel:
                    current_streams = current_channel.get('streams', [])
                    if stream['id'] not in current_streams:
                        new_streams = current_streams + [stream['id']]
                        with self._dispatcharr_lock:
                            update_result = self.channel_api.update_channel(
                                parent_channel['dispatcharr_channel_id'],
                                {'streams': new_streams}
                            )
                        if update_result.get('success'):
                            # Track in database
                            add_stream_to_channel(
                                managed_channel_id=parent_channel['id'],
                                dispatcharr_stream_id=stream['id'],
                                source_group_id=child_group['id'],
                                stream_name=stream.get('name'),
                                source_group_type='child',
                                m3u_account_id=stream.get('m3u_account_id'),
                                m3u_account_name=stream.get('m3u_account_name')
                            )
                            log_channel_history(
                                managed_channel_id=parent_channel['id'],
                                change_type='stream_added',
                                change_source='epg_generation',
                                notes=f"Added stream '{stream.get('name')}' from child group {child_group.get('group_name')}"
                            )
                            results['streams_added'].append({
                                'stream': stream['name'],
                                'channel': parent_channel['channel_name'],
                                'keyword': matched_keyword
                            })
                            logger.debug(
                                f"Added child stream '{stream['name']}' to parent channel "
                                f"'{parent_channel['channel_name']}' (keyword: {matched_keyword})"
                            )
                        else:
                            results['errors'].append({
                                'stream': stream['name'],
                                'error': update_result.get('error', 'Failed to update channel')
                            })
            except Exception as e:
                logger.warning(f"Failed to add child stream to parent channel: {e}")
                results['errors'].append({
                    'stream': stream['name'],
                    'error': str(e)
                })

        if results['streams_added']:
            logger.info(
                f"Child group '{child_group.get('group_name')}': "
                f"added {len(results['streams_added'])} streams to parent channels"
            )

        return results

    def enforce_stream_keyword_placement(self) -> Dict[str, Any]:
        """
        Enforce correct stream placement based on exception keywords.

        Runs once per EPG generation to fix streams that are on the wrong channel:
        - Streams with keyword match should be on keyword channel, not main channel
        - Streams without keyword match should be on main channel, not keyword channel

        This handles cases where:
        - Keywords were added after streams were already placed
        - Keywords were removed and streams need to move back to main
        - Stream names changed and now match/don't match keywords

        Returns:
            Dict with 'moved' count and 'errors' list
        """
        from database import (
            get_consolidation_exception_keywords,
            get_all_managed_channel_streams,
            find_existing_channel,
            remove_stream_from_channel,
            add_stream_to_channel,
            stream_exists_on_channel,
            log_channel_history
        )
        from utils.keyword_matcher import check_exception_keyword

        results = {
            'moved': 0,
            'errors': []
        }

        exception_keywords = get_consolidation_exception_keywords()
        if not exception_keywords:
            return results  # No keywords configured, nothing to enforce

        # Get all streams across all channels
        all_streams = get_all_managed_channel_streams()

        for stream_record in all_streams:
            stream_name = stream_record.get('stream_name', '')
            if not stream_name:
                continue

            channel_id = stream_record['managed_channel_id']
            channel_event_id = stream_record.get('espn_event_id')
            channel_group_id = stream_record.get('event_epg_group_id')
            channel_keyword = stream_record.get('channel_exception_keyword')  # Current channel's keyword
            stream_id = stream_record['dispatcharr_stream_id']

            if not channel_event_id or not channel_group_id:
                continue

            # Check what keyword this stream SHOULD have
            matched_keyword, behavior = check_exception_keyword(stream_name, exception_keywords)

            # Skip if behavior is 'ignore' - stream shouldn't be anywhere
            if matched_keyword and behavior == 'ignore':
                continue

            # Normalize for comparison (both None or both have value)
            current_keyword = channel_keyword if channel_keyword else None
            target_keyword = matched_keyword if matched_keyword else None

            # If stream is on correct channel, skip
            if current_keyword == target_keyword:
                continue

            # Stream is on wrong channel - need to move it
            # Find the correct target channel
            target_channel = find_existing_channel(
                group_id=channel_group_id,
                event_id=channel_event_id,
                exception_keyword=target_keyword,
                mode='consolidate'
            )

            if not target_channel:
                # Target channel doesn't exist - can't move
                # This is OK - the stream will stay where it is
                logger.debug(
                    f"No target channel for stream '{stream_name}' "
                    f"(keyword: {target_keyword}) - leaving in place"
                )
                continue

            if target_channel['id'] == channel_id:
                # Already on correct channel (shouldn't happen, but safety check)
                continue

            # Check if already on target channel
            if stream_exists_on_channel(target_channel['id'], stream_id):
                # Already on target - just remove from wrong channel
                pass
            else:
                # Add to target channel in Dispatcharr
                try:
                    with self._dispatcharr_lock:
                        target_dispatcharr = self.channel_api.get_channel(target_channel['dispatcharr_channel_id'])
                        if target_dispatcharr:
                            target_streams = target_dispatcharr.get('streams', [])
                            if stream_id not in target_streams:
                                target_streams.append(stream_id)
                                self.channel_api.update_channel(
                                    target_channel['dispatcharr_channel_id'],
                                    {'streams': target_streams}
                                )

                    # Add to target in DB
                    add_stream_to_channel(
                        managed_channel_id=target_channel['id'],
                        dispatcharr_stream_id=stream_id,
                        source_group_id=stream_record.get('source_group_id', channel_group_id),
                        stream_name=stream_name,
                        source_group_type=stream_record.get('source_group_type', 'parent'),
                        m3u_account_id=stream_record.get('m3u_account_id'),
                        m3u_account_name=stream_record.get('m3u_account_name')
                    )
                    log_channel_history(
                        managed_channel_id=target_channel['id'],
                        change_type='stream_added',
                        change_source='keyword_enforcement',
                        notes=f"Moved stream '{stream_name}' from keyword '{current_keyword or 'main'}'"
                    )
                except Exception as e:
                    logger.warning(f"Failed to add stream to target channel: {e}")
                    results['errors'].append({
                        'stream': stream_name,
                        'error': f'Failed to add to target: {e}'
                    })
                    continue

            # Remove from wrong channel in Dispatcharr
            try:
                with self._dispatcharr_lock:
                    current_dispatcharr = self.channel_api.get_channel(stream_record['dispatcharr_channel_id'])
                    if current_dispatcharr:
                        current_streams = current_dispatcharr.get('streams', [])
                        if stream_id in current_streams:
                            current_streams.remove(stream_id)
                            self.channel_api.update_channel(
                                stream_record['dispatcharr_channel_id'],
                                {'streams': current_streams}
                            )

                # Remove from wrong channel in DB
                remove_stream_from_channel(channel_id, stream_id)

                log_channel_history(
                    managed_channel_id=channel_id,
                    change_type='stream_removed',
                    change_source='keyword_enforcement',
                    notes=f"Moved stream '{stream_name}' to keyword channel '{target_keyword or 'main'}'"
                )

                results['moved'] += 1
                logger.info(
                    f"Moved stream '{stream_name}' from "
                    f"'{stream_record.get('channel_name')}' to '{target_channel['channel_name']}' "
                    f"(keyword: {current_keyword} → {target_keyword})"
                )
            except Exception as e:
                logger.warning(f"Failed to remove stream from wrong channel: {e}")
                results['errors'].append({
                    'stream': stream_name,
                    'error': f'Failed to remove from source: {e}'
                })

        if results['moved'] > 0:
            logger.info(f"🔄 Keyword enforcement: moved {results['moved']} stream(s) to correct channels")

        return results

    def enforce_keyword_channel_ordering(self) -> Dict[str, Any]:
        """
        Ensure keyword (sub-consolidated) channels come AFTER the main channel for the same event.

        For each event with multiple channels (main + keyword channels), ensures:
        - Main channel (no exception_keyword) has the lowest channel number
        - Keyword channels have higher channel numbers

        Returns:
            Dict with 'reordered' count
        """
        from database import get_channels_needing_reorder, update_managed_channel

        results = {
            'reordered': 0,
            'errors': []
        }

        # Get channels grouped by event where keyword channel has lower number than main
        channels_to_fix = get_channels_needing_reorder()

        for fix in channels_to_fix:
            main_channel = fix['main_channel']
            keyword_channel = fix['keyword_channel']

            # Swap channel numbers
            main_number = main_channel['channel_number']
            keyword_number = keyword_channel['channel_number']

            try:
                # Update Dispatcharr
                with self._dispatcharr_lock:
                    # Set main channel to keyword's (lower) number
                    self.channel_api.update_channel(
                        main_channel['dispatcharr_channel_id'],
                        {'channel_number': keyword_number}
                    )
                    # Set keyword channel to main's (higher) number
                    self.channel_api.update_channel(
                        keyword_channel['dispatcharr_channel_id'],
                        {'channel_number': main_number}
                    )

                # Update DB
                update_managed_channel(main_channel['id'], {'channel_number': keyword_number})
                update_managed_channel(keyword_channel['id'], {'channel_number': main_number})

                # Log history for both channels
                from database import log_channel_history
                log_channel_history(
                    managed_channel_id=main_channel['id'],
                    change_type='number_swapped',
                    change_source='keyword_ordering',
                    field_name='channel_number',
                    old_value=str(main_number),
                    new_value=str(keyword_number),
                    notes=f"Swapped with keyword channel to maintain main-first ordering"
                )
                log_channel_history(
                    managed_channel_id=keyword_channel['id'],
                    change_type='number_swapped',
                    change_source='keyword_ordering',
                    field_name='channel_number',
                    old_value=str(keyword_number),
                    new_value=str(main_number),
                    notes=f"Swapped with main channel to maintain main-first ordering"
                )

                results['reordered'] += 1
                logger.info(
                    f"Reordered channels for event {main_channel['espn_event_id']}: "
                    f"main #{keyword_number} ↔ keyword #{main_number}"
                )
            except Exception as e:
                logger.warning(f"Failed to reorder channels: {e}")
                results['errors'].append({
                    'event_id': main_channel['espn_event_id'],
                    'error': str(e)
                })

        if results['reordered'] > 0:
            logger.info(f"🔢 Channel ordering: reordered {results['reordered']} keyword channel(s)")

        return results

    def cleanup_deleted_streams(
        self,
        group: Dict,
        current_stream_ids: List[int]
    ) -> Dict[str, Any]:
        """
        Clean up channels for streams that no longer exist.

        ALWAYS runs regardless of delete_timing - if a stream no longer exists,
        the channel should be deleted immediately. This ensures channels are
        cleaned up at the EARLIEST of:
        - Stream removed from M3U provider
        - Scheduled delete time (end of day for 'same_day', etc.)

        Args:
            group: Event EPG group configuration
            current_stream_ids: List of current stream IDs from Dispatcharr

        Returns:
            Dict with deleted and error counts
        """
        from database import get_managed_channels_for_group, get_channel_streams

        results = {
            'deleted': [],
            'errors': []
        }

        # Get all active managed channels for this group
        managed_channels = get_managed_channels_for_group(group['id'])
        current_ids_set = set(current_stream_ids)

        for channel in managed_channels:
            # V2: Check all streams on the channel, not just primary
            channel_streams = get_channel_streams(channel['id'])
            if channel_streams:
                # Check if ALL streams are gone (channel should be deleted)
                # vs just SOME streams gone (remove those streams but keep channel)
                valid_streams = [s for s in channel_streams if s['dispatcharr_stream_id'] in current_ids_set]
                missing_streams = [s for s in channel_streams if s['dispatcharr_stream_id'] not in current_ids_set]

                if not valid_streams:
                    # All streams are gone - delete channel
                    delete_result = self.delete_managed_channel(channel, reason='all streams removed')

                    if delete_result.get('success'):
                        results['deleted'].append({
                            'channel_id': channel['dispatcharr_channel_id'],
                            'channel_number': channel['channel_number'],
                            'channel_name': channel['channel_name'],
                            'logo_deleted': delete_result.get('logo_deleted')
                        })
                    else:
                        results['errors'].append({
                            'channel_id': channel['dispatcharr_channel_id'],
                            'error': delete_result.get('error')
                        })
                elif missing_streams:
                    # Some streams are gone - remove them from channel but keep channel
                    from database import remove_stream_from_channel, log_channel_history
                    for stream in missing_streams:
                        try:
                            # Remove from Teamarr DB
                            remove_stream_from_channel(channel['id'], stream['dispatcharr_stream_id'])

                            # Also update Dispatcharr - remove the missing stream from the channel
                            # (the stream may already be gone from Dispatcharr, but we still need to
                            # update the channel's stream list to remove the stale reference)
                            with self._dispatcharr_lock:
                                current_channel = self.channel_api.get_channel(channel['dispatcharr_channel_id'])
                                if current_channel:
                                    current_streams = current_channel.get('streams', [])
                                    if stream['dispatcharr_stream_id'] in current_streams:
                                        new_streams = [s for s in current_streams if s != stream['dispatcharr_stream_id']]
                                        self.channel_api.update_channel(
                                            channel['dispatcharr_channel_id'],
                                            {'streams': new_streams}
                                        )

                            log_channel_history(
                                managed_channel_id=channel['id'],
                                change_type='stream_removed',
                                change_source='epg_generation',
                                notes=f"Stream '{stream.get('stream_name', stream['dispatcharr_stream_id'])}' no longer exists"
                            )
                            logger.debug(
                                f"Removed missing stream {stream['dispatcharr_stream_id']} from channel "
                                f"'{channel['channel_name']}'"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to remove missing stream from channel: {e}")
            else:
                # Legacy: Check primary stream ID (V1 channels without stream records)
                primary_stream_id = channel.get('dispatcharr_stream_id')
                if primary_stream_id and primary_stream_id not in current_ids_set:
                    # Stream no longer exists - delete channel
                    delete_result = self.delete_managed_channel(channel, reason='stream removed')

                    if delete_result.get('success'):
                        results['deleted'].append({
                            'channel_id': channel['dispatcharr_channel_id'],
                            'channel_number': channel['channel_number'],
                            'channel_name': channel['channel_name'],
                            'logo_deleted': delete_result.get('logo_deleted')
                        })
                    else:
                        results['errors'].append({
                            'channel_id': channel['dispatcharr_channel_id'],
                            'error': delete_result.get('error')
                        })

        return results

    def cleanup_disabled_groups(self) -> Dict[str, Any]:
        """
        Clean up channels from disabled event groups.

        When a group is DISABLED (not deleted), channels are cleaned up at
        the next EPG generation rather than immediately. This allows users
        to re-enable the group without losing channels.

        Returns:
            Dict with 'deleted' and 'errors' lists
        """
        from database import get_all_event_epg_groups, get_managed_channels_for_group

        results = {
            'deleted': [],
            'errors': []
        }

        # Get all disabled groups
        all_groups = get_all_event_epg_groups(enabled_only=False)
        disabled_groups = [g for g in all_groups if not g.get('enabled', True)]

        if not disabled_groups:
            return results

        logger.info(f"Checking {len(disabled_groups)} disabled group(s) for channel cleanup...")

        for group in disabled_groups:
            group_id = group['id']
            group_name = group.get('group_name', f'Group {group_id}')

            # Get active managed channels for this disabled group
            channels = get_managed_channels_for_group(group_id)
            if not channels:
                continue

            logger.info(f"Cleaning up {len(channels)} channel(s) from disabled group '{group_name}'...")

            for channel in channels:
                # Use unified delete method
                delete_result = self.delete_managed_channel(
                    channel,
                    reason=f"group '{group_name}' disabled"
                )

                if delete_result.get('success'):
                    results['deleted'].append({
                        'group_name': group_name,
                        'channel_id': channel['dispatcharr_channel_id'],
                        'channel_name': channel['channel_name']
                    })
                else:
                    results['errors'].append({
                        'group_name': group_name,
                        'channel_id': channel.get('dispatcharr_channel_id'),
                        'error': delete_result.get('error')
                    })

        if results['deleted']:
            logger.info(f"Cleaned up {len(results['deleted'])} channel(s) from disabled groups")

        return results

    def process_scheduled_deletions(self) -> Dict[str, Any]:
        """
        Process channels that are past their scheduled deletion time.

        Should be called periodically (e.g., on each refresh or via cron).

        Returns:
            Dict with deleted and error counts
        """
        from database import get_channels_pending_deletion

        results = {
            'deleted': [],
            'errors': []
        }

        pending = get_channels_pending_deletion()

        for channel in pending:
            # Use unified delete method
            delete_result = self.delete_managed_channel(channel, reason='scheduled deletion')

            if delete_result.get('success'):
                results['deleted'].append({
                    'channel_id': channel['dispatcharr_channel_id'],
                    'channel_number': channel['channel_number'],
                    'channel_name': channel['channel_name'],
                    'logo_deleted': delete_result.get('logo_deleted')
                })
            else:
                results['errors'].append({
                    'channel_id': channel['dispatcharr_channel_id'],
                    'error': delete_result.get('error')
                })

        return results

    def update_existing_channels(
        self,
        matched_streams: List[Dict],
        group: Dict
    ) -> Dict[str, Any]:
        """
        Update existing managed channels with fresh event data.

        This recalculates scheduled delete times based on current event info
        and group settings, in case events were rescheduled or settings changed.

        Args:
            matched_streams: List of dicts with 'stream', 'teams', 'event' keys
            group: Event EPG group configuration

        Returns:
            Dict with updated count and any errors
        """
        from database import (
            get_managed_channel_by_event,
            update_managed_channel,
            get_template
        )

        results = {
            'updated': [],
            'errors': []
        }

        # Get delete timing - always use global settings (no per-group overrides)
        global_settings = get_global_lifecycle_settings()
        delete_timing = global_settings['channel_delete_timing']
        sport = group.get('assigned_sport')

        # Get template for duration calculation
        template = None
        if group.get('event_template_id'):
            template = get_template(group['event_template_id'])

        for matched in matched_streams:
            event = matched['event']
            espn_event_id = event.get('id')
            if not espn_event_id:
                continue

            # Check if we have an existing channel for this event
            existing = get_managed_channel_by_event(espn_event_id, group['id'])
            if not existing:
                continue

            # Skip if already deleted
            if existing.get('deleted_at'):
                continue

            # Recalculate scheduled delete time (uses template duration if custom)
            new_delete_at = calculate_delete_time(event, delete_timing, self.timezone, sport, self.settings, template)
            old_delete_at = existing.get('scheduled_delete_at')

            # Convert old_delete_at to compare (may be string from DB)
            old_delete_str = old_delete_at if old_delete_at else None
            new_delete_str = new_delete_at.isoformat() if new_delete_at else None

            # Update if changed
            if old_delete_str != new_delete_str:
                try:
                    update_managed_channel(
                        existing['id'],
                        {'scheduled_delete_at': new_delete_str}
                    )
                    results['updated'].append({
                        'channel_id': existing['dispatcharr_channel_id'],
                        'channel_name': existing['channel_name'],
                        'old_delete_at': old_delete_str,
                        'new_delete_at': new_delete_str
                    })
                    logger.debug(
                        f"Updated delete time for channel '{existing['channel_name']}': "
                        f"{old_delete_str} -> {new_delete_str}"
                    )
                except Exception as e:
                    results['errors'].append({
                        'channel_id': existing['dispatcharr_channel_id'],
                        'error': str(e)
                    })

        if results['updated']:
            logger.info(f"Updated scheduled delete times for {len(results['updated'])} channels")

        return results

    def sync_group_settings(self, group: Dict) -> Dict[str, Any]:
        """
        Sync all active channels in a group with current group settings.

        This ensures that if the user changes delete_timing or other settings,
        ALL existing channels for this group are updated accordingly.
        Called during every EPG refresh to ensure settings changes are honored.

        Args:
            group: Event EPG group configuration

        Returns:
            Dict with updated count and any errors
        """
        from database import (
            get_managed_channels_for_group,
            update_managed_channel,
            get_template
        )

        results = {
            'updated': [],
            'cleared': [],
            'errors': []
        }

        # Get delete timing - always use global settings (no per-group overrides)
        global_settings = get_global_lifecycle_settings()
        delete_timing = global_settings['channel_delete_timing']
        sport = group.get('assigned_sport')

        # Get template for duration calculation
        template = None
        if group.get('event_template_id'):
            template = get_template(group['event_template_id'])

        # Get all active (non-deleted) channels for this group
        channels = get_managed_channels_for_group(group['id'])

        if not channels:
            return results

        # For 'manual' or 'stream_removed' timing, clear any existing scheduled_delete_at
        if delete_timing in ('manual', 'stream_removed'):
            for channel in channels:
                if channel.get('scheduled_delete_at'):
                    try:
                        update_managed_channel(
                            channel['id'],
                            {'scheduled_delete_at': None}
                        )
                        results['cleared'].append({
                            'channel_id': channel['dispatcharr_channel_id'],
                            'channel_name': channel['channel_name']
                        })
                        logger.debug(
                            f"Cleared scheduled delete for '{channel['channel_name']}' "
                            f"(group set to {delete_timing})"
                        )
                    except Exception as e:
                        results['errors'].append({
                            'channel_id': channel['dispatcharr_channel_id'],
                            'error': str(e)
                        })

            if results['cleared']:
                logger.info(
                    f"Cleared scheduled delete times for {len(results['cleared'])} channels "
                    f"(group delete_timing is now '{delete_timing}')"
                )
            return results

        # For timed deletion, use stored event_date to recalculate delete time
        # No need to call ESPN API - we already have the event date stored
        for channel in channels:
            event_date = channel.get('event_date')
            if not event_date:
                continue

            try:
                # Build minimal event dict from stored data
                # calculate_delete_time only needs event['date']
                event = {'date': event_date}

                # Recalculate scheduled delete time (uses template duration if custom)
                new_delete_at = calculate_delete_time(event, delete_timing, self.timezone, sport, self.settings, template)
                old_delete_at = channel.get('scheduled_delete_at')

                old_delete_str = old_delete_at if old_delete_at else None
                new_delete_str = new_delete_at.isoformat() if new_delete_at else None

                if old_delete_str != new_delete_str:
                    update_managed_channel(
                        channel['id'],
                        {'scheduled_delete_at': new_delete_str}
                    )
                    results['updated'].append({
                        'channel_id': channel['dispatcharr_channel_id'],
                        'channel_name': channel['channel_name'],
                        'old_delete_at': old_delete_str,
                        'new_delete_at': new_delete_str
                    })

            except Exception as e:
                logger.debug(f"Could not update channel {channel['channel_name']}: {e}")
                continue

        if results['updated']:
            logger.info(f"Synced delete times for {len(results['updated'])} channels with group settings")

        return results

    def associate_epg_with_channels(
        self,
        group_id: int = None
    ) -> Dict[str, Any]:
        """
        Associate EPG data with managed channels after EPG refresh.

        This implements Dispatcharr's pattern:
        1. Look up EPGData by tvg_id (filtered by EPG source)
        2. Call set_channel_epg(channel_id, epg_data_id)

        Should be called AFTER EPG is refreshed in Dispatcharr.

        Args:
            group_id: Optional group ID to filter channels (None = all groups)

        Returns:
            Dict with 'associated', 'skipped', 'errors' lists
        """
        results = {
            'associated': [],
            'skipped': [],
            'errors': []
        }

        # Get managed channels
        from database import get_managed_channels_for_group, update_managed_channel, get_connection

        if group_id:
            channels = get_managed_channels_for_group(group_id)
        else:
            # Get all active managed channels
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM managed_channels WHERE deleted_at IS NULL"
            )
            channels = [dict(row) for row in cursor.fetchall()]

        if not channels:
            logger.debug("No managed channels to associate EPG with")
            return results

        # Get EPG source ID from settings
        if not self.epg_data_id:
            logger.warning("No EPG source configured - cannot associate EPG")
            return results

        logger.info(f"Associating EPG with {len(channels)} managed channels...")

        for channel in channels:
            tvg_id = channel.get('tvg_id')
            dispatcharr_channel_id = channel.get('dispatcharr_channel_id')
            channel_name = channel.get('channel_name', f"Channel {dispatcharr_channel_id}")

            if not tvg_id:
                # Generate tvg_id from ESPN event ID for legacy channels
                espn_event_id = channel.get('espn_event_id')
                if espn_event_id:
                    tvg_id = generate_event_tvg_id(espn_event_id)
                    # Update the channel record with the tvg_id
                    update_managed_channel(channel['id'], {'tvg_id': tvg_id})
                else:
                    results['skipped'].append({
                        'channel_name': channel_name,
                        'reason': 'No tvg_id or espn_event_id'
                    })
                    continue

            # Serialize Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Look up EPGData by tvg_id in the Teamarr EPG source
                epg_data = self.channel_api.find_epg_data_by_tvg_id(
                    tvg_id,
                    epg_source_id=self.epg_data_id
                )

                if not epg_data:
                    results['skipped'].append({
                        'channel_name': channel_name,
                        'tvg_id': tvg_id,
                        'reason': f'EPGData not found for tvg_id={tvg_id}'
                    })
                    continue

                # Associate EPG with channel
                epg_data_id = epg_data['id']
                epg_result = self.channel_api.set_channel_epg(
                    dispatcharr_channel_id,
                    epg_data_id
                )

            if epg_result.get('success'):
                results['associated'].append({
                    'channel_name': channel_name,
                    'dispatcharr_channel_id': dispatcharr_channel_id,
                    'tvg_id': tvg_id,
                    'epg_data_id': epg_data_id
                })
                logger.debug(f"Associated EPG with channel '{channel_name}' (epg_data_id={epg_data_id})")
            else:
                results['errors'].append({
                    'channel_name': channel_name,
                    'tvg_id': tvg_id,
                    'error': epg_result.get('error', 'Unknown error')
                })
                logger.warning(
                    f"Failed to associate EPG with channel '{channel_name}': "
                    f"{epg_result.get('error')}"
                )

        # Summary logging
        if results['associated']:
            logger.info(f"Associated EPG with {len(results['associated'])} channels")
        if results['skipped']:
            logger.debug(f"Skipped {len(results['skipped'])} channels (no matching EPGData)")
        if results['errors']:
            logger.warning(f"Failed to associate {len(results['errors'])} channels")

        return results


# =============================================================================
# Background Scheduler
# =============================================================================

_scheduler_thread = None
_scheduler_stop_event = None


def start_lifecycle_scheduler(interval_minutes: int = 15):
    """
    Start background scheduler for processing channel lifecycle.

    Runs periodically to:
    - Process scheduled deletions

    Args:
        interval_minutes: How often to run (default: 15 minutes)
    """
    import threading

    global _scheduler_thread, _scheduler_stop_event

    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.warning("Lifecycle scheduler already running")
        return

    _scheduler_stop_event = threading.Event()

    def scheduler_loop():
        logger.info(f"Channel lifecycle scheduler started (interval: {interval_minutes} min)")

        while not _scheduler_stop_event.is_set():
            # Wait for interval (or stop event)
            if _scheduler_stop_event.wait(timeout=interval_minutes * 60):
                break  # Stop event was set

            try:
                logger.debug("Running scheduled lifecycle check...")
                manager = get_lifecycle_manager()
                if manager:
                    results = manager.process_scheduled_deletions()
                    if results['deleted']:
                        logger.info(f"Scheduler deleted {len(results['deleted'])} channels")
                    if results['errors']:
                        logger.warning(f"Scheduler had {len(results['errors'])} errors")
            except Exception as e:
                logger.error(f"Error in lifecycle scheduler: {e}")

        logger.info("Channel lifecycle scheduler stopped")

    _scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_lifecycle_scheduler():
    """Stop the background lifecycle scheduler."""
    global _scheduler_thread, _scheduler_stop_event

    if _scheduler_stop_event:
        _scheduler_stop_event.set()

    if _scheduler_thread:
        _scheduler_thread.join(timeout=5)
        _scheduler_thread = None
        _scheduler_stop_event = None
        logger.info("Lifecycle scheduler stopped")


def is_scheduler_running() -> bool:
    """Check if the lifecycle scheduler is running."""
    return _scheduler_thread is not None and _scheduler_thread.is_alive()


def get_lifecycle_manager() -> Optional[ChannelLifecycleManager]:
    """
    Get a ChannelLifecycleManager instance using settings from database.

    Returns:
        ChannelLifecycleManager or None if Dispatcharr not configured
    """
    from database import get_connection

    conn = get_connection()
    try:
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    finally:
        conn.close()

    if not settings.get('dispatcharr_enabled'):
        return None

    url = settings.get('dispatcharr_url')
    username = settings.get('dispatcharr_username')
    password = settings.get('dispatcharr_password')
    timezone = settings.get('default_timezone', 'America/Detroit')

    # EPG source ID in Dispatcharr (for direct EPG injection)
    epg_data_id = settings.get('dispatcharr_epg_id')

    if not all([url, username, password]):
        return None

    return ChannelLifecycleManager(
        url, username, password,
        timezone=timezone,
        epg_data_id=epg_data_id,
        settings=settings
    )
