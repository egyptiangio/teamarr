"""Database operations for Regular TV groups (Virtual M3U)."""

import logging
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection

logger = logging.getLogger(__name__)


@dataclass
class RegularTVGroup:
    """Regular TV group configuration."""

    id: int
    name: str
    m3u_group_name: str
    m3u_account_id: int | None
    m3u_group_id: int | None
    enabled: bool
    epg_source_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _row_to_group(row) -> RegularTVGroup:
    """Convert database row to RegularTVGroup."""
    return RegularTVGroup(
        id=row["id"],
        name=row["name"],
        m3u_group_name=row["m3u_group_name"],
        m3u_account_id=row["m3u_account_id"],
        m3u_group_id=row["m3u_group_id"] if "m3u_group_id" in row.keys() else None,
        enabled=bool(row["enabled"]),
        epg_source_id=row["epg_source_id"] if "epg_source_id" in row.keys() else None,
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
    )


def get_all_groups(conn: Connection) -> list[RegularTVGroup]:
    """Get all Regular TV groups."""
    cursor = conn.execute("SELECT * FROM regular_tv_groups ORDER BY name")
    return [_row_to_group(row) for row in cursor.fetchall()]


def get_group(conn: Connection, group_id: int) -> RegularTVGroup | None:
    """Get a Regular TV group by ID."""
    cursor = conn.execute("SELECT * FROM regular_tv_groups WHERE id = ?", (group_id,))
    row = cursor.fetchone()
    return _row_to_group(row) if row else None


def get_group_by_name(
    conn: Connection, name: str, m3u_account_id: int | None = None
) -> RegularTVGroup | None:
    """Get a Regular TV group by name, optionally scoped to an M3U account.

    Args:
        conn: Database connection
        name: Group name
        m3u_account_id: Optional M3U account ID to scope the search

    Returns:
        RegularTVGroup or None if not found
    """
    if m3u_account_id is not None:
        cursor = conn.execute(
            "SELECT * FROM regular_tv_groups WHERE name = ? AND m3u_account_id = ?",
            (name, m3u_account_id),
        )
    else:
        cursor = conn.execute("SELECT * FROM regular_tv_groups WHERE name = ?", (name,))
    row = cursor.fetchone()
    return _row_to_group(row) if row else None


def create_group(
    conn: Connection,
    name: str,
    m3u_group_name: str,
    m3u_account_id: int | None = None,
    m3u_group_id: int | None = None,
    enabled: bool = True,
    epg_source_id: int | None = None,
) -> int:
    """Create a new Regular TV group."""
    if epg_source_id is None:
        # Fetch default from settings
        row = conn.execute("SELECT regular_tv_epg_source_id FROM settings WHERE id = 1").fetchone()
        if row:
            epg_source_id = row["regular_tv_epg_source_id"]

    try:
        cursor = conn.execute(
            """INSERT INTO regular_tv_groups (name, m3u_group_name, m3u_account_id, m3u_group_id, enabled, epg_source_id)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (name, m3u_group_name, m3u_account_id, m3u_group_id, int(enabled), epg_source_id),
        )
        group_id = cursor.lastrowid
        logger.info("[CREATED] Regular TV group id=%d name=%s", group_id, name)
        return group_id
    except Exception as e:
        logger.error(f"Failed to create Regular TV group: {e}")
        print(f"DEBUG ERROR in create_group: {e}", flush=True)
        raise

def update_group(
    conn: Connection,
    group_id: int,
    **kwargs,
) -> bool:
    """Update a Regular TV group from keyword arguments."""
    updates = []
    values = []

    allowed_keys = {"name", "m3u_group_name", "m3u_account_id", "m3u_group_id", "enabled", "epg_source_id"}

    for key, value in kwargs.items():
        if key not in allowed_keys:
            continue

        if key == "enabled":
            updates.append("enabled = ?")
            values.append(int(value))
        else:
            updates.append(f"{key} = ?")
            values.append(value)

    if not updates:
        return True

    updates.append("updated_at = CURRENT_TIMESTAMP")
    
    values.append(group_id)
    query = f"UPDATE regular_tv_groups SET {', '.join(updates)} WHERE id = ?"
    cursor = conn.execute(query, values)
    
    # A rowcount of 0 can mean the values were the same, which is not an error.
    # A failed update (e.g. constraint) would raise an exception.
    # The route handler already checked for existence, so we can consider this successful.
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Regular TV group id=%d", group_id)
    return True


def delete_group(conn: Connection, group_id: int) -> bool:
    """Delete a Regular TV group."""
    cursor = conn.execute("DELETE FROM regular_tv_groups WHERE id = ?", (group_id,))
    if cursor.rowcount > 0:
        logger.info("[DELETED] Regular TV group id=%d", group_id)
        return True
    return False