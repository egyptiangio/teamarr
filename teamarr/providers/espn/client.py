"""ESPN API HTTP client.

Handles raw HTTP requests to ESPN endpoints.
No data transformation - just fetch and return JSON.
"""

import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_CORE_URL = "http://sports.core.api.espn.com/v2/sports"

# UFC athlete endpoint (for fighter profiles)
ESPN_UFC_ATHLETE_URL = "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/athletes"

COLLEGE_SCOREBOARD_GROUPS = {
    "mens-college-basketball": "50",
    "womens-college-basketball": "50",
    # Note: college-football omitted to return both FBS + FCS games
    # Note: mens-college-hockey does NOT need groups param
}

# ESPN team ID corrections for known mismatches between /teams endpoint and scoreboard
# Format: (league, wrong_id) -> correct_id
# These are cases where ESPN's /teams endpoint returns a different ID than the scoreboard uses
ESPN_TEAM_ID_CORRECTIONS: dict[tuple[str, str], str] = {
    # Minnesota State Mavericks: /teams returns 2364 (generic school ID), scoreboard uses 24059
    ("womens-college-hockey", "2364"): "24059",
}


class ESPNClient:
    """Low-level ESPN API client."""

    def __init__(
        self,
        timeout: float = 15.0,
        retry_count: int = 3,
        retry_delay: float = 1.0,
    ):
        self._timeout = timeout
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                # Double-check after acquiring lock
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=self._timeout,
                        # Limit concurrent connections to avoid overwhelming ESPN API
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    )
        return self._client

    def _request(self, url: str, params: dict | None = None) -> dict | None:
        """Make HTTP request with retry logic."""
        for attempt in range(self._retry_count):
            try:
                client = self._get_client()
                response = client.get(url, params=params)
                response.raise_for_status()
                logger.debug("[FETCH] %s", url.split("/sports/")[-1] if "/sports/" in url else url)
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.warning("[ESPN] HTTP %d for %s", e.response.status_code, url)
                if attempt < self._retry_count - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                return None
            except (httpx.RequestError, RuntimeError, OSError) as e:
                # RuntimeError: "Cannot send a request, as the client has been closed"
                # OSError: "Bad file descriptor" from stale connections
                logger.warning("[ESPN] Request failed for %s: %s", url, e)
                # Don't reset client here - causes race conditions in parallel processing
                # httpx connection pool handles stale connections automatically
                if attempt < self._retry_count - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                return None

        return None

    def _reset_client(self) -> None:
        """Reset the HTTP client to clear stale connections."""
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                except Exception as e:
                    logger.debug("[ESPN] Error closing HTTP client: %s", e)
                self._client = None

    def get_sport_league(
        self, league: str, override: tuple[str, str] | None = None
    ) -> tuple[str, str]:
        """Convert canonical league to ESPN sport/league pair.

        Args:
            league: Canonical league code (e.g., 'nfl', 'nba')
            override: (sport, league) tuple from database config (required for non-soccer)

        Returns:
            (sport, espn_league) tuple for API path construction
        """
        # Database config is the source of truth
        if override:
            return override
        # Soccer leagues use dot notation - can infer sport
        if "." in league:
            return ("soccer", league)
        # No config provided - log warning and return league as-is
        logger.warning("[ESPN] No database config for league '%s' - add to leagues table", league)
        return ("unknown", league)

    def _correct_team_id(self, league: str, team_id: str) -> str:
        """Apply team ID corrections for known ESPN mismatches.

        Some teams have different IDs in ESPN's /teams endpoint vs scoreboard.
        This maps the wrong ID (from /teams) to the correct ID (from scoreboard).
        """
        corrected = ESPN_TEAM_ID_CORRECTIONS.get((league, team_id))
        if corrected:
            logger.info("[ESPN] Correcting team ID %s -> %s for %s", team_id, corrected, league)
            return corrected
        return team_id

    def get_scoreboard(
        self,
        league: str,
        date_str: str,
        sport_league: tuple[str, str] | None = None,
    ) -> dict | None:
        """Fetch scoreboard for a league on a given date.

        Args:
            league: Canonical league code (e.g., 'nfl', 'nba')
            date_str: Date in YYYYMMDD format
            sport_league: Optional (sport, league) tuple from database config

        Returns:
            Raw ESPN response or None on error
        """
        sport, espn_league = self.get_sport_league(league, sport_league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/scoreboard"
        params = {"dates": date_str}

        if league in COLLEGE_SCOREBOARD_GROUPS:
            params["groups"] = COLLEGE_SCOREBOARD_GROUPS[league]

        return self._request(url, params)

    def get_league_info(
        self,
        league: str,
        sport_league: tuple[str, str] | None = None,
    ) -> dict | None:
        """Fetch league metadata including logo from scoreboard endpoint.

        Args:
            league: Canonical league code (e.g., 'eng.fa', 'uefa.champions')
            sport_league: Optional (sport, league) tuple

        Returns:
            Dict with name, logo_url, abbreviation or None on error
        """
        sport, espn_league = self.get_sport_league(league, sport_league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/scoreboard"

        data = self._request(url)
        if not data:
            return None

        leagues = data.get("leagues", [])
        if not leagues:
            return None

        league_data = leagues[0]
        logo_url = None

        # Extract logo - prefer default, fallback to first
        logos = league_data.get("logos", [])
        for logo in logos:
            rel = logo.get("rel", [])
            if "default" in rel:
                logo_url = logo.get("href")
                break
        if not logo_url and logos:
            logo_url = logos[0].get("href")

        return {
            "name": league_data.get("name"),
            "abbreviation": league_data.get("abbreviation"),
            "logo_url": logo_url,
            "id": league_data.get("id"),
        }

    def get_team_schedule(
        self,
        league: str,
        team_id: str,
        sport_league: tuple[str, str] | None = None,
    ) -> dict | None:
        """Fetch schedule for a specific team.

        Args:
            league: Canonical league code
            team_id: ESPN team ID
            sport_league: Optional (sport, league) tuple from database config

        Returns:
            Raw ESPN response or None on error
        """
        team_id = self._correct_team_id(league, team_id)
        sport, espn_league = self.get_sport_league(league, sport_league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams/{team_id}/schedule"
        return self._request(url)

    def get_team(
        self,
        league: str,
        team_id: str,
        sport_league: tuple[str, str] | None = None,
    ) -> dict | None:
        """Fetch team information.

        Args:
            league: Canonical league code
            team_id: ESPN team ID
            sport_league: Optional (sport, league) tuple from database config

        Returns:
            Raw ESPN response or None on error
        """
        team_id = self._correct_team_id(league, team_id)
        sport, espn_league = self.get_sport_league(league, sport_league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams/{team_id}"
        return self._request(url)

    def get_event(
        self,
        league: str,
        event_id: str,
        sport_league: tuple[str, str] | None = None,
    ) -> dict | None:
        """Fetch a single event by ID.

        Args:
            league: Canonical league code
            event_id: ESPN event ID
            sport_league: Optional (sport, league) tuple from database config

        Returns:
            Raw ESPN response or None on error
        """
        sport, espn_league = self.get_sport_league(league, sport_league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/summary"
        return self._request(url, {"event": event_id})

    def get_teams(self, league: str, sport_league: tuple[str, str] | None = None) -> dict | None:
        """Fetch all teams for a league.

        Args:
            league: Canonical league code
            sport_league: Optional (sport, league) tuple from database config

        Returns:
            Raw ESPN response with teams list or None on error
        """
        sport, espn_league = self.get_sport_league(league, sport_league)
        url = f"{ESPN_BASE_URL}/{sport}/{espn_league}/teams"
        return self._request(url, {"limit": 1000})

    # UFC-specific endpoints

    def get_ufc_scoreboard(self) -> dict | None:
        """Fetch UFC scoreboard with correct bout times.

        The scoreboard endpoint returns accurate segment times, unlike the
        app API which is 3 hours off.

        Returns:
            Raw ESPN scoreboard response or None on error
        """
        url = f"{ESPN_BASE_URL}/mma/ufc/scoreboard"
        return self._request(url)

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

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
