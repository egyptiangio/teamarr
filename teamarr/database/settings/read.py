"""Settings read operations.

Query functions to fetch settings from the database.
"""

import json
from sqlite3 import Connection

from .types import (
    AllSettings,
    APISettings,
    ChannelNumberingSettings,
    DispatcharrSettings,
    DisplaySettings,
    DurationSettings,
    EPGSettings,
    LifecycleSettings,
    ReconciliationSettings,
    SchedulerSettings,
    StreamFilterSettings,
    StreamOrderingRule,
    StreamOrderingSettings,
    TeamFilterSettings,
    UpdateCheckSettings,
)

# Single source of truth for defaults - the dataclass itself
_DISPLAY_DEFAULTS = DisplaySettings()


def _build_display_settings(row) -> DisplaySettings:
    """Build DisplaySettings from DB row, using dataclass defaults for NULL values."""
    d = _DISPLAY_DEFAULTS
    return DisplaySettings(
        time_format=row["time_format"] or d.time_format,
        show_timezone=bool(row["show_timezone"])
        if row["show_timezone"] is not None
        else d.show_timezone,
        channel_id_format=row["channel_id_format"] or d.channel_id_format,
        xmltv_generator_name=row["xmltv_generator_name"] or d.xmltv_generator_name,
        xmltv_generator_url=row["xmltv_generator_url"] or d.xmltv_generator_url,
    )


def get_all_settings(conn: Connection) -> AllSettings:
    """Get all application settings.

    Args:
        conn: Database connection

    Returns:
        AllSettings object with all configuration
    """
    cursor = conn.execute("SELECT * FROM settings WHERE id = 1")
    row = cursor.fetchone()

    if not row:
        return AllSettings()

    # Parse default_channel_profile_ids
    # None = all profiles, [] = no profiles, [1,2,...] = specific profiles
    default_profile_ids: list[int] | None = None
    if row["default_channel_profile_ids"]:
        try:
            parsed = json.loads(row["default_channel_profile_ids"])
            # json.loads("null") returns Python None, which is valid
            # json.loads("[]") returns Python [], which is valid
            # json.loads("[1,2]") returns Python [1,2], which is valid
            default_profile_ids = parsed
        except json.JSONDecodeError:
            default_profile_ids = None

    return AllSettings(
        dispatcharr=DispatcharrSettings(
            enabled=bool(row["dispatcharr_enabled"]),
            url=row["dispatcharr_url"],
            username=row["dispatcharr_username"],
            password=row["dispatcharr_password"],
            epg_id=row["dispatcharr_epg_id"],
            default_channel_profile_ids=default_profile_ids,
        ),
        lifecycle=LifecycleSettings(
            channel_create_timing=row["channel_create_timing"] or "same_day",
            channel_delete_timing=row["channel_delete_timing"] or "day_after",
            channel_range_start=row["channel_range_start"] or 101,
            channel_range_end=row["channel_range_end"],
        ),
        reconciliation=ReconciliationSettings(
            reconcile_on_epg_generation=bool(row["reconcile_on_epg_generation"]),
            reconcile_on_startup=bool(row["reconcile_on_startup"]),
            auto_fix_orphan_teamarr=bool(row["auto_fix_orphan_teamarr"]),
            auto_fix_orphan_dispatcharr=bool(row["auto_fix_orphan_dispatcharr"]),
            auto_fix_duplicates=bool(row["auto_fix_duplicates"]),
            default_duplicate_event_handling=(
                row["default_duplicate_event_handling"] or "consolidate"
            ),
            channel_history_retention_days=row["channel_history_retention_days"] or 90,
        ),
        scheduler=SchedulerSettings(
            enabled=bool(row["scheduler_enabled"]),
            interval_minutes=row["scheduler_interval_minutes"] or 15,
        ),
        epg=EPGSettings(
            team_schedule_days_ahead=row["team_schedule_days_ahead"] or 30,
            event_match_days_ahead=row["event_match_days_ahead"] or 3,
            event_match_days_back=row["event_match_days_back"] or 7,
            epg_output_days_ahead=row["epg_output_days_ahead"] or 14,
            epg_lookback_hours=row["epg_lookback_hours"] or 6,
            epg_timezone=row["epg_timezone"] or "America/New_York",
            epg_output_path=row["epg_output_path"] or "./data/teamarr.xml",
            include_final_events=bool(row["include_final_events"]),
            midnight_crossover_mode=row["midnight_crossover_mode"] or "postgame",
            cron_expression=row["cron_expression"] or "0 * * * *",
            prepend_postponed_label=bool(row["prepend_postponed_label"])
            if row["prepend_postponed_label"] is not None
            else True,
        ),
        durations=DurationSettings(
            default=row["duration_default"] or 3.0,
            basketball=row["duration_basketball"] or 3.0,
            football=row["duration_football"] or 3.5,
            hockey=row["duration_hockey"] or 3.0,
            baseball=row["duration_baseball"] or 3.5,
            soccer=row["duration_soccer"] or 2.5,
            mma=row["duration_mma"] or 5.0,
            rugby=row["duration_rugby"] or 2.5,
            boxing=row["duration_boxing"] or 4.0,
            tennis=row["duration_tennis"] or 3.0,
            golf=row["duration_golf"] or 6.0,
            racing=row["duration_racing"] or 3.0,
            cricket=row["duration_cricket"] or 4.0,
            volleyball=row["duration_volleyball"] or 2.5,
        ),
        display=_build_display_settings(row),
        api=APISettings(
            timeout=row["api_timeout"] or 10,
            retry_count=row["api_retry_count"] or 3,
            soccer_cache_refresh_frequency=(row["soccer_cache_refresh_frequency"] or "weekly"),
            team_cache_refresh_frequency=row["team_cache_refresh_frequency"] or "weekly",
        ),
        stream_filter=StreamFilterSettings(
            require_event_pattern=bool(row["stream_filter_require_event_pattern"])
            if row["stream_filter_require_event_pattern"] is not None
            else True,
            include_patterns=json.loads(row["stream_filter_include_patterns"] or "[]"),
            exclude_patterns=json.loads(row["stream_filter_exclude_patterns"] or "[]"),
        ),
        team_filter=TeamFilterSettings(
            enabled=bool(row["team_filter_enabled"])
            if row["team_filter_enabled"] is not None
            else True,
            include_teams=json.loads(row["default_include_teams"])
            if row["default_include_teams"]
            else None,
            exclude_teams=json.loads(row["default_exclude_teams"])
            if row["default_exclude_teams"]
            else None,
            mode=row["default_team_filter_mode"] or "include",
        ),
        channel_numbering=ChannelNumberingSettings(
            numbering_mode=row["channel_numbering_mode"] or "strict_block",
            sorting_scope=row["channel_sorting_scope"] or "per_group",
            sort_by=row["channel_sort_by"] or "time",
        ),
        stream_ordering=StreamOrderingSettings(
            rules=_parse_stream_ordering_rules(row["stream_ordering_rules"])
        ),
        update_check=_build_update_check_settings(row),
        epg_generation_counter=row["epg_generation_counter"] or 0,
        schema_version=row["schema_version"] or 2,
    )


def get_dispatcharr_settings(conn: Connection) -> DispatcharrSettings:
    """Get Dispatcharr integration settings.

    Args:
        conn: Database connection

    Returns:
        DispatcharrSettings object
    """
    cursor = conn.execute(
        """SELECT dispatcharr_enabled, dispatcharr_url, dispatcharr_username,
                  dispatcharr_password, dispatcharr_epg_id, default_channel_profile_ids
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return DispatcharrSettings()

    # Parse JSON for default_channel_profile_ids
    # None = all profiles, [] = no profiles, [1,2,...] = specific profiles
    default_profile_ids: list[int] | None = None
    if row["default_channel_profile_ids"]:
        try:
            parsed = json.loads(row["default_channel_profile_ids"])
            default_profile_ids = parsed
        except json.JSONDecodeError:
            default_profile_ids = None

    return DispatcharrSettings(
        enabled=bool(row["dispatcharr_enabled"]),
        url=row["dispatcharr_url"],
        username=row["dispatcharr_username"],
        password=row["dispatcharr_password"],
        epg_id=row["dispatcharr_epg_id"],
        default_channel_profile_ids=default_profile_ids,
    )


def get_scheduler_settings(conn: Connection) -> SchedulerSettings:
    """Get scheduler settings.

    Args:
        conn: Database connection

    Returns:
        SchedulerSettings object
    """
    cursor = conn.execute(
        "SELECT scheduler_enabled, scheduler_interval_minutes FROM settings WHERE id = 1"
    )
    row = cursor.fetchone()

    if not row:
        return SchedulerSettings()

    return SchedulerSettings(
        enabled=bool(row["scheduler_enabled"]),
        interval_minutes=row["scheduler_interval_minutes"] or 15,
    )


def get_lifecycle_settings(conn: Connection) -> LifecycleSettings:
    """Get channel lifecycle settings.

    Args:
        conn: Database connection

    Returns:
        LifecycleSettings object
    """
    cursor = conn.execute(
        """SELECT channel_create_timing, channel_delete_timing,
                  channel_range_start, channel_range_end
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return LifecycleSettings()

    return LifecycleSettings(
        channel_create_timing=row["channel_create_timing"] or "same_day",
        channel_delete_timing=row["channel_delete_timing"] or "day_after",
        channel_range_start=row["channel_range_start"] or 101,
        channel_range_end=row["channel_range_end"],
    )


def get_epg_settings(conn: Connection) -> EPGSettings:
    """Get EPG generation settings.

    Args:
        conn: Database connection

    Returns:
        EPGSettings object
    """
    cursor = conn.execute(
        """SELECT team_schedule_days_ahead, event_match_days_ahead, event_match_days_back,
                  epg_output_days_ahead, epg_lookback_hours, epg_timezone,
                  epg_output_path, include_final_events, midnight_crossover_mode,
                  cron_expression, prepend_postponed_label
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return EPGSettings()

    return EPGSettings(
        team_schedule_days_ahead=row["team_schedule_days_ahead"] or 30,
        event_match_days_ahead=row["event_match_days_ahead"] or 3,
        event_match_days_back=row["event_match_days_back"] or 7,
        epg_output_days_ahead=row["epg_output_days_ahead"] or 14,
        epg_lookback_hours=row["epg_lookback_hours"] or 6,
        epg_timezone=row["epg_timezone"] or "America/New_York",
        epg_output_path=row["epg_output_path"] or "./data/teamarr.xml",
        include_final_events=bool(row["include_final_events"]),
        midnight_crossover_mode=row["midnight_crossover_mode"] or "postgame",
        cron_expression=row["cron_expression"] or "0 * * * *",
        prepend_postponed_label=bool(row["prepend_postponed_label"])
        if row["prepend_postponed_label"] is not None
        else True,
    )


def get_display_settings(conn: Connection) -> DisplaySettings:
    """Get display settings.

    Returns:
        DisplaySettings dataclass with time_format, show_timezone, etc.
    """
    cursor = conn.cursor()
    cursor.execute(
        """SELECT time_format, show_timezone, channel_id_format,
                  xmltv_generator_name, xmltv_generator_url
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return DisplaySettings()

    return _build_display_settings(row)


def get_stream_filter_settings(conn: Connection) -> StreamFilterSettings:
    """Get stream filtering settings.

    Args:
        conn: Database connection

    Returns:
        StreamFilterSettings object with global filter configuration
    """
    cursor = conn.execute(
        """SELECT stream_filter_require_event_pattern,
                  stream_filter_include_patterns,
                  stream_filter_exclude_patterns
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return StreamFilterSettings()

    return StreamFilterSettings(
        require_event_pattern=bool(row["stream_filter_require_event_pattern"])
        if row["stream_filter_require_event_pattern"] is not None
        else True,
        include_patterns=json.loads(row["stream_filter_include_patterns"] or "[]"),
        exclude_patterns=json.loads(row["stream_filter_exclude_patterns"] or "[]"),
    )


def get_team_filter_settings(conn: Connection) -> TeamFilterSettings:
    """Get default team filtering settings.

    Args:
        conn: Database connection

    Returns:
        TeamFilterSettings object with global default team filter
    """
    cursor = conn.execute(
        """SELECT team_filter_enabled, default_include_teams, default_exclude_teams,
                  default_team_filter_mode
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return TeamFilterSettings()

    return TeamFilterSettings(
        enabled=bool(row["team_filter_enabled"])
        if row["team_filter_enabled"] is not None
        else True,
        include_teams=json.loads(row["default_include_teams"])
        if row["default_include_teams"]
        else None,
        exclude_teams=json.loads(row["default_exclude_teams"])
        if row["default_exclude_teams"]
        else None,
        mode=row["default_team_filter_mode"] or "include",
    )


def get_channel_numbering_settings(conn: Connection) -> ChannelNumberingSettings:
    """Get channel numbering and sorting settings.

    Args:
        conn: Database connection

    Returns:
        ChannelNumberingSettings object with numbering mode, sorting scope, and sort by
    """
    cursor = conn.execute(
        """SELECT channel_numbering_mode, channel_sorting_scope, channel_sort_by
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return ChannelNumberingSettings()

    return ChannelNumberingSettings(
        numbering_mode=row["channel_numbering_mode"] or "strict_block",
        sorting_scope=row["channel_sorting_scope"] or "per_group",
        sort_by=row["channel_sort_by"] or "time",
    )


def _parse_stream_ordering_rules(rules_json: str | None) -> list[StreamOrderingRule]:
    """Parse stream ordering rules from JSON.

    Args:
        rules_json: JSON string of rules or None

    Returns:
        List of StreamOrderingRule objects
    """
    if not rules_json:
        return []

    try:
        rules_data = json.loads(rules_json)
        if not isinstance(rules_data, list):
            return []

        return [
            StreamOrderingRule(
                type=rule.get("type", "m3u"),
                value=rule.get("value", ""),
                priority=rule.get("priority", 99),
            )
            for rule in rules_data
            if isinstance(rule, dict) and rule.get("type") and rule.get("value")
        ]
    except json.JSONDecodeError:
        return []


def get_stream_ordering_settings(conn: Connection) -> StreamOrderingSettings:
    """Get stream ordering rules.

    Args:
        conn: Database connection

    Returns:
        StreamOrderingSettings object with rules list
    """
    cursor = conn.execute("SELECT stream_ordering_rules FROM settings WHERE id = 1")
    row = cursor.fetchone()

    if not row:
        return StreamOrderingSettings()

    return StreamOrderingSettings(rules=_parse_stream_ordering_rules(row["stream_ordering_rules"]))


# Single source of truth for update check defaults
_UPDATE_CHECK_DEFAULTS = UpdateCheckSettings()


def _build_update_check_settings(row) -> UpdateCheckSettings:
    """Build UpdateCheckSettings from DB row, using dataclass defaults for NULL values."""
    d = _UPDATE_CHECK_DEFAULTS
    return UpdateCheckSettings(
        enabled=bool(row["update_check_enabled"])
        if row["update_check_enabled"] is not None
        else d.enabled,
        notify_stable=bool(row["update_notify_stable"])
        if row["update_notify_stable"] is not None
        else d.notify_stable,
        notify_dev=bool(row["update_notify_dev"])
        if row["update_notify_dev"] is not None
        else d.notify_dev,
        github_owner=row["update_github_owner"] or d.github_owner,
        github_repo=row["update_github_repo"] or d.github_repo,
        dev_branch=row["update_dev_branch"] or d.dev_branch,
        auto_detect_branch=bool(row["update_auto_detect_branch"])
        if row["update_auto_detect_branch"] is not None
        else d.auto_detect_branch,
    )


def get_update_check_settings(conn: Connection) -> UpdateCheckSettings:
    """Get update check settings.

    Args:
        conn: Database connection

    Returns:
        UpdateCheckSettings object with update notification configuration
    """
    cursor = conn.execute(
        """SELECT update_check_enabled, update_notify_stable, update_notify_dev,
                  update_github_owner, update_github_repo, update_dev_branch,
                  update_auto_detect_branch
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return UpdateCheckSettings()

    return _build_update_check_settings(row)
