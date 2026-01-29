"""Update check settings and status endpoints."""

from fastapi import APIRouter

from teamarr.config import VERSION
from teamarr.database import get_db

from .models import (
    UpdateCheckSettingsModel,
    UpdateCheckSettingsUpdate,
    UpdateInfoModel,
)

router = APIRouter()


# =============================================================================
# UPDATE CHECK SETTINGS
# =============================================================================


@router.get("/settings/update-check", response_model=UpdateCheckSettingsModel)
def get_update_check_settings():
    """Get update check settings."""
    from teamarr.database.settings import get_update_check_settings

    with get_db() as conn:
        settings = get_update_check_settings(conn)

    return UpdateCheckSettingsModel(
        enabled=settings.enabled,
        notify_stable=settings.notify_stable,
        notify_dev=settings.notify_dev,
        github_owner=settings.github_owner,
        github_repo=settings.github_repo,
        dev_branch=settings.dev_branch,
        auto_detect_branch=settings.auto_detect_branch,
    )


@router.put("/settings/update-check", response_model=UpdateCheckSettingsModel)
def update_update_check_settings(update: UpdateCheckSettingsUpdate):
    """Update update check settings."""
    from teamarr.database.settings import (
        get_update_check_settings,
    )
    from teamarr.database.settings import (
        update_update_check_settings as db_update,
    )

    with get_db() as conn:
        db_update(
            conn,
            enabled=update.enabled,
            notify_stable=update.notify_stable,
            notify_dev=update.notify_dev,
            github_owner=update.github_owner,
            github_repo=update.github_repo,
            dev_branch=update.dev_branch,
            auto_detect_branch=update.auto_detect_branch,
        )

    with get_db() as conn:
        settings = get_update_check_settings(conn)

    return UpdateCheckSettingsModel(
        enabled=settings.enabled,
        notify_stable=settings.notify_stable,
        notify_dev=settings.notify_dev,
        github_owner=settings.github_owner,
        github_repo=settings.github_repo,
        dev_branch=settings.dev_branch,
        auto_detect_branch=settings.auto_detect_branch,
    )


# =============================================================================
# UPDATE STATUS
# =============================================================================


@router.get("/updates/check", response_model=UpdateInfoModel)
def check_for_updates(force: bool = False):
    """Check for available updates.

    Args:
        force: Skip cache and force a fresh check from GitHub

    Returns update information including current version, latest version,
    and whether an update is available.
    """
    from teamarr.database.settings import get_update_check_settings
    from teamarr.services.update_checker import create_update_checker

    with get_db() as conn:
        settings = get_update_check_settings(conn)

    if not settings.enabled:
        # Return minimal info when disabled
        return UpdateInfoModel(
            current_version=VERSION,
            latest_version=None,
            update_available=False,
            checked_at="",
            build_type="unknown",
        )

    checker = create_update_checker(
        version=VERSION,
        owner=settings.github_owner,
        repo=settings.github_repo,
        dev_branch=settings.dev_branch,
        auto_detect_branch=settings.auto_detect_branch,
    )

    update_info = checker.check_for_updates(force=force)

    if not update_info:
        return UpdateInfoModel(
            current_version=VERSION,
            latest_version=None,
            update_available=False,
            checked_at="",
            build_type="unknown",
        )

    return UpdateInfoModel(
        current_version=update_info.current_version,
        latest_version=update_info.latest_version,
        update_available=update_info.update_available,
        checked_at=update_info.checked_at.isoformat(),
        build_type=update_info.build_type,
        download_url=update_info.download_url,
        latest_stable=update_info.latest_stable,
        latest_dev=update_info.latest_dev,
        latest_date=update_info.latest_date.isoformat() if update_info.latest_date else None,
    )
