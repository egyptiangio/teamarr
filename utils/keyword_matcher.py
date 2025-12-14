"""
Keyword Matcher Module

Provides keyword matching for consolidation exception handling.
Matches stream names against user-defined keywords to determine
how duplicate streams should be handled.

Keywords are stored in the database and are fully user-editable.
Default language keywords are seeded on first install but can be
modified or deleted by the user.
"""

from typing import Optional, Tuple, List, Dict


def get_all_exception_keywords() -> List[Dict]:
    """
    Get all exception keywords from the database.

    Returns:
        List of keyword dicts with 'keywords' and 'behavior' keys
    """
    from database import get_consolidation_exception_keywords
    return get_consolidation_exception_keywords()


def check_exception_keyword(stream_name: str, keywords_list: List[Dict]) -> Tuple[Optional[str], Optional[str]]:
    """
    Check if stream matches any exception keyword.

    Keywords are matched case-insensitively as substrings within the stream name.
    The first keyword variant is used as the "canonical" name for grouping
    when behavior is 'consolidate'.

    Args:
        stream_name: The stream name to check (e.g., "NFL: Chiefs vs Raiders (ManningCast)")
        keywords_list: List of keyword dicts from get_consolidation_exception_keywords().
                      Each dict has 'keywords' (comma-separated variants) and 'behavior'.

    Returns:
        Tuple of (canonical_keyword, behavior) if match found.
        (None, None) if no match.

    Examples:
        >>> keywords = [{'keywords': 'Prime Vision, Primevision', 'behavior': 'separate'}]
        >>> check_exception_keyword('NFL: Chiefs vs Raiders (Prime Vision)', keywords)
        ('prime vision', 'separate')

        >>> keywords = [{'keywords': 'ManningCast, Manning Cast', 'behavior': 'consolidate'}]
        >>> check_exception_keyword('NFL: Chiefs vs Raiders (ManningCast)', keywords)
        ('manningcast', 'consolidate')

        >>> check_exception_keyword('NFL: Chiefs vs Raiders', keywords)
        (None, None)
    """
    if not stream_name or not keywords_list:
        return (None, None)

    stream_lower = stream_name.lower()

    for entry in keywords_list:
        keywords_str = entry.get('keywords', '')
        behavior = entry.get('behavior', 'consolidate')

        # Split comma-separated keywords, normalize
        variants = [k.strip().lower() for k in keywords_str.split(',') if k.strip()]

        for variant in variants:
            if variant in stream_lower:
                # Return first variant as canonical (for grouping)
                canonical = variants[0]
                return (canonical, behavior)

    return (None, None)


def normalize_keyword(keyword: str) -> str:
    """
    Normalize a keyword for consistent matching and storage.

    Args:
        keyword: The keyword to normalize

    Returns:
        Lowercase, stripped keyword
    """
    return keyword.strip().lower() if keyword else ''


def parse_keywords_string(keywords_str: str) -> List[str]:
    """
    Parse a comma-separated keywords string into a list of normalized keywords.

    Args:
        keywords_str: Comma-separated keyword variants (e.g., "Prime Vision, Primevision")

    Returns:
        List of normalized keyword strings
    """
    if not keywords_str:
        return []
    return [k.strip().lower() for k in keywords_str.split(',') if k.strip()]


def get_canonical_keyword(keywords_str: str) -> Optional[str]:
    """
    Get the canonical (first) keyword from a comma-separated string.

    Args:
        keywords_str: Comma-separated keyword variants

    Returns:
        First keyword (normalized), or None if empty
    """
    variants = parse_keywords_string(keywords_str)
    return variants[0] if variants else None


def strip_exception_keywords(stream_name: str, keywords_list: List[Dict] = None) -> Tuple[str, Optional[str]]:
    """
    Strip exception keywords from stream name before team matching.

    This should be called early in the matching pipeline to remove language
    indicators and other exception keywords that could interfere with team
    extraction. The matched keyword is returned for later use in consolidation.

    Strips keywords that appear:
    - As prefixes: "En Español: Chiefs vs Raiders" -> "Chiefs vs Raiders"
    - In parentheses: "Chiefs vs Raiders (ESP)" -> "Chiefs vs Raiders"
    - Inline with separators: "Spanish - Chiefs vs Raiders" -> "Chiefs vs Raiders"

    Args:
        stream_name: Raw stream name
        keywords_list: Optional list of keyword dicts. If None, loads from database.

    Returns:
        Tuple of (cleaned_stream_name, matched_canonical_keyword)
        matched_canonical_keyword is None if no keyword was stripped
    """
    import re

    if not stream_name:
        return (stream_name, None)

    if keywords_list is None:
        keywords_list = get_all_exception_keywords()

    if not keywords_list:
        return (stream_name, None)

    # Build list of all keyword variants for pattern matching
    # Sort by length (longest first) to match most specific first
    all_variants = []
    variant_to_canonical = {}

    for entry in keywords_list:
        keywords_str = entry.get('keywords', '')
        variants = [k.strip() for k in keywords_str.split(',') if k.strip()]
        if variants:
            canonical = variants[0].lower()
            for v in variants:
                all_variants.append(v)
                variant_to_canonical[v.lower()] = canonical

    # Sort by length (longest first) for greedy matching
    all_variants.sort(key=len, reverse=True)

    cleaned = stream_name
    matched_canonical = None

    for variant in all_variants:
        # Escape regex special chars in variant
        escaped = re.escape(variant)

        # Check if variant is already parenthesized like "(ESP)"
        is_parenthesized = variant.startswith('(') and variant.endswith(')')

        patterns = []

        # Pattern 1: Prefix with separator (colon, dash, pipe)
        # "En Español: Chiefs vs Raiders" or "Spanish - NFL Game"
        patterns.append(rf'^{escaped}\s*[-:|]\s*')

        if is_parenthesized:
            # Variant already has parens - match it directly anywhere
            # "(ESP)" in "NFL (ESP): Chiefs vs Raiders" or "(FRA) Soccer: PSG vs Lyon"
            patterns.append(rf'\s*{escaped}\s*[-:|]?\s*')
        else:
            # Wrap in parens for parenthesized matching
            # "Chiefs vs Raiders (Spanish)" or "(French) NHL: Bruins vs Rangers"
            patterns.append(rf'\s*\({escaped}\)\s*')
            patterns.append(rf'^\({escaped}\)\s*[-:|]?\s*')

        # Suffix with separator
        # "Chiefs vs Raiders - Spanish"
        patterns.append(rf'\s*[-:|]\s*{escaped}\s*$')

        for pattern in patterns:
            new_cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
            if new_cleaned != cleaned:
                matched_canonical = variant_to_canonical.get(variant.lower())
                cleaned = new_cleaned.strip()

    return (cleaned, matched_canonical)
