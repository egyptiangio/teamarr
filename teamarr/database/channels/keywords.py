"""Exception keywords operations.

Read-only access to consolidation_exception_keywords for lifecycle service.
Full CRUD is in database/exception_keywords.py.
"""

from sqlite3 import Connection

from teamarr.database.exception_keywords import ExceptionKeyword, get_all_keywords


def get_exception_keywords(conn: Connection, enabled_only: bool = True) -> list[ExceptionKeyword]:
    """Get all consolidation exception keywords.

    Args:
        conn: Database connection
        enabled_only: Only return enabled keywords

    Returns:
        List of ExceptionKeyword objects
    """
    return get_all_keywords(conn, include_disabled=not enabled_only)


def check_exception_keyword(
    stream_name: str,
    keywords: list[ExceptionKeyword],
) -> tuple[str | None, str | None]:
    """Check if stream name matches any exception keyword.

    Args:
        stream_name: Stream name to check
        keywords: List of ExceptionKeyword objects

    Returns:
        Tuple of (matched_keyword, behavior) or (None, None) if no match
    """
    stream_lower = stream_name.lower()

    for kw in keywords:
        for variant in kw.keyword_list:  # Use property instead of direct list
            if variant.lower() in stream_lower:
                return (variant, kw.behavior)

    return (None, None)
