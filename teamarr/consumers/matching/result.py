"""Match Result System for stream-to-event matching.

Clean result hierarchy with three discrete categories:
- FILTERED: Pre-match, didn't attempt to match (stream excluded by configuration)
- FAILED: Matching attempted but couldn't find event
- MATCHED: Successfully matched to an event
- EXCLUDED: Matched successfully but won't create channel/EPG

Each category has reason subtypes for detailed breakdown.

Refactored Jan 2026 for clean separation:
- FILTERED = pre-match filtering only (regex, not_event)
- FAILED = matching phase failures (no teams, no events)
- EXCLUDED = post-match exclusions (lifecycle timing, league mismatch)
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from teamarr.core.types import Event

# =============================================================================
# RESULT CATEGORIES
# =============================================================================


class ResultCategory(Enum):
    """Top-level result category for stream matching."""

    FILTERED = "filtered"  # Pre-match: didn't attempt to match
    FAILED = "failed"  # Matching attempted but failed
    MATCHED = "matched"  # Successfully matched to event
    EXCLUDED = "excluded"  # Matched but excluded (lifecycle/config)


# =============================================================================
# FILTERED REASONS - Pre-match filtering (didn't attempt to match)
# =============================================================================


class FilteredReason(Enum):
    """Reasons for filtering BEFORE matching is attempted.

    These are expected exclusions based on stream characteristics or
    user regex configuration. The stream never entered the matching phase.
    """

    # Stream doesn't look like an event (no vs/@/at, dates, or event patterns)
    NOT_EVENT = "not_event"

    # User regex configuration
    INCLUDE_REGEX = "include_regex"  # Didn't match user's inclusion pattern
    EXCLUDE_REGEX = "exclude_regex"  # Matched user's exclusion pattern

    # Stream's detected league not in group's search leagues (pre-match)
    LEAGUE_NOT_INCLUDED = "league_not_included"

    # Stream marked as stale in Dispatcharr (no longer in M3U source)
    STALE = "stale"

    # Stream's detected sport is not supported (swimming, diving, gymnastics, etc.)
    SPORT_NOT_SUPPORTED = "sport_not_supported"


# =============================================================================
# FAILED REASONS - Matching attempted but couldn't complete
# =============================================================================


class FailedReason(Enum):
    """Reasons for match FAILURE - matching was attempted but couldn't complete.

    These represent genuine failures during the matching phase:
    - Data issues (teams not in provider)
    - Detection limitations (ambiguous streams)
    - Scheduling gaps (no event found)
    """

    # Team parsing failures
    TEAMS_NOT_PARSED = "teams_not_parsed"  # Couldn't extract team names from stream

    # Team lookup failures
    TEAM1_NOT_FOUND = "team1_not_found"  # First team not found in any league
    TEAM2_NOT_FOUND = "team2_not_found"  # Second team not found in any league
    BOTH_TEAMS_NOT_FOUND = "both_teams_not_found"  # Neither team found
    NO_COMMON_LEAGUE = "no_common_league"  # Teams exist but in different leagues

    # League detection failures (multi-sport groups)
    NO_LEAGUE_DETECTED = "no_league_detected"  # Teams matched but can't determine league
    AMBIGUOUS_LEAGUE = "ambiguous_league"  # Multiple possible leagues, can't decide

    # Event lookup failures
    NO_EVENT_FOUND = "no_event_found"  # Teams matched, league detected, no game scheduled

    # Event card failures (UFC, boxing)
    NO_EVENT_CARD_MATCH = "no_event_card_match"  # Could not match to event card

    # Date validation failures (stream has date that doesn't match any event)
    DATE_MISMATCH = "date_mismatch"  # Stream date != event date


# =============================================================================
# MATCH METHOD - How the match was made
# =============================================================================


class MatchMethod(Enum):
    """Method used to achieve a successful match.

    Different methods have different confidence levels.
    """

    # Cache lookups (fastest)
    CACHE = "cache"  # Hit existing algorithmic cache entry
    USER_CORRECTED = "user_corrected"  # User-corrected match (pinned)

    # Alias matching (high confidence)
    ALIAS = "alias"  # Matched via user-defined alias

    # Pattern matching (high confidence)
    PATTERN = "pattern"  # Matched via team name pattern from events

    # Fuzzy matching (varies by score)
    FUZZY = "fuzzy"  # Matched via fuzzy string matching

    # Keyword matching (for event cards)
    KEYWORD = "keyword"  # Matched via keyword (UFC, boxing)

    # Direct assignment (single-league groups)
    DIRECT = "direct"  # Group has single assigned league


# =============================================================================
# EXCLUDED REASONS - Matched but excluded by lifecycle timing
# =============================================================================


class ExcludedReason(Enum):
    """Reasons for EXCLUSION - stream matched successfully but won't create channel.

    These are NOT failures - the match was correct, but the stream/event is
    excluded due to configuration or lifecycle timing. Users can see
    "this matched correctly, just excluded because...".
    """

    # Matcher-level exclusions (during matching phase, after successful match)
    LEAGUE_NOT_INCLUDED = "league_not_included"  # Matched but league not in group's leagues[]

    # Lifecycle-level exclusions (after matching, during channel creation)
    EVENT_FINAL = "event_final"  # Event status is final
    EVENT_PAST = "event_past"  # Event already ended (past delete threshold)
    BEFORE_WINDOW = "before_window"  # Too early to create channel


# =============================================================================
# MATCH OUTCOME - Unified result object
# =============================================================================


@dataclass
class MatchOutcome:
    """Unified result object for stream matching.

    Use the factory methods to create instances:
        MatchOutcome.filtered(FilteredReason.NOT_EVENT)
        MatchOutcome.failed(FailedReason.NO_EVENT_FOUND, detail="...")
        MatchOutcome.matched(MatchMethod.FUZZY, event=event, confidence=0.85)
    """

    category: ResultCategory

    # For FILTERED results
    filtered_reason: FilteredReason | None = None

    # For FAILED results
    failed_reason: FailedReason | None = None

    # For EXCLUDED results (matched but excluded by lifecycle)
    excluded_reason: ExcludedReason | None = None

    # For MATCHED and EXCLUDED results
    match_method: MatchMethod | None = None
    event: Event | None = None
    detected_league: str | None = None
    confidence: float = 0.0  # 0.0 to 1.0, relevant for fuzzy matches
    origin_match_method: str | None = None  # For CACHE hits: original method (e.g., "fuzzy")

    # Common fields
    stream_name: str | None = None
    stream_id: int | None = None
    detail: str | None = None

    # Parsed team info (for debugging/display)
    parsed_team1: str | None = None
    parsed_team2: str | None = None

    # For EXCLUDED/LEAGUE_NOT_INCLUDED - the league that was found (for display)
    found_league: str | None = None
    found_league_name: str | None = None

    @classmethod
    def filtered(
        cls,
        reason: FilteredReason,
        *,
        stream_name: str | None = None,
        stream_id: int | None = None,
        detail: str | None = None,
    ) -> "MatchOutcome":
        """Create a FILTERED result."""
        return cls(
            category=ResultCategory.FILTERED,
            filtered_reason=reason,
            stream_name=stream_name,
            stream_id=stream_id,
            detail=detail,
        )

    @classmethod
    def failed(
        cls,
        reason: FailedReason,
        *,
        stream_name: str | None = None,
        stream_id: int | None = None,
        detail: str | None = None,
        parsed_team1: str | None = None,
        parsed_team2: str | None = None,
    ) -> "MatchOutcome":
        """Create a FAILED result."""
        return cls(
            category=ResultCategory.FAILED,
            failed_reason=reason,
            stream_name=stream_name,
            stream_id=stream_id,
            detail=detail,
            parsed_team1=parsed_team1,
            parsed_team2=parsed_team2,
        )

    @classmethod
    def matched(
        cls,
        method: MatchMethod,
        event: Event,
        *,
        detected_league: str | None = None,
        confidence: float = 1.0,
        stream_name: str | None = None,
        stream_id: int | None = None,
        parsed_team1: str | None = None,
        parsed_team2: str | None = None,
        origin_match_method: str | None = None,
    ) -> "MatchOutcome":
        """Create a MATCHED result.

        Args:
            method: How the match was made (CACHE, FUZZY, ALIAS, etc.)
            event: The matched event
            detected_league: League code
            confidence: Match confidence (0.0 to 1.0)
            stream_name: Original stream name
            stream_id: Stream ID
            parsed_team1: First parsed team name
            parsed_team2: Second parsed team name
            origin_match_method: For CACHE hits, the original method used (e.g., "fuzzy")
        """
        return cls(
            category=ResultCategory.MATCHED,
            match_method=method,
            event=event,
            detected_league=detected_league or event.league,
            confidence=confidence,
            stream_name=stream_name,
            stream_id=stream_id,
            parsed_team1=parsed_team1,
            parsed_team2=parsed_team2,
            origin_match_method=origin_match_method,
        )

    @classmethod
    def excluded(
        cls,
        reason: ExcludedReason,
        matched_outcome: "MatchOutcome",
        *,
        found_league: str | None = None,
        found_league_name: str | None = None,
    ) -> "MatchOutcome":
        """Create an EXCLUDED result from a matched outcome.

        Converts a successful match to an excluded result, preserving the
        original match information for visibility.

        Args:
            reason: Why the match was excluded (EVENT_PAST, EVENT_FINAL, etc.)
            matched_outcome: The original successful match outcome
            found_league: For LEAGUE_NOT_INCLUDED - the league code that was found
            found_league_name: For LEAGUE_NOT_INCLUDED - the league display name
        """
        return cls(
            category=ResultCategory.EXCLUDED,
            excluded_reason=reason,
            match_method=matched_outcome.match_method,
            event=matched_outcome.event,
            detected_league=matched_outcome.detected_league,
            confidence=matched_outcome.confidence,
            stream_name=matched_outcome.stream_name,
            stream_id=matched_outcome.stream_id,
            parsed_team1=matched_outcome.parsed_team1,
            parsed_team2=matched_outcome.parsed_team2,
            origin_match_method=matched_outcome.origin_match_method,
            found_league=found_league,
            found_league_name=found_league_name,
        )

    @property
    def is_filtered(self) -> bool:
        """Check if this is a FILTERED result."""
        return self.category == ResultCategory.FILTERED

    @property
    def is_failed(self) -> bool:
        """Check if this is a FAILED result."""
        return self.category == ResultCategory.FAILED

    @property
    def is_matched(self) -> bool:
        """Check if this is a MATCHED result."""
        return self.category == ResultCategory.MATCHED

    @property
    def is_excluded(self) -> bool:
        """Check if this is an EXCLUDED result (matched but excluded by lifecycle)."""
        return self.category == ResultCategory.EXCLUDED

    @property
    def reason(self) -> FilteredReason | FailedReason | ExcludedReason | None:
        """Get the reason enum (for FILTERED, FAILED, or EXCLUDED results)."""
        if self.filtered_reason:
            return self.filtered_reason
        if self.failed_reason:
            return self.failed_reason
        if self.excluded_reason:
            return self.excluded_reason
        return None

    @property
    def reason_value(self) -> str | None:
        """Get the string value of the reason."""
        reason = self.reason
        return reason.value if reason else None

    def should_record_as_failure(self) -> bool:
        """Check if this outcome should be recorded in the failed matches table.

        Only actual failures are recorded - filtered streams are expected exclusions.
        """
        return self.is_failed

    def affects_match_rate(self) -> bool:
        """Check if this outcome counts toward match rate calculation.

        Returns True for outcomes where we TRIED to match:
        - MATCHED: Successfully matched
        - FAILED: Attempted but couldn't match
        - EXCLUDED: Matched but excluded (still counts as a match attempt)

        Returns False for FILTERED streams that never entered matching.
        """
        return self.is_matched or self.is_failed or self.is_excluded


# =============================================================================
# DISPLAY TEXT - Human-readable descriptions
# =============================================================================

FILTERED_DISPLAY: dict[FilteredReason, str] = {
    FilteredReason.NOT_EVENT: "Not an event stream",
    FilteredReason.INCLUDE_REGEX: "Didn't match include regex",
    FilteredReason.EXCLUDE_REGEX: "Matched exclude regex",
    FilteredReason.LEAGUE_NOT_INCLUDED: "League not in group",
    FilteredReason.STALE: "Stale stream",
    FilteredReason.SPORT_NOT_SUPPORTED: "Sport not supported",
}

FAILED_DISPLAY: dict[FailedReason, str] = {
    FailedReason.TEAMS_NOT_PARSED: "Could not parse team names",
    FailedReason.TEAM1_NOT_FOUND: "First team not found",
    FailedReason.TEAM2_NOT_FOUND: "Second team not found",
    FailedReason.BOTH_TEAMS_NOT_FOUND: "Neither team found",
    FailedReason.NO_COMMON_LEAGUE: "Teams have no common league",
    FailedReason.NO_LEAGUE_DETECTED: "Could not detect league",
    FailedReason.AMBIGUOUS_LEAGUE: "Multiple leagues possible",
    FailedReason.NO_EVENT_FOUND: "No scheduled event found",
    FailedReason.NO_EVENT_CARD_MATCH: "No matching event card",
    FailedReason.DATE_MISMATCH: "Stream date doesn't match event",
}

METHOD_DISPLAY: dict[MatchMethod, str] = {
    MatchMethod.CACHE: "Cache hit",
    MatchMethod.USER_CORRECTED: "User corrected",
    MatchMethod.ALIAS: "Alias match",
    MatchMethod.PATTERN: "Pattern match",
    MatchMethod.FUZZY: "Fuzzy match",
    MatchMethod.KEYWORD: "Keyword match",
    MatchMethod.DIRECT: "Direct assignment",
}

EXCLUDED_DISPLAY: dict[ExcludedReason, str] = {
    ExcludedReason.LEAGUE_NOT_INCLUDED: "League not in group",
    ExcludedReason.EVENT_FINAL: "Event is final",
    ExcludedReason.EVENT_PAST: "Event already ended",
    ExcludedReason.BEFORE_WINDOW: "Before create window",
}


def get_display_text(outcome: MatchOutcome) -> str:
    """Get human-readable display text for a match result.

    Args:
        outcome: MatchOutcome object

    Returns:
        Human-readable description
    """
    if outcome.is_matched:
        method_text = METHOD_DISPLAY.get(outcome.match_method, str(outcome.match_method))
        if outcome.match_method == MatchMethod.FUZZY and outcome.confidence < 1.0:
            return f"{method_text} ({outcome.confidence:.0%})"
        return method_text

    elif outcome.is_excluded:
        reason_text = EXCLUDED_DISPLAY.get(outcome.excluded_reason, str(outcome.excluded_reason))
        method_text = METHOD_DISPLAY.get(outcome.match_method, "")
        # Add league context for LEAGUE_NOT_INCLUDED
        if (
            outcome.excluded_reason == ExcludedReason.LEAGUE_NOT_INCLUDED
            and outcome.found_league_name
        ):
            return f"Found in {outcome.found_league_name} (not in group)"
        if method_text:
            return f"{reason_text} (matched via {method_text})"
        return reason_text

    elif outcome.is_failed:
        return FAILED_DISPLAY.get(outcome.failed_reason, str(outcome.failed_reason))

    elif outcome.is_filtered:
        return FILTERED_DISPLAY.get(outcome.filtered_reason, str(outcome.filtered_reason))

    return str(outcome)


# =============================================================================
# LOGGING UTILITIES
# =============================================================================


def log_result(
    logger: logging.Logger,
    outcome: MatchOutcome,
    max_stream_len: int = 60,
) -> None:
    """Log a match result with consistent formatting.

    Format:
        [FILTERED:reason] stream_name | detail
        [FAILED:reason] stream_name | detail
        [METHOD] stream_name -> LEAGUE | event_name

    Args:
        logger: Logger instance
        outcome: MatchOutcome to log
        max_stream_len: Max length before truncating stream name
    """
    stream_name = outcome.stream_name or ""
    display_name = stream_name[:max_stream_len]
    if len(stream_name) > max_stream_len:
        display_name += "..."

    if outcome.is_matched:
        method = outcome.match_method.value if outcome.match_method else "?"
        league = (outcome.detected_league or "").upper()
        event_name = ""
        if outcome.event:
            event_name = outcome.event.short_name or outcome.event.name

        conf = " (%.0f%%)" % (outcome.confidence * 100) if outcome.confidence < 1.0 else ""
        logger.info("[%s%s] %s -> %s | %s", method.upper(), conf, display_name, league, event_name)

    elif outcome.is_excluded:
        reason = outcome.excluded_reason.value if outcome.excluded_reason else "unknown"
        method = outcome.match_method.value if outcome.match_method else "?"
        league = (outcome.detected_league or "").upper()
        event_name = ""
        if outcome.event:
            event_name = outcome.event.short_name or outcome.event.name

        logger.info(
            "[EXCLUDED:%s] %s -> %s | %s (via %s)", reason, display_name, league, event_name, method
        )

    elif outcome.is_failed:
        reason = outcome.failed_reason.value if outcome.failed_reason else "unknown"
        detail = outcome.detail or ""

        if detail:
            logger.info("[FAILED:%s] %s | %s", reason, display_name, detail)
        else:
            logger.info("[FAILED:%s] %s", reason, display_name)

    elif outcome.is_filtered:
        reason = outcome.filtered_reason.value if outcome.filtered_reason else "unknown"
        # All filtered reasons are debug-level (expected high volume, pre-match)
        logger.debug("[FILTERED:%s] %s", reason, display_name)


def format_result_summary(
    filtered_count: int = 0,
    failed_count: int = 0,
    matched_count: int = 0,
    excluded_count: int = 0,
    by_filtered_reason: dict[FilteredReason, int] | None = None,
    by_failed_reason: dict[FailedReason, int] | None = None,
    by_excluded_reason: dict[ExcludedReason, int] | None = None,
    by_method: dict[MatchMethod, int] | None = None,
) -> str:
    """Format a summary of match results for logging.

    Returns:
        Multi-line summary string
    """
    lines = []
    total = filtered_count + failed_count + matched_count + excluded_count
    rate = f"{matched_count / total:.0%}" if total > 0 else "N/A"

    lines.append(
        f"Match Results: {matched_count} matched, {excluded_count} excluded, "
        f"{failed_count} failed, {filtered_count} filtered (rate: {rate})"
    )

    if by_method:
        method_parts = [f"{m.value}:{c}" for m, c in sorted(by_method.items(), key=lambda x: -x[1])]
        lines.append(f"  By method: {', '.join(method_parts)}")

    if by_excluded_reason:
        excl_parts = [f"{r.value}:{c}" for r, c in by_excluded_reason.items()]
        lines.append(f"  Excluded: {', '.join(excl_parts)}")

    if by_failed_reason:
        fail_parts = [f"{r.value}:{c}" for r, c in by_failed_reason.items()]
        lines.append(f"  Failed: {', '.join(fail_parts)}")

    if by_filtered_reason:
        filt_parts = [f"{r.value}:{c}" for r, c in by_filtered_reason.items()]
        lines.append(f"  Filtered: {', '.join(filt_parts)}")

    return "\n".join(lines)


# =============================================================================
# RESULT AGGREGATOR
# =============================================================================


@dataclass
class ResultAggregator:
    """Aggregates match results for statistics.

    Usage:
        agg = ResultAggregator()
        for outcome in outcomes:
            agg.add(outcome)
        logger.info(agg.summary())
    """

    matched: int = 0
    excluded: int = 0
    failed: int = 0
    filtered: int = 0

    by_method: dict[MatchMethod, int] = field(default_factory=dict)
    by_excluded_reason: dict[ExcludedReason, int] = field(default_factory=dict)
    by_failed_reason: dict[FailedReason, int] = field(default_factory=dict)
    by_filtered_reason: dict[FilteredReason, int] = field(default_factory=dict)

    # For match rate calculation (excludes pre-filtered streams)
    eligible: int = 0

    def add(self, outcome: MatchOutcome) -> None:
        """Add an outcome to the aggregation."""
        if outcome.is_matched:
            self.matched += 1
            if outcome.match_method:
                self.by_method[outcome.match_method] = (
                    self.by_method.get(outcome.match_method, 0) + 1
                )
        elif outcome.is_excluded:
            self.excluded += 1
            if outcome.excluded_reason:
                self.by_excluded_reason[outcome.excluded_reason] = (
                    self.by_excluded_reason.get(outcome.excluded_reason, 0) + 1
                )
            # Also track by method for excluded streams (they matched successfully)
            if outcome.match_method:
                self.by_method[outcome.match_method] = (
                    self.by_method.get(outcome.match_method, 0) + 1
                )
        elif outcome.is_failed:
            self.failed += 1
            if outcome.failed_reason:
                self.by_failed_reason[outcome.failed_reason] = (
                    self.by_failed_reason.get(outcome.failed_reason, 0) + 1
                )
        elif outcome.is_filtered:
            self.filtered += 1
            if outcome.filtered_reason:
                self.by_filtered_reason[outcome.filtered_reason] = (
                    self.by_filtered_reason.get(outcome.filtered_reason, 0) + 1
                )

        if outcome.affects_match_rate():
            self.eligible += 1

    @property
    def total(self) -> int:
        """Total outcomes processed."""
        return self.matched + self.excluded + self.failed + self.filtered

    @property
    def match_rate(self) -> float:
        """Match rate as a fraction (0.0 to 1.0)."""
        if self.eligible == 0:
            return 0.0
        return self.matched / self.eligible

    def summary(self) -> str:
        """Get formatted summary string."""
        return format_result_summary(
            filtered_count=self.filtered,
            failed_count=self.failed,
            matched_count=self.matched,
            excluded_count=self.excluded,
            by_filtered_reason=self.by_filtered_reason if self.by_filtered_reason else None,
            by_failed_reason=self.by_failed_reason if self.by_failed_reason else None,
            by_excluded_reason=self.by_excluded_reason if self.by_excluded_reason else None,
            by_method=self.by_method if self.by_method else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "matched": self.matched,
            "excluded": self.excluded,
            "failed": self.failed,
            "filtered": self.filtered,
            "total": self.total,
            "eligible": self.eligible,
            "match_rate": self.match_rate,
            "by_method": {m.value: c for m, c in self.by_method.items()},
            "by_excluded_reason": {r.value: c for r, c in self.by_excluded_reason.items()},
            "by_failed_reason": {r.value: c for r, c in self.by_failed_reason.items()},
            "by_filtered_reason": {r.value: c for r, c in self.by_filtered_reason.items()},
        }
