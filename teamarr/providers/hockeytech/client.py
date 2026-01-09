"""HockeyTech API HTTP client.

Fetches data from the HockeyTech API that powers CHL league websites (OHL, WHL, QMJHL)
plus AHL, PWHL, and USHL. This is an undocumented API - endpoints discovered via
browser network inspection.

No rate limiting observed, but we implement caching to be respectful.

Layer separation: Uses LeagueMappingSource for league routing (same as TSDB).
API keys are constants since they're public keys from official league websites.
"""

import logging
import threading
from datetime import date

import httpx

from teamarr.core.interfaces import LeagueMappingSource
from teamarr.utilities.cache import TTLCache, make_cache_key

logger = logging.getLogger(__name__)

HOCKEYTECH_BASE_URL = "https://lscluster.hockeytech.com/feed/"

# Cache TTLs (seconds) - match TSDB pattern
CACHE_TTL_SCHEDULE = 30 * 60  # 30 minutes - full season schedule
CACHE_TTL_TEAMS = 24 * 60 * 60  # 24 hours - teams rarely change


def get_cache_ttl_for_date(target_date: date) -> int:
    """Get cache TTL based on date proximity.

    Past:       7 days (effectively permanent until cleanup)
    Today:      30 minutes (live scores)
    Tomorrow:   4 hours (flex scheduling possible)
    Days 3-7:   8 hours (mostly stable)
    Days 8+:    24 hours (playoffs/new games may appear)
    """
    today = date.today()
    days_from_today = (target_date - today).days

    if days_from_today < 0:  # Past
        return 7 * 24 * 3600  # 7 days
    elif days_from_today == 0:  # Today
        return 30 * 60  # 30 minutes
    elif days_from_today == 1:  # Tomorrow
        return 4 * 3600  # 4 hours
    elif days_from_today <= 7:  # Days 3-7
        return 8 * 3600  # 8 hours
    else:  # Days 8+
        return 24 * 3600  # 24 hours


# API keys by client_code - extracted from official league websites
# These are public keys used by the official league websites
# Source: https://gist.github.com/sethwv/e48703d6a8a557391e0938849329e329
API_KEYS: dict[str, str] = {
    # CHL - Canadian Hockey League and member leagues (shared key)
    "chl": "f1aa699db3d81487",
    "ohl": "f1aa699db3d81487",
    "whl": "f1aa699db3d81487",
    "lhjmq": "f1aa699db3d81487",  # QMJHL uses lhjmq as client_code
    # AHL - American Hockey League
    "ahl": "50c2cd9b5e18e390",
    # ECHL - East Coast Hockey League
    "echl": "2c2b89ea7345cae8",
    # PWHL - Professional Women's Hockey League
    "pwhl": "446521baf8c38984",
    # USHL - United States Hockey League
    "ushl": "e828f89b243dc43f",
    # Canadian Junior A leagues
    "ojhl": "77a0bd73d9d363d3",   # Ontario Junior Hockey League
    "bchl": "ca4e9e599d4dae55",   # British Columbia Hockey League
    "sjhl": "2fb5c2e84bf3e4a8",   # Saskatchewan Junior Hockey League
    "ajhl": "cbe60a1d91c44ade",   # Alberta Junior Hockey League
    "mjhl": "f894c324fe5fd8f0",   # Manitoba Junior Hockey League
    "mhl": "4a948e7faf5ee58d",    # Maritime Junior Hockey League
}


class HockeyTechClient:
    """Low-level HockeyTech API client.

    Provides access to CHL league data (OHL, WHL, QMJHL) plus AHL, PWHL, USHL
    via the HockeyTech API that powers their official websites.

    Uses LeagueMappingSource for league routing - provider_league_id in database
    contains the HockeyTech client_code (ohl, whl, lhjmq, etc.).
    """

    def __init__(
        self,
        league_mapping_source: LeagueMappingSource | None = None,
        timeout: float = 10.0,
        retry_count: int = 3,
    ):
        self._league_mapping_source = league_mapping_source
        self._timeout = timeout
        self._retry_count = retry_count
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()
        self._cache = TTLCache()

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client (thread-safe)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=self._timeout,
                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
                    )
        return self._client

    def supports_league(self, league: str) -> bool:
        """Check if we support this league via LeagueMappingSource."""
        if not self._league_mapping_source:
            return False
        return self._league_mapping_source.supports_league(league, "hockeytech")

    def get_league_config(self, league: str) -> tuple[str, str] | None:
        """Get (client_code, api_key) for a league.

        Uses LeagueMappingSource to get client_code from provider_league_id,
        then looks up API key from constants.
        """
        if not self._league_mapping_source:
            return None

        mapping = self._league_mapping_source.get_mapping(league, "hockeytech")
        if not mapping:
            return None

        client_code = mapping.provider_league_id
        api_key = API_KEYS.get(client_code)
        if not api_key:
            logger.warning(f"No API key for HockeyTech client_code: {client_code}")
            return None

        return (client_code, api_key)

    def get_sport(self, league: str) -> str:
        """Get sport name for a league."""
        if not self._league_mapping_source:
            return "Hockey"
        mapping = self._league_mapping_source.get_mapping(league, "hockeytech")
        return mapping.sport if mapping else "Hockey"

    def _request(
        self,
        client_code: str,
        api_key: str,
        view: str,
        extra_params: dict | None = None,
    ) -> dict | None:
        """Make HTTP request to HockeyTech API.

        Args:
            client_code: League client code (ohl, whl, lhjmq, etc.)
            api_key: API key for this league
            view: API view (schedule, scorebar, teamsbyseason, etc.)
            extra_params: Additional query parameters

        Returns:
            Parsed JSON response or None on error
        """
        params = {
            "feed": "modulekit",
            "key": api_key,
            "view": view,
            "client_code": client_code,
            "fmt": "json",
            "lang": "en",
        }
        if extra_params:
            params.update(extra_params)

        for attempt in range(self._retry_count):
            try:
                client = self._get_client()
                response = client.get(HOCKEYTECH_BASE_URL, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"HockeyTech HTTP {e.response.status_code} for {view} "
                    f"(attempt {attempt + 1}/{self._retry_count})"
                )
                if attempt < self._retry_count - 1:
                    continue
                return None
            except (httpx.RequestError, RuntimeError, OSError) as e:
                logger.warning(
                    f"HockeyTech request failed for {view}: {e} "
                    f"(attempt {attempt + 1}/{self._retry_count})"
                )
                if attempt < self._retry_count - 1:
                    continue
                return None

        return None

    def get_schedule(self, league: str) -> list[dict]:
        """Get full season schedule for a league.

        Args:
            league: League code (ohl, whl, qmjhl, ahl, pwhl, ushl)

        Returns:
            List of game dicts from SiteKit.Schedule
        """
        config = self.get_league_config(league)
        if not config:
            logger.warning(f"Unknown HockeyTech league: {league}")
            return []

        client_code, api_key = config
        cache_key = make_cache_key("hockeytech", "schedule", league)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"HockeyTech cache hit: {cache_key}")
            return cached

        data = self._request(client_code, api_key, "schedule")
        if not data:
            return []

        schedule = data.get("SiteKit", {}).get("Schedule", [])
        if schedule:
            self._cache.set(cache_key, schedule, CACHE_TTL_SCHEDULE)
            logger.debug(f"HockeyTech cached {len(schedule)} games for {league}")

        return schedule

    def get_events_by_date(self, league: str, target_date: date) -> list[dict]:
        """Get games for a specific date.

        Filters the full schedule by date.

        Args:
            league: League code (ohl, whl, qmjhl, ahl, pwhl, ushl)
            target_date: Date to filter for

        Returns:
            List of game dicts for that date
        """
        schedule = self.get_schedule(league)
        date_str = target_date.strftime("%Y-%m-%d")

        return [game for game in schedule if game.get("date_played") == date_str]

    # Days to look back for .last variable resolution
    DAYS_BACK = 7

    def get_team_schedule(self, league: str, team_id: str, days_ahead: int = 14) -> list[dict]:
        """Get schedule for a specific team including past and future games.

        Filters the full schedule for games where this team is home or away.
        Includes past games (DAYS_BACK) for .last template variable resolution.

        Args:
            league: League code
            team_id: HockeyTech team ID
            days_ahead: Number of days to look ahead

        Returns:
            List of game dicts for this team (sorted by date)
        """
        from datetime import timedelta

        schedule = self.get_schedule(league)
        today = date.today()
        start_date = today - timedelta(days=self.DAYS_BACK)
        end_date = today + timedelta(days=days_ahead)

        team_games = []
        for game in schedule:
            # Check if team is home or away
            if game.get("home_team") == team_id or game.get("visiting_team") == team_id:
                # Check date is within range (includes past games)
                game_date_str = game.get("date_played")
                if game_date_str:
                    try:
                        game_date = date.fromisoformat(game_date_str)
                        if start_date <= game_date <= end_date:
                            team_games.append(game)
                    except ValueError:
                        continue

        # Sort by date
        team_games.sort(key=lambda g: g.get("date_played", ""))
        return team_games

    def get_teams(self, league: str) -> list[dict]:
        """Get all teams in a league.

        Uses teamsbyseason view.

        Args:
            league: League code (ohl, whl, qmjhl, ahl, pwhl, ushl)

        Returns:
            List of team dicts
        """
        config = self.get_league_config(league)
        if not config:
            logger.warning(f"Unknown HockeyTech league: {league}")
            return []

        client_code, api_key = config
        cache_key = make_cache_key("hockeytech", "teams", league)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"HockeyTech cache hit: {cache_key}")
            return cached

        data = self._request(client_code, api_key, "teamsbyseason")
        if not data:
            return []

        teams = data.get("SiteKit", {}).get("Teamsbyseason", [])
        if teams:
            self._cache.set(cache_key, teams, CACHE_TTL_TEAMS)
            logger.debug(f"HockeyTech cached {len(teams)} teams for {league}")

        return teams

    def get_scorebar(self, league: str) -> list[dict]:
        """Get live scorebar data.

        Returns recent and upcoming games with live status.
        Not cached - used for live data.

        Args:
            league: League code (ohl, whl, qmjhl, ahl, pwhl, ushl)

        Returns:
            List of game dicts with live status
        """
        config = self.get_league_config(league)
        if not config:
            return []

        client_code, api_key = config
        data = self._request(client_code, api_key, "scorebar")
        if not data:
            return []

        return data.get("SiteKit", {}).get("Scorebar", [])

    def get_game(self, league: str, game_id: str) -> dict | None:
        """Get a specific game by ID.

        Searches the full schedule for the game.

        Args:
            league: League code
            game_id: HockeyTech game ID

        Returns:
            Game dict or None if not found
        """
        schedule = self.get_schedule(league)
        for game in schedule:
            if str(game.get("game_id")) == str(game_id):
                return game
        return None

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
