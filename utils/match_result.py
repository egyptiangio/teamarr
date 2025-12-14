"""
Match Result System - Single Source of Truth

Comprehensive result hierarchy for stream matching with three categories:
- FILTERED: Stream excluded before matching attempted
- FAILED: Matching attempted but couldn't complete
- MATCHED: Successfully matched with detection tier

This module provides:
- Enums for all result categories and reasons
- Helper functions for classification and display
- Logging utilities with consistent formatting
- Database column mappings for statistics

Usage:
    from utils.match_result import (
        ResultCategory, FilteredReason, FailedReason, MatchedTier,
        MatchOutcome, is_filtered, is_failed, is_matched,
        get_display_text, log_result
    )
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Union
import logging


# =============================================================================
# RESULT CATEGORIES
# =============================================================================

class ResultCategory(Enum):
    """Top-level result category for stream matching."""
    FILTERED = 'filtered'  # Stream excluded before matching
    FAILED = 'failed'      # Matching attempted but failed
    MATCHED = 'matched'    # Successfully matched to event


# =============================================================================
# FILTERED REASONS - Stream excluded before matching attempted
# =============================================================================

class FilteredReason(Enum):
    """
    Reasons for filtering a stream BEFORE matching is attempted.

    These are expected exclusions based on stream characteristics or
    user configuration - not failures.
    """
    # Pre-filter exclusions (stream doesn't look like a game)
    NO_GAME_INDICATOR = 'no_game_indicator'      # No vs/@/at detected
    INCLUDE_REGEX_MISS = 'include_regex_miss'    # Didn't match inclusion pattern
    EXCLUDE_REGEX_MATCH = 'exclude_regex_match'  # Matched exclusion pattern

    # Unsupported content (detected during team parsing)
    UNSUPPORTED_BEACH_SOCCER = 'unsupported_beach_soccer'  # BS/BSC suffix
    UNSUPPORTED_BOXING_MMA = 'unsupported_boxing_mma'      # Main card, prelims, etc.
    UNSUPPORTED_FUTSAL = 'unsupported_futsal'              # FP suffix

    # Event timing exclusions (event found but excluded by timing rules)
    EVENT_PAST = 'event_past'              # Event already completed (past day)
    EVENT_FINAL = 'event_final'            # Event is final (excluded by setting)
    EVENT_OUTSIDE_WINDOW = 'event_outside_window'  # Outside lookahead window

    # Configuration exclusions
    LEAGUE_NOT_ENABLED = 'league_not_enabled'  # Event in non-enabled league


# =============================================================================
# FAILED REASONS - Matching attempted but couldn't complete
# =============================================================================

class FailedReason(Enum):
    """
    Reasons for match FAILURE - matching was attempted but couldn't complete.

    These represent genuine failures that might indicate:
    - Data issues (teams not in ESPN)
    - Detection limitations (ambiguous streams)
    - Scheduling gaps (no event found)
    """
    # Team parsing failures
    TEAMS_NOT_PARSED = 'teams_not_parsed'    # Couldn't extract team names

    # Team lookup failures
    TEAM1_NOT_FOUND = 'team1_not_found'      # First team not in any ESPN league
    TEAM2_NOT_FOUND = 'team2_not_found'      # Second team not in any ESPN league
    BOTH_TEAMS_NOT_FOUND = 'both_teams_not_found'  # Neither team found
    NO_COMMON_LEAGUE = 'no_common_league'    # Teams in different leagues

    # League detection failures (multi-sport groups)
    NO_LEAGUE_DETECTED = 'no_league_detected'  # Teams matched but can't determine league
    AMBIGUOUS_LEAGUE = 'ambiguous_league'      # Multiple possible leagues, can't decide

    # Event lookup failures
    NO_EVENT_FOUND = 'no_event_found'  # Teams matched, league detected, but no game scheduled


# =============================================================================
# MATCHED TIERS - Success with detection method info
# =============================================================================

class MatchedTier(Enum):
    """
    Detection tier for successful matches.

    Higher tiers indicate more confidence/specificity in the match.
    """
    # Tier 1: Explicit league indicator in stream name
    TIER_1 = '1'  # "NHL: Bruins vs Rangers" → NHL

    # Tier 2: Sport indicator, tried leagues within sport
    TIER_2 = '2'  # "Hockey: Bruins vs Rangers" → NHL

    # Tier 3: Cache lookup with varying specificity
    TIER_3A = '3a'  # Both teams + date + time → exact schedule match
    TIER_3B = '3b'  # Both teams + time only → infer today
    TIER_3C = '3c'  # Both teams only → closest game to now

    # Tier 4: Partial match with schedule search
    TIER_4A = '4a'  # One team + date/time → search schedule for opponent
    TIER_4B = '4b'  # One team only → search schedule, closest game

    # Direct assignment (single-league groups)
    DIRECT = 'direct'  # Group has single assigned league

    # Cache hit (fingerprint cache)
    CACHE = 'cache'  # Match retrieved from fingerprint cache


# =============================================================================
# MATCH OUTCOME - Unified result object
# =============================================================================

@dataclass
class MatchOutcome:
    """
    Unified result object for stream matching.

    Use the factory methods to create instances:
        MatchOutcome.filtered(FilteredReason.NO_GAME_INDICATOR)
        MatchOutcome.failed(FailedReason.NO_EVENT_FOUND, detail="...")
        MatchOutcome.matched(MatchedTier.TIER_3A, event=event_dict)
    """
    category: ResultCategory

    # For FILTERED results
    filtered_reason: Optional[FilteredReason] = None

    # For FAILED results
    failed_reason: Optional[FailedReason] = None

    # For MATCHED results
    matched_tier: Optional[MatchedTier] = None
    event: Optional[Dict] = None
    detected_league: Optional[str] = None

    # Common fields
    stream: Optional[Dict] = None
    detail: Optional[str] = None
    parsed_teams: Optional[Dict] = None
    team_result: Optional[Dict] = None

    # For LEAGUE_NOT_ENABLED - the league that was found
    found_league: Optional[str] = None
    found_league_name: Optional[str] = None

    @classmethod
    def filtered(
        cls,
        reason: FilteredReason,
        stream: Dict = None,
        detail: str = None,
        found_league: str = None,
        found_league_name: str = None
    ) -> 'MatchOutcome':
        """Create a FILTERED result."""
        return cls(
            category=ResultCategory.FILTERED,
            filtered_reason=reason,
            stream=stream,
            detail=detail,
            found_league=found_league,
            found_league_name=found_league_name
        )

    @classmethod
    def failed(
        cls,
        reason: FailedReason,
        stream: Dict = None,
        detail: str = None,
        parsed_teams: Dict = None,
        team_result: Dict = None
    ) -> 'MatchOutcome':
        """Create a FAILED result."""
        return cls(
            category=ResultCategory.FAILED,
            failed_reason=reason,
            stream=stream,
            detail=detail,
            parsed_teams=parsed_teams,
            team_result=team_result
        )

    @classmethod
    def matched(
        cls,
        tier: MatchedTier,
        event: Dict,
        detected_league: str,
        stream: Dict = None,
        team_result: Dict = None
    ) -> 'MatchOutcome':
        """Create a MATCHED result."""
        return cls(
            category=ResultCategory.MATCHED,
            matched_tier=tier,
            event=event,
            detected_league=detected_league,
            stream=stream,
            team_result=team_result
        )

    @property
    def is_filtered(self) -> bool:
        return self.category == ResultCategory.FILTERED

    @property
    def is_failed(self) -> bool:
        return self.category == ResultCategory.FAILED

    @property
    def is_matched(self) -> bool:
        return self.category == ResultCategory.MATCHED

    @property
    def reason(self) -> Optional[Union[FilteredReason, FailedReason]]:
        """Get the reason enum (for FILTERED or FAILED results)."""
        if self.filtered_reason:
            return self.filtered_reason
        if self.failed_reason:
            return self.failed_reason
        return None

    @property
    def reason_value(self) -> Optional[str]:
        """Get the string value of the reason."""
        reason = self.reason
        return reason.value if reason else None


# =============================================================================
# DISPLAY TEXT - Human-readable descriptions
# =============================================================================

FILTERED_DISPLAY = {
    FilteredReason.NO_GAME_INDICATOR: 'No game indicator (vs/@/at)',
    FilteredReason.INCLUDE_REGEX_MISS: 'Did not match inclusion pattern',
    FilteredReason.EXCLUDE_REGEX_MATCH: 'Matched exclusion pattern',
    FilteredReason.UNSUPPORTED_BEACH_SOCCER: 'Unsupported: Beach Soccer',
    FilteredReason.UNSUPPORTED_BOXING_MMA: 'Unsupported: Boxing/MMA',
    FilteredReason.UNSUPPORTED_FUTSAL: 'Unsupported: Futsal',
    FilteredReason.EVENT_PAST: 'Event already completed',
    FilteredReason.EVENT_FINAL: 'Event is final (excluded)',
    FilteredReason.EVENT_OUTSIDE_WINDOW: 'Outside lookahead window',
    FilteredReason.LEAGUE_NOT_ENABLED: 'League not enabled',
}

FAILED_DISPLAY = {
    FailedReason.TEAMS_NOT_PARSED: 'Could not parse team names',
    FailedReason.TEAM1_NOT_FOUND: 'First team not found in ESPN',
    FailedReason.TEAM2_NOT_FOUND: 'Second team not found in ESPN',
    FailedReason.BOTH_TEAMS_NOT_FOUND: 'Neither team found in ESPN',
    FailedReason.NO_COMMON_LEAGUE: 'Teams have no common league',
    FailedReason.NO_LEAGUE_DETECTED: 'Could not detect league',
    FailedReason.AMBIGUOUS_LEAGUE: 'Multiple leagues possible',
    FailedReason.NO_EVENT_FOUND: 'No scheduled event found',
}

MATCHED_DISPLAY = {
    MatchedTier.TIER_1: 'League indicator',
    MatchedTier.TIER_2: 'Sport indicator',
    MatchedTier.TIER_3A: 'Teams + date + time',
    MatchedTier.TIER_3B: 'Teams + time',
    MatchedTier.TIER_3C: 'Teams only',
    MatchedTier.TIER_4A: 'One team + date/time',
    MatchedTier.TIER_4B: 'One team only',
    MatchedTier.DIRECT: 'Direct assignment',
    MatchedTier.CACHE: 'Fingerprint cache',
}


def get_display_text(
    outcome: Union[MatchOutcome, FilteredReason, FailedReason, MatchedTier, str],
    lookahead_days: int = None,
    league_name: str = None
) -> str:
    """
    Get human-readable display text for a match result.

    Args:
        outcome: MatchOutcome object, reason enum, or legacy string
        lookahead_days: Optional days for NO_EVENT_FOUND
        league_name: Optional league name for LEAGUE_NOT_ENABLED

    Returns:
        Human-readable description
    """
    # Handle MatchOutcome objects
    if isinstance(outcome, MatchOutcome):
        if outcome.is_matched:
            tier_text = MATCHED_DISPLAY.get(outcome.matched_tier, str(outcome.matched_tier))
            return f"Matched via {tier_text}"
        elif outcome.is_failed:
            return FAILED_DISPLAY.get(outcome.failed_reason, str(outcome.failed_reason))
        elif outcome.is_filtered:
            text = FILTERED_DISPLAY.get(outcome.filtered_reason, str(outcome.filtered_reason))
            if outcome.filtered_reason == FilteredReason.LEAGUE_NOT_ENABLED and outcome.found_league_name:
                return f"Found in {outcome.found_league_name} (not enabled)"
            return text

    # Handle individual enums
    if isinstance(outcome, FilteredReason):
        text = FILTERED_DISPLAY.get(outcome, str(outcome))
        if outcome == FilteredReason.LEAGUE_NOT_ENABLED and league_name:
            return f"Found in {league_name} (not enabled)"
        return text

    if isinstance(outcome, FailedReason):
        text = FAILED_DISPLAY.get(outcome, str(outcome))
        if outcome == FailedReason.NO_EVENT_FOUND and lookahead_days:
            return f"No event in {lookahead_days} day lookahead"
        return text

    if isinstance(outcome, MatchedTier):
        return MATCHED_DISPLAY.get(outcome, str(outcome))

    # Handle legacy string values
    if isinstance(outcome, str):
        return _get_legacy_display_text(outcome)

    return str(outcome)


def _get_legacy_display_text(reason: str) -> str:
    """Handle legacy string reason values for backwards compatibility."""
    # Map old string values to display text
    legacy_map = {
        'no_game_indicator': 'No game indicator',
        'include_regex_not_matched': 'Did not match inclusion pattern',
        'exclude_regex_matched': 'Matched exclusion pattern',
        'game_past': 'Event already passed',
        'game_final_excluded': 'Event is final (excluded)',
        'no_game_found': 'No event found',
        'outside_lookahead': 'Outside lookahead range',
        'teams_not_parsed': 'Teams not parsed',
        'teams_not_in_espn': 'Team(s) not in ESPN',
        'no_common_league': 'No common league',
        'no_league_detected': 'League not detected',
        'unsupported_beach_soccer': 'Unsupported: Beach Soccer',
        'unsupported_boxing_mma': 'Unsupported: Boxing/MMA',
        'unsupported_futsal': 'Unsupported: Futsal',
        'league_not_enabled': 'League not enabled',
    }
    return legacy_map.get(reason, reason)


# =============================================================================
# CLASSIFICATION HELPERS
# =============================================================================

def is_filtered(outcome: Union[MatchOutcome, FilteredReason, str]) -> bool:
    """Check if result is a FILTERED outcome."""
    if isinstance(outcome, MatchOutcome):
        return outcome.is_filtered
    if isinstance(outcome, FilteredReason):
        return True
    if isinstance(outcome, FailedReason):
        return False
    # Legacy string check
    return outcome in {r.value for r in FilteredReason}


def is_failed(outcome: Union[MatchOutcome, FailedReason, str]) -> bool:
    """Check if result is a FAILED outcome."""
    if isinstance(outcome, MatchOutcome):
        return outcome.is_failed
    if isinstance(outcome, FailedReason):
        return True
    if isinstance(outcome, FilteredReason):
        return False
    # Legacy string check
    return outcome in {r.value for r in FailedReason}


def is_matched(outcome: Union[MatchOutcome, str]) -> bool:
    """Check if result is a MATCHED outcome."""
    if isinstance(outcome, MatchOutcome):
        return outcome.is_matched
    return False  # Legacy strings can't represent matched


def should_record_failure(outcome: Union[MatchOutcome, FailedReason, FilteredReason, str]) -> bool:
    """
    Check if this outcome should be recorded in the failed matches table.

    Only actual failures are recorded - filtered streams are expected exclusions.
    """
    if isinstance(outcome, MatchOutcome):
        return outcome.is_failed
    # Direct enum check
    if isinstance(outcome, FailedReason):
        return True  # All FailedReason enums are failures
    if isinstance(outcome, FilteredReason):
        return False  # FilteredReason are not failures
    # Legacy string check
    return outcome in {r.value for r in FailedReason}


def affects_match_rate(outcome: Union[MatchOutcome, str]) -> bool:
    """
    Check if this outcome should count toward the match rate denominator.

    Returns True for outcomes that represent streams we TRIED to match
    (failed or matched). Returns False for streams we filtered out before
    attempting to match.
    """
    if isinstance(outcome, MatchOutcome):
        return outcome.is_failed or outcome.is_matched

    # Legacy: Only certain filtered reasons should exclude from denominator
    excluded = {
        FilteredReason.NO_GAME_INDICATOR.value,
        FilteredReason.INCLUDE_REGEX_MISS.value,
        FilteredReason.EXCLUDE_REGEX_MATCH.value,
        FilteredReason.UNSUPPORTED_BEACH_SOCCER.value,
        FilteredReason.UNSUPPORTED_BOXING_MMA.value,
        FilteredReason.UNSUPPORTED_FUTSAL.value,
    }
    return outcome not in excluded


# =============================================================================
# LOGGING UTILITIES
# =============================================================================

def log_result(
    logger: logging.Logger,
    outcome: MatchOutcome,
    stream_name: str = None,
    max_stream_len: int = 60
) -> None:
    """
    Log a match result with consistent formatting.

    Format:
        [FILTERED:reason] stream_name | detail
        [FAILED:reason] stream_name | detail
        [TIER X] stream_name → LEAGUE | event_name

    Args:
        logger: Logger instance
        outcome: MatchOutcome to log
        stream_name: Stream name (uses outcome.stream if not provided)
        max_stream_len: Max length before truncating stream name
    """
    if stream_name is None:
        stream_name = outcome.stream.get('name', '') if outcome.stream else ''

    # Truncate stream name if needed
    display_name = stream_name[:max_stream_len]
    if len(stream_name) > max_stream_len:
        display_name += '...'

    if outcome.is_matched:
        tier = outcome.matched_tier.value if outcome.matched_tier else '?'
        league = (outcome.detected_league or '').upper()
        event_name = ''
        if outcome.event:
            event_name = outcome.event.get('name', outcome.event.get('shortName', ''))

        logger.info(f"[TIER {tier}] {display_name} → {league} | {event_name}")

    elif outcome.is_failed:
        reason = outcome.failed_reason.value if outcome.failed_reason else 'unknown'
        detail = outcome.detail or ''

        if detail:
            logger.info(f"[FAILED:{reason}] {display_name} | {detail}")
        else:
            logger.info(f"[FAILED:{reason}] {display_name}")

    elif outcome.is_filtered:
        reason = outcome.filtered_reason.value if outcome.filtered_reason else 'unknown'

        # Some filtered reasons are debug-level (expected high volume)
        if outcome.filtered_reason in (
            FilteredReason.NO_GAME_INDICATOR,
            FilteredReason.INCLUDE_REGEX_MISS,
            FilteredReason.EXCLUDE_REGEX_MATCH,
        ):
            logger.debug(f"[FILTERED:{reason}] {display_name}")
        else:
            detail = outcome.detail or ''
            if detail:
                logger.info(f"[FILTERED:{reason}] {display_name} | {detail}")
            else:
                logger.info(f"[FILTERED:{reason}] {display_name}")


def format_result_summary(
    filtered_count: int = 0,
    failed_count: int = 0,
    matched_count: int = 0,
    by_filtered_reason: Dict[FilteredReason, int] = None,
    by_failed_reason: Dict[FailedReason, int] = None,
    by_tier: Dict[MatchedTier, int] = None
) -> str:
    """
    Format a summary of match results for logging.

    Returns:
        Multi-line summary string
    """
    lines = []
    lines.append(f"Match Results: {matched_count} matched, {failed_count} failed, {filtered_count} filtered")

    if by_tier:
        tier_parts = [f"T{t.value}:{c}" for t, c in sorted(by_tier.items(), key=lambda x: x[0].value)]
        lines.append(f"  Matched by tier: {', '.join(tier_parts)}")

    if by_failed_reason:
        fail_parts = [f"{r.value}:{c}" for r, c in by_failed_reason.items()]
        lines.append(f"  Failed by reason: {', '.join(fail_parts)}")

    if by_filtered_reason:
        filt_parts = [f"{r.value}:{c}" for r, c in by_filtered_reason.items()]
        lines.append(f"  Filtered by reason: {', '.join(filt_parts)}")

    return '\n'.join(lines)


# =============================================================================
# DATABASE COLUMN MAPPING
# =============================================================================

# Maps reasons to database columns for aggregate statistics
DB_COLUMN_MAPPING = {
    # Filtered reasons
    FilteredReason.NO_GAME_INDICATOR: 'filtered_no_indicator',
    FilteredReason.INCLUDE_REGEX_MISS: 'filtered_include_regex',
    FilteredReason.EXCLUDE_REGEX_MATCH: 'filtered_exclude_regex',
    FilteredReason.UNSUPPORTED_BEACH_SOCCER: 'filtered_unsupported_sport',
    FilteredReason.UNSUPPORTED_BOXING_MMA: 'filtered_unsupported_sport',
    FilteredReason.UNSUPPORTED_FUTSAL: 'filtered_unsupported_sport',
    FilteredReason.EVENT_PAST: 'filtered_outside_lookahead',
    FilteredReason.EVENT_FINAL: 'filtered_final',
    FilteredReason.EVENT_OUTSIDE_WINDOW: 'filtered_outside_lookahead',
    FilteredReason.LEAGUE_NOT_ENABLED: 'filtered_league_not_enabled',

    # Failed reasons (all map to a general failures column or tracked separately)
    FailedReason.TEAMS_NOT_PARSED: 'failed_matches',
    FailedReason.TEAM1_NOT_FOUND: 'failed_matches',
    FailedReason.TEAM2_NOT_FOUND: 'failed_matches',
    FailedReason.BOTH_TEAMS_NOT_FOUND: 'failed_matches',
    FailedReason.NO_COMMON_LEAGUE: 'failed_matches',
    FailedReason.NO_LEAGUE_DETECTED: 'failed_matches',
    FailedReason.AMBIGUOUS_LEAGUE: 'failed_matches',
    FailedReason.NO_EVENT_FOUND: 'failed_matches',
}


# =============================================================================
# LEGACY COMPATIBILITY
# =============================================================================

# Map old FilterReason string values to new enums
LEGACY_TO_FILTERED = {
    'no_game_indicator': FilteredReason.NO_GAME_INDICATOR,
    'include_regex_not_matched': FilteredReason.INCLUDE_REGEX_MISS,
    'exclude_regex_matched': FilteredReason.EXCLUDE_REGEX_MATCH,
    'unsupported_beach_soccer': FilteredReason.UNSUPPORTED_BEACH_SOCCER,
    'unsupported_boxing_mma': FilteredReason.UNSUPPORTED_BOXING_MMA,
    'unsupported_futsal': FilteredReason.UNSUPPORTED_FUTSAL,
    'game_past': FilteredReason.EVENT_PAST,
    'game_final_excluded': FilteredReason.EVENT_FINAL,
    'outside_lookahead': FilteredReason.EVENT_OUTSIDE_WINDOW,
    'league_not_enabled': FilteredReason.LEAGUE_NOT_ENABLED,
}

LEGACY_TO_FAILED = {
    'teams_not_parsed': FailedReason.TEAMS_NOT_PARSED,
    'teams_not_in_espn': FailedReason.BOTH_TEAMS_NOT_FOUND,
    'no_common_league': FailedReason.NO_COMMON_LEAGUE,
    'no_league_detected': FailedReason.NO_LEAGUE_DETECTED,
    'no_game_found': FailedReason.NO_EVENT_FOUND,
}


def convert_legacy_reason(reason: str) -> Union[FilteredReason, FailedReason, str]:
    """Convert a legacy string reason to the new enum type."""
    if reason in LEGACY_TO_FILTERED:
        return LEGACY_TO_FILTERED[reason]
    if reason in LEGACY_TO_FAILED:
        return LEGACY_TO_FAILED[reason]
    return reason  # Return as-is if not recognized


def normalize_reason(reason: Union[str, FilteredReason, FailedReason]) -> str:
    """
    Normalize a reason to its string value for database storage.

    Works with both new enums and legacy strings.
    """
    if isinstance(reason, (FilteredReason, FailedReason)):
        return reason.value
    return reason


def categorize_team_matcher_reason(reason) -> str:
    """
    Convert verbose team_matcher reasons to standardized failure reason codes.

    team_matcher.extract_teams() returns reasons like:
    - "Away team not found: albany"
    - "Home team not found: yale"
    - "No team data available for league: xyz"
    - "Stream name empty after normalization"

    This converts them to standard FailedReason enum values.
    Also handles FailedReason enums that are already categorized.
    """
    if not reason:
        return FailedReason.TEAMS_NOT_PARSED.value

    # Handle FailedReason enum - already categorized
    if isinstance(reason, FailedReason):
        return reason.value

    # Handle FilteredReason enum - convert to string value
    if isinstance(reason, FilteredReason):
        return reason.value

    # Convert to string and lowercase for pattern matching
    reason_lower = str(reason).lower()

    # Team lookup failures
    if 'away team not found' in reason_lower:
        return FailedReason.TEAM1_NOT_FOUND.value
    if 'home team not found' in reason_lower:
        return FailedReason.TEAM2_NOT_FOUND.value
    if 'no team data' in reason_lower:
        return FailedReason.TEAMS_NOT_PARSED.value

    # Parsing failures
    if 'empty after normalization' in reason_lower:
        return FailedReason.TEAMS_NOT_PARSED.value
    if 'no separator' in reason_lower:
        return FailedReason.TEAMS_NOT_PARSED.value
    if 'pattern did not match' in reason_lower:
        return FailedReason.TEAMS_NOT_PARSED.value

    # Return as-is if not recognized (will be stored as verbose string)
    return reason


# =============================================================================
# DETECTION HELPERS (moved from filter_reasons.py)
# =============================================================================

def is_boxing_mma(stream_name: str) -> bool:
    """Detect if stream is likely boxing/MMA based on card terminology."""
    import re
    if not stream_name:
        return False
    pattern = re.compile(
        r'\b(main\s*card|under\s*card|prelims|preliminary\s*card|early\s*prelims)\b',
        re.IGNORECASE
    )
    return bool(pattern.search(stream_name))


def is_beach_soccer(team1: str, team2: str) -> bool:
    """Detect if teams are likely beach soccer based on BS/BSC suffix."""
    import re
    if not team1 and not team2:
        return False
    pattern = re.compile(r'\bBS\s*\)?$', re.IGNORECASE)
    for team in [team1, team2]:
        if team and pattern.search(team.strip()):
            return True
    return False


def is_futsal(team1: str, team2: str) -> bool:
    """Detect if teams are likely futsal based on FP suffix."""
    import re
    if not team1 and not team2:
        return False
    pattern = re.compile(r'(\bFP\s*$|^FP\s+)', re.IGNORECASE)
    for team in [team1, team2]:
        if team and pattern.search(team.strip()):
            return True
    return False
