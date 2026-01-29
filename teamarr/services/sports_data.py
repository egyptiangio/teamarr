"""Sports data service layer.

Routes requests to appropriate providers with caching.
Consumers call this service - never providers directly.

Uses PersistentTTLCache for all caching:
- Fast in-memory operations (no SQLite during generation)
- Background flush to SQLite every 2 minutes
- Persists across restarts
- Call flush_cache() after EPG generation for immediate persistence
"""

import logging
import threading
from datetime import date

from teamarr.core import Event, SportsProvider, Team, TeamStats
from teamarr.database.provider_cache import (
    dict_to_event,
    dict_to_stats,
    dict_to_team,
    event_to_dict,
    stats_to_dict,
    team_to_dict,
)
from teamarr.providers import ProviderRegistry
from teamarr.utilities.cache import (
    CACHE_TTL_SCHEDULE,
    CACHE_TTL_SINGLE_EVENT,
    CACHE_TTL_TEAM_INFO,
    CACHE_TTL_TEAM_STATS,
    PersistentTTLCache,
    get_events_cache_ttl,
    make_cache_key,
)

logger = logging.getLogger(__name__)

# Singleton cache instance - shared across all SportsDataService instances
# This ensures one in-memory cache with background persistence
_shared_cache: PersistentTTLCache | None = None
_cache_lock = threading.Lock()


def _get_shared_cache() -> PersistentTTLCache:
    """Get or create the shared cache singleton."""
    global _shared_cache
    if _shared_cache is None:
        with _cache_lock:
            if _shared_cache is None:
                _shared_cache = PersistentTTLCache()
                logger.info("[CACHE] Initialized shared service cache")
    return _shared_cache


def flush_shared_cache() -> int:
    """Flush the shared cache to SQLite.

    Call after EPG generation for immediate persistence.
    Returns number of entries written.
    """
    if _shared_cache is not None:
        return _shared_cache.flush()
    return 0


def _ensure_registry_initialized() -> None:
    """Ensure ProviderRegistry is initialized with dependencies.

    Called automatically by create_default_service() to ensure providers
    have access to league mappings from the database.
    """
    if ProviderRegistry.is_initialized():
        return

    from teamarr.database import get_db
    from teamarr.services.league_mappings import init_league_mapping_service

    league_mapping_service = init_league_mapping_service(get_db)
    ProviderRegistry.initialize(league_mapping_service)
    logger.info("[STARTUP] Auto-initialized ProviderRegistry with league mappings")


def create_default_service() -> "SportsDataService":
    """Create SportsDataService with providers from registry.

    Providers are registered in teamarr/providers/__init__.py.
    Priority is determined by registration order and priority values.

    Automatically initializes ProviderRegistry if not already done
    (e.g., when called from CLI or scheduler outside FastAPI context).
    """
    # Ensure registry is initialized with database league mappings
    _ensure_registry_initialized()

    # Get all enabled providers from the registry, sorted by priority
    providers = ProviderRegistry.get_all()
    return SportsDataService(providers=providers)


class SportsDataService:
    """Service layer for sports data access.

    Provides a unified interface to sports data regardless of provider.
    Handles provider selection, fallback, and caching.

    Cache TTLs (optimized for hourly EPG regeneration):
    - Scoreboard (league events): 8 hours - daily schedule rarely changes
    - Team schedules: 8 hours - games rarely added/removed
    - Single event: 30 minutes - fresh scores/odds for current games
    - Team stats: 4 hours - record/standings change infrequently
    - Team info: 24 hours - static team data
    """

    def __init__(self, providers: list[SportsProvider] | None = None):
        self._providers: list[SportsProvider] = providers or []
        self._cache = _get_shared_cache()

    def add_provider(self, provider: SportsProvider) -> None:
        """Register a provider."""
        self._providers.append(provider)

    def get_events(self, league: str, target_date: date, cache_only: bool = False) -> list[Event]:
        """Get all events for a league on a given date.

        Args:
            league: League code
            target_date: Date to get events for
            cache_only: If True, only return cached events (no API calls).
                       Use for older dates where we don't want to fetch.

        Returns:
            List of events (may be empty if cache_only and not cached)
        """
        cache_key = make_cache_key("events", league, target_date.isoformat())

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return [dict_to_event(e) for e in cached]
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # If cache_only, don't fetch from API
        if cache_only:
            return []

        # Iterate through providers
        for provider in self._providers:
            if provider.supports_league(league):
                events = provider.get_events(league, target_date)
                ttl = get_events_cache_ttl(target_date)
                # Cache ALL results including empty lists to avoid repeated API calls
                # for leagues with no events on a given day
                self._cache.set(cache_key, [event_to_dict(e) for e in events], ttl)
                return events
        return []

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Get schedule for a team (past and future games)."""
        cache_key = make_cache_key("schedule", league, team_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return [dict_to_event(e) for e in cached]
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                events = provider.get_team_schedule(team_id, league, days_ahead)
                if events:
                    # Serialize to dict before caching
                    serialized = [event_to_dict(e) for e in events]
                    self._cache.set(cache_key, serialized, CACHE_TTL_SCHEDULE)
                    return events
        return []

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details."""
        cache_key = make_cache_key("team", league, team_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return dict_to_team(cached)
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                team = provider.get_team(team_id, league)
                if team:
                    # Serialize to dict before caching
                    self._cache.set(cache_key, team_to_dict(team), CACHE_TTL_TEAM_INFO)
                    return team
        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID.

        Uses shorter TTL (30min) since this is called for fresh scores/odds.
        """
        # Guard against empty event_id which would cause malformed API requests
        if not event_id:
            logger.warning(
                "[SPORTS_DATA] get_event called with empty event_id for league %s", league
            )
            return None

        cache_key = make_cache_key("event", league, event_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return dict_to_event(cached)
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        for provider in self._providers:
            if provider.supports_league(league):
                event = provider.get_event(event_id, league)
                if event:
                    # Serialize to dict before caching
                    self._cache.set(cache_key, event_to_dict(event), CACHE_TTL_SINGLE_EVENT)
                    return event
        return None

    def refresh_event_status(self, event: Event) -> Event:
        """Refresh event with fresh status from provider.

        Invalidates cache first, then fetches fresh data from summary endpoint.
        Used by filler generation for conditional descriptions (final vs not final).

        Args:
            event: Event with potentially stale status from schedule/scoreboard cache

        Returns:
            Event with refreshed status (or original if refresh fails)
        """
        if not event:
            return event

        # Invalidate cache to force fresh fetch from provider
        cache_key = make_cache_key("event", event.league, event.id)
        self._cache.delete(cache_key)

        # Fetch fresh event data from provider
        fresh_event = self.get_event(event.id, event.league)
        if fresh_event:
            logger.debug(
                "[REFRESH] event=%s status: %s → %s",
                event.id,
                event.status.state if event.status else "N/A",
                fresh_event.status.state if fresh_event.status else "N/A",
            )
            # Preserve segment_times from original (summary endpoint doesn't have them)
            if event.segment_times and not fresh_event.segment_times:
                fresh_event.segment_times = event.segment_times
            if event.main_card_start and not fresh_event.main_card_start:
                fresh_event.main_card_start = event.main_card_start
            return fresh_event

        # Return original if refresh fails
        logger.debug("[SPORTS_DATA] Could not refresh event %s, using cached status", event.id)
        return event

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get detailed team statistics."""
        cache_key = make_cache_key("stats", league, team_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return dict_to_stats(cached)
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                stats = provider.get_team_stats(team_id, league)
                if stats:
                    # Serialize to dict before caching
                    self._cache.set(cache_key, stats_to_dict(stats), CACHE_TTL_TEAM_STATS)
                    return stats
        return None

    # Cache management

    def get_provider_name(self, league: str) -> str | None:
        """Get the provider name that handles a league.

        Returns provider name (e.g., 'espn', 'tsdb') or None if no provider.
        """
        for provider in self._providers:
            if provider.supports_league(league):
                return provider.name
        return None

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

    def flush_cache(self) -> int:
        """Flush dirty cache entries to SQLite.

        Call after EPG generation for immediate persistence.
        Returns number of entries written.
        """
        return self._cache.flush()

    def invalidate_team(self, team_id: str, league: str) -> None:
        """Invalidate all cached data for a team."""
        self._cache.delete(make_cache_key("team", league, team_id))
        self._cache.delete(make_cache_key("stats", league, team_id))
        self._cache.delete(make_cache_key("schedule", league, team_id))

    def provider_stats(self) -> dict:
        """Get statistics from all providers for UI feedback.

        Returns a dict with provider-specific stats including:
        - Rate limit status (TSDB)
        - Cache statistics (if provider has internal cache)

        Example response:
        {
            "espn": {"name": "espn", "has_rate_limit": False},
            "tsdb": {
                "name": "tsdb",
                "has_rate_limit": True,
                "rate_limit": {
                    "total_requests": 10,
                    "is_rate_limited": True,
                    "total_wait_seconds": 45.2,
                    ...
                },
                "cache": {"total_entries": 5, ...}
            }
        }
        """
        stats = {}
        for provider in self._providers:
            provider_stats: dict = {"name": provider.name, "has_rate_limit": False}

            # Check for TSDB-specific stats
            if hasattr(provider, "_client"):
                client = provider._client
                if hasattr(client, "rate_limit_stats"):
                    provider_stats["has_rate_limit"] = True
                    provider_stats["rate_limit"] = client.rate_limit_stats().to_dict()
                if hasattr(client, "cache_stats"):
                    provider_stats["cache"] = client.cache_stats()

            stats[provider.name] = provider_stats

        return stats

    def reset_provider_stats(self) -> None:
        """Reset provider statistics (call at start of EPG generation).

        Resets rate limit counters so each generation has clean stats.
        """
        for provider in self._providers:
            if hasattr(provider, "_client"):
                client = provider._client
                if hasattr(client, "reset_rate_limit_stats"):
                    client.reset_rate_limit_stats()

    def prewarm_tsdb_leagues(self, leagues: list[str], days_ahead: int = 14) -> None:
        """Pre-warm TSDB events cache for multiple leagues.

        Fetches events for each league/day upfront, populating the cache.
        This ensures all subsequent get_team_schedule calls are cache hits.

        NOTE: Team name lookup uses seeded database cache (not API), so we
        only need to pre-warm events, not teams. This saves 2 API calls per league.

        Args:
            leagues: List of canonical league codes to pre-warm
            days_ahead: Number of days to pre-warm (default 14, matches get_team_schedule)
        """
        from datetime import timedelta

        if not leagues:
            return

        # Find TSDB provider
        tsdb_provider = None
        for provider in self._providers:
            if provider.name == "tsdb":
                tsdb_provider = provider
                break

        if not tsdb_provider:
            logger.debug("[PREWARM] No TSDB provider registered, skipping pre-warm")
            return

        unique_leagues = list(set(leagues))
        today = date.today()

        # Cap to TSDB's max days (same as provider)
        days_ahead = min(days_ahead, 14)

        total_calls = len(unique_leagues) * days_ahead  # N days per league
        logger.info(
            "[PREWARM] TSDB: %d leagues × %d days = ~%d API calls",
            len(unique_leagues),
            days_ahead,
            total_calls,
        )

        for league in unique_leagues:
            if not tsdb_provider.supports_league(league):
                continue

            # Pre-warm events cache for each day
            # Team names come from seeded database cache (no API needed)
            for i in range(days_ahead):
                target_date = today + timedelta(days=i)
                # Use get_events which goes through provider → client cache
                tsdb_provider.get_events(league, target_date)

            logger.debug("[PREWARM] TSDB league %s: %d days", league, days_ahead)
