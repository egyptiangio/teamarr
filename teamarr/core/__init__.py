"""Core types and interfaces."""

from teamarr.core.interfaces import LeagueMapping, LeagueMappingSource, SportsProvider
from teamarr.core.types import (
    Event,
    EventStatus,
    Programme,
    Team,
    TeamStats,
    TemplateConfig,
    Venue,
)

__all__ = [
    "Event",
    "EventStatus",
    "LeagueMapping",
    "LeagueMappingSource",
    "Programme",
    "SportsProvider",
    "Team",
    "TeamStats",
    "TemplateConfig",
    "Venue",
]
