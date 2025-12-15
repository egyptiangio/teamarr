"""Provider registry - single source of truth for data providers.

All provider configuration happens here. Adding a new provider:
1. Create provider module (providers/newprovider/)
2. Register it in providers/__init__.py using ProviderRegistry.register()
3. Add league mappings to database (league_provider_mappings table)

The rest of the system (SportsDataService, CacheRefresher, etc.)
automatically discovers and uses registered providers.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from teamarr.core import SportsProvider

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for a registered provider."""

    name: str
    provider_class: type
    factory: Callable[[], "SportsProvider"] | None = None
    config: dict = field(default_factory=dict)
    enabled: bool = True
    priority: int = 0  # Lower = higher priority (tried first)

    # Lazy instance
    _instance: "SportsProvider | None" = field(default=None, repr=False)

    def get_instance(self) -> "SportsProvider":
        """Get or create provider instance."""
        if self._instance is None:
            if self.factory:
                self._instance = self.factory()
            else:
                self._instance = self.provider_class(**self.config)
        return self._instance

    def reset_instance(self) -> None:
        """Reset cached instance (for testing)."""
        self._instance = None


class ProviderRegistry:
    """Central registry for all data providers.

    This is the SINGLE place where providers are configured.
    All other parts of the system use this registry to discover providers.

    Usage:
        # Registration (in providers/__init__.py)
        ProviderRegistry.register(
            name="espn",
            provider_class=ESPNProvider,
            priority=0,  # Primary provider
        )

        # Discovery (in services, consumers, etc.)
        for provider in ProviderRegistry.get_all():
            if provider.supports_league(league):
                return provider.get_events(league, date)
    """

    _providers: dict[str, ProviderConfig] = {}
    _initialized: bool = False

    @classmethod
    def register(
        cls,
        name: str,
        provider_class: type,
        *,
        factory: Callable[[], "SportsProvider"] | None = None,
        config: dict | None = None,
        enabled: bool = True,
        priority: int = 100,
    ) -> None:
        """Register a provider.

        Args:
            name: Unique provider identifier (e.g., 'espn', 'tsdb')
            provider_class: Provider class (must implement SportsProvider)
            factory: Optional factory function to create instance
            config: Optional config dict passed to constructor
            enabled: Whether provider is active
            priority: Lower = higher priority (tried first)
        """
        if name in cls._providers:
            logger.warning(f"Provider '{name}' already registered, overwriting")

        cls._providers[name] = ProviderConfig(
            name=name,
            provider_class=provider_class,
            factory=factory,
            config=config or {},
            enabled=enabled,
            priority=priority,
        )
        logger.debug(f"Registered provider: {name} (priority={priority})")

    @classmethod
    def get(cls, name: str) -> "SportsProvider | None":
        """Get a specific provider by name."""
        config = cls._providers.get(name)
        if config and config.enabled:
            return config.get_instance()
        return None

    @classmethod
    def get_all(cls) -> list["SportsProvider"]:
        """Get all enabled providers, sorted by priority."""
        configs = sorted(
            (c for c in cls._providers.values() if c.enabled),
            key=lambda c: c.priority,
        )
        return [c.get_instance() for c in configs]

    @classmethod
    def get_for_league(cls, league: str) -> "SportsProvider | None":
        """Get the first provider that supports a league."""
        for provider in cls.get_all():
            if provider.supports_league(league):
                return provider
        return None

    @classmethod
    def get_all_configs(cls) -> list[ProviderConfig]:
        """Get all provider configs (for debugging/status)."""
        return list(cls._providers.values())

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if a provider is registered."""
        return name in cls._providers

    @classmethod
    def unregister(cls, name: str) -> bool:
        """Unregister a provider (mainly for testing)."""
        if name in cls._providers:
            del cls._providers[name]
            return True
        return False

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations (for testing)."""
        cls._providers.clear()
        cls._initialized = False

    @classmethod
    def reset_instances(cls) -> None:
        """Reset all cached instances (for testing)."""
        for config in cls._providers.values():
            config.reset_instance()

    @classmethod
    def provider_names(cls) -> list[str]:
        """Get list of registered provider names."""
        return list(cls._providers.keys())

    @classmethod
    def enabled_provider_names(cls) -> list[str]:
        """Get list of enabled provider names."""
        return [name for name, config in cls._providers.items() if config.enabled]
