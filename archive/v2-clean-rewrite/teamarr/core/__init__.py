"""Core types and interfaces."""

from teamarr.core.interfaces import SportsProvider
from teamarr.core.types import (
    Event,
    EventStatus,
    Programme,
    Team,
    TeamStats,
    Venue,
)

__all__ = [
    "Event",
    "EventStatus",
    "Programme",
    "SportsProvider",
    "Team",
    "TeamStats",
    "Venue",
]
