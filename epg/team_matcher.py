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
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple

from epg.league_config import get_league_config, parse_api_path, is_college_league
from utils.logger import get_logger
from utils.regex_helper import REGEX_MODULE

logger = get_logger(__name__)


def fix_mojibake(text: str) -> str:
    """
    Fix UTF-8 mojibake (double-encoding) in stream names.

    This happens when UTF-8 bytes are incorrectly decoded as Latin-1/Windows-1252,
    resulting in garbled characters like:
    - Ã© instead of é
    - Ã¼ instead of ü
    - Ã± instead of ñ
    - Ã¶ instead of ö

    Args:
        text: Potentially mojibake'd text

    Returns:
        Fixed text with proper Unicode characters
    """
    if not text:
        return text

    # Common mojibake patterns (Latin-1 interpretation of UTF-8 bytes)
    # Only fix if we see these specific patterns to avoid breaking valid text
    mojibake_patterns = [
        ('Ã©', 'é'),  # e-acute
        ('Ã¨', 'è'),  # e-grave
        ('Ã±', 'ñ'),  # n-tilde
        ('Ã¼', 'ü'),  # u-umlaut
        ('Ã¶', 'ö'),  # o-umlaut
        ('Ã¤', 'ä'),  # a-umlaut
        ('Ã³', 'ó'),  # o-acute
        ('Ã¡', 'á'),  # a-acute
        ('Ã­', 'í'),  # i-acute
        ('Ãº', 'ú'),  # u-acute
        ('Ã§', 'ç'),  # c-cedilla
        ('Ã£', 'ã'),  # a-tilde
        ('Ãµ', 'õ'),  # o-tilde
        ('Ã', 'Á'),   # A-acute (uppercase) - must come after others
    ]

    result = text
    for wrong, right in mojibake_patterns:
        result = result.replace(wrong, right)

    return result


# Name variant mappings - all variants map to ESPN's canonical form
# This is ONE-WAY: stream variant → ESPN name (no back-and-forth replacement)
# ESPN uses a MIX of English and German names - verified against soccer_team_leagues cache
CITY_NAME_VARIANTS = {
    # ESPN uses ENGLISH for these cities
    'münchen': 'munich',
    'munchen': 'munich',
    'köln': 'cologne',
    'koln': 'cologne',
    # ESPN uses GERMAN (with umlauts) for these
    'nuremberg': 'nürnberg',
    'nurnberg': 'nürnberg',
    'dusseldorf': 'düsseldorf',
    'furth': 'fürth',
    'monchengladbach': 'mönchengladbach',
    'munster': 'münster',
    # German team name variants → ESPN canonical
    'hertha bsc': 'hertha berlin',
    'hamburger sv': 'hamburg sv',
    'sv werder bremen': 'werder bremen',
    # Italian team name variants → ESPN canonical
    'inter milan': 'internazionale',
    'inter': 'internazionale',
    # College team aliases (stream name → ESPN name)
    'albany': 'ualbany',
    'st leo': 'saint leo',
    'st. leo': 'saint leo',
}


# Module-level shared cache for team data across all TeamMatcher instances
# This prevents redundant ESPN API calls when processing streams in parallel
_shared_team_cache: Dict[str, Dict] = {}
_shared_team_cache_lock = threading.Lock()


def parse_date_from_regex_match(match, stream_name: str = None) -> Optional[datetime]:
    """
    Parse a date from a regex match object with flexible group support.

    Supports two patterns:
    1. Single date group: (?P<date>...) - passes to extract_date_from_text()
    2. Separate groups: (?P<day>...), (?P<month>...), and optionally (?P<year>...)
       - This gives users full control over DD/MM vs MM/DD interpretation

    Args:
        match: Regex match object with named groups
        stream_name: Original stream name (fallback for extract_date_from_text)

    Returns:
        datetime object or None
    """
    from datetime import datetime

    if not match:
        return None

    groups = match.groupdict()
    current_year = datetime.now().year

    # Check for separate day/month/year groups first (explicit control)
    if 'day' in groups and 'month' in groups:
        try:
            day = int(groups['day']) if groups['day'] else None
            month_raw = groups['month']

            if not day or not month_raw:
                return None

            # Month can be numeric or text (Jan, January, etc.)
            month_names = {
                'jan': 1, 'january': 1, 'feb': 2, 'february': 2,
                'mar': 3, 'march': 3, 'apr': 4, 'april': 4,
                'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
                'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
                'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
                'dec': 12, 'december': 12
            }

            # Try numeric first, then text
            try:
                month = int(month_raw)
            except ValueError:
                month = month_names.get(month_raw.lower().strip())

            if not month or month < 1 or month > 12:
                return None
            if day < 1 or day > 31:
                return None

            # Year is optional - default to current/next year
            year = current_year
            if 'year' in groups and groups['year']:
                try:
                    year = int(groups['year'])
                    if year < 100:
                        year += 2000
                except ValueError:
                    pass

            date = datetime(year, month, day)

            # If no explicit year and date is >6 months in past, assume next year
            if 'year' not in groups or not groups['year']:
                if (datetime.now() - date).days > 180:
                    date = datetime(year + 1, month, day)

            return date
        except (ValueError, TypeError):
            return None

    # Fall back to single (?P<date>...) group
    if 'date' in groups and groups['date']:
        return extract_date_from_text(groups['date'].strip())

    # Try first capture group or full match
    try:
        date_text = match.group(1) if match.groups() else match.group(0)
        if date_text:
            return extract_date_from_text(date_text.strip())
    except (IndexError, AttributeError):
        pass

    # Last resort: parse from original stream name
    if stream_name:
        return extract_date_from_text(stream_name)

    return None


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
    SEPARATORS = [' vs. ', ' vs ', ' at ', ' @ ', ' v. ', ' v ', ' x ']

    # Cache duration for team lists (1 hour)
    CACHE_DURATION = timedelta(hours=1)

    def __init__(self, espn_client, db_connection_func=None):
        """
        Initialize TeamMatcher.

        Args:
            espn_client: ESPNClient instance for fetching team data
            db_connection_func: Function that returns a database connection
                               (for alias lookups). If None, aliases won't be used.

        Note: Team data is cached at the module level (shared across all instances)
        to prevent redundant ESPN API calls when processing streams in parallel.
        """
        self.espn = espn_client
        self.db_connection_func = db_connection_func

        # League config cache (from database) - per instance since config is fast to fetch
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

    def _load_teams_from_db_cache(self, league_code: str) -> Optional[List[Dict]]:
        """
        Load teams from TeamLeagueCache database table.

        The team_league_cache table is pre-populated on startup and contains
        all teams for non-soccer leagues. This avoids hitting ESPN API during
        EPG generation.

        Args:
            league_code: League code (e.g., 'nfl', 'mens-college-basketball')

        Returns:
            List of team dicts or None if not found in DB cache
        """
        if not self.db_connection_func:
            return None

        try:
            conn = self.db_connection_func()
            cursor = conn.execute("""
                SELECT espn_team_id, team_name, team_abbrev, team_short_name, sport
                FROM team_league_cache
                WHERE league_code = ?
            """, (league_code.lower(),))
            rows = cursor.fetchall()

            if not rows:
                return None

            # Convert DB rows to team dicts matching ESPN API format
            teams = []
            for row in rows:
                team = {
                    'id': row[0],
                    'displayName': row[1],
                    'name': row[1],  # Use full name as name too
                    'abbreviation': row[2],
                    'shortName': row[3],
                    'slug': row[1].lower().replace(' ', '-') if row[1] else '',
                }
                teams.append(team)

            logger.debug(f"Loaded {len(teams)} teams for {league_code} from DB cache")
            return teams

        except Exception as e:
            logger.warning(f"Error loading teams from DB cache for {league_code}: {e}")
            return None

    def _get_teams_for_league(self, league_code: str) -> List[Dict]:
        """
        Get all teams for a league, using shared cache when available.

        Priority order:
        1. In-memory cache (fastest, per-generation)
        2. TeamLeagueCache DB (pre-warmed on startup, no API call)
        3. ESPN API (fallback for uncached leagues)

        Args:
            league_code: League code (e.g., 'nfl', 'epl', 'ncaam')

        Returns:
            List of team dicts with id, name, abbreviation, shortName, slug
        """
        global _shared_team_cache
        league_lower = league_code.lower()

        # Check shared cache first (with lock for thread safety)
        with _shared_team_cache_lock:
            if league_lower in _shared_team_cache:
                cached = _shared_team_cache[league_lower]
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

        # Double-check lock pattern: re-check cache after acquiring lock
        # Another thread may have populated the cache while we were getting config
        # IMPORTANT: Fetch must happen INSIDE the lock to prevent race conditions
        with _shared_team_cache_lock:
            if league_lower in _shared_team_cache:
                cached = _shared_team_cache[league_lower]
                if datetime.now() - cached['fetched_at'] < self.CACHE_DURATION:
                    return cached['teams']

            # Try TeamLeagueCache DB first (pre-warmed on startup, no API call needed)
            # This eliminates ~23 seconds of API calls during EPG generation
            teams = self._load_teams_from_db_cache(league_lower)
            if teams:
                logger.info(f"Loaded {len(teams)} teams for {league_code} from DB cache (no API call)")
            else:
                # Fallback to ESPN API for leagues not in DB cache
                if is_college_league(league_lower) or is_college_league(league):
                    teams = self._fetch_college_teams(sport, league)
                else:
                    logger.info(f"Fetching teams for {league_code} from ESPN API")
                    teams = self.espn.get_league_teams(sport, league)

            if not teams:
                logger.warning(f"No teams returned for {league_code}")
                return []

            # Build search index with normalized names
            for team in teams:
                team['_search_names'] = self._build_search_names(team)

            # Cache results
            _shared_team_cache[league_lower] = {
                'teams': teams,
                'fetched_at': datetime.now()
            }

            logger.info(f"Cached {len(teams)} teams for {league_code}")
            return teams

    def _fetch_college_teams(self, sport: str, league: str) -> List[Dict]:
        """
        Fetch all teams for a college league using get_all_teams_by_conference().

        This combines teams from both /groups and /teams endpoints to ensure
        we don't miss any teams. ESPN's /teams?limit=500 returns 362 teams but
        misses ~3 recently-transitioned D1 schools that only appear in /groups.

        Args:
            sport: Sport (e.g., 'basketball', 'football')
            league: League identifier (e.g., 'mens-college-basketball')

        Returns:
            List of all team dicts
        """
        logger.info(f"Fetching college teams for {league} via get_all_teams_by_conference")

        # Use get_all_teams_by_conference which merges /groups and /teams endpoints
        # This ensures we get ALL teams including recently-transitioned schools
        conferences = self.espn.get_all_teams_by_conference(sport, league)
        if not conferences:
            # Fall back to simple teams list
            logger.warning(f"No conference data for {league}, falling back to get_league_teams")
            teams = self.espn.get_league_teams(sport, league)
            if not teams:
                logger.warning(f"No teams found for {league}")
                return []
            return teams

        # Flatten conference structure to single list
        teams = []
        for conf in conferences:
            teams.extend(conf.get('teams', []))

        logger.info(f"Fetched {len(teams)} college teams for {league}")
        return teams

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

        # Normalize special characters (handles ESPN stream name quirks)
        # Backtick → apostrophe (e.g., "Hawai`i" → "Hawai'i")
        text = text.replace('`', "'")
        # Underscore → space (e.g., "Gardner_Webb" → "Gardner Webb")
        text = text.replace('_', ' ')

        # Remove parenthetical content EXCEPT US state abbreviations like (OH), (FL), (PA)
        # These are used to disambiguate teams like "Miami (OH)" vs "Miami"
        # State codes are exactly 2 uppercase letters
        def remove_non_state_parens(match):
            content = match.group(1).strip().upper()
            # US state abbreviations (2 letters) - preserve these
            us_states = {
                'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
            }
            if content in us_states:
                return match.group(0)  # Preserve the parenthetical
            return ''  # Remove it
        text = re.sub(r'\(([^)]*)\)', remove_non_state_parens, text)

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

        # Remove standalone timezone abbreviations (ET, EST, PT, GMT, etc.)
        text = re.sub(r'\b(et|est|pt|pst|ct|cst|mt|mst|gmt|utc)\b', '', text, flags=re.I)

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

        # Remove periods (normalizes "St." to "St")
        text = re.sub(r'\.', '', text)

        # Remove trailing @ (leftover from "@ Dec 03" after date removal)
        text = re.sub(r'\s*@\s*$', '', text)

        # Normalize whitespace
        text = ' '.join(text.split())

        return text.strip()

    def _mask_times_in_text(self, text: str) -> Tuple[str, List[Tuple[str, int, int]]]:
        """
        Find and mask all time patterns in text, returning masked text and positions.

        Handles:
        - 12-hour with minutes: 8:15pm, 8:15 PM, 1:30 PM ET (NOT 01:12pm - leading zeros only for 24h)
        - 12-hour hour-only: 12pm, 4pm, 8am (hour followed immediately by am/pm)
        - 24-hour: 18:00, 20:15

        Args:
            text: Raw text that may contain times

        Returns:
            Tuple of (masked_text, list of (original, start, end) tuples)
        """
        masked = text
        found_times = []

        # Pattern 1: 12-hour with minutes (8:15pm, 8:15 PM, 1:00 PM ET)
        # Key insight: 12-hour times don't use leading zeros on hour (1:30pm not 01:30pm)
        # Leading zeros are only used in 24-hour format (01:30 = 1:30 AM)
        # So we need to ensure hour is 1-12 (valid 12h) and NOT preceded by another digit
        time_12h_pattern = re.compile(
            r'(?<!\d)(\d{1,2}):(\d{2})\s*(am|pm)(\s*(et|est|pt|pst|ct|cst|mt|mst))?',
            re.IGNORECASE
        )
        for match in time_12h_pattern.finditer(text):
            hour = int(match.group(1))
            # Valid 12-hour times: 1-12 (not 0, not >12)
            # Also reject if it looks like "01:12pm" (leading zero on single-digit hour)
            # In 12h format, you write "1:12pm" not "01:12pm"
            if 1 <= hour <= 12:
                # Check for leading zero anomaly: if hour < 10 and starts with '0', skip
                hour_str = match.group(1)
                if hour < 10 and hour_str.startswith('0'):
                    # This is like "01:12pm" - not a valid 12h time, skip
                    continue
                found_times.append((match.group(0), match.start(), match.end()))

        # Pattern 2: 12-hour hour-only (12pm, 4pm, 8am) - hour directly followed by am/pm
        # This catches "CB01:12pm" -> the "12pm" part, and standalone "4pm"
        hour_only_pattern = re.compile(r'\b(\d{1,2})(am|pm)\b', re.IGNORECASE)
        for match in hour_only_pattern.finditer(text):
            # Avoid duplicating times already found (like the minutes part of 8:15pm)
            overlap = False
            for _, start, end in found_times:
                if match.start() >= start and match.end() <= end:
                    overlap = True
                    break
            if not overlap:
                found_times.append((match.group(0), match.start(), match.end()))

        # Pattern 3: 24-hour format (18:00, 20:15)
        time_24h_pattern = re.compile(r'\b(\d{2}:\d{2})\b')
        for match in time_24h_pattern.finditer(text):
            # Check if it's a valid 24-hour time (not already captured)
            time_str = match.group(1)
            parts = time_str.split(':')
            if len(parts) == 2:
                hour, minute = int(parts[0]), int(parts[1])
                if 0 <= hour < 24 and 0 <= minute < 60:
                    overlap = False
                    for _, start, end in found_times:
                        if match.start() >= start and match.end() <= end:
                            overlap = True
                            break
                    if not overlap:
                        found_times.append((match.group(0), match.start(), match.end()))

        # Sort by position (reverse) so we can replace from end to start without offset issues
        found_times.sort(key=lambda x: x[1], reverse=True)

        # Replace each time with a placeholder
        for original, start, end in found_times:
            masked = masked[:start] + ('_' * (end - start)) + masked[end:]

        return masked, found_times

    def _mask_dates_in_text(self, text: str) -> Tuple[str, List[Tuple[str, int, int]]]:
        """
        Find and mask all date patterns in text, returning masked text and positions.

        Handles:
        - ISO: 2025-11-30
        - US with year: 11/30/2025, 11/30/25
        - US without year: 11/30
        - Text month: Nov 30, November 30

        Args:
            text: Raw text that may contain dates

        Returns:
            Tuple of (masked_text, list of (original, start, end) tuples)
        """
        masked = text
        found_dates = []

        # Pattern 1: ISO format (2025-11-30)
        iso_pattern = re.compile(r'\d{4}-\d{2}-\d{2}')
        for match in iso_pattern.finditer(text):
            found_dates.append((match.group(0), match.start(), match.end()))

        # Pattern 2: US format with year (11/30/2025 or 11/30/25)
        us_full_pattern = re.compile(r'\d{1,2}/\d{1,2}/\d{2,4}')
        for match in us_full_pattern.finditer(text):
            found_dates.append((match.group(0), match.start(), match.end()))

        # Pattern 3: US format without year (11/30) - avoid matching if already part of longer pattern
        us_short_pattern = re.compile(r'\d{1,2}/\d{1,2}(?!/)')
        for match in us_short_pattern.finditer(text):
            overlap = False
            for _, start, end in found_dates:
                if match.start() >= start and match.end() <= end:
                    overlap = True
                    break
            if not overlap:
                found_dates.append((match.group(0), match.start(), match.end()))

        # Pattern 4: Text month format (Nov 30, November 30)
        text_month_pattern = re.compile(
            r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
            r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
            r'\s+\d{1,2}\b',
            re.IGNORECASE
        )
        for match in text_month_pattern.finditer(text):
            found_dates.append((match.group(0), match.start(), match.end()))

        # Sort by position (reverse) for replacement
        found_dates.sort(key=lambda x: x[1], reverse=True)

        # Replace each date with a placeholder
        for original, start, end in found_dates:
            masked = masked[:start] + ('_' * (end - start)) + masked[end:]

        return masked, found_dates

    def _strip_prefix_at_colon(self, text: str, masked_text: str = None) -> str:
        """
        Strip everything before first colon, if the colon appears before the game separator.

        This handles stream names like "NCAAW B 14: Washington State vs BYU" where
        everything before the colon is metadata (league, sport code, stream number).

        When masked_text is provided, uses it to detect colons (times already masked),
        making the logic simple: any colon in masked text is a metadata colon.

        Args:
            text: Original stream name text
            masked_text: Text with times/dates masked (optional, for simpler detection)

        Returns:
            Text with prefix stripped, or original if no valid prefix colon found
        """
        # Use masked text for colon detection if provided
        detect_text = masked_text if masked_text else text

        # Find game separator position in original text
        sep_pos = len(text)
        for sep in self.SEPARATORS:
            pos = text.lower().find(sep)
            if pos > 0 and pos < sep_pos:
                sep_pos = pos

        # Find LAST colon in masked text before separator (times already masked, so any colon is metadata)
        # This handles nested colons like "Channel: Event Context: Team @ Team"
        colon_pos = detect_text.rfind(':', 0, sep_pos)

        # Only strip if colon found before separator
        if colon_pos > 0:
            return text[colon_pos + 1:].strip()

        return text

    def _normalize_for_stream(self, stream_name: str) -> str:
        """
        Normalize a stream name, removing everything except team names.

        More aggressive than _normalize_text - removes more noise
        that's common in IPTV stream names.

        Architecture (mask-then-strip):
        0. Fix mojibake (UTF-8 double-encoding)
        1. Apply simple prefix removals that don't affect colon positions
        2. Mask times (so colons in times don't confuse prefix detection)
        3. Strip prefix at colon (now simple - any remaining colon is metadata)
        4. Apply standard normalization
        5. Apply city name variants (München→Munich, Köln→Cologne)

        Args:
            stream_name: Raw stream/channel name

        Returns:
            Cleaned string with just team matchup info
        """
        # Step 0: Fix mojibake first (e.g., "Ã©" → "é")
        text = fix_mojibake(stream_name)

        # Remove country/region prefixes like "(UK)", "(US)", "CA"
        text = re.sub(r'^\(?\s*(uk|us|usa|ca|au)\s*\)?[\s|:]*', '', text, flags=re.I)

        # Remove provider prefixes like "(Sky+ 11)", "(Dazn 070)", "(Peacock 023)"
        text = re.sub(r'\([^)]*(?:sky|dazn|peacock|tsn|sportsnet|espn|fox|nbc|cbs|abc)[^)]*\)', '', text, flags=re.I)

        # Remove "on TSN+:", "NBA on ESPN:", etc.
        text = re.sub(r'(nfl|nba|nhl|mlb|ncaa[mfwb]?|soccer|epl|mls)\s+on\s+\w+\s*:?\s*', '', text, flags=re.I)

        # Remove standalone league prefixes like "NCAA Basketball:", "NCAAM:", "College Basketball:"
        text = re.sub(r'^(ncaa[mfwb]?|college)\s*(basketball|football|hockey)?\s*:?\s*', '', text, flags=re.I)

        # NOW mask times on the cleaned text (so text and masked_text stay in sync)
        # This makes colon detection trivial - any remaining colon is metadata
        masked_text, _ = self._mask_times_in_text(text)

        # Strip metadata prefix at colon using masked text for detection
        # "CB01:12pm 10 ISU @ 1 PUR" -> masked "CB01:____ 10 ISU @ 1 PUR" -> strips at colon
        text = self._strip_prefix_at_colon(text, masked_text)

        # Strip exception keywords (language prefixes, etc.) using database-driven patterns
        # This replaces the hardcoded language patterns and uses user-configurable keywords
        from utils.keyword_matcher import strip_exception_keywords
        text, _ = strip_exception_keywords(text)

        # Apply standard normalization first
        text = self._normalize_text(text)

        # Apply city/team name variants to match ESPN format
        # nuremberg→nürnberg, hertha bsc→hertha berlin, etc.
        for variant, canonical in CITY_NAME_VARIANTS.items():
            # Use word boundary matching to avoid partial replacements
            text = re.sub(r'\b' + re.escape(variant) + r'\b', canonical, text, flags=re.I)

        return text

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

        Matching priority (longer matches preferred within each tier):
        1. Exact match - immediate return
        2. Input text is prefix of a team's full name (e.g., "washington state" matches
           "washington state cougars") - this catches partial team names
        3. Team name appears as whole word in input text
        4. Team name is prefix of input text (fallback)

        The key insight is that "washington state" should match Washington State Cougars
        (because input is prefix of "washington state cougars") rather than Washington
        Huskies (where "washington" appears as a word in input).

        Args:
            text: Normalized text to search in
            teams: List of team dicts with _search_names, _primary_names, _secondary_names

        Returns:
            Team dict or None
        """
        text = text.strip().lower()
        if not text:
            return None

        # Track matches by tier, keeping the longest match in each tier
        # Tier 1: Input is prefix of search name (e.g., "washington state" prefix of "washington state cougars")
        input_prefix_match = None
        input_prefix_length = 0

        # Tier 2: Word boundary match (search name appears as whole word in input)
        word_match = None
        word_match_length = 0

        # Tier 3: Search name is prefix of input (e.g., "washington" prefix of "washington state")
        name_prefix_match = None
        name_prefix_length = 0

        for team in teams:
            # Check primary names (team-specific: nickname, displayName, abbreviation)
            for search_name in team.get('_primary_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Exact match - immediate return
                if text == search_lower:
                    return team

                # Input is prefix of search name
                # e.g., "washington state" is prefix of "washington state cougars"
                if search_lower.startswith(text) and len(text) >= 3:
                    if len(text) > input_prefix_length:
                        input_prefix_match = team
                        input_prefix_length = len(text)

                # Whole word match
                if len(search_lower) >= 3:
                    pattern = r'\b' + re.escape(search_lower) + r'\b'
                    if re.search(pattern, text):
                        if len(search_lower) > word_match_length:
                            word_match = team
                            word_match_length = len(search_lower)

                # Search name is prefix of input
                # e.g., "washington" is prefix of "washington state"
                if text.startswith(search_lower) and len(search_lower) >= 3:
                    if len(search_lower) > name_prefix_length:
                        name_prefix_match = team
                        name_prefix_length = len(search_lower)

            # Check secondary names (location-only, lower priority)
            for search_name in team.get('_secondary_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Exact match - immediate return
                if text == search_lower:
                    return team

                # Only check word boundary for secondary names (not prefix matches)
                # to avoid location-only matches taking precedence
                if len(search_lower) >= 3:
                    pattern = r'\b' + re.escape(search_lower) + r'\b'
                    if re.search(pattern, text):
                        # Only use if we don't have a better primary match
                        if len(search_lower) > word_match_length and not input_prefix_match:
                            word_match = team
                            word_match_length = len(search_lower)

        # Return best match, preferring longer matches
        # Compare across tiers - a significantly longer match should win
        if input_prefix_match and input_prefix_length >= word_match_length:
            return input_prefix_match
        if word_match and word_match_length > name_prefix_length:
            return word_match
        if input_prefix_match:
            return input_prefix_match
        if word_match:
            return word_match
        if name_prefix_match:
            return name_prefix_match

        return None

    def _find_all_matching_teams(self, text: str, teams: List[Dict], max_results: int = 5) -> List[Dict]:
        """
        Find ALL teams that match the given text, sorted by match quality.

        This is used for team disambiguation when the primary match doesn't find a game.
        For example, "Maryland" could match:
        - Maryland Terrapins (best match - exact location)
        - Maryland Eastern Shore Hawks (contains "Maryland")
        - Loyola Maryland Greyhounds (contains "Maryland")

        Match quality tiers (higher = better):
        - Tier 4: Exact match
        - Tier 3: Input is prefix of team name
        - Tier 2: Team name appears as whole word in input
        - Tier 1: Team name is prefix of input

        Args:
            text: Normalized text to search (e.g., "maryland")
            teams: List of team dicts with _search_names, _primary_names, _secondary_names
            max_results: Maximum number of teams to return (default 5)

        Returns:
            List of team dicts sorted by match quality (best first)
        """
        text = text.strip().lower()
        if not text:
            return []

        # Collect all matches with their quality scores
        # Format: (team, tier, match_length, is_primary)
        matches = []
        seen_team_ids = set()

        for team in teams:
            team_id = team.get('id')
            if team_id in seen_team_ids:
                continue

            best_tier = 0
            best_length = 0
            best_is_primary = False

            # Check primary names first (higher priority)
            for search_name in team.get('_primary_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Tier 4: Exact match
                if text == search_lower:
                    best_tier = 4
                    best_length = len(search_lower)
                    best_is_primary = True
                    break

                # Tier 3: Input is prefix of search name
                if search_lower.startswith(text) and len(text) >= 3:
                    if 3 > best_tier or (3 == best_tier and len(text) > best_length):
                        best_tier = 3
                        best_length = len(text)
                        best_is_primary = True

                # Tier 2: Whole word match
                if len(search_lower) >= 3:
                    pattern = r'\b' + re.escape(search_lower) + r'\b'
                    if re.search(pattern, text):
                        if 2 > best_tier or (2 == best_tier and len(search_lower) > best_length):
                            best_tier = 2
                            best_length = len(search_lower)
                            best_is_primary = True

                # Tier 1: Search name is prefix of input
                if text.startswith(search_lower) and len(search_lower) >= 3:
                    if 1 > best_tier or (1 == best_tier and len(search_lower) > best_length):
                        best_tier = 1
                        best_length = len(search_lower)
                        best_is_primary = True

            # Check secondary names (location-only, lower priority)
            for search_name in team.get('_secondary_names', []):
                if not search_name:
                    continue
                search_lower = search_name.lower()

                # Tier 4: Exact match
                if text == search_lower:
                    if 4 > best_tier:
                        best_tier = 4
                        best_length = len(search_lower)
                        best_is_primary = False

                # Tier 2: Whole word match (only for secondary)
                if len(search_lower) >= 3:
                    pattern = r'\b' + re.escape(search_lower) + r'\b'
                    if re.search(pattern, text):
                        if 2 > best_tier or (2 == best_tier and len(search_lower) > best_length and not best_is_primary):
                            best_tier = 2
                            best_length = len(search_lower)
                            best_is_primary = False

            if best_tier > 0:
                matches.append((team, best_tier, best_length, best_is_primary))
                seen_team_ids.add(team_id)

        # Sort by: tier (desc), is_primary (desc), length (desc)
        matches.sort(key=lambda x: (x[1], x[3], x[2]), reverse=True)

        # Return just the team dicts, limited to max_results
        return [m[0] for m in matches[:max_results]]

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
        # TRACE: Log the input
        logger.debug(f"[TRACE] extract_teams START | stream='{stream_name}' | league={league}")

        # Initialize result
        game_date, game_time = self._extract_metadata(stream_name)
        result = {
            'matched': False,
            'stream_name': stream_name,
            'league': league,
            'game_date': game_date,
            'game_time': game_time
        }

        # TRACE: Log extracted date/time
        if game_date or game_time:
            logger.debug(f"[TRACE] Metadata | date={game_date.date() if game_date else None} | time={game_time.strftime('%H:%M') if game_time else None}")

        # Get teams for this league
        teams = self._get_teams_for_league(league)
        if not teams:
            result['reason'] = f'No team data available for league: {league}'
            logger.debug(f"[TRACE] extract_teams FAIL | reason=no team data for {league}")
            return result

        # Normalize and split the stream name
        normalized = self._normalize_for_stream(stream_name)
        if not normalized:
            result['reason'] = 'Stream name empty after normalization'
            logger.debug(f"[TRACE] extract_teams FAIL | reason=empty after normalization")
            return result

        # TRACE: Log normalized result
        logger.debug(f"[TRACE] Normalized | '{stream_name}' -> '{normalized}'")

        away_part, home_part, split_error = self._split_matchup(normalized)

        if split_error:
            # No separator found - try separator-less matching as fallback
            logger.debug(f"[TRACE] No separator found, trying separator-less matching for: {normalized}")
            away_team, home_team, fallback_error = self._extract_teams_without_separator(
                normalized, league, teams
            )

            if fallback_error:
                result['reason'] = fallback_error
                logger.debug(f"[TRACE] extract_teams FAIL | reason={fallback_error}")
                return result
        else:
            result['raw_away'] = away_part
            result['raw_home'] = home_part

            # TRACE: Log the split parts
            logger.debug(f"[TRACE] Split | away_part='{away_part}' | home_part='{home_part}'")

            # Find both teams using separator-based parts
            away_team = self._find_team(away_part, league, teams)
            home_team = self._find_team(home_part, league, teams)

            if not away_team:
                result['reason'] = f'Away team not found: {away_part}'
                result['unmatched_team'] = away_part
                logger.debug(f"[TRACE] extract_teams FAIL | away_part='{away_part}' not found in {league}")
                return result

            if not home_team:
                result['reason'] = f'Home team not found: {home_part}'
                result['unmatched_team'] = home_part
                logger.debug(f"[TRACE] extract_teams FAIL | home_part='{home_part}' not found in {league}")
                return result

        # Both teams found - populate result
        result['matched'] = True
        result['away_team_id'] = away_team.get('id')
        result['away_team_name'] = away_team.get('name')
        result['away_team_abbrev'] = away_team.get('abbreviation', '')
        result['home_team_id'] = home_team.get('id')
        result['home_team_name'] = home_team.get('name')
        result['home_team_abbrev'] = home_team.get('abbreviation', '')

        # TRACE: Log successful match
        logger.debug(f"[TRACE] extract_teams OK | '{away_team.get('name')}' (id={away_team.get('id')}) vs '{home_team.get('name')}' (id={home_team.get('id')})")

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

        # Apply teams pattern (uses regex module for advanced pattern support like variable-width lookbehind)
        try:
            teams_match = REGEX_MODULE.search(teams_pattern, stream_name, REGEX_MODULE.IGNORECASE)
            if not teams_match:
                result['reason'] = 'Teams pattern did not match stream name'
                return result
        except Exception as e:
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

        # Extract optional date (uses regex module for advanced pattern support)
        # Supports: (?P<date>...) OR separate (?P<day>...), (?P<month>...), (?P<year>...)
        if date_pattern:
            try:
                date_match = REGEX_MODULE.search(date_pattern, stream_name, REGEX_MODULE.IGNORECASE)
                if date_match:
                    result['game_date'] = parse_date_from_regex_match(date_match, stream_name)
            except Exception as e:
                result['reason'] = f'Invalid date pattern: {e}'
                return result

        # Extract optional time (uses regex module for advanced pattern support)
        if time_pattern:
            try:
                time_match = REGEX_MODULE.search(time_pattern, stream_name, REGEX_MODULE.IGNORECASE)
                if time_match:
                    # Try named group first, then first capture group, then full match
                    try:
                        time_text = time_match.group('time')
                    except IndexError:
                        time_text = time_match.group(1) if time_match.groups() else time_match.group(0)
                    if time_text:
                        result['game_time'] = extract_time_from_text(time_text.strip())
            except Exception as e:
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

    def extract_teams_with_selective_regex(
        self,
        stream_name: str,
        league: str,
        teams_pattern: str = None,
        teams_enabled: bool = False,
        date_pattern: str = None,
        date_enabled: bool = False,
        time_pattern: str = None,
        time_enabled: bool = False
    ) -> Dict[str, Any]:
        """
        Extract team matchup with selective custom regex per field.

        Allows enabling custom regex for specific fields while falling back
        to built-in extraction for others.

        Args:
            stream_name: Raw stream/channel name
            league: League code for team resolution
            teams_pattern: Custom regex with (?P<team1>...) and (?P<team2>...) groups
            teams_enabled: Whether to use custom teams pattern
            date_pattern: Custom regex for date extraction
            date_enabled: Whether to use custom date pattern
            time_pattern: Custom regex for time extraction
            time_enabled: Whether to use custom time pattern

        Returns:
            Dict with same structure as extract_teams()
        """
        # If custom teams pattern is enabled, use custom extraction for teams
        if teams_enabled and teams_pattern:
            result = self._extract_teams_custom(stream_name, league, teams_pattern)
            if not result.get('matched'):
                return result
        else:
            # Use built-in team extraction
            result = self.extract_teams(stream_name, league)
            if not result.get('matched'):
                return result

        # Now handle date/time - override with custom if enabled, otherwise keep defaults
        # Uses REGEX_MODULE for advanced pattern support like variable-width lookbehind
        # Supports: (?P<date>...) OR separate (?P<day>...), (?P<month>...), (?P<year>...)
        if date_enabled and date_pattern:
            try:
                date_match = REGEX_MODULE.search(date_pattern, stream_name, REGEX_MODULE.IGNORECASE)
                if date_match:
                    result['game_date'] = parse_date_from_regex_match(date_match, stream_name)
                else:
                    result['game_date'] = None
            except Exception as e:
                logger.warning(f"Invalid custom date pattern: {e}")
                # Fall back to default
                result['game_date'] = extract_date_from_text(stream_name)

        if time_enabled and time_pattern:
            try:
                time_match = REGEX_MODULE.search(time_pattern, stream_name, REGEX_MODULE.IGNORECASE)
                if time_match:
                    try:
                        time_text = time_match.group('time')
                    except (IndexError, Exception):
                        time_text = time_match.group(1) if time_match.groups() else time_match.group(0)
                    if time_text:
                        result['game_time'] = extract_time_from_text(time_text.strip())
                    else:
                        result['game_time'] = None
                else:
                    result['game_time'] = None
            except Exception as e:
                logger.warning(f"Invalid custom time pattern: {e}")
                # Fall back to default
                result['game_time'] = extract_time_from_text(stream_name)

        return result

    def _extract_teams_custom(
        self,
        stream_name: str,
        league: str,
        teams_pattern: str
    ) -> Dict[str, Any]:
        """
        Extract teams using custom regex pattern only.

        Helper method for extract_teams_with_selective_regex.
        """
        result = {
            'matched': False,
            'stream_name': stream_name,
            'league': league,
            'game_date': extract_date_from_text(stream_name),
            'game_time': extract_time_from_text(stream_name)
        }

        # Uses REGEX_MODULE for advanced pattern support like variable-width lookbehind
        try:
            teams_match = REGEX_MODULE.search(teams_pattern, stream_name, REGEX_MODULE.IGNORECASE)
            if not teams_match:
                result['reason'] = 'Teams pattern did not match stream name'
                return result
        except Exception as e:
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

    def extract_raw_matchup(
        self,
        stream_name: str,
        custom_regex_teams: str = None,
        custom_regex_teams_enabled: bool = False,
        custom_regex_date: str = None,
        custom_regex_date_enabled: bool = False,
        custom_regex_time: str = None,
        custom_regex_time_enabled: bool = False
    ) -> Dict[str, Any]:
        """
        Extract raw matchup data from a stream name WITHOUT resolving to ESPN teams.

        This is useful for multi-sport mode where we don't know the league yet.
        Extracts: team1, team2, date, time, and detected league indicator.

        Supports custom regex patterns for teams/date/time extraction, shared with
        regular event groups.

        Args:
            stream_name: Raw stream/channel name
            custom_regex_teams: Custom regex pattern with (?P<team1>...) and (?P<team2>...)
            custom_regex_teams_enabled: Whether to use custom teams pattern
            custom_regex_date: Custom regex pattern for date
            custom_regex_date_enabled: Whether to use custom date pattern
            custom_regex_time: Custom regex pattern for time
            custom_regex_time_enabled: Whether to use custom time pattern

        Returns:
            Dict with:
            - success: bool - whether team names were extracted
            - team1, team2: Raw team name strings (if found)
            - game_date: datetime or None
            - game_time: datetime or None
            - detected_league: str or None - league code if indicator found
            - detected_sport: str or None - sport if indicator found
            - reason: str - error reason if not successful
        """
        result = {
            'success': False,
            'stream_name': stream_name,
            'team1': None,
            'team2': None,
            'game_date': None,
            'game_time': None,
            'detected_league': None,
            'detected_sport': None,
            'reason': None
        }

        # Extract date using custom or default pattern
        # Supports: (?P<date>...) OR separate (?P<day>...), (?P<month>...), (?P<year>...)
        if custom_regex_date_enabled and custom_regex_date:
            try:
                date_match = REGEX_MODULE.search(custom_regex_date, stream_name, REGEX_MODULE.IGNORECASE)
                if date_match:
                    result['game_date'] = parse_date_from_regex_match(date_match, stream_name)
            except Exception as e:
                logger.warning(f"Custom date regex failed: {e}")
                result['game_date'] = extract_date_from_text(stream_name)
        else:
            result['game_date'] = extract_date_from_text(stream_name)

        # Extract time using custom or default pattern
        if custom_regex_time_enabled and custom_regex_time:
            try:
                time_match = REGEX_MODULE.search(custom_regex_time, stream_name, REGEX_MODULE.IGNORECASE)
                if time_match:
                    time_str = time_match.group('time') if 'time' in time_match.groupdict() else time_match.group(1)
                    if time_str:
                        result['game_time'] = extract_time_from_text(time_str)
            except Exception as e:
                logger.warning(f"Custom time regex failed: {e}")
                result['game_time'] = extract_time_from_text(stream_name)
        else:
            result['game_time'] = extract_time_from_text(stream_name)

        # Try to detect league from indicators in stream name
        from epg.league_detector import LEAGUE_INDICATORS, SPORT_INDICATORS, get_sport_for_league
        from database import normalize_league_code
        import re

        for pattern, league in LEAGUE_INDICATORS.items():
            if re.search(pattern, stream_name, re.IGNORECASE):
                # Normalize alias to ESPN slug (single source of truth)
                result['detected_league'] = normalize_league_code(league)
                result['detected_sport'] = get_sport_for_league(league)
                break

        if not result['detected_league']:
            for pattern, leagues in SPORT_INDICATORS.items():
                if re.search(pattern, stream_name, re.IGNORECASE):
                    result['detected_sport'] = pattern.strip(r'\b').lower()
                    break

        # Extract teams using custom or default pattern
        if custom_regex_teams_enabled and custom_regex_teams:
            try:
                teams_match = REGEX_MODULE.search(custom_regex_teams, stream_name, REGEX_MODULE.IGNORECASE)
                if teams_match:
                    result['team1'] = teams_match.group('team1').strip() if 'team1' in teams_match.groupdict() else None
                    result['team2'] = teams_match.group('team2').strip() if 'team2' in teams_match.groupdict() else None
                    if result['team1'] and result['team2']:
                        result['success'] = True
                        return result
                    else:
                        result['reason'] = 'Custom teams regex matched but missing team1 or team2 groups'
                else:
                    result['reason'] = 'Custom teams regex did not match stream name'
                return result
            except Exception as e:
                logger.warning(f"Custom teams regex failed: {e}")
                result['reason'] = f'Custom teams regex error: {e}'
                return result
        else:
            # Use default extraction
            normalized = self._normalize_for_stream(stream_name)
            if not normalized:
                result['reason'] = 'Stream name empty after normalization'
                return result

            away_part, home_part, split_error = self._split_matchup(normalized)
            if split_error:
                result['reason'] = split_error
                return result

            result['team1'] = away_part
            result['team2'] = home_part
            result['success'] = True

        return result

    def clear_cache(self, league: str = None) -> None:
        """
        Clear the shared team cache.

        Args:
            league: Specific league to clear, or None to clear all
        """
        global _shared_team_cache
        with _shared_team_cache_lock:
            if league:
                _shared_team_cache.pop(league.lower(), None)
            else:
                _shared_team_cache.clear()
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

    def get_all_matching_teams(self, team_text: str, league: str, max_results: int = 5) -> List[Dict]:
        """
        Get all teams that match the given text in a league, sorted by match quality.

        This is used for team disambiguation when the primary match doesn't find a game.
        For example, "Maryland" could match:
        - Maryland Terrapins (best match - exact location)
        - Maryland Eastern Shore Hawks (contains "Maryland")
        - Loyola Maryland Greyhounds (contains "Maryland")

        Args:
            team_text: Team name text to search for (e.g., "Maryland")
            league: League code (e.g., "ncaam")
            max_results: Maximum number of teams to return (default 5)

        Returns:
            List of team dicts with 'id' and 'name' keys, sorted by match quality (best first)
        """
        teams = self._get_teams_for_league(league)
        if not teams:
            return []

        # Normalize the input text
        normalized = team_text.strip().lower()
        if not normalized:
            return []

        # Use internal method to find all matches
        matching_teams = self._find_all_matching_teams(normalized, teams, max_results)

        # Return clean version without internal search names
        return [
            {'id': team.get('id'), 'name': team.get('name'), 'abbrev': team.get('abbrev', '')}
            for team in matching_teams
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
