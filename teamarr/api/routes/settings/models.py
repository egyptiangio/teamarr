"""Settings Pydantic models.

All request/response models for settings endpoints.
"""

from pydantic import BaseModel, Field

# =============================================================================
# DISPATCHARR SETTINGS
# =============================================================================


class DispatcharrSettingsModel(BaseModel):
    """Dispatcharr integration settings."""

    enabled: bool = False
    url: str | None = None
    username: str | None = None
    password: str | None = None
    epg_id: int | None = None


class DispatcharrSettingsUpdate(BaseModel):
    """Update model for Dispatcharr settings (all fields optional)."""

    enabled: bool | None = None
    url: str | None = None
    username: str | None = None
    password: str | None = None
    epg_id: int | None = None


class ConnectionTestRequest(BaseModel):
    """Request to test Dispatcharr connection."""

    url: str | None = Field(None, description="Override URL (uses saved if not provided)")
    username: str | None = Field(None, description="Override username")
    password: str | None = Field(None, description="Override password")


class ConnectionTestResponse(BaseModel):
    """Response from connection test."""

    success: bool
    url: str | None = None
    username: str | None = None
    version: str | None = None
    account_count: int | None = None
    group_count: int | None = None
    channel_count: int | None = None
    error: str | None = None


# =============================================================================
# LIFECYCLE SETTINGS
# =============================================================================


class LifecycleSettingsModel(BaseModel):
    """Channel lifecycle settings."""

    channel_create_timing: str = "same_day"
    channel_delete_timing: str = "day_after"
    channel_range_start: int = 101
    channel_range_end: int | None = None


# =============================================================================
# RECONCILIATION SETTINGS
# =============================================================================


class ReconciliationSettingsModel(BaseModel):
    """Reconciliation settings."""

    reconcile_on_epg_generation: bool = True
    reconcile_on_startup: bool = True
    auto_fix_orphan_teamarr: bool = True
    auto_fix_orphan_dispatcharr: bool = True
    auto_fix_duplicates: bool = False
    default_duplicate_event_handling: str = "consolidate"
    channel_history_retention_days: int = 90


# =============================================================================
# SCHEDULER SETTINGS
# =============================================================================


class SchedulerSettingsModel(BaseModel):
    """Scheduler settings."""

    enabled: bool = True
    interval_minutes: int = 15


class SchedulerStatusResponse(BaseModel):
    """Scheduler status response."""

    running: bool
    cron_expression: str | None = None
    last_run: str | None = None
    next_run: str | None = None


# =============================================================================
# EPG SETTINGS
# =============================================================================


class EPGSettingsModel(BaseModel):
    """EPG generation settings."""

    team_schedule_days_ahead: int = 30
    event_match_days_ahead: int = 3
    epg_output_days_ahead: int = 14
    epg_lookback_hours: int = 6
    epg_timezone: str = "America/New_York"
    epg_output_path: str = "./teamarr.xml"
    include_final_events: bool = False
    midnight_crossover_mode: str = "postgame"
    cron_expression: str = "0 * * * *"


# =============================================================================
# DURATION SETTINGS
# =============================================================================


class DurationSettingsModel(BaseModel):
    """Game duration settings (in hours)."""

    default: float = 3.0
    basketball: float = 3.0
    football: float = 3.5
    hockey: float = 3.0
    baseball: float = 3.5
    soccer: float = 2.5
    mma: float = 5.0
    rugby: float = 2.5
    boxing: float = 4.0
    tennis: float = 3.0
    golf: float = 6.0
    racing: float = 3.0
    cricket: float = 4.0


# =============================================================================
# DISPLAY SETTINGS
# =============================================================================


class DisplaySettingsModel(BaseModel):
    """Display and formatting settings."""

    time_format: str = "12h"
    show_timezone: bool = True
    channel_id_format: str = "{team_name_pascal}.{league_id}"
    xmltv_generator_name: str = "Teamarr v2"
    xmltv_generator_url: str = ""
    tsdb_api_key: str | None = None  # Optional TheSportsDB premium API key


# =============================================================================
# ALL SETTINGS
# =============================================================================


class AllSettingsModel(BaseModel):
    """Complete application settings."""

    dispatcharr: DispatcharrSettingsModel
    lifecycle: LifecycleSettingsModel
    reconciliation: ReconciliationSettingsModel
    scheduler: SchedulerSettingsModel
    epg: EPGSettingsModel
    durations: DurationSettingsModel
    display: DisplaySettingsModel
    epg_generation_counter: int = 0
    schema_version: int = 3
