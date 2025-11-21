"""XMLTV EPG Generator following Gracenote best practices"""
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
import hashlib

class XMLTVGenerator:
    """Generate XMLTV format EPG files"""

    def __init__(self, generator_name: str = "Teamarr Sports EPG Generator",
                 generator_url: str = "http://localhost:9195"):
        self.generator_name = generator_name
        self.generator_url = generator_url

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
        # Create root element
        tv = ET.Element('tv')
        tv.set('generator-info-name', self.generator_name)
        tv.set('generator-info-url', self.generator_url)

        # Add channels (one per team)
        for team in teams:
            self._add_channel(tv, team)

        # Add programmes for each team
        for team in teams:
            team_events = events.get(str(team['id']), [])
            for event in team_events:
                self._add_programme(tv, team, event)

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

    def _add_programme(self, parent: ET.Element, team: Dict, event: Dict):
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
            for category in team.get('categories', []):
                if category not in added_categories:
                    self._add_category(programme, category)
                    added_categories.add(category)

        # Date (program air date in YYYYMMDD format)
        flags = team.get('flags', {})
        if flags.get('date', False):
            date_elem = ET.SubElement(programme, 'date')
            # Use the start date of the event
            date_elem.text = event['start_datetime'].strftime('%Y%m%d')

        # Icon (team logo)
        if team.get('team_logo_url'):
            icon = ET.SubElement(programme, 'icon')
            icon.set('src', team['team_logo_url'])

        # Flags (Gracenote: both new AND live for upcoming live events)
        game_status = event.get('status', 'scheduled')

        if game_status == 'scheduled':
            if flags.get('new', True):
                ET.SubElement(programme, 'new')
            if flags.get('live', True):
                # Gracenote includes <live/> for upcoming live events
                ET.SubElement(programme, 'live')

        elif game_status in ['in_progress', 'halftime']:
            # Game currently happening
            ET.SubElement(programme, 'live')

        if flags.get('premiere', False):
            ET.SubElement(programme, 'premiere')

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
        """Add XML declaration and DOCTYPE"""
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
