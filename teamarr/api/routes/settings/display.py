"""Duration, display, and reconciliation settings endpoints."""

from fastapi import APIRouter, HTTPException, status

from teamarr.database import get_db

from .models import (
    DisplaySettingsModel,
    DurationSettingsModel,
    ReconciliationSettingsModel,
)

router = APIRouter()


# =============================================================================
# DURATION SETTINGS
# =============================================================================


@router.get("/settings/durations", response_model=DurationSettingsModel)
def get_duration_settings():
    """Get game duration settings."""
    from teamarr.database.settings import get_all_settings

    with get_db() as conn:
        settings = get_all_settings(conn)

    return DurationSettingsModel(
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
    )


@router.put("/settings/durations", response_model=DurationSettingsModel)
def update_duration_settings(update: DurationSettingsModel):
    """Update game duration settings."""
    from teamarr.database.settings import get_all_settings, update_duration_settings

    with get_db() as conn:
        update_duration_settings(
            conn,
            default=update.default,
            basketball=update.basketball,
            football=update.football,
            hockey=update.hockey,
            baseball=update.baseball,
            soccer=update.soccer,
            mma=update.mma,
            rugby=update.rugby,
            boxing=update.boxing,
            tennis=update.tennis,
            golf=update.golf,
            racing=update.racing,
            cricket=update.cricket,
        )

    with get_db() as conn:
        settings = get_all_settings(conn)

    return DurationSettingsModel(
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
    )


# =============================================================================
# RECONCILIATION SETTINGS
# =============================================================================


@router.get("/settings/reconciliation", response_model=ReconciliationSettingsModel)
def get_reconciliation_settings():
    """Get reconciliation settings."""
    from teamarr.database.settings import get_all_settings

    with get_db() as conn:
        settings = get_all_settings(conn)

    return ReconciliationSettingsModel(
        reconcile_on_epg_generation=settings.reconciliation.reconcile_on_epg_generation,
        reconcile_on_startup=settings.reconciliation.reconcile_on_startup,
        auto_fix_orphan_teamarr=settings.reconciliation.auto_fix_orphan_teamarr,
        auto_fix_orphan_dispatcharr=settings.reconciliation.auto_fix_orphan_dispatcharr,
        auto_fix_duplicates=settings.reconciliation.auto_fix_duplicates,
        default_duplicate_event_handling=settings.reconciliation.default_duplicate_event_handling,
        channel_history_retention_days=settings.reconciliation.channel_history_retention_days,
    )


@router.put("/settings/reconciliation", response_model=ReconciliationSettingsModel)
def update_reconciliation_settings(update: ReconciliationSettingsModel):
    """Update reconciliation settings."""
    from teamarr.database.settings import (
        get_all_settings,
        update_reconciliation_settings,
    )

    valid_modes = {"consolidate", "separate", "ignore"}
    if update.default_duplicate_event_handling not in valid_modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid duplicate handling mode. Valid: {valid_modes}",
        )

    with get_db() as conn:
        update_reconciliation_settings(
            conn,
            reconcile_on_epg_generation=update.reconcile_on_epg_generation,
            reconcile_on_startup=update.reconcile_on_startup,
            auto_fix_orphan_teamarr=update.auto_fix_orphan_teamarr,
            auto_fix_orphan_dispatcharr=update.auto_fix_orphan_dispatcharr,
            auto_fix_duplicates=update.auto_fix_duplicates,
            default_duplicate_event_handling=update.default_duplicate_event_handling,
            channel_history_retention_days=update.channel_history_retention_days,
        )

    with get_db() as conn:
        settings = get_all_settings(conn)

    return ReconciliationSettingsModel(
        reconcile_on_epg_generation=settings.reconciliation.reconcile_on_epg_generation,
        reconcile_on_startup=settings.reconciliation.reconcile_on_startup,
        auto_fix_orphan_teamarr=settings.reconciliation.auto_fix_orphan_teamarr,
        auto_fix_orphan_dispatcharr=settings.reconciliation.auto_fix_orphan_dispatcharr,
        auto_fix_duplicates=settings.reconciliation.auto_fix_duplicates,
        default_duplicate_event_handling=settings.reconciliation.default_duplicate_event_handling,
        channel_history_retention_days=settings.reconciliation.channel_history_retention_days,
    )


# =============================================================================
# DISPLAY SETTINGS
# =============================================================================


@router.get("/settings/display", response_model=DisplaySettingsModel)
def get_display_settings():
    """Get display/formatting settings."""
    from teamarr.database.settings import get_all_settings

    with get_db() as conn:
        settings = get_all_settings(conn)

    # Get tsdb_api_key directly from DB (not in display dataclass)
    with get_db() as conn:
        cursor = conn.execute("SELECT tsdb_api_key FROM settings WHERE id = 1")
        row = cursor.fetchone()
        tsdb_api_key = row["tsdb_api_key"] if row else None

    return DisplaySettingsModel(
        time_format=settings.display.time_format,
        show_timezone=settings.display.show_timezone,
        channel_id_format=settings.display.channel_id_format,
        xmltv_generator_name=settings.display.xmltv_generator_name,
        xmltv_generator_url=settings.display.xmltv_generator_url,
        tsdb_api_key=tsdb_api_key,
    )


@router.put("/settings/display", response_model=DisplaySettingsModel)
def update_display_settings_endpoint(update: DisplaySettingsModel):
    """Update display/formatting settings."""
    from teamarr.config import set_display_settings as set_config_display
    from teamarr.database.settings import get_all_settings, update_display_settings

    valid_time_formats = {"12h", "24h"}
    if update.time_format not in valid_time_formats:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid time_format. Valid: {valid_time_formats}",
        )

    with get_db() as conn:
        update_display_settings(
            conn,
            time_format=update.time_format,
            show_timezone=update.show_timezone,
            channel_id_format=update.channel_id_format,
            xmltv_generator_name=update.xmltv_generator_name,
            xmltv_generator_url=update.xmltv_generator_url,
            tsdb_api_key=update.tsdb_api_key,
        )

    # Update cached display settings so new values are used immediately
    set_config_display(
        time_format=update.time_format,
        show_timezone=update.show_timezone,
        channel_id_format=update.channel_id_format,
        xmltv_generator_name=update.xmltv_generator_name,
        xmltv_generator_url=update.xmltv_generator_url,
    )

    with get_db() as conn:
        settings = get_all_settings(conn)
        # Get tsdb_api_key directly
        cursor = conn.execute("SELECT tsdb_api_key FROM settings WHERE id = 1")
        row = cursor.fetchone()
        tsdb_api_key = row["tsdb_api_key"] if row else None

    return DisplaySettingsModel(
        time_format=settings.display.time_format,
        show_timezone=settings.display.show_timezone,
        channel_id_format=settings.display.channel_id_format,
        xmltv_generator_name=settings.display.xmltv_generator_name,
        xmltv_generator_url=settings.display.xmltv_generator_url,
        tsdb_api_key=tsdb_api_key,
    )
