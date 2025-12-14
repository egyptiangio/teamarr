"""TheSportsDB sports data provider."""

from providers.tsdb.client import TSDBClient
from providers.tsdb.provider import TSDBProvider

__all__ = ["TSDBClient", "TSDBProvider"]
