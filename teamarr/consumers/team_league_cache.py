"""Unified team and league cache.

Provides reverse-lookup for:
1. Event matching: "Freiburg vs Stuttgart" → candidate leagues
2. Team multi-league: Liverpool → [eng.1, uefa.champions, eng.fa, ...]
3. League discovery: all soccer leagues for "soccer_all"

Caches data from all registered providers (ESPN, TSDB, etc.).
Refresh weekly to handle promotion/relegation.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

from teamarr.core import SportsProvider
from teamarr.database import get_db

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
    logo_url: str | None


@dataclass
class LeagueEntry:
    """A league entry from the cache."""

    league_slug: str
    provider: str
    league_name: str | None
    sport: str
    logo_url: str | None
    team_count: int


# =============================================================================
# CACHE QUERIES
# =============================================================================


class TeamLeagueCache:
    """Query interface for team and league cache."""

    def __init__(self, db_factory: Callable = get_db):
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
    ) -> list[str]:
        """Get all leagues a team plays in.

        Used for team-based multi-league schedule aggregation.

        Args:
            provider_team_id: Team ID from the provider
            provider: Provider name ('espn' or 'tsdb')

        Returns:
            List of league slugs
        """
        with self._db() as conn:
            cursor = conn.cursor()
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
    ) -> list[LeagueEntry]:
        """Get all cached leagues.

        Args:
            sport: Optional filter by sport (e.g., 'soccer')
            provider: Optional filter by provider

        Returns:
            List of LeagueEntry objects
        """
        with self._db() as conn:
            cursor = conn.cursor()

            query = """SELECT league_slug, provider, league_name, sport, logo_url, team_count
                FROM league_cache WHERE 1=1"""
            params: list = []

            if sport:
                query += " AND sport = ?"
                params.append(sport)
            if provider:
                query += " AND provider = ?"
                params.append(provider)

            cursor.execute(query, params)

            return [
                LeagueEntry(
                    league_slug=row[0],
                    provider=row[1],
                    league_name=row[2],
                    sport=row[3],
                    logo_url=row[4],
                    team_count=row[5] or 0,
                )
                for row in cursor.fetchall()
            ]

    def get_league_info(self, league_slug: str) -> LeagueEntry | None:
        """Get metadata for a specific league."""
        with self._db() as conn:
            cursor = conn.cursor()
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
                    last_refresh = datetime.fromisoformat(
                        str(row[1]).replace("Z", "+00:00")
                    )
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


# =============================================================================
# CACHE REFRESH
# =============================================================================


class CacheRefresher:
    """Refreshes team and league cache from providers."""

    # Max parallel requests
    MAX_WORKERS = 50

    def __init__(self, db_factory: Callable = get_db):
        self._db = db_factory

    def refresh(
        self,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> dict:
        """Refresh entire cache from all registered providers.

        Uses ProviderRegistry to discover all providers and fetch their data.

        Args:
            progress_callback: Optional callback(message, percent)

        Returns:
            Dict with refresh statistics
        """
        from teamarr.providers import ProviderRegistry

        start_time = time.time()

        def report(msg: str, pct: int) -> None:
            logger.info(f"Cache refresh: {msg}")
            if progress_callback:
                progress_callback(msg, pct)

        try:
            self._set_refresh_in_progress(True)
            report("Starting cache refresh...", 5)

            # Collect all teams and leagues
            all_teams: list[dict] = []
            all_leagues: list[dict] = []

            # Get all enabled providers from the registry
            providers = ProviderRegistry.get_all()
            num_providers = len(providers)

            if num_providers == 0:
                logger.warning("No providers registered!")
                return {
                    "success": False,
                    "leagues_count": 0,
                    "teams_count": 0,
                    "duration_seconds": 0,
                    "error": "No providers registered",
                }

            # Calculate progress chunks per provider
            # Reserve 5% for start, 5% for saving
            progress_per_provider = 90 // num_providers

            for i, provider in enumerate(providers):
                base_progress = 5 + (i * progress_per_provider)
                report(f"Fetching from {provider.name}...", base_progress)

                # Create progress callback with captured values
                def make_progress_callback(
                    bp: int, ppp: int
                ) -> Callable[[str, int], None]:
                    def callback(msg: str, pct: int) -> None:
                        actual_pct = bp + int(pct * ppp / 100)
                        report(msg, actual_pct)
                    return callback

                leagues, teams = self._discover_from_provider(
                    provider, make_progress_callback(base_progress, progress_per_provider)
                )
                all_leagues.extend(leagues)
                all_teams.extend(teams)

            # Save to database (95-100%)
            report(f"Saving {len(all_teams)} teams, {len(all_leagues)} leagues...", 95)
            self._save_cache(all_teams, all_leagues)

            # Update metadata
            duration = time.time() - start_time
            self._update_meta(len(all_leagues), len(all_teams), duration, None)
            self._set_refresh_in_progress(False)

            report(f"Cache refresh complete in {duration:.1f}s", 100)

            return {
                "success": True,
                "leagues_count": len(all_leagues),
                "teams_count": len(all_teams),
                "duration_seconds": duration,
                "error": None,
            }

        except Exception as e:
            logger.error(f"Cache refresh failed: {e}")
            self._update_meta(0, 0, time.time() - start_time, str(e))
            self._set_refresh_in_progress(False)
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

    def _discover_from_provider(
        self,
        provider: SportsProvider,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Discover all leagues and teams from a provider.

        Uses the provider's get_supported_leagues() and get_league_teams() methods.
        For ESPN, also does dynamic soccer league discovery.

        Args:
            provider: The sports provider to discover from
            progress_callback: Optional callback(message, percent)

        Returns:
            (leagues, teams) tuple
        """
        provider_name = provider.name
        leagues: list[dict] = []
        teams: list[dict] = []

        # Get leagues this provider supports
        supported_leagues = provider.get_supported_leagues()

        # For ESPN, also discover dynamic soccer leagues
        if provider_name == "espn":
            soccer_slugs = self._fetch_espn_soccer_league_slugs()
            # Add soccer leagues not already in supported_leagues
            for slug in soccer_slugs:
                if slug not in supported_leagues:
                    supported_leagues.append(slug)

        if not supported_leagues:
            logger.info(f"No leagues found for provider {provider_name}")
            return [], []

        # Build league list with sport info
        all_leagues_with_sport: list[tuple[str, str]] = []
        for league_slug in supported_leagues:
            # Determine sport from league slug
            sport = self._infer_sport_from_league(league_slug)
            all_leagues_with_sport.append((league_slug, sport))

        total = len(all_leagues_with_sport)
        completed = 0

        def fetch_league_teams(league_slug: str, sport: str) -> tuple[dict, list[dict]]:
            """Fetch teams for a single league."""
            try:
                league_teams = provider.get_league_teams(league_slug)

                league_info = {
                    "league_slug": league_slug,
                    "provider": provider_name,
                    "sport": sport,
                    "league_name": None,
                    "logo_url": None,
                    "team_count": len(league_teams) if league_teams else 0,
                }

                team_entries = []
                for team in league_teams or []:
                    team_entries.append({
                        "team_name": team.name,
                        "team_abbrev": team.abbreviation,
                        "team_short_name": team.short_name,
                        "provider": provider_name,
                        "provider_team_id": team.id,
                        "league": league_slug,
                        "sport": team.sport or sport,
                        "logo_url": team.logo_url,
                    })

                return league_info, team_entries
            except Exception as e:
                logger.warning(f"Failed to fetch {provider_name} teams for {league_slug}: {e}")
                return {
                    "league_slug": league_slug,
                    "provider": provider_name,
                    "sport": sport,
                    "league_name": None,
                    "logo_url": None,
                    "team_count": 0,
                }, []

        # Fetch in parallel
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_league_teams, slug, sport): (slug, sport)
                for slug, sport in all_leagues_with_sport
            }

            for future in as_completed(futures):
                completed += 1
                if progress_callback and completed % 20 == 0:
                    pct = int((completed / total) * 100)
                    progress_callback(f"{provider_name}: {completed}/{total} leagues", pct)

                try:
                    league_info, team_entries = future.result()
                    leagues.append(league_info)
                    teams.extend(team_entries)
                except Exception as e:
                    slug, sport = futures[future]
                    logger.warning(f"Error processing {provider_name} {slug}: {e}")

        logger.info(f"{provider_name} discovery: {len(leagues)} leagues, {len(teams)} teams")
        return leagues, teams

    def _infer_sport_from_league(self, league_slug: str) -> str:
        """Infer sport from league slug.

        Uses common patterns and database mappings.
        """
        # Check common patterns
        sport_patterns = {
            "nfl": "football",
            "nba": "basketball",
            "nhl": "hockey",
            "mlb": "baseball",
            "wnba": "basketball",
            "mls": "soccer",
            "ufc": "mma",
            "college-football": "football",
            "mens-college-basketball": "basketball",
            "womens-college-basketball": "basketball",
            "mens-college-hockey": "hockey",
            "womens-college-hockey": "hockey",
        }

        if league_slug in sport_patterns:
            return sport_patterns[league_slug]

        # Soccer leagues use dot notation (e.g., eng.1, ger.1)
        if "." in league_slug:
            return "soccer"

        # Check database for league mapping
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport FROM league_provider_mappings
                WHERE league_code = ? LIMIT 1
                """,
                (league_slug,),
            )
            row = cursor.fetchone()
            if row:
                return row[0].lower()

        # Default fallback
        return "sports"

    def _fetch_espn_soccer_league_slugs(self) -> list[str]:
        """Fetch all ESPN soccer league slugs."""
        import httpx

        url = "https://sports.core.api.espn.com/v2/sports/soccer/leagues?limit=500"

        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()

            # Extract league refs and fetch slugs
            league_refs = data.get("items", [])
            slugs = []

            def fetch_slug(ref_url: str) -> str | None:
                try:
                    with httpx.Client(timeout=10) as client:
                        resp = client.get(ref_url)
                        if resp.status_code == 200:
                            return resp.json().get("slug")
                except Exception:
                    pass
                return None

            # Fetch slugs in parallel
            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_slug, ref["$ref"]): ref
                    for ref in league_refs
                    if "$ref" in ref
                }

                for future in as_completed(futures):
                    slug = future.result()
                    if slug and self._should_include_soccer_league(slug):
                        slugs.append(slug)

            logger.info(f"Found {len(slugs)} ESPN soccer leagues")
            return slugs

        except Exception as e:
            logger.error(f"Failed to fetch ESPN soccer leagues: {e}")
            return []

    def _should_include_soccer_league(self, slug: str) -> bool:
        """Filter out junk soccer leagues."""
        skip_slugs = {"nonfifa", "usa.ncaa.m.1", "usa.ncaa.w.1"}
        skip_patterns = ["not_used"]

        if slug in skip_slugs:
            return False
        for pattern in skip_patterns:
            if pattern in slug:
                return False
        return True

    def _save_cache(self, teams: list[dict], leagues: list[dict]) -> None:
        """Save teams and leagues to database."""
        now = datetime.utcnow().isoformat() + "Z"

        with self._db() as conn:
            cursor = conn.cursor()

            # Clear old data
            cursor.execute("DELETE FROM team_cache")
            cursor.execute("DELETE FROM league_cache")

            # Insert leagues
            for league in leagues:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO league_cache
                    (league_slug, provider, league_name, sport, logo_url,
                     team_count, last_refreshed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        league["league_slug"],
                        league["provider"],
                        league.get("league_name"),
                        league["sport"],
                        league.get("logo_url"),
                        league.get("team_count", 0),
                        now,
                    ),
                )

            # Deduplicate teams by (provider, provider_team_id, league)
            seen: set = set()
            unique_teams = []
            for team in teams:
                key = (team["provider"], team["provider_team_id"], team["league"])
                if key not in seen:
                    seen.add(key)
                    unique_teams.append(team)

            # Insert teams
            for team in unique_teams:
                cursor.execute(
                    """
                    INSERT INTO team_cache
                    (team_name, team_abbrev, team_short_name, provider,
                     provider_team_id, league, sport, logo_url, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        team["team_name"],
                        team.get("team_abbrev"),
                        team.get("team_short_name"),
                        team["provider"],
                        team["provider_team_id"],
                        team["league"],
                        team["sport"],
                        team.get("logo_url"),
                        now,
                    ),
                )

            logger.info(
                f"Saved {len(leagues)} leagues and {len(unique_teams)} teams to cache"
            )

    def _update_meta(
        self,
        leagues_count: int,
        teams_count: int,
        duration: float,
        error: str | None,
    ) -> None:
        """Update cache metadata."""
        now = datetime.utcnow().isoformat() + "Z"

        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE cache_meta SET
                    last_full_refresh = ?,
                    leagues_count = ?,
                    teams_count = ?,
                    refresh_duration_seconds = ?,
                    last_error = ?
                WHERE id = 1
                """,
                (now, leagues_count, teams_count, duration, error),
            )

    def _set_refresh_in_progress(self, in_progress: bool) -> None:
        """Set refresh in progress flag."""
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE cache_meta SET refresh_in_progress = ? WHERE id = 1",
                (1 if in_progress else 0,),
            )


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
        provider: Optional provider filter ('espn', 'tsdb')

    Returns:
        Expanded list of league slugs
    """
    cache = get_cache()
    result = []

    for league in leagues:
        if league == "soccer_all":
            # Expand to all soccer leagues
            soccer_leagues = cache.get_all_leagues(sport="soccer", provider=provider)
            result.extend(lg.league_slug for lg in soccer_leagues)
        elif league.endswith("_all"):
            # General pattern: sport_all → all leagues for that sport
            sport = league[:-4]  # Remove "_all" suffix
            sport_leagues = cache.get_all_leagues(sport=sport, provider=provider)
            result.extend(lg.league_slug for lg in sport_leagues)
        else:
            # Pass-through
            result.append(league)

    # Remove duplicates while preserving order
    seen: set = set()
    return [lg for lg in result if not (lg in seen or seen.add(lg))]  # type: ignore


def find_leagues_for_stream(
    stream_name: str,
    sport: str | None = None,
    provider: str | None = None,
    max_results: int = 5,
) -> list[str]:
    """Find candidate leagues for a stream by searching team cache.

    Scans the team cache for team names that appear in the stream,
    then returns the leagues those teams play in.

    This is useful for soccer matching where there are 300+ leagues -
    we can narrow down to just a few based on team name matches.

    Args:
        stream_name: Stream name to search
        sport: Optional sport filter
        provider: Optional provider filter
        max_results: Maximum leagues to return

    Returns:
        List of candidate league slugs
    """
    from teamarr.database import get_db

    stream_lower = stream_name.lower()
    candidate_leagues: set = set()

    with get_db() as conn:
        cursor = conn.cursor()

        # Build query to find teams whose names appear in the stream
        query = """
            SELECT DISTINCT league, team_name, team_abbrev, team_short_name
            FROM team_cache
            WHERE 1=1
        """
        params: list = []

        if sport:
            query += " AND sport = ?"
            params.append(sport)
        if provider:
            query += " AND provider = ?"
            params.append(provider)

        cursor.execute(query, params)

        # Check each team against the stream name
        for row in cursor.fetchall():
            league = row["league"]

            # Check if team name variants appear in stream
            for name_field in ["team_name", "team_short_name", "team_abbrev"]:
                name = row[name_field]
                if name and len(name) >= 3:  # Skip very short names
                    name_lower = name.lower()
                    if name_lower in stream_lower:
                        candidate_leagues.add(league)
                        break  # Found a match for this team, move to next

            if len(candidate_leagues) >= max_results:
                break

    return list(candidate_leagues)[:max_results]
