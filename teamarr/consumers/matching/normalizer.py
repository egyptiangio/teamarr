"""Stream name normalization for matching.

Cleans up heterogeneous, poorly-formatted stream names before matching:
- Fixes mojibake (double-encoded UTF-8)
- Strips provider prefixes (ESPN+, DAZN, etc.)
- Applies city translations (München → Munich)
- Masks datetime patterns for separator detection
- Extracts date/time hints for validation
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, time

from unidecode import unidecode

from teamarr.utilities.constants import BROADCAST_NETWORKS, CITY_TRANSLATIONS, PROVIDER_PREFIXES

logger = logging.getLogger(__name__)


@dataclass
class NormalizedStream:
    """Result of stream normalization with extracted metadata."""

    original: str
    normalized: str

    # Extracted metadata (may be None)
    extracted_date: date | None = None
    extracted_time: time | None = None
    league_hint: str | None = None
    provider_prefix: str | None = None


# =============================================================================
# MOJIBAKE DETECTION AND FIXING
# =============================================================================

# Common mojibake patterns (double-encoded UTF-8)
MOJIBAKE_PATTERNS = [
    # German umlauts
    (r"Ã¼", "ü"),
    (r"Ã¶", "ö"),
    (r"Ã¤", "ä"),
    (r"Ãœ", "Ü"),
    (r"Ã–", "Ö"),
    (r"Ã„", "Ä"),
    (r"ÃŸ", "ß"),
    # Spanish/Portuguese
    (r"Ã±", "ñ"),
    (r"Ã©", "é"),
    (r"Ã¡", "á"),
    (r"Ã­", "í"),
    (r"Ã³", "ó"),
    (r"Ãº", "ú"),
    (r"Ã§", "ç"),
    # French
    (r"Ã¨", "è"),
    (r"Ãª", "ê"),
    (r"Ã«", "ë"),
    (r"Ã®", "î"),
    (r"Ã¯", "i"),
    (r"Ã´", "o"),
    (r"Ã¹", "u"),
    (r"Ã»", "u"),
]


def fix_mojibake(text: str) -> str:
    """Fix common mojibake patterns from double-encoded UTF-8.

    Args:
        text: Potentially mojibake'd text

    Returns:
        Fixed text with proper unicode characters
    """
    if not text:
        return text

    result = text
    for pattern, replacement in MOJIBAKE_PATTERNS:
        result = result.replace(pattern, replacement)

    if result != text:
        logger.debug("[MOJIBAKE] Fixed: '%s' -> '%s'", text[:40], result[:40])

    return result


# =============================================================================
# PROVIDER PREFIX STRIPPING
# =============================================================================


def strip_provider_prefix(text: str) -> tuple[str, str | None]:
    """Remove provider prefix from stream name.

    Args:
        text: Stream name potentially with provider prefix

    Returns:
        Tuple of (cleaned text, removed prefix or None)
    """
    if not text:
        return text, None

    text_lower = text.lower()

    for prefix in PROVIDER_PREFIXES:
        if text_lower.startswith(prefix.lower()):
            return text[len(prefix) :].strip(), prefix.strip()

    return text, None


# =============================================================================
# CITY TRANSLATIONS
# =============================================================================


def apply_city_translations(text: str) -> str:
    """Apply city name translations.

    First normalizes with unidecode (München → Munchen),
    then applies manual translations (munchen → munich).

    Args:
        text: Text containing city names

    Returns:
        Text with city names translated to English
    """
    if not text:
        return text

    # First pass: unidecode to normalize accents
    # This converts München → Munchen
    text = unidecode(text)

    # Second pass: apply manual translations
    # Work on lowercased version for matching, preserve original case pattern
    result = text
    text_lower = text.lower()

    for variant, english in CITY_TRANSLATIONS.items():
        if variant in text_lower:
            # Find the position and replace preserving some case
            pattern = re.compile(re.escape(variant), re.IGNORECASE)
            result = pattern.sub(english, result)

    return result


# =============================================================================
# DATETIME EXTRACTION AND MASKING
# =============================================================================

# Date patterns to extract and mask
_MONTHS = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_PATTERNS = [
    # ISO format: 2026-01-09 (YYYY-MM-DD) - must be before MM/DD/YYYY pattern
    (r"\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b", "DATE_MASK_ISO"),
    # 12/31/25, 12/31/2025 (MM/DD/YY or MM/DD/YYYY)
    (r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", "DATE_MASK"),
    # 1/17, 12/31 (MM/DD without year) - infer year based on proximity to today
    # Must come after MM/DD/YYYY to avoid partial matches
    (r"\b(\d{1,2})[/\-](\d{1,2})\b", "DATE_MASK_NO_YEAR"),
    # 31 Dec, 31 December - check this BEFORE "Dec 31" to prefer "14 Jan" over "Jan 11"
    (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})[a-z]*\b", "DATE_MASK"),
    # Dec 31, December 31 - use negative lookahead (?!:) to avoid matching "Jan 11:45pm"
    (rf"\b({_MONTHS})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!:)\b", "DATE_MASK"),
]

# Time patterns to extract and mask
TIME_PATTERNS = [
    # 7:00 PM, 7:00PM, 19:00, 15:00:05
    (r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)?\b", "TIME_MASK"),
    # 7PM, 7 PM
    (r"\b(\d{1,2})\s*(AM|PM|am|pm)\b", "TIME_MASK"),
]


def extract_and_mask_datetime(text: str) -> tuple[str, date | None, time | None]:
    """Extract date/time from stream name and mask for separator detection.

    Masking prevents date components like "12/31" from being mistaken
    for score patterns or other separators.

    Args:
        text: Stream name

    Returns:
        Tuple of (masked text, extracted date, extracted time)
    """
    from datetime import timedelta

    if not text:
        return text, None, None

    result = text

    # Normalize em dashes (—) and en dashes (–) to spaces for pattern matching
    result = result.replace("\u2014", " ").replace("\u2013", " ")

    extracted_date = None
    extracted_time = None

    # Extract and mask dates
    for pattern, mask in DATE_PATTERNS:
        match = re.search(pattern, result, re.IGNORECASE)
        if match:
            is_iso = mask == "DATE_MASK_ISO"
            no_year = mask == "DATE_MASK_NO_YEAR"
            extracted_date = _parse_date_match(match, is_iso=is_iso, no_year=no_year)
            result = re.sub(pattern, " DATE_MASK ", result, count=1, flags=re.IGNORECASE)
            break

    # Extract and mask times
    for pattern, mask in TIME_PATTERNS:
        match = re.search(pattern, result, re.IGNORECASE)
        if match:
            extracted_time = _parse_time_match(match)
            result = re.sub(pattern, f" {mask} ", result, count=1, flags=re.IGNORECASE)
            break

    # Adjust date for European timezone streams showing US evening games
    # European streams show after-midnight times for US evening games
    # e.g., 7pm ET = 1am CET next day, so "Thu 29 Jan 01:35" = Wed Jan 28 US time
    # Adjust if time is 00:00-06:29 AND no US timezone marker present
    if extracted_date and extracted_time and (extracted_time.hour < 6 or (extracted_time.hour == 6 and extracted_time.minute < 30)):
        text_upper = text.upper()

        # Check for US timezone indicators - if present, don't adjust
        us_tz_pattern = r'\b(ET|EST|EDT|PT|PST|PDT|CT|CST|CDT|MT|MST|MDT|EASTERN|PACIFIC|CENTRAL)\b'
        has_us_tz = re.search(us_tz_pattern, text_upper)

        # Only adjust if NO US timezone marker (assumes European source)
        if not has_us_tz:
            extracted_date = extracted_date - timedelta(days=1)
            logger.debug(
                "[NORMALIZE] Adjusted date -1 day for European timezone: %s (time=%s)",
                text[:50], extracted_time
            )

    # Clean up multiple spaces
    result = " ".join(result.split())

    return result, extracted_date, extracted_time


def _parse_date_match(match: re.Match, is_iso: bool = False, no_year: bool = False) -> date | None:
    """Parse a date from regex match.

    Args:
        match: Regex match object
        is_iso: True if pattern matched ISO format (YYYY-MM-DD)
        no_year: True if pattern matched MM/DD without year (infer year)
    """
    try:
        groups = match.groups()
        text = match.group(0)

        # Check if it's a month name pattern
        month_names = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }

        for month_abbr, month_num in month_names.items():
            if month_abbr in text.lower():
                # Extract day number
                day_match = re.search(r"(\d{1,2})", text)
                if day_match:
                    day = int(day_match.group(1))
                    return _infer_year_for_date(month_num, day)
                return None

        # MM/DD without year - infer year based on proximity to today
        if no_year and len(groups) >= 2:
            month = int(groups[0])
            day = int(groups[1])
            return _infer_year_for_date(month, day)

        # Numeric date patterns with year
        if len(groups) >= 3:
            if is_iso:
                # ISO format: YYYY-MM-DD
                year = int(groups[0])
                month = int(groups[1])
                day = int(groups[2])
            else:
                # US format: MM/DD/YY or MM/DD/YYYY
                month = int(groups[0])
                day = int(groups[1])
                year = int(groups[2])

                # Handle 2-digit year
                if year < 100:
                    year += 2000 if year < 50 else 1900

            return date(year, month, day)

    except (ValueError, IndexError, TypeError):
        pass

    return None


def _infer_year_for_date(month: int, day: int) -> date | None:
    """Infer the year for a MM/DD date based on proximity to today.

    For sports streams, prefer dates in the near future over past.
    If the date in current year is more than 6 months ago, assume next year.
    """
    from datetime import datetime

    today = datetime.now().date()
    current_year = today.year

    try:
        # Try current year first
        candidate = date(current_year, month, day)

        # If more than 6 months in the past, try next year
        days_ago = (today - candidate).days
        if days_ago > 180:
            candidate = date(current_year + 1, month, day)
        # If more than 6 months in the future, try previous year
        elif days_ago < -180:
            candidate = date(current_year - 1, month, day)

        return candidate
    except ValueError:
        # Invalid date (e.g., Feb 30)
        return None


def _parse_time_match(match: re.Match) -> time | None:
    """Parse a time from regex match."""
    try:
        groups = match.groups()

        hour = int(groups[0])

        # Check for minutes
        minute = 0
        if len(groups) > 1 and groups[1] and groups[1].isdigit():
            minute = int(groups[1])

        # Check for AM/PM
        am_pm = None
        for g in groups:
            if g and g.upper() in ("AM", "PM"):
                am_pm = g.upper()
                break

        # Convert to 24-hour
        if am_pm == "PM" and hour < 12:
            hour += 12
        elif am_pm == "AM" and hour == 12:
            hour = 0

        return time(hour, minute)

    except (ValueError, IndexError, TypeError):
        pass

    return None


# =============================================================================
# MAIN NORMALIZATION PIPELINE
# =============================================================================


def normalize_stream(stream_name: str) -> NormalizedStream:
    """Full normalization pipeline for stream names.

    Applies all normalization steps in order:
    1. Fix mojibake (double-encoded UTF-8)
    2. Strip provider prefix
    3. Apply city translations (with unidecode)
    4. Extract and mask datetime
    5. Clean whitespace

    Args:
        stream_name: Raw stream name from M3U

    Returns:
        NormalizedStream with cleaned text and extracted metadata
    """
    if not stream_name:
        return NormalizedStream(
            original=stream_name or "",
            normalized="",
        )

    original = stream_name

    # Step 0: Normalize newlines to spaces (some streams have literal newlines)
    text = re.sub(r"[\r\n]+", " ", stream_name)

    # Step 1: Fix mojibake
    text = fix_mojibake(text)

    # Step 2: Strip provider prefix
    text, provider_prefix = strip_provider_prefix(text)

    # Step 3: Apply city translations (includes unidecode)
    text = apply_city_translations(text)

    # Step 4: Extract and mask datetime
    text, extracted_date, extracted_time = extract_and_mask_datetime(text)

    # Step 5: Clean whitespace and normalize
    text = " ".join(text.split())
    text = text.strip()

    logger.debug(
        "[NORMALIZE] '%s' -> '%s' (date=%s, time=%s, prefix=%s)",
        original[:60],
        text[:60],
        extracted_date,
        extracted_time,
        provider_prefix,
    )

    return NormalizedStream(
        original=original,
        normalized=text,
        extracted_date=extracted_date,
        extracted_time=extracted_time,
        provider_prefix=provider_prefix,
    )


def normalize_for_matching(text: str) -> str:
    """Quick normalization for matching (no metadata extraction).

    Use this for normalizing team names or event names before comparison.
    Applies: unidecode, city translations, lowercase, strip punctuation,
    and removes broadcast network names that add noise to fuzzy matching.

    Args:
        text: Text to normalize

    Returns:
        Normalized lowercase text
    """
    if not text:
        return ""

    # Unidecode and city translations
    text = apply_city_translations(text)

    # Lowercase
    text = text.lower()

    # Remove broadcast network names (ESPN, FOX, etc.) that add noise
    # These appear in streams like "MIL Bucks ( ESPN Feed )"
    for network in BROADCAST_NETWORKS:
        text = re.sub(rf"\b{re.escape(network.lower())}\b", " ", text)

    # Remove punctuation except spaces (hyphens become spaces for matching)
    text = re.sub(r"[^\w\s]", " ", text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text.strip()
