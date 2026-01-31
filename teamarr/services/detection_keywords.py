"""Detection keyword service for stream classification.

Loads detection patterns from both:
1. Built-in constants (defaults)
2. User-defined keywords from database (override/extend)

User keywords are merged with built-in patterns:
- Higher priority user keywords come first
- User keywords can add new patterns or override defaults
- Disabled user keywords are skipped

Provides an abstraction layer so classifier uses the service,
and the service handles where patterns come from.
"""

import json
import logging
import re
from re import Pattern
from typing import ClassVar

from teamarr.utilities.constants import (
    CARD_SEGMENT_PATTERNS,
    COMBAT_SPORTS_EXCLUDE_PATTERNS,
    COMBAT_SPORTS_KEYWORDS,
    GAME_SEPARATORS,
    LEAGUE_HINT_PATTERNS,
    PLACEHOLDER_PATTERNS,
    SPORT_HINT_PATTERNS,
)

logger = logging.getLogger(__name__)


def _load_user_keywords(category: str) -> list[dict]:
    """Load user-defined keywords from database.

    Args:
        category: Keyword category to load

    Returns:
        List of keyword dicts with keys: keyword, is_regex, target_value, priority
    """
    try:
        from teamarr.database import get_db

        with get_db() as conn:
            rows = conn.execute(
                """SELECT keyword, is_regex, target_value, priority
                   FROM detection_keywords
                   WHERE category = ? AND enabled = 1
                   ORDER BY priority DESC, keyword""",
                (category,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        # DB not available (tests, early startup) - return empty
        logger.debug("[DETECT_SVC] Could not load user keywords for %s: %s", category, e)
        return []


class DetectionKeywordService:
    """Service for loading and caching detection patterns.

    All methods are class methods to allow stateless usage.
    Patterns are compiled and cached on first access.

    Phase 1: Reads from constants.py
    Phase 2: Will read from database with constants as fallback
    """

    # Class-level caches for compiled patterns
    _combat_keywords: ClassVar[list[str] | None] = None
    _league_hints: ClassVar[list[tuple[Pattern[str], str | list[str]]] | None] = None
    _sport_hints: ClassVar[list[tuple[Pattern[str], str]] | None] = None
    _placeholder_patterns: ClassVar[list[Pattern[str]] | None] = None
    _card_segment_patterns: ClassVar[list[tuple[Pattern[str], str]] | None] = None
    _exclusion_patterns: ClassVar[list[Pattern[str]] | None] = None
    _separators: ClassVar[list[str] | None] = None

    # ==========================================================================
    # Pattern Accessors
    # ==========================================================================

    @classmethod
    def get_combat_keywords(cls) -> list[str]:
        """Get keywords that indicate combat sports (EVENT_CARD category).

        Merges built-in keywords with user-defined keywords from database.
        User keywords with higher priority come first.

        Returns:
            List of lowercase keywords (e.g., ['ufc', 'bellator', 'main card', ...])
        """
        if cls._combat_keywords is None:
            # Start with built-in keywords
            built_in = set(COMBAT_SPORTS_KEYWORDS)

            # Add user-defined keywords (higher priority come first)
            user_keywords = _load_user_keywords("combat_sports")
            user_kw_list = [kw["keyword"].lower() for kw in user_keywords]

            # Merge: user keywords first (by priority), then built-in not in user list
            cls._combat_keywords = user_kw_list + [
                kw for kw in COMBAT_SPORTS_KEYWORDS if kw.lower() not in user_kw_list
            ]
            logger.debug(
                "[DETECT_SVC] Loaded %d combat sports keywords (%d user, %d built-in)",
                len(cls._combat_keywords),
                len(user_kw_list),
                len(built_in),
            )
        return cls._combat_keywords

    @classmethod
    def get_league_hints(cls) -> list[tuple[Pattern[str], str | list[str]]]:
        """Get compiled league hint patterns.

        Merges built-in patterns with user-defined patterns from database.
        User patterns with higher priority come first.

        Returns:
            List of (compiled_pattern, league_code) tuples.
            league_code can be a string or list[str] for umbrella brands.
        """
        if cls._league_hints is None:
            cls._league_hints = []
            user_count = 0

            # Load user-defined patterns first (higher priority)
            user_keywords = _load_user_keywords("league_hints")
            for kw in user_keywords:
                pattern_str = kw["keyword"]
                target = kw["target_value"] or ""
                # target_value may be JSON array for umbrella brands
                try:
                    code: str | list[str] = json.loads(target)
                except (json.JSONDecodeError, TypeError):
                    code = target

                try:
                    if kw["is_regex"]:
                        compiled = re.compile(pattern_str, re.IGNORECASE)
                    else:
                        # Plain text - escape for literal match
                        compiled = re.compile(re.escape(pattern_str), re.IGNORECASE)
                    cls._league_hints.append((compiled, code))
                    user_count += 1
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid user league hint '%s': %s",
                        pattern_str,
                        e,
                    )

            # Add built-in patterns
            for pattern_str, code in LEAGUE_HINT_PATTERNS:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE)
                    cls._league_hints.append((compiled, code))
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid league hint pattern '%s': %s",
                        pattern_str,
                        e,
                    )
            logger.debug(
                "[DETECT_SVC] Compiled %d league hint patterns (%d user)",
                len(cls._league_hints),
                user_count,
            )
        return cls._league_hints

    @classmethod
    def get_sport_hints(cls) -> list[tuple[Pattern[str], str]]:
        """Get compiled sport hint patterns.

        Merges built-in patterns with user-defined patterns from database.
        User patterns with higher priority come first.

        Returns:
            List of (compiled_pattern, sport_name) tuples.
        """
        if cls._sport_hints is None:
            cls._sport_hints = []
            user_count = 0

            # Load user-defined patterns first (higher priority)
            user_keywords = _load_user_keywords("sport_hints")
            for kw in user_keywords:
                pattern_str = kw["keyword"]
                sport = kw["target_value"] or ""
                try:
                    if kw["is_regex"]:
                        compiled = re.compile(pattern_str, re.IGNORECASE)
                    else:
                        compiled = re.compile(re.escape(pattern_str), re.IGNORECASE)
                    cls._sport_hints.append((compiled, sport))
                    user_count += 1
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid user sport hint '%s': %s",
                        pattern_str,
                        e,
                    )

            # Add built-in patterns
            for pattern_str, sport in SPORT_HINT_PATTERNS:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE)
                    cls._sport_hints.append((compiled, sport))
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid sport hint pattern '%s': %s",
                        pattern_str,
                        e,
                    )
            logger.debug(
                "[DETECT_SVC] Compiled %d sport hint patterns (%d user)",
                len(cls._sport_hints),
                user_count,
            )
        return cls._sport_hints

    @classmethod
    def get_placeholder_patterns(cls) -> list[Pattern[str]]:
        """Get compiled placeholder patterns.

        Merges built-in patterns with user-defined patterns from database.
        User patterns with higher priority come first.

        Returns:
            List of compiled regex patterns that identify placeholder streams.
        """
        if cls._placeholder_patterns is None:
            cls._placeholder_patterns = []
            user_count = 0

            # Load user-defined patterns first (higher priority)
            user_keywords = _load_user_keywords("placeholders")
            for kw in user_keywords:
                pattern_str = kw["keyword"]
                try:
                    if kw["is_regex"]:
                        compiled = re.compile(pattern_str, re.IGNORECASE)
                    else:
                        compiled = re.compile(re.escape(pattern_str), re.IGNORECASE)
                    cls._placeholder_patterns.append(compiled)
                    user_count += 1
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid user placeholder pattern '%s': %s",
                        pattern_str,
                        e,
                    )

            # Add built-in patterns
            for pattern_str in PLACEHOLDER_PATTERNS:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE)
                    cls._placeholder_patterns.append(compiled)
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid placeholder pattern '%s': %s",
                        pattern_str,
                        e,
                    )
            logger.debug(
                "[DETECT_SVC] Compiled %d placeholder patterns (%d user)",
                len(cls._placeholder_patterns),
                user_count,
            )
        return cls._placeholder_patterns

    @classmethod
    def get_card_segment_patterns(cls) -> list[tuple[Pattern[str], str]]:
        """Get compiled card segment patterns.

        Merges built-in patterns with user-defined patterns from database.
        User patterns with higher priority come first.

        Returns:
            List of (compiled_pattern, segment_name) tuples.
            segment_name is one of: 'early_prelims', 'prelims', 'main_card', 'combined'
        """
        if cls._card_segment_patterns is None:
            cls._card_segment_patterns = []
            user_count = 0

            # Load user-defined patterns first (higher priority)
            user_keywords = _load_user_keywords("card_segments")
            for kw in user_keywords:
                pattern_str = kw["keyword"]
                segment = kw["target_value"] or "combined"
                try:
                    if kw["is_regex"]:
                        compiled = re.compile(pattern_str, re.IGNORECASE)
                    else:
                        compiled = re.compile(re.escape(pattern_str), re.IGNORECASE)
                    cls._card_segment_patterns.append((compiled, segment))
                    user_count += 1
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid user card segment pattern '%s': %s",
                        pattern_str,
                        e,
                    )

            # Add built-in patterns
            for pattern_str, segment in CARD_SEGMENT_PATTERNS:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE)
                    cls._card_segment_patterns.append((compiled, segment))
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid card segment pattern '%s': %s",
                        pattern_str,
                        e,
                    )
            logger.debug(
                "[DETECT_SVC] Compiled %d card segment patterns (%d user)",
                len(cls._card_segment_patterns),
                user_count,
            )
        return cls._card_segment_patterns

    @classmethod
    def get_exclusion_patterns(cls) -> list[Pattern[str]]:
        """Get compiled combat sports exclusion patterns.

        Merges built-in patterns with user-defined patterns from database.
        User patterns with higher priority come first.

        Returns:
            List of compiled patterns for content to exclude (weigh-ins, etc.)
        """
        if cls._exclusion_patterns is None:
            cls._exclusion_patterns = []
            user_count = 0

            # Load user-defined patterns first (higher priority)
            user_keywords = _load_user_keywords("exclusions")
            for kw in user_keywords:
                pattern_str = kw["keyword"]
                try:
                    if kw["is_regex"]:
                        compiled = re.compile(pattern_str, re.IGNORECASE)
                    else:
                        compiled = re.compile(re.escape(pattern_str), re.IGNORECASE)
                    cls._exclusion_patterns.append(compiled)
                    user_count += 1
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid user exclusion pattern '%s': %s",
                        pattern_str,
                        e,
                    )

            # Add built-in patterns
            for pattern_str in COMBAT_SPORTS_EXCLUDE_PATTERNS:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE)
                    cls._exclusion_patterns.append(compiled)
                except re.error as e:
                    logger.warning(
                        "[DETECT_SVC] Invalid exclusion pattern '%s': %s",
                        pattern_str,
                        e,
                    )
            logger.debug(
                "[DETECT_SVC] Compiled %d exclusion patterns (%d user)",
                len(cls._exclusion_patterns),
                user_count,
            )
        return cls._exclusion_patterns

    @classmethod
    def get_separators(cls) -> list[str]:
        """Get game separator strings.

        Merges built-in separators with user-defined separators from database.
        User separators with higher priority come first.

        Returns:
            List of separators like ' vs ', ' @ ', ' at ', etc.
        """
        if cls._separators is None:
            # Load user-defined separators first (higher priority)
            user_keywords = _load_user_keywords("separators")
            user_seps = [kw["keyword"] for kw in user_keywords]

            # Merge: user separators first, then built-in not in user list
            user_seps_lower = {s.lower() for s in user_seps}
            cls._separators = user_seps + [
                s for s in GAME_SEPARATORS if s.lower() not in user_seps_lower
            ]
            logger.debug(
                "[DETECT_SVC] Loaded %d game separators (%d user)",
                len(cls._separators),
                len(user_seps),
            )
        return cls._separators

    # ==========================================================================
    # Detection Methods
    # ==========================================================================

    # Class-level cache for compiled combat keywords patterns
    _combat_keyword_patterns: ClassVar[list[Pattern[str]] | None] = None

    @classmethod
    def _get_combat_keyword_patterns(cls) -> list[Pattern[str]]:
        """Get compiled word-boundary patterns for combat keywords."""
        if cls._combat_keyword_patterns is None:
            cls._combat_keyword_patterns = []
            for keyword in cls.get_combat_keywords():
                # Use word boundaries to avoid matching 'wbo' in 'Cowboys'
                pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
                cls._combat_keyword_patterns.append(pattern)
        return cls._combat_keyword_patterns

    @classmethod
    def is_combat_sport(cls, text: str) -> bool:
        """Check if text contains combat sports keywords.

        Uses word boundary matching to avoid false positives like
        'wbo' matching within 'Cowboys'.

        Args:
            text: Stream name or text to check

        Returns:
            True if any combat sports keyword is found
        """
        for pattern in cls._get_combat_keyword_patterns():
            if pattern.search(text):
                return True
        return False

    @classmethod
    def detect_league(cls, text: str) -> str | list[str] | None:
        """Detect league code from text.

        Args:
            text: Stream name to check

        Returns:
            League code (str), list of codes for umbrella brands, or None
        """
        for pattern, code in cls.get_league_hints():
            if pattern.search(text):
                return code
        return None

    @classmethod
    def detect_sport(cls, text: str) -> str | None:
        """Detect sport name from text.

        Args:
            text: Stream name to check

        Returns:
            Sport name (e.g., 'Hockey', 'Soccer') or None
        """
        for pattern, sport in cls.get_sport_hints():
            if pattern.search(text):
                return sport
        return None

    @classmethod
    def is_placeholder(cls, text: str) -> bool:
        """Check if text matches placeholder patterns.

        Args:
            text: Stream name to check

        Returns:
            True if stream appears to be a placeholder/filler
        """
        for pattern in cls.get_placeholder_patterns():
            if pattern.search(text):
                return True
        return False

    @classmethod
    def detect_card_segment(cls, text: str) -> str | None:
        """Detect card segment from combat sports stream name.

        Args:
            text: Stream name to check

        Returns:
            Segment name ('early_prelims', 'prelims', 'main_card', 'combined') or None
        """
        for pattern, segment in cls.get_card_segment_patterns():
            if pattern.search(text):
                return segment
        return None

    @classmethod
    def is_excluded(cls, text: str) -> bool:
        """Check if text should be excluded from matching.

        Args:
            text: Stream name to check

        Returns:
            True if stream matches exclusion patterns (weigh-ins, press conferences, etc.)
        """
        for pattern in cls.get_exclusion_patterns():
            if pattern.search(text):
                return True
        return False

    @classmethod
    def find_separator(cls, text: str) -> tuple[str | None, int]:
        """Find game separator in text.

        Args:
            text: Stream name to search

        Returns:
            Tuple of (separator_found, position) or (None, -1) if not found
        """
        text_lower = text.lower()
        for sep in cls.get_separators():
            pos = text_lower.find(sep.lower())
            if pos != -1:
                return sep, pos
        return None, -1

    # ==========================================================================
    # Cache Management
    # ==========================================================================

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear all cached patterns.

        Call this after updating patterns in the database (Phase 2)
        or when constants change during testing.
        """
        cls._combat_keywords = None
        cls._combat_keyword_patterns = None
        cls._league_hints = None
        cls._sport_hints = None
        cls._placeholder_patterns = None
        cls._card_segment_patterns = None
        cls._exclusion_patterns = None
        cls._separators = None
        logger.info("[DETECT_SVC] Pattern cache invalidated")

    @classmethod
    def warm_cache(cls) -> dict[str, int]:
        """Pre-compile all patterns and return stats.

        Returns:
            Dict with counts of loaded patterns by category
        """
        return {
            "combat_keywords": len(cls.get_combat_keywords()),
            "league_hints": len(cls.get_league_hints()),
            "sport_hints": len(cls.get_sport_hints()),
            "placeholder_patterns": len(cls.get_placeholder_patterns()),
            "card_segment_patterns": len(cls.get_card_segment_patterns()),
            "exclusion_patterns": len(cls.get_exclusion_patterns()),
            "separators": len(cls.get_separators()),
        }
