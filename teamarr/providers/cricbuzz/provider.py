"""Cricbuzz sports data provider.

Normalizes Cricbuzz scraped data into our dataclass format.
Used for cricket leagues (IPL, CPL, BPL, BBL, etc.).

Layer Separation:
- Does NOT access database directly
- Uses LeagueMappingSource (injected) for league routing
- Normalizes all data to core types (Team, Event, etc.)
- Health monitoring is self-contained
"""

import logging
from datetime import UTC, date, datetime

from teamarr.core import (
    Event,
    EventStatus,
    LeagueMappingSource,
    SportsProvider,
    Team,
    TeamStats,
    Venue,
)
from teamarr.providers.cricbuzz.client import CricbuzzClient

logger = logging.getLogger(__name__)


class CricbuzzProvider(SportsProvider):
    """Cricbuzz implementation of SportsProvider.

    Provides cricket data by scraping Cricbuzz.com.
    Handles leagues like IPL, CPL, BPL, BBL.
    """

    def __init__(
        self,
        league_mapping_source: LeagueMappingSource | None = None,
        client: CricbuzzClient | None = None,
    ):
        self._league_mapping_source = league_mapping_source
        self._client = client or CricbuzzClient(
            league_mapping_source=league_mapping_source,
        )

    @property
    def name(self) -> str:
        return "cricbuzz"

    def supports_league(self, league: str) -> bool:
        return self._client.supports_league(league)

    def get_events(self, league: str, target_date: date) -> list[Event]:
        """Get events for a league on a specific date.

        Args:
            league: League code (e.g., "ipl", "cpl")
            target_date: Date to get events for

        Returns:
            List of Event objects
        """
        date_str = target_date.strftime("%Y-%m-%d")
        matches = self._client.get_events_by_date(league, date_str)

        events = []
        for match_data in matches:
            event = self._parse_event(match_data, league)
            if event:
                events.append(event)

        return events

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Get upcoming schedule for a team.

        Args:
            team_id: Cricbuzz team ID
            league: League code
            days_ahead: Number of days to look ahead

        Returns:
            List of Event objects for this team
        """
        matches = self._client.get_team_schedule(league, team_id, days_ahead)

        events = []
        for match_data in matches:
            event = self._parse_event(match_data, league)
            if event:
                events.append(event)

        # Sort by start time
        events.sort(key=lambda e: e.start_time)
        return events

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details.

        Looks up team from series teams list.

        Args:
            team_id: Cricbuzz team ID
            league: League code

        Returns:
            Team object or None if not found
        """
        config = self._client.get_league_config(league)
        if not config:
            return None

        series_id, series_slug = config
        teams = self._client.get_series_teams(series_id, series_slug)

        team_id_int = int(team_id)
        for team_data in teams:
            if team_data.get("teamId") == team_id_int:
                return self._parse_team(team_data, league)

        return None

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get team statistics.

        Cricbuzz doesn't provide team stats in a structured way.
        Returns None.
        """
        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID.

        Searches the series schedule for the match.

        Args:
            event_id: Cricbuzz match ID
            league: League code

        Returns:
            Event object or None if not found
        """
        config = self._client.get_league_config(league)
        if not config:
            return None

        series_id, series_slug = config
        matches = self._client.get_series_schedule(series_id, series_slug)

        match_id_int = int(event_id)
        for match_data in matches:
            if match_data.get("matchId") == match_id_int:
                return self._parse_event(match_data, league)

        return None

    def get_teams_in_league(self, league: str) -> list[Team]:
        """Get all teams in a league.

        Args:
            league: League code

        Returns:
            List of Team objects
        """
        config = self._client.get_league_config(league)
        if not config:
            logger.warning(f"Unknown Cricbuzz league: {league}")
            return []

        series_id, series_slug = config
        teams_data = self._client.get_series_teams(series_id, series_slug)

        teams = []
        for team_data in teams_data:
            team = self._parse_team(team_data, league)
            if team:
                teams.append(team)

        return teams

    def get_league_teams(self, league: str) -> list[Team]:
        """Get all teams in a league.

        Alias for get_teams_in_league() for consistent interface.
        Used by cache refresh.
        """
        return self.get_teams_in_league(league)

    def get_supported_leagues(self) -> list[str]:
        """Get all leagues this provider supports.

        Uses the league mapping source for all enabled Cricbuzz league mappings.
        """
        if not self._league_mapping_source:
            return []
        mappings = self._league_mapping_source.get_leagues_for_provider("cricbuzz")
        return [m.league_code for m in mappings]

    def health_check(self) -> dict:
        """Get provider health status.

        Exposes client health monitoring.
        Use this to detect when Cricbuzz site structure changes.
        """
        return self._client.health_check()

    def _parse_event(self, data: dict, league: str) -> Event | None:
        """Parse Cricbuzz match data into Event dataclass."""
        try:
            match_id = data.get("matchId")
            if not match_id:
                return None

            # Parse start time (Unix timestamp in milliseconds)
            start_ts = data.get("startDate")
            if not start_ts:
                return None

            if isinstance(start_ts, str):
                start_ts = int(start_ts)

            start_time = datetime.fromtimestamp(start_ts / 1000, tz=UTC)

            # Parse end time if available
            end_ts = data.get("endDate")
            if end_ts:
                if isinstance(end_ts, str):
                    end_ts = int(end_ts)
                # end_time = datetime.fromtimestamp(end_ts / 1000, tz=UTC)

            # Parse teams
            team1_data = data.get("team1", {})
            team2_data = data.get("team2", {})

            home_team = self._parse_team(team1_data, league)
            away_team = self._parse_team(team2_data, league)

            if not home_team or not away_team:
                logger.warning(f"Could not parse teams for match {match_id}")
                return None

            # Parse status
            status = self._parse_status(data)

            # Parse venue
            venue = self._parse_venue(data.get("venueInfo", {}))

            # Build event name
            series_name = data.get("seriesName", "")
            match_desc = data.get("matchDesc", "")
            event_name = f"{away_team.name} vs {home_team.name}"
            if match_desc:
                event_name = f"{event_name} - {match_desc}"

            short_name = f"{away_team.abbreviation} vs {home_team.abbreviation}"

            return Event(
                id=str(match_id),
                provider=self.name,
                name=event_name,
                short_name=short_name,
                start_time=start_time,
                home_team=home_team,
                away_team=away_team,
                status=status,
                league=league,
                sport="Cricket",
                venue=venue,
                broadcasts=[],  # Cricbuzz doesn't provide broadcast info
            )

        except Exception as e:
            logger.warning(f"Failed to parse Cricbuzz match {data.get('matchId', 'unknown')}: {e}")
            return None

    def _parse_team(self, data: dict, league: str) -> Team | None:
        """Parse team data dict into Team dataclass."""
        team_id = data.get("teamId")
        team_name = data.get("teamName")

        if not team_id or not team_name:
            return None

        short_name = data.get("teamSName", "")
        if not short_name:
            short_name = team_name[:3].upper()

        # Generate abbreviation
        abbreviation = short_name if len(short_name) <= 4 else self._make_abbrev(team_name)

        # Build logo URL from imageId
        image_id = data.get("imageId")
        logo_url = None
        if image_id:
            logo_url = f"https://static.cricbuzz.com/a/img/v1/i1/c{image_id}/i.jpg"

        return Team(
            id=str(team_id),
            provider=self.name,
            name=team_name,
            short_name=short_name,
            abbreviation=abbreviation,
            league=league,
            sport="Cricket",
            logo_url=logo_url,
            color=None,  # Cricbuzz doesn't provide team colors
        )

    def _parse_status(self, data: dict) -> EventStatus:
        """Parse event status from Cricbuzz data."""
        state = data.get("state", "").lower()
        status_text = data.get("status", "")

        # Map Cricbuzz states to our EventStatus
        if state in ("complete", "finished"):
            return EventStatus(state="final", detail=status_text)
        elif state in ("live", "inprogress", "innings break"):
            return EventStatus(state="live", detail=status_text)
        elif state in ("preview", "upcoming"):
            return EventStatus(state="scheduled", detail=None)
        elif state in ("delay", "delayed"):
            return EventStatus(state="delayed", detail=status_text)
        elif state in ("rain", "rain delay"):
            return EventStatus(state="delayed", detail=status_text or "Rain delay")
        elif state in ("abandon", "abandoned", "no result"):
            return EventStatus(state="cancelled", detail=status_text)
        elif state in ("postponed"):
            return EventStatus(state="postponed", detail=status_text)

        # Default to scheduled
        return EventStatus(state="scheduled", detail=status_text if status_text else None)

    def _parse_venue(self, data: dict) -> Venue | None:
        """Parse venue from Cricbuzz data."""
        ground = data.get("ground")
        if not ground:
            return None

        return Venue(
            name=ground,
            city=data.get("city"),
            state=None,  # Cricbuzz doesn't separate state
            country=None,  # Cricbuzz doesn't always include country
        )

    def _make_abbrev(self, team_name: str) -> str:
        """Generate abbreviation from team name."""
        if not team_name:
            return ""

        words = team_name.split()
        if len(words) >= 2:
            # Take first letter of each word
            return "".join(w[0] for w in words[:3]).upper()
        else:
            # Take first 3 letters
            return team_name[:3].upper()
