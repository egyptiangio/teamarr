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

from teamarr.providers.espn import ESPNClient, ESPNProvider
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


def _create_tsdb_provider() -> TSDBProvider:
    """Factory for TSDB provider with injected dependencies."""
    return TSDBProvider(
        league_mapping_source=ProviderRegistry.get_league_mapping_source(),
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
    name="tsdb",
    provider_class=TSDBProvider,
    factory=_create_tsdb_provider,
    priority=100,  # Fallback provider
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
    # TheSportsDB
    "RateLimitStats",
    "TSDBClient",
    "TSDBProvider",
]
