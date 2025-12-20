"""Abstract interfaces for Teamarr v2.

Defines the contracts that providers must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from teamarr.core.types import Event, Team, TeamStats

# =============================================================================
# LEAGUE MAPPING - Used by providers to look up league configuration
# =============================================================================


@dataclass(frozen=True)
class LeagueMapping:
    """League mapping configuration.

    Maps canonical league codes to provider-specific identifiers.
    Immutable dataclass for thread safety.
    """

    league_code: str  # Canonical code: 'nfl', 'ohl', 'eng.1'
    provider: str  # 'espn' or 'tsdb'
    provider_league_id: str  # ESPN: 'football/nfl', TSDB: '5159'
    provider_league_name: str | None  # TSDB only: 'Canadian OHL'
    sport: str  # 'Football', 'Hockey', 'Soccer'
    display_name: str  # 'NFL', 'Ontario Hockey League'
    logo_url: str | None = None


class LeagueMappingSource(Protocol):
    """Protocol for league mapping lookup.

    Providers depend on this protocol, not the database directly.
    Implementations can be database-backed, cached, or mocked for testing.
    """

    def get_mapping(self, league_code: str, provider: str) -> LeagueMapping | None:
        """Get mapping for a specific league and provider."""
        ...

    def supports_league(self, league_code: str, provider: str) -> bool:
        """Check if provider supports the given league."""
        ...

    def get_leagues_for_provider(self, provider: str) -> list[LeagueMapping]:
        """Get all leagues supported by a provider."""
        ...


# =============================================================================
# SPORTS PROVIDER - Main provider interface
# =============================================================================


class SportsProvider(ABC):
    """Abstract base class for sports data providers.

    Providers fetch data from external APIs (ESPN, TheSportsDB, etc.)
    and normalize it into our dataclass format.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., 'espn', 'thesportsdb')."""
        ...

    @abstractmethod
    def supports_league(self, league: str) -> bool:
        """Check if this provider supports the given league."""
        ...

    @abstractmethod
    def get_events(self, league: str, date: date) -> list[Event]:
        """Get all events for a league on a given date.

        Args:
            league: League identifier (e.g., 'nfl', 'nba')
            date: Date to fetch events for

        Returns:
            List of events, empty list if none found or on error
        """
        ...

    @abstractmethod
    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Get upcoming schedule for a specific team.

        Args:
            team_id: Provider's team ID
            league: League identifier
            days_ahead: Number of days to look ahead

        Returns:
            List of upcoming events, empty list if none found or on error
        """
        ...

    @abstractmethod
    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details.

        Args:
            team_id: Provider's team ID
            league: League identifier

        Returns:
            Team if found, None otherwise
        """
        ...

    @abstractmethod
    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID.

        Args:
            event_id: Provider's event ID
            league: League identifier

        Returns:
            Event if found, None otherwise
        """
        ...

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get detailed team statistics.

        Fetches season stats including record, rankings, scoring averages,
        and conference/division info. Used for template variables.

        Args:
            team_id: Provider's team ID
            league: League identifier

        Returns:
            TeamStats if available, None otherwise

        Note:
            This method has a default implementation returning None.
            Providers should override if they support team stats.
        """
        return None

    def get_league_teams(self, league: str) -> list[Team]:
        """Get all teams in a league.

        Used by cache refresh to populate team_cache table.

        Args:
            league: League identifier

        Returns:
            List of teams in the league, empty list if not supported

        Note:
            This method has a default implementation returning empty list.
            Providers should override to support cache discovery.
        """
        return []

    def get_supported_leagues(self) -> list[str]:
        """Get list of leagues this provider can handle.

        Used by cache refresh to discover all leagues from this provider.

        Returns:
            List of league codes this provider supports

        Note:
            Default returns empty list. Providers should override
            to enable automatic cache discovery.
        """
        return []
