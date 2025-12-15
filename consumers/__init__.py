"""Consumer layer for EPG generation.

Consumers implement business logic using the service layer.
All processing uses dataclasses - no dict conversion layers.
"""

from consumers.event_epg import (
    EventChannelInfo,
    EventEPGGenerator,
    EventEPGOptions,
    EventTemplateConfig,
    MatchedStream,
)
from consumers.event_matcher import EventMatcher, has_game_indicator
from consumers.generation import (
    EPGResult,
    EventStats,
    LifecycleStats,
    TeamStats,
    generate_epg,
)
from consumers.orchestrator import (
    GenerationResult,
    Orchestrator,
)
from consumers.stream_matcher import (
    BatchMatchResult,
    MultiLeagueMatcher,
    SingleLeagueMatcher,
    StreamMatchResult,
)
from consumers.team_epg import (
    TeamChannelConfig,
    TeamEPGGenerator,
    TeamEPGOptions,
    TemplateConfig,
)

__all__ = [
    # Full Generation (main entry point)
    "EPGResult",
    "EventStats",
    "LifecycleStats",
    "TeamStats",
    "generate_epg",
    # Orchestrator
    "GenerationResult",
    "Orchestrator",
    # Team EPG
    "TeamChannelConfig",
    "TeamEPGGenerator",
    "TeamEPGOptions",
    "TemplateConfig",
    # Event EPG
    "EventChannelInfo",
    "EventEPGGenerator",
    "EventEPGOptions",
    "EventTemplateConfig",
    "MatchedStream",
    # Event Matcher (V1 approach)
    "EventMatcher",
    "has_game_indicator",
    # Stream Matcher (V2 approach - Events→Streams)
    "BatchMatchResult",
    "MultiLeagueMatcher",
    "SingleLeagueMatcher",
    "StreamMatchResult",
]
