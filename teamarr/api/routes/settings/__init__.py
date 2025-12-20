"""Settings management endpoints.

Provides REST API for:
- Reading and updating application settings
- Testing Dispatcharr connection
- Scheduler status and control
"""

from fastapi import APIRouter

from teamarr.database import get_db

from .dispatcharr import router as dispatcharr_router
from .display import router as display_router
from .epg import router as epg_router
from .lifecycle import router as lifecycle_router
from .models import (
    AllSettingsModel,
    DispatcharrSettingsModel,
    DisplaySettingsModel,
    DurationSettingsModel,
    EPGSettingsModel,
    LifecycleSettingsModel,
    ReconciliationSettingsModel,
    SchedulerSettingsModel,
)

# Main router that includes all sub-routers
router = APIRouter()

# Include sub-routers
router.include_router(dispatcharr_router)
router.include_router(lifecycle_router)
router.include_router(epg_router)
router.include_router(display_router)


# =============================================================================
# MAIN SETTINGS ENDPOINT
# =============================================================================


@router.get("/settings", response_model=AllSettingsModel)
def get_settings():
    """Get all application settings."""
    from teamarr.database.settings import get_all_settings

    with get_db() as conn:
        settings = get_all_settings(conn)

    return AllSettingsModel(
        dispatcharr=DispatcharrSettingsModel(
            enabled=settings.dispatcharr.enabled,
            url=settings.dispatcharr.url,
            username=settings.dispatcharr.username,
            password="********" if settings.dispatcharr.password else None,
            epg_id=settings.dispatcharr.epg_id,
        ),
        lifecycle=LifecycleSettingsModel(
            channel_create_timing=settings.lifecycle.channel_create_timing,
            channel_delete_timing=settings.lifecycle.channel_delete_timing,
            channel_range_start=settings.lifecycle.channel_range_start,
            channel_range_end=settings.lifecycle.channel_range_end,
        ),
        reconciliation=ReconciliationSettingsModel(
            reconcile_on_epg_generation=settings.reconciliation.reconcile_on_epg_generation,
            reconcile_on_startup=settings.reconciliation.reconcile_on_startup,
            auto_fix_orphan_teamarr=settings.reconciliation.auto_fix_orphan_teamarr,
            auto_fix_orphan_dispatcharr=settings.reconciliation.auto_fix_orphan_dispatcharr,
            auto_fix_duplicates=settings.reconciliation.auto_fix_duplicates,
            default_duplicate_event_handling=settings.reconciliation.default_duplicate_event_handling,
            channel_history_retention_days=settings.reconciliation.channel_history_retention_days,
        ),
        scheduler=SchedulerSettingsModel(
            enabled=settings.scheduler.enabled,
            interval_minutes=settings.scheduler.interval_minutes,
        ),
        epg=EPGSettingsModel(
            team_schedule_days_ahead=settings.epg.team_schedule_days_ahead,
            event_match_days_ahead=settings.epg.event_match_days_ahead,
            epg_output_days_ahead=settings.epg.epg_output_days_ahead,
            epg_lookback_hours=settings.epg.epg_lookback_hours,
            epg_timezone=settings.epg.epg_timezone,
            epg_output_path=settings.epg.epg_output_path,
            include_final_events=settings.epg.include_final_events,
            midnight_crossover_mode=settings.epg.midnight_crossover_mode,
            cron_expression=settings.epg.cron_expression,
        ),
        durations=DurationSettingsModel(
            default=settings.durations.default,
            basketball=settings.durations.basketball,
            football=settings.durations.football,
            hockey=settings.durations.hockey,
            baseball=settings.durations.baseball,
            soccer=settings.durations.soccer,
            mma=settings.durations.mma,
            rugby=settings.durations.rugby,
            boxing=settings.durations.boxing,
            tennis=settings.durations.tennis,
            golf=settings.durations.golf,
            racing=settings.durations.racing,
            cricket=settings.durations.cricket,
        ),
        display=DisplaySettingsModel(
            time_format=settings.display.time_format,
            show_timezone=settings.display.show_timezone,
            channel_id_format=settings.display.channel_id_format,
            xmltv_generator_name=settings.display.xmltv_generator_name,
            xmltv_generator_url=settings.display.xmltv_generator_url,
        ),
        epg_generation_counter=settings.epg_generation_counter,
        schema_version=settings.schema_version,
    )


# Export models for external use
__all__ = [
    "router",
    "AllSettingsModel",
    "DispatcharrSettingsModel",
    "DisplaySettingsModel",
    "DurationSettingsModel",
    "EPGSettingsModel",
    "LifecycleSettingsModel",
    "ReconciliationSettingsModel",
    "SchedulerSettingsModel",
]
