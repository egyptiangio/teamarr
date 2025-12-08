"""
Time formatting utilities for consistent time display across the application.

Provides helper functions that respect user's time format preferences:
- 12h vs 24h format
- Show/hide timezone abbreviation

Used by:
- Template engines (EPG descriptions)
- API responses
- Frontend (via settings passed to templates)
"""

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


def format_time(
    dt: datetime,
    time_format: str = '12h',
    show_timezone: bool = True,
    timezone: Optional[str] = None
) -> str:
    """
    Format a datetime's time component using user preferences.

    Args:
        dt: The datetime to format (should be timezone-aware or will be localized)
        time_format: '12h' or '24h'
        show_timezone: Whether to include timezone abbreviation (EST, PST, etc.)
        timezone: Target timezone name (e.g., 'America/Detroit'). If provided,
                  converts dt to this timezone before formatting.

    Returns:
        Formatted time string, e.g., "7:30 PM EST" or "19:30"

    Examples:
        >>> format_time(dt, '12h', True)   # "7:30 PM EST"
        >>> format_time(dt, '12h', False)  # "7:30 PM"
        >>> format_time(dt, '24h', True)   # "19:30 EST"
        >>> format_time(dt, '24h', False)  # "19:30"
    """
    # Convert to target timezone if specified
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            if dt.tzinfo is None:
                # Naive datetime - assume UTC
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            dt = dt.astimezone(tz)
        except Exception:
            pass  # Keep original if conversion fails

    # Build format string based on preferences
    if time_format == '24h':
        fmt = '%H:%M'
    else:
        fmt = '%I:%M %p'

    # Add timezone if requested
    if show_timezone:
        fmt += ' %Z'

    result = dt.strftime(fmt)

    # Clean up leading zero in 12h format (e.g., "07:30 PM" -> "7:30 PM")
    if time_format == '12h' and result.startswith('0'):
        result = result[1:]

    return result


def format_datetime(
    dt: datetime,
    time_format: str = '12h',
    show_timezone: bool = True,
    timezone: Optional[str] = None,
    date_format: str = 'long'
) -> str:
    """
    Format a full datetime using user preferences.

    Args:
        dt: The datetime to format
        time_format: '12h' or '24h'
        show_timezone: Whether to include timezone abbreviation
        timezone: Target timezone name
        date_format: 'long' (November 27, 2025), 'short' (11/27/25), 'iso' (2025-11-27)

    Returns:
        Formatted datetime string

    Examples:
        >>> format_datetime(dt, '12h', True, date_format='long')
        "November 27, 2025 at 7:30 PM EST"
    """
    # Convert to target timezone if specified
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            dt = dt.astimezone(tz)
        except Exception:
            pass

    # Date portion
    if date_format == 'long':
        date_str = dt.strftime('%B %d, %Y')
    elif date_format == 'short':
        date_str = dt.strftime('%m/%d/%y')
    else:  # iso
        date_str = dt.strftime('%Y-%m-%d')

    # Time portion
    time_str = format_time(dt, time_format, show_timezone, timezone=None)  # Already converted

    return f"{date_str} at {time_str}"


def get_time_format_string(time_format: str = '12h', show_timezone: bool = True) -> str:
    """
    Get the strftime format string for the given preferences.

    Useful when you need to format times directly without the helper.

    Args:
        time_format: '12h' or '24h'
        show_timezone: Whether to include timezone

    Returns:
        strftime format string
    """
    if time_format == '24h':
        fmt = '%H:%M'
    else:
        fmt = '%I:%M %p'

    if show_timezone:
        fmt += ' %Z'

    return fmt


def get_time_settings(settings: dict) -> tuple:
    """
    Extract time format settings from a settings dict.

    Args:
        settings: Settings dict from database

    Returns:
        Tuple of (time_format, show_timezone)
    """
    time_format = settings.get('time_format', '12h')
    show_timezone = settings.get('show_timezone', True)

    # Handle string 'true'/'false' from database
    if isinstance(show_timezone, str):
        show_timezone = show_timezone.lower() in ('true', '1', 'yes')

    return time_format, show_timezone


# Convenience function for template engines
def format_game_time(
    event_datetime: datetime,
    settings: dict,
    timezone: Optional[str] = None
) -> str:
    """
    Format a game time for use in EPG descriptions.

    This is the primary function used by template engines.

    Args:
        event_datetime: The event's datetime (usually UTC from ESPN)
        settings: User settings dict containing time_format and show_timezone
        timezone: User's timezone (falls back to settings['default_timezone'])

    Returns:
        Formatted time string based on user preferences
    """
    time_format, show_timezone = get_time_settings(settings)

    if timezone is None:
        timezone = settings.get('default_timezone', 'America/Detroit')

    return format_time(event_datetime, time_format, show_timezone, timezone)


def get_user_timezone(db_connection_func=None) -> str:
    """
    Get user's timezone from database settings.

    Args:
        db_connection_func: Function that returns DB connection.
                           If not provided, falls back to database.get_connection.

    Returns:
        Timezone string (e.g., 'America/Detroit', 'America/New_York')
        Falls back to 'America/Detroit' if not set.
    """
    DEFAULT_TZ = 'America/Detroit'

    if db_connection_func is None:
        try:
            from database import get_connection
            db_connection_func = get_connection
        except ImportError:
            return DEFAULT_TZ

    try:
        conn = db_connection_func()
        cursor = conn.cursor()
        cursor.execute("SELECT default_timezone FROM settings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    return DEFAULT_TZ


def get_today_in_user_tz(db_connection_func=None) -> datetime:
    """
    Get current date in user's timezone.

    This is important for "today/yesterday" comparisons because
    a game at 7pm EST on Dec 6 becomes Dec 7 at midnight UTC.
    We want to compare against the user's concept of "today".

    Args:
        db_connection_func: Function that returns DB connection.

    Returns:
        datetime.date in user's timezone
    """
    user_tz_str = get_user_timezone(db_connection_func)
    try:
        user_tz = ZoneInfo(user_tz_str)
    except Exception:
        user_tz = ZoneInfo('America/Detroit')

    now_utc = datetime.now(ZoneInfo('UTC'))
    now_user = now_utc.astimezone(user_tz)
    return now_user.date()
