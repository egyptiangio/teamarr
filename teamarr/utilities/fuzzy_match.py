"""Fuzzy string matching for team names.

Uses rapidfuzz for fast, maintenance-free fuzzy matching.
Provides whole-name token_set_ratio matching for event name comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rapidfuzz import fuzz
from unidecode import unidecode

if TYPE_CHECKING:
    from teamarr.core import Team

# Common abbreviations to expand for better matching
# Key: abbreviation (lowercase), Value: expansion
ABBREVIATIONS = {
    # UFC/MMA
    "fn": "fight night",
    "ufc fn": "ufc fight night",
    "ppv": "pay per view",
    # Sports generic
    "vs": "versus",
    "v": "versus",
}


@dataclass
class TeamPattern:
    """A searchable pattern for team matching.

    Simplified: just holds text for alias checking and fallback matching.

    Attributes:
        pattern: The normalized pattern text (lowercase, no punctuation)
        source: Debug info about where this pattern came from
    """

    pattern: str
    source: str = ""


@dataclass
class FuzzyMatchResult:
    """Result of a fuzzy match."""

    matched: bool
    score: float
    pattern_used: str | None = None


def normalize_text(value: str) -> str:
    """Normalize text for matching.

    Applies: unidecode, lowercase, strip punctuation, normalize whitespace.
    """
    # Normalize: strip accents (é→e, ü→u), lowercase
    normalized = unidecode(value).lower().strip()
    # Remove punctuation (hyphens become spaces)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    # Clean up whitespace
    normalized = " ".join(normalized.split())
    return normalized


def match_event_name(
    stream_text: str,
    event_name: str,
    threshold: float = 75.0,
) -> FuzzyMatchResult:
    """Match stream text against event display name.

    Uses token_set_ratio for order-independent matching.
    "Seattle U vs Portland" matches "Portland Pilots vs Seattle Redhawks"

    Args:
        stream_text: Normalized stream text
        event_name: Event display name (e.g., "Home Team vs Away Team")
        threshold: Minimum score to consider a match (0-100)

    Returns:
        FuzzyMatchResult with match status and score
    """
    stream_normalized = normalize_text(stream_text)
    event_normalized = normalize_text(event_name)

    score = fuzz.token_set_ratio(stream_normalized, event_normalized)

    return FuzzyMatchResult(
        matched=score >= threshold,
        score=score,
        pattern_used=event_name,
    )


class FuzzyMatcher:
    """Fuzzy string matcher for team/event names.

    Uses rapidfuzz for fast matching with configurable thresholds.
    """

    # Thresholds for whole-name matching
    HIGH_CONFIDENCE_THRESHOLD = 85.0  # High confidence match
    ACCEPT_WITH_DATE_THRESHOLD = 75.0  # Accept only if date/time validates

    def __init__(
        self,
        threshold: float = 85.0,
        partial_threshold: float = 90.0,
    ):
        """Initialize matcher.

        Args:
            threshold: Minimum score for full string match (0-100)
            partial_threshold: Minimum score for partial/token match (0-100)
        """
        self.threshold = threshold
        self.partial_threshold = partial_threshold

    def generate_team_patterns(self, team: "Team") -> list[TeamPattern]:  # noqa: UP037
        """Generate searchable patterns for a team.

        Simplified: just creates patterns for alias checking.
        Returns patterns in priority order (most specific first).

        Args:
            team: Team object to generate patterns for

        Returns:
            List of TeamPattern objects
        """

        patterns: list[TeamPattern] = []
        seen: set[str] = set()

        def add(value: str | None, source: str) -> None:
            if value:
                normalized = normalize_text(value)
                if normalized and normalized not in seen and len(normalized) >= 2:
                    seen.add(normalized)
                    patterns.append(TeamPattern(normalized, source))

        # 1. Full name: "Boston Celtics"
        if team.name:
            add(team.name, source="full_name")

        # 2. Short name: "Celtics" or "Florida Atlantic"
        if team.short_name:
            add(team.short_name, source="short_name")

        # 3. Abbreviation: "BOS", "CHI"
        if team.abbreviation:
            add(team.abbreviation, source="abbreviation")

        return patterns

    def match_event_name(
        self,
        stream_text: str,
        event_name: str,
        threshold: float | None = None,
    ) -> FuzzyMatchResult:
        """Match stream text against event display name.

        Uses token_set_ratio for order-independent matching.

        Args:
            stream_text: Normalized stream text
            event_name: Event display name (e.g., "Home Team vs Away Team")
            threshold: Override threshold (uses self.threshold if None)

        Returns:
            FuzzyMatchResult with match status and score
        """
        if threshold is None:
            threshold = self.ACCEPT_WITH_DATE_THRESHOLD

        return match_event_name(stream_text, event_name, threshold)

    def _expand_abbreviations(self, text: str) -> str:
        """Expand known abbreviations in text for better matching.

        E.g., "UFC FN Prelims" -> "UFC Fight Night Prelims"
        """
        result = text.lower()

        # Sort by length descending to match longer abbreviations first
        # (e.g., "ufc fn" before "fn")
        for abbrev in sorted(ABBREVIATIONS.keys(), key=len, reverse=True):
            expansion = ABBREVIATIONS[abbrev]
            # Use word boundaries to avoid partial matches
            pattern = r"\b" + re.escape(abbrev) + r"\b"
            result = re.sub(pattern, expansion, result, flags=re.IGNORECASE)

        return result

    def best_match(
        self,
        pattern: str,
        candidates: list[str],
    ) -> tuple[str | None, float]:
        """Find the best matching candidate for a pattern.

        Args:
            pattern: Pattern to match
            candidates: List of candidate strings

        Returns:
            Tuple of (best_match, score) or (None, 0) if no match
        """
        best_candidate = None
        best_score = 0.0

        pattern_lower = pattern.lower()

        for candidate in candidates:
            candidate_lower = candidate.lower()

            # Try different scoring methods, take the best
            scores = [
                fuzz.ratio(pattern_lower, candidate_lower),
                fuzz.token_set_ratio(pattern_lower, candidate_lower),
                fuzz.partial_ratio(pattern_lower, candidate_lower),
            ]
            score = max(scores)

            if score > best_score:
                best_score = score
                best_candidate = candidate

        if best_score >= self.threshold:
            return best_candidate, best_score

        return None, 0.0


# Default singleton for convenience
_default_matcher: FuzzyMatcher | None = None


def get_matcher() -> FuzzyMatcher:
    """Get the default FuzzyMatcher instance."""
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = FuzzyMatcher()
    return _default_matcher
