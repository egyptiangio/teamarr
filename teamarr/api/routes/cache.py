"""Team and league cache API endpoints.

Provides endpoints for cache management:
- GET /cache/status - Get cache statistics
- POST /cache/refresh - Trigger cache refresh
- GET /cache/leagues - List cached leagues
- GET /cache/teams/search - Search teams by name
- GET /cache/candidate-leagues - Find candidate leagues for a matchup
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Query

from teamarr.consumers.cache import (
    CacheRefresher,
    get_cache,
)
from teamarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cache")


@router.get("/status")
def get_cache_status() -> dict:
    """Get cache statistics and status.

    Returns:
        Cache status including last refresh time, counts, and staleness
    """
    cache = get_cache()
    stats = cache.get_cache_stats()

    return {
        "last_refresh": stats.last_refresh.isoformat() if stats.last_refresh else None,
        "leagues_count": stats.leagues_count,
        "teams_count": stats.teams_count,
        "refresh_duration_seconds": stats.refresh_duration_seconds,
        "is_stale": stats.is_stale,
        "is_empty": cache.is_cache_empty(),
        "refresh_in_progress": stats.refresh_in_progress,
        "last_error": stats.last_error,
    }


@router.post("/refresh")
def trigger_refresh(background_tasks: BackgroundTasks) -> dict:
    """Trigger a cache refresh from all providers.

    This runs in the background. Check /cache/status for progress.

    Returns:
        Acknowledgement that refresh was started
    """
    cache = get_cache()
    stats = cache.get_cache_stats()

    if stats.refresh_in_progress:
        return {
            "status": "already_running",
            "message": "Cache refresh is already in progress",
        }

    def run_refresh():
        refresher = CacheRefresher(get_db)
        result = refresher.refresh()
        logger.info(f"Cache refresh completed: {result}")

    # Run in background thread
    background_tasks.add_task(run_refresh)

    return {
        "status": "started",
        "message": "Cache refresh started in background",
    }


@router.get("/leagues")
def list_leagues(
    sport: str | None = Query(None, description="Filter by sport (e.g., 'soccer')"),
    provider: str | None = Query(None, description="Filter by provider"),
    import_only: bool = Query(False, description="Only import-enabled leagues"),
) -> dict:
    """List all available leagues.

    By default, returns all leagues (configured + discovered).
    Use import_only=True for Team Importer to get only explicitly configured
    leagues with import_enabled=1.

    Args:
        sport: Optional sport filter
        provider: Optional provider filter
        import_only: If True, only return import-enabled configured leagues

    Returns:
        List of leagues
    """
    cache = get_cache()
    leagues = cache.get_all_leagues(sport=sport, provider=provider, import_enabled_only=import_only)

    return {
        "count": len(leagues),
        "leagues": [
            {
                "slug": league.league_slug,
                "provider": league.provider,
                "name": league.league_name,
                "sport": league.sport,
                "team_count": league.team_count,
                "logo_url": league.logo_url,
                "import_enabled": league.import_enabled,
            }
            for league in leagues
        ],
    }


@router.get("/teams/search")
def search_teams(
    q: str = Query(..., min_length=2, description="Search query (team name)"),
    league: str | None = Query(None, description="Filter by league slug"),
    sport: str | None = Query(None, description="Filter by sport"),
) -> dict:
    """Search for teams in the cache.

    Args:
        q: Search query (partial match on team name)
        league: Optional league filter
        sport: Optional sport filter

    Returns:
        Matching teams
    """
    q_lower = q.lower().strip()

    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT team_name, team_abbrev, team_short_name, provider,
                   provider_team_id, league, sport, logo_url
            FROM team_cache
            WHERE (LOWER(team_name) LIKE ?
                   OR LOWER(team_abbrev) = ?
                   OR LOWER(team_short_name) LIKE ?)
        """
        params: list = [f"%{q_lower}%", q_lower, f"%{q_lower}%"]

        if league:
            query += " AND league = ?"
            params.append(league)
        if sport:
            query += " AND sport = ?"
            params.append(sport)

        query += " ORDER BY team_name LIMIT 50"

        cursor.execute(query, params)

        teams = [
            {
                "name": row["team_name"],
                "abbrev": row["team_abbrev"],
                "short_name": row["team_short_name"],
                "provider": row["provider"],
                "team_id": row["provider_team_id"],
                "league": row["league"],
                "sport": row["sport"],
                "logo_url": row["logo_url"],
            }
            for row in cursor.fetchall()
        ]

    return {
        "query": q,
        "count": len(teams),
        "teams": teams,
    }


@router.get("/candidate-leagues")
def find_candidate_leagues(
    team1: str = Query(..., min_length=2, description="First team name"),
    team2: str = Query(..., min_length=2, description="Second team name"),
    sport: str | None = Query(None, description="Filter by sport"),
) -> dict:
    """Find leagues where both teams exist.

    Used for event matching - given two team names, find which leagues
    they could both be playing in.

    Args:
        team1: First team name
        team2: Second team name
        sport: Optional sport filter

    Returns:
        List of (league, provider) tuples where both teams exist
    """
    cache = get_cache()
    candidates = cache.find_candidate_leagues(team1, team2, sport)

    return {
        "team1": team1,
        "team2": team2,
        "candidates": [{"league": league, "provider": provider} for league, provider in candidates],
        "count": len(candidates),
    }


@router.get("/leagues/{league_slug}/teams")
def get_league_teams(league_slug: str) -> list[dict]:
    """Get all teams for a specific league.

    Args:
        league_slug: League identifier (e.g., 'nfl', 'eng.1')

    Returns:
        List of teams in the league
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, team_name, team_abbrev, team_short_name, provider,
                   provider_team_id, league, sport, logo_url
            FROM team_cache
            WHERE league = ?
            ORDER BY team_name
            """,
            (league_slug,),
        )

        return [
            {
                "id": row["id"],
                "team_name": row["team_name"],
                "team_abbrev": row["team_abbrev"],
                "team_short_name": row["team_short_name"],
                "provider": row["provider"],
                "provider_team_id": row["provider_team_id"],
                "league": row["league"],
                "sport": row["sport"],
                "logo_url": row["logo_url"],
            }
            for row in cursor.fetchall()
        ]


@router.get("/team-leagues/{provider}/{provider_team_id}")
def get_team_leagues(provider: str, provider_team_id: str) -> dict:
    """Get all leagues a team plays in.

    Used for multi-league display (e.g., soccer teams playing in
    domestic league, Champions League, cup competitions, etc.)

    Args:
        provider: Provider name ('espn' or 'tsdb')
        provider_team_id: Team ID from the provider

    Returns:
        Dict with team info and list of leagues
    """
    cache = get_cache()
    leagues = cache.get_team_leagues(provider_team_id, provider)

    # Get league details for each
    league_details = []
    for league_slug in leagues:
        league_entries = cache.get_all_leagues()
        for entry in league_entries:
            if entry.league_slug == league_slug:
                league_details.append(
                    {
                        "slug": entry.league_slug,
                        "name": entry.league_name,
                        "sport": entry.sport,
                        "logo_url": entry.logo_url,
                    }
                )
                break
        else:
            # League not found in cache, add basic info
            league_details.append(
                {
                    "slug": league_slug,
                    "name": league_slug.upper(),
                    "sport": None,
                    "logo_url": None,
                }
            )

    return {
        "provider": provider,
        "provider_team_id": provider_team_id,
        "leagues": league_details,
        "count": len(league_details),
    }


@router.get("/league/{league_slug}")
def get_league_info(league_slug: str) -> dict:
    """Get info for a specific league.

    Args:
        league_slug: League identifier (e.g., 'nfl', 'eng.1')

    Returns:
        League metadata or 404
    """
    cache = get_cache()
    league = cache.get_league_info(league_slug)

    if not league:
        return {"error": "League not found", "league": league_slug}

    return {
        "slug": league.league_slug,
        "provider": league.provider,
        "name": league.league_name,
        "sport": league.sport,
        "team_count": league.team_count,
    }
