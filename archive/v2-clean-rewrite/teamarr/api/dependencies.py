"""FastAPI dependencies for dependency injection."""

from functools import lru_cache

from teamarr.services import SportsDataService, create_default_service


@lru_cache
def get_sports_service() -> SportsDataService:
    """Get singleton SportsDataService with providers from registry.

    Providers are configured in teamarr/providers/__init__.py.
    """
    return create_default_service()
