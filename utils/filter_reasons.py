"""
Filter Reasons - Backwards Compatibility Layer

This module now re-exports from utils.match_result for backwards compatibility.
New code should import directly from utils.match_result.

Migration Guide:
    OLD: from utils.filter_reasons import FilterReason, get_display_text
    NEW: from utils.match_result import FilteredReason, FailedReason, get_display_text

The old FilterReason class is preserved but deprecated - it maps to the new
FilteredReason and FailedReason enums.
"""

# Re-export from new module for backwards compatibility
from utils.match_result import (
    # New types
    ResultCategory,
    FilteredReason,
    FailedReason,
    MatchedTier,
    MatchOutcome,

    # Helper functions
    get_display_text,
    is_filtered,
    is_failed,
    is_matched,
    should_record_failure,
    affects_match_rate,
    normalize_reason,
    convert_legacy_reason,

    # Logging utilities
    log_result,
    format_result_summary,

    # Detection helpers
    is_boxing_mma,
    is_beach_soccer,
    is_futsal,

    # Database mapping
    DB_COLUMN_MAPPING,
)


# =============================================================================
# LEGACY FilterReason CLASS - DEPRECATED
# =============================================================================
# This class exists for backwards compatibility with existing code.
# New code should use FilteredReason and FailedReason enums.

class FilterReason:
    """
    DEPRECATED: Use FilteredReason and FailedReason from utils.match_result

    Constants for filter/match reasons - maintained for backwards compatibility.
    """

    # Stream Filtering Reasons (now in FilteredReason)
    NO_GAME_INDICATOR = FilteredReason.NO_GAME_INDICATOR.value
    INCLUDE_REGEX_NOT_MATCHED = FilteredReason.INCLUDE_REGEX_MISS.value
    EXCLUDE_REGEX_MATCHED = FilteredReason.EXCLUDE_REGEX_MATCH.value

    # Event Timing Reasons (now in FilteredReason)
    GAME_PAST = FilteredReason.EVENT_PAST.value
    GAME_FINAL_EXCLUDED = FilteredReason.EVENT_FINAL.value
    OUTSIDE_LOOKAHEAD = FilteredReason.EVENT_OUTSIDE_WINDOW.value

    # League Config (now in FilteredReason)
    LEAGUE_NOT_ENABLED = FilteredReason.LEAGUE_NOT_ENABLED.value

    # Unsupported Sports (now in FilteredReason)
    UNSUPPORTED_BEACH_SOCCER = FilteredReason.UNSUPPORTED_BEACH_SOCCER.value
    UNSUPPORTED_BOXING_MMA = FilteredReason.UNSUPPORTED_BOXING_MMA.value
    UNSUPPORTED_FUTSAL = FilteredReason.UNSUPPORTED_FUTSAL.value

    # Team/Event Failures (now in FailedReason)
    TEAMS_NOT_PARSED = FailedReason.TEAMS_NOT_PARSED.value
    TEAMS_NOT_IN_ESPN = FailedReason.BOTH_TEAMS_NOT_FOUND.value
    NO_COMMON_LEAGUE = FailedReason.NO_COMMON_LEAGUE.value
    NO_LEAGUE_DETECTED = FailedReason.NO_LEAGUE_DETECTED.value
    NO_GAME_FOUND = FailedReason.NO_EVENT_FOUND.value


# =============================================================================
# LEGACY DISPLAY TEXT - DEPRECATED
# =============================================================================

# Display text mapping using old FilterReason constants
DISPLAY_TEXT = {
    FilterReason.NO_GAME_INDICATOR: 'Excluded by filter',
    FilterReason.INCLUDE_REGEX_NOT_MATCHED: 'Did not match inclusion pattern',
    FilterReason.EXCLUDE_REGEX_MATCHED: 'Matched exclusion pattern',
    FilterReason.GAME_PAST: 'Event already passed',
    FilterReason.GAME_FINAL_EXCLUDED: 'Event is final (excluded)',
    FilterReason.NO_GAME_FOUND: 'No event found',
    FilterReason.OUTSIDE_LOOKAHEAD: 'Outside lookahead range',
    FilterReason.TEAMS_NOT_PARSED: 'Teams not parsed',
    FilterReason.TEAMS_NOT_IN_ESPN: 'Team(s) not in ESPN database',
    FilterReason.NO_COMMON_LEAGUE: 'No common league for teams',
    FilterReason.NO_LEAGUE_DETECTED: 'League not detected',
    FilterReason.UNSUPPORTED_BEACH_SOCCER: 'Unsupported (Beach Soccer)',
    FilterReason.UNSUPPORTED_BOXING_MMA: 'Unsupported (Boxing/MMA)',
    FilterReason.UNSUPPORTED_FUTSAL: 'Unsupported (Futsal)',
    FilterReason.LEAGUE_NOT_ENABLED: 'League not enabled',
}


# =============================================================================
# LEGACY COMPATIBILITY FUNCTIONS
# =============================================================================

# Internal reasons mapping (used by event_matcher.py)
INTERNAL_REASONS = {
    'Game already completed (past)': FilterReason.GAME_PAST,
    'Game completed (excluded)': FilterReason.GAME_FINAL_EXCLUDED,
    'No game found between teams': FilterReason.NO_GAME_FOUND,
}

INTERNAL_TO_DISPLAY = {
    'Game already completed (past)': DISPLAY_TEXT[FilterReason.GAME_PAST],
    'Game completed (excluded)': DISPLAY_TEXT[FilterReason.GAME_FINAL_EXCLUDED],
    'No game found between teams': DISPLAY_TEXT[FilterReason.NO_GAME_FOUND],
}


def is_failed_match(reason: str) -> bool:
    """
    DEPRECATED: Use should_record_failure() from utils.match_result

    Check if a reason represents a true failed match that should be recorded.
    """
    return reason in (
        FilterReason.TEAMS_NOT_PARSED,
        FilterReason.TEAMS_NOT_IN_ESPN,
        FilterReason.NO_COMMON_LEAGUE,
        FilterReason.NO_LEAGUE_DETECTED,
        FilterReason.NO_GAME_FOUND,
    )


def is_excluded_from_count(reason: str) -> bool:
    """
    DEPRECATED: Use affects_match_rate() from utils.match_result

    Check if a reason should exclude the stream from the match rate denominator.
    """
    normalized = INTERNAL_REASONS.get(reason, reason)
    return normalized in (
        FilterReason.GAME_PAST,
        FilterReason.GAME_FINAL_EXCLUDED,
        FilterReason.NO_GAME_FOUND,
        FilterReason.LEAGUE_NOT_ENABLED,
    )
