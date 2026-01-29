"""Teams API endpoints."""

import json
import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from teamarr.api.models import TeamCreate, TeamResponse, TeamUpdate
from teamarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


def generate_channel_id(team_name: str, primary_league: str) -> str:
    """Generate channel ID from team name and league."""
    from teamarr.database.leagues import get_league_id

    name = "".join(
        word.capitalize()
        for word in "".join(c if c.isalnum() or c.isspace() else "" for c in team_name).split()
    )

    with get_db() as conn:
        league_id = get_league_id(conn, primary_league)

    return f"{name}.{league_id}"


def _parse_leagues(leagues_str: str | None) -> list[str]:
    """Parse leagues JSON string to list."""
    if not leagues_str:
        return []
    try:
        return json.loads(leagues_str)
    except (json.JSONDecodeError, TypeError):
        return []


def _get_league_sport(conn, league_code: str) -> str | None:
    """Get sport for a league from the database."""
    cursor = conn.execute(
        "SELECT sport FROM leagues WHERE league_code = ?",
        (league_code,),
    )
    row = cursor.fetchone()
    return row["sport"].lower() if row else None


def _get_all_leagues_from_cache(
    conn, provider: str, provider_team_id: str, sport: str
) -> list[str]:
    """Get all leagues a team appears in from the cache for a given sport."""
    cursor = conn.execute(
        "SELECT DISTINCT league FROM team_cache WHERE provider = ? AND provider_team_id = ? AND sport = ?",  # noqa: E501
        (provider, provider_team_id, sport),
    )
    return [row["league"] for row in cursor.fetchall()]


def _can_consolidate_leagues(conn, league1: str, league2: str) -> bool:
    """Check if two leagues can be consolidated (same team plays in both).

    ONLY soccer teams play in multiple competitions (EPL + Champions League),
    so only soccer leagues can consolidate.

    All other sports (NFL, NCAAF, NHL, NBA, etc.) have separate teams per league
    and ESPN reuses team IDs across leagues, so they must NOT be consolidated.

    Returns:
        True if leagues can share a team, False if they must be separate.
    """
    if league1 == league2:
        return True

    # Only soccer leagues can consolidate across competitions
    sport1 = _get_league_sport(conn, league1)
    sport2 = _get_league_sport(conn, league2)

    if sport1 == "soccer" and sport2 == "soccer":
        return True

    # All other sports: do not consolidate
    return False


def _row_to_response(row) -> dict:
    """Convert database row to response dict with parsed leagues."""
    data = dict(row)
    data["leagues"] = _parse_leagues(data.get("leagues"))
    return data


class BulkImportTeam(BaseModel):
    """Team data from cache for bulk import."""

    team_name: str
    team_abbrev: str | None = None
    provider: str
    provider_team_id: str
    league: str  # League this team was found in
    sport: str
    logo_url: str | None = None


class BulkImportRequest(BaseModel):
    """Bulk import request body."""

    teams: list[BulkImportTeam]


class BulkImportResponse(BaseModel):
    """Bulk import result."""

    imported: int
    updated: int  # Teams that had new leagues added
    skipped: int


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(active_only: bool = False):
    """List all teams."""
    with get_db() as conn:
        if active_only:
            cursor = conn.execute("SELECT * FROM teams WHERE active = 1 ORDER BY team_name")
        else:
            cursor = conn.execute("SELECT * FROM teams ORDER BY team_name")
        return [_row_to_response(row) for row in cursor.fetchall()]


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(team: TeamCreate):
    """Create a new team."""
    # Ensure primary_league is in leagues list
    leagues = list(set(team.leagues + [team.primary_league]))
    leagues_json = json.dumps(sorted(leagues))

    with get_db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO teams (
                    provider, provider_team_id, primary_league, leagues, sport,
                    team_name, team_abbrev, team_logo_url, team_color,
                    channel_id, channel_logo_url, template_id, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team.provider,
                    team.provider_team_id,
                    team.primary_league,
                    leagues_json,
                    team.sport,
                    team.team_name,
                    team.team_abbrev,
                    team.team_logo_url,
                    team.team_color,
                    team.channel_id,
                    team.channel_logo_url,
                    team.template_id,
                    team.active,
                ),
            )
            team_id = cursor.lastrowid
            logger.info("[CREATED] Team id=%d name=%s", team_id, team.team_name)
            cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
            return _row_to_response(cursor.fetchone())
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Team with this channel_id or provider/team_id/sport already exists",
                ) from None
            raise


@router.get("/teams/{team_id}", response_model=TeamResponse)
def get_team(team_id: int):
    """Get a team by ID."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        return _row_to_response(row)


@router.put("/teams/{team_id}", response_model=TeamResponse)
@router.patch("/teams/{team_id}", response_model=TeamResponse)
def update_team(team_id: int, team: TeamUpdate):
    """Update a team (full or partial)."""
    updates = {k: v for k, v in team.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    # Convert leagues list to JSON if present
    if "leagues" in updates:
        updates["leagues"] = json.dumps(updates["leagues"])

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [team_id]

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE teams SET {set_clause} WHERE id = ?", values)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

        # Clean up XMLTV content when team is deactivated
        if updates.get("active") is False:
            conn.execute("DELETE FROM team_epg_xmltv WHERE team_id = ?", (team_id,))

        logger.info("[UPDATED] Team id=%d fields=%s", team_id, list(updates.keys()))
        cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
        return _row_to_response(cursor.fetchone())


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: int):
    """Delete a team and its associated XMLTV content."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        # Clean up orphaned XMLTV content
        conn.execute("DELETE FROM team_epg_xmltv WHERE team_id = ?", (team_id,))
        logger.info("[DELETED] Team id=%d", team_id)


@router.post("/teams/bulk-import", response_model=BulkImportResponse)
def bulk_import_teams(request: BulkImportRequest):
    """Bulk import teams from cache.

    Key behavior:
    - Soccer: teams play in multiple competitions (EPL + Champions League), so
      they are consolidated by (provider, provider_team_id, sport). New leagues
      are added to existing team's leagues array.
    - Non-soccer: ESPN reuses team IDs across leagues for DIFFERENT teams
      (e.g., ID 8 = Detroit Pistons in NBA, Minnesota Lynx in WNBA).
      Each league gets its own team entry.
    """
    imported = 0
    updated = 0
    skipped = 0

    with get_db() as conn:
        # Build two indexes for existing teams:
        # 1. Full key (provider, id, sport, league) - for exact lookups
        # 2. Sport key (provider, id, sport) - for soccer consolidation lookups
        cursor = conn.execute(
            "SELECT id, provider, provider_team_id, sport, primary_league, leagues FROM teams"
        )
        existing_full: dict[tuple[str, str, str, str], tuple[int, list[str]]] = {}
        existing_sport: dict[tuple[str, str, str], list[tuple[int, str, list[str]]]] = {}

        for row in cursor.fetchall():
            full_key = (
                row["provider"],
                row["provider_team_id"],
                row["sport"],
                row["primary_league"],
            )
            sport_key = (row["provider"], row["provider_team_id"], row["sport"])
            leagues = _parse_leagues(row["leagues"])

            existing_full[full_key] = (row["id"], leagues)
            if sport_key not in existing_sport:
                existing_sport[sport_key] = []
            existing_sport[sport_key].append((row["id"], row["primary_league"], leagues))

        # Pre-load all leagues from team_cache for soccer teams (avoids N+1 queries)
        soccer_teams = [t for t in request.teams if t.sport.lower() == "soccer"]
        team_cache_leagues: dict[tuple[str, str, str], list[str]] = {}
        if soccer_teams:
            # Build placeholders for batch query
            keys = [(t.provider, t.provider_team_id, t.sport) for t in soccer_teams]
            unique_keys = list(set(keys))
            if unique_keys:
                # Query all leagues at once
                placeholders = " OR ".join(
                    ["(provider = ? AND provider_team_id = ? AND sport = ?)"] * len(unique_keys)
                )
                params = [val for key in unique_keys for val in key]
                cursor = conn.execute(
                    f"SELECT provider, provider_team_id, sport, league FROM team_cache WHERE {placeholders}",  # noqa: E501
                    params,
                )
                for row in cursor.fetchall():
                    cache_key = (row["provider"], row["provider_team_id"], row["sport"])
                    if cache_key not in team_cache_leagues:
                        team_cache_leagues[cache_key] = []
                    team_cache_leagues[cache_key].append(row["league"])

        for team in request.teams:
            is_soccer = team.sport.lower() == "soccer"
            full_key = (team.provider, team.provider_team_id, team.sport, team.league)
            sport_key = (team.provider, team.provider_team_id, team.sport)

            if is_soccer:
                # Soccer: consolidate all leagues into one team entry
                # Use pre-loaded cache instead of querying per team
                all_leagues = team_cache_leagues.get(sport_key, []).copy()
                if team.league not in all_leagues:
                    all_leagues.append(team.league)

                if sport_key in existing_sport:
                    # Found existing soccer team - update its leagues array
                    team_id, primary_league, current_leagues = existing_sport[sport_key][0]
                    new_to_add = [lg for lg in all_leagues if lg not in current_leagues]
                    if not new_to_add:
                        skipped += 1
                    else:
                        new_leagues = sorted(set(current_leagues + all_leagues))
                        conn.execute(
                            "UPDATE teams SET leagues = ? WHERE id = ?",
                            (json.dumps(new_leagues), team_id),
                        )
                        existing_sport[sport_key][0] = (team_id, primary_league, new_leagues)
                        updated += 1
                else:
                    # Create new soccer team
                    channel_id = generate_channel_id(team.team_name, team.league)
                    leagues_json = json.dumps(sorted(all_leagues))
                    cursor = conn.execute(
                        """
                        INSERT INTO teams (
                            provider, provider_team_id, primary_league, leagues, sport,
                            team_name, team_abbrev, team_logo_url, channel_id, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            team.provider,
                            team.provider_team_id,
                            team.league,
                            leagues_json,
                            team.sport,
                            team.team_name,
                            team.team_abbrev,
                            team.logo_url,
                            channel_id,
                        ),
                    )
                    new_id = cursor.lastrowid
                    existing_full[full_key] = (new_id, all_leagues)
                    existing_sport[sport_key] = [(new_id, team.league, all_leagues)]
                    imported += 1
            else:
                # Non-soccer: each league gets its own team entry
                # ESPN reuses IDs across leagues for different teams
                if full_key in existing_full:
                    # Exact match exists - skip
                    skipped += 1
                else:
                    # Create new team for this league
                    channel_id = generate_channel_id(team.team_name, team.league)
                    leagues_json = json.dumps([team.league])
                    cursor = conn.execute(
                        """
                        INSERT INTO teams (
                            provider, provider_team_id, primary_league, leagues, sport,
                            team_name, team_abbrev, team_logo_url, channel_id, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            team.provider,
                            team.provider_team_id,
                            team.league,
                            leagues_json,
                            team.sport,
                            team.team_name,
                            team.team_abbrev,
                            team.logo_url,
                            channel_id,
                        ),
                    )
                    new_id = cursor.lastrowid
                    existing_full[full_key] = (new_id, [team.league])
                    if sport_key not in existing_sport:
                        existing_sport[sport_key] = []
                    existing_sport[sport_key].append((new_id, team.league, [team.league]))
                    imported += 1

    logger.info(
        "[BULK_IMPORT] Teams: %d imported, %d updated, %d skipped", imported, updated, skipped
    )
    return BulkImportResponse(imported=imported, updated=updated, skipped=skipped)


class BulkChannelIdRequest(BaseModel):
    """Bulk channel ID update request."""

    team_ids: list[int]
    format_template: str


class BulkChannelIdResponse(BaseModel):
    """Bulk channel ID update response."""

    updated: int
    errors: list[str]


def to_pascal_case(name: str) -> str:
    """Convert a string to PascalCase."""
    return "".join(
        word.capitalize()
        for word in "".join(c if c.isalnum() or c.isspace() else "" for c in name).split()
    )


@router.post("/teams/bulk-channel-id", response_model=BulkChannelIdResponse)
def bulk_update_channel_ids(request: BulkChannelIdRequest):
    """Bulk update channel IDs based on a format template.

    Supported format variables:
    - {team_name_pascal}: Team name in PascalCase (e.g., "MichiganWolverines")
    - {team_abbrev}: Team abbreviation lowercase (e.g., "mich")
    - {team_name}: Team name lowercase with dashes (e.g., "michigan-wolverines")
    - {provider_team_id}: Provider's team ID
    - {league_id}: League code lowercase (e.g., "ncaam")
    - {league}: League display name (e.g., "NCAAM")
    - {sport}: Sport name lowercase (e.g., "basketball")
    """
    import re

    from teamarr.database.leagues import get_league_display, get_league_id

    if not request.team_ids:
        return BulkChannelIdResponse(updated=0, errors=["No teams selected"])

    if not request.format_template:
        return BulkChannelIdResponse(updated=0, errors=["No format template provided"])

    updated_count = 0
    errors: list[str] = []

    with get_db() as conn:
        for team_id in request.team_ids:
            try:
                cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
                row = cursor.fetchone()
                if not row:
                    errors.append(f"Team ID {team_id} not found")
                    continue

                team_data = dict(row)
                team_name = team_data.get("team_name", "")
                primary_league = team_data.get("primary_league", "")

                # Get league display name and ID
                league_display = get_league_display(conn, primary_league)
                league_id = get_league_id(conn, primary_league)

                # Generate channel ID from format template
                channel_id = request.format_template
                channel_id = channel_id.replace("{team_name_pascal}", to_pascal_case(team_name))
                channel_id = channel_id.replace(
                    "{team_abbrev}", (team_data.get("team_abbrev") or "").lower()
                )
                channel_id = channel_id.replace("{team_name}", team_name.lower().replace(" ", "-"))
                channel_id = channel_id.replace(
                    "{provider_team_id}", str(team_data.get("provider_team_id") or "")
                )
                channel_id = channel_id.replace("{league_id}", league_id)
                channel_id = channel_id.replace("{league}", league_display)
                channel_id = channel_id.replace("{sport}", (team_data.get("sport") or "").lower())

                # Clean up channel ID
                if (
                    "{team_name_pascal}" in request.format_template
                    or "{league}" in request.format_template
                ):
                    # Allow uppercase letters for PascalCase
                    channel_id = re.sub(r"[^a-zA-Z0-9.-]+", "", channel_id)
                else:
                    # Lowercase only
                    channel_id = re.sub(r"[^a-z0-9.-]+", "-", channel_id)
                    channel_id = re.sub(r"-+", "-", channel_id)
                    channel_id = channel_id.strip("-")

                if not channel_id:
                    errors.append(f"Generated empty channel ID for team '{team_name}'")
                    continue

                # Update the team's channel_id
                conn.execute(
                    "UPDATE teams SET channel_id = ? WHERE id = ?",
                    (channel_id, team_id),
                )
                updated_count += 1

            except Exception as e:
                errors.append(f"Error updating team ID {team_id}: {str(e)}")

    return BulkChannelIdResponse(updated=updated_count, errors=errors[:5])
