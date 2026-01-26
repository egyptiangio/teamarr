"""Update check endpoints."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from teamarr.config import VERSION
from teamarr.database import get_db
from teamarr.database.settings import get_all_settings, update_update_check_settings
from teamarr.services.update_checker import create_update_checker

logger = logging.getLogger(__name__)
router = APIRouter()


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
    latest_stable: str | None = None  # Latest stable release version
    latest_dev: str | None = None  # Latest dev build digest (short)


class UpdateCheckSettingsRequest(BaseModel):
    """Request model for updating update check settings."""

    enabled: bool | None = None
    check_interval_hours: int | None = None
    notify_stable_updates: bool | None = None
    notify_dev_updates: bool | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    dev_branch: str | None = None


@router.get("/updates/status")
def get_update_status(force: bool = False) -> UpdateStatusResponse:
    """Get current update status.

    Args:
        force: Force a fresh check, bypassing cache

    Returns:
        Update status information
    """
    global _last_update_info

    with get_db() as conn:
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
                "github_owner": update_settings.github_owner,
                "github_repo": update_settings.github_repo,
                "dev_branch": update_settings.dev_branch,
            },
        )

    # Check for updates with configured repositories
    checker = create_update_checker(
        version=VERSION,
        owner=update_settings.github_owner,
        repo=update_settings.github_repo,
        dev_branch=update_settings.dev_branch if hasattr(update_settings, 'dev_branch') else "dev",
        cache_duration_hours=update_settings.check_interval_hours,
    )
    update_info = checker.check_for_updates(force=force)

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
            latest_stable=update_info.latest_stable,
            latest_dev=update_info.latest_dev,
            settings={
                "enabled": True,
                "check_interval_hours": update_settings.check_interval_hours,
                "notify_stable_updates": update_settings.notify_stable_updates,
                "notify_dev_updates": update_settings.notify_dev_updates,
                "github_owner": update_settings.github_owner,
                "github_repo": update_settings.github_repo,
                "dev_branch": update_settings.dev_branch,
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
            latest_stable=None,
            latest_dev=None,
            settings={
                "enabled": True,
                "check_interval_hours": update_settings.check_interval_hours,
                "notify_stable_updates": update_settings.notify_stable_updates,
                "notify_dev_updates": update_settings.notify_dev_updates,
                "github_owner": update_settings.github_owner,
                "github_repo": update_settings.github_repo,
                "dev_branch": update_settings.dev_branch,
            },
        )


@router.patch("/updates/settings")
def update_settings(request: UpdateCheckSettingsRequest) -> dict:
    """Update update check settings.

    Args:
        request: Update settings request

    Returns:
        Success status
    """
    with get_db() as conn:
        updated = update_update_check_settings(
            conn,
            enabled=request.enabled,
            check_interval_hours=request.check_interval_hours,
            notify_stable_updates=request.notify_stable_updates,
            notify_dev_updates=request.notify_dev_updates,
            github_owner=request.github_owner,
            github_repo=request.github_repo,
            dev_branch=request.dev_branch,
        )

        if updated:
            conn.commit()
            return {"success": True, "message": "Update check settings updated"}
        else:
            return {"success": False, "message": "No changes made"}
