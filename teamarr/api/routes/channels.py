"""Channel management endpoints.

Provides REST API for:
- Listing managed channels
- Manual channel operations (delete, sync)
- Reconciliation (detect and fix issues)
- Lifecycle sync (create/delete based on timing)
"""

import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from teamarr.database import get_db

logger = logging.getLogger(__name__)


def _safe_isoformat(value: Any) -> str | None:
    """Safely convert a date/datetime value to ISO format string.

    Handles cases where the value might already be a string from the database.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


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
    deleted_at: str | None = None


class ManagedChannelListResponse(BaseModel):
    """List of managed channels."""

    channels: list[ManagedChannelModel]
    total: int


class ReconciliationRequest(BaseModel):
    """Request for reconciliation."""

    auto_fix: bool = Field(default=False, description="Automatically fix issues")
    group_ids: list[int] | None = Field(default=None, description="Limit to specific groups")


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
                channel_number=str(c.channel_number) if c.channel_number is not None else None,
                logo_url=c.logo_url,
                dispatcharr_channel_id=c.dispatcharr_channel_id,
                dispatcharr_uuid=c.dispatcharr_uuid,
                home_team=c.home_team,
                away_team=c.away_team,
                event_date=_safe_isoformat(c.event_date),
                event_name=c.event_name,
                league=c.league,
                sport=c.sport,
                scheduled_delete_at=_safe_isoformat(c.scheduled_delete_at),
                sync_status=c.sync_status,
                created_at=_safe_isoformat(c.created_at),
                updated_at=_safe_isoformat(c.updated_at),
                deleted_at=_safe_isoformat(c.deleted_at),
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
        channel_number=str(channel.channel_number) if channel.channel_number is not None else None,
        logo_url=channel.logo_url,
        dispatcharr_channel_id=channel.dispatcharr_channel_id,
        dispatcharr_uuid=channel.dispatcharr_uuid,
        home_team=channel.home_team,
        away_team=channel.away_team,
        event_date=_safe_isoformat(channel.event_date),
        event_name=channel.event_name,
        league=channel.league,
        sport=channel.sport,
        scheduled_delete_at=_safe_isoformat(channel.scheduled_delete_at),
        sync_status=channel.sync_status,
        created_at=_safe_isoformat(channel.created_at),
        updated_at=_safe_isoformat(channel.updated_at),
    )


@router.delete("/managed/{channel_id}", response_model=DeleteResponse)
def delete_managed_channel(channel_id: int):
    """Delete a managed channel.

    Removes the channel from Dispatcharr (if configured) and marks as deleted in DB.
    """
    from teamarr.database.channels import get_managed_channel
    from teamarr.dispatcharr import get_dispatcharr_client
    from teamarr.services import create_channel_service, create_default_service

    with get_db() as conn:
        channel = get_managed_channel(conn, channel_id)
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel {channel_id} not found",
            )

    # Get Dispatcharr client (may be None if not configured)
    try:
        client = get_dispatcharr_client(get_db)
    except Exception:
        client = None

    # Get sports service for template resolution
    sports_service = create_default_service()
    channel_service = create_channel_service(get_db, sports_service, client)

    with get_db() as conn:
        success = channel_service.delete_channel(conn, channel_id, reason="manual")
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
    from teamarr.database.settings import get_dispatcharr_settings
    from teamarr.dispatcharr import get_dispatcharr_client
    from teamarr.services import create_channel_service, create_default_service

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    # Get Dispatcharr client
    try:
        client = get_dispatcharr_client(get_db)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect to Dispatcharr: {e}",
        ) from e

    if not client:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr connection not available",
        )

    # Get sports service for template resolution
    sports_service = create_default_service()
    channel_service = create_channel_service(get_db, sports_service, client)

    # Process scheduled deletions
    result = channel_service.process_scheduled_deletions()

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
    from teamarr.database.settings import get_dispatcharr_settings
    from teamarr.dispatcharr import get_dispatcharr_client
    from teamarr.services import create_channel_service, create_default_service

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.enabled:
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

    # Get Dispatcharr client
    try:
        client = get_dispatcharr_client(get_db)
    except Exception:
        client = None

    sports_service = create_default_service()
    channel_service = create_channel_service(get_db, sports_service, client)

    # Run detect-only
    result = channel_service.reconcile(auto_fix=False, group_ids=parsed_group_ids)

    return ReconciliationResponse(
        started_at=result.started_at.isoformat() if result.started_at else None,
        completed_at=result.completed_at.isoformat() if result.completed_at else None,
        summary=ReconciliationSummary(
            orphan_teamarr=result.summary.orphan_teamarr,
            orphan_dispatcharr=result.summary.orphan_dispatcharr,
            duplicate=result.summary.duplicates,
            drift=result.summary.drift,
        ),
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
        issues_fixed=[],
        issues_skipped=[],
        errors=result.errors,
    )


@router.post("/reconciliation/fix", response_model=ReconciliationResponse)
def fix_reconciliation(request: ReconciliationRequest):
    """Run reconciliation with optional auto-fix.

    Detects issues and optionally fixes them based on settings.
    """
    from teamarr.database.settings import get_dispatcharr_settings
    from teamarr.dispatcharr import get_dispatcharr_client
    from teamarr.services import create_channel_service, create_default_service

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    # Get Dispatcharr client
    try:
        client = get_dispatcharr_client(get_db)
    except Exception as e:
        if request.auto_fix:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Cannot auto-fix without Dispatcharr connection: {e}",
            ) from e
        client = None

    sports_service = create_default_service()
    channel_service = create_channel_service(get_db, sports_service, client)

    # Run reconciliation
    result = channel_service.reconcile(
        auto_fix=request.auto_fix,
        group_ids=request.group_ids,
    )

    return ReconciliationResponse(
        started_at=result.started_at.isoformat() if result.started_at else None,
        completed_at=result.completed_at.isoformat() if result.completed_at else None,
        summary=ReconciliationSummary(
            orphan_teamarr=result.summary.orphan_teamarr,
            orphan_dispatcharr=result.summary.orphan_dispatcharr,
            duplicate=result.summary.duplicates,
            drift=result.summary.drift,
        ),
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
        issues_fixed=[],
        issues_skipped=[],
        errors=result.errors,
    )


@router.get("/pending-deletions")
def get_pending_deletions() -> dict:
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
                "scheduled_delete_at": _safe_isoformat(c.scheduled_delete_at),
                "dispatcharr_channel_id": c.dispatcharr_channel_id,
            }
            for c in channels
        ],
    }


@router.get("/history/{channel_id}")
def get_channel_history(
    channel_id: int,
    limit: int = Query(50, ge=1, le=500, description="Maximum records to return"),
) -> dict:
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


@router.delete("/dispatcharr/{channel_id}", response_model=DeleteResponse)
def delete_dispatcharr_channel(channel_id: int):
    """Delete a channel directly from Dispatcharr by its Dispatcharr ID.

    Use this for orphan_dispatcharr channels that exist in Dispatcharr
    but aren't tracked by Teamarr. This bypasses the managed channels table.
    """
    from teamarr.database.settings import get_dispatcharr_settings
    from teamarr.dispatcharr import get_dispatcharr_client
    from teamarr.dispatcharr.managers.channels import ChannelManager

    with get_db() as conn:
        settings = get_dispatcharr_settings(conn)

    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr not configured",
        )

    try:
        client = get_dispatcharr_client(get_db)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect to Dispatcharr: {e}",
        ) from e

    if not client:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr connection not available",
        )

    manager = ChannelManager(client)

    # First verify the channel exists
    channel = manager.get_channel(channel_id, use_cache=False)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel {channel_id} not found in Dispatcharr",
        )

    # Delete it
    result = manager.delete_channel(channel_id)

    if result.success:
        return DeleteResponse(
            success=True,
            message=f"Channel '{channel.name}' (ID: {channel_id}) deleted from Dispatcharr",
        )
    else:
        return DeleteResponse(
            success=False,
            message=f"Failed to delete channel: {result.error}",
        )


# =============================================================================
# RESET ALL TEAMARR CHANNELS
# =============================================================================


class ResetChannelInfo(BaseModel):
    """Info about a Teamarr channel in Dispatcharr."""

    dispatcharr_channel_id: int
    uuid: str | None = None
    tvg_id: str
    channel_name: str
    channel_number: str | None = None
    stream_count: int = 0


class ResetPreviewResponse(BaseModel):
    """Response for reset preview."""

    success: bool
    channel_count: int
    channels: list[ResetChannelInfo]


class ResetExecuteResponse(BaseModel):
    """Response for reset execution."""

    success: bool
    deleted_count: int
    error_count: int
    errors: list[str] = Field(default_factory=list)


@router.get("/reset", response_model=ResetPreviewResponse)
def preview_reset_channels():
    """Preview all Teamarr-created channels that would be deleted by reset.

    Returns all channels in Dispatcharr with teamarr-event-* tvg_id,
    regardless of whether they're tracked in managed_channels.
    """
    from teamarr.dispatcharr import ChannelManager, get_dispatcharr_client

    client = get_dispatcharr_client(get_db)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr connection not available",
        )

    manager = ChannelManager(client)
    all_channels = manager.get_channels()

    # Find ALL teamarr-event channels
    teamarr_channels = []
    for ch in all_channels:
        tvg_id = ch.tvg_id or ""
        if tvg_id.startswith("teamarr-event-"):
            teamarr_channels.append(
                ResetChannelInfo(
                    dispatcharr_channel_id=ch.id,
                    uuid=ch.uuid,
                    tvg_id=tvg_id,
                    channel_name=ch.name,
                    channel_number=ch.channel_number,
                    stream_count=len(ch.streams) if ch.streams else 0,
                )
            )

    return ResetPreviewResponse(
        success=True,
        channel_count=len(teamarr_channels),
        channels=teamarr_channels,
    )


@router.post("/reset", response_model=ResetExecuteResponse)
def execute_reset_channels():
    """Delete ALL Teamarr-created channels from Dispatcharr.

    This is a destructive operation that removes all channels with
    teamarr-event-* tvg_id. Also marks all managed_channels as deleted.
    """
    from teamarr.dispatcharr import ChannelManager, get_dispatcharr_client

    client = get_dispatcharr_client(get_db)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dispatcharr connection not available",
        )

    manager = ChannelManager(client)
    all_channels = manager.get_channels()

    deleted_count = 0
    errors: list[str] = []

    # Find and delete ALL teamarr-event channels
    for ch in all_channels:
        tvg_id = ch.tvg_id or ""
        if not tvg_id.startswith("teamarr-event-"):
            continue

        result = manager.delete_channel(ch.id)
        if result.success:
            deleted_count += 1
        else:
            errors.append(f"Failed to delete {ch.name}: {result.error}")

    # Mark all managed_channels as deleted
    import logging

    logger = logging.getLogger(__name__)

    with get_db() as conn:
        # Count before
        cursor = conn.execute("SELECT COUNT(*) FROM managed_channels WHERE deleted_at IS NULL")
        count_before = cursor.fetchone()[0]

        cursor = conn.execute(
            """UPDATE managed_channels
               SET deleted_at = CURRENT_TIMESTAMP
               WHERE deleted_at IS NULL"""
        )
        rows_updated = cursor.rowcount
        conn.commit()

        logger.info(
            f"Reset: marked {rows_updated} managed_channels as deleted "
            f"(had {count_before} active records before)"
        )

    return ResetExecuteResponse(
        success=len(errors) == 0,
        deleted_count=deleted_count,
        error_count=len(errors),
        errors=errors,
    )
