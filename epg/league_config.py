"""
Shared League Configuration Module

Centralizes league configuration lookups used by both TeamMatcher and EventMatcher.
Single source of truth: the league_config database table.
"""

from typing import Optional, Dict, Tuple, Callable

from utils.logger import get_logger

logger = get_logger(__name__)


# College leagues that need conference-based team fetching
# Includes both ESPN slugs (primary) and legacy aliases for backward compatibility
COLLEGE_LEAGUES = {
    # ESPN slugs (primary - used in league_config.league_code)
    'mens-college-basketball', 'womens-college-basketball', 'college-football',
    'mens-college-hockey', 'womens-college-hockey', 'mens-college-volleyball', 'womens-college-volleyball',
    'usa.ncaa.m.1', 'usa.ncaa.w.1',  # NCAA soccer
    # Legacy aliases (for backward compatibility during transition)
    'ncaam', 'ncaaw', 'ncaaf', 'ncaah', 'ncaawh', 'ncaavb-m', 'ncaavb-w', 'ncaas', 'ncaaws',
}


def get_league_config(
    league_code: str,
    db_connection_func: Optional[Callable] = None,
    cache: Optional[Dict[str, Dict]] = None
) -> Optional[Dict[str, str]]:
    """
    Get league configuration (sport, api_path) from database.

    Args:
        league_code: League code (e.g., 'nfl', 'epl', 'ncaam')
        db_connection_func: Function that returns a database connection.
        cache: Optional cache dict to store results. If provided, will
               check cache first and store results for future lookups.

    Returns:
        Dict with 'sport' and 'api_path' keys, or None if not found.
        Example: {'sport': 'football', 'api_path': 'football/nfl'}
    """
    league_lower = league_code.lower()

    # Check cache first
    if cache is not None and league_lower in cache:
        return cache[league_lower]

    # Database lookup
    if db_connection_func:
        try:
            conn = db_connection_func()
            cursor = conn.cursor()
            result = cursor.execute(
                "SELECT sport, api_path FROM league_config WHERE league_code = ?",
                (league_lower,)
            ).fetchone()
            conn.close()

            if result:
                config = {'sport': result[0], 'api_path': result[1]}
                if cache is not None:
                    cache[league_lower] = config
                return config
        except Exception as e:
            logger.error(f"Error fetching league config for {league_code}: {e}")

    logger.warning(f"No league config found for {league_code}")
    return None


def parse_api_path(api_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse api_path into (sport, league) tuple.

    Args:
        api_path: API path string (e.g., 'football/nfl', 'basketball/mens-college-basketball')

    Returns:
        Tuple of (sport, league) or (None, None) if invalid format.
        Example: ('football', 'nfl')
    """
    if not api_path:
        return None, None

    parts = api_path.split('/')
    if len(parts) == 2:
        return parts[0], parts[1]

    logger.error(f"Invalid api_path format: {api_path}")
    return None, None


def is_college_league(league_code: str) -> bool:
    """
    Check if a league is a college league (requires conference-based team fetching).

    Args:
        league_code: League code to check

    Returns:
        True if college league, False otherwise
    """
    return league_code.lower() in COLLEGE_LEAGUES or 'college' in league_code.lower()


def is_soccer_league(league_code: str) -> bool:
    """
    Check if a league is a soccer league by checking the soccer cache.

    Args:
        league_code: League code to check (e.g., 'eng.1', 'aus.1', 'usa.ncaa.m.1')

    Returns:
        True if soccer league, False otherwise
    """
    league_lower = league_code.lower()

    # Check if league exists in soccer cache (covers 240+ leagues)
    try:
        from database import get_connection
        conn = get_connection()
        cursor = conn.execute(
            "SELECT 1 FROM soccer_team_leagues WHERE league_slug = ? LIMIT 1",
            (league_lower,)
        )
        if cursor.fetchone():
            return True
    except Exception:
        pass

    # Fallback: Check league_config for sport='soccer'
    from database import get_connection
    config = get_league_config(league_code, get_connection)
    if config and config.get('sport') == 'soccer':
        return True

    return False


# =============================================================================
# SOCCER LEAGUE COMPATIBILITY
# =============================================================================
#
# ESPN's API has significant differences for soccer leagues vs US sports.
# This section documents all differences and provides utilities to handle them.
#
# DATA STRUCTURE DIFFERENCES:
# ┌─────────────────────┬──────────────────────────┬──────────────────────────┐
# │ Field               │ US Sports                │ Soccer                   │
# ├─────────────────────┼──────────────────────────┼──────────────────────────┤
# │ Record format       │ W-L or W-L-T             │ W-D-L (draws in middle)  │
# │ Home/Away records   │ Separate record items    │ Stats in total record    │
# │ Streak              │ Consecutive W/L          │ Draws break streaks      │
# │ Head coach          │ Current coach            │ Historical list (broken) │
# │ Division/Conference │ Structured hierarchy     │ None (stale data)        │
# │ Venue state         │ US state abbreviation    │ None (no states in UK)   │
# │ PPG/PAPG            │ Points per game          │ 0 (not applicable)       │
# │ Playoff seed        │ Playoff position         │ 0 (no playoffs)          │
# │ Schedule API        │ Past + future events     │ Past only (use scoreboard)│
# └─────────────────────┴──────────────────────────┴──────────────────────────┘
#
# AFFECTED TEMPLATE VARIABLES:
# - {team_record}, {opponent_record}: Use W-D-L format from ESPN summary
# - {home_record}, {away_record}: Built from homeWins/homeTies/homeLosses stats
# - {last_5_record}, {last_10_record}: Include draws in format
# - {home_streak}, {away_streak}: Draws break streaks (return empty)
# - {head_coach}: Disabled (returns empty) - ESPN data is corrupt
# - {pro_division}, {division_name}: Disabled (returns empty) - stale data
# - {venue_state}: Empty for non-US venues (graceful degradation)
# - {team_ppg}, {team_papg}: Returns 0 (not a soccer stat)
# - {playoff_seed}: Returns 0 (no playoffs in most soccer leagues)
#
# =============================================================================


class SoccerCompat:
    """
    Centralized soccer league compatibility utilities.

    Use these methods to handle soccer-specific data transformations
    instead of scattering is_soccer_league() checks throughout the code.
    """

    @staticmethod
    def should_skip_coach(league: str) -> bool:
        """
        Check if coach lookup should be skipped.

        ESPN's soccer coach data is completely unreliable - returns wrong
        managers or managers who never worked at those clubs.

        Returns:
            True if coach should be skipped (returns empty), False otherwise
        """
        return is_soccer_league(league)

    @staticmethod
    def should_skip_division(league: str) -> bool:
        """
        Check if division/conference lookup should be skipped.

        Soccer leagues don't have meaningful divisions/conferences and
        ESPN returns stale/garbage data (e.g., "English Premiership 2001-2002").

        Returns:
            True if division lookup should be skipped, False otherwise
        """
        return is_soccer_league(league)

    @staticmethod
    def uses_draws(league: str) -> bool:
        """
        Check if the league uses draws (ties that count differently).

        Soccer uses W-D-L format where draws are in the middle.
        US sports use W-L or W-L-T where ties are at the end.

        Returns:
            True if draws should be formatted in the middle (W-D-L)
        """
        return is_soccer_league(league)

    @staticmethod
    def draws_break_streaks(league: str) -> bool:
        """
        Check if draws break win/loss streaks.

        In soccer, a draw ends both winning and losing streaks.
        In US sports, ties are rare and don't typically affect streak counting.

        Returns:
            True if draws should break streaks, False otherwise
        """
        return is_soccer_league(league)

    @staticmethod
    def needs_scoreboard_for_schedule(league: str) -> bool:
        """
        Check if scoreboard API is needed to get future fixtures.

        ESPN's schedule API for soccer only returns past match results,
        not upcoming fixtures. The scoreboard API must be used instead.

        Returns:
            True if scoreboard is needed for future games, False otherwise
        """
        return is_soccer_league(league)

    @staticmethod
    def format_record(wins: int, losses: int, draws: int, league: str) -> str:
        """
        Format a record string appropriate for the league.

        Args:
            wins: Number of wins
            losses: Number of losses
            draws: Number of draws/ties
            league: League code

        Returns:
            Formatted record string (e.g., "6-2-4" for soccer, "6-4" for NFL)
        """
        if is_soccer_league(league):
            # Soccer: W-D-L format (draws in middle)
            return f"{wins}-{draws}-{losses}"
        elif draws > 0:
            # US sports with ties: W-L-T format
            return f"{wins}-{losses}-{draws}"
        else:
            # US sports without ties: W-L format
            return f"{wins}-{losses}"

    @staticmethod
    def get_disabled_fields(league: str) -> set:
        """
        Get the set of fields that should be empty/disabled for this league.

        Returns:
            Set of field names that should return empty values
        """
        if is_soccer_league(league):
            return {
                'head_coach',       # ESPN data is corrupt
                'division_name',    # Stale data
                'division_abbrev',  # Stale data
                'division_id',      # Meaningless for soccer
            }
        return set()

    @staticmethod
    def get_zero_fields(league: str) -> set:
        """
        Get the set of fields that return 0 but are not applicable.

        These fields return 0 from ESPN but aren't meaningful for the sport.
        They're not "disabled" but users should know they're N/A.

        Returns:
            Set of field names that return 0 but aren't applicable
        """
        if is_soccer_league(league):
            return {
                'ppg',           # Points per game - not a soccer stat
                'papg',          # Points against per game - not a soccer stat
                'playoff_seed',  # Most soccer leagues don't have playoffs
            }
        return set()
