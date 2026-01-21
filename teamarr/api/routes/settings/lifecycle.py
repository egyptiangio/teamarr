"""Lifecycle and scheduler settings endpoints."""

from fastapi import APIRouter, HTTPException, status

from teamarr.database import get_db

from .models import (
    LifecycleSettingsModel,
    SchedulerSettingsModel,
    SchedulerStatusResponse,
)

router = APIRouter()


# =============================================================================
# LIFECYCLE SETTINGS
# =============================================================================


@router.get("/settings/lifecycle", response_model=LifecycleSettingsModel)
def get_lifecycle_settings():
    """Get channel lifecycle settings."""
    from teamarr.database.settings import get_lifecycle_settings

    with get_db() as conn:
        settings = get_lifecycle_settings(conn)

    return LifecycleSettingsModel(
        channel_create_timing=settings.channel_create_timing,
        channel_delete_timing=settings.channel_delete_timing,
        channel_range_start=settings.channel_range_start,
        channel_range_end=settings.channel_range_end,
    )


@router.put("/settings/lifecycle", response_model=LifecycleSettingsModel)
def update_lifecycle_settings(update: LifecycleSettingsModel):
    """Update channel lifecycle settings."""
    from teamarr.database.settings import (
        get_lifecycle_settings,
        update_lifecycle_settings,
    )

    # Validate timing values
    valid_create = {
        "stream_available",
        "same_day",
        "day_before",
        "2_days_before",
        "3_days_before",
        "1_week_before",
    }
    valid_delete = {
        "stream_removed",
        "6_hours_after",
        "same_day",
        "day_after",
        "2_days_after",
        "3_days_after",
        "1_week_after",
    }

    if update.channel_create_timing not in valid_create:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid channel_create_timing. Valid: {valid_create}",
        )
    if update.channel_delete_timing not in valid_delete:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid channel_delete_timing. Valid: {valid_delete}",
        )

    with get_db() as conn:
        update_lifecycle_settings(
            conn,
            channel_create_timing=update.channel_create_timing,
            channel_delete_timing=update.channel_delete_timing,
            channel_range_start=update.channel_range_start,
            channel_range_end=update.channel_range_end,
        )

    with get_db() as conn:
        settings = get_lifecycle_settings(conn)

    return LifecycleSettingsModel(
        channel_create_timing=settings.channel_create_timing,
        channel_delete_timing=settings.channel_delete_timing,
        channel_range_start=settings.channel_range_start,
        channel_range_end=settings.channel_range_end,
    )


# =============================================================================
# SCHEDULER SETTINGS & CONTROL
# =============================================================================


@router.get("/settings/scheduler", response_model=SchedulerSettingsModel)
def get_scheduler_settings():
    """Get scheduler settings."""
    from teamarr.database.settings import get_scheduler_settings

    with get_db() as conn:
        settings = get_scheduler_settings(conn)

    return SchedulerSettingsModel(
        enabled=settings.enabled,
        interval_minutes=settings.interval_minutes,
    )


@router.put("/settings/scheduler", response_model=SchedulerSettingsModel)
def update_scheduler_settings(update: SchedulerSettingsModel):
    """Update scheduler settings."""
    from teamarr.consumers.scheduler import (
        start_lifecycle_scheduler,
        stop_lifecycle_scheduler,
    )
    from teamarr.database.settings import (
        get_scheduler_settings,
        update_scheduler_settings,
    )

    if update.interval_minutes < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="interval_minutes must be at least 1",
        )

    with get_db() as conn:
        update_scheduler_settings(
            conn,
            enabled=update.enabled,
            interval_minutes=update.interval_minutes,
        )

    # Apply scheduler state change immediately
    stop_lifecycle_scheduler()
    if update.enabled:
        start_lifecycle_scheduler(get_db)

    with get_db() as conn:
        settings = get_scheduler_settings(conn)

    return SchedulerSettingsModel(
        enabled=settings.enabled,
        interval_minutes=settings.interval_minutes,
    )


@router.get("/scheduler/status", response_model=SchedulerStatusResponse)
def get_scheduler_status():
    """Get current scheduler status."""
    from teamarr.services import create_scheduler_service

    scheduler_service = create_scheduler_service(get_db)
    status = scheduler_service.get_status()

    return SchedulerStatusResponse(
        running=status.running,
        cron_expression=status.cron_expression,
        last_run=status.last_run.isoformat() if status.last_run else None,
        next_run=status.next_run.isoformat() if status.next_run else None,
    )


@router.post("/scheduler/run")
def trigger_scheduler_run() -> dict:
    """Manually trigger a scheduler run."""
    from teamarr.dispatcharr import get_dispatcharr_client
    from teamarr.services import create_scheduler_service

    try:
        client = get_dispatcharr_client(get_db)
    except Exception:
        client = None

    scheduler_service = create_scheduler_service(get_db, client)
    result = scheduler_service.run_once()

    return {
        "success": True,
        "results": {
            "started_at": result.started_at.isoformat() if result.started_at else None,
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            "epg_generation": result.epg_generation,
            "deletions": result.deletions,
            "reconciliation": result.reconciliation,
            "cleanup": result.cleanup,
        },
    }
