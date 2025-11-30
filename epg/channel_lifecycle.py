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
    timezone: str = None
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

    Returns:
        Channel name string
    """
    # If template has channel_name and we have an engine, use it
    if template and template.get('channel_name') and template_engine:
        from epg.event_template_engine import build_event_context
        ctx = build_event_context(event, {}, {}, timezone)
        return template_engine.resolve(template['channel_name'], ctx)

    # Default format: "Away @ Home"
    home = event.get('home_team', {})
    away = event.get('away_team', {})

    home_name = home.get('shortDisplayName') or home.get('name', 'Home')
    away_name = away.get('shortDisplayName') or away.get('name', 'Away')

    return f"{away_name} @ {home_name}"


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
            new_logo_url = template_engine.resolve(template['channel_logo_url'], logo_ctx)

            if not new_logo_url:
                return

            # Serialize Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Get current logo URL from Dispatcharr
                current_logo_id = existing.get('dispatcharr_logo_id')
                current_logo_url = None

                if current_logo_id:
                    current_logo = self.channel_api.get_logo(current_logo_id)
                    if current_logo:
                        current_logo_url = current_logo.get('url')

                # Compare URLs - if same, no update needed
                if current_logo_url == new_logo_url:
                    return

                # Upload new logo (or find existing by URL)
                channel_name = existing.get('channel_name', 'Unknown')
                logo_name = f"{channel_name} Logo"
                logo_result = self.channel_api.upload_logo(logo_name, new_logo_url)

                if not logo_result.get('success'):
                    logger.warning(f"Failed to upload logo for '{channel_name}': {logo_result.get('error')}")
                    return

                new_logo_id = logo_result.get('logo_id')

                # Update channel in Dispatcharr with new logo
                dispatcharr_channel_id = existing.get('dispatcharr_channel_id')
                update_result = self.channel_api.update_channel(dispatcharr_channel_id, {'logo_id': new_logo_id})

                if not update_result.get('success'):
                    logger.warning(f"Failed to update channel logo for '{channel_name}': {update_result.get('error')}")
                    return

            # Update managed_channels record with new logo_id
            update_managed_channel(existing['id'], {'dispatcharr_logo_id': new_logo_id})

            results['logo_updated'].append({
                'channel_name': channel_name,
                'channel_id': dispatcharr_channel_id,
                'old_logo_url': current_logo_url,
                'new_logo_url': new_logo_url,
                'new_logo_id': new_logo_id
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
                new_channel_name = generate_channel_name(
                    event,
                    template=template,
                    template_engine=template_engine,
                    timezone=self.timezone
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

            # Check channel_profile_id (handled separately - profiles maintain channel lists)
            group_channel_profile_id = group.get('channel_profile_id')
            stored_channel_profile_id = existing.get('channel_profile_id')

            if group_channel_profile_id != stored_channel_profile_id:
                from database import update_managed_channel
                profile_changed = False

                # Serialize Dispatcharr profile operations
                with self._dispatcharr_lock:
                    # Remove from old profile if there was one
                    if stored_channel_profile_id:
                        remove_result = self.channel_api.remove_channel_from_profile(
                            stored_channel_profile_id, dispatcharr_channel_id
                        )
                        if remove_result.get('success'):
                            logger.debug(f"Removed channel {dispatcharr_channel_id} from profile {stored_channel_profile_id}")
                            profile_changed = True
                        else:
                            logger.warning(
                                f"Failed to remove channel {dispatcharr_channel_id} from profile {stored_channel_profile_id}: "
                                f"{remove_result.get('error')}"
                            )

                    # Add to new profile if there is one
                    if group_channel_profile_id:
                        add_result = self.channel_api.add_channel_to_profile(
                            group_channel_profile_id, dispatcharr_channel_id
                        )
                        if add_result.get('success'):
                            logger.debug(f"Added channel {dispatcharr_channel_id} to profile {group_channel_profile_id}")
                            profile_changed = True
                        else:
                            logger.warning(
                                f"Failed to add channel {dispatcharr_channel_id} to profile {group_channel_profile_id}: "
                                f"{add_result.get('error')}"
                            )

                # Update our record with the new profile ID
                if profile_changed:
                    update_managed_channel(existing['id'], {'channel_profile_id': group_channel_profile_id})

                    # Track in results
                    if 'profile_updated' not in results:
                        results['profile_updated'] = []
                    results['profile_updated'].append({
                        'channel_name': stored_channel_name,
                        'channel_id': dispatcharr_channel_id,
                        'old_profile': stored_channel_profile_id,
                        'new_profile': group_channel_profile_id
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
            find_existing_channel,
            stream_exists_on_channel,
            log_channel_history
        )
        from epg.event_template_engine import EventTemplateEngine

        # Create template engine for channel name resolution
        template_engine = EventTemplateEngine()

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
        channel_profile_id = group.get('channel_profile_id')  # Dispatcharr channel profile
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

            # V2: Check for existing channel based on duplicate handling mode
            existing = find_existing_channel(
                group_id=group['id'],
                event_id=espn_event_id,
                stream_id=stream.get('id') if duplicate_mode == 'separate' else None,
                mode=duplicate_mode
            )

            if existing:
                # Handle based on duplicate mode
                if duplicate_mode == 'ignore':
                    # Skip - don't add stream
                    results['existing'].append({
                        'stream': stream['name'],
                        'channel_id': existing['dispatcharr_channel_id'],
                        'channel_number': existing['channel_number'],
                        'action': 'ignored'
                    })

                elif duplicate_mode == 'consolidate':
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
                                                m3u_account_name=stream.get('m3u_account_name')
                                            )
                                            # Log history
                                            log_channel_history(
                                                managed_channel_id=existing['id'],
                                                change_type='stream_added',
                                                change_source='epg_generation',
                                                notes=f"Added stream '{stream.get('name')}' (consolidate mode)"
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
                if template and template.get('channel_logo_url'):
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

            # Generate channel name using template
            channel_name = generate_channel_name(
                event,
                template=template,
                template_engine=template_engine,
                timezone=self.timezone
            )

            # Calculate scheduled delete time (uses template duration if custom)
            delete_at = calculate_delete_time(event, delete_timing, self.timezone, sport, self.settings, template)

            # Generate tvg_id for channel-EPG association
            # This must match the channel id in the generated XMLTV
            tvg_id = generate_event_tvg_id(espn_event_id)

            # Serialize all Dispatcharr operations to prevent race conditions
            with self._dispatcharr_lock:
                # Generate and upload channel logo if template has channel_logo_url
                logo_id = None
                if template and template.get('channel_logo_url'):
                    from epg.event_template_engine import build_event_context
                    logo_ctx = build_event_context(event, stream, group, self.timezone)
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

                # Add to channel profile if configured
                if channel_profile_id:
                    profile_result = self.channel_api.add_channel_to_profile(
                        channel_profile_id, dispatcharr_channel_id
                    )
                    if not profile_result.get('success'):
                        logger.warning(
                            f"Failed to add channel {dispatcharr_channel_id} to profile {channel_profile_id}: "
                            f"{profile_result.get('error')}"
                        )
                    else:
                        logger.debug(f"Added channel {dispatcharr_channel_id} to profile {channel_profile_id}")

            # Note: EPG association happens AFTER EPG refresh in Dispatcharr
            # See associate_epg_with_channels() method

            # Track in database with V2 extended fields
            try:
                home_team_obj = event.get('home_team', {})
                away_team_obj = event.get('away_team', {})

                home_team = home_team_obj.get('name', '')
                away_team = away_team_obj.get('name', '')
                home_team_abbrev = home_team_obj.get('abbreviation', '')
                away_team_abbrev = away_team_obj.get('abbreviation', '')
                home_team_logo = home_team_obj.get('logo', '')
                away_team_logo = away_team_obj.get('logo', '')

                # Store full UTC datetime (convert to user TZ in display)
                event_date = event.get('date', '') or None

                # Build event name
                event_name = f"{away_team_abbrev or away_team} @ {home_team_abbrev or home_team}"

                # Get venue and broadcast info if available
                venue = event.get('venue', {}).get('fullName', '') if event.get('venue') else ''
                broadcast = event.get('broadcast', '')

                # Get logo URL from template if present
                logo_url_source = None
                if template and template.get('channel_logo_url'):
                    from epg.event_template_engine import build_event_context
                    logo_ctx = build_event_context(event, stream, group, self.timezone)
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
                    channel_profile_id=channel_profile_id,
                    dispatcharr_uuid=dispatcharr_uuid,
                    # V2 fields
                    primary_stream_id=stream['id'] if duplicate_mode == 'separate' else None,
                    channel_group_id=channel_group_id,
                    stream_profile_id=stream_profile_id,
                    logo_url=logo_url_source,
                    home_team_abbrev=home_team_abbrev,
                    home_team_logo=home_team_logo,
                    away_team_abbrev=away_team_abbrev,
                    away_team_logo=away_team_logo,
                    event_name=event_name,
                    league=league,
                    sport=sport,
                    venue=venue,
                    broadcast=broadcast,
                    sync_status='created'
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
                    m3u_account_name=stream.get('m3u_account_name')
                )

                # V2: Log channel creation in history
                log_channel_history(
                    managed_channel_id=managed_id,
                    change_type='created',
                    change_source='epg_generation',
                    notes=f"Channel created for event {event_name}"
                )

                results['created'].append({
                    'stream': stream['name'],
                    'channel_id': dispatcharr_channel_id,
                    'channel_number': channel_number,
                    'channel_name': channel_name,
                    'managed_id': managed_id,
                    'logo_id': logo_id,
                    'scheduled_delete_at': delete_at.isoformat() if delete_at else None
                })

                logger.info(
                    f"Created channel {channel_number} '{channel_name}' "
                    f"for stream '{stream['name']}'"
                )

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

    def cleanup_deleted_streams(
        self,
        group: Dict,
        current_stream_ids: List[int]
    ) -> Dict[str, Any]:
        """
        Clean up channels for streams that no longer exist.

        Only applies if group's delete_timing is 'stream_removed'.

        Args:
            group: Event EPG group configuration
            current_stream_ids: List of current stream IDs from Dispatcharr

        Returns:
            Dict with deleted and error counts
        """
        from database import get_managed_channels_for_group

        results = {
            'deleted': [],
            'errors': []
        }

        # Get delete timing - always use global settings (no per-group overrides)
        global_settings = get_global_lifecycle_settings()
        delete_timing = global_settings['channel_delete_timing']
        if delete_timing != 'stream_removed':
            return results

        # Get all active managed channels for this group
        managed_channels = get_managed_channels_for_group(group['id'])
        current_ids_set = set(current_stream_ids)

        for channel in managed_channels:
            if channel['dispatcharr_stream_id'] not in current_ids_set:
                # Stream no longer exists - use unified delete method
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
