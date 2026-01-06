"""Teams API endpoints."""

import json

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from teamarr.api.models import TeamCreate, TeamResponse, TeamUpdate
from teamarr.database import get_db

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


def _get_all_leagues_from_cache(conn, provider: str, provider_team_id: str, sport: str) -> list[str]:
    """Get all leagues a team appears in from the cache for a given sport."""
    cursor = conn.execute(
        "SELECT DISTINCT league FROM team_cache WHERE provider = ? AND provider_team_id = ? AND sport = ?",
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


@router.post("/teams/bulk-import", response_model=BulkImportResponse)
def bulk_import_teams(request: BulkImportRequest):
    """Bulk import teams from cache.

    Consolidates by (provider, provider_team_id, sport) when leagues are compatible:
    - Soccer: teams play in multiple competitions, so all leagues consolidate
    - American Football: NFL and college-football have overlapping IDs for different
      teams, so they are NOT consolidated
    - New teams are created with their league in the leagues array
    - Existing teams have new leagues added if compatible
    - Skips when league already exists for the team
    """
    imported = 0
    updated = 0
    skipped = 0

    with get_db() as conn:
        # Get existing teams indexed by (provider, provider_team_id, sport)
        # Store list of (team_id, leagues) since multiple teams may share the key
        # when leagues are incompatible (e.g., NFL vs college-football)
        cursor = conn.execute("SELECT id, provider, provider_team_id, sport, leagues FROM teams")
        existing: dict[tuple[str, str, str], list[tuple[int, list[str]]]] = {}
        for row in cursor.fetchall():
            key = (row["provider"], row["provider_team_id"], row["sport"])
            leagues = _parse_leagues(row["leagues"])
            if key not in existing:
                existing[key] = []
            existing[key].append((row["id"], leagues))

        for team in request.teams:
            key = (team.provider, team.provider_team_id, team.sport)

            # For soccer, auto-discover all leagues from cache
            is_soccer = team.sport.lower() == "soccer"
            if is_soccer:
                all_leagues = _get_all_leagues_from_cache(
                    conn, team.provider, team.provider_team_id, team.sport
                )
                # Ensure the requested league is included
                if team.league not in all_leagues:
                    all_leagues.append(team.league)
            else:
                all_leagues = [team.league]

            if key in existing:
                # Check if any existing team with this key can accept this league
                found_compatible = False
                for i, (team_id, current_leagues) in enumerate(existing[key]):
                    # Check if new league is compatible with existing leagues
                    if all(_can_consolidate_leagues(conn, team.league, lg) for lg in current_leagues):
                        # Check which leagues are actually new
                        new_to_add = [lg for lg in all_leagues if lg not in current_leagues]
                        if not new_to_add:
                            skipped += 1
                        else:
                            # Add all new leagues to the existing team
                            new_leagues = sorted(set(current_leagues + all_leagues))
                            conn.execute(
                                "UPDATE teams SET leagues = ? WHERE id = ?",
                                (json.dumps(new_leagues), team_id),
                            )
                            existing[key][i] = (team_id, new_leagues)
                            updated += 1
                        found_compatible = True
                        break

                if not found_compatible:
                    # No compatible team found - create new team
                    channel_id = generate_channel_id(team.team_name, team.league)
                    leagues_json = json.dumps(sorted(all_leagues))

                    cursor = conn.execute(
                        """
                        INSERT INTO teams (
                            provider, provider_team_id, primary_league, leagues, sport,
                            team_name, team_abbrev, team_logo_url,
                            channel_id, active
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
                    new_team_id = cursor.lastrowid
                    existing[key].append((new_team_id, all_leagues))
                    imported += 1
            else:
                # New team - create with this league as primary
                channel_id = generate_channel_id(team.team_name, team.league)
                leagues_json = json.dumps(sorted(all_leagues))

                cursor = conn.execute(
                    """
                    INSERT INTO teams (
                        provider, provider_team_id, primary_league, leagues, sport,
                        team_name, team_abbrev, team_logo_url,
                        channel_id, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        team.provider,
                        team.provider_team_id,
                        team.league,  # First league becomes primary
                        leagues_json,
                        team.sport,
                        team.team_name,
                        team.team_abbrev,
                        team.logo_url,
                        channel_id,
                    ),
                )
                team_id = cursor.lastrowid
                existing[key] = [(team_id, all_leagues)]
                imported += 1

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
