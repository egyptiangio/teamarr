"""Settings dataclasses.

Each settings group is represented by a dataclass for type safety.
"""

from dataclasses import dataclass, field


@dataclass
class DispatcharrSettings:
    """Dispatcharr integration settings."""

    enabled: bool = False
    url: str | None = None
    username: str | None = None
    password: str | None = None
    epg_id: int | None = None
    default_channel_profile_ids: list[int] = field(default_factory=list)


@dataclass
class LifecycleSettings:
    """Channel lifecycle settings."""

    channel_create_timing: str = "same_day"
    channel_delete_timing: str = "day_after"
    channel_range_start: int = 101
    channel_range_end: int | None = None


@dataclass
class ReconciliationSettings:
    """Reconciliation settings."""

    reconcile_on_epg_generation: bool = True
    reconcile_on_startup: bool = True
    auto_fix_orphan_teamarr: bool = True
    auto_fix_orphan_dispatcharr: bool = True
    auto_fix_duplicates: bool = False
    default_duplicate_event_handling: str = "consolidate"
    channel_history_retention_days: int = 90


@dataclass
class SchedulerSettings:
    """Background scheduler settings."""

    enabled: bool = True
    interval_minutes: int = 15


@dataclass
class EPGSettings:
    """EPG generation settings."""

    team_schedule_days_ahead: int = 30
    event_match_days_ahead: int = 3
    event_match_days_back: int = 7
    epg_output_days_ahead: int = 14
    epg_lookback_hours: int = 6
    epg_timezone: str = "America/New_York"
    epg_output_path: str = "./teamarr.xml"
    include_final_events: bool = False
    midnight_crossover_mode: str = "postgame"
    cron_expression: str = "0 * * * *"


@dataclass
class DurationSettings:
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
    volleyball: float = 2.5


@dataclass
class DisplaySettings:
    """Display and formatting settings."""

    time_format: str = "12h"
    show_timezone: bool = True
    channel_id_format: str = "{team_name_pascal}.{league_id}"
    xmltv_generator_name: str = "Teamarr v2"
    xmltv_generator_url: str = "https://github.com/egyptiangio/teamarr"


@dataclass
class APISettings:
    """API behavior settings."""

    timeout: int = 10
    retry_count: int = 3
    soccer_cache_refresh_frequency: str = "weekly"
    team_cache_refresh_frequency: str = "weekly"


@dataclass
class StreamFilterSettings:
    """Stream filtering settings (global defaults for event groups)."""

    # Require event pattern: only match streams that look like events (have vs/@/at/date patterns)
    require_event_pattern: bool = True
    # Custom inclusion patterns (regex) - stream must match at least one if provided
    include_patterns: list[str] = field(default_factory=list)
    # Custom exclusion patterns (regex) - stream must NOT match any
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass
class TeamFilterSettings:
    """Default team filtering for event groups.

    Global default applied to all event groups that don't have their own filter.
    Groups can override this with their own include/exclude_teams settings.
    """

    include_teams: list[dict] | None = None
    exclude_teams: list[dict] | None = None
    mode: str = "include"  # 'include' or 'exclude'


@dataclass
class AllSettings:
    """Complete application settings."""

    dispatcharr: DispatcharrSettings = field(default_factory=DispatcharrSettings)
    lifecycle: LifecycleSettings = field(default_factory=LifecycleSettings)
    reconciliation: ReconciliationSettings = field(default_factory=ReconciliationSettings)
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    epg: EPGSettings = field(default_factory=EPGSettings)
    durations: DurationSettings = field(default_factory=DurationSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    api: APISettings = field(default_factory=APISettings)
    stream_filter: StreamFilterSettings = field(default_factory=StreamFilterSettings)
    team_filter: TeamFilterSettings = field(default_factory=TeamFilterSettings)
    epg_generation_counter: int = 0
    schema_version: int = 22
