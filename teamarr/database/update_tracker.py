"""Update tracker database operations.

Manages persistent storage of dev build digests for update detection.
"""

from datetime import datetime
from sqlite3 import Connection


def get_current_dev_digest(conn: Connection) -> str | None:
    """Get the currently stored dev build digest.

    Args:
        conn: Database connection

    Returns:
        Current digest string or None if not set
    """
    cursor = conn.execute(
        "SELECT current_dev_digest FROM update_tracker WHERE id = 1"
    )
    row = cursor.fetchone()
    return row["current_dev_digest"] if row else None


def update_dev_digest(conn: Connection, digest: str) -> None:
    """Update the stored dev build digest.

    Args:
        conn: Database connection
        digest: New digest to store
    """
    conn.execute(
        """
        UPDATE update_tracker
        SET current_dev_digest = ?,
            last_checked_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (digest,),
    )
    conn.commit()


def mark_update_notified(conn: Connection) -> None:
    """Mark that user has been notified of an update.

    Args:
        conn: Database connection
    """
    conn.execute(
        """
        UPDATE update_tracker
        SET last_notified_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
    )
    conn.commit()


def get_last_notified_time(conn: Connection) -> datetime | None:
    """Get when user was last notified of an update.

    Args:
        conn: Database connection

    Returns:
        Last notified timestamp or None
    """
    cursor = conn.execute(
        "SELECT last_notified_at FROM update_tracker WHERE id = 1"
    )
    row = cursor.fetchone()
    if row and row["last_notified_at"]:
        return datetime.fromisoformat(row["last_notified_at"])
    return None
