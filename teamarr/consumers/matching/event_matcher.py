"""Event card matcher for combat sports.

Matches streams for UFC, Boxing, and other event-card sports.
These don't have team-vs-team format but instead match by:
- Event number (UFC 315)
- Event keywords (Main Card, Prelims)
- Fighter names (fallback)
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from teamarr.consumers.matching.classifier import ClassifiedStream, StreamCategory
from teamarr.consumers.matching.result import (
    FailedReason,
    FilteredReason,
    MatchMethod,
    MatchOutcome,
)
from teamarr.consumers.stream_match_cache import StreamMatchCache, event_to_cache_data
from teamarr.core.types import Event
from teamarr.services.sports_data import SportsDataService
from teamarr.utilities.constants import EVENT_CARD_KEYWORDS

logger = logging.getLogger(__name__)


@dataclass
class EventMatchContext:
    """Context for event card matching."""

    stream_name: str
    stream_id: int
    group_id: int
    target_date: date
    generation: int
    user_tz: ZoneInfo
    classified: ClassifiedStream


class EventCardMatcher:
    """Matches event card streams (UFC, Boxing) to provider events.

    Event cards are identified by:
    - Event number: "UFC 315", "PFL 5"
    - Keywords: "Main Card", "Prelims", "Early Prelims"
    - Event name patterns

    Unlike team sports, combat sports typically have one event per date,
    so matching is simpler - we just need to confirm it's the right event.
    """

    def __init__(
        self,
        service: SportsDataService,
        cache: StreamMatchCache,
    ):
        """Initialize matcher.

        Args:
            service: Sports data service for event lookups
            cache: Stream match cache
        """
        self._service = service
        self._cache = cache

    def match(
        self,
        classified: ClassifiedStream,
        league: str,
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
    ) -> MatchOutcome:
        """Match an event card stream to a provider event.

        Args:
            classified: Pre-classified stream (should be EVENT_CARD)
            league: League code (ufc, boxing)
            target_date: Date to match events for
            group_id: Event group ID (for caching)
            stream_id: Stream ID (for caching)
            generation: Cache generation counter
            user_tz: User timezone for date validation

        Returns:
            MatchOutcome with result
        """
        if classified.category != StreamCategory.EVENT_CARD:
            return MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
                detail="Not an event card stream",
            )

        ctx = EventMatchContext(
            stream_name=classified.normalized.original,
            stream_id=stream_id,
            group_id=group_id,
            target_date=target_date,
            generation=generation,
            user_tz=user_tz,
            classified=classified,
        )

        # Check cache first
        cache_result = self._check_cache(ctx)
        if cache_result:
            logger.debug(
                "[CACHE HIT] event_card stream=%s matched=%s",
                ctx.stream_name[:50],
                cache_result.event.name if cache_result.event else "None",
            )
            return cache_result

        # Get events for this league (TSDB leagues use cache-only)
        is_tsdb = self._service.get_provider_name(league) == "tsdb"
        events = self._service.get_events(league, target_date, cache_only=is_tsdb)
        if not events:
            return MatchOutcome.failed(
                FailedReason.NO_EVENT_CARD_MATCH,
                stream_name=ctx.stream_name,
                stream_id=stream_id,
                detail=f"No {league} events for {target_date}",
            )

        # Filter to events on target date
        date_events = [e for e in events if e.start_time.astimezone(user_tz).date() == target_date]

        if not date_events:
            return MatchOutcome.failed(
                FailedReason.NO_EVENT_CARD_MATCH,
                stream_name=ctx.stream_name,
                stream_id=stream_id,
                detail=f"No {league} events on {target_date}",
            )

        # Try to match
        result = self._match_to_event_card(ctx, date_events, league)

        # Cache successful matches
        if result.is_matched and result.event:
            self._cache_result(ctx, result)

        return result

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    def _check_cache(self, ctx: EventMatchContext) -> MatchOutcome | None:
        """Check cache for existing match."""
        entry = self._cache.get(ctx.group_id, ctx.stream_id, ctx.stream_name)
        if not entry:
            return None

        # Touch to keep fresh
        self._cache.touch(ctx.group_id, ctx.stream_id, ctx.stream_name, ctx.generation)

        # Reconstruct event
        from teamarr.consumers.matching.team_matcher import TeamMatcher

        # Reuse reconstruction logic (bit of a hack but avoids duplication)
        matcher = TeamMatcher(self._service, self._cache)
        event = matcher._reconstruct_event(entry.cached_data)

        if not event:
            self._cache.delete(ctx.group_id, ctx.stream_id, ctx.stream_name)
            return None

        # Validate date
        event_date = event.start_time.astimezone(ctx.user_tz).date()
        if event_date != ctx.target_date:
            return None

        return MatchOutcome.matched(
            MatchMethod.CACHE,
            event,
            detected_league=entry.league,
            confidence=1.0,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            origin_match_method=entry.match_method,  # Original method
        )

    def _match_to_event_card(
        self,
        ctx: EventMatchContext,
        events: list[Event],
        league: str,
    ) -> MatchOutcome:
        """Match stream to an event card."""
        stream_lower = ctx.stream_name.lower()
        event_hint = ctx.classified.event_hint

        # Strategy 1: Match by event number (UFC 315)
        if event_hint:
            event_num = self._extract_event_number(event_hint)
            if event_num:
                for event in events:
                    if event_num.lower() in event.name.lower():
                        logger.debug(
                            "[MATCHED] event_card stream=%s -> %s (method=event_number)",
                            ctx.stream_name[:40],
                            event.name,
                        )
                        return MatchOutcome.matched(
                            MatchMethod.KEYWORD,
                            event,
                            detected_league=league,
                            confidence=1.0,
                            stream_name=ctx.stream_name,
                            stream_id=ctx.stream_id,
                        )

        # Strategy 2: Keyword matching
        keywords = EVENT_CARD_KEYWORDS.get(league, [])
        keyword_matches = []

        for keyword in keywords:
            if keyword.lower() in stream_lower:
                keyword_matches.append(keyword)

        # If we have event-specific keywords, we're confident
        if keyword_matches:
            # For single events on the date, just return it
            if len(events) == 1:
                logger.debug(
                    "[MATCHED] event_card stream=%s -> %s (method=keyword, single event)",
                    ctx.stream_name[:40],
                    events[0].name,
                )
                return MatchOutcome.matched(
                    MatchMethod.KEYWORD,
                    events[0],
                    detected_league=league,
                    confidence=0.9,
                    stream_name=ctx.stream_name,
                    stream_id=ctx.stream_id,
                )

            # Multiple events - try to narrow down
            # Check if any event name matches stream content
            for event in events:
                event_name_lower = event.name.lower()
                # Check if event name words appear in stream
                event_words = set(event_name_lower.split())
                stream_words = set(stream_lower.split())
                overlap = event_words & stream_words
                if len(overlap) >= 2:  # At least 2 matching words
                    logger.debug(
                        "[MATCHED] event_card stream=%s -> %s (method=keyword, word overlap)",
                        ctx.stream_name[:40],
                        event.name,
                    )
                    return MatchOutcome.matched(
                        MatchMethod.KEYWORD,
                        event,
                        detected_league=league,
                        confidence=0.85,
                        stream_name=ctx.stream_name,
                        stream_id=ctx.stream_id,
                    )

        # Strategy 3: Fighter name matching (fallback)
        # Try to find fighter names in stream
        for event in events:
            home_name = event.home_team.name.lower() if event.home_team else ""
            away_name = event.away_team.name.lower() if event.away_team else ""

            # Check for last names (more reliable)
            home_parts = home_name.split()
            away_parts = away_name.split()

            # Try last name first, then full name
            for parts in [home_parts, away_parts]:
                if len(parts) >= 1:
                    last_name = parts[-1]
                    if len(last_name) >= 4 and last_name in stream_lower:
                        logger.debug(
                            "[MATCHED] event_card stream=%s -> %s (method=fighter_name, '%s')",
                            ctx.stream_name[:40],
                            event.name,
                            last_name,
                        )
                        return MatchOutcome.matched(
                            MatchMethod.FUZZY,
                            event,
                            detected_league=league,
                            confidence=0.75,
                            stream_name=ctx.stream_name,
                            stream_id=ctx.stream_id,
                        )

        # No match found
        logger.debug(
            "[FAILED] event_card stream=%s: no match in %d events for %s",
            ctx.stream_name[:40],
            len(events),
            league,
        )
        return MatchOutcome.failed(
            FailedReason.NO_EVENT_CARD_MATCH,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            detail=f"Could not match to any {league} event",
        )

    def _extract_event_number(self, hint: str) -> str | None:
        """Extract event identifier from hint.

        Args:
            hint: Event hint like "UFC 315" or "PFL 5"

        Returns:
            Normalized event identifier or None
        """
        if not hint:
            return None

        # UFC 315, UFC FN 45
        match = re.search(
            r"(ufc\s*(?:fn|fight\s*night)?\s*\d+)",
            hint,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).upper().replace("  ", " ")

        # PFL 5, Bellator 300
        match = re.search(
            r"((?:pfl|bellator|one\s*fc)\s*\d+)",
            hint,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).upper()

        return None

    def _cache_result(self, ctx: EventMatchContext, result: MatchOutcome) -> None:
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
