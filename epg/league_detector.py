"""
League Detector for Multi-Sport Event Groups

Detects the appropriate league for a stream when the group spans multiple sports/leagues.
Uses a tiered detection system with fallback strategies.

Detection Tiers:
    Tier 1: League indicator + Teams → Direct match (e.g., "NHL: Predators vs Panthers")
    Tier 2: Sport indicator + Teams → Match within sport's leagues
    Tier 3a: Teams + Date + Time → Exact schedule match across candidate leagues
    Tier 3b: Teams + Time only → Infer today's date, exact schedule match
    Tier 3c: Teams only → Closest game to now across candidate leagues

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

logger = get_logger(__name__)


def strip_team_numbers(name: str) -> str:
    """
    Strip standalone numbers from team name for fuzzy matching.
    Handles cases like "SV 07 Elversberg" -> "SV Elversberg"
    """
    stripped = re.sub(r'\b\d+\b', '', name)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return stripped


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
    r'\bEPL\b': 'epl',
    r'\bPremier League\b': 'epl',
    r'\bEnglish Premier League\b': 'epl',
    r'\bLa Liga\b': 'laliga',
    r'\bLaLiga\b': 'laliga',
    r'\bBundesliga\b': 'bundesliga',
    r'\bSerie A\b': 'seriea',
    r'\bLigue 1\b': 'ligue1',
    r'\bMLS\b': 'mls',
    r'\bMajor League Soccer\b': 'mls',
    r'\bNWSL\b': 'nwsl',
    r'\bEFL\b': 'efl',
    r'\bEFL Championship\b': 'efl',
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
        result = self._detect_tier1(stream_name, team1, team2)
        if result.detected:
            return result

        # Tier 2: Check for sport indicator
        result = self._detect_tier2(stream_name, team1, team2)
        if result.detected:
            return result

        # Tier 3: Team-based lookup with schedule disambiguation
        if team1 and team2:
            result = self._detect_tier3(
                team1, team2,
                team1_id, team2_id,
                game_date, game_time
            )
            if result.detected:
                return result

        # No detection possible
        return DetectionResult(
            detected=False,
            method="No league detected - no indicators or team matches"
        )

    def find_candidate_leagues(self, team1: str, team2: str, include_soccer: bool = True) -> List[str]:
        """
        Find all enabled leagues where both teams might exist.

        Uses TeamLeagueCache for non-soccer teams and SoccerMultiLeague for soccer.

        Args:
            team1: First team name
            team2: Second team name
            include_soccer: Whether to also check soccer leagues (default True)

        Returns:
            List of league codes where both teams exist
        """
        from epg.team_league_cache import TeamLeagueCache

        # Get non-soccer leagues for these teams
        candidates = TeamLeagueCache.find_candidate_leagues(
            team1, team2,
            enabled_leagues=self.enabled_leagues
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
        Find all enabled leagues where both team IDs exist.

        Args:
            team1_id: ESPN team ID for first team
            team2_id: ESPN team ID for second team

        Returns:
            List of league codes where both teams exist
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

            # Intersection filtered by enabled leagues
            candidates = leagues1 & leagues2
            if self.enabled_leagues:
                candidates = candidates & set(self.enabled_leagues)

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
            team1_lower = team1.lower().strip()
            team2_lower = team2.lower().strip()

            def find_leagues_for_team(team_name: str) -> set:
                """
                Find leagues for a team with tiered fallback search.

                Tiers:
                1. Direct match (exact or substring)
                2. Accent-normalized match (Atletico -> Atlético)
                3. Number-stripped match (SV Elversberg -> SV 07 Elversberg)
                4. Article-stripped match (Atlético de Madrid -> Atlético Madrid)
                """
                # Tier 1: Direct match
                cursor.execute("""
                    SELECT DISTINCT league_slug FROM soccer_team_leagues
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_name) LIKE ?
                       OR INSTR(?, LOWER(team_name)) > 0
                """, (f"%{team_name}%", f"{team_name}%", team_name))
                leagues = {row[0] for row in cursor.fetchall()}

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
                    if search_normalized in db_normalized or db_normalized in search_normalized:
                        leagues.add(row[0])
                if leagues:
                    logger.debug(f"Found leagues via accent-stripped search: '{team_name}' -> '{search_normalized}'")
                    return leagues

                # Tier 3: Strip numbers from both search term and DB values
                # Handles "SV Elversberg" matching "SV 07 Elversberg"
                stripped = normalize_team_name(team_name, strip_articles=False)

                if stripped and stripped != team_name:
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

                # Tier 5: Word-overlap matching for city name variants
                # Handles German/English city differences:
                #   - München vs Munich (Bayern Munich in DB, FC Bayern München in stream)
                #   - Köln vs Cologne
                #   - Napoli vs Naples
                search_words = set(search_normalized.split())
                # Filter out common prefixes AND generic words that cause false positives
                non_significant_words = {
                    # Soccer club prefixes
                    'fc', 'sc', 'sv', 'vfb', 'vfl', 'tsv', 'fsv', 'bsc', '1.', 'ac', 'as', 'ss', 'us',
                    'cd', 'cf', 'rc', 'rcd', 'ud', 'sd', 'real', 'sporting', 'athletic', 'atletico',
                    # Generic institutional words (cause false positives)
                    'college', 'university', 'state', 'city', 'united', 'town', 'county',
                    # Common suffixes
                    'afc', 'utd',
                }
                search_words_significant = search_words - non_significant_words

                if search_words_significant:
                    cursor.execute("""
                        SELECT league_slug, team_name FROM soccer_team_leagues
                    """)
                    for row in cursor.fetchall():
                        db_normalized = strip_accents(row[1].lower())
                        db_words = set(db_normalized.split())
                        db_words_significant = db_words - non_significant_words

                        # Match if any significant word overlaps
                        # This catches "Bayern" in both "FC Bayern München" and "Bayern Munich"
                        overlap = search_words_significant & db_words_significant
                        if overlap:
                            leagues.add(row[0])

                    if leagues:
                        logger.debug(f"Found leagues via word-overlap search: '{team_name}' -> significant words: {search_words_significant}")
                        return leagues

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
                1. Direct match (exact or substring)
                2. Accent-normalized match (Atletico -> Atlético)
                3. Number-stripped match (SV Elversberg -> SV 07 Elversberg)
                4. Article-stripped match (Atlético de Madrid -> Atlético Madrid)
                """
                team_lower = team_name.lower().strip()
                team_accent_stripped = strip_accents(team_lower)
                team_stripped = normalize_team_name(team_lower, strip_articles=False)
                team_normalized = normalize_team_name(team_lower, strip_articles=True)

                # Tier 1: Direct match (bidirectional substring)
                cursor.execute("""
                    SELECT espn_team_id, team_name FROM soccer_team_leagues
                    WHERE league_slug = ? AND (
                        LOWER(team_name) LIKE ?
                        OR LOWER(team_name) LIKE ?
                        OR INSTR(?, LOWER(team_name)) > 0
                    )
                    ORDER BY LENGTH(team_name) ASC
                    LIMIT 1
                """, (league, f"%{team_lower}%", f"{team_lower}%", team_lower))
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
                if team_stripped != team_lower:
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
                if team_normalized != team_stripped:
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

                # Tier 5: Word-overlap matching for city name variants
                # Handles German/English city differences:
                #   - München vs Munich (Bayern Munich in DB, FC Bayern München in stream)
                #   - Köln vs Cologne
                # Match if significant words overlap (ignoring common prefixes like FC, VfB)
                search_words = set(team_accent_stripped.split())
                # Filter out common prefixes AND generic words that cause false positives
                non_significant_words = {
                    # Soccer club prefixes
                    'fc', 'sc', 'sv', 'vfb', 'vfl', 'tsv', 'fsv', 'bsc', '1.', 'ac', 'as', 'ss', 'us',
                    'cd', 'cf', 'rc', 'rcd', 'ud', 'sd', 'real', 'sporting', 'athletic', 'atletico',
                    # Generic institutional words (cause false positives)
                    'college', 'university', 'state', 'city', 'united', 'town', 'county',
                    # Common suffixes
                    'afc', 'utd',
                }
                search_words_significant = search_words - non_significant_words

                if search_words_significant:
                    cursor.execute("""
                        SELECT espn_team_id, team_name FROM soccer_team_leagues
                        WHERE league_slug = ?
                    """, (league,))
                    for row in cursor.fetchall():
                        db_normalized = strip_accents(row[1].lower())
                        db_words = set(db_normalized.split())
                        db_words_significant = db_words - non_significant_words

                        # Match if any significant word overlaps
                        overlap = search_words_significant & db_words_significant
                        if overlap:
                            logger.debug(f"Team '{team_name}' matched via word-overlap: {row[1]} (shared: {overlap})")
                            return row

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
        team2: str
    ) -> Dict[str, Any]:
        """
        Diagnose why teams couldn't be matched to a common league.

        Provides detailed info about which teams were found, in which leagues,
        and why no common league exists.

        Args:
            team1: First team name from stream
            team2: Second team name from stream

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
        from utils.filter_reasons import FilterReason

        if not team1 or not team2:
            return {
                'team1_found': False,
                'team2_found': False,
                'team1_leagues': [],
                'team2_leagues': [],
                'common_leagues': [],
                'reason': FilterReason.TEAMS_NOT_PARSED,
                'detail': 'Team names not provided'
            }

        conn = get_connection()
        try:
            cursor = conn.cursor()
            team1_lower = team1.lower().strip()
            team2_lower = team2.lower().strip()

            # Find leagues for team1 (check both soccer_team_leagues and team_league_cache)
            cursor.execute("""
                SELECT DISTINCT league_slug, team_name FROM soccer_team_leagues
                WHERE LOWER(team_name) LIKE ?
                   OR LOWER(team_name) LIKE ?
                   OR INSTR(?, LOWER(team_name)) > 0
                UNION
                SELECT DISTINCT league_code, team_name FROM team_league_cache
                WHERE LOWER(team_name) LIKE ?
                   OR LOWER(team_name) LIKE ?
                   OR LOWER(team_short_name) LIKE ?
                   OR INSTR(?, LOWER(team_name)) > 0
                   OR INSTR(?, LOWER(team_short_name)) > 0
            """, (f"%{team1_lower}%", f"{team1_lower}%", team1_lower,
                  f"%{team1_lower}%", f"{team1_lower}%", f"%{team1_lower}%", team1_lower, team1_lower))
            team1_results = cursor.fetchall()
            team1_leagues = list(set(r[0] for r in team1_results))
            team1_found = len(team1_leagues) > 0

            # Find leagues for team2 (check both soccer_team_leagues and team_league_cache)
            cursor.execute("""
                SELECT DISTINCT league_slug, team_name FROM soccer_team_leagues
                WHERE LOWER(team_name) LIKE ?
                   OR LOWER(team_name) LIKE ?
                   OR INSTR(?, LOWER(team_name)) > 0
                UNION
                SELECT DISTINCT league_code, team_name FROM team_league_cache
                WHERE LOWER(team_name) LIKE ?
                   OR LOWER(team_name) LIKE ?
                   OR LOWER(team_short_name) LIKE ?
                   OR INSTR(?, LOWER(team_name)) > 0
                   OR INSTR(?, LOWER(team_short_name)) > 0
            """, (f"%{team2_lower}%", f"{team2_lower}%", team2_lower,
                  f"%{team2_lower}%", f"{team2_lower}%", f"%{team2_lower}%", team2_lower, team2_lower))
            team2_results = cursor.fetchall()
            team2_leagues = list(set(r[0] for r in team2_results))
            team2_found = len(team2_leagues) > 0

            # Find common leagues
            common_leagues = list(set(team1_leagues) & set(team2_leagues))

            # Determine reason and detail
            if not team1_found and not team2_found:
                reason = FilterReason.TEAMS_NOT_IN_ESPN
                detail = f"Neither '{team1}' nor '{team2}' found in ESPN database"
            elif not team1_found:
                reason = FilterReason.TEAMS_NOT_IN_ESPN
                detail = f"'{team1}' not found in ESPN database"
            elif not team2_found:
                reason = FilterReason.TEAMS_NOT_IN_ESPN
                detail = f"'{team2}' not found in ESPN database"
            elif not common_leagues:
                reason = FilterReason.NO_COMMON_LEAGUE
                detail = (f"'{team1}' in [{', '.join(team1_leagues[:3])}{'...' if len(team1_leagues) > 3 else ''}], "
                         f"'{team2}' in [{', '.join(team2_leagues[:3])}{'...' if len(team2_leagues) > 3 else ''}] - no common league")
            else:
                # Shouldn't happen if this is called for failure diagnosis
                reason = None
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
        team2: str
    ) -> DetectionResult:
        """
        Tier 1: Detect league from explicit league indicator in stream name.

        Example: "NHL: Predators vs Panthers" → NHL
        """
        detected_league = None

        for pattern, league in self._league_patterns:
            if pattern.search(stream_name):
                # Check if this league is enabled
                if league in self.enabled_leagues:
                    detected_league = league
                    break

        if not detected_league:
            return DetectionResult(detected=False)

        # Validate teams exist in this league
        if team1 and team2:
            candidates = self.find_candidate_leagues(team1, team2)
            if detected_league not in candidates:
                logger.debug(
                    f"Tier 1: League indicator {detected_league} found but teams "
                    f"'{team1}' vs '{team2}' not found in that league"
                )
                return DetectionResult(
                    detected=False,
                    method=f"League indicator {detected_league} found but teams not in league",
                    candidates_checked=[detected_league]
                )

        sport = get_sport_for_league(detected_league)

        return DetectionResult(
            detected=True,
            league=detected_league,
            sport=sport,
            tier=1,
            tier_detail='1',
            method=f"League indicator '{detected_league.upper()}' found in stream name",
            candidates_checked=[detected_league]
        )

    # ==========================================================================
    # TIER 2: Sport Indicator Detection
    # ==========================================================================

    def _detect_tier2(
        self,
        stream_name: str,
        team1: str,
        team2: str
    ) -> DetectionResult:
        """
        Tier 2: Detect league from sport indicator + team lookup.

        Example: "Hockey: Predators vs Panthers" + teams in NHL → NHL
        """
        detected_sport = None
        sport_leagues = []

        for pattern, leagues in self._sport_patterns:
            if pattern.search(stream_name):
                detected_sport = pattern.pattern.strip(r'\b')
                sport_leagues = [l for l in leagues if l in self.enabled_leagues]
                break

        if not sport_leagues:
            return DetectionResult(detected=False)

        # If we have teams, find which sport league(s) they're in
        if team1 and team2:
            candidates = self.find_candidate_leagues(team1, team2)
            matching_leagues = [l for l in candidates if l in sport_leagues]

            if len(matching_leagues) == 1:
                league = matching_leagues[0]
                return DetectionResult(
                    detected=True,
                    league=league,
                    sport=get_sport_for_league(league),
                    tier=2,
                    tier_detail='2',
                    method=f"Sport indicator '{detected_sport}' + teams in {league.upper()}",
                    candidates_checked=sport_leagues
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
            return DetectionResult(
                detected=False,
                method=f"No leagues found containing both '{team1}' and '{team2}'"
            )

        if len(candidates) == 1:
            # Unambiguous - only one league has both teams
            league = candidates[0]
            return DetectionResult(
                detected=True,
                league=league,
                sport=get_sport_for_league(league),
                tier=3,
                tier_detail='3c',  # Single candidate, no schedule check needed
                method=f"Only league with both teams: {league.upper()}",
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

        # Determine tier based on available date/time
        if game_date and game_time:
            # Tier 3a: Exact date + time
            return self._detect_tier3a(
                team1_id or team1,
                team2_id or team2,
                candidates,
                game_date,
                game_time
            )
        elif game_time:
            # Tier 3b: Time only, infer today
            return self._detect_tier3b(
                team1_id or team1,
                team2_id or team2,
                candidates,
                game_time
            )
        else:
            # Tier 3c: Teams only, find closest game
            return self._detect_tier3c(
                team1_id or team1,
                team2_id or team2,
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
    # SCHEDULE SEARCH HELPERS
    # ==========================================================================

    def _search_schedules(
        self,
        team1: str,
        team2: str,
        candidates: List[str],
        target_datetime: datetime = None,
        tolerance_minutes: int = None
    ) -> List[ScheduleMatch]:
        """
        Search schedules across multiple leagues for a team matchup.

        Args:
            team1: Team name or ID
            team2: Team name or ID
            candidates: List of league codes to search
            target_datetime: Specific datetime to match (None = search all)
            tolerance_minutes: Time tolerance for matching (None = any time)

        Returns:
            List of ScheduleMatch objects
        """
        from epg.league_config import get_league_config, parse_api_path

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

                # Search team1's schedule
                schedule = self.espn.get_team_schedule(sport, api_league, str(team1))
                if not schedule or 'events' not in schedule:
                    continue

                for event in schedule.get('events', []):
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

                        if str(team2) not in team_ids:
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
