"""ESPN sports data provider.

Fetches data from ESPN API and normalizes into our dataclass format.
Pure fetch + normalize - no caching (caching is in service layer).
"""

import logging
from datetime import UTC, date, datetime

from teamarr.core import Event, EventStatus, SportsProvider, Team, TeamStats, Venue
from teamarr.database import get_db, get_leagues_for_provider
from teamarr.providers.espn.client import SPORT_MAPPING, ESPNClient
from teamarr.utilities.tz import to_user_tz

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "live",
    "STATUS_HALFTIME": "live",
    "STATUS_END_PERIOD": "live",
    "STATUS_FINAL": "final",
    "STATUS_FINAL_OT": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
    "STATUS_DELAYED": "scheduled",
}


class ESPNProvider(SportsProvider):
    """ESPN implementation of SportsProvider.

    Pure fetch + normalize layer. No caching - that's handled by SportsDataService.
    """

    def __init__(self, client: ESPNClient | None = None):
        self._client = client or ESPNClient()

    @property
    def name(self) -> str:
        return "espn"

    def supports_league(self, league: str) -> bool:
        if league in SPORT_MAPPING:
            return True
        if "." in league:
            return True
        return False

    def _get_sport(self, league: str) -> str:
        """Get sport name for a league from ESPN's own mapping.

        This is the authoritative source - ESPN knows what sport each league is.
        """
        sport, _ = self._client.get_sport_league(league)
        return sport

    # Sports that are tournament-based (no home/away teams)
    TOURNAMENT_SPORTS = {"tennis", "golf", "racing"}

    def get_events(self, league: str, target_date: date) -> list[Event]:
        # UFC uses different API and parsing
        if league == "ufc":
            return self._get_ufc_events(target_date)

        # Check if this is a tournament sport
        sport = self._get_sport(league)
        if sport in self.TOURNAMENT_SPORTS:
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

    def _get_tournament_events(
        self, league: str, target_date: date, sport: str
    ) -> list[Event]:
        """Get events for tournament sports (tennis, golf, racing).

        These sports have tournaments/races as events with many competitors,
        not head-to-head matchups with home/away.
        """
        date_str = target_date.strftime("%Y%m%d")
        data = self._client.get_scoreboard(league, date_str)
        if not data:
            return []

        events = []
        for event_data in data.get("events", []):
            event = self._parse_tournament_event(event_data, league, sport)
            if event:
                events.append(event)

        return events

    def _parse_tournament_event(
        self, data: dict, league: str, sport: str
    ) -> Event | None:
        """Parse a tournament-style event (tennis, golf, racing).

        Creates placeholder 'teams' representing the tournament/event itself.
        """
        try:
            event_id = data.get("id", "")
            if not event_id:
                return None

            # Parse start time
            date_str = data.get("date")
            if not date_str:
                return None

            start_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

            event_name = data.get("name", "")
            short_name = data.get("shortName", event_name)

            # For tournaments, create placeholder "teams"
            # This allows the event to work with existing matching logic
            tournament_team = Team(
                id=f"tournament_{event_id}",
                provider=self.name,
                name=event_name,
                short_name=short_name[:20] if short_name else "",
                abbreviation=self._make_tournament_abbrev(event_name),
                league=league,
                sport=sport,
                logo_url=None,
                color=None,
            )

            # Parse status
            status_data = data.get("status", {})
            type_data = status_data.get("type", {}) if status_data else {}
            state = type_data.get("state", "pre")

            if state == "in":
                status = EventStatus(state="live", detail=type_data.get("detail"))
            elif state == "post":
                status = EventStatus(state="final", detail=type_data.get("detail"))
            else:
                status = EventStatus(state="scheduled")

            # Parse venue if available
            venue = None
            competitions = data.get("competitions", [])
            if competitions:
                venue_data = competitions[0].get("venue")
                if venue_data:
                    venue = Venue(
                        name=venue_data.get("fullName", ""),
                        city=venue_data.get("address", {}).get("city", ""),
                        state=venue_data.get("address", {}).get("state", ""),
                        country=venue_data.get("address", {}).get("country", ""),
                    )

            return Event(
                id=str(event_id),
                provider=self.name,
                name=event_name,
                short_name=short_name,
                start_time=start_time,
                home_team=tournament_team,
                away_team=tournament_team,  # Same team for tournaments
                status=status,
                league=league,
                sport=sport,
                venue=venue,
                broadcasts=[],
            )

        except Exception as e:
            logger.warning(f"Failed to parse tournament event: {e}")
            return None

    def _make_tournament_abbrev(self, name: str) -> str:
        """Make abbreviation for tournament name."""
        # Take first letters of significant words
        words = [w for w in name.split() if len(w) > 2]
        if len(words) >= 2:
            return "".join(w[0].upper() for w in words[:4])
        return name[:6].upper()

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        data = self._client.get_team_schedule(league, team_id)
        if not data:
            return []

        now = datetime.now(UTC)
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)

        events = []
        for event_data in data.get("events", []):
            event = self._parse_event(event_data, league)
            if event and event.start_time >= cutoff:
                events.append(event)

        events.sort(key=lambda e: e.start_time)
        return events

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
        """Extract broadcast network names."""
        networks = []
        for broadcast in broadcasts_data:
            names = broadcast.get("names", [])
            networks.extend(names)
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

    def get_league_teams(self, league: str) -> list[Team]:
        """Fetch all teams for a league.

        Used by cache refresh to populate team_cache table.

        Args:
            league: Canonical league code (e.g., 'nfl', 'eng.1')

        Returns:
            List of Team objects for this league
        """
        data = self._client.get_teams(league)
        if not data:
            return []

        sport = self._get_sport(league)
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

        Returns SPORT_MAPPING keys (core leagues) plus any additional
        leagues configured in the database.
        """
        # Start with core leagues from SPORT_MAPPING
        leagues = set(SPORT_MAPPING.keys())

        # Add any additional leagues from database
        with get_db() as conn:
            db_mappings = get_leagues_for_provider(conn, "espn")
            for mapping in db_mappings:
                leagues.add(mapping.league_code)

        return sorted(leagues)

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

    def _build_record_from_stats(
        self, stats: dict, prefix: str, overall_record: str
    ) -> str | None:
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

    # UFC-specific parsing

    def _get_ufc_events(self, target_date: date) -> list[Event]:
        """Fetch and parse UFC events for a specific date.

        UFC API returns all upcoming events, so we filter to target_date.
        """
        data = self._client.get_ufc_events()
        if not data:
            return []

        try:
            ufc_events = data["sports"][0]["leagues"][0]["events"]
        except (KeyError, IndexError):
            logger.warning("Unexpected UFC events response structure")
            return []

        events = []
        for event_data in ufc_events:
            event = self._parse_ufc_event(event_data)
            if event:
                # Compare dates in user timezone (late night UTC = same day locally)
                local_date = to_user_tz(event.start_time).date()
                if local_date == target_date:
                    events.append(event)

        return events

    def _parse_ufc_event(self, data: dict) -> Event | None:
        """Parse UFC fight card into Event.

        Maps the main event fighters as home_team/away_team for compatibility.
        Extracts prelims vs main card start times.
        """
        try:
            event_id = str(data.get("id", ""))
            if not event_id:
                return None

            competitions = data.get("competitions", [])
            if not competitions:
                return None

            # Group bouts by start time to find prelims vs main card
            bout_times = set()
            for comp in competitions:
                if "date" in comp:
                    bout_times.add(comp["date"])

            if not bout_times:
                return None

            prelims_start = min(bout_times)
            main_card_start_str = max(bout_times) if len(bout_times) > 1 else None

            # Find the main event (first bout at main card time)
            main_event = None
            if main_card_start_str:
                main_event = next(
                    (c for c in competitions if c.get("date") == main_card_start_str),
                    None,
                )
            if not main_event:
                main_event = competitions[0]

            # Extract fighters as "teams"
            competitors = main_event.get("competitors", [])
            if len(competitors) < 2:
                return None

            fighter1 = self._parse_fighter_as_team(competitors[0])
            fighter2 = self._parse_fighter_as_team(competitors[1])

            # Parse times
            start_time = self._parse_datetime(prelims_start)
            if not start_time:
                return None

            main_card_start = None
            if main_card_start_str and main_card_start_str != prelims_start:
                main_card_start = self._parse_datetime(main_card_start_str)

            # Parse status from main event
            status = self._parse_ufc_status(main_event.get("status", {}))

            return Event(
                id=event_id,
                provider=self.name,
                name=data.get("name", ""),
                short_name=f"{fighter1.short_name} vs {fighter2.short_name}",
                start_time=start_time,
                home_team=fighter1,
                away_team=fighter2,
                status=status,
                league="ufc",
                sport="mma",
                main_card_start=main_card_start,
            )
        except Exception as e:
            logger.warning(f"Failed to parse UFC event {data.get('id', 'unknown')}: {e}")
            return None

    def _parse_fighter_as_team(self, competitor: dict) -> Team:
        """Convert UFC fighter to Team dataclass for compatibility."""
        athlete = competitor.get("athlete", {})

        # Get headshot URL
        headshots = athlete.get("headshots", {})
        logo_url = None
        if headshots:
            # Prefer full size, fallback to any available
            logo_url = headshots.get("full", {}).get("href")
            if not logo_url:
                for size in ["xlarge", "large", "medium"]:
                    if size in headshots:
                        logo_url = headshots[size].get("href")
                        break

        short_name = athlete.get("shortName", "")

        return Team(
            id=str(athlete.get("id", "")),
            provider=self.name,
            name=athlete.get("displayName", ""),
            short_name=short_name,
            abbreviation=short_name.replace(".", "").replace(" ", ""),
            league="ufc",
            sport="mma",
            logo_url=logo_url,
            color=None,
        )

    def _parse_ufc_status(self, status_data: dict) -> EventStatus:
        """Parse UFC event status."""
        state_map = {
            "pre": "scheduled",
            "in": "live",
            "post": "final",
        }
        state = status_data.get("state", "pre")
        mapped_state = state_map.get(state, "scheduled")

        return EventStatus(
            state=mapped_state,
            detail=status_data.get("description"),
            period=None,
            clock=None,
        )
