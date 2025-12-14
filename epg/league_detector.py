"""
League Detector for Multi-Sport Event Groups

Detects the appropriate league for a stream when the group spans multiple sports/leagues.
Uses a tiered detection system with fallback strategies.

Detection Tiers:
    Tier 1: League indicator + Teams → Direct match (e.g., "NHL: Predators vs Panthers")
    Tier 2: Sport indicator + Teams → Match within sport's leagues
    Tier 3a: Both teams in cache + Date + Time + GAME FOUND → Exact schedule match
    Tier 3b: Both teams in cache + Time only + GAME FOUND → Infer today, schedule match
    Tier 3c: Both teams in cache + GAME FOUND → Closest game to now
    Tier 4a: Both teams in cache but NO GAME between them → Search schedules for RAW opponent name
    Tier 4b: One team in cache + Date/Time → Search schedule for opponent by name, exact time
    Tier 4c: One team in cache only → Search schedule for opponent by name, closest game

Tier 4 handles two cases:
1. Both teams "matched" but to WRONG teams (e.g., "IU East" → "IU Indianapolis"), so no game
   exists between them. Tier 4a searches all schedules for the RAW opponent name string.
2. Only one team is in ESPN's database (e.g., NAIA vs NCAA). Tier 4b/4c searches the known
   team's schedule for the unknown opponent by name string.

Usage:
    from epg.league_detector import LeagueDetector, DetectionResult

    detector = LeagueDetector(enabled_leagues=['nhl', 'nba', 'nfl', 'mlb'])

    result = detector.detect(
        stream_name="ESPN+ 51 : Nashville Predators vs. Florida Panthers",
        team1="Predators",
        team2="Panthers",
        game_date=None,
        game_time=None
    )

    if result.detected:
        print(f"League: {result.league} (Tier {result.tier})")
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Set, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from utils.logger import get_logger
from epg.team_matcher import CITY_NAME_VARIANTS

logger = get_logger(__name__)


def apply_name_variants(name: str) -> str:
    """
    Apply team/city name variants to match ESPN's canonical naming.
    E.g., "inter milan" → "internazionale", "hertha bsc" → "hertha berlin"
    """
    result = name.lower()
    for variant, canonical in CITY_NAME_VARIANTS.items():
        result = re.sub(r'\b' + re.escape(variant) + r'\b', canonical, result, flags=re.I)
    return result


def strip_team_numbers(name: str) -> str:
    """
    Strip standalone numbers from team name for fuzzy matching.
    Handles cases like "SV 07 Elversberg" -> "SV Elversberg"
    """
    stripped = re.sub(r'\b\d+\b', '', name)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return stripped


def get_abbreviation_variants(name: str) -> List[str]:
    """
    Generate all variants of a name with/without periods in common abbreviations.

    Handles inconsistencies in API data where team names may have periods:
    - "St. Lawrence Saints" vs "St Lawrence Saints"
    - "Mt. Rainier" vs "Mt Rainier"
    - "Ft. Wayne" vs "Ft Wayne"

    Common abbreviations handled:
    - st/st. (Saint)
    - mt/mt. (Mount)
    - ft/ft. (Fort)

    Args:
        name: Team name to generate variants for

    Returns:
        List of unique variants (always includes original)
    """
    variants = set()
    name_lower = name.lower().strip()
    variants.add(name_lower)

    # Common abbreviations that may appear with or without periods in sports team names
    # Pattern: word boundary, abbreviation, optional period, space
    abbrevs = ['st', 'mt', 'ft']

    # Create variant WITH periods: "st lawrence" -> "st. lawrence"
    with_period = name_lower
    for abbrev in abbrevs:
        # Only match if followed by space (not end of string) to avoid false positives
        with_period = re.sub(
            rf'\b{abbrev}\s+',
            f'{abbrev}. ',
            with_period,
            flags=re.IGNORECASE
        )
    variants.add(with_period)

    # Create variant WITHOUT periods: "st. lawrence" -> "st lawrence"
    without_period = name_lower
    for abbrev in abbrevs:
        without_period = re.sub(
            rf'\b{abbrev}\.\s+',
            f'{abbrev} ',
            without_period,
            flags=re.IGNORECASE
        )
    variants.add(without_period)

    # German club name abbreviation patterns
    # "Sport-Club Freiburg" -> "SC Freiburg", "Sportverein Werder" -> "SV Werder"
    german_expansions = [
        (r'\bsport[\-\s]*club\b', 'sc'),           # Sport-Club -> SC
        (r'\bsportverein\b', 'sv'),                 # Sportverein -> SV
        (r'\bfussball[\-\s]*club\b', 'fc'),        # Fussball-Club -> FC
        (r'\bfußball[\-\s]*club\b', 'fc'),         # Fußball-Club -> FC
        (r'\bturn[\-\s]*und[\-\s]*sportverein\b', 'tsv'),  # Turn- und Sportverein -> TSV
        (r'\bballspielverein\b', 'bv'),            # Ballspielverein -> BV
        (r'\bborussia\b', 'bor'),                   # Borussia -> Bor (sometimes used)
    ]
    for pattern, abbrev in german_expansions:
        abbreviated = re.sub(pattern, abbrev, name_lower, flags=re.IGNORECASE)
        if abbreviated != name_lower:
            # Also remove hyphens/dashes that might remain
            abbreviated = re.sub(r'[\-]+', ' ', abbreviated)
            abbreviated = re.sub(r'\s+', ' ', abbreviated).strip()
            variants.add(abbreviated)

    # Return unique variants (filter out duplicates)
    return list(variants)


def strip_accents(text: str) -> str:
    """
    Remove diacritical marks (accents) from text.

    Handles common accented characters in European team names:
    - Spanish: á, é, í, ó, ú, ñ, ü
    - German: ä, ö, ü, ß
    - French: à, â, ç, è, é, ê, ë, î, ï, ô, û, ù, ÿ
    - Portuguese: ã, õ

    Uses Unicode normalization (NFD) to decompose characters,
    then removes combining marks.
    """
    import unicodedata
    # NFD decomposition separates base characters from combining marks
    # e.g., 'é' becomes 'e' + combining acute accent
    nfkd = unicodedata.normalize('NFD', text)
    # Keep only characters that aren't combining marks
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def normalize_team_name(name: str, strip_articles: bool = False) -> str:
    """
    Normalize team name for fuzzy matching.

    - Removes accents (Atlético -> Atletico)
    - Strips numbers (SV 07 Elversberg -> SV Elversberg)
    - Optionally removes common articles/prepositions (Atlético de Madrid -> Atletico Madrid)
    - Normalizes whitespace

    Note: Article stripping is OFF by default because it can break team names like
    "El Salvador", "Los Angeles FC", etc. Only use as a last-resort fallback.

    Args:
        name: Team name to normalize
        strip_articles: If True, also remove articles (de, del, da, do, di, du)
                       Default False - only strip accents, numbers, and normalize whitespace.
    """
    # First strip accents - this is always safe and helps match
    # "Atletico" (stream) to "Atlético" (DB)
    normalized = strip_accents(name.lower().strip())

    if strip_articles:
        # Remove common articles/prepositions that vary between sources
        # Only standalone short words that are commonly omitted
        # Spanish: de (of), del (of the)
        # Portuguese: do, da (of the)
        # Italian: di (of)
        # French: de (of), du (of the)
        # Note: Excludes "el", "la", "los", "las", "le", "les" as these are often
        # integral to team names (El Salvador, Los Angeles, La Galaxy, etc.)
        articles = r'\b(de|del|da|do|di|du)\b'
        normalized = re.sub(articles, '', normalized, flags=re.I)

    # Strip numbers
    normalized = re.sub(r'\b\d+\b', '', normalized)

    # Normalize whitespace
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


# SQL expression to strip numbers and normalize spaces from team_name
# Used in WHERE clauses for fuzzy matching
SQL_STRIP_NUMBERS = """
    TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            LOWER(team_name), '0', ''), '1', ''), '2', ''), '3', ''), '4', ''),
        '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), '  ', ' '))
"""


# =============================================================================
# CONSTANTS
# =============================================================================

# League indicator patterns - these can appear ANYWHERE in stream name
# Maps pattern (case-insensitive) to league code
LEAGUE_INDICATORS = {
    # Hockey
    r'\bNHL\b': 'nhl',
    r'\bNational Hockey League\b': 'nhl',
    r'\bNCAA Hockey\b': 'ncaah',
    r'\bCollege Hockey\b': 'ncaah',

    # Basketball
    r'\bNBA\b': 'nba',
    r'\bNational Basketball Association\b': 'nba',
    r'\bNBA G[ -]?League\b': 'nba-g',
    r'\bG[ -]?League\b': 'nba-g',
    r'\bWNBA\b': 'wnba',
    r"\bWomen'?s NBA\b": 'wnba',
    r'\bNCAA Men\'?s Basketball\b': 'ncaam',
    r'\bMen\'?s College Basketball\b': 'ncaam',
    r'\bNCAA Women\'?s Basketball\b': 'ncaaw',
    r'\bWomen\'?s College Basketball\b': 'ncaaw',

    # Football
    r'\bNFL\b': 'nfl',
    r'\bNational Football League\b': 'nfl',
    r'\bCollege Football\b': 'ncaaf',
    r'\bNCAA Football\b': 'ncaaf',
    r'\bCFB\b': 'ncaaf',

    # Baseball
    r'\bMLB\b': 'mlb',
    r'\bMajor League Baseball\b': 'mlb',

    # Volleyball
    r'\bNCAA Men\'?s Volleyball\b': 'ncaavb-m',
    r'\bMen\'?s College Volleyball\b': 'ncaavb-m',
    r'\bNCAA Women\'?s Volleyball\b': 'ncaavb-w',
    r'\bWomen\'?s College Volleyball\b': 'ncaavb-w',

    # Soccer - Major Leagues
    r'\bEPL\b': 'eng.1',
    r'\bPremier League\b': 'eng.1',
    r'\bEnglish Premier League\b': 'eng.1',
    r'\bLa Liga\b': 'esp.1',
    r'\bLaLiga\b': 'esp.1',
    r'\bBundesliga\b': 'ger.1',
    r'\bSerie A\b': 'ita.1',
    r'\bLigue 1\b': 'fra.1',
    r'\bMLS\b': 'usa.1',
    r'\bMajor League Soccer\b': 'usa.1',
    r'\bNWSL\b': 'usa.nwsl',
    r'\bEFL\b': 'eng.2',
    r'\bEFL Championship\b': 'eng.2',

    # Soccer - UEFA Competitions
    r'\bUEFA Champions League\b': 'uefa.champions',
    r'\bChampions League\b': 'uefa.champions',
    r'\bUCL\b': 'uefa.champions',
    r'\bUEFA Europa League\b': 'uefa.europa',
    r'\bEuropa League\b': 'uefa.europa',
    r'\bUEL\b': 'uefa.europa',
    r'\bUEFA Conference League\b': 'uefa.europa.conf',
    r'\bConference League\b': 'uefa.europa.conf',
    r'\bUECL\b': 'uefa.europa.conf',
}

# Sport indicator patterns - maps to list of leagues for that sport
SPORT_INDICATORS = {
    r'\bHockey\b': ['nhl', 'ncaah'],
    r'\bBasketball\b': ['nba', 'nba-g', 'wnba', 'ncaam', 'ncaaw'],
    r'\bFootball\b': ['nfl', 'ncaaf'],
    r'\bBaseball\b': ['mlb'],
    r'\bVolleyball\b': ['ncaavb-m', 'ncaavb-w'],
    r'\bSoccer\b': ['ncaas', 'ncaaws'],  # NCAA soccer handled like other college sports
}

# =============================================================================
# LEAGUE TO SPORT MAPPING - Single source of truth: league_config table
# =============================================================================

# Cache for league_code -> sport mapping (loaded from database)
_LEAGUE_TO_SPORT_CACHE: Dict[str, str] = {}
_LEAGUE_TO_SPORT_LOADED = False


def _load_league_to_sport_cache() -> Dict[str, str]:
    """
    Load league_code -> sport mapping from league_config database.

    This is the single source of truth for which leagues belong to which sport.
    Called once on first access, then cached.

    Returns:
        Dict mapping league_code to sport name
    """
    global _LEAGUE_TO_SPORT_CACHE, _LEAGUE_TO_SPORT_LOADED

    if _LEAGUE_TO_SPORT_LOADED:
        return _LEAGUE_TO_SPORT_CACHE

    try:
        from database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT league_code, sport FROM league_config")
        for row in cursor.fetchall():
            _LEAGUE_TO_SPORT_CACHE[row[0]] = row[1]
        conn.close()
        _LEAGUE_TO_SPORT_LOADED = True
        logger.debug(f"Loaded {len(_LEAGUE_TO_SPORT_CACHE)} leagues from league_config")
    except Exception as e:
        logger.error(f"Failed to load league_config: {e}")
        # Fallback to minimal static mapping if DB fails
        _LEAGUE_TO_SPORT_CACHE = {
            'nhl': 'hockey', 'nba': 'basketball', 'nfl': 'football', 'mlb': 'baseball'
        }
        _LEAGUE_TO_SPORT_LOADED = True

    return _LEAGUE_TO_SPORT_CACHE


def get_league_to_sport() -> Dict[str, str]:
    """
    Get the league_code -> sport mapping.

    Loads from database on first call, cached thereafter.
    """
    return _load_league_to_sport_cache()


class _LazyLeagueToSport(dict):
    """Dict that loads from database on first access for backwards compatibility."""

    def __init__(self):
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self.update(_load_league_to_sport_cache())
            self._loaded = True

    def __getitem__(self, key):
        self._ensure_loaded()
        return super().__getitem__(key)

    def __contains__(self, key):
        self._ensure_loaded()
        return super().__contains__(key)

    def get(self, key, default=None):
        self._ensure_loaded()
        return super().get(key, default)

    def keys(self):
        self._ensure_loaded()
        return super().keys()

    def values(self):
        self._ensure_loaded()
        return super().values()

    def items(self):
        self._ensure_loaded()
        return super().items()

    def __iter__(self):
        self._ensure_loaded()
        return super().__iter__()

    def __len__(self):
        self._ensure_loaded()
        return super().__len__()


LEAGUE_TO_SPORT = _LazyLeagueToSport()


def get_sport_for_league(league_code: str) -> Optional[str]:
    """
    Get the sport for a league code.

    Uses league_config database as single source of truth.
    Also handles soccer leagues not yet in league_config via pattern matching.

    Args:
        league_code: ESPN API league slug (e.g., 'ger.1', 'nhl')

    Returns:
        Sport name or None if not found
    """
    sport = LEAGUE_TO_SPORT.get(league_code)
    if sport:
        return sport

    # For soccer leagues not in league_config, check if it looks like a soccer slug
    # ESPN soccer slugs follow patterns like: eng.1, ger.dfb_pokal, uefa.champions
    soccer_patterns = [
        '.1', '.2', '.3', '.4',  # Division numbers
        'uefa.', 'conmebol.', 'concacaf.', 'afc.', 'caf.',  # Continental
        'club.', 'fifa.',  # Club/international
        'cup', 'pokal', 'copa', 'super_cup',  # Cup competitions
        '.league', '.friendly',
    ]
    league_lower = league_code.lower()
    for pattern in soccer_patterns:
        if pattern in league_lower:
            return 'soccer'

    return None

# Time tolerance for schedule matching (±30 minutes)
TIME_TOLERANCE_MINUTES = 30


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DetectionResult:
    """Result of league detection for a stream."""
    detected: bool
    league: Optional[str] = None
    sport: Optional[str] = None
    tier: Optional[int] = None  # 1, 2, 3 (3a/3b/3c reported as 3)
    tier_detail: Optional[str] = None  # '1', '2', '3a', '3b', '3c'
    method: Optional[str] = None  # Human-readable description
    candidates_checked: List[str] = None  # Leagues that were considered
    event_id: Optional[str] = None  # ESPN event ID if schedule-matched
    event_date: Optional[datetime] = None  # Event date/time if matched
    league_not_enabled: bool = False  # True if found in a league user hasn't enabled

    def __post_init__(self):
        if self.candidates_checked is None:
            self.candidates_checked = []


@dataclass
class ScheduleMatch:
    """A matching event from schedule search."""
    league: str
    event_id: str
    event_date: datetime
    home_team_id: str
    away_team_id: str
    time_diff_minutes: float  # Difference from target time (or from now)


# =============================================================================
# MAIN CLASS
# =============================================================================

class LeagueDetector:
    """
    Detects the appropriate league for multi-sport event groups.

    Uses tiered detection with fallback strategies.
    """

    def __init__(
        self,
        espn_client=None,
        enabled_leagues: List[str] = None,
        lookahead_days: int = 7
    ):
        """
        Initialize LeagueDetector.

        Args:
            espn_client: ESPNClient instance for schedule queries (optional for Tier 1/2)
            enabled_leagues: List of league codes to consider (None = all non-soccer)
            lookahead_days: How many days ahead to search for games
        """
        self.espn = espn_client
        self.lookahead_days = lookahead_days

        # Default to all non-soccer leagues if not specified
        if enabled_leagues is None:
            self.enabled_leagues = list(LEAGUE_TO_SPORT.keys())
        else:
            self.enabled_leagues = [l for l in enabled_leagues if l in LEAGUE_TO_SPORT]

        # Pre-compile league indicator patterns
        self._league_patterns = [
            (re.compile(pattern, re.IGNORECASE), league)
            for pattern, league in LEAGUE_INDICATORS.items()
        ]

        # Pre-compile sport indicator patterns
        self._sport_patterns = [
            (re.compile(pattern, re.IGNORECASE), leagues)
            for pattern, leagues in SPORT_INDICATORS.items()
        ]

    def is_league_enabled(self, league: str) -> bool:
        """Check if a league is in the enabled list for this detector."""
        return league in self.enabled_leagues

    def get_league_name(self, league_code: str) -> str:
        """
        Get the friendly name for a league code.

        Looks up in league_config first, then soccer_leagues_cache.

        Args:
            league_code: ESPN league slug (e.g., 'womens-college-hockey', 'eng.1')

        Returns:
            Friendly league name or uppercase league code if not found
        """
        from database import get_connection

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Try league_config first
            cursor.execute("""
                SELECT league_name FROM league_config WHERE league_code = ?
            """, (league_code,))
            row = cursor.fetchone()
            if row:
                return row[0]

            # Try soccer_leagues_cache
            cursor.execute("""
                SELECT league_name FROM soccer_leagues_cache WHERE league_slug = ?
            """, (league_code,))
            row = cursor.fetchone()
            if row:
                return row[0]

            # Fallback to uppercase code
            return league_code.upper().replace('-', ' ')
        finally:
            conn.close()

    # ==========================================================================
    # PUBLIC API
    # ==========================================================================

    def detect(
        self,
        stream_name: str,
        team1: str = None,
        team2: str = None,
        team1_id: str = None,
        team2_id: str = None,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> DetectionResult:
        """
        Detect the league for a stream.

        Tries detection tiers in order:
        1. League indicator in stream name + teams
        2. Sport indicator in stream name + teams
        3a. Teams + date + time → schedule match
        3b. Teams + time only → infer today, schedule match
        3c. Teams only → closest game to now

        Args:
            stream_name: Raw stream name (for indicator detection)
            team1: First team name (from TeamMatcher)
            team2: Second team name (from TeamMatcher)
            team1_id: ESPN team ID for team1 (if already resolved)
            team2_id: ESPN team ID for team2 (if already resolved)
            game_date: Extracted date from stream name (or None)
            game_time: Extracted time from stream name (or None)

        Returns:
            DetectionResult with league, tier, and method details
        """
        # Tier 1: Check for explicit league indicator
        result = self._detect_tier1(stream_name, team1, team2, game_date, game_time)
        if result.detected:
            return result

        # Tier 2: Check for sport indicator
        result = self._detect_tier2(stream_name, team1, team2, game_date, game_time)
        if result.detected:
            return result

        # Tier 3: Team-based lookup with schedule disambiguation
        # NOTE: This now searches ALL leagues, not just enabled ones.
        # The enabled check happens in the caller after a successful match.
        if team1 and team2:
            result = self._detect_tier3(
                team1, team2,
                team1_id, team2_id,
                game_date, game_time
            )
            if result.detected:
                return result

        # No detection possible - we searched ALL leagues
        return DetectionResult(
            detected=False,
            method="No league detected - teams not found in any league"
        )

    def find_candidate_leagues(self, team1: str, team2: str, include_soccer: bool = True) -> List[str]:
        """
        Find ALL leagues where both teams might exist.

        Searches all leagues regardless of enabled_leagues setting.
        The enabled check happens AFTER a match is made.

        Uses TeamLeagueCache for non-soccer teams and SoccerMultiLeague for soccer.

        Args:
            team1: First team name
            team2: Second team name
            include_soccer: Whether to also check soccer leagues (default True)

        Returns:
            List of league codes where both teams exist (ALL leagues, not filtered)
        """
        from epg.team_league_cache import TeamLeagueCache

        # Get non-soccer leagues for these teams - NO enabled filter
        candidates = TeamLeagueCache.find_candidate_leagues(
            team1, team2,
            enabled_leagues=None  # Search ALL leagues
        )

        # Also check soccer leagues if enabled
        if include_soccer:
            try:
                soccer_leagues = self._find_soccer_leagues_for_teams(team1, team2)
                for league in soccer_leagues:
                    if league not in candidates:
                        candidates.append(league)
            except Exception as e:
                logger.debug(f"Error checking soccer leagues: {e}")

        return candidates

    def find_candidate_leagues_by_id(
        self,
        team1_id: str,
        team2_id: str
    ) -> List[str]:
        """
        Find ALL leagues where both team IDs exist.

        Searches all leagues regardless of enabled_leagues setting.
        The enabled check happens AFTER a match is made.

        Args:
            team1_id: ESPN team ID for first team
            team2_id: ESPN team ID for second team

        Returns:
            List of league codes where both teams exist (ALL leagues, not filtered)
        """
        from database import get_connection

        # Query by team ID
        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Find leagues for team1 in team_league_cache (US sports)
            cursor.execute("""
                SELECT DISTINCT league_code FROM team_league_cache
                WHERE espn_team_id = ?
            """, (str(team1_id),))
            leagues1 = {row[0] for row in cursor.fetchall()}

            # Also check soccer_team_leagues
            cursor.execute("""
                SELECT DISTINCT league_slug FROM soccer_team_leagues
                WHERE espn_team_id = ?
            """, (str(team1_id),))
            leagues1.update(row[0] for row in cursor.fetchall())

            # Find leagues for team2 in team_league_cache (US sports)
            cursor.execute("""
                SELECT DISTINCT league_code FROM team_league_cache
                WHERE espn_team_id = ?
            """, (str(team2_id),))
            leagues2 = {row[0] for row in cursor.fetchall()}

            # Also check soccer_team_leagues
            cursor.execute("""
                SELECT DISTINCT league_slug FROM soccer_team_leagues
                WHERE espn_team_id = ?
            """, (str(team2_id),))
            leagues2.update(row[0] for row in cursor.fetchall())

            # Intersection - NO enabled filter, search all leagues
            candidates = leagues1 & leagues2
            # Enabled check happens AFTER match is made, not here

            return list(candidates)

        finally:
            conn.close()

    def _find_soccer_leagues_for_teams(
        self,
        team1: str,
        team2: str
    ) -> List[str]:
        """
        Find soccer leagues where both teams exist using the soccer_team_leagues cache.

        Unlike TeamLeagueCache (which uses team IDs), this queries by team name
        since we may not have ESPN team IDs yet during detection.

        Args:
            team1: First team name
            team2: Second team name

        Returns:
            List of soccer league slugs where both teams exist
        """
        from database import get_connection

        if not team1 or not team2:
            return []

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Normalize team names for fuzzy matching
            # Apply name variants first (inter milan → internazionale, etc.)
            team1_lower = apply_name_variants(team1.lower().strip())
            team2_lower = apply_name_variants(team2.lower().strip())

            def find_leagues_for_team(team_name: str) -> set:
                """
                Find leagues for a team with tiered fallback search.

                Tiers:
                0. Exact abbreviation match (PSG, ATH, BAR)
                1. Direct match with abbreviation variants (st/st., mt/mt.)
                2. Accent-normalized match (Atletico -> Atlético)
                3. Number-stripped match (SV Elversberg -> SV 07 Elversberg)
                4. Article-stripped match (Atlético de Madrid -> Atlético Madrid)
                """
                leagues = set()
                team_lower = team_name.lower().strip()

                # Tier 0a: Exact abbreviation match (e.g., PSG, ATH, BAR)
                cursor.execute("""
                    SELECT DISTINCT league_slug FROM soccer_team_leagues
                    WHERE LOWER(team_abbrev) = ?
                """, (team_lower,))
                for row in cursor.fetchall():
                    leagues.add(row[0])

                if leagues:
                    logger.debug(f"Found leagues via abbreviation match: '{team_name}'")
                    return leagues

                # Tier 1: Direct match with abbreviation variants (st/st., mt/mt.)
                for variant in get_abbreviation_variants(team_name):
                    # Note: INSTR(search, db_name) checks if db_name is a substring of search.
                    # Only allow this for db names >= 6 chars to avoid "Sport", "Port" false positives
                    cursor.execute("""
                        SELECT DISTINCT league_slug FROM soccer_team_leagues
                        WHERE LOWER(team_name) LIKE ?
                           OR LOWER(team_name) LIKE ?
                           OR (INSTR(?, LOWER(team_name)) > 0 AND LENGTH(team_name) >= 6)
                    """, (f"%{variant}%", f"{variant}%", variant))
                    for row in cursor.fetchall():
                        leagues.add(row[0])

                if leagues:
                    return leagues

                # Tier 2: Accent-normalized match
                # Handles "Atletico" (stream) matching "Atlético" (DB)
                # SQLite can't strip accents natively, so we fetch candidates and filter
                search_normalized = strip_accents(team_name)
                # Always try accent-normalized search - the search term may not have accents
                # but the DB values do (Atletico in stream, Atlético in DB)
                cursor.execute("""
                    SELECT league_slug, team_name FROM soccer_team_leagues
                """)
                for row in cursor.fetchall():
                    db_normalized = strip_accents(row[1].lower())
                    # Check if search is in DB or DB is in search
                    # But only allow short DB names (< 6 chars) in search if they're exact word matches
                    if search_normalized in db_normalized:
                        leagues.add(row[0])
                    elif db_normalized in search_normalized and len(db_normalized) >= 6:
                        # Only allow substring match for longer team names to avoid
                        # "Sport" or "Port" matching everything
                        leagues.add(row[0])
                if leagues:
                    logger.debug(f"Found leagues via accent-stripped search: '{team_name}' -> '{search_normalized}'")
                    return leagues

                # Tier 3: Strip numbers from both search term and DB values
                # Handles "SV Elversberg" matching "SV 07 Elversberg"
                # IMPORTANT: Always run DB-side stripping even if search has no numbers,
                # because the DB value might have numbers (e.g., "SV 07 Elversberg")
                stripped = normalize_team_name(team_name, strip_articles=False)

                if stripped:
                    cursor.execute(f"""
                        SELECT DISTINCT league_slug FROM soccer_team_leagues
                        WHERE {SQL_STRIP_NUMBERS} LIKE ?
                    """, (f"%{stripped}%",))
                    leagues = {row[0] for row in cursor.fetchall()}
                    if leagues:
                        logger.debug(f"Found leagues via number-stripped search: '{team_name}' -> '{stripped}'")
                        return leagues

                # Tier 4: Also strip common articles (de, del, da, do, di, du)
                # Handles "Atlético de Madrid" matching "Atlético Madrid"
                # This is conservative - only strips prepositions, not "el/la/los/las"
                normalized = normalize_team_name(team_name, strip_articles=True)

                if normalized and normalized != stripped:
                    # Search for normalized name anywhere in DB team name (accent-stripped)
                    cursor.execute("""
                        SELECT league_slug, team_name FROM soccer_team_leagues
                    """)
                    for row in cursor.fetchall():
                        db_normalized = strip_accents(row[1].lower())
                        # Strip articles from DB value too for comparison
                        db_articles_stripped = re.sub(r'\b(de|del|da|do|di|du)\b', '', db_normalized, flags=re.I)
                        db_articles_stripped = re.sub(r'\s+', ' ', db_articles_stripped).strip()
                        if normalized in db_articles_stripped:
                            leagues.add(row[0])
                    if leagues:
                        logger.debug(f"Found leagues via article-stripped search: '{team_name}' -> '{normalized}'")
                        return leagues

                # Tier 5: Strip common club suffixes (CF, FC, SC, AFC, etc.)
                # Handles "Elche CF" matching "Elche", "Girona FC" matching "Girona"
                # These suffixes are often added by stream providers but not in ESPN names
                # NOTE: Do NOT strip "Town", "City", "United" - causes ambiguity (Manchester City vs United)
                suffix_pattern = r'\s+(cf|fc|sc|afc|sfc|ac|bc|fk|sk|if|bk|ik|sv|vfl|vfb|tsv|fsv|spvgg)$'
                suffix_stripped = re.sub(suffix_pattern, '', team_name, flags=re.I).strip()

                if suffix_stripped and suffix_stripped != team_name:
                    # Try direct match with suffix stripped
                    cursor.execute("""
                        SELECT DISTINCT league_slug FROM soccer_team_leagues
                        WHERE LOWER(team_name) LIKE ?
                           OR LOWER(team_name) = ?
                    """, (f"%{suffix_stripped}%", suffix_stripped))
                    leagues = {row[0] for row in cursor.fetchall()}
                    if leagues:
                        logger.debug(f"Found leagues via suffix-stripped search: '{team_name}' -> '{suffix_stripped}'")
                        return leagues

                # Word-overlap / longest-word matching DISABLED
                # Too risky - "manchester" matches Man City AND Man United,
                # "madrid" matches Real, Atlético, Rayo, etc.
                # Originally intended for city name transliterations (München/Munich, Köln/Cologne)
                # but ESPN already uses English names, and accent-stripping (Tier 2) handles accented variants.
                # The word-overlap logic caused many false positives:
                #   - "Tampa Bay Lightning" (NHL) matching "Monterey Bay F.C." (soccer) via 'bay'
                #   - "Chicago Bulls" (NBA) matching "Chicago Fire FC" (soccer) via 'chicago'
                # Net negative value - disabled entirely.

                return leagues

            # Find leagues for team1
            leagues1 = find_leagues_for_team(team1_lower)

            if not leagues1:
                return []

            # Find leagues for team2
            leagues2 = find_leagues_for_team(team2_lower)

            if not leagues2:
                return []

            # Intersection - leagues where both teams exist
            candidates = leagues1 & leagues2

            logger.debug(
                f"Soccer leagues for '{team1}' vs '{team2}': "
                f"team1={len(leagues1)}, team2={len(leagues2)}, "
                f"intersection={len(candidates)}"
            )

            return list(candidates)

        except Exception as e:
            logger.debug(f"Error querying soccer_team_leagues: {e}")
            return []
        finally:
            conn.close()

    def get_soccer_team_ids_for_league(
        self,
        team1: str,
        team2: str,
        league_slug: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get ESPN team IDs for a team pair in a specific soccer league.

        This is used for disambiguation when teams with the same name exist
        in multiple leagues with different IDs (e.g., Arsenal in EPL vs WSL).

        Uses tiered matching:
        1. Direct match (exact or substring)
        2. Number-stripped match (SV 07 Elversberg -> SV Elversberg)
        3. Article-stripped match (Atlético de Madrid -> Atlético Madrid)

        Args:
            team1: First team name
            team2: Second team name
            league_slug: ESPN soccer league slug (e.g., 'eng.w.1', 'eng.1')

        Returns:
            Dict with team1_id, team1_name, team2_id, team2_name or None if not found
        """
        from database import get_connection

        if not team1 or not team2 or not league_slug:
            return None

        conn = get_connection()
        try:
            cursor = conn.cursor()

            def find_team_in_league(team_name: str, league: str) -> Optional[tuple]:
                """
                Find a team in a league with tiered fallback matching.

                Tiers:
                0. Exact abbreviation match (PSG -> Paris Saint-Germain)
                1. Direct match with abbreviation variants (st/st., mt/mt.)
                2. Accent-normalized match (Atletico -> Atlético)
                3. Number-stripped match (SV Elversberg -> SV 07 Elversberg)
                4. Article-stripped match (Atlético de Madrid -> Atlético Madrid)
                """
                # Apply name variants first (inter milan → internazionale, etc.)
                team_lower = apply_name_variants(team_name.lower().strip())
                team_accent_stripped = strip_accents(team_lower)
                team_stripped = normalize_team_name(team_lower, strip_articles=False)
                team_normalized = normalize_team_name(team_lower, strip_articles=True)

                # Tier 0: Exact abbreviation match (e.g., PSG, ATH, BAR)
                # This handles common stream abbreviations that don't substring-match team names
                cursor.execute("""
                    SELECT espn_team_id, team_name FROM soccer_team_leagues
                    WHERE league_slug = ? AND LOWER(team_abbrev) = ?
                    LIMIT 1
                """, (league, team_lower))
                row = cursor.fetchone()
                if row:
                    logger.debug(f"Team '{team_name}' matched via abbreviation: {row[1]}")
                    return row

                # Tier 1: Direct match with abbreviation variants (st/st., mt/mt.)
                # IMPORTANT: INSTR checks require LENGTH >= 6 to avoid "Sport", "Port" false positives
                for variant in get_abbreviation_variants(team_name):
                    cursor.execute("""
                        SELECT espn_team_id, team_name FROM soccer_team_leagues
                        WHERE league_slug = ? AND (
                            LOWER(team_name) LIKE ?
                            OR LOWER(team_name) LIKE ?
                            OR (INSTR(?, LOWER(team_name)) > 0 AND LENGTH(team_name) >= 6)
                        )
                        ORDER BY LENGTH(team_name) ASC
                        LIMIT 1
                    """, (league, f"%{variant}%", f"{variant}%", variant))
                    row = cursor.fetchone()
                    if row:
                        return row

                # Tier 2: Accent-normalized match
                # Handles "Atletico" (stream) matching "Atlético" (DB)
                cursor.execute("""
                    SELECT espn_team_id, team_name FROM soccer_team_leagues
                    WHERE league_slug = ?
                """, (league,))
                for row in cursor.fetchall():
                    db_normalized = strip_accents(row[1].lower())
                    if team_accent_stripped in db_normalized or db_normalized in team_accent_stripped:
                        logger.debug(f"Team '{team_name}' matched via accent-stripping: {row[1]}")
                        return row

                # Tier 3: Number-stripped match
                # IMPORTANT: Always run DB-side stripping even if search has no numbers,
                # because the DB value might have numbers (e.g., "SV 07 Elversberg")
                if team_stripped:
                    cursor.execute(f"""
                        SELECT espn_team_id, team_name FROM soccer_team_leagues
                        WHERE league_slug = ? AND (
                            {SQL_STRIP_NUMBERS} LIKE ?
                            OR {SQL_STRIP_NUMBERS} LIKE ?
                            OR INSTR(?, {SQL_STRIP_NUMBERS}) > 0
                        )
                        ORDER BY LENGTH(team_name) ASC
                        LIMIT 1
                    """, (league, f"%{team_stripped}%", f"{team_stripped}%", team_stripped))
                    row = cursor.fetchone()
                    if row:
                        logger.debug(f"Team '{team_name}' matched via number-stripping: {row[1]}")
                        return row

                # Tier 4: Article-stripped match (de, del, da, do, di, du)
                if team_normalized and team_normalized != team_stripped:
                    # Search with both accent and article stripping
                    cursor.execute("""
                        SELECT espn_team_id, team_name FROM soccer_team_leagues
                        WHERE league_slug = ?
                    """, (league,))
                    for row in cursor.fetchall():
                        db_normalized = strip_accents(row[1].lower())
                        # Strip articles from DB value too
                        db_articles_stripped = re.sub(r'\b(de|del|da|do|di|du)\b', '', db_normalized, flags=re.I)
                        db_articles_stripped = re.sub(r'\s+', ' ', db_articles_stripped).strip()
                        if team_normalized in db_articles_stripped:
                            logger.debug(f"Team '{team_name}' matched via article-stripping: {row[1]}")
                            return row

                # Word-overlap matching DISABLED (v1.4.1)
                # See comment in _find_soccer_leagues_for_teams() for rationale.

                return None

            # Find both teams
            row1 = find_team_in_league(team1, league_slug)
            if not row1:
                logger.debug(f"Team '{team1}' not found in {league_slug}")
                return None

            row2 = find_team_in_league(team2, league_slug)
            if not row2:
                logger.debug(f"Team '{team2}' not found in {league_slug}")
                return None

            logger.debug(
                f"Soccer team IDs for '{team1}' vs '{team2}' in {league_slug}: "
                f"{row1[0]} ({row1[1]}) vs {row2[0]} ({row2[1]})"
            )

            return {
                'team1_id': str(row1[0]),
                'team1_name': row1[1],
                'team2_id': str(row2[0]),
                'team2_name': row2[1],
                'league_slug': league_slug
            }

        except Exception as e:
            logger.debug(f"Error getting soccer team IDs: {e}")
            return None
        finally:
            conn.close()

    def get_soccer_candidates_with_team_ids(
        self,
        team1: str,
        team2: str
    ) -> List[Dict[str, Any]]:
        """
        Get all soccer league candidates with league-specific team IDs.

        This is the key method for soccer disambiguation. It finds all leagues
        where both teams exist and returns the correct team IDs for each league.
        This handles cases like Arsenal in EPL (ID 359) vs WSL (ID 19973).

        Args:
            team1: First team name from stream
            team2: Second team name from stream

        Returns:
            List of dicts with:
                - league_slug: ESPN slug (e.g., 'eng.w.1')
                - league_code: Mapped code if in league_config, else None
                - api_path_override: For unmapped leagues, the API path (e.g., 'soccer/eng.w.1')
                - team1_id, team1_name: First team info for this league
                - team2_id, team2_name: Second team info for this league
        """
        from database import get_soccer_slug_mapping

        if not team1 or not team2:
            return []

        # Find all leagues where both teams exist
        soccer_slugs = self._find_soccer_leagues_for_teams(team1, team2)
        if not soccer_slugs:
            return []

        # Get mapping from slug to league_config code
        slug_to_code = get_soccer_slug_mapping()

        candidates = []
        for slug in soccer_slugs[:10]:  # Limit to first 10
            # Get league-specific team IDs
            team_ids = self.get_soccer_team_ids_for_league(team1, team2, slug)
            if not team_ids:
                continue

            # Check if this slug is mapped to a league_config code
            league_code = slug_to_code.get(slug)

            candidates.append({
                'league_slug': slug,
                'league_code': league_code,  # None if not in league_config
                'api_path_override': None if league_code else f"soccer/{slug}",
                'team1_id': team_ids['team1_id'],
                'team1_name': team_ids['team1_name'],
                'team2_id': team_ids['team2_id'],
                'team2_name': team_ids['team2_name']
            })

        logger.debug(
            f"Soccer candidates for '{team1}' vs '{team2}': "
            f"{len(candidates)} leagues with team IDs"
        )

        return candidates

    def diagnose_team_match_failure(
        self,
        team1: str,
        team2: str,
        stream_name: str = None
    ) -> Dict[str, Any]:
        """
        Diagnose why teams couldn't be matched to a common league.

        Provides detailed info about which teams were found, in which leagues,
        and why no common league exists.

        Args:
            team1: First team name from stream
            team2: Second team name from stream
            stream_name: Optional full stream name (for detecting boxing/MMA)

        Returns:
            Dict with diagnostic info:
                - team1_found: bool
                - team1_leagues: list of leagues team1 exists in
                - team2_found: bool
                - team2_leagues: list of leagues team2 exists in
                - common_leagues: list of common leagues (should be empty if called)
                - reason: FilterReason constant
                - detail: Human-readable explanation
        """
        from database import get_connection
        from utils.match_result import FilteredReason, FailedReason
        from utils.filter_reasons import FilterReason  # For backwards compat

        if not team1 or not team2:
            return {
                'team1_found': False,
                'team2_found': False,
                'team1_leagues': [],
                'team2_leagues': [],
                'common_leagues': [],
                'reason': FailedReason.TEAMS_NOT_PARSED,
                'detail': 'Team names not provided'
            }

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Get all variants of team names (with/without periods in abbreviations)
            team1_variants = get_abbreviation_variants(team1)
            team2_variants = get_abbreviation_variants(team2)

            # Build dynamic query for all team1 variants
            # Each variant gets a LIKE clause in both soccer and US sports caches
            # IMPORTANT: INSTR checks require LENGTH >= 6 to avoid "Sport", "Port" false positives
            team1_leagues = set()
            for variant in team1_variants:
                cursor.execute("""
                    SELECT DISTINCT league_slug, team_name FROM soccer_team_leagues
                    WHERE LOWER(team_name) LIKE ?
                       OR (INSTR(?, LOWER(team_name)) > 0 AND LENGTH(team_name) >= 6)
                    UNION
                    SELECT DISTINCT league_code, team_name FROM team_league_cache
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_short_name) LIKE ?
                       OR (INSTR(?, LOWER(team_name)) > 0 AND LENGTH(team_name) >= 6)
                       OR (INSTR(?, LOWER(team_short_name)) > 0 AND LENGTH(team_short_name) >= 6)
                """, (f"%{variant}%", variant, f"%{variant}%", f"%{variant}%", variant, variant))
                for row in cursor.fetchall():
                    team1_leagues.add(row[0])
            team1_leagues = list(team1_leagues)
            team1_found = len(team1_leagues) > 0

            # Build dynamic query for all team2 variants
            # IMPORTANT: INSTR checks require LENGTH >= 6 to avoid "Sport", "Port" false positives
            team2_leagues = set()
            for variant in team2_variants:
                cursor.execute("""
                    SELECT DISTINCT league_slug, team_name FROM soccer_team_leagues
                    WHERE LOWER(team_name) LIKE ?
                       OR (INSTR(?, LOWER(team_name)) > 0 AND LENGTH(team_name) >= 6)
                    UNION
                    SELECT DISTINCT league_code, team_name FROM team_league_cache
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_short_name) LIKE ?
                       OR (INSTR(?, LOWER(team_name)) > 0 AND LENGTH(team_name) >= 6)
                       OR (INSTR(?, LOWER(team_short_name)) > 0 AND LENGTH(team_short_name) >= 6)
                """, (f"%{variant}%", variant, f"%{variant}%", f"%{variant}%", variant, variant))
                for row in cursor.fetchall():
                    team2_leagues.add(row[0])
            team2_leagues = list(team2_leagues)
            team2_found = len(team2_leagues) > 0

            # Find common leagues
            common_leagues = list(set(team1_leagues) & set(team2_leagues))

            # Determine reason and detail using new match_result types
            from utils.match_result import is_beach_soccer, is_boxing_mma, is_futsal

            if not team1_found and not team2_found:
                # Neither team found - check if it's an unsupported sport as FINAL fallback
                # This gives better user feedback than just "teams not found"
                if stream_name and is_boxing_mma(stream_name):
                    reason = FilteredReason.UNSUPPORTED_BOXING_MMA
                    detail = "Boxing/MMA not supported by ESPN API"
                    logger.debug(f"[FILTERED:unsupported_boxing_mma] {stream_name[:60]}")
                elif is_beach_soccer(team1, team2):
                    reason = FilteredReason.UNSUPPORTED_BEACH_SOCCER
                    detail = "Beach soccer not supported by ESPN API"
                    logger.debug(f"[FILTERED:unsupported_beach_soccer] '{team1}' vs '{team2}'")
                elif is_futsal(team1, team2):
                    reason = FilteredReason.UNSUPPORTED_FUTSAL
                    detail = "Futsal not supported by ESPN API"
                    logger.debug(f"[FILTERED:unsupported_futsal] '{team1}' vs '{team2}'")
                else:
                    reason = FailedReason.BOTH_TEAMS_NOT_FOUND
                    detail = f"Neither '{team1}' nor '{team2}' found in ESPN database"
            elif not team1_found:
                # Only team1 not found - check if it looks like unsupported sport
                if is_beach_soccer(team1, None):
                    reason = FilteredReason.UNSUPPORTED_BEACH_SOCCER
                    detail = "Beach soccer not supported by ESPN API"
                elif is_futsal(team1, None):
                    reason = FilteredReason.UNSUPPORTED_FUTSAL
                    detail = "Futsal not supported by ESPN API"
                else:
                    reason = FailedReason.TEAM1_NOT_FOUND
                    detail = f"'{team1}' not found in ESPN database"
            elif not team2_found:
                # Only team2 not found - check if it looks like unsupported sport
                if is_beach_soccer(None, team2):
                    reason = FilteredReason.UNSUPPORTED_BEACH_SOCCER
                    detail = "Beach soccer not supported by ESPN API"
                elif is_futsal(None, team2):
                    reason = FilteredReason.UNSUPPORTED_FUTSAL
                    detail = "Futsal not supported by ESPN API"
                else:
                    reason = FailedReason.TEAM2_NOT_FOUND
                    detail = f"'{team2}' not found in ESPN database"
            elif not common_leagues:
                reason = FailedReason.NO_COMMON_LEAGUE
                detail = (f"'{team1}' in [{', '.join(team1_leagues[:3])}{'...' if len(team1_leagues) > 3 else ''}], "
                         f"'{team2}' in [{', '.join(team2_leagues[:3])}{'...' if len(team2_leagues) > 3 else ''}] - no common league")
            else:
                # Common leagues found but teams didn't match - likely event not found
                # This happens when teams exist in DB but couldn't match to event
                reason = FailedReason.NO_EVENT_FOUND
                detail = f"Common leagues found: {common_leagues}"

            return {
                'team1_found': team1_found,
                'team1_leagues': team1_leagues[:5],  # Limit to 5
                'team2_found': team2_found,
                'team2_leagues': team2_leagues[:5],  # Limit to 5
                'common_leagues': common_leagues,
                'reason': reason,
                'detail': detail
            }
        finally:
            conn.close()

    # ==========================================================================
    # TIER 1: League Indicator Detection
    # ==========================================================================

    def _detect_tier1(
        self,
        stream_name: str,
        team1: str,
        team2: str,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> DetectionResult:
        """
        Tier 1: Detect league from explicit league indicator in stream name.

        Example: "NHL: Predators vs Panthers" → NHL

        Note: Does NOT filter by enabled_leagues - the enabled check happens
        in the caller after a successful match.

        IMPORTANT: Only returns success if an event_id is found!
        """
        detected_league = None

        for pattern, league in self._league_patterns:
            if pattern.search(stream_name):
                # Found a league indicator - use it (don't filter by enabled)
                detected_league = league
                break

        if not detected_league:
            return DetectionResult(detected=False)

        # NOTE: We no longer validate teams exist in league cache before searching schedules.
        # The league indicator is trusted - if teams don't match, the schedule search will fail.
        # This avoids false negatives from abbreviations (PSG, ATH) not being in the soccer cache.
        # The schedule search uses team_matcher which handles abbreviations properly.

        sport = get_sport_for_league(detected_league)

        # CRITICAL: Must verify a game exists and get event_id
        # League indicator alone is not sufficient for success
        if not self.espn or not team1 or not team2:
            logger.debug(f"Tier 1: League indicator {detected_league} found but cannot verify game (no ESPN client or teams)")
            return DetectionResult(
                detected=False,
                method=f"League indicator {detected_league} found but cannot verify game exists",
                candidates_checked=[detected_league]
            )

        # Search for actual game - pass team NAMES, not IDs
        # Team IDs are resolved per-league inside _search_schedules
        schedule_result = self._search_schedules(
            team1, team2,
            [detected_league], game_time, TIME_TOLERANCE_MINUTES if game_time else None
        )

        if not schedule_result:
            logger.debug(f"Tier 1: League indicator {detected_league} found but no game between teams")
            return DetectionResult(
                detected=False,
                method=f"League indicator {detected_league} found but no game between teams",
                candidates_checked=[detected_league]
            )

        closest = schedule_result[0]
        return DetectionResult(
            detected=True,
            league=detected_league,
            sport=sport,
            tier=1,
            tier_detail='1',
            method=f"League indicator '{detected_league.upper()}' + game verified",
            candidates_checked=[detected_league],
            event_id=closest.event_id,
            event_date=closest.event_date
        )

    # ==========================================================================
    # TIER 2: Sport Indicator Detection
    # ==========================================================================

    def _detect_tier2(
        self,
        stream_name: str,
        team1: str,
        team2: str,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> DetectionResult:
        """
        Tier 2: Detect league from sport indicator + team lookup.

        Example: "Hockey: Predators vs Panthers" + teams in NHL → NHL

        Note: Does NOT filter by enabled_leagues - the enabled check happens
        in the caller after a successful match.

        IMPORTANT: Only returns success if an event_id is found!
        """
        detected_sport = None
        sport_leagues = []

        for pattern, leagues in self._sport_patterns:
            if pattern.search(stream_name):
                detected_sport = pattern.pattern.strip(r'\b')
                # Don't filter by enabled - search all leagues for this sport
                sport_leagues = leagues
                break

        if not sport_leagues:
            return DetectionResult(detected=False)

        # If we have teams, find which sport league(s) they're in
        if team1 and team2:
            candidates = self.find_candidate_leagues(team1, team2)
            matching_leagues = [l for l in candidates if l in sport_leagues]

            if len(matching_leagues) == 1:
                league = matching_leagues[0]

                # CRITICAL: Must verify a game exists and get event_id
                if not self.espn:
                    logger.debug(f"Tier 2: Sport {detected_sport} + league {league} but no ESPN client")
                    return DetectionResult(
                        detected=False,
                        method=f"Sport indicator '{detected_sport}' + {league} but cannot verify game",
                        candidates_checked=sport_leagues
                    )

                # Search for actual game - pass team NAMES, not IDs
                # Team IDs are resolved per-league inside _search_schedules
                schedule_result = self._search_schedules(
                    team1, team2,
                    [league], game_time, TIME_TOLERANCE_MINUTES if game_time else None
                )

                if not schedule_result:
                    logger.debug(f"Tier 2: Sport {detected_sport} + league {league} but no game between teams")
                    return DetectionResult(
                        detected=False,
                        method=f"Sport indicator '{detected_sport}' + {league} but no game between teams",
                        candidates_checked=sport_leagues
                    )

                closest = schedule_result[0]
                return DetectionResult(
                    detected=True,
                    league=league,
                    sport=get_sport_for_league(league),
                    tier=2,
                    tier_detail='2',
                    method=f"Sport indicator '{detected_sport}' + game verified in {league.upper()}",
                    candidates_checked=sport_leagues,
                    event_id=closest.event_id,
                    event_date=closest.event_date
                )
            elif len(matching_leagues) > 1:
                # Multiple leagues within sport - need Tier 3 disambiguation
                logger.debug(
                    f"Tier 2: Sport {detected_sport} found, multiple league matches: {matching_leagues}"
                )
                return DetectionResult(detected=False, candidates_checked=matching_leagues)
            else:
                # No matching leagues for these teams
                logger.debug(
                    f"Tier 2: Sport {detected_sport} found but teams not in any {sport_leagues}"
                )
                return DetectionResult(detected=False, candidates_checked=sport_leagues)

        # Sport indicator but no teams to validate
        return DetectionResult(detected=False)

    # ==========================================================================
    # TIER 3: Schedule-Based Disambiguation
    # ==========================================================================

    def _detect_tier3(
        self,
        team1: str,
        team2: str,
        team1_id: str = None,
        team2_id: str = None,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> DetectionResult:
        """
        Tier 3: Detect league via team lookup + schedule disambiguation.

        3a: Date + time → exact schedule match
        3b: Time only → infer today, exact schedule match
        3c: Teams only → closest game to now
        """
        # Find candidate leagues for these teams
        if team1_id and team2_id:
            candidates = self.find_candidate_leagues_by_id(team1_id, team2_id)
        else:
            candidates = self.find_candidate_leagues(team1, team2)

        if not candidates:
            # FINAL FALLBACK: Single-team schedule search
            # If one team is in our caches but the other isn't (e.g., NAIA vs NCAA),
            # search the known team's schedule for opponent by NAME
            fallback_result = self._single_team_schedule_fallback(
                team1, team2, game_date, game_time
            )
            if fallback_result and fallback_result.detected:
                return fallback_result

            return DetectionResult(
                detected=False,
                method=f"No leagues found containing both '{team1}' and '{team2}'"
            )

        if len(candidates) == 1:
            # Single candidate - but we still need to verify a game exists
            # If no game exists, the teams may have been matched incorrectly
            league = candidates[0]

            # Try to verify a game exists (if we have ESPN client)
            if self.espn:
                # Search for actual game - pass team NAMES, not IDs
                # Team IDs are resolved per-league inside _search_schedules
                # This is critical because the same team name can have different IDs
                # in different leagues (e.g., Iowa State = 66 in volleyball, 20535 in soccer)
                schedule_result = self._search_schedules(
                    team1, team2,
                    [league], game_time, TIME_TOLERANCE_MINUTES if game_time else None
                )
                if schedule_result:
                    # Game found - Tier 3 success
                    closest = schedule_result[0]
                    tier_detail = '3a' if game_date and game_time else '3b' if game_time else '3c'
                    return DetectionResult(
                        detected=True,
                        league=league,
                        sport=get_sport_for_league(league),
                        tier=3,
                        tier_detail=tier_detail,
                        method=f"Game found in {league.upper()}",
                        candidates_checked=candidates,
                        event_id=closest.event_id,
                        event_date=closest.event_date
                    )
                else:
                    # No game between matched teams - try Tier 4a
                    logger.debug(
                        f"Single candidate {league} but no game between matched teams, "
                        f"trying Tier 4a schedule search"
                    )
                    fallback_result = self._schedule_search_fallback(
                        team1, team2, game_date, game_time
                    )
                    if fallback_result and fallback_result.detected:
                        return fallback_result

            # Cannot verify game exists - NO SUCCESS without event_id
            # (happens when no ESPN client)
            logger.debug(
                f"Single candidate {league} but cannot verify game exists (no ESPN client)"
            )
            return DetectionResult(
                detected=False,
                method=f"League {league.upper()} found but cannot verify game exists",
                candidates_checked=candidates
            )

        # Multiple candidates - need schedule disambiguation
        if not self.espn:
            logger.warning("Multiple league candidates but no ESPN client for schedule check")
            return DetectionResult(
                detected=False,
                method=f"Multiple candidates {candidates} but no ESPN client",
                candidates_checked=candidates
            )

        # Team IDs are now resolved PER-LEAGUE in _search_schedules
        # This is critical because the same team name can have different IDs
        # in different leagues (e.g., Iowa State = 66 in volleyball, 20535 in soccer)
        # Pass team NAMES to tier detection, not pre-resolved IDs

        # Determine tier based on available date/time
        if game_date and game_time:
            # Tier 3a: Exact date + time
            return self._detect_tier3a(
                team1,  # Pass name, not ID
                team2,  # Pass name, not ID
                candidates,
                game_date,
                game_time
            )
        elif game_time:
            # Tier 3b: Time only, infer today
            return self._detect_tier3b(
                team1,  # Pass name, not ID
                team2,  # Pass name, not ID
                candidates,
                game_time
            )
        else:
            # Tier 3c: Teams only, find closest game
            return self._detect_tier3c(
                team1,  # Pass name, not ID
                team2,  # Pass name, not ID
                candidates
            )

    def _detect_tier3a(
        self,
        team1: str,
        team2: str,
        candidates: List[str],
        game_date: datetime,
        game_time: datetime
    ) -> DetectionResult:
        """
        Tier 3a: Date + time available, find exact schedule match.
        """
        # Combine date and time
        target_dt = datetime.combine(
            game_date.date(),
            game_time.time(),
            tzinfo=game_time.tzinfo or ZoneInfo('UTC')
        )

        matches = self._search_schedules(
            team1, team2, candidates,
            target_datetime=target_dt,
            tolerance_minutes=TIME_TOLERANCE_MINUTES
        )

        if len(matches) == 1:
            match = matches[0]
            return DetectionResult(
                detected=True,
                league=match.league,
                sport=get_sport_for_league(match.league),
                tier=3,
                tier_detail='3a',
                method=f"Schedule match: {match.league.upper()} at {match.event_date}",
                candidates_checked=candidates,
                event_id=match.event_id,
                event_date=match.event_date
            )
        elif len(matches) > 1:
            # Multiple matches - extremely rare (identical game times)
            leagues = list(set(m.league for m in matches))
            logger.warning(f"Tier 3a: Multiple schedule matches: {leagues}")
            return DetectionResult(
                detected=False,
                method=f"Ambiguous: multiple games at {target_dt} in {leagues}",
                candidates_checked=candidates
            )
        else:
            return DetectionResult(
                detected=False,
                method=f"No game found at {target_dt} in {candidates}",
                candidates_checked=candidates
            )

    def _detect_tier3b(
        self,
        team1: str,
        team2: str,
        candidates: List[str],
        game_time: datetime
    ) -> DetectionResult:
        """
        Tier 3b: Time only, infer today's date.
        """
        # Use today's date with the given time
        now = datetime.now(ZoneInfo('UTC'))
        target_dt = datetime.combine(
            now.date(),
            game_time.time(),
            tzinfo=game_time.tzinfo or ZoneInfo('UTC')
        )

        matches = self._search_schedules(
            team1, team2, candidates,
            target_datetime=target_dt,
            tolerance_minutes=TIME_TOLERANCE_MINUTES
        )

        if len(matches) == 1:
            match = matches[0]
            return DetectionResult(
                detected=True,
                league=match.league,
                sport=get_sport_for_league(match.league),
                tier=3,
                tier_detail='3b',
                method=f"Schedule match (inferred today): {match.league.upper()} at {match.event_date}",
                candidates_checked=candidates,
                event_id=match.event_id,
                event_date=match.event_date
            )
        elif len(matches) > 1:
            leagues = list(set(m.league for m in matches))
            logger.warning(f"Tier 3b: Multiple schedule matches: {leagues}")
            return DetectionResult(
                detected=False,
                method=f"Ambiguous: multiple games at {target_dt} in {leagues}",
                candidates_checked=candidates
            )
        else:
            return DetectionResult(
                detected=False,
                method=f"No game found today at {game_time.strftime('%H:%M')} in {candidates}",
                candidates_checked=candidates
            )

    def _detect_tier3c(
        self,
        team1: str,
        team2: str,
        candidates: List[str]
    ) -> DetectionResult:
        """
        Tier 3c: Teams only, find closest game to now.
        """
        matches = self._search_schedules(
            team1, team2, candidates,
            target_datetime=None,  # Will search full lookahead
            tolerance_minutes=None  # Return all matches
        )

        if not matches:
            return DetectionResult(
                detected=False,
                method=f"No upcoming games found for teams in {candidates}",
                candidates_checked=candidates
            )

        # Sort by absolute time difference from now
        now = datetime.now(ZoneInfo('UTC'))
        matches.sort(key=lambda m: abs((m.event_date - now).total_seconds()))

        closest = matches[0]

        # Check for tie (multiple games equally close - within 5 minutes)
        if len(matches) > 1:
            second = matches[1]
            time_diff = abs((closest.event_date - second.event_date).total_seconds())
            if time_diff < 300:  # 5 minutes
                leagues = [closest.league, second.league]
                logger.warning(f"Tier 3c: Tie between {leagues}")
                return DetectionResult(
                    detected=False,
                    method=f"Tie: games in {leagues} at similar times",
                    candidates_checked=candidates
                )

        return DetectionResult(
            detected=True,
            league=closest.league,
            sport=get_sport_for_league(closest.league),
            tier=3,
            tier_detail='3c',
            method=f"Closest game: {closest.league.upper()} at {closest.event_date}",
            candidates_checked=candidates,
            event_id=closest.event_id,
            event_date=closest.event_date
        )

    # ==========================================================================
    # TIER 4: SCHEDULE SEARCH FALLBACK
    # ==========================================================================
    #
    # Tier 4 is used when Tier 3 (both teams in cache with game found) fails.
    # It searches team schedules looking for opponent by NAME string.
    #
    # Sub-tiers:
    #   4a: Both teams in cache but no game between them → search each team's
    #       schedule for the RAW opponent name (handles wrong cache matches)
    #   4b: One team in cache + date/time → search for opponent name, exact time
    #   4c: One team in cache only → search for opponent name, closest game
    #
    # ==========================================================================

    def _schedule_search_fallback(
        self,
        team1: str,
        team2: str,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> Optional[DetectionResult]:
        """
        Tier 4 fallback: Search team schedules for opponent by NAME.

        Handles cases where:
        - Tier 4a: Both teams matched but to WRONG teams, no game between them
        - Tier 4b/4c: Only one team in cache (e.g., NAIA vs NCAA game)

        Args:
            team1: First team name (raw from stream)
            team2: Second team name (raw from stream)
            game_date: Optional target date from stream
            game_time: Optional target time from stream

        Returns:
            DetectionResult if found, None otherwise
        """
        from database import get_connection
        from epg.league_config import get_league_config, parse_api_path

        if not self.espn:
            return None

        # Build target datetime for matching
        target_dt = None
        if game_date and game_time:
            target_dt = datetime.combine(
                game_date.date(),
                game_time.time(),
                tzinfo=game_time.tzinfo or ZoneInfo('UTC')
            )
        elif game_time:
            # Time only - use today
            today = datetime.now(ZoneInfo('UTC')).date()
            target_dt = datetime.combine(
                today,
                game_time.time(),
                tzinfo=game_time.tzinfo or ZoneInfo('UTC')
            )

        conn = get_connection()
        cursor = conn.cursor()

        # Find all (team_id, league_code, team_name) entries for each raw team
        team1_entries = []
        team2_entries = []

        for team_name, entries_list in [(team1, team1_entries), (team2, team2_entries)]:
            variants = get_abbreviation_variants(team_name)
            for variant in variants:
                # Check US sports cache - search team_name, team_abbrev, and team_short_name
                cursor.execute("""
                    SELECT espn_team_id, league_code, team_name
                    FROM team_league_cache
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_abbrev) LIKE ?
                       OR LOWER(team_short_name) LIKE ?
                """, (f'%{variant}%', f'%{variant}%', f'%{variant}%'))
                for row in cursor.fetchall():
                    entry = (row[0], row[1], row[2], 'us_sports')
                    if entry not in entries_list:
                        entries_list.append(entry)

                # Check soccer cache
                cursor.execute("""
                    SELECT espn_team_id, league_slug, team_name
                    FROM soccer_team_leagues
                    WHERE LOWER(team_name) LIKE ?
                """, (f'%{variant}%',))
                for row in cursor.fetchall():
                    entry = (row[0], row[1], row[2], 'soccer')
                    if entry not in entries_list:
                        entries_list.append(entry)

        conn.close()

        # Determine tier based on what we found
        if team1_entries and team2_entries:
            # TIER 4a: Both teams found in caches - search BOTH teams' schedules
            # for the RAW opponent name (handles wrong cache matches)
            logger.debug(
                f"Tier 4a: both teams in cache ({len(team1_entries)} + {len(team2_entries)} entries), "
                f"searching schedules for raw opponent names"
            )
            return self._search_both_teams_schedules(
                team1, team1_entries,
                team2, team2_entries,
                target_dt
            )
        elif team1_entries and not team2_entries:
            # TIER 4b/4c: Only team1 in cache
            known_team_entries = team1_entries
            unknown_team = team2
            logger.debug(f"Tier 4b/c: found {len(team1_entries)} entries for '{team1}', searching for '{team2}'")
        elif team2_entries and not team1_entries:
            # TIER 4b/4c: Only team2 in cache
            known_team_entries = team2_entries
            unknown_team = team1
            logger.debug(f"Tier 4b/c: found {len(team2_entries)} entries for '{team2}', searching for '{team1}'")
        else:
            # Neither team found
            logger.debug(f"Tier 4: neither team found in caches")
            return None

        # Continue with single-team search (existing Tier 4b/4c logic)
        return self._search_single_team_schedule(
            known_team_entries, unknown_team, target_dt
        )

    def _search_both_teams_schedules(
        self,
        team1_raw: str,
        team1_entries: List,
        team2_raw: str,
        team2_entries: List,
        target_dt: datetime = None
    ) -> Optional[DetectionResult]:
        """
        Tier 4a: Search BOTH teams' schedules for the raw opponent name.

        When both teams are in cache but no game exists between the matched teams,
        the cache match may be wrong. Search each team's schedule looking for
        an event that contains the RAW opponent string.

        Example: "IU East vs Eastern Kentucky"
        - "IU East" wrongly matched to "IU Indianapolis"
        - "Eastern Kentucky" correctly matched
        - No game between IU Indianapolis and Eastern Kentucky
        - But Eastern Kentucky's schedule contains "Indiana University East IU EAST..."
        - We find it by searching for "iu east" in event names

        Args:
            team1_raw: Raw team1 string from stream
            team1_entries: List of (team_id, league_code, team_name, cache_type)
            team2_raw: Raw team2 string from stream
            team2_entries: List of (team_id, league_code, team_name, cache_type)
            target_dt: Optional target datetime for time matching

        Returns:
            DetectionResult if found, None otherwise
        """
        from epg.league_config import get_league_config, parse_api_path
        from database import get_connection

        now = datetime.now(ZoneInfo('UTC'))
        cutoff_future = now + timedelta(days=self.lookahead_days)

        # Extract primary words for matching (skip common words)
        common_words = {'college', 'university', 'state', 'city', 'the', 'of', 'at'}

        def get_primary_word(name: str) -> str:
            words = [w for w in name.lower().split() if w not in common_words]
            return words[0] if words else name.lower().split()[0] if name else ''

        team1_lower = team1_raw.lower().strip()
        team2_lower = team2_raw.lower().strip()
        team1_primary = get_primary_word(team1_raw)
        team2_primary = get_primary_word(team2_raw)

        candidates = []  # List of (event_id, event_dt, league_code, sport, event_name)

        # Search team1's schedules for team2_raw
        for team_id, league_code, team_name, cache_type in team1_entries:
            self._search_schedule_for_opponent(
                team_id, league_code, team_name,
                team2_lower, team2_primary,
                now, cutoff_future, target_dt, candidates
            )

        # Search team2's schedules for team1_raw
        for team_id, league_code, team_name, cache_type in team2_entries:
            self._search_schedule_for_opponent(
                team_id, league_code, team_name,
                team1_lower, team1_primary,
                now, cutoff_future, target_dt, candidates
            )

        if not candidates:
            logger.debug("Tier 4a: no matches found in any schedule")
            return None

        # If we have exact time match with target, prefer it
        if target_dt:
            target_utc = target_dt.astimezone(ZoneInfo('UTC')) if target_dt.tzinfo else target_dt.replace(tzinfo=ZoneInfo('UTC'))
            for event_id, event_dt, league_code, sport, event_name in candidates:
                time_diff = abs((event_dt - target_utc).total_seconds() / 60)
                if time_diff <= TIME_TOLERANCE_MINUTES:
                    logger.info(f"Tier 4a: exact time match '{event_name}' in {league_code}")
                    return DetectionResult(
                        detected=True,
                        league=league_code,
                        sport=sport,
                        tier=4,
                        tier_detail='4a',
                        method=f"Tier 4a: {event_name} (both teams, raw name match, exact time)",
                        event_id=event_id,
                        event_date=event_dt
                    )

        # Return closest to target or now
        if target_dt:
            target_utc = target_dt.astimezone(ZoneInfo('UTC')) if target_dt.tzinfo else target_dt.replace(tzinfo=ZoneInfo('UTC'))
            candidates.sort(key=lambda c: abs((c[1] - target_utc).total_seconds()))
        else:
            candidates.sort(key=lambda c: abs((c[1] - now).total_seconds()))

        best = candidates[0]
        event_id, event_dt, league_code, sport, event_name = best

        logger.info(f"Tier 4a: best match '{event_name}' in {league_code}")
        return DetectionResult(
            detected=True,
            league=league_code,
            sport=sport,
            tier=4,
            tier_detail='4a',
            method=f"Tier 4a: {event_name} (both teams, raw name match, closest game)",
            event_id=event_id,
            event_date=event_dt
        )

    def _search_schedule_for_opponent(
        self,
        team_id: str,
        league_code: str,
        team_name: str,
        opponent_lower: str,
        opponent_primary: str,
        now: datetime,
        cutoff_future: datetime,
        target_dt: datetime,
        candidates: List
    ):
        """
        Helper: Search a single team's schedule for events matching opponent name.

        OPTIMIZATION: Checks cached scoreboard first before making schedule API call.
        Scoreboard is already cached from earlier tiers, so this is essentially free.

        Appends matching events to candidates list.
        """
        from epg.league_config import get_league_config, parse_api_path
        from database import get_connection

        try:
            config = get_league_config(league_code, get_connection)
            if config:
                sport, api_league = parse_api_path(config['api_path'])
                if not sport:
                    return
            else:
                # Fallback for soccer leagues not in league_config but in soccer cache
                # (e.g., eng.fa, esp.copa_del_rey, etc.)
                conn_check = get_connection()
                cursor_check = conn_check.cursor()
                cursor_check.execute(
                    "SELECT 1 FROM soccer_leagues_cache WHERE league_slug = ?",
                    (league_code,)
                )
                is_soccer = cursor_check.fetchone() is not None
                conn_check.close()

                if is_soccer:
                    sport = 'soccer'
                    api_league = league_code
                    logger.debug(f"Tier 4: using soccer fallback for league {league_code}")
                else:
                    return

            # SCOREBOARD FIRST: Check cached scoreboard before hitting schedule API
            # The scoreboard is already cached from Tier 3 checks, so this is free
            scoreboard_events = self._search_scoreboard_for_team_and_opponent(
                sport, api_league, str(team_id), team_name,
                opponent_lower, opponent_primary, now, cutoff_future
            )
            if scoreboard_events:
                for event_id, event_dt, event_name in scoreboard_events:
                    logger.debug(
                        f"Tier 4 found via SCOREBOARD: '{event_name}' for {team_name} "
                        f"(matched '{opponent_lower}' or '{opponent_primary}')"
                    )
                    candidates.append((event_id, event_dt, league_code, sport, event_name))
                return  # Found on scoreboard, no need for schedule API call

            # SCHEDULE FALLBACK: Only call API if not found on scoreboard
            schedule = self.espn.get_team_schedule(sport, api_league, str(team_id))
            if not schedule or 'events' not in schedule:
                return

            for event in schedule.get('events', []):
                event_name = event.get('name', '')
                event_name_lower = event_name.lower()

                # Check if opponent name appears in event name
                if opponent_lower not in event_name_lower and opponent_primary not in event_name_lower:
                    continue

                event_date_str = event.get('date', '')
                if not event_date_str:
                    continue

                try:
                    event_dt = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                except Exception:
                    continue

                # Check if within window
                if event_dt < now - timedelta(days=1) or event_dt > cutoff_future:
                    continue

                event_id = event.get('id')
                logger.debug(
                    f"Tier 4 found: '{event_name}' in {team_name}'s {league_code} schedule "
                    f"(matched '{opponent_lower}' or '{opponent_primary}')"
                )
                candidates.append((event_id, event_dt, league_code, sport, event_name))

        except Exception as e:
            logger.debug(f"Tier 4 error searching {team_name}'s {league_code} schedule: {e}")

    def _search_scoreboard_for_team_and_opponent(
        self,
        sport: str,
        api_league: str,
        team_id: str,
        team_name: str,
        opponent_lower: str,
        opponent_primary: str,
        now: datetime,
        cutoff_future: datetime
    ) -> List[Tuple[str, datetime, str]]:
        """
        Search cached scoreboard for games involving a known team where opponent matches.

        This is an optimization for Tier 4 - instead of fetching a team's schedule,
        we first check if the game is on an already-cached scoreboard.

        Args:
            sport: Sport (e.g., 'basketball')
            api_league: League for API (e.g., 'mens-college-basketball')
            team_id: ESPN team ID of the known team
            team_name: Display name of known team (for logging)
            opponent_lower: Lowercase opponent name to match
            opponent_primary: Primary word of opponent name
            now: Current datetime
            cutoff_future: Future cutoff datetime

        Returns:
            List of (event_id, event_datetime, event_name) tuples
        """
        results = []

        # Check scoreboard for multiple days (same logic as other scoreboard checks)
        # Early exit once we find a match - most games are on today's scoreboard
        for day_offset in range(-1, min(self.lookahead_days, 7)):
            check_date = now + timedelta(days=day_offset)
            date_str = check_date.strftime('%Y%m%d')

            # Use cached scoreboard fetch (from ESPNClient class-level cache)
            scoreboard = self.espn.get_scoreboard(sport, api_league, date_str)
            if not scoreboard or 'events' not in scoreboard:
                continue

            for event in scoreboard.get('events', []):
                competitions = event.get('competitions', [])
                if not competitions:
                    continue

                competitors = competitions[0].get('competitors', [])
                if len(competitors) != 2:
                    continue

                # Check if our known team is in this game
                team_ids_in_game = [str(c.get('team', {}).get('id', '')) for c in competitors]
                if team_id not in team_ids_in_game:
                    continue

                # Known team is in this game - check if opponent name matches
                event_name = event.get('name', '')
                event_name_lower = event_name.lower()

                if opponent_lower not in event_name_lower and opponent_primary not in event_name_lower:
                    continue

                # Found a match!
                event_date_str = event.get('date', '')
                if not event_date_str:
                    continue

                try:
                    event_dt = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                except Exception:
                    continue

                # Check if within window
                if event_dt < now - timedelta(days=1) or event_dt > cutoff_future:
                    continue

                event_id = event.get('id')
                results.append((event_id, event_dt, event_name))
                # Don't early exit - collect all matches so caller can pick best time match
                # There might be multiple games (e.g., doubleheader, or same-name teams)

        return results

    def _search_single_team_schedule(
        self,
        known_team_entries: List,
        unknown_team: str,
        target_dt: datetime = None
    ) -> Optional[DetectionResult]:
        """
        Tier 4b/4c: Search known team's schedule for unknown opponent by name.

        Args:
            known_team_entries: List of (team_id, league_code, team_name, cache_type)
            unknown_team: Raw name of team NOT in cache
            target_dt: Optional target datetime for matching

        Returns:
            DetectionResult if found, None otherwise
        """
        from epg.league_config import get_league_config, parse_api_path
        from database import get_connection

        now = datetime.now(ZoneInfo('UTC'))
        cutoff_future = now + timedelta(days=self.lookahead_days)

        # Normalize unknown team name for matching
        unknown_lower = unknown_team.lower()
        common_words = {'college', 'university', 'state', 'city'}
        unknown_words = [w for w in unknown_lower.split() if w not in common_words]
        unknown_primary = unknown_words[0] if unknown_words else unknown_lower.split()[0]

        candidates = []

        for team_id, league_code, team_name, cache_type in known_team_entries:
            self._search_schedule_for_opponent(
                team_id, league_code, team_name,
                unknown_lower, unknown_primary,
                now, cutoff_future, target_dt, candidates
            )

        if not candidates:
            return None

        # If we have exact time match, return immediately
        if target_dt:
            target_utc = target_dt.astimezone(ZoneInfo('UTC')) if target_dt.tzinfo else target_dt.replace(tzinfo=ZoneInfo('UTC'))
            for event_id, event_dt, league_code, sport, event_name in candidates:
                time_diff = abs((event_dt - target_utc).total_seconds() / 60)
                if time_diff <= TIME_TOLERANCE_MINUTES:
                    logger.info(f"Tier 4b: exact time match in {league_code}")
                    return DetectionResult(
                        detected=True,
                        league=league_code,
                        sport=sport,
                        tier=4,
                        tier_detail='4b',
                        method=f"Tier 4b: {event_name} (single team, opponent by name, exact time)",
                        event_id=event_id,
                        event_date=event_dt
                    )

        # Return closest to target or now
        if target_dt:
            target_utc = target_dt.astimezone(ZoneInfo('UTC')) if target_dt.tzinfo else target_dt.replace(tzinfo=ZoneInfo('UTC'))
            candidates.sort(key=lambda c: abs((c[1] - target_utc).total_seconds()))
        else:
            candidates.sort(key=lambda c: abs((c[1] - now).total_seconds()))

        best = candidates[0]
        event_id, event_dt, league_code, sport, event_name = best

        tier_detail = '4b' if target_dt else '4c'
        logger.info(f"Tier {tier_detail}: best match is {event_name} in {league_code}")
        return DetectionResult(
            detected=True,
            league=league_code,
            sport=sport,
            tier=4,
            tier_detail=tier_detail,
            method=f"Tier {tier_detail}: {event_name} (single team, opponent by name, closest game)",
            event_id=event_id,
            event_date=event_dt
        )

    # Legacy alias for backward compatibility
    def _single_team_schedule_fallback(
        self,
        team1: str,
        team2: str,
        game_date: datetime = None,
        game_time: datetime = None
    ) -> Optional[DetectionResult]:
        """Legacy alias - calls _schedule_search_fallback."""
        return self._schedule_search_fallback(team1, team2, game_date, game_time)

    # ==========================================================================
    # SCHEDULE SEARCH HELPERS
    # ==========================================================================

    def _search_soccer_scoreboard(
        self,
        sport: str,
        api_league: str,
        team1_id: str,
        team2_id: str,
        lookahead_days: int
    ) -> List[Dict]:
        """
        Search soccer scoreboard for games between two teams.

        Soccer schedule API only returns past results, so we use scoreboard
        for present/future fixtures. This mirrors EventMatcher._search_scoreboard.

        Args:
            sport: Sport type (should be 'soccer')
            api_league: League slug (e.g., 'ned.1', 'eng.1')
            team1_id: ESPN team ID for first team
            team2_id: ESPN team ID for second team
            lookahead_days: Number of days to search ahead

        Returns:
            List of events that involve both teams
        """
        now_utc = datetime.now(ZoneInfo('UTC'))
        candidate_events = []

        for day_offset in range(lookahead_days):
            check_date = now_utc + timedelta(days=day_offset)
            date_str = check_date.strftime('%Y%m%d')

            try:
                scoreboard_data = self.espn.get_scoreboard(sport, api_league, date_str)
                if not scoreboard_data or 'events' not in scoreboard_data:
                    continue

                for sb_event in scoreboard_data.get('events', []):
                    competitions = sb_event.get('competitions', [])
                    if not competitions:
                        continue

                    competitors = competitions[0].get('competitors', [])
                    team_ids_in_event = {str(c.get('team', {}).get('id', '')) for c in competitors}

                    if str(team1_id) in team_ids_in_event and str(team2_id) in team_ids_in_event:
                        candidate_events.append(sb_event)
                        logger.debug(f"Soccer scoreboard match: {sb_event.get('name')} on {sb_event.get('date')}")

            except Exception as e:
                logger.debug(f"Error fetching scoreboard for {api_league} on {date_str}: {e}")
                continue

        return candidate_events

    def _search_schedules(
        self,
        team1_name: str,
        team2_name: str,
        candidates: List[str],
        target_datetime: datetime = None,
        tolerance_minutes: int = None
    ) -> List[ScheduleMatch]:
        """
        Search schedules across multiple leagues for a team matchup.

        Args:
            team1_name: Team name (will be resolved to ID per-league)
            team2_name: Team name (will be resolved to ID per-league)
            candidates: List of league codes to search
            target_datetime: Specific datetime to match (None = search all)
            tolerance_minutes: Time tolerance for matching (None = any time)

        Returns:
            List of ScheduleMatch objects
        """
        from epg.league_config import get_league_config, parse_api_path
        from epg.team_league_cache import TeamLeagueCache

        matches = []
        now = datetime.now(ZoneInfo('UTC'))
        cutoff_future = now + timedelta(days=self.lookahead_days)
        cutoff_past = now - timedelta(days=1)  # Include games from yesterday

        for league in candidates:
            try:
                from database import get_connection
                config = get_league_config(league, get_connection)
                if config:
                    sport, api_league = parse_api_path(config['api_path'])
                    if not sport:
                        continue
                else:
                    # Fallback for soccer leagues not in league_config but in soccer cache
                    # ESPN soccer API path is simply "soccer/{league_slug}"
                    from database import get_connection
                    conn_check = get_connection()
                    cursor_check = conn_check.cursor()
                    cursor_check.execute(
                        "SELECT 1 FROM soccer_leagues_cache WHERE league_slug = ?",
                        (league,)
                    )
                    is_soccer = cursor_check.fetchone() is not None
                    conn_check.close()

                    if is_soccer:
                        sport = 'soccer'
                        api_league = league
                        logger.debug(f"Using soccer fallback for league {league}")
                    else:
                        continue

                # Resolve team IDs for THIS specific league
                # Critical: same team name can have different IDs in different leagues
                # e.g., Iowa State Cyclones = ID 66 in volleyball, ID 20535 in women's soccer
                if sport == 'soccer':
                    # Use soccer cache for soccer leagues
                    soccer_ids = self.get_soccer_team_ids_for_league(team1_name, team2_name, league)
                    if soccer_ids:
                        team1_id = soccer_ids['team1_id']
                        team2_id = soccer_ids['team2_id']
                    else:
                        logger.debug(f"Could not resolve teams '{team1_name}' vs '{team2_name}' in soccer league {league}")
                        continue
                else:
                    # Use non-soccer cache for other sports
                    team1_id = TeamLeagueCache.get_team_id_for_league(team1_name, league)
                    team2_id = TeamLeagueCache.get_team_id_for_league(team2_name, league)

                    if not team1_id:
                        logger.debug(f"Could not resolve team1 '{team1_name}' in {league}")
                        continue
                    if not team2_id:
                        logger.debug(f"Could not resolve team2 '{team2_name}' in {league}")
                        continue

                # Collect events from both schedule AND scoreboard
                # Schedule API has future games, but some events (NCAA tournaments)
                # only appear on scoreboard. Fetch scoreboard for multiple days.
                all_events = []
                existing_ids = set()

                # 1. Check scoreboard for lookahead window (NCAA tournaments may only be here)
                # Start from -1 (yesterday) to handle timezone edge cases
                for day_offset in range(-1, min(self.lookahead_days, 7)):
                    check_date = now + timedelta(days=day_offset)
                    date_str = check_date.strftime('%Y%m%d')
                    scoreboard = self.espn.get_scoreboard(sport, api_league, date_str)
                    if scoreboard and 'events' in scoreboard:
                        for event in scoreboard.get('events', []):
                            event_id = event.get('id')
                            if event_id and event_id not in existing_ids:
                                all_events.append(event)
                                existing_ids.add(event_id)

                # 2. Also check team schedule (may have additional future games)
                schedule = self.espn.get_team_schedule(sport, api_league, team1_id)
                if schedule and 'events' in schedule:
                    for event in schedule.get('events', []):
                        event_id = event.get('id')
                        if event_id and event_id not in existing_ids:
                            all_events.append(event)
                            existing_ids.add(event_id)

                if not all_events:
                    continue

                for event in all_events:
                    try:
                        event_date_str = event.get('date', '')
                        if not event_date_str:
                            continue

                        event_date = datetime.fromisoformat(
                            event_date_str.replace('Z', '+00:00')
                        )

                        # Skip events outside window
                        if event_date < cutoff_past or event_date > cutoff_future:
                            continue

                        # Check if team2 is in this game
                        competitions = event.get('competitions', [])
                        if not competitions:
                            continue

                        competitors = competitions[0].get('competitors', [])
                        team_ids = [
                            str(c.get('team', {}).get('id', c.get('id')))
                            for c in competitors
                        ]

                        if str(team2_id) not in team_ids:
                            continue

                        # Calculate time difference
                        if target_datetime:
                            time_diff = abs((event_date - target_datetime).total_seconds() / 60)

                            # Apply tolerance filter
                            if tolerance_minutes and time_diff > tolerance_minutes:
                                continue
                        else:
                            time_diff = abs((event_date - now).total_seconds() / 60)

                        # Extract home/away
                        home_id = away_id = None
                        for c in competitors:
                            tid = str(c.get('team', {}).get('id', c.get('id')))
                            if c.get('homeAway') == 'home':
                                home_id = tid
                            else:
                                away_id = tid

                        matches.append(ScheduleMatch(
                            league=league,
                            event_id=event.get('id'),
                            event_date=event_date,
                            home_team_id=home_id,
                            away_team_id=away_id,
                            time_diff_minutes=time_diff
                        ))

                    except Exception as e:
                        logger.debug(f"Error parsing event in {league}: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Error searching {league} schedule: {e}")
                continue

        return matches


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_league_detector(
    enabled_leagues: List[str] = None,
    lookahead_days: int = 7
) -> LeagueDetector:
    """
    Create a LeagueDetector with default configuration.

    Args:
        enabled_leagues: List of league codes to consider
        lookahead_days: How many days ahead to search

    Returns:
        Configured LeagueDetector instance
    """
    from api.espn_client import ESPNClient

    espn = ESPNClient()
    return LeagueDetector(
        espn_client=espn,
        enabled_leagues=enabled_leagues,
        lookahead_days=lookahead_days
    )
