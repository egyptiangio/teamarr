"""Service layer for sports data access.

Consumers call services - never providers directly.
Services handle routing, caching, and provider selection.
"""

from services.sports_data import SportsDataService, create_default_service

__all__ = [
    "SportsDataService",
    "create_default_service",
]
