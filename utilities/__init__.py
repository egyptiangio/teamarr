"""Utilities for Teamarr V2."""

from utilities.fuzzy_match import (
    ABBREVIATIONS,
    MASCOT_WORDS,
    FuzzyMatcher,
    MatchResult,
    get_matcher,
)

__all__ = [
    "ABBREVIATIONS",
    "MASCOT_WORDS",
    "FuzzyMatcher",
    "MatchResult",
    "get_matcher",
]
