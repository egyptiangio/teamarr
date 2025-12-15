"""Unified team and league cache.

Provides reverse-lookup for:
1. Event matching: "Freiburg vs Stuttgart" → candidate leagues
2. Team multi-league: Liverpool → [eng.1, uefa.champions, eng.fa, ...]
3. League discovery: all soccer leagues for "soccer_all"

Consolidates the separate V1 caches:
- team_league_cache (non-soccer sports)
- soccer_team_leagues / soccer_leagues_cache (soccer)

Uses ProviderRegistry to discover and refresh from all providers.
Refresh weekly to handle promotion/relegation.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

from database import get_connection

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class CacheStats:
    """Cache refresh statistics."""

    last_refresh: datetime | None
    leagues_count: int
    teams_count: int
    refresh_duration_seconds: float
    is_stale: bool
    refresh_in_progress: bool
    last_error: str | None


@dataclass
class TeamEntry:
    """A team entry from the cache."""

    team_name: str
    team_abbrev: str | None
    team_short_name: str | None
    provider: str
    provider_team_id: str
    league: str
    sport: str
    logo_url: str | None = None


@dataclass
class LeagueEntry:
    """A league entry from the cache."""

    league_slug: str
    provider: str
    league_name: str | None
    sport: str
    logo_url: str | None
    team_count: int
    tags: list[str] | None = None


# =============================================================================
# CACHE QUERIES
# =============================================================================


class TeamLeagueCache:
    """Query interface for team and league cache.

    Provides a unified interface over the V1 separate tables:
    - team_league_cache (non-soccer)
    - soccer_team_leagues (soccer)
    """

    def __init__(self, db_factory: Callable = get_connection):
        self._db = db_factory

    def find_candidate_leagues(
        self,
        team1: str,
        team2: str,
        sport: str | None = None,
        enabled_leagues: list[str] | None = None,
    ) -> list[str]:
        """Find leagues where both teams exist.

        Args:
            team1: First team name
            team2: Second team name
            sport: Optional filter by sport
            enabled_leagues: Optional filter - only return these leagues

        Returns:
            List of league codes where both teams exist
        """
        leagues1 = self._get_leagues_for_team(team1, sport)
        leagues2 = self._get_leagues_for_team(team2, sport)

        # Intersection - leagues where both exist
        candidates = leagues1 & leagues2

        # Filter by enabled leagues if specified
        if enabled_leagues:
            enabled_set = set(enabled_leagues)
            candidates = candidates & enabled_set

        return list(candidates)

    def get_team_leagues(
        self,
        provider_team_id: str,
        provider: str = "espn",
    ) -> list[str]:
        """Get all leagues a team plays in.

        Used for team-based multi-league schedule aggregation.

        Args:
            provider_team_id: Team ID from the provider
            provider: Provider name ('espn' or 'tsdb')

        Returns:
            List of league slugs
        """
        conn = self._db()
        try:
            cursor = conn.cursor()
            leagues = set()

            # Query non-soccer cache
            cursor.execute(
                """
                SELECT DISTINCT league_code FROM team_league_cache
                WHERE espn_team_id = ?
                """,
                (provider_team_id,),
            )
            leagues.update(row[0] for row in cursor.fetchall())

            # Query soccer cache
            cursor.execute(
                """
                SELECT DISTINCT league_slug FROM soccer_team_leagues
                WHERE espn_team_id = ?
                """,
                (provider_team_id,),
            )
            leagues.update(row[0] for row in cursor.fetchall())

            return sorted(leagues)
        finally:
            conn.close()

    def get_leagues_for_team(self, team_name: str) -> set[str]:
        """Get all leagues a team name could belong to.

        Public wrapper for _get_leagues_for_team.
        """
        return self._get_leagues_for_team(team_name)

    def get_all_leagues(
        self,
        sport: str | None = None,
        provider: str | None = None,
    ) -> list[LeagueEntry]:
        """Get all cached leagues.

        Args:
            sport: Optional filter by sport (e.g., 'soccer')
            provider: Optional filter by provider

        Returns:
            List of LeagueEntry objects
        """
        conn = self._db()
        try:
            cursor = conn.cursor()
            leagues = []

            # Query soccer leagues cache
            if sport is None or sport == "soccer":
                cursor.execute(
                    """
                    SELECT league_slug, league_name, league_abbrev,
                           league_tags, league_logo_url, team_count
                    FROM soccer_leagues_cache
                    """
                )
                for row in cursor.fetchall():
                    tags = self._parse_tags(row[3])
                    leagues.append(
                        LeagueEntry(
                            league_slug=row[0],
                            provider="espn",
                            league_name=row[1],
                            sport="soccer",
                            logo_url=row[4],
                            team_count=row[5] or 0,
                            tags=tags,
                        )
                    )

            # Query non-soccer leagues from team_league_cache
            if sport != "soccer":
                query = """
                    SELECT DISTINCT league_code, sport, COUNT(*) as team_count
                    FROM team_league_cache
                """
                params = []
                if sport:
                    query += " WHERE sport = ?"
                    params.append(sport)
                query += " GROUP BY league_code, sport"

                cursor.execute(query, params)
                for row in cursor.fetchall():
                    leagues.append(
                        LeagueEntry(
                            league_slug=row[0],
                            provider="espn",
                            league_name=None,  # Not stored in V1 schema
                            sport=row[1],
                            logo_url=None,
                            team_count=row[2],
                            tags=None,
                        )
                    )

            return leagues
        finally:
            conn.close()

    def get_league_info(self, league_slug: str) -> LeagueEntry | None:
        """Get metadata for a specific league."""
        conn = self._db()
        try:
            cursor = conn.cursor()

            # Try soccer leagues cache first
            cursor.execute(
                """
                SELECT league_slug, league_name, league_abbrev,
                       league_tags, league_logo_url, team_count
                FROM soccer_leagues_cache WHERE league_slug = ?
                """,
                (league_slug,),
            )
            row = cursor.fetchone()
            if row:
                tags = self._parse_tags(row[3])
                return LeagueEntry(
                    league_slug=row[0],
                    provider="espn",
                    league_name=row[1],
                    sport="soccer",
                    logo_url=row[4],
                    team_count=row[5] or 0,
                    tags=tags,
                )

            # Try non-soccer cache
            cursor.execute(
                """
                SELECT league_code, sport, COUNT(*) as team_count
                FROM team_league_cache
                WHERE league_code = ?
                GROUP BY league_code, sport
                """,
                (league_slug,),
            )
            row = cursor.fetchone()
            if row:
                return LeagueEntry(
                    league_slug=row[0],
                    provider="espn",
                    league_name=None,
                    sport=row[1],
                    logo_url=None,
                    team_count=row[2],
                    tags=None,
                )

            return None
        finally:
            conn.close()

    def get_team_id_for_league(
        self,
        team_name: str,
        league: str,
    ) -> str | None:
        """Get provider team ID for a team in a specific league.

        Uses tiered matching:
        1. Direct match with abbreviation variants
        2. Accent-normalized match
        3. Number-stripped match
        4. Article-stripped match

        Args:
            team_name: Team name to search
            league: League slug

        Returns:
            Provider team ID if found, None otherwise
        """
        if not team_name or not league:
            return None

        team_lower = team_name.lower().strip()
        team_normalized = self._normalize_team_name(team_lower)

        conn = self._db()
        try:
            cursor = conn.cursor()

            # Determine if this is a soccer league
            is_soccer = "." in league or league in ("mls",)

            if is_soccer:
                return self._find_team_in_soccer_cache(
                    cursor, team_lower, team_normalized, league
                )
            else:
                return self._find_team_in_nonsoccer_cache(
                    cursor, team_lower, team_normalized, league
                )
        finally:
            conn.close()

    def get_team_info(self, team_name: str) -> list[TeamEntry]:
        """Get full team info for all matches of a team name.

        Args:
            team_name: Team name to search

        Returns:
            List of TeamEntry objects for all matching teams
        """
        if not team_name:
            return []

        team_lower = team_name.lower().strip()
        results = []

        conn = self._db()
        try:
            cursor = conn.cursor()

            # Search non-soccer cache
            cursor.execute(
                """
                SELECT espn_team_id, team_name, team_abbrev, team_short_name,
                       sport, league_code
                FROM team_league_cache
                WHERE LOWER(team_name) LIKE ?
                   OR LOWER(team_abbrev) = ?
                   OR LOWER(team_short_name) LIKE ?
                """,
                (f"%{team_lower}%", team_lower, f"%{team_lower}%"),
            )
            for row in cursor.fetchall():
                results.append(
                    TeamEntry(
                        provider_team_id=str(row[0]),
                        team_name=row[1],
                        team_abbrev=row[2],
                        team_short_name=row[3],
                        sport=row[4],
                        league=row[5],
                        provider="espn",
                    )
                )

            # Search soccer cache
            cursor.execute(
                """
                SELECT espn_team_id, team_name, team_abbrev, team_type, league_slug
                FROM soccer_team_leagues
                WHERE LOWER(team_name) LIKE ?
                   OR LOWER(team_abbrev) = ?
                """,
                (f"%{team_lower}%", team_lower),
            )
            for row in cursor.fetchall():
                results.append(
                    TeamEntry(
                        provider_team_id=str(row[0]),
                        team_name=row[1],
                        team_abbrev=row[2],
                        team_short_name=None,
                        sport="soccer",
                        league=row[4],
                        provider="espn",
                    )
                )

            return results
        finally:
            conn.close()

    def get_cache_stats(self) -> CacheStats:
        """Get combined cache status and statistics."""
        conn = self._db()
        try:
            cursor = conn.cursor()

            # Get soccer cache stats
            cursor.execute("SELECT * FROM soccer_cache_meta WHERE id = 1")
            soccer_row = cursor.fetchone()

            # Get non-soccer cache stats
            cursor.execute("SELECT * FROM team_league_cache_meta WHERE id = 1")
            nonsoccer_row = cursor.fetchone()

            # Combine stats
            last_refresh = None
            is_stale = True
            leagues_count = 0
            teams_count = 0

            # Use most recent refresh time
            if soccer_row and soccer_row[1]:
                try:
                    soccer_refresh = datetime.fromisoformat(
                        str(soccer_row[1]).replace("Z", "+00:00")
                    )
                    last_refresh = soccer_refresh
                    days_old = (
                        datetime.now(soccer_refresh.tzinfo) - soccer_refresh
                    ).days
                    is_stale = days_old > 7
                except (ValueError, TypeError):
                    pass
                leagues_count += soccer_row[2] or 0
                teams_count += soccer_row[3] or 0

            if nonsoccer_row and nonsoccer_row[1]:
                try:
                    ns_refresh = datetime.fromisoformat(
                        str(nonsoccer_row[1]).replace("Z", "+00:00")
                    )
                    if last_refresh is None or ns_refresh > last_refresh:
                        last_refresh = ns_refresh
                    days_old = (datetime.now(ns_refresh.tzinfo) - ns_refresh).days
                    is_stale = is_stale or days_old > 7
                except (ValueError, TypeError):
                    pass
                leagues_count += nonsoccer_row[2] or 0
                teams_count += nonsoccer_row[3] or 0

            return CacheStats(
                last_refresh=last_refresh,
                leagues_count=leagues_count,
                teams_count=teams_count,
                refresh_duration_seconds=0,  # Would need to track separately
                is_stale=is_stale,
                refresh_in_progress=False,
                last_error=None,
            )
        finally:
            conn.close()

    def is_cache_empty(self) -> bool:
        """Check if cache has any data."""
        try:
            conn = self._db()
            try:
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) FROM team_league_cache")
                nonsoccer_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM soccer_team_leagues")
                soccer_count = cursor.fetchone()[0]

                return nonsoccer_count == 0 and soccer_count == 0
            finally:
                conn.close()
        except Exception:
            return True

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    def _get_leagues_for_team(
        self,
        team_name: str,
        sport: str | None = None,
    ) -> set[str]:
        """Get all leagues a team name could belong to."""
        if not team_name:
            return set()

        team_lower = team_name.lower().strip()
        results = set()

        conn = self._db()
        try:
            cursor = conn.cursor()

            # Query non-soccer cache
            if sport != "soccer":
                query = """
                    SELECT DISTINCT league_code FROM team_league_cache
                    WHERE (LOWER(team_name) LIKE ?
                           OR LOWER(team_abbrev) = ?
                           OR LOWER(team_short_name) LIKE ?)
                """
                params = [f"%{team_lower}%", team_lower, f"%{team_lower}%"]

                if sport:
                    query += " AND sport = ?"
                    params.append(sport)

                cursor.execute(query, params)
                results.update(row[0] for row in cursor.fetchall())

            # Query soccer cache
            if sport is None or sport == "soccer":
                cursor.execute(
                    """
                    SELECT DISTINCT league_slug FROM soccer_team_leagues
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_abbrev) = ?
                    """,
                    (f"%{team_lower}%", team_lower),
                )
                results.update(row[0] for row in cursor.fetchall())

            return results
        finally:
            conn.close()

    def _normalize_team_name(self, name: str) -> str:
        """Normalize team name for matching."""
        # Strip accents
        import unicodedata

        normalized = unicodedata.normalize("NFD", name)
        stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")

        # Strip numbers
        stripped = re.sub(r"\d+", "", stripped)

        # Strip common articles
        stripped = re.sub(r"\b(de|del|da|do|di|du|the|fc|sc|ac)\b", "", stripped, flags=re.I)

        # Normalize whitespace
        return re.sub(r"\s+", " ", stripped).strip()

    def _parse_tags(self, tags_raw: str | None) -> list[str] | None:
        """Parse tags from JSON string."""
        if not tags_raw:
            return None
        try:
            tags = json.loads(tags_raw)
            if isinstance(tags, str):
                return [tags] if tags else []
            return tags
        except json.JSONDecodeError:
            return [tags_raw] if tags_raw else []

    def _find_team_in_soccer_cache(
        self,
        cursor,
        team_lower: str,
        team_normalized: str,
        league: str,
    ) -> str | None:
        """Find team ID in soccer cache."""
        # Tier 1: Direct match
        cursor.execute(
            """
            SELECT espn_team_id, team_name FROM soccer_team_leagues
            WHERE league_slug = ?
              AND (LOWER(team_name) LIKE ?
                   OR LOWER(team_abbrev) = ?)
            ORDER BY LENGTH(team_name) ASC
            LIMIT 1
            """,
            (league, f"%{team_lower}%", team_lower),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Team '{team_lower}' matched via direct lookup in {league}: {row[1]}"
            )
            return str(row[0])

        # Tier 2-4: Normalized matching against all teams in league
        cursor.execute(
            """
            SELECT espn_team_id, team_name FROM soccer_team_leagues
            WHERE league_slug = ?
            """,
            (league,),
        )
        for row in cursor.fetchall():
            db_normalized = self._normalize_team_name(row[1].lower())
            if team_normalized in db_normalized or db_normalized in team_normalized:
                logger.debug(
                    f"Team '{team_lower}' matched via normalization in {league}: {row[1]}"
                )
                return str(row[0])

        return None

    def _find_team_in_nonsoccer_cache(
        self,
        cursor,
        team_lower: str,
        team_normalized: str,
        league: str,
    ) -> str | None:
        """Find team ID in non-soccer cache."""
        # Tier 1: Direct match
        cursor.execute(
            """
            SELECT espn_team_id, team_name FROM team_league_cache
            WHERE league_code = ?
              AND (LOWER(team_name) LIKE ?
                   OR LOWER(team_abbrev) = ?
                   OR LOWER(team_short_name) LIKE ?)
            ORDER BY LENGTH(team_name) ASC
            LIMIT 1
            """,
            (league, f"%{team_lower}%", team_lower, f"%{team_lower}%"),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Team '{team_lower}' matched via direct lookup in {league}: {row[1]}"
            )
            return str(row[0])

        # Tier 2-4: Normalized matching
        cursor.execute(
            """
            SELECT espn_team_id, team_name FROM team_league_cache
            WHERE league_code = ?
            """,
            (league,),
        )
        for row in cursor.fetchall():
            db_normalized = self._normalize_team_name(row[1].lower())
            if team_normalized in db_normalized or db_normalized in team_normalized:
                logger.debug(
                    f"Team '{team_lower}' matched via normalization in {league}: {row[1]}"
                )
                return str(row[0])

        return None


# =============================================================================
# CACHE REFRESH
# =============================================================================


class CacheRefresher:
    """Refreshes team and league cache from providers.

    Uses ProviderRegistry to discover all registered providers and
    fetches team/league data from each.
    """

    MAX_WORKERS = 50

    def __init__(self, db_factory: Callable = get_connection):
        self._db = db_factory

    def refresh(
        self,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> dict:
        """Refresh entire cache from all registered providers.

        Args:
            progress_callback: Optional callback(message, percent)

        Returns:
            Dict with refresh statistics
        """
        from providers import ProviderRegistry

        start_time = time.time()

        def report(msg: str, pct: int) -> None:
            logger.info(f"Cache refresh: {msg}")
            if progress_callback:
                progress_callback(msg, pct)

        try:
            report("Starting cache refresh...", 5)

            # Get all enabled providers
            providers = ProviderRegistry.get_all()
            if not providers:
                logger.warning("No providers registered!")
                return {
                    "success": False,
                    "leagues_count": 0,
                    "teams_count": 0,
                    "duration_seconds": 0,
                    "error": "No providers registered",
                }

            total_leagues = 0
            total_teams = 0

            # Process each provider
            progress_per_provider = 85 // len(providers)

            for i, provider in enumerate(providers):
                base_progress = 5 + (i * progress_per_provider)
                report(f"Fetching from {provider.name}...", base_progress)

                try:
                    # Get leagues this provider supports
                    leagues = provider.get_supported_leagues()
                    if not leagues:
                        logger.info(f"No leagues from {provider.name}")
                        continue

                    # Separate soccer and non-soccer leagues
                    soccer_leagues = [lg for lg in leagues if "." in lg or lg == "mls"]
                    nonsoccer_leagues = [lg for lg in leagues if lg not in soccer_leagues]

                    # Fetch teams from non-soccer leagues
                    if nonsoccer_leagues:
                        # Capture base_progress in default arg to avoid closure issue
                        def make_callback(bp: int) -> Callable[[str, int], None]:
                            return lambda msg, pct: report(msg, bp + int(pct * 0.4))

                        ns_teams = self._fetch_nonsoccer_teams(
                            provider,
                            nonsoccer_leagues,
                            make_callback(base_progress),
                        )
                        total_teams += ns_teams
                        total_leagues += len(nonsoccer_leagues)

                    # Soccer leagues use separate refresh
                    # (would call SoccerMultiLeague.refresh_cache)
                    if soccer_leagues:
                        report(
                            f"Soccer leagues from {provider.name} use separate cache",
                            base_progress + 50,
                        )

                except Exception as e:
                    logger.error(f"Error processing provider {provider.name}: {e}")

            duration = time.time() - start_time
            report(f"Cache refresh complete in {duration:.1f}s", 100)

            return {
                "success": True,
                "leagues_count": total_leagues,
                "teams_count": total_teams,
                "duration_seconds": duration,
                "error": None,
            }

        except Exception as e:
            logger.error(f"Cache refresh failed: {e}")
            return {
                "success": False,
                "leagues_count": 0,
                "teams_count": 0,
                "duration_seconds": time.time() - start_time,
                "error": str(e),
            }

    def refresh_if_needed(self, max_age_days: int = 7) -> bool:
        """Refresh cache if stale.

        Args:
            max_age_days: Maximum cache age before refresh

        Returns:
            True if refresh was performed
        """
        cache = TeamLeagueCache(self._db)
        stats = cache.get_cache_stats()

        if stats.is_stale or cache.is_cache_empty():
            logger.info("Cache is stale or empty, refreshing...")
            result = self.refresh()
            return result["success"]

        return False

    def _fetch_nonsoccer_teams(
        self,
        provider,
        leagues: list[str],
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> int:
        """Fetch teams from non-soccer leagues.

        Returns count of teams saved.
        """
        all_teams = []
        completed = 0
        total = len(leagues)

        def fetch_league(league: str) -> list[dict]:
            """Fetch teams for a single league."""
            try:
                teams = provider.get_league_teams(league)
                if not teams:
                    return []

                return [
                    {
                        "league_code": league,
                        "espn_team_id": t.id,
                        "team_name": t.name,
                        "team_abbrev": t.abbreviation,
                        "team_short_name": t.short_name,
                        "sport": t.sport or self._infer_sport(league),
                    }
                    for t in teams
                ]
            except Exception as e:
                logger.warning(f"Failed to fetch teams from {league}: {e}")
                return []

        # Fetch in parallel
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_league, lg): lg for lg in leagues}

            for future in as_completed(futures):
                completed += 1
                if progress_callback and completed % 5 == 0:
                    pct = int((completed / total) * 100)
                    progress_callback(f"Processed {completed}/{total} leagues", pct)

                try:
                    teams = future.result()
                    all_teams.extend(teams)
                except Exception as e:
                    league = futures[future]
                    logger.warning(f"Error processing {league}: {e}")

        # Save to database
        if all_teams:
            self._save_nonsoccer_cache(all_teams)

        return len(all_teams)

    def _save_nonsoccer_cache(self, teams: list[dict]) -> None:
        """Save non-soccer teams to database."""
        conn = self._db()
        try:
            cursor = conn.cursor()

            # Clear old data
            cursor.execute("DELETE FROM team_league_cache")

            # Deduplicate
            seen = set()
            for team in teams:
                key = (team["league_code"], team["espn_team_id"])
                if key in seen:
                    continue
                seen.add(key)

                cursor.execute(
                    """
                    INSERT INTO team_league_cache
                    (league_code, espn_team_id, team_name, team_abbrev,
                     team_short_name, sport)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        team["league_code"],
                        team["espn_team_id"],
                        team["team_name"],
                        team["team_abbrev"],
                        team["team_short_name"],
                        team["sport"],
                    ),
                )

            # Update metadata
            now = datetime.utcnow().isoformat() + "Z"
            unique_leagues = len({t["league_code"] for t in teams})
            cursor.execute(
                """
                UPDATE team_league_cache_meta SET
                    last_refresh = ?,
                    leagues_processed = ?,
                    teams_indexed = ?
                WHERE id = 1
                """,
                (now, unique_leagues, len(seen)),
            )

            conn.commit()
            logger.info(f"Saved {len(seen)} teams to team_league_cache")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save cache: {e}")
            raise
        finally:
            conn.close()

    def _infer_sport(self, league: str) -> str:
        """Infer sport from league code."""
        sport_patterns = {
            "nfl": "football",
            "nba": "basketball",
            "nhl": "hockey",
            "mlb": "baseball",
            "wnba": "basketball",
            "college-football": "football",
            "mens-college-basketball": "basketball",
            "womens-college-basketball": "basketball",
            "mens-college-hockey": "hockey",
            "womens-college-hockey": "hockey",
        }
        return sport_patterns.get(league, "sports")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def get_cache() -> TeamLeagueCache:
    """Get default cache instance."""
    return TeamLeagueCache()


def refresh_cache(
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict:
    """Refresh cache from all providers."""
    return CacheRefresher().refresh(progress_callback)


def refresh_cache_if_needed(max_age_days: int = 7) -> bool:
    """Refresh cache if stale."""
    return CacheRefresher().refresh_if_needed(max_age_days)


def find_candidate_leagues(
    team1: str,
    team2: str,
    enabled_leagues: list[str] | None = None,
) -> list[str]:
    """Find leagues where both teams exist."""
    return get_cache().find_candidate_leagues(team1, team2, enabled_leagues=enabled_leagues)


def get_leagues_for_team(team_name: str) -> set[str]:
    """Get all leagues for a team name."""
    return get_cache().get_leagues_for_team(team_name)


def expand_leagues(
    leagues: list[str],
    provider: str | None = None,
) -> list[str]:
    """Expand special league patterns to actual league slugs.

    Handles patterns like:
    - "soccer_all" → all cached soccer leagues
    - "nfl" → ["nfl"]  (pass-through)

    Args:
        leagues: List of league patterns
        provider: Optional provider filter

    Returns:
        Expanded list of league slugs
    """
    cache = get_cache()
    result = []

    for league in leagues:
        if league == "soccer_all":
            # Expand to all soccer leagues
            soccer_leagues = cache.get_all_leagues(sport="soccer")
            result.extend(lg.league_slug for lg in soccer_leagues)
        elif league.endswith("_all"):
            # General pattern: sport_all → all leagues for that sport
            sport = league[:-4]  # Remove "_all" suffix
            sport_leagues = cache.get_all_leagues(sport=sport)
            result.extend(lg.league_slug for lg in sport_leagues)
        else:
            # Pass-through
            result.append(league)

    # Remove duplicates while preserving order
    seen: set = set()
    return [lg for lg in result if not (lg in seen or seen.add(lg))]


def find_leagues_for_stream(
    stream_name: str,
    sport: str | None = None,
    max_results: int = 5,
) -> list[str]:
    """Find candidate leagues for a stream by searching team cache.

    Scans the team cache for team names that appear in the stream,
    then returns the leagues those teams play in.

    Args:
        stream_name: Stream name to search
        sport: Optional sport filter
        max_results: Maximum leagues to return

    Returns:
        List of candidate league slugs
    """
    stream_lower = stream_name.lower()
    candidate_leagues: set = set()

    # Get all teams and check against stream name
    # This is a simple implementation - could be optimized with full-text search
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Check non-soccer teams
        if sport is None or sport != "soccer":
            query = """
                SELECT DISTINCT league_code, team_name, team_abbrev, team_short_name
                FROM team_league_cache
            """
            params = []
            if sport:
                query += " WHERE sport = ?"
                params.append(sport)

            cursor.execute(query, params)
            for row in cursor.fetchall():
                for name_field in [row[1], row[2], row[3]]:
                    if name_field and len(name_field) >= 3:
                        if name_field.lower() in stream_lower:
                            candidate_leagues.add(row[0])
                            break

                if len(candidate_leagues) >= max_results:
                    return list(candidate_leagues)[:max_results]

        # Check soccer teams
        if sport is None or sport == "soccer":
            cursor.execute(
                """
                SELECT DISTINCT league_slug, team_name, team_abbrev
                FROM soccer_team_leagues
                """
            )
            for row in cursor.fetchall():
                for name_field in [row[1], row[2]]:
                    if name_field and len(name_field) >= 3:
                        if name_field.lower() in stream_lower:
                            candidate_leagues.add(row[0])
                            break

                if len(candidate_leagues) >= max_results:
                    return list(candidate_leagues)[:max_results]

    finally:
        conn.close()

    return list(candidate_leagues)[:max_results]
