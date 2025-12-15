"""Stream-to-event matching with Events→Streams approach.

The V2 matching approach:
1. Fetch events for a league/date
2. Generate search patterns from each event's team names
3. For each stream, use fuzzy matching to find event patterns in stream text

This is the inverse of V1 (which extracted teams from streams then searched events).
Benefits: More tolerant of stream name variations, better fuzzy matching.
"""

from dataclasses import dataclass
from datetime import date

from core import Event
from services import SportsDataService
from utilities.fuzzy_match import FuzzyMatcher, get_matcher


@dataclass
class StreamMatchResult:
    """Result of a stream-to-event match."""

    stream_id: str
    stream_name: str
    event: Event | None
    league: str
    matched: bool
    match_score: float = 0.0
    exception_keyword: str | None = None

    @property
    def is_exception(self) -> bool:
        """True if stream was excluded by exception keyword."""
        return self.exception_keyword is not None


@dataclass
class BatchMatchResult:
    """Result of matching a batch of streams."""

    results: list[StreamMatchResult]
    events_found: int
    streams_matched: int
    streams_total: int

    @property
    def match_rate(self) -> float:
        """Percentage of streams that matched."""
        if self.streams_total == 0:
            return 0.0
        return self.streams_matched / self.streams_total * 100


class SingleLeagueMatcher:
    """Matches streams to events for a single known league.

    Uses Events→Streams approach:
    - Fetches all events for the league/date
    - Generates search patterns from event team names (via FuzzyMatcher)
    - Scans each stream name for those patterns

    Example:
        matcher = SingleLeagueMatcher(service, "nfl")
        result = matcher.match("Lions vs Bears", date.today())
        if result.matched:
            print(f"Matched to: {result.event.name}")
    """

    def __init__(
        self,
        service: SportsDataService,
        league: str,
        exception_keywords: list[str] | None = None,
        fuzzy_matcher: FuzzyMatcher | None = None,
    ):
        """Initialize matcher.

        Args:
            service: Sports data service
            league: League code (e.g., "nfl", "nba", "eng.1")
            exception_keywords: Keywords that exclude a stream from matching
            fuzzy_matcher: Custom fuzzy matcher (uses default if None)
        """
        self._service = service
        self._league = league
        self._exception_keywords = [kw.lower() for kw in (exception_keywords or [])]
        self._fuzzy = fuzzy_matcher or get_matcher()

        # Built during match - cached for efficiency
        self._events: list[Event] = []
        self._event_patterns: list[tuple[Event, list[str], list[str], list[str]]] = []
        self._cache_date: date | None = None

    def match(
        self,
        stream_name: str,
        target_date: date,
        stream_id: str = "",
    ) -> StreamMatchResult:
        """Match a stream name to an event in the configured league.

        Args:
            stream_name: The stream title to match
            target_date: Date to fetch events for
            stream_id: Optional stream identifier

        Returns:
            StreamMatchResult with match status and event if found
        """
        stream_lower = stream_name.lower()

        # Check for exception keyword first
        for keyword in self._exception_keywords:
            if keyword in stream_lower:
                return StreamMatchResult(
                    stream_id=stream_id,
                    stream_name=stream_name,
                    event=None,
                    league=self._league,
                    matched=False,
                    exception_keyword=keyword,
                )

        # Build/refresh patterns from events
        self._build_patterns(target_date)

        # Find matching event
        event, score = self._find_matching_event(stream_lower)

        return StreamMatchResult(
            stream_id=stream_id,
            stream_name=stream_name,
            event=event,
            league=self._league,
            matched=event is not None,
            match_score=score,
        )

    def match_batch(
        self,
        streams: list[tuple[str, str]],  # (stream_id, stream_name)
        target_date: date,
    ) -> BatchMatchResult:
        """Match multiple streams efficiently.

        Builds patterns once, then matches all streams.

        Args:
            streams: List of (stream_id, stream_name) tuples
            target_date: Date to fetch events for

        Returns:
            BatchMatchResult with all results and stats
        """
        # Pre-build patterns
        self._build_patterns(target_date)

        results = []
        matched_count = 0

        for stream_id, stream_name in streams:
            result = self.match(stream_name, target_date, stream_id)
            results.append(result)
            if result.matched:
                matched_count += 1

        return BatchMatchResult(
            results=results,
            events_found=len(self._events),
            streams_matched=matched_count,
            streams_total=len(streams),
        )

    def _build_patterns(self, target_date: date) -> None:
        """Build search patterns from events using fuzzy matcher.

        Caches patterns by date to avoid refetching.
        """
        if self._cache_date == target_date:
            return  # Already built for this date

        # Fetch events from service
        self._events = self._service.get_events(self._league, target_date)
        self._event_patterns = []

        for event in self._events:
            if not event.home_team or not event.away_team:
                continue

            # Generate patterns for each team (includes mascot stripping)
            home_patterns = self._fuzzy.generate_team_patterns(event.home_team)
            away_patterns = self._fuzzy.generate_team_patterns(event.away_team)

            # Also use event name patterns (for UFC, boxing, etc.)
            event_patterns = self._unique_patterns([event.name, event.short_name])

            self._event_patterns.append(
                (event, home_patterns, away_patterns, event_patterns)
            )

        self._cache_date = target_date

    def _unique_patterns(self, values: list[str | None]) -> list[str]:
        """Normalize and dedupe patterns.

        Also generates truncated patterns for names with ":"
        (e.g., "UFC Fight Night: Royval vs. Kape" -> also adds "UFC Fight Night")
        """
        seen = set()
        result = []

        for v in values:
            if not v:
                continue
            lower = v.lower()
            if lower not in seen and len(lower) >= 2:
                seen.add(lower)
                result.append(lower)

            # For names with ":", also add the prefix as a pattern
            # Handles "UFC Fight Night: Royval vs. Kape" -> "ufc fight night"
            if ":" in lower:
                prefix = lower.split(":")[0].strip()
                if prefix not in seen and len(prefix) >= 2:
                    seen.add(prefix)
                    result.append(prefix)

        return result

    def _find_matching_event(self, stream_lower: str) -> tuple[Event | None, float]:
        """Find event that matches the stream name using fuzzy matching.

        Strategy:
        1. Try team-based matching first (BOTH teams must match)
        2. Fallback to event name matching (for UFC, boxing, etc.)

        Returns:
            Tuple of (matched_event, score) or (None, 0)
        """
        best_event = None
        best_score = 0.0

        # Strategy 1: Team-based matching (need BOTH teams)
        for event, home_patterns, away_patterns, _ in self._event_patterns:
            home_match = self._fuzzy.matches_any(home_patterns, stream_lower)
            away_match = self._fuzzy.matches_any(away_patterns, stream_lower)

            if home_match.matched and away_match.matched:
                # Both teams found - this is a strong match
                combined_score = (home_match.score + away_match.score) / 2
                if combined_score > best_score:
                    best_score = combined_score
                    best_event = event

        if best_event:
            return best_event, best_score

        # Strategy 2: Event name matching (for UFC, boxing, etc.)
        for event, _, _, event_patterns in self._event_patterns:
            event_match = self._fuzzy.matches_any(event_patterns, stream_lower)
            if event_match.matched and event_match.score > best_score:
                best_score = event_match.score
                best_event = event

        return best_event, best_score

    def get_events(self, target_date: date) -> list[Event]:
        """Get the cached events (builds if needed).

        Useful for inspection or debugging.
        """
        self._build_patterns(target_date)
        return self._events.copy()

    def clear_cache(self) -> None:
        """Clear the pattern cache."""
        self._events = []
        self._event_patterns = []
        self._cache_date = None


# Single-event leagues: leagues where there's typically only ONE event per day
# For these, we can match on league keywords alone if only one event exists
SINGLE_EVENT_LEAGUES = {
    "ufc": ["ufc", "fight night", "mma"],
}


class MultiLeagueMatcher:
    """Matches streams to events across multiple leagues.

    Uses include/exclude lists to narrow down which leagues to search.

    Example:
        matcher = MultiLeagueMatcher(
            service,
            search_leagues=["nfl", "nba", "nhl"],
            include_leagues=["nfl"],  # Only return NFL matches
        )
        result = matcher.match_all(streams, date.today())
    """

    def __init__(
        self,
        service: SportsDataService,
        search_leagues: list[str],
        include_leagues: list[str] | None = None,
        exception_keywords: list[str] | None = None,
        fuzzy_matcher: FuzzyMatcher | None = None,
    ):
        """Initialize multi-league matcher.

        Args:
            service: Sports data service
            search_leagues: Leagues to search for events
            include_leagues: If set, only return matches for these leagues
            exception_keywords: Keywords that exclude a stream from matching
            fuzzy_matcher: Custom fuzzy matcher (uses default if None)
        """
        self._service = service
        self._search_leagues = search_leagues
        self._include_leagues = set(include_leagues) if include_leagues else None
        self._exception_keywords = [kw.lower() for kw in (exception_keywords or [])]
        self._fuzzy = fuzzy_matcher or get_matcher()

        # Per-league matchers (created on demand)
        self._matchers: dict[str, SingleLeagueMatcher] = {}

    def match_all(
        self,
        streams: list[tuple[str, str]],  # (stream_id, stream_name)
        target_date: date,
    ) -> BatchMatchResult:
        """Match all streams against all configured leagues.

        Args:
            streams: List of (stream_id, stream_name) tuples
            target_date: Date to fetch events for

        Returns:
            BatchMatchResult with all results
        """
        results: list[StreamMatchResult] = []
        matched_count = 0
        total_events = 0

        # Build all league matchers and collect events
        for league in self._search_leagues:
            if league not in self._matchers:
                self._matchers[league] = SingleLeagueMatcher(
                    self._service,
                    league,
                    exception_keywords=self._exception_keywords,
                    fuzzy_matcher=self._fuzzy,
                )
            # Pre-build patterns
            matcher = self._matchers[league]
            matcher._build_patterns(target_date)
            total_events += len(matcher._events)

        # Match each stream
        for stream_id, stream_name in streams:
            result = self._match_stream(stream_id, stream_name, target_date)
            results.append(result)
            if result.matched:
                matched_count += 1

        return BatchMatchResult(
            results=results,
            events_found=total_events,
            streams_matched=matched_count,
            streams_total=len(streams),
        )

    def _match_stream(
        self,
        stream_id: str,
        stream_name: str,
        target_date: date,
    ) -> StreamMatchResult:
        """Match a single stream against all leagues."""
        stream_lower = stream_name.lower()

        # Check for exception keyword
        for keyword in self._exception_keywords:
            if keyword in stream_lower:
                return StreamMatchResult(
                    stream_id=stream_id,
                    stream_name=stream_name,
                    event=None,
                    league="",
                    matched=False,
                    exception_keyword=keyword,
                )

        # Check for single-event league keywords first
        for league, keywords in SINGLE_EVENT_LEAGUES.items():
            if league not in self._search_leagues:
                continue

            # Check if stream contains any of the league keywords
            if any(kw in stream_lower for kw in keywords):
                matcher = self._matchers.get(league)
                if matcher and len(matcher._events) == 1:
                    # Single event in this league - auto-match
                    event = matcher._events[0]
                    if self._should_include(league):
                        return StreamMatchResult(
                            stream_id=stream_id,
                            stream_name=stream_name,
                            event=event,
                            league=league,
                            matched=True,
                            match_score=80.0,  # Lower confidence for keyword-only match
                        )

        # Try each league's matcher
        best_result: StreamMatchResult | None = None
        best_score = 0.0

        for league in self._search_leagues:
            if not self._should_include(league):
                continue

            matcher = self._matchers[league]
            result = matcher.match(stream_name, target_date, stream_id)

            if result.matched and result.match_score > best_score:
                best_score = result.match_score
                best_result = result

        if best_result:
            return best_result

        # No match found
        return StreamMatchResult(
            stream_id=stream_id,
            stream_name=stream_name,
            event=None,
            league="",
            matched=False,
        )

    def _should_include(self, league: str) -> bool:
        """Check if matches for this league should be included."""
        if self._include_leagues is None:
            return True
        return league in self._include_leagues

    def clear_cache(self) -> None:
        """Clear all matcher caches."""
        for matcher in self._matchers.values():
            matcher.clear_cache()
        self._matchers.clear()
