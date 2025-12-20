"""Database operations for consolidation exception keywords.

Provides CRUD operations for the consolidation_exception_keywords table.
Exception keywords control how duplicate streams are handled during event matching.
"""

from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection
from typing import Literal

ExceptionBehavior = Literal["consolidate", "separate", "ignore"]


@dataclass
class ExceptionKeyword:
    """Consolidation exception keyword configuration."""

    id: int | None = None
    keywords: str = ""  # Comma-separated keywords
    behavior: ExceptionBehavior = "consolidate"
    display_name: str | None = None
    enabled: bool = True
    created_at: datetime | None = None

    @property
    def keyword_list(self) -> list[str]:
        """Get keywords as a list."""
        return [k.strip() for k in self.keywords.split(",") if k.strip()]


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
        keywords=row["keywords"] or "",
        behavior=row["behavior"] or "consolidate",
        display_name=row["display_name"],
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
        cursor = conn.execute(
            "SELECT * FROM consolidation_exception_keywords ORDER BY display_name"
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM consolidation_exception_keywords
               WHERE enabled = 1 ORDER BY display_name"""
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
           ORDER BY display_name""",
        (behavior,),
    )
    return [_row_to_keyword(row) for row in cursor.fetchall()]


def get_all_keyword_patterns(conn: Connection) -> list[str]:
    """Get all enabled keyword patterns as a flat list.

    Useful for matching stream names against exception keywords.

    Args:
        conn: Database connection

    Returns:
        List of individual keyword strings (lowercased)
    """
    keywords = get_all_keywords(conn, include_disabled=False)
    patterns = []
    for kw in keywords:
        patterns.extend([k.lower() for k in kw.keyword_list])
    return patterns


# =============================================================================
# CREATE OPERATIONS
# =============================================================================


def create_keyword(
    conn: Connection,
    keywords: str,
    behavior: ExceptionBehavior = "consolidate",
    display_name: str | None = None,
    enabled: bool = True,
) -> int:
    """Create a new exception keyword entry.

    Args:
        conn: Database connection
        keywords: Comma-separated keyword variants
        behavior: How to handle matched streams
        display_name: Display name for UI
        enabled: Whether the keyword is active

    Returns:
        New keyword ID
    """
    cursor = conn.execute(
        """INSERT INTO consolidation_exception_keywords
           (keywords, behavior, display_name, enabled)
           VALUES (?, ?, ?, ?)""",
        (keywords, behavior, display_name, int(enabled)),
    )
    conn.commit()
    return cursor.lastrowid


# =============================================================================
# UPDATE OPERATIONS
# =============================================================================


def update_keyword(
    conn: Connection,
    keyword_id: int,
    keywords: str | None = None,
    behavior: ExceptionBehavior | None = None,
    display_name: str | None = None,
    enabled: bool | None = None,
    clear_display_name: bool = False,
) -> bool:
    """Update an exception keyword.

    Only updates fields that are explicitly provided (not None).

    Args:
        conn: Database connection
        keyword_id: Keyword ID to update
        keywords: New keywords string
        behavior: New behavior
        display_name: New display name
        enabled: New enabled status
        clear_display_name: Set display_name to NULL

    Returns:
        True if updated
    """
    updates = []
    values = []

    if keywords is not None:
        updates.append("keywords = ?")
        values.append(keywords)

    if behavior is not None:
        updates.append("behavior = ?")
        values.append(behavior)

    if display_name is not None:
        updates.append("display_name = ?")
        values.append(display_name)
    elif clear_display_name:
        updates.append("display_name = NULL")

    if enabled is not None:
        updates.append("enabled = ?")
        values.append(int(enabled))

    if not updates:
        return False

    values.append(keyword_id)
    query = f"UPDATE consolidation_exception_keywords SET {', '.join(updates)} WHERE id = ?"
    cursor = conn.execute(query, values)
    conn.commit()
    return cursor.rowcount > 0


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
    return cursor.rowcount > 0


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
    return cursor.rowcount > 0
