"""Core data types for Teamarr v2.

All data structures are dataclasses with attribute access.
Provider-scoped IDs: every entity carries its `id` and `provider`.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Venue:
    """Event location."""

    name: str
    city: str | None = None
    state: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class Team:
    """Team identity."""

    id: str
    provider: str
    name: str
    short_name: str
    abbreviation: str
    league: str
    sport: str  # e.g., "football", "basketball", "soccer"
    logo_url: str | None = None
    color: str | None = None


@dataclass(frozen=True)
class EventStatus:
    """Current state of an event."""

    state: str  # "scheduled" | "live" | "final" | "postponed" | "cancelled"
    detail: str | None = None
    period: int | None = None
    clock: str | None = None


@dataclass
class Event:
    """A single sporting event (game/match)."""

    id: str
    provider: str
    name: str
    short_name: str
    start_time: datetime
    home_team: Team
    away_team: Team
    status: EventStatus
    league: str
    sport: str  # e.g., "football", "basketball", "soccer"

    home_score: int | None = None
    away_score: int | None = None
    venue: Venue | None = None
    broadcasts: list[str] = field(default_factory=list)
    season_year: int | None = None
    season_type: str | None = None

    # MMA-specific: when main card begins (prelims start at start_time)
    main_card_start: datetime | None = None


@dataclass(frozen=True)
class TeamStats:
    """Team statistics for template variables.

    Record fields store formatted strings like "10-2" or "8-3-1".
    Numeric fields store parsed values for calculations.
    """

    # Overall record
    record: str  # "10-2" or "8-3-1" (W-L or W-L-T)
    wins: int = 0
    losses: int = 0
    ties: int = 0

    # Home/away splits
    home_record: str | None = None
    away_record: str | None = None

    # Streak info
    streak: str | None = None  # "W3" or "L2" format
    streak_count: int = 0  # positive = wins, negative = losses

    # Rankings and standings
    rank: int | None = None  # College sports ranking (1-25, None if unranked)
    playoff_seed: int | None = None
    games_back: float | None = None

    # Conference/division
    conference: str | None = None  # Full name
    conference_abbrev: str | None = None
    division: str | None = None

    # Scoring stats
    ppg: float | None = None  # Points per game
    papg: float | None = None  # Points allowed per game


@dataclass
class Programme:
    """An XMLTV programme entry."""

    channel_id: str
    title: str
    start: datetime
    stop: datetime
    description: str | None = None
    subtitle: str | None = None
    category: str | None = None
    icon: str | None = None
    episode_num: str | None = None
