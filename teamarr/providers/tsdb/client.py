"""TheSportsDB API HTTP client.

Handles raw HTTP requests to TSDB endpoints with rate limiting and caching.
No data transformation - just fetch and return JSON.

Rate limits (free tier):
- 30 requests/minute overall
- Some endpoints: 1 request/minute

Caching is aggressive to stay within rate limits:
- Events by date: 2 hours (games don't change often)
- Teams in league: 24 hours (teams rarely change)
- League next events: 1 hour
- Team search: 24 hours

Rate limit handling:
- Preemptive: Sliding window limiter prevents hitting API limit
- Reactive: If we get 429, wait and retry (tracks statistics)
- All waits are tracked for UI feedback

Dependencies are injected via constructor:
- LeagueMappingSource: For league configuration lookup
- api_key: From database settings (passed by factory in providers/__init__.py)

This client has NO direct database access - all config is injected.
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from teamarr.core import LeagueMappingSource
from teamarr.utilities.cache import TTLCache, make_cache_key

logger = logging.getLogger(__name__)

TSDB_BASE_URL = "https://www.thesportsdb.com/api/v1/json"

# Cache TTLs (seconds) - tiered by date proximity
TSDB_CACHE_TTL_TEAMS = 24 * 60 * 60  # 24 hours - teams in league
TSDB_CACHE_TTL_NEXT_EVENTS = 1 * 60 * 60  # 1 hour - league next events
TSDB_CACHE_TTL_SEARCH = 24 * 60 * 60  # 24 hours - team search


def get_cache_ttl_for_date(target_date: date) -> int:
    """Get cache TTL based on how far the date is from today.

    Past:       7 days (effectively permanent until cleanup)
    Today:      30 minutes (flex times, live scores)
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


@dataclass
class RateLimitStats:
    """Statistics about rate limiting for UI feedback.

    Tracks both preemptive waits (our limiter) and reactive waits (429 responses).
    Can be used by the UI to show users when rate limiting is affecting performance.
    """

    total_requests: int = 0
    preemptive_waits: int = 0  # Times our limiter made us wait
    reactive_waits: int = 0  # Times we hit 429 from API
    total_wait_seconds: float = 0.0
    last_wait_at: datetime | None = None
    last_wait_seconds: float = 0.0
    session_start: datetime = field(default_factory=datetime.now)

    @property
    def is_rate_limited(self) -> bool:
        """True if we've had to wait at all this session."""
        return self.preemptive_waits > 0 or self.reactive_waits > 0

    @property
    def total_waits(self) -> int:
        """Total number of wait events."""
        return self.preemptive_waits + self.reactive_waits

    def to_dict(self) -> dict:
        """Convert to dict for API responses."""
        return {
            "total_requests": self.total_requests,
            "preemptive_waits": self.preemptive_waits,
            "reactive_waits": self.reactive_waits,
            "total_waits": self.total_waits,
            "total_wait_seconds": round(self.total_wait_seconds, 1),
            "last_wait_at": self.last_wait_at.isoformat() if self.last_wait_at else None,
            "last_wait_seconds": round(self.last_wait_seconds, 1),
            "is_rate_limited": self.is_rate_limited,
            "session_start": self.session_start.isoformat(),
        }


class RateLimiter:
    """Sliding window rate limiter with statistics tracking.

    Tracks all wait events for UI feedback. Never fails - always waits and continues.
    Premium API keys bypass rate limiting entirely.
    """

    # Cooldown duration when internal limit is hit (seconds)
    INTERNAL_COOLDOWN = 30.0

    def __init__(
        self,
        max_requests: int = 30,
        window_seconds: float = 60.0,
        is_premium: bool = False,
    ):
        self._max_requests = max_requests
        self._window = window_seconds
        self._is_premium = is_premium
        self._requests: deque[float] = deque()
        self._lock = threading.Lock()
        self._stats = RateLimitStats()

    @property
    def stats(self) -> RateLimitStats:
        """Get current rate limit statistics."""
        return self._stats

    def reset_stats(self) -> None:
        """Reset statistics (e.g., at start of new EPG generation)."""
        self._stats = RateLimitStats()

    def record_reactive_wait(self, wait_seconds: float, attempt: int, max_attempts: int) -> None:
        """Record a reactive wait (429 response from API)."""
        with self._lock:
            self._stats.reactive_waits += 1
            self._stats.total_wait_seconds += wait_seconds
            self._stats.last_wait_at = datetime.now()
            self._stats.last_wait_seconds = wait_seconds

    def acquire(self) -> None:
        """Block until a request slot is available. Never fails.

        Premium API keys skip rate limiting entirely.
        Free API keys wait 30 seconds when limit is reached.
        """
        # Premium keys bypass rate limiting
        if self._is_premium:
            with self._lock:
                self._stats.total_requests += 1
            return

        with self._lock:
            self._stats.total_requests += 1
            now = time.time()

            # Remove expired timestamps
            while self._requests and self._requests[0] < now - self._window:
                self._requests.popleft()

            # If at limit, wait with fixed cooldown
            if len(self._requests) >= self._max_requests:
                # Track the wait
                self._stats.preemptive_waits += 1
                self._stats.total_wait_seconds += self.INTERNAL_COOLDOWN
                self._stats.last_wait_at = datetime.now()
                self._stats.last_wait_seconds = self.INTERNAL_COOLDOWN

                logger.info(
                    f"TSDB free API limit reached ({self._max_requests}/min). "
                    f"Waiting {self.INTERNAL_COOLDOWN:.0f}s..."
                )

                # Release lock while sleeping so other threads can check stats
                self._lock.release()
                try:
                    time.sleep(self.INTERNAL_COOLDOWN)
                finally:
                    self._lock.acquire()

                logger.info("[TSDB] Cooldown complete, resuming API requests")

                # Clear the window after cooldown
                now = time.time()
                self._requests.clear()

            self._requests.append(time.time())


class TSDBClient:
    """Low-level TheSportsDB API client with rate limiting.

    API key resolution:
    1. Explicit api_key parameter (from database via factory)
    2. Free test key "123"

    Configure premium key in Settings UI.

    Free tier limitations:
    - 30 requests/minute
    - Team schedule (eventsnext.php) only shows HOME events
    - No livescores or highlights

    League mappings provided via LeagueMappingSource (no direct database access).
    """

    # Free test key
    FREE_API_KEY = "123"

    def __init__(
        self,
        league_mapping_source: LeagueMappingSource | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
        retry_count: int = 3,
        retry_delay: float = 1.0,
        requests_per_minute: int = 30,  # TSDB free tier limit
    ):
        self._league_mapping_source = league_mapping_source
        self._explicit_key = api_key
        self._timeout = timeout
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()
        self._requests_per_minute = requests_per_minute
        # Rate limiter initialized lazily after we can check is_premium
        self._rate_limiter: RateLimiter | None = None
        self._cache = TTLCache()

    @property
    def _api_key(self) -> str:
        """Resolve API key.

        Uses explicit parameter (from database via factory) or free key.
        Configure premium key in Settings UI.
        """
        if self._explicit_key:
            return self._explicit_key
        return self.FREE_API_KEY

    @property
    def is_premium(self) -> bool:
        """Check if using premium API key."""
        return self._api_key != self.FREE_API_KEY

    def _get_rate_limiter(self) -> RateLimiter:
        """Get or create rate limiter (lazy init to check is_premium)."""
        if self._rate_limiter is None:
            self._rate_limiter = RateLimiter(
                max_requests=self._requests_per_minute,
                window_seconds=60.0,
                is_premium=self.is_premium,
            )
            if self.is_premium:
                logger.info("[TSDB] Using premium API key - rate limiting disabled")
        return self._rate_limiter

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            with self._client_lock:
                # Double-check after acquiring lock
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=self._timeout,
                        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                    )
        return self._client

    # Exponential backoff for 429 responses
    # Starts at 5s, doubles each retry, caps at 120s
    BACKOFF_BASE = 5.0
    BACKOFF_MAX = 120.0
    BACKOFF_MAX_RETRIES = 5

    def _request(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make HTTP request with rate limiting and retry logic.

        Rate limiting strategy:
        1. Preemptive: Internal limit (30/min for free API) with 30s cooldown
        2. Reactive: If 429 received, exponential backoff (5s, 10s, 20s, 40s, 80s)

        Never fails due to rate limits - always waits and continues.
        All waits are tracked in rate_limit_stats() for UI feedback.
        """
        # Wait for rate limit slot (preemptive)
        rate_limiter = self._get_rate_limiter()
        rate_limiter.acquire()

        url = f"{TSDB_BASE_URL}/{self._api_key}/{endpoint}"
        backoff_attempt = 0

        for attempt in range(self._retry_count + self.BACKOFF_MAX_RETRIES):
            try:
                client = self._get_client()
                response = client.get(url, params=params)

                # Handle rate limit response (reactive) with exponential backoff
                if response.status_code == 429:
                    backoff_attempt += 1
                    if backoff_attempt > self.BACKOFF_MAX_RETRIES:
                        logger.warning(
                            f"TSDB 429 persisted after {self.BACKOFF_MAX_RETRIES} retries. "
                            "Check API key or try again later."
                        )
                        return None

                    # Exponential backoff: 5s, 10s, 20s, 40s, 80s (capped at 120s)
                    wait_seconds = min(
                        self.BACKOFF_BASE * (2 ** (backoff_attempt - 1)),
                        self.BACKOFF_MAX,
                    )
                    rate_limiter.record_reactive_wait(
                        wait_seconds, backoff_attempt, self.BACKOFF_MAX_RETRIES
                    )
                    logger.info(
                        f"TSDB 429 rate limit hit. Retry {backoff_attempt}/"
                        f"{self.BACKOFF_MAX_RETRIES} in {wait_seconds:.0f}s..."
                    )
                    time.sleep(wait_seconds)
                    logger.info(
                        f"TSDB backoff complete, retrying request "
                        f"(attempt {backoff_attempt + 1}/{self.BACKOFF_MAX_RETRIES + 1})"
                    )
                    continue

                response.raise_for_status()

                # Success after backoff - log recovery
                if backoff_attempt > 0:
                    logger.info(
                        f"TSDB request succeeded after {backoff_attempt} rate limit retry(ies)"
                    )

                return response.json()

            except httpx.HTTPStatusError as e:
                logger.warning("[TSDB] HTTP %d for %s", e.response.status_code, url)
                if attempt < self._retry_count - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                return None

            except (httpx.RequestError, RuntimeError, OSError) as e:
                # RuntimeError: "Cannot send a request, as the client has been closed"
                # OSError: "Bad file descriptor" from stale connections
                logger.warning("[TSDB] Request failed for %s: %s", url, e)
                # Don't reset client here - causes race conditions in parallel processing
                # httpx connection pool handles stale connections automatically
                if attempt < self._retry_count - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                return None

        return None

    def _reset_client(self) -> None:
        """Reset the HTTP client to clear stale connections."""
        with self._client_lock:
            if self._client:
                try:
                    self._client.close()
                except Exception as e:
                    logger.debug("[TSDB] Error closing HTTP client: %s", e)
                self._client = None

    def supports_league(self, league: str) -> bool:
        """Check if we have mapping for this league."""
        if not self._league_mapping_source:
            return False
        return self._league_mapping_source.supports_league(league, "tsdb")

    def get_league_id(self, league: str) -> str | None:
        """Get TSDB league ID (idLeague) for canonical league code.

        Used by: eventsnextleague.php, eventspastleague.php, eventsseason.php
        """
        if not self._league_mapping_source:
            return None
        mapping = self._league_mapping_source.get_mapping(league, "tsdb")
        return mapping.provider_league_id if mapping else None

    def get_league_name(self, league: str) -> str | None:
        """Get TSDB league name (strLeague) for canonical league code.

        Used by: eventsday.php (which takes league name, not ID)
        """
        if not self._league_mapping_source:
            return None
        mapping = self._league_mapping_source.get_mapping(league, "tsdb")
        return mapping.provider_league_name if mapping else None

    def get_sport(self, league: str) -> str:
        """Get sport name for a league."""
        if not self._league_mapping_source:
            return "Sports"
        mapping = self._league_mapping_source.get_mapping(league, "tsdb")
        return mapping.sport if mapping else "Sports"

    def get_events_by_date(self, league: str, date_str: str) -> dict | None:
        """Fetch events for a league on a specific date.

        Uses eventsday.php which takes league NAME (strLeague), not ID.
        Cache TTL is tiered based on date proximity:
        - Past: 7 days, Today: 30 min, Tomorrow: 4 hr, etc.

        Args:
            league: Canonical league code
            date_str: Date in YYYY-MM-DD format

        Returns:
            Raw TSDB response or None
        """
        cache_key = make_cache_key("tsdb", "eventsday", league, date_str)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[TSDB] Cache hit: %s", cache_key)
            return cached

        league_name = self.get_league_name(league)
        if not league_name:
            return None

        # eventsday.php uses 'l' for league NAME (strLeague), not ID
        result = self._request("eventsday.php", {"d": date_str, "l": league_name})
        if result:
            # Use tiered TTL based on date
            target_date = date.fromisoformat(date_str)
            ttl = get_cache_ttl_for_date(target_date)
            self._cache.set(cache_key, result, ttl)
            logger.debug("[TSDB] Cached %s for %dh %dm", cache_key, ttl // 3600, (ttl % 3600) // 60)
        return result

    def get_league_next_events(self, league: str) -> dict | None:
        """Fetch upcoming events for a league.

        Results cached for 1 hour.

        Args:
            league: Canonical league code

        Returns:
            Raw TSDB response or None
        """
        cache_key = make_cache_key("tsdb", "nextleague", league)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[TSDB] Cache hit: %s", cache_key)
            return cached

        league_id = self.get_league_id(league)
        if not league_id:
            return None

        result = self._request("eventsnextleague.php", {"id": league_id})
        if result:
            self._cache.set(cache_key, result, TSDB_CACHE_TTL_NEXT_EVENTS)
        return result

    def get_events_by_round(
        self, league: str, round_num: int = 1, season: str | None = None
    ) -> dict | None:
        """Fetch events for a specific round of a league season.

        Uses eventsround.php with league ID. This works for leagues where
        eventsday.php and eventsnextleague.php don't return data (e.g., Unrivaled).

        Args:
            league: Canonical league code
            round_num: Round number (default 1 for all events in some leagues)
            season: Season year (e.g., "2026"). Auto-detected if not provided.

        Returns:
            Raw TSDB response with {"events": [...]} or None
        """
        league_id = self.get_league_id(league)
        if not league_id:
            return None

        if not season:
            # Use current year for calendar-year leagues
            season = str(date.today().year)

        cache_key = make_cache_key("tsdb", "eventsround", league, round_num, season)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[TSDB] Cache hit: %s", cache_key)
            return cached

        result = self._request("eventsround.php", {"id": league_id, "r": round_num, "s": season})
        if result:
            # Cache for 2 hours (same as eventsday)
            self._cache.set(cache_key, result, 2 * 60 * 60)
        return result

    def get_team_next_events(self, team_id: str) -> dict | None:
        """Fetch upcoming events for a team.

        Note: Free tier only returns HOME events.

        Args:
            team_id: TSDB team ID

        Returns:
            Raw TSDB response or None
        """
        return self._request("eventsnext.php", {"id": team_id})

    def get_team_last_events(self, team_id: str) -> dict | None:
        """Fetch recent events for a team.

        Args:
            team_id: TSDB team ID

        Returns:
            Raw TSDB response or None
        """
        return self._request("eventslast.php", {"id": team_id})

    def get_team(self, team_id: str) -> dict | None:
        """Fetch team details.

        Note: lookupteam.php is broken on free tier (returns wrong team).
        This method still uses it for premium keys, but callers should
        prefer search_team() for free tier reliability.

        Args:
            team_id: TSDB team ID

        Returns:
            Raw TSDB response or None
        """
        return self._request("lookupteam.php", {"id": team_id})

    def get_event(self, event_id: str) -> dict | None:
        """Fetch event details.

        Args:
            event_id: TSDB event ID

        Returns:
            Raw TSDB response or None
        """
        return self._request("lookupevent.php", {"id": event_id})

    def search_team(self, team_name: str) -> dict | None:
        """Search for a team by name.

        Results cached for 24 hours.

        Args:
            team_name: Team name to search

        Returns:
            Raw TSDB response or None
        """
        cache_key = make_cache_key("tsdb", "searchteam", team_name.lower())
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[TSDB] Cache hit: %s", cache_key)
            return cached

        result = self._request("searchteams.php", {"t": team_name})
        if result:
            self._cache.set(cache_key, result, TSDB_CACHE_TTL_SEARCH)
        return result

    def get_season_events(self, league: str, season: str | None = None) -> dict | None:
        """Get all events for a league season.

        Uses eventsseason.php with league ID.
        Free tier returns 15 events per request.

        Args:
            league: Canonical league code
            season: Season string. Format varies by sport:
                    - Hockey: "2024-2025" (fall-spring)
                    - Cricket: "2024" (calendar year)
                    - Boxing: "2024" (calendar year)

        Returns:
            Raw TSDB response or None
        """
        league_id = self.get_league_id(league)
        if not league_id:
            return None

        if not season:
            from datetime import date

            year = date.today().year
            month = date.today().month
            sport = self.get_sport(league).lower()

            # Different sports use different season formats
            if sport in ("cricket", "boxing"):
                # Calendar year seasons
                season = str(year)
            else:
                # Fall-spring seasons (hockey, etc.)
                # Use previous year if before August
                if month < 8:
                    season = f"{year - 1}-{year}"
                else:
                    season = f"{year}-{year + 1}"

        return self._request("eventsseason.php", {"id": league_id, "s": season})

    def get_teams_in_league(self, league: str) -> dict | None:
        """Get all teams in a league.

        Uses a two-phase approach to work around free tier 10-team limit:
        1. search_all_teams.php - returns up to 10 teams with full details
        2. eventsseason.php - extract additional teams from scheduled games

        Results are merged and cached for 24 hours.

        Args:
            league: Canonical league code

        Returns:
            Dict with 'teams' list or None
        """
        cache_key = make_cache_key("tsdb", "teams", league)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[TSDB] Cache hit: %s", cache_key)
            return cached

        league_name = self.get_league_name(league)
        if not league_name:
            return None

        # Phase 1: Get teams from search_all_teams (capped at 10 on free tier)
        search_result = self._request("search_all_teams.php", {"l": league_name})
        teams_by_id: dict[str, dict] = {}

        if search_result and isinstance(search_result.get("teams"), list):
            for team in search_result["teams"]:
                team_id = str(team.get("idTeam", ""))
                if team_id:
                    teams_by_id[team_id] = team

        logger.debug("[TSDB] search_all_teams for %s: %d teams", league, len(teams_by_id))

        # Phase 2: Extract additional teams from season events
        # This works around the 10-team limit by finding teams in scheduled games
        season_result = self.get_season_events(league)
        if season_result and isinstance(season_result.get("events"), list):
            for event in season_result["events"]:
                # Extract home team
                home_id = str(event.get("idHomeTeam", ""))
                if home_id and home_id not in teams_by_id:
                    teams_by_id[home_id] = self._team_from_event(event, "Home", league)

                # Extract away team
                away_id = str(event.get("idAwayTeam", ""))
                if away_id and away_id not in teams_by_id:
                    teams_by_id[away_id] = self._team_from_event(event, "Away", league)

        search_teams = (search_result.get("teams") or []) if search_result else []
        season_events = (season_result.get("events") or []) if season_result else []
        logger.info(
            f"TSDB teams for {league}: {len(teams_by_id)} total "
            f"(search: {len(search_teams)}, events: {len(season_events)})"
        )

        result = {"teams": list(teams_by_id.values())}
        self._cache.set(cache_key, result, TSDB_CACHE_TTL_TEAMS)
        return result

    def _team_from_event(self, event: dict, prefix: str, league: str) -> dict:
        """Build minimal team dict from event data.

        Args:
            event: TSDB event dict
            prefix: "Home" or "Away"
            league: Canonical league code

        Returns:
            Team dict compatible with search_all_teams format
        """
        return {
            "idTeam": event.get(f"id{prefix}Team"),
            "strTeam": event.get(f"str{prefix}Team"),
            "strTeamShort": None,  # Not available in events
            "strLeague": league,
            "strSport": self.get_sport(league),
            "strBadge": event.get(f"str{prefix}TeamBadge"),
        }

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

    def rate_limit_stats(self) -> RateLimitStats:
        """Get rate limit statistics for UI feedback.

        Returns statistics about rate limiting this session:
        - total_requests: Number of API requests made
        - preemptive_waits: Times our limiter made us wait
        - reactive_waits: Times we hit 429 from API
        - total_wait_seconds: Total time spent waiting
        - is_rate_limited: True if any waits occurred

        Use .to_dict() on the result for JSON serialization.
        """
        return self._get_rate_limiter().stats

    def reset_rate_limit_stats(self) -> None:
        """Reset rate limit statistics.

        Call at the start of EPG generation to get clean stats for that run.
        """
        self._get_rate_limiter().reset_stats()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
