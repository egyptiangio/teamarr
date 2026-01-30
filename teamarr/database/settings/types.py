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
    # When True, call Dispatcharr's /api/channels/logos/cleanup/ after generation
    # This removes ALL unused logos in Dispatcharr, not just ones Teamarr uploaded
    cleanup_unused_logos: bool = False


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
    epg_output_path: str = "./data/teamarr.xml"
    include_final_events: bool = False
    midnight_crossover_mode: str = "postgame"
    cron_expression: str = "0 * * * *"
    prepend_postponed_label: bool = True


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
    xmltv_generator_name: str = "Teamarr"
    xmltv_generator_url: str = "https://github.com/Pharaoh-Labs/teamarr"


@dataclass
class APISettings:
    """API behavior settings."""

    timeout: int = 10
    retry_count: int = 3
    soccer_cache_refresh_frequency: str = "weekly"
    team_cache_refresh_frequency: str = "weekly"
    # Startup cache refresh: number of days before cache is considered stale
    # 0 = disabled (never auto-refresh on startup), >0 = refresh if older than N days
    startup_cache_max_age_days: int = 1  # Default: refresh if older than 1 day


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


@dataclass
class StreamOrderingRule:
    """A single stream ordering rule.

    Rules are evaluated in priority order (lowest number first).
    First matching rule determines the stream's sort position within a channel.
    """

    type: str  # "m3u", "group", "regex"
    value: str  # Account name, group name, or regex pattern
    priority: int  # 1-99, lower = higher priority


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
class ChannelNumberingSettings:
    """Channel numbering and sorting settings for AUTO groups.

    Controls how channel numbers are assigned and sorted across event groups.

    Numbering modes:
    - strict_block: Reserve blocks by total_stream_count (current behavior, large gaps, minimal drift)
    - rational_block: Reserve by actual channel count rounded to 10 (smaller gaps, low drift)
    - strict_compact: No reservation, sequential numbers (no gaps, higher drift risk)

    Sorting scopes (only for rational_block and strict_compact):
    - per_group: Sort channels within each group
    - global: Sort all AUTO channels across groups by sport/league/time

    Sort by options (for per_group scope):
    - sport_league_time: Sort by sport, then league, then event time
    - time: Sort by event time only
    - stream_order: Keep original stream order from M3U
    """  # noqa: E501

    numbering_mode: str = "strict_block"  # 'strict_block', 'rational_block', 'strict_compact'
    sorting_scope: str = "per_group"  # 'per_group', 'global'
    sort_by: str = "time"  # 'sport_league_time', 'time', 'stream_order'


# =============================================================================
# AI PROVIDER SETTINGS
# =============================================================================


@dataclass
class OllamaProviderSettings:
    """Ollama (local) provider settings."""

    enabled: bool = False
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout: int = 180


@dataclass
class OpenAIProviderSettings:
    """OpenAI (ChatGPT) provider settings."""

    enabled: bool = False
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: int = 60
    # Organization ID (optional)
    organization: str = ""


@dataclass
class AnthropicProviderSettings:
    """Anthropic (Claude) provider settings."""

    enabled: bool = False
    api_key: str = ""
    model: str = "claude-3-5-sonnet-20241022"
    timeout: int = 60


@dataclass
class GrokProviderSettings:
    """xAI (Grok) provider settings."""

    enabled: bool = False
    api_key: str = ""
    model: str = "grok-2-latest"
    timeout: int = 60


@dataclass
class GroqProviderSettings:
    """Groq (fast inference) provider settings.

    Free tier available with generous rate limits.
    Supports Llama, Mixtral, and other open models.
    """

    enabled: bool = False
    api_key: str = ""
    model: str = "llama-3.1-8b-instant"  # Fast, free tier friendly
    timeout: int = 60


@dataclass
class GeminiProviderSettings:
    """Google Gemini provider settings.

    Free tier available for Gemini 1.5 Flash.
    """

    enabled: bool = False
    api_key: str = ""
    model: str = "gemini-2.0-flash"  # Free tier model
    timeout: int = 60


@dataclass
class OpenRouterProviderSettings:
    """OpenRouter provider settings.

    Aggregator with access to many models.
    Some models have free tiers or very low costs.
    """

    enabled: bool = False
    api_key: str = ""
    model: str = "meta-llama/llama-3.1-8b-instruct:free"  # Free model
    timeout: int = 60
    # Optional: site URL and app name for rankings
    site_url: str = ""
    app_name: str = "Teamarr"


# AI Task types - used for task-to-provider assignment
AI_TASKS = [
    "pattern_learning",  # Learning regex patterns from streams
    "stream_parsing",    # Parsing individual streams with AI
    "event_cards",       # Generating event group cards (future)
    "team_matching",     # AI-assisted team matching (future)
    "description_gen",   # Generating programme descriptions (future)
]

# Default provider for each task
DEFAULT_TASK_PROVIDERS = {
    "pattern_learning": "ollama",
    "stream_parsing": "ollama",
    "event_cards": "ollama",
    "team_matching": "ollama",
    "description_gen": "ollama",
}


@dataclass
class AITaskAssignments:
    """Maps AI tasks to their assigned provider."""

    pattern_learning: str = "ollama"
    stream_parsing: str = "ollama"
    event_cards: str = "ollama"
    team_matching: str = "ollama"
    description_gen: str = "ollama"


@dataclass
class AISettings:
    """AI integration settings with multi-provider support.

    Supports multiple AI providers:
    - ollama: Local Ollama instance (free, private)
    - openai: OpenAI API (ChatGPT)
    - anthropic: Anthropic API (Claude)
    - grok: xAI API (Grok) - paid
    - groq: Groq (fast inference) - FREE TIER
    - gemini: Google Gemini - FREE TIER
    - openrouter: OpenRouter (aggregator) - FREE TIER available

    Each task can be assigned to a specific provider via task_assignments.
    """

    # Master toggle
    enabled: bool = False

    # Provider configurations
    ollama: OllamaProviderSettings = field(default_factory=OllamaProviderSettings)
    openai: OpenAIProviderSettings = field(default_factory=OpenAIProviderSettings)
    anthropic: AnthropicProviderSettings = field(default_factory=AnthropicProviderSettings)
    grok: GrokProviderSettings = field(default_factory=GrokProviderSettings)
    groq: GroqProviderSettings = field(default_factory=GroqProviderSettings)
    gemini: GeminiProviderSettings = field(default_factory=GeminiProviderSettings)
    openrouter: OpenRouterProviderSettings = field(default_factory=OpenRouterProviderSettings)

    # Task-to-provider assignments
    task_assignments: AITaskAssignments = field(default_factory=AITaskAssignments)

    # General settings (apply to all providers)
    batch_size: int = 10  # Streams per AI batch call
    learn_patterns: bool = True  # Learn regex patterns from AI results
    fallback_to_regex: bool = True  # Fall back to built-in regex if AI fails

    # Legacy fields for backwards compatibility (mapped to ollama provider)
    @property
    def ollama_url(self) -> str:
        return self.ollama.url

    @property
    def model(self) -> str:
        return self.ollama.model

    @property
    def timeout(self) -> int:
        return self.ollama.timeout

    @property
    def use_for_parsing(self) -> bool:
        return self.enabled and self.ollama.enabled

    @property
    def use_for_matching(self) -> bool:
        return False  # Experimental, not yet implemented


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
    ai: AISettings = field(default_factory=AISettings)
    epg_generation_counter: int = 0
    schema_version: int = 44
