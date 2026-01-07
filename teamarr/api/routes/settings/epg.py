"""EPG settings endpoints."""

from fastapi import APIRouter

from teamarr.database import get_db

from .models import EPGSettingsModel

router = APIRouter()


@router.get("/settings/epg", response_model=EPGSettingsModel)
def get_epg_settings():
    """Get EPG generation settings."""
    from teamarr.database.settings import get_epg_settings

    with get_db() as conn:
        settings = get_epg_settings(conn)

    return EPGSettingsModel(
        team_schedule_days_ahead=settings.team_schedule_days_ahead,
        event_match_days_ahead=settings.event_match_days_ahead,
        epg_output_days_ahead=settings.epg_output_days_ahead,
        epg_lookback_hours=settings.epg_lookback_hours,
        epg_timezone=settings.epg_timezone,
        epg_output_path=settings.epg_output_path,
        include_final_events=settings.include_final_events,
        midnight_crossover_mode=settings.midnight_crossover_mode,
        cron_expression=settings.cron_expression,
    )


@router.put("/settings/epg", response_model=EPGSettingsModel)
def update_epg_settings(update: EPGSettingsModel):
    """Update EPG generation settings."""
    from teamarr.config import set_timezone
    from teamarr.database.settings import get_epg_settings, update_epg_settings

    with get_db() as conn:
        update_epg_settings(
            conn,
            team_schedule_days_ahead=update.team_schedule_days_ahead,
            event_match_days_ahead=update.event_match_days_ahead,
            epg_output_days_ahead=update.epg_output_days_ahead,
            epg_lookback_hours=update.epg_lookback_hours,
            epg_timezone=update.epg_timezone,
            epg_output_path=update.epg_output_path,
            include_final_events=update.include_final_events,
            midnight_crossover_mode=update.midnight_crossover_mode,
            cron_expression=update.cron_expression,
        )

    # Update cached timezone so new value is used immediately
    set_timezone(update.epg_timezone)

    with get_db() as conn:
        settings = get_epg_settings(conn)

    return EPGSettingsModel(
        team_schedule_days_ahead=settings.team_schedule_days_ahead,
        event_match_days_ahead=settings.event_match_days_ahead,
        epg_output_days_ahead=settings.epg_output_days_ahead,
        epg_lookback_hours=settings.epg_lookback_hours,
        epg_timezone=settings.epg_timezone,
        epg_output_path=settings.epg_output_path,
        include_final_events=settings.include_final_events,
        midnight_crossover_mode=settings.midnight_crossover_mode,
        cron_expression=settings.cron_expression,
    )
