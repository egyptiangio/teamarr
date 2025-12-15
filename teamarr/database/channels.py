"""Database operations for managed channels.

Provides CRUD operations for the managed_channels table and related tables
(managed_channel_streams, managed_channel_history, consolidation_exception_keywords).
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from sqlite3 import Connection

logger = logging.getLogger(__name__)


# =============================================================================
# DATA TYPES
# =============================================================================


@dataclass
class ManagedChannel:
    """A managed channel from the database."""

    id: int
    event_epg_group_id: int
    event_id: str
    event_provider: str
    tvg_id: str
    channel_name: str
    channel_number: str | None = None
    logo_url: str | None = None

    # Dispatcharr integration
    dispatcharr_channel_id: int | None = None
    dispatcharr_uuid: str | None = None
    dispatcharr_logo_id: int | None = None

    # Channel settings
    channel_group_id: int | None = None
    stream_profile_id: int | None = None
    channel_profile_ids: list[int] = field(default_factory=list)
    primary_stream_id: int | None = None
    exception_keyword: str | None = None

    # Event context
    home_team: str | None = None
    away_team: str | None = None
    event_date: datetime | None = None
    event_name: str | None = None
    league: str | None = None
    sport: str | None = None

    # Lifecycle
    scheduled_delete_at: datetime | None = None
    deleted_at: datetime | None = None
    delete_reason: str | None = None

    # Sync status
    sync_status: str = "pending"
    sync_message: str | None = None
    last_verified_at: datetime | None = None

    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "ManagedChannel":
        """Create from database row dict."""
        profile_ids = row.get("channel_profile_ids")
        if profile_ids and isinstance(profile_ids, str):
            try:
                profile_ids = json.loads(profile_ids)
            except json.JSONDecodeError:
                profile_ids = []

        return cls(
            id=row["id"],
            event_epg_group_id=row["event_epg_group_id"],
            event_id=row["event_id"],
            event_provider=row["event_provider"],
            tvg_id=row["tvg_id"],
            channel_name=row["channel_name"],
            channel_number=row.get("channel_number"),
            logo_url=row.get("logo_url"),
            dispatcharr_channel_id=row.get("dispatcharr_channel_id"),
            dispatcharr_uuid=row.get("dispatcharr_uuid"),
            dispatcharr_logo_id=row.get("dispatcharr_logo_id"),
            channel_group_id=row.get("channel_group_id"),
            stream_profile_id=row.get("stream_profile_id"),
            channel_profile_ids=profile_ids or [],
            primary_stream_id=row.get("primary_stream_id"),
            exception_keyword=row.get("exception_keyword"),
            home_team=row.get("home_team"),
            away_team=row.get("away_team"),
            event_date=row.get("event_date"),
            event_name=row.get("event_name"),
            league=row.get("league"),
            sport=row.get("sport"),
            scheduled_delete_at=row.get("scheduled_delete_at"),
            deleted_at=row.get("deleted_at"),
            delete_reason=row.get("delete_reason"),
            sync_status=row.get("sync_status", "pending"),
            sync_message=row.get("sync_message"),
            last_verified_at=row.get("last_verified_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass
class ManagedChannelStream:
    """A stream attached to a managed channel."""

    id: int
    managed_channel_id: int
    dispatcharr_stream_id: int
    stream_name: str | None = None
    source_group_id: int | None = None
    source_group_type: str = "parent"
    priority: int = 0
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None
    exception_keyword: str | None = None
    added_at: datetime | None = None
    removed_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "ManagedChannelStream":
        """Create from database row dict."""
        return cls(
            id=row["id"],
            managed_channel_id=row["managed_channel_id"],
            dispatcharr_stream_id=row["dispatcharr_stream_id"],
            stream_name=row.get("stream_name"),
            source_group_id=row.get("source_group_id"),
            source_group_type=row.get("source_group_type", "parent"),
            priority=row.get("priority", 0),
            m3u_account_id=row.get("m3u_account_id"),
            m3u_account_name=row.get("m3u_account_name"),
            exception_keyword=row.get("exception_keyword"),
            added_at=row.get("added_at"),
            removed_at=row.get("removed_at"),
        )


@dataclass
class ExceptionKeyword:
    """A consolidation exception keyword configuration."""

    id: int
    keywords: list[str]
    behavior: str
    display_name: str | None = None
    enabled: bool = True

    @classmethod
    def from_row(cls, row: dict) -> "ExceptionKeyword":
        """Create from database row dict."""
        keywords = row.get("keywords", "")
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        return cls(
            id=row["id"],
            keywords=keywords,
            behavior=row.get("behavior", "consolidate"),
            display_name=row.get("display_name"),
            enabled=bool(row.get("enabled", 1)),
        )


# =============================================================================
# MANAGED CHANNEL CRUD
# =============================================================================


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


# =============================================================================
# STREAM MANAGEMENT
# =============================================================================


def add_stream_to_channel(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    stream_name: str | None = None,
    priority: int = 0,
    **kwargs,
) -> int:
    """Add a stream to a managed channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        dispatcharr_stream_id: Stream ID in Dispatcharr
        stream_name: Stream display name
        priority: Stream priority (0 = primary)
        **kwargs: Additional fields

    Returns:
        ID of created stream record
    """
    columns = ["managed_channel_id", "dispatcharr_stream_id", "priority"]
    values = [managed_channel_id, dispatcharr_stream_id, priority]

    if stream_name:
        columns.append("stream_name")
        values.append(stream_name)

    allowed_fields = [
        "source_group_id",
        "source_group_type",
        "m3u_account_id",
        "m3u_account_name",
        "exception_keyword",
    ]

    for field_name in allowed_fields:
        if field_name in kwargs and kwargs[field_name] is not None:
            columns.append(field_name)
            values.append(kwargs[field_name])

    placeholders = ", ".join(["?"] * len(values))
    column_str = ", ".join(columns)

    cursor = conn.execute(
        f"INSERT INTO managed_channel_streams ({column_str}) VALUES ({placeholders})",
        values,
    )
    return cursor.lastrowid


def remove_stream_from_channel(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    reason: str | None = None,
) -> bool:
    """Soft-remove a stream from a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        dispatcharr_stream_id: Stream ID
        reason: Removal reason

    Returns:
        True if removed, False if not found
    """
    cursor = conn.execute(
        """UPDATE managed_channel_streams
           SET removed_at = datetime('now'),
               remove_reason = ?
           WHERE managed_channel_id = ?
             AND dispatcharr_stream_id = ?
             AND removed_at IS NULL""",
        (reason, managed_channel_id, dispatcharr_stream_id),
    )
    return cursor.rowcount > 0


def get_channel_streams(
    conn: Connection,
    managed_channel_id: int,
    include_removed: bool = False,
) -> list[ManagedChannelStream]:
    """Get all streams for a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        include_removed: Whether to include removed streams

    Returns:
        List of ManagedChannelStream objects (ordered by priority)
    """
    if include_removed:
        cursor = conn.execute(
            """SELECT * FROM managed_channel_streams
               WHERE managed_channel_id = ?
               ORDER BY priority, added_at""",
            (managed_channel_id,),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM managed_channel_streams
               WHERE managed_channel_id = ? AND removed_at IS NULL
               ORDER BY priority, added_at""",
            (managed_channel_id,),
        )
    return [ManagedChannelStream.from_row(dict(row)) for row in cursor.fetchall()]


def stream_exists_on_channel(
    conn: Connection,
    managed_channel_id: int,
    dispatcharr_stream_id: int,
) -> bool:
    """Check if stream is attached to channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID
        dispatcharr_stream_id: Stream ID

    Returns:
        True if stream exists on channel
    """
    cursor = conn.execute(
        """SELECT 1 FROM managed_channel_streams
           WHERE managed_channel_id = ?
             AND dispatcharr_stream_id = ?
             AND removed_at IS NULL""",
        (managed_channel_id, dispatcharr_stream_id),
    )
    return cursor.fetchone() is not None


def get_next_stream_priority(conn: Connection, managed_channel_id: int) -> int:
    """Get the next available stream priority for a channel.

    Args:
        conn: Database connection
        managed_channel_id: Channel ID

    Returns:
        Next priority number (max + 1, or 0 if no streams)
    """
    cursor = conn.execute(
        """SELECT COALESCE(MAX(priority), -1) + 1 FROM managed_channel_streams
           WHERE managed_channel_id = ? AND removed_at IS NULL""",
        (managed_channel_id,),
    )
    return cursor.fetchone()[0]


# =============================================================================
# HISTORY / AUDIT
# =============================================================================


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
    return cursor.rowcount


# =============================================================================
# EXCEPTION KEYWORDS
# =============================================================================


def get_exception_keywords(conn: Connection, enabled_only: bool = True) -> list[ExceptionKeyword]:
    """Get all consolidation exception keywords.

    Args:
        conn: Database connection
        enabled_only: Only return enabled keywords

    Returns:
        List of ExceptionKeyword objects
    """
    if enabled_only:
        cursor = conn.execute(
            "SELECT * FROM consolidation_exception_keywords WHERE enabled = 1"
        )
    else:
        cursor = conn.execute("SELECT * FROM consolidation_exception_keywords")
    return [ExceptionKeyword.from_row(dict(row)) for row in cursor.fetchall()]


def check_exception_keyword(
    stream_name: str,
    keywords: list[ExceptionKeyword],
) -> tuple[str | None, str | None]:
    """Check if stream name matches any exception keyword.

    Args:
        stream_name: Stream name to check
        keywords: List of ExceptionKeyword objects

    Returns:
        Tuple of (matched_keyword, behavior) or (None, None) if no match
    """
    stream_lower = stream_name.lower()

    for kw in keywords:
        for variant in kw.keywords:
            if variant.lower() in stream_lower:
                return (variant, kw.behavior)

    return (None, None)


# =============================================================================
# SETTINGS HELPERS
# =============================================================================


def get_dispatcharr_settings(conn: Connection) -> dict:
    """Get Dispatcharr integration settings.

    Args:
        conn: Database connection

    Returns:
        Dict with enabled, url, username, password, epg_id
    """
    cursor = conn.execute(
        """SELECT dispatcharr_enabled, dispatcharr_url, dispatcharr_username,
                  dispatcharr_password, dispatcharr_epg_id
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()
    if not row:
        return {
            "enabled": False,
            "url": None,
            "username": None,
            "password": None,
            "epg_id": None,
        }
    return {
        "enabled": bool(row["dispatcharr_enabled"]),
        "url": row["dispatcharr_url"],
        "username": row["dispatcharr_username"],
        "password": row["dispatcharr_password"],
        "epg_id": row["dispatcharr_epg_id"],
    }


def get_reconciliation_settings(conn: Connection) -> dict:
    """Get reconciliation settings.

    Args:
        conn: Database connection

    Returns:
        Dict with reconciliation settings
    """
    cursor = conn.execute(
        """SELECT reconcile_on_epg_generation, reconcile_on_startup,
                  auto_fix_orphan_teamarr, auto_fix_orphan_dispatcharr,
                  auto_fix_duplicates, default_duplicate_event_handling,
                  channel_history_retention_days
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()
    if not row:
        return {
            "reconcile_on_epg_generation": True,
            "reconcile_on_startup": True,
            "auto_fix_orphan_teamarr": True,
            "auto_fix_orphan_dispatcharr": False,
            "auto_fix_duplicates": False,
            "default_duplicate_event_handling": "consolidate",
            "channel_history_retention_days": 90,
        }
    return {
        "reconcile_on_epg_generation": bool(row["reconcile_on_epg_generation"]),
        "reconcile_on_startup": bool(row["reconcile_on_startup"]),
        "auto_fix_orphan_teamarr": bool(row["auto_fix_orphan_teamarr"]),
        "auto_fix_orphan_dispatcharr": bool(row["auto_fix_orphan_dispatcharr"]),
        "auto_fix_duplicates": bool(row["auto_fix_duplicates"]),
        "default_duplicate_event_handling": row["default_duplicate_event_handling"],
        "channel_history_retention_days": row["channel_history_retention_days"] or 90,
    }


def get_scheduler_settings(conn: Connection) -> dict:
    """Get scheduler settings.

    Args:
        conn: Database connection

    Returns:
        Dict with scheduler settings
    """
    cursor = conn.execute(
        """SELECT scheduler_enabled, scheduler_interval_minutes
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()
    if not row:
        return {"enabled": True, "interval_minutes": 15}
    return {
        "enabled": bool(row["scheduler_enabled"]),
        "interval_minutes": row["scheduler_interval_minutes"] or 15,
    }
