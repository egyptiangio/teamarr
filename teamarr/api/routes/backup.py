"""Backup and restore API endpoints."""

import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from teamarr.database.connection import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup")


class RestoreResponse(BaseModel):
    success: bool
    message: str
    backup_path: str | None = None


@router.get("", response_class=FileResponse)
async def download_backup():
    """Download a backup of the database.

    Returns the SQLite database file as a downloadable attachment.
    """
    if not DEFAULT_DB_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database file not found",
        )

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"teamarr_backup_{timestamp}.db"

    logger.info("[BACKUP] Downloading backup as %s", filename)

    return FileResponse(
        path=str(DEFAULT_DB_PATH),
        filename=filename,
        media_type="application/x-sqlite3",
    )


@router.post("", response_model=RestoreResponse)
async def restore_backup(file: UploadFile = File(...)):
    """Restore database from uploaded backup.

    The uploaded file must be a valid SQLite database.
    A backup of the current database is created before restoring.

    WARNING: This will replace ALL current data!
    """
    # Validate file extension
    if not file.filename or not file.filename.endswith(".db"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Please upload a .db file.",
        )

    # Create temp file to validate upload
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp_path = Path(tmp.name)
        try:
            # Write uploaded content to temp file
            content = await file.read()
            tmp.write(content)
            tmp.flush()

            # Validate it's a valid SQLite database
            try:
                conn = sqlite3.connect(str(tmp_path))
                cursor = conn.cursor()
                # Check for expected tables
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
                )
                if not cursor.fetchone():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid backup file: missing required tables",
                    )
                conn.close()
            except sqlite3.DatabaseError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid SQLite database: {e}",
                ) from e

            # Create backup of current database before restoring
            backup_path = None
            if DEFAULT_DB_PATH.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = DEFAULT_DB_PATH.parent / f"teamarr_pre_restore_{timestamp}.db"
                shutil.copy2(DEFAULT_DB_PATH, backup_path)
                logger.info("[RESTORE] Created pre-restore backup at %s", backup_path)

            # Replace database with uploaded file
            shutil.copy2(tmp_path, DEFAULT_DB_PATH)
            logger.info("[RESTORE] Database restored from uploaded backup")

            return RestoreResponse(
                success=True,
                message="Database restored. Please restart the application for changes to take effect.",  # noqa: E501
                backup_path=str(backup_path) if backup_path else None,
            )

        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)
