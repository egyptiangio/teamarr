"""
Stream Filtering for Event-Based EPG

Filters streams to identify actual game streams vs placeholders and non-game content.
Uses positive detection - streams must contain game indicators (vs, @, at) to be counted.

This ensures match rate calculations reflect reality:
- "10/12 matched" (game streams only)
- Not "10/20 matched" (inflated by placeholders and non-game streams)
"""

import re
from typing import Dict, List, Tuple

from utils.regex_helper import compile_pattern

# Pattern to detect game indicators in stream names
# Matches: vs, vs., at (as word), v (as word), x (as word)
# Note: @ is handled separately - only counts as game indicator when followed by team name
GAME_INDICATOR_PATTERN = re.compile(
    r'\b(vs\.?|at|v|x)\b',
    re.IGNORECASE
)

# Month pattern for detecting date separators
# Handles both short (Jan, Feb) and full (January, February) month names
_MONTHS_PATTERN = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'

# Pattern to detect @ used as team separator (not date/time separator)
# Matches: "Team @ Team" or "Team @ #4 Team" (@ followed by team or ranking + team)
# Does NOT match: "@ Dec 05", "@ 12:00", "@ 2025-12-06" (date/time markers)
#
# Key patterns:
# - "@ Ravens" -> game (@ followed by team name)
# - "@ 4 Texas T" -> game (@ followed by ranking + team)
# - "@ #8 Alabama" -> game (@ followed by ranking + team)
# - "@ Dec 05" -> NOT game (@ followed by month)
# - "@ 2025-12-06" -> NOT game (@ followed by 4-digit year)
# - "@ 12:00" -> NOT game (@ followed by time)
AT_AS_SEPARATOR_PATTERN = re.compile(
    rf'@\s+(?!{_MONTHS_PATTERN}\b|20\d{{2}}|\d{{1,2}}:\d{{2}})'  # Reject month, year (20xx), or time
    rf'(?:#?\d+\s+)?'  # Optional ranking like "4 " or "#8 "
    rf'([A-Za-z]{{2,}})',  # Team name (at least 2 letters)
    re.IGNORECASE
)


def has_game_indicator(stream_name: str) -> bool:
    """
    Check if a stream name contains a matchup indicator.

    Game indicators are patterns that suggest this is an actual game stream:
    - "vs" or "vs." (e.g., "Lakers vs Celtics")
    - "@" when used as team separator (e.g., "Chiefs @ Ravens")
    - "at" as a word (e.g., "Patriots at Bills")
    - "v" as a word (e.g., "Arsenal v Chelsea" - soccer style)
    - "x" as a word (e.g., "76ers x Wizards" - some providers use this)

    Note: "@" followed by a date/time is NOT a game indicator:
    - "UFC 302 @ Dec 05 08:00 PM ET" - NOT a game (@ is date separator)
    - "ESPN+ 122 : Show Name @ Dec 05" - NOT a game (@ is date separator)

    Args:
        stream_name: The stream name to check

    Returns:
        True if the stream appears to be a game stream

    Examples:
        >>> has_game_indicator("NBA 01: Lakers vs Celtics")
        True
        >>> has_game_indicator("NFL 02: Chiefs @ Ravens")
        True
        >>> has_game_indicator("NFL 03 - ")
        False
        >>> has_game_indicator("RedZone")
        False
        >>> has_game_indicator("NFL Network")
        False
        >>> has_game_indicator("UFC 302 @ Dec 05 08:00 PM ET")
        False
    """
    # Check for standard game indicators (vs, vs., at, v, x)
    if GAME_INDICATOR_PATTERN.search(stream_name):
        return True

    # Check for @ used as team separator (not date separator)
    if AT_AS_SEPARATOR_PATTERN.search(stream_name):
        return True

    return False


def filter_game_streams(
    streams: List[Dict],
    include_regex: str = None,
    exclude_regex: str = None
) -> Dict:
    """
    Filter streams to only those that appear to be game streams.

    Three-layer filtering:
    1. Built-in: Must have game indicator (vs/@/at/v/x)
    2. Optional: User inclusion regex (stream must match to be processed)
    3. Optional: User exclusion regex (stream must NOT match to be processed)

    Filter order matters:
    - Include filter runs first (whitelist)
    - Exclude filter runs second (blacklist)
    - This allows: "include Washington teams, but exclude George Washington"

    Args:
        streams: List of stream dicts with 'name' key
        include_regex: Optional regex pattern - only matching streams are processed
        exclude_regex: Optional regex pattern to exclude additional streams

    Returns:
        Dict with:
        - 'game_streams': Streams that passed filtering
        - 'filtered_streams': All streams that were filtered out
        - 'filtered_no_indicator': Count of streams without vs/@/at
        - 'filtered_include_regex': Count of streams not matching inclusion regex
        - 'filtered_exclude_regex': Count of streams matching exclusion regex

    Example:
        >>> streams = [
        ...     {'name': 'NBA 01: Lakers vs Celtics', 'id': 1},
        ...     {'name': 'NBA 02 - ', 'id': 2},
        ...     {'name': 'RedZone', 'id': 3},
        ... ]
        >>> result = filter_game_streams(streams)
        >>> len(result['game_streams'])
        1
        >>> result['filtered_no_indicator']
        2
    """
    game_streams = []
    filtered_streams = []
    filtered_no_indicator = 0
    filtered_include_regex = 0
    filtered_exclude_regex = 0

    # Compile user patterns using regex helper (supports variable-width lookbehind)
    include_pattern = compile_pattern(include_regex) if include_regex else None
    exclude_pattern = compile_pattern(exclude_regex) if exclude_regex else None

    for stream in streams:
        name = stream.get('name', '')

        # Layer 1: Must have game indicator
        if not has_game_indicator(name):
            filtered_streams.append(stream)
            filtered_no_indicator += 1
            continue

        # Layer 2: Check user inclusion pattern (must match to pass)
        if include_pattern and not include_pattern.search(name):
            filtered_streams.append(stream)
            filtered_include_regex += 1
            continue

        # Layer 3: Check user exclusion pattern (must NOT match to pass)
        if exclude_pattern and exclude_pattern.search(name):
            filtered_streams.append(stream)
            filtered_exclude_regex += 1
            continue

        game_streams.append(stream)

    return {
        'game_streams': game_streams,
        'filtered_streams': filtered_streams,
        'filtered_no_indicator': filtered_no_indicator,
        'filtered_include_regex': filtered_include_regex,
        'filtered_exclude_regex': filtered_exclude_regex,
    }


def get_filter_summary(
    total_count: int,
    game_count: int,
    matched_count: int
) -> str:
    """
    Generate a human-readable summary of filtering results.

    Args:
        total_count: Total streams from provider
        game_count: Streams that passed game indicator filter
        matched_count: Streams that matched to ESPN events

    Returns:
        Summary string like "8/10 matched (5 non-game filtered)"
    """
    filtered_count = total_count - game_count

    if filtered_count > 0:
        return f"{matched_count}/{game_count} matched ({filtered_count} non-game filtered)"
    else:
        return f"{matched_count}/{game_count} matched"
