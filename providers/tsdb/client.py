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

League mappings are stored in the database (league_provider_mappings table).
"""

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import date, datetime
from sqlite3 import Connection

import httpx

from database import get_league_mapping, provider_supports_league
from utilities.cache import TTLCache, make_cache_key

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
    """

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0):
        self._max_requests = max_requests
        self._window = window_seconds
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

    def record_reactive_wait(self, wait_seconds: float) -> None:
        """Record a reactive wait (429 response from API).

        Called by the client when it receives a 429 and has to wait.
        """
        with self._lock:
            self._stats.reactive_waits += 1
            self._stats.total_wait_seconds += wait_seconds
            self._stats.last_wait_at = datetime.now()
            self._stats.last_wait_seconds = wait_seconds
            logger.info(
                f"TSDB rate limit hit (429). Waiting {wait_seconds:.0f}s. "
                f"Total waits this session: {self._stats.total_waits}"
            )

    def acquire(self) -> None:
        """Block until a request slot is available. Never fails."""
        with self._lock:
            self._stats.total_requests += 1
            now = time.time()

            # Remove expired timestamps
            while self._requests and self._requests[0] < now - self._window:
                self._requests.popleft()

            # If at limit, wait (preemptive rate limiting)
            if len(self._requests) >= self._max_requests:
                wait_time = self._requests[0] + self._window - now
                if wait_time > 0:
                    # Track the wait
                    self._stats.preemptive_waits += 1
                    self._stats.total_wait_seconds += wait_time
                    self._stats.last_wait_at = datetime.now()
                    self._stats.last_wait_seconds = wait_time

                    logger.info(
                        f"TSDB rate limit approaching. Waiting {wait_time:.1f}s. "
                        f"Total waits this session: {self._stats.total_waits}"
                    )

                    # Release lock while sleeping so other threads can check stats
                    self._lock.release()
                    try:
                        time.sleep(wait_time)
                    finally:
                        self._lock.acquire()

                    # Clean up again after wait
                    now = time.time()
                    while self._requests and self._requests[0] < now - self._window:
                        self._requests.popleft()

            self._requests.append(time.time())


class TSDBClient:
    """Low-level TheSportsDB API client with rate limiting.

    API key resolution order:
    1. Explicit api_key parameter
    2. TSDB_API_KEY environment variable
    3. Free test key "123"

    Free tier limitations:
    - 30 requests/minute
    - Team schedule (eventsnext.php) only shows HOME events
    - No livescores or highlights

    League mappings come from the database (league_provider_mappings table).
    """

    # Free test key
    FREE_API_KEY = "123"

    def __init__(
        self,
        db_getter: Callable[[], Generator[Connection, None, None]],
        api_key: str | None = None,
        timeout: float = 10.0,
        retry_count: int = 3,
        retry_delay: float = 1.0,
        requests_per_minute: int = 25,  # Leave headroom below 30 limit
    ):
        self._db_getter = db_getter
        self._explicit_key = api_key
        self._timeout = timeout
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._client: httpx.Client | None = None
        self._rate_limiter = RateLimiter(requests_per_minute, 60.0)
        self._cache = TTLCache()

    @property
    def _api_key(self) -> str:
        """Resolve API key from available sources."""
        # 1. Explicit parameter
        if self._explicit_key:
            return self._explicit_key

        # 2. Environment variable
        env_key = os.getenv("TSDB_API_KEY")
        if env_key:
            return env_key

        # 3. Fall back to free key
        return self.FREE_API_KEY

    @property
    def is_premium(self) -> bool:
        """Check if using premium API key."""
        return self._api_key != self.FREE_API_KEY

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    def _request(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make HTTP request with rate limiting and retry logic.

        Never fails due to rate limits - always waits and continues.
        All waits are tracked in rate_limit_stats() for UI feedback.
        """
        # Wait for rate limit slot (preemptive)
        self._rate_limiter.acquire()

        url = f"{TSDB_BASE_URL}/{self._api_key}/{endpoint}"
        client = self._get_client()

        for attempt in range(self._retry_count):
            try:
                response = client.get(url, params=params)

                # Handle rate limit response (reactive)
                if response.status_code == 429:
                    wait_seconds = 60.0
                    self._rate_limiter.record_reactive_wait(wait_seconds)
                    time.sleep(wait_seconds)
                    continue

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

    def supports_league(self, league: str) -> bool:
        """Check if we have mapping for this league in DB."""
        with self._db_getter() as conn:
            return provider_supports_league(conn, league, "tsdb")

    def get_league_id(self, league: str) -> str | None:
        """Get TSDB league ID (idLeague) for canonical league code.

        Used by: eventsnextleague.php, eventspastleague.php, eventsseason.php
        """
        with self._db_getter() as conn:
            mapping = get_league_mapping(conn, league, "tsdb")
            return mapping.provider_league_id if mapping else None

    def get_league_name(self, league: str) -> str | None:
        """Get TSDB league name (strLeague) for canonical league code.

        Used by: eventsday.php (which takes league name, not ID)
        """
        with self._db_getter() as conn:
            mapping = get_league_mapping(conn, league, "tsdb")
            return mapping.provider_league_name if mapping else None

    def get_sport(self, league: str) -> str:
        """Get sport name for a league from DB."""
        with self._db_getter() as conn:
            mapping = get_league_mapping(conn, league, "tsdb")
            return mapping.sport if mapping else "Sports"

    def get_events_by_date(
        self, league: str, date_str: str
    ) -> dict | None:
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
            logger.debug(f"TSDB cache hit: {cache_key}")
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
            logger.debug(f"TSDB cached {cache_key} for {ttl // 3600}h {(ttl % 3600) // 60}m")
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
            logger.debug(f"TSDB cache hit: {cache_key}")
            return cached

        league_id = self.get_league_id(league)
        if not league_id:
            return None

        result = self._request("eventsnextleague.php", {"id": league_id})
        if result:
            self._cache.set(cache_key, result, TSDB_CACHE_TTL_NEXT_EVENTS)
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
            logger.debug(f"TSDB cache hit: {cache_key}")
            return cached

        result = self._request("searchteams.php", {"t": team_name})
        if result:
            self._cache.set(cache_key, result, TSDB_CACHE_TTL_SEARCH)
        return result

    def get_teams_in_league(self, league: str) -> dict | None:
        """Get all teams in a league.

        Uses search_all_teams.php with league NAME (not lookup_all_teams.php
        with league ID, which is broken on free tier).
        Results cached for 24 hours.

        Args:
            league: Canonical league code

        Returns:
            Raw TSDB response or None
        """
        cache_key = make_cache_key("tsdb", "teams", league)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"TSDB cache hit: {cache_key}")
            return cached

        league_name = self.get_league_name(league)
        if not league_name:
            return None

        # search_all_teams.php uses 'l' for league NAME (strLeague)
        result = self._request("search_all_teams.php", {"l": league_name})
        if result:
            self._cache.set(cache_key, result, TSDB_CACHE_TTL_TEAMS)
        return result

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
        return self._rate_limiter.stats

    def reset_rate_limit_stats(self) -> None:
        """Reset rate limit statistics.

        Call at the start of EPG generation to get clean stats for that run.
        """
        self._rate_limiter.reset_stats()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
