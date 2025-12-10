"""
Event EPG Generator for Dispatcharr Event Channels

Generates XMLTV files for streams matched to ESPN events.
Reuses core XMLTV formatting from the team-based generator.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo

from epg.xmltv_generator import XMLTVGenerator
from epg.event_template_engine import EventTemplateEngine, build_event_context

logger = logging.getLogger(__name__)


class EventEPGGenerator:
    """
    Generate XMLTV EPG for event-based channels.

    Unlike the team-based generator that creates EPG for team channels,
    this generator creates EPG for event streams from Dispatcharr.

    Each stream represents a single game/event, so:
    - One channel per stream
    - One programme per channel (the event)
    """

    # Fallback durations (hours) - only used if settings unavailable
    FALLBACK_DURATIONS = {
        'football': 3.5,
        'basketball': 2.5,
        'hockey': 3.0,
        'baseball': 3.5,
        'soccer': 2.0,
    }

    def __init__(self, timezone: str = 'America/Detroit'):
        """
        Initialize Event EPG Generator.

        Args:
            timezone: Default timezone for display (IANA format)
        """
        self.timezone = timezone
        # Reuse XMLTVGenerator for formatting helpers only
        # Watermarking is handled by the consolidator
        self._xmltv = XMLTVGenerator()
        self._template_engine = EventTemplateEngine()

    def _get_event_duration(
        self,
        group_info: Dict,
        settings: Optional[Dict],
        template: Optional[Dict]
    ) -> float:
        """
        Get event duration based on template mode and settings.

        Respects the same duration modes as team-based EPG:
        - 'custom': Use template's custom override value
        - 'sport': Use sport-specific setting from settings
        - 'default': Use global default from settings

        Args:
            group_info: Event group with assigned_sport
            settings: Settings dict with duration values
            template: Optional template with duration override

        Returns:
            Duration in hours
        """
        settings = settings or {}
        sport = group_info.get('assigned_sport', 'football').lower()

        # Get duration mode from template (default to 'sport' for event templates)
        duration_mode = 'sport'
        if template:
            duration_mode = template.get('game_duration_mode', 'sport')

        if duration_mode == 'custom':
            # Use custom override from template (value is in hours)
            if template and template.get('game_duration_override'):
                return float(template['game_duration_override'])
            # Fall back to sport-specific if no override value
            duration_mode = 'sport'

        if duration_mode == 'sport':
            # Use sport-specific setting from settings
            sport_key = f'game_duration_{sport}'
            if sport_key in settings:
                return float(settings[sport_key])
            # Fall back to global default
            if 'game_duration_default' in settings:
                return float(settings['game_duration_default'])
        elif duration_mode == 'default':
            # Use global default from settings
            if 'game_duration_default' in settings:
                return float(settings['game_duration_default'])

        # Final fallback to hardcoded values
        return self.FALLBACK_DURATIONS.get(sport, 3.0)

    def _build_effective_group_info(
        self,
        group_info: Dict,
        matched: Dict
    ) -> Dict:
        """
        Build effective group_info with per-stream overrides for multi-sport groups.

        For multi-sport groups, the group's assigned_league is empty and assigned_sport
        is 'multi'. This method overrides those with per-stream detected values so that
        template variables {league} and {sport} resolve correctly.

        For single-sport groups, returns group_info unchanged.

        Args:
            group_info: Event EPG group configuration
            matched: Matched stream data with 'teams' containing 'detected_league'

        Returns:
            Effective group_info dict (copy with overrides, or original)
        """
        # Only apply overrides for multi-sport groups
        if group_info.get('assigned_sport') != 'multi':
            return group_info

        # Extract detected league from matched data
        # detected_league is stored in teams dict or at top level of matched
        teams = matched.get('teams', {})
        detected_league = matched.get('detected_league') or teams.get('detected_league')

        if not detected_league:
            return group_info

        # Derive sport from league
        from epg.league_detector import get_sport_for_league
        detected_sport = get_sport_for_league(detected_league)

        # Build effective group_info with overrides
        effective = dict(group_info)
        effective['assigned_league'] = detected_league
        if detected_sport:
            effective['assigned_sport'] = detected_sport

        return effective

    def generate(
        self,
        matched_streams: List[Dict],
        group_info: Dict,
        settings: Optional[Dict] = None,
        template: Optional[Dict] = None,
        epg_start_datetime: Optional[datetime] = None
    ) -> str:
        """
        Generate XMLTV for matched streams.

        Args:
            matched_streams: List of dicts with 'stream', 'teams', 'event' keys
            group_info: Event EPG group configuration
            settings: Optional settings dict
            template: Optional event template for customizing programme content
            epg_start_datetime: Optional EPG start datetime (for filler spanning multiple days)

        Returns:
            XMLTV XML string
        """
        import xml.etree.ElementTree as ET

        settings = settings or {}

        # Get days_ahead from settings for calculating EPG end date
        days_ahead = settings.get('epg_days_ahead', 14)

        # Calculate EPG start datetime if not provided
        epg_timezone = settings.get('default_timezone', 'America/Detroit')
        try:
            tz = ZoneInfo(epg_timezone)
        except Exception:
            tz = ZoneInfo('America/Detroit')

        if epg_start_datetime is None:
            # Default to now, rounded down to last hour
            now = datetime.now(tz)
            epg_start_datetime = now.replace(minute=0, second=0, microsecond=0)
        elif epg_start_datetime.tzinfo is None:
            epg_start_datetime = epg_start_datetime.replace(tzinfo=tz)

        # Create root element (no watermark - consolidator handles that)
        tv = ET.Element('tv')

        # Track channels we've added (avoid duplicates)
        added_channels = set()

        for matched in matched_streams:
            stream = matched['stream']
            event = matched['event']
            exception_keyword = matched.get('exception_keyword')

            # For multi-sport groups, build effective group_info with per-stream league/sport
            # This ensures {league} and {sport} template variables resolve to detected values
            effective_group_info = self._build_effective_group_info(group_info, matched)

            channel_id = self._get_channel_id(stream, event)

            # Add channel if not already added
            if channel_id not in added_channels:
                self._add_channel(tv, stream, event, effective_group_info, settings, template, exception_keyword)
                added_channels.add(channel_id)

            # Add pregame filler if enabled in template (EPG start to event start)
            if template and template.get('pregame_enabled'):
                self._add_pregame_programmes(
                    tv, stream, event, effective_group_info, settings, template,
                    epg_start_datetime, days_ahead, exception_keyword
                )

            # Add programme for the event
            self._add_programme(tv, stream, event, effective_group_info, settings, template, exception_keyword)

            # Add postgame filler if enabled in template (event end to EPG end)
            if template and template.get('postgame_enabled'):
                self._add_postgame_programmes(
                    tv, stream, event, effective_group_info, settings, template,
                    epg_start_datetime, days_ahead, exception_keyword
                )

        # Convert to pretty XML
        xml_str = self._xmltv._prettify(tv)
        xml_with_doctype = self._xmltv._add_doctype(xml_str)

        return xml_with_doctype

    def _get_channel_id(self, stream: Dict, event: Dict = None) -> str:
        """
        Generate consistent channel ID (tvg_id) for an event stream.

        Uses ESPN event ID for consistency across EPG generation and channel creation.
        Format: teamarr-event-{espn_event_id}

        This tvg_id is used:
        1. In XMLTV <channel id="..."> and <programme channel="...">
        2. When creating channels in Dispatcharr
        3. To look up EPGData for channel-EPG association
        """
        if event and event.get('id'):
            return f"teamarr-event-{event['id']}"
        # Fallback for edge cases (shouldn't happen in normal flow)
        return stream.get('tvg_id') or f"event-{stream.get('id', 'unknown')}"

    def _add_channel(
        self,
        parent,
        stream: Dict,
        event: Dict,
        group_info: Dict,
        settings: Dict = None,
        template: Dict = None,
        exception_keyword: str = None
    ):
        """Add channel element for a stream."""
        import xml.etree.ElementTree as ET

        channel = ET.SubElement(parent, 'channel')
        channel.set('id', self._get_channel_id(stream, event))

        # Display name - use template channel_name if available, else stream name
        display_name = ET.SubElement(channel, 'display-name')
        if template and template.get('channel_name'):
            epg_timezone = settings.get('default_timezone', 'America/Detroit') if settings else 'America/Detroit'
            template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings, exception_keyword=exception_keyword)
            display_name.text = self._template_engine.resolve(template['channel_name'], template_ctx)
        else:
            display_name.text = stream.get('name', '')

        # Channel icon/logo - use template channel_logo_url if available
        if template and template.get('channel_logo_url'):
            epg_timezone = settings.get('default_timezone', 'America/Detroit') if settings else 'America/Detroit'
            template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings, exception_keyword=exception_keyword)
            logo_url = self._template_engine.resolve(template['channel_logo_url'], template_ctx)
            if logo_url:
                icon = ET.SubElement(channel, 'icon')
                icon.set('src', logo_url)

    def _add_programme(
        self,
        parent,
        stream: Dict,
        event: Dict,
        group_info: Dict,
        settings: Dict,
        template: Optional[Dict] = None,
        exception_keyword: str = None
    ):
        """Add programme element for an event."""
        import xml.etree.ElementTree as ET

        # Parse event date
        event_date = self._parse_event_date(event.get('date'))
        if not event_date:
            logger.warning(f"Could not parse event date for {event.get('name')}")
            return

        # Calculate duration using settings-based values (same as team EPG)
        duration_hours = self._get_event_duration(group_info, settings, template)
        end_date = event_date + timedelta(hours=duration_hours)

        # Format times for XMLTV
        start_time = self._xmltv._format_xmltv_time(event_date)
        stop_time = self._xmltv._format_xmltv_time(end_date)

        programme = ET.SubElement(parent, 'programme')
        programme.set('start', start_time)
        programme.set('stop', stop_time)
        programme.set('channel', self._get_channel_id(stream, event))

        # Build template context for variable resolution
        epg_timezone = settings.get('default_timezone', 'America/Detroit')
        template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings, exception_keyword=exception_keyword)

        # Title - from template (required)
        title = ET.SubElement(programme, 'title')
        title.set('lang', 'en')
        if template and template.get('title_format'):
            title.text = self._template_engine.resolve(template['title_format'], template_ctx)
        else:
            # Minimal fallback - just team names
            home = event.get('home_team', {}).get('name', '')
            away = event.get('away_team', {}).get('name', '')
            title.text = f"{away} @ {home}" if home and away else stream.get('name', '')

        # Sub-title - from template only
        if template and template.get('subtitle_template'):
            subtitle_text = self._template_engine.resolve(template['subtitle_template'], template_ctx)
            if subtitle_text:
                sub_title = ET.SubElement(programme, 'sub-title')
                sub_title.set('lang', 'en')
                sub_title.text = subtitle_text

        # Description - from template only
        if template and template.get('description_options'):
            desc_template = self._template_engine.select_description(
                template['description_options'],
                template_ctx
            )
            if desc_template:
                desc_text = self._template_engine.resolve(desc_template, template_ctx)
                if desc_text:
                    desc = ET.SubElement(programme, 'desc')
                    desc.set('lang', 'en')
                    desc.text = desc_text

        # Categories - from template with variable resolution (respects categories_apply_to)
        self._add_categories(programme, template, template_ctx, is_filler=False)

        # Date - convert to user's timezone for correct local date
        from zoneinfo import ZoneInfo
        try:
            user_tz = ZoneInfo(epg_timezone)
            local_date = event_date.astimezone(user_tz)
        except Exception:
            local_date = event_date
        date_elem = ET.SubElement(programme, 'date')
        date_elem.text = local_date.strftime('%Y%m%d')

        # Programme Icon/Art - from template only
        if template and template.get('program_art_url'):
            icon_url = self._template_engine.resolve(template['program_art_url'], template_ctx)
            if icon_url:
                icon = ET.SubElement(programme, 'icon')
                icon.set('src', icon_url)

        # Flags - from template (not hardcoded)
        self._add_flags(programme, template)

        # Teamarr metadata (invisible to EPG readers, used internally)
        programme.append(ET.Comment("teamarr:event-event"))

    def _parse_event_date(self, date_str: str) -> Optional[datetime]:
        """Parse ESPN event date string to datetime."""
        if not date_str:
            return None

        try:
            # ESPN format: "2024-01-15T20:00Z" or "2024-01-15T20:00:00Z"
            if date_str.endswith('Z'):
                date_str = date_str[:-1] + '+00:00'
            return datetime.fromisoformat(date_str)
        except Exception as e:
            logger.warning(f"Could not parse date '{date_str}': {e}")
            return None

    def _add_pregame_programmes(
        self,
        parent,
        stream: Dict,
        event: Dict,
        group_info: Dict,
        settings: Dict,
        template: Dict,
        epg_start_datetime: datetime,
        days_ahead: int,
        exception_keyword: str = None
    ):
        """
        Add pregame filler programmes from EPG start to event start.

        Creates daily filler programmes spanning from EPG start time to event start,
        which can span multiple days if channel is created before game day.

        Args:
            parent: XML parent element
            stream: Stream data
            event: ESPN event data
            group_info: Event EPG group config
            settings: Settings dict
            template: Event template
            epg_start_datetime: When EPG starts (channel creation or EPG generation time)
            days_ahead: Number of days in EPG window
            exception_keyword: Optional matched exception keyword for sub-consolidation
        """
        import xml.etree.ElementTree as ET

        # Parse event date
        event_date = self._parse_event_date(event.get('date'))
        if not event_date:
            return

        # Get user's timezone
        epg_timezone = settings.get('default_timezone', 'America/Detroit')
        try:
            tz = ZoneInfo(epg_timezone)
        except Exception:
            tz = ZoneInfo('America/Detroit')

        # Convert times to user's timezone
        epg_start_local = epg_start_datetime.astimezone(tz)
        event_local = event_date.astimezone(tz)

        # Don't generate pregame if EPG starts at or after event start
        if epg_start_local >= event_local:
            return

        # Build template context for variable resolution (once, reused for all programmes)
        template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings, exception_keyword=exception_keyword)

        # Create daily filler programmes from EPG start to event start
        current_start = epg_start_local
        while current_start < event_local:
            # Calculate end of this programme (midnight of next day, or event start if sooner)
            next_midnight = (current_start + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            current_end = min(next_midnight, event_local)

            # Create the programme element
            self._create_filler_programme(
                parent=parent,
                stream=stream,
                event=event,
                start_dt=current_start,
                end_dt=current_end,
                template=template,
                template_ctx=template_ctx,
                filler_type='pregame',
                settings=settings
            )

            current_start = current_end

    def _enrich_event_for_postgame(self, event: Dict, group_info: Dict, settings: Dict) -> Dict:
        """
        Enrich event with fresh scoreboard data for postgame filler.

        When generating postgame filler, the original event data may be stale
        (captured when the game was matched, not when EPG is generated).
        This method fetches the latest data from the scoreboard to get
        updated scores and status for completed games.

        Args:
            event: Original ESPN event data
            group_info: Event EPG group config (contains sport/league info)
            settings: Settings dict (for timezone)

        Returns:
            Enriched event dict with updated scores/status, or original if enrichment fails
        """
        try:
            from api.espn_client import ESPNClient
            from epg.league_config import get_league_config, parse_api_path
            from database import get_connection

            # Get event ID and date
            event_id = event.get('id')
            event_date_str = event.get('date', '')
            if not event_id or not event_date_str:
                return event

            # Get league info - check event first (for multi-sport), then group
            league = event.get('league') or group_info.get('assigned_league', '')
            if not league:
                return event

            # Get API path for the league
            config = get_league_config(league, get_connection)
            if not config:
                # Try soccer leagues cache as fallback
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM soccer_leagues_cache WHERE league_slug = ?",
                    (league,)
                )
                is_soccer = cursor.fetchone() is not None
                conn.close()

                if is_soccer:
                    api_sport = 'soccer'
                    api_league = league
                else:
                    logger.debug(f"No config for league {league}, cannot enrich postgame event")
                    return event
            else:
                api_sport, api_league = parse_api_path(config['api_path'])
                if not api_sport or not api_league:
                    return event

            # Parse event date to get scoreboard date
            epg_timezone = settings.get('default_timezone', 'America/Detroit')
            try:
                tz = ZoneInfo(epg_timezone)
            except Exception:
                tz = ZoneInfo('America/Detroit')

            event_date_utc = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
            event_date_local = event_date_utc.astimezone(tz)
            date_str = event_date_local.strftime('%Y%m%d')

            # Fetch scoreboard for the event's date
            espn = ESPNClient()
            scoreboard_data = espn.get_scoreboard(api_sport, api_league, date_str)

            if not scoreboard_data or 'events' not in scoreboard_data:
                logger.debug(f"No scoreboard data for {api_league} on {date_str}")
                return event

            # Find our event in the scoreboard
            for sb_event in scoreboard_data.get('events', []):
                if str(sb_event.get('id')) == str(event_id):
                    # Found it - extract updated data
                    competitions = sb_event.get('competitions', [])
                    if not competitions:
                        return event

                    comp = competitions[0]

                    # Update status
                    status = comp.get('status', {})
                    status_type = status.get('type', {})
                    event['status'] = {
                        'name': status_type.get('name'),
                        'state': status_type.get('state'),
                        'completed': status_type.get('completed', False),
                        'detail': status_type.get('detail') or status_type.get('shortDetail'),
                        'period': status.get('period', 0)
                    }

                    # Update team scores
                    competitors = comp.get('competitors', [])
                    for competitor in competitors:
                        team_data = competitor.get('team', {})
                        team_id = str(team_data.get('id', ''))
                        score = competitor.get('score')

                        # Match to home/away team
                        if event.get('home_team', {}).get('id') == team_id:
                            event['home_team']['score'] = score
                        elif event.get('away_team', {}).get('id') == team_id:
                            event['away_team']['score'] = score

                    logger.debug(f"Enriched event {event_id} with scoreboard data: "
                                f"status={event['status'].get('name')}, "
                                f"home={event.get('home_team', {}).get('score')}, "
                                f"away={event.get('away_team', {}).get('score')}")
                    return event

            logger.debug(f"Event {event_id} not found in scoreboard for {date_str}")
            return event

        except Exception as e:
            logger.warning(f"Error enriching event for postgame: {e}")
            return event

    def _add_postgame_programmes(
        self,
        parent,
        stream: Dict,
        event: Dict,
        group_info: Dict,
        settings: Dict,
        template: Dict,
        epg_start_datetime: datetime,
        days_ahead: int,
        exception_keyword: str = None
    ):
        """
        Add postgame filler programmes from event end to EPG end.

        Creates daily filler programmes spanning from event end to EPG window end,
        which can span multiple days based on days_ahead setting.

        Args:
            parent: XML parent element
            stream: Stream data
            event: ESPN event data
            group_info: Event EPG group config
            settings: Settings dict
            template: Event template
            epg_start_datetime: When EPG starts
            days_ahead: Number of days in EPG window
            exception_keyword: Optional matched exception keyword for sub-consolidation
        """
        import xml.etree.ElementTree as ET

        # Parse event date
        event_date = self._parse_event_date(event.get('date'))
        if not event_date:
            return

        # Get user's timezone
        epg_timezone = settings.get('default_timezone', 'America/Detroit')
        try:
            tz = ZoneInfo(epg_timezone)
        except Exception:
            tz = ZoneInfo('America/Detroit')

        # Calculate event end time
        duration_hours = self._get_event_duration(group_info, settings, template)
        event_end = event_date + timedelta(hours=duration_hours)

        # Convert times to user's timezone
        epg_start_local = epg_start_datetime.astimezone(tz)
        event_end_local = event_end.astimezone(tz)

        # Calculate EPG end datetime (midnight after the last day)
        # EPG covers days_ahead days starting from epg_start_datetime's date
        epg_start_date = epg_start_local.date()
        epg_end_date = epg_start_date + timedelta(days=days_ahead)
        epg_end_datetime = datetime.combine(epg_end_date, datetime.min.time()).replace(tzinfo=tz)

        # Don't generate postgame if event ends at or after EPG end
        if event_end_local >= epg_end_datetime:
            return

        # Re-enrich event with latest scoreboard data to get updated scores/status
        # This ensures postgame filler shows final scores even if game finished after initial match
        enriched_event = self._enrich_event_for_postgame(event, group_info, settings)

        # Build template context for variable resolution (once, reused for all programmes)
        template_ctx = build_event_context(enriched_event, stream, group_info, epg_timezone, settings, exception_keyword=exception_keyword)

        # Create daily filler programmes from event end to EPG end
        current_start = event_end_local
        while current_start < epg_end_datetime:
            # Calculate end of this programme (midnight of next day, or EPG end if sooner)
            next_midnight = (current_start + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            current_end = min(next_midnight, epg_end_datetime)

            # Create the programme element (use enriched_event for updated scores/status)
            self._create_filler_programme(
                parent=parent,
                stream=stream,
                event=enriched_event,
                start_dt=current_start,
                end_dt=current_end,
                template=template,
                template_ctx=template_ctx,
                filler_type='postgame',
                settings=settings
            )

            current_start = current_end

    def _create_filler_programme(
        self,
        parent,
        stream: Dict,
        event: Dict,
        start_dt: datetime,
        end_dt: datetime,
        template: Dict,
        template_ctx: Dict,
        filler_type: str,
        settings: Dict
    ):
        """
        Create a single filler programme element.

        Helper method used by _add_pregame_programmes and _add_postgame_programmes
        to create individual daily filler programme elements.

        Args:
            parent: XML parent element
            stream: Stream data
            event: ESPN event data
            start_dt: Programme start datetime (timezone-aware)
            end_dt: Programme end datetime (timezone-aware)
            template: Event template
            template_ctx: Pre-built template context for variable resolution
            filler_type: 'pregame' or 'postgame'
            settings: Settings dict
        """
        import xml.etree.ElementTree as ET

        # Format times for XMLTV
        start_time = self._xmltv._format_xmltv_time(start_dt)
        stop_time = self._xmltv._format_xmltv_time(end_dt)

        programme = ET.SubElement(parent, 'programme')
        programme.set('start', start_time)
        programme.set('stop', stop_time)
        programme.set('channel', self._get_channel_id(stream, event))

        # Title - use {filler_type}_title from template
        title = ET.SubElement(programme, 'title')
        title.set('lang', 'en')
        title_template = template.get(f'{filler_type}_title', f'{filler_type.capitalize()} Coverage')
        title.text = self._template_engine.resolve(title_template, template_ctx)

        # Sub-title - use {filler_type}_subtitle from template
        subtitle_template = template.get(f'{filler_type}_subtitle', '')
        if subtitle_template:
            subtitle_text = self._template_engine.resolve(subtitle_template, template_ctx)
            if subtitle_text:
                sub_title = ET.SubElement(programme, 'sub-title')
                sub_title.set('lang', 'en')
                sub_title.text = subtitle_text

        # Description - use {filler_type}_description from template
        # Postgame supports conditional logic based on game final status
        if filler_type == 'postgame':
            desc_template = self._get_postgame_description(template, event)
        else:
            desc_template = template.get(f'{filler_type}_description', '')

        if desc_template:
            desc_text = self._template_engine.resolve(desc_template, template_ctx)
            if desc_text and desc_text.strip():
                desc = ET.SubElement(programme, 'desc')
                desc.set('lang', 'en')
                desc.text = desc_text

        # Art URL if available
        art_url_key = f'{filler_type}_art_url'
        if template.get(art_url_key):
            icon_url = self._template_engine.resolve(template[art_url_key], template_ctx)
            if icon_url:
                icon = ET.SubElement(programme, 'icon')
                icon.set('src', icon_url)

        # Categories - respects categories_apply_to setting
        self._add_categories(programme, template, template_ctx, is_filler=True)

        # Teamarr metadata (invisible to EPG readers, used internally)
        programme.append(ET.Comment(f"teamarr:event-filler-{filler_type}"))

    def _add_categories(
        self,
        programme,
        template: Optional[Dict],
        context: Dict = None,
        is_filler: bool = False
    ):
        """
        Add category elements from template, resolving any variables.

        Matches team-based EPG generator behavior for consistency.

        Args:
            programme: XML programme element
            template: Template dict
            context: Variable resolution context
            is_filler: True if this is a filler programme (pregame/postgame)
        """
        import xml.etree.ElementTree as ET
        import json

        if not template:
            return

        # Check categories_apply_to setting (matches team-based generator)
        # 'all' = apply to all programs (events and filler)
        # 'events' = apply only to actual game events (not filler)
        categories_apply_to = template.get('categories_apply_to', 'events')
        should_add_categories = (categories_apply_to == 'all') or (categories_apply_to == 'events' and not is_filler)

        if not should_add_categories:
            return

        # Get categories from template
        categories_json = template.get('categories')
        if not categories_json:
            return

        # Parse if JSON string
        if isinstance(categories_json, str):
            try:
                categories = json.loads(categories_json)
            except:
                return
        else:
            categories = categories_json

        if not categories:
            return

        added_categories = set()
        for cat in categories:
            if cat:  # Skip empty strings
                # Resolve any template variables in category
                resolved_cat = cat
                if context and '{' in cat:
                    resolved_cat = self._template_engine.resolve(cat, context)
                # Avoid duplicates
                if resolved_cat not in added_categories:
                    cat_elem = ET.SubElement(programme, 'category')
                    cat_elem.set('lang', 'en')
                    cat_elem.text = resolved_cat
                    added_categories.add(resolved_cat)

    def _get_postgame_description(self, template: Dict, event: Dict) -> str:
        """
        Get the appropriate postgame description based on conditional logic.

        If postgame_conditional_enabled is True:
        - Use postgame_description_final when game is finished
        - Use postgame_description_not_final when game is not finished

        Otherwise, use the standard postgame_description.

        Args:
            template: Template dict
            event: ESPN event data

        Returns:
            Description template string
        """
        # Check if conditional logic is enabled
        if template.get('postgame_conditional_enabled'):
            status = event.get('status', {})
            is_final = (
                status.get('name', '') in ['STATUS_FINAL', 'Final'] or
                status.get('state', '') == 'post'
            )

            if is_final:
                return template.get('postgame_description_final', '')
            else:
                return template.get('postgame_description_not_final', '')

        # Default: use standard postgame_description
        return template.get('postgame_description', '')

    def _add_flags(self, programme, template: Optional[Dict]):
        """
        Add programme flags (new, live) from template settings.

        Flags are entirely template-controlled - no hardcoded logic.

        Args:
            programme: XML programme element
            template: Template dict with 'flags' JSON field
        """
        import xml.etree.ElementTree as ET
        import json

        if not template:
            return

        flags = template.get('flags')
        if not flags:
            return

        # Parse if JSON string
        if isinstance(flags, str):
            try:
                flags = json.loads(flags)
            except:
                return

        if not isinstance(flags, dict):
            return

        # Add flags based on template settings
        if flags.get('new'):
            ET.SubElement(programme, 'new')

        if flags.get('live'):
            ET.SubElement(programme, 'live')

    def _get_best_logo(self, stream: Dict, event: Dict) -> Optional[str]:
        """Get best available logo URL."""
        # Try stream logo first
        if stream.get('logo'):
            return stream['logo']

        # Try home team logo
        home = event.get('home_team', {})
        if home.get('logo'):
            return home['logo']

        # Try away team logo
        away = event.get('away_team', {})
        if away.get('logo'):
            return away['logo']

        return None

    def save_to_file(self, xml_content: str, group_id: int, data_dir: str = None) -> str:
        """
        Save generated XMLTV to file.

        Args:
            xml_content: Generated XMLTV XML string
            group_id: Event EPG group ID
            data_dir: Directory to save file (default: ./data)

        Returns:
            Path to saved file
        """
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

        os.makedirs(data_dir, exist_ok=True)

        file_path = os.path.join(data_dir, f'event_epg_{group_id}.xml')

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        logger.info(f"Saved event EPG to {file_path}")
        return file_path


def generate_event_epg(
    matched_streams: List[Dict],
    group_info: Dict,
    save: bool = True,
    data_dir: str = None,
    settings: Dict = None,
    template: Dict = None,
    epg_start_datetime: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Convenience function to generate event EPG.

    Args:
        matched_streams: List of matched stream/event dicts
        group_info: Event EPG group configuration
        save: Whether to save to file (default: True)
        data_dir: Directory to save file
        settings: Optional settings dict (for timezone, etc.)
        template: Optional event template for customizing programme content
        epg_start_datetime: Optional EPG start datetime (for filler spanning multiple days)

    Returns:
        Dict with:
        - success: bool
        - xml_content: str (if successful)
        - file_path: str (if saved)
        - channel_count: int
        - programme_count: int (total programmes including filler)
        - event_count: int (actual events/games)
        - pregame_count: int (pregame filler programmes)
        - postgame_count: int (postgame filler programmes)
    """
    try:
        generator = EventEPGGenerator()

        xml_content = generator.generate(
            matched_streams,
            group_info,
            settings=settings,
            template=template,
            epg_start_datetime=epg_start_datetime
        )

        # Count programmes by type
        event_count = len(matched_streams)  # Each matched stream = 1 event
        pregame_count = 0
        postgame_count = 0

        # If template has pregame enabled, each stream gets a pregame programme
        if template and template.get('pregame_enabled'):
            pregame_count = len(matched_streams)

        # If template has postgame enabled, each stream gets a postgame programme
        if template and template.get('postgame_enabled'):
            postgame_count = len(matched_streams)

        # Total programmes = events + pregame + postgame
        total_programmes = event_count + pregame_count + postgame_count

        result = {
            'success': True,
            'xml_content': xml_content,
            'channel_count': len(matched_streams),
            'programme_count': total_programmes,
            'event_count': event_count,
            'pregame_count': pregame_count,
            'postgame_count': postgame_count,
        }

        if save:
            file_path = generator.save_to_file(
                xml_content,
                group_info['id'],
                data_dir
            )
            result['file_path'] = file_path

        return result

    except Exception as e:
        logger.error(f"Error generating event EPG: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


def merge_xmltv_files(
    file_paths: List[str],
    output_path: str,
    generator_name: str = "Teamarr"
) -> Dict[str, Any]:
    """
    Merge multiple XMLTV files into one.

    Combines channels and programmes from multiple sources,
    removing duplicates by channel ID.

    Args:
        file_paths: List of XMLTV file paths to merge
        output_path: Path for merged output file
        generator_name: Generator name for output

    Returns:
        Dict with success status and stats
    """
    import xml.etree.ElementTree as ET
    from xml.dom import minidom
    from config import VERSION

    try:
        # Create root element
        tv = ET.Element('tv')
        tv.set('generator-info-name', generator_name)
        tv.set('generator-info-url', 'https://github.com/egyptiangio/teamarr')

        # Collect all channels and programmes separately first
        # XMLTV spec requires all <channel> elements before all <programme> elements
        seen_channels = set()
        seen_programmes = set()  # Track (channel, start, stop) to dedupe programmes
        all_channels = []
        all_programmes = []

        for file_path in file_paths:
            if not os.path.exists(file_path):
                logger.warning(f"Skipping missing file: {file_path}")
                continue

            try:
                # Parse with comments enabled to preserve teamarr metadata
                parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
                tree = ET.parse(file_path, parser)
                root = tree.getroot()

                # Collect channels (skip duplicates)
                for channel in root.findall('channel'):
                    channel_id = channel.get('id')
                    if channel_id and channel_id not in seen_channels:
                        all_channels.append(channel)
                        seen_channels.add(channel_id)

                # Collect programmes (skip duplicates by channel+start+stop)
                for programme in root.findall('programme'):
                    prog_key = (
                        programme.get('channel', ''),
                        programme.get('start', ''),
                        programme.get('stop', '')
                    )
                    if prog_key not in seen_programmes:
                        all_programmes.append(programme)
                        seen_programmes.add(prog_key)

            except ET.ParseError as e:
                logger.warning(f"Error parsing {file_path}: {e}")
                continue

        # Add all channels first, then all programmes (per XMLTV spec)
        for channel in all_channels:
            tv.append(channel)
        for programme in all_programmes:
            tv.append(programme)

        total_programmes = len(all_programmes)

        # Convert to pretty XML
        rough_string = ET.tostring(tv, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        pretty = reparsed.toprettyxml(indent='  ')

        # Clean up XML
        lines = [line for line in pretty.split('\n') if line.strip()]
        if lines and lines[0].startswith('<?xml'):
            lines = lines[1:]
        xml_str = '\n'.join(lines)

        # Add declaration, watermark, and doctype
        declaration = '<?xml version="1.0" encoding="UTF-8"?>'
        watermark = (
            '<!--\n'
            f'  Generated with Teamarr v{VERSION} - Dynamic EPG Generator for Sports Channels\n'
            '  https://github.com/egyptiangio/teamarr\n'
            '-->'
        )
        doctype = '<!DOCTYPE tv SYSTEM "xmltv.dtd">'
        final_xml = f"{declaration}\n{watermark}\n{doctype}\n{xml_str}"

        # Write output
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(final_xml)

        logger.info(f"Merged {len(file_paths)} files -> {output_path} ({len(seen_channels)} channels, {total_programmes} programmes)")

        return {
            'success': True,
            'output_path': output_path,
            'channel_count': len(seen_channels),
            'programme_count': total_programmes,
            'files_merged': len([f for f in file_paths if os.path.exists(f)])
        }

    except Exception as e:
        logger.error(f"Error merging XMLTV files: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }
