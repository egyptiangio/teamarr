"""Stream classification for matching strategy selection.

Classifies streams into categories that determine which matching
strategy to use:
- TEAM_VS_TEAM: Standard team sports (NFL, NBA, Soccer, etc.)
- EVENT_CARD: Combat sports with event cards (UFC, Boxing)
- PLACEHOLDER: Filler streams with no event info (skip)
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, time
from enum import Enum
from re import Pattern

from teamarr.consumers.matching.normalizer import NormalizedStream, normalize_stream
from teamarr.services.detection_keywords import DetectionKeywordService
from teamarr.utilities.constants import (
    EVENT_CARD_KEYWORDS,  # Legacy - used by extract_event_card_hint
)

logger = logging.getLogger(__name__)


class StreamCategory(Enum):
    """Stream category for matching strategy selection."""

    TEAM_VS_TEAM = "team_vs_team"  # Standard team matchup (vs/@/at)
    EVENT_CARD = "event_card"  # Combat sports (UFC, Boxing)
    PLACEHOLDER = "placeholder"  # No event info, skip


@dataclass
class ClassifiedStream:
    """Result of stream classification with extracted components."""

    category: StreamCategory
    normalized: NormalizedStream

    # For TEAM_VS_TEAM: extracted team names
    # For EVENT_CARD: also used for fighter names (fighters treated as "teams")
    team1: str | None = None
    team2: str | None = None
    separator_found: str | None = None

    # For EVENT_CARD: event hint (e.g., "UFC 315")
    event_hint: str | None = None

    # For EVENT_CARD: card segment (e.g., "early_prelims", "prelims", "main_card")
    card_segment: str | None = None

    # Detected league hint (for any category)
    # Can be a single league code or list for umbrella brands (e.g., EFL → [eng.2, eng.3, eng.4])
    league_hint: str | list[str] | None = None

    # Detected sport hint (e.g., "Hockey", "Football")
    sport_hint: str | None = None

    # Track if custom regex was used
    custom_regex_used: bool = False


@dataclass
class CustomRegexConfig:
    """Configuration for custom regex extraction patterns."""

    # TEAM_VS_TEAM patterns
    teams_pattern: str | None = None
    teams_enabled: bool = False
    date_pattern: str | None = None
    date_enabled: bool = False
    time_pattern: str | None = None
    time_enabled: bool = False
    league_pattern: str | None = None
    league_enabled: bool = False

    # EVENT_CARD patterns (UFC, Boxing, MMA)
    fighters_pattern: str | None = None
    fighters_enabled: bool = False
    event_name_pattern: str | None = None
    event_name_enabled: bool = False

    # Compiled patterns (cached)
    _compiled_teams: Pattern | None = None
    _compiled_date: Pattern | None = None
    _compiled_time: Pattern | None = None
    _compiled_league: Pattern | None = None
    _compiled_fighters: Pattern | None = None
    _compiled_event_name: Pattern | None = None

    def get_pattern(self) -> Pattern | None:
        """Get compiled teams regex pattern, compiling on first access."""
        if not self.teams_enabled or not self.teams_pattern:
            return None

        if self._compiled_teams is None:
            try:
                self._compiled_teams = re.compile(self.teams_pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[CLASSIFY] Invalid custom teams regex pattern: %s", e)
                return None

        return self._compiled_teams

    def get_date_pattern(self) -> Pattern | None:
        """Get compiled date regex pattern, compiling on first access."""
        if not self.date_enabled or not self.date_pattern:
            return None

        if self._compiled_date is None:
            try:
                self._compiled_date = re.compile(self.date_pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[CLASSIFY] Invalid custom date regex pattern: %s", e)
                return None

        return self._compiled_date

    def get_time_pattern(self) -> Pattern | None:
        """Get compiled time regex pattern, compiling on first access."""
        if not self.time_enabled or not self.time_pattern:
            return None

        if self._compiled_time is None:
            try:
                self._compiled_time = re.compile(self.time_pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[CLASSIFY] Invalid custom time regex pattern: %s", e)
                return None

        return self._compiled_time

    def get_league_pattern(self) -> Pattern | None:
        """Get compiled league regex pattern, compiling on first access."""
        if not self.league_enabled or not self.league_pattern:
            return None

        if self._compiled_league is None:
            try:
                self._compiled_league = re.compile(self.league_pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[CLASSIFY] Invalid custom league regex pattern: %s", e)
                return None

        return self._compiled_league

    def get_fighters_pattern(self) -> Pattern | None:
        """Get compiled fighters regex pattern for EVENT_CARD streams."""
        if not self.fighters_enabled or not self.fighters_pattern:
            return None

        if self._compiled_fighters is None:
            try:
                self._compiled_fighters = re.compile(self.fighters_pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[CLASSIFY] Invalid custom fighters regex pattern: %s", e)
                return None

        return self._compiled_fighters

    def get_event_name_pattern(self) -> Pattern | None:
        """Get compiled event name regex pattern for EVENT_CARD streams."""
        if not self.event_name_enabled or not self.event_name_pattern:
            return None

        if self._compiled_event_name is None:
            try:
                self._compiled_event_name = re.compile(self.event_name_pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("[CLASSIFY] Invalid custom event_name regex pattern: %s", e)
                return None

        return self._compiled_event_name


def extract_teams_with_custom_regex(
    text: str,
    config: CustomRegexConfig,
) -> tuple[str | None, str | None, bool]:
    """Extract team names using custom regex pattern.

    Args:
        text: Stream name (normalized)
        config: Custom regex configuration

    Returns:
        Tuple of (team1, team2, success)
    """
    pattern = config.get_pattern()
    if not pattern:
        return None, None, False

    match = pattern.search(text)
    if not match:
        return None, None, False

    # Try numbered groups first (group 1 and 2)
    groups = match.groups()
    if len(groups) >= 2:
        team1 = groups[0].strip() if groups[0] else None
        team2 = groups[1].strip() if groups[1] else None
        if team1 and team2:
            return team1, team2, True

    # Try named groups (?P<team1>...) and (?P<team2>...)
    try:
        team1 = match.group("team1")
        team2 = match.group("team2")
        if team1 and team2:
            return team1.strip(), team2.strip(), True
    except (IndexError, re.error):
        pass

    return None, None, False


def extract_fighters_with_custom_regex(
    text: str,
    config: CustomRegexConfig,
) -> tuple[str | None, str | None, bool]:
    """Extract fighter names using custom regex pattern for EVENT_CARD streams.

    Args:
        text: Stream name (normalized)
        config: Custom regex configuration

    Returns:
        Tuple of (fighter1, fighter2, success)
    """
    pattern = config.get_fighters_pattern()
    if not pattern:
        return None, None, False

    match = pattern.search(text)
    if not match:
        return None, None, False

    # Try numbered groups first (group 1 and 2)
    groups = match.groups()
    if len(groups) >= 2:
        fighter1 = groups[0].strip() if groups[0] else None
        fighter2 = groups[1].strip() if groups[1] else None
        if fighter1 and fighter2:
            return fighter1, fighter2, True

    # Try named groups (?P<fighter1>...) and (?P<fighter2>...)
    try:
        fighter1 = match.group("fighter1")
        fighter2 = match.group("fighter2")
        if fighter1 and fighter2:
            return fighter1.strip(), fighter2.strip(), True
    except (IndexError, re.error):
        pass

    return None, None, False


def extract_event_name_with_custom_regex(
    text: str,
    config: CustomRegexConfig,
) -> str | None:
    """Extract event name using custom regex pattern for EVENT_CARD streams.

    Args:
        text: Stream name (original, not normalized)
        config: Custom regex configuration

    Returns:
        Event name string or None
    """
    pattern = config.get_event_name_pattern()
    if not pattern:
        return None

    match = pattern.search(text)
    if not match:
        return None

    # Try named group (?P<event_name>...)
    try:
        event_name = match.group("event_name")
        if event_name:
            return event_name.strip()
    except (IndexError, re.error):
        pass

    # Try first capture group
    groups = match.groups()
    if groups and groups[0]:
        return groups[0].strip()

    return None


def extract_date_with_custom_regex(
    text: str,
    config: CustomRegexConfig,
) -> date | None:
    """Extract date using custom regex pattern.

    Supports:
    - Named group: (?P<date>...) - returns raw string to parse
    - Named groups: (?P<month>...) (?P<day>...) (?P<year>...) - combines
    - Single capture group - returns raw string to parse

    Args:
        text: Stream name (original, not normalized)
        config: Custom regex configuration

    Returns:
        Extracted date or None
    """
    from datetime import datetime

    pattern = config.get_date_pattern()
    if not pattern:
        return None

    match = pattern.search(text)
    if not match:
        return None

    try:
        # Try named group 'date' first (full date string)
        try:
            date_str = match.group("date")
            if date_str:
                return _parse_date_string(date_str.strip())
        except (IndexError, re.error):
            pass

        # Try individual named groups (month, day, year)
        try:
            month_str = match.group("month")
            day_str = match.group("day")
            if month_str and day_str:
                month = _parse_month(month_str.strip())
                day = int(day_str.strip())
                try:
                    year = int(match.group("year").strip())
                    if year < 100:
                        year += 2000 if year < 50 else 1900
                except (IndexError, re.error, ValueError, AttributeError):
                    year = datetime.now().year
                return date(year, month, day)
        except (IndexError, re.error, ValueError, AttributeError):
            pass

        # Try first capture group as raw date string
        groups = match.groups()
        if groups and groups[0]:
            return _parse_date_string(groups[0].strip())

    except (ValueError, TypeError) as e:
        logger.debug("[CLASSIFY] Failed to parse custom date: %s", e)

    return None


def _parse_month(month_str: str) -> int:
    """Parse month from string (name or number)."""
    month_names = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    month_lower = month_str.lower()
    if month_lower in month_names:
        return month_names[month_lower]
    return int(month_str)


def _parse_date_string(date_str: str) -> date | None:
    """Parse various date string formats."""
    from datetime import datetime

    # Common formats to try
    formats = [
        "%d %b",  # 14 Jan
        "%d %B",  # 14 January
        "%b %d",  # Jan 14
        "%B %d",  # January 14
        "%m/%d/%Y",  # 01/14/2026
        "%m/%d/%y",  # 01/14/26
        "%d/%m/%Y",  # 14/01/2026
        "%d/%m/%y",  # 14/01/26
        "%Y-%m-%d",  # 2026-01-14
        "%d-%m-%Y",  # 14-01-2026
    ]

    # Clean up ordinal suffixes (1st, 2nd, 3rd, 4th)
    date_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date_str, flags=re.IGNORECASE)

    for fmt in formats:
        try:
            parsed = datetime.strptime(date_str, fmt)
            # If no year in format, use current year
            if "%Y" not in fmt and "%y" not in fmt:
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.date()
        except ValueError:
            continue

    return None


def extract_time_with_custom_regex(
    text: str,
    config: CustomRegexConfig,
) -> time | None:
    """Extract time using custom regex pattern.

    Supports:
    - Named group: (?P<time>...) - returns raw string to parse
    - Named groups: (?P<hour>...) (?P<minute>...) (?P<ampm>...) - combines
    - Single capture group - returns raw string to parse

    Args:
        text: Stream name (original, not normalized)
        config: Custom regex configuration

    Returns:
        Extracted time or None
    """
    pattern = config.get_time_pattern()
    if not pattern:
        return None

    match = pattern.search(text)
    if not match:
        return None

    try:
        # Try named group 'time' first (full time string)
        try:
            time_str = match.group("time")
            if time_str:
                return _parse_time_string(time_str.strip())
        except (IndexError, re.error):
            pass

        # Try individual named groups (hour, minute, ampm)
        try:
            hour = int(match.group("hour").strip())
            try:
                minute = int(match.group("minute").strip())
            except (IndexError, re.error, ValueError, AttributeError):
                minute = 0

            try:
                ampm = match.group("ampm").strip().upper()
                if ampm == "PM" and hour < 12:
                    hour += 12
                elif ampm == "AM" and hour == 12:
                    hour = 0
            except (IndexError, re.error, ValueError, AttributeError):
                pass

            return time(hour, minute)
        except (IndexError, re.error, ValueError, AttributeError):
            pass

        # Try first capture group as raw time string
        groups = match.groups()
        if groups and groups[0]:
            return _parse_time_string(groups[0].strip())

    except (ValueError, TypeError) as e:
        logger.debug("[CLASSIFY] Failed to parse custom time: %s", e)

    return None


def _parse_time_string(time_str: str) -> time | None:
    """Parse various time string formats."""
    from datetime import datetime

    # Common formats to try
    formats = [
        "%I:%M%p",  # 6:45pm
        "%I:%M %p",  # 6:45 pm
        "%I%p",  # 6pm
        "%I %p",  # 6 pm
        "%H:%M",  # 18:45
        "%H%M",  # 1845
    ]

    # Normalize: remove spaces between number and am/pm
    time_str_normalized = re.sub(r"(\d+)\s*(am|pm)", r"\1\2", time_str, flags=re.IGNORECASE)

    for fmt in formats:
        try:
            parsed = datetime.strptime(time_str_normalized, fmt)
            return parsed.time()
        except ValueError:
            continue

    # Also try the original string
    if time_str != time_str_normalized:
        for fmt in formats:
            try:
                parsed = datetime.strptime(time_str, fmt)
                return parsed.time()
            except ValueError:
                continue

    return None


def extract_league_with_custom_regex(
    text: str,
    config: CustomRegexConfig,
) -> str | None:
    """Extract league hint using custom regex pattern.

    Supports:
    - Named group: (?P<league>...) - returns the captured league code
    - Single capture group - returns the captured string

    Args:
        text: Stream name (original, not normalized)
        config: Custom regex configuration

    Returns:
        Extracted league code or None
    """
    pattern = config.get_league_pattern()
    if not pattern:
        return None

    match = pattern.search(text)
    if not match:
        return None

    try:
        # Try named group 'league' first
        try:
            league = match.group("league")
            if league:
                return league.strip().lower()
        except (IndexError, re.error):
            pass

        # Try first capture group
        groups = match.groups()
        if groups and groups[0]:
            return groups[0].strip().lower()

    except (ValueError, TypeError) as e:
        logger.debug("[CLASSIFY] Failed to extract custom league: %s", e)

    return None


# =============================================================================
# PLACEHOLDER DETECTION
# =============================================================================


def is_placeholder(text: str) -> bool:
    """Check if stream name matches placeholder patterns.

    Placeholders are filler streams with no real event info,
    like "ESPN+ 45" or "Coming Soon".

    Args:
        text: Normalized stream name

    Returns:
        True if stream is a placeholder
    """
    if not text:
        return True

    text_lower = text.lower().strip()

    # Check against placeholder patterns via service
    if DetectionKeywordService.is_placeholder(text_lower):
        return True

    # Additional check: very short names with just numbers
    if re.match(r"^[\d\s\-:]+$", text_lower):
        return True

    return False


# =============================================================================
# GAME SEPARATOR DETECTION
# =============================================================================


def find_game_separator(text: str) -> tuple[str | None, int]:
    """Find game separator in stream name.

    Args:
        text: Stream name (should be normalized)

    Returns:
        Tuple of (separator found, position) or (None, -1)
    """
    if not text:
        return None, -1

    return DetectionKeywordService.find_separator(text)


def extract_teams_from_separator(
    text: str, separator: str, sep_position: int
) -> tuple[str | None, str | None]:
    """Extract team names from a stream with a separator.

    Args:
        text: Stream name
        separator: The separator found (e.g., " vs ")
        sep_position: Position of separator in text

    Returns:
        Tuple of (team1, team2)
    """
    if sep_position < 0:
        return None, None

    team1 = text[:sep_position].strip()
    team2 = text[sep_position + len(separator) :].strip()

    # Clean up teams (remove DATE_MASK, TIME_MASK, trailing punctuation)
    team1 = _clean_team_name(team1)
    team2 = _clean_team_name(team2)

    # Validate: both teams should have substance
    # Minimum 3 chars - even short team abbrevs are 3+ (USC, LSU, BYU, etc.)
    if not team1 or len(team1) < 3:
        team1 = None
    if not team2 or len(team2) < 3:
        team2 = None

    return team1, team2


def _clean_team_name(name: str) -> str:
    """Clean extracted team name."""
    if not name:
        return ""

    # Normalize newlines and carriage returns to spaces
    # Some streams have literal newlines: "NFL\n01: Bills vs Broncos"
    name = re.sub(r"[\r\n]+", " ", name)

    # Truncate at "//" which is often used as timezone separator
    # "Indiana Pacers // UK Wed 14 Jan" → "Indiana Pacers"
    if " // " in name:
        name = name.split(" // ")[0]

    # Remove datetime masks
    name = re.sub(r"\bDATE_MASK\b", "", name)
    name = re.sub(r"\bTIME_MASK\b", "", name)

    # Remove parentheses left empty/near-empty after datetime mask removal
    # Handles: () (   ) (:05) (  -- ) (  --  :40) etc.
    name = re.sub(r"\(\s*[\s:\-]*\d{0,2}\s*\)", "", name)

    # Clean up "@ ET", "@ EST", "@ PT", etc. at end
    name = re.sub(r"\s*@\s*[A-Z]{2,4}T?\s*$", "", name, flags=re.IGNORECASE)

    # Remove standalone timezone codes (ET, EST, PT, PST, CT, CST, MT, MST, etc.)
    # These can remain after date/time stripping: "Jan 17 5PM ET" → "ET"
    name = re.sub(
        r"^(E|P|C|M)(S|D)?T$",  # ET, EST, EDT, PT, PST, PDT, CT, CST, CDT, MT, MST, MDT
        "",
        name,
        flags=re.IGNORECASE,
    )

    # Remove trailing punctuation (NOT digits - they could be team names like 49ers, 76ers)
    name = re.sub(r"[\s\-:.,@]+$", "", name)

    # Remove channel numbers like "(1)" or "[2]"
    name = re.sub(r"\s*[\(\[]\d+[\)\]]\s*$", "", name)

    # Remove HD, SD, 4K, UHD quality indicators (at start or end)
    name = re.sub(r"^\s*\b(HD|SD|FHD|4K|UHD)\b\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+\b(HD|SD|FHD|4K|UHD)\b\s*$", "", name, flags=re.IGNORECASE)

    # Remove broadcast network indicators like (CBS), (FOX), (ABC), (NBC), (ESPN)
    name = re.sub(
        r"\s*\((CBS|FOX|ABC|NBC|ESPN|ESPN2|TNT|TBS|FS1|FS2|NBCSN|USA|PEACOCK)\)\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    # Strip round/competition indicators at end of team names
    round_pattern = r"""
        \s*\(
        (?:
            (?:Round|Rd|Rnd|R)\s*\d+\w*  |  # Round 3, Rd 3, R3
            \d+(?:st|nd|rd|th)?\s*(?:Round|Rd|Leg)  |  # 3rd Round, 1st Leg
            (?:First|Second|Third|Fourth|Fifth)\s*(?:Round|Leg)  |  # Third Round
            (?:Group|Grp|Gr)\s*\w*  |  # Group A, Group Stage
            (?:Matchday|MD|Week|Wk)\s*\d*  |  # Matchday 5, MD5, Week 10
            (?:Leg|Game)\s*(?:One|Two|\d+)  |  # Leg 1, Leg One
            (?:Quarter|Semi|Half)?-?(?:Final|Finals)  |  # Final, Semi-Final
            (?:QF|SF|F)  |  # QF, SF, F
            (?:Play-?off|Play-?offs)  |  # Playoff, Play-off
            (?:Qualifying|Qual|Q)\d*  |  # Qualifying, Q1
            (?:Prelim|Preliminary)  |  # Preliminary
            (?:1H|2H|OT|ET)  |  # 1st half, overtime, extra time markers
            (?:Live|LIVE|Replay|Encore)  # Broadcast markers
        )
        \s*\)
    """
    name = re.sub(round_pattern, "", name, flags=re.IGNORECASE | re.VERBOSE)

    # Handle "|" separator - preserve pipe content for fuzzy matching disambiguation
    # The matcher will try both sides of the pipe and pick the one that matches.
    # Here we only strip OBVIOUS prefix noise (league hints, channel numbers) from the
    # start, keeping the rest intact for the matcher to disambiguate.
    # "NFL | Bills vs Broncos" → "Bills vs Broncos" (NFL is league hint)
    # "Montreal Canadiens | Bell Centre" → "Montreal Canadiens | Bell Centre" (pass through)
    if "|" in name:
        parts = name.split("|")
        first_part = parts[0].strip()
        rest = "|".join(parts[1:]).strip()

        # Check if first part is a known prefix (league hint) that should be stripped
        # Use existing detection - no hardcoded lists
        first_is_league = detect_league_hint(first_part + ":") is not None
        first_is_sport = detect_sport_hint(first_part) is not None

        # Check if first part is a provider/channel prefix pattern
        # Handles: "US (Paramount 010)", "UK (Sky Sports 042)", "CA (TSN 3)"
        first_is_provider = bool(re.match(r"^[A-Z]{2,3}\s*\(.*\d+\)$", first_part, re.IGNORECASE))

        # Also strip if first part is mostly datetime placeholders
        first_stripped = re.sub(r"\bDATE_MASK\b", "", first_part)
        first_stripped = re.sub(r"\bTIME_MASK\b", "", first_stripped)
        first_stripped = re.sub(r"\b[ECPM][SD]?T\b", "", first_stripped, flags=re.IGNORECASE)
        first_stripped = re.sub(r"[\s\-:.,]+", " ", first_stripped).strip()
        first_is_datetime_noise = len(first_stripped) < 3

        if first_is_league or first_is_sport or first_is_datetime_noise or first_is_provider:
            # First part is prefix noise - take the rest
            # Check for colon in rest (show name prefix pattern)
            if ":" in rest:
                after_colon = rest.split(":")[-1].strip()
                if after_colon and len(after_colon) >= 3:
                    name = after_colon
                else:
                    name = rest
            else:
                name = rest
        # else: keep the full pipe-separated string for matcher disambiguation

    # Strip channel number prefixes like "02 -", "15 -", "142 -" at the start
    name = re.sub(r"^\d+\s*-\s*", "", name)

    # Strip leading channel numbers like "02 :", "15 :", "142 :"
    name = re.sub(r"^\d+\s*:\s*", "", name)

    # Strip 1-2 digit channel numbers followed by whitespace only (no dash/colon)
    # "01 Bills" → "Bills", "03 49ers" → "49ers"
    # Safe because after separator split, a leading 1-2 digit number + space is a channel number
    name = re.sub(r"^\d{1,2}\s+", "", name)

    # Strip numbered channel prefixes like "NFL Game Pass 03:", "ESPN+ 45:"
    name = re.sub(r"^[A-Za-z][A-Za-z\s+]*\d*:\s*", "", name)

    # Strip show name prefixes like "MNF Playbook:", "NFL RedZone:"
    prev = None
    while prev != name:
        prev = name
        name = re.sub(r"^[A-Z][A-Za-z\s]+:\s*", "", name)

    # Strip common league abbreviations at start (even without colon)
    # "NFL Bills" → "Bills", "NBA 03 Lakers" → "03 Lakers"
    # This handles streams without pipe separators like "NFL 03 3PM Texans at Patriots"
    name = re.sub(
        r"^(NFL|NBA|MLB|NHL|MLS|NCAAF|NCAAB|NCAAW|WNBA|EPL|UCL|UFC|MMA)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    )

    # Re-strip channel numbers in case league prefix revealed one
    # "NFL 03 Bills" → after league strip: "03 Bills" → "Bills"
    name = re.sub(r"^\d{1,2}\s+", "", name)

    # Remove leading punctuation and whitespace
    name = re.sub(r"^[\s\-:.,]+", "", name)

    # NOW remove unmasked time patterns at the start (e.g., "3PM Texans" → "Texans")
    # This must happen AFTER prefix stripping so the time is actually at the start
    name = re.sub(r"^\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*", "", name, flags=re.IGNORECASE)

    # Final cleanup of leading/trailing whitespace
    return name.strip()


# =============================================================================
# LEAGUE HINT DETECTION
# =============================================================================


def detect_league_hint(text: str) -> str | list[str] | None:
    """Detect league from stream name patterns.

    Examples:
        "NHL: Bruins vs Rangers" → "nhl"
        "EPL - Arsenal vs Chelsea" → "eng.1"
        "UFC 315: Main Card" → "ufc"
        "EFL: Portsmouth vs Southampton" → ["eng.2", "eng.3", "eng.4"]

    Args:
        text: Stream name (should be normalized)

    Returns:
        League code (str), list of league codes (for umbrella brands), or None
    """
    if not text:
        return None

    return DetectionKeywordService.detect_league(text)


def detect_sport_hint(text: str) -> str | None:
    """Detect sport type from stream name.

    Unlike league hints which only match at start of string,
    sport hints can match anywhere (e.g., "Ice Hockey" in the middle).

    Examples:
        "US (BTN+) | Ice Hockey (W): Minnesota at Wisconsin" → "Hockey"
        "ESPN: NFL Sunday Football" → "Football"
        "Basketball: Lakers vs Celtics" → "Basketball"

    Args:
        text: Stream name (should be normalized)

    Returns:
        Sport name matching leagues.sport column if detected, None otherwise
    """
    if not text:
        return None

    return DetectionKeywordService.detect_sport(text)


# =============================================================================
# EVENT CARD DETECTION
# =============================================================================


def is_event_card(text: str, league_event_type: str | None = None) -> bool:
    """Check if stream is a combat sports event card (UFC, MMA, Boxing).

    Args:
        text: Normalized stream name
        league_event_type: Optional event_type from leagues table

    Returns:
        True if stream is a combat sports event card
    """
    if not text:
        return False

    # If we know the league type, use that
    if league_event_type == "event_card":
        return True

    return DetectionKeywordService.is_combat_sport(text)


def extract_event_card_hint(text: str) -> str | None:
    """Extract event card identifier (e.g., "UFC 315").

    Args:
        text: Stream name

    Returns:
        Event identifier if found, None otherwise
    """
    if not text:
        return None

    # UFC 315, UFC FN 123, etc.
    ufc_match = re.search(r"\b(ufc\s*(?:fn|fight\s*night)?\s*\d+)\b", text, re.IGNORECASE)
    if ufc_match:
        return ufc_match.group(1).upper().replace("  ", " ")

    # PFL 5, Bellator 300, etc.
    org_match = re.search(r"\b((?:pfl|bellator|one\s*fc)\s*\d+)\b", text, re.IGNORECASE)
    if org_match:
        return org_match.group(1).upper()

    # Boxing event names are less standardized - look for main event patterns
    if any(kw in text.lower() for kw in EVENT_CARD_KEYWORDS.get("boxing", [])):
        # Try to extract fighter names or event name
        # For now, just return a generic hint
        return "BOXING_EVENT"

    return None


def detect_card_segment(text: str) -> str | None:
    """Detect card segment from stream name (UFC, MMA).

    Segments:
    - "early_prelims": Early prelims / pre-show
    - "prelims": Regular prelims / preliminary card
    - "main_card": Main card / main event
    - "combined": Prelims + Mains combined stream

    Examples:
        "UFC 324 (Prelims)" → "prelims"
        "Gaethje vs Pimblett (Early Prelims)" → "early_prelims"
        "UFC 324 - Gaethje vs. Pimblett" → None (defaults to main_card later)
        "UFC 324: Main English" → "main_card"

    Args:
        text: Stream name (original, not normalized - for accurate pattern matching)

    Returns:
        Segment code or None if no segment detected
    """
    if not text:
        return None

    segment = DetectionKeywordService.detect_card_segment(text)
    if segment:
        logger.debug("[CLASSIFY] Detected card segment '%s' from '%s'", segment, text[:50])
    return segment


def is_combat_sports_excluded(text: str) -> bool:
    """Check if stream should be excluded from combat sports matching.

    Excludes weigh-ins, press conferences, countdowns, and other non-event content.

    Args:
        text: Stream name

    Returns:
        True if stream should be excluded
    """
    if not text:
        return False

    is_excluded = DetectionKeywordService.is_excluded(text)
    if is_excluded:
        logger.debug("[CLASSIFY] Combat sports excluded: %s", text[:50])
    return is_excluded


def extract_fighters_from_event_card(text: str) -> tuple[str | None, str | None]:
    """Extract fighter names from an EVENT_CARD stream.

    Uses the same separator logic as team extraction but handles fighter-specific
    patterns like "Gaethje vs Pimblett" or "Gaethje v Pimblett".

    Args:
        text: Stream name (normalized)

    Returns:
        Tuple of (fighter1, fighter2)
    """
    if not text:
        return None, None

    # Find separator and extract fighters
    separator, sep_position = find_game_separator(text)
    if separator:
        fighter1, fighter2 = extract_teams_from_separator(text, separator, sep_position)

        # Clean up fighter names - strip segment suffixes and event prefixes
        fighter1 = _clean_fighter_name(fighter1) if fighter1 else None
        fighter2 = _clean_fighter_name(fighter2) if fighter2 else None

        if fighter1 or fighter2:
            return fighter1, fighter2

    return None, None


def _clean_fighter_name(name: str) -> str | None:
    """Clean extracted fighter name for UFC/MMA matching.

    Strips segment suffixes, event prefixes, and other noise specific to
    combat sports streams.

    Args:
        name: Raw extracted fighter name

    Returns:
        Cleaned fighter name or None if nothing remains
    """
    if not name:
        return None

    # Strip segment suffixes: (Prelims), (Main Card 1), etc.
    for pattern, _segment in DetectionKeywordService.get_card_segment_patterns():
        name = pattern.sub("", name)

    # Strip empty parentheses left after segment removal
    name = re.sub(r"\(\s*\)", "", name)

    # Strip UFC event number prefix: "324 - Gaethje" → "Gaethje"
    # Also handles "UFC 324 Gaethje"
    name = re.sub(r"^(?:ufc\s+)?\d+\s*[-:]?\s*", "", name, flags=re.IGNORECASE)

    # Strip "UFC" prefix
    name = re.sub(r"^ufc\s+", "", name, flags=re.IGNORECASE)

    # Strip channel prefixes like "LIVE EVENT 03 -"
    name = re.sub(r"^live\s+event\s+\d+\s*[-:]\s*", "", name, flags=re.IGNORECASE)

    # Strip time prefixes like "9PM"
    name = re.sub(r"^\d{1,2}\s*(?:AM|PM)\s*", "", name, flags=re.IGNORECASE)

    # Strip common noise words at start
    name = re.sub(r"^(?:main\s+english|english|prelims?)\s*:?\s*", "", name, flags=re.IGNORECASE)

    # Clean up whitespace and punctuation
    name = re.sub(r"[\s\-:]+$", "", name)
    name = re.sub(r"^[\s\-:]+", "", name)
    name = name.strip()

    # Must have at least 2 characters to be a valid fighter name
    if len(name) < 2:
        return None

    return name


# =============================================================================
# MAIN CLASSIFICATION
# =============================================================================


def classify_stream(
    stream_name: str,
    league_event_type: str | None = None,
    custom_regex: CustomRegexConfig | None = None,
) -> ClassifiedStream:
    """Classify a stream for matching strategy selection.

    Classification order:
    1. Normalize stream name
    1b. Apply custom date/time regex (if configured) to override extracted values
    2. Check for event card keywords/type → EVENT_CARD
    3. Try custom regex for team extraction (if configured) → TEAM_VS_TEAM
    4. Check for game separator (vs/@/at) → TEAM_VS_TEAM
    5. Default → PLACEHOLDER (can't classify)

    Note: Placeholder pattern detection is now handled by StreamFilter before
    streams reach the classifier. This classifier focuses purely on categorizing
    streams that have passed eligibility filtering.

    Args:
        stream_name: Raw stream name to classify
        league_event_type: Optional event_type from leagues table (e.g., "fight" for UFC)
        custom_regex: Optional custom regex configuration for team/date/time extraction

    Returns:
        ClassifiedStream with category and extracted info
    """
    # Step 1: Normalize
    normalized = normalize_stream(stream_name)
    result: ClassifiedStream | None = None

    # Step 1b: Apply custom date/time regex to override built-in extraction
    # Uses ORIGINAL stream name (not normalized) for more flexible matching
    if custom_regex:
        if custom_regex.date_enabled:
            custom_date = extract_date_with_custom_regex(stream_name, custom_regex)
            if custom_date:
                normalized.extracted_date = custom_date
                logger.debug(
                    "[CLASSIFY] Custom date regex extracted: %s from '%s'",
                    custom_date,
                    stream_name[:50],
                )

        if custom_regex.time_enabled:
            custom_time = extract_time_with_custom_regex(stream_name, custom_regex)
            if custom_time:
                normalized.extracted_time = custom_time
                logger.debug(
                    "[CLASSIFY] Custom time regex extracted: %s from '%s'",
                    custom_time,
                    stream_name[:50],
                )

    # Early exit for empty streams
    if not normalized.normalized:
        result = ClassifiedStream(
            category=StreamCategory.PLACEHOLDER,
            normalized=normalized,
        )
    else:
        text = normalized.normalized

        # Detect league and sport hints (useful for all categories)
        league_hint = detect_league_hint(text)
        sport_hint = detect_sport_hint(text)

        # Step 1c: Apply custom league regex to override built-in detection
        # Uses ORIGINAL stream name (not normalized) for more flexible matching
        if custom_regex and custom_regex.league_enabled:
            custom_league = extract_league_with_custom_regex(stream_name, custom_regex)
            if custom_league:
                league_hint = custom_league
                logger.debug(
                    "[CLASSIFY] Custom league regex extracted: %s from '%s'",
                    custom_league,
                    stream_name[:50],
                )

        # Step 2: Check for event card
        if is_event_card(text, league_event_type):
            event_hint = extract_event_card_hint(text)

            # Detect card segment (early_prelims, prelims, main_card, combined)
            # Use original stream name for more accurate pattern matching
            card_segment = detect_card_segment(stream_name)

            # Try custom regex for fighters first (if configured)
            custom_regex_used = False
            fighter1, fighter2 = None, None
            if custom_regex and custom_regex.fighters_enabled:
                fighter1, fighter2, success = extract_fighters_with_custom_regex(
                    stream_name, custom_regex
                )
                if success:
                    custom_regex_used = True
                    logger.debug(
                        "[CLASSIFY] Custom fighters regex matched: %s vs %s",
                        fighter1,
                        fighter2,
                    )

            # Fallback to builtin extraction if custom regex didn't match
            if not fighter1 and not fighter2:
                fighter1, fighter2 = extract_fighters_from_event_card(text)

            # Try custom regex for event name (if configured)
            custom_event_name = None
            if custom_regex and custom_regex.event_name_enabled:
                custom_event_name = extract_event_name_with_custom_regex(
                    stream_name, custom_regex
                )
                if custom_event_name:
                    event_hint = custom_event_name
                    custom_regex_used = True
                    logger.debug(
                        "[CLASSIFY] Custom event_name regex matched: %s", custom_event_name
                    )

            result = ClassifiedStream(
                category=StreamCategory.EVENT_CARD,
                normalized=normalized,
                team1=fighter1,  # Fighter 1 (treated as team for matching)
                team2=fighter2,  # Fighter 2 (treated as team for matching)
                event_hint=event_hint,
                card_segment=card_segment,
                league_hint=league_hint,
                sport_hint=sport_hint,
                custom_regex_used=custom_regex_used,
            )

        # Step 3: Try custom regex for team extraction (if configured)
        # Uses ORIGINAL stream name (not normalized) for intuitive pattern matching
        if result is None and custom_regex and custom_regex.teams_enabled:
            team1, team2, success = extract_teams_with_custom_regex(stream_name, custom_regex)
            if success:
                result = ClassifiedStream(
                    category=StreamCategory.TEAM_VS_TEAM,
                    normalized=normalized,
                    team1=team1,
                    team2=team2,
                    separator_found="custom_regex",
                    league_hint=league_hint,
                    sport_hint=sport_hint,
                    custom_regex_used=True,
                )

        # Step 4: Check for game separator (builtin fallback)
        if result is None:
            separator, sep_position = find_game_separator(text)
            if separator:
                team1, team2 = extract_teams_from_separator(text, separator, sep_position)

                # Only classify as TEAM_VS_TEAM if we got at least one team
                if team1 or team2:
                    result = ClassifiedStream(
                        category=StreamCategory.TEAM_VS_TEAM,
                        normalized=normalized,
                        team1=team1,
                        team2=team2,
                        separator_found=separator,
                        league_hint=league_hint,
                        sport_hint=sport_hint,
                    )

        # Step 5: Default to placeholder if we can't classify
        if result is None:
            result = ClassifiedStream(
                category=StreamCategory.PLACEHOLDER,
                normalized=normalized,
                league_hint=league_hint,
                sport_hint=sport_hint,
            )

    logger.debug(
        "[CLASSIFY] '%s' -> %s (league=%s, sport=%s, teams=%s/%s, segment=%s)",
        stream_name[:50],
        result.category.value,
        result.league_hint,
        result.sport_hint,
        result.team1,
        result.team2,
        result.card_segment,
    )

    return result


def classify_streams(
    stream_names: list[str],
    league_event_type: str | None = None,
    custom_regex: CustomRegexConfig | None = None,
) -> list[ClassifiedStream]:
    """Classify multiple streams.

    Args:
        stream_names: List of raw stream names
        league_event_type: Optional event_type from leagues table
        custom_regex: Optional custom regex configuration for team extraction

    Returns:
        List of ClassifiedStream objects
    """
    return [classify_stream(name, league_event_type, custom_regex) for name in stream_names]
