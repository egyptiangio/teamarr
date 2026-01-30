"""Settings update operations.

Functions to modify settings in the database.
"""

import json
import logging
from sqlite3 import Connection

logger = logging.getLogger(__name__)


_NOT_PROVIDED = object()  # Sentinel to distinguish "not provided" from None


def update_dispatcharr_settings(
    conn: Connection,
    enabled: bool | None = None,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    epg_id: int | None = None,
    default_channel_profile_ids: list[int] | None | object = _NOT_PROVIDED,
    cleanup_unused_logos: bool | None = None,
) -> bool:
    """Update Dispatcharr settings.

    Only updates fields that are explicitly provided.

    Args:
        conn: Database connection
        enabled: Enable/disable integration
        url: Dispatcharr URL
        username: Username
        password: Password
        epg_id: EPG source ID in Dispatcharr
        default_channel_profile_ids: Default channel profiles for event channels.
            None = all profiles, [] = no profiles, [1,2,...] = specific profiles.
        cleanup_unused_logos: Call Dispatcharr cleanup API after generation.

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
    # default_channel_profile_ids semantics:
    # - _NOT_PROVIDED (default) → don't update
    # - None → "all profiles" → store as JSON "null"
    # - [] → "no profiles" → store as JSON "[]"
    # - [1, 2, ...] → specific profiles → store as JSON "[1, 2, ...]"
    if default_channel_profile_ids is not _NOT_PROVIDED:
        updates.append("default_channel_profile_ids = ?")
        values.append(json.dumps(default_channel_profile_ids))
    if cleanup_unused_logos is not None:
        updates.append("cleanup_unused_logos = ?")
        values.append(int(cleanup_unused_logos))

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Dispatcharr settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


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
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Scheduler settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


def update_lifecycle_settings(
    conn: Connection,
    channel_create_timing: str | None = None,
    channel_delete_timing: str | None = None,
    channel_range_start: int | None = None,
    channel_range_end: int | None | object = _NOT_PROVIDED,
) -> bool:
    """Update channel lifecycle settings.

    Args:
        conn: Database connection
        channel_create_timing: When to create channels
        channel_delete_timing: When to delete channels
        channel_range_start: First auto-assigned channel number
        channel_range_end: Last auto-assigned channel number (None = no limit)

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
    # channel_range_end: _NOT_PROVIDED = don't update, None = no limit, int = set value
    if channel_range_end is not _NOT_PROVIDED:
        updates.append("channel_range_end = ?")
        values.append(channel_range_end)  # None becomes SQL NULL

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Lifecycle settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


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
        "event_match_days_back": "event_match_days_back",
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
    if cursor.rowcount > 0:
        logger.info("[UPDATED] EPG settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


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
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Reconciliation settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


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
        "volleyball": "duration_volleyball",
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
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Duration settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


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
        "tsdb_api_key": "tsdb_api_key",
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
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Display settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


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


def update_team_filter_settings(
    conn: Connection,
    enabled: bool | None = None,
    include_teams: list[dict] | None = None,
    exclude_teams: list[dict] | None = None,
    mode: str | None = None,
    clear_include_teams: bool = False,
    clear_exclude_teams: bool = False,
) -> bool:
    """Update default team filtering settings.

    Args:
        conn: Database connection
        enabled: Master toggle for team filtering
        include_teams: Teams to include (replaces existing)
        exclude_teams: Teams to exclude (replaces existing)
        mode: Filter mode ('include' or 'exclude')
        clear_include_teams: Set to True to clear include_teams to NULL
        clear_exclude_teams: Set to True to clear exclude_teams to NULL

    Returns:
        True if updated
    """
    updates = []
    values = []

    if enabled is not None:
        updates.append("team_filter_enabled = ?")
        values.append(int(enabled))

    # Team filtering - treat empty list as clear (NULL)
    if clear_include_teams:
        updates.append("default_include_teams = NULL")
    elif include_teams is not None:
        if include_teams:  # Non-empty list
            updates.append("default_include_teams = ?")
            values.append(json.dumps(include_teams))
        else:  # Empty list - clear to NULL
            updates.append("default_include_teams = NULL")

    if clear_exclude_teams:
        updates.append("default_exclude_teams = NULL")
    elif exclude_teams is not None:
        if exclude_teams:  # Non-empty list
            updates.append("default_exclude_teams = ?")
            values.append(json.dumps(exclude_teams))
        else:  # Empty list - clear to NULL
            updates.append("default_exclude_teams = NULL")

    if mode is not None:
        updates.append("default_team_filter_mode = ?")
        values.append(mode)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Team filter settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


def update_channel_numbering_settings(
    conn: Connection,
    numbering_mode: str | None = None,
    sorting_scope: str | None = None,
    sort_by: str | None = None,
) -> bool:
    """Update channel numbering and sorting settings.

    Args:
        conn: Database connection
        numbering_mode: Numbering mode ('strict_block', 'rational_block', 'strict_compact')
        sorting_scope: Sorting scope ('per_group', 'global')
        sort_by: Sort order ('sport_league_time', 'time', 'stream_order')

    Returns:
        True if updated
    """
    updates = []
    values = []

    if numbering_mode is not None:
        # Validate mode
        valid_modes = ("strict_block", "rational_block", "strict_compact")
        if numbering_mode not in valid_modes:
            logger.warning(
                "[CHANNEL_NUM] Invalid numbering mode '%s', must be one of %s",
                numbering_mode,
                valid_modes,
            )
            return False
        updates.append("channel_numbering_mode = ?")
        values.append(numbering_mode)

    if sorting_scope is not None:
        # Validate scope
        valid_scopes = ("per_group", "global")
        if sorting_scope not in valid_scopes:
            logger.warning(
                "[CHANNEL_NUM] Invalid sorting scope '%s', must be one of %s",
                sorting_scope,
                valid_scopes,
            )
            return False
        updates.append("channel_sorting_scope = ?")
        values.append(sorting_scope)

    if sort_by is not None:
        # Validate sort_by
        valid_sort_by = ("sport_league_time", "time", "stream_order")
        if sort_by not in valid_sort_by:
            logger.warning(
                "[CHANNEL_NUM] Invalid sort_by '%s', must be one of %s",
                sort_by,
                valid_sort_by,
            )
            return False
        updates.append("channel_sort_by = ?")
        values.append(sort_by)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[CHANNEL_NUM] Updated settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


def update_stream_ordering_rules(
    conn: Connection,
    rules: list,
) -> bool:
    """Update stream ordering rules (full replacement).

    Args:
        conn: Database connection
        rules: List of rules (dicts or StreamOrderingRule dataclasses).
            Each rule: {"type": "m3u"|"group"|"regex", "value": str, "priority": int}

    Returns:
        True if updated
    """
    from .types import StreamOrderingRule as RuleType

    # Validate rules structure
    valid_types = {"m3u", "group", "regex"}
    validated_rules = []

    for rule in rules:
        # Handle both dict and dataclass
        if isinstance(rule, RuleType):
            rule_type = rule.type
            rule_value = rule.value
            rule_priority = rule.priority
        else:
            rule_type = rule.get("type")
            rule_value = rule.get("value")
            rule_priority = rule.get("priority")

        if rule_type not in valid_types:
            logger.warning(
                "[STREAM_ORDER] Invalid rule type '%s', must be one of %s",
                rule_type,
                valid_types,
            )
            continue

        if not rule_value or not isinstance(rule_value, str):
            logger.warning("[STREAM_ORDER] Rule missing value, skipping")
            continue

        if not isinstance(rule_priority, int) or rule_priority < 1 or rule_priority > 99:
            logger.warning(
                "[STREAM_ORDER] Invalid priority %s, must be 1-99, defaulting to 99",
                rule_priority,
            )
            rule_priority = 99

        validated_rules.append(
            {
                "type": rule_type,
                "value": rule_value,
                "priority": rule_priority,
            }
        )

    # Store as JSON
    rules_json = json.dumps(validated_rules)
    cursor = conn.execute(
        "UPDATE settings SET stream_ordering_rules = ? WHERE id = 1",
        (rules_json,),
    )

    if cursor.rowcount > 0:
        logger.info("[STREAM_ORDER] Updated %d rules", len(validated_rules))
        return True
    return False


def update_api_settings(
    conn: Connection,
    timeout: int | None = None,
    retry_count: int | None = None,
    soccer_cache_refresh_frequency: str | None = None,
    team_cache_refresh_frequency: str | None = None,
    startup_cache_max_age_days: int | None = None,
) -> bool:
    """Update API behavior settings.

    Args:
        conn: Database connection
        timeout: API request timeout in seconds
        retry_count: Number of retries for failed requests
        soccer_cache_refresh_frequency: How often to refresh soccer cache
        team_cache_refresh_frequency: How often to refresh team cache
        startup_cache_max_age_days: Days before cache is considered stale on startup
            0 = disabled (never auto-refresh), >0 = refresh if older than N days

    Returns:
        True if updated
    """
    updates = []
    values = []

    if timeout is not None:
        updates.append("api_timeout = ?")
        values.append(timeout)
    if retry_count is not None:
        updates.append("api_retry_count = ?")
        values.append(retry_count)
    if soccer_cache_refresh_frequency is not None:
        updates.append("soccer_cache_refresh_frequency = ?")
        values.append(soccer_cache_refresh_frequency)
    if team_cache_refresh_frequency is not None:
        updates.append("team_cache_refresh_frequency = ?")
        values.append(team_cache_refresh_frequency)
    if startup_cache_max_age_days is not None:
        updates.append("startup_cache_max_age_days = ?")
        values.append(startup_cache_max_age_days)

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] API settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False


def update_update_check_settings(
    conn: Connection,
    enabled: bool | None = None,
    notify_stable: bool | None = None,
    notify_dev: bool | None = None,
    github_owner: str | None = None,
    github_repo: str | None = None,
    dev_branch: str | None = None,
    auto_detect_branch: bool | None = None,
) -> bool:
    """Update update check settings.

    Args:
        conn: Database connection
        enabled: Master toggle for update checking
        notify_stable: Notify about stable releases
        notify_dev: Notify about dev builds (if running dev)
        github_owner: Repository owner (for forks)
        github_repo: Repository name (for forks)
        dev_branch: Branch to check for dev builds
        auto_detect_branch: Auto-detect branch from version string

    Returns:
        True if updated
    """
    updates = []
    values = []

    if enabled is not None:
        updates.append("update_check_enabled = ?")
        values.append(int(enabled))
    if notify_stable is not None:
        updates.append("update_notify_stable = ?")
        values.append(int(notify_stable))
    if notify_dev is not None:
        updates.append("update_notify_dev = ?")
        values.append(int(notify_dev))
    if github_owner is not None:
        updates.append("update_github_owner = ?")
        values.append(github_owner)
    if github_repo is not None:
        updates.append("update_github_repo = ?")
        values.append(github_repo)
    if dev_branch is not None:
        updates.append("update_dev_branch = ?")
        values.append(dev_branch)
    if auto_detect_branch is not None:
        updates.append("update_auto_detect_branch = ?")
        values.append(int(auto_detect_branch))

    if not updates:
        return False

    query = f"UPDATE settings SET {', '.join(updates)} WHERE id = 1"
    cursor = conn.execute(query, values)
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Update check settings: %s", [u.split(" = ")[0] for u in updates])
        return True
    return False
