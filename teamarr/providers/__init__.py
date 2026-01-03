"""Provider layer - sports data providers.

This is the SINGLE place where providers are configured and registered.
All other code discovers providers via ProviderRegistry.

Adding a new provider:
1. Create provider module (providers/newprovider/)
2. Register it here using ProviderRegistry.register()
3. Add league config to database (leagues table)

The rest of the system automatically discovers and uses registered providers.

Dependency Injection:
ProviderRegistry.initialize() must be called during app startup
to inject the LeagueMappingSource into providers.
"""

from teamarr.providers.cricbuzz import CricbuzzClient, CricbuzzProvider
from teamarr.providers.cricket_hybrid import CricketHybridProvider
from teamarr.providers.espn import ESPNClient, ESPNProvider
from teamarr.providers.hockeytech import HockeyTechClient, HockeyTechProvider
from teamarr.providers.registry import ProviderConfig, ProviderRegistry
from teamarr.providers.tsdb import RateLimitStats, TSDBClient, TSDBProvider

# =============================================================================
# PROVIDER FACTORY FUNCTIONS
# =============================================================================
# Factories inject dependencies from the registry at instantiation time.


def _create_espn_provider() -> ESPNProvider:
    """Factory for ESPN provider with injected dependencies."""
    return ESPNProvider(
        league_mapping_source=ProviderRegistry.get_league_mapping_source(),
    )


def _get_tsdb_api_key() -> str | None:
    """Fetch TSDB API key from database settings.

    This is the boundary where database access happens before
    passing to the provider layer (which should not access database).
    """
    try:
        from teamarr.database import get_db

        with get_db() as conn:
            cursor = conn.execute("SELECT tsdb_api_key FROM settings WHERE id = 1")
            row = cursor.fetchone()
            if row and row["tsdb_api_key"]:
                return row["tsdb_api_key"]
    except Exception:
        # Database not available or column doesn't exist yet - expected during startup
        # No logging here to avoid noise during initialization
        pass
    return None


def _create_tsdb_provider() -> TSDBProvider:
    """Factory for TSDB provider with injected dependencies."""
    return TSDBProvider(
        league_mapping_source=ProviderRegistry.get_league_mapping_source(),
        api_key=_get_tsdb_api_key(),
    )


def _create_hockeytech_provider() -> HockeyTechProvider:
    """Factory for HockeyTech provider with injected dependencies."""
    return HockeyTechProvider(
        league_mapping_source=ProviderRegistry.get_league_mapping_source(),
    )


def _create_cricbuzz_provider() -> CricbuzzProvider:
    """Factory for Cricbuzz provider with injected dependencies."""
    return CricbuzzProvider(
        league_mapping_source=ProviderRegistry.get_league_mapping_source(),
    )


def _create_cricket_hybrid_provider() -> CricketHybridProvider:
    """Factory for cricket hybrid provider.

    Combines TSDB (teams/logos from cache) with Cricbuzz (events/schedules).
    Used when TSDB API key is free tier (not premium).
    """
    from teamarr.database import get_db

    return CricketHybridProvider(
        cricbuzz_provider=_create_cricbuzz_provider(),
        league_mapping_source=ProviderRegistry.get_league_mapping_source(),
        db_factory=get_db,
    )


# =============================================================================
# PROVIDER REGISTRATION
# =============================================================================
# This is the ONLY place providers need to be added.
# Priority: Lower = higher priority (tried first for matching leagues)

ProviderRegistry.register(
    name="espn",
    provider_class=ESPNProvider,
    factory=_create_espn_provider,
    priority=0,  # Primary provider
    enabled=True,
)

ProviderRegistry.register(
    name="hockeytech",
    provider_class=HockeyTechProvider,
    factory=_create_hockeytech_provider,
    priority=50,  # CHL leagues (OHL, WHL, QMJHL) + AHL, PWHL, USHL
    enabled=True,
)

ProviderRegistry.register(
    name="cricbuzz",
    provider_class=CricbuzzProvider,
    factory=_create_cricbuzz_provider,
    priority=60,  # Cricket - direct Cricbuzz (rarely used standalone)
    enabled=False,  # Disabled - use cricket_hybrid instead
)

ProviderRegistry.register(
    name="cricket_hybrid",
    provider_class=CricketHybridProvider,
    factory=_create_cricket_hybrid_provider,
    priority=55,  # Cricket leagues - TSDB teams + Cricbuzz events
    enabled=True,
)

ProviderRegistry.register(
    name="tsdb",
    provider_class=TSDBProvider,
    factory=_create_tsdb_provider,
    priority=100,  # Fallback provider for boxing, etc.
    enabled=True,
)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Registry
    "ProviderConfig",
    "ProviderRegistry",
    # ESPN
    "ESPNClient",
    "ESPNProvider",
    # HockeyTech
    "HockeyTechClient",
    "HockeyTechProvider",
    # Cricket
    "CricbuzzClient",
    "CricbuzzProvider",
    "CricketHybridProvider",
    # TheSportsDB
    "RateLimitStats",
    "TSDBClient",
    "TSDBProvider",
]
