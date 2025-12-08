"""
Event Template Engine for Dispatcharr Event Channels

Resolves template variables for event-based EPG generation.
Unlike the team-based TemplateEngine, this engine:
- Has no concept of "our team" - variables are positional (home/away)
- Focuses on event-specific variables (scores, results, event name)
- Does not support .next/.last suffixes (each stream = one event)
"""

import re
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from utils import to_pascal_case
from utils.time_format import format_time as fmt_time, get_time_settings

logger = logging.getLogger(__name__)


class EventTemplateEngine:
    """
    Resolves template variables for event-based EPG channels.

    Variables are positional (home_team/away_team) rather than
    perspective-based (team/opponent) like the team-based engine.
    """

    def __init__(self):
        pass

    # Variables that should be gracefully removed with surrounding chars when empty
    OPTIONAL_VARS = {'exception_keyword', 'exception_keyword_title'}

    def resolve(self, template: str, context: Dict[str, Any]) -> str:
        """
        Resolve all template variables in a string.

        Args:
            template: String with {variable} placeholders
            context: Dictionary containing event data

        Returns:
            String with all variables replaced with actual values
        """
        if not template:
            return ""

        # Build all variables from context
        variables = self._build_variable_dict(context)

        # First pass: Remove optional variables with their surrounding brackets/parens when empty
        # Pattern matches: (optional_var), [optional_var], or just the var with surrounding spaces
        for var_name in self.OPTIONAL_VARS:
            var_value = variables.get(var_name, '')
            if not var_value:
                # Remove patterns like "({var})" or "( {var} )" or "[ {var} ]" etc.
                template = re.sub(
                    r'\s*[\(\[]\s*\{' + var_name + r'\}\s*[\)\]]\s*',
                    '',
                    template,
                    flags=re.IGNORECASE
                )
                # Also remove standalone " - {var}" or " {var}" patterns
                template = re.sub(
                    r'\s*[-–—]\s*\{' + var_name + r'\}',
                    '',
                    template,
                    flags=re.IGNORECASE
                )

        # Second pass: Replace all remaining {variable} patterns
        pattern = r'\{([a-z_][a-z0-9_]*)\}'

        def replace_variable(match):
            var_name = match.group(1)
            var_value = variables.get(var_name, '')
            return str(var_value)

        result = re.sub(pattern, replace_variable, template, flags=re.IGNORECASE)

        # Clean up any double spaces left behind
        result = re.sub(r'  +', ' ', result).strip()

        return result

    def _build_variable_dict(self, context: Dict[str, Any]) -> Dict[str, str]:
        """
        Build complete dictionary of event variables.

        Args:
            context: Event context containing:
                - event: ESPN event data
                - stream: Dispatcharr stream info
                - group_info: Event EPG group configuration

        Returns:
            Dictionary of variable_name: value pairs
        """
        variables = {}

        event = context.get('event', {}) or {}
        stream = context.get('stream', {}) or {}
        group_info = context.get('group_info', {}) or {}
        epg_timezone = context.get('epg_timezone', 'America/Detroit')
        time_format_settings = context.get('time_format_settings', {})

        # Extract team data
        home_team = event.get('home_team', {}) or {}
        away_team = event.get('away_team', {}) or {}
        venue = event.get('venue', {}) or {}
        status = event.get('status', {}) or {}

        # =====================================================================
        # EVENT IDENTIFICATION
        # =====================================================================

        variables['event_name'] = event.get('short_name') or event.get('name', '')
        variables['matchup'] = f"{away_team.get('name', '')} @ {home_team.get('name', '')}"
        variables['matchup_abbrev'] = f"{away_team.get('abbrev', '')} @ {home_team.get('abbrev', '')}"

        # =====================================================================
        # HOME TEAM VARIABLES
        # =====================================================================

        variables['home_team'] = home_team.get('name', '')
        variables['home_team_abbrev'] = home_team.get('abbrev', '')
        variables['home_team_abbrev_lower'] = variables['home_team_abbrev'].lower()
        variables['home_team_pascal'] = to_pascal_case(variables['home_team'])
        variables['home_team_logo'] = home_team.get('logo', '')

        # Home team record
        home_record = home_team.get('record', {})
        if isinstance(home_record, dict):
            variables['home_team_record'] = home_record.get('summary', home_record.get('displayValue', ''))
        else:
            variables['home_team_record'] = str(home_record) if home_record else ''

        # Home team conference/division (from enrich_with_team_stats)
        variables['home_team_college_conference'] = home_team.get('college_conference', '')
        variables['home_team_college_conference_abbrev'] = home_team.get('college_conference_abbrev', '')
        variables['home_team_pro_conference'] = home_team.get('pro_conference', '')
        variables['home_team_pro_conference_abbrev'] = home_team.get('pro_conference_abbrev', '')
        variables['home_team_pro_division'] = home_team.get('pro_division', '')

        # Home team rank/seed/streak (from enrich_with_team_stats)
        variables['home_team_rank'] = home_team.get('rank', '')
        variables['home_team_seed'] = home_team.get('seed', '')
        variables['home_team_streak'] = home_team.get('streak', '')

        # =====================================================================
        # AWAY TEAM VARIABLES
        # =====================================================================

        variables['away_team'] = away_team.get('name', '')
        variables['away_team_abbrev'] = away_team.get('abbrev', '')
        variables['away_team_abbrev_lower'] = variables['away_team_abbrev'].lower()
        variables['away_team_pascal'] = to_pascal_case(variables['away_team'])
        variables['away_team_logo'] = away_team.get('logo', '')

        # Away team record
        away_record = away_team.get('record', {})
        if isinstance(away_record, dict):
            variables['away_team_record'] = away_record.get('summary', away_record.get('displayValue', ''))
        else:
            variables['away_team_record'] = str(away_record) if away_record else ''

        # Away team conference/division (from enrich_with_team_stats)
        variables['away_team_college_conference'] = away_team.get('college_conference', '')
        variables['away_team_college_conference_abbrev'] = away_team.get('college_conference_abbrev', '')
        variables['away_team_pro_conference'] = away_team.get('pro_conference', '')
        variables['away_team_pro_conference_abbrev'] = away_team.get('pro_conference_abbrev', '')
        variables['away_team_pro_division'] = away_team.get('pro_division', '')

        # Away team rank/seed/streak (from enrich_with_team_stats)
        variables['away_team_rank'] = away_team.get('rank', '')
        variables['away_team_seed'] = away_team.get('seed', '')
        variables['away_team_streak'] = away_team.get('streak', '')

        # =====================================================================
        # SPORT AND LEAGUE
        # For multi-sport groups, use the event's sport/league (detected per-stream)
        # Fall back to group's assigned values for single-sport groups
        # =====================================================================

        # Get sport from event first (for multi-sport), then group (for single-sport)
        sport_code = event.get('sport', '') or group_info.get('assigned_sport', '')
        sport_display_names = {
            'basketball': 'Basketball',
            'football': 'Football',
            'hockey': 'Hockey',
            'baseball': 'Baseball',
            'soccer': 'Soccer'
        }
        variables['sport'] = sport_display_names.get(sport_code, sport_code.capitalize())

        # Get league from event first (for multi-sport), then group (for single-sport)
        # event['league'] contains the ESPN slug (e.g., 'aus.1', 'eng.1', 'nfl')
        event_league = event.get('league', '') or group_info.get('assigned_league', '')

        # {league_id} - Check aliases table for friendly name, fallback to ESPN slug
        # This ensures consistent output whether from single-sport or multi-sport groups
        # Convert ESPN slug to friendly alias for display (e.g., 'womens-college-basketball' -> 'ncaaw')
        from database import get_league_alias
        variables['league_id'] = get_league_alias(event_league.lower()) if event_league else ''

        # Look up the display name for the league
        # For soccer: use soccer_leagues_cache (e.g., 'aus.1' -> 'Australian A-League Men')
        # For US sports: use league_config.league_name (e.g., 'nfl' -> 'NFL')
        league_display_name = ''
        if event_league:
            if sport_code == 'soccer':
                # Soccer leagues use the soccer cache (240+ leagues)
                from epg.soccer_multi_league import SoccerMultiLeague
                league_display_name = SoccerMultiLeague.get_league_name(event_league)

            # If not found in soccer cache (or not soccer), try league_config
            if not league_display_name:
                from database import get_connection
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    result = cursor.execute(
                        "SELECT league_name FROM league_config WHERE league_code = ?",
                        (event_league.lower(),)
                    ).fetchone()
                    conn.close()
                    if result and result[0]:
                        league_display_name = result[0]
                except Exception:
                    pass

        # {league} shows display name if available, else uppercase code
        variables['league'] = league_display_name or event_league.upper()
        # {league_name} is the full display name (e.g., "English Premier League")
        variables['league_name'] = league_display_name

        # =====================================================================
        # DATE & TIME
        # =====================================================================

        game_date_str = event.get('date', '')
        if game_date_str:
            try:
                from zoneinfo import ZoneInfo
                game_datetime = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                local_datetime = game_datetime.astimezone(ZoneInfo(epg_timezone))

                variables['game_date'] = local_datetime.strftime('%A, %B %d, %Y')
                variables['game_date_short'] = local_datetime.strftime('%b %d')

                # Use user's time format preferences for game_time
                if time_format_settings:
                    tf, show_tz = get_time_settings(time_format_settings)
                    variables['game_time'] = fmt_time(local_datetime, tf, show_tz)
                else:
                    variables['game_time'] = local_datetime.strftime('%I:%M %p %Z')

                variables['game_day'] = local_datetime.strftime('%A')
                variables['game_day_short'] = local_datetime.strftime('%a')
                variables['today_tonight'] = 'tonight' if local_datetime.hour >= 17 else 'today'
                variables['today_tonight_title'] = 'Tonight' if local_datetime.hour >= 17 else 'Today'

            except Exception as e:
                logger.debug(f"Could not parse event date: {e}")

        # =====================================================================
        # VENUE
        # =====================================================================

        venue_name = venue.get('name') or venue.get('fullName', '')
        venue_city = venue.get('city') or venue.get('address', {}).get('city', '')
        venue_state = venue.get('state') or venue.get('address', {}).get('state', '')

        variables['venue'] = venue_name
        variables['venue_city'] = venue_city
        variables['venue_state'] = venue_state

        if venue_name and venue_city and venue_state:
            variables['venue_full'] = f"{venue_name}, {venue_city}, {venue_state}"
        elif venue_name and venue_city:
            variables['venue_full'] = f"{venue_name}, {venue_city}"
        else:
            variables['venue_full'] = venue_name

        # =====================================================================
        # SCORES (for completed/live games)
        # =====================================================================

        home_score_raw = home_team.get('score', 0)
        away_score_raw = away_team.get('score', 0)

        # Extract numeric score
        if isinstance(home_score_raw, dict):
            home_score = int(home_score_raw.get('value', 0) or home_score_raw.get('displayValue', '0') or 0)
        else:
            home_score = int(home_score_raw) if home_score_raw else 0

        if isinstance(away_score_raw, dict):
            away_score = int(away_score_raw.get('value', 0) or away_score_raw.get('displayValue', '0') or 0)
        else:
            away_score = int(away_score_raw) if away_score_raw else 0

        variables['home_team_score'] = str(home_score)
        variables['away_team_score'] = str(away_score)

        # =====================================================================
        # EVENT RESULT (for completed games)
        # =====================================================================

        is_final = status.get('name', '') in ['STATUS_FINAL', 'Final'] or status.get('state', '') == 'post'

        if is_final and (home_score > 0 or away_score > 0):
            # Full result: "Giants 24 - Patriots 17"
            variables['event_result'] = f"{home_team.get('name', '')} {home_score} - {away_team.get('name', '')} {away_score}"
            # Abbreviated result: "NYG 24 - NE 17"
            variables['event_result_abbrev'] = f"{home_team.get('abbrev', '')} {home_score} - {away_team.get('abbrev', '')} {away_score}"

            # Winner/Loser variables
            if home_score > away_score:
                variables['winner'] = home_team.get('name', '')
                variables['winner_abbrev'] = home_team.get('abbrev', '')
                variables['loser'] = away_team.get('name', '')
                variables['loser_abbrev'] = away_team.get('abbrev', '')
            elif away_score > home_score:
                variables['winner'] = away_team.get('name', '')
                variables['winner_abbrev'] = away_team.get('abbrev', '')
                variables['loser'] = home_team.get('name', '')
                variables['loser_abbrev'] = home_team.get('abbrev', '')
            else:
                # Tie
                variables['winner'] = 'Tie'
                variables['winner_abbrev'] = 'TIE'
                variables['loser'] = 'Tie'
                variables['loser_abbrev'] = 'TIE'

            # Check for overtime - compare periods to regulation threshold per sport
            periods = status.get('period', 0) or 0
            overtime_thresholds = {
                'basketball': 4,  # NBA/NCAAM = 4 quarters/halves
                'hockey': 3,      # NHL = 3 periods
                'football': 4,    # NFL/NCAAF = 4 quarters
                'baseball': 9     # MLB = 9 innings
            }
            overtime_threshold = overtime_thresholds.get(sport_code, 4)

            if periods > overtime_threshold:
                variables['overtime_text'] = 'in overtime'
            else:
                variables['overtime_text'] = ''
        else:
            # Game not final - empty results
            variables['event_result'] = ''
            variables['event_result_abbrev'] = ''
            variables['winner'] = ''
            variables['winner_abbrev'] = ''
            variables['loser'] = ''
            variables['loser_abbrev'] = ''
            variables['overtime_text'] = ''

        # =====================================================================
        # BROADCAST
        # =====================================================================

        broadcasts = event.get('broadcasts', [])
        if broadcasts:
            # Normalize broadcasts - handle both string list and dict list formats
            # ESPN can return [{"names": ["ESPN"]}] or ["ESPN"] depending on code path
            broadcast_names = []
            for b in broadcasts:
                if b is None:
                    continue
                if isinstance(b, str):
                    broadcast_names.append(b)
                elif isinstance(b, dict):
                    # Handle {"names": ["ESPN"]} or {"name": "ESPN"} format
                    names = b.get('names', [])
                    if names:
                        broadcast_names.extend(names)
                    elif b.get('name'):
                        broadcast_names.append(b['name'])

            variables['broadcast_simple'] = ', '.join(broadcast_names[:3])
            variables['broadcast_network'] = broadcast_names[0] if broadcast_names else ''
        else:
            variables['broadcast_simple'] = ''
            variables['broadcast_network'] = ''

        # =====================================================================
        # STATUS
        # =====================================================================

        variables['status_detail'] = status.get('detail', '')
        variables['status_state'] = status.get('state', 'pre')
        variables['is_final'] = 'true' if is_final else 'false'

        # =====================================================================
        # ODDS
        # =====================================================================

        odds = event.get('odds', {}) or {}
        variables['odds_spread'] = str(odds.get('spread', '')) if odds.get('spread') else ''
        variables['odds_over_under'] = str(odds.get('over_under', '')) if odds.get('over_under') else ''
        variables['odds_provider'] = odds.get('provider', '') or ''
        variables['odds_details'] = str(odds.get('spread', '')) if odds.get('spread') else ''  # Same as spread

        # Moneyline - for event-based, use home as primary perspective
        home_ml = odds.get('home_moneyline')
        away_ml = odds.get('away_moneyline')
        variables['odds_moneyline'] = str(home_ml) if home_ml else ''
        variables['odds_opponent_moneyline'] = str(away_ml) if away_ml else ''
        variables['odds_opponent_spread'] = ''  # Not available in current data extraction

        # =====================================================================
        # WEATHER (outdoor venues)
        # =====================================================================

        weather = event.get('weather', {}) or {}
        variables['weather'] = weather.get('display', '')

        # =====================================================================
        # STREAM INFO
        # =====================================================================

        variables['stream_name'] = stream.get('name', '')
        variables['stream_id'] = str(stream.get('id', ''))

        # Channel ID (tvg_id) - use ESPN event ID for consistency with channel creation
        # Format: teamarr-event-{espn_event_id}
        if event.get('id'):
            variables['channel_id'] = f"teamarr-event-{event['id']}"
        else:
            variables['channel_id'] = stream.get('tvg_id') or f"event-{stream.get('id', 'unknown')}"

        # =====================================================================
        # EXCEPTION KEYWORD (for sub-consolidation)
        # =====================================================================

        exception_keyword = context.get('exception_keyword', '')
        variables['exception_keyword'] = exception_keyword or ''
        # Title case version for display (e.g., "Prime Vision")
        variables['exception_keyword_title'] = exception_keyword.title() if exception_keyword else ''

        return variables

    def select_description(self, description_options: Any, context: Dict[str, Any]) -> str:
        """
        Select the best description template based on conditional logic.

        Simplified version for events - mainly uses fallback descriptions
        since events don't have the same complex conditions as team channels.

        Args:
            description_options: JSON string or list of description options
            context: Event context for evaluation

        Returns:
            Selected description template string
        """
        # Parse description_options if it's a JSON string
        if isinstance(description_options, str):
            try:
                options = json.loads(description_options) if description_options else []
            except:
                return ''
        elif isinstance(description_options, list):
            options = description_options
        else:
            return ''

        if not options:
            return ''

        # For events, prioritize by priority value (lower = higher priority)
        # Filter to options that have templates
        valid_options = [opt for opt in options if opt.get('template')]

        if not valid_options:
            return ''

        # Sort by priority (default 50 if not specified)
        valid_options.sort(key=lambda x: x.get('priority', 50))

        # For now, just return the highest priority template
        # Future: Add condition evaluation for event-specific conditions
        return valid_options[0]['template']


def build_event_context(
    event: Dict,
    stream: Dict,
    group_info: Dict,
    epg_timezone: str = 'America/Detroit',
    time_format_settings: Dict = None,
    exception_keyword: str = None
) -> Dict[str, Any]:
    """
    Build context dictionary for event template resolution.

    Args:
        event: ESPN event data
        stream: Dispatcharr stream info
        group_info: Event EPG group configuration
        epg_timezone: Timezone for display
        time_format_settings: User's time format preferences (time_format, show_timezone)
        exception_keyword: Optional matched exception keyword for sub-consolidation

    Returns:
        Context dictionary ready for EventTemplateEngine.resolve()
    """
    return {
        'event': event,
        'stream': stream,
        'group_info': group_info,
        'epg_timezone': epg_timezone,
        'time_format_settings': time_format_settings or {},
        'exception_keyword': exception_keyword
    }
