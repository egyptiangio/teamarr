"""Event EPG groups management endpoints.

Provides REST API for:
- CRUD operations on event EPG groups
- Group statistics and channel counts
- M3U group discovery from Dispatcharr
"""

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from teamarr.database import get_db

router = APIRouter()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class GroupCreate(BaseModel):
    """Create event EPG group request."""

    name: str = Field(..., min_length=1, max_length=100)
    leagues: list[str] = Field(..., min_length=1)
    template_id: int | None = None
    channel_start_number: int | None = Field(None, ge=1)
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] | None = None
    create_timing: str = "same_day"
    delete_timing: str = "same_day"
    duplicate_event_handling: str = "consolidate"
    channel_assignment_mode: str = "auto"
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    active: bool = True


class GroupUpdate(BaseModel):
    """Update event EPG group request."""

    name: str | None = Field(None, min_length=1, max_length=100)
    leagues: list[str] | None = None
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] | None = None
    create_timing: str | None = None
    delete_timing: str | None = None
    duplicate_event_handling: str | None = None
    channel_assignment_mode: str | None = None
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    active: bool | None = None

    # Clear flags for nullable fields
    clear_template: bool = False
    clear_channel_start_number: bool = False
    clear_channel_group_id: bool = False
    clear_stream_profile_id: bool = False
    clear_channel_profile_ids: bool = False
    clear_m3u_group_id: bool = False
    clear_m3u_group_name: bool = False


class GroupResponse(BaseModel):
    """Event EPG group response."""

    id: int
    name: str
    leagues: list[str]
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] = []
    create_timing: str = "same_day"
    delete_timing: str = "same_day"
    duplicate_event_handling: str = "consolidate"
    channel_assignment_mode: str = "auto"
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    active: bool = True
    created_at: str | None = None
    updated_at: str | None = None
    channel_count: int | None = None


class GroupListResponse(BaseModel):
    """List of event EPG groups."""

    groups: list[GroupResponse]
    total: int


class GroupStatsResponse(BaseModel):
    """Group statistics."""

    group_id: int
    total: int = 0
    active: int = 0
    deleted: int = 0
    by_status: dict = {}


class M3UGroupResponse(BaseModel):
    """M3U group from Dispatcharr."""

    id: int
    name: str
    stream_count: int | None = None


class M3UGroupListResponse(BaseModel):
    """List of M3U groups."""

    groups: list[M3UGroupResponse]
    total: int


# =============================================================================
# VALIDATION
# =============================================================================

VALID_CREATE_TIMING = {
    "stream_available",
    "same_day",
    "day_before",
    "2_days_before",
    "3_days_before",
    "1_week_before",
    "manual",
}

VALID_DELETE_TIMING = {
    "stream_removed",
    "same_day",
    "day_after",
    "2_days_after",
    "3_days_after",
    "1_week_after",
    "manual",
}

VALID_DUPLICATE_HANDLING = {"consolidate", "separate", "ignore"}
VALID_ASSIGNMENT_MODE = {"auto", "manual"}


def validate_group_fields(
    create_timing: str | None = None,
    delete_timing: str | None = None,
    duplicate_event_handling: str | None = None,
    channel_assignment_mode: str | None = None,
):
    """Validate group field values."""
    if create_timing and create_timing not in VALID_CREATE_TIMING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid create_timing. Valid: {VALID_CREATE_TIMING}",
        )
    if delete_timing and delete_timing not in VALID_DELETE_TIMING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid delete_timing. Valid: {VALID_DELETE_TIMING}",
        )
    if duplicate_event_handling and duplicate_event_handling not in VALID_DUPLICATE_HANDLING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid duplicate_event_handling. Valid: {VALID_DUPLICATE_HANDLING}",
        )
    if channel_assignment_mode and channel_assignment_mode not in VALID_ASSIGNMENT_MODE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid channel_assignment_mode. Valid: {VALID_ASSIGNMENT_MODE}",
        )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("", response_model=GroupListResponse)
def list_groups(
    include_inactive: bool = Query(False, description="Include inactive groups"),
    include_stats: bool = Query(False, description="Include channel counts"),
):
    """List all event EPG groups."""
    from teamarr.database.groups import get_all_group_stats, get_all_groups

    with get_db() as conn:
        groups = get_all_groups(conn, include_inactive=include_inactive)

        stats = {}
        if include_stats:
            stats = get_all_group_stats(conn)

    return GroupListResponse(
        groups=[
            GroupResponse(
                id=g.id,
                name=g.name,
                leagues=g.leagues,
                template_id=g.template_id,
                channel_start_number=g.channel_start_number,
                channel_group_id=g.channel_group_id,
                stream_profile_id=g.stream_profile_id,
                channel_profile_ids=g.channel_profile_ids,
                create_timing=g.create_timing,
                delete_timing=g.delete_timing,
                duplicate_event_handling=g.duplicate_event_handling,
                channel_assignment_mode=g.channel_assignment_mode,
                m3u_group_id=g.m3u_group_id,
                m3u_group_name=g.m3u_group_name,
                active=g.active,
                created_at=g.created_at.isoformat() if g.created_at else None,
                updated_at=g.updated_at.isoformat() if g.updated_at else None,
                channel_count=stats.get(g.id, {}).get("active"),
            )
            for g in groups
        ],
        total=len(groups),
    )


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(request: GroupCreate):
    """Create a new event EPG group."""
    from teamarr.database.groups import create_group, get_group, get_group_by_name

    validate_group_fields(
        create_timing=request.create_timing,
        delete_timing=request.delete_timing,
        duplicate_event_handling=request.duplicate_event_handling,
        channel_assignment_mode=request.channel_assignment_mode,
    )

    with get_db() as conn:
        # Check for duplicate name
        existing = get_group_by_name(conn, request.name)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group with name '{request.name}' already exists",
            )

        group_id = create_group(
            conn,
            name=request.name,
            leagues=request.leagues,
            template_id=request.template_id,
            channel_start_number=request.channel_start_number,
            channel_group_id=request.channel_group_id,
            stream_profile_id=request.stream_profile_id,
            channel_profile_ids=request.channel_profile_ids,
            create_timing=request.create_timing,
            delete_timing=request.delete_timing,
            duplicate_event_handling=request.duplicate_event_handling,
            channel_assignment_mode=request.channel_assignment_mode,
            m3u_group_id=request.m3u_group_id,
            m3u_group_name=request.m3u_group_name,
            active=request.active,
        )

        group = get_group(conn, group_id)

    return GroupResponse(
        id=group.id,
        name=group.name,
        leagues=group.leagues,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        stream_profile_id=group.stream_profile_id,
        channel_profile_ids=group.channel_profile_ids,
        create_timing=group.create_timing,
        delete_timing=group.delete_timing,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        active=group.active,
        created_at=group.created_at.isoformat() if group.created_at else None,
        updated_at=group.updated_at.isoformat() if group.updated_at else None,
    )


@router.get("/{group_id}", response_model=GroupResponse)
def get_group_by_id(group_id: int):
    """Get a single event EPG group."""
    from teamarr.database.groups import get_group, get_group_channel_count

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        channel_count = get_group_channel_count(conn, group_id)

    return GroupResponse(
        id=group.id,
        name=group.name,
        leagues=group.leagues,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        stream_profile_id=group.stream_profile_id,
        channel_profile_ids=group.channel_profile_ids,
        create_timing=group.create_timing,
        delete_timing=group.delete_timing,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        active=group.active,
        created_at=group.created_at.isoformat() if group.created_at else None,
        updated_at=group.updated_at.isoformat() if group.updated_at else None,
        channel_count=channel_count,
    )


@router.put("/{group_id}", response_model=GroupResponse)
def update_group_by_id(group_id: int, request: GroupUpdate):
    """Update an event EPG group."""
    from teamarr.database.groups import (
        get_group,
        get_group_by_name,
        get_group_channel_count,
        update_group,
    )

    validate_group_fields(
        create_timing=request.create_timing,
        delete_timing=request.delete_timing,
        duplicate_event_handling=request.duplicate_event_handling,
        channel_assignment_mode=request.channel_assignment_mode,
    )

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        # Check for duplicate name if changing
        if request.name and request.name != group.name:
            existing = get_group_by_name(conn, request.name)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Group with name '{request.name}' already exists",
                )

        update_group(
            conn,
            group_id,
            name=request.name,
            leagues=request.leagues,
            template_id=request.template_id,
            channel_start_number=request.channel_start_number,
            channel_group_id=request.channel_group_id,
            stream_profile_id=request.stream_profile_id,
            channel_profile_ids=request.channel_profile_ids,
            create_timing=request.create_timing,
            delete_timing=request.delete_timing,
            duplicate_event_handling=request.duplicate_event_handling,
            channel_assignment_mode=request.channel_assignment_mode,
            m3u_group_id=request.m3u_group_id,
            m3u_group_name=request.m3u_group_name,
            active=request.active,
            clear_template=request.clear_template,
            clear_channel_start_number=request.clear_channel_start_number,
            clear_channel_group_id=request.clear_channel_group_id,
            clear_stream_profile_id=request.clear_stream_profile_id,
            clear_channel_profile_ids=request.clear_channel_profile_ids,
            clear_m3u_group_id=request.clear_m3u_group_id,
            clear_m3u_group_name=request.clear_m3u_group_name,
        )

        group = get_group(conn, group_id)
        channel_count = get_group_channel_count(conn, group_id)

    return GroupResponse(
        id=group.id,
        name=group.name,
        leagues=group.leagues,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        stream_profile_id=group.stream_profile_id,
        channel_profile_ids=group.channel_profile_ids,
        create_timing=group.create_timing,
        delete_timing=group.delete_timing,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        active=group.active,
        created_at=group.created_at.isoformat() if group.created_at else None,
        updated_at=group.updated_at.isoformat() if group.updated_at else None,
        channel_count=channel_count,
    )


@router.delete("/{group_id}")
def delete_group_by_id(group_id: int) -> dict:
    """Delete an event EPG group.

    Warning: This will cascade delete all managed channels for this group.
    """
    from teamarr.database.groups import delete_group, get_group, get_group_channel_count

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        channel_count = get_group_channel_count(conn, group_id)
        delete_group(conn, group_id)

    return {
        "success": True,
        "message": f"Deleted group '{group.name}'",
        "channels_deleted": channel_count,
    }


@router.get("/{group_id}/stats", response_model=GroupStatsResponse)
def get_group_stats(group_id: int):
    """Get statistics for an event EPG group."""
    from teamarr.database.groups import get_group, get_group_stats

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        stats = get_group_stats(conn, group_id)

    return GroupStatsResponse(
        group_id=group_id,
        **stats,
    )


@router.post("/{group_id}/activate")
def activate_group(group_id: int) -> dict:
    """Activate an event EPG group."""
    from teamarr.database.groups import get_group, set_group_active

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        set_group_active(conn, group_id, True)

    return {"success": True, "message": f"Group '{group.name}' activated"}


@router.post("/{group_id}/deactivate")
def deactivate_group(group_id: int) -> dict:
    """Deactivate an event EPG group."""
    from teamarr.database.groups import get_group, set_group_active

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        set_group_active(conn, group_id, False)

    return {"success": True, "message": f"Group '{group.name}' deactivated"}


# =============================================================================
# M3U GROUP DISCOVERY
# =============================================================================


@router.get("/m3u/groups", response_model=M3UGroupListResponse)
def list_m3u_groups():
    """List available M3U groups from Dispatcharr.

    Returns groups that can be used as stream sources for event EPG groups.
    """
    from teamarr.dispatcharr import get_dispatcharr_connection

    conn = get_dispatcharr_connection(get_db)
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured or not connected",
        )

    try:
        groups = conn.m3u.list_groups()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch M3U groups: {e}",
        ) from e

    return M3UGroupListResponse(
        groups=[
            M3UGroupResponse(
                id=g.id,
                name=g.name,
                stream_count=getattr(g, "stream_count", None),
            )
            for g in groups
        ],
        total=len(groups),
    )


@router.get("/dispatcharr/channel-groups")
def list_dispatcharr_channel_groups() -> dict:
    """List available channel groups from Dispatcharr.

    Returns channel groups that can be assigned to event EPG groups.
    """
    from teamarr.dispatcharr import get_dispatcharr_connection

    conn = get_dispatcharr_connection(get_db)
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured or not connected",
        )

    try:
        groups = conn.m3u.list_groups()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch channel groups: {e}",
        ) from e

    return {
        "groups": [{"id": g.id, "name": g.name} for g in groups],
        "total": len(groups),
    }


# =============================================================================
# GROUP PROCESSING
# =============================================================================


class ProcessGroupResponse(BaseModel):
    """Response from processing a group."""

    group_id: int
    group_name: str
    streams_fetched: int
    streams_matched: int
    streams_unmatched: int
    channels_created: int
    channels_existing: int
    channels_skipped: int
    channel_errors: int
    errors: list[str]
    duration_seconds: float


class ProcessAllResponse(BaseModel):
    """Response from processing all groups."""

    groups_processed: int
    total_channels_created: int
    total_errors: int
    duration_seconds: float
    results: list[ProcessGroupResponse]


@router.post("/{group_id}/process", response_model=ProcessGroupResponse)
def process_group(group_id: int):
    """Process an event EPG group.

    Fetches streams from Dispatcharr, matches them to events,
    and creates/updates channels.
    """
    from datetime import date

    from teamarr.consumers import process_event_group
    from teamarr.database.groups import get_group
    from teamarr.dispatcharr import get_factory

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

    # Get Dispatcharr client
    factory = get_factory(get_db)
    client = factory.get_client() if factory else None

    # Process the group
    result = process_event_group(
        db_factory=get_db,
        group_id=group_id,
        dispatcharr_client=client,
        target_date=date.today(),
    )

    duration = 0.0
    if result.started_at and result.completed_at:
        duration = (result.completed_at - result.started_at).total_seconds()

    return ProcessGroupResponse(
        group_id=result.group_id,
        group_name=result.group_name,
        streams_fetched=result.streams_fetched,
        streams_matched=result.streams_matched,
        streams_unmatched=result.streams_unmatched,
        channels_created=result.channels_created,
        channels_existing=result.channels_existing,
        channels_skipped=result.channels_skipped,
        channel_errors=result.channel_errors,
        errors=result.errors,
        duration_seconds=duration,
    )


@router.post("/process-all", response_model=ProcessAllResponse)
def process_all_groups():
    """Process all active event EPG groups.

    Fetches streams from Dispatcharr, matches them to events,
    and creates/updates channels for all active groups.
    """
    from datetime import date

    from teamarr.consumers import process_all_event_groups
    from teamarr.dispatcharr import get_factory

    # Get Dispatcharr client
    factory = get_factory(get_db)
    client = factory.get_client() if factory else None

    # Process all groups
    batch_result = process_all_event_groups(
        db_factory=get_db,
        dispatcharr_client=client,
        target_date=date.today(),
    )

    duration = 0.0
    if batch_result.started_at and batch_result.completed_at:
        duration = (batch_result.completed_at - batch_result.started_at).total_seconds()

    return ProcessAllResponse(
        groups_processed=batch_result.groups_processed,
        total_channels_created=batch_result.total_channels_created,
        total_errors=batch_result.total_errors,
        duration_seconds=duration,
        results=[
            ProcessGroupResponse(
                group_id=r.group_id,
                group_name=r.group_name,
                streams_fetched=r.streams_fetched,
                streams_matched=r.streams_matched,
                streams_unmatched=r.streams_unmatched,
                channels_created=r.channels_created,
                channels_existing=r.channels_existing,
                channels_skipped=r.channels_skipped,
                channel_errors=r.channel_errors,
                errors=r.errors,
                duration_seconds=(
                    (r.completed_at - r.started_at).total_seconds()
                    if r.started_at and r.completed_at
                    else 0.0
                ),
            )
            for r in batch_result.results
        ],
    )


# =============================================================================
# GROUP XMLTV ENDPOINTS
# =============================================================================


@router.get("/{group_id}/xmltv")
def get_group_xmltv(group_id: int) -> Response:
    """Get the stored XMLTV for an event group.

    This endpoint serves the XMLTV content that was generated when
    the group was last processed. Dispatcharr can be configured to
    fetch from this URL.

    Returns 404 if the group hasn't been processed yet.
    """
    from teamarr.database.groups import get_group

    with get_db() as conn:
        # Verify group exists
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        # Get stored XMLTV
        row = conn.execute(
            "SELECT xmltv_content, updated_at FROM event_epg_xmltv WHERE group_id = ?",
            (group_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No XMLTV generated for group '{group.name}'. Process the group first.",
            )

    return Response(
        content=row["xmltv_content"],
        media_type="application/xml",
        headers={
            "Content-Disposition": f"inline; filename=teamarr-group-{group_id}.xml",
            "X-Generated-At": row["updated_at"] if row["updated_at"] else "",
        },
    )


@router.get("/xmltv/combined")
def get_combined_xmltv() -> Response:
    """Get combined XMLTV from all active event groups.

    Merges XMLTV content from all groups that have been processed.
    This is useful for having a single EPG source in Dispatcharr.
    """
    from teamarr.database.groups import get_all_groups

    with get_db() as conn:
        # Get all active groups
        groups = get_all_groups(conn, include_inactive=False)
        group_ids = [g.id for g in groups]

        if not group_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active event groups found",
            )

        # Get all XMLTV content
        placeholders = ",".join("?" * len(group_ids))
        rows = conn.execute(
            f"SELECT xmltv_content FROM event_epg_xmltv WHERE group_id IN ({placeholders})",
            group_ids,
        ).fetchall()

        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No XMLTV generated for any groups. Process groups first.",
            )

    # Merge XMLTV files
    from teamarr.utilities.xmltv import merge_xmltv_content

    combined = merge_xmltv_content([row["xmltv_content"] for row in rows])

    return Response(
        content=combined,
        media_type="application/xml",
        headers={"Content-Disposition": "inline; filename=teamarr-events.xml"},
    )
