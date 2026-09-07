"""ESPN sports data provider.

Fetches data from ESPN API and normalizes into our dataclass format.
Pure fetch + normalize - no caching (caching is in service layer).
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from teamarr.core import (
    SEASON_OFFSEASON,
    SEASON_POSTSEASON,
    SEASON_PRESEASON,
    SEASON_REGULAR,
    Event,
    EventStatus,
    LeagueMappingSource,
    SportsProvider,
    Team,
    TeamStats,
    Venue,
)
from teamarr.core.sports import normalize_sport
from teamarr.providers.espn.client import (
    ESPN_TEAM_ID_CORRECTIONS,
    ESPNClient,
    league_publishes_rankings,
)
from teamarr.providers.espn.constants import STATUS_MAP, TOURNAMENT_SPORTS
from teamarr.providers.espn.editorial_canary import EditorialDriftCanary
from teamarr.providers.espn.tennis import TennisParserMixin
from teamarr.providers.espn.tournament import TournamentParserMixin
from teamarr.providers.espn.ufc import UFCParserMixin
from teamarr.utilities.event_status import is_event_final

logger = logging.getLogger(__name__)

# Poll rankings (#710). ESPN's /teams/{id} payload carries no rank field for any
# league, so TeamStats.rank has to come from the league's /rankings polls.
#
# A poll only counts while it is being voted on: ESPN keeps serving a season's
# final poll all offseason (on 2026-09-04 the latest college-hockey poll was
# 2026-04-13), which would stamp last season's ranks onto this season's
# listings. In-season polls refresh weekly, so anything older than the cutoff is
# an ended season, not a bye week.
RANKING_POLL_MAX_AGE_DAYS = 45
# Tournament brackets ride in the same payload ("NCAA Men's Hockey Tournament
# Seedings"); a 4-team seed is not a poll rank — that's playoff_seed's job.
RANKING_SEEDING_POLL_TYPE = "tournament"
# AP wins ties so an FBS team gets its AP rank rather than the coaches poll,
# while polls AP does not cover (FCS, D-II) still fill in their own teams.
RANKING_PRIMARY_POLL_TYPE = "ap"
RANKING_MAX = 25


class ESPNProvider(UFCParserMixin, TennisParserMixin, TournamentParserMixin, SportsProvider):
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
        # Optional cached, league-wide events fetcher injected by SportsDataService.
        # When set, the future-day team scan reuses the shared per-(league, date)
        # events cache instead of fetching each day's scoreboard once per team —
        # so a league's daily scoreboard is fetched once per run, not N_teams times.
        self._cached_events_fn: Callable[[str, date], list[Event]] | None = None
        # Drift canary (#506): warns if the editorial scoreboard keys the
        # empty-safe features rely on vanish from every payload (rename drift).
        self._editorial_canary = EditorialDriftCanary()

    @property
    def name(self) -> str:
        return "espn"

    def set_cached_events_fn(
        self, fn: Callable[[str, date], list[Event]] | None
    ) -> None:
        """Inject a cached league-wide events fetcher (SportsDataService.get_events).

        Used by the future-day team scan so all teams in a league share one
        scoreboard fetch per day. When unset (provider used standalone, e.g. in
        tests or cache refresh), the scan falls back to direct per-day fetches.
        """
        self._cached_events_fn = fn

    def supports_league(self, league: str) -> bool:
        # Database is the source of truth
        if self._league_mapping_source:
            if self._league_mapping_source.supports_league(league, "espn"):
                return True
            # If the league is configured in the leagues table for a DIFFERENT
            # provider, honor that — don't let the dotted-soccer heuristic below
            # override an explicit assignment (e.g. uru.2 → tsdb, whose ESPN data
            # is stale). Discovered soccer leagues aren't in the leagues table, so
            # this guard never blocks genuine dynamic discovery (#218).
            if self._league_mapping_source.get_mapping_by_league(league) is not None:
                return False
        # Soccer leagues use dot notation - can be discovered dynamically
        if "." in league:
            return True
        return False

    def _get_sport_league_from_db(self, league: str) -> tuple[str, str] | None:
        """Get sport/league pair from database config for API URL construction.

        Returns (sport, espn_league) tuple from provider_league_id (e.g., "basketball/nba").
        Sport is lowercase for ESPN API paths.
        Returns None if not found in database.
        """
        if not self._league_mapping_source:
            return None
        mapping = self._league_mapping_source.get_mapping(league, "espn")
        if mapping and mapping.provider_league_id:
            # provider_league_id is "sport/league" format (lowercase for API)
            parts = mapping.provider_league_id.split("/", 1)
            if len(parts) == 2:
                return (parts[0], parts[1])
        return None

    def _get_display_sport(self, league: str) -> str:
        """Get display sport name (title case) for a league.

        Uses mapping.sport from database for consistent title case display.
        Falls back to title-casing the API sport if no mapping found.
        """
        if self._league_mapping_source:
            mapping = self._league_mapping_source.get_mapping(league, "espn")
            if mapping and mapping.sport:
                return mapping.sport
        # Fallback: title case the API sport
        db_result = self._get_sport_league_from_db(league)
        if db_result:
            return db_result[0].title()
        return "Unknown"

    def _get_sport(self, league: str) -> str:
        """Get canonical sport code for a league (lowercase).

        Resolution chain:
        1. leagues table mapping (authoritative)
        2. league_cache sport (discovered leagues)
        3. dot-notation inference (ESPN soccer slugs, e.g. 'bra.carioca.groupa')
        4. 'unknown' fallback
        """
        display = self._get_display_sport(league)
        sport = display.lower() if display else "unknown"
        if sport == "unknown" and self._league_mapping_source:
            # Try league_cache (discovered leagues not in static leagues table)
            cached_sport = self._league_mapping_source.get_league_sport(league)
            if cached_sport:
                sport = cached_sport
        # ESPN soccer leagues use dot notation (eng.1, bra.carioca.groupa); mirror
        # the API-path inference in client._resolve_sport_league so discovered
        # soccer leagues cache as 'soccer' instead of 'unknown'.
        if sport == "unknown" and "." in league:
            sport = "soccer"
        return sport

    def _capture_league_name(self, data: dict, league: str) -> None:
        """Extract league name from scoreboard response and register if discovered.

        ESPN scoreboard responses include a 'leagues' array with name and logo.
        For discovered leagues (not in static leagues table), this captures the
        display name so {league} template variable resolves correctly.
        """
        if not self._league_mapping_source:
            return

        try:
            leagues = data.get("leagues", [])
            if not leagues:
                return

            league_info = leagues[0]
            name = league_info.get("name")
            if not name:
                return

            sport = self._get_sport(league)
            logo_url = None
            logos = league_info.get("logos", [])
            if logos:
                logo_url = logos[0].get("href")

            self._league_mapping_source.register_discovered_league(
                league_code=league,
                league_name=name,
                sport=sport,
                logo_url=logo_url,
            )
        except Exception:
            pass  # Best-effort, don't break event fetching

    def get_events(self, league: str, target_date: date) -> list[Event]:
        # UFC uses different API endpoint
        if league == "ufc":
            # ESPN's default MMA scoreboard returns ONLY the current featured
            # card, so any other card was invisible regardless of stream name
            # or regex (#345). Query a ±1-day window around target_date: a
            # PPV spans midnight (early prelims and main card land on adjacent
            # days), so a single-date query can drop the card.
            window = "{}-{}".format(
                (target_date - timedelta(days=1)).strftime("%Y%m%d"),
                (target_date + timedelta(days=1)).strftime("%Y%m%d"),
            )
            data = self._client.get_ufc_scoreboard(window)
            if not data:
                return []
            # Mixin handles: pure parsing only. The ±1-day fetch window IS the
            # superset; segment-aware date membership (a card touching two
            # days belongs to both, #345) is decided by the user-day window
            # at the service seam (#590).
            return self._parse_ufc_events(data)

        # Get sport/league from database config
        sport_league = self._get_sport_league_from_db(league)

        # Check if this is a tournament sport
        sport = self._get_sport(league)
        if sport in TOURNAMENT_SPORTS:
            return self._get_tournament_events(league, target_date, sport, sport_league)

        return self._get_scoreboard_events(league, target_date, sport_league)

    def _get_scoreboard_events(
        self,
        league: str,
        target_date: date,
        sport_league: tuple[str, str] | None,
    ) -> list[Event]:
        """Standard per-date scoreboard fetch (non-UFC, non-tournament)."""
        date_str = target_date.strftime("%Y%m%d")
        data = self._client.get_scoreboard(league, date_str, sport_league)
        if not data:
            return []

        # Capture league name from scoreboard for discovered leagues
        self._capture_league_name(data, league)

        events = []
        for event_data in data.get("events", []):
            event = self._parse_event(event_data, league)
            if event:
                events.append(event)

        return events

    def get_sample_candidates(self, league: str) -> list[Event]:
        """Recent + upcoming events for a sample preview, in ≤2 calls.

        Uses ESPN's **default scoreboard** (no date) — which returns the
        most-recent-relevant slate, i.e. the last completed game even deep in the
        offseason — plus **yesterday's** scoreboard to surface recent finals
        during the season. The caller prefers a final game so postgame vars
        populate. Avoids the sparse fixed-date scan that misses spaced-out
        schedules (NBA Finals, weekly NFL).
        """
        sport = self._get_sport(league)
        if league == "ufc":
            # Cards run ~weekly, so a today/yesterday scan is empty most of the
            # week and combat previews always fell back to static samples
            # (#260). One ±7-day range call captures both the last finished
            # card and the next scheduled one; no per-date coverage filter —
            # every card in the window is a candidate.
            today = date.today()
            window = "{}-{}".format(
                (today - timedelta(days=7)).strftime("%Y%m%d"),
                (today + timedelta(days=7)).strftime("%Y%m%d"),
            )
            data = self._client.get_ufc_scoreboard(window)
            return self._parse_ufc_events(data) if data else []
        if sport in TOURNAMENT_SPORTS:
            # Special endpoints — reuse the per-date path over a few days.
            by_event_id: dict[str, Event] = {}
            for d in (date.today(), date.today() - timedelta(days=1)):
                for e in self.get_events(league, d):
                    by_event_id[e.id] = e
            return list(by_event_id.values())

        sport_league = self._get_sport_league_from_db(league)
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        by_id: dict[str, Event] = {}
        for date_str in (None, yesterday):  # None = ESPN default (most recent) slate
            data = self._client.get_scoreboard(league, date_str, sport_league)
            if not data:
                continue
            self._capture_league_name(data, league)
            for event_data in data.get("events", []):
                event = self._parse_event(event_data, league)
                if event:
                    by_id[event.id] = event
        return list(by_id.values())

    def get_recent_final(self, league: str) -> Event | None:
        """The single most-recent FINAL game, however long ago it was.

        ESPN's default scoreboard jumps to the *next* season in the deep
        offseason, so a between-seasons league (NFL in June) otherwise yields
        only empty upcoming games. This walks back in ~35-day windows (well
        under ESPN's ~100-event range cap) until it finds a window with finals,
        then returns the most recent one — e.g. NFL in June → the Super Bowl.
        Best sample, since a finished game populates every postgame variable.
        """
        if self._get_sport(league) in TOURNAMENT_SPORTS:
            return None
        if league == "ufc":
            # Cards are ~weekly; the longest dark stretches (holidays) are a
            # few weeks, so one 35-day window nearly always hits. Finished-
            # first matters for combat: a final card is the only sample that
            # populates fight_result/finish_* variables (#260).
            end = date.today()
            for _ in range(3):
                start = end - timedelta(days=35)
                data = self._client.get_ufc_scoreboard(
                    f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
                )
                finals = [
                    e
                    for e in (self._parse_ufc_events(data) if data else [])
                    if e.home_team and e.away_team and is_event_final(e)
                ]
                if finals:
                    return max(finals, key=lambda e: e.start_time)
                end = start
            return None
        sport_league = self._get_sport_league_from_db(league)
        window = timedelta(days=35)
        end = date.today()
        for _ in range(9):  # ~9 months back
            start = end - window
            data = self._client.get_scoreboard(
                league, f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}", sport_league
            )
            finals = []
            for event_data in (data or {}).get("events", []):
                event = self._parse_event(event_data, league)
                if event and event.home_team and event.away_team and is_event_final(event):
                    finals.append(event)
            if finals:
                return max(finals, key=lambda e: e.start_time)
            end = start
        return None

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Fetch team schedule using hybrid approach.

        Uses two sources for comprehensive coverage:
        1. Schedule endpoint: Gets ALL past games (efficient, single call)
           - Needed for .last template variables
           - Works regardless of how long ago the last game was
        2. Scoreboard scanning: Gets future games (days_ahead days)
           - More reliable for playoffs (schedule endpoint may not include them)
           - Works for all sports including soccer

        This hybrid approach ensures .last variables work even during long breaks
        (bye weeks, all-star break, offseason) while still capturing playoffs.
        """
        # Apply team ID correction for known ESPN mismatches
        corrected_id = ESPN_TEAM_ID_CORRECTIONS.get((league, team_id))
        if corrected_id:
            logger.info("[ESPN] Correcting team ID %s -> %s for %s", team_id, corrected_id, league)
            team_id = corrected_id

        sport_league = self._get_sport_league_from_db(league)
        events = []
        date.today()
        seen_ids: set[str] = set()

        # 1. Get past games from schedule endpoint (all past games in one call)
        # Skipped for leagues without a teams endpoint and for synthetic
        # player_* ids (tennis) — /teams/{id}/schedule 400s on both (#282);
        # scoreboard scanning below is the only source for those.
        if league not in self.LEAGUES_WITHOUT_TEAMS and not team_id.startswith("player_"):
            past_events = self._get_past_games_from_schedule(team_id, league, sport_league)
            for event in past_events:
                if event.id not in seen_ids:
                    seen_ids.add(event.id)
                    events.append(event)

        # 2. Get future games from scoreboard scanning (reliable for playoffs)
        future_events = self._scan_scoreboard_for_team(team_id, league, days_ahead, sport_league)
        for event in future_events:
            if event.id not in seen_ids:
                seen_ids.add(event.id)
                events.append(event)

        events.sort(key=lambda e: e.start_time)
        return events

    def _get_past_games_from_schedule(
        self,
        team_id: str,
        league: str,
        sport_league: tuple[str, str] | None = None,
    ) -> list[Event]:
        """Get past games from ESPN's schedule endpoint.

        Returns all completed games from the season. More efficient than
        scanning scoreboards day by day, and works regardless of how long
        ago the last game was.

        Note: Uses full datetime comparison (not just date) because ESPN returns
        dates in UTC. A game at 10 PM EST on Jan 31 is 3 AM UTC on Feb 1, so
        date-only comparison with local date.today() would incorrectly exclude it.
        """
        data = self._client.get_team_schedule(league, team_id, sport_league)
        if not data:
            return []

        events = []
        now_utc = datetime.now(UTC)

        for event_data in data.get("events", []):
            # Parse the full datetime (includes timezone)
            event_date_str = event_data.get("date", "")
            if not event_date_str:
                continue

            event_datetime = self._parse_datetime(event_date_str)
            if not event_datetime:
                continue

            # Only include past games (before current time)
            # This correctly handles late-night games where UTC date differs from local date
            if event_datetime >= now_utc:
                continue

            event = self._parse_schedule_event(event_data, league)
            if event:
                events.append(event)

        return events

    def _parse_schedule_event(self, event_data: dict, league: str) -> Event | None:
        """Parse an event from the schedule endpoint.

        Schedule endpoint format is slightly different from scoreboard.
        """
        # Schedule endpoint uses similar structure to scoreboard
        # Try to parse using existing method
        return self._parse_event(event_data, league)

    def _scan_scoreboard_for_team(
        self,
        team_id: str,
        league: str,
        days_ahead: int,
        sport_league: tuple[str, str] | None = None,
    ) -> list[Event]:
        """Get future games by scanning scoreboard.

        Scans the scoreboard for the next N days, filtering for games
        involving the specified team. This approach works for all sports
        and captures both regular season and playoff games.

        When a cached events fetcher is injected (the normal path via
        SportsDataService), each day's full-league scoreboard is fetched once
        and shared across every team in the league — and with event-group
        matching, which reads the same cache. Falls back to direct per-day
        scoreboard fetches when used standalone (no cache wired).
        """
        events = []
        today = date.today()

        for day_offset in range(days_ahead):
            target_date = today + timedelta(days=day_offset)

            if self._cached_events_fn is not None:
                # Shared, cached league-wide events; league name is captured
                # inside the underlying get_events → no separate capture needed.
                for event in self._cached_events_fn(league, target_date):
                    if self._event_has_team(team_id, event):
                        events.append(event)
                continue

            date_str = target_date.strftime("%Y%m%d")
            data = self._client.get_scoreboard(league, date_str, sport_league)
            if not data:
                continue

            # Capture league name on first successful response
            if day_offset == 0:
                self._capture_league_name(data, league)

            for event_data in data.get("events", []):
                if self._team_in_event(team_id, event_data):
                    event = self._parse_event(event_data, league)
                    if event:
                        events.append(event)

        return events

    def _team_in_event(self, team_id: str, event_data: dict) -> bool:
        """Check if a team is playing in this event (raw scoreboard dict)."""
        competitions = event_data.get("competitions", [])
        if not competitions:
            return False

        for competitor in competitions[0].get("competitors", []):
            comp_team = competitor.get("team", {})
            if str(comp_team.get("id")) == str(team_id):
                return True
        return False

    @staticmethod
    def _event_has_team(team_id: str, event: Event) -> bool:
        """Check if a team is playing in this event (parsed Event)."""
        tid = str(team_id)
        for team in (event.home_team, event.away_team):
            if team and str(team.id) == tid:
                return True
        return False

    def get_team(self, team_id: str, league: str) -> Team | None:
        # Combat sports don't have teams endpoint - skip to avoid 404 spam
        if league in self.LEAGUES_WITHOUT_TEAMS:
            return None

        # Guard against empty team_id (ESPN sometimes returns None for fighter IDs)
        if not team_id:
            return None

        # Get sport/league from database config
        sport_league = self._get_sport_league_from_db(league)

        data = self._client.get_team(league, team_id, sport_league)
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
            short_name=team_data.get("shortDisplayName") or team_data.get("name") or "",
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

    # Leagues without summary endpoint support
    # These leagues only have scoreboard data - no per-event detail endpoint
    # When get_event() is called for these, we return None immediately to avoid 404s
    # Tennis: site/v2 summary returns HTTP 400 for atp/wta (#282)
    LEAGUES_WITHOUT_SUMMARY = {"ufc", "atp", "wta"}

    # Leagues without teams endpoint support
    # Leagues where /teams endpoint doesn't work or isn't needed:
    # - Combat sports (MMA, boxing): individual fighters, not teams
    # - Olympics: teams only in events, no team filtering/import needed
    # - Tennis: players ride as Teams with synthetic player_* ids (scoreboard
    #   athlete ids are null); ESPN's teams endpoints 400 on them (#282)
    LEAGUES_WITHOUT_TEAMS = {
        "ufc",
        "boxing",
        "olympics-mens-ice-hockey",
        "olympics-womens-ice-hockey",
        "atp",
        "wta",
    }

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Fetch single event with full details from summary endpoint."""
        # Some leagues don't have summary endpoints - scoreboard is the only source
        # See LEAGUES_WITHOUT_SUMMARY for the list
        if league in self.LEAGUES_WITHOUT_SUMMARY:
            logger.debug(
                "[ESPN] Summary endpoint not available for %s (league=%s)", event_id, league
            )
            return None

        # Get sport/league from database config
        sport_league = self._get_sport_league_from_db(league)

        data = self._client.get_event(league, event_id, sport_league)
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

        # Summary endpoint nests season under header (vs scoreboard's top level).
        # Pass it through so _parse_event sees the same shape either way.
        # Note: summary's header.season typically lacks the 'slug' field, so
        # soccer leagues fall back to type-number mapping (int 13xxx → None).
        # refresh_event_status preserves season_type from the cached event when
        # the refresh returns None, so soccer postseason still survives.
        event_data = {
            "id": event_id,
            "name": header.get("gameNote", ""),
            "shortName": self._build_short_name(competition),
            "date": competition.get("date"),
            "competitions": [competition],
            "season": header.get("season"),
        }

        event = self._parse_event(event_data, league)
        if event:
            # Per-event editorial copy lives at the summary top level (not in the
            # competition), so attach it here. Raw passthrough, empty when absent.
            article = data.get("article") or {}
            if article.get("type") == "Preview":
                event.game_preview = self._editorial_text(article)
            series = data.get("seasonseries") or []
            event.series_summary = (series[0].get("summary") if series else "") or ""
            # Structured preview (tvnk.15): recent-form W-L per team from
            # lastFiveGames — available days ahead, unlike preview prose.
            home_form, away_form = self._parse_last_five(
                data.get("lastFiveGames") or [], event
            )
            event.home_last_five = home_form
            event.away_last_five = away_form
            # Typed provider facts used by public variables and the optional
            # generated-preview formatter. Betting payloads are never parsed.
            from teamarr.providers.espn.preview import apply_generated_preview_fields

            apply_generated_preview_fields(data, event)
        return event

    @staticmethod
    def _parse_last_five(last_five: list, event: Event) -> tuple[str, str]:
        """W-L record over each team's last five games ('4-1'), from the
        summary's lastFiveGames blocks. Empty strings when absent."""
        forms = {"home": "", "away": ""}
        for entry in last_five:
            team_id = str((entry.get("team") or {}).get("id") or "")
            results = [
                (e.get("gameResult") or "").upper() for e in entry.get("events") or []
            ]
            if not team_id or not results:
                continue
            wins = results.count("W")
            losses = results.count("L")
            if wins + losses == 0:
                continue
            form = f"{wins}-{losses}"
            if event.home_team and team_id == str(event.home_team.id):
                forms["home"] = form
            elif event.away_team and team_id == str(event.away_team.id):
                forms["away"] = form
        return forms["home"], forms["away"]

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
            broadcast_markets = self._parse_broadcast_markets(competition.get("broadcasts", []))
            odds_data = self._parse_odds(competition.get("odds", []))

            # Editorial/context copy — straight from the scoreboard, no per-event call.
            game_recap = self._headline_of_type(competition, "Recap")
            notes = competition.get("notes") or []
            game_event_note = (notes[0].get("headline") if notes else "") or ""
            soccer_match_note = competition.get("altGameNote") or ""
            neutral_site = bool(competition.get("neutralSite"))
            self._editorial_canary.record(
                competition,
                sport=sport,
                # Same final indicators as is_event_final (ESPN emits both)
                is_final=status.state in ("final", "post"),
            )

            home_score = self._parse_score(home_data.get("score"))
            away_score = self._parse_score(away_data.get("score"))

            # Parse season type from ESPN data (slug-first, type-number fallback)
            season_data = data.get("season") or {}
            season_type = self._parse_season_type(season_data)
            season_year = season_data.get("year")

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
                season_type=season_type,
                season_year=season_year,
                game_recap=game_recap,
                game_event_note=game_event_note,
                soccer_match_note=soccer_match_note,
                neutral_site=neutral_site,
                broadcast_markets=broadcast_markets,
            )
        except Exception as e:
            logger.warning("[ESPN] Failed to parse event %s: %s", data.get("id", "unknown"), e)
            return None

    @staticmethod
    def _editorial_text(obj: dict) -> str:
        """EPG-friendly editorial copy from an ESPN headline/article object.

        Prefer `shortLinkText` — a clean, self-contained headline that carries
        the result/score and fits a guide-grid cell ('Mets beat Reds 9-1 to
        avoid sweep'). Fall back to the long `description` body, stripping the
        leftover AP-dateline em dash ('— Bo Bichette continued…' → 'Bo
        Bichette continued…'). US wire copy carries the dash; soccer does not,
        so the strip is conditional by construction. Empty when neither field
        is present.
        """
        short = (obj.get("shortLinkText") or "").strip()
        if short:
            return short
        desc = obj.get("description") or ""
        return re.sub(r"^\s*[—–-]\s+", "", desc).strip()

    @classmethod
    def _headline_of_type(cls, competition: dict, want_type: str) -> str:
        """Return EPG-friendly copy from the first headline matching want_type.

        ESPN tags scoreboard headlines by type ('Recap', 'Preview'); we select
        by tag rather than infer from game state. Empty when none present.
        """
        for headline in competition.get("headlines") or []:
            if headline.get("type") == want_type:
                return cls._editorial_text(headline)
        return ""

    def _parse_team(self, competitor: dict, league: str, sport: str) -> Team:
        """Parse competitor data into Team."""
        team_data = competitor.get("team", {})
        # Summary endpoint omits `shortDisplayName` (returns null) but always has
        # `name` populated with the short form ("Rays", "Blue Jays"). Scoreboard
        # has both. Fall back to `name` so the field stays populated regardless
        # of which endpoint the team came from. (#201)
        short_name = (
            team_data.get("shortDisplayName")
            or team_data.get("name")
            or ""
        )
        return Team(
            id=team_data.get("id", competitor.get("id", "")),
            provider=self.name,
            name=team_data.get("displayName", ""),
            short_name=short_name,
            abbreviation=team_data.get("abbreviation", ""),
            league=league,
            sport=sport,
            logo_url=self._extract_logo(team_data),
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

    @staticmethod
    def _parse_broadcast_markets(broadcasts_data: list) -> dict[str, str]:
        """Broadcast name → market ('national'/'home'/'away') from the
        scoreboard payload — data-driven feed discrimination for
        team-branded/regional channels ('Brewers.TV' → away, #343). The
        summary format carries no market and is skipped.
        """
        markets: dict[str, str] = {}
        for broadcast in broadcasts_data:
            market = broadcast.get("market")
            if not market or not isinstance(market, str):
                continue
            for name in broadcast.get("names") or []:
                if name:
                    markets[name] = market.lower()
        return markets

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

    # ESPN league-agnostic season slug → canonical value.
    # Slugs come from scoreboard per-event season dicts. Soccer knockout
    # rounds also expose slugs like semifinals/final/group-stage.
    _SEASON_SLUG_MAP = {
        "pre-season": SEASON_PRESEASON,
        "preseason": SEASON_PRESEASON,
        "regular-season": SEASON_REGULAR,
        "regular": SEASON_REGULAR,
        "post-season": SEASON_POSTSEASON,
        "postseason": SEASON_POSTSEASON,
        "off-season": SEASON_OFFSEASON,
        "offseason": SEASON_OFFSEASON,
        # Soccer knockouts
        "round-of-16": SEASON_POSTSEASON,
        "quarterfinals": SEASON_POSTSEASON,
        "semifinals": SEASON_POSTSEASON,
        "final": SEASON_POSTSEASON,
        "group-stage": SEASON_REGULAR,
    }

    # ESPN integer season_type values (US sports only; soccer uses opaque IDs).
    _SEASON_TYPE_NUM_MAP = {
        1: SEASON_PRESEASON,
        2: SEASON_REGULAR,
        3: SEASON_POSTSEASON,
        4: SEASON_OFFSEASON,
    }

    def _parse_season_type(self, season_data: dict | None) -> str | None:
        """Parse ESPN season data to a canonical season_type string.

        ESPN emits two signals — a slug (strings like 'post-season' or
        'semifinals') and a type number (1–4 for US sports, opaque IDs for
        soccer). Slug is preferred because it's meaningful across all leagues
        including soccer. Type number is the fallback — works for US sports
        but not for soccer (summary endpoint often omits slug, so soccer
        summary-path refresh returns None; refresh_event_status preserves
        the cached season_type in that case).

        Returns one of the SEASON_* canonical constants or None.
        """
        if not season_data:
            return None
        slug = (season_data.get("slug") or "").lower()
        if slug in self._SEASON_SLUG_MAP:
            return self._SEASON_SLUG_MAP[slug]
        season_type_num = season_data.get("type")
        if not isinstance(season_type_num, int):
            return None
        return self._SEASON_TYPE_NUM_MAP.get(season_type_num)

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
            logger.debug("[ESPN] Failed to parse odds: %s", e)
            return None

    def get_league_teams(self, league: str) -> list[Team]:
        """Fetch all teams for a league.

        Used by cache refresh to populate team_cache table.

        Args:
            league: Canonical league code (e.g., 'nfl', 'eng.1')

        Returns:
            List of Team objects for this league
        """
        # Combat sports don't have teams - skip to avoid 404 spam
        if league in self.LEAGUES_WITHOUT_TEAMS:
            logger.debug("[ESPN] Teams endpoint not available for %s (individual sport)", league)
            return []

        # Get sport/league from database (source of truth)
        sport_league = self._get_sport_league_from_db(league)
        data = self._client.get_teams(league, sport_league)
        if not data:
            return []

        teams = []

        # ESPN teams endpoint returns {"sports": [{"leagues": [{"teams": [...]}]}]}
        # or just {"teams": [...]} depending on endpoint version
        team_list = []
        sport = None
        if "teams" in data:
            team_list = data["teams"]
        else:
            try:
                # Extract canonical sport name from ESPN response and normalize
                espn_sport = data["sports"][0].get("name")
                if espn_sport:
                    sport = normalize_sport(espn_sport)
                team_list = data["sports"][0]["leagues"][0]["teams"]
            except (KeyError, IndexError):
                logger.warning("[ESPN] Unexpected teams response structure for %s", league)
                return []

        # Fall back to database/heuristics only if ESPN didn't provide sport
        if not sport:
            sport = self._get_sport(league)

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
            short_name=team_data.get("shortDisplayName") or team_data.get("name") or "",
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
        # Get sport/league from database config
        sport_league = self._get_sport_league_from_db(league)

        data = self._client.get_team(league, team_id, sport_league)
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
            # ESPN's team payload carries no rank field for any league (#710);
            # rank is filled from the league polls by SportsDataService.
            rank=None,
            playoff_seed=int(stats.get("playoffSeed", 0)) or None,
            games_back=float(stats.get("gamesBehind", 0)) or None,
            conference=conference,
            conference_abbrev=conference_abbrev,
            division=division,
            ppg=float(stats.get("avgPointsFor", 0)) or None,
            papg=float(stats.get("avgPointsAgainst", 0)) or None,
        )

    def get_rankings(self, league: str) -> dict[str, int]:
        """Fetch the league's current poll rankings as {team_id: rank}.

        Merges every live poll in the payload, AP first, so an FBS team gets its
        AP rank while FCS and D-II teams — whom AP never covers — still get
        theirs from their own polls. Tournament seedings and offseason-stale
        polls are skipped; see the module constants for why.
        """
        sport_league = self._get_sport_league_from_db(league)
        _, espn_league = self._client.get_sport_league(league, sport_league)
        if not league_publishes_rankings(espn_league):
            return {}

        data = self._client.get_rankings(league, sport_league)
        if not data or not isinstance(data.get("rankings"), list):
            return {}

        polls = sorted(
            (p for p in data["rankings"] if isinstance(p, dict)),
            key=lambda p: str(p.get("type", "")).lower() != RANKING_PRIMARY_POLL_TYPE,
        )

        rankings: dict[str, int] = {}
        for poll in polls:
            if str(poll.get("type", "")).lower() == RANKING_SEEDING_POLL_TYPE:
                continue
            entries = poll.get("ranks")
            if not isinstance(entries, list) or self._poll_is_stale(entries, league, poll):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                team_id = str((entry.get("team") or {}).get("id") or "")
                current = entry.get("current")
                if not isinstance(current, (int, str)):
                    continue
                try:
                    rank = int(current)
                except ValueError:
                    continue
                if team_id and 1 <= rank <= RANKING_MAX:
                    rankings.setdefault(team_id, rank)

        logger.debug("[ESPN] %s rankings: %d ranked teams", league, len(rankings))
        return rankings

    def _poll_is_stale(self, entries: list, league: str, poll: dict) -> bool:
        """True when the poll's newest entry predates the staleness cutoff."""
        latest = max(
            (d for d in (self._parse_poll_date(e) for e in entries) if d is not None),
            default=None,
        )
        if latest is None:
            return False
        if datetime.now(UTC) - latest <= timedelta(days=RANKING_POLL_MAX_AGE_DAYS):
            return False
        logger.debug(
            "[ESPN] %s poll '%s' is stale (last updated %s) - skipping",
            league,
            poll.get("name"),
            latest.date(),
        )
        return True

    @staticmethod
    def _parse_poll_date(entry: object) -> datetime | None:
        """Parse a rank entry's lastUpdated/date stamp (ESPN uses 'Z' suffixes)."""
        if not isinstance(entry, dict):
            return None
        for field in ("lastUpdated", "date"):
            raw = entry.get(field)
            if not isinstance(raw, str):
                continue
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None

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
