"""Cache refresh logic.

Refreshes team and league cache from all registered providers.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from teamarr.core import SportsProvider
from teamarr.database import get_db

from .constants import KNOWN_LEAGUE_LOGOS, KNOWN_LEAGUE_NAMES, SPORT_PATTERNS
from .queries import TeamLeagueCache

logger = logging.getLogger(__name__)


class CacheRefresher:
    """Refreshes team and league cache from providers."""

    # Max parallel requests
    MAX_WORKERS = 50

    def __init__(self, db_factory: Callable = get_db) -> None:
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
                def make_progress_callback(bp: int, ppp: int) -> Callable[[str, int], None]:
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

                # Use known mappings, fallback to slug for name
                league_name = KNOWN_LEAGUE_NAMES.get(league_slug)
                logo_url = KNOWN_LEAGUE_LOGOS.get(league_slug)

                league_info = {
                    "league_slug": league_slug,
                    "provider": provider_name,
                    "sport": sport,
                    "league_name": league_name,
                    "logo_url": logo_url,
                    "team_count": len(league_teams) if league_teams else 0,
                }

                team_entries = []
                for team in league_teams or []:
                    team_entries.append(
                        {
                            "team_name": team.name,
                            "team_abbrev": team.abbreviation,
                            "team_short_name": team.short_name,
                            "provider": provider_name,
                            "provider_team_id": team.id,
                            "league": league_slug,
                            "sport": team.sport or sport,
                            "logo_url": team.logo_url,
                        }
                    )

                return league_info, team_entries
            except Exception as e:
                logger.warning(f"Failed to fetch {provider_name} teams for {league_slug}: {e}")
                return {
                    "league_slug": league_slug,
                    "provider": provider_name,
                    "sport": sport,
                    "league_name": KNOWN_LEAGUE_NAMES.get(league_slug),
                    "logo_url": KNOWN_LEAGUE_LOGOS.get(league_slug),
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
        if league_slug in SPORT_PATTERNS:
            return SPORT_PATTERNS[league_slug]

        # Soccer leagues use dot notation (e.g., eng.1, ger.1)
        if "." in league_slug:
            return "soccer"

        # Check database for configured leagues
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport FROM leagues
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

            # Update cached_team_count in the leagues table for configured leagues
            self._update_leagues_team_counts(cursor, leagues)

            logger.info(f"Saved {len(leagues)} leagues and {len(unique_teams)} teams to cache")

    def _update_leagues_team_counts(self, cursor, leagues: list[dict]) -> None:
        """Update cached_team_count in the leagues table.

        Updates the cached team count for configured leagues based on
        what we discovered during cache refresh.
        """
        now = datetime.utcnow().isoformat() + "Z"

        for league in leagues:
            league_slug = league["league_slug"]
            team_count = league.get("team_count", 0)

            cursor.execute(
                """
                UPDATE leagues
                SET cached_team_count = ?, last_cache_refresh = ?
                WHERE league_code = ?
                """,
                (team_count, now, league_slug),
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
