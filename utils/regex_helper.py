"""
Regex Helper Module

Provides unified regex compilation with support for advanced patterns.
Uses the 'regex' module if available (supports variable-width lookbehind),
otherwise falls back to standard 're' module.

Usage:
    from utils.regex_helper import compile_pattern, validate_pattern, REGEX_MODULE

    # Compile a user-provided pattern (returns None on error)
    pattern = compile_pattern(user_input, ignore_case=True)
    if pattern:
        match = pattern.search(text)

    # Validate without compiling (for form validation)
    is_valid, error_msg = validate_pattern(user_input)

    # Access the module directly if needed
    REGEX_MODULE.search(pattern, text)
"""

import re
from typing import Optional, Tuple, Union

# Try to import 'regex' module which supports advanced features like
# variable-width lookbehind. Fall back to standard 're' if not available.
try:
    import regex
    REGEX_MODULE = regex
    REGEX_MODULE_NAME = 'regex'
    SUPPORTS_VARIABLE_LOOKBEHIND = True
except ImportError:
    REGEX_MODULE = re
    REGEX_MODULE_NAME = 're'
    SUPPORTS_VARIABLE_LOOKBEHIND = False


def compile_pattern(
    pattern: Optional[str],
    ignore_case: bool = True,
    default: Optional[object] = None
) -> Optional[object]:
    """
    Compile a regex pattern with error handling.

    Uses the 'regex' module if available (supports variable-width lookbehind),
    otherwise falls back to standard 're' module.

    Args:
        pattern: The regex pattern string to compile. If None or empty, returns default.
        ignore_case: Whether to use case-insensitive matching (default True).
        default: Value to return if pattern is None/empty or compilation fails.

    Returns:
        Compiled regex pattern object, or default value on error/empty input.

    Examples:
        >>> pattern = compile_pattern(r'(?<=:\s|vs\s)Washington(?=\s+@)')
        >>> if pattern:
        ...     match = pattern.search('NCAA: Washington @ UCLA')

        >>> # Returns None for invalid patterns
        >>> compile_pattern(r'[invalid')
        None
    """
    if not pattern or not pattern.strip():
        return default

    flags = REGEX_MODULE.IGNORECASE if ignore_case else 0

    try:
        return REGEX_MODULE.compile(pattern.strip(), flags)
    except Exception:
        # Catches both re.error and regex-specific errors
        return default


def validate_pattern(pattern: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate a regex pattern without compiling for reuse.

    Useful for form validation to give users feedback before saving.

    Args:
        pattern: The regex pattern string to validate.

    Returns:
        Tuple of (is_valid, error_message).
        If valid, returns (True, None).
        If invalid, returns (False, "error description").

    Examples:
        >>> validate_pattern(r'(?P<team1>\w+)\s+vs\s+(?P<team2>\w+)')
        (True, None)

        >>> validate_pattern(r'[unclosed')
        (False, "unterminated character set at position 0")
    """
    if not pattern or not pattern.strip():
        return (True, None)  # Empty is valid (means disabled)

    try:
        REGEX_MODULE.compile(pattern.strip())
        return (True, None)
    except Exception as e:
        return (False, str(e))


def search(
    pattern: Union[str, object],
    text: str,
    ignore_case: bool = True
) -> Optional[object]:
    """
    Search for a pattern in text, compiling if necessary.

    Convenience function for one-off searches.

    Args:
        pattern: Either a compiled pattern or a pattern string.
        text: The text to search in.
        ignore_case: Whether to use case-insensitive matching (if pattern is a string).

    Returns:
        Match object if found, None otherwise.
    """
    if isinstance(pattern, str):
        compiled = compile_pattern(pattern, ignore_case=ignore_case)
        if not compiled:
            return None
        return compiled.search(text)
    else:
        # Already compiled
        return pattern.search(text)


def get_module_info() -> dict:
    """
    Get information about the regex module being used.

    Useful for debugging and status display.

    Returns:
        Dict with 'module_name', 'supports_variable_lookbehind', 'version'.
    """
    version = getattr(REGEX_MODULE, '__version__', 'builtin')
    return {
        'module_name': REGEX_MODULE_NAME,
        'supports_variable_lookbehind': SUPPORTS_VARIABLE_LOOKBEHIND,
        'version': version
    }


def get_group_filter_patterns(group: Optional[dict]) -> Tuple[Optional[str], Optional[str]]:
    """
    Get include/exclude regex pattern strings from an event EPG group config.

    Returns the raw pattern strings (not compiled) for passing to functions
    like filter_game_streams() that compile internally.

    Args:
        group: Event EPG group dict with stream_include_regex, stream_include_regex_enabled,
               stream_exclude_regex, stream_exclude_regex_enabled fields.

    Returns:
        Tuple of (include_pattern_str, exclude_pattern_str). Either can be None if
        not enabled or empty.

    Example:
        >>> include_str, exclude_str = get_group_filter_patterns(db_group)
        >>> result = filter_game_streams(streams, include_regex=include_str, exclude_regex=exclude_str)
    """
    if not group:
        return (None, None)

    include_pattern = None
    if bool(group.get('stream_include_regex_enabled')) and group.get('stream_include_regex'):
        include_pattern = group['stream_include_regex']

    exclude_pattern = None
    if bool(group.get('stream_exclude_regex_enabled')) and group.get('stream_exclude_regex'):
        exclude_pattern = group['stream_exclude_regex']

    return (include_pattern, exclude_pattern)


def compile_group_filters(group: Optional[dict]) -> Tuple[Optional[object], Optional[object]]:
    """
    Compile include/exclude regex patterns from an event EPG group config.

    Consolidates the common pattern of checking if filters are enabled
    and compiling them. Returns compiled pattern objects.

    Args:
        group: Event EPG group dict with stream_include_regex, stream_include_regex_enabled,
               stream_exclude_regex, stream_exclude_regex_enabled fields.

    Returns:
        Tuple of (include_pattern, exclude_pattern). Either can be None if
        not enabled, empty, or invalid.

    Example:
        >>> include_re, exclude_re = compile_group_filters(db_group)
        >>> if include_re and not include_re.search(stream_name):
        ...     # Stream filtered out by inclusion pattern
    """
    include_str, exclude_str = get_group_filter_patterns(group)
    include_pattern = compile_pattern(include_str) if include_str else None
    exclude_pattern = compile_pattern(exclude_str) if exclude_str else None
    return (include_pattern, exclude_pattern)
