"""Stream matching module.

Provides stream-to-event matching with classification, normalization,
and result tracking.

Main entry point:
    from teamarr.consumers.matching import StreamMatcher

    matcher = StreamMatcher(service, db_factory, group_id, search_leagues)
    result = matcher.match_all(streams, target_date)
"""

from teamarr.consumers.matching.classifier import (
    ClassifiedStream,
    StreamCategory,
    classify_stream,
    classify_streams,
)
from teamarr.consumers.matching.constants import (
    ACCEPT_WITH_DATE_THRESHOLD,
    BOTH_TEAMS_THRESHOLD,
    DATE_MISMATCH_PENALTY,
    HIGH_CONFIDENCE_THRESHOLD,
    MATCH_WINDOW_DAYS,
)
from teamarr.consumers.matching.event_matcher import (
    EventCardMatcher,
    EventMatchContext,
)
from teamarr.consumers.matching.matcher import (
    BatchMatchResult,
    MatchedStreamResult,
    StreamMatcher,
)
from teamarr.consumers.matching.normalizer import (
    NormalizedStream,
    normalize_for_matching,
    normalize_stream,
)
from teamarr.consumers.matching.result import (
    FailedReason,
    FilteredReason,
    MatchMethod,
    MatchOutcome,
    ResultAggregator,
    ResultCategory,
)
from teamarr.consumers.matching.team_matcher import (
    MatchContext,
    TeamMatcher,
)

__all__ = [
    # Constants
    "MATCH_WINDOW_DAYS",
    "HIGH_CONFIDENCE_THRESHOLD",
    "ACCEPT_WITH_DATE_THRESHOLD",
    "BOTH_TEAMS_THRESHOLD",
    "DATE_MISMATCH_PENALTY",
    # Main entry point
    "StreamMatcher",
    "MatchedStreamResult",
    "BatchMatchResult",
    # Result types
    "ResultCategory",
    "FilteredReason",
    "FailedReason",
    "MatchMethod",
    "MatchOutcome",
    "ResultAggregator",
    # Normalizer
    "NormalizedStream",
    "normalize_stream",
    "normalize_for_matching",
    # Classifier
    "StreamCategory",
    "ClassifiedStream",
    "classify_stream",
    "classify_streams",
    # TeamMatcher
    "TeamMatcher",
    "MatchContext",
    # EventCardMatcher
    "EventCardMatcher",
    "EventMatchContext",
]
