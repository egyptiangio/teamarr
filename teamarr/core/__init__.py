"""Core types and interfaces."""

from teamarr.core.interfaces import LeagueMapping, LeagueMappingSource, SportsProvider
from teamarr.core.types import (
    GENERATED_PREVIEW_FIELDS,
    SEASON_OFFSEASON,
    SEASON_POSTSEASON,
    SEASON_PRESEASON,
    SEASON_REGULAR,
    Bout,
    Event,
    EventStatus,
    Programme,
    RacingResult,
    RacingSession,
    Team,
    TeamStats,
    TemplateConfig,
    Venue,
)

__all__ = [
    "Bout",
    "Event",
    "EventStatus",
    "GENERATED_PREVIEW_FIELDS",
    "LeagueMapping",
    "LeagueMappingSource",
    "Programme",
    "RacingResult",
    "RacingSession",
    "SEASON_OFFSEASON",
    "SEASON_POSTSEASON",
    "SEASON_PRESEASON",
    "SEASON_REGULAR",
    "SportsProvider",
    "Team",
    "TeamStats",
    "TemplateConfig",
    "Venue",
]
