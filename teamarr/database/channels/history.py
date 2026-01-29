"""Channel history and audit operations.

CRUD operations for managed_channel_history table.
"""

import logging
from sqlite3 import Connection

logger = logging.getLogger(__name__)


def log_channel_history(
    conn: Connection,
    managed_channel_id: int,
    change_type: str,
    change_source: str | None = None,
    field_name: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    notes: str | None = None,
) -> int:
    """Log a change to channel history.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        change_type: Type of change (created, modified, deleted, etc.)
        change_source: Source of change (epg_generation, reconciliation, etc.)
        field_name: Field that changed (for modified)
        old_value: Previous value
        new_value: New value
        notes: Additional notes

    Returns:
        ID of history record
    """
    cursor = conn.execute(
        """INSERT INTO managed_channel_history
           (managed_channel_id, change_type, change_source, field_name, old_value, new_value, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (managed_channel_id, change_type, change_source, field_name, old_value, new_value, notes),
    )
    logger.debug(
        "[HISTORY] channel_id=%d type=%s source=%s", managed_channel_id, change_type, change_source
    )
    return cursor.lastrowid


def get_channel_history(
    conn: Connection,
    managed_channel_id: int,
    limit: int = 50,
) -> list[dict]:
    """Get history for a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        limit: Maximum records to return

    Returns:
        List of history records (newest first)
    """
    cursor = conn.execute(
        """SELECT * FROM managed_channel_history
           WHERE managed_channel_id = ?
           ORDER BY changed_at DESC
           LIMIT ?""",
        (managed_channel_id, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


def cleanup_old_history(conn: Connection, retention_days: int = 90) -> int:
    """Delete history records older than retention period.

    Args:
        conn: Database connection
        retention_days: Days to keep

    Returns:
        Number of records deleted
    """
    cursor = conn.execute(
        """DELETE FROM managed_channel_history
           WHERE changed_at < datetime('now', ? || ' days')""",
        (f"-{retention_days}",),
    )
    if cursor.rowcount > 0:
        logger.info(
            "[CLEANUP] Deleted %d history records older than %d days",
            cursor.rowcount,
            retention_days,
        )
    return cursor.rowcount
