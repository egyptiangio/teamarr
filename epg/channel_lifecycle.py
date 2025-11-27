"""
Channel Lifecycle Manager for Event-based EPG

Handles automatic channel creation and deletion in Dispatcharr
based on matched streams and lifecycle settings.

EPG is injected directly via Dispatcharr's set-epg API, so tvg_id matching
is not needed for managed channels.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def get_global_lifecycle_settings() -> Dict[str, str]:
    """
    Get global channel lifecycle settings from the settings table.

    Returns:
        Dict with channel_create_timing and channel_delete_timing
    """
    try:
        from database import get_connection
        conn = get_connection()
        row = conn.execute("""
            SELECT channel_create_timing, channel_delete_timing
            FROM settings WHERE id = 1
        """).fetchone()
        conn.close()

        if row:
            return {
                'channel_create_timing': row['channel_create_timing'] or 'same_day',
                'channel_delete_timing': row['channel_delete_timing'] or 'same_day'
            }
    except Exception as e:
        logger.warning(f"Could not get global lifecycle settings: {e}")

    return {
        'channel_create_timing': 'same_day',
        'channel_delete_timing': 'same_day'
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
    timezone: str
) -> Tuple[bool, str]:
    """
    Check if a channel should be created based on event date and timing setting.

    This is the "earliest creation" check - channel won't be created before
    the threshold date, even if the stream exists.

    Args:
        event: ESPN event data with 'date' field
        create_timing: One of 'stream_available', 'same_day', 'day_before', '2_days_before', 'manual'
        timezone: Timezone for date comparison

    Returns:
        Tuple of (should_create: bool, reason: str)
    """
    # Normalize legacy values
    create_timing = normalize_create_timing(create_timing)

    # Manual means never auto-create
    if create_timing == 'manual':
        return False, "Manual creation only"

    # stream_available means create immediately when stream exists
    if create_timing == 'stream_available':
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

        # Get current date in same timezone
        now = datetime.now(tz)
        today = now.date()

        # Calculate threshold based on timing (earliest creation date)
        if create_timing == 'same_day' or create_timing == 'day_of':
            threshold_date = event_date
        elif create_timing == 'day_before':
            threshold_date = event_date - timedelta(days=1)
        elif create_timing == '2_days_before':
            threshold_date = event_date - timedelta(days=2)
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


def get_sport_duration_hours(sport: str) -> float:
    """
    Get typical duration for a sport in hours.

    These are conservative estimates including potential overtime/extra innings.
    """
    durations = {
        'football': 4.0,      # NFL/CFB games ~3-3.5 hours + buffer
        'basketball': 3.0,    # NBA/CBB games ~2-2.5 hours + buffer
        'hockey': 3.0,        # NHL games ~2.5 hours + buffer
        'baseball': 4.0,      # MLB games can go long with extra innings
        'soccer': 2.5,        # 90 min + halftime + stoppage + buffer
    }
    return durations.get(sport.lower(), 3.5)  # Default 3.5 hours


def calculate_delete_time(
    event: Dict,
    delete_timing: str,
    timezone: str,
    sport: str = None
) -> Optional[datetime]:
    """
    Calculate when a channel should be deleted based on event and timing setting.

    This is the "latest deletion" time - channel will be deleted by this time
    even if the stream still exists.

    Uses the actual event start time and sport duration to determine if the
    event will cross midnight, then schedules deletion appropriately.

    Args:
        event: ESPN event data with 'date' field
        delete_timing: One of 'stream_removed', 'same_day', 'day_after', '2_days_after', 'manual'
        timezone: Timezone for date calculation
        sport: Sport type for duration calculation (e.g., 'basketball', 'football')

    Returns:
        Datetime when channel should be deleted (at 23:59), or None for 'manual'/'stream_removed'
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

        # Calculate event end time based on sport duration
        duration_hours = get_sport_duration_hours(sport) if sport else 3.5
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
        else:
            return None

        if crosses_midnight:
            logger.debug(
                f"Event starts {event_start.strftime('%m/%d %I:%M %p')}, "
                f"ends ~{event_end.strftime('%m/%d %I:%M %p')} (crosses midnight)"
            )

        # Return as datetime at 23:59:59 on the delete date
        from datetime import time
        end_of_day = time(23, 59, 59)
        return datetime.combine(delete_date, end_of_day).replace(tzinfo=tz)

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
        epg_data_id: int = None
    ):
        """
        Initialize the lifecycle manager.

        Args:
            dispatcharr_url: Dispatcharr base URL
            dispatcharr_username: Dispatcharr username
            dispatcharr_password: Dispatcharr password
            epg_data_id: Teamarr's EPG source ID in Dispatcharr (for direct EPG injection)
            timezone: Default timezone for date calculations
        """
        from api.dispatcharr_client import ChannelManager

        self.channel_api = ChannelManager(
            dispatcharr_url,
            dispatcharr_username,
            dispatcharr_password
        )
        self.epg_data_id = epg_data_id
        self.timezone = timezone

    def process_matched_streams(
        self,
        matched_streams: List[Dict],
        group: Dict,
        template: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Process matched streams and create/update channels as needed.

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
        """
        from database import (
            get_managed_channel_by_event,
            get_next_channel_number,
            create_managed_channel
        )
        from epg.event_template_engine import EventTemplateEngine

        # Create template engine for channel name resolution
        template_engine = EventTemplateEngine()

        results = {
            'created': [],
            'skipped': [],
            'errors': [],
            'existing': []
        }

        # Get lifecycle settings from group, falling back to global settings
        global_settings = get_global_lifecycle_settings()
        channel_start = group.get('channel_start')
        channel_group_id = group.get('channel_group_id')  # Dispatcharr channel group
        create_timing = group.get('channel_create_timing') or global_settings['channel_create_timing']
        delete_timing = group.get('channel_delete_timing') or global_settings['channel_delete_timing']
        sport = group.get('assigned_sport')

        # Check if group has channel management enabled
        if not channel_start:
            logger.info(f"Group {group['id']} has no channel_start configured - skipping channel creation")
            results['skipped'] = [
                {'stream': m['stream']['name'], 'reason': 'No channel_start configured for group'}
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

            # Check if channel already exists for this event
            existing = get_managed_channel_by_event(espn_event_id, group['id'])
            if existing:
                results['existing'].append({
                    'stream': stream['name'],
                    'channel_id': existing['dispatcharr_channel_id'],
                    'channel_number': existing['channel_number']
                })
                continue

            # Check if we should create channel based on timing
            should_create, reason = should_create_channel(event, create_timing, self.timezone)
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

            # Calculate scheduled delete time
            delete_at = calculate_delete_time(event, delete_timing, self.timezone, sport)

            # Create channel in Dispatcharr
            create_result = self.channel_api.create_channel(
                name=channel_name,
                channel_number=channel_number,
                stream_ids=[stream['id']],
                channel_group_id=channel_group_id,
                logo_id=logo_id
            )

            if not create_result.get('success'):
                results['errors'].append({
                    'stream': stream['name'],
                    'error': create_result.get('error', 'Unknown error')
                })
                continue

            dispatcharr_channel = create_result['channel']
            dispatcharr_channel_id = dispatcharr_channel['id']

            # Inject EPG directly via set-epg API
            if self.epg_data_id:
                epg_result = self.channel_api.set_channel_epg(
                    dispatcharr_channel_id,
                    self.epg_data_id
                )
                if not epg_result.get('success'):
                    logger.warning(
                        f"Failed to set EPG for channel {dispatcharr_channel_id}: "
                        f"{epg_result.get('error')}"
                    )

            # Track in database
            try:
                home_team = event.get('home_team', {}).get('name', '')
                away_team = event.get('away_team', {}).get('name', '')
                event_date = event.get('date', '')[:10] if event.get('date') else None

                managed_id = create_managed_channel(
                    event_epg_group_id=group['id'],
                    dispatcharr_channel_id=dispatcharr_channel_id,
                    dispatcharr_stream_id=stream['id'],
                    channel_number=channel_number,
                    channel_name=channel_name,
                    espn_event_id=espn_event_id,
                    event_date=event_date,
                    home_team=home_team,
                    away_team=away_team,
                    scheduled_delete_at=delete_at.isoformat() if delete_at else None,
                    dispatcharr_logo_id=logo_id  # Track logo for cleanup on deletion
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
        from database import (
            get_managed_channels_for_group,
            mark_managed_channel_deleted
        )

        results = {
            'deleted': [],
            'errors': []
        }

        # Get delete timing from group or global settings
        global_settings = get_global_lifecycle_settings()
        delete_timing = group.get('channel_delete_timing') or global_settings['channel_delete_timing']
        if delete_timing != 'stream_removed':
            return results

        # Get all active managed channels for this group
        managed_channels = get_managed_channels_for_group(group['id'])
        current_ids_set = set(current_stream_ids)

        for channel in managed_channels:
            if channel['dispatcharr_stream_id'] not in current_ids_set:
                # Stream no longer exists - delete channel
                delete_result = self.channel_api.delete_channel(
                    channel['dispatcharr_channel_id']
                )

                if delete_result.get('success') or 'not found' in str(delete_result.get('error', '')).lower():
                    # Mark as deleted in database
                    mark_managed_channel_deleted(channel['id'])

                    # Clean up associated logo if present
                    logo_id = channel.get('dispatcharr_logo_id')
                    if logo_id:
                        logo_result = self.channel_api.delete_logo(logo_id)
                        if logo_result.get('status') == 'deleted':
                            logger.debug(f"Deleted logo {logo_id} for channel '{channel['channel_name']}'")

                    results['deleted'].append({
                        'channel_id': channel['dispatcharr_channel_id'],
                        'channel_number': channel['channel_number'],
                        'channel_name': channel['channel_name'],
                        'logo_deleted': logo_id if logo_id else None
                    })
                    logger.info(
                        f"Deleted channel {channel['channel_number']} "
                        f"'{channel['channel_name']}' - stream removed"
                    )
                else:
                    results['errors'].append({
                        'channel_id': channel['dispatcharr_channel_id'],
                        'error': delete_result.get('error')
                    })

        return results

    def process_scheduled_deletions(self) -> Dict[str, Any]:
        """
        Process channels that are past their scheduled deletion time.

        Should be called periodically (e.g., on each refresh or via cron).

        Returns:
            Dict with deleted and error counts
        """
        from database import (
            get_channels_pending_deletion,
            mark_managed_channel_deleted
        )

        results = {
            'deleted': [],
            'errors': []
        }

        pending = get_channels_pending_deletion()

        for channel in pending:
            delete_result = self.channel_api.delete_channel(
                channel['dispatcharr_channel_id']
            )

            if delete_result.get('success') or 'not found' in str(delete_result.get('error', '')).lower():
                mark_managed_channel_deleted(channel['id'])

                # Clean up associated logo if present
                logo_id = channel.get('dispatcharr_logo_id')
                if logo_id:
                    logo_result = self.channel_api.delete_logo(logo_id)
                    if logo_result.get('status') == 'deleted':
                        logger.debug(f"Deleted logo {logo_id} for channel '{channel['channel_name']}'")

                results['deleted'].append({
                    'channel_id': channel['dispatcharr_channel_id'],
                    'channel_number': channel['channel_number'],
                    'channel_name': channel['channel_name'],
                    'logo_deleted': logo_id if logo_id else None
                })
                logger.info(
                    f"Deleted channel {channel['channel_number']} "
                    f"'{channel['channel_name']}' - scheduled deletion"
                )
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
            update_managed_channel
        )

        results = {
            'updated': [],
            'errors': []
        }

        # Get delete timing from group or global settings
        global_settings = get_global_lifecycle_settings()
        delete_timing = group.get('channel_delete_timing') or global_settings['channel_delete_timing']
        sport = group.get('assigned_sport')

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

            # Recalculate scheduled delete time
            new_delete_at = calculate_delete_time(event, delete_timing, self.timezone, sport)
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
            update_managed_channel
        )
        from api.espn_client import ESPNClient

        results = {
            'updated': [],
            'cleared': [],
            'errors': []
        }

        # Get delete timing from group or global settings
        global_settings = get_global_lifecycle_settings()
        delete_timing = group.get('channel_delete_timing') or global_settings['channel_delete_timing']
        sport = group.get('assigned_sport')

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

        # For timed deletion, we need event data to recalculate
        # Channels without event data will keep their current schedule
        espn = ESPNClient()

        for channel in channels:
            espn_event_id = channel.get('espn_event_id')
            if not espn_event_id:
                continue

            # Try to fetch current event data from ESPN
            try:
                event = espn.get_event(espn_event_id)
                if not event:
                    continue

                # Recalculate scheduled delete time
                new_delete_at = calculate_delete_time(event, delete_timing, self.timezone, sport)
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
    timezone = settings.get('default_timezone', 'America/New_York')

    # EPG source ID in Dispatcharr (for direct EPG injection)
    epg_data_id = settings.get('dispatcharr_epg_id')

    if not all([url, username, password]):
        return None

    return ChannelLifecycleManager(
        url, username, password,
        timezone=timezone,
        epg_data_id=epg_data_id
    )
