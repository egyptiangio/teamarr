"""Consumer layer - EPG generation, matching, channels."""

from teamarr.consumers.cache import (
    CacheRefresher,
    CacheStats,
    LeagueEntry,
    TeamEntry,
    TeamLeagueCache,
    expand_leagues,
    find_leagues_for_stream,
    get_cache,
    refresh_cache,
    refresh_cache_if_needed,
)
from teamarr.consumers.cached_matcher import (
    CachedBatchResult,
    CachedMatcher,
    CachedMatchResult,
)
from teamarr.consumers.channel_lifecycle import (
    ChannelCreationResult,
    ChannelLifecycleManager,
    ChannelLifecycleService,
    CreateTiming,
    DeleteTiming,
    DuplicateMode,
    LifecycleDecision,
    StreamProcessResult,
    create_lifecycle_service,
    generate_event_tvg_id,
    get_lifecycle_settings,
)
from teamarr.consumers.event_epg import (
    EventChannelInfo,
    EventEPGGenerator,
    EventEPGOptions,
)
from teamarr.consumers.event_group_processor import (
    BatchProcessingResult,
    EventGroupProcessor,
    ProcessingResult,
    process_all_event_groups,
    process_event_group,
)
from teamarr.consumers.event_matcher import EventMatcher
from teamarr.consumers.filler import (
    ConditionalFillerTemplate,
    FillerConfig,
    FillerGenerator,
    FillerOptions,
    FillerTemplate,
    FillerType,
    OffseasonFillerTemplate,
)
from teamarr.consumers.multi_league_matcher import (
    BatchMatchResult,
    MultiLeagueMatcher,
    StreamMatchResult,
)
from teamarr.consumers.orchestrator import (
    GenerationResult,
    Orchestrator,
    TeamChannelConfig,
)
from teamarr.consumers.reconciliation import (
    ChannelReconciler,
    ReconciliationIssue,
    ReconciliationResult,
    create_reconciler,
)
from teamarr.consumers.scheduler import (
    LifecycleScheduler,
    get_scheduler_status,
    is_scheduler_running,
    start_lifecycle_scheduler,
    stop_lifecycle_scheduler,
)
from teamarr.consumers.single_league_matcher import MatchResult, SingleLeagueMatcher
from teamarr.consumers.stream_match_cache import (
    StreamCacheEntry,
    StreamMatchCache,
    compute_fingerprint,
    event_to_cache_data,
    get_generation_counter,
    increment_generation_counter,
)
from teamarr.consumers.team_epg import TeamEPGGenerator, TeamEPGOptions
from teamarr.consumers.team_processor import (
    BatchTeamResult,
    TeamProcessingResult,
    TeamProcessor,
    get_all_team_xmltv,
    process_all_teams,
    process_team,
)
from teamarr.core import TemplateConfig
from teamarr.database.templates import EventTemplateConfig

__all__ = [
    # Channel lifecycle
    "ChannelCreationResult",
    "ChannelLifecycleManager",
    "ChannelLifecycleService",
    "CreateTiming",
    "DeleteTiming",
    "DuplicateMode",
    "LifecycleDecision",
    "StreamProcessResult",
    "create_lifecycle_service",
    "generate_event_tvg_id",
    "get_lifecycle_settings",
    # Stream matching
    "MatchResult",
    "SingleLeagueMatcher",
    "BatchMatchResult",
    "MultiLeagueMatcher",
    "StreamMatchResult",
    # Cached stream matching
    "CachedBatchResult",
    "CachedMatcher",
    "CachedMatchResult",
    # Stream match cache
    "StreamCacheEntry",
    "StreamMatchCache",
    "compute_fingerprint",
    "event_to_cache_data",
    "get_generation_counter",
    "increment_generation_counter",
    # Team/league cache
    "CacheRefresher",
    "CacheStats",
    "LeagueEntry",
    "TeamEntry",
    "TeamLeagueCache",
    "expand_leagues",
    "find_leagues_for_stream",
    "get_cache",
    "refresh_cache",
    "refresh_cache_if_needed",
    # Filler generation
    "ConditionalFillerTemplate",
    "FillerConfig",
    "FillerGenerator",
    "FillerOptions",
    "FillerTemplate",
    "FillerType",
    "OffseasonFillerTemplate",
    # Event-based EPG
    "EventChannelInfo",
    "EventEPGGenerator",
    "EventEPGOptions",
    "EventMatcher",
    "EventTemplateConfig",
    # Event group processing
    "BatchProcessingResult",
    "EventGroupProcessor",
    "ProcessingResult",
    "process_all_event_groups",
    "process_event_group",
    # Reconciliation
    "ChannelReconciler",
    "ReconciliationIssue",
    "ReconciliationResult",
    "create_reconciler",
    # Scheduler
    "LifecycleScheduler",
    "get_scheduler_status",
    "is_scheduler_running",
    "start_lifecycle_scheduler",
    "stop_lifecycle_scheduler",
    # Team-based EPG
    "GenerationResult",
    "Orchestrator",
    "TeamChannelConfig",
    "TeamEPGGenerator",
    "TeamEPGOptions",
    "TemplateConfig",
    # Team processing
    "BatchTeamResult",
    "TeamProcessor",
    "TeamProcessingResult",
    "get_all_team_xmltv",
    "process_all_teams",
    "process_team",
]
