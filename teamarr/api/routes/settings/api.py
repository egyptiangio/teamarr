"""API settings endpoints."""

from fastapi import APIRouter

from teamarr.database import get_db

from .models import APISettingsModel, APISettingsUpdate

router = APIRouter()


@router.get("/settings/api", response_model=APISettingsModel)
def get_api_settings():
    """Get API behavior settings.

    Includes:
    - API timeout and retry settings
    - Cache refresh frequencies
    - Startup cache max age (0 = disabled, >0 = refresh if older than N days)
    """
    from teamarr.database.settings import get_all_settings

    with get_db() as conn:
        settings = get_all_settings(conn)

    return APISettingsModel(
        timeout=settings.api.timeout,
        retry_count=settings.api.retry_count,
        soccer_cache_refresh_frequency=settings.api.soccer_cache_refresh_frequency,
        team_cache_refresh_frequency=settings.api.team_cache_refresh_frequency,
        startup_cache_max_age_days=settings.api.startup_cache_max_age_days,
    )


@router.put("/settings/api", response_model=APISettingsModel)
def update_api_settings_endpoint(update: APISettingsUpdate):
    """Update API behavior settings.

    startup_cache_max_age_days:
    - 0 = disabled (never auto-refresh cache on startup)
    - >0 = refresh cache if older than N days
    """
    from teamarr.database.settings import get_all_settings, update_api_settings

    with get_db() as conn:
        update_api_settings(
            conn,
            timeout=update.timeout,
            retry_count=update.retry_count,
            soccer_cache_refresh_frequency=update.soccer_cache_refresh_frequency,
            team_cache_refresh_frequency=update.team_cache_refresh_frequency,
            startup_cache_max_age_days=update.startup_cache_max_age_days,
        )

    with get_db() as conn:
        settings = get_all_settings(conn)

    return APISettingsModel(
        timeout=settings.api.timeout,
        retry_count=settings.api.retry_count,
        soccer_cache_refresh_frequency=settings.api.soccer_cache_refresh_frequency,
        team_cache_refresh_frequency=settings.api.team_cache_refresh_frequency,
        startup_cache_max_age_days=settings.api.startup_cache_max_age_days,
    )
