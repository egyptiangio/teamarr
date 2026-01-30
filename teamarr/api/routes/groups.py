"""Event EPG groups management endpoints.

Provides REST API for:
- CRUD operations on event EPG groups
- Group statistics and channel counts
- M3U group discovery from Dispatcharr
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from teamarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


def _validate_profile_ids(v: Any) -> list[str | int] | None:
    """Validate channel_profile_ids accepts mixed int/str types.

    Pydantic v2 union validation can fail on mixed types when the first
    element is an int (it infers list[int] and rejects subsequent strings).
    This validator explicitly handles the mixed case.
    """
    if v is None:
        return None
    if not isinstance(v, list):
        return v
    result: list[str | int] = []
    for item in v:
        if isinstance(item, int):
            result.append(item)
        elif isinstance(item, str):
            # Keep wildcards as strings, convert numeric strings to int
            if item in ("{sport}", "{league}"):
                result.append(item)
            elif item.isdigit():
                result.append(int(item))
            else:
                result.append(item)
        else:
            # Let Pydantic handle invalid types
            result.append(item)
    return result


class TeamFilterEntry(BaseModel):
    """A team reference for include/exclude filtering.

    Uses canonical team selection from team_cache for unambiguous identification.
    """

    provider: str  # e.g., "espn", "tsdb"
    team_id: str  # provider_team_id from team_cache
    league: str  # e.g., "nfl", "nba"
    name: str | None = None  # For display only, not used in matching


class GroupCreate(BaseModel):
    """Create event EPG group request."""

    name: str = Field(..., min_length=1, max_length=100)
    display_name: str | None = Field(None, max_length=100)  # Optional display name override
    leagues: list[str] = Field(..., min_length=1)
    group_mode: str = "single"  # "single" or "multi" - persisted to preserve user intent
    parent_group_id: int | None = None
    template_id: int | None = None
    channel_start_number: int | None = Field(None, ge=1)
    channel_group_id: int | None = None
    channel_group_mode: str = "static"  # "static", "sport", "league"
    channel_profile_ids: list[str | int] | None = None  # IDs or "{sport}", "{league}"
    stream_profile_id: int | None = None  # Stream profile (overrides global default)
    stream_timezone: str | None = None  # Timezone for stream datetime parsing
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
    custom_regex_league: str | None = None
    custom_regex_league_enabled: bool = False
    skip_builtin_filter: bool = False
    # Team filtering (canonical team selection, inherited by children)
    include_teams: list[TeamFilterEntry] | None = None
    exclude_teams: list[TeamFilterEntry] | None = None
    team_filter_mode: str = "include"  # "include" (whitelist) or "exclude" (blacklist)
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str = "time"
    overlap_handling: str = "add_stream"
    enabled: bool = True

    @field_validator("channel_profile_ids", mode="before")
    @classmethod
    def validate_profile_ids(cls, v: Any) -> list[str | int] | None:
        return _validate_profile_ids(v)


class GroupUpdate(BaseModel):
    """Update event EPG group request."""

    name: str | None = Field(None, min_length=1, max_length=100)
    display_name: str | None = Field(None, max_length=100)  # Optional display name override
    leagues: list[str] | None = None
    group_mode: str | None = None  # "single" or "multi" - persisted to preserve user intent
    parent_group_id: int | None = None
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    channel_group_mode: str | None = None  # "static", "sport", "league"
    channel_profile_ids: list[str | int] | None = None  # IDs or "{sport}", "{league}"
    stream_profile_id: int | None = None  # Stream profile (overrides global default)
    stream_timezone: str | None = None  # Timezone for stream datetime parsing
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
    custom_regex_league: str | None = None
    custom_regex_league_enabled: bool | None = None
    skip_builtin_filter: bool | None = None
    # Team filtering (canonical team selection, inherited by children)
    include_teams: list[TeamFilterEntry] | None = None
    exclude_teams: list[TeamFilterEntry] | None = None
    team_filter_mode: str | None = None  # "include" (whitelist) or "exclude" (blacklist)
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
    clear_channel_profile_ids: bool = False
    clear_stream_profile_id: bool = False
    clear_stream_timezone: bool = False
    clear_m3u_group_id: bool = False
    clear_m3u_group_name: bool = False
    clear_m3u_account_id: bool = False
    clear_m3u_account_name: bool = False
    clear_stream_include_regex: bool = False
    clear_stream_exclude_regex: bool = False
    clear_custom_regex_teams: bool = False
    clear_custom_regex_date: bool = False
    clear_custom_regex_time: bool = False
    clear_custom_regex_league: bool = False
    clear_include_teams: bool = False
    clear_exclude_teams: bool = False

    @field_validator("channel_profile_ids", mode="before")
    @classmethod
    def validate_profile_ids(cls, v: Any) -> list[str | int] | None:
        return _validate_profile_ids(v)


class GroupResponse(BaseModel):
    """Event EPG group response."""

    id: int
    name: str
    display_name: str | None = None  # Optional display name override for UI
    leagues: list[str]
    group_mode: str = "single"  # "single" or "multi" - persisted to preserve user intent
    parent_group_id: int | None = None
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    channel_group_mode: str = "static"  # "static", "sport", "league"
    channel_profile_ids: list[str | int] = []  # IDs or "{sport}", "{league}"
    stream_profile_id: int | None = None  # Stream profile (overrides global default)
    stream_timezone: str | None = None  # Timezone for stream datetime parsing
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
    custom_regex_league: str | None = None
    custom_regex_league_enabled: bool = False
    skip_builtin_filter: bool = False
    # Team filtering (canonical team selection, inherited by children)
    include_teams: list[TeamFilterEntry] | None = None
    exclude_teams: list[TeamFilterEntry] | None = None
    team_filter_mode: str = "include"  # "include" (whitelist) or "exclude" (blacklist)
    # Processing stats
    last_refresh: str | None = None
    stream_count: int = 0
    matched_count: int = 0
    # Processing stats by category (FILTERED / FAILED / EXCLUDED)
    filtered_stale: int = 0  # FILTERED: Stream marked as stale in Dispatcharr
    filtered_include_regex: int = 0  # FILTERED: Didn't match include regex
    filtered_exclude_regex: int = 0  # FILTERED: Matched exclude regex
    filtered_not_event: int = 0  # FILTERED: Stream doesn't look like event
    filtered_team: int = 0  # FILTERED: Team not in include/exclude filter
    failed_count: int = 0  # FAILED: Match attempted but couldn't find event
    streams_excluded: int = 0  # EXCLUDED: Matched but excluded (aggregate)
    # EXCLUDED breakdown by reason
    excluded_event_final: int = 0
    excluded_event_past: int = 0
    excluded_before_window: int = 0
    excluded_league_not_included: int = 0
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str = "time"
    overlap_handling: str = "add_stream"
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None
    channel_count: int | None = None

    @field_validator("channel_profile_ids", mode="before")
    @classmethod
    def validate_profile_ids(cls, v: Any) -> list[str | int]:
        result = _validate_profile_ids(v)
        return result if result is not None else []


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


class BulkGroupItem(BaseModel):
    """Single group to create in bulk import."""

    m3u_group_id: int
    m3u_group_name: str
    m3u_account_id: int
    m3u_account_name: str


class BulkGroupSettings(BaseModel):
    """Shared settings for bulk group creation."""

    group_mode: str = "single"  # "single" or "multi"
    leagues: list[str] = Field(..., min_length=1)
    template_id: int | None = None
    channel_group_id: int | None = None
    channel_group_mode: str = "static"  # "static", "sport", "league"
    channel_profile_ids: list[str | int] | None = None  # IDs or "{sport}", "{league}"
    stream_profile_id: int | None = None  # Stream profile (overrides global default)
    stream_timezone: str | None = None  # Timezone for stream datetime parsing
    duplicate_event_handling: str = "consolidate"
    channel_sort_order: str = "time"
    overlap_handling: str = "add_stream"
    enabled: bool = True

    @field_validator("channel_profile_ids", mode="before")
    @classmethod
    def validate_profile_ids(cls, v: Any) -> list[str | int] | None:
        return _validate_profile_ids(v)


class BulkGroupCreateRequest(BaseModel):
    """Bulk create event EPG groups request."""

    groups: list[BulkGroupItem] = Field(..., min_length=1)
    settings: BulkGroupSettings


class BulkGroupCreateResult(BaseModel):
    """Result of a single group creation in bulk."""

    m3u_group_id: int
    m3u_account_id: int
    group_id: int | None = None
    name: str
    success: bool
    error: str | None = None


class BulkGroupCreateResponse(BaseModel):
    """Response from bulk group creation."""

    created: list[BulkGroupCreateResult]
    total_requested: int
    total_created: int
    total_failed: int


class BulkGroupUpdateRequest(BaseModel):
    """Bulk update event EPG groups request.

    Only provided (non-None) fields will be updated.
    Use clear_* flags to explicitly set fields to NULL.
    """

    group_ids: list[int] = Field(..., min_length=1)

    # Updateable fields (all optional - only provided fields are applied)
    leagues: list[str] | None = None
    template_id: int | None = None
    channel_group_id: int | None = None
    channel_group_mode: str | None = None
    channel_profile_ids: list[str | int] | None = None
    stream_profile_id: int | None = None  # Stream profile (overrides global default)
    stream_timezone: str | None = None  # Timezone for stream datetime parsing
    duplicate_event_handling: str | None = None
    channel_sort_order: str | None = None
    overlap_handling: str | None = None
    enabled: bool | None = None

    # Clear flags to explicitly set fields to NULL
    clear_template: bool = False
    clear_channel_group_id: bool = False
    clear_channel_profile_ids: bool = False
    clear_stream_profile_id: bool = False
    clear_stream_timezone: bool = False

    @field_validator("channel_profile_ids", mode="before")
    @classmethod
    def validate_profile_ids(cls, v: Any) -> list[str | int] | None:
        return _validate_profile_ids(v)


class ClearCacheRequest(BaseModel):
    """Request to clear stream match cache for multiple groups."""

    group_ids: list[int] = Field(..., min_length=1)


class ClearCacheGroupResult(BaseModel):
    """Result of clearing cache for a single group."""

    group_id: int
    cleared: int


class ClearCacheResponse(BaseModel):
    """Response from clearing stream match cache."""

    success: bool
    group_id: int | None = None  # For single group
    group_name: str | None = None  # For single group
    entries_cleared: int | None = None  # For single group
    total_cleared: int | None = None  # For bulk
    by_group: list[ClearCacheGroupResult] | None = None  # For bulk
    # Fields to update (only non-None values are applied)
    leagues: list[str] | None = None
    template_id: int | None = None
    channel_group_id: int | None = None
    channel_group_mode: str | None = None  # "static", "sport", "league"
    channel_profile_ids: list[str | int] | None = None  # IDs or "{sport}", "{league}"
    channel_sort_order: str | None = None
    overlap_handling: str | None = None
    # Clear flags for nullable fields
    clear_template: bool = False
    clear_channel_group_id: bool = False
    clear_channel_profile_ids: bool = False

    @field_validator("channel_profile_ids", mode="before")
    @classmethod
    def validate_profile_ids(cls, v: Any) -> list[str | int] | None:
        return _validate_profile_ids(v)


class BulkGroupUpdateResult(BaseModel):
    """Result of a single group update in bulk."""

    group_id: int
    name: str
    success: bool
    error: str | None = None


class BulkGroupUpdateResponse(BaseModel):
    """Response from bulk group update."""

    results: list[BulkGroupUpdateResult]
    total_requested: int
    total_updated: int
    total_failed: int


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
    from teamarr.dispatcharr import get_dispatcharr_connection

    with get_db() as conn:
        groups = get_all_groups(conn, include_disabled=include_disabled)

        stats = {}
        if include_stats:
            stats = get_all_group_stats(conn)

    # Fetch fresh M3U account names from Dispatcharr
    m3u_account_names: dict[int, str] = {}
    account_ids = {g.m3u_account_id for g in groups if g.m3u_account_id}
    if account_ids:
        try:
            dispatcharr = get_dispatcharr_connection(get_db)
            if dispatcharr:
                accounts = dispatcharr.m3u.list_accounts()
                m3u_account_names = {a.id: a.name for a in accounts}
        except Exception:
            pass  # Fall back to stored names if Dispatcharr unavailable

    def get_account_name(g):
        """Get fresh M3U account name, falling back to stored name."""
        if g.m3u_account_id and g.m3u_account_id in m3u_account_names:
            return m3u_account_names[g.m3u_account_id]
        return g.m3u_account_name

    return GroupListResponse(
        groups=[
            GroupResponse(
                id=g.id,
                name=g.name,
                display_name=g.display_name,
                leagues=g.leagues,
                group_mode=g.group_mode,
                parent_group_id=g.parent_group_id,
                template_id=g.template_id,
                channel_start_number=g.channel_start_number,
                channel_group_id=g.channel_group_id,
                channel_profile_ids=g.channel_profile_ids,
                duplicate_event_handling=g.duplicate_event_handling,
                channel_assignment_mode=g.channel_assignment_mode,
                sort_order=g.sort_order,
                total_stream_count=g.total_stream_count,
                m3u_group_id=g.m3u_group_id,
                m3u_group_name=g.m3u_group_name,
                m3u_account_id=g.m3u_account_id,
                m3u_account_name=get_account_name(g),
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
                custom_regex_league=g.custom_regex_league,
                custom_regex_league_enabled=g.custom_regex_league_enabled,
                skip_builtin_filter=g.skip_builtin_filter,
                include_teams=[TeamFilterEntry(**t) for t in g.include_teams]
                if g.include_teams
                else None,
                exclude_teams=[TeamFilterEntry(**t) for t in g.exclude_teams]
                if g.exclude_teams
                else None,
                team_filter_mode=g.team_filter_mode,
                last_refresh=g.last_refresh.isoformat() if g.last_refresh else None,
                stream_count=g.stream_count,
                matched_count=g.matched_count,
                filtered_stale=g.filtered_stale,
                filtered_include_regex=g.filtered_include_regex,
                filtered_exclude_regex=g.filtered_exclude_regex,
                filtered_not_event=g.filtered_not_event,
                filtered_team=g.filtered_team,
                failed_count=g.failed_count,
                streams_excluded=g.streams_excluded,
                excluded_event_final=g.excluded_event_final,
                excluded_event_past=g.excluded_event_past,
                excluded_before_window=g.excluded_before_window,
                excluded_league_not_included=g.excluded_league_not_included,
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
        # Check for duplicate name within same M3U account
        existing = get_group_by_name(conn, request.name, request.m3u_account_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group with name '{request.name}' already exists for this M3U account",
            )

        group_id = create_group(
            conn,
            name=request.name,
            leagues=request.leagues,
            display_name=request.display_name,
            group_mode=request.group_mode,
            parent_group_id=request.parent_group_id,
            template_id=request.template_id,
            channel_start_number=request.channel_start_number,
            channel_group_id=request.channel_group_id,
            channel_group_mode=request.channel_group_mode,
            channel_profile_ids=request.channel_profile_ids,
            stream_profile_id=request.stream_profile_id,
            stream_timezone=request.stream_timezone,
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
            custom_regex_league=request.custom_regex_league,
            custom_regex_league_enabled=request.custom_regex_league_enabled,
            skip_builtin_filter=request.skip_builtin_filter,
            include_teams=[t.model_dump() for t in request.include_teams]
            if request.include_teams is not None
            else None,
            exclude_teams=[t.model_dump() for t in request.exclude_teams]
            if request.exclude_teams is not None
            else None,
            team_filter_mode=request.team_filter_mode,
            channel_sort_order=request.channel_sort_order,
            overlap_handling=request.overlap_handling,
            enabled=request.enabled,
        )

        group = get_group(conn, group_id)

    logger.info("[CREATED] Event group id=%d name=%s", group_id, request.name)

    return GroupResponse(
        id=group.id,
        name=group.name,
        display_name=group.display_name,
        leagues=group.leagues,
        group_mode=group.group_mode,
        parent_group_id=group.parent_group_id,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        channel_group_mode=group.channel_group_mode,
        channel_profile_ids=group.channel_profile_ids,
        stream_profile_id=group.stream_profile_id,
        stream_timezone=group.stream_timezone,
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
        include_teams=[TeamFilterEntry(**t) for t in group.include_teams]
        if group.include_teams
        else None,
        exclude_teams=[TeamFilterEntry(**t) for t in group.exclude_teams]
        if group.exclude_teams
        else None,
        team_filter_mode=group.team_filter_mode,
        last_refresh=group.last_refresh.isoformat() if group.last_refresh else None,
        stream_count=group.stream_count,
        matched_count=group.matched_count,
        filtered_stale=group.filtered_stale,
        filtered_include_regex=group.filtered_include_regex,
        filtered_exclude_regex=group.filtered_exclude_regex,
        filtered_not_event=group.filtered_not_event,
        filtered_team=group.filtered_team,
        failed_count=group.failed_count,
        streams_excluded=group.streams_excluded,
        excluded_event_final=group.excluded_event_final,
        excluded_event_past=group.excluded_event_past,
        excluded_before_window=group.excluded_before_window,
        excluded_league_not_included=group.excluded_league_not_included,
        channel_sort_order=group.channel_sort_order,
        overlap_handling=group.overlap_handling,
        enabled=group.enabled,
        created_at=group.created_at.isoformat() if group.created_at else None,
        updated_at=group.updated_at.isoformat() if group.updated_at else None,
    )


@router.post("/bulk", response_model=BulkGroupCreateResponse, status_code=status.HTTP_201_CREATED)
def create_groups_bulk(request: BulkGroupCreateRequest):
    """Bulk create event EPG groups with shared settings.

    All groups will be created with the same mode, leagues, and settings.
    Useful for importing multiple groups from the same M3U account.
    """
    from teamarr.database.groups import create_group, get_group_by_name

    # Validate settings
    validate_group_fields(
        duplicate_event_handling=request.settings.duplicate_event_handling,
        channel_sort_order=request.settings.channel_sort_order,
        overlap_handling=request.settings.overlap_handling,
    )

    results: list[BulkGroupCreateResult] = []
    total_created = 0
    total_failed = 0

    with get_db() as conn:
        for item in request.groups:
            try:
                # Check for duplicate name within same M3U account
                existing = get_group_by_name(conn, item.m3u_group_name, item.m3u_account_id)
                if existing:
                    results.append(
                        BulkGroupCreateResult(
                            m3u_group_id=item.m3u_group_id,
                            m3u_account_id=item.m3u_account_id,
                            name=item.m3u_group_name,
                            success=False,
                            error="Group already exists for this M3U account",
                        )
                    )
                    total_failed += 1
                    continue

                # Create the group
                group_id = create_group(
                    conn,
                    name=item.m3u_group_name,
                    leagues=request.settings.leagues,
                    group_mode=request.settings.group_mode,
                    template_id=request.settings.template_id,
                    channel_group_id=request.settings.channel_group_id,
                    channel_group_mode=request.settings.channel_group_mode,
                    channel_profile_ids=request.settings.channel_profile_ids,
                    stream_profile_id=request.settings.stream_profile_id,
                    stream_timezone=request.settings.stream_timezone,
                    duplicate_event_handling=request.settings.duplicate_event_handling,
                    channel_sort_order=request.settings.channel_sort_order,
                    overlap_handling=request.settings.overlap_handling,
                    m3u_group_id=item.m3u_group_id,
                    m3u_group_name=item.m3u_group_name,
                    m3u_account_id=item.m3u_account_id,
                    m3u_account_name=item.m3u_account_name,
                    enabled=request.settings.enabled,
                )

                results.append(
                    BulkGroupCreateResult(
                        m3u_group_id=item.m3u_group_id,
                        m3u_account_id=item.m3u_account_id,
                        group_id=group_id,
                        name=item.m3u_group_name,
                        success=True,
                    )
                )
                total_created += 1

            except Exception as e:
                results.append(
                    BulkGroupCreateResult(
                        m3u_group_id=item.m3u_group_id,
                        m3u_account_id=item.m3u_account_id,
                        name=item.m3u_group_name,
                        success=False,
                        error=str(e),
                    )
                )
                total_failed += 1

    logger.info("[BULK_IMPORT] Event groups: %d created, %d failed", total_created, total_failed)

    return BulkGroupCreateResponse(
        created=results,
        total_requested=len(request.groups),
        total_created=total_created,
        total_failed=total_failed,
    )


@router.put("/bulk", response_model=BulkGroupUpdateResponse)
def update_groups_bulk(request: BulkGroupUpdateRequest):
    """Bulk update event EPG groups with shared settings.

    Only provided (non-None) fields will be updated across all selected groups.
    Use clear_* flags to explicitly set fields to NULL.

    Note: All groups must have the same group_mode (single/multi) - the frontend
    should prevent mixed selections.
    """
    from teamarr.database.groups import get_group, update_group

    # Validate fields
    validate_group_fields(
        channel_sort_order=request.channel_sort_order,
        overlap_handling=request.overlap_handling,
    )

    results: list[BulkGroupUpdateResult] = []
    total_updated = 0
    total_failed = 0

    with get_db() as conn:
        for group_id in request.group_ids:
            try:
                # Verify group exists
                group = get_group(conn, group_id)
                if not group:
                    results.append(
                        BulkGroupUpdateResult(
                            group_id=group_id,
                            name=f"Group {group_id}",
                            success=False,
                            error="Group not found",
                        )
                    )
                    total_failed += 1
                    continue

                # Update the group with provided fields
                update_group(
                    conn,
                    group_id,
                    leagues=request.leagues,
                    template_id=request.template_id,
                    channel_group_id=request.channel_group_id,
                    channel_group_mode=request.channel_group_mode,
                    channel_profile_ids=request.channel_profile_ids,
                    stream_profile_id=request.stream_profile_id,
                    stream_timezone=request.stream_timezone,
                    duplicate_event_handling=request.duplicate_event_handling,
                    channel_sort_order=request.channel_sort_order,
                    overlap_handling=request.overlap_handling,
                    enabled=request.enabled,
                    clear_template=request.clear_template,
                    clear_channel_group_id=request.clear_channel_group_id,
                    clear_channel_profile_ids=request.clear_channel_profile_ids,
                    clear_stream_profile_id=request.clear_stream_profile_id,
                    clear_stream_timezone=request.clear_stream_timezone,
                )

                results.append(
                    BulkGroupUpdateResult(
                        group_id=group_id,
                        name=group.name,
                        success=True,
                    )
                )
                total_updated += 1

            except Exception as e:
                logger.exception("[BULK_UPDATE] Failed to update group %d: %s", group_id, e)
                results.append(
                    BulkGroupUpdateResult(
                        group_id=group_id,
                        name=f"Group {group_id}",
                        success=False,
                        error=str(e),
                    )
                )
                total_failed += 1

    logger.info("[BULK_UPDATE] Event groups: %d updated, %d failed", total_updated, total_failed)

    return BulkGroupUpdateResponse(
        results=results,
        total_requested=len(request.group_ids),
        total_updated=total_updated,
        total_failed=total_failed,
    )


@router.get("/{group_id}", response_model=GroupResponse)
def get_group_by_id(group_id: int):
    """Get a single event EPG group."""
    from teamarr.database.groups import get_group, get_group_channel_count
    from teamarr.dispatcharr import get_dispatcharr_connection

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        channel_count = get_group_channel_count(conn, group_id)

    # Fetch fresh M3U account name from Dispatcharr
    m3u_account_name = group.m3u_account_name
    if group.m3u_account_id:
        try:
            dispatcharr = get_dispatcharr_connection(get_db)
            if dispatcharr:
                accounts = dispatcharr.m3u.list_accounts()
                for a in accounts:
                    if a.id == group.m3u_account_id:
                        m3u_account_name = a.name
                        break
        except Exception:
            pass  # Fall back to stored name if Dispatcharr unavailable

    return GroupResponse(
        id=group.id,
        name=group.name,
        display_name=group.display_name,
        leagues=group.leagues,
        group_mode=group.group_mode,
        parent_group_id=group.parent_group_id,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        channel_group_mode=group.channel_group_mode,
        channel_profile_ids=group.channel_profile_ids,
        stream_profile_id=group.stream_profile_id,
        stream_timezone=group.stream_timezone,
        duplicate_event_handling=group.duplicate_event_handling,
        channel_assignment_mode=group.channel_assignment_mode,
        sort_order=group.sort_order,
        total_stream_count=group.total_stream_count,
        m3u_group_id=group.m3u_group_id,
        m3u_group_name=group.m3u_group_name,
        m3u_account_id=group.m3u_account_id,
        m3u_account_name=m3u_account_name,
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
        include_teams=[TeamFilterEntry(**t) for t in group.include_teams]
        if group.include_teams
        else None,
        exclude_teams=[TeamFilterEntry(**t) for t in group.exclude_teams]
        if group.exclude_teams
        else None,
        team_filter_mode=group.team_filter_mode,
        last_refresh=group.last_refresh.isoformat() if group.last_refresh else None,
        stream_count=group.stream_count,
        matched_count=group.matched_count,
        filtered_stale=group.filtered_stale,
        filtered_include_regex=group.filtered_include_regex,
        filtered_exclude_regex=group.filtered_exclude_regex,
        filtered_not_event=group.filtered_not_event,
        filtered_team=group.filtered_team,
        failed_count=group.failed_count,
        streams_excluded=group.streams_excluded,
        excluded_event_final=group.excluded_event_final,
        excluded_event_past=group.excluded_event_past,
        excluded_before_window=group.excluded_before_window,
        excluded_league_not_included=group.excluded_league_not_included,
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

        # Check for duplicate name if changing (within same M3U account)
        # Determine the target account_id (could be changing)
        target_account_id = (
            None
            if request.clear_m3u_account_id
            else request.m3u_account_id
            if request.m3u_account_id is not None
            else group.m3u_account_id
        )
        target_name = request.name if request.name else group.name
        if target_name != group.name or target_account_id != group.m3u_account_id:
            existing = get_group_by_name(conn, target_name, target_account_id)
            if existing and existing.id != group_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Group with name '{target_name}' already exists for this M3U account",
                )

        update_group(
            conn,
            group_id,
            name=request.name,
            display_name=request.display_name,
            leagues=request.leagues,
            group_mode=request.group_mode,
            parent_group_id=request.parent_group_id,
            template_id=request.template_id,
            channel_start_number=request.channel_start_number,
            channel_group_id=request.channel_group_id,
            channel_group_mode=request.channel_group_mode,
            channel_profile_ids=request.channel_profile_ids,
            stream_profile_id=request.stream_profile_id,
            stream_timezone=request.stream_timezone,
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
            custom_regex_league=request.custom_regex_league,
            custom_regex_league_enabled=request.custom_regex_league_enabled,
            skip_builtin_filter=request.skip_builtin_filter,
            include_teams=[t.model_dump() for t in request.include_teams]
            if request.include_teams is not None
            else None,
            exclude_teams=[t.model_dump() for t in request.exclude_teams]
            if request.exclude_teams is not None
            else None,
            team_filter_mode=request.team_filter_mode,
            channel_sort_order=request.channel_sort_order,
            overlap_handling=request.overlap_handling,
            enabled=request.enabled,
            clear_display_name=request.clear_display_name,
            clear_parent_group_id=request.clear_parent_group_id,
            clear_template=request.clear_template,
            clear_channel_start_number=request.clear_channel_start_number,
            clear_channel_group_id=request.clear_channel_group_id,
            clear_channel_profile_ids=request.clear_channel_profile_ids,
            clear_stream_profile_id=request.clear_stream_profile_id,
            clear_stream_timezone=request.clear_stream_timezone,
            clear_m3u_group_id=request.clear_m3u_group_id,
            clear_m3u_group_name=request.clear_m3u_group_name,
            clear_m3u_account_id=request.clear_m3u_account_id,
            clear_m3u_account_name=request.clear_m3u_account_name,
            clear_stream_include_regex=request.clear_stream_include_regex,
            clear_stream_exclude_regex=request.clear_stream_exclude_regex,
            clear_custom_regex_teams=request.clear_custom_regex_teams,
            clear_custom_regex_date=request.clear_custom_regex_date,
            clear_custom_regex_time=request.clear_custom_regex_time,
            clear_custom_regex_league=request.clear_custom_regex_league,
            clear_include_teams=request.clear_include_teams,
            clear_exclude_teams=request.clear_exclude_teams,
        )

        # Clean up XMLTV content when group is disabled
        if request.enabled is False:
            conn.execute("DELETE FROM event_epg_xmltv WHERE group_id = ?", (group_id,))

        group = get_group(conn, group_id)
        channel_count = get_group_channel_count(conn, group_id)

    logger.info("[UPDATED] Event group id=%d", group_id)

    return GroupResponse(
        id=group.id,
        name=group.name,
        display_name=group.display_name,
        leagues=group.leagues,
        group_mode=group.group_mode,
        parent_group_id=group.parent_group_id,
        template_id=group.template_id,
        channel_start_number=group.channel_start_number,
        channel_group_id=group.channel_group_id,
        channel_group_mode=group.channel_group_mode,
        channel_profile_ids=group.channel_profile_ids,
        stream_profile_id=group.stream_profile_id,
        stream_timezone=group.stream_timezone,
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
        include_teams=[TeamFilterEntry(**t) for t in group.include_teams]
        if group.include_teams
        else None,
        exclude_teams=[TeamFilterEntry(**t) for t in group.exclude_teams]
        if group.exclude_teams
        else None,
        team_filter_mode=group.team_filter_mode,
        last_refresh=group.last_refresh.isoformat() if group.last_refresh else None,
        stream_count=group.stream_count,
        matched_count=group.matched_count,
        filtered_stale=group.filtered_stale,
        filtered_include_regex=group.filtered_include_regex,
        filtered_exclude_regex=group.filtered_exclude_regex,
        filtered_not_event=group.filtered_not_event,
        filtered_team=group.filtered_team,
        failed_count=group.failed_count,
        streams_excluded=group.streams_excluded,
        excluded_event_final=group.excluded_event_final,
        excluded_event_past=group.excluded_event_past,
        excluded_before_window=group.excluded_before_window,
        excluded_league_not_included=group.excluded_league_not_included,
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

    logger.info(
        "[DELETED] Event group id=%d name=%s channels=%d", group_id, group.name, channel_count
    )

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


@router.post("/{group_id}/cache/clear", response_model=ClearCacheResponse)
def clear_group_match_cache(group_id: int):
    """Clear stream match cache for a specific event group.

    Forces re-matching on next EPG generation run. Useful when matching
    algorithm changes or cached matches are incorrect.
    """
    from teamarr.consumers.stream_match_cache import StreamMatchCache
    from teamarr.database.groups import get_group

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

    cache = StreamMatchCache(get_db)
    entries_cleared = cache.clear_group(group_id)

    logger.info(
        "[CACHE_CLEAR] group_id=%d name=%s entries=%d", group_id, group.name, entries_cleared
    )

    return ClearCacheResponse(
        success=True,
        group_id=group_id,
        group_name=group.name,
        entries_cleared=entries_cleared,
    )


@router.post("/cache/clear", response_model=ClearCacheResponse)
def clear_groups_match_cache(request: ClearCacheRequest):
    """Clear stream match cache for multiple event groups.

    Forces re-matching on next EPG generation run for all specified groups.
    """
    from teamarr.consumers.stream_match_cache import StreamMatchCache
    from teamarr.database.groups import get_group

    cache = StreamMatchCache(get_db)
    results: list[ClearCacheGroupResult] = []
    total_cleared = 0

    with get_db() as conn:
        for group_id in request.group_ids:
            group = get_group(conn, group_id)
            if not group:
                continue

            cleared = cache.clear_group(group_id)
            results.append(ClearCacheGroupResult(group_id=group_id, cleared=cleared))
            total_cleared += cleared

    logger.info("[CACHE_CLEAR_BULK] groups=%d total_cleared=%d", len(results), total_cleared)

    return ClearCacheResponse(
        success=True,
        total_cleared=total_cleared,
        by_group=results,
    )


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
    filtered_stale: int = 0
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
        filtered_stale=result.filtered_stale,
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


class RawStreamModel(BaseModel):
    """Stream info for regex testing with builtin filter status."""

    stream_id: int
    stream_name: str
    # Builtin filter results (None if passes, string describing why filtered)
    builtin_filtered: str | None = None


class RawStreamsResponse(BaseModel):
    """Response for raw streams endpoint."""

    group_id: int
    group_name: str
    total: int
    streams: list[RawStreamModel]


@router.get("/{group_id}/streams/raw", response_model=RawStreamsResponse)
def get_raw_streams(group_id: int):
    """Get raw stream names for a group without filtering or matching.

    Returns minimal stream data (id + name) for regex testing in the UI.
    Fetches directly from Dispatcharr without running the matching pipeline.
    """
    from teamarr.database.groups import get_group
    from teamarr.dispatcharr import get_factory

    with get_db() as conn:
        group = get_group(conn, group_id)
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

    factory = get_factory(get_db)
    if not factory:
        return RawStreamsResponse(
            group_id=group_id,
            group_name=group.name,
            total=0,
            streams=[],
        )

    conn = factory.get_connection()
    if not conn or not conn.m3u:
        return RawStreamsResponse(
            group_id=group_id,
            group_name=group.name,
            total=0,
            streams=[],
        )

    raw = conn.m3u.list_streams(
        group_id=group.m3u_group_id,
        account_id=group.m3u_account_id,
    )

    from teamarr.api.routes import natural_sort_key
    from teamarr.services.stream_filter import (
        UNSUPPORTED_SPORTS,
        detect_sport_hint,
        is_event_stream,
        is_placeholder,
    )

    def get_builtin_filter_reason(name: str) -> str | None:
        """Check all builtin filters and return reason if filtered."""
        if is_placeholder(name):
            return "placeholder"
        sport = detect_sport_hint(name)
        if sport and sport in UNSUPPORTED_SPORTS:
            return f"unsupported_sport:{sport}"
        if not is_event_stream(name):
            return "not_event"
        return None

    streams = sorted(
        (
            RawStreamModel(
                stream_id=s.id,
                stream_name=s.name,
                builtin_filtered=get_builtin_filter_reason(s.name),
            )
            for s in raw
        ),
        key=lambda s: natural_sort_key(s.stream_name),
    )

    return RawStreamsResponse(
        group_id=group_id,
        group_name=group.name,
        total=len(streams),
        streams=streams,
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
    from teamarr.database.settings import get_display_settings
    from teamarr.utilities.xmltv import merge_xmltv_content

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

        display_settings = get_display_settings(conn)

    combined = merge_xmltv_content(
        [row["xmltv_content"] for row in rows],
        generator_name=display_settings.xmltv_generator_name,
        generator_url=display_settings.xmltv_generator_url,
    )

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
