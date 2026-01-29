"""Channel sort priorities CRUD operations.

Manages the channel_sort_priorities table for global channel sorting.
Used when channel_sorting_scope is 'global' to determine channel order
across all AUTO event groups by sport and league.
"""

import logging
from dataclasses import dataclass
from sqlite3 import Connection

logger = logging.getLogger(__name__)


@dataclass
class SortPriority:
    """A sport/league sort priority entry."""

    id: int
    sport: str
    league_code: str | None  # None = sport-level priority only
    sort_priority: int
    created_at: str | None = None
    updated_at: str | None = None


def get_all_sort_priorities(conn: Connection) -> list[SortPriority]:
    """Get all sort priority entries ordered by priority.

    Args:
        conn: Database connection

    Returns:
        List of SortPriority objects ordered by sort_priority (ascending)
    """
    cursor = conn.execute("""
        SELECT id, sport, league_code, sort_priority, created_at, updated_at
        FROM channel_sort_priorities
        ORDER BY sort_priority ASC
    """)

    return [
        SortPriority(
            id=row["id"],
            sport=row["sport"],
            league_code=row["league_code"],
            sort_priority=row["sort_priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in cursor.fetchall()
    ]


def get_active_sort_priorities(conn: Connection) -> list[SortPriority]:
    """Get sort priorities only for sports/leagues with active AUTO groups.

    This filters to only include entries that are relevant to current
    channel numbering - sports/leagues that have enabled AUTO groups.

    Args:
        conn: Database connection

    Returns:
        List of SortPriority objects for active leagues, ordered by sort_priority
    """
    # Get unique sport/league combinations from enabled AUTO groups
    # The leagues column is a JSON array like ["nfl", "nba"]
    cursor = conn.execute("""
        SELECT DISTINCT l.sport, l.league_code
        FROM event_epg_groups g, json_each(g.leagues) AS je
        JOIN leagues l ON je.value = l.league_code
        WHERE g.channel_assignment_mode = 'auto'
          AND g.enabled = 1
    """)
    active_leagues = {(row["sport"], row["league_code"]) for row in cursor.fetchall()}
    active_sports = {sport for sport, _ in active_leagues}

    if not active_leagues:
        return []

    # Get all priorities
    all_priorities = get_all_sort_priorities(conn)

    # Filter to active entries
    # Include sport-level entries (league_code IS NULL) if sport has any active leagues
    # Include league-level entries if that specific league is active
    return [
        p
        for p in all_priorities
        if (p.league_code is None and p.sport in active_sports)
        or (p.league_code is not None and (p.sport, p.league_code) in active_leagues)
    ]


def get_sort_priority(
    conn: Connection, sport: str, league_code: str | None = None
) -> SortPriority | None:
    """Get a specific sort priority entry.

    Args:
        conn: Database connection
        sport: Sport code (e.g., 'football', 'basketball')
        league_code: League code or None for sport-level priority

    Returns:
        SortPriority if found, None otherwise
    """
    if league_code is None:
        cursor = conn.execute(
            """
            SELECT id, sport, league_code, sort_priority, created_at, updated_at
            FROM channel_sort_priorities
            WHERE sport = ? AND league_code IS NULL
            """,
            (sport,),
        )
    else:
        cursor = conn.execute(
            """
            SELECT id, sport, league_code, sort_priority, created_at, updated_at
            FROM channel_sort_priorities
            WHERE sport = ? AND league_code = ?
            """,
            (sport, league_code),
        )

    row = cursor.fetchone()
    if not row:
        return None

    return SortPriority(
        id=row["id"],
        sport=row["sport"],
        league_code=row["league_code"],
        sort_priority=row["sort_priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_sort_priority(
    conn: Connection, sport: str, league_code: str | None, priority: int
) -> bool:
    """Insert or update a sort priority entry.

    Args:
        conn: Database connection
        sport: Sport code (e.g., 'football', 'basketball')
        league_code: League code or None for sport-level priority
        priority: Sort priority value (lower = earlier in channel list)

    Returns:
        True if inserted/updated successfully
    """
    try:
        conn.execute(
            """
            INSERT INTO channel_sort_priorities (sport, league_code, sort_priority)
            VALUES (?, ?, ?)
            ON CONFLICT(sport, league_code) DO UPDATE SET
                sort_priority = excluded.sort_priority,
                updated_at = CURRENT_TIMESTAMP
            """,
            (sport, league_code, priority),
        )
        logger.debug(
            "[SORT_PRIORITY] Upserted: sport=%s, league=%s, priority=%d",
            sport,
            league_code,
            priority,
        )
        return True
    except Exception as e:
        logger.error("[SORT_PRIORITY] Failed to upsert: %s", e)
        return False


def delete_sort_priority(conn: Connection, sport: str, league_code: str | None = None) -> bool:
    """Delete a sort priority entry.

    Args:
        conn: Database connection
        sport: Sport code
        league_code: League code or None for sport-level priority

    Returns:
        True if deleted (or didn't exist)
    """
    try:
        if league_code is None:
            cursor = conn.execute(
                "DELETE FROM channel_sort_priorities WHERE sport = ? AND league_code IS NULL",
                (sport,),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM channel_sort_priorities WHERE sport = ? AND league_code = ?",
                (sport, league_code),
            )

        if cursor.rowcount > 0:
            logger.info("[SORT_PRIORITY] Deleted: sport=%s, league=%s", sport, league_code)
        return True
    except Exception as e:
        logger.error("[SORT_PRIORITY] Failed to delete: %s", e)
        return False


def reorder_sort_priorities(conn: Connection, ordered_list: list[dict]) -> bool:
    """Bulk reorder sort priorities based on UI drag-drop.

    Args:
        conn: Database connection
        ordered_list: List of dicts with 'sport', 'league_code' (optional), 'priority'
                      Example: [
                          {'sport': 'football', 'league_code': None, 'priority': 0},
                          {'sport': 'football', 'league_code': 'nfl', 'priority': 1},
                          {'sport': 'basketball', 'league_code': None, 'priority': 100},
                      ]

    Returns:
        True if all updates succeeded
    """
    try:
        for item in ordered_list:
            sport = item["sport"]
            league_code = item.get("league_code")
            priority = item["priority"]

            if league_code is None:
                conn.execute(
                    """
                    UPDATE channel_sort_priorities
                    SET sort_priority = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE sport = ? AND league_code IS NULL
                    """,
                    (priority, sport),
                )
            else:
                conn.execute(
                    """
                    UPDATE channel_sort_priorities
                    SET sort_priority = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE sport = ? AND league_code = ?
                    """,
                    (priority, sport, league_code),
                )

        logger.info("[SORT_PRIORITY] Reordered %d entries", len(ordered_list))
        return True
    except Exception as e:
        logger.error("[SORT_PRIORITY] Failed to reorder: %s", e)
        return False


def auto_populate_sort_priorities(conn: Connection) -> int:
    """Populate sort priorities from active AUTO groups.

    Scans all enabled AUTO event groups and creates sort priority entries
    for any sport/league combinations that don't already have entries.
    New entries are added alphabetically at the end.

    Uses league_cache (discovered leagues from ESPN) as primary source,
    falling back to the predefined leagues table. This allows access to
    all 200+ ESPN soccer leagues, not just the ~44 predefined ones.

    Args:
        conn: Database connection

    Returns:
        Count of new entries added
    """
    # Get unique sport/league combinations from enabled AUTO groups
    # The leagues column is a JSON array like ["nfl", "nba"]
    # Prioritize leagues table (curated) first, then league_cache (discovered)
    # This ensures predefined leagues appear before discovered ones
    cursor = conn.execute("""
        SELECT DISTINCT
            COALESCE(l.sport, lc.sport) as sport,
            je.value as league_code,
            CASE WHEN l.league_code IS NOT NULL THEN 0 ELSE 1 END as is_discovered
        FROM event_epg_groups g, json_each(g.leagues) AS je
        LEFT JOIN leagues l ON je.value = l.league_code
        LEFT JOIN league_cache lc ON je.value = lc.league_slug
        WHERE g.channel_assignment_mode = 'auto'
          AND g.enabled = 1
          AND COALESCE(l.sport, lc.sport) IS NOT NULL
        ORDER BY is_discovered, COALESCE(l.sport, lc.sport), je.value
    """)
    active_leagues = cursor.fetchall()

    if not active_leagues:
        logger.info("[SORT_PRIORITY] No active AUTO groups found")
        return 0

    # Get existing priorities
    existing = {(p.sport, p.league_code) for p in get_all_sort_priorities(conn)}

    # Get max priority to append new entries at end
    cursor = conn.execute(
        "SELECT COALESCE(MAX(sort_priority), -1) as max_pri FROM channel_sort_priorities"
    )
    max_priority = cursor.fetchone()["max_pri"]

    # Group leagues by sport
    sports_leagues: dict[str, list[str]] = {}
    for row in active_leagues:
        sport = row["sport"]
        league = row["league_code"]
        if sport not in sports_leagues:
            sports_leagues[sport] = []
        sports_leagues[sport].append(league)

    added = 0
    current_priority = max_priority + 1

    # Add missing entries (alphabetical by sport, then leagues within sport)
    for sport in sorted(sports_leagues.keys()):
        # Add sport-level entry if missing
        if (sport, None) not in existing:
            upsert_sort_priority(conn, sport, None, current_priority)
            current_priority += 1
            added += 1

        # Add league entries for this sport
        for league in sorted(sports_leagues[sport]):
            if (sport, league) not in existing:
                upsert_sort_priority(conn, sport, league, current_priority)
                current_priority += 1
                added += 1

    if added > 0:
        logger.info("[SORT_PRIORITY] Auto-populated %d new entries", added)

    return added


def get_sort_priorities_with_channel_counts(
    conn: Connection,
) -> list[dict]:
    """Get sort priorities with channel counts for UI display.

    Returns sort priorities enriched with:
    - display_name: Human-readable sport/league name
    - channel_count: Number of active channels using this sport/league

    Args:
        conn: Database connection

    Returns:
        List of dicts with priority info + display data
    """
    from teamarr.core.sports import get_sport_display_names_from_db

    priorities = get_all_sort_priorities(conn)

    # Get sport display names
    sport_names = get_sport_display_names_from_db(conn)

    # Get league display names
    cursor = conn.execute("SELECT league_code, display_name FROM leagues")
    league_names = {row["league_code"]: row["display_name"] for row in cursor.fetchall()}

    # Get channel counts per sport/league
    cursor = conn.execute("""
        SELECT mc.sport, mc.league, COUNT(*) as count
        FROM managed_channels mc
        JOIN event_epg_groups g ON mc.event_epg_group_id = g.id
        WHERE g.channel_assignment_mode = 'auto'
          AND g.enabled = 1
          AND mc.deleted_at IS NULL
        GROUP BY mc.sport, mc.league
    """)
    channel_counts = {(row["sport"], row["league"]): row["count"] for row in cursor.fetchall()}

    # Also get sport-level counts
    sport_channel_counts: dict[str, int] = {}
    for (sport, _), count in channel_counts.items():
        sport_channel_counts[sport] = sport_channel_counts.get(sport, 0) + count

    result = []
    for p in priorities:
        if p.league_code is None:
            # Sport-level entry
            display_name = sport_names.get(p.sport, p.sport.title())
            count = sport_channel_counts.get(p.sport, 0)
        else:
            # League-level entry
            display_name = league_names.get(p.league_code, p.league_code.upper())
            count = channel_counts.get((p.sport, p.league_code), 0)

        result.append(
            {
                "id": p.id,
                "sport": p.sport,
                "league_code": p.league_code,
                "sort_priority": p.sort_priority,
                "display_name": display_name,
                "channel_count": count,
            }
        )

    return result
