"""Cache data types.

Dataclasses for cache entries and statistics.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CacheStats:
    """Cache refresh statistics."""

    last_refresh: datetime | None
    leagues_count: int
    teams_count: int
    refresh_duration_seconds: float
    is_stale: bool
    refresh_in_progress: bool
    last_error: str | None


@dataclass
class TeamEntry:
    """A team entry from the cache."""

    team_name: str
    team_abbrev: str | None
    team_short_name: str | None
    provider: str
    provider_team_id: str
    league: str
    sport: str
    logo_url: str | None


@dataclass
class LeagueEntry:
    """A league entry from the cache."""

    league_slug: str
    provider: str
    league_name: str | None
    sport: str
    logo_url: str | None
    team_count: int
    import_enabled: bool = False  # Show in team importer
