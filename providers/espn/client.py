"""ESPN API HTTP client.

Handles raw HTTP requests to ESPN endpoints.
No data transformation - just fetch and return JSON.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_CORE_URL = "http://sports.core.api.espn.com/v2/sports"

SPORT_MAPPING = {
    # US Major Leagues
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "wnba": ("basketball", "wnba"),
    "mls": ("soccer", "usa.1"),
    # Combat Sports
    "ufc": ("mma", "ufc"),
    # College Sports
    "mens-college-basketball": ("basketball", "mens-college-basketball"),
    "womens-college-basketball": ("basketball", "womens-college-basketball"),
    "college-football": ("football", "college-football"),
    "mens-college-hockey": ("hockey", "mens-college-hockey"),
    "womens-college-hockey": ("hockey", "womens-college-hockey"),
    # Rugby Union
    "six-nations": ("rugby", "180659"),
    "rugby-championship": ("rugby", "244293"),
    "premiership-rugby": ("rugby", "267979"),
    "united-rugby-championship": ("rugby", "270557"),
    "super-rugby": ("rugby", "242041"),
    "mlr": ("rugby", "289262"),
    "rugby-world-cup": ("rugby", "164205"),
    # Rugby League
    "nrl": ("rugby-league", "3"),
    # Tennis
    "atp": ("tennis", "atp"),
    "wta": ("tennis", "wta"),
    # Golf
    "pga": ("golf", "pga"),
    "lpga": ("golf", "lpga"),
    "liv": ("golf", "liv"),
    "dp-world": ("golf", "eur"),
    "champions-tour": ("golf", "champions-tour"),
    # Motorsport / Racing
    "f1": ("racing", "f1"),
    "indycar": ("racing", "irl"),
    "nascar": ("racing", "nascar-premier"),
    "nascar-xfinity": ("racing", "nascar-secondary"),
    "nascar-truck": ("racing", "nascar-truck"),
}

# UFC uses different API endpoints
ESPN_UFC_EVENTS_URL = "https://api-app.espn.com/v1/sports/mma/ufc/events"
ESPN_UFC_ATHLETE_URL = "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/athletes"

COLLEGE_SCOREBOARD_GROUPS = {
    "mens-college-basketball": "50",
    "womens-college-basketball": "50",
    "college-football": "80",
    # Note: mens-college-hockey does NOT need groups param
}


class ESPNClient:
    """Low-level ESPN API client."""

    def __init__(
        self,
        timeout: float = 10.0,
        retry_count: int = 3,
        retry_delay: float = 1.0,
    ):
        self._timeout = timeout
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=10),
            )
        return self._client

    def _request(self, url: str, params: dict | None = None) -> dict | None:
        """Make HTTP request with retry logic."""
        client = self._get_client()

        for attempt in range(self._retry_count):
            try:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP {e.response.status_code} for {url}")
                if attempt < self._retry_count - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                return None
            except httpx.RequestError as e:
                logger.warning(f"Request failed for {url}: {e}")
                if attempt < self._retry_count - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                return None

        return None

    def get_sport_league(self, league: str) -> tuple[str, str]:
        """Convert canonical league to ESPN sport/league pair."""
        if league in SPORT_MAPPING:
            return SPORT_MAPPING[league]
        if "." in league:
            return ("soccer", league)
        return ("football", league)

    def get_scoreboard(self, league: str, date_str: str) -> dict | None:
        """Fetch scoreboard for a league on a given date.

        Args:
            league: Canonical league code (e.g., 'nfl', 'nba')
            date_str: Date in YYYYMMDD format

        Returns:
            Raw ESPN response or None on error
        """
        sport, espn_league = self.get_sport_league(league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/scoreboard"
        params = {"dates": date_str}

        if league in COLLEGE_SCOREBOARD_GROUPS:
            params["groups"] = COLLEGE_SCOREBOARD_GROUPS[league]

        return self._request(url, params)

    def get_team_schedule(self, league: str, team_id: str) -> dict | None:
        """Fetch schedule for a specific team.

        Args:
            league: Canonical league code
            team_id: ESPN team ID

        Returns:
            Raw ESPN response or None on error
        """
        sport, espn_league = self.get_sport_league(league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams/{team_id}/schedule"
        return self._request(url)

    def get_team(self, league: str, team_id: str) -> dict | None:
        """Fetch team information.

        Args:
            league: Canonical league code
            team_id: ESPN team ID

        Returns:
            Raw ESPN response or None on error
        """
        sport, espn_league = self.get_sport_league(league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams/{team_id}"
        return self._request(url)

    def get_event(self, league: str, event_id: str) -> dict | None:
        """Fetch a single event by ID.

        Args:
            league: Canonical league code
            event_id: ESPN event ID

        Returns:
            Raw ESPN response or None on error
        """
        sport, espn_league = self.get_sport_league(league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/summary"
        return self._request(url, {"event": event_id})

    def get_teams(self, league: str) -> dict | None:
        """Fetch all teams for a league.

        Args:
            league: Canonical league code

        Returns:
            Raw ESPN response with teams list or None on error
        """
        sport, espn_league = self.get_sport_league(league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams"
        return self._request(url, {"limit": 500})

    def get_team_roster(self, league: str, team_id: str) -> dict | None:
        """Fetch team roster data including coaches.

        Args:
            league: Canonical league code
            team_id: ESPN team ID

        Returns:
            Raw ESPN response with roster/coach data or None on error
        """
        sport, espn_league = self.get_sport_league(league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams/{team_id}/roster"
        return self._request(url)

    # UFC-specific endpoints

    def get_ufc_events(self) -> dict | None:
        """Fetch all UFC events from the app API.

        Returns:
            Raw ESPN response with events list or None on error
        """
        return self._request(ESPN_UFC_EVENTS_URL)

    def get_fighter(self, fighter_id: str) -> dict | None:
        """Fetch UFC fighter profile.

        Args:
            fighter_id: ESPN fighter/athlete ID

        Returns:
            Raw ESPN response or None on error
        """
        url = f"{ESPN_UFC_ATHLETE_URL}/{fighter_id}"
        return self._request(url)

    def get_fighter_record(self, fighter_id: str) -> dict | None:
        """Fetch UFC fighter record (W-L-D with breakdown).

        Args:
            fighter_id: ESPN fighter/athlete ID

        Returns:
            Raw ESPN response with record data or None on error
        """
        url = f"{ESPN_UFC_ATHLETE_URL}/{fighter_id}/records"
        return self._request(url)

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
