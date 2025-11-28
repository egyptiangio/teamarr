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

    def generate(
        self,
        matched_streams: List[Dict],
        group_info: Dict,
        settings: Optional[Dict] = None,
        template: Optional[Dict] = None
    ) -> str:
        """
        Generate XMLTV for matched streams.

        Args:
            matched_streams: List of dicts with 'stream', 'teams', 'event' keys
            group_info: Event EPG group configuration
            settings: Optional settings dict
            template: Optional event template for customizing programme content

        Returns:
            XMLTV XML string
        """
        import xml.etree.ElementTree as ET

        settings = settings or {}

        # Create root element (no watermark - consolidator handles that)
        tv = ET.Element('tv')

        # Track channels we've added (avoid duplicates)
        added_channels = set()

        for matched in matched_streams:
            stream = matched['stream']
            event = matched['event']

            channel_id = self._get_channel_id(stream, event)

            # Add channel if not already added
            if channel_id not in added_channels:
                self._add_channel(tv, stream, event, group_info, settings, template)
                added_channels.add(channel_id)

            # Add pregame filler if enabled in template (00:00 to event start)
            if template and template.get('pregame_enabled'):
                self._add_pregame_programme(tv, stream, event, group_info, settings, template)

            # Add programme for the event
            self._add_programme(tv, stream, event, group_info, settings, template)

            # Add postgame filler if enabled in template (event end to 23:59:59)
            if template and template.get('postgame_enabled'):
                self._add_postgame_programme(tv, stream, event, group_info, settings, template)

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
        template: Dict = None
    ):
        """Add channel element for a stream."""
        import xml.etree.ElementTree as ET

        channel = ET.SubElement(parent, 'channel')
        channel.set('id', self._get_channel_id(stream, event))

        # Display name - use template channel_name if available, else stream name
        display_name = ET.SubElement(channel, 'display-name')
        if template and template.get('channel_name'):
            epg_timezone = settings.get('default_timezone', 'America/Detroit') if settings else 'America/Detroit'
            template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings)
            display_name.text = self._template_engine.resolve(template['channel_name'], template_ctx)
        else:
            display_name.text = stream.get('name', '')

        # Channel icon/logo - use template channel_logo_url if available
        if template and template.get('channel_logo_url'):
            epg_timezone = settings.get('default_timezone', 'America/Detroit') if settings else 'America/Detroit'
            template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings)
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
        template: Optional[Dict] = None
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
        template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings)

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
        self._add_categories(programme, template, template_ctx, programme_type='game')

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

    def _add_pregame_programme(
        self,
        parent,
        stream: Dict,
        event: Dict,
        group_info: Dict,
        settings: Dict,
        template: Dict
    ):
        """Add pregame filler programme (00:00 to event start)."""
        import xml.etree.ElementTree as ET
        from zoneinfo import ZoneInfo

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

        # Convert event time to user's timezone
        event_local = event_date.astimezone(tz)

        # Calculate start of day (00:00) in user's timezone
        day_start = event_local.replace(hour=0, minute=0, second=0, microsecond=0)

        # Don't generate pregame if event starts at midnight or day_start >= event_date
        if day_start >= event_date:
            return

        # Format times for XMLTV (convert back to UTC for XMLTV format)
        start_time = self._xmltv._format_xmltv_time(day_start)
        stop_time = self._xmltv._format_xmltv_time(event_date)

        programme = ET.SubElement(parent, 'programme')
        programme.set('start', start_time)
        programme.set('stop', stop_time)
        programme.set('channel', self._get_channel_id(stream, event))

        # Build template context for variable resolution
        template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings)

        # Title - use pregame_title from template
        title = ET.SubElement(programme, 'title')
        title.set('lang', 'en')
        pregame_title = template.get('pregame_title', 'Pregame Coverage')
        title.text = self._template_engine.resolve(pregame_title, template_ctx)

        # Sub-title - use pregame_subtitle from template
        pregame_subtitle = template.get('pregame_subtitle', '')
        if pregame_subtitle:
            subtitle_text = self._template_engine.resolve(pregame_subtitle, template_ctx)
            if subtitle_text:
                sub_title = ET.SubElement(programme, 'sub-title')
                sub_title.set('lang', 'en')
                sub_title.text = subtitle_text

        # Description - use pregame_description from template
        pregame_desc = template.get('pregame_description', '')
        if pregame_desc:
            desc_text = self._template_engine.resolve(pregame_desc, template_ctx)
            if desc_text:
                desc = ET.SubElement(programme, 'desc')
                desc.set('lang', 'en')
                desc.text = desc_text

        # Pregame art if available
        if template.get('pregame_art_url'):
            icon_url = self._template_engine.resolve(template['pregame_art_url'], template_ctx)
            if icon_url:
                icon = ET.SubElement(programme, 'icon')
                icon.set('src', icon_url)

        # Categories - respects categories_apply_to setting
        self._add_categories(programme, template, template_ctx, programme_type='pregame')

        # Flags - from template
        self._add_flags(programme, template)

    def _add_postgame_programme(
        self,
        parent,
        stream: Dict,
        event: Dict,
        group_info: Dict,
        settings: Dict,
        template: Dict
    ):
        """Add postgame filler programme (event end to 23:59:59)."""
        import xml.etree.ElementTree as ET
        from zoneinfo import ZoneInfo

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

        # Convert event end to user's timezone
        event_end_local = event_end.astimezone(tz)

        # Calculate end of day (23:59:59) in user's timezone
        day_end = event_end_local.replace(hour=23, minute=59, second=59, microsecond=0)

        # Don't generate postgame if event ends at or after midnight
        if event_end >= day_end:
            return

        # Format times for XMLTV
        start_time = self._xmltv._format_xmltv_time(event_end)
        stop_time = self._xmltv._format_xmltv_time(day_end)

        programme = ET.SubElement(parent, 'programme')
        programme.set('start', start_time)
        programme.set('stop', stop_time)
        programme.set('channel', self._get_channel_id(stream, event))

        # Build template context for variable resolution
        template_ctx = build_event_context(event, stream, group_info, epg_timezone, settings)

        # Title - use postgame_title from template
        title = ET.SubElement(programme, 'title')
        title.set('lang', 'en')
        postgame_title = template.get('postgame_title', 'Postgame Recap')
        title.text = self._template_engine.resolve(postgame_title, template_ctx)

        # Sub-title - use postgame_subtitle from template
        postgame_subtitle = template.get('postgame_subtitle', '')
        if postgame_subtitle:
            subtitle_text = self._template_engine.resolve(postgame_subtitle, template_ctx)
            if subtitle_text:
                sub_title = ET.SubElement(programme, 'sub-title')
                sub_title.set('lang', 'en')
                sub_title.text = subtitle_text

        # Description - supports conditional logic based on game final status
        postgame_desc = self._get_postgame_description(template, event)
        if postgame_desc:
            desc_text = self._template_engine.resolve(postgame_desc, template_ctx)
            if desc_text and desc_text.strip():
                desc = ET.SubElement(programme, 'desc')
                desc.set('lang', 'en')
                desc.text = desc_text

        # Postgame art if available
        if template.get('postgame_art_url'):
            icon_url = self._template_engine.resolve(template['postgame_art_url'], template_ctx)
            if icon_url:
                icon = ET.SubElement(programme, 'icon')
                icon.set('src', icon_url)

        # Categories - respects categories_apply_to setting
        self._add_categories(programme, template, template_ctx, programme_type='postgame')

        # Flags - from template
        self._add_flags(programme, template)

    def _add_categories(
        self,
        programme,
        template: Optional[Dict],
        context: Dict = None,
        programme_type: str = 'game'
    ):
        """
        Add category elements from template, resolving any variables.

        Args:
            programme: XML programme element
            template: Template dict
            context: Variable resolution context
            programme_type: 'game', 'pregame', or 'postgame'
        """
        import xml.etree.ElementTree as ET
        import json

        if not template:
            return

        # Check categories_apply_to setting
        apply_to = template.get('categories_apply_to', 'all')
        if apply_to != 'all':
            # Parse apply_to - can be comma-separated: "game,pregame" or single: "game"
            allowed_types = [t.strip().lower() for t in apply_to.split(',')]
            if programme_type not in allowed_types:
                return  # Categories don't apply to this programme type

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

        for cat in categories:
            if cat:  # Skip empty strings
                # Resolve any template variables in category
                resolved_cat = cat
                if context and '{' in cat:
                    resolved_cat = self._template_engine.resolve(cat, context)
                cat_elem = ET.SubElement(programme, 'category')
                cat_elem.set('lang', 'en')
                cat_elem.text = resolved_cat

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
    template: Dict = None
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
            template=template
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
        all_channels = []
        all_programmes = []

        for file_path in file_paths:
            if not os.path.exists(file_path):
                logger.warning(f"Skipping missing file: {file_path}")
                continue

            try:
                tree = ET.parse(file_path)
                root = tree.getroot()

                # Collect channels (skip duplicates)
                for channel in root.findall('channel'):
                    channel_id = channel.get('id')
                    if channel_id and channel_id not in seen_channels:
                        all_channels.append(channel)
                        seen_channels.add(channel_id)

                # Collect all programmes
                for programme in root.findall('programme'):
                    all_programmes.append(programme)

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
