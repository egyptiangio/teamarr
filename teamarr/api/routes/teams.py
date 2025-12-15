"""Teams API endpoints."""

from fastapi import APIRouter, HTTPException, status

from teamarr.api.models import TeamCreate, TeamResponse, TeamUpdate
from teamarr.database import get_db

router = APIRouter()


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(active_only: bool = False):
    """List all teams."""
    with get_db() as conn:
        if active_only:
            cursor = conn.execute("SELECT * FROM teams WHERE active = 1 ORDER BY team_name")
        else:
            cursor = conn.execute("SELECT * FROM teams ORDER BY team_name")
        return [dict(row) for row in cursor.fetchall()]


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(team: TeamCreate):
    """Create a new team."""
    with get_db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO teams (
                    provider, provider_team_id, league, sport,
                    team_name, team_abbrev, team_logo_url, team_color,
                    channel_id, channel_logo_url, template_id, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team.provider,
                    team.provider_team_id,
                    team.league,
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
            return dict(cursor.fetchone())
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Team with this channel_id or provider/team_id/league already exists",
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
        return dict(row)


@router.put("/teams/{team_id}", response_model=TeamResponse)
@router.patch("/teams/{team_id}", response_model=TeamResponse)
def update_team(team_id: int, team: TeamUpdate):
    """Update a team (full or partial)."""
    updates = {k: v for k, v in team.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [team_id]

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE teams SET {set_clause} WHERE id = ?", values)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
        return dict(cursor.fetchone())


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: int):
    """Delete a team."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
