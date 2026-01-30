"""Database operations for event EPG groups.

Provides CRUD operations for the event_epg_groups table.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from sqlite3 import Connection

logger = logging.getLogger(__name__)


@dataclass
class EventEPGGroup:
    """Event EPG group configuration."""

    id: int
    name: str
    display_name: str | None = None  # Optional display name override for UI
    leagues: list[str] = field(default_factory=list)
    group_mode: str = "single"  # "single" or "multi" - persisted to preserve user intent
    template_id: int | None = None
    channel_start_number: int | None = None
    channel_group_id: int | None = None
    channel_group_mode: str = "static"  # "static", "sport", "league"
    channel_profile_ids: list[int | str] = field(
        default_factory=list
    )  # IDs or "{sport}", "{league}"
    stream_profile_id: int | None = None  # Stream profile (overrides global default)
    stream_timezone: str | None = None  # Timezone for stream datetime parsing
    duplicate_event_handling: str = "consolidate"
    channel_assignment_mode: str = "auto"
    sort_order: int = 0
    total_stream_count: int = 0
    parent_group_id: int | None = None
    m3u_group_id: int | None = None
    m3u_group_name: str | None = None
    m3u_account_id: int | None = None
    m3u_account_name: str | None = None
    # Processing stats
    last_refresh: datetime | None = None
    stream_count: int = 0
    matched_count: int = 0
    # Stream filtering (Phase 2)
    stream_include_regex: str | None = None
    stream_include_regex_enabled: bool = False
    stream_exclude_regex: str | None = None
    stream_exclude_regex_enabled: bool = False
    custom_regex_teams: str | None = None
    custom_regex_teams_enabled: bool = False
    custom_regex_date: str | None = None
    custom_regex_date_enabled: bool = False
    custom_regex_time: str | None = None
    custom_regex_time_enabled: bool = False
    custom_regex_league: str | None = None
    custom_regex_league_enabled: bool = False
    skip_builtin_filter: bool = False
    # Team filtering (canonical team selection, inherited by children)
    include_teams: list[dict] | None = None
    exclude_teams: list[dict] | None = None
    team_filter_mode: str = "include"
    # Processing stats by category (FILTERED / FAILED / EXCLUDED)
    filtered_stale: int = 0  # FILTERED: Stream marked as stale in Dispatcharr
    filtered_include_regex: int = 0  # FILTERED: Didn't match include regex
    filtered_exclude_regex: int = 0  # FILTERED: Matched exclude regex
    filtered_not_event: int = 0  # FILTERED: Stream doesn't look like event (placeholder)
    filtered_team: int = 0  # FILTERED: Team not in include/exclude filter
    failed_count: int = 0  # FAILED: Match attempted but couldn't find event
    streams_excluded: int = 0  # EXCLUDED: Matched but excluded (aggregate)
    # EXCLUDED breakdown by reason
    excluded_event_final: int = 0
    excluded_event_past: int = 0
    excluded_before_window: int = 0
    excluded_league_not_included: int = 0
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str = "time"
    overlap_handling: str = "add_stream"
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _row_to_group(row) -> EventEPGGroup:
    """Convert a database row to EventEPGGroup."""
    leagues = json.loads(row["leagues"]) if row["leagues"] else []
    channel_profile_ids = []
    if row["channel_profile_ids"]:
        try:
            channel_profile_ids = json.loads(row["channel_profile_ids"])
        except (json.JSONDecodeError, TypeError):
            pass

    created_at = None
    if row["created_at"]:
        try:
            created_at = datetime.fromisoformat(row["created_at"])
        except (ValueError, TypeError):
            pass

    updated_at = None
    if row["updated_at"]:
        try:
            updated_at = datetime.fromisoformat(row["updated_at"])
        except (ValueError, TypeError):
            pass

    last_refresh = None
    if row["last_refresh"]:
        try:
            last_refresh = datetime.fromisoformat(row["last_refresh"])
        except (ValueError, TypeError):
            pass

    return EventEPGGroup(
        id=row["id"],
        name=row["name"],
        display_name=row["display_name"] if "display_name" in row.keys() else None,
        leagues=leagues,
        group_mode=row["group_mode"] if "group_mode" in row.keys() else "single",
        template_id=row["template_id"],
        channel_start_number=row["channel_start_number"],
        channel_group_id=row["channel_group_id"],
        channel_group_mode=row["channel_group_mode"]
        if "channel_group_mode" in row.keys()
        else "static",
        channel_profile_ids=channel_profile_ids,
        stream_profile_id=row["stream_profile_id"] if "stream_profile_id" in row.keys() else None,
        stream_timezone=row["stream_timezone"] if "stream_timezone" in row.keys() else None,
        duplicate_event_handling=row["duplicate_event_handling"] or "consolidate",
        channel_assignment_mode=row["channel_assignment_mode"] or "auto",
        sort_order=row["sort_order"] or 0,
        total_stream_count=row["total_stream_count"] or 0,
        parent_group_id=row["parent_group_id"],
        m3u_group_id=row["m3u_group_id"],
        m3u_group_name=row["m3u_group_name"],
        m3u_account_id=row["m3u_account_id"],
        m3u_account_name=row["m3u_account_name"],
        last_refresh=last_refresh,
        stream_count=row["stream_count"] or 0,
        matched_count=row["matched_count"] or 0,
        # Stream filtering
        stream_include_regex=row["stream_include_regex"],
        stream_include_regex_enabled=bool(row["stream_include_regex_enabled"]),
        stream_exclude_regex=row["stream_exclude_regex"],
        stream_exclude_regex_enabled=bool(row["stream_exclude_regex_enabled"]),
        custom_regex_teams=row["custom_regex_teams"],
        custom_regex_teams_enabled=bool(row["custom_regex_teams_enabled"]),
        custom_regex_date=row["custom_regex_date"] if "custom_regex_date" in row.keys() else None,
        custom_regex_date_enabled=bool(row["custom_regex_date_enabled"])
        if "custom_regex_date_enabled" in row.keys()
        else False,
        custom_regex_time=row["custom_regex_time"] if "custom_regex_time" in row.keys() else None,
        custom_regex_time_enabled=bool(row["custom_regex_time_enabled"])
        if "custom_regex_time_enabled" in row.keys()
        else False,
        custom_regex_league=row["custom_regex_league"]
        if "custom_regex_league" in row.keys()
        else None,
        custom_regex_league_enabled=bool(row["custom_regex_league_enabled"])
        if "custom_regex_league_enabled" in row.keys()
        else False,
        skip_builtin_filter=bool(row["skip_builtin_filter"]),
        # Team filtering
        include_teams=json.loads(row["include_teams"]) if row["include_teams"] else None,
        exclude_teams=json.loads(row["exclude_teams"]) if row["exclude_teams"] else None,
        team_filter_mode=row["team_filter_mode"] if "team_filter_mode" in row.keys() else "include",
        # Processing stats by category (FILTERED / FAILED / EXCLUDED)
        filtered_stale=row["filtered_stale"] if "filtered_stale" in row.keys() else 0,
        filtered_include_regex=row["filtered_include_regex"] or 0,
        filtered_exclude_regex=row["filtered_exclude_regex"] or 0,
        filtered_not_event=row["filtered_not_event"] if "filtered_not_event" in row.keys() else 0,
        filtered_team=row["filtered_team"] if "filtered_team" in row.keys() else 0,
        # Handle both old (filtered_no_match) and new (failed_count) column names
        failed_count=(
            row["failed_count"]
            if "failed_count" in row.keys()
            else (row["filtered_no_match"] if "filtered_no_match" in row.keys() else 0)
        )
        or 0,
        streams_excluded=row["streams_excluded"] if "streams_excluded" in row.keys() else 0,
        # EXCLUDED breakdown by reason
        excluded_event_final=row["excluded_event_final"]
        if "excluded_event_final" in row.keys()
        else 0,
        excluded_event_past=row["excluded_event_past"]
        if "excluded_event_past" in row.keys()
        else 0,
        excluded_before_window=row["excluded_before_window"]
        if "excluded_before_window" in row.keys()
        else 0,
        excluded_league_not_included=row["excluded_league_not_included"]
        if "excluded_league_not_included" in row.keys()
        else 0,
        # Multi-sport enhancements
        channel_sort_order=row["channel_sort_order"] or "time",
        overlap_handling=row["overlap_handling"] or "add_stream",
        enabled=bool(row["enabled"]),
        created_at=created_at,
        updated_at=updated_at,
    )


# =============================================================================
# READ OPERATIONS
# =============================================================================


def get_all_groups(conn: Connection, include_disabled: bool = False) -> list[EventEPGGroup]:
    """Get all event EPG groups.

    Args:
        conn: Database connection
        include_disabled: Include disabled groups

    Returns:
        List of EventEPGGroup objects
    """
    if include_disabled:
        cursor = conn.execute("SELECT * FROM event_epg_groups ORDER BY sort_order, name")
    else:
        cursor = conn.execute(
            "SELECT * FROM event_epg_groups WHERE enabled = 1 ORDER BY sort_order, name"
        )

    return [_row_to_group(row) for row in cursor.fetchall()]


def get_group(conn: Connection, group_id: int) -> EventEPGGroup | None:
    """Get a single event EPG group by ID.

    Args:
        conn: Database connection
        group_id: Group ID

    Returns:
        EventEPGGroup or None if not found
    """
    cursor = conn.execute("SELECT * FROM event_epg_groups WHERE id = ?", (group_id,))
    row = cursor.fetchone()
    return _row_to_group(row) if row else None


def get_group_by_name(
    conn: Connection, name: str, m3u_account_id: int | None = None
) -> EventEPGGroup | None:
    """Get a single event EPG group by name (optionally scoped to account).

    Args:
        conn: Database connection
        name: Group name
        m3u_account_id: If provided, checks for name within this account only

    Returns:
        EventEPGGroup or None if not found
    """
    if m3u_account_id is not None:
        cursor = conn.execute(
            "SELECT * FROM event_epg_groups WHERE name = ? AND m3u_account_id = ?",
            (name, m3u_account_id),
        )
    else:
        cursor = conn.execute("SELECT * FROM event_epg_groups WHERE name = ?", (name,))
    row = cursor.fetchone()
    return _row_to_group(row) if row else None


def get_groups_for_league(conn: Connection, league: str) -> list[EventEPGGroup]:
    """Get all enabled groups that include a specific league.

    Args:
        conn: Database connection
        league: League code to search for

    Returns:
        List of EventEPGGroup objects that include the league
    """
    cursor = conn.execute(
        "SELECT * FROM event_epg_groups WHERE enabled = 1 ORDER BY sort_order, name"
    )

    groups = []
    for row in cursor.fetchall():
        leagues = json.loads(row["leagues"]) if row["leagues"] else []
        if league in leagues:
            groups.append(_row_to_group(row))

    return groups


# =============================================================================
# CREATE OPERATIONS
# =============================================================================


def create_group(
    conn: Connection,
    name: str,
    leagues: list[str],
    display_name: str | None = None,
    group_mode: str = "single",
    template_id: int | None = None,
    channel_start_number: int | None = None,
    channel_group_id: int | None = None,
    channel_group_mode: str = "static",
    channel_profile_ids: list[int | str] | None = None,
    stream_profile_id: int | None = None,
    stream_timezone: str | None = None,
    duplicate_event_handling: str = "consolidate",
    channel_assignment_mode: str = "auto",
    sort_order: int = 0,
    total_stream_count: int = 0,
    parent_group_id: int | None = None,
    m3u_group_id: int | None = None,
    m3u_group_name: str | None = None,
    m3u_account_id: int | None = None,
    m3u_account_name: str | None = None,
    # Stream filtering
    stream_include_regex: str | None = None,
    stream_include_regex_enabled: bool = False,
    stream_exclude_regex: str | None = None,
    stream_exclude_regex_enabled: bool = False,
    custom_regex_teams: str | None = None,
    custom_regex_teams_enabled: bool = False,
    custom_regex_date: str | None = None,
    custom_regex_date_enabled: bool = False,
    custom_regex_time: str | None = None,
    custom_regex_time_enabled: bool = False,
    custom_regex_league: str | None = None,
    custom_regex_league_enabled: bool = False,
    skip_builtin_filter: bool = False,
    # Team filtering
    include_teams: list[dict] | None = None,
    exclude_teams: list[dict] | None = None,
    team_filter_mode: str = "include",
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str = "time",
    overlap_handling: str = "add_stream",
    enabled: bool = True,
) -> int:
    """Create a new event EPG group.

    Args:
        conn: Database connection
        name: Unique group name
        leagues: List of league codes to scan
        template_id: Optional template ID
        channel_start_number: Starting channel number (for MANUAL mode)
        channel_group_id: Dispatcharr channel group ID
        channel_profile_ids: List of channel profile IDs
        duplicate_event_handling: How to handle duplicate events
        channel_assignment_mode: 'auto' or 'manual'
        sort_order: Ordering for AUTO channel allocation
        total_stream_count: Expected streams for range reservation
        parent_group_id: Parent group for child relationships
        m3u_group_id: M3U group ID to scan
        m3u_group_name: M3U group name
        m3u_account_id: M3U account ID
        m3u_account_name: M3U account name for display
        enabled: Whether group is enabled

    Returns:
        New group ID
    """
    # Auto-calculate sort_order for AUTO mode groups
    if channel_assignment_mode == "auto" and sort_order == 0:
        max_order = conn.execute(
            """SELECT COALESCE(MAX(sort_order), -1) + 1
               FROM event_epg_groups
               WHERE channel_assignment_mode = 'auto'
                 AND parent_group_id IS NULL"""
        ).fetchone()[0]
        sort_order = max_order

    cursor = conn.execute(
        """INSERT INTO event_epg_groups (
            name, display_name, leagues, group_mode, template_id, channel_start_number,
            channel_group_id, channel_group_mode, channel_profile_ids, stream_profile_id,
            stream_timezone, duplicate_event_handling, channel_assignment_mode, sort_order,
            total_stream_count, parent_group_id, m3u_group_id, m3u_group_name,
            m3u_account_id, m3u_account_name,
            stream_include_regex, stream_include_regex_enabled,
            stream_exclude_regex, stream_exclude_regex_enabled,
            custom_regex_teams, custom_regex_teams_enabled,
            custom_regex_date, custom_regex_date_enabled,
            custom_regex_time, custom_regex_time_enabled,
            custom_regex_league, custom_regex_league_enabled,
            skip_builtin_filter,
            include_teams, exclude_teams, team_filter_mode,
            channel_sort_order, overlap_handling, enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",  # noqa: E501
        (
            name,
            display_name,
            json.dumps(leagues),
            group_mode,
            template_id,
            channel_start_number,
            channel_group_id,
            channel_group_mode,
            json.dumps(channel_profile_ids) if channel_profile_ids else None,
            stream_profile_id,
            stream_timezone,
            duplicate_event_handling,
            channel_assignment_mode,
            sort_order,
            total_stream_count,
            parent_group_id,
            m3u_group_id,
            m3u_group_name,
            m3u_account_id,
            m3u_account_name,
            stream_include_regex,
            int(stream_include_regex_enabled),
            stream_exclude_regex,
            int(stream_exclude_regex_enabled),
            custom_regex_teams,
            int(custom_regex_teams_enabled),
            custom_regex_date,
            int(custom_regex_date_enabled),
            custom_regex_time,
            int(custom_regex_time_enabled),
            custom_regex_league,
            int(custom_regex_league_enabled),
            int(skip_builtin_filter),
            json.dumps(include_teams) if include_teams else None,
            json.dumps(exclude_teams) if exclude_teams else None,
            team_filter_mode,
            channel_sort_order,
            overlap_handling,
            int(enabled),
        ),
    )
    group_id = cursor.lastrowid
    logger.info("[CREATED] Event group id=%d name=%s", group_id, name)
    return group_id


# =============================================================================
# UPDATE OPERATIONS
# =============================================================================


def update_group(
    conn: Connection,
    group_id: int,
    name: str | None = None,
    display_name: str | None = None,
    leagues: list[str] | None = None,
    group_mode: str | None = None,
    template_id: int | None = None,
    channel_start_number: int | None = None,
    channel_group_id: int | None = None,
    channel_group_mode: str | None = None,
    channel_profile_ids: list[int | str] | None = None,
    stream_profile_id: int | None = None,
    stream_timezone: str | None = None,
    duplicate_event_handling: str | None = None,
    channel_assignment_mode: str | None = None,
    sort_order: int | None = None,
    total_stream_count: int | None = None,
    parent_group_id: int | None = None,
    m3u_group_id: int | None = None,
    m3u_group_name: str | None = None,
    m3u_account_id: int | None = None,
    m3u_account_name: str | None = None,
    # Stream filtering
    stream_include_regex: str | None = None,
    stream_include_regex_enabled: bool | None = None,
    stream_exclude_regex: str | None = None,
    stream_exclude_regex_enabled: bool | None = None,
    custom_regex_teams: str | None = None,
    custom_regex_teams_enabled: bool | None = None,
    custom_regex_date: str | None = None,
    custom_regex_date_enabled: bool | None = None,
    custom_regex_time: str | None = None,
    custom_regex_time_enabled: bool | None = None,
    custom_regex_league: str | None = None,
    custom_regex_league_enabled: bool | None = None,
    skip_builtin_filter: bool | None = None,
    # Team filtering
    include_teams: list[dict] | None = None,
    exclude_teams: list[dict] | None = None,
    team_filter_mode: str | None = None,
    # Multi-sport enhancements (Phase 3)
    channel_sort_order: str | None = None,
    overlap_handling: str | None = None,
    enabled: bool | None = None,
    # Clear flags
    clear_display_name: bool = False,
    clear_template: bool = False,
    clear_channel_start_number: bool = False,
    clear_channel_group_id: bool = False,
    clear_channel_profile_ids: bool = False,
    clear_stream_profile_id: bool = False,
    clear_stream_timezone: bool = False,
    clear_parent_group_id: bool = False,
    clear_m3u_group_id: bool = False,
    clear_m3u_group_name: bool = False,
    clear_m3u_account_id: bool = False,
    clear_m3u_account_name: bool = False,
    clear_stream_include_regex: bool = False,
    clear_stream_exclude_regex: bool = False,
    clear_custom_regex_teams: bool = False,
    clear_custom_regex_date: bool = False,
    clear_custom_regex_time: bool = False,
    clear_custom_regex_league: bool = False,
    clear_include_teams: bool = False,
    clear_exclude_teams: bool = False,
) -> bool:
    """Update an event EPG group.

    Only updates fields that are explicitly provided (not None).
    Use clear_* flags to explicitly set fields to NULL.

    Args:
        conn: Database connection
        group_id: Group ID to update
        ... (field parameters)
        clear_*: Set corresponding field to NULL

    Returns:
        True if updated
    """
    updates = []
    values = []

    if name is not None:
        updates.append("name = ?")
        values.append(name)

    if display_name is not None:
        updates.append("display_name = ?")
        values.append(display_name)
    elif clear_display_name:
        updates.append("display_name = NULL")

    if leagues is not None:
        updates.append("leagues = ?")
        values.append(json.dumps(leagues))

    if group_mode is not None:
        updates.append("group_mode = ?")
        values.append(group_mode)

    if template_id is not None:
        updates.append("template_id = ?")
        values.append(template_id)
    elif clear_template:
        updates.append("template_id = NULL")

    if channel_start_number is not None:
        updates.append("channel_start_number = ?")
        values.append(channel_start_number)
    elif clear_channel_start_number:
        updates.append("channel_start_number = NULL")

    if channel_group_id is not None:
        updates.append("channel_group_id = ?")
        values.append(channel_group_id)
    elif clear_channel_group_id:
        updates.append("channel_group_id = NULL")

    if channel_group_mode is not None:
        updates.append("channel_group_mode = ?")
        values.append(channel_group_mode)

    if channel_profile_ids is not None:
        updates.append("channel_profile_ids = ?")
        values.append(json.dumps(channel_profile_ids))
    elif clear_channel_profile_ids:
        updates.append("channel_profile_ids = NULL")

    if stream_profile_id is not None:
        updates.append("stream_profile_id = ?")
        values.append(stream_profile_id)
    elif clear_stream_profile_id:
        updates.append("stream_profile_id = NULL")

    if stream_timezone is not None:
        updates.append("stream_timezone = ?")
        values.append(stream_timezone)
    elif clear_stream_timezone:
        updates.append("stream_timezone = NULL")

    if duplicate_event_handling is not None:
        updates.append("duplicate_event_handling = ?")
        values.append(duplicate_event_handling)

    if channel_assignment_mode is not None:
        updates.append("channel_assignment_mode = ?")
        values.append(channel_assignment_mode)

    if sort_order is not None:
        updates.append("sort_order = ?")
        values.append(sort_order)

    if total_stream_count is not None:
        updates.append("total_stream_count = ?")
        values.append(total_stream_count)

    if parent_group_id is not None:
        updates.append("parent_group_id = ?")
        values.append(parent_group_id)
    elif clear_parent_group_id:
        updates.append("parent_group_id = NULL")

    if m3u_group_id is not None:
        updates.append("m3u_group_id = ?")
        values.append(m3u_group_id)
    elif clear_m3u_group_id:
        updates.append("m3u_group_id = NULL")

    if m3u_group_name is not None:
        updates.append("m3u_group_name = ?")
        values.append(m3u_group_name)
    elif clear_m3u_group_name:
        updates.append("m3u_group_name = NULL")

    if m3u_account_id is not None:
        updates.append("m3u_account_id = ?")
        values.append(m3u_account_id)
    elif clear_m3u_account_id:
        updates.append("m3u_account_id = NULL")

    if m3u_account_name is not None:
        updates.append("m3u_account_name = ?")
        values.append(m3u_account_name)
    elif clear_m3u_account_name:
        updates.append("m3u_account_name = NULL")

    # Stream filtering fields
    if stream_include_regex is not None:
        updates.append("stream_include_regex = ?")
        values.append(stream_include_regex)
    elif clear_stream_include_regex:
        updates.append("stream_include_regex = NULL")

    if stream_include_regex_enabled is not None:
        updates.append("stream_include_regex_enabled = ?")
        values.append(int(stream_include_regex_enabled))

    if stream_exclude_regex is not None:
        updates.append("stream_exclude_regex = ?")
        values.append(stream_exclude_regex)
    elif clear_stream_exclude_regex:
        updates.append("stream_exclude_regex = NULL")

    if stream_exclude_regex_enabled is not None:
        updates.append("stream_exclude_regex_enabled = ?")
        values.append(int(stream_exclude_regex_enabled))

    if custom_regex_teams is not None:
        updates.append("custom_regex_teams = ?")
        values.append(custom_regex_teams)
    elif clear_custom_regex_teams:
        updates.append("custom_regex_teams = NULL")

    if custom_regex_teams_enabled is not None:
        updates.append("custom_regex_teams_enabled = ?")
        values.append(int(custom_regex_teams_enabled))

    if custom_regex_date is not None:
        updates.append("custom_regex_date = ?")
        values.append(custom_regex_date)
    elif clear_custom_regex_date:
        updates.append("custom_regex_date = NULL")

    if custom_regex_date_enabled is not None:
        updates.append("custom_regex_date_enabled = ?")
        values.append(int(custom_regex_date_enabled))

    if custom_regex_time is not None:
        updates.append("custom_regex_time = ?")
        values.append(custom_regex_time)
    elif clear_custom_regex_time:
        updates.append("custom_regex_time = NULL")

    if custom_regex_time_enabled is not None:
        updates.append("custom_regex_time_enabled = ?")
        values.append(int(custom_regex_time_enabled))

    if custom_regex_league is not None:
        updates.append("custom_regex_league = ?")
        values.append(custom_regex_league)
    elif clear_custom_regex_league:
        updates.append("custom_regex_league = NULL")

    if custom_regex_league_enabled is not None:
        updates.append("custom_regex_league_enabled = ?")
        values.append(int(custom_regex_league_enabled))

    if skip_builtin_filter is not None:
        updates.append("skip_builtin_filter = ?")
        values.append(int(skip_builtin_filter))

    # Team filtering - treat empty list as clear (NULL)
    if include_teams is not None:
        if include_teams:  # Non-empty list
            updates.append("include_teams = ?")
            values.append(json.dumps(include_teams))
        else:  # Empty list - clear to NULL
            updates.append("include_teams = NULL")
    elif clear_include_teams:
        updates.append("include_teams = NULL")

    if exclude_teams is not None:
        if exclude_teams:  # Non-empty list
            updates.append("exclude_teams = ?")
            values.append(json.dumps(exclude_teams))
        else:  # Empty list - clear to NULL
            updates.append("exclude_teams = NULL")
    elif clear_exclude_teams:
        updates.append("exclude_teams = NULL")

    if team_filter_mode is not None:
        updates.append("team_filter_mode = ?")
        values.append(team_filter_mode)

    # Multi-sport enhancements (Phase 3)
    if channel_sort_order is not None:
        updates.append("channel_sort_order = ?")
        values.append(channel_sort_order)

    if overlap_handling is not None:
        updates.append("overlap_handling = ?")
        values.append(overlap_handling)

    if enabled is not None:
        updates.append("enabled = ?")
        values.append(int(enabled))

    if not updates:
        return False

    values.append(group_id)
    query = f"UPDATE event_epg_groups SET {', '.join(updates)} WHERE id = ?"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Event group id=%d", group_id)
        return True
    return False


def set_group_enabled(conn: Connection, group_id: int, enabled: bool) -> bool:
    """Set group enabled status.

    Args:
        conn: Database connection
        group_id: Group ID
        enabled: New enabled status

    Returns:
        True if updated
    """
    cursor = conn.execute(
        "UPDATE event_epg_groups SET enabled = ? WHERE id = ?", (int(enabled), group_id)
    )
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Event group id=%d enabled=%s", group_id, enabled)
        return True
    return False


def update_group_stats(
    conn: Connection,
    group_id: int,
    stream_count: int,
    matched_count: int,
    filtered_stale: int = 0,
    filtered_include_regex: int = 0,
    filtered_exclude_regex: int = 0,
    filtered_not_event: int = 0,
    filtered_team: int = 0,
    failed_count: int = 0,
    streams_excluded: int = 0,
    total_stream_count: int | None = None,
    # EXCLUDED breakdown
    excluded_event_final: int = 0,
    excluded_event_past: int = 0,
    excluded_before_window: int = 0,
    excluded_league_not_included: int = 0,
) -> bool:
    """Update processing stats for a group after EPG generation.

    Stats are organized into three categories:
    - FILTERED: Pre-match filtering (stale, regex, not_event, team)
    - FAILED: Match attempted but couldn't find event
    - EXCLUDED: Matched but excluded (timing/config)

    Args:
        conn: Database connection
        group_id: Group ID
        stream_count: Number of streams after filtering (eligible for matching)
        matched_count: Number of streams successfully matched to events
        filtered_stale: FILTERED - Stream marked as stale in Dispatcharr
        filtered_include_regex: FILTERED - Didn't match include regex
        filtered_exclude_regex: FILTERED - Matched exclude regex
        filtered_not_event: FILTERED - Stream doesn't look like event
        filtered_team: FILTERED - Team not in include/exclude list
        failed_count: FAILED - Match attempted but couldn't find event
        streams_excluded: EXCLUDED - Matched but excluded (aggregate)
        total_stream_count: Total streams fetched (before filtering)
        excluded_event_final: EXCLUDED - Event status is final
        excluded_event_past: EXCLUDED - Event already ended
        excluded_before_window: EXCLUDED - Too early to create channel
        excluded_league_not_included: EXCLUDED - League not in group

    Returns:
        True if updated
    """
    if total_stream_count is not None:
        cursor = conn.execute(
            """UPDATE event_epg_groups
               SET last_refresh = datetime('now'),
                   stream_count = ?,
                   matched_count = ?,
                   filtered_stale = ?,
                   filtered_include_regex = ?,
                   filtered_exclude_regex = ?,
                   filtered_not_event = ?,
                   filtered_team = ?,
                   failed_count = ?,
                   streams_excluded = ?,
                   excluded_event_final = ?,
                   excluded_event_past = ?,
                   excluded_before_window = ?,
                   excluded_league_not_included = ?,
                   total_stream_count = ?
               WHERE id = ?""",
            (
                stream_count,
                matched_count,
                filtered_stale,
                filtered_include_regex,
                filtered_exclude_regex,
                filtered_not_event,
                filtered_team,
                failed_count,
                streams_excluded,
                excluded_event_final,
                excluded_event_past,
                excluded_before_window,
                excluded_league_not_included,
                total_stream_count,
                group_id,
            ),
        )
    else:
        cursor = conn.execute(
            """UPDATE event_epg_groups
               SET last_refresh = datetime('now'),
                   stream_count = ?,
                   matched_count = ?,
                   filtered_stale = ?,
                   filtered_include_regex = ?,
                   filtered_exclude_regex = ?,
                   filtered_not_event = ?,
                   filtered_team = ?,
                   failed_count = ?,
                   streams_excluded = ?,
                   excluded_event_final = ?,
                   excluded_event_past = ?,
                   excluded_before_window = ?,
                   excluded_league_not_included = ?
               WHERE id = ?""",
            (
                stream_count,
                matched_count,
                filtered_stale,
                filtered_include_regex,
                filtered_exclude_regex,
                filtered_not_event,
                filtered_team,
                failed_count,
                streams_excluded,
                excluded_event_final,
                excluded_event_past,
                excluded_before_window,
                excluded_league_not_included,
                group_id,
            ),
        )
    return cursor.rowcount > 0


# =============================================================================
# DELETE OPERATIONS
# =============================================================================


def delete_group(conn: Connection, group_id: int) -> bool:
    """Delete an event EPG group and all its children.

    Note: This will cascade delete all managed_channels for this group
    and recursively delete all child groups.

    Args:
        conn: Database connection
        group_id: Group ID to delete

    Returns:
        True if deleted
    """
    # Recursively delete child groups first
    cursor = conn.execute("SELECT id FROM event_epg_groups WHERE parent_group_id = ?", (group_id,))
    for row in cursor.fetchall():
        delete_group(conn, row["id"])

    # Delete the group itself
    cursor = conn.execute("DELETE FROM event_epg_groups WHERE id = ?", (group_id,))
    if cursor.rowcount > 0:
        logger.info("[DELETED] Event group id=%d", group_id)
        return True
    return False


# =============================================================================
# STATS / HELPERS
# =============================================================================


def get_group_channel_count(conn: Connection, group_id: int) -> int:
    """Get count of managed channels for a group.

    Args:
        conn: Database connection
        group_id: Group ID

    Returns:
        Number of managed channels (active, not deleted)
    """
    cursor = conn.execute(
        """SELECT COUNT(*) as count FROM managed_channels
           WHERE event_epg_group_id = ? AND deleted_at IS NULL""",
        (group_id,),
    )
    row = cursor.fetchone()
    return row["count"] if row else 0


def get_group_stats(conn: Connection, group_id: int) -> dict:
    """Get statistics for a group.

    Args:
        conn: Database connection
        group_id: Group ID

    Returns:
        Dict with channel counts and status breakdown
    """
    cursor = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) as active,
            SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) as deleted,
            SUM(CASE WHEN sync_status = 'pending' AND deleted_at IS NULL
                THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN sync_status = 'created' AND deleted_at IS NULL
                THEN 1 ELSE 0 END) as created,
            SUM(CASE WHEN sync_status = 'in_sync' AND deleted_at IS NULL
                THEN 1 ELSE 0 END) as in_sync,
            SUM(CASE WHEN sync_status = 'error' AND deleted_at IS NULL
                THEN 1 ELSE 0 END) as errors
        FROM managed_channels
        WHERE event_epg_group_id = ?""",
        (group_id,),
    )
    row = cursor.fetchone()

    if not row:
        return {"total": 0, "active": 0, "deleted": 0, "by_status": {}}

    return {
        "total": row["total"] or 0,
        "active": row["active"] or 0,
        "deleted": row["deleted"] or 0,
        "by_status": {
            "pending": row["pending"] or 0,
            "created": row["created"] or 0,
            "in_sync": row["in_sync"] or 0,
            "errors": row["errors"] or 0,
        },
    }


def get_all_group_stats(conn: Connection) -> dict[int, dict]:
    """Get statistics for all groups.

    Args:
        conn: Database connection

    Returns:
        Dict mapping group_id to stats dict
    """
    cursor = conn.execute(
        """SELECT
            event_epg_group_id,
            COUNT(*) as total,
            SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) as active
        FROM managed_channels
        GROUP BY event_epg_group_id"""
    )

    stats = {}
    for row in cursor.fetchall():
        stats[row["event_epg_group_id"]] = {
            "total": row["total"] or 0,
            "active": row["active"] or 0,
        }

    return stats


# =============================================================================
# XMLTV CONTENT OPERATIONS
# =============================================================================


def get_group_xmltv(conn: Connection, group_id: int) -> str | None:
    """Get stored XMLTV content for a group.

    Args:
        conn: Database connection
        group_id: Group ID

    Returns:
        XMLTV content string or None if not found
    """
    cursor = conn.execute(
        "SELECT xmltv_content FROM event_epg_xmltv WHERE group_id = ?",
        (group_id,),
    )
    row = cursor.fetchone()
    return row["xmltv_content"] if row else None


def get_all_group_xmltv(conn: Connection, group_ids: list[int] | None = None) -> list[str]:
    """Get stored XMLTV content for multiple groups.

    Args:
        conn: Database connection
        group_ids: Optional list of group IDs (None = all active groups)

    Returns:
        List of XMLTV content strings (non-empty only)
    """
    if group_ids:
        placeholders = ",".join("?" * len(group_ids))
        cursor = conn.execute(
            f"""SELECT xmltv_content FROM event_epg_xmltv
                WHERE group_id IN ({placeholders})
                AND xmltv_content IS NOT NULL AND xmltv_content != ''""",
            group_ids,
        )
    else:
        # Get XMLTV for all enabled groups
        cursor = conn.execute(
            """SELECT x.xmltv_content FROM event_epg_xmltv x
               JOIN event_epg_groups g ON x.group_id = g.id
               WHERE g.enabled = 1
               AND x.xmltv_content IS NOT NULL AND x.xmltv_content != ''"""
        )

    return [row["xmltv_content"] for row in cursor.fetchall()]


def store_group_xmltv(conn: Connection, group_id: int, xmltv_content: str) -> None:
    """Store XMLTV content for a group.

    Args:
        conn: Database connection
        group_id: Group ID
        xmltv_content: XMLTV content string
    """
    conn.execute(
        """INSERT INTO event_epg_xmltv (group_id, xmltv_content, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(group_id) DO UPDATE SET
               xmltv_content = excluded.xmltv_content,
               updated_at = datetime('now')""",
        (group_id, xmltv_content),
    )
    conn.commit()
    logger.debug("[STORED] XMLTV for group id=%d size=%d", group_id, len(xmltv_content))


def delete_group_xmltv(conn: Connection, group_id: int) -> bool:
    """Delete stored XMLTV content for a group.

    Args:
        conn: Database connection
        group_id: Group ID

    Returns:
        True if deleted
    """
    cursor = conn.execute("DELETE FROM event_epg_xmltv WHERE group_id = ?", (group_id,))
    conn.commit()
    if cursor.rowcount > 0:
        logger.debug("[DELETED] XMLTV for group id=%d", group_id)
        return True
    return False
