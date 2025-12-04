"""
Keyword Matcher Module

Provides keyword matching for consolidation exception handling.
Matches stream names against user-defined keywords to determine
how duplicate streams should be handled.
"""

from typing import Optional, Tuple, List, Dict


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
