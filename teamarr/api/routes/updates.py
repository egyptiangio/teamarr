"""Update check endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from teamarr.api.dependencies import get_db_connection
from teamarr.config import VERSION
from teamarr.database.settings import get_all_settings, update_update_check_settings
from teamarr.services.update_checker import UpdateInfo, create_update_checker

logger = logging.getLogger(__name__)
router = APIRouter()

# Global update checker instance (cached)
_update_checker = None
_last_update_info: UpdateInfo | None = None


def get_update_checker():
    """Get or create the update checker instance."""
    global _update_checker
    if _update_checker is None:
        _update_checker = create_update_checker(VERSION)
    return _update_checker


class UpdateStatusResponse(BaseModel):
    """Response model for update status."""

    current_version: str
    latest_version: str | None
    update_available: bool
    build_type: str
    download_url: str | None
    release_notes_url: str | None
    checked_at: str | None
    settings: dict


class UpdateCheckSettingsRequest(BaseModel):
    """Request model for updating update check settings."""

    enabled: bool | None = None
    check_interval_hours: int | None = None
    notify_stable_updates: bool | None = None
    notify_dev_updates: bool | None = None


@router.get("/updates/status")
def get_update_status(
    conn: Annotated[object, Depends(get_db_connection)],
    force: bool = False,
) -> UpdateStatusResponse:
    """Get current update status.

    Args:
        conn: Database connection
        force: Force a fresh check, bypassing cache

    Returns:
        Update status information
    """
    global _last_update_info

    settings = get_all_settings(conn)
    update_settings = settings.update_check

    # If update checking is disabled, return current version only
    if not update_settings.enabled:
        return UpdateStatusResponse(
            current_version=VERSION,
            latest_version=None,
            update_available=False,
            build_type="unknown",
            download_url=None,
            release_notes_url=None,
            checked_at=None,
            settings={
                "enabled": False,
                "check_interval_hours": update_settings.check_interval_hours,
                "notify_stable_updates": update_settings.notify_stable_updates,
                "notify_dev_updates": update_settings.notify_dev_updates,
            },
        )

    # Check for updates
    checker = get_update_checker()
    update_info = checker.check_for_updates(force=force)

    # Store last check for other uses
    if update_info:
        _last_update_info = update_info

    # Return status
    if update_info:
        return UpdateStatusResponse(
            current_version=update_info.current_version,
            latest_version=update_info.latest_version,
            update_available=update_info.update_available,
            build_type=update_info.build_type,
            download_url=update_info.download_url,
            release_notes_url=update_info.release_notes_url,
            checked_at=update_info.checked_at.isoformat(),
            settings={
                "enabled": True,
                "check_interval_hours": update_settings.check_interval_hours,
                "notify_stable_updates": update_settings.notify_stable_updates,
                "notify_dev_updates": update_settings.notify_dev_updates,
            },
        )
    else:
        # Check failed, return current version
        return UpdateStatusResponse(
            current_version=VERSION,
            latest_version=None,
            update_available=False,
            build_type="unknown",
            download_url=None,
            release_notes_url=None,
            checked_at=None,
            settings={
                "enabled": True,
                "check_interval_hours": update_settings.check_interval_hours,
                "notify_stable_updates": update_settings.notify_stable_updates,
                "notify_dev_updates": update_settings.notify_dev_updates,
            },
        )


@router.patch("/updates/settings")
def update_settings(
    request: UpdateCheckSettingsRequest,
    conn: Annotated[object, Depends(get_db_connection)],
) -> dict:
    """Update update check settings.

    Args:
        request: Update settings request
        conn: Database connection

    Returns:
        Success status
    """
    updated = update_update_check_settings(
        conn,
        enabled=request.enabled,
        check_interval_hours=request.check_interval_hours,
        notify_stable_updates=request.notify_stable_updates,
        notify_dev_updates=request.notify_dev_updates,
    )

    if updated:
        conn.commit()
        return {"success": True, "message": "Update check settings updated"}
    else:
        return {"success": False, "message": "No changes made"}
