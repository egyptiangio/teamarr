"""Database operations for AI-learned patterns."""

import json
import logging
from datetime import datetime, timezone
from sqlite3 import Connection

logger = logging.getLogger(__name__)


def init_ai_tables(conn: Connection) -> None:
    """Create AI-related tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_patterns (
            pattern_id TEXT PRIMARY KEY,
            regex TEXT NOT NULL,
            description TEXT,
            example_streams TEXT,  -- JSON array
            field_map TEXT,        -- JSON object
            confidence REAL DEFAULT 0.5,
            match_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            group_id INTEGER,      -- Optional: which event group this pattern is for
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE SET NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_patterns_group
        ON ai_patterns(group_id)
    """)

    conn.commit()


def save_pattern(
    conn: Connection,
    pattern_id: str,
    regex: str,
    description: str,
    example_streams: list[str],
    field_map: dict[str, str],
    confidence: float,
    group_id: int | None = None,
) -> None:
    """Save or update a learned pattern."""
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO ai_patterns (
            pattern_id, regex, description, example_streams, field_map,
            confidence, group_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pattern_id) DO UPDATE SET
            regex = excluded.regex,
            description = excluded.description,
            example_streams = excluded.example_streams,
            field_map = excluded.field_map,
            confidence = excluded.confidence,
            group_id = excluded.group_id,
            updated_at = excluded.updated_at
    """, (
        pattern_id,
        regex,
        description,
        json.dumps(example_streams),
        json.dumps(field_map),
        confidence,
        group_id,
        now,
        now,
    ))
    conn.commit()


def get_patterns_for_group(conn: Connection, group_id: int) -> list[dict]:
    """Get all patterns for a specific group."""
    cursor = conn.execute("""
        SELECT pattern_id, regex, description, example_streams, field_map,
               confidence, match_count, fail_count
        FROM ai_patterns
        WHERE group_id = ?
        ORDER BY confidence DESC, match_count DESC
    """, (group_id,))

    patterns = []
    for row in cursor:
        patterns.append({
            "pattern_id": row[0],
            "regex": row[1],
            "description": row[2],
            "example_streams": json.loads(row[3]) if row[3] else [],
            "field_map": json.loads(row[4]) if row[4] else {},
            "confidence": row[5],
            "match_count": row[6],
            "fail_count": row[7],
        })

    return patterns


def get_all_patterns(conn: Connection) -> list[dict]:
    """Get all learned patterns."""
    cursor = conn.execute("""
        SELECT pattern_id, regex, description, example_streams, field_map,
               confidence, match_count, fail_count, group_id
        FROM ai_patterns
        ORDER BY confidence DESC, match_count DESC
    """)

    patterns = []
    for row in cursor:
        patterns.append({
            "pattern_id": row[0],
            "regex": row[1],
            "description": row[2],
            "example_streams": json.loads(row[3]) if row[3] else [],
            "field_map": json.loads(row[4]) if row[4] else {},
            "confidence": row[5],
            "match_count": row[6],
            "fail_count": row[7],
            "group_id": row[8],
        })

    return patterns


def update_pattern_stats(
    conn: Connection,
    pattern_id: str,
    matched: bool,
) -> None:
    """Update match/fail counts for a pattern."""
    if matched:
        conn.execute("""
            UPDATE ai_patterns
            SET match_count = match_count + 1, updated_at = ?
            WHERE pattern_id = ?
        """, (datetime.now(timezone.utc).isoformat(), pattern_id))
    else:
        conn.execute("""
            UPDATE ai_patterns
            SET fail_count = fail_count + 1, updated_at = ?
            WHERE pattern_id = ?
        """, (datetime.now(timezone.utc).isoformat(), pattern_id))
    conn.commit()


def delete_pattern(conn: Connection, pattern_id: str) -> None:
    """Delete a pattern."""
    conn.execute("DELETE FROM ai_patterns WHERE pattern_id = ?", (pattern_id,))
    conn.commit()


def delete_patterns_for_group(conn: Connection, group_id: int) -> int:
    """Delete all patterns for a group. Returns count deleted."""
    cursor = conn.execute(
        "DELETE FROM ai_patterns WHERE group_id = ?",
        (group_id,)
    )
    conn.commit()
    return cursor.rowcount
