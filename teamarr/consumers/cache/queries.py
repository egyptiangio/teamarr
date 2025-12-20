"""Cache query interface.

Read-only queries against the team and league cache.
"""

from collections.abc import Callable
from datetime import datetime

from teamarr.database import get_db

from .types import CacheStats, LeagueEntry


class TeamLeagueCache:
    """Query interface for team and league cache."""

    def __init__(self, db_factory: Callable = get_db) -> None:
        self._db = db_factory

    def find_candidate_leagues(
        self,
        team1: str,
        team2: str,
        sport: str | None = None,
    ) -> list[tuple[str, str]]:
        """Find leagues where both teams exist.

        Args:
            team1: First team name
            team2: Second team name
            sport: Optional filter by sport

        Returns:
            List of (league, provider) tuples where both teams exist
        """
        leagues1 = self._get_leagues_for_team(team1, sport)
        leagues2 = self._get_leagues_for_team(team2, sport)

        # Intersection - leagues where both exist
        return list(leagues1 & leagues2)

    def get_team_leagues(
        self,
        provider_team_id: str,
        provider: str,
        sport: str | None = None,
    ) -> list[str]:
        """Get all leagues a team plays in.

        Used for team-based multi-league schedule aggregation.

        IMPORTANT: ESPN team IDs are only unique within a sport, not globally.
        Team ID 6 in MLB is Detroit Tigers, but ID 6 in NHL is Edmonton Oilers.
        Always pass sport to get correct leagues.

        Args:
            provider_team_id: Team ID from the provider
            provider: Provider name ('espn' or 'tsdb')
            sport: Sport to filter by (REQUIRED for ESPN to avoid cross-sport collision)

        Returns:
            List of league slugs
        """
        with self._db() as conn:
            cursor = conn.cursor()
            if sport:
                cursor.execute(
                    """
                    SELECT DISTINCT league FROM team_cache
                    WHERE provider_team_id = ? AND provider = ? AND sport = ?
                    """,
                    (provider_team_id, provider, sport),
                )
            else:
                cursor.execute(
                    """
                    SELECT DISTINCT league FROM team_cache
                    WHERE provider_team_id = ? AND provider = ?
                    """,
                    (provider_team_id, provider),
                )
            return [row[0] for row in cursor.fetchall()]

    def get_all_leagues(
        self,
        sport: str | None = None,
        provider: str | None = None,
        import_enabled_only: bool = False,
    ) -> list[LeagueEntry]:
        """Get all available leagues (configured + discovered).

        For Team Importer: set import_enabled_only=True to show only
        explicitly configured leagues with import_enabled=1.

        For general use (event matching, etc.): returns UNION of:
        - Configured leagues (from `leagues` table) - preferred
        - Discovered leagues (from `league_cache`) - fallback

        Args:
            sport: Optional filter by sport (e.g., 'soccer')
            provider: Optional filter by provider
            import_enabled_only: If True, only return import-enabled leagues

        Returns:
            List of LeagueEntry objects
        """
        with self._db() as conn:
            cursor = conn.cursor()

            if import_enabled_only:
                # Team Importer: only configured leagues with import_enabled=1
                query = """
                    SELECT league_code as league_slug, provider,
                           display_name as league_name, sport, logo_url,
                           cached_team_count as team_count, import_enabled
                    FROM leagues
                    WHERE import_enabled = 1 AND enabled = 1
                """
                params: list = []

                if sport:
                    query += " AND sport = ?"
                    params.append(sport)
                if provider:
                    query += " AND provider = ?"
                    params.append(provider)

                query += " ORDER BY sport, display_name"
            else:
                # General use: UNION of configured + discovered leagues
                # Prefer configured leagues, fallback to discovered
                query = """
                    SELECT league_slug, provider, league_name, sport,
                           logo_url, team_count, import_enabled
                    FROM (
                        -- Configured leagues (preferred)
                        SELECT league_code as league_slug, provider,
                               display_name as league_name, sport, logo_url,
                               cached_team_count as team_count, import_enabled,
                               1 as priority
                        FROM leagues
                        WHERE enabled = 1

                        UNION ALL

                        -- Discovered leagues (fallback, exclude if already configured)
                        SELECT lc.league_slug, lc.provider,
                               lc.league_name, lc.sport, lc.logo_url,
                               lc.team_count, 0 as import_enabled,
                               2 as priority
                        FROM league_cache lc
                        WHERE NOT EXISTS (
                            SELECT 1 FROM leagues l
                            WHERE l.league_code = lc.league_slug
                        )
                    )
                    WHERE 1=1
                """
                params = []

                if sport:
                    query += " AND sport = ?"
                    params.append(sport)
                if provider:
                    query += " AND provider = ?"
                    params.append(provider)

                query += " ORDER BY priority, sport, league_name"

            cursor.execute(query, params)

            return [
                LeagueEntry(
                    league_slug=row[0],
                    provider=row[1],
                    league_name=row[2],
                    sport=row[3],
                    logo_url=row[4],
                    team_count=row[5] or 0,
                    import_enabled=bool(row[6]),
                )
                for row in cursor.fetchall()
            ]

    def get_league_info(self, league_slug: str) -> LeagueEntry | None:
        """Get metadata for a specific league.

        Checks configured leagues first, then falls back to discovered leagues.
        """
        with self._db() as conn:
            cursor = conn.cursor()

            # Check configured leagues first
            cursor.execute(
                """
                SELECT league_code, provider, display_name, sport, logo_url,
                       cached_team_count, import_enabled
                FROM leagues WHERE league_code = ?
                """,
                (league_slug,),
            )
            row = cursor.fetchone()
            if row:
                return LeagueEntry(
                    league_slug=row[0],
                    provider=row[1],
                    league_name=row[2],
                    sport=row[3],
                    logo_url=row[4],
                    team_count=row[5] or 0,
                    import_enabled=bool(row[6]),
                )

            # Fallback to discovered leagues
            cursor.execute(
                """
                SELECT league_slug, provider, league_name, sport, logo_url, team_count
                FROM league_cache WHERE league_slug = ?
                """,
                (league_slug,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            return LeagueEntry(
                league_slug=row[0],
                provider=row[1],
                league_name=row[2],
                sport=row[3],
                logo_url=row[4],
                team_count=row[5] or 0,
                import_enabled=False,  # Discovered leagues are not import-enabled
            )

    def get_team_id_for_league(
        self,
        team_name: str,
        league: str,
    ) -> tuple[str, str] | None:
        """Get provider team ID for a team in a specific league.

        Args:
            team_name: Team name to search
            league: League slug

        Returns:
            (provider_team_id, provider) tuple or None
        """
        with self._db() as conn:
            cursor = conn.cursor()
            team_lower = team_name.lower().strip()

            cursor.execute(
                """
                SELECT provider_team_id, provider FROM team_cache
                WHERE league = ?
                  AND (LOWER(team_name) LIKE ?
                       OR LOWER(team_abbrev) = ?
                       OR LOWER(team_short_name) LIKE ?)
                ORDER BY LENGTH(team_name) ASC
                LIMIT 1
                """,
                (league, f"%{team_lower}%", team_lower, f"%{team_lower}%"),
            )
            row = cursor.fetchone()
            return (row[0], row[1]) if row else None

    def get_cache_stats(self) -> CacheStats:
        """Get cache status and statistics."""
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cache_meta WHERE id = 1")
            row = cursor.fetchone()

            last_refresh = None
            is_stale = True

            if row and row[1]:  # last_full_refresh
                try:
                    last_refresh = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
                    days_old = (datetime.now(last_refresh.tzinfo) - last_refresh).days
                    is_stale = days_old > 7
                except (ValueError, TypeError):
                    pass

            return CacheStats(
                last_refresh=last_refresh,
                leagues_count=row[4] if row else 0,
                teams_count=row[5] if row else 0,
                refresh_duration_seconds=row[6] if row else 0,
                is_stale=is_stale,
                refresh_in_progress=bool(row[7]) if row else False,
                last_error=row[8] if row else None,
            )

    def is_cache_empty(self) -> bool:
        """Check if cache has any data."""
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM team_cache")
                row = cursor.fetchone()
                return row[0] == 0 if row else True
        except Exception:
            return True

    def _get_leagues_for_team(
        self,
        team_name: str,
        sport: str | None = None,
    ) -> set[tuple[str, str]]:
        """Get all leagues a team name could belong to.

        Returns set of (league, provider) tuples.
        """
        if not team_name:
            return set()

        team_lower = team_name.lower().strip()

        with self._db() as conn:
            cursor = conn.cursor()

            query = """
                SELECT DISTINCT league, provider FROM team_cache
                WHERE (LOWER(team_name) LIKE ?
                       OR LOWER(team_abbrev) = ?
                       OR LOWER(team_short_name) LIKE ?)
            """
            params: list = [f"%{team_lower}%", team_lower, f"%{team_lower}%"]

            if sport:
                query += " AND sport = ?"
                params.append(sport)

            cursor.execute(query, params)
            return {(row[0], row[1]) for row in cursor.fetchall()}
