"""Cricbuzz web scraper client.

Extracts cricket data from Cricbuzz.com by parsing embedded JSON from their
React/Next.js pages. This is web scraping - not an official API.

Health Monitoring:
- Tracks parse success/failure rates
- Validates expected JSON structure
- Logs CRITICAL alerts when structure changes detected
- Call health_check() to get current status

IMPORTANT: Site structure may change. If data stops flowing, check:
1. health_check() status for parse errors
2. Logs for CRITICAL alerts about missing fields
3. The actual site HTML to see if JSON structure changed
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from teamarr.core.interfaces import LeagueMappingSource
from teamarr.utilities.cache import TTLCache, make_cache_key

logger = logging.getLogger(__name__)

CRICBUZZ_BASE_URL = "https://www.cricbuzz.com"

# Cache TTLs (seconds)
CACHE_TTL_LIVE = 5 * 60  # 5 minutes - live scores
CACHE_TTL_SCHEDULE = 4 * 60 * 60  # 4 hours - series schedule
CACHE_TTL_TEAMS = 24 * 60 * 60  # 24 hours - team list


# Expected fields for structure validation
REQUIRED_MATCH_FIELDS = {"matchId", "seriesId", "startDate", "team1", "team2", "state"}
REQUIRED_TEAM_FIELDS = {"teamId", "teamName"}


@dataclass
class HealthStats:
    """Health monitoring statistics."""

    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    parse_errors: int = 0
    structure_warnings: int = 0
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None
    missing_fields: list[str] = field(default_factory=list)


class CricbuzzClient:
    """Low-level Cricbuzz web scraper.

    Extracts cricket data from embedded JSON in Cricbuzz pages.
    This is web scraping - site changes may break parsing.

    Uses LeagueMappingSource for league routing - provider_league_id
    contains the Cricbuzz series ID.
    """

    def __init__(
        self,
        league_mapping_source: LeagueMappingSource | None = None,
        timeout: float = 15.0,
        retry_count: int = 3,
    ):
        self._league_mapping_source = league_mapping_source
        self._timeout = timeout
        self._retry_count = retry_count
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()
        self._cache = TTLCache()
        self._health = HealthStats()
        self._health_lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client (thread-safe)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=self._timeout,
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0.0.0 Safari/537.36"
                            ),
                            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.5",
                        },
                    )
        return self._client

    def _record_success(self) -> None:
        """Record successful request."""
        with self._health_lock:
            self._health.requests_total += 1
            self._health.requests_success += 1
            self._health.last_success = datetime.now()

    def _record_failure(self, error: str) -> None:
        """Record failed request."""
        with self._health_lock:
            self._health.requests_total += 1
            self._health.requests_failed += 1
            self._health.last_failure = datetime.now()
            self._health.last_error = error

    def _record_parse_error(self, error: str) -> None:
        """Record parse error (structure changed?)."""
        with self._health_lock:
            self._health.parse_errors += 1
            self._health.last_error = error
        logger.warning("[CRICBUZZ] Parse error: %s", error)

    def _record_structure_warning(self, missing_field: str) -> None:
        """Record structure warning (field not found)."""
        with self._health_lock:
            self._health.structure_warnings += 1
            if missing_field not in self._health.missing_fields:
                self._health.missing_fields.append(missing_field)
        logger.warning("[CRICBUZZ] Structure warning: missing field '%s'", missing_field)

    def supports_league(self, league: str) -> bool:
        """Check if we support this league.

        Supports both:
        1. League codes mapped in leagues table (provider='cricbuzz')
        2. Direct series ID paths like '9241/indian-premier-league-2026'
           (used when Cricbuzz is fallback for TSDB leagues)
        """
        # Direct series ID path - always supported
        if "/" in league and league.split("/")[0].isdigit():
            return True

        # Normal path: check league mapping
        if not self._league_mapping_source:
            return False
        return self._league_mapping_source.supports_league(league, "cricbuzz")

    def get_league_config(self, league: str) -> tuple[str, str] | None:
        """Get (series_id, series_slug) for a league.

        Uses LeagueMappingSource to get series_id from provider_league_id.
        Format: 'series_id/url-slug' (e.g., '9241/indian-premier-league-2026')

        Also handles direct series ID paths when used as fallback provider.
        If league looks like 'series_id/slug', it's used directly.
        """
        # Check if league is already a series ID path (used when Cricbuzz is fallback)
        # Format: '9241/indian-premier-league-2026'
        if "/" in league and league.split("/")[0].isdigit():
            parts = league.split("/", 1)
            series_id = parts[0]
            slug = parts[1]
            logger.debug("[CRICBUZZ] Using direct series ID path: %s/%s", series_id, slug)
            return (series_id, slug)

        # Normal path: lookup from leagues table
        if not self._league_mapping_source:
            return None

        mapping = self._league_mapping_source.get_mapping(league, "cricbuzz")
        if not mapping:
            return None

        # Parse provider_league_id format: 'series_id/url-slug'
        provider_id = mapping.provider_league_id
        if "/" in provider_id:
            parts = provider_id.split("/", 1)
            series_id = parts[0]
            slug = parts[1]
        else:
            # Fallback: just series ID, generate slug from display name
            series_id = provider_id
            slug = mapping.display_name.lower().replace(" ", "-").replace(",", "")

        return (series_id, slug)

    def get_sport(self, league: str) -> str:
        """Get sport name for a league."""
        return "Cricket"  # All Cricbuzz leagues are cricket

    def _fetch_page(self, url: str) -> str | None:
        """Fetch HTML page with retry logic."""
        for attempt in range(self._retry_count):
            try:
                client = self._get_client()
                response = client.get(url)
                response.raise_for_status()
                self._record_success()
                return response.text
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"Cricbuzz HTTP {e.response.status_code} for {url} "
                    f"(attempt {attempt + 1}/{self._retry_count})"
                )
                if attempt < self._retry_count - 1:
                    continue
                self._record_failure(f"HTTP {e.response.status_code}")
                return None
            except (httpx.RequestError, RuntimeError, OSError) as e:
                logger.warning(
                    f"Cricbuzz request failed: {e} (attempt {attempt + 1}/{self._retry_count})"
                )
                if attempt < self._retry_count - 1:
                    continue
                self._record_failure(str(e))
                return None

        return None

    def _extract_json_data(self, html: str) -> dict | None:
        """Extract embedded JSON data from Cricbuzz HTML.

        Cricbuzz uses React/Next.js with JSON data embedded in
        self.__next_f.push() calls. Data is JSON-escaped with \\".
        """
        try:
            # Find the escaped JSON data blocks
            # The JSON is escaped with \" instead of " in the HTML
            escaped_matches_list = '\\"matchesList\\"'
            escaped_series_matches = '\\"seriesMatches\\"'

            # Try matchesList first (live scores page)
            idx = html.find(escaped_matches_list)
            if idx == -1:
                # Try seriesMatches (schedule pages)
                idx = html.find(escaped_series_matches)

            if idx == -1:
                self._record_parse_error("No match data found in HTML")
                return None

            # Extract a large chunk and unescape
            # Large series like SA20 have 77+ matches, pages can be 250KB+
            chunk_size = 500000  # 500KB to capture full series schedules
            end_idx = min(len(html), idx + chunk_size)
            chunk = html[idx:end_idx]

            # Unescape JSON - convert \" to " and \\ to \
            unescaped = chunk.replace('\\"', '"').replace("\\\\", "\\")

            # Parse the match data
            return self._parse_match_data(unescaped)

        except Exception as e:
            self._record_parse_error(f"JSON extraction failed: {e}")
            return None

    def _parse_match_data(self, data: str) -> dict | None:
        """Parse match data from unescaped JSON string."""
        matches = []

        # Use manual extraction which is more reliable for nested JSON
        # Find all matchId occurrences and extract surrounding data
        match_id_pattern = r'"matchId":(\d+)'

        seen_ids: set = set()
        for match in re.finditer(match_id_pattern, data):
            match_id = match.group(1)
            if match_id in seen_ids:
                continue
            seen_ids.add(match_id)

            # Find the start of this match's matchInfo block
            start = data.rfind('"matchInfo":{', 0, match.start())
            if start == -1:
                continue

            # Extract the matchInfo using manual field extraction
            # This is more reliable than trying to parse nested JSON with regex
            match_info = self._extract_match_info_manually(data[start : start + 2000])
            if match_info:
                matches.append(match_info)

        if not matches:
            self._record_parse_error("No valid matches parsed")
            return None

        return {"matches": matches}

    def _extract_match_info_manually(self, data: str) -> dict | None:
        """Manually extract match info when JSON parsing fails."""
        try:
            info = {}

            # Extract numeric fields (may be quoted strings or unquoted numbers)
            # Cricbuzz sends startDate/endDate as strings: "startDate":"1767483000000"
            for field in ["matchId", "seriesId", "startDate", "endDate"]:
                # Try quoted string first (more common for dates), then unquoted number
                match = re.search(rf'"{field}":"(\d+)"', data)
                if not match:
                    match = re.search(rf'"{field}":(\d+)', data)
                if match:
                    info[field] = int(match.group(1))

            # Extract string fields
            for field in ["seriesName", "matchDesc", "matchFormat", "state", "status"]:
                match = re.search(rf'"{field}":"([^"]*)"', data)
                if match:
                    info[field] = match.group(1)

            # Extract team1 (with optional imageId for logos)
            team1_match = re.search(
                r'"team1":\{[^}]*"teamId":(\d+)[^}]*"teamName":"([^"]*)"[^}]*"teamSName":"([^"]*)"[^}]*"imageId":(\d+)',
                data,
            )
            if team1_match:
                info["team1"] = {
                    "teamId": int(team1_match.group(1)),
                    "teamName": team1_match.group(2),
                    "teamSName": team1_match.group(3),
                    "imageId": int(team1_match.group(4)),
                }
            else:
                # Fallback without imageId
                team1_match = re.search(
                    r'"team1":\{[^}]*"teamId":(\d+)[^}]*"teamName":"([^"]*)"[^}]*"teamSName":"([^"]*)"',
                    data,
                )
                if team1_match:
                    info["team1"] = {
                        "teamId": int(team1_match.group(1)),
                        "teamName": team1_match.group(2),
                        "teamSName": team1_match.group(3),
                    }

            # Extract team2 (with optional imageId for logos)
            team2_match = re.search(
                r'"team2":\{[^}]*"teamId":(\d+)[^}]*"teamName":"([^"]*)"[^}]*"teamSName":"([^"]*)"[^}]*"imageId":(\d+)',
                data,
            )
            if team2_match:
                info["team2"] = {
                    "teamId": int(team2_match.group(1)),
                    "teamName": team2_match.group(2),
                    "teamSName": team2_match.group(3),
                    "imageId": int(team2_match.group(4)),
                }
            else:
                # Fallback without imageId
                team2_match = re.search(
                    r'"team2":\{[^}]*"teamId":(\d+)[^}]*"teamName":"([^"]*)"[^}]*"teamSName":"([^"]*)"',
                    data,
                )
                if team2_match:
                    info["team2"] = {
                        "teamId": int(team2_match.group(1)),
                        "teamName": team2_match.group(2),
                        "teamSName": team2_match.group(3),
                    }

            # Extract venue
            venue_match = re.search(r'"ground":"([^"]*)"[^}]*"city":"([^"]*)"', data)
            if venue_match:
                info["venueInfo"] = {
                    "ground": venue_match.group(1),
                    "city": venue_match.group(2),
                }

            # Validate we got minimum required fields
            if info.get("matchId") and info.get("team1") and info.get("team2"):
                return info

            return None

        except Exception as e:
            logger.debug("[CRICBUZZ] Failed to parse match info: %s", e)
            return None

    def get_live_matches(self) -> list[dict]:
        """Get all live and recent matches.

        Returns:
            List of match dicts from live-scores page
        """
        cache_key = make_cache_key("cricbuzz", "live")
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CRICBUZZ] Cache hit: live")
            return cached

        url = f"{CRICBUZZ_BASE_URL}/cricket-match/live-scores"
        html = self._fetch_page(url)
        if not html:
            return []

        data = self._extract_json_data(html)
        if not data:
            return []

        matches = data.get("matches", [])
        if matches:
            self._cache.set(cache_key, matches, CACHE_TTL_LIVE)
            logger.debug("[CRICBUZZ] Cached %d live matches", len(matches))

        return matches

    def get_series_schedule(self, series_id: str, series_slug: str) -> list[dict]:
        """Get full schedule for a series.

        Args:
            series_id: Cricbuzz series ID
            series_slug: URL slug (e.g., "indian-premier-league-2025")

        Returns:
            List of match dicts for this specific series only
        """
        cache_key = make_cache_key("cricbuzz", "schedule", series_id)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CRICBUZZ] Cache hit: schedule %s", series_id)
            return cached

        url = f"{CRICBUZZ_BASE_URL}/cricket-series/{series_id}/{series_slug}/matches"
        html = self._fetch_page(url)
        if not html:
            return []

        data = self._extract_json_data(html)
        if not data:
            return []

        # Filter for this series only (page may contain featured matches from other series)
        series_id_int = int(series_id)
        matches = [m for m in data.get("matches", []) if m.get("seriesId") == series_id_int]

        if matches:
            self._cache.set(cache_key, matches, CACHE_TTL_SCHEDULE)
            logger.debug("[CRICBUZZ] Cached %d matches for series %s", len(matches), series_id)

        return matches

    def get_series_teams(self, series_id: str, series_slug: str) -> list[dict]:
        """Get all teams in a series.

        Extracts unique teams from series schedule.

        Returns:
            List of team dicts with teamId, teamName, teamSName, imageId
        """
        cache_key = make_cache_key("cricbuzz", "teams", series_id)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CRICBUZZ] Cache hit: teams %s", series_id)
            return cached

        # Get schedule and extract unique teams
        matches = self.get_series_schedule(series_id, series_slug)
        if not matches:
            return []

        teams_map: dict[int, dict] = {}
        for match in matches:
            for team_key in ["team1", "team2"]:
                team = match.get(team_key, {})
                team_id = team.get("teamId")
                if team_id and team_id not in teams_map:
                    teams_map[team_id] = team

        teams = list(teams_map.values())
        if teams:
            self._cache.set(cache_key, teams, CACHE_TTL_TEAMS)
            logger.debug("[CRICBUZZ] Cached %d teams for series %s", len(teams), series_id)

        return teams

    def get_events_by_date(self, league: str, target_date: str) -> list[dict]:
        """Get matches for a specific date.

        Args:
            league: League code (mapped via LeagueMappingSource)
            target_date: Date string "YYYY-MM-DD"

        Returns:
            List of match dicts for that date
        """
        config = self.get_league_config(league)
        if not config:
            logger.warning("[CRICBUZZ] Unknown league: %s", league)
            return []

        series_id, series_slug = config
        schedule = self.get_series_schedule(series_id, series_slug)

        # Filter by date
        # startDate is Unix timestamp in milliseconds
        matches = []
        for match in schedule:
            start_ts = match.get("startDate")
            if not start_ts:
                continue

            # Convert to date string
            if isinstance(start_ts, str):
                start_ts = int(start_ts)

            match_date = datetime.utcfromtimestamp(start_ts / 1000).strftime("%Y-%m-%d")
            if match_date == target_date:
                matches.append(match)

        return matches

    # Days to look back for .last variable resolution
    DAYS_BACK = 7

    def get_team_schedule(self, league: str, team_id: str, days_ahead: int = 14) -> list[dict]:
        """Get schedule for a specific team including past and future matches.

        Includes past matches (DAYS_BACK) for .last template variable resolution.

        Args:
            league: League code
            team_id: Cricbuzz team ID
            days_ahead: Number of days to look ahead

        Returns:
            List of match dicts for this team (sorted by date)
        """
        from datetime import timedelta

        config = self.get_league_config(league)
        if not config:
            return []

        series_id, series_slug = config
        schedule = self.get_series_schedule(series_id, series_slug)

        today = datetime.utcnow()
        start_date = today - timedelta(days=self.DAYS_BACK)
        end_date = today + timedelta(days=days_ahead)
        team_id_int = int(team_id)

        team_games = []
        for match in schedule:
            # Check if team is in this match
            team1_id = match.get("team1", {}).get("teamId")
            team2_id = match.get("team2", {}).get("teamId")

            if team_id_int not in (team1_id, team2_id):
                continue

            # Check date is within range (includes past games)
            start_ts = match.get("startDate")
            if not start_ts:
                continue

            if isinstance(start_ts, str):
                start_ts = int(start_ts)

            match_dt = datetime.utcfromtimestamp(start_ts / 1000)
            if start_date <= match_dt <= end_date:
                team_games.append(match)

        # Sort by date
        team_games.sort(key=lambda m: m.get("startDate", 0))
        return team_games

    def health_check(self) -> dict:
        """Get health check status.

        Returns dict with:
        - status: "healthy", "degraded", or "unhealthy"
        - stats: HealthStats as dict
        - message: Human-readable status message

        Use this to detect when site structure changes.
        """
        with self._health_lock:
            stats = {
                "requests_total": self._health.requests_total,
                "requests_success": self._health.requests_success,
                "requests_failed": self._health.requests_failed,
                "parse_errors": self._health.parse_errors,
                "structure_warnings": self._health.structure_warnings,
                "last_success": (
                    self._health.last_success.isoformat() if self._health.last_success else None
                ),
                "last_failure": (
                    self._health.last_failure.isoformat() if self._health.last_failure else None
                ),
                "last_error": self._health.last_error,
                "missing_fields": self._health.missing_fields.copy(),
            }

        # Determine health status
        if self._health.requests_total == 0:
            status = "unknown"
            message = "No requests made yet"
        elif self._health.parse_errors > 5:
            status = "unhealthy"
            message = (
                f"Multiple parse errors ({self._health.parse_errors}) - "
                "site structure may have changed"
            )
            logger.critical(
                f"Cricbuzz health: UNHEALTHY - {message}. "
                f"Missing fields: {self._health.missing_fields}"
            )
        elif self._health.structure_warnings > 10:
            status = "degraded"
            message = (
                f"Structure warnings ({self._health.structure_warnings}) - some fields missing"
            )
            logger.warning("[CRICBUZZ] Health: DEGRADED - %s", message)
        elif self._health.requests_failed > self._health.requests_success:
            status = "degraded"
            message = "More failures than successes"
        else:
            status = "healthy"
            message = "Operating normally"

        return {"status": status, "message": message, "stats": stats}

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

    def discover_active_series(self) -> dict[str, str]:
        """Discover current active series IDs from Cricbuzz.

        Scrapes the league listing page to find current series.
        Returns a dict mapping slug patterns to full series paths.

        Example return:
        {
            "indian-premier-league": "9241/indian-premier-league-2026",
            "big-bash-league": "10289/big-bash-league-2025-26",
            "bpl": "11328/bpl-2025-26",
            "sa20": "10394/sa20-2025-26",
        }

        Use this during cache refresh to auto-update provider_league_id values.
        """
        url = f"{CRICBUZZ_BASE_URL}/cricket-series"
        html = self._fetch_page(url)
        if not html:
            logger.warning("[CRICBUZZ] Failed to fetch league listing for auto-discovery")
            return {}

        # Extract all series paths from the page
        # Format: cricket-series/12345/slug-name-year
        pattern = r"cricket-series/(\d+)/([a-z0-9-]+)"
        matches = re.findall(pattern, html.lower())

        # Build mapping: base slug -> full path
        # Base slug = slug without year suffix (e.g., "indian-premier-league" from "indian-premier-league-2026")  # noqa: E501
        series_map: dict[str, str] = {}

        for series_id, slug in matches:
            # Remove year suffix patterns like "-2025", "-2025-26", "-2026"
            base_slug = re.sub(r"-\d{4}(-\d{2})?$", "", slug)

            full_path = f"{series_id}/{slug}"

            # Keep the most recent (highest series_id) for each base slug
            if base_slug not in series_map:
                series_map[base_slug] = full_path
            else:
                # Compare series IDs - higher is newer
                existing_id = int(series_map[base_slug].split("/")[0])
                if int(series_id) > existing_id:
                    series_map[base_slug] = full_path

        logger.info("[CRICBUZZ] Auto-discovery found %d active series", len(series_map))
        return series_map

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
