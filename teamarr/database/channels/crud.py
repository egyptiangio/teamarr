"""Managed channel CRUD operations.

Create, Read, Update, Delete operations for managed_channels table.
"""

import json
from sqlite3 import Connection

from .types import ManagedChannel


def create_managed_channel(
    conn: Connection,
    event_epg_group_id: int,
    event_id: str,
    event_provider: str,
    tvg_id: str,
    channel_name: str,
    **kwargs,
) -> int:
    """Create a managed channel record.

    Args:
        conn: Database connection
        event_epg_group_id: Parent group ID
        event_id: Event ID from provider
        event_provider: Provider name (espn, tsdb, etc.)
        tvg_id: XMLTV TVG ID
        channel_name: Display name
        **kwargs: Additional fields (channel_number, logo_url, etc.)

    Returns:
        ID of created record
    """
    # Build column list and values
    columns = [
        "event_epg_group_id",
        "event_id",
        "event_provider",
        "tvg_id",
        "channel_name",
    ]
    values = [event_epg_group_id, event_id, event_provider, tvg_id, channel_name]

    # Add optional fields
    allowed_fields = [
        "channel_number",
        "logo_url",
        "dispatcharr_channel_id",
        "dispatcharr_uuid",
        "dispatcharr_logo_id",
        "channel_group_id",
        "stream_profile_id",
        "channel_profile_ids",
        "primary_stream_id",
        "exception_keyword",
        "home_team",
        "home_team_abbrev",
        "home_team_logo",
        "away_team",
        "away_team_abbrev",
        "away_team_logo",
        "event_date",
        "event_name",
        "league",
        "sport",
        "venue",
        "broadcast",
        "scheduled_delete_at",
        "sync_status",
    ]

    for field_name in allowed_fields:
        if field_name in kwargs and kwargs[field_name] is not None:
            columns.append(field_name)
            value = kwargs[field_name]
            # Serialize lists/dicts to JSON
            if isinstance(value, (list, dict)):
                value = json.dumps(value)
            values.append(value)

    placeholders = ", ".join(["?"] * len(values))
    column_str = ", ".join(columns)

    cursor = conn.execute(
        f"INSERT INTO managed_channels ({column_str}) VALUES ({placeholders})",
        values,
    )
    return cursor.lastrowid


def get_managed_channel(conn: Connection, channel_id: int) -> ManagedChannel | None:
    """Get a managed channel by ID.

    Args:
        conn: Database connection
        channel_id: Channel ID

    Returns:
        ManagedChannel or None if not found
    """
    cursor = conn.execute("SELECT * FROM managed_channels WHERE id = ?", (channel_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return ManagedChannel.from_row(dict(row))


def get_managed_channel_by_tvg_id(conn: Connection, tvg_id: str) -> ManagedChannel | None:
    """Get a managed channel by TVG ID.

    Args:
        conn: Database connection
        tvg_id: TVG ID

    Returns:
        ManagedChannel or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM managed_channels WHERE tvg_id = ? AND deleted_at IS NULL",
        (tvg_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return ManagedChannel.from_row(dict(row))


def get_managed_channel_by_event(
    conn: Connection,
    event_id: str,
    event_provider: str,
    group_id: int | None = None,
) -> ManagedChannel | None:
    """Get a managed channel by event ID.

    Args:
        conn: Database connection
        event_id: Event ID
        event_provider: Provider name
        group_id: Optional group filter

    Returns:
        ManagedChannel or None if not found
    """
    if group_id:
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_id = ? AND event_provider = ?
                 AND event_epg_group_id = ? AND deleted_at IS NULL""",
            (event_id, event_provider, group_id),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_id = ? AND event_provider = ? AND deleted_at IS NULL""",
            (event_id, event_provider),
        )
    row = cursor.fetchone()
    if not row:
        return None
    return ManagedChannel.from_row(dict(row))


def get_managed_channel_by_dispatcharr_id(
    conn: Connection,
    dispatcharr_channel_id: int,
) -> ManagedChannel | None:
    """Get a managed channel by Dispatcharr channel ID.

    Args:
        conn: Database connection
        dispatcharr_channel_id: Dispatcharr channel ID

    Returns:
        ManagedChannel or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM managed_channels WHERE dispatcharr_channel_id = ?",
        (dispatcharr_channel_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return ManagedChannel.from_row(dict(row))


def get_managed_channels_for_group(
    conn: Connection,
    group_id: int,
    include_deleted: bool = False,
) -> list[ManagedChannel]:
    """Get all managed channels for a group.

    Args:
        conn: Database connection
        group_id: Event EPG group ID
        include_deleted: Whether to include deleted channels

    Returns:
        List of ManagedChannel objects
    """
    if include_deleted:
        cursor = conn.execute(
            "SELECT * FROM managed_channels WHERE event_epg_group_id = ? ORDER BY channel_number",
            (group_id,),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_epg_group_id = ? AND deleted_at IS NULL
               ORDER BY channel_number""",
            (group_id,),
        )
    return [ManagedChannel.from_row(dict(row)) for row in cursor.fetchall()]


def get_channels_pending_deletion(conn: Connection) -> list[ManagedChannel]:
    """Get channels past their scheduled delete time.

    Args:
        conn: Database connection

    Returns:
        List of ManagedChannel objects ready for deletion
    """
    cursor = conn.execute(
        """SELECT * FROM managed_channels
           WHERE scheduled_delete_at IS NOT NULL
             AND scheduled_delete_at <= datetime('now')
             AND deleted_at IS NULL
           ORDER BY scheduled_delete_at""",
    )
    return [ManagedChannel.from_row(dict(row)) for row in cursor.fetchall()]


def get_all_managed_channels(
    conn: Connection,
    include_deleted: bool = False,
) -> list[ManagedChannel]:
    """Get all managed channels.

    Args:
        conn: Database connection
        include_deleted: Whether to include deleted channels

    Returns:
        List of ManagedChannel objects
    """
    if include_deleted:
        cursor = conn.execute(
            "SELECT * FROM managed_channels ORDER BY event_epg_group_id, channel_number"
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE deleted_at IS NULL
               ORDER BY event_epg_group_id, channel_number"""
        )
    return [ManagedChannel.from_row(dict(row)) for row in cursor.fetchall()]


def update_managed_channel(conn: Connection, channel_id: int, data: dict) -> bool:
    """Update managed channel fields.

    Args:
        conn: Database connection
        channel_id: Channel ID to update
        data: Fields to update

    Returns:
        True if updated, False if not found
    """
    if not data:
        return False

    # Serialize JSON fields
    for key in ["channel_profile_ids"]:
        if key in data and isinstance(data[key], (list, dict)):
            data[key] = json.dumps(data[key])

    set_clause = ", ".join(f"{k} = ?" for k in data.keys())
    values = list(data.values()) + [channel_id]

    cursor = conn.execute(
        f"UPDATE managed_channels SET {set_clause} WHERE id = ?",
        values,
    )
    return cursor.rowcount > 0


def mark_channel_deleted(
    conn: Connection,
    channel_id: int,
    reason: str | None = None,
) -> bool:
    """Mark a channel as deleted (soft delete).

    Args:
        conn: Database connection
        channel_id: Channel ID
        reason: Delete reason

    Returns:
        True if updated, False if not found
    """
    cursor = conn.execute(
        """UPDATE managed_channels
           SET deleted_at = datetime('now'),
               delete_reason = ?,
               sync_status = 'deleted'
           WHERE id = ?""",
        (reason, channel_id),
    )
    return cursor.rowcount > 0


def find_existing_channel(
    conn: Connection,
    group_id: int,
    event_id: str,
    event_provider: str,
    exception_keyword: str | None = None,
    stream_id: int | None = None,
    mode: str = "consolidate",
) -> ManagedChannel | None:
    """Find existing channel based on duplicate handling mode.

    Args:
        conn: Database connection
        group_id: Event EPG group ID
        event_id: Event ID
        event_provider: Provider name
        exception_keyword: Exception keyword for separate consolidation
        stream_id: Stream ID (for 'separate' mode)
        mode: Duplicate handling mode (consolidate, separate, ignore)

    Returns:
        Existing ManagedChannel or None
    """
    if mode == "separate":
        # In separate mode, each stream gets its own channel
        # Look for channel with same primary stream
        if stream_id:
            cursor = conn.execute(
                """SELECT * FROM managed_channels
                   WHERE event_epg_group_id = ?
                     AND event_id = ?
                     AND event_provider = ?
                     AND primary_stream_id = ?
                     AND deleted_at IS NULL""",
                (group_id, event_id, event_provider, stream_id),
            )
            row = cursor.fetchone()
            if row:
                return ManagedChannel.from_row(dict(row))
        return None

    elif mode == "ignore":
        # In ignore mode, first stream wins - just check if any channel exists
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_epg_group_id = ?
                 AND event_id = ?
                 AND event_provider = ?
                 AND deleted_at IS NULL
               LIMIT 1""",
            (group_id, event_id, event_provider),
        )
        row = cursor.fetchone()
        if row:
            return ManagedChannel.from_row(dict(row))
        return None

    else:  # consolidate (default)
        # In consolidate mode, look for channel with same keyword
        if exception_keyword:
            cursor = conn.execute(
                """SELECT * FROM managed_channels
                   WHERE event_epg_group_id = ?
                     AND event_id = ?
                     AND event_provider = ?
                     AND exception_keyword = ?
                     AND deleted_at IS NULL""",
                (group_id, event_id, event_provider, exception_keyword),
            )
        else:
            cursor = conn.execute(
                """SELECT * FROM managed_channels
                   WHERE event_epg_group_id = ?
                     AND event_id = ?
                     AND event_provider = ?
                     AND exception_keyword IS NULL
                     AND deleted_at IS NULL""",
                (group_id, event_id, event_provider),
            )
        row = cursor.fetchone()
        if row:
            return ManagedChannel.from_row(dict(row))
        return None


def find_parent_channel_for_event(
    conn: Connection,
    parent_group_id: int,
    event_id: str,
    event_provider: str,
    exception_keyword: str | None = None,
) -> ManagedChannel | None:
    """Find a parent group's channel for an event (used by child groups).

    Child groups add their streams to parent's existing channels.
    This function finds the appropriate parent channel, considering
    exception keywords for sub-consolidated channels.

    Args:
        conn: Database connection
        parent_group_id: Parent group ID
        event_id: Event ID
        event_provider: Provider name
        exception_keyword: Optional keyword for sub-consolidated channel lookup

    Returns:
        Parent's ManagedChannel or None if not found
    """
    if exception_keyword:
        # Look for channel with matching keyword (sub-consolidated)
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_epg_group_id = ?
                 AND event_id = ?
                 AND event_provider = ?
                 AND exception_keyword = ?
                 AND deleted_at IS NULL""",
            (parent_group_id, event_id, event_provider, exception_keyword),
        )
    else:
        # Look for main channel (no keyword)
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_epg_group_id = ?
                 AND event_id = ?
                 AND event_provider = ?
                 AND (exception_keyword IS NULL OR exception_keyword = '')
                 AND deleted_at IS NULL""",
            (parent_group_id, event_id, event_provider),
        )
    row = cursor.fetchone()
    if row:
        return ManagedChannel.from_row(dict(row))
    return None


def find_any_channel_for_event(
    conn: Connection,
    event_id: str,
    event_provider: str,
    exclude_group_id: int | None = None,
) -> ManagedChannel | None:
    """Find any group's channel for an event (used for cross-group consolidation).

    Used by multi-league groups to check if a single-league group already
    has a channel for the same event, enabling stream consolidation.

    Args:
        conn: Database connection
        event_id: Event ID
        event_provider: Provider name
        exclude_group_id: Optional group ID to exclude from search

    Returns:
        First matching ManagedChannel or None if not found
    """
    if exclude_group_id:
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_id = ?
                 AND event_provider = ?
                 AND event_epg_group_id != ?
                 AND deleted_at IS NULL
               ORDER BY created_at ASC
               LIMIT 1""",
            (event_id, event_provider, exclude_group_id),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM managed_channels
               WHERE event_id = ?
                 AND event_provider = ?
                 AND deleted_at IS NULL
               ORDER BY created_at ASC
               LIMIT 1""",
            (event_id, event_provider),
        )
    row = cursor.fetchone()
    if row:
        return ManagedChannel.from_row(dict(row))
    return None
