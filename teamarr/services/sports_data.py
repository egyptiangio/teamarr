"""Sports data service layer.

Routes requests to appropriate providers with caching.
Consumers call this service - never providers directly.
"""

import logging
from datetime import date

from teamarr.core import Event, SportsProvider, Team, TeamStats
from teamarr.providers import ProviderRegistry
from teamarr.utilities.cache import (
    CACHE_TTL_SCHEDULE,
    CACHE_TTL_SINGLE_EVENT,
    CACHE_TTL_TEAM_INFO,
    CACHE_TTL_TEAM_STATS,
    TTLCache,
    get_events_cache_ttl,
    make_cache_key,
)

logger = logging.getLogger(__name__)


def create_default_service() -> "SportsDataService":
    """Create SportsDataService with providers from registry.

    Providers are registered in teamarr/providers/__init__.py.
    Priority is determined by registration order and priority values.
    """
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
        self._cache = TTLCache()

    def add_provider(self, provider: SportsProvider) -> None:
        """Register a provider."""
        self._providers.append(provider)

    def get_events(self, league: str, target_date: date) -> list[Event]:
        """Get all events for a league on a given date."""
        cache_key = make_cache_key("events", league, target_date.isoformat())

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return cached

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                events = provider.get_events(league, target_date)
                if events:
                    ttl = get_events_cache_ttl(target_date)
                    self._cache.set(cache_key, events, ttl)
                    return events
        return []

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Get upcoming schedule for a team."""
        cache_key = make_cache_key("schedule", league, team_id)

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return cached

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                events = provider.get_team_schedule(team_id, league, days_ahead)
                if events:
                    self._cache.set(cache_key, events, CACHE_TTL_SCHEDULE)
                    return events
        return []

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details."""
        cache_key = make_cache_key("team", league, team_id)

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return cached

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                team = provider.get_team(team_id, league)
                if team:
                    self._cache.set(cache_key, team, CACHE_TTL_TEAM_INFO)
                    return team
        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID.

        Uses shorter TTL (30min) since this is called for fresh scores/odds.
        """
        cache_key = make_cache_key("event", league, event_id)

        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return cached

        for provider in self._providers:
            if provider.supports_league(league):
                event = provider.get_event(event_id, league)
                if event:
                    self._cache.set(cache_key, event, CACHE_TTL_SINGLE_EVENT)
                    return event
        return None

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get detailed team statistics."""
        cache_key = make_cache_key("stats", league, team_id)

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return cached

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                stats = provider.get_team_stats(team_id, league)
                if stats:
                    self._cache.set(cache_key, stats, CACHE_TTL_TEAM_STATS)
                    return stats
        return None

    # Cache management

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

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
