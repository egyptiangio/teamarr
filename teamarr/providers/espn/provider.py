"""ESPN sports data provider.

Fetches data from ESPN API and normalizes into our dataclass format.
Pure fetch + normalize - no caching (caching is in service layer).
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
from teamarr.providers.espn.client import ESPNClient
from teamarr.providers.espn.constants import STATUS_MAP, TOURNAMENT_SPORTS
from teamarr.providers.espn.tournament import TournamentParserMixin
from teamarr.providers.espn.ufc import UFCParserMixin

logger = logging.getLogger(__name__)


class ESPNProvider(UFCParserMixin, TournamentParserMixin, SportsProvider):
    """ESPN implementation of SportsProvider.

    Pure fetch + normalize layer. No caching - that's handled by SportsDataService.
    """

    def __init__(
        self,
        client: ESPNClient | None = None,
        league_mapping_source: LeagueMappingSource | None = None,
    ):
        self._client = client or ESPNClient()
        self._league_mapping_source = league_mapping_source

    @property
    def name(self) -> str:
        return "espn"

    def supports_league(self, league: str) -> bool:
        # Database is the source of truth
        if self._league_mapping_source:
            if self._league_mapping_source.supports_league(league, "espn"):
                return True
        # Soccer leagues use dot notation - can be discovered dynamically
        if "." in league:
            return True
        return False

    def _get_sport_league_from_db(self, league: str) -> tuple[str, str] | None:
        """Get sport/league pair from database config.

        Returns (sport, espn_league) tuple from provider_league_id (e.g., "basketball/nba").
        Returns None if not found in database.
        """
        if not self._league_mapping_source:
            return None
        mapping = self._league_mapping_source.get_mapping(league, "espn")
        if mapping and mapping.provider_league_id:
            # provider_league_id is "sport/league" format
            parts = mapping.provider_league_id.split("/", 1)
            if len(parts) == 2:
                return (parts[0], parts[1])
        return None

    def _get_sport(self, league: str) -> str:
        """Get sport name for a league.

        Uses database config as source of truth, falls back to client mapping.
        """
        # Try database first
        db_result = self._get_sport_league_from_db(league)
        if db_result:
            return db_result[0]
        # Fallback to client mapping
        sport, _ = self._client.get_sport_league(league)
        return sport

    def get_events(self, league: str, target_date: date) -> list[Event]:
        # UFC uses different API and parsing
        if league == "ufc":
            return self._get_ufc_events(target_date)

        # Check if this is a tournament sport
        sport = self._get_sport(league)
        if sport in TOURNAMENT_SPORTS:
            return self._get_tournament_events(league, target_date, sport)

        date_str = target_date.strftime("%Y%m%d")
        data = self._client.get_scoreboard(league, date_str)
        if not data:
            return []

        events = []
        for event_data in data.get("events", []):
            event = self._parse_event(event_data, league)
            if event:
                events.append(event)

        return events

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Fetch team schedule.

        NOTE: ESPN's schedule endpoint returns only PAST games for soccer leagues.
        For soccer, we scan the scoreboard endpoint for upcoming days and filter
        by team ID to find their games.
        """
        sport = self._get_sport(league)
        now = datetime.now(UTC)
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # For soccer, the schedule endpoint only returns past games.
        # Use scoreboard scanning instead.
        if sport == "soccer":
            return self._get_soccer_team_schedule(team_id, league, days_ahead)

        # Standard behavior for US sports
        # ESPN schedule endpoint returns full season - filter by days_ahead
        data = self._client.get_team_schedule(league, team_id)
        if not data:
            return []

        max_date = now + timedelta(days=days_ahead)

        events = []
        for event_data in data.get("events", []):
            event = self._parse_event(event_data, league)
            if event and cutoff <= event.start_time <= max_date:
                events.append(event)

        events.sort(key=lambda e: e.start_time)
        return events

    def _get_soccer_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int,
    ) -> list[Event]:
        """Get soccer team schedule by scanning scoreboard.

        ESPN's schedule endpoint for soccer only returns past games.
        We scan the scoreboard for the next N days and filter for games
        involving the specified team.
        """
        from datetime import timedelta

        events = []
        today = date.today()

        for day_offset in range(days_ahead):
            target_date = today + timedelta(days=day_offset)
            date_str = target_date.strftime("%Y%m%d")

            data = self._client.get_scoreboard(league, date_str)
            if not data:
                continue

            for event_data in data.get("events", []):
                # Check if this team is playing
                if self._team_in_event(team_id, event_data):
                    event = self._parse_event(event_data, league)
                    if event:
                        events.append(event)

        events.sort(key=lambda e: e.start_time)
        return events

    def _team_in_event(self, team_id: str, event_data: dict) -> bool:
        """Check if a team is playing in this event."""
        competitions = event_data.get("competitions", [])
        if not competitions:
            return False

        for competitor in competitions[0].get("competitors", []):
            comp_team = competitor.get("team", {})
            if str(comp_team.get("id")) == str(team_id):
                return True
        return False

    def get_team(self, team_id: str, league: str) -> Team | None:
        data = self._client.get_team(league, team_id)
        if not data:
            return None

        team_data = data.get("team", {})
        if not team_data:
            return None

        logo_url = self._extract_logo(team_data)
        sport = self._get_sport(league)

        return Team(
            id=team_data.get("id", team_id),
            provider=self.name,
            name=team_data.get("displayName", ""),
            short_name=team_data.get("shortDisplayName", ""),
            abbreviation=team_data.get("abbreviation", ""),
            league=league,
            sport=sport,
            logo_url=logo_url,
            color=team_data.get("color"),
        )

    def _extract_logo(self, data: dict) -> str | None:
        """Extract logo URL from team data. Handles 'logo' or 'logos' field."""
        if "logo" in data and data["logo"]:
            return data["logo"]
        logos = data.get("logos", [])
        if logos:
            for logo in logos:
                if "default" in logo.get("rel", []):
                    return logo.get("href")
            return logos[0].get("href")
        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Fetch single event with full details from summary endpoint."""
        data = self._client.get_event(league, event_id)
        if not data:
            return None

        header = data.get("header", {})
        competitions = header.get("competitions", [])
        if not competitions:
            return None

        competition = competitions[0]

        # Summary endpoint has venue in gameInfo, not competition
        game_info = data.get("gameInfo", {})
        venue_data = game_info.get("venue")
        if venue_data:
            # Normalize venue format to match scoreboard structure
            competition["venue"] = {
                "fullName": venue_data.get("fullName", ""),
                "address": venue_data.get("address", {}),
            }

        # Summary endpoint has odds in pickcenter, not competition.odds
        pickcenter = data.get("pickcenter", [])
        if pickcenter and not competition.get("odds"):
            # Convert pickcenter format to scoreboard odds format
            competition["odds"] = pickcenter

        event_data = {
            "id": event_id,
            "name": header.get("gameNote", ""),
            "shortName": self._build_short_name(competition),
            "date": competition.get("date"),
            "competitions": [competition],
        }

        return self._parse_event(event_data, league)

    def _build_short_name(self, competition: dict) -> str:
        """Build short name from competitors."""
        competitors = competition.get("competitors", [])
        if len(competitors) < 2:
            return ""
        home = away = None
        for c in competitors:
            team = c.get("team", {})
            abbrev = team.get("abbreviation", "")
            if c.get("homeAway") == "home":
                home = abbrev
            else:
                away = abbrev
        if home and away:
            return f"{away} @ {home}"
        return ""

    def _parse_event(self, data: dict, league: str) -> Event | None:
        """Parse ESPN event data into Event dataclass."""
        try:
            event_id = data.get("id", "")
            if not event_id:
                return None

            competitions = data.get("competitions", [])
            if not competitions:
                return None

            competition = competitions[0]
            competitors = competition.get("competitors", [])
            if len(competitors) < 2:
                return None

            home_data = None
            away_data = None
            for comp in competitors:
                if comp.get("homeAway") == "home":
                    home_data = comp
                else:
                    away_data = comp

            if not home_data or not away_data:
                return None

            # Get sport from ESPN's own league mapping
            sport = self._get_sport(league)

            home_team = self._parse_team(home_data, league, sport)
            away_team = self._parse_team(away_data, league, sport)

            date_str = data.get("date") or competition.get("date", "")
            start_time = self._parse_datetime(date_str)
            if not start_time:
                return None

            status = self._parse_status(competition.get("status", {}))
            venue = self._parse_venue(competition.get("venue"))
            broadcasts = self._parse_broadcasts(competition.get("broadcasts", []))
            odds_data = self._parse_odds(competition.get("odds", []))

            home_score = self._parse_score(home_data.get("score"))
            away_score = self._parse_score(away_data.get("score"))

            return Event(
                id=event_id,
                provider=self.name,
                name=data.get("name", ""),
                short_name=data.get("shortName", ""),
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
                odds_data=odds_data,
            )
        except Exception as e:
            logger.warning(f"Failed to parse event {data.get('id', 'unknown')}: {e}")
            return None

    def _parse_team(self, competitor: dict, league: str, sport: str) -> Team:
        """Parse competitor data into Team."""
        team_data = competitor.get("team", {})
        return Team(
            id=team_data.get("id", competitor.get("id", "")),
            provider=self.name,
            name=team_data.get("displayName", ""),
            short_name=team_data.get("shortDisplayName", ""),
            abbreviation=team_data.get("abbreviation", ""),
            league=league,
            sport=sport,
            logo_url=team_data.get("logo"),
            color=team_data.get("color"),
        )

    def _parse_status(self, status_data: dict) -> EventStatus:
        """Parse status data into EventStatus."""
        type_data = status_data.get("type", {})
        espn_status = type_data.get("name", "STATUS_SCHEDULED")
        state = STATUS_MAP.get(espn_status, "scheduled")

        return EventStatus(
            state=state,
            detail=type_data.get("description"),
            period=status_data.get("period"),
            clock=status_data.get("displayClock"),
        )

    def _parse_venue(self, venue_data: dict | None) -> Venue | None:
        """Parse venue data into Venue."""
        if not venue_data:
            return None

        address = venue_data.get("address", {})
        return Venue(
            name=venue_data.get("fullName", ""),
            city=address.get("city"),
            state=address.get("state"),
            country=address.get("country"),
        )

    def _parse_broadcasts(self, broadcasts_data: list) -> list[str]:
        """Extract broadcast network names.

        Handles two formats:
        - Scoreboard: [{"names": ["FOX"]}]
        - Summary: [{"media": {"shortName": "NBC"}}]
        """
        networks = []
        for broadcast in broadcasts_data:
            # Scoreboard format: names array
            names = broadcast.get("names", [])
            if names:
                networks.extend(names)
            # Summary format: media.shortName
            elif "media" in broadcast:
                short_name = broadcast["media"].get("shortName")
                if short_name:
                    networks.append(short_name)
        return networks

    def _parse_datetime(self, date_str: str) -> datetime | None:
        """Parse ESPN date string to UTC datetime."""
        if not date_str:
            return None
        try:
            if date_str.endswith("Z"):
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return datetime.fromisoformat(date_str)
        except ValueError:
            return None

    def _parse_score(self, score) -> int | None:
        """Parse score to int. Handles string or dict format."""
        if score is None:
            return None
        try:
            if isinstance(score, dict):
                score = score.get("displayValue") or score.get("value")
            if score is None:
                return None
            return int(float(score))
        except (ValueError, TypeError):
            return None

    def _parse_odds(self, odds_list: list) -> dict | None:
        """Parse ESPN odds data into structured dict.

        ESPN provides odds from multiple providers. We take the first one
        (usually highest priority provider like DraftKings).

        Handles two formats:
        - Scoreboard: moneyline.home.close.odds (string)
        - Pickcenter: homeTeamOdds.moneyLine (int)

        Returns dict with:
            provider: str - Provider name
            spread: float - Point spread (negative = favorite)
            over_under: float - Total points line
            details: str - Human-readable odds string
            home_moneyline: int - Home team moneyline
            away_moneyline: int - Away team moneyline
        """
        if not odds_list:
            return None

        try:
            # Take first provider (highest priority)
            odds = odds_list[0]

            provider_data = odds.get("provider", {})
            provider_name = provider_data.get("name", "")

            # Get spread and over/under
            spread = odds.get("spread", 0.0)
            over_under = odds.get("overUnder", 0.0)
            details = odds.get("details", "")

            # Get moneylines - try pickcenter format first (simpler)
            home_ml = None
            away_ml = None

            # Pickcenter format: homeTeamOdds.moneyLine (int)
            home_team_odds = odds.get("homeTeamOdds", {})
            away_team_odds = odds.get("awayTeamOdds", {})
            if home_team_odds.get("moneyLine") is not None:
                home_ml = int(home_team_odds["moneyLine"])
            if away_team_odds.get("moneyLine") is not None:
                away_ml = int(away_team_odds["moneyLine"])

            # Scoreboard format: moneyline.home.close.odds (string)
            if home_ml is None or away_ml is None:
                moneyline = odds.get("moneyline", {})
                if moneyline:
                    if home_ml is None:
                        home_close = moneyline.get("home", {}).get("close", {})
                        try:
                            home_ml = int(home_close.get("odds", "").replace("+", ""))
                        except (ValueError, AttributeError):
                            pass
                    if away_ml is None:
                        away_close = moneyline.get("away", {}).get("close", {})
                        try:
                            away_ml = int(away_close.get("odds", "").replace("+", ""))
                        except (ValueError, AttributeError):
                            pass

            return {
                "provider": provider_name,
                "spread": float(spread) if spread else 0.0,
                "over_under": float(over_under) if over_under else 0.0,
                "details": details,
                "home_moneyline": home_ml,
                "away_moneyline": away_ml,
            }
        except Exception as e:
            logger.debug(f"Failed to parse odds: {e}")
            return None

    def get_league_teams(self, league: str) -> list[Team]:
        """Fetch all teams for a league.

        Used by cache refresh to populate team_cache table.

        Args:
            league: Canonical league code (e.g., 'nfl', 'eng.1')

        Returns:
            List of Team objects for this league
        """
        # Get sport/league from database (source of truth)
        sport_league = self._get_sport_league_from_db(league)
        data = self._client.get_teams(league, sport_league)
        if not data:
            return []

        sport = sport_league[0] if sport_league else self._get_sport(league)
        teams = []

        # ESPN teams endpoint returns {"sports": [{"leagues": [{"teams": [...]}]}]}
        # or just {"teams": [...]} depending on endpoint version
        team_list = []
        if "teams" in data:
            team_list = data["teams"]
        else:
            try:
                team_list = data["sports"][0]["leagues"][0]["teams"]
            except (KeyError, IndexError):
                logger.warning(f"Unexpected teams response structure for {league}")
                return []

        for entry in team_list:
            # Entry may be {"team": {...}} or just {...}
            team_data = entry.get("team", entry)
            team = self._parse_team_from_teams_endpoint(team_data, league, sport)
            if team:
                teams.append(team)

        return teams

    def _parse_team_from_teams_endpoint(
        self, team_data: dict, league: str, sport: str
    ) -> Team | None:
        """Parse team data from the /teams endpoint."""
        team_id = team_data.get("id")
        if not team_id:
            return None

        logo_url = self._extract_logo(team_data)

        return Team(
            id=str(team_id),
            provider=self.name,
            name=team_data.get("displayName", ""),
            short_name=team_data.get("shortDisplayName", ""),
            abbreviation=team_data.get("abbreviation", ""),
            league=league,
            sport=sport,
            logo_url=logo_url,
            color=team_data.get("color"),
        )

    def get_supported_leagues(self) -> list[str]:
        """Get all leagues this provider supports.

        Returns only leagues explicitly configured in the database.
        """
        if not self._league_mapping_source:
            return []

        mappings = self._league_mapping_source.get_leagues_for_provider("espn")
        return sorted(m.league_code for m in mappings)

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Fetch detailed team statistics from ESPN.

        Returns TeamStats with record, rankings, scoring averages,
        and conference/division info.
        """
        data = self._client.get_team(league, team_id)
        if not data or "team" not in data:
            return None

        team_data = data["team"]
        record = team_data.get("record", {})
        record_items = record.get("items", [])

        if not record_items:
            return None

        # Find overall record (type='total')
        overall = next((r for r in record_items if r.get("type") == "total"), None)
        if not overall:
            return None

        # Parse stats from overall record
        stats = {s["name"]: s["value"] for s in overall.get("stats", [])}

        # Parse record string
        record_str = overall.get("summary", "0-0")
        wins, losses, ties = self._parse_record_string(record_str)

        # Get home/away records
        home_rec = next((r for r in record_items if r.get("type") == "home"), None)
        away_rec = next((r for r in record_items if r.get("type") == "road"), None)

        home_record = home_rec.get("summary") if home_rec else None
        away_record = away_rec.get("summary") if away_rec else None

        # Fallback: build home/away from stats (needed for soccer)
        if not home_record:
            home_record = self._build_record_from_stats(stats, "home", record_str)
        if not away_record:
            away_record = self._build_record_from_stats(stats, "away", record_str)

        # Parse streak
        streak_count = int(stats.get("streak", 0))
        streak_str = self._format_streak(streak_count)

        # Get conference/division
        groups = team_data.get("groups", {})
        conference, conference_abbrev, division = self._parse_groups(groups)

        return TeamStats(
            record=record_str,
            wins=wins,
            losses=losses,
            ties=ties,
            home_record=home_record,
            away_record=away_record,
            streak=streak_str,
            streak_count=streak_count,
            rank=team_data.get("rank") if team_data.get("rank", 99) <= 25 else None,
            playoff_seed=int(stats.get("playoffSeed", 0)) or None,
            games_back=float(stats.get("gamesBehind", 0)) or None,
            conference=conference,
            conference_abbrev=conference_abbrev,
            division=division,
            ppg=float(stats.get("avgPointsFor", 0)) or None,
            papg=float(stats.get("avgPointsAgainst", 0)) or None,
        )

    def _parse_record_string(self, record_str: str) -> tuple[int, int, int]:
        """Parse record string like '10-2' or '8-3-1' into (wins, losses, ties)."""
        parts = record_str.split("-")
        try:
            if len(parts) == 2:
                return int(parts[0]), int(parts[1]), 0
            elif len(parts) == 3:
                return int(parts[0]), int(parts[2]), int(parts[1])  # W-D-L for soccer
            return 0, 0, 0
        except ValueError:
            return 0, 0, 0

    def _build_record_from_stats(self, stats: dict, prefix: str, overall_record: str) -> str | None:
        """Build home/away record from individual stat fields."""
        wins = int(stats.get(f"{prefix}Wins", 0))
        losses = int(stats.get(f"{prefix}Losses", 0))
        ties = int(stats.get(f"{prefix}Ties", 0))

        if not wins and not losses and not ties:
            return None

        # Check if overall uses W-D-L format (soccer)
        uses_draws = len(overall_record.split("-")) == 3

        if uses_draws:
            return f"{wins}-{ties}-{losses}"
        elif ties > 0:
            return f"{wins}-{losses}-{ties}"
        return f"{wins}-{losses}"

    def _format_streak(self, streak_count: int) -> str:
        """Format streak count to 'W3' or 'L2' format."""
        if streak_count > 0:
            return f"W{streak_count}"
        elif streak_count < 0:
            return f"L{abs(streak_count)}"
        return ""

    def _parse_groups(self, groups: dict) -> tuple[str | None, str | None, str | None]:
        """Parse conference/division from groups structure.

        Returns (conference_name, conference_abbrev, division_name).
        Note: Full conference/division names require additional API calls
        to the Core API. For now, we return IDs as placeholders.
        """
        if not groups:
            return None, None, None

        # ESPN structure varies:
        # - Pro leagues: groups.id = division, groups.parent.id = conference
        # - College: groups.id = subdivision, groups.parent.id = conference
        # - isConference=true: groups.id is the conference itself

        is_conference = groups.get("isConference", False)
        group_id = groups.get("id")
        parent_id = groups.get("parent", {}).get("id")

        if is_conference:
            # groups.id is the conference
            return f"Conference {group_id}", None, None

        # groups.id is division/subdivision, parent is conference
        conference = f"Conference {parent_id}" if parent_id else None
        division = f"Division {group_id}" if group_id else None

        return conference, None, division
