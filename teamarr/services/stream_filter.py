"""Stream filtering service for event EPG groups.

Provides regex-based stream filtering and team name extraction.
Uses the 'regex' module if available for advanced patterns,
otherwise falls back to standard 're' module.
"""

import logging
import re

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from re import Pattern

# Try to import 'regex' module which supports advanced features
try:
    import regex

    REGEX_MODULE = regex
    SUPPORTS_VARIABLE_LOOKBEHIND = True
except ImportError:
    REGEX_MODULE = re
    SUPPORTS_VARIABLE_LOOKBEHIND = False


# Builtin patterns for identifying EVENT streams (inclusion approach)
# Streams that look like actual sports events (team vs team, with date/time)
# This is more robust than exclusion - only match streams that look like events
BUILTIN_EVENT_PATTERNS = [
    # Team vs Team separators
    r"\s+(?:vs\.?|versus)\s+",  # "Team A vs Team B" or "Team A versus Team B"
    r"\s+@\s+",  # "Team A @ Team B"
    r"\s+at\s+",  # "Team A at Team B" (word boundary)
    r"\s+v\s+",  # "Team A v Team B"
    # Date patterns commonly used in event streams
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b",
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",  # MM/DD or MM/DD/YYYY
    r"\b\d{4}-\d{2}-\d{2}\b",  # ISO date
    # Time patterns
    r"\d{1,2}:\d{2}\s*(?:AM|PM|ET|PT|CT|MT)\b",
    # Event-style patterns (non-team-vs-team sports)
    r"\bWeek\s+\d+\b",  # "Week 16" (NFL, etc.) - may not match event but passes filter
    r"\bDay\s+\d+\b",  # "Day 5" (Tennis Grand Slams, etc.)
    r"\bRound\s+\d+\b",  # "Round 3" (Golf, Tennis, etc.)
    r"\bRace\s+\d+\b",  # "Race 1" (Motorsports)
    r"\bGame\s+\d+\b",  # "Game 7" (Playoffs)
    r"\bMatch\s+\d+\b",  # "Match 1"
    r"\bUFC\s+\d+\b",  # "UFC 310"
    r"\bUFC\s+(?:Fight\s*Night|FN)\b",  # "UFC Fight Night" without number
    r"\bPPV\b",  # Pay-per-view events
    r"\bRedZone\b",  # NFL RedZone - passes filter, may not match event
    # Combat sports patterns
    r"\b(?:UFC|Boxing|MMA)\s*:",  # "UFC: Main Card", "Boxing: Crawford vs Spence"
    r"\b(?:Main\s*Card|Prelims|Early\s*Prelims|Undercard)\b",  # Card segments
    r"\bContender\s*Series\b",  # Dana White's Contender Series
    r"\bPBC\b",  # Premier Boxing Champions
    r"\bTop\s*Rank\b",  # Top Rank Boxing
    r"\bMatchroom\b",  # Matchroom Boxing
    r"\bBellator\b",  # Bellator MMA
    r"\bONE\s*(?:Championship|FC)\b",  # ONE Championship
]

# Compiled regex for event stream detection
_BUILTIN_EVENT_REGEX: Pattern | None = None


def get_builtin_event_pattern() -> Pattern:
    """Get compiled regex pattern for detecting event-like streams."""
    global _BUILTIN_EVENT_REGEX
    if _BUILTIN_EVENT_REGEX is None:
        combined = "|".join(f"({p})" for p in BUILTIN_EVENT_PATTERNS)
        _BUILTIN_EVENT_REGEX = REGEX_MODULE.compile(combined, REGEX_MODULE.IGNORECASE)
    return _BUILTIN_EVENT_REGEX


def is_event_stream(stream_name: str) -> bool:
    """Check if a stream name looks like a sports event.

    Uses inclusion patterns - returns True if the stream contains
    team separators (vs, @, at) or date/time patterns.

    Args:
        stream_name: The stream name to check

    Returns:
        True if stream looks like an event, False otherwise
    """
    if not stream_name or not stream_name.strip():
        return False
    pattern = get_builtin_event_pattern()
    return bool(pattern.search(stream_name))


@dataclass
class StreamFilterConfig:
    """Configuration for stream filtering."""

    include_regex: str | None = None
    include_enabled: bool = False
    exclude_regex: str | None = None
    exclude_enabled: bool = False
    custom_teams_regex: str | None = None
    custom_teams_enabled: bool = False
    skip_builtin: bool = False
    # Inclusion filter: only process streams that look like events (vs, @, date/time)
    # Disabled by default - rely on matching to filter non-events
    require_event_pattern: bool = False


@dataclass
class FilterResult:
    """Result of stream filtering."""

    # Streams that passed all filters
    passed: list[dict] = field(default_factory=list)

    # Filtering stats
    total_input: int = 0
    filtered_include: int = 0  # Didn't match include pattern
    filtered_exclude: int = 0  # Matched exclude pattern
    filtered_not_event: int = 0  # Didn't look like an event stream
    filtered_stale: int = 0  # Marked as stale in Dispatcharr
    passed_count: int = 0


@dataclass
class TeamExtractionResult:
    """Result of team name extraction from a stream name."""

    success: bool = False
    team1: str | None = None
    team2: str | None = None
    method: str = ""  # 'custom', 'builtin', 'none'


def compile_pattern(pattern: str | None, ignore_case: bool = True) -> Pattern | None:
    """Compile a regex pattern with error handling.

    Args:
        pattern: The regex pattern string to compile
        ignore_case: Whether to use case-insensitive matching

    Returns:
        Compiled regex pattern or None on error/empty input
    """
    if not pattern or not pattern.strip():
        return None

    flags = REGEX_MODULE.IGNORECASE if ignore_case else 0

    try:
        return REGEX_MODULE.compile(pattern.strip(), flags)
    except Exception as e:
        logger.debug("[FILTER] Failed to compile regex pattern '%s': %s", pattern, e)
        return None


def validate_pattern(pattern: str | None) -> tuple[bool, str | None]:
    """Validate a regex pattern without compiling for reuse.

    Args:
        pattern: The regex pattern string to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not pattern or not pattern.strip():
        return (True, None)

    try:
        REGEX_MODULE.compile(pattern.strip())
        return (True, None)
    except Exception as e:
        return (False, str(e))


class StreamFilter:
    """Filters streams based on regex patterns."""

    def __init__(self, config: StreamFilterConfig):
        """Initialize stream filter.

        Args:
            config: Filter configuration
        """
        self.config = config
        self._include_pattern: Pattern | None = None
        self._exclude_pattern: Pattern | None = None
        self._teams_pattern: Pattern | None = None
        self._event_pattern: Pattern | None = None

        # Compile patterns
        if config.include_enabled and config.include_regex:
            self._include_pattern = compile_pattern(config.include_regex)

        if config.exclude_enabled and config.exclude_regex:
            self._exclude_pattern = compile_pattern(config.exclude_regex)

        if config.custom_teams_enabled and config.custom_teams_regex:
            self._teams_pattern = compile_pattern(config.custom_teams_regex)

        # Event pattern for inclusion filtering (if enabled)
        if config.require_event_pattern:
            self._event_pattern = get_builtin_event_pattern()

    def filter(self, streams: list[dict]) -> FilterResult:
        """Apply filters and return filtered streams with stats.

        Filter order:
        1. Stale filter - skip streams marked as stale in Dispatcharr
        2. Event pattern check (if enabled) - stream must look like an event
        3. Include filter (if enabled)
        4. Exclude filter (if enabled)

        Args:
            streams: List of stream dicts with at least 'id' and 'name' keys

        Returns:
            FilterResult with passed streams and stats
        """
        result = FilterResult(total_input=len(streams))

        for stream in streams:
            name = stream.get("name", "")

            # Stale filter: skip streams marked as stale in Dispatcharr
            if stream.get("is_stale", False):
                logger.debug(
                    "[FILTER] Skipping stale stream: %s (id=%s)",
                    name[:50],
                    stream.get("id"),
                )
                result.filtered_stale += 1
                continue

            # Event pattern filter: stream must look like an event (vs, @, date/time)
            if self._event_pattern:
                if not self._event_pattern.search(name):
                    result.filtered_not_event += 1
                    continue

            # Include filter: stream must match
            if self._include_pattern:
                if not self._include_pattern.search(name):
                    result.filtered_include += 1
                    continue

            # Exclude filter: stream must NOT match
            if self._exclude_pattern:
                if self._exclude_pattern.search(name):
                    result.filtered_exclude += 1
                    continue

            # Stream passed all filters
            result.passed.append(stream)

        result.passed_count = len(result.passed)
        return result

    def extract_teams(self, stream_name: str) -> TeamExtractionResult:
        """Extract team names from a stream name.

        Uses custom regex if configured, otherwise falls back to
        builtin patterns (unless skip_builtin is set).

        Args:
            stream_name: The stream name to parse

        Returns:
            TeamExtractionResult with extracted team names
        """
        # Try custom pattern first
        if self._teams_pattern:
            match = self._teams_pattern.search(stream_name)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    return TeamExtractionResult(
                        success=True,
                        team1=groups[0],
                        team2=groups[1],
                        method="custom",
                    )
                # Try named groups
                try:
                    team1 = match.group("team1")
                    team2 = match.group("team2")
                    return TeamExtractionResult(
                        success=True, team1=team1, team2=team2, method="custom"
                    )
                except (IndexError, KeyError):
                    pass

        # Skip builtin if configured
        if self.config.skip_builtin:
            return TeamExtractionResult(success=False, method="none")

        # Builtin patterns for common formats
        return self._extract_teams_builtin(stream_name)

    def _extract_teams_builtin(self, stream_name: str) -> TeamExtractionResult:
        """Extract teams using builtin patterns.

        Supports common formats:
        - "Team A vs Team B"
        - "Team A @ Team B"
        - "Team A v Team B"
        - "Team A - Team B"
        """
        # Common separators: vs, v, @, at, -
        patterns = [
            r"(.+?)\s+(?:vs\.?|versus)\s+(.+?)(?:\s*[\|\-\[]|$)",
            r"(.+?)\s+@\s+(.+?)(?:\s*[\|\-\[]|$)",
            r"(.+?)\s+(?:at)\s+(.+?)(?:\s*[\|\-\[]|$)",
            r"(.+?)\s+v\s+(.+?)(?:\s*[\|\-\[]|$)",
            r"(.+?)\s+-\s+(.+?)(?:\s*[\|\-\[]|$)",
        ]

        for pattern in patterns:
            match = REGEX_MODULE.search(pattern, stream_name, REGEX_MODULE.IGNORECASE)
            if match:
                team1 = match.group(1).strip()
                team2 = match.group(2).strip()
                if team1 and team2:
                    return TeamExtractionResult(
                        success=True, team1=team1, team2=team2, method="builtin"
                    )

        return TeamExtractionResult(success=False, method="none")


def create_filter_from_group(group) -> StreamFilter:
    """Create a StreamFilter from an EventEPGGroup.

    Args:
        group: EventEPGGroup dataclass or dict with filter config

    Returns:
        Configured StreamFilter instance
    """
    if hasattr(group, "stream_include_regex"):
        # Dataclass
        config = StreamFilterConfig(
            include_regex=group.stream_include_regex,
            include_enabled=group.stream_include_regex_enabled,
            exclude_regex=group.stream_exclude_regex,
            exclude_enabled=group.stream_exclude_regex_enabled,
            custom_teams_regex=group.custom_regex_teams,
            custom_teams_enabled=group.custom_regex_teams_enabled,
            skip_builtin=group.skip_builtin_filter,
        )
    else:
        # Dict
        config = StreamFilterConfig(
            include_regex=group.get("stream_include_regex"),
            include_enabled=bool(group.get("stream_include_regex_enabled")),
            exclude_regex=group.get("stream_exclude_regex"),
            exclude_enabled=bool(group.get("stream_exclude_regex_enabled")),
            custom_teams_regex=group.get("custom_regex_teams"),
            custom_teams_enabled=bool(group.get("custom_regex_teams_enabled")),
            skip_builtin=bool(group.get("skip_builtin_filter")),
        )

    return StreamFilter(config)
