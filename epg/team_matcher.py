"""
Team Matcher for Event Channel EPG

Extracts team names from stream/channel names and matches them to ESPN teams.
Uses dynamic team data fetched from ESPN rather than hardcoded lists.

Key Features:
- Dynamic team database from ESPN (handles relegation/promotion)
- User-defined aliases for edge cases (e.g., "Spurs" → "Tottenham Hotspur")
- Normalizes messy stream names to extract team matchups
- Separator detection (vs, at, @, v)
- Date extraction for disambiguating multiple matchups
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple

from epg.league_config import get_league_config, parse_api_path, is_college_league
from utils.logger import get_logger

logger = get_logger(__name__)


def extract_date_from_text(text: str) -> Optional[datetime]:
    """
    Extract a date from stream name text.

    Handles common formats:
    - ISO: 2025-11-30, 2025-11-30T18:00
    - US: 11/30, 11/30/2025, 11/30/25
    - Text: Nov 30, November 30
    - With parens: (2025-11-30)

    Args:
        text: Raw text that may contain a date

    Returns:
        datetime object (date only, no time) or None
    """
    import re
    from datetime import datetime

    # Current year for relative dates
    current_year = datetime.now().year

    # Pattern 1: ISO format (2025-11-30) or with time (2025-11-30T18:00:05)
    iso_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if iso_match:
        try:
            return datetime(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3))
            )
        except ValueError:
            pass

    # Pattern 2: US format with year (11/30/2025 or 11/30/25)
    us_full_match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', text)
    if us_full_match:
        try:
            year = int(us_full_match.group(3))
            if year < 100:
                year += 2000
            return datetime(
                year,
                int(us_full_match.group(1)),
                int(us_full_match.group(2))
            )
        except ValueError:
            pass

    # Pattern 3: US format without year (11/30)
    us_short_match = re.search(r'(\d{1,2})/(\d{1,2})(?!\d)', text)
    if us_short_match:
        try:
            month = int(us_short_match.group(1))
            day = int(us_short_match.group(2))
            # Assume current or next year
            date = datetime(current_year, month, day)
            # If date is more than 6 months in the past, assume next year
            if (datetime.now() - date).days > 180:
                date = datetime(current_year + 1, month, day)
            return date
        except ValueError:
            pass

    # Pattern 4: Text month format (Nov 30, November 30)
    month_names = {
        'jan': 1, 'january': 1,
        'feb': 2, 'february': 2,
        'mar': 3, 'march': 3,
        'apr': 4, 'april': 4,
        'may': 5,
        'jun': 6, 'june': 6,
        'jul': 7, 'july': 7,
        'aug': 8, 'august': 8,
        'sep': 9, 'september': 9,
        'oct': 10, 'october': 10,
        'nov': 11, 'november': 11,
        'dec': 12, 'december': 12
    }

    text_month_match = re.search(
        r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
        r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
        r'\s+(\d{1,2})',
        text.lower()
    )
    if text_month_match:
        try:
            month = month_names.get(text_month_match.group(1).lower())
            day = int(text_month_match.group(2))
            if month:
                date = datetime(current_year, month, day)
                if (datetime.now() - date).days > 180:
                    date = datetime(current_year + 1, month, day)
                return date
        except ValueError:
            pass

    return None


def extract_time_from_text(text: str) -> Optional[datetime]:
    """
    Extract a time from stream name text.

    Handles common formats:
    - 12-hour: 8:15PM, 8:15 PM, 1:00 PM ET
    - 24-hour: 18:00, 20:15
    - With timezone: 8:15PM ET, 1:00 PM EST

    Args:
        text: Raw text that may contain a time

    Returns:
        datetime object with time (date is today) or None
    """
    from datetime import datetime

    # Pattern 1: 12-hour format (8:15PM, 8:15 PM, 1:00PM)
    time_12h_match = re.search(
        r'(\d{1,2}):(\d{2})\s*(am|pm)',
        text.lower()
    )
    if time_12h_match:
        try:
            hour = int(time_12h_match.group(1))
            minute = int(time_12h_match.group(2))
            is_pm = time_12h_match.group(3) == 'pm'

            if is_pm and hour != 12:
                hour += 12
            elif not is_pm and hour == 12:
                hour = 0

            return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            pass

    # Pattern 2: 24-hour format (18:00, 20:15)
    time_24h_match = re.search(r'(\d{2}):(\d{2})(?::\d{2})?(?!\d)', text)
    if time_24h_match:
        try:
            hour = int(time_24h_match.group(1))
            minute = int(time_24h_match.group(2))
            if 0 <= hour < 24 and 0 <= minute < 60:
                return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            pass

    return None


class TeamMatcher:
    """
    Match team names from stream/channel names to ESPN team IDs.

    Uses ESPN API to fetch teams dynamically, with caching to reduce API calls.
    Also supports user-defined aliases stored in the database.

    Usage:
        from api.espn_client import ESPNClient
        from epg.team_matcher import TeamMatcher

        espn = ESPNClient()
        matcher = TeamMatcher(espn)

        result = matcher.extract_teams("NFL | 16 -8:15PM Giants at Patriots", "nfl")
        # Returns: {
        #     'matched': True,
        #     'away_team_id': '19',
        #     'away_team_name': 'New York Giants',
        #     'home_team_id': '17',
        #     'home_team_name': 'New England Patriots'
        # }
    """

    # Team vs team separators (order matters - check longer ones first)
    SEPARATORS = [' vs. ', ' vs ', ' at ', ' @ ', ' v. ', ' v ']

    # Cache duration for team lists (1 hour)
    CACHE_DURATION = timedelta(hours=1)

    def __init__(self, espn_client, db_connection_func=None):
        """
        Initialize TeamMatcher.

        Args:
            espn_client: ESPNClient instance for fetching team data
            db_connection_func: Function that returns a database connection
                               (for alias lookups). If None, aliases won't be used.
        """
        self.espn = espn_client
        self.db_connection_func = db_connection_func

        # Cache: {league_code: {'teams': [...], 'fetched_at': datetime}}
        self._team_cache: Dict[str, Dict] = {}

        # League config cache (from database)
        self._league_config: Dict[str, Dict] = {}

    def _get_league_config(self, league_code: str) -> Optional[Dict]:
        """
        Get league configuration (sport, api_path) using shared module.

        Args:
            league_code: League code (e.g., 'nfl', 'epl')

        Returns:
            Dict with league config or None if not found
        """
        return get_league_config(league_code, self.db_connection_func, self._league_config)

    def _get_teams_for_league(self, league_code: str) -> List[Dict]:
        """
        Get all teams for a league, using cache when available.

        Fetches from ESPN API and caches for CACHE_DURATION.
        College leagues use conference-based fetching to get all teams.

        Args:
            league_code: League code (e.g., 'nfl', 'epl', 'ncaam')

        Returns:
            List of team dicts with id, name, abbreviation, shortName, slug
        """
        league_lower = league_code.lower()

        # Check cache
        if league_lower in self._team_cache:
            cached = self._team_cache[league_lower]
            if datetime.now() - cached['fetched_at'] < self.CACHE_DURATION:
                return cached['teams']

        # Get league config
        config = self._get_league_config(league_lower)
        if not config:
            logger.warning(f"No league config found for {league_code}")
            return []

        # Parse sport and league from api_path (e.g., "basketball/nba" -> "basketball", "nba")
        sport, league = parse_api_path(config['api_path'])
        if not sport or not league:
            return []

        # College leagues need conference-based fetching
        if is_college_league(league_lower) or is_college_league(league):
            teams = self._fetch_college_teams(sport, league)
        else:
            # Pro leagues - simple team list
            logger.info(f"Fetching teams for {league_code} from ESPN API")
            teams = self.espn.get_league_teams(sport, league)

        if not teams:
            logger.warning(f"No teams returned for {league_code}")
            return []

        # Build search index with normalized names
        for team in teams:
            team['_search_names'] = self._build_search_names(team)

        # Cache results
        self._team_cache[league_lower] = {
            'teams': teams,
            'fetched_at': datetime.now()
        }

        logger.info(f"Cached {len(teams)} teams for {league_code}")
        return teams

    def _fetch_college_teams(self, sport: str, league: str) -> List[Dict]:
        """
        Fetch all teams for a college league by iterating through conferences.

        College sports have 300+ teams organized by conference, so we need
        to fetch each conference's teams separately.

        Args:
            sport: Sport (e.g., 'basketball', 'football')
            league: League identifier (e.g., 'mens-college-basketball')

        Returns:
            List of all team dicts
        """
        logger.info(f"Fetching college teams for {league} via conferences")

        # Get all conferences
        conferences = self.espn.get_league_conferences(sport, league)
        if not conferences:
            logger.warning(f"No conferences found for {league}")
            return []

        all_teams = []
        seen_ids = set()

        for conf in conferences:
            conf_id = conf.get('id')
            conf_name = conf.get('name', 'Unknown')

            if not conf_id:
                continue

            logger.debug(f"Fetching teams for conference: {conf_name}")
            conf_teams = self.espn.get_conference_teams(sport, league, conf_id)

            if conf_teams:
                for team in conf_teams:
                    team_id = team.get('id')
                    if team_id and team_id not in seen_ids:
                        seen_ids.add(team_id)
                        all_teams.append(team)

        logger.info(f"Fetched {len(all_teams)} college teams from {len(conferences)} conferences")
        return all_teams

    def _build_search_names(self, team: Dict) -> List[str]:
        """
        Build list of normalized search names for a team.

        Includes variations like:
        - Full name: "New York Giants"
        - Short name: "Giants"
        - Abbreviation: "NYG"
        - Slug: "new-york-giants"
        - City/region: "New York"
        - Nickname only: "giants"

        Args:
            team: Team dict from ESPN API

        Returns:
            List of lowercase normalized search strings
        """
        primary = set()    # Team-specific names (nickname, full name, abbreviation)
        secondary = set()  # Location-only names (city/region)

        # Full display name - PRIMARY (team-specific)
        if team.get('displayName'):
            primary.add(self._normalize_text(team['displayName']))

        # Team name (often just nickname like "Lakers") - PRIMARY
        if team.get('name'):
            primary.add(self._normalize_text(team['name']))

        # Short name (usually just nickname) - PRIMARY
        if team.get('shortName'):
            primary.add(self._normalize_text(team['shortName']))

        # Abbreviation - PRIMARY
        if team.get('abbreviation'):
            primary.add(team['abbreviation'].lower())

        # Slug - PRIMARY
        if team.get('slug'):
            primary.add(team['slug'].lower().replace('-', ' '))

        # Location - SECONDARY (can be shared between teams like LA Lakers/Clippers)
        if team.get('location'):
            secondary.add(self._normalize_text(team['location']))

        # Extract city from displayName if it has multiple words
        if team.get('displayName'):
            full_name = team['displayName']
            words = full_name.split()
            if len(words) > 2:
                # City is everything except the last word (nickname)
                potential_city = ' '.join(words[:-1])
                secondary.add(self._normalize_text(potential_city))

        # Store both lists - primary names searched first
        # Combine into single list with primary names first (order matters for tie-breaking)
        all_names = list(primary) + list(secondary)

        # Also store separately for priority matching
        team['_primary_names'] = list(primary)
        team['_secondary_names'] = list(secondary)

        return all_names

    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for matching.

        - Lowercase
        - Remove special characters
        - Normalize whitespace
        - Remove common prefixes/suffixes

        Args:
            text: Raw text string

        Returns:
            Normalized lowercase string
        """
        if not text:
            return ''

        text = text.lower()

        # Remove parenthetical content
        text = re.sub(r'\([^)]*\)', '', text)

        # Remove common channel prefixes (ncaa covers ncaaf, ncaam, ncaaw, ncaab)
        # Include optional colon after prefix (e.g., "NCAAM: Duke vs UNC")
        text = re.sub(r'^(nfl|nba|nhl|mlb|ncaa[mfwb]?|mls|epl|premier\s*league|soccer)\s*:?\s*', '', text, flags=re.I)

        # Remove "game pass", "on" prefixes
        text = re.sub(r'game\s*pass\s*\d*:?\s*', '', text, flags=re.I)
        text = re.sub(r'^on\s+', '', text, flags=re.I)

        # Remove times (e.g., "8:15PM", "01:00 PM ET", "1pm", "8pm")
        text = re.sub(r'\d{1,2}:\d{2}\s*(am|pm|et|est|pt|pst|ct|cst|mt|mst)?\s*', '', text, flags=re.I)
        # Also remove hour-only times like "1pm", "8pm", "12am"
        text = re.sub(r'\b\d{1,2}\s*(am|pm)\b\s*', '', text, flags=re.I)

        # Remove dates (e.g., "11/23", "2025-11-26", "Nov 26")
        text = re.sub(r'\d{1,2}/\d{1,2}(/\d{2,4})?\s*', '', text)
        text = re.sub(r'\d{4}-\d{2}-\d{2}\s*', '', text)
        text = re.sub(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{1,2}\s*', '', text, flags=re.I)

        # Remove channel numbers (e.g., "| 16 -", "05:")
        text = re.sub(r'\|\s*\d+\s*[-:]?\s*', '', text)
        text = re.sub(r'^\d+\s*[-:]?\s*', '', text)

        # Remove rankings (e.g., "#8 Alabama", "8 Alabama")
        text = re.sub(r'#?\d+\s+(?=[a-z])', '', text)

        # Remove special characters but keep spaces
        text = re.sub(r'[|:\-#\[\]]+', ' ', text)

        # Remove "FC", "SC", "CF" suffixes for soccer (keep as optional)
        # Don't remove - these are part of team names

        # Normalize whitespace
        text = ' '.join(text.split())

        return text.strip()

    def _normalize_for_stream(self, stream_name: str) -> str:
        """
        Normalize a stream name, removing everything except team names.

        More aggressive than _normalize_text - removes more noise
        that's common in IPTV stream names.

        Args:
            stream_name: Raw stream/channel name

        Returns:
            Cleaned string with just team matchup info
        """
        text = stream_name

        # Remove country/region prefixes like "(UK)", "(US)", "CA"
        text = re.sub(r'^\(?\s*(uk|us|usa|ca|au)\s*\)?[\s|:]*', '', text, flags=re.I)

        # Remove provider prefixes like "(Sky+ 11)", "(Dazn 070)", "(Peacock 023)"
        text = re.sub(r'\([^)]*(?:sky|dazn|peacock|tsn|sportsnet|espn|fox|nbc|cbs|abc)[^)]*\)', '', text, flags=re.I)

        # Remove "on TSN+:", "NBA on ESPN:", etc.
        text = re.sub(r'(nfl|nba|nhl|mlb|ncaa[mfwb]?|soccer|epl|mls)\s+on\s+\w+\s*:?\s*', '', text, flags=re.I)

        # Remove standalone league prefixes like "NCAA Basketball:", "NCAAM:", "College Basketball:"
        text = re.sub(r'^(ncaa[mfwb]?|college)\s*(basketball|football|hockey)?\s*:?\s*', '', text, flags=re.I)

        # Now apply standard normalization
        return self._normalize_text(text)

    def _find_separator(self, text: str) -> Tuple[Optional[str], int]:
        """
        Find the team separator in a normalized stream name.

        Args:
            text: Normalized stream name

        Returns:
            Tuple of (separator, position) or (None, -1) if not found
        """
        for sep in self.SEPARATORS:
            pos = text.find(sep)
            if pos > 0:  # Must have content before separator
                return (sep, pos)

        return (None, -1)

    def _find_team_in_text(self, text: str, teams: List[Dict]) -> Optional[Dict]:
        """
        Find a team match in the given text.

        Matching priority:
        1. Exact match with primary name (team-specific: nickname, full name, abbrev)
        2. Exact match with secondary name (location-only)
        3. Primary name appears as whole word in text
        4. Secondary name appears as whole word in text
        5. Prefix/substring matches (fallback)

        This ensures "los angeles clippers" matches Clippers (via "clippers")
        rather than Lakers (via "los angeles" location).

        Args:
            text: Normalized text to search in
            teams: List of team dicts with _search_names, _primary_names, _secondary_names

        Returns:
            Team dict or None
        """
        text = text.strip().lower()
        if not text:
            return None

        # Track matches by priority tier
        primary_word_match = None
        primary_word_length = 0
        secondary_word_match = None
        secondary_word_length = 0
        fallback_match = None
        fallback_length = 0

        for team in teams:
            # Check primary names first (team-specific: nickname, displayName, abbreviation)
            for search_name in team.get('_primary_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Exact match - immediate return
                if text == search_lower:
                    return team

                # Whole word match with primary name
                if len(search_lower) >= 3:
                    pattern = r'\b' + re.escape(search_lower) + r'\b'
                    if re.search(pattern, text):
                        if len(search_lower) > primary_word_length:
                            primary_word_match = team
                            primary_word_length = len(search_lower)

            # Check secondary names (location-only, can be shared between teams)
            for search_name in team.get('_secondary_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Exact match - immediate return
                if text == search_lower:
                    return team

                # Whole word match with secondary name
                if len(search_lower) >= 3:
                    pattern = r'\b' + re.escape(search_lower) + r'\b'
                    if re.search(pattern, text):
                        if len(search_lower) > secondary_word_length:
                            secondary_word_match = team
                            secondary_word_length = len(search_lower)

            # Fallback: check all search names for prefix/substring matches
            for search_name in team.get('_search_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Prefix match
                if text.startswith(search_lower) or search_lower.startswith(text):
                    match_len = max(len(search_lower), len(text))
                    if match_len > fallback_length:
                        fallback_match = team
                        fallback_length = match_len

        # Return by priority: primary word match beats secondary, which beats fallback
        if primary_word_match:
            return primary_word_match
        if secondary_word_match:
            return secondary_word_match
        if fallback_match:
            return fallback_match

        return None

    def _find_all_teams_in_text(self, text: str, teams: List[Dict]) -> List[Tuple[Dict, int, int]]:
        """
        Find all team matches in the given text with their positions.

        Used for separator-less matching where we need to find two teams
        anywhere in the stream name.

        Args:
            text: Normalized text to search in
            teams: List of team dicts with _search_names

        Returns:
            List of (team_dict, start_pos, match_length) tuples, sorted by position
        """
        text_lower = text.lower()
        matches = []
        seen_team_ids = set()

        for team in teams:
            for search_name in team.get('_search_names', []):
                if not search_name or len(search_name) < 3:
                    continue

                search_lower = search_name.lower()

                # Look for whole word matches using word boundaries
                pattern = r'\b' + re.escape(search_lower) + r'\b'
                for match in re.finditer(pattern, text_lower):
                    team_id = team.get('id')
                    # Avoid duplicate matches for same team (different search names)
                    if team_id not in seen_team_ids:
                        matches.append((team, match.start(), len(search_lower)))
                        seen_team_ids.add(team_id)
                        break  # Found this team, move to next

        # Sort by position in text
        matches.sort(key=lambda x: x[1])
        return matches

    def _extract_teams_without_separator(
        self,
        normalized: str,
        league: str,
        teams: List[Dict]
    ) -> Tuple[Optional[Dict], Optional[Dict], Optional[str]]:
        """
        Extract two teams from text without a separator.

        Fallback for streams that don't use vs/at/@ separators.
        Uses positional order: first team found = away, second = home.

        Args:
            normalized: Normalized stream name
            league: League code
            teams: Team list for the league

        Returns:
            Tuple of (away_team, home_team, error_reason)
        """
        # Find all team mentions in the text
        all_matches = self._find_all_teams_in_text(normalized, teams)

        if len(all_matches) == 0:
            return None, None, f'No teams found in: {normalized}'

        if len(all_matches) == 1:
            team_name = all_matches[0][0].get('name', 'unknown')
            return None, None, f'Only one team found ({team_name}), need two for matchup'

        if len(all_matches) > 2:
            # Take the two longest matches to handle cases like
            # "NY Giants vs New England Patriots" where "Giants" and "Patriots" also match
            all_matches.sort(key=lambda x: x[2], reverse=True)  # Sort by match length
            all_matches = all_matches[:2]
            all_matches.sort(key=lambda x: x[1])  # Re-sort by position

        # First team = away, second team = home
        away_team = all_matches[0][0]
        home_team = all_matches[1][0]

        logger.debug(f"Separator-less match: {away_team.get('name')} vs {home_team.get('name')}")

        return away_team, home_team, None

    def _lookup_alias(self, text: str, league: str) -> Optional[Dict]:
        """
        Look up user-defined alias in database.

        Args:
            text: Normalized team text
            league: League code

        Returns:
            Dict with espn_team_id, espn_team_name or None
        """
        if not self.db_connection_func:
            return None

        try:
            conn = self.db_connection_func()
            cursor = conn.cursor()

            # Look for exact alias match
            result = cursor.execute(
                """
                SELECT espn_team_id, espn_team_name
                FROM team_aliases
                WHERE alias = ? AND league = ?
                """,
                (text.lower().strip(), league.lower())
            ).fetchone()

            conn.close()

            if result:
                return {
                    'id': result[0],
                    'name': result[1],
                    'source': 'alias'
                }
            return None

        except Exception as e:
            logger.error(f"Error looking up alias '{text}' for {league}: {e}")
            return None

    def _find_team(self, text: str, league: str, teams: List[Dict]) -> Optional[Dict]:
        """
        Find a team match using aliases first, then ESPN data.

        Args:
            text: Text to search for team in
            league: League code
            teams: Cached team list for the league

        Returns:
            Team dict with id, name, or None
        """
        normalized = self._normalize_text(text)

        if not normalized:
            return None

        # 1. Check user aliases first (highest priority)
        alias_match = self._lookup_alias(normalized, league)
        if alias_match:
            logger.debug(f"Alias match: '{text}' -> {alias_match['name']}")
            return alias_match

        # 2. Check ESPN team database
        team_match = self._find_team_in_text(normalized, teams)
        if team_match:
            logger.debug(f"ESPN match: '{text}' -> {team_match.get('name')}")
            return team_match

        # 3. No match found
        logger.debug(f"No match for: '{text}' in {league}")
        return None

    def _extract_metadata(self, stream_name: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Extract date and time from stream name for disambiguation.

        Args:
            stream_name: Raw stream/channel name

        Returns:
            Tuple of (game_date, game_time) - either may be None
        """
        game_date = extract_date_from_text(stream_name)
        if game_date:
            logger.debug(f"Extracted date from stream name: {game_date.date()}")

        game_time = extract_time_from_text(stream_name)
        if game_time:
            logger.debug(f"Extracted time from stream name: {game_time.strftime('%H:%M')}")

        return game_date, game_time

    def _split_matchup(self, normalized: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Split normalized stream name into away and home parts.

        Args:
            normalized: Normalized stream name

        Returns:
            Tuple of (away_part, home_part, error_reason)
            - If successful: (away, home, None)
            - If failed: (None, None, reason)
        """
        separator, sep_pos = self._find_separator(normalized)
        if not separator:
            return None, None, f'No separator found in: {normalized}'

        # Convention: "Away vs/at Home" or "Away @ Home"
        away_part = normalized[:sep_pos].strip()
        home_part = normalized[sep_pos + len(separator):].strip()

        return away_part, home_part, None

    def extract_teams(self, stream_name: str, league: str) -> Dict[str, Any]:
        """
        Extract team matchup from a stream/channel name.

        Main entry point for team extraction.

        Args:
            stream_name: Raw stream/channel name
                         (e.g., "NFL | 16 -8:15PM Giants at Patriots")
            league: League code (e.g., "nfl", "epl")

        Returns:
            Dict with:
            - matched: bool - whether both teams were found
            - away_team_id, away_team_name: Away team info (if matched)
            - home_team_id, home_team_name: Home team info (if matched)
            - reason: str - error reason if not matched
            - raw_away, raw_home: Raw extracted strings (for debugging)
            - game_date: datetime or None - extracted date from stream name
        """
        # Initialize result
        game_date, game_time = self._extract_metadata(stream_name)
        result = {
            'matched': False,
            'stream_name': stream_name,
            'league': league,
            'game_date': game_date,
            'game_time': game_time
        }

        # Get teams for this league
        teams = self._get_teams_for_league(league)
        if not teams:
            result['reason'] = f'No team data available for league: {league}'
            return result

        # Normalize and split the stream name
        normalized = self._normalize_for_stream(stream_name)
        if not normalized:
            result['reason'] = 'Stream name empty after normalization'
            return result

        away_part, home_part, split_error = self._split_matchup(normalized)

        if split_error:
            # No separator found - try separator-less matching as fallback
            logger.debug(f"No separator found, trying separator-less matching for: {normalized}")
            away_team, home_team, fallback_error = self._extract_teams_without_separator(
                normalized, league, teams
            )

            if fallback_error:
                result['reason'] = fallback_error
                return result
        else:
            result['raw_away'] = away_part
            result['raw_home'] = home_part

            # Find both teams using separator-based parts
            away_team = self._find_team(away_part, league, teams)
            home_team = self._find_team(home_part, league, teams)

            if not away_team:
                result['reason'] = f'Away team not found: {away_part}'
                result['unmatched_team'] = away_part
                return result

            if not home_team:
                result['reason'] = f'Home team not found: {home_part}'
                result['unmatched_team'] = home_part
                return result

        # Both teams found - populate result
        result['matched'] = True
        result['away_team_id'] = away_team.get('id')
        result['away_team_name'] = away_team.get('name')
        result['away_team_abbrev'] = away_team.get('abbreviation', '')
        result['home_team_id'] = home_team.get('id')
        result['home_team_name'] = home_team.get('name')
        result['home_team_abbrev'] = home_team.get('abbreviation', '')

        return result

    def extract_teams_with_regex(
        self,
        stream_name: str,
        regex_pattern: str,
        league: str
    ) -> Dict[str, Any]:
        """
        Extract team matchup using a custom regex pattern.

        The regex must have named capture groups:
        - team1 (required): First team name
        - team2 (required): Second team name
        - game_date (optional): Date string (will attempt to parse)
        - game_time (optional): Time string (will attempt to parse)

        Example regex: r'(?P<team1>\w+)\s*[-@vs]+\s*(?P<team2>\w+)'

        Args:
            stream_name: Raw stream/channel name
            regex_pattern: Regex pattern with named groups
            league: League code for team resolution

        Returns:
            Dict with same structure as extract_teams()
        """
        result = {
            'matched': False,
            'stream_name': stream_name,
            'league': league,
            'game_date': None,
            'game_time': None
        }

        # Validate and apply regex
        try:
            match = re.search(regex_pattern, stream_name, re.IGNORECASE)
        except re.error as e:
            result['reason'] = f'Invalid regex pattern: {e}'
            return result

        if not match:
            result['reason'] = 'Regex did not match stream name'
            return result

        groups = match.groupdict()

        # Check required groups
        team1_text = groups.get('team1', '').strip()
        team2_text = groups.get('team2', '').strip()

        if not team1_text:
            result['reason'] = 'Regex matched but team1 group is empty'
            return result

        if not team2_text:
            result['reason'] = 'Regex matched but team2 group is empty'
            return result

        result['raw_away'] = team1_text
        result['raw_home'] = team2_text

        # Parse optional date/time groups
        if groups.get('game_date'):
            result['game_date'] = extract_date_from_text(groups['game_date'])

        if groups.get('game_time'):
            result['game_time'] = extract_time_from_text(groups['game_time'])

        # Get teams for this league
        teams = self._get_teams_for_league(league)
        if not teams:
            result['reason'] = f'No team data available for league: {league}'
            return result

        # Resolve team names to ESPN IDs
        away_team = self._find_team(team1_text, league, teams)
        home_team = self._find_team(team2_text, league, teams)

        if not away_team:
            result['reason'] = f'Team not found in ESPN database: {team1_text}'
            result['unmatched_team'] = team1_text
            return result

        if not home_team:
            result['reason'] = f'Team not found in ESPN database: {team2_text}'
            result['unmatched_team'] = team2_text
            return result

        # Both teams found
        result['matched'] = True
        result['away_team_id'] = away_team.get('id')
        result['away_team_name'] = away_team.get('name')
        result['away_team_abbrev'] = away_team.get('abbreviation', '')
        result['home_team_id'] = home_team.get('id')
        result['home_team_name'] = home_team.get('name')
        result['home_team_abbrev'] = home_team.get('abbreviation', '')

        return result

    def extract_teams_with_combined_regex(
        self,
        stream_name: str,
        league: str,
        teams_pattern: str,
        date_pattern: str = None,
        time_pattern: str = None
    ) -> Dict[str, Any]:
        """
        Extract team matchup using a combined regex pattern with named groups.

        The teams pattern must include named groups (?P<team1>...) and (?P<team2>...).
        Date and time patterns should use (?P<date>...) and (?P<time>...) if provided.

        Args:
            stream_name: Raw stream/channel name
            league: League code for team resolution
            teams_pattern: Regex with (?P<team1>...) and (?P<team2>...) groups (required)
            date_pattern: Regex to extract game date (optional)
            time_pattern: Regex to extract game time (optional)

        Returns:
            Dict with same structure as extract_teams()
        """
        result = {
            'matched': False,
            'stream_name': stream_name,
            'league': league,
            'game_date': None,
            'game_time': None
        }

        # Apply teams pattern
        try:
            teams_match = re.search(teams_pattern, stream_name, re.IGNORECASE)
            if not teams_match:
                result['reason'] = 'Teams pattern did not match stream name'
                return result
        except re.error as e:
            result['reason'] = f'Invalid teams pattern: {e}'
            return result

        # Extract team1 and team2 from named groups
        try:
            team1_text = teams_match.group('team1')
            if not team1_text or not team1_text.strip():
                result['reason'] = 'team1 group matched but captured empty text'
                return result
            team1_text = team1_text.strip()
        except IndexError:
            result['reason'] = 'Pattern missing required (?P<team1>...) group'
            return result

        try:
            team2_text = teams_match.group('team2')
            if not team2_text or not team2_text.strip():
                result['reason'] = 'team2 group matched but captured empty text'
                return result
            team2_text = team2_text.strip()
        except IndexError:
            result['reason'] = 'Pattern missing required (?P<team2>...) group'
            return result

        result['raw_away'] = team1_text
        result['raw_home'] = team2_text

        # Extract optional date
        if date_pattern:
            try:
                date_match = re.search(date_pattern, stream_name, re.IGNORECASE)
                if date_match:
                    # Try named group first, then first capture group, then full match
                    try:
                        date_text = date_match.group('date')
                    except IndexError:
                        date_text = date_match.group(1) if date_match.groups() else date_match.group(0)
                    if date_text:
                        result['game_date'] = extract_date_from_text(date_text.strip())
            except re.error as e:
                result['reason'] = f'Invalid date pattern: {e}'
                return result

        # Extract optional time
        if time_pattern:
            try:
                time_match = re.search(time_pattern, stream_name, re.IGNORECASE)
                if time_match:
                    # Try named group first, then first capture group, then full match
                    try:
                        time_text = time_match.group('time')
                    except IndexError:
                        time_text = time_match.group(1) if time_match.groups() else time_match.group(0)
                    if time_text:
                        result['game_time'] = extract_time_from_text(time_text.strip())
            except re.error as e:
                result['reason'] = f'Invalid time pattern: {e}'
                return result

        # Get teams for this league
        teams = self._get_teams_for_league(league)
        if not teams:
            result['reason'] = f'No team data available for league: {league}'
            return result

        # Resolve team names to ESPN IDs
        away_team = self._find_team(team1_text, league, teams)
        home_team = self._find_team(team2_text, league, teams)

        if not away_team:
            result['reason'] = f'Team not found in ESPN database: {team1_text}'
            result['unmatched_team'] = team1_text
            return result

        if not home_team:
            result['reason'] = f'Team not found in ESPN database: {team2_text}'
            result['unmatched_team'] = team2_text
            return result

        # Both teams found
        result['matched'] = True
        result['away_team_id'] = away_team.get('id')
        result['away_team_name'] = away_team.get('name')
        result['away_team_abbrev'] = away_team.get('abbreviation', '')
        result['home_team_id'] = home_team.get('id')
        result['home_team_name'] = home_team.get('name')
        result['home_team_abbrev'] = home_team.get('abbreviation', '')

        return result

    def clear_cache(self, league: str = None) -> None:
        """
        Clear the team cache.

        Args:
            league: Specific league to clear, or None to clear all
        """
        if league:
            self._team_cache.pop(league.lower(), None)
        else:
            self._team_cache.clear()
        logger.info(f"Team cache cleared: {league or 'all'}")

    def get_teams_for_league(self, league: str) -> List[Dict]:
        """
        Public method to get teams for a league (for UI dropdowns, etc).

        Args:
            league: League code

        Returns:
            List of team dicts
        """
        teams = self._get_teams_for_league(league)
        # Return clean version without internal search names
        return [
            {k: v for k, v in team.items() if not k.startswith('_')}
            for team in teams
        ]


# Convenience function for standalone use
def create_matcher() -> TeamMatcher:
    """
    Create a TeamMatcher instance with default configuration.

    Returns:
        Configured TeamMatcher instance
    """
    from api.espn_client import ESPNClient
    from database import get_connection

    espn = ESPNClient()
    return TeamMatcher(espn, db_connection_func=get_connection)
