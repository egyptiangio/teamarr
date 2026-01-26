"""Health check endpoint."""

import logging

from fastapi import APIRouter

from teamarr.api.startup_state import get_startup_state
from teamarr.config import VERSION
from teamarr.database import get_db
from teamarr.database.settings import get_all_settings
from teamarr.services.update_checker import create_update_checker

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """Health check endpoint with startup status and update information."""
    startup_state = get_startup_state()
    startup_info = startup_state.to_dict()

    # Get update check settings
    update_info = None
    with get_db() as conn:
        settings = get_all_settings(conn)
        update_settings = settings.update_check

        # Only check for updates if enabled and startup is complete
        if update_settings.enabled and startup_state.is_ready:
            try:
                # Create checker with configured repositories
                checker = create_update_checker(
                    version=VERSION,
                    owner=update_settings.github_owner,
                    repo=update_settings.github_repo,
                    ghcr_owner=update_settings.ghcr_owner,
                    ghcr_image=update_settings.ghcr_image,
                    dev_tag=update_settings.dev_tag,
                    cache_duration_hours=update_settings.check_interval_hours,
                    db_factory=get_db,  # Pass database factory for digest persistence
                )
                # Don't force - use cached result if available
                result = checker.check_for_updates(force=False)
                if result:
                    update_info = {
                        "update_available": result.update_available,
                        "latest_version": result.latest_version,
                        "build_type": result.build_type,
                        "checked_at": result.checked_at.isoformat() if result.checked_at else None,
                    }
            except Exception as e:
                # Silently ignore update check failures in health endpoint
                # But log at debug level for troubleshooting
                logger.debug("[HEALTH] Update check failed: %s", e)
                pass

    response = {
        "status": "healthy" if startup_state.is_ready else "starting",
        "version": VERSION,
        "startup": startup_info,
    }

    # Add update info if available
    if update_info:
        response["update"] = update_info

    return response
