"""Team vs Team stream matcher.

Matches streams that contain team matchups (vs/@/at) to provider events.
Supports two modes:
- Single-league: Search only the authoritative league (team EPG)
- Multi-league: Detect league hint, search enabled leagues (event EPG)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from teamarr.consumers.matching import MATCH_WINDOW_DAYS
from teamarr.consumers.matching.classifier import ClassifiedStream, StreamCategory
from teamarr.consumers.matching.normalizer import normalize_for_matching
from teamarr.consumers.matching.result import (
    FailedReason,
    FilteredReason,
    MatchMethod,
    MatchOutcome,
)
from teamarr.consumers.stream_match_cache import StreamMatchCache, event_to_cache_data
from teamarr.core.types import Event, Team
from teamarr.services.sports_data import SportsDataService
from teamarr.utilities.constants import TEAM_ALIASES
from teamarr.utilities.fuzzy_match import get_matcher

logger = logging.getLogger(__name__)


@dataclass
class MatchContext:
    """Context for a matching attempt."""

    stream_name: str
    stream_id: int
    group_id: int
    target_date: date
    generation: int
    user_tz: ZoneInfo

    # From classifier
    classified: ClassifiedStream

    # Extracted team names (from classifier)
    team1: str | None = None
    team2: str | None = None

    # Sport durations for ongoing event detection (hours)
    sport_durations: dict[str, float] = field(default_factory=dict)

    def is_event_in_search_window(self, event: "Event") -> bool:
        """Check if an event falls within the 30-day search window for matching.

        V2 uses full 30-day cache for matching to support stats tracking.
        The lifecycle layer will categorize matched-but-past events as EXCLUDED,
        allowing users to see that streams matched correctly even if events are over.

        Final/completed status is NOT checked here - lifecycle handles exclusions.
        """
        event_start = event.start_time.astimezone(self.user_tz)
        event_date = event_start.date()

        earliest_date = self.target_date - timedelta(days=MATCH_WINDOW_DAYS)

        return event_date >= earliest_date


class TeamMatcher:
    """Matches team-vs-team streams to provider events.

    Flow:
    1. Check user-corrected cache (pinned)
    2. Check algorithmic cache
    3. Match via: aliases → patterns → fuzzy
    4. Validate date
    5. Cache result
    """

    def __init__(
        self,
        service: SportsDataService,
        cache: StreamMatchCache,
        db_factory: Any = None,
        days_ahead: int = 3,
    ):
        """Initialize matcher.

        Args:
            service: Sports data service for event/team lookups
            cache: Stream match cache
            db_factory: Optional database factory for alias lookups
            days_ahead: Days to look ahead for events (default 3)
        """
        self._service = service
        self._cache = cache
        self._db = db_factory
        self._fuzzy = get_matcher()
        self._days_ahead = days_ahead

    def match_single_league(
        self,
        classified: ClassifiedStream,
        league: str,
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
        sport_durations: dict[str, float] | None = None,
    ) -> MatchOutcome:
        """Single-league matching - search only the specified league.

        Used for team EPG where the league is known from the team config.

        Args:
            classified: Pre-classified stream
            league: Authoritative league code
            target_date: Date to match events for
            group_id: Event group ID (for caching)
            stream_id: Stream ID (for caching)
            generation: Cache generation counter
            user_tz: User timezone for date validation
            sport_durations: Sport duration settings for ongoing event detection

        Returns:
            MatchOutcome with result
        """
        if classified.category != StreamCategory.TEAM_VS_TEAM:
            return MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
            )

        ctx = MatchContext(
            stream_name=classified.normalized.original,
            stream_id=stream_id,
            group_id=group_id,
            target_date=target_date,
            generation=generation,
            user_tz=user_tz,
            classified=classified,
            team1=classified.team1,
            team2=classified.team2,
            sport_durations=sport_durations or {},
        )

        # Check cache first
        cache_result = self._check_cache(ctx)
        if cache_result:
            return cache_result

        # Fetch events from MATCH_WINDOW_DAYS back to days_ahead
        # - Today + future: fetch from API (ESPN)
        # - Past: always use cache
        # - TSDB leagues: always cache-only
        is_tsdb = self._service.get_provider_name(league) == "tsdb"
        events = []
        for offset in range(-MATCH_WINDOW_DAYS, self._days_ahead + 1):
            fetch_date = target_date + timedelta(days=offset)
            # Today and future: fetch from API; Past/TSDB: cache only
            cache_only = is_tsdb or offset < 0
            events.extend(self._service.get_events(league, fetch_date, cache_only=cache_only))

        if not events:
            return MatchOutcome.failed(
                FailedReason.NO_EVENT_FOUND,
                stream_name=ctx.stream_name,
                stream_id=stream_id,
                detail=f"No events in {league} for {target_date}",
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # Try to match (is_event_ongoing filters out completed yesterday events)
        result = self._match_against_events(ctx, events, league)

        # Cache successful matches
        if result.is_matched and result.event:
            self._cache_result(ctx, result)

        return result

    def match_multi_league(
        self,
        classified: ClassifiedStream,
        enabled_leagues: list[str],
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
        sport_durations: dict[str, float] | None = None,
        prefetched_events: dict[str, list["Event"]] | None = None,
    ) -> MatchOutcome:
        """Multi-league matching with league hint detection.

        Used for event EPG groups with multiple leagues configured.

        Strategy:
        1. Check cache
        2. Detect league hint from stream name
           - If hint not in enabled_leagues → FILTERED:LEAGUE_NOT_INCLUDED
           - If hint in enabled_leagues → search only that league
        3. If no hint, search all enabled leagues
        4. Match and cache

        Args:
            classified: Pre-classified stream
            enabled_leagues: List of league codes enabled for this group
            target_date: Date to match events for
            group_id: Event group ID (for caching)
            stream_id: Stream ID (for caching)
            generation: Cache generation counter
            user_tz: User timezone for date validation
            sport_durations: Sport duration settings for ongoing event detection
            prefetched_events: Optional pre-fetched events by league (for performance)

        Returns:
            MatchOutcome with result
        """
        if classified.category != StreamCategory.TEAM_VS_TEAM:
            return MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
            )

        ctx = MatchContext(
            stream_name=classified.normalized.original,
            stream_id=stream_id,
            group_id=group_id,
            target_date=target_date,
            generation=generation,
            user_tz=user_tz,
            classified=classified,
            team1=classified.team1,
            team2=classified.team2,
            sport_durations=sport_durations or {},
        )

        # Check cache first
        cache_result = self._check_cache(ctx)
        if cache_result:
            return cache_result

        # Detect league hint
        league_hint = classified.league_hint

        if league_hint:
            if league_hint not in enabled_leagues:
                # Stream is for a league we're not tracking
                return MatchOutcome.filtered(
                    FilteredReason.LEAGUE_NOT_INCLUDED,
                    stream_name=ctx.stream_name,
                    stream_id=stream_id,
                    detail=f"League '{league_hint}' not in enabled leagues",
                )
            # Narrow search to hinted league
            leagues_to_search = [league_hint]
        else:
            # No hint, search all enabled leagues
            leagues_to_search = enabled_leagues

        # Use prefetched events if available (much faster for multi-stream matching)
        # Otherwise, fetch events: use full 30-day cache for matching
        all_events: list[tuple[str, Event]] = []

        if prefetched_events:
            # Use pre-fetched events (already fetched once for all streams)
            for league in leagues_to_search:
                for event in prefetched_events.get(league, []):
                    all_events.append((league, event))
        else:
            # Fallback: fetch events per-stream (slower, used when no prefetch)
            for league in leagues_to_search:
                is_tsdb = self._service.get_provider_name(league) == "tsdb"
                for offset in range(-MATCH_WINDOW_DAYS, self._days_ahead + 1):
                    fetch_date = target_date + timedelta(days=offset)
                    # Today and future: fetch from API; Past/TSDB: cache only
                    cache_only = is_tsdb or offset < 0
                    events = self._service.get_events(league, fetch_date, cache_only=cache_only)
                    for event in events:
                        all_events.append((league, event))

        if not all_events:
            return MatchOutcome.failed(
                FailedReason.NO_EVENT_FOUND,
                stream_name=ctx.stream_name,
                stream_id=stream_id,
                detail=f"No events in any league for {target_date}",
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # Try to match against all events
        result = self._match_against_multi_league_events(ctx, all_events)

        # Cache successful matches
        if result.is_matched and result.event:
            self._cache_result(ctx, result)

        return result

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    def _check_cache(self, ctx: MatchContext) -> MatchOutcome | None:
        """Check cache for existing match.

        User-corrected entries are always trusted (pinned).
        Algorithmic entries are validated against date.
        """
        entry = self._cache.get(ctx.group_id, ctx.stream_id, ctx.stream_name)
        if not entry:
            return None

        # Touch the cache entry to keep it fresh
        self._cache.touch(ctx.group_id, ctx.stream_id, ctx.stream_name, ctx.generation)

        # Reconstruct event from cached data
        event = self._reconstruct_event(entry.cached_data)
        if not event:
            # Cache entry is invalid
            logger.debug("[MATCH_CACHE] Invalid: failed to reconstruct event for stream=%d", ctx.stream_id)
            self._cache.delete(ctx.group_id, ctx.stream_id, ctx.stream_name)
            return None

        # User-corrected entries are pinned - always trust them regardless of date
        if entry.user_corrected:
            logger.debug(
                "[CACHE_HIT] stream_id=%d event=%s (user corrected)",
                ctx.stream_id,
                event.id,
            )
            return MatchOutcome.matched(
                MatchMethod.USER_CORRECTED,
                event,
                detected_league=entry.league,
                confidence=1.0,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # V1 Parity: Cached events from yesterday should be re-matched to get fresh status.
        # The cached event has OLD status from when it was cached, which may have
        # changed to "final". Re-matching ensures we get current status from ESPN.
        event_date = event.start_time.astimezone(ctx.user_tz).date()
        if event_date < ctx.target_date:
            # Event is from a previous day - invalidate cache to get fresh status
            logger.debug("[MATCH_CACHE] Stale: event from %s < target %s", event_date, ctx.target_date)
            return None

        # Today's events: use cache (final status handled in _outcome_to_result)
        if event_date != ctx.target_date:
            logger.debug("[MATCH_CACHE] Mismatch: event from %s != target %s", event_date, ctx.target_date)
            return None

        logger.debug(
            "[CACHE_HIT] stream_id=%d event=%s",
            ctx.stream_id,
            event.id,
        )
        return MatchOutcome.matched(
            MatchMethod.CACHE,
            event,
            detected_league=entry.league,
            confidence=1.0,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            parsed_team1=ctx.team1,
            parsed_team2=ctx.team2,
            origin_match_method=entry.match_method,  # Original method (fuzzy, alias, etc.)
        )

    def _match_against_events(
        self,
        ctx: MatchContext,
        events: list[Event],
        league: str,
    ) -> MatchOutcome:
        """Try to match classified stream against events in a single league."""
        team1_normalized = normalize_for_matching(ctx.team1) if ctx.team1 else None
        team2_normalized = normalize_for_matching(ctx.team2) if ctx.team2 else None

        if not team1_normalized and not team2_normalized:
            return MatchOutcome.failed(
                FailedReason.TEAMS_NOT_PARSED,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                detail="No team names extracted",
            )

        best_match: Event | None = None
        best_method: MatchMethod = MatchMethod.FUZZY
        best_confidence: float = 0.0
        best_is_future: bool = False  # Whether best match is today or future
        best_date_distance: int = 999  # Absolute days from target_date

        for event in events:
            # Validate event is within search window (lifecycle handles exclusions)
            if not ctx.is_event_in_search_window(event):
                continue

            event_date = event.start_time.astimezone(ctx.user_tz).date()

            # Check for date mismatch from stream (if extracted)
            if ctx.classified.normalized.extracted_date:
                if ctx.classified.normalized.extracted_date != event_date:
                    continue

            # Check for sport mismatch from stream (if detected)
            # e.g., "Ice Hockey" in stream name should not match NFL events
            # Use case-insensitive comparison (event.sport is lowercase, hint may be capitalized)
            if ctx.classified.sport_hint:
                if event.sport.lower() != ctx.classified.sport_hint.lower():
                    continue

            # Try to match teams
            match_result = self._match_teams_to_event(team1_normalized, team2_normalized, event)

            if match_result:
                # Calculate date metrics for comparison
                days_from_target = (event_date - ctx.target_date).days
                is_future = days_from_target >= 0  # Today or future
                abs_distance = abs(days_from_target)

                # Prefer: higher confidence > future over past > closer to target_date
                is_better = False
                if match_result[1] > best_confidence:
                    is_better = True
                elif match_result[1] == best_confidence:
                    if is_future and not best_is_future:
                        # Future beats past
                        is_better = True
                    elif is_future == best_is_future and abs_distance < best_date_distance:
                        # Same future/past status, prefer closer
                        is_better = True

                if is_better:
                    best_match = event
                    best_method = match_result[0]
                    best_confidence = match_result[1]
                    best_is_future = is_future
                    best_date_distance = abs_distance

        if best_match:
            # If multiple events same day (doubleheader), pick closest to stream time
            if ctx.classified.normalized.extracted_time:
                matching_events = [
                    e
                    for e in events
                    if e.start_time.astimezone(ctx.user_tz).date() == ctx.target_date
                    and self._match_teams_to_event(team1_normalized, team2_normalized, e)
                ]
                if len(matching_events) > 1:
                    best_match = self._disambiguate_by_time(
                        matching_events,
                        ctx.classified.normalized.extracted_time,
                        ctx.user_tz,
                    )

            logger.debug(
                "[MATCHED] stream_id=%d method=%s event=%s confidence=%.0f%%",
                ctx.stream_id,
                best_method.value,
                best_match.id,
                best_confidence,
            )
            return MatchOutcome.matched(
                best_method,
                best_match,
                detected_league=league,
                confidence=best_confidence / 100.0,  # Convert to 0-1
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # No match found
        if team1_normalized and not team2_normalized:
            reason = FailedReason.TEAM2_NOT_FOUND
        elif team2_normalized and not team1_normalized:
            reason = FailedReason.TEAM1_NOT_FOUND
        else:
            reason = FailedReason.NO_EVENT_FOUND

        logger.debug(
            "[FAILED] stream_id=%d reason=%s teams=%s/%s",
            ctx.stream_id,
            reason.value,
            ctx.team1,
            ctx.team2,
        )
        return MatchOutcome.failed(
            reason,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            parsed_team1=ctx.team1,
            parsed_team2=ctx.team2,
        )

    def _match_against_multi_league_events(
        self,
        ctx: MatchContext,
        events: list[tuple[str, Event]],
    ) -> MatchOutcome:
        """Try to match against events from multiple leagues."""
        team1_normalized = normalize_for_matching(ctx.team1) if ctx.team1 else None
        team2_normalized = normalize_for_matching(ctx.team2) if ctx.team2 else None

        if not team1_normalized and not team2_normalized:
            return MatchOutcome.failed(
                FailedReason.TEAMS_NOT_PARSED,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                detail="No team names extracted",
            )

        best_match: Event | None = None
        best_league: str | None = None
        best_method: MatchMethod = MatchMethod.FUZZY
        best_confidence: float = 0.0
        best_is_future: bool = False  # Whether best match is today or future
        best_date_distance: int = 999  # Absolute days from target_date

        for league, event in events:
            # Validate event is within search window (lifecycle handles exclusions)
            if not ctx.is_event_in_search_window(event):
                continue

            event_date = event.start_time.astimezone(ctx.user_tz).date()

            # Check for date mismatch from stream (if extracted)
            if ctx.classified.normalized.extracted_date:
                if ctx.classified.normalized.extracted_date != event_date:
                    continue

            # Check for sport mismatch from stream (if detected)
            # e.g., "Ice Hockey" in stream name should not match NFL events
            # Use case-insensitive comparison (event.sport is lowercase, hint may be capitalized)
            if ctx.classified.sport_hint:
                if event.sport.lower() != ctx.classified.sport_hint.lower():
                    continue

            # Try to match teams
            match_result = self._match_teams_to_event(team1_normalized, team2_normalized, event)

            if match_result:
                # Calculate date metrics for comparison
                days_from_target = (event_date - ctx.target_date).days
                is_future = days_from_target >= 0  # Today or future
                abs_distance = abs(days_from_target)

                # Prefer: higher confidence > future over past > closer to target_date
                is_better = False
                if match_result[1] > best_confidence:
                    is_better = True
                elif match_result[1] == best_confidence:
                    if is_future and not best_is_future:
                        # Future beats past
                        is_better = True
                    elif is_future == best_is_future and abs_distance < best_date_distance:
                        # Same future/past status, prefer closer
                        is_better = True

                if is_better:
                    best_match = event
                    best_league = league
                    best_method = match_result[0]
                    best_confidence = match_result[1]
                    best_is_future = is_future
                    best_date_distance = abs_distance

        if best_match and best_league:
            logger.debug(
                "[MATCHED] stream_id=%d method=%s event=%s league=%s confidence=%.0f%%",
                ctx.stream_id,
                best_method.value,
                best_match.id,
                best_league,
                best_confidence,
            )
            return MatchOutcome.matched(
                best_method,
                best_match,
                detected_league=best_league,
                confidence=best_confidence / 100.0,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # No match found
        if team1_normalized and not team2_normalized:
            reason = FailedReason.TEAM2_NOT_FOUND
        elif team2_normalized and not team1_normalized:
            reason = FailedReason.TEAM1_NOT_FOUND
        else:
            reason = FailedReason.NO_EVENT_FOUND

        logger.debug(
            "[FAILED] stream_id=%d reason=%s teams=%s/%s",
            ctx.stream_id,
            reason.value,
            ctx.team1,
            ctx.team2,
        )
        return MatchOutcome.failed(
            reason,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            parsed_team1=ctx.team1,
            parsed_team2=ctx.team2,
        )

    def _match_teams_to_event(
        self,
        team1: str | None,
        team2: str | None,
        event: Event,
    ) -> tuple[MatchMethod, float] | None:
        """Try to match extracted team names to event teams.

        Returns:
            Tuple of (method, confidence) if matched, None otherwise
        """
        home_patterns = self._fuzzy.generate_team_patterns(event.home_team)
        away_patterns = self._fuzzy.generate_team_patterns(event.away_team)

        team1_match = False
        team2_match = False
        best_score = 0.0
        method = MatchMethod.FUZZY

        # Check team1
        if team1:
            # First check built-in aliases
            canonical = TEAM_ALIASES.get(team1.lower())
            if canonical:
                # Check if canonical matches either team
                if any(canonical in p for p in home_patterns):
                    team1_match = True
                    best_score = max(best_score, 100.0)
                    method = MatchMethod.ALIAS
                elif any(canonical in p for p in away_patterns):
                    team1_match = True
                    best_score = max(best_score, 100.0)
                    method = MatchMethod.ALIAS

            # Try fuzzy match
            if not team1_match:
                home_result = self._fuzzy.matches_any(home_patterns, team1)
                away_result = self._fuzzy.matches_any(away_patterns, team1)

                if home_result.matched or away_result.matched:
                    team1_match = True
                    score = max(home_result.score, away_result.score)
                    best_score = max(best_score, score)

        # Check team2
        if team2:
            # First check built-in aliases
            canonical = TEAM_ALIASES.get(team2.lower())
            if canonical:
                if any(canonical in p for p in home_patterns):
                    team2_match = True
                    best_score = max(best_score, 100.0)
                    if method != MatchMethod.ALIAS:
                        method = MatchMethod.ALIAS
                elif any(canonical in p for p in away_patterns):
                    team2_match = True
                    best_score = max(best_score, 100.0)
                    if method != MatchMethod.ALIAS:
                        method = MatchMethod.ALIAS

            # Try fuzzy match
            if not team2_match:
                home_result = self._fuzzy.matches_any(home_patterns, team2)
                away_result = self._fuzzy.matches_any(away_patterns, team2)

                if home_result.matched or away_result.matched:
                    team2_match = True
                    score = max(home_result.score, away_result.score)
                    best_score = max(best_score, score)

        # Need both teams to match (if both were extracted)
        if team1 and team2:
            if team1_match and team2_match:
                return (method, best_score)
            return None
        elif team1 and team1_match:
            return (method, best_score)
        elif team2 and team2_match:
            return (method, best_score)

        return None

    def _disambiguate_by_time(
        self,
        events: list[Event],
        stream_time: time,
        user_tz: ZoneInfo,
    ) -> Event:
        """Pick event closest to stream time for doubleheaders."""
        if len(events) <= 1:
            return events[0] if events else None

        # Combine stream time with event date
        ref_date = events[0].start_time.astimezone(user_tz).date()
        stream_dt = datetime.combine(ref_date, stream_time, tzinfo=user_tz)

        return min(events, key=lambda e: abs(e.start_time.astimezone(user_tz) - stream_dt))

    def _cache_result(self, ctx: MatchContext, result: MatchOutcome) -> None:
        """Cache a successful match."""
        if not result.event:
            return

        cached_data = event_to_cache_data(result.event)

        # Store the original match method so we can show "Cache (origin: fuzzy)" etc.
        match_method_value = result.match_method.value if result.match_method else None

        self._cache.set(
            group_id=ctx.group_id,
            stream_id=ctx.stream_id,
            stream_name=ctx.stream_name,
            event_id=result.event.id,
            league=result.detected_league or result.event.league,
            cached_data=cached_data,
            generation=ctx.generation,
            match_method=match_method_value,
        )

    def _reconstruct_event(self, cached_data: dict[str, Any]) -> Event | None:
        """Reconstruct Event from cached dict."""
        try:
            # Handle datetime parsing
            start_time = cached_data.get("start_time")
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)

            # Reconstruct teams
            home_data = cached_data.get("home_team", {})
            away_data = cached_data.get("away_team", {})

            home_team = Team(
                id=home_data.get("id", ""),
                provider=home_data.get("provider", ""),
                name=home_data.get("name", ""),
                short_name=home_data.get("short_name", ""),
                abbreviation=home_data.get("abbreviation", ""),
                league=home_data.get("league", ""),
                sport=home_data.get("sport", ""),
                logo_url=home_data.get("logo_url"),
                color=home_data.get("color"),
            )

            away_team = Team(
                id=away_data.get("id", ""),
                provider=away_data.get("provider", ""),
                name=away_data.get("name", ""),
                short_name=away_data.get("short_name", ""),
                abbreviation=away_data.get("abbreviation", ""),
                league=away_data.get("league", ""),
                sport=away_data.get("sport", ""),
                logo_url=away_data.get("logo_url"),
                color=away_data.get("color"),
            )

            from teamarr.core.types import EventStatus

            status_data = cached_data.get("status", {})
            status = EventStatus(
                state=status_data.get("state", "scheduled"),
                detail=status_data.get("detail"),
                period=status_data.get("period"),
                clock=status_data.get("clock"),
            )

            # Handle broadcast/broadcasts field compatibility
            broadcast_val = cached_data.get("broadcasts") or cached_data.get("broadcast")
            broadcasts = (
                broadcast_val
                if isinstance(broadcast_val, list)
                else [broadcast_val]
                if broadcast_val
                else []
            )

            # Reconstruct Venue from dict if present
            from teamarr.core.types import Venue

            venue_data = cached_data.get("venue")
            venue = None
            if venue_data:
                if isinstance(venue_data, dict):
                    venue = Venue(
                        name=venue_data.get("name", ""),
                        city=venue_data.get("city"),
                        state=venue_data.get("state"),
                        country=venue_data.get("country"),
                    )
                else:
                    venue = venue_data  # Already a Venue

            return Event(
                id=cached_data.get("id", ""),
                provider=cached_data.get("provider", ""),
                name=cached_data.get("name", ""),
                short_name=cached_data.get("short_name"),
                start_time=start_time,
                home_team=home_team,
                away_team=away_team,
                status=status,
                league=cached_data.get("league", ""),
                sport=cached_data.get("sport", ""),
                venue=venue,
                broadcasts=broadcasts,
            )
        except Exception as e:
            logger.warning("[MATCH_CACHE] Failed to reconstruct event from cache: %s", e)
            return None
