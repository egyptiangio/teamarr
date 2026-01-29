"""V1 to V2 migration detection and database archival.

Detects V1 database format and provides options for users to:
1. Archive V1 database and start fresh with V2
2. Download their archived V1 database
"""

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from teamarr.database.connection import DEFAULT_DB_PATH, get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/migration", tags=["migration"])


class MigrationStatus(BaseModel):
    """V1 migration status."""

    is_v1_database: bool
    has_archived_backup: bool
    database_path: str
    backup_path: str | None


class ArchiveResult(BaseModel):
    """Result of archiving V1 database."""

    success: bool
    message: str
    backup_path: str | None


def detect_v1_database() -> bool:
    """Detect if current database is V1 format.

    V1 databases have tables like:
    - schedule_cache
    - h2h_cache
    - epg_history
    - league_config

    V2 databases have tables like:
    - leagues
    - team_cache
    - league_cache
    - processing_runs
    """
    db_path = Path(DEFAULT_DB_PATH)
    if not db_path.exists():
        return False

    try:
        conn = get_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schedule_cache'"
        )
        has_schedule_cache = cursor.fetchone() is not None

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'"
        )
        has_leagues = cursor.fetchone() is not None

        # V1 has schedule_cache but not leagues table
        return has_schedule_cache and not has_leagues
    except Exception as e:
        logger.error("[MIGRATION] Error detecting database version: %s", e)
        return False


def get_backup_path() -> Path:
    """Get the path for V1 backup."""
    return Path(DEFAULT_DB_PATH).parent / ".teamarr.v1.bak"


@router.get("/status", response_model=MigrationStatus)
async def get_migration_status():
    """Check if database is V1 format and needs migration."""
    db_path = Path(DEFAULT_DB_PATH)
    backup_path = get_backup_path()

    return MigrationStatus(
        is_v1_database=detect_v1_database(),
        has_archived_backup=backup_path.exists(),
        database_path=str(db_path),
        backup_path=str(backup_path) if backup_path.exists() else None,
    )


@router.post("/archive", response_model=ArchiveResult)
async def archive_v1_database():
    """Archive V1 database and prepare for fresh V2 start.

    Moves the V1 database to .teamarr.v1.bak and allows
    the app to create a fresh V2 database on next startup.
    """
    db_path = Path(DEFAULT_DB_PATH)
    backup_path = get_backup_path()

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="No database found to archive")

    if not detect_v1_database():
        raise HTTPException(
            status_code=400,
            detail="Current database is not V1 format. Cannot archive.",
        )

    try:
        # Move database to backup location
        shutil.move(str(db_path), str(backup_path))

        logger.info("[MIGRATION] Archived V1 database to %s", backup_path)

        return ArchiveResult(
            success=True,
            message="V1 database archived successfully. Restart the application to initialize V2.",
            backup_path=str(backup_path),
        )
    except Exception as e:
        logger.error("[MIGRATION] Failed to archive V1 database: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to archive database: {e}") from e


@router.get("/download-backup")
async def download_v1_backup():
    """Download the archived V1 database backup."""
    backup_path = get_backup_path()

    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="No V1 backup found")

    return FileResponse(
        path=str(backup_path),
        filename="teamarr-v1-backup.db",
        media_type="application/x-sqlite3",
    )


@router.post("/clear-backup")
async def clear_v1_backup():
    """Move V1 backup to backups subfolder after user proceeds to V2.

    Preserves the backup for future reference but removes it from the
    detection path so the upgrade page won't show again.
    """
    backup_path = get_backup_path()

    if backup_path.exists():
        try:
            # Create backups subfolder
            backups_dir = backup_path.parent / "backups"
            backups_dir.mkdir(exist_ok=True)

            # Move backup to subfolder with timestamp
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_path = backups_dir / f"teamarr-v1-{timestamp}.db"

            shutil.move(str(backup_path), str(dest_path))
            logger.info("[MIGRATION] V1 backup moved to %s", dest_path)

            return {"success": True, "message": f"Backup moved to {dest_path}"}
        except Exception as e:
            logger.error("[MIGRATION] Failed to move V1 backup: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to move backup: {e}") from e

    return {"success": True, "message": "No backup to move"}


@router.post("/restart")
async def trigger_restart():
    """Trigger application restart after V1 migration.

    Moves backup to subfolder and schedules an immediate exit.
    Docker (with restart policy) or supervisor will restart the process.
    """
    import asyncio
    import sys

    backup_path = get_backup_path()

    # Move backup if it exists
    if backup_path.exists():
        try:
            backups_dir = backup_path.parent / "backups"
            backups_dir.mkdir(exist_ok=True)

            from datetime import datetime

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_path = backups_dir / f"teamarr-v1-{timestamp}.db"

            shutil.move(str(backup_path), str(dest_path))
            logger.info("[MIGRATION] V1 backup moved to %s", dest_path)
        except Exception as e:
            logger.warning("[MIGRATION] Failed to move V1 backup: %s", e)

    logger.info("[MIGRATION] Triggering application restart for V2 initialization...")

    # Schedule exit after response is sent
    async def delayed_exit():
        await asyncio.sleep(0.5)  # Give time for response to be sent
        logger.info("[MIGRATION] Exiting for restart...")
        sys.exit(0)

    asyncio.create_task(delayed_exit())

    return {"success": True, "message": "Restart triggered"}
