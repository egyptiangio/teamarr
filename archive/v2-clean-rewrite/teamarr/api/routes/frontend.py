"""Frontend API endpoints.

This module provides API endpoints specifically for the UI.
All endpoints use V2's clean infrastructure (providers, services, cache)
but return data in the exact format the UI expects.

Endpoint groups:
- /api/variables - Template variable definitions
- /api/condition-presets - Saved condition presets (CRUD)
- /api/leagues - League discovery and team import
- /api/teams - Team management
- /api/event-epg - Event-based EPG management
- /api/cache - Cache status and refresh
- /api/settings - Settings management
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from teamarr.consumers.team_league_cache import CacheRefresher, get_cache
from teamarr.database import get_db

logger = logging.getLogger(__name__)

# Create router without prefix - we'll mount at root to match /api/* paths
router = APIRouter(tags=["Frontend"])


# =============================================================================
# Template Variables
# =============================================================================


@router.get("/api/variables")
def get_template_variables() -> dict:
    """Get all template variables with their definitions.

    Returns the variables.json config file that defines all available
    template variables, their categories, formats, and examples.
    """
    # Load variables from config file
    config_path = Path(__file__).parent.parent.parent / "config" / "variables.json"

    if not config_path.exists():
        # Return a minimal structure if config doesn't exist yet
        return {
            "total_variables": 0,
            "categories": [],
            "variables": [],
        }

    try:
        with open(config_path, encoding="utf-8") as f:
            variables_data = json.load(f)

        # Ensure all variables have available_suffixes field
        for var in variables_data.get("variables", []):
            if "available_suffixes" not in var or not var["available_suffixes"]:
                var["available_suffixes"] = ["base", "next", "last"]

        return variables_data
    except Exception as e:
        logger.error(f"Failed to load variables.json: {e}")
        return {
            "error": "Failed to load variables",
            "total_variables": 0,
            "variables": [],
        }


# =============================================================================
# Condition Presets
# =============================================================================


class ConditionPresetCreate(BaseModel):
    """Request body for creating a condition preset."""

    name: str
    description: str | None = None
    conditions: list[dict]


@router.get("/api/condition-presets")
def list_condition_presets() -> list[dict]:
    """Get all saved condition presets."""
    with get_db() as conn:
        cursor = conn.execute(
            """SELECT id, name, description, conditions, created_at
            FROM condition_presets ORDER BY name"""
        )
        presets = []
        for row in cursor.fetchall():
            preset = dict(row)
            # Parse conditions JSON
            if preset.get("conditions"):
                try:
                    preset["conditions"] = json.loads(preset["conditions"])
                except json.JSONDecodeError:
                    preset["conditions"] = []
            presets.append(preset)
        return presets


@router.post("/api/condition-presets")
def create_condition_preset(preset: ConditionPresetCreate) -> dict:
    """Create a new condition preset."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO condition_presets (name, description, conditions)
            VALUES (?, ?, ?)
            """,
            (preset.name, preset.description, json.dumps(preset.conditions)),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "name": preset.name, "status": "created"}


@router.delete("/api/condition-presets/{preset_id}")
def delete_condition_preset(preset_id: int) -> dict:
    """Delete a condition preset."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM condition_presets WHERE id = ?", (preset_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Preset not found")
        return {"status": "deleted", "id": preset_id}


# =============================================================================
# Leagues & Team Import
# =============================================================================


def _get_league_metadata() -> dict:
    """Get league metadata including logos and display info.

    Returns a dict of league_code -> metadata for UI enrichment.
    """
    # Static metadata for known leagues (logos, display names, etc.)
    # This can be extended or moved to database later
    return {
        "nfl": {
            "name": "NFL",
            "logo": "https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png",
            "sport": "football",
            "hasConferences": True,
        },
        "nba": {
            "name": "NBA",
            "logo": "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
            "sport": "basketball",
            "hasConferences": True,
        },
        "nhl": {
            "name": "NHL",
            "logo": "https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png",
            "sport": "hockey",
            "hasConferences": True,
        },
        "mlb": {
            "name": "MLB",
            "logo": "https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png",
            "sport": "baseball",
            "hasConferences": True,
        },
        "mls": {
            "name": "MLS",
            "logo": "https://a.espncdn.com/i/teamlogos/leagues/500/mls.png",
            "sport": "soccer",
            "hasConferences": True,
        },
        "college-football": {
            "name": "College Football",
            "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/ncaa.png",
            "sport": "football",
            "hasConferences": True,
        },
        "mens-college-basketball": {
            "name": "Men's College Basketball",
            "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/ncaa.png",
            "sport": "basketball",
            "hasConferences": True,
        },
        "womens-college-basketball": {
            "name": "Women's College Basketball",
            "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/ncaa.png",
            "sport": "basketball",
            "hasConferences": True,
        },
        "wnba": {
            "name": "WNBA",
            "logo": "https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png",
            "sport": "basketball",
            "hasConferences": False,
        },
        # Soccer leagues
        "eng.1": {
            "name": "Premier League",
            "logo": "https://a.espncdn.com/i/leaguelogos/soccer/500/23.png",
            "sport": "soccer",
            "hasConferences": False,
        },
        "esp.1": {
            "name": "La Liga",
            "logo": "https://a.espncdn.com/i/leaguelogos/soccer/500/15.png",
            "sport": "soccer",
            "hasConferences": False,
        },
        "ger.1": {
            "name": "Bundesliga",
            "logo": "https://a.espncdn.com/i/leaguelogos/soccer/500/10.png",
            "sport": "soccer",
            "hasConferences": False,
        },
        "ita.1": {
            "name": "Serie A",
            "logo": "https://a.espncdn.com/i/leaguelogos/soccer/500/12.png",
            "sport": "soccer",
            "hasConferences": False,
        },
        "fra.1": {
            "name": "Ligue 1",
            "logo": "https://a.espncdn.com/i/leaguelogos/soccer/500/9.png",
            "sport": "soccer",
            "hasConferences": False,
        },
        "uefa.champions": {
            "name": "UEFA Champions League",
            "logo": "https://a.espncdn.com/i/leaguelogos/soccer/500/2.png",
            "sport": "soccer",
            "hasConferences": False,
        },
        # Add more as needed...
    }


@router.get("/api/leagues")
def list_leagues() -> dict:
    """Get all available leagues for team import.

    Combines cached league data with static metadata (logos, display names).
    Returns in format expected by team_import.html.
    """
    cache = get_cache()
    cached_leagues = cache.get_all_leagues()
    metadata = _get_league_metadata()

    leagues = []
    for league in cached_leagues:
        league_code = league.league_slug
        meta = metadata.get(league_code, {})

        leagues.append({
            "code": league_code,
            "name": meta.get("name", league.league_name),
            "logo": meta.get("logo", league.logo_url or ""),
            "sport": league.sport,
            "provider": league.provider,
            "team_count": league.team_count,
            "hasConferences": meta.get("hasConferences", False),
        })

    # Sort by sport then name
    leagues.sort(key=lambda x: (x["sport"], x["name"]))

    return {"leagues": leagues, "count": len(leagues)}


@router.get("/api/leagues/{league_code}/teams")
def get_league_teams(league_code: str) -> dict:
    """Get all teams for a league.

    Returns teams in format expected by team_import.html.
    """
    with get_db() as conn:
        # Get teams from cache
        cursor = conn.execute(
            """
            SELECT team_name, team_abbrev, team_short_name, provider,
                   provider_team_id, league, sport, logo_url
            FROM team_cache
            WHERE league = ?
            ORDER BY team_name
            """,
            (league_code,),
        )

        teams = []
        for row in cursor.fetchall():
            # Check if already imported
            imported = conn.execute(
                """
                SELECT id FROM teams
                WHERE provider_team_id = ? AND league = ?
                """,
                (row["provider_team_id"], league_code),
            ).fetchone()

            teams.append({
                "id": row["provider_team_id"],
                "name": row["team_name"],
                "abbreviation": row["team_abbrev"] or "",
                "shortName": row["team_short_name"] or row["team_name"],
                "logo": row["logo_url"] or "",
                "provider": row["provider"],
                "isImported": imported is not None,
            })

    return {"teams": teams, "count": len(teams), "league": league_code}


@router.get("/api/leagues/{league_code}/conferences/batch")
def get_league_conferences_batch(league_code: str) -> dict:
    """Get conferences with their teams for a league.

    For leagues with conferences (NFL, NBA, college), returns teams
    grouped by conference. For leagues without conferences, returns
    a flat structure.
    """
    # Get teams first
    teams_response = get_league_teams(league_code)
    teams = teams_response["teams"]

    # For now, return flat structure (conference support can be added later)
    # The UI handles this gracefully by falling back to flat team list
    return {
        "conferences": [],
        "teams": teams,
        "count": len(teams),
        "league": league_code,
        "hasConferences": False,
    }


@router.get("/api/teams/imported")
def get_imported_teams() -> dict:
    """Get list of already imported teams.

    Returns team IDs that have been imported, for UI to show import status.
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT provider_team_id, league FROM teams"
        )
        imported = [
            {"teamId": row["provider_team_id"], "league": row["league"]}
            for row in cursor.fetchall()
        ]
    return {"imported": imported, "count": len(imported)}


class BulkImportRequest(BaseModel):
    """Request body for bulk team import."""

    teams: list[dict]
    template_id: int | None = None


@router.post("/api/teams/bulk-import")
def bulk_import_teams(request: BulkImportRequest) -> dict:
    """Bulk import teams from provider.

    Creates team records in database for selected teams.
    """
    imported = []
    errors = []

    with get_db() as conn:
        for team_data in request.teams:
            try:
                # Check if already exists
                existing = conn.execute(
                    """
                    SELECT id FROM teams
                    WHERE provider_team_id = ? AND league = ?
                    """,
                    (team_data["id"], team_data["league"]),
                ).fetchone()

                if existing:
                    errors.append({
                        "team": team_data["name"],
                        "error": "Already imported",
                    })
                    continue

                # Generate channel_id from team name
                channel_id = team_data["name"].lower().replace(" ", "_").replace(".", "")

                conn.execute(
                    """
                    INSERT INTO teams (
                        provider, provider_team_id, league, sport,
                        team_name, team_abbrev, team_logo_url,
                        channel_id, template_id, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        team_data.get("provider", "espn"),
                        team_data["id"],
                        team_data["league"],
                        team_data.get("sport", ""),
                        team_data["name"],
                        team_data.get("abbreviation", ""),
                        team_data.get("logo", ""),
                        channel_id,
                        request.template_id,
                    ),
                )
                imported.append(team_data["name"])
            except Exception as e:
                errors.append({"team": team_data.get("name", "Unknown"), "error": str(e)})

        conn.commit()

    return {
        "imported": imported,
        "imported_count": len(imported),
        "errors": errors,
        "error_count": len(errors),
    }


# =============================================================================
# Soccer Multi-League Support
# =============================================================================


@router.get("/api/soccer/team/{team_id}/leagues")
def get_soccer_team_leagues(team_id: str) -> dict:
    """Get all leagues a soccer team plays in.

    Soccer teams often play in multiple competitions (league, cup, European).
    Returns all leagues where this team is found.
    """
    cache = get_cache()
    leagues = cache.get_team_leagues(team_id, provider="espn")

    return {
        "team_id": team_id,
        "leagues": leagues,
        "count": len(leagues),
    }


# =============================================================================
# Cache Management
# =============================================================================


@router.get("/api/cache/team-league/status")
def get_team_league_cache_status() -> dict:
    """Get team/league cache status."""
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


@router.post("/api/cache/team-league/refresh")
def refresh_team_league_cache(background_tasks: BackgroundTasks) -> dict:
    """Trigger team/league cache refresh."""
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

    background_tasks.add_task(run_refresh)

    return {
        "status": "started",
        "message": "Cache refresh started in background",
    }


@router.get("/api/soccer/cache/status")
def get_soccer_cache_status() -> dict:
    """Get soccer-specific cache status (alias for team-league cache)."""
    return get_team_league_cache_status()


@router.post("/api/soccer/cache/refresh")
def refresh_soccer_cache(background_tasks: BackgroundTasks) -> dict:
    """Refresh soccer cache (alias for team-league refresh)."""
    return refresh_team_league_cache(background_tasks)


@router.post("/api/cache/refresh-all")
def refresh_all_caches(background_tasks: BackgroundTasks) -> dict:
    """Refresh all caches."""
    return refresh_team_league_cache(background_tasks)


# =============================================================================
# Team Management
# =============================================================================


@router.post("/teams/{team_id}/toggle-status")
def toggle_team_status(team_id: int) -> dict:
    """Toggle a team's active status."""
    with get_db() as conn:
        # Get current status
        row = conn.execute(
            "SELECT active FROM teams WHERE id = ?", (team_id,)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Team not found")

        new_status = 0 if row["active"] else 1
        conn.execute(
            "UPDATE teams SET active = ? WHERE id = ?",
            (new_status, team_id),
        )
        conn.commit()

    return {"id": team_id, "active": bool(new_status)}


# =============================================================================
# Settings - Exception Keywords
# =============================================================================


@router.get("/api/settings/exception-keywords")
def list_exception_keywords() -> list[dict]:
    """Get all exception keywords for channel consolidation."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT id, keywords, behavior, enabled, created_at
            FROM consolidation_exception_keywords
            ORDER BY keywords
            """
        )
        return [dict(row) for row in cursor.fetchall()]


@router.delete("/api/settings/exception-keywords/{keyword_id}")
def delete_exception_keyword(keyword_id: int) -> dict:
    """Delete an exception keyword."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM consolidation_exception_keywords WHERE id = ?",
            (keyword_id,),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Keyword not found")
    return {"status": "deleted", "id": keyword_id}


# =============================================================================
# Helper function to get league logos dict for UI templates
# =============================================================================


def get_league_logos_dict() -> dict[str, str]:
    """Get league code -> logo URL mapping for UI templates.

    This is used by UI routes to populate the league_logos context variable.
    """
    cache = get_cache()
    cached_leagues = cache.get_all_leagues()
    metadata = _get_league_metadata()

    logos = {}
    for league in cached_leagues:
        code = league.league_slug
        # Prefer static metadata logo, fall back to cached
        logos[code] = metadata.get(code, {}).get("logo", league.logo_url or "")

    return logos
