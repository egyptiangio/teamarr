"""Safe SQL query building utilities.

Provides column name validation to prevent SQL injection via dynamic column names.
"""

import re
from typing import Any

# Valid column names per table (whitelist approach)
VALID_COLUMNS: dict[str, set[str]] = {
    "teams": {
        "team_name",
        "team_abbrev",
        "league",
        "provider_team_id",
        "channel_id",
        "team_logo_url",
        "channel_logo_url",
        "template_id",
        "additional_leagues",
        "active",
        "updated_at",
    },
    "templates": {
        "name",
        "description",
        "title_format",
        "subtitle_template",
        "description_template",
        "pregame_title",
        "pregame_description",
        "postgame_title",
        "postgame_description",
        "idle_title",
        "idle_description",
        "xmltv_categories",
        "pregame_periods",
        "postgame_periods",
        "use_conditional_descriptions",
        "conditional_descriptions",
        "updated_at",
    },
    "event_epg_groups": {
        "name",
        "leagues",
        "template_id",
        "channel_start_number",
        "channel_group_id",
        "stream_profile_id",
        "channel_profile_ids",
        "create_timing",
        "delete_timing",
        "duplicate_event_handling",
        "channel_assignment_mode",
        "m3u_group_id",
        "m3u_group_name",
        "enabled",
        "sort_order",
        "total_stream_count",
        "parent_group_id",
    },
    "managed_channels": {
        "channel_name",
        "channel_number",
        "logo_url",
        "dispatcharr_channel_id",
        "dispatcharr_uuid",
        "sync_status",
        "sync_error",
        "scheduled_delete_at",
        "deleted_at",
        "updated_at",
    },
    "condition_presets": {
        "name",
        "description",
        "conditions",
    },
    "consolidation_exception_keywords": {
        "keywords",
        "behavior",
        "display_name",
        "enabled",
    },
    "settings": {
        # Settings has many columns - list primary ones
        "team_schedule_days_ahead",
        "event_match_days_ahead",
        "epg_output_days_ahead",
        "epg_lookback_hours",
        "duration_default",
        "duration_basketball",
        "duration_football",
        "duration_hockey",
        "duration_baseball",
        "duration_soccer",
        "dispatcharr_enabled",
        "dispatcharr_url",
        "dispatcharr_username",
        "dispatcharr_password",
        "scheduler_enabled",
        "scheduler_interval_minutes",
    },
}

# Pattern for valid SQL identifiers
VALID_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_column_name(column: str, table: str | None = None) -> bool:
    """Validate that a column name is safe for SQL.

    Args:
        column: Column name to validate
        table: Optional table name for whitelist check

    Returns:
        True if column is valid

    Raises:
        ValueError: If column name is invalid
    """
    # Basic format check
    if not VALID_IDENTIFIER_PATTERN.match(column):
        raise ValueError(f"Invalid column name format: {column}")

    # Length check
    if len(column) > 64:
        raise ValueError(f"Column name too long: {column}")

    # Table-specific whitelist check
    if table and table in VALID_COLUMNS:
        if column not in VALID_COLUMNS[table]:
            raise ValueError(f"Column '{column}' not allowed for table '{table}'")

    return True


def validate_columns(columns: list[str], table: str | None = None) -> bool:
    """Validate multiple column names.

    Args:
        columns: List of column names
        table: Optional table name for whitelist check

    Returns:
        True if all columns are valid

    Raises:
        ValueError: If any column is invalid
    """
    for col in columns:
        validate_column_name(col, table)
    return True


def build_update_query(
    table: str,
    updates: dict[str, Any],
    where_column: str = "id",
) -> tuple[str, list[Any]]:
    """Build a safe UPDATE query with validated column names.

    Args:
        table: Table name
        updates: Dict of column -> value to update
        where_column: Column for WHERE clause (default: id)

    Returns:
        Tuple of (query_string, values_list)

    Raises:
        ValueError: If table or column names are invalid
    """
    if not updates:
        raise ValueError("No updates provided")

    # Validate table name
    if not VALID_IDENTIFIER_PATTERN.match(table):
        raise ValueError(f"Invalid table name: {table}")

    # Validate all column names
    columns = list(updates.keys())
    validate_columns(columns, table)
    validate_column_name(where_column, table if where_column != "id" else None)

    # Build SET clause
    set_parts = [f"{col} = ?" for col in columns]
    set_clause = ", ".join(set_parts)

    # Build query
    query = f"UPDATE {table} SET {set_clause} WHERE {where_column} = ?"

    # Build values list (update values + where value placeholder)
    values = list(updates.values())

    return query, values


def build_insert_query(
    table: str,
    data: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Build a safe INSERT query with validated column names.

    Args:
        table: Table name
        data: Dict of column -> value to insert

    Returns:
        Tuple of (query_string, values_list)

    Raises:
        ValueError: If table or column names are invalid
    """
    if not data:
        raise ValueError("No data provided")

    # Validate table name
    if not VALID_IDENTIFIER_PATTERN.match(table):
        raise ValueError(f"Invalid table name: {table}")

    # Validate all column names
    columns = list(data.keys())
    validate_columns(columns, table)

    # Build query
    col_list = ", ".join(columns)
    placeholders = ", ".join("?" * len(columns))
    query = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    values = list(data.values())

    return query, values
