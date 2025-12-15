"""Cached stream matcher - combines cache with MultiLeagueMatcher.

Uses fingerprint cache to avoid expensive matching on every EPG run.
On cache hit: returns cached event data, refreshes dynamic fields from API.
On cache miss: performs full match, caches result.

Usage:
    from teamarr.consumers import CachedMatcher
    from teamarr.database import get_db

    matcher = CachedMatcher(
        service=sports_data_service,
        get_connection=get_db,
        search_leagues=["nfl", "nba"],
        group_id=1,
    )

    # Match all streams (uses cache where possible)
    result = matcher.match_all(stream_names, target_date)

    # Purge old cache entries at end of run
    matcher.purge_stale()
"""

import logging
from dataclasses import dataclass
from datetime import date

from teamarr.consumers.multi_league_matcher import (
    BatchMatchResult,
    MultiLeagueMatcher,
    StreamMatchResult,
)
from teamarr.consumers.stream_match_cache import (
    StreamMatchCache,
    event_to_cache_data,
    get_generation_counter,
    increment_generation_counter,
)
from teamarr.core import Event
from teamarr.services import SportsDataService
from teamarr.utilities.fuzzy_match import FuzzyMatcher

logger = logging.getLogger(__name__)


@dataclass
class CachedMatchResult(StreamMatchResult):
    """Extended match result with cache info."""

    from_cache: bool = False
    refreshed: bool = False


@dataclass
class CachedBatchResult(BatchMatchResult):
    """Extended batch result with cache stats."""

    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total


class CachedMatcher:
    """Stream matcher with fingerprint cache integration.

    Wraps MultiLeagueMatcher with cache layer:
    1. Check cache before matching
    2. On hit: use cached event data, touch entry
    3. On miss: perform full match, cache result
    4. At end: purge stale entries
    """

    def __init__(
        self,
        service: SportsDataService,
        get_connection,
        search_leagues: list[str],
        group_id: int,
        include_leagues: list[str] | None = None,
        exception_keywords: list[str] | None = None,
        fuzzy_matcher: FuzzyMatcher | None = None,
    ):
        """Initialize cached matcher.

        Args:
            service: Sports data service for fetching events
            get_connection: Database connection factory
            search_leagues: Leagues to search for events
            group_id: Event group ID for cache fingerprints
            include_leagues: Whitelist of leagues to include in results
            exception_keywords: Keywords that bypass matching
            fuzzy_matcher: Optional custom fuzzy matcher
        """
        self._service = service
        self._get_connection = get_connection
        self._group_id = group_id

        # Initialize matcher and cache
        self._matcher = MultiLeagueMatcher(
            service=service,
            search_leagues=search_leagues,
            include_leagues=include_leagues,
            exception_keywords=exception_keywords,
            fuzzy_matcher=fuzzy_matcher,
        )
        self._cache = StreamMatchCache(get_connection)

        # Get current generation
        self._generation = get_generation_counter(get_connection)

    def match_all(
        self,
        streams: list[dict],
        target_date: date,
    ) -> CachedBatchResult:
        """Match streams against events, using cache where possible.

        Args:
            streams: List of dicts with 'id' and 'name' keys
            target_date: Date to match events for

        Returns:
            CachedBatchResult with match results and cache stats
        """
        # Increment generation counter at start of run
        self._generation = increment_generation_counter(self._get_connection)

        results = []
        cache_hits = 0
        cache_misses = 0

        for stream in streams:
            stream_id = stream.get("id", 0)
            stream_name = stream.get("name", "")

            # Try cache first
            cached = self._cache.get(self._group_id, stream_id, stream_name)

            if cached:
                cache_hits += 1
                # Cache hit - use cached event, refresh dynamic fields
                event = self._refresh_event(cached.event_id, cached.league)
                self._cache.touch(
                    self._group_id, stream_id, stream_name, self._generation
                )

                result = CachedMatchResult(
                    stream_name=stream_name,
                    matched=True,
                    event=event,
                    league=cached.league,
                    included=True,
                    from_cache=True,
                    refreshed=event is not None,
                )
            else:
                cache_misses += 1
                # Cache miss - perform full match
                match_result = self._match_single(stream_name, target_date)
                result = CachedMatchResult(
                    stream_name=match_result.stream_name,
                    matched=match_result.matched,
                    event=match_result.event,
                    league=match_result.league,
                    included=match_result.included,
                    exclusion_reason=match_result.exclusion_reason,
                    exception_keyword=match_result.exception_keyword,
                    from_cache=False,
                    refreshed=False,
                )

                # Cache successful match
                if match_result.matched and match_result.event:
                    self._cache.set(
                        group_id=self._group_id,
                        stream_id=stream_id,
                        stream_name=stream_name,
                        event_id=match_result.event.id,
                        league=match_result.league,
                        cached_data=event_to_cache_data(match_result.event),
                        generation=self._generation,
                    )

            results.append(result)

        # Build batch result
        return CachedBatchResult(
            results=results,
            target_date=target_date,
            leagues_searched=self._matcher._search_leagues,
            include_leagues=(
                list(self._matcher._include_leagues)
                if self._matcher._include_leagues
                else self._matcher._search_leagues
            ),
            events_found=len(self._matcher._event_patterns),
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        )

    def _match_single(
        self, stream_name: str, target_date: date
    ) -> StreamMatchResult:
        """Match a single stream using the underlying matcher."""
        # Use the batch matcher for a single stream
        batch_result = self._matcher.match_all([stream_name], target_date)
        return batch_result.results[0] if batch_result.results else StreamMatchResult(
            stream_name=stream_name,
            matched=False,
            exclusion_reason="matcher_error",
        )

    def _refresh_event(self, event_id: str, league: str) -> Event | None:
        """Refresh dynamic event fields from API.

        Uses single event endpoint (30min cache) to get fresh:
        - scores
        - status
        - odds (if available)
        """
        return self._service.get_event(event_id, league)

    def purge_stale(self) -> int:
        """Purge stale cache entries not seen in recent generations.

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
