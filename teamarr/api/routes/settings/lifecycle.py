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
        "manual",
    }
    valid_delete = {
        "stream_removed",
        "same_day",
        "day_after",
        "2_days_after",
        "3_days_after",
        "1_week_after",
        "manual",
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

    with get_db() as conn:
        settings = get_scheduler_settings(conn)

    return SchedulerSettingsModel(
        enabled=settings.enabled,
        interval_minutes=settings.interval_minutes,
    )


@router.get("/scheduler/status", response_model=SchedulerStatusResponse)
def get_scheduler_status():
    """Get current scheduler status."""
    from teamarr.consumers import get_scheduler_status

    scheduler_status = get_scheduler_status()

    return SchedulerStatusResponse(
        running=scheduler_status.get("running", False),
        last_run=scheduler_status.get("last_run"),
        interval_minutes=scheduler_status.get("interval_minutes"),
    )


@router.post("/scheduler/run")
def trigger_scheduler_run() -> dict:
    """Manually trigger a scheduler run."""
    from teamarr.consumers import LifecycleScheduler
    from teamarr.dispatcharr import get_dispatcharr_client

    try:
        client = get_dispatcharr_client(get_db)
    except Exception:
        client = None

    scheduler = LifecycleScheduler(
        db_factory=get_db,
        dispatcharr_client=client,
    )

    results = scheduler.run_once()

    return {
        "success": True,
        "results": results,
    }
