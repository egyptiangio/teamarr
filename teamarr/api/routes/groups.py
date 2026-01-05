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
    display_name: str | None = Field(None, max_length=100)  # Optional display name override
    leagues: list[str] = Field(..., min_length=1)
    parent_group_id: int | None = None
    template_id: int | None = None
    channel_start_number: int | None = Field(None, ge=1)
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] | None = None
    duplicate_event_handling: str = "consolidate"
    channel_assignment_mode: str = "auto"
    sort_order: int = 0
    total_stream_count: int = 0
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None
    # Stream filtering
    stream_include_regex: str | None = None
    stream_include_regex_enabled: bool = False
    stream_exclude_regex: str | None = None
    stream_exclude_regex_enabled: bool = False
    custom_regex_teams: str | None = None
    custom_regex_teams_enabled: bool = False
    custom_regex_date: str | None = None
    custom_regex_date_enabled: bool = False
    custom_regex_time: str | None = None
    custom_regex_time_enabled: bool = False
    skip_builtin_filter: bool = False
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str = "time"
    overlap_handling: str = "add_stream"
    enabled: bool = True


class GroupUpdate(BaseModel):
    """Update event EPG group request."""

    name: str | None = Field(None, min_length=1, max_length=100)
    display_name: str | None = Field(None, max_length=100)  # Optional display name override
    leagues: list[str] | None = None
    parent_group_id: int | None = None
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] | None = None
    duplicate_event_handling: str | None = None
    channel_assignment_mode: str | None = None
    sort_order: int | None = None
    total_stream_count: int | None = None
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None
    # Stream filtering
    stream_include_regex: str | None = None
    stream_include_regex_enabled: bool | None = None
    stream_exclude_regex: str | None = None
    stream_exclude_regex_enabled: bool | None = None
    custom_regex_teams: str | None = None
    custom_regex_teams_enabled: bool | None = None
    custom_regex_date: str | None = None
    custom_regex_date_enabled: bool | None = None
    custom_regex_time: str | None = None
    custom_regex_time_enabled: bool | None = None
    skip_builtin_filter: bool | None = None
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str | None = None
    overlap_handling: str | None = None
    enabled: bool | None = None

    # Clear flags for nullable fields
    clear_display_name: bool = False
    clear_parent_group_id: bool = False
    clear_template: bool = False
    clear_channel_start_number: bool = False
    clear_channel_group_id: bool = False
    clear_stream_profile_id: bool = False
    clear_channel_profile_ids: bool = False
    clear_m3u_group_id: bool = False
    clear_m3u_group_name: bool = False
    clear_m3u_account_id: bool = False
    clear_m3u_account_name: bool = False
    clear_stream_include_regex: bool = False
    clear_stream_exclude_regex: bool = False
    clear_custom_regex_teams: bool = False
    clear_custom_regex_date: bool = False
    clear_custom_regex_time: bool = False


class GroupResponse(BaseModel):
    """Event EPG group response."""

    id: int
    name: str
    display_name: str | None = None  # Optional display name override for UI
    leagues: list[str]
    parent_group_id: int | None = None
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] = []
    duplicate_event_handling: str = "consolidate"
    channel_assignment_mode: str = "auto"
    sort_order: int = 0
    total_stream_count: int = 0
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None
    # Stream filtering
    stream_include_regex: str | None = None
    stream_include_regex_enabled: bool = False
    stream_exclude_regex: str | None = None
    stream_exclude_regex_enabled: bool = False
    custom_regex_teams: str | None = None
    custom_regex_teams_enabled: bool = False
    custom_regex_date: str | None = None
    custom_regex_date_enabled: bool = False
    custom_regex_time: str | None = None
    custom_regex_time_enabled: bool = False
    skip_builtin_filter: bool = False
    # Processing stats
    last_refresh: str | None = None
    stream_count: int = 0
    matched_count: int = 0
    # Processing stats by category (FILTERED / FAILED / EXCLUDED)
    filtered_include_regex: int = 0  # FILTERED: Didn't match include regex
    filtered_exclude_regex: int = 0  # FILTERED: Matched exclude regex
    filtered_not_event: int = 0  # FILTERED: Stream doesn't look like event
    failed_count: int = 0  # FAILED: Match attempted but couldn't find event
    streams_excluded: int = 0  # EXCLUDED: Matched but excluded (timing/config)
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str = "time"
    overlap_handling: str = "add_stream"
    enabled: bool = True
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

VALID_DUPLICATE_HANDLING = {"consolidate", "separate", "ignore"}
VALID_ASSIGNMENT_MODE = {"auto", "manual"}
VALID_CHANNEL_SORT_ORDER = {"time", "sport_time", "league_time"}
VALID_OVERLAP_HANDLING = {"add_stream", "add_only", "create_all", "skip"}


def validate_group_fields(
    duplicate_event_handling: str | None = None,
    channel_assignment_mode: str | None = None,
    channel_sort_order: str | None = None,
    overlap_handling: str | None = None,
):
    """Validate group field values."""
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
    if channel_sort_order and channel_sort_order not in VALID_CHANNEL_SORT_ORDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid channel_sort_order. Valid: {VALID_CHANNEL_SORT_ORDER}",
        )
    if overlap_handling and overlap_handling not in VALID_OVERLAP_HANDLING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid overlap_handling. Valid: {VALID_OVERLAP_HANDLING}",
        )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("", response_model=GroupListResponse)
def list_groups(
    include_disabled: bool = Query(False, description="Include disabled groups"),
    include_stats: bool = Query(False, description="Include channel counts"),
):
    """List all event EPG groups."""
    from teamarr.database.groups import get_all_group_stats, get_all_groups

    with get_db() as conn:
        groups = get_all_groups(conn, include_disabled=include_disabled)

        stats = {}
        if include_stats:
            stats = get_all_group_stats(conn)

    return GroupListResponse(
        groups=[
            GroupResponse(
                id=g.id,
                name=g.name,
                display_name=g.display_name,
                leagues=g.leagues,
                parent_group_id=g.parent_group_id,
                template_id=g.template_id,
                channel_start_number=g.channel_start_number,
                channel_group_id=g.channel_group_id,
                stream_profile_id=g.stream_profile_id,
                channel_profile_ids=g.channel_profile_ids,
                duplicate_event_handling=g.duplicate_event_handling,
                channel_assignment_mode=g.channel_assignment_mode,
                sort_order=g.sort_order,
                total_stream_count=g.total_stream_count,
                m3u_group_id=g.m3u_group_id,
                m3u_group_name=g.m3u_group_name,
                m3u_account_id=g.m3u_account_id,
                m3u_account_name=g.m3u_account_name,
                stream_include_regex=g.stream_include_regex,
                stream_include_regex_enabled=g.stream_include_regex_enabled,
                stream_exclude_regex=g.stream_exclude_regex,
                stream_exclude_regex_enabled=g.stream_exclude_regex_enabled,
                custom_regex_teams=g.custom_regex_teams,
                custom_regex_teams_enabled=g.custom_regex_teams_enabled,
                custom_regex_date=g.custom_regex_date,
                custom_regex_date_enabled=g.custom_regex_date_enabled,
                custom_regex_time=g.custom_regex_time,
                custom_regex_time_enabled=g.custom_regex_time_enabled,
                skip_builtin_filter=g.skip_builtin_filter,
                last_refresh=g.last_refresh.isoformat() if g.last_refresh else None,
                stream_count=g.stream_count,
                matched_count=g.matched_count,
                filtered_include_regex=g.filtered_include_regex,
                filtered_exclude_regex=g.filtered_exclude_regex,
                failed_count=g.failed_count,
                filtered_not_event=g.filtered_not_event,
                streams_excluded=g.streams_excluded,
                channel_sort_order=g.channel_sort_order,
                overlap_handling=g.overlap_handling,
                enabled=g.enabled,
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
        duplicate_event_handling=request.duplicate_event_handling,
        channel_assignment_mode=request.channel_assignment_mode,
        channel_sort_order=request.channel_sort_order,
        overlap_handling=request.overlap_handling,
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
            display_name=request.display_name,
            parent_group_id=request.parent_group_id,
            template_id=request.template_id,
            channel_start_number=request.channel_start_number,
            channel_group_id=request.channel_group_id,
            stream_profile_id=request.stream_profile_id,
            channel_profile_ids=request.channel_profile_ids,
            duplicate_event_handling=request.duplicate_event_handling,
            channel_assignment_mode=request.channel_assignment_mode,
            sort_order=request.sort_order,
            total_stream_count=request.total_stream_count,
            m3u_group_id=request.m3u_group_id,
            m3u_group_name=request.m3u_group_name,
            m3u_account_id=request.m3u_account_id,
            m3u_account_name=request.m3u_account_name,
            stream_include_regex=request.stream_include_regex,
            stream_include_regex_enabled=request.stream_include_regex_enabled,
            stream_exclude_regex=request.stream_exclude_regex,
            stream_exclude_regex_enabled=request.stream_exclude_regex_enabled,
            custom_regex_teams=request.custom_regex_teams,
            custom_regex_teams_enabled=request.custom_regex_teams_enabled,
            custom_regex_date=request.custom_regex_date,
            custom_regex_date_enabled=request.custom_regex_date_enabled,
            custom_regex_time=request.custom_regex_time,
            custom_regex_time_enabled=request.custom_regex_time_enabled,
            skip_builtin_filter=request.skip_builtin_filter,
            channel_sort_order=request.channel_sort_order,
            overlap_handling=request.overlap_handling,
            enabled=request.enabled,
        )

        group = get_group(conn, group_id)

    return GroupResponse(
        id=group.id,
        name=group.name,
        display_name=group.display_name,
        leagues=group.leagues,
        parent_group_id=group.parent_group_id,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        stream_profile_id=group.stream_profile_id,
        channel_profile_ids=group.channel_profile_ids,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        sort_order=group.sort_order,
        total_stream_count=group.total_stream_count,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        m3u_account_id=group.m3u_account_id,
        m3u_account_name=group.m3u_account_name,
        stream_include_regex=group.stream_include_regex,
        stream_include_regex_enabled=group.stream_include_regex_enabled,
        stream_exclude_regex=group.stream_exclude_regex,
        stream_exclude_regex_enabled=group.stream_exclude_regex_enabled,
        custom_regex_teams=group.custom_regex_teams,
        custom_regex_teams_enabled=group.custom_regex_teams_enabled,
        custom_regex_date=group.custom_regex_date,
        custom_regex_date_enabled=group.custom_regex_date_enabled,
        custom_regex_time=group.custom_regex_time,
        custom_regex_time_enabled=group.custom_regex_time_enabled,
        skip_builtin_filter=group.skip_builtin_filter,
        last_refresh=group.last_refresh.isoformat() if group.last_refresh else None,
        stream_count=group.stream_count,
        matched_count=group.matched_count,
        filtered_include_regex=group.filtered_include_regex,
        filtered_exclude_regex=group.filtered_exclude_regex,
        failed_count=group.failed_count,
        filtered_not_event=group.filtered_not_event,
        streams_excluded=group.streams_excluded,
        channel_sort_order=group.channel_sort_order,
        overlap_handling=group.overlap_handling,
        enabled=group.enabled,
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
        display_name=group.display_name,
        leagues=group.leagues,
        parent_group_id=group.parent_group_id,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        stream_profile_id=group.stream_profile_id,
        channel_profile_ids=group.channel_profile_ids,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        sort_order=group.sort_order,
        total_stream_count=group.total_stream_count,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        m3u_account_id=group.m3u_account_id,
        m3u_account_name=group.m3u_account_name,
        stream_include_regex=group.stream_include_regex,
        stream_include_regex_enabled=group.stream_include_regex_enabled,
        stream_exclude_regex=group.stream_exclude_regex,
        stream_exclude_regex_enabled=group.stream_exclude_regex_enabled,
        custom_regex_teams=group.custom_regex_teams,
        custom_regex_teams_enabled=group.custom_regex_teams_enabled,
        custom_regex_date=group.custom_regex_date,
        custom_regex_date_enabled=group.custom_regex_date_enabled,
        custom_regex_time=group.custom_regex_time,
        custom_regex_time_enabled=group.custom_regex_time_enabled,
        skip_builtin_filter=group.skip_builtin_filter,
        last_refresh=group.last_refresh.isoformat() if group.last_refresh else None,
        stream_count=group.stream_count,
        matched_count=group.matched_count,
        filtered_include_regex=group.filtered_include_regex,
        filtered_exclude_regex=group.filtered_exclude_regex,
        failed_count=group.failed_count,
        filtered_not_event=group.filtered_not_event,
        streams_excluded=group.streams_excluded,
        channel_sort_order=group.channel_sort_order,
        overlap_handling=group.overlap_handling,
        enabled=group.enabled,
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
        duplicate_event_handling=request.duplicate_event_handling,
        channel_assignment_mode=request.channel_assignment_mode,
        channel_sort_order=request.channel_sort_order,
        overlap_handling=request.overlap_handling,
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
            display_name=request.display_name,
            leagues=request.leagues,
            parent_group_id=request.parent_group_id,
            template_id=request.template_id,
            channel_start_number=request.channel_start_number,
            channel_group_id=request.channel_group_id,
            stream_profile_id=request.stream_profile_id,
            channel_profile_ids=request.channel_profile_ids,
            duplicate_event_handling=request.duplicate_event_handling,
            channel_assignment_mode=request.channel_assignment_mode,
            sort_order=request.sort_order,
            total_stream_count=request.total_stream_count,
            m3u_group_id=request.m3u_group_id,
            m3u_group_name=request.m3u_group_name,
            m3u_account_id=request.m3u_account_id,
            m3u_account_name=request.m3u_account_name,
            stream_include_regex=request.stream_include_regex,
            stream_include_regex_enabled=request.stream_include_regex_enabled,
            stream_exclude_regex=request.stream_exclude_regex,
            stream_exclude_regex_enabled=request.stream_exclude_regex_enabled,
            custom_regex_teams=request.custom_regex_teams,
            custom_regex_teams_enabled=request.custom_regex_teams_enabled,
            custom_regex_date=request.custom_regex_date,
            custom_regex_date_enabled=request.custom_regex_date_enabled,
            custom_regex_time=request.custom_regex_time,
            custom_regex_time_enabled=request.custom_regex_time_enabled,
            skip_builtin_filter=request.skip_builtin_filter,
            channel_sort_order=request.channel_sort_order,
            overlap_handling=request.overlap_handling,
            enabled=request.enabled,
            clear_display_name=request.clear_display_name,
            clear_parent_group_id=request.clear_parent_group_id,
            clear_template=request.clear_template,
            clear_channel_start_number=request.clear_channel_start_number,
            clear_channel_group_id=request.clear_channel_group_id,
            clear_stream_profile_id=request.clear_stream_profile_id,
            clear_channel_profile_ids=request.clear_channel_profile_ids,
            clear_m3u_group_id=request.clear_m3u_group_id,
            clear_m3u_group_name=request.clear_m3u_group_name,
            clear_m3u_account_id=request.clear_m3u_account_id,
            clear_m3u_account_name=request.clear_m3u_account_name,
            clear_stream_include_regex=request.clear_stream_include_regex,
            clear_stream_exclude_regex=request.clear_stream_exclude_regex,
            clear_custom_regex_teams=request.clear_custom_regex_teams,
            clear_custom_regex_date=request.clear_custom_regex_date,
            clear_custom_regex_time=request.clear_custom_regex_time,
        )

        group = get_group(conn, group_id)
        channel_count = get_group_channel_count(conn, group_id)

    return GroupResponse(
        id=group.id,
        name=group.name,
        display_name=group.display_name,
        leagues=group.leagues,
        parent_group_id=group.parent_group_id,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        stream_profile_id=group.stream_profile_id,
        channel_profile_ids=group.channel_profile_ids,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        sort_order=group.sort_order,
        total_stream_count=group.total_stream_count,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        m3u_account_id=group.m3u_account_id,
        m3u_account_name=group.m3u_account_name,
        stream_include_regex=group.stream_include_regex,
        stream_include_regex_enabled=group.stream_include_regex_enabled,
        stream_exclude_regex=group.stream_exclude_regex,
        stream_exclude_regex_enabled=group.stream_exclude_regex_enabled,
        custom_regex_teams=group.custom_regex_teams,
        custom_regex_teams_enabled=group.custom_regex_teams_enabled,
        custom_regex_date=group.custom_regex_date,
        custom_regex_date_enabled=group.custom_regex_date_enabled,
        custom_regex_time=group.custom_regex_time,
        custom_regex_time_enabled=group.custom_regex_time_enabled,
        skip_builtin_filter=group.skip_builtin_filter,
        last_refresh=group.last_refresh.isoformat() if group.last_refresh else None,
        stream_count=group.stream_count,
        matched_count=group.matched_count,
        filtered_include_regex=group.filtered_include_regex,
        filtered_exclude_regex=group.filtered_exclude_regex,
        failed_count=group.failed_count,
        filtered_not_event=group.filtered_not_event,
        streams_excluded=group.streams_excluded,
        channel_sort_order=group.channel_sort_order,
        overlap_handling=group.overlap_handling,
        enabled=group.enabled,
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


@router.post("/{group_id}/enable")
def enable_group(group_id: int) -> dict:
    """Enable an event EPG group."""
    from teamarr.database.groups import get_group, set_group_enabled

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        set_group_enabled(conn, group_id, True)

    return {"success": True, "message": f"Group '{group.name}' enabled"}


@router.post("/{group_id}/disable")
def disable_group(group_id: int) -> dict:
    """Disable an event EPG group."""
    from teamarr.database.groups import get_group, set_group_enabled

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        set_group_enabled(conn, group_id, False)

    return {"success": True, "message": f"Group '{group.name}' disabled"}


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


class PreviewStreamModel(BaseModel):
    """Individual stream preview result."""

    stream_id: int
    stream_name: str
    matched: bool
    event_id: str | None = None
    event_name: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    league: str | None = None
    start_time: str | None = None
    from_cache: bool = False
    exclusion_reason: str | None = None


class PreviewGroupResponse(BaseModel):
    """Response from previewing stream matches for a group."""

    group_id: int
    group_name: str
    total_streams: int
    filtered_count: int
    matched_count: int
    unmatched_count: int
    filtered_not_event: int = 0
    filtered_include_regex: int = 0
    filtered_exclude_regex: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    streams: list[PreviewStreamModel]
    errors: list[str]


@router.get("/{group_id}/preview", response_model=PreviewGroupResponse)
def preview_group(group_id: int):
    """Preview stream matching for a group without creating channels.

    Fetches streams from Dispatcharr, filters them, matches them to events,
    but does NOT create channels or generate EPG.
    """
    from datetime import date

    from teamarr.database.groups import get_group
    from teamarr.dispatcharr import get_factory
    from teamarr.services import create_group_service

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

    # Get Dispatcharr connection (has m3u manager)
    factory = get_factory(get_db)
    conn = factory.get_connection() if factory else None

    # Preview the group
    group_service = create_group_service(get_db, conn)
    result = group_service.preview_group(group_id, date.today())

    return PreviewGroupResponse(
        group_id=result.group_id,
        group_name=result.group_name,
        total_streams=result.total_streams,
        filtered_count=result.filtered_count,
        matched_count=result.matched_count,
        unmatched_count=result.unmatched_count,
        filtered_not_event=result.filtered_not_event,
        filtered_include_regex=result.filtered_include_regex,
        filtered_exclude_regex=result.filtered_exclude_regex,
        cache_hits=result.cache_hits,
        cache_misses=result.cache_misses,
        streams=[
            PreviewStreamModel(
                stream_id=s.stream_id,
                stream_name=s.stream_name,
                matched=s.matched,
                event_id=s.event_id,
                event_name=s.event_name,
                home_team=s.home_team,
                away_team=s.away_team,
                league=s.league,
                start_time=s.start_time,
                from_cache=s.from_cache,
                exclusion_reason=s.exclusion_reason,
            )
            for s in result.streams
        ],
        errors=result.errors,
    )


@router.post("/{group_id}/process", response_model=ProcessGroupResponse)
def process_group(group_id: int):
    """Process an event EPG group.

    Fetches streams from Dispatcharr, matches them to events,
    and creates/updates channels.
    """
    from datetime import date

    from teamarr.database.groups import get_group
    from teamarr.dispatcharr import get_factory
    from teamarr.services import create_group_service

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
    group_service = create_group_service(get_db, client)
    result = group_service.process_group(group_id, date.today())

    duration = 0.0
    if result.started_at and result.completed_at:
        duration = (result.completed_at - result.started_at).total_seconds()

    return ProcessGroupResponse(
        group_id=result.group_id,
        group_name=result.group_name,
        streams_fetched=result.streams.fetched,
        streams_matched=result.streams.matched,
        streams_unmatched=result.streams.unmatched,
        channels_created=result.channels.created,
        channels_existing=result.channels.existing,
        channels_skipped=result.channels.skipped,
        channel_errors=result.channels.errors,
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

    from teamarr.dispatcharr import get_factory
    from teamarr.services import create_group_service

    # Get Dispatcharr client
    factory = get_factory(get_db)
    client = factory.get_client() if factory else None

    # Process all groups
    group_service = create_group_service(get_db, client)
    batch_result = group_service.process_all_groups(date.today())

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
                streams_fetched=r.streams.fetched,
                streams_matched=r.streams.matched,
                streams_unmatched=r.streams.unmatched,
                channels_created=r.channels.created,
                channels_existing=r.channels.existing,
                channels_skipped=r.channels.skipped,
                channel_errors=r.channels.errors,
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
    """Get combined XMLTV from all enabled event groups.

    Merges XMLTV content from all groups that have been processed.
    This is useful for having a single EPG source in Dispatcharr.
    """
    from teamarr.database.groups import get_all_groups

    with get_db() as conn:
        # Get all enabled groups
        groups = get_all_groups(conn, include_disabled=False)
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


# ========================================================================
# Group Reordering (for AUTO channel assignment)
# ========================================================================


class GroupOrderItem(BaseModel):
    """Single group reorder item."""

    group_id: int
    sort_order: int


class ReorderGroupsRequest(BaseModel):
    """Request to reorder multiple groups."""

    groups: list[GroupOrderItem]


class ReorderGroupsResponse(BaseModel):
    """Response from reordering groups."""

    success: bool
    updated_count: int
    message: str


@router.post("/reorder", response_model=ReorderGroupsResponse)
def reorder_groups(request: ReorderGroupsRequest):
    """Reorder groups by updating their sort_order values.

    Used for drag-and-drop reordering of AUTO channel assignment groups.
    Affects the order in which channel number ranges are allocated.
    """
    if not request.groups:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No groups to reorder",
        )

    with get_db() as conn:
        updated = 0
        for item in request.groups:
            conn.execute(
                "UPDATE event_epg_groups SET sort_order = ? WHERE id = ?",
                (item.sort_order, item.group_id),
            )
            updated += 1

        conn.commit()

    return ReorderGroupsResponse(
        success=True,
        updated_count=updated,
        message=f"Updated sort order for {updated} groups",
    )
