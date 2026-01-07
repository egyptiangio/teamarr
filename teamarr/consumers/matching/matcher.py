"""Unified stream matcher - the main entry point for stream matching.

Replaces CachedMatcher and MultiLeagueMatcher with a cleaner architecture:
1. Classify streams (placeholder, team_vs_team, event_card)
2. Route to appropriate matcher
3. Track results with MatchOutcome system
4. Handle caching with method tracking

Usage:
    from teamarr.consumers.matching import StreamMatcher

    matcher = StreamMatcher(
        service=sports_data_service,
        db_factory=get_db,
        group_id=1,
        search_leagues=["nfl", "nba"],
    )

    result = matcher.match_all(streams, target_date)
    matcher.purge_stale()
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from teamarr.config import get_user_timezone
from teamarr.consumers.matching.classifier import (
    ClassifiedStream,
    CustomRegexConfig,
    StreamCategory,
    classify_stream,
)
from teamarr.consumers.matching.event_matcher import EventCardMatcher
from teamarr.consumers.matching.result import (
    FilteredReason,
    MatchMethod,
    MatchOutcome,
    ResultAggregator,
)
from teamarr.consumers.matching.team_matcher import TeamMatcher
from teamarr.consumers.stream_match_cache import (
    StreamMatchCache,
    get_generation_counter,
    increment_generation_counter,
)
from teamarr.core import Event
from teamarr.database.leagues import get_league
from teamarr.services import SportsDataService

logger = logging.getLogger(__name__)


@dataclass
class MatchedStreamResult:
    """Result of matching a single stream."""

    stream_name: str
    stream_id: int

    # Match outcome
    matched: bool
    event: Event | None = None
    league: str | None = None

    # Inclusion decision
    included: bool = False
    exclusion_reason: str | None = None

    # Method tracking
    match_method: MatchMethod | None = None
    confidence: float = 0.0
    from_cache: bool = False
    origin_match_method: str | None = None  # For cache hits: original method (fuzzy, alias, etc.)

    # Classification info
    category: StreamCategory | None = None
    parsed_team1: str | None = None
    parsed_team2: str | None = None
    detected_league: str | None = None

    # Exception handling
    exception_keyword: str | None = None

    @property
    def is_exception(self) -> bool:
        return self.exception_keyword is not None


@dataclass
class BatchMatchResult:
    """Result of matching a batch of streams."""

    results: list[MatchedStreamResult] = field(default_factory=list)
    target_date: date | None = None
    leagues_searched: list[str] = field(default_factory=list)
    include_leagues: list[str] = field(default_factory=list)

    # Cache stats
    cache_hits: int = 0
    cache_misses: int = 0

    # Aggregated stats
    aggregator: ResultAggregator = field(default_factory=ResultAggregator)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def matched_count(self) -> int:
        """Count of streams that matched to an event (includes excluded)."""
        return sum(1 for r in self.results if r.matched)

    @property
    def included_count(self) -> int:
        """Count of streams that will be included in output (matched AND not excluded)."""
        return sum(1 for r in self.results if r.included)

    @property
    def unmatched_count(self) -> int:
        """Count of streams that failed to match."""
        return sum(1 for r in self.results if not r.matched and not r.is_exception)

    @property
    def excluded_count(self) -> int:
        """Count of streams that matched but were excluded."""
        return sum(1 for r in self.results if r.matched and not r.included)

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0


class StreamMatcher:
    """Unified stream matcher with classification and caching.

    Matches streams to events using:
    1. Classification: placeholder, team_vs_team, event_card
    2. Routing: TeamMatcher for team sports, EventCardMatcher for UFC/boxing
    3. Caching: fingerprint cache with method tracking
    """

    def __init__(
        self,
        service: SportsDataService,
        db_factory,
        group_id: int,
        search_leagues: list[str],
        include_leagues: list[str] | None = None,
        include_final_events: bool = False,
        sport_durations: dict[str, float] | None = None,
        user_tz: ZoneInfo | None = None,
        generation: int | None = None,
        custom_regex_teams: str | None = None,
        custom_regex_teams_enabled: bool = False,
    ):
        """Initialize the matcher.

        Args:
            service: Sports data service
            db_factory: Database connection factory
            group_id: Event group ID for cache fingerprints
            search_leagues: Leagues to search for events
            include_leagues: Whitelist of leagues to include (None = all search_leagues)
            include_final_events: Include completed events
            sport_durations: Sport duration settings
            user_tz: User timezone for date calculations
            generation: Cache generation counter (if None, will be fetched/incremented)
            custom_regex_teams: Custom regex pattern for extracting team names
            custom_regex_teams_enabled: Whether custom regex is enabled
        """
        self._service = service
        self._db_factory = db_factory
        self._group_id = group_id
        self._search_leagues = search_leagues
        self._include_leagues = set(include_leagues or search_leagues)
        self._include_final_events = include_final_events
        self._sport_durations = sport_durations or {}
        self._user_tz = user_tz or get_user_timezone()

        # Custom regex configuration
        self._custom_regex = CustomRegexConfig(
            teams_pattern=custom_regex_teams,
            teams_enabled=custom_regex_teams_enabled,
        ) if custom_regex_teams_enabled and custom_regex_teams else None

        # Initialize cache
        self._cache = StreamMatchCache(db_factory)
        # Use provided generation or fetch current
        self._generation = generation or get_generation_counter(db_factory)
        self._generation_provided = generation is not None

        # Initialize sub-matchers
        self._team_matcher = TeamMatcher(service, self._cache)
        self._event_matcher = EventCardMatcher(service, self._cache)

        # League event types cache
        self._league_event_types: dict[str, str] = {}

        # Prefetched events (populated in match_all for multi-league matching)
        self._prefetched_events: dict[str, list[Event]] | None = None

    def match_all(
        self,
        streams: list[dict],
        target_date: date,
        progress_callback: Callable | None = None,
    ) -> BatchMatchResult:
        """Match all streams to events.

        Args:
            streams: List of dicts with 'id' and 'name' keys
            target_date: Date to match events for
            progress_callback: Optional callback(current, total, stream_name, matched)

        Returns:
            BatchMatchResult with all results
        """
        # Only increment generation if not provided from parent run
        # (When called as part of full EPG generation, generation is shared across groups)
        if not self._generation_provided:
            self._generation = increment_generation_counter(self._db_factory)

        # Load league event types
        self._load_league_event_types()

        # Prefetch events for multi-league matching (significant performance boost)
        # This fetches events ONCE for all streams instead of per-stream
        if len(self._search_leagues) > 1:
            self._prefetch_events(target_date)
        else:
            self._prefetched_events = None

        result = BatchMatchResult(
            target_date=target_date,
            leagues_searched=self._search_leagues,
            include_leagues=list(self._include_leagues),
        )

        total_streams = len(streams)
        for idx, stream in enumerate(streams, 1):
            stream_id = stream.get("id", 0)
            stream_name = stream.get("name", "")

            match_result = self._match_single(
                stream_id=stream_id,
                stream_name=stream_name,
                target_date=target_date,
            )

            # Track cache stats
            if match_result.from_cache:
                result.cache_hits += 1
            else:
                result.cache_misses += 1

            result.results.append(match_result)

            # Report per-stream progress
            if progress_callback:
                progress_callback(idx, total_streams, stream_name, match_result.matched)

        return result

    def _prefetch_events(self, target_date: date) -> None:
        """Prefetch all events for multi-league matching.

        For groups with many leagues (e.g., 278 leagues), fetching events
        per-stream is extremely slow (278 leagues × 15 days × 400 streams).
        Instead, fetch all events ONCE and reuse for all streams.

        Strategy:
        - Today: always fetch from API (ESPN only)
        - Past dates: use full 30-day cache for matching
        - TSDB leagues: always cache-only
        """
        MATCH_WINDOW_DAYS = 30  # Use full 30-day cache for matching

        self._prefetched_events = {}
        total_events = 0

        for league in self._search_leagues:
            league_events: list[Event] = []
            is_tsdb = self._service.get_provider_name(league) == "tsdb"

            for offset in range(-MATCH_WINDOW_DAYS, 1):
                fetch_date = target_date + timedelta(days=offset)
                # Today: fetch fresh; Past: always use cache
                cache_only = is_tsdb or offset < 0
                events = self._service.get_events(league, fetch_date, cache_only=cache_only)
                league_events.extend(events)

            if league_events:
                self._prefetched_events[league] = league_events
                total_events += len(league_events)

        logger.debug(
            f"Prefetched {total_events} events from {len(self._prefetched_events)} leagues "
            f"(window: -{MATCH_WINDOW_DAYS} to 0 days)"
        )

    def _match_single(
        self,
        stream_id: int,
        stream_name: str,
        target_date: date,
    ) -> MatchedStreamResult:
        """Match a single stream."""
        # Step 1: Classify the stream
        # Determine event type from configured leagues
        league_event_type = self._get_dominant_event_type()

        classified = classify_stream(stream_name, league_event_type, self._custom_regex)

        # Step 2: Handle placeholders
        if classified.category == StreamCategory.PLACEHOLDER:
            return MatchedStreamResult(
                stream_name=stream_name,
                stream_id=stream_id,
                matched=False,
                included=False,
                category=StreamCategory.PLACEHOLDER,
                exclusion_reason="placeholder",
            )

        # Step 3: Route to appropriate matcher based on category
        if classified.category == StreamCategory.EVENT_CARD:
            outcome = self._match_event_card(
                classified=classified,
                stream_id=stream_id,
                target_date=target_date,
            )
        else:  # TEAM_VS_TEAM
            outcome = self._match_team_vs_team(
                classified=classified,
                stream_id=stream_id,
                target_date=target_date,
            )

        # Step 4: Convert outcome to result
        return self._outcome_to_result(
            outcome=outcome,
            stream_id=stream_id,
            stream_name=stream_name,
            classified=classified,
        )

    def _match_team_vs_team(
        self,
        classified: ClassifiedStream,
        stream_id: int,
        target_date: date,
    ) -> MatchOutcome:
        """Match a team-vs-team stream."""
        # Determine if single-league or multi-league matching
        if len(self._search_leagues) == 1:
            league = self._search_leagues[0]
            return self._team_matcher.match_single_league(
                classified=classified,
                league=league,
                target_date=target_date,
                group_id=self._group_id,
                stream_id=stream_id,
                generation=self._generation,
                user_tz=self._user_tz,
                sport_durations=self._sport_durations,
            )
        else:
            return self._team_matcher.match_multi_league(
                classified=classified,
                enabled_leagues=self._search_leagues,
                target_date=target_date,
                group_id=self._group_id,
                stream_id=stream_id,
                generation=self._generation,
                user_tz=self._user_tz,
                sport_durations=self._sport_durations,
                prefetched_events=self._prefetched_events,
            )

    def _match_event_card(
        self,
        classified: ClassifiedStream,
        stream_id: int,
        target_date: date,
    ) -> MatchOutcome:
        """Match an event card stream (UFC, boxing)."""
        # Find the event card league in our search leagues
        event_card_leagues = [
            lg for lg in self._search_leagues if self._league_event_types.get(lg) == "event_card"
        ]

        if not event_card_leagues:
            return MatchOutcome.filtered(
                FilteredReason.LEAGUE_NOT_INCLUDED,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
                detail="No event card leagues configured",
            )

        # Try each event card league
        for league in event_card_leagues:
            outcome = self._event_matcher.match(
                classified=classified,
                league=league,
                target_date=target_date,
                group_id=self._group_id,
                stream_id=stream_id,
                generation=self._generation,
                user_tz=self._user_tz,
            )
            if outcome.is_matched:
                return outcome

        # No match in any event card league
        return MatchOutcome.failed(
            reason=outcome.failed_reason if outcome else None,
            stream_name=classified.normalized.original,
            stream_id=stream_id,
            detail="No matching event card found",
        )

    def _outcome_to_result(
        self,
        outcome: MatchOutcome,
        stream_id: int,
        stream_name: str,
        classified: ClassifiedStream,
    ) -> MatchedStreamResult:
        """Convert MatchOutcome to MatchedStreamResult."""
        # Determine inclusion
        included = False
        exclusion_reason = None

        if outcome.is_matched and outcome.event:
            # Check if league is in include list
            if outcome.detected_league and outcome.detected_league in self._include_leagues:
                # Check if event is final
                if not self._include_final_events and outcome.event.status:
                    if outcome.event.status.state == "final":
                        exclusion_reason = "event_final"
                    else:
                        included = True
                else:
                    included = True
            else:
                exclusion_reason = f"league_not_included:{outcome.detected_league}"
        elif outcome.is_filtered:
            reason = outcome.filtered_reason.value if outcome.filtered_reason else "filtered"
            exclusion_reason = reason
        elif outcome.is_failed:
            reason = outcome.failed_reason.value if outcome.failed_reason else "failed"
            exclusion_reason = reason

        return MatchedStreamResult(
            stream_name=stream_name,
            stream_id=stream_id,
            matched=outcome.is_matched,
            event=outcome.event,
            league=outcome.detected_league,
            included=included,
            exclusion_reason=exclusion_reason,
            match_method=outcome.match_method,
            confidence=outcome.confidence,
            from_cache=outcome.match_method == MatchMethod.CACHE if outcome.match_method else False,
            origin_match_method=outcome.origin_match_method,  # For cache hits
            category=classified.category,
            parsed_team1=classified.team1,
            parsed_team2=classified.team2,
            detected_league=classified.league_hint,
        )

    def _get_dominant_event_type(self) -> str | None:
        """Get the dominant event type from configured leagues."""
        if not self._league_event_types:
            return None

        # Count event types
        type_counts: dict[str, int] = {}
        for league in self._search_leagues:
            event_type = self._league_event_types.get(league, "team_vs_team")
            type_counts[event_type] = type_counts.get(event_type, 0) + 1

        # Return the most common type
        if type_counts:
            return max(type_counts, key=type_counts.get)
        return None

    def _load_league_event_types(self) -> None:
        """Load event types for all search leagues."""
        with self._db_factory() as conn:
            for league in self._search_leagues:
                league_info = get_league(conn, league)
                if league_info:
                    self._league_event_types[league] = league_info.get("event_type", "team_vs_team")

    def purge_stale(self) -> int:
        """Purge stale cache entries.

        Call at end of EPG run.

        Returns:
            Number of entries purged
        """
        return self._cache.purge_stale(self._generation)

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "generation": self._generation,
            "size": self._cache.get_size(),
            **self._cache.get_stats(),
        }
