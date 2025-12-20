"""Channel lifecycle types and dataclasses.

Contains timing types, result dataclasses, and helper functions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# =============================================================================
# TIMING TYPES
# =============================================================================


CreateTiming = Literal[
    "stream_available",
    "same_day",
    "day_before",
    "2_days_before",
    "3_days_before",
    "1_week_before",
    "manual",
]

DeleteTiming = Literal[
    "stream_removed",
    "same_day",
    "day_after",
    "2_days_after",
    "3_days_after",
    "1_week_after",
    "manual",
]

DuplicateMode = Literal["consolidate", "separate", "ignore"]


# =============================================================================
# RESULT DATACLASSES
# =============================================================================


@dataclass
class LifecycleDecision:
    """Result of a lifecycle check."""

    should_act: bool
    reason: str
    threshold_time: datetime | None = None


@dataclass
class ChannelCreationResult:
    """Result of channel creation."""

    success: bool
    channel_id: int | None = None
    dispatcharr_channel_id: int | None = None
    channel_number: str | None = None
    tvg_id: str | None = None
    error: str | None = None


@dataclass
class StreamProcessResult:
    """Result of processing matched streams."""

    created: list[dict] = field(default_factory=list)
    existing: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    streams_added: list[dict] = field(default_factory=list)
    logo_updated: list[dict] = field(default_factory=list)
    settings_updated: list[dict] = field(default_factory=list)
    deleted: list[dict] = field(default_factory=list)

    def merge(self, other: "StreamProcessResult") -> None:
        """Merge another result into this one."""
        self.created.extend(other.created)
        self.existing.extend(other.existing)
        self.skipped.extend(other.skipped)
        self.errors.extend(other.errors)
        self.streams_added.extend(other.streams_added)
        self.logo_updated.extend(other.logo_updated)
        self.settings_updated.extend(other.settings_updated)
        self.deleted.extend(other.deleted)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "created": self.created,
            "existing": self.existing,
            "skipped": self.skipped,
            "errors": self.errors,
            "streams_added": self.streams_added,
            "logo_updated": self.logo_updated,
            "settings_updated": self.settings_updated,
            "deleted": self.deleted,
            "summary": {
                "created_count": len(self.created),
                "existing_count": len(self.existing),
                "skipped_count": len(self.skipped),
                "error_count": len(self.errors),
            },
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def generate_event_tvg_id(event_id: str, provider: str = "espn") -> str:
    """Generate consistent tvg_id for an event.

    This tvg_id is used:
    1. In XMLTV <channel id="..."> and <programme channel="...">
    2. When creating channels in Dispatcharr
    3. To look up EPGData for channel-EPG association

    Args:
        event_id: Provider event ID (e.g., "401547679")
        provider: Provider name (default: espn)

    Returns:
        Formatted tvg_id (e.g., "teamarr-event-401547679")
    """
    return f"teamarr-event-{event_id}"
