"""Database checkpoint for version 47.

Adds columns for unmatched stream handling:
- create_unmatched_channels (BOOLEAN)
- unmatched_channel_epg_source_id (INTEGER)
"""

import logging
from sqlite3 import Connection

logger = logging.getLogger(__name__)


def apply_checkpoint_v47(conn: Connection, current_version: int) -> None:
    """Apply database schema updates for version 47.

    Args:
        conn: Database connection
        current_version: Current schema version
    """
    if current_version >= 47:
        return

    logger.info("[MIGRATE] applying v47 checkpoint (unmatched stream handling)")

    # Add columns to event_epg_groups if not present
    cursor = conn.execute("PRAGMA table_info(event_epg_groups)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "create_unmatched_channels" not in columns:
        conn.execute(
            "ALTER TABLE event_epg_groups ADD COLUMN create_unmatched_channels BOOLEAN DEFAULT 0"
        )
        logger.info("[MIGRATE] Added event_epg_groups.create_unmatched_channels column")

    if "unmatched_channel_epg_source_id" not in columns:
        conn.execute(
            "ALTER TABLE event_epg_groups ADD COLUMN unmatched_channel_epg_source_id INTEGER"
        )
        logger.info("[MIGRATE] Added event_epg_groups.unmatched_channel_epg_source_id column")

    # Update schema version
    conn.execute("UPDATE settings SET schema_version = 47 WHERE id = 1")
    logger.info("[MIGRATE] Schema updated to version 47")
