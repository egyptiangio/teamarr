"""API routes for Regular TV groups."""

import logging
import time
import inspect
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection, IntegrityError

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ...database import get_db
from ...database.regular_tv import (
    RegularTVGroup,
    create_group,
    delete_group,
    get_all_groups,
    get_group,
    get_group_by_name,
    update_group,
)

logger = logging.getLogger(__name__)
logger.info("DEBUG: Loading regular_tv routes module")

router = APIRouter(tags=["Regular TV"])

class RegularTVSettings(BaseModel):
    """Regular TV global settings."""
    enabled: bool = True
    lookback_hours: float
    lookahead_hours: float
    epg_source_id: int | None = None

class RegularTVGroupCreate(BaseModel):
    name: str
    m3u_group_name: str
    m3u_account_id: int
    m3u_group_id: int | None = None
    enabled: bool = True
    epg_source_id: int | None = None

class RegularTVGroupUpdate(BaseModel):
    name: str | None = None
    m3u_group_name: str | None = None
    m3u_account_id: int | None = None
    m3u_group_id: int | None = None
    enabled: bool | None = None
    epg_source_id: int | None = None

class RegularTVGroupResponse(BaseModel):
    id: int
    name: str
    m3u_group_name: str
    m3u_group_id: int | None
    m3u_account_id: int | None
    enabled: bool
    epg_source_id: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True

class BulkRegularTVGroupCreate(BaseModel):
    groups: list[RegularTVGroupCreate]

def get_db_conn():
    """Get database connection dependency."""
    with get_db() as conn:
        yield conn


@router.get("/groups", response_model=list[RegularTVGroupResponse])
def get_regular_tv_groups(db: Connection = Depends(get_db_conn)):
    """List all Regular TV groups."""
    groups = get_all_groups(db)
    return [RegularTVGroupResponse.model_validate(g) for g in groups]


@router.get("/settings", response_model=RegularTVSettings)
def get_regular_tv_settings(db: Connection = Depends(get_db_conn)):
    """Get Regular TV settings."""
    row = db.execute(
        "SELECT regular_tv_enabled, regular_tv_lookback_hours, regular_tv_lookahead_hours, regular_tv_epg_source_id FROM settings WHERE id = 1"
    ).fetchone()
    if not row:
        return RegularTVSettings(enabled=True, lookback_hours=0, lookahead_hours=24, epg_source_id=None)
    
    return RegularTVSettings(
        enabled=bool(row["regular_tv_enabled"]) if row["regular_tv_enabled"] is not None else True,
        lookback_hours=row["regular_tv_lookback_hours"] or 0.0,
        lookahead_hours=row["regular_tv_lookahead_hours"] or 24.0,
        epg_source_id=row["regular_tv_epg_source_id"],
    )


@router.put("/settings", response_model=RegularTVSettings)
def update_regular_tv_settings(
    settings: RegularTVSettings, db: Connection = Depends(get_db_conn)
):
    """Update Regular TV settings."""
    db.execute(
        """
        UPDATE settings 
        SET regular_tv_enabled = ?,
            regular_tv_lookback_hours = ?, 
            regular_tv_lookahead_hours = ?,
            regular_tv_epg_source_id = ?
        WHERE id = 1
        """,
        (settings.enabled, settings.lookback_hours, settings.lookahead_hours, settings.epg_source_id),
    )
    db.commit()
    return settings


@router.get("/playlist", response_class=PlainTextResponse)
def get_regular_tv_playlist():
    """Get the generated Regular TV M3U playlist."""
    file_path = Path("data/regular_tv.m3u")
    logger.debug(f"DEBUG: Looking for playlist at {file_path.absolute()}")
    if not file_path.is_file():
        logger.info("Playlist file not found at %s, returning empty", file_path.absolute())
        return "#EXTM3U\n"
    return file_path.read_text(encoding="utf-8")


@router.get("/playlist/excluded", response_class=PlainTextResponse)
def get_regular_tv_excluded_playlist():
    """Get the generated Regular TV excluded M3U playlist."""
    file_path = Path("data/regular_tv_excluded.m3u")
    if not file_path.is_file():
        return "#EXTM3U\n"
    return file_path.read_text(encoding="utf-8")


@router.get("/stats")
def get_regular_tv_stats():
    """Get stats about the last generated Regular TV playlist."""
    included_path = Path("data/regular_tv.m3u")
    excluded_path = Path("data/regular_tv_excluded.m3u")

    def count_entries(path: Path) -> int:
        if not path.is_file(): return 0
        try: return path.read_text(encoding="utf-8").count("#EXTINF:")
        except Exception: return 0

    return {"included": count_entries(included_path), "excluded": count_entries(excluded_path)}


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_regular_tv_playlist():
    """Generate the Regular TV M3U playlist."""
    logger.info(f"--- DEBUG: generate_regular_tv_playlist route handler executed at {time.time()} ---")
    logger.info("Received request to generate Regular TV playlist")
    try:
        from ...services.regular_tv import RegularTVService
    except ImportError as e:
        logger.error(f"Failed to import RegularTVService: {e}")
        raise HTTPException(status_code=500, detail=f"Service import failed: {e}")

    service = RegularTVService()
    try:
        logger.debug("Calling RegularTVService.generate_playlist()")
        result = service.generate_playlist()
        if inspect.isawaitable(result):
            stats = await result
        else:
            stats = result
        logger.info("Regular TV playlist generation completed. Stats: %s", stats)
        return stats
    except Exception as e:
        logger.error("Error generating Regular TV playlist: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate playlist: {e}",
        )


@router.post("/groups", response_model=RegularTVGroupResponse, status_code=status.HTTP_201_CREATED)
def create_regular_tv_group(
    group_data: RegularTVGroupCreate, db: Connection = Depends(get_db_conn)
):
    """Create a new Regular TV group."""
    existing = get_group_by_name(
        db, group_data.m3u_group_name, group_data.m3u_account_id
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{group_data.m3u_group_name}' for this M3U account already exists.",
        )

    try:
        group_id = create_group(
            db,
            name=group_data.name,
            m3u_group_name=group_data.m3u_group_name,
            m3u_account_id=group_data.m3u_account_id,
            m3u_group_id=group_data.m3u_group_id,
            epg_source_id=group_data.epg_source_id,
            enabled=group_data.enabled,
        )
        new_group = get_group(db, group_id)
        if not new_group:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created group.",
            )
        return RegularTVGroupResponse.model_validate(new_group)
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Database integrity error: {e}",
        ) from e
    except Exception as e:
        logger.error("Error creating Regular TV group: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {e}",
        ) from e


@router.post("/groups/bulk", status_code=status.HTTP_201_CREATED)
def bulk_create_regular_tv_groups(
    payload: BulkRegularTVGroupCreate, db: Connection = Depends(get_db_conn)
):
    """Bulk import Regular TV groups."""
    created_count = 0
    skipped_count = 0
    errors = []

    for group_data in payload.groups:
        try:
            existing_group = get_group_by_name(
                db, group_data.m3u_group_name, group_data.m3u_account_id
            )

            if existing_group:
                skipped_count += 1
                errors.append(
                    f"Group '{group_data.m3u_group_name}' for M3U account "
                    f"'{group_data.m3u_account_id}' already exists. Skipped."
                )
                continue

            create_group(
                db,
                name=group_data.name,
                m3u_group_name=group_data.m3u_group_name,
                m3u_account_id=group_data.m3u_account_id,
                m3u_group_id=group_data.m3u_group_id,
                epg_source_id=group_data.epg_source_id,
                enabled=group_data.enabled,
            )
            created_count += 1
        except IntegrityError as e:
            skipped_count += 1
            errors.append(
                f"Database integrity error for group '{group_data.m3u_group_name}': {e}"
            )
        except Exception as e:
            skipped_count += 1
            errors.append(
                f"Error creating group '{group_data.m3u_group_name}': {e}"
            )

    db.commit()

    if created_count == 0 and skipped_count == 0:
        return {"message": "No groups were processed."}

    message = f"Successfully imported {created_count} groups."
    if skipped_count > 0:
        message += f" {skipped_count} groups skipped."
    return {"message": message, "errors": errors if errors else None}


@router.get("/groups/{group_id}", response_model=RegularTVGroupResponse)
def get_regular_tv_group(group_id: int, db: Connection = Depends(get_db_conn)):
    """Get a single Regular TV group by ID."""
    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return RegularTVGroupResponse.model_validate(group)


@router.patch("/groups/{group_id}", response_model=RegularTVGroupResponse)
def update_regular_tv_group(
    group_id: int, group_update: RegularTVGroupUpdate, db: Connection = Depends(get_db_conn)
):
    """Update a Regular TV group."""
    existing_group = get_group(db, group_id)
    if not existing_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    update_data = group_update.model_dump(exclude_unset=True)

    # Check for duplicate name if changing
    if "name" in update_data and update_data["name"] != existing_group.name:
        # Regular TV groups don't have m3u_account_id in their unique constraint,
        # only name is unique. So we check globally.
        existing_by_name = get_group_by_name(db, update_data["name"])
        if existing_by_name and existing_by_name.id != group_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group with name '{update_data['name']}' already exists.",
            )

    if not update_data:
        # If the request body is empty, just return the existing group
        return RegularTVGroupResponse.model_validate(existing_group)

    updated = update_group(
        db,
        group_id,
        **update_data,
    )

    if not updated:
        # This should now only happen if the group was deleted between the existence check and the update
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update group.",
        )

    updated_group = get_group(db, group_id)
    if not updated_group:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve updated group.",
        )
    return RegularTVGroupResponse.model_validate(updated_group)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_regular_tv_group(group_id: int, db: Connection = Depends(get_db_conn)):
    """Delete a Regular TV group."""
    existing_group = get_group(db, group_id)
    if not existing_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    deleted = delete_group(db, group_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete group.",
        )
    return None