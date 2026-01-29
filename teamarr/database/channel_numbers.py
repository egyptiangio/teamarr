"""Channel numbering and range management.

Handles automatic channel number assignment with three numbering modes:
- strict_block: Reserve by total_stream_count (large gaps, minimal drift)
- rational_block: Reserve by actual channel count rounded to 10 (smaller gaps, low drift)
- strict_compact: No reservation, sequential numbers (no gaps, higher drift risk)

Assignment modes (per-group):
- MANUAL: User sets channel_start, sequential assignment from there
- AUTO: Dynamic allocation based on sort_order and numbering mode

Features:
- Range reservation: Groups reserve space based on numbering mode
- Range validation: Auto-reassign if channel is out of range
- Global range settings: channel_range_start/end in settings
- 100-block intervals: New manual groups start at x01 boundaries
- 10-block packing: Auto groups pack in 10-channel blocks (strict/rational)
"""

import logging
from sqlite3 import Connection

logger = logging.getLogger(__name__)

MAX_CHANNEL = 999999  # Effectively no limit per Dispatcharr update

# Valid numbering modes
NUMBERING_MODES = ("strict_block", "rational_block", "strict_compact")


def get_global_channel_range(conn: Connection) -> tuple[int, int | None]:
    """Get global channel range settings.

    Returns:
        Tuple of (range_start, range_end). range_end may be None (no limit).
    """
    cursor = conn.execute(
        "SELECT channel_range_start, channel_range_end FROM settings WHERE id = 1"
    )
    row = cursor.fetchone()
    if not row:
        return 101, None
    return row["channel_range_start"] or 101, row["channel_range_end"]


def get_channel_numbering_mode(conn: Connection) -> str:
    """Get the current channel numbering mode from settings.

    Returns:
        One of: 'strict_block', 'rational_block', 'strict_compact'
    """
    cursor = conn.execute("SELECT channel_numbering_mode FROM settings WHERE id = 1")
    row = cursor.fetchone()
    if not row or not row["channel_numbering_mode"]:
        return "strict_block"
    mode = row["channel_numbering_mode"]
    return mode if mode in NUMBERING_MODES else "strict_block"


def get_next_channel_number(
    conn: Connection,
    group_id: int,
    auto_assign: bool = True,
) -> int | None:
    """Get the next available channel number for a group.

    For MANUAL groups: Uses the group's channel_start_number and finds next unused.
    For AUTO groups: Calculates effective start based on sort_order, numbering mode,
                     and preceding groups.

    Numbering modes (for AUTO groups):
    - strict_block: Reserve by total_stream_count (large gaps, minimal drift)
    - rational_block: Reserve by actual channel count (smaller gaps, low drift)
    - strict_compact: No block reservation (no gaps, higher drift risk)

    Args:
        conn: Database connection
        group_id: The event group ID
        auto_assign: If True, auto-assign channel_start when missing (MANUAL mode only)

    Returns:
        Next available channel number, or None if disabled/would exceed max
    """
    cursor = conn.execute(
        """SELECT channel_start_number, channel_assignment_mode, sort_order
           FROM event_epg_groups WHERE id = ?""",
        (group_id,),
    )
    group = cursor.fetchone()
    if not group:
        return None

    channel_start = group["channel_start_number"]
    assignment_mode = group["channel_assignment_mode"] or "manual"

    # Get the numbering mode for AUTO groups
    numbering_mode = get_channel_numbering_mode(conn)

    # For AUTO mode, calculate effective channel_start dynamically
    block_end = None
    if assignment_mode == "auto":
        # For strict_compact mode, use global allocation across all AUTO groups
        if numbering_mode == "strict_compact":
            # Strict compact: find next available globally across all AUTO groups
            # Skip the per-group logic entirely - return the globally available number
            return get_next_compact_channel_number(conn)

        # strict_block or rational_block: use block-based calculation
        channel_start = _calculate_auto_channel_start(
            conn, group_id, group["sort_order"], numbering_mode
        )

        if not channel_start:
            logger.warning(
                "[CHANNEL_NUM] Could not calculate auto channel_start for group %d (mode=%s)",
                group_id,
                numbering_mode,
            )
            return None

        # Calculate block_end by finding where the next group starts
        # This allows dynamic expansion as a group adds more channels
        block_end = _calculate_auto_block_end(conn, group_id, group["sort_order"], channel_start)

    # For MANUAL mode with no channel_start, auto-assign if enabled
    elif not channel_start and auto_assign:
        channel_start = _get_next_available_range_start(conn)
        if channel_start:
            conn.execute(
                "UPDATE event_epg_groups SET channel_start_number = ? WHERE id = ?",
                (channel_start, group_id),
            )
            conn.commit()
            logger.info(
                "[CHANNEL_NUM] Auto-assigned channel_start %d to MANUAL group %d",
                channel_start,
                group_id,
            )
        else:
            logger.warning(
                "[CHANNEL_NUM] Could not auto-assign channel_start for group %d", group_id
            )

    if not channel_start:
        return None

    # Get all active channel numbers for this group
    # Note: channel_number may be stored as TEXT or INTEGER, so cast to int
    used_rows = conn.execute(
        """SELECT channel_number FROM managed_channels
           WHERE event_epg_group_id = ? AND deleted_at IS NULL
           ORDER BY channel_number""",
        (group_id,),
    ).fetchall()
    used_set = set()
    for row in used_rows:
        if row["channel_number"]:
            try:
                used_set.add(int(float(row["channel_number"])))
            except (ValueError, TypeError):
                pass  # Skip invalid channel numbers

    # Find the first available number starting from channel_start
    next_num = channel_start
    while next_num in used_set:
        next_num += 1

    # For AUTO mode with block reservation, enforce block_end limit
    if block_end and next_num > block_end:
        logger.warning(
            "[CHANNEL_NUM] Group %d AUTO range exhausted (%d-%d, mode=%s)",
            group_id,
            channel_start,
            block_end,
            numbering_mode,
        )
        return None

    # Check global max
    if next_num > MAX_CHANNEL:
        logger.warning("[CHANNEL_NUM] Channel number %d exceeds max %d", next_num, MAX_CHANNEL)
        return None

    return next_num


def _calculate_strict_compact_start(conn: Connection, group_id: int) -> int | None:
    """Calculate channel_start for strict_compact mode.

    In strict_compact mode, there's no block reservation. All AUTO groups
    share the same channel pool starting at range_start. Channels are
    assigned sequentially with no gaps between groups.

    The "start" for any group is the global range_start. The actual
    next available number is calculated by checking ALL AUTO channels
    globally (handled in get_next_channel_number with use_global_pool=True).

    Returns:
        The global range start
    """
    range_start, _ = get_global_channel_range(conn)
    return range_start


def _get_all_auto_used_channels(conn: Connection) -> set[int]:
    """Get all channel numbers used by enabled AUTO groups.

    Used by strict_compact mode to find globally available channels
    across all AUTO groups (no per-group block reservation).

    Returns:
        Set of all used channel numbers across AUTO groups
    """
    cursor = conn.execute(
        """SELECT mc.channel_number
           FROM managed_channels mc
           JOIN event_epg_groups g ON mc.event_epg_group_id = g.id
           WHERE g.channel_assignment_mode = 'auto'
             AND g.enabled = 1
             AND mc.deleted_at IS NULL"""
    )

    used_set = set()
    for row in cursor.fetchall():
        if row["channel_number"]:
            try:
                used_set.add(int(float(row["channel_number"])))
            except (ValueError, TypeError):
                pass
    return used_set


def get_next_compact_channel_number(conn: Connection) -> int | None:
    """Get the next available channel number in strict_compact mode.

    Finds the first unused channel number starting from range_start,
    considering ALL channels across ALL enabled AUTO groups.

    This is the core allocation function for strict_compact mode.

    Returns:
        Next available channel number, or None if range exhausted
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get all used channels across all AUTO groups
    used_set = _get_all_auto_used_channels(conn)

    # Find first available starting from range_start
    next_num = range_start
    while next_num in used_set:
        next_num += 1
        if next_num > effective_end:
            logger.warning(
                "[CHANNEL_NUM] strict_compact: No available channels (range %d-%d exhausted)",
                range_start,
                effective_end,
            )
            return None

    if next_num > MAX_CHANNEL:
        logger.warning("[CHANNEL_NUM] Channel number %d exceeds max %d", next_num, MAX_CHANNEL)
        return None

    return next_num


def _get_actual_channel_count(conn: Connection, group_id: int) -> int:
    """Get the actual count of active (non-deleted) channels for a group."""
    cursor = conn.execute(
        """SELECT COUNT(*) as cnt FROM managed_channels
           WHERE event_epg_group_id = ? AND deleted_at IS NULL""",
        (group_id,),
    )
    row = cursor.fetchone()
    return row["cnt"] if row else 0


def _get_total_stream_count(conn: Connection, group_id: int) -> int:
    """Get the total raw M3U stream count for a group (before filtering).

    This is used for range reservation - we want to reserve space for ALL
    potential streams, not just currently matched ones.
    """
    cursor = conn.execute(
        """SELECT total_stream_count FROM event_epg_groups WHERE id = ?""",
        (group_id,),
    )
    row = cursor.fetchone()
    return row["total_stream_count"] if row and row["total_stream_count"] else 0


def _get_max_channel_number(conn: Connection, group_id: int) -> int | None:
    """Get the maximum channel number currently assigned to a group.

    Returns None if no channels are assigned.
    """
    cursor = conn.execute(
        """SELECT MAX(CAST(channel_number AS INTEGER)) as max_ch
           FROM managed_channels
           WHERE event_epg_group_id = ? AND deleted_at IS NULL""",
        (group_id,),
    )
    row = cursor.fetchone()
    return row["max_ch"] if row and row["max_ch"] else None


def _calculate_auto_block_end(
    conn: Connection,
    group_id: int,
    sort_order: int,
    channel_start: int,
) -> int:
    """Calculate the block_end for an AUTO group.

    Uses the minimum channel number from the NEXT group that has channels.
    This allows groups to grow into empty space between them.

    If no following group has channels, returns the global range end.
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get all AUTO groups sorted by sort_order, excluding child groups
    auto_groups = conn.execute(
        """SELECT id, sort_order
           FROM event_epg_groups
           WHERE channel_assignment_mode = 'auto'
             AND parent_group_id IS NULL
             AND enabled = 1
           ORDER BY sort_order ASC""",
    ).fetchall()

    # Find the first group AFTER us that has actual channels
    found_self = False
    for grp in auto_groups:
        if grp["id"] == group_id:
            found_self = True
            continue

        if found_self:
            # Check if this group has any channels
            next_min = _get_min_channel_number(conn, grp["id"])
            if next_min:
                # Found a following group with channels - our block ends before theirs
                return next_min - 1

    # No following group has channels - use global range end
    return effective_end


def _calculate_blocks_needed(stream_count: int) -> int:
    """Calculate blocks needed for a group based on total stream count.

    Reserves enough space for all potential streams (raw M3U count).
    Uses 10-channel blocks with ceiling division.

    Examples:
        0 streams → 1 block (10 slots) - minimum reservation
        1-10 streams → 1 block (10 slots)
        11-20 streams → 2 blocks (20 slots)
        21-30 streams → 3 blocks (30 slots)
        100 streams → 10 blocks (100 slots)
    """
    if stream_count == 0:
        return 1
    # Ceiling division: (n + 9) // 10 rounds up to nearest 10
    return (stream_count + 9) // 10


def _get_min_channel_number(conn: Connection, group_id: int) -> int | None:
    """Get the minimum channel number currently assigned to a group.

    Returns None if no channels are assigned.
    """
    cursor = conn.execute(
        """SELECT MIN(CAST(channel_number AS INTEGER)) as min_ch
           FROM managed_channels
           WHERE event_epg_group_id = ? AND deleted_at IS NULL""",
        (group_id,),
    )
    row = cursor.fetchone()
    return row["min_ch"] if row and row["min_ch"] else None


def _calculate_auto_channel_start(
    conn: Connection,
    group_id: int,
    sort_order: int,
    numbering_mode: str = "strict_block",
) -> int | None:
    """Calculate effective channel_start for an AUTO group based on sort_order.

    Dispatches to the appropriate calculation based on numbering_mode:
    - strict_block: Uses total_stream_count for block reservation
    - rational_block: Uses actual channel count for block reservation
    - strict_compact: No reservation (handled separately)

    Args:
        conn: Database connection
        group_id: The event group ID
        sort_order: Group's sort_order value
        numbering_mode: One of NUMBERING_MODES

    Returns:
        Calculated channel_start, or None if range exhausted
    """
    if numbering_mode == "rational_block":
        return _calculate_rational_block_start(conn, group_id, sort_order)
    else:
        # Default to strict_block behavior
        return _calculate_strict_block_start(conn, group_id, sort_order)


def _calculate_strict_block_start(
    conn: Connection,
    group_id: int,
    sort_order: int,
) -> int | None:
    """Calculate channel_start using strict block reservation (total_stream_count).

    AUTO groups are allocated channel blocks in 10-channel increments.
    Each group starts based on how many blocks preceding groups need.

    Uses total_stream_count (raw M3U stream count) for reservation.
    This creates larger gaps but minimizes channel drift when streams
    are added/removed.

    Example with range_start=9001:
    - Group 1 (total_stream_count=16): 9001 (needs 2 blocks of 10)
    - Group 2 (total_stream_count=25): 9021 (needs 3 blocks)
    - Group 3 (total_stream_count=250): 9051 (needs 25 blocks)

    Returns:
        Calculated channel_start, or None if range exhausted
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get all AUTO groups sorted by sort_order, excluding child groups
    auto_groups = conn.execute(
        """SELECT id, sort_order
           FROM event_epg_groups
           WHERE channel_assignment_mode = 'auto'
             AND parent_group_id IS NULL
             AND enabled = 1
           ORDER BY sort_order ASC""",
    ).fetchall()

    # Calculate cumulative block usage up to our group
    current_start = range_start
    for grp in auto_groups:
        if grp["id"] == group_id:
            # This is our group
            # Use MIN of calculated start and actual min channel number
            # This prevents range conflicts when preceding groups grow
            min_existing = _get_min_channel_number(conn, group_id)
            if min_existing and min_existing < current_start:
                current_start = min_existing

            if current_start > effective_end:
                logger.warning(
                    "[CHANNEL_NUM] AUTO group %d would start at %d, exceeds range end %d",
                    group_id,
                    current_start,
                    effective_end,
                )
                return None
            return current_start

        # Calculate blocks needed based on total raw stream count
        total_streams = _get_total_stream_count(conn, grp["id"])
        blocks_needed = _calculate_blocks_needed(total_streams)
        current_start += blocks_needed * 10

    # Group not found in AUTO groups
    return None


def _calculate_rational_block_start(
    conn: Connection,
    group_id: int,
    sort_order: int,
) -> int | None:
    """Calculate channel_start using rational block reservation (actual channel count).

    Similar to strict_block but uses ACTUAL channel count instead of
    total_stream_count. This creates smaller gaps while still maintaining
    10-channel block boundaries.

    Block calculation:
    - 0 channels → 1 block (10 slots) - minimum reservation
    - 1-10 channels → 1 block (10 slots)
    - 11-20 channels → 2 blocks (20 slots)

    Example with range_start=9001:
    - Group 1 (8 actual channels): 9001 (needs 1 block of 10)
    - Group 2 (12 actual channels): 9011 (needs 2 blocks)
    - Group 3 (5 actual channels): 9031 (needs 1 block)

    Returns:
        Calculated channel_start, or None if range exhausted
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get all AUTO groups sorted by sort_order, excluding child groups
    auto_groups = conn.execute(
        """SELECT id, sort_order
           FROM event_epg_groups
           WHERE channel_assignment_mode = 'auto'
             AND parent_group_id IS NULL
             AND enabled = 1
           ORDER BY sort_order ASC""",
    ).fetchall()

    # Calculate cumulative block usage up to our group
    current_start = range_start
    for grp in auto_groups:
        if grp["id"] == group_id:
            # This is our group
            # Use MIN of calculated start and actual min channel number
            # This prevents range conflicts when preceding groups grow
            min_existing = _get_min_channel_number(conn, group_id)
            if min_existing and min_existing < current_start:
                current_start = min_existing

            if current_start > effective_end:
                logger.warning(
                    "[CHANNEL_NUM] AUTO group %d would start at %d, exceeds range end %d",
                    group_id,
                    current_start,
                    effective_end,
                )
                return None
            return current_start

        # Calculate blocks needed based on ACTUAL channel count
        actual_count = _get_actual_channel_count(conn, grp["id"])
        blocks_needed = _calculate_blocks_needed(actual_count)
        current_start += blocks_needed * 10

    # Group not found in AUTO groups
    return None


def _get_next_available_range_start(conn: Connection) -> int | None:
    """Get the next available channel range start for a new MANUAL group.

    Uses 10-channel intervals starting at x1 (101, 111, 121, etc.).
    Respects existing group reservations.

    Returns:
        Next available x1 channel start, or None if range exhausted
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get all groups with their reserved ranges
    groups = conn.execute(
        """SELECT channel_start_number, total_stream_count, channel_assignment_mode
           FROM event_epg_groups
           WHERE enabled = 1 AND channel_start_number IS NOT NULL"""
    ).fetchall()

    # Build set of used channel ranges
    used_ranges: list[tuple[int, int]] = []
    for grp in groups:
        start = grp["channel_start_number"]
        count = grp["total_stream_count"] or 10  # Default reservation of 10
        end = start + count - 1
        used_ranges.append((start, end))

    # Sort by start
    used_ranges.sort(key=lambda x: x[0])

    # Find highest used channel
    highest_used = range_start - 1
    for _start, end in used_ranges:
        if end > highest_used:
            highest_used = end

    # Calculate next x1 boundary (10-block intervals: 101, 111, 121, 131...)
    # e.g., if highest_used=105, next is 111; if highest_used=110, next is 111
    next_ten = ((highest_used // 10) + 1) * 10 + 1

    # Make sure it's >= range_start
    if next_ten < range_start:
        next_ten = ((range_start - 1) // 10) * 10 + 1
        if next_ten < range_start:
            next_ten += 10

    if next_ten > effective_end:
        logger.warning("[CHANNEL_NUM] No available channel range (would start at %d)", next_ten)
        return None

    return next_ten


def get_group_channel_range(
    conn: Connection,
    group_id: int,
) -> tuple[int | None, int | None]:
    """Get the effective channel range for a group.

    For AUTO groups, the range depends on the numbering mode:
    - strict_block/rational_block: Reserved block range per group
    - strict_compact: Global shared range (all AUTO groups)

    Returns:
        Tuple of (range_start, range_end) for the group.
        Both may be None if group not configured.
    """
    cursor = conn.execute(
        """SELECT channel_start_number, channel_assignment_mode, sort_order, total_stream_count
           FROM event_epg_groups WHERE id = ?""",
        (group_id,),
    )
    group = cursor.fetchone()
    if not group:
        return None, None

    assignment_mode = group["channel_assignment_mode"] or "manual"
    stream_count = group["total_stream_count"] or 0

    if assignment_mode == "auto":
        numbering_mode = get_channel_numbering_mode(conn)

        # strict_compact: All AUTO groups share the global range
        if numbering_mode == "strict_compact":
            return get_global_channel_range(conn)

        # strict_block or rational_block: Calculate per-group block range
        start = _calculate_auto_channel_start(conn, group_id, group["sort_order"], numbering_mode)
        if not start:
            return None, None

        # Calculate block size based on mode
        if numbering_mode == "rational_block":
            actual_count = _get_actual_channel_count(conn, group_id)
            blocks_needed = _calculate_blocks_needed(actual_count)
        else:
            # strict_block uses total_stream_count
            blocks_needed = (stream_count + 9) // 10 if stream_count > 0 else 1

        end = start + (blocks_needed * 10) - 1
        return start, end
    else:
        # MANUAL mode
        start = group["channel_start_number"]
        if not start:
            return None, None
        # Manual groups don't have a strict end, but we can estimate
        end = start + max(stream_count, 10) - 1
        return start, end


def validate_channel_in_range(
    conn: Connection,
    group_id: int,
    channel_number: int,
) -> bool:
    """Check if a channel number is within the group's valid range.

    Args:
        conn: Database connection
        group_id: The event group ID
        channel_number: The channel number to validate

    Returns:
        True if channel is in valid range, False otherwise
    """
    range_start, range_end = get_group_channel_range(conn, group_id)
    if range_start is None:
        return False

    if channel_number < range_start:
        return False

    if range_end and channel_number > range_end:
        return False

    return True


def reassign_out_of_range_channel(
    conn: Connection,
    group_id: int,
    channel_id: int,
    current_number: int,
) -> int | None:
    """Reassign a channel that's out of range.

    Args:
        conn: Database connection
        group_id: The event group ID
        channel_id: The managed channel ID
        current_number: Current channel number (for logging)

    Returns:
        New channel number if reassigned, None if failed
    """
    new_number = get_next_channel_number(conn, group_id)
    if not new_number:
        logger.warning(
            "[CHANNEL_NUM] Could not reassign channel %d - no available numbers", channel_id
        )
        return None

    conn.execute(
        "UPDATE managed_channels SET channel_number = ? WHERE id = ?",
        (new_number, channel_id),
    )
    conn.commit()

    range_start, range_end = get_group_channel_range(conn, group_id)
    logger.info(
        "[CHANNEL_NUM] Reassigned channel %d: %d -> %d (group range %s-%s)",
        channel_id,
        current_number,
        new_number,
        range_start,
        range_end,
    )

    return new_number


# =============================================================================
# Global Sorting Functions
# =============================================================================


def get_channel_sorting_scope(conn: Connection) -> str:
    """Get the current channel sorting scope from settings.

    Returns:
        One of: 'per_group', 'global'
    """
    cursor = conn.execute("SELECT channel_sorting_scope FROM settings WHERE id = 1")
    row = cursor.fetchone()
    if not row or not row["channel_sorting_scope"]:
        return "per_group"
    scope = row["channel_sorting_scope"]
    return scope if scope in ("per_group", "global") else "per_group"


def get_channel_sort_by(conn: Connection) -> str:
    """Get the current channel sort by setting from settings.

    Returns:
        One of: 'sport_league_time', 'time', 'stream_order'
    """
    cursor = conn.execute("SELECT channel_sort_by FROM settings WHERE id = 1")
    row = cursor.fetchone()
    if not row or not row["channel_sort_by"]:
        return "time"
    sort_by = row["channel_sort_by"]
    valid = ("sport_league_time", "time", "stream_order")
    return sort_by if sort_by in valid else "time"


def get_all_auto_channels_globally_sorted(conn: Connection) -> list[dict]:
    """Get all AUTO group channels sorted by sport/league/time.

    Fetches all active channels from AUTO groups and sorts them according
    to the sort_priorities table and event start times.

    Sort order:
    1. Sport priority (from channel_sort_priorities, lower = earlier)
    2. League priority (from channel_sort_priorities, lower = earlier)
    3. Event start time (earlier = earlier)

    Sports/leagues not in sort_priorities get priority 9999 (sorted last).

    Returns:
        List of channel dicts with sort-relevant fields, ordered globally
    """
    from datetime import datetime

    # Import here to avoid circular import
    from teamarr.database.sort_priorities import get_all_sort_priorities

    # 1. Get sort priorities (normalize to lowercase for case-insensitive matching)
    priorities = get_all_sort_priorities(conn)
    sport_order = {p.sport.lower(): p.sort_priority for p in priorities if p.league_code is None}
    league_order = {
        (p.sport.lower(), p.league_code.lower() if p.league_code else None): p.sort_priority
        for p in priorities
        if p.league_code is not None
    }

    # 2. Get all channels from AUTO groups with event info
    cursor = conn.execute("""
        SELECT
            mc.id,
            mc.dispatcharr_channel_id,
            mc.channel_number,
            mc.channel_name,
            mc.event_epg_group_id,
            mc.primary_stream_id,
            mc.sport,
            mc.league,
            mc.event_date,
            mc.created_at
        FROM managed_channels mc
        JOIN event_epg_groups g ON mc.event_epg_group_id = g.id
        WHERE g.channel_assignment_mode = 'auto'
          AND g.enabled = 1
          AND mc.deleted_at IS NULL
    """)

    channels = []
    for row in cursor.fetchall():
        channels.append(
            {
                "id": row["id"],
                "dispatcharr_channel_id": row["dispatcharr_channel_id"],
                "channel_number": row["channel_number"],
                "channel_name": row["channel_name"],
                "event_epg_group_id": row["event_epg_group_id"],
                "primary_stream_id": row["primary_stream_id"],
                "sport": row["sport"],
                "league": row["league"],
                "event_date": row["event_date"],
                "created_at": row["created_at"],
            }
        )

    # 3. Sort by: sport priority → league priority → event time
    def sort_key(ch):
        sport = ch.get("sport") or ""
        league = ch.get("league") or ""
        event_date_str = ch.get("event_date")

        # Normalize sport/league to lowercase for priority lookup
        # (managed_channels may store title case "Football", sort_priorities uses lowercase "football")  # noqa: E501
        sport_lower = sport.lower()
        league_lower = league.lower()

        # Get priorities (default to 9999 for unknown)
        sport_pri = sport_order.get(sport_lower, 9999)
        league_pri = league_order.get((sport_lower, league_lower), 9999)

        # Parse event date for sorting
        if event_date_str:
            try:
                # Handle various formats
                if "T" in str(event_date_str):
                    event_date = datetime.fromisoformat(str(event_date_str).replace("Z", "+00:00"))
                else:
                    event_date = datetime.strptime(str(event_date_str), "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                event_date = datetime.max
        else:
            event_date = datetime.max

        return (sport_pri, league_pri, event_date, ch.get("channel_name", ""))

    sorted_channels = sorted(channels, key=sort_key)

    logger.debug(
        "[CHANNEL_SORT] Global sort: %d channels, %d sports in priorities, %d leagues",
        len(sorted_channels),
        len(sport_order),
        len(league_order),
    )

    return sorted_channels


def reassign_channels_globally(conn: Connection) -> dict:
    """Reassign channel numbers globally based on sort order.

    Used when switching to strict_compact with global sorting, or when
    user requests a global renumber.

    This function:
    1. Gets all AUTO channels sorted globally
    2. Assigns sequential numbers starting from range_start
    3. Updates channel_number in database
    4. Logs any drift (channels that changed numbers)

    Returns:
        Dict with statistics: channels_processed, channels_moved, drift_details
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get globally sorted channels
    sorted_channels = get_all_auto_channels_globally_sorted(conn)

    if not sorted_channels:
        logger.info("[CHANNEL_SORT] No AUTO channels to reassign globally")
        return {"channels_processed": 0, "channels_moved": 0, "drift_details": []}

    # Track changes
    channels_moved = 0
    drift_details = []

    # Assign sequential numbers
    next_num = range_start
    for ch in sorted_channels:
        old_num = ch["channel_number"]

        # Skip if we'd exceed range
        if next_num > effective_end:
            logger.warning(
                "[CHANNEL_SORT] Global reassign stopped at channel %d - range exhausted",
                ch["id"],
            )
            break

        # Update if number changed
        if old_num != next_num:
            conn.execute(
                "UPDATE managed_channels SET channel_number = ? WHERE id = ?",
                (next_num, ch["id"]),
            )
            drift_details.append(
                {
                    "channel_id": ch["id"],
                    "dispatcharr_channel_id": ch["dispatcharr_channel_id"],
                    "channel_name": ch["channel_name"],
                    "old_number": old_num,
                    "new_number": next_num,
                }
            )
            channels_moved += 1

            # Log drift at debug level (summary is at INFO)
            logger.debug(
                "[CHANNEL_NUM] '%s' moved #%s → #%d", ch["channel_name"], old_num, next_num
            )

        next_num += 1

    logger.info(
        "[CHANNEL_SORT] Global reassign complete: %d channels processed, %d moved",
        len(sorted_channels),
        channels_moved,
    )

    return {
        "channels_processed": len(sorted_channels),
        "channels_moved": channels_moved,
        "drift_details": drift_details,
    }
