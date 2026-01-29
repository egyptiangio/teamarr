"""Database operations for consolidation exception keywords.

Provides CRUD operations for the consolidation_exception_keywords table.
Exception keywords control how duplicate streams are handled during event matching.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection
from typing import Literal

logger = logging.getLogger(__name__)

ExceptionBehavior = Literal["consolidate", "separate", "ignore"]


@dataclass
class ExceptionKeyword:
    """Consolidation exception keyword configuration.

    Attributes:
        label: Primary identifier used in channel names and {exception_keyword} template variable
        match_terms: Comma-separated phrases/words to match in stream names
        behavior: How to handle matched streams (consolidate/separate/ignore)
    """

    id: int | None = None
    label: str = ""  # Used for channel naming and {exception_keyword} variable
    match_terms: str = ""  # Comma-separated terms to match
    behavior: ExceptionBehavior = "consolidate"
    enabled: bool = True
    created_at: datetime | None = None

    @property
    def match_term_list(self) -> list[str]:
        """Get match terms as a list."""
        return [k.strip() for k in self.match_terms.split(",") if k.strip()]


def _row_to_keyword(row) -> ExceptionKeyword:
    """Convert a database row to ExceptionKeyword."""
    created_at = None
    if row["created_at"]:
        try:
            created_at = datetime.fromisoformat(row["created_at"])
        except (ValueError, TypeError):
            pass

    return ExceptionKeyword(
        id=row["id"],
        label=row["label"] or "",
        match_terms=row["match_terms"] or "",
        behavior=row["behavior"] or "consolidate",
        enabled=bool(row["enabled"]),
        created_at=created_at,
    )


# =============================================================================
# READ OPERATIONS
# =============================================================================


def get_all_keywords(conn: Connection, include_disabled: bool = False) -> list[ExceptionKeyword]:
    """Get all exception keywords.

    Args:
        conn: Database connection
        include_disabled: Include disabled keywords

    Returns:
        List of ExceptionKeyword objects
    """
    if include_disabled:
        cursor = conn.execute("SELECT * FROM consolidation_exception_keywords ORDER BY label")
    else:
        cursor = conn.execute(
            """SELECT * FROM consolidation_exception_keywords
               WHERE enabled = 1 ORDER BY label"""
        )

    return [_row_to_keyword(row) for row in cursor.fetchall()]


def get_keyword(conn: Connection, keyword_id: int) -> ExceptionKeyword | None:
    """Get a single exception keyword by ID.

    Args:
        conn: Database connection
        keyword_id: Keyword ID

    Returns:
        ExceptionKeyword or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM consolidation_exception_keywords WHERE id = ?", (keyword_id,)
    )
    row = cursor.fetchone()
    return _row_to_keyword(row) if row else None


def get_keywords_by_behavior(
    conn: Connection, behavior: ExceptionBehavior
) -> list[ExceptionKeyword]:
    """Get all enabled keywords with a specific behavior.

    Args:
        conn: Database connection
        behavior: Behavior type to filter by

    Returns:
        List of ExceptionKeyword objects
    """
    cursor = conn.execute(
        """SELECT * FROM consolidation_exception_keywords
           WHERE behavior = ? AND enabled = 1
           ORDER BY label""",
        (behavior,),
    )
    return [_row_to_keyword(row) for row in cursor.fetchall()]


def get_all_keyword_patterns(conn: Connection) -> list[str]:
    """Get all enabled keyword patterns as a flat list.

    Useful for matching stream names against exception keywords.

    Args:
        conn: Database connection

    Returns:
        List of individual match term strings (lowercased)
    """
    keywords = get_all_keywords(conn, include_disabled=False)
    patterns = []
    for kw in keywords:
        patterns.extend([k.lower() for k in kw.match_term_list])
    return patterns


# =============================================================================
# CREATE OPERATIONS
# =============================================================================


def create_keyword(
    conn: Connection,
    label: str,
    match_terms: str,
    behavior: ExceptionBehavior = "consolidate",
    enabled: bool = True,
) -> int:
    """Create a new exception keyword entry.

    Args:
        conn: Database connection
        label: Label for channel naming and {exception_keyword} variable
        match_terms: Comma-separated terms to match in stream names
        behavior: How to handle matched streams
        enabled: Whether the keyword is active

    Returns:
        New keyword ID
    """
    cursor = conn.execute(
        """INSERT INTO consolidation_exception_keywords
           (label, match_terms, behavior, enabled)
           VALUES (?, ?, ?, ?)""",
        (label, match_terms, behavior, int(enabled)),
    )
    conn.commit()
    keyword_id = cursor.lastrowid
    logger.info("[CREATED] Exception keyword id=%d label=%s", keyword_id, label)
    return keyword_id


# =============================================================================
# UPDATE OPERATIONS
# =============================================================================


def update_keyword(
    conn: Connection,
    keyword_id: int,
    label: str | None = None,
    match_terms: str | None = None,
    behavior: ExceptionBehavior | None = None,
    enabled: bool | None = None,
) -> bool:
    """Update an exception keyword.

    Only updates fields that are explicitly provided (not None).

    Args:
        conn: Database connection
        keyword_id: Keyword ID to update
        label: New label for channel naming
        match_terms: New match terms string
        behavior: New behavior
        enabled: New enabled status

    Returns:
        True if updated
    """
    updates = []
    values = []

    if label is not None:
        updates.append("label = ?")
        values.append(label)

    if match_terms is not None:
        updates.append("match_terms = ?")
        values.append(match_terms)

    if behavior is not None:
        updates.append("behavior = ?")
        values.append(behavior)

    if enabled is not None:
        updates.append("enabled = ?")
        values.append(int(enabled))

    if not updates:
        return False

    values.append(keyword_id)
    query = f"UPDATE consolidation_exception_keywords SET {', '.join(updates)} WHERE id = ?"
    cursor = conn.execute(query, values)
    conn.commit()
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Exception keyword id=%d", keyword_id)
        return True
    return False


def set_keyword_enabled(conn: Connection, keyword_id: int, enabled: bool) -> bool:
    """Enable or disable an exception keyword.

    Args:
        conn: Database connection
        keyword_id: Keyword ID
        enabled: New enabled status

    Returns:
        True if updated
    """
    cursor = conn.execute(
        "UPDATE consolidation_exception_keywords SET enabled = ? WHERE id = ?",
        (int(enabled), keyword_id),
    )
    conn.commit()
    if cursor.rowcount > 0:
        logger.info("[UPDATED] Exception keyword id=%d enabled=%s", keyword_id, enabled)
        return True
    return False


# =============================================================================
# DELETE OPERATIONS
# =============================================================================


def delete_keyword(conn: Connection, keyword_id: int) -> bool:
    """Delete an exception keyword.

    Args:
        conn: Database connection
        keyword_id: Keyword ID to delete

    Returns:
        True if deleted
    """
    cursor = conn.execute(
        "DELETE FROM consolidation_exception_keywords WHERE id = ?", (keyword_id,)
    )
    conn.commit()
    if cursor.rowcount > 0:
        logger.info("[DELETED] Exception keyword id=%d", keyword_id)
        return True
    return False
