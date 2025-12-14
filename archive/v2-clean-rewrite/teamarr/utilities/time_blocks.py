"""6-hour time block utilities for filler alignment.

Filler programmes align to 6-hour boundaries: 0000, 0600, 1200, 1800.
This provides consistent EPG structure (4 blocks per day).
"""

from datetime import datetime, timedelta

# Time block boundaries (hours)
TIME_BLOCK_HOURS = [0, 6, 12, 18]


def get_next_time_block(dt: datetime) -> datetime:
    """Get the next 6-hour time block boundary.

    Boundaries are at 0000, 0600, 1200, 1800.

    Args:
        dt: Current datetime

    Returns:
        Datetime of next block boundary

    Example:
        2:00 PM → 6:00 PM (1800)
        7:00 PM → 12:00 AM next day (0000)
    """
    current_hour = dt.hour

    # Find next block boundary
    for block_hour in TIME_BLOCK_HOURS:
        if current_hour < block_hour:
            return dt.replace(hour=block_hour, minute=0, second=0, microsecond=0)

    # No more blocks today, return first block of next day (midnight)
    next_day = dt + timedelta(days=1)
    return next_day.replace(hour=0, minute=0, second=0, microsecond=0)


def get_previous_time_block(dt: datetime) -> datetime:
    """Get the previous 6-hour time block boundary.

    Args:
        dt: Current datetime

    Returns:
        Datetime of previous block boundary

    Example:
        2:00 PM → 12:00 PM (1200)
        5:00 AM → 12:00 AM same day (0000)
    """
    current_hour = dt.hour

    # Find previous block boundary (iterate in reverse)
    for block_hour in reversed(TIME_BLOCK_HOURS):
        if current_hour >= block_hour:
            return dt.replace(hour=block_hour, minute=0, second=0, microsecond=0)

    # Before first block, return last block of previous day (1800)
    prev_day = dt - timedelta(days=1)
    return prev_day.replace(hour=18, minute=0, second=0, microsecond=0)


def create_filler_chunks(
    start_dt: datetime, end_dt: datetime
) -> list[tuple[datetime, datetime]]:
    """Create filler time chunks aligned to 6-hour boundaries.

    Splits a time range into chunks that align with block boundaries.

    Args:
        start_dt: Start of filler period
        end_dt: End of filler period

    Returns:
        List of (chunk_start, chunk_end) tuples

    Example:
        2:00 PM to 10:00 PM becomes:
        - (2:00 PM, 6:00 PM)
        - (6:00 PM, 10:00 PM)
    """
    if start_dt >= end_dt:
        return []

    chunks = []
    current_start = start_dt

    while current_start < end_dt:
        # Find next block boundary
        next_block = get_next_time_block(current_start)

        # Don't go past end_dt
        chunk_end = min(next_block, end_dt)

        chunks.append((current_start, chunk_end))
        current_start = chunk_end

    return chunks


def get_block_for_time(dt: datetime) -> int:
    """Get which 6-hour block a datetime falls into.

    Args:
        dt: Datetime to check

    Returns:
        Block number (0-3):
        - 0: 00:00-05:59
        - 1: 06:00-11:59
        - 2: 12:00-17:59
        - 3: 18:00-23:59
    """
    hour = dt.hour
    if hour < 6:
        return 0
    elif hour < 12:
        return 1
    elif hour < 18:
        return 2
    else:
        return 3


def crosses_midnight(start_dt: datetime, end_dt: datetime) -> bool:
    """Check if a time range crosses midnight.

    Args:
        start_dt: Start datetime
        end_dt: End datetime

    Returns:
        True if the range spans across midnight
    """
    return start_dt.date() != end_dt.date()
