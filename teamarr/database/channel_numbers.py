"""Channel numbering and range management.

Handles automatic channel number assignment with two modes:
- MANUAL: User sets channel_start, sequential assignment from there
- AUTO: Dynamic allocation based on sort_order and stream counts

Features:
- Range reservation: Groups reserve space for total stream count
- Range validation: Auto-reassign if channel is out of range
- Global range settings: channel_range_start/end in settings
- 100-block intervals: New manual groups start at x01 boundaries
- 10-block packing: Auto groups pack in 10-channel blocks
"""

import logging
from sqlite3 import Connection

logger = logging.getLogger(__name__)

MAX_CHANNEL = 9999


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


def get_next_channel_number(
    conn: Connection,
    group_id: int,
    auto_assign: bool = True,
) -> int | None:
    """Get the next available channel number for a group.

    For MANUAL groups: Uses the group's channel_start_number and finds next unused.
    For AUTO groups: Calculates effective start based on sort_order and preceding groups.

    Args:
        conn: Database connection
        group_id: The event group ID
        auto_assign: If True, auto-assign channel_start when missing (MANUAL mode only)

    Returns:
        Next available channel number, or None if disabled/would exceed max
    """
    cursor = conn.execute(
        """SELECT channel_start_number, channel_assignment_mode, sort_order, total_stream_count
           FROM event_epg_groups WHERE id = ?""",
        (group_id,),
    )
    group = cursor.fetchone()
    if not group:
        return None

    channel_start = group["channel_start_number"]
    assignment_mode = group["channel_assignment_mode"] or "manual"

    # For AUTO mode, calculate effective channel_start dynamically
    block_end = None
    if assignment_mode == "auto":
        channel_start = _calculate_auto_channel_start(conn, group_id, group["sort_order"])
        if not channel_start:
            logger.warning(f"Could not calculate auto channel_start for group {group_id}")
            return None
        # Calculate block_end based on stream count
        stream_count = group["total_stream_count"] or 0
        blocks_needed = (stream_count + 9) // 10 if stream_count > 0 else 1
        range_size = blocks_needed * 10
        block_end = channel_start + range_size - 1

    # For MANUAL mode with no channel_start, auto-assign if enabled
    elif not channel_start and auto_assign:
        channel_start = _get_next_available_range_start(conn)
        if channel_start:
            conn.execute(
                "UPDATE event_epg_groups SET channel_start_number = ? WHERE id = ?",
                (channel_start, group_id),
            )
            conn.commit()
            logger.info(f"Auto-assigned channel_start {channel_start} to MANUAL group {group_id}")
        else:
            logger.warning(f"Could not auto-assign channel_start for group {group_id}")

    if not channel_start:
        return None

    # Get all active channel numbers for this group
    used_rows = conn.execute(
        """SELECT channel_number FROM managed_channels
           WHERE event_epg_group_id = ? AND deleted_at IS NULL
           ORDER BY channel_number""",
        (group_id,),
    ).fetchall()
    used_set = {row["channel_number"] for row in used_rows if row["channel_number"]}

    # Find the first available number starting from channel_start
    next_num = channel_start
    while next_num in used_set:
        next_num += 1

    # For AUTO mode, enforce block_end limit
    if block_end and next_num > block_end:
        logger.warning(f"Group {group_id} AUTO range exhausted ({channel_start}-{block_end})")
        return None

    # Check global max
    if next_num > MAX_CHANNEL:
        logger.warning(f"Channel number {next_num} exceeds max {MAX_CHANNEL}")
        return None

    return next_num


def _calculate_auto_channel_start(
    conn: Connection,
    group_id: int,
    sort_order: int,
) -> int | None:
    """Calculate effective channel_start for an AUTO group based on sort_order.

    AUTO groups are allocated channel blocks in 10-channel increments.
    Each group starts based on how many blocks preceding groups need.

    Example with range_start=9001:
    - Group 1 (16 streams): 9001 (needs 2 blocks of 10)
    - Group 2 (20 streams): 9021 (needs 2 blocks of 10)
    - Group 3 (250 streams): 9041 (needs 25 blocks of 10)

    Returns:
        Calculated channel_start, or None if range exhausted
    """
    range_start, range_end = get_global_channel_range(conn)
    effective_end = range_end if range_end else MAX_CHANNEL

    # Get all AUTO groups sorted by sort_order, excluding child groups
    auto_groups = conn.execute(
        """SELECT id, sort_order, total_stream_count
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
            # This is our group - return current_start
            if current_start > effective_end:
                logger.warning(
                    f"AUTO group {group_id} would start at {current_start}, "
                    f"exceeds range end {effective_end}"
                )
                return None
            return current_start

        # Calculate blocks needed for this preceding group
        stream_count = grp["total_stream_count"] or 0
        blocks_needed = (stream_count + 9) // 10 if stream_count > 0 else 1
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
        logger.warning(f"No available channel range (would start at {next_ten})")
        return None

    return next_ten


def get_group_channel_range(
    conn: Connection,
    group_id: int,
) -> tuple[int | None, int | None]:
    """Get the effective channel range for a group.

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
        start = _calculate_auto_channel_start(conn, group_id, group["sort_order"])
        if not start:
            return None, None
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
        logger.warning(f"Could not reassign channel {channel_id} - no available numbers")
        return None

    conn.execute(
        "UPDATE managed_channels SET channel_number = ? WHERE id = ?",
        (new_number, channel_id),
    )
    conn.commit()

    range_start, range_end = get_group_channel_range(conn, group_id)
    logger.info(
        f"Reassigned channel {channel_id}: {current_number} -> {new_number} "
        f"(group range {range_start}-{range_end})"
    )

    return new_number
