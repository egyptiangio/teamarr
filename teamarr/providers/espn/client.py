"""ESPN API HTTP client.

Handles raw HTTP requests to ESPN endpoints.
No data transformation - just fetch and return JSON.

Configuration via environment variables:
    ESPN_MAX_CONNECTIONS: Max concurrent connections (default: 100)
    ESPN_TIMEOUT: Request timeout in seconds (default: 10)
    ESPN_RETRY_COUNT: Number of retry attempts (default: 3)
"""

import logging
import os
import random
import threading
import time

import httpx

logger = logging.getLogger(__name__)

# Environment variable configuration with defaults
# These allow users with DNS throttling (PiHole, AdGuard) to tune performance
ESPN_MAX_CONNECTIONS = int(os.environ.get("ESPN_MAX_CONNECTIONS", 100))
ESPN_TIMEOUT = float(os.environ.get("ESPN_TIMEOUT", 10.0))
ESPN_RETRY_COUNT = int(os.environ.get("ESPN_RETRY_COUNT", 3))

# Retry backoff configuration (ESPN-tuned)
# ESPN is fast and reliable, so we use short delays with jitter
RETRY_BASE_DELAY = 0.5  # Start at 500ms
RETRY_MAX_DELAY = 10.0  # Cap at 10s (ESPN rarely needs more)
RETRY_JITTER = 0.3  # ±30% randomization to prevent thundering herd

# Rate limit (429) handling - reactive defense
# ESPN rarely rate-limits, but we handle it gracefully if it happens
RATE_LIMIT_BASE_DELAY = 5.0  # Start at 5s for 429s (more serious)
RATE_LIMIT_MAX_DELAY = 60.0  # Cap at 60s
RATE_LIMIT_MAX_RETRIES = 3  # Give up after 3 rate-limit retries

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
    """Low-level ESPN API client.

    Connection pool is configured to maximize keepalive connections, reducing
    DNS lookups. This helps users with rate-limited DNS (PiHole, AdGuard).

    All settings can be tuned via environment variables for constrained environments.
    """

    def __init__(
        self,
        timeout: float | None = None,
        retry_count: int | None = None,
        max_connections: int | None = None,
    ):
        self._timeout = timeout if timeout is not None else ESPN_TIMEOUT
        self._retry_count = retry_count if retry_count is not None else ESPN_RETRY_COUNT
        self._max_connections = (
            max_connections if max_connections is not None else ESPN_MAX_CONNECTIONS
        )
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                # Double-check after acquiring lock
                if self._client is None:
                    # Set keepalive = max_connections to maximize connection reuse
                    # This reduces DNS lookups, helping users with DNS throttling
                    self._client = httpx.Client(
                        timeout=self._timeout,
                        limits=httpx.Limits(
                            max_connections=self._max_connections,
                            max_keepalive_connections=self._max_connections,
                        ),
                    )
        return self._client

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate retry delay with exponential backoff and jitter.

        ESPN-tuned: short base delay (0.5s) since ESPN is fast,
        with jitter to prevent thundering herd when multiple
        parallel requests retry simultaneously.

        Args:
            attempt: Zero-based attempt number (0, 1, 2...)

        Returns:
            Delay in seconds with jitter applied
        """
        # Exponential backoff: 0.5, 1, 2, 4... capped at 10s
        base_delay = RETRY_BASE_DELAY * (2**attempt)
        capped = min(base_delay, RETRY_MAX_DELAY)
        # Add jitter: ±30% randomization
        jitter = capped * RETRY_JITTER * (2 * random.random() - 1)
        return max(0.1, capped + jitter)  # Minimum 100ms

    def _request(self, url: str, params: dict | None = None) -> dict | None:
        """Make HTTP request with retry logic.

        Uses exponential backoff with jitter for resilience against
        transient failures and DNS throttling. Handles 429 rate limits
        with longer backoff and Retry-After header support.
        """
        rate_limit_retries = 0

        for attempt in range(self._retry_count + RATE_LIMIT_MAX_RETRIES):
            try:
                client = self._get_client()
                response = client.get(url, params=params)

                # Handle 429 rate limit separately with longer backoff
                if response.status_code == 429:
                    rate_limit_retries += 1
                    if rate_limit_retries > RATE_LIMIT_MAX_RETRIES:
                        logger.error(
                            "[ESPN] Rate limit (429) persisted after %d retries for %s",
                            RATE_LIMIT_MAX_RETRIES,
                            url,
                        )
                        return None

                    # Respect Retry-After header if present
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), RATE_LIMIT_MAX_DELAY)
                        except ValueError:
                            delay = RATE_LIMIT_BASE_DELAY * (2 ** (rate_limit_retries - 1))
                    else:
                        delay = min(
                            RATE_LIMIT_BASE_DELAY * (2 ** (rate_limit_retries - 1)),
                            RATE_LIMIT_MAX_DELAY,
                        )

                    logger.warning(
                        "[ESPN] Rate limited (429). Retry %d/%d in %.1fs for %s",
                        rate_limit_retries,
                        RATE_LIMIT_MAX_RETRIES,
                        delay,
                        url,
                    )
                    time.sleep(delay)
                    continue

                response.raise_for_status()
                logger.debug("[FETCH] %s", url.split("/sports/")[-1] if "/sports/" in url else url)
                return response.json()

            except httpx.HTTPStatusError as e:
                logger.warning("[ESPN] HTTP %d for %s", e.response.status_code, url)
                if attempt < self._retry_count - 1:
                    delay = self._calculate_delay(attempt)
                    time.sleep(delay)
                    continue
                return None
            except (httpx.RequestError, RuntimeError, OSError) as e:
                # RuntimeError: "Cannot send a request, as the client has been closed"
                # OSError: "Bad file descriptor" from stale connections
                # httpx.RequestError: DNS failures, connection refused, etc.
                logger.warning("[ESPN] Request failed for %s: %s", url, e)
                # Don't reset client here - causes race conditions in parallel processing
                # httpx connection pool handles stale connections automatically
                if attempt < self._retry_count - 1:
                    delay = self._calculate_delay(attempt)
                    time.sleep(delay)
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
