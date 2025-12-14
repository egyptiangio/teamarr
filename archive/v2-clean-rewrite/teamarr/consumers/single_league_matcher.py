"""Single-league stream matcher.

Uses Events → Streams approach for a single known league.
Generates patterns from events, scans stream names for matches.
Uses fuzzy matching for better tolerance of name variations.
"""

from dataclasses import dataclass
from datetime import date

from teamarr.core import Event
from teamarr.services import SportsDataService
from teamarr.utilities.fuzzy_match import FuzzyMatcher, get_matcher


@dataclass
class MatchResult:
    """Result of a stream-to-event match."""

    stream_name: str
    event: Event | None
    league: str
    matched: bool
    exception_keyword: str | None = None

    @property
    def is_exception(self) -> bool:
        return self.exception_keyword is not None


class SingleLeagueMatcher:
    """Matches streams to events for a single known league."""

    def __init__(
        self,
        service: SportsDataService,
        league: str,
        exception_keywords: list[str] | None = None,
        fuzzy_matcher: FuzzyMatcher | None = None,
    ):
        self._service = service
        self._league = league
        self._exception_keywords = [kw.lower() for kw in (exception_keywords or [])]
        self._fuzzy = fuzzy_matcher or get_matcher()

        # Built during match
        self._events: list[Event] = []
        self._event_patterns: list[tuple[Event, list[str], list[str], list[str]]] = []
        self._cache_date: date | None = None

    def match(self, stream_name: str, target_date: date) -> MatchResult:
        """Match a stream name to an event in the configured league."""
        stream_lower = stream_name.lower()

        # Check for exception keyword
        for keyword in self._exception_keywords:
            if keyword in stream_lower:
                return MatchResult(
                    stream_name=stream_name,
                    event=None,
                    league=self._league,
                    matched=False,
                    exception_keyword=keyword,
                )

        # Build patterns if needed
        self._build_patterns(target_date)

        # Find matching event
        event = self._find_matching_event(stream_lower)

        return MatchResult(
            stream_name=stream_name,
            event=event,
            league=self._league,
            matched=event is not None,
        )

    def match_batch(
        self, stream_names: list[str], target_date: date
    ) -> list[MatchResult]:
        """Match multiple streams efficiently."""
        self._build_patterns(target_date)
        return [self.match(name, target_date) for name in stream_names]

    def _build_patterns(self, target_date: date) -> None:
        """Build search patterns from events using fuzzy matcher."""
        if self._cache_date == target_date:
            return

        self._events = self._service.get_events(self._league, target_date)
        self._event_patterns = []

        for event in self._events:
            # Use fuzzy matcher to generate patterns (includes mascot stripping)
            home_patterns = self._fuzzy.generate_team_patterns(event.home_team)
            away_patterns = self._fuzzy.generate_team_patterns(event.away_team)
            event_patterns = self._unique([event.name, event.short_name])
            self._event_patterns.append((event, home_patterns, away_patterns, event_patterns))

        self._cache_date = target_date

    def _unique(self, values: list[str]) -> list[str]:
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

    def _find_matching_event(self, stream_lower: str) -> Event | None:
        """Find event that matches the stream name using fuzzy matching."""
        # Try team-based matching first (need BOTH teams)
        for event, home_patterns, away_patterns, _ in self._event_patterns:
            home_match = self._fuzzy.matches_any(home_patterns, stream_lower)
            away_match = self._fuzzy.matches_any(away_patterns, stream_lower)
            if home_match.matched and away_match.matched:
                return event

        # Fallback: event name matching
        for event, _, _, event_patterns in self._event_patterns:
            event_match = self._fuzzy.matches_any(event_patterns, stream_lower)
            if event_match.matched:
                return event

        return None
