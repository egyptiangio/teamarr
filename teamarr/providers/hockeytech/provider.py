"""HockeyTech sports data provider.

Fetches data from HockeyTech API and normalizes into our dataclass format.
Used for CHL leagues (OHL, WHL, QMJHL) plus AHL, PWHL, USHL.
"""

import logging
import re
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
from teamarr.providers.hockeytech.client import HockeyTechClient

logger = logging.getLogger(__name__)


class HockeyTechProvider(SportsProvider):
    """HockeyTech implementation of SportsProvider.

    Handles CHL leagues (OHL, WHL, QMJHL) plus AHL, PWHL, USHL.
    """

    def __init__(
        self,
        league_mapping_source: LeagueMappingSource | None = None,
        client: HockeyTechClient | None = None,
    ):
        self._league_mapping_source = league_mapping_source
        self._client = client or HockeyTechClient(
            league_mapping_source=league_mapping_source,
        )

    @property
    def name(self) -> str:
        return "hockeytech"

    def supports_league(self, league: str) -> bool:
        return self._client.supports_league(league)

    def get_events(self, league: str, target_date: date) -> list[Event]:
        """Get events for a league on a specific date.

        Filters the full season schedule by date.
        """
        games = self._client.get_events_by_date(league, target_date)
        events = []
        for game in games:
            event = self._parse_event(game, league)
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

        Filters the full schedule for games where this team is home or away.
        """
        games = self._client.get_team_schedule(league, team_id, days_ahead)
        events = []
        for game in games:
            event = self._parse_event(game, league)
            if event:
                events.append(event)
        # Sort by start time
        events.sort(key=lambda e: e.start_time)
        return events

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details."""
        teams = self._client.get_teams(league)
        for team_data in teams:
            if str(team_data.get("id")) == str(team_id):
                return self._parse_team(team_data, league)
        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID."""
        game = self._client.get_game(league, event_id)
        if not game:
            return None
        return self._parse_event(game, league)

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get team statistics.

        HockeyTech has standings data but not as rich as ESPN.
        Returns None for consistency with TSDB.
        """
        return None

    def get_league_teams(self, league: str) -> list[Team]:
        """Get all teams in a league.

        Used by cache refresh.
        """
        teams_data = self._client.get_teams(league)
        teams = []
        for team_data in teams_data:
            team = self._parse_team(team_data, league)
            if team:
                teams.append(team)
        return teams

    def get_supported_leagues(self) -> list[str]:
        """Get all leagues this provider supports.

        Uses the league mapping source for all enabled HockeyTech league mappings.
        """
        if not self._league_mapping_source:
            return []
        mappings = self._league_mapping_source.get_leagues_for_provider("hockeytech")
        return [m.league_code for m in mappings]

    def _parse_event(self, game: dict, league: str) -> Event | None:
        """Parse HockeyTech game data into Event dataclass."""
        try:
            game_id = game.get("game_id")
            if not game_id:
                return None

            # Parse start time - HockeyTech provides ISO8601 with timezone
            start_time = self._parse_datetime(game)
            if not start_time:
                return None

            sport = self._client.get_sport(league)
            config = self._client.get_league_config(league)
            client_code = config[0] if config else league

            # Parse teams
            home_team = self._parse_team_from_game(game, "home", league, sport, client_code)
            away_team = self._parse_team_from_game(game, "visiting", league, sport, client_code)

            # Parse status
            status = self._parse_status(game)

            # Parse scores
            home_score = self._parse_score(game.get("home_goal_count"))
            away_score = self._parse_score(game.get("visiting_goal_count"))

            # Parse venue
            venue = self._parse_venue(game)

            # Parse broadcasts
            broadcasts = self._parse_broadcasts(game)

            # Build names
            event_name = f"{away_team.name} at {home_team.name}"
            short_name = f"{away_team.abbreviation} @ {home_team.abbreviation}"

            return Event(
                id=str(game_id),
                provider=self.name,
                name=event_name,
                short_name=short_name,
                start_time=start_time,
                home_team=home_team,
                away_team=away_team,
                status=status,
                league=league,
                sport=sport,
                home_score=home_score,
                away_score=away_score,
                venue=venue,
                broadcasts=broadcasts,
            )

        except Exception as e:
            logger.warning(
                "[HOCKEYTECH] Failed to parse game %s: %s", game.get("game_id", "unknown"), e
            )
            return None

    def _parse_team_from_game(
        self,
        game: dict,
        side: str,
        league: str,
        sport: str,
        client_code: str,
    ) -> Team:
        """Parse team data from game dict (home or visiting)."""
        team_id = game.get(f"{side}_team")
        city = game.get(f"{side}_team_city", "")
        nickname = game.get(f"{side}_team_nickname", "")
        code = game.get(f"{side}_team_code", "")

        # Build full name: "City Nickname" (e.g., "London Knights")
        name = f"{city} {nickname}".strip() if city and nickname else city or nickname

        # Logo URL pattern
        logo_url = None
        if team_id:
            logo_url = f"https://assets.leaguestat.com/{client_code}/logos/{team_id}.png"

        return Team(
            id=str(team_id) if team_id else "",
            provider=self.name,
            name=name,
            short_name=nickname or name,
            abbreviation=code or self._make_abbrev(name),
            league=league,
            sport=sport,
            logo_url=logo_url,
            color=None,  # Not available from HockeyTech
        )

    def _parse_team(self, team_data: dict, league: str) -> Team | None:
        """Parse team data from teamsbyseason response."""
        team_id = team_data.get("id")
        if not team_id:
            return None

        sport = self._client.get_sport(league)
        config = self._client.get_league_config(league)
        client_code = config[0] if config else league

        city = team_data.get("city", "")
        nickname = team_data.get("nickname", "")
        code = team_data.get("code", "")

        name = f"{city} {nickname}".strip() if city and nickname else team_data.get("name", "")
        logo_url = f"https://assets.leaguestat.com/{client_code}/logos/{team_id}.png"

        return Team(
            id=str(team_id),
            provider=self.name,
            name=name,
            short_name=nickname or name,
            abbreviation=code or self._make_abbrev(name),
            league=league,
            sport=sport,
            logo_url=logo_url,
            color=None,
        )

    def _parse_datetime(self, game: dict) -> datetime | None:
        """Parse game datetime from HockeyTech data.

        HockeyTech provides GameDateISO8601 with timezone info.
        """
        # Try ISO8601 timestamp first (most reliable)
        iso_str = game.get("GameDateISO8601")
        if iso_str:
            try:
                # Handle format like "2025-01-03T19:00:00-05:00"
                return datetime.fromisoformat(iso_str)
            except ValueError:
                pass

        # Fallback to date_played + time fields
        date_str = game.get("date_played")
        time_str = game.get("game_time") or game.get("scheduled_time")
        if date_str:
            try:
                if time_str:
                    dt = datetime.fromisoformat(f"{date_str}T{time_str}")
                else:
                    dt = datetime.fromisoformat(date_str)
                # Assume UTC if no timezone
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                pass

        return None

    def _parse_status(self, game: dict) -> EventStatus:
        """Parse game status from HockeyTech data.

        Status examples:
        - "7:00 PM EST" → scheduled
        - "15:23 1st", "2nd", "OT" → live
        - "Final", "Final OT", "Final SO" → final
        - "PPD" → postponed
        """
        status_str = game.get("game_status", "")

        if not status_str:
            return EventStatus(state="scheduled", detail=None)

        status_lower = status_str.lower()

        # Final states
        if status_lower.startswith("final"):
            return EventStatus(state="final", detail=status_str)

        # Postponed
        if status_lower in ("ppd", "postponed"):
            return EventStatus(state="postponed", detail="Postponed")

        # Cancelled
        if status_lower in ("cancelled", "canceled"):
            return EventStatus(state="cancelled", detail=status_str)

        # Live game - check for period indicators
        period_pattern = r"(1st|2nd|3rd|ot|so|\d+:\d+)"
        if re.search(period_pattern, status_lower):
            return EventStatus(state="live", detail=status_str)

        # Time pattern (scheduled) - e.g., "7:00 PM EST"
        time_pattern = r"\d{1,2}:\d{2}\s*(am|pm|AM|PM)"
        if re.search(time_pattern, status_str):
            return EventStatus(state="scheduled", detail=None)

        # Default to scheduled
        return EventStatus(state="scheduled", detail=status_str if status_str else None)

    def _parse_venue(self, game: dict) -> Venue | None:
        """Parse venue from HockeyTech data."""
        venue_name = game.get("venue_name")
        if not venue_name:
            return None

        # venue_location often contains "City, Province/State"
        location = game.get("venue_location", "")
        city = None
        state = None
        if location:
            parts = location.split(",")
            if len(parts) >= 1:
                city = parts[0].strip()
            if len(parts) >= 2:
                state = parts[1].strip()

        return Venue(
            name=venue_name,
            city=city,
            state=state,
            country=None,
        )

    def _parse_broadcasts(self, game: dict) -> list[str]:
        """Parse broadcast info from HockeyTech data."""
        broadcasts = []
        broadcasters = game.get("broadcasters", {})

        # Handle different broadcaster types
        for key in ["home", "away", "national"]:
            bc = broadcasters.get(key, [])
            if isinstance(bc, list):
                for b in bc:
                    if isinstance(b, dict):
                        name = b.get("name") or b.get("short_name")
                        if name and name not in broadcasts:
                            broadcasts.append(name)
                    elif isinstance(b, str) and b not in broadcasts:
                        broadcasts.append(b)

        return broadcasts

    def _parse_score(self, score) -> int | None:
        """Parse score value."""
        if score is None or score == "":
            return None
        try:
            return int(score)
        except (ValueError, TypeError):
            return None

    def _make_abbrev(self, team_name: str) -> str:
        """Generate abbreviation from team name."""
        if not team_name:
            return ""
        # Take first 3 letters of last word, uppercase
        words = team_name.split()
        if words:
            return words[-1][:3].upper()
        return ""
