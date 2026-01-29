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

from .queries import TeamLeagueCache

logger = logging.getLogger(__name__)

# Expected league counts per provider (for progress estimation)
# These are approximate and used for work-proportional progress allocation
EXPECTED_LEAGUES = {
    "espn": 280,  # ~52 configured + ~228 discovered soccer leagues
    "tsdb": 6,  # NRL, Boxing, IPL, BBL, BPL, SA20 (cricket primary, Cricbuzz fallback)
    "hockeytech": 6,
    "cricbuzz": 0,  # Cricket moved to TSDB primary (Cricbuzz is fallback for schedules only)
}


class CacheRefresher:
    """Refreshes team and league cache from providers."""

    # Max parallel requests
    MAX_WORKERS = 50
    # Update progress every N leagues
    PROGRESS_UPDATE_INTERVAL = 5

    def __init__(self, db_factory: Callable = get_db) -> None:
        self._db = db_factory

    def _get_league_metadata(self, league_slug: str) -> dict | None:
        """Get league metadata from the leagues table.

        The leagues table is the single source of truth for league display data.

        Returns:
            Dict with display_name, logo_url, sport, league_id or None
        """
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT display_name, logo_url, sport, league_id
                FROM leagues WHERE league_code = ?
                """,
                (league_slug,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "display_name": row["display_name"],
                    "logo_url": row["logo_url"],
                    "sport": row["sport"],
                    "league_id": row["league_id"],
                }
        return None

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
            if progress_callback:
                progress_callback(msg, pct)

        try:
            self._set_refresh_in_progress(True)
            logger.info("[STARTED] Cache refresh")
            report("Starting cache refresh...", 5)

            # Collect all teams and leagues
            all_teams: list[dict] = []
            all_leagues: list[dict] = []

            # Get all enabled providers from the registry
            providers = ProviderRegistry.get_all()
            num_providers = len(providers)

            if num_providers == 0:
                logger.warning("[CACHE_REFRESH] No providers registered!")
                return {
                    "success": False,
                    "leagues_count": 0,
                    "teams_count": 0,
                    "duration_seconds": 0,
                    "error": "No providers registered",
                }

            # Calculate work-proportional progress allocation
            # Reserve 5% for start, 5% for saving = 90% for discovery
            total_expected_leagues = sum(EXPECTED_LEAGUES.get(p.name, 10) for p in providers)

            # Calculate progress ranges per provider based on expected work
            provider_progress: list[tuple[SportsProvider, int, int]] = []
            current_pct = 5  # Start at 5%
            for provider in providers:
                expected = EXPECTED_LEAGUES.get(provider.name, 10)
                # Proportional share of the 90% discovery budget
                share = int(90 * expected / total_expected_leagues)
                end_pct = min(current_pct + share, 95)
                provider_progress.append((provider, current_pct, end_pct))
                current_pct = end_pct

            for provider, start_pct, end_pct in provider_progress:
                report(f"Fetching from {provider.name}...", start_pct)

                # Create progress callback with captured values
                def make_progress_callback(sp: int, ep: int) -> Callable[[str, int], None]:
                    def callback(msg: str, pct: int) -> None:
                        # Map 0-100% within this provider to start_pct-end_pct
                        actual_pct = sp + int(pct * (ep - sp) / 100)
                        report(msg, actual_pct)

                    return callback

                leagues, teams = self._discover_from_provider(
                    provider, make_progress_callback(start_pct, end_pct)
                )
                all_leagues.extend(leagues)
                all_teams.extend(teams)

            # Merge TSDB seed data with API results before saving
            # This fills in teams that the free tier API doesn't return
            all_teams, all_leagues = self._merge_with_seed(all_teams, all_leagues)

            # Auto-discover Cricbuzz series IDs (yearly updates)
            self._update_cricbuzz_series_ids(progress_callback)

            # Save to database (95-100%)
            report(f"Saving {len(all_teams)} teams, {len(all_leagues)} leagues...", 95)
            self._save_cache(all_teams, all_leagues)

            # Update existing soccer teams with newly discovered leagues
            soccer_updated = self._refresh_soccer_team_leagues()
            if soccer_updated > 0:
                report(f"Updated {soccer_updated} soccer teams with new leagues", 98)

            # Update metadata
            duration = time.time() - start_time
            self._update_meta(len(all_leagues), len(all_teams), duration, None)
            self._set_refresh_in_progress(False)

            logger.info(
                "[COMPLETED] Cache refresh: %d leagues, %d teams, %.1fs",
                len(all_leagues),
                len(all_teams),
                duration,
            )
            report(f"Cache refresh complete in {duration:.1f}s", 100)

            return {
                "success": True,
                "leagues_count": len(all_leagues),
                "teams_count": len(all_teams),
                "duration_seconds": duration,
                "error": None,
            }

        except Exception as e:
            logger.error("[FAILED] Cache refresh: %s", e)
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
            logger.info("[CACHE_REFRESH] Cache is stale or empty, refreshing...")
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
            if progress_callback:
                progress_callback("Discovering ESPN soccer leagues...", 0)
            soccer_slugs = self._fetch_espn_soccer_league_slugs(progress_callback)
            # Add soccer leagues not already in supported_leagues
            for slug in soccer_slugs:
                if slug not in supported_leagues:
                    supported_leagues.append(slug)

        if not supported_leagues:
            logger.info("[CACHE_REFRESH] No leagues found for provider %s", provider_name)
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

                # Check leagues table first (single source of truth)
                db_metadata = self._get_league_metadata(league_slug)
                league_name = db_metadata["display_name"] if db_metadata else None
                logo_url = db_metadata["logo_url"] if db_metadata else None

                # Fall back to ESPN API if not in leagues table
                if (not logo_url or not league_name) and provider_name == "espn":
                    try:
                        from teamarr.providers.espn.client import ESPNClient

                        client = ESPNClient()
                        league_info_api = client.get_league_info(league_slug)
                        if league_info_api:
                            if not logo_url:
                                logo_url = league_info_api.get("logo_url")
                            if not league_name:
                                league_name = league_info_api.get("name")
                    except Exception as e:
                        logger.debug(
                            "[CACHE_REFRESH] Could not fetch league info for %s: %s", league_slug, e
                        )

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
                logger.warning(
                    "[CACHE_REFRESH] Failed to fetch %s teams for %s: %s",
                    provider_name,
                    league_slug,
                    e,
                )
                db_metadata = self._get_league_metadata(league_slug)
                return {
                    "league_slug": league_slug,
                    "provider": provider_name,
                    "sport": sport,
                    "league_name": db_metadata["display_name"] if db_metadata else None,
                    "logo_url": db_metadata["logo_url"] if db_metadata else None,
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
                # Report progress for every league (real-time streaming)
                if progress_callback:
                    pct = int((completed / total) * 100)
                    progress_callback(f"{provider_name}: {completed}/{total} leagues", pct)

                try:
                    league_info, team_entries = future.result()
                    leagues.append(league_info)
                    teams.extend(team_entries)
                except Exception as e:
                    slug, sport = futures[future]
                    logger.warning(
                        "[CACHE_REFRESH] Error processing %s %s: %s", provider_name, slug, e
                    )

        logger.debug(
            "[DISCOVERY] %s: %d leagues, %d teams",
            provider_name,
            len(leagues),
            len(teams),
        )
        return leagues, teams

    def _infer_sport_from_league(self, league_slug: str) -> str:
        """Infer sport from league slug.

        Checks leagues table first (single source of truth), then uses heuristics.
        """
        # Check database first (single source of truth)
        db_metadata = self._get_league_metadata(league_slug)
        if db_metadata and db_metadata.get("sport"):
            return db_metadata["sport"].lower()

        # Soccer leagues use dot notation (e.g., eng.1, ger.1)
        if "." in league_slug:
            return "soccer"

        # Heuristic fallbacks for undiscovered leagues
        if "football" in league_slug:
            return "football"
        if "basketball" in league_slug:
            return "basketball"
        if "hockey" in league_slug:
            return "hockey"
        if "baseball" in league_slug:
            return "baseball"
        if "lacrosse" in league_slug:
            return "lacrosse"
        if "volleyball" in league_slug:
            return "volleyball"
        if "softball" in league_slug:
            return "softball"

        # Default fallback
        return "sports"

    def _fetch_espn_soccer_league_slugs(
        self, progress_callback: Callable[[str, int], None] | None = None
    ) -> list[str]:
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
            total = len(league_refs)
            completed = 0

            def fetch_slug(ref_url: str) -> str | None:
                try:
                    with httpx.Client(timeout=10) as client:
                        resp = client.get(ref_url)
                        if resp.status_code == 200:
                            return resp.json().get("slug")
                except (httpx.RequestError, httpx.HTTPStatusError) as e:
                    logger.debug(
                        "[CACHE_REFRESH] Failed to fetch league slug from %s: %s", ref_url, e
                    )
                return None

            # Fetch slugs in parallel
            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_slug, ref["$ref"]): ref
                    for ref in league_refs
                    if "$ref" in ref
                }

                for future in as_completed(futures):
                    completed += 1
                    # Report progress during discovery (maps to 0-10% of provider range)
                    if progress_callback and completed % 5 == 0:
                        discovery_pct = int((completed / total) * 10)  # 0-10%
                        progress_callback(
                            f"Discovering soccer leagues: {completed}/{total}", discovery_pct
                        )

                    slug = future.result()
                    if slug and self._should_include_soccer_league(slug):
                        slugs.append(slug)

            logger.info("[CACHE_REFRESH] Found %d ESPN soccer leagues", len(slugs))
            return slugs

        except Exception as e:
            logger.error("[CACHE_REFRESH] Failed to fetch ESPN soccer leagues: %s", e)
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
        """Save teams and leagues to database using batch inserts."""
        now = datetime.utcnow().isoformat() + "Z"

        with self._db() as conn:
            cursor = conn.cursor()

            # Clear old data
            cursor.execute("DELETE FROM team_cache")
            cursor.execute("DELETE FROM league_cache")

            # Batch insert leagues using executemany (much faster than one-by-one)
            league_data = [
                (
                    league["league_slug"],
                    league["provider"],
                    league.get("league_name"),
                    league["sport"],
                    league.get("logo_url"),
                    league.get("team_count", 0),
                    now,
                )
                for league in leagues
            ]
            cursor.executemany(
                """
                INSERT OR REPLACE INTO league_cache
                (league_slug, provider, league_name, sport, logo_url,
                 team_count, last_refreshed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                league_data,
            )

            # Deduplicate teams by (provider, provider_team_id, league)
            # Skip teams without names (required field)
            seen: set = set()
            unique_teams = []
            for team in teams:
                # Skip teams without required name field
                if not team.get("team_name"):
                    continue
                key = (team["provider"], team["provider_team_id"], team["league"])
                if key not in seen:
                    seen.add(key)
                    unique_teams.append(team)

            # Batch insert teams using executemany (much faster than one-by-one)
            team_data = [
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
                )
                for team in unique_teams
            ]
            cursor.executemany(
                """
                INSERT INTO team_cache
                (team_name, team_abbrev, team_short_name, provider,
                 provider_team_id, league, sport, logo_url, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                team_data,
            )

            # Update cached_team_count in the leagues table for configured leagues
            self._update_leagues_team_counts(cursor, leagues)

            logger.debug(
                "[SAVED] Cache: %d leagues, %d teams",
                len(leagues),
                len(unique_teams),
            )

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

    def _merge_with_seed(
        self, api_teams: list[dict], api_leagues: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Merge API results with TSDB seed data.

        TSDB free tier only returns 10 teams per league. The seed file contains
        complete team rosters. This merges them efficiently in memory:
        - Seed data provides the base
        - API data overwrites seed for matching keys (fresher data)

        Args:
            api_teams: Teams fetched from providers
            api_leagues: Leagues fetched from providers

        Returns:
            (merged_teams, merged_leagues) tuple
        """
        from teamarr.database.seed import load_tsdb_seed

        seed_data = load_tsdb_seed()
        if not seed_data:
            return api_teams, api_leagues

        # Merge teams: seed first, API overwrites (API data is fresher)
        teams_by_key: dict[tuple, dict] = {}

        # Add seed teams first
        for team in seed_data.get("teams", []):
            key = (team["provider"], team["provider_team_id"], team["league"])
            teams_by_key[key] = {
                "team_name": team["team_name"],
                "team_abbrev": team.get("team_abbrev"),
                "team_short_name": team.get("team_short_name"),
                "provider": team["provider"],
                "provider_team_id": team["provider_team_id"],
                "league": team["league"],
                "sport": team["sport"],
                "logo_url": team.get("logo_url"),
            }

        # API teams overwrite seed (fresher data)
        for team in api_teams:
            if not team.get("team_name"):
                continue
            key = (team["provider"], team["provider_team_id"], team["league"])
            teams_by_key[key] = team

        # Merge leagues: seed first, API overwrites
        leagues_by_key: dict[tuple, dict] = {}

        # Add seed leagues first
        for league in seed_data.get("leagues", []):
            key = (league["code"], "tsdb")
            leagues_by_key[key] = {
                "league_slug": league["code"],
                "provider": "tsdb",
                "sport": league["sport"],
                "league_name": league.get("provider_league_name"),
                "logo_url": None,  # Seed doesn't have logos
                "team_count": league.get("team_count", 0),
            }

        # API leagues overwrite seed
        for league in api_leagues:
            key = (league["league_slug"], league["provider"])
            leagues_by_key[key] = league

        merged_teams = list(teams_by_key.values())
        merged_leagues = list(leagues_by_key.values())

        # Update league team counts to reflect merged totals
        league_team_counts: dict[str, int] = {}
        for team in merged_teams:
            league = team.get("league")
            if league:
                league_team_counts[league] = league_team_counts.get(league, 0) + 1

        for league in merged_leagues:
            slug = league.get("league_slug")
            if slug in league_team_counts:
                league["team_count"] = league_team_counts[slug]

        added_from_seed = len(merged_teams) - len(api_teams)
        if added_from_seed > 0:
            logger.info("[CACHE_REFRESH] Merged %d teams from TSDB seed", added_from_seed)

        return merged_teams, merged_leagues

    def _update_cricbuzz_series_ids(
        self,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> int:
        """Auto-discover and update Cricbuzz series IDs.

        Cricbuzz series IDs change yearly (e.g., IPL 2025 -> IPL 2026).
        This method discovers current series and updates:
        - fallback_league_id for leagues with fallback_provider='cricbuzz'
        - provider_league_id for leagues with provider='cricbuzz' (legacy)

        Returns:
            Number of leagues updated
        """
        from teamarr.providers.cricbuzz import CricbuzzClient

        if progress_callback:
            progress_callback("Discovering Cricbuzz series...", 92)

        # Get leagues with series_slug_pattern (Cricbuzz auto-discovery enabled)
        # Supports both primary Cricbuzz leagues and TSDB leagues with Cricbuzz fallback
        with self._db() as conn:
            cursor = conn.execute(
                """
                SELECT league_code, provider, provider_league_id,
                       fallback_provider, fallback_league_id, series_slug_pattern
                FROM leagues
                WHERE (provider = 'cricbuzz' OR fallback_provider = 'cricbuzz')
                  AND series_slug_pattern IS NOT NULL
                  AND enabled = 1
                """
            )
            cricbuzz_leagues = cursor.fetchall()

        if not cricbuzz_leagues:
            return 0

        # Discover current series from Cricbuzz
        client = CricbuzzClient()
        discovered = client.discover_active_series()

        if not discovered:
            logger.warning("[CRICBUZZ] Auto-discovery returned no series")
            return 0

        # Update leagues with discovered series IDs
        updated = 0
        with self._db() as conn:
            for row in cricbuzz_leagues:
                league_code = row["league_code"]
                pattern = row["series_slug_pattern"]

                # Find matching discovered series
                if pattern not in discovered:
                    continue

                new_id = discovered[pattern]

                # Determine which column to update based on provider config
                if row["fallback_provider"] == "cricbuzz":
                    # TSDB primary with Cricbuzz fallback - update fallback_league_id
                    current_id = row["fallback_league_id"]
                    if new_id != current_id:
                        conn.execute(
                            "UPDATE leagues SET fallback_league_id = ? WHERE league_code = ?",
                            (new_id, league_code),
                        )
                        logger.info(
                            "[CRICBUZZ] Auto-update (fallback): %s %s -> %s",
                            league_code,
                            current_id,
                            new_id,
                        )
                        updated += 1
                elif row["provider"] == "cricbuzz":
                    # Cricbuzz primary (legacy) - update provider_league_id
                    current_id = row["provider_league_id"]
                    if new_id != current_id:
                        conn.execute(
                            "UPDATE leagues SET provider_league_id = ? WHERE league_code = ?",
                            (new_id, league_code),
                        )
                        logger.info(
                            "[CRICBUZZ] Auto-update: %s %s -> %s",
                            league_code,
                            current_id,
                            new_id,
                        )
                        updated += 1

        if updated > 0:
            logger.info("[CRICBUZZ] Updated %d series ID(s)", updated)

        return updated

    def _refresh_soccer_team_leagues(self) -> int:
        """Update existing soccer teams with all leagues from cache.

        Soccer teams play in multiple competitions (EPL + Champions League + FA Cup).
        After cache refresh, update existing teams' leagues arrays with any new
        competitions found in the cache.

        Returns:
            Number of teams updated
        """
        import json

        updated = 0

        with self._db() as conn:
            # Get all soccer teams
            cursor = conn.execute(
                "SELECT id, provider, provider_team_id, leagues FROM teams WHERE sport = 'soccer'"
            )
            soccer_teams = cursor.fetchall()

            for team in soccer_teams:
                team_id = team["id"]
                provider = team["provider"]
                provider_team_id = team["provider_team_id"]

                # Parse current leagues
                try:
                    current_leagues = json.loads(team["leagues"]) if team["leagues"] else []
                except (json.JSONDecodeError, TypeError):
                    current_leagues = []

                # Get all leagues from cache for this team
                cache_cursor = conn.execute(
                    """SELECT DISTINCT league FROM team_cache
                    WHERE provider = ? AND provider_team_id = ? AND sport = 'soccer'""",
                    (provider, provider_team_id),
                )
                cache_leagues = [row["league"] for row in cache_cursor.fetchall()]

                # Merge and check if there are new leagues
                all_leagues = sorted(set(current_leagues + cache_leagues))
                if all_leagues != sorted(current_leagues):
                    conn.execute(
                        "UPDATE teams SET leagues = ? WHERE id = ?",
                        (json.dumps(all_leagues), team_id),
                    )
                    updated += 1
                    logger.debug(
                        "[CACHE_REFRESH] Updated soccer team %d: %d -> %d leagues",
                        team_id,
                        len(current_leagues),
                        len(all_leagues),
                    )

            if updated > 0:
                logger.info("[CACHE_REFRESH] Updated leagues for %d soccer teams", updated)

        return updated
