"""UFC card segment handling.

Expands UFC events into segment-based channels (Early Prelims, Prelims, Main Card).
Streams are routed to correct segment channel based on detected card_segment.

Segment timing comes from ESPN bout-level data:
- PPV events: 3 segments (early_prelims, prelims, main_card)
- Fight Night: 2 segments (prelims, main_card)
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from teamarr.consumers.matching.classifier import (
    ClassifiedStream,
    detect_card_segment,
    is_ufc_excluded,
)
from teamarr.core.types import Event

logger = logging.getLogger(__name__)

# Display names for segment suffixes in channel names
SEGMENT_DISPLAY_NAMES: dict[str, str] = {
    "early_prelims": "Early Prelims",
    "prelims": "Prelims",
    "main_card": "",  # Main card = no suffix (default channel)
    "combined": "",  # Combined streams go to main card channel
}

# Segment codes ordered from earliest to latest
SEGMENT_ORDER = ["early_prelims", "prelims", "main_card"]


def canonicalize_segment(detected: str, event: Event) -> str:
    """Validate detected segment against ESPN's segment_times.

    If ESPN has segment data, ensures the detected segment exists.
    If not, maps to the closest valid segment.

    Args:
        detected: Segment detected from stream name
        event: UFC Event with segment_times from ESPN

    Returns:
        Validated segment code that exists in ESPN's data
    """
    # If no ESPN segment data, trust the detection
    if not event.segment_times:
        return detected

    espn_segments = set(event.segment_times.keys())

    # If detected segment exists in ESPN data, use it
    if detected in espn_segments:
        return detected

    # Map to closest valid segment
    # Priority: try to find the next available segment in order
    detected_idx = SEGMENT_ORDER.index(detected) if detected in SEGMENT_ORDER else -1

    if detected_idx >= 0:
        # Try segments at same position or later first
        for segment in SEGMENT_ORDER[detected_idx:]:
            if segment in espn_segments:
                logger.info(
                    "[UFC_SEGMENTS] Mapped '%s' to '%s' (not in ESPN data: %s)",
                    detected,
                    segment,
                    sorted(espn_segments),
                )
                return segment
        # Fall back to earlier segments
        for segment in reversed(SEGMENT_ORDER[:detected_idx]):
            if segment in espn_segments:
                logger.info(
                    "[UFC_SEGMENTS] Mapped '%s' to '%s' (not in ESPN data: %s)",
                    detected,
                    segment,
                    sorted(espn_segments),
                )
                return segment

    # Last resort: use main_card if available, else first available
    if "main_card" in espn_segments:
        logger.warning("[UFC_SEGMENTS] Unknown segment '%s', defaulting to main_card", detected)
        return "main_card"

    fallback = next(iter(espn_segments))
    logger.warning("[UFC_SEGMENTS] Unknown segment '%s', defaulting to '%s'", detected, fallback)
    return fallback


def extract_time_from_stream(stream_name: str) -> time | None:
    """Extract time from stream name for segment disambiguation.

    Looks for common time patterns in stream names:
    - "5:30 PM", "5:30PM", "5:30pm"
    - "10pm", "10 pm", "10PM"
    - "22:30" (24-hour format)

    Args:
        stream_name: Raw stream name

    Returns:
        Extracted time or None
    """
    if not stream_name:
        return None

    # Pattern 1: 12-hour format with minutes - "5:30 PM", "5:30PM"
    match = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", stream_name, re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        ampm = match.group(3).upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        return time(hour, minute)

    # Pattern 2: 12-hour format without minutes - "10pm", "10 pm"
    match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", stream_name, re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        ampm = match.group(2).upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        return time(hour, 0)

    # Pattern 3: 24-hour format - "22:30"
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", stream_name)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        # Only use if it looks like a time (not like "UFC 324")
        # Times typically have hours >= 10 or are early morning (< 6)
        if hour >= 10 or hour < 6:
            return time(hour, minute)

    return None


@dataclass
class SegmentInfo:
    """Information about a UFC card segment."""

    code: str  # "early_prelims", "prelims", "main_card"
    display_name: str  # "Early Prelims", "Prelims", ""
    start_time: datetime
    end_time: datetime


def is_ufc_event(event: Event | None) -> bool:
    """Check if event is a UFC/MMA event that should have segment handling."""
    if not event:
        return False
    return event.sport == "mma" and event.league == "ufc"


def get_stream_segment(stream: dict, classified: ClassifiedStream | None = None) -> str | None:
    """Get segment code for a stream.

    Args:
        stream: Stream dict with 'name' key
        classified: Optional pre-classified stream with card_segment

    Returns:
        Segment code or None if no segment detected
    """
    # Use pre-classified segment if available
    if classified and classified.card_segment:
        return classified.card_segment

    # Detect from stream name
    stream_name = stream.get("name", "")
    return detect_card_segment(stream_name)


def should_exclude_stream(stream: dict) -> bool:
    """Check if UFC stream should be excluded (weigh-in, press conference, etc.)."""
    stream_name = stream.get("name", "")
    return is_ufc_excluded(stream_name)


def get_segment_display_suffix(segment: str | None) -> str:
    """Get display suffix for channel name.

    Args:
        segment: Segment code ("early_prelims", "prelims", "main_card")

    Returns:
        Display suffix (e.g., " - Early Prelims") or empty string
    """
    if not segment:
        return ""

    display = SEGMENT_DISPLAY_NAMES.get(segment, "")
    if display:
        return f" - {display}"
    return ""


def disambiguate_prelims_by_time(
    detected_segment: str,
    stream_time: time | None,
    event: Event,
) -> str:
    """Disambiguate "prelims" segment based on stream time.

    If a stream says "prelims" but has a time in its name that's closer to
    early_prelims, reassign to early_prelims.

    Stream times are assumed to be in user's local timezone. ESPN times (UTC)
    are converted to user timezone for comparison.

    Args:
        detected_segment: Segment detected from stream name ("prelims")
        stream_time: Time extracted from stream name (in local timezone)
        event: UFC Event with segment_times from ESPN (UTC)

    Returns:
        Disambiguated segment code
    """
    from teamarr.utilities.tz import to_user_tz

    # Only disambiguate "prelims" - other segments are unambiguous
    if detected_segment != "prelims":
        return detected_segment

    # Need stream time and ESPN segment data to disambiguate
    if not stream_time or not event.segment_times:
        return detected_segment

    # Need both early_prelims and prelims times for comparison
    early_prelims_dt = event.segment_times.get("early_prelims")
    prelims_dt = event.segment_times.get("prelims")

    if not early_prelims_dt or not prelims_dt:
        return detected_segment

    # Convert ESPN datetime (UTC) to user timezone, then extract time
    early_time = to_user_tz(early_prelims_dt).time()
    prelims_time = to_user_tz(prelims_dt).time()

    # Calculate time differences (in seconds from midnight)
    def time_to_seconds(t: time) -> int:
        return t.hour * 3600 + t.minute * 60 + t.second

    stream_secs = time_to_seconds(stream_time)
    early_secs = time_to_seconds(early_time)
    prelims_secs = time_to_seconds(prelims_time)

    # Distance from stream time to each segment time
    # Handle wrap-around at midnight (e.g., stream at 23:00, event at 01:00)
    def time_distance(t1_secs: int, t2_secs: int) -> int:
        diff = abs(t1_secs - t2_secs)
        # If difference > 12 hours, it's probably wrap-around
        return min(diff, 86400 - diff)

    dist_to_early = time_distance(stream_secs, early_secs)
    dist_to_prelims = time_distance(stream_secs, prelims_secs)

    # If stream time is closer to early_prelims, reassign
    if dist_to_early < dist_to_prelims:
        logger.info(
            "[UFC_SEGMENTS] Disambiguated 'prelims' to 'early_prelims' based on time "
            "(stream=%s, early=%s, prelims=%s)",
            stream_time,
            early_time,
            prelims_time,
        )
        return "early_prelims"

    return detected_segment


def get_segment_times(
    event: Event,
    segment: str,
    sport_durations: dict[str, float] | None = None,
) -> tuple[datetime, datetime]:
    """Get exact start/end times for a segment from ESPN bout-level data.

    Uses event.segment_times populated from ESPN API. Falls back to estimation
    only if ESPN data is not available (should be rare).

    Args:
        event: UFC Event with segment_times from ESPN
        segment: Segment code ("early_prelims", "prelims", "main_card")
        sport_durations: Optional duration settings (for fallback only)

    Returns:
        Tuple of (start_time, end_time)
    """
    mma_duration = (sport_durations or {}).get("mma", 5.0)

    # Use exact ESPN segment times if available
    if event.segment_times and segment in event.segment_times:
        start_time = event.segment_times[segment]

        # End time = next segment's start, or estimated duration for last segment
        segment_list = [s for s in SEGMENT_ORDER if s in event.segment_times]
        try:
            seg_idx = segment_list.index(segment)
            if seg_idx < len(segment_list) - 1:
                # Not the last segment - end at next segment's start
                next_segment = segment_list[seg_idx + 1]
                end_time = event.segment_times[next_segment]
            else:
                # Last segment - use estimated duration
                # Main card typically runs 2-3 hours
                end_time = start_time + timedelta(hours=mma_duration / 2)
        except ValueError:
            end_time = start_time + timedelta(hours=mma_duration / 3)

        return start_time, end_time

    # Fallback: estimate if no ESPN data (should be rare)
    logger.warning(
        "[UFC_SEGMENTS] No ESPN segment_times for event %s segment %s, using estimates",
        event.id,
        segment,
    )
    return _estimate_segment_times_fallback(event, segment, mma_duration)


def _estimate_segment_times_fallback(
    event: Event,
    segment: str,
    mma_duration: float,
) -> tuple[datetime, datetime]:
    """Fallback estimation when ESPN data is not available."""
    if event.main_card_start:
        if segment == "early_prelims":
            prelims_start = event.main_card_start - timedelta(hours=1.5)
            return event.start_time, prelims_start
        elif segment == "prelims":
            prelims_start = event.main_card_start - timedelta(hours=1.5)
            if event.start_time > prelims_start:
                prelims_start = event.start_time
            return prelims_start, event.main_card_start
        else:
            main_duration = timedelta(hours=mma_duration / 2)
            return event.main_card_start, event.main_card_start + main_duration

    # No main_card_start - crude estimation
    segment_duration = timedelta(hours=mma_duration / 3)
    if segment == "early_prelims":
        return event.start_time, event.start_time + segment_duration
    elif segment == "prelims":
        start = event.start_time + segment_duration
        return start, start + segment_duration
    else:
        start = event.start_time + 2 * segment_duration
        return start, start + segment_duration


def expand_ufc_segments(
    matched_streams: list[dict],
    sport_durations: dict[str, float] | None = None,
) -> list[dict]:
    """Expand UFC matched streams into segment-based channels.

    Groups UFC streams by detected segment and creates separate channel
    entries for each segment. Non-UFC streams pass through unchanged.

    Args:
        matched_streams: List of {'stream': ..., 'event': ...} dicts
        sport_durations: Optional sport duration settings

    Returns:
        Expanded list with UFC streams grouped by segment
    """
    result = []

    # Group UFC streams by event ID and segment
    # {event_id: {segment: [streams]}}
    ufc_by_segment: dict[str, dict[str, list[dict]]] = {}

    for match in matched_streams:
        event = match.get("event")
        stream = match.get("stream", {})

        # Non-UFC events pass through unchanged
        if not is_ufc_event(event):
            result.append(match)
            continue

        # Check for excluded streams (weigh-ins, etc.)
        if should_exclude_stream(stream):
            logger.debug(
                "[UFC_SEGMENTS] Excluding stream '%s' (non-event content)",
                stream.get("name", "")[:50],
            )
            continue

        # Use pre-detected segment from classifier, or detect from stream name
        segment = match.get("card_segment") or get_stream_segment(stream)

        # Default to main_card if no segment detected
        if not segment:
            segment = "main_card"

        # Combined streams go to main_card
        if segment == "combined":
            segment = "main_card"

        # Disambiguate "prelims" using time if available
        # Streams labeled "prelims" might actually be early prelims based on time
        if segment == "prelims":
            stream_name = stream.get("name", "")
            stream_time = extract_time_from_stream(stream_name)
            if stream_time:
                segment = disambiguate_prelims_by_time(segment, stream_time, event)

        # Validate against ESPN's segment data - ensures segment exists
        segment = canonicalize_segment(segment, event)

        event_id = event.id
        if event_id not in ufc_by_segment:
            ufc_by_segment[event_id] = {}
        if segment not in ufc_by_segment[event_id]:
            ufc_by_segment[event_id][segment] = []

        ufc_by_segment[event_id][segment].append(match)

    # Create segment entries for each UFC event
    for event_id, segments in ufc_by_segment.items():
        # Get the event from any stream (they all have the same event)
        first_match = next(iter(next(iter(segments.values()))))
        event = first_match.get("event")

        # Create entry for each discovered segment
        for segment in SEGMENT_ORDER:
            if segment not in segments:
                continue

            streams_for_segment = segments[segment]
            if not streams_for_segment:
                continue

            # Get exact segment timing from ESPN data
            start_time, end_time = get_segment_times(event, segment, sport_durations)

            # Create segment entry with metadata
            for match in streams_for_segment:
                segment_match = {
                    "stream": match.get("stream"),
                    "event": event,
                    "segment": segment,
                    "segment_display": SEGMENT_DISPLAY_NAMES.get(segment, ""),
                    "segment_start": start_time,
                    "segment_end": end_time,
                }
                result.append(segment_match)

            logger.debug(
                "[UFC_SEGMENTS] Event %s segment '%s': %d streams, %s - %s",
                event_id,
                segment,
                len(streams_for_segment),
                start_time.strftime("%H:%M"),
                end_time.strftime("%H:%M"),
            )

    # Log summary
    ufc_count = sum(len(streams) for segs in ufc_by_segment.values() for streams in segs.values())
    segment_count = sum(len(segs) for segs in ufc_by_segment.values())
    if ufc_count > 0:
        logger.info(
            "[UFC_SEGMENTS] Expanded %d UFC streams into %d segment channels",
            ufc_count,
            segment_count,
        )

    return result
