"""Channel management endpoints.

Provides REST API for:
- Listing managed channels
- Manual channel operations (delete, sync)
- Reconciliation (detect and fix issues)
- Lifecycle sync (create/delete based on timing)
"""


from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from teamarr.database import get_db

router = APIRouter()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class ManagedChannelModel(BaseModel):
    """Managed channel response model."""

    id: int
    event_epg_group_id: int
    event_id: str
    event_provider: str
    tvg_id: str
    channel_name: str
    channel_number: str | None = None
    logo_url: str | None = None

    dispatcharr_channel_id: int | None = None
    dispatcharr_uuid: str | None = None

    home_team: str | None = None
    away_team: str | None = None
    event_date: str | None = None
    event_name: str | None = None
    league: str | None = None
    sport: str | None = None

    scheduled_delete_at: str | None = None
    sync_status: str = "pending"

    created_at: str | None = None
    updated_at: str | None = None


class ManagedChannelListResponse(BaseModel):
    """List of managed channels."""

    channels: list[ManagedChannelModel]
    total: int


class ReconciliationRequest(BaseModel):
    """Request for reconciliation."""

    auto_fix: bool = Field(default=False, description="Automatically fix issues")
    group_ids: list[int] | None = Field(
        default=None, description="Limit to specific groups"
    )


class ReconciliationIssueModel(BaseModel):
    """Single reconciliation issue."""

    issue_type: str
    severity: str
    managed_channel_id: int | None = None
    dispatcharr_channel_id: int | None = None
    channel_name: str | None = None
    event_id: str | None = None
    details: dict = {}
    suggested_action: str | None = None
    auto_fixable: bool = False


class ReconciliationSummary(BaseModel):
    """Reconciliation summary."""

    orphan_teamarr: int = 0
    orphan_dispatcharr: int = 0
    duplicate: int = 0
    drift: int = 0
    total: int = 0
    fixed: int = 0
    skipped: int = 0
    errors: int = 0


class ReconciliationResponse(BaseModel):
    """Reconciliation response."""

    started_at: str | None = None
    completed_at: str | None = None
    summary: ReconciliationSummary
    issues_found: list[ReconciliationIssueModel] = []
    issues_fixed: list[dict] = []
    issues_skipped: list[dict] = []
    errors: list[str] = []


class SyncResponse(BaseModel):
    """Channel sync response."""

    created_count: int = 0
    existing_count: int = 0
    skipped_count: int = 0
    deleted_count: int = 0
    error_count: int = 0
    created: list[dict] = []
    errors: list[dict] = []


class DeleteResponse(BaseModel):
    """Channel delete response."""

    success: bool
    message: str


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("/managed", response_model=ManagedChannelListResponse)
def list_managed_channels(
    group_id: int | None = Query(None, description="Filter by event EPG group"),
    include_deleted: bool = Query(False, description="Include deleted channels"),
):
    """List all managed channels.

    Returns channels tracked by Teamarr for lifecycle management.
    """
    from teamarr.database.channels import (
        get_all_managed_channels,
        get_managed_channels_for_group,
    )

    with get_db() as conn:
        if group_id:
            channels = get_managed_channels_for_group(
                conn, group_id, include_deleted=include_deleted
            )
        else:
            channels = get_all_managed_channels(conn, include_deleted=include_deleted)

    return ManagedChannelListResponse(
        channels=[
            ManagedChannelModel(
                id=c.id,
                event_epg_group_id=c.event_epg_group_id,
                event_id=c.event_id,
                event_provider=c.event_provider,
                tvg_id=c.tvg_id,
                channel_name=c.channel_name,
                channel_number=c.channel_number,
                logo_url=c.logo_url,
                dispatcharr_channel_id=c.dispatcharr_channel_id,
                dispatcharr_uuid=c.dispatcharr_uuid,
                home_team=c.home_team,
                away_team=c.away_team,
                event_date=c.event_date.isoformat() if c.event_date else None,
                event_name=c.event_name,
                league=c.league,
                sport=c.sport,
                scheduled_delete_at=(
                    c.scheduled_delete_at.isoformat() if c.scheduled_delete_at else None
                ),
                sync_status=c.sync_status,
                created_at=c.created_at.isoformat() if c.created_at else None,
                updated_at=c.updated_at.isoformat() if c.updated_at else None,
            )
            for c in channels
        ],
        total=len(channels),
    )


@router.get("/managed/{channel_id}", response_model=ManagedChannelModel)
def get_managed_channel(channel_id: int):
    """Get a single managed channel by ID."""
    from teamarr.database.channels import get_managed_channel

    with get_db() as conn:
        channel = get_managed_channel(conn, channel_id)

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel {channel_id} not found",
        )

    return ManagedChannelModel(
        id=channel.id,
        event_epg_group_id=channel.event_epg_group_id,
        event_id=channel.event_id,
        event_provider=channel.event_provider,
        tvg_id=channel.tvg_id,
        channel_name=channel.channel_name,
        channel_number=channel.channel_number,
        logo_url=channel.logo_url,
        dispatcharr_channel_id=channel.dispatcharr_channel_id,
        dispatcharr_uuid=channel.dispatcharr_uuid,
        home_team=channel.home_team,
        away_team=channel.away_team,
        event_date=channel.event_date.isoformat() if channel.event_date else None,
        event_name=channel.event_name,
        league=channel.league,
        sport=channel.sport,
        scheduled_delete_at=(
            channel.scheduled_delete_at.isoformat()
            if channel.scheduled_delete_at
            else None
        ),
        sync_status=channel.sync_status,
        created_at=channel.created_at.isoformat() if channel.created_at else None,
        updated_at=channel.updated_at.isoformat() if channel.updated_at else None,
    )


@router.delete("/managed/{channel_id}", response_model=DeleteResponse)
def delete_managed_channel(channel_id: int):
    """Delete a managed channel.

    Removes the channel from Dispatcharr (if configured) and marks as deleted in DB.
    """
    from teamarr.consumers import create_lifecycle_service
    from teamarr.database.channels import get_managed_channel

    with get_db() as conn:
        channel = get_managed_channel(conn, channel_id)
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel {channel_id} not found",
            )

    # Create lifecycle service without Dispatcharr client
    # (just marks as deleted in DB - no live Dispatcharr connection)
    service = create_lifecycle_service(get_db)

    with get_db() as conn:
        success = service.delete_managed_channel(conn, channel_id, reason="manual")
        conn.commit()

    if success:
        return DeleteResponse(
            success=True,
            message=f"Channel '{channel.channel_name}' deleted",
        )
    else:
        return DeleteResponse(
            success=False,
            message="Failed to delete channel",
        )


@router.post("/sync", response_model=SyncResponse)
def sync_lifecycle():
    """Trigger lifecycle sync.

    Creates channels that are due and deletes expired channels.
    Requires Dispatcharr to be configured.
    """
    from teamarr.consumers import create_lifecycle_service
    from teamarr.database.channels import get_dispatcharr_settings

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    # Create lifecycle service (without live Dispatcharr client for now)
    service = create_lifecycle_service(get_db)

    # Process scheduled deletions
    result = service.process_scheduled_deletions()

    return SyncResponse(
        deleted_count=len(result.deleted),
        error_count=len(result.errors),
        errors=result.errors,
    )


@router.get("/reconciliation/status", response_model=ReconciliationResponse)
def get_reconciliation_status(
    group_ids: str | None = Query(None, description="Comma-separated group IDs"),
):
    """Get reconciliation status (detect only).

    Checks for issues without making any changes.
    """
    from teamarr.consumers import create_reconciler
    from teamarr.database.channels import get_dispatcharr_settings

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    # Parse group IDs
    parsed_group_ids = None
    if group_ids:
        try:
            parsed_group_ids = [int(x.strip()) for x in group_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="group_ids must be comma-separated integers",
            ) from None

    # Create reconciler (without live Dispatcharr client for now)
    reconciler = create_reconciler(get_db)

    # Run detect-only
    result = reconciler.reconcile(auto_fix=False, group_ids=parsed_group_ids)

    return ReconciliationResponse(
        started_at=result.started_at.isoformat() if result.started_at else None,
        completed_at=result.completed_at.isoformat() if result.completed_at else None,
        summary=ReconciliationSummary(**result.summary),
        issues_found=[
            ReconciliationIssueModel(
                issue_type=i.issue_type,
                severity=i.severity,
                managed_channel_id=i.managed_channel_id,
                dispatcharr_channel_id=i.dispatcharr_channel_id,
                channel_name=i.channel_name,
                event_id=i.event_id,
                details=i.details,
                suggested_action=i.suggested_action,
                auto_fixable=i.auto_fixable,
            )
            for i in result.issues_found
        ],
        issues_fixed=result.issues_fixed,
        issues_skipped=result.issues_skipped,
        errors=result.errors,
    )


@router.post("/reconciliation/fix", response_model=ReconciliationResponse)
def fix_reconciliation(request: ReconciliationRequest):
    """Run reconciliation with optional auto-fix.

    Detects issues and optionally fixes them based on settings.
    """
    from teamarr.consumers import create_reconciler
    from teamarr.database.channels import get_dispatcharr_settings

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    # Create reconciler (without live Dispatcharr client for now)
    reconciler = create_reconciler(get_db)

    # Run reconciliation
    result = reconciler.reconcile(
        auto_fix=request.auto_fix,
        group_ids=request.group_ids,
    )

    return ReconciliationResponse(
        started_at=result.started_at.isoformat() if result.started_at else None,
        completed_at=result.completed_at.isoformat() if result.completed_at else None,
        summary=ReconciliationSummary(**result.summary),
        issues_found=[
            ReconciliationIssueModel(
                issue_type=i.issue_type,
                severity=i.severity,
                managed_channel_id=i.managed_channel_id,
                dispatcharr_channel_id=i.dispatcharr_channel_id,
                channel_name=i.channel_name,
                event_id=i.event_id,
                details=i.details,
                suggested_action=i.suggested_action,
                auto_fixable=i.auto_fixable,
            )
            for i in result.issues_found
        ],
        issues_fixed=result.issues_fixed,
        issues_skipped=result.issues_skipped,
        errors=result.errors,
    )


@router.get("/pending-deletions")
def get_pending_deletions():
    """Get channels pending deletion.

    Returns channels that are past their scheduled delete time.
    """
    from teamarr.database.channels import get_channels_pending_deletion

    with get_db() as conn:
        channels = get_channels_pending_deletion(conn)

    return {
        "count": len(channels),
        "channels": [
            {
                "id": c.id,
                "channel_name": c.channel_name,
                "tvg_id": c.tvg_id,
                "scheduled_delete_at": (
                    c.scheduled_delete_at.isoformat() if c.scheduled_delete_at else None
                ),
                "dispatcharr_channel_id": c.dispatcharr_channel_id,
            }
            for c in channels
        ],
    }


@router.get("/history/{channel_id}")
def get_channel_history(
    channel_id: int,
    limit: int = Query(50, ge=1, le=500, description="Maximum records to return"),
):
    """Get history for a managed channel."""
    from teamarr.database.channels import get_channel_history, get_managed_channel

    with get_db() as conn:
        channel = get_managed_channel(conn, channel_id)
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel {channel_id} not found",
            )

        history = get_channel_history(conn, channel_id, limit=limit)

    return {
        "channel_id": channel_id,
        "channel_name": channel.channel_name,
        "history": history,
    }
