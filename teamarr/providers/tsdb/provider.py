"""TheSportsDB sports data provider.

Fetches data from TSDB API and normalizes into our dataclass format.
Used as fallback for leagues not supported by ESPN.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from teamarr.core import (
    Event,
    EventStatus,
    LeagueMappingSource,
    SportsProvider,
    Team,
    TeamStats,
    Venue,
)
from teamarr.providers.tsdb.client import TSDBClient

logger = logging.getLogger(__name__)


class TSDBProvider(SportsProvider):
    """TheSportsDB implementation of SportsProvider.

    Handles leagues not covered by ESPN.
    """

    def __init__(
        self,
        league_mapping_source: LeagueMappingSource | None = None,
        client: TSDBClient | None = None,
        api_key: str | None = None,
    ):
        self._league_mapping_source = league_mapping_source
        self._client = client or TSDBClient(
            league_mapping_source=league_mapping_source,
            api_key=api_key,
        )

    @property
    def name(self) -> str:
        return "tsdb"

    def supports_league(self, league: str) -> bool:
        return self._client.supports_league(league)

    def get_events(self, league: str, target_date: date) -> list[Event]:
        """Get events for a league on a specific date.

        Tries eventsday.php first, falls back to eventsnextleague.php
        filtered by date (some leagues don't populate eventsday).
        """
        date_str = target_date.strftime("%Y-%m-%d")

        # Try date-specific endpoint first
        data = self._client.get_events_by_date(league, date_str)
        if data and data.get("events"):
            events = []
            for event_data in data["events"]:
                event = self._parse_event(event_data, league)
                if event:
                    events.append(event)
            return events

        # Fall back to next league events, filter by date
        data = self._client.get_league_next_events(league)
        if not data or not data.get("events"):
            return []

        events = []
        for event_data in data["events"]:
            # Filter to target date
            event_date = event_data.get("dateEvent")
            if event_date != date_str:
                continue
            event = self._parse_event(event_data, league)
            if event:
                events.append(event)

        return events

    # TSDB rate limit optimization: cap at 14 days regardless of caller request
    # ESPN can handle 30+ days, but TSDB's 25 req/min limit makes that expensive
    TSDB_MAX_DAYS_AHEAD = 14

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Get upcoming schedule for a team.

        Uses eventsday.php across multiple days to get both HOME and AWAY
        games (eventsnext.php only returns HOME on free tier).

        Note: days_ahead is capped at TSDB_MAX_DAYS_AHEAD (14) to reduce
        API calls. TSDB data is sparse for far-future dates anyway.
        """
        # Cap days_ahead for rate limit optimization
        days_ahead = min(days_ahead, self.TSDB_MAX_DAYS_AHEAD)

        # First, get team name from league teams
        team_name = self._get_team_name(team_id, league)
        if not team_name:
            logger.warning(f"Could not find team name for ID {team_id}")
            return []

        events = []
        today = date.today()

        # Query each day and filter for this team
        for i in range(days_ahead):
            target_date = today + timedelta(days=i)
            date_str = target_date.strftime("%Y-%m-%d")

            data = self._client.get_events_by_date(league, date_str)
            if not data or not data.get("events"):
                continue

            for event_data in data["events"]:
                # Check if team is home or away
                home = event_data.get("strHomeTeam", "")
                away = event_data.get("strAwayTeam", "")
                if team_name in (home, away):
                    event = self._parse_event(event_data, league)
                    if event:
                        events.append(event)

        # Sort by start time
        events.sort(key=lambda e: e.start_time)
        return events

    def _get_team_name(self, team_id: str, league: str) -> str | None:
        """Get team name from ID using seeded database cache.

        Teams are seeded from tsdb_seed.json on startup and refreshed
        weekly via cache refresh. No API calls needed.
        """
        from teamarr.consumers.cache import get_cache

        cache = get_cache()
        team_name = cache.get_team_name_by_id(team_id, league, provider="tsdb")
        if team_name:
            return team_name

        # Team not in seeded cache - this shouldn't happen in normal operation
        logger.warning(
            f"TSDB team {team_id} not found in seeded cache for league {league}. "
            "Run cache refresh or check tsdb_seed.json."
        )
        return None

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details.

        Note: lookupteam.php is broken on free tier (returns wrong team).
        This method validates the returned ID matches the requested ID.
        """
        data = self._client.get_team(team_id)

        if not data:
            return None

        teams = data.get("teams") or []
        if not teams:
            return None

        team_data = teams[0]

        # Validate - free tier bug returns wrong team
        if str(team_data.get("idTeam")) != str(team_id):
            logger.warning(
                f"TSDB lookupteam.php bug: requested {team_id}, "
                f"got {team_data.get('idTeam')} ({team_data.get('strTeam')})"
            )
            return None

        return self._parse_team(team_data, league)

    def search_team(self, team_name: str, league: str) -> Team | None:
        """Search for a team by name.

        More reliable than get_team on free tier.
        """
        data = self._client.search_team(team_name)

        if not data:
            return None

        teams = data.get("teams") or []
        if not teams:
            return None

        # Return first match
        return self._parse_team(teams[0], league)

    def _parse_team(self, team_data: dict, league: str) -> Team:
        """Parse team data dict into Team dataclass."""
        sport = self._client.get_sport(league)

        return Team(
            id=str(team_data.get("idTeam", "")),
            provider=self.name,
            name=team_data.get("strTeam", ""),
            short_name=team_data.get("strTeamShort") or team_data.get("strTeam", ""),
            abbreviation=self._make_abbrev(
                team_data.get("strTeamShort") or team_data.get("strTeam", "")
            ),
            league=league,
            sport=sport,
            logo_url=team_data.get("strBadge"),
            color=team_data.get("strColour1"),  # May be None for some leagues
        )

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID."""
        data = self._client.get_event(event_id)

        if not data:
            return None

        events = data.get("events") or []
        if not events:
            return None

        return self._parse_event(events[0], league)

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get team statistics.

        TSDB's lookuptable.php only works for featured soccer leagues,
        not hockey/lacrosse/etc. Returns None for unsupported leagues.
        """
        # lookuptable.php is limited to featured soccer leagues on free tier
        # For OHL and other non-soccer leagues, stats are not available
        return None

    def get_teams_in_league(self, league: str) -> list[Team]:
        """Get all teams in a league.

        Uses search_all_teams.php which works on free tier.
        """
        data = self._client.get_teams_in_league(league)

        if not data:
            return []

        teams = []
        for team_data in data.get("teams") or []:
            teams.append(self._parse_team(team_data, league))

        return teams

    def get_league_teams(self, league: str) -> list[Team]:
        """Get all teams in a league.

        Alias for get_teams_in_league() for consistent interface with ESPN.
        Used by cache refresh.
        """
        return self.get_teams_in_league(league)

    def get_supported_leagues(self) -> list[str]:
        """Get all leagues this provider supports.

        Uses the league mapping source for all enabled TSDB league mappings.
        """
        if not self._league_mapping_source:
            return []
        mappings = self._league_mapping_source.get_leagues_for_provider("tsdb")
        return [m.league_code for m in mappings]

    def _parse_event(self, data: dict, league: str) -> Event | None:
        """Parse TSDB event data into Event dataclass."""
        try:
            event_id = data.get("idEvent")
            if not event_id:
                return None

            # Parse start time
            start_time = self._parse_datetime(
                data.get("dateEvent"),
                data.get("strTime"),
                data.get("strTimestamp"),
            )
            if not start_time:
                return None

            sport = self._client.get_sport(league)
            event_name = data.get("strEvent", "")

            # Check if this is a combat sport with fighters in event name
            # (boxing, etc. have None for home/away team)
            home_name = data.get("strHomeTeam")
            away_name = data.get("strAwayTeam")

            if not home_name and not away_name and " vs " in event_name:
                # Parse fighters from event name: "Fighter A vs Fighter B"
                home_team, away_team = self._parse_fighters_from_event_name(
                    event_name, event_id, league, sport
                )
            else:
                # Standard team sport
                home_team = Team(
                    id=str(data.get("idHomeTeam", "")),
                    provider=self.name,
                    name=home_name or "",
                    short_name=home_name or "",
                    abbreviation=self._make_abbrev(home_name or ""),
                    league=league,
                    sport=sport,
                    logo_url=data.get("strHomeTeamBadge"),
                    color=None,
                )

                away_team = Team(
                    id=str(data.get("idAwayTeam", "")),
                    provider=self.name,
                    name=away_name or "",
                    short_name=away_name or "",
                    abbreviation=self._make_abbrev(away_name or ""),
                    league=league,
                    sport=sport,
                    logo_url=data.get("strAwayTeamBadge"),
                    color=None,
                )

            # Parse status
            status = self._parse_status(data)

            # Parse scores
            home_score = self._parse_score(data.get("intHomeScore"))
            away_score = self._parse_score(data.get("intAwayScore"))

            # Parse venue
            venue = self._parse_venue(data)

            # Build short name
            if home_team.name and away_team.name:
                short_name = f"{away_team.abbreviation} vs {home_team.abbreviation}"
            else:
                short_name = event_name

            return Event(
                id=str(event_id),
                provider=self.name,
                name=event_name or f"{away_team.name} vs {home_team.name}",
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
                broadcasts=[],  # TSDB doesn't provide broadcast info
            )

        except Exception as e:
            logger.warning(f"Failed to parse TSDB event {data.get('idEvent', 'unknown')}: {e}")
            return None

    def _parse_fighters_from_event_name(
        self,
        event_name: str,
        event_id: str,
        league: str,
        sport: str,
    ) -> tuple[Team, Team]:
        """Parse fighters from event name like 'Fighter A vs Fighter B'.

        Returns (home_team, away_team) - first fighter is "home".
        """
        # Split on " vs " (case insensitive)
        parts = event_name.split(" vs ")
        if len(parts) != 2:
            # Try " v " as fallback
            parts = event_name.split(" v ")

        if len(parts) == 2:
            fighter1 = parts[0].strip()
            fighter2 = parts[1].strip()
        else:
            # Can't parse, use full name
            fighter1 = event_name
            fighter2 = "TBD"

        # Create Team objects for fighters
        home_team = Team(
            id=f"{event_id}_1",
            provider=self.name,
            name=fighter1,
            short_name=self._make_short_name(fighter1),
            abbreviation=self._make_fighter_abbrev(fighter1),
            league=league,
            sport=sport,
            logo_url=None,
            color=None,
        )

        away_team = Team(
            id=f"{event_id}_2",
            provider=self.name,
            name=fighter2,
            short_name=self._make_short_name(fighter2),
            abbreviation=self._make_fighter_abbrev(fighter2),
            league=league,
            sport=sport,
            logo_url=None,
            color=None,
        )

        return home_team, away_team

    def _make_short_name(self, name: str) -> str:
        """Make short name from full name (e.g., 'Diego Pacheco' -> 'D. Pacheco')."""
        parts = name.split()
        if len(parts) >= 2:
            return f"{parts[0][0]}. {parts[-1]}"
        return name

    def _make_fighter_abbrev(self, name: str) -> str:
        """Make abbreviation for a fighter name."""
        parts = name.split()
        if len(parts) >= 2:
            # Use last name
            return parts[-1].upper()[:6]
        return name.upper()[:6]

    def _parse_datetime(
        self,
        date_str: str | None,
        time_str: str | None,
        timestamp_str: str | None,
    ) -> datetime | None:
        """Parse TSDB date/time into UTC datetime."""
        # Try timestamp first (most reliable)
        if timestamp_str:
            try:
                # TSDB timestamps are ISO format, may or may not have Z suffix
                if timestamp_str.endswith("Z"):
                    return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                dt = datetime.fromisoformat(timestamp_str)
                # Assume UTC if no timezone
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                pass

        # Fall back to date + time
        if date_str:
            try:
                if time_str:
                    dt_str = f"{date_str}T{time_str}"
                    dt = datetime.fromisoformat(dt_str)
                else:
                    dt = datetime.fromisoformat(date_str)

                # Assume UTC if no timezone
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                pass

        return None

    def _parse_status(self, data: dict) -> EventStatus:
        """Parse event status from TSDB data."""
        status_str = data.get("strStatus", "")
        post_state = data.get("strPostponed", "")

        if post_state == "yes":
            return EventStatus(state="postponed", detail="Postponed")

        # TSDB uses different status values
        status_lower = status_str.lower() if status_str else ""

        if status_lower in ("ft", "aet", "finished", "match finished"):
            return EventStatus(state="final", detail=status_str)
        elif status_lower in ("live", "1h", "2h", "ht", "et"):
            return EventStatus(state="live", detail=status_str)
        elif status_lower in ("ns", "not started", ""):
            return EventStatus(state="scheduled", detail=None)
        elif status_lower in ("cancelled", "canceled"):
            return EventStatus(state="cancelled", detail=status_str)

        # Default to scheduled
        return EventStatus(state="scheduled", detail=status_str if status_str else None)

    def _parse_venue(self, data: dict) -> Venue | None:
        """Parse venue from TSDB data."""
        venue_name = data.get("strVenue")
        if not venue_name:
            return None

        return Venue(
            name=venue_name,
            city=data.get("strCity"),
            state=None,  # TSDB doesn't separate state
            country=data.get("strCountry"),
        )

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
        # Take first 3 letters of first word, uppercase
        words = team_name.split()
        if words:
            return words[0][:3].upper()
        return ""
