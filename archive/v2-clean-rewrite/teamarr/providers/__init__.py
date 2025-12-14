"""Provider layer - sports data providers.

This is the SINGLE place where providers are configured and registered.
All other code discovers providers via ProviderRegistry.

Adding a new provider:
1. Create provider module (providers/newprovider/)
2. Register it here using ProviderRegistry.register()
3. Add league mappings to database (league_provider_mappings table)

The rest of the system automatically discovers and uses registered providers.
"""

from teamarr.providers.espn import ESPNClient, ESPNProvider
from teamarr.providers.registry import ProviderConfig, ProviderRegistry
from teamarr.providers.tsdb import RateLimitStats, TSDBClient, TSDBProvider

# =============================================================================
# PROVIDER REGISTRATION
# =============================================================================
# This is the ONLY place providers need to be added.
# Priority: Lower = higher priority (tried first for matching leagues)

ProviderRegistry.register(
    name="espn",
    provider_class=ESPNProvider,
    priority=0,  # Primary provider
    enabled=True,
)

ProviderRegistry.register(
    name="tsdb",
    provider_class=TSDBProvider,
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
