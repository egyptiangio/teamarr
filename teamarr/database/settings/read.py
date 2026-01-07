"""Settings read operations.

Query functions to fetch settings from the database.
"""

import json
from sqlite3 import Connection

from .types import (
    AllSettings,
    APISettings,
    DispatcharrSettings,
    DisplaySettings,
    DurationSettings,
    EPGSettings,
    LifecycleSettings,
    ReconciliationSettings,
    SchedulerSettings,
    StreamFilterSettings,
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

    return AllSettings(
        dispatcharr=DispatcharrSettings(
            enabled=bool(row["dispatcharr_enabled"]),
            url=row["dispatcharr_url"],
            username=row["dispatcharr_username"],
            password=row["dispatcharr_password"],
            epg_id=row["dispatcharr_epg_id"],
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
            event_match_days_back=row["event_match_days_back"] or 2,
            epg_output_days_ahead=row["epg_output_days_ahead"] or 14,
            epg_lookback_hours=row["epg_lookback_hours"] or 6,
            epg_timezone=row["epg_timezone"] or "America/New_York",
            epg_output_path=row["epg_output_path"] or "./teamarr.xml",
            include_final_events=bool(row["include_final_events"]),
            midnight_crossover_mode=row["midnight_crossover_mode"] or "postgame",
            cron_expression=row["cron_expression"] or "0 * * * *",
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
                  dispatcharr_password, dispatcharr_epg_id
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return DispatcharrSettings()

    return DispatcharrSettings(
        enabled=bool(row["dispatcharr_enabled"]),
        url=row["dispatcharr_url"],
        username=row["dispatcharr_username"],
        password=row["dispatcharr_password"],
        epg_id=row["dispatcharr_epg_id"],
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
                  cron_expression
           FROM settings WHERE id = 1"""
    )
    row = cursor.fetchone()

    if not row:
        return EPGSettings()

    return EPGSettings(
        team_schedule_days_ahead=row["team_schedule_days_ahead"] or 30,
        event_match_days_ahead=row["event_match_days_ahead"] or 3,
        event_match_days_back=row["event_match_days_back"] or 2,
        epg_output_days_ahead=row["epg_output_days_ahead"] or 14,
        epg_lookback_hours=row["epg_lookback_hours"] or 6,
        epg_timezone=row["epg_timezone"] or "America/New_York",
        epg_output_path=row["epg_output_path"] or "./teamarr.xml",
        include_final_events=bool(row["include_final_events"]),
        midnight_crossover_mode=row["midnight_crossover_mode"] or "postgame",
        cron_expression=row["cron_expression"] or "0 * * * *",
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
