"""Settings update operations.

Functions to modify settings in the database.
"""

from sqlite3 import Connection


def update_dispatcharr_settings(
    conn: Connection,
    enabled: bool | None = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    epg_id: int | None = None,
) -> bool:
    """Update Dispatcharr settings.

    Only updates fields that are explicitly provided (not None).

    Args:
        conn: Database connection
        enabled: Enable/disable integration
        url: Dispatcharr URL
        username: Username
        password: Password
        epg_id: EPG source ID in Dispatcharr

    Returns:
        True if updated
    """
    updates = []
    values = []

    if enabled is not None:
        updates.append("dispatcharr_enabled = ?")
        values.append(int(enabled))
    if url is not None:
        updates.append("dispatcharr_url = ?")
        values.append(url)
    if username is not None:
        updates.append("dispatcharr_username = ?")
        values.append(username)
    if password is not None:
        updates.append("dispatcharr_password = ?")
        values.append(password)
    if epg_id is not None:
        updates.append("dispatcharr_epg_id = ?")
        values.append(epg_id)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def update_scheduler_settings(
    conn: Connection,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
) -> bool:
    """Update scheduler settings.

    Args:
        conn: Database connection
        enabled: Enable/disable scheduler
        interval_minutes: Minutes between runs

    Returns:
        True if updated
    """
    updates = []
    values = []

    if enabled is not None:
        updates.append("scheduler_enabled = ?")
        values.append(int(enabled))
    if interval_minutes is not None:
        updates.append("scheduler_interval_minutes = ?")
        values.append(interval_minutes)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def update_lifecycle_settings(
    conn: Connection,
    channel_create_timing: str | None = None,
    channel_delete_timing: str | None = None,
    channel_range_start: int | None = None,
    channel_range_end: int | None = None,
) -> bool:
    """Update channel lifecycle settings.

    Args:
        conn: Database connection
        channel_create_timing: When to create channels
        channel_delete_timing: When to delete channels
        channel_range_start: First auto-assigned channel number
        channel_range_end: Last auto-assigned channel number

    Returns:
        True if updated
    """
    updates = []
    values = []

    if channel_create_timing is not None:
        updates.append("channel_create_timing = ?")
        values.append(channel_create_timing)
    if channel_delete_timing is not None:
        updates.append("channel_delete_timing = ?")
        values.append(channel_delete_timing)
    if channel_range_start is not None:
        updates.append("channel_range_start = ?")
        values.append(channel_range_start)
    if channel_range_end is not None:
        updates.append("channel_range_end = ?")
        values.append(channel_range_end)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def update_epg_settings(conn: Connection, **kwargs) -> bool:
    """Update EPG generation settings.

    Args:
        conn: Database connection
        **kwargs: EPG settings to update

    Returns:
        True if updated
    """
    field_mapping = {
        "team_schedule_days_ahead": "team_schedule_days_ahead",
        "event_match_days_ahead": "event_match_days_ahead",
        "epg_output_days_ahead": "epg_output_days_ahead",
        "epg_lookback_hours": "epg_lookback_hours",
        "epg_timezone": "epg_timezone",
        "epg_output_path": "epg_output_path",
        "include_final_events": "include_final_events",
        "midnight_crossover_mode": "midnight_crossover_mode",
        "cron_expression": "cron_expression",
    }

    updates = []
    values = []

    for key, column in field_mapping.items():
        if key in kwargs and kwargs[key] is not None:
            updates.append(f"{column} = ?")
            value = kwargs[key]
            if isinstance(value, bool):
                value = int(value)
            values.append(value)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def update_reconciliation_settings(conn: Connection, **kwargs) -> bool:
    """Update reconciliation settings.

    Args:
        conn: Database connection
        **kwargs: Reconciliation settings to update

    Returns:
        True if updated
    """
    field_mapping = {
        "reconcile_on_epg_generation": "reconcile_on_epg_generation",
        "reconcile_on_startup": "reconcile_on_startup",
        "auto_fix_orphan_teamarr": "auto_fix_orphan_teamarr",
        "auto_fix_orphan_dispatcharr": "auto_fix_orphan_dispatcharr",
        "auto_fix_duplicates": "auto_fix_duplicates",
        "default_duplicate_event_handling": "default_duplicate_event_handling",
        "channel_history_retention_days": "channel_history_retention_days",
    }

    updates = []
    values = []

    for key, column in field_mapping.items():
        if key in kwargs and kwargs[key] is not None:
            updates.append(f"{column} = ?")
            value = kwargs[key]
            if isinstance(value, bool):
                value = int(value)
            values.append(value)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def update_duration_settings(conn: Connection, **kwargs) -> bool:
    """Update game duration settings.

    Args:
        conn: Database connection
        **kwargs: Duration settings (default, basketball, football, etc.)

    Returns:
        True if updated
    """
    field_mapping = {
        "default": "duration_default",
        "basketball": "duration_basketball",
        "football": "duration_football",
        "hockey": "duration_hockey",
        "baseball": "duration_baseball",
        "soccer": "duration_soccer",
        "mma": "duration_mma",
        "rugby": "duration_rugby",
        "boxing": "duration_boxing",
        "tennis": "duration_tennis",
        "golf": "duration_golf",
        "racing": "duration_racing",
        "cricket": "duration_cricket",
    }

    updates = []
    values = []

    for key, column in field_mapping.items():
        if key in kwargs and kwargs[key] is not None:
            updates.append(f"{column} = ?")
            values.append(kwargs[key])

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def update_display_settings(conn: Connection, **kwargs) -> bool:
    """Update display/formatting settings.

    Args:
        conn: Database connection
        **kwargs: Display settings to update

    Returns:
        True if updated
    """
    field_mapping = {
        "time_format": "time_format",
        "show_timezone": "show_timezone",
        "channel_id_format": "channel_id_format",
        "xmltv_generator_name": "xmltv_generator_name",
        "xmltv_generator_url": "xmltv_generator_url",
    }

    updates = []
    values = []

    for key, column in field_mapping.items():
        if key in kwargs and kwargs[key] is not None:
            updates.append(f"{column} = ?")
            value = kwargs[key]
            if isinstance(value, bool):
                value = int(value)
            values.append(value)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    return cursor.rowcount > 0


def increment_epg_generation_counter(conn: Connection) -> int:
    """Increment the EPG generation counter and return new value.

    Args:
        conn: Database connection

    Returns:
        New counter value
    """
    conn.execute(
        "UPDATE settings SET epg_generation_counter = epg_generation_counter + 1 WHERE id = 1"
    )
    cursor = conn.execute("SELECT epg_generation_counter FROM settings WHERE id = 1")
    row = cursor.fetchone()
    return row["epg_generation_counter"] if row else 1
