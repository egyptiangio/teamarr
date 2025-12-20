"""Unified team and league cache.

Provides reverse-lookup for:
1. Event matching: "Freiburg vs Stuttgart" → candidate leagues
2. Team multi-league: Liverpool → [eng.1, uefa.champions, eng.fa, ...]
3. League discovery: all soccer leagues for "soccer_all"

Caches data from all registered providers (ESPN, TSDB, etc.).
Refresh weekly to handle promotion/relegation.
"""

from collections.abc import Callable

from teamarr.database import get_db

from .constants import KNOWN_LEAGUE_LOGOS, KNOWN_LEAGUE_NAMES, SPORT_PATTERNS
from .queries import TeamLeagueCache
from .refresh import CacheRefresher
from .types import CacheStats, LeagueEntry, TeamEntry

# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def get_cache() -> TeamLeagueCache:
    """Get default cache instance."""
    return TeamLeagueCache()


def refresh_cache(
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict:
    """Refresh cache from all providers."""
    return CacheRefresher().refresh(progress_callback)


def refresh_cache_if_needed(max_age_days: int = 7) -> bool:
    """Refresh cache if stale."""
    return CacheRefresher().refresh_if_needed(max_age_days)


def expand_leagues(
    leagues: list[str],
    provider: str | None = None,
) -> list[str]:
    """Expand special league patterns to actual league slugs.

    Handles patterns like:
    - "soccer_all" → all cached soccer leagues
    - "nfl" → ["nfl"]  (pass-through)

    Args:
        leagues: List of league patterns
        provider: Optional provider filter ('espn', 'tsdb')

    Returns:
        Expanded list of league slugs
    """
    cache = get_cache()
    result = []

    for league in leagues:
        if league == "soccer_all":
            # Expand to all soccer leagues
            soccer_leagues = cache.get_all_leagues(sport="soccer", provider=provider)
            result.extend(lg.league_slug for lg in soccer_leagues)
        elif league.endswith("_all"):
            # General pattern: sport_all → all leagues for that sport
            sport = league[:-4]  # Remove "_all" suffix
            sport_leagues = cache.get_all_leagues(sport=sport, provider=provider)
            result.extend(lg.league_slug for lg in sport_leagues)
        else:
            # Pass-through
            result.append(league)

    # Remove duplicates while preserving order
    seen: set = set()
    return [lg for lg in result if not (lg in seen or seen.add(lg))]  # type: ignore


def find_leagues_for_stream(
    stream_name: str,
    sport: str | None = None,
    provider: str | None = None,
    max_results: int = 5,
) -> list[str]:
    """Find candidate leagues for a stream by searching team cache.

    Scans the team cache for team names that appear in the stream,
    then returns the leagues those teams play in.

    This is useful for soccer matching where there are 300+ leagues -
    we can narrow down to just a few based on team name matches.

    Args:
        stream_name: Stream name to search
        sport: Optional sport filter
        provider: Optional provider filter
        max_results: Maximum leagues to return

    Returns:
        List of candidate league slugs
    """
    stream_lower = stream_name.lower()
    candidate_leagues: set = set()

    with get_db() as conn:
        cursor = conn.cursor()

        # Build query to find teams whose names appear in the stream
        query = """
            SELECT DISTINCT league, team_name, team_abbrev, team_short_name
            FROM team_cache
            WHERE 1=1
        """
        params: list = []

        if sport:
            query += " AND sport = ?"
            params.append(sport)
        if provider:
            query += " AND provider = ?"
            params.append(provider)

        cursor.execute(query, params)

        # Check each team against the stream name
        for row in cursor.fetchall():
            league = row["league"]

            # Check if team name variants appear in stream
            for name_field in ["team_name", "team_short_name", "team_abbrev"]:
                name = row[name_field]
                if name and len(name) >= 3:  # Skip very short names
                    name_lower = name.lower()
                    if name_lower in stream_lower:
                        candidate_leagues.add(league)
                        break  # Found a match for this team, move to next

            if len(candidate_leagues) >= max_results:
                break

    return list(candidate_leagues)[:max_results]


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Types
    "CacheStats",
    "TeamEntry",
    "LeagueEntry",
    # Constants
    "KNOWN_LEAGUE_LOGOS",
    "KNOWN_LEAGUE_NAMES",
    "SPORT_PATTERNS",
    # Classes
    "TeamLeagueCache",
    "CacheRefresher",
    # Functions
    "get_cache",
    "refresh_cache",
    "refresh_cache_if_needed",
    "expand_leagues",
    "find_leagues_for_stream",
]
