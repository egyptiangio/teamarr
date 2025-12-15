"""Service layer for sports data access.

Consumers call services - never providers directly.
Services handle routing, caching, and provider selection.
"""

from services.sports_data import SportsDataService, create_default_service
from services.team_league_cache import (
    CacheRefresher,
    CacheStats,
    LeagueEntry,
    TeamEntry,
    TeamLeagueCache,
    expand_leagues,
    find_candidate_leagues,
    find_leagues_for_stream,
    get_cache,
    get_leagues_for_team,
    refresh_cache,
    refresh_cache_if_needed,
)

__all__ = [
    # Sports data service
    "SportsDataService",
    "create_default_service",
    # Team league cache
    "CacheRefresher",
    "CacheStats",
    "LeagueEntry",
    "TeamEntry",
    "TeamLeagueCache",
    "expand_leagues",
    "find_candidate_leagues",
    "find_leagues_for_stream",
    "get_cache",
    "get_leagues_for_team",
    "refresh_cache",
    "refresh_cache_if_needed",
]
