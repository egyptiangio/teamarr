"""League mapping service.

Provides database-backed implementation of LeagueMappingSource.
Providers depend on this service, not the database directly.
"""

from collections.abc import Callable, Generator
from sqlite3 import Connection

from teamarr.core import LeagueMapping


class LeagueMappingService:
    """Database-backed league mapping source.

    Implements the LeagueMappingSource protocol defined in core.
    Providers receive an instance of this service at construction time.
    """

    def __init__(
        self,
        db_getter: Callable[[], Generator[Connection, None, None]],
    ):
        self._db_getter = db_getter

    def get_mapping(self, league_code: str, provider: str) -> LeagueMapping | None:
        """Get mapping for a specific league and provider."""
        with self._db_getter() as conn:
            cursor = conn.execute(
                """
                SELECT league_code, provider, provider_league_id,
                       provider_league_name, sport, display_name, logo_url
                FROM leagues
                WHERE league_code = ? AND provider = ? AND enabled = 1
                """,
                (league_code.lower(), provider),
            )
            row = cursor.fetchone()
            if not row:
                return None

            return LeagueMapping(
                league_code=row["league_code"],
                provider=row["provider"],
                provider_league_id=row["provider_league_id"],
                provider_league_name=row["provider_league_name"],
                sport=row["sport"],
                display_name=row["display_name"],
                logo_url=row["logo_url"],
            )

    def supports_league(self, league_code: str, provider: str) -> bool:
        """Check if provider supports the given league."""
        with self._db_getter() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM leagues
                WHERE league_code = ? AND provider = ? AND enabled = 1
                """,
                (league_code.lower(), provider),
            )
            return cursor.fetchone() is not None

    def get_leagues_for_provider(self, provider: str) -> list[LeagueMapping]:
        """Get all leagues supported by a provider."""
        with self._db_getter() as conn:
            cursor = conn.execute(
                """
                SELECT league_code, provider, provider_league_id,
                       provider_league_name, sport, display_name, logo_url
                FROM leagues
                WHERE provider = ? AND enabled = 1
                ORDER BY league_code
                """,
                (provider,),
            )
            return [
                LeagueMapping(
                    league_code=row["league_code"],
                    provider=row["provider"],
                    provider_league_id=row["provider_league_id"],
                    provider_league_name=row["provider_league_name"],
                    sport=row["sport"],
                    display_name=row["display_name"],
                    logo_url=row["logo_url"],
                )
                for row in cursor.fetchall()
            ]


# Singleton instance - initialized by app startup
_league_mapping_service: LeagueMappingService | None = None


def init_league_mapping_service(
    db_getter: Callable[[], Generator[Connection, None, None]],
) -> LeagueMappingService:
    """Initialize the global league mapping service.

    Called during app startup after database is ready.
    """
    global _league_mapping_service
    _league_mapping_service = LeagueMappingService(db_getter)
    return _league_mapping_service


def get_league_mapping_service() -> LeagueMappingService:
    """Get the global league mapping service.

    Raises RuntimeError if not initialized.
    """
    if _league_mapping_service is None:
        raise RuntimeError(
            "LeagueMappingService not initialized. Call init_league_mapping_service() first."
        )
    return _league_mapping_service
