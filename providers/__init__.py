"""Provider registration - single source of truth for all data providers.

All provider configuration happens here. The rest of the system
(SportsDataService, etc.) uses ProviderRegistry to discover providers.

Adding a new provider:
1. Create provider module (providers/newprovider/)
2. Register it below using ProviderRegistry.register()
3. Add league mappings to database (league_provider_mappings table)
"""

from providers.registry import ProviderConfig, ProviderRegistry

# Import providers for registration
from providers.espn import ESPNProvider
from providers.tsdb import TSDBProvider

# Register ESPN as primary provider (priority 0 = tried first)
ProviderRegistry.register(
    name="espn",
    provider_class=ESPNProvider,
    priority=0,  # Primary provider
    enabled=True,
)

# Register TheSportsDB as fallback (priority 100 = tried after ESPN)
ProviderRegistry.register(
    name="tsdb",
    provider_class=TSDBProvider,
    priority=100,  # Fallback provider
    enabled=True,
)

__all__ = [
    "ProviderConfig",
    "ProviderRegistry",
    "ESPNProvider",
    "TSDBProvider",
]
