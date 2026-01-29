"""API routes for team aliases."""

from sqlite3 import Connection, IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from teamarr.database import get_db
from teamarr.database.aliases import (
    TeamAlias,
    bulk_create_aliases,
    create_alias,
    delete_alias,
    export_aliases,
    get_alias,
    list_aliases,
    update_alias,
)

router = APIRouter(prefix="/aliases", tags=["Aliases"])


# =============================================================================
# Pydantic Models
# =============================================================================


class AliasCreate(BaseModel):
    """Create a new team alias."""

    alias: str = Field(..., description="Alias text (e.g., 'spurs', 'man u')")
    league: str = Field(..., description="League code (e.g., 'eng.1', 'nfl')")
    team_id: str = Field(..., description="Provider's team ID")
    team_name: str = Field(..., description="Provider's team name")
    provider: str = Field("espn", description="Provider name")


class AliasUpdate(BaseModel):
    """Update an existing alias."""

    alias: str | None = None
    league: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    provider: str | None = None


class AliasResponse(BaseModel):
    """Response model for a single alias."""

    id: int
    alias: str
    league: str
    provider: str
    team_id: str
    team_name: str
    created_at: str | None = None

    @classmethod
    def from_db(cls, alias: TeamAlias) -> "AliasResponse":
        return cls(
            id=alias.id,
            alias=alias.alias,
            league=alias.league,
            provider=alias.provider,
            team_id=alias.team_id,
            team_name=alias.team_name,
            created_at=str(alias.created_at) if alias.created_at else None,
        )


class AliasListResponse(BaseModel):
    """Response model for list of aliases."""

    aliases: list[AliasResponse]
    total: int


class BulkImportRequest(BaseModel):
    """Request model for bulk alias import."""

    aliases: list[AliasCreate]


class BulkImportResponse(BaseModel):
    """Response model for bulk import result."""

    created: int
    skipped: int
    total: int


# =============================================================================
# Dependency
# =============================================================================


def get_connection():
    """Get database connection."""
    with get_db() as conn:
        yield conn


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=AliasListResponse)
def list_all_aliases(
    league: str | None = Query(None, description="Filter by league"),
    provider: str | None = Query(None, description="Filter by provider"),
    conn: Connection = Depends(get_connection),
):
    """List all team aliases, optionally filtered by league or provider."""
    aliases = list_aliases(conn, league=league, provider=provider)
    return AliasListResponse(
        aliases=[AliasResponse.from_db(a) for a in aliases],
        total=len(aliases),
    )


@router.get("/export")
def export_all_aliases(conn: Connection = Depends(get_connection)):
    """Export all aliases as JSON for backup/transfer."""
    return export_aliases(conn)


@router.post("/import", response_model=BulkImportResponse)
def import_aliases(
    request: BulkImportRequest,
    conn: Connection = Depends(get_connection),
):
    """Bulk import aliases, skipping duplicates."""
    aliases_data = [
        {
            "alias": a.alias,
            "league": a.league,
            "team_id": a.team_id,
            "team_name": a.team_name,
            "provider": a.provider,
        }
        for a in request.aliases
    ]
    created, skipped = bulk_create_aliases(conn, aliases_data)
    return BulkImportResponse(
        created=created,
        skipped=skipped,
        total=len(request.aliases),
    )


@router.get("/{alias_id}", response_model=AliasResponse)
def get_alias_by_id(
    alias_id: int,
    conn: Connection = Depends(get_connection),
):
    """Get a single alias by ID."""
    alias = get_alias(conn, alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    return AliasResponse.from_db(alias)


@router.post("", response_model=AliasResponse, status_code=201)
def create_new_alias(
    request: AliasCreate,
    conn: Connection = Depends(get_connection),
):
    """Create a new team alias."""
    try:
        alias = create_alias(
            conn,
            alias=request.alias,
            league=request.league,
            team_id=request.team_id,
            team_name=request.team_name,
            provider=request.provider,
        )
        return AliasResponse.from_db(alias)
    except IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Alias '{request.alias}' already exists for league '{request.league}'",
        ) from e


@router.patch("/{alias_id}", response_model=AliasResponse)
def update_existing_alias(
    alias_id: int,
    request: AliasUpdate,
    conn: Connection = Depends(get_connection),
):
    """Update an existing alias."""
    alias = update_alias(
        conn,
        alias_id=alias_id,
        alias=request.alias,
        league=request.league,
        team_id=request.team_id,
        team_name=request.team_name,
        provider=request.provider,
    )
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    return AliasResponse.from_db(alias)


@router.delete("/{alias_id}", status_code=204)
def delete_alias_by_id(
    alias_id: int,
    conn: Connection = Depends(get_connection),
):
    """Delete an alias."""
    if not delete_alias(conn, alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
