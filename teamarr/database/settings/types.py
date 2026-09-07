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
    # None = all profiles, [] = no profiles
    # Can contain: int IDs, "{sport}", "{league}" wildcards
    # e.g., [1, 5, "{sport}"] = profiles 1, 5, plus dynamic sport profile
    default_channel_profile_ids: list[int | str] | None = None
    # Default stream profile for event channels (overrideable per-group)
    default_stream_profile_id: int | None = None
    # Default channel group for event channels (overrideable per-league)
    default_channel_group_id: int | None = None
    # Channel group mode: 'static', 'sport', 'league', or custom pattern like '{sport} | {league}'
    default_channel_group_mode: str = "static"
    # When True, call Dispatcharr's /api/channels/logos/cleanup/ after generation
    # This removes ALL unused logos in Dispatcharr, not just ones Teamarr uploaded
    cleanup_unused_logos: bool = False


@dataclass
class ProxySettings:
    """SOCKS5 policy for provider upstream requests only."""

    enabled: bool = False
    url: str | None = None
    user_agent: str | None = None
    excluded_providers: list[str] = field(default_factory=list)


@dataclass
class LifecycleSettings:
    """Channel lifecycle settings."""

    channel_create_timing: str = "same_day"
    channel_delete_timing: str = "same_day"
    channel_pre_buffer_minutes: int = 60
    channel_post_buffer_minutes: int = 60
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
    # Scheduled channel reset (for Jellyfin logo cache issues)
    channel_reset_enabled: bool = False
    channel_reset_cron: str | None = None


@dataclass
class EPGSettings:
    """EPG generation settings."""

    team_schedule_days_ahead: int = 30
    event_match_days_ahead: int = 3
    event_match_days_back: int = 7
    epg_output_days_ahead: int = 14
    epg_lookback_hours: int = 6
    epg_timezone: str = "America/New_York"
    epg_output_path: str = "./data/teamarr.xml"
    include_final_events: bool = False
    midnight_crossover_mode: str = "postgame"
    cron_expression: str = "0 * * * *"
    prepend_postponed_label: bool = True
    # XC provider EPG fallback (epic crs) — opt-in backup when DP has no mapping
    epg_xtream_fallback_enabled: bool = False
    # How long a downloaded XC provider EPG is reused before re-fetching (hours)
    epg_xtream_cache_hours: int = 24
    # Dispatcharr-channels as EPG source (epic 183.9) — additive source from curated channels
    epg_channel_source_enabled: bool = False
    # Which DP channel groups to include as channel-source candidates (group ids).
    # Empty = include all (ybt.2). Scopes the scan and drives the sorting rule.
    epg_channel_source_groups: list[int] = field(default_factory=list)
    # Explicit stream-profile override targets. Current target_type is
    # 'dispatcharr_channel_group'; future source types can share this contract.
    stream_profile_overrides: list[dict[str, int | str]] = field(
        default_factory=list
    )
    epg_stream_pre_buffer_minutes: int = 60
    epg_stream_post_buffer_minutes: int = 60
    # Tennis: only match/attach grand-slam tournaments (#283 first slice) —
    # ESPN marks tournaments major=true; smaller events are filtered at the
    # tennis matcher so junk-tour channels never get created.
    tennis_majors_only: bool = False
    # Game-thumbs base URL (epic z02s): optional prefix for relative art paths in
    # templates. Empty = no prefixing. Absolute (http(s)://) art values bypass it.
    art_base_url: str = ""


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
    sport_naming: str = "us"  # 'us' | 'international' (#691)
    matchup_order: str = "auto"  # 'auto' | 'away_first' | 'home_first' (#692)
    channel_id_format: str = "{team_name|pascal}.{league_id}"
    xmltv_generator_name: str = "Teamarr"
    xmltv_generator_url: str = "https://github.com/Pharaoh-Labs/teamarr"
    tsdb_api_key: str | None = None


@dataclass
class APISettings:
    """API behavior settings."""

    timeout: int = 30
    retry_count: int = 5
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

    enabled: bool = True  # Master toggle - when False, filtering is skipped entirely
    include_teams: list[dict] | None = None
    exclude_teams: list[dict] | None = None
    mode: str = "include"  # 'include' or 'exclude'
    bypass_filter_for_playoffs: bool = False  # Include all playoff/All-Star games regardless


@dataclass
class StreamOrderingRule:
    """A single stream ordering rule.

    Two rule classes, discriminated by ``mode`` (epic teamarr-5ag):

    - ``mode="priority"`` (hard): an ordered, first-match-wins list. The first
      priority rule a stream matches sets its *band*; ``priority`` (1-99, lower
      first) orders these rules and becomes the band weight. This is the legacy
      behaviour and the strict-precedence escape hatch.
    - ``mode="score"`` (soft): additive. A stream *sums* ``points`` (signed)
      across every score rule it matches; the total ranks streams within a band
      (and is the sole ranking when no priority rule matches). Negative points
      demote a stream below the baseline.

    The dataclass default is ``mode="priority"`` — the legacy-safe fallback, so
    any rule constructed or deserialized without an explicit mode behaves exactly
    as it did before scoring existed. "New rules default to score" is a UI/API
    concern (the add-rule form and request model default to score); it is
    deliberately not baked into this storage type, which must preserve old data.
    """

    # One of VALID_RULE_TYPES below (m3u/group/regex/stream_type/team_feed/
    # not_team_feed/epg_match/dispatcharr_group/home_feed/away_feed/
    # stats_metric/catch_all)
    type: str
    value: str  # Account name, group name, regex pattern, or team key(s)
    priority: int  # 1-99, lower = higher priority (orders 'priority'-mode rules / sets band)
    mode: str = "priority"  # 'priority' (hard, first-match band) or 'score' (soft, additive)
    points: int = 0  # signed; summed across matched 'score' rules (ignored for 'priority' mode)


VALID_RULE_TYPES: frozenset[str] = frozenset({
    "m3u", "group", "regex", "stream_type",
    "team_feed", "not_team_feed", "epg_match", "dispatcharr_group",
    "home_feed", "away_feed",
    "stats_metric", "catch_all",
})
VALID_RULE_MODES: frozenset[str] = frozenset({"priority", "score"})
# Legacy rows (pre epic teamarr-5ag) carry no 'mode'; they are hard priority rules.
LEGACY_RULE_MODE: str = "priority"
NO_VALUE_RULE_TYPES: frozenset[str] = frozenset(
    {"team_feed", "not_team_feed", "epg_match", "home_feed", "away_feed", "catch_all"}
)


@dataclass
class StreamOrderingSettings:
    """Stream ordering rules for prioritizing streams within channels.

    Rules are evaluated in order by priority number (lowest first).
    First matching rule determines stream's position.
    Non-matching streams get priority 999 (sorted to end).
    """

    rules: list[StreamOrderingRule] = field(default_factory=list)


@dataclass
class UpdateCheckSettings:
    """Update notification settings.

    Controls how and when users are notified about new versions.
    Supports both stable releases and dev builds, with configurable
    repository settings for forks.
    """

    enabled: bool = True  # Master toggle for update checking
    notify_stable: bool = True  # Notify about stable releases
    notify_dev: bool = True  # Notify about dev builds (if running dev)
    github_owner: str = "Pharaoh-Labs"  # Repository owner (for forks)
    github_repo: str = "teamarr"  # Repository name (for forks)
    dev_branch: str = "dev"  # Branch to check for dev builds
    auto_detect_branch: bool = True  # Auto-detect branch from version string


@dataclass
class BackupSettings:
    """Scheduled backup settings.

    Controls automatic database backups with rotation and protection.
    Backups are stored as SQLite database copies with optional protection
    to prevent automatic rotation deletion.
    """

    enabled: bool = False  # Master toggle for scheduled backups
    cron: str = "0 3 * * *"  # Cron expression (default: 3 AM daily)
    max_count: int = 7  # Maximum backups to keep (rotation)
    path: str = "./data/backups"  # Directory for backup files


@dataclass
class ChannelNumberingSettings:
    """Global channel numbering and consolidation settings.

    Channel modes:
    - auto: Sequential numbering from channel_range_start by sort priority
    - manual: Per-league starting channel numbers from league_channel_starts

    Consolidation modes:
    - consolidate: Merge all streams for same event into one channel
    - separate: Each stream gets its own channel

    Sort order is always sport→league→time→event_id, controlled by
    channel_sort_priorities table (drag-drop UI).
    """

    global_channel_mode: str = "auto"  # 'auto', 'manual'
    league_channel_starts: dict = field(default_factory=dict)  # {"nfl": 1001, ...}
    global_consolidation_mode: str = "consolidate"  # 'consolidate', 'separate'

    # Stability (AUTO mode only): how existing numbers behave across runs.
    channel_stability_mode: str = "compact"  # 'compact', 'gap', 'strict'
    channel_gap_size: int = 3  # spacing between channels in 'gap' mode
    channel_daily_reset_enabled: bool = True  # run the periodic full re-layout
    channel_daily_reset_time: str = "04:00"  # local HH:MM reset window
    force_channel_relayout_pending: bool = False  # one-shot re-grid armed for next run


@dataclass
class FeedSeparationSettings:
    """Feed separation settings for HOME/AWAY stream detection.

    When enabled, detects feed indicators (HOME/AWAY tokens or team names)
    in stream names and creates separate channels per feed, resolving
    to actual team names.

    ``sports`` scopes the split (#732). Home/away feeds are an RSN phenomenon —
    real for baseball/basketball/hockey/football, absent by definition in
    racing, combat sports, tennis and golf (see lifecycle/feed_side.py). An
    empty list means every sport, which is what existing installs upgrade to.
    """

    enabled: bool = False  # Master toggle
    sports: list[str] = field(default_factory=list)  # Sport codes; [] = all sports
    home_terms: list[str] = field(default_factory=lambda: ["HOME"])
    away_terms: list[str] = field(default_factory=lambda: ["AWAY"])
    detect_team_names: bool = True  # Also match team names as feed indicators
    label_style: str = "team_name"  # 'team_name', 'short_name', 'home_away'


@dataclass
class MediaServerEntry:
    """One Emby/Jellyfin server target for post-generation guide refresh (#471)."""

    name: str = ""
    url: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None


@dataclass
class EmbySettings:
    """Emby integration settings for Live TV guide refresh.

    Multi-server (#471): refresh fans out over every entry in `servers`.
    """

    enabled: bool = False
    servers: list[MediaServerEntry] = field(default_factory=list)


@dataclass
class JellyfinSettings:
    """Jellyfin integration settings for Live TV guide refresh.

    Multi-server (#471): refresh fans out over every entry in `servers`.
    """

    enabled: bool = False
    servers: list[MediaServerEntry] = field(default_factory=list)


@dataclass
class ChannelsDVRServer:
    """One Channels DVR server target for post-generation refresh (#381)."""

    name: str = ""
    url: str | None = None
    source_name: str | None = None
    lineup_id: str | None = None


@dataclass
class ChannelsDVRSettings:
    """Channels DVR integration settings for M3U + XMLTV refresh.

    Multi-server (#381): refresh fans out over every entry in `servers`.
    All servers receive the same channel set/EPG from one Dispatcharr.
    """

    enabled: bool = False
    servers: list[ChannelsDVRServer] = field(default_factory=list)


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
    channel_numbering: ChannelNumberingSettings = field(default_factory=ChannelNumberingSettings)
    stream_ordering: StreamOrderingSettings = field(default_factory=StreamOrderingSettings)
    update_check: UpdateCheckSettings = field(default_factory=UpdateCheckSettings)
    backup: BackupSettings = field(default_factory=BackupSettings)
    feed_separation: FeedSeparationSettings = field(default_factory=FeedSeparationSettings)
    emby: EmbySettings = field(default_factory=EmbySettings)
    jellyfin: JellyfinSettings = field(default_factory=JellyfinSettings)
    channelsdvr: ChannelsDVRSettings = field(default_factory=ChannelsDVRSettings)
    proxy: ProxySettings = field(default_factory=ProxySettings)
    epg_generation_counter: int = 0
    schema_version: int = 52
