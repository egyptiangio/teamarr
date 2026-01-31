"""Service layer.

This layer provides clean APIs for business operations, hiding the consumer
layer implementation details from the API layer.

Layer hierarchy:
    API → Services → Consumers → Providers
"""

# Core services
# Service facades (hide consumer layer from API)
from teamarr.services.cache_service import (
    CacheService,
    CacheStats,
    LeagueInfo,
    RefreshResult,
    TeamInfo,
    create_cache_service,
)
from teamarr.services.channel_service import (
    ChannelService,
    DeletionResult,
    ReconciliationIssue,
    ReconciliationResult,
    ReconciliationSummary,
    create_channel_service,
)
from teamarr.services.detection_keywords import DetectionKeywordService
from teamarr.services.epg_service import (
    EPGService,
    EventEPGOptions,
    GenerationResult,
    TeamChannelConfig,
    TeamEPGOptions,
    create_epg_service,
)
from teamarr.services.group_service import (
    BatchGroupResult,
    ChannelStats,
    EPGStats,
    GroupProcessingResult,
    GroupService,
    StreamStats,
    create_group_service,
)
from teamarr.services.league_mappings import (
    LeagueMappingService,
    get_league_mapping_service,
    init_league_mapping_service,
)
from teamarr.services.matching_service import (
    MatchingService,
    MatchResult,
    create_matching_service,
)
from teamarr.services.scheduler_service import (
    SchedulerRunResult,
    SchedulerService,
    SchedulerStatus,
    create_scheduler_service,
)
from teamarr.services.sports_data import SportsDataService, create_default_service
from teamarr.services.stream_ordering import (
    StreamOrderingService,
    StreamWithPriority,
    get_stream_ordering_service,
)

__all__ = [
    # Core services
    "LeagueMappingService",
    "SportsDataService",
    "create_default_service",
    "get_league_mapping_service",
    "init_league_mapping_service",
    # EPG service
    "EPGService",
    "EventEPGOptions",
    "GenerationResult",
    "TeamChannelConfig",
    "TeamEPGOptions",
    "create_epg_service",
    # Matching service
    "MatchingService",
    "MatchResult",
    "create_matching_service",
    # Channel service
    "ChannelService",
    "DeletionResult",
    "ReconciliationIssue",
    "ReconciliationResult",
    "ReconciliationSummary",
    "create_channel_service",
    # Scheduler service
    "SchedulerRunResult",
    "SchedulerService",
    "SchedulerStatus",
    "create_scheduler_service",
    # Cache service
    "CacheService",
    "CacheStats",
    "LeagueInfo",
    "RefreshResult",
    "TeamInfo",
    "create_cache_service",
    # Group service
    "BatchGroupResult",
    "ChannelStats",
    "EPGStats",
    "GroupProcessingResult",
    "GroupService",
    "StreamStats",
    "create_group_service",
    # Stream ordering service
    "StreamOrderingService",
    "StreamWithPriority",
    "get_stream_ordering_service",
    # Detection keyword service
    "DetectionKeywordService",
]
