"""XMLTV EPG Generator following Gracenote best practices"""
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
import hashlib

class XMLTVGenerator:
    """Generate XMLTV format EPG files"""

    def __init__(self, generator_name: str = "Teamarr - Dynamic EPG Generator for Sports Channels",
                 generator_url: str = "http://localhost:9195",
                 version: str = "1.0.0"):
        self.generator_name = generator_name
        self.generator_url = generator_url
        self.version = version

    def generate(self, teams: List[Dict], events: Dict[str, List[Dict]],
                 settings: Dict) -> str:
        """
        Generate complete XMLTV document

        Args:
            teams: List of team configurations
            events: Dict mapping team_id to list of events
            settings: Global settings

        Returns:
            XMLTV XML string
        """
        # Create root element (no watermark - consolidator handles that)
        tv = ET.Element('tv')

        # Add channels (one per team)
        for team in teams:
            self._add_channel(tv, team)

        # Add programmes for each team
        for team in teams:
            team_events = events.get(str(team['id']), [])
            for event in team_events:
                self._add_programme(tv, team, event, settings)

        # Convert to pretty XML string
        xml_str = self._prettify(tv)

        # Add DOCTYPE
        xml_with_doctype = self._add_doctype(xml_str)

        return xml_with_doctype

    def _add_channel(self, parent: ET.Element, team: Dict):
        """Add channel element for a team"""
        channel = ET.SubElement(parent, 'channel')
        channel.set('id', team['channel_id'])

        # Display name
        display_name = ET.SubElement(channel, 'display-name')
        display_name.text = team['team_name']

        # Icon (team logo)
        if team.get('team_logo_url'):
            icon = ET.SubElement(channel, 'icon')
            icon.set('src', team['team_logo_url'])

    def _add_programme(self, parent: ET.Element, team: Dict, event: Dict, settings: Dict):
        """
        Add programme element for a game/event

        Following Gracenote best practices:
        - Generic sport titles ("NFL Football")
        - Specific matchup in sub-title
        - Both <new/> and <live/> flags for upcoming live events
        - Rich descriptions with context
        """
        # Calculate start/stop times
        start_time = self._format_xmltv_time(event['start_datetime'])
        stop_time = self._format_xmltv_time(event['end_datetime'])

        programme = ET.SubElement(parent, 'programme')
        programme.set('start', start_time)
        programme.set('stop', stop_time)
        programme.set('channel', team['channel_id'])

        # Title (resolved template)
        title = ET.SubElement(programme, 'title')
        title.set('lang', 'en')
        title.text = event.get('title', f"{team['team_name']} Game")

        # Sub-title (matchup or venue)
        if event.get('subtitle'):
            sub_title = ET.SubElement(programme, 'sub-title')
            sub_title.set('lang', 'en')
            sub_title.text = event['subtitle']

        # Description (rich context)
        if event.get('description'):
            desc = ET.SubElement(programme, 'desc')
            desc.set('lang', 'en')
            desc.text = event['description']

        # Categories - only user-defined categories
        # Check if categories should be applied based on team settings and event type
        categories_apply_to = team.get('categories_apply_to', 'events')
        is_filler = event.get('status') == 'filler'

        # Apply categories based on setting:
        # 'all' = apply to all programs (events and filler)
        # 'events' = apply only to actual game events (not filler)
        should_add_categories = (categories_apply_to == 'all') or (categories_apply_to == 'events' and not is_filler)

        if should_add_categories:
            added_categories = set()
            # Get template variables from event context for variable resolution
            template_vars = event.get('context', {})

            categories = team.get('categories') or []
            for category in categories:
                # Resolve template variables in category (e.g., {sport} -> Basketball)
                resolved_category = category
                if '{' in category:
                    # Simple template variable replacement
                    import re
                    for var_name, var_value in template_vars.items():
                        resolved_category = resolved_category.replace(f'{{{var_name}}}', str(var_value))

                if resolved_category not in added_categories:
                    self._add_category(programme, resolved_category)
                    added_categories.add(resolved_category)

        # Date (program air date in YYYYMMDD format)
        flags = team.get('flags', {})
        if flags.get('date', False):
            date_elem = ET.SubElement(programme, 'date')
            # Use the start date of the event in user's timezone
            # Convert from UTC to user's timezone for correct date
            from zoneinfo import ZoneInfo
            user_tz = settings.get('default_timezone', 'America/Detroit')
            local_dt = event['start_datetime'].astimezone(ZoneInfo(user_tz))
            date_elem.text = local_dt.strftime('%Y%m%d')

        # Icon (program art URL takes priority, fallback to team logo)
        icon_url = event.get('program_art_url') or team.get('team_logo_url')
        if icon_url:
            icon = ET.SubElement(programme, 'icon')
            icon.set('src', icon_url)

        # Flags - only apply to actual events, not filler programmes
        if not is_filler:
            if flags.get('new', False):
                ET.SubElement(programme, 'new')
            if flags.get('live', False):
                ET.SubElement(programme, 'live')

        # Teamarr metadata (invisible to EPG readers, used internally)
        # Format: teamarr:teams-event, teamarr:teams-filler-pregame, etc.
        if is_filler:
            filler_type = event.get('filler_type', 'idle')
            programme.append(ET.Comment(f"teamarr:teams-filler-{filler_type}"))
        else:
            programme.append(ET.Comment("teamarr:teams-event"))

    def _add_category(self, programme: ET.Element, category: str):
        """Add category element"""
        cat = ET.SubElement(programme, 'category')
        cat.set('lang', 'en')
        cat.text = category

    def _format_xmltv_time(self, dt: datetime) -> str:
        """
        Format datetime to XMLTV format: YYYYMMDDHHmmss +0000

        Args:
            dt: datetime object (with timezone)

        Returns:
            XMLTV formatted time string
        """
        # Convert to UTC
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)

        return dt.strftime('%Y%m%d%H%M%S +0000')

    def _prettify(self, elem: ET.Element) -> str:
        """Return pretty-printed XML string"""
        rough_string = ET.tostring(elem, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        pretty = reparsed.toprettyxml(indent='  ')

        # Remove extra blank lines
        lines = [line for line in pretty.split('\n') if line.strip()]

        # Remove XML declaration (we'll add it back with DOCTYPE)
        if lines and lines[0].startswith('<?xml'):
            lines = lines[1:]

        return '\n'.join(lines)

    def _add_doctype(self, xml_str: str) -> str:
        """Add XML declaration and DOCTYPE (watermark added by consolidator)"""
        declaration = '<?xml version="1.0" encoding="UTF-8"?>'
        doctype = '<!DOCTYPE tv SYSTEM "xmltv.dtd">'

        return f"{declaration}\n{doctype}\n{xml_str}"

    def calculate_file_hash(self, xml_content: str) -> str:
        """Calculate SHA256 hash of XML content for change detection"""
        return hashlib.sha256(xml_content.encode('utf-8')).hexdigest()

    def validate_xmltv(self, xml_content: str) -> bool:
        """
        Basic validation of XMLTV structure

        Returns:
            True if valid, False otherwise
        """
        try:
            root = ET.fromstring(xml_content)

            # Must have tv root element
            if root.tag != 'tv':
                return False

            # Must have at least one channel
            channels = root.findall('channel')
            if not channels:
                return False

            # All channels must have id
            for channel in channels:
                if not channel.get('id'):
                    return False

            # All programmes must have start, stop, channel
            programmes = root.findall('programme')
            for prog in programmes:
                if not prog.get('start') or not prog.get('stop') or not prog.get('channel'):
                    return False

            return True

        except ET.ParseError:
            return False
