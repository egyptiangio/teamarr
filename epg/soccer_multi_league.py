"""
Soccer Multi-League Support

Provides reverse-lookup cache for soccer teams to find all leagues they play in.
Solves the problem of soccer teams playing in multiple competitions simultaneously.

Usage:
    from epg.soccer_multi_league import SoccerMultiLeague

    # Get leagues for a team
    leagues = SoccerMultiLeague.get_team_leagues("364")  # Liverpool
    # Returns: ['eng.1', 'uefa.champions', 'eng.fa', 'eng.league_cup', ...]

    # Refresh the cache
    SoccerMultiLeague.refresh_cache()
"""

import requests
from requests.adapters import HTTPAdapter
import threading
import time
import re
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from database import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)


# Module-level HTTP session for connection pooling
_soccer_session: Optional[requests.Session] = None
_soccer_session_lock = threading.Lock()


def _get_soccer_session() -> requests.Session:
    """Get or create a shared HTTP session for soccer API calls."""
    global _soccer_session
    if _soccer_session is None:
        with _soccer_session_lock:
            if _soccer_session is None:
                session = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=100,  # Match MAX_WORKERS for parallel league fetching
                    max_retries=0
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)
                _soccer_session = session
                logger.debug("Soccer HTTP session created with connection pooling")
    return _soccer_session


# =============================================================================
# CONSTANTS
# =============================================================================

# ESPN API endpoints
ESPN_LEAGUES_URL = "https://sports.core.api.espn.com/v2/sports/soccer/leagues?limit=500"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams"
ESPN_LEAGUE_DETAIL_URL = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/{slug}"

# League slugs to skip (junk leagues or handled elsewhere)
SKIP_LEAGUE_SLUGS = {
    'nonfifa',
    'usa.ncaa.m.1',   # NCAA Men's Soccer - handled via TeamLeagueCache as 'ncaas'
    'usa.ncaa.w.1',   # NCAA Women's Soccer - handled via TeamLeagueCache as 'ncaaws'
}
SKIP_LEAGUE_PATTERNS = ['not_used']

# League tag detection patterns
# Each pattern maps to one or more tags that should be applied
# Tags: mens, womens, domestic, continental, world, club, national, cup, league, qualifier, friendly, youth
#
# Pattern matching notes:
# - Patterns starting with ^ or containing $ are treated as regex
# - Other patterns are simple substring matches
# - Order matters within each tag's patterns (first match wins for that tag)
LEAGUE_TAG_PATTERNS = {
    # Gender detection - checked first, womens prevents 'mens' default
    'womens': [
        '.w.', 'women', 'wchampions', 'wchamps', 'weuro', 'wwc', 'wworldq', 'wworld',
        '.w$', '_w_', '_w$', 'femenina', 'shebelieves', 'arnold.clark',
    ],
    'youth': ['u17', 'u19', 'u20', 'u21', 'u23', 'youth', 'junior', 'toulon'],

    # Competition scope - domestic only for country-code leagues
    'domestic': [
        r'^[a-z]{3}\.[1-9]$',           # eng.1, esp.1, ger.1
        r'^[a-z]{3}\.[1-9][0-9]$',      # eng.10 (exactly 2 digits after dot)
        r'^[a-z]{3}\.w\.[1-9]$',        # eng.w.1 (women's domestic)
        r'^[a-z]{3}\.(fa|cup|pokal|coupe|coppa|league_cup|charity|trophy|camp\.|copa|supercopa)$',  # domestic cups
        r'^[a-z]{3}\.[a-z_]+$',         # Other domestic (eng.fa, ger.dfb_pokal)
    ],
    'continental': [
        'uefa.', 'conmebol.', 'concacaf.', 'afc.', 'caf.', 'ofc.',
        'aff.', 'saff.', 'cosafa',
    ],
    'world': [
        r'^fifa\.world', r'^fifa\.cwc', r'^fifa\.club', r'^fifa\.olympics', r'^fifa\.w\.olympics',
        'fifa.confederations', r'^global\.',
    ],

    # Competition type
    'cup': [
        r'\.fa$', r'\.cup$', r'_cup$', 'pokal', 'coupe', 'coppa',
        'league_cup', 'charity', 'shield', 'supercup', 'super_cup',
        'trophy', 'trofeo', 'recopa', 'finalissima', 'challenge_cup',
        r'^[a-z]{3}\.copa$',            # arg.copa, col.copa etc
    ],
    'league': [
        r'^[a-z]{3}\.[1-9]',            # Domestic leagues (eng.1, fra.2)
        r'\.liga$', 'serie', 'bundesliga', 'premier',
        'eredivisie', r'^[a-z]{3}\.superliga$',
    ],
    'qualifier': [
        'qual', '_qual', 'worldq', 'olympicsq', 'playoff',
    ],
    'friendly': [
        'friendly', 'world_challenge', 'preseason', 'emirates_cup',
    ],

    # Club vs National - be more specific to avoid false positives
    'club': [
        # Club-specific continental competitions
        'uefa.champions', 'uefa.wchampions', 'uefa.europa', 'uefa.conf',
        'conmebol.libertadores', 'conmebol.sudamericana',
        'afc.champions', 'caf.champions', 'caf.confed',
        'concacaf.champions', 'concacaf.leagues',
        # Club-specific keywords
        r'\.club', r'^club\.', 'cwc',
        # Domestic club leagues (country.number pattern)
        r'^[a-z]{3}\.[1-9]',
        # Domestic cups are club competitions
        r'^[a-z]{3}\.(fa|cup|pokal|coupe|coppa|league_cup|charity|trophy|copa|supercopa)',
    ],
    'national': [
        # National team continental competitions
        'uefa.euro', 'uefa.weuro', 'uefa.nations', 'uefa.w.nations',
        'concacaf.gold', 'concacaf.w.gold', 'concacaf.nations',
        'conmebol.america', 'caf.nations', 'afc.asian',
        # FIFA national team competitions
        r'^fifa\.world', r'^fifa\.wwc', r'^fifa\.olympics', r'^fifa\.w\.olympics',
        'confederations',
        # Continental national team championships
        'aff.championship', 'saff.championship', 'caf.championship',
        'cosafa',
    ],
}

# Thread pool size for parallel fetching
MAX_WORKERS = 100


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class LeagueInfo:
    """League metadata from cache."""
    slug: str
    name: str
    abbrev: str
    tags: List[str]  # Multiple tags: ['domestic', 'club', 'league', 'mens']
    logo_url: str
    team_count: int

    @property
    def category(self) -> str:
        """Legacy: return primary category for backwards compatibility."""
        # Priority order for primary category
        if 'world' in self.tags:
            return 'world_club' if 'club' in self.tags else 'world_national'
        if 'continental' in self.tags:
            return 'continental_club' if 'club' in self.tags else 'continental_national'
        if 'cup' in self.tags:
            return 'domestic_cup'
        if 'friendly' in self.tags:
            return 'friendly'
        if 'qualifier' in self.tags:
            return 'qualifier'
        if 'domestic' in self.tags:
            return 'domestic'
        return 'other'


@dataclass
class TeamLeagueInfo:
    """Team's league membership info from cache."""
    team_id: str
    team_name: str
    team_type: str  # 'club' or 'national'
    leagues: List[str]
    # Note: default_league is NOT cached - use get_team_default_league() for authoritative value


@dataclass
class CacheStats:
    """Cache refresh statistics."""
    last_refresh: Optional[datetime]
    leagues_processed: int
    teams_indexed: int
    refresh_duration: float
    is_stale: bool
    staleness_days: int


# =============================================================================
# MAIN CLASS
# =============================================================================

class SoccerMultiLeague:
    """
    Manages the soccer multi-league cache.

    All methods are static/class methods - no instance needed.
    """

    # ==========================================================================
    # PUBLIC API: Cache Queries
    # ==========================================================================

    @classmethod
    def get_team_leagues(cls, espn_team_id: str) -> List[str]:
        """
        Get all league slugs for a soccer team.

        Args:
            espn_team_id: ESPN team ID (e.g., "364" for Liverpool)

        Returns:
            List of league slugs (e.g., ['eng.1', 'uefa.champions', 'eng.fa'])
            Returns empty list if team not found in cache.
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT league_slug FROM soccer_team_leagues WHERE espn_team_id = ?",
                (str(espn_team_id),)
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    @classmethod
    def get_team_info(cls, espn_team_id: str) -> Optional[TeamLeagueInfo]:
        """
        Get full team info including all leagues from cache.

        Note: default_league is NOT included - use get_team_default_league()
        to fetch the authoritative value from ESPN when needed.

        Args:
            espn_team_id: ESPN team ID

        Returns:
            TeamLeagueInfo dataclass or None if not found
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT league_slug, team_name, team_type
                   FROM soccer_team_leagues WHERE espn_team_id = ?""",
                (str(espn_team_id),)
            )
            rows = cursor.fetchall()

            if not rows:
                return None

            first = rows[0]
            return TeamLeagueInfo(
                team_id=str(espn_team_id),
                team_name=first[1] or '',
                team_type=first[2] or 'club',
                leagues=[row[0] for row in rows]
            )
        finally:
            conn.close()

    @classmethod
    def get_league_info(cls, league_slug: str) -> Optional[LeagueInfo]:
        """
        Get league metadata by slug.

        Args:
            league_slug: League slug (e.g., "eng.1", "uefa.champions")

        Returns:
            LeagueInfo dataclass or None if not found
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT league_slug, league_name, league_abbrev, league_tags, league_logo_url, team_count FROM soccer_leagues_cache WHERE league_slug = ?",
                (league_slug,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            # Parse tags from JSON (backwards compat: may be old category string)
            tags_raw = row[3] or '[]'
            try:
                tags = json.loads(tags_raw)
                if isinstance(tags, str):
                    # Old format: single category string - convert to tags list
                    tags = [tags] if tags else []
            except json.JSONDecodeError:
                # Old format: plain string category
                tags = [tags_raw] if tags_raw else []

            return LeagueInfo(
                slug=row[0],
                name=row[1] or league_slug,
                abbrev=row[2] or '',
                tags=tags,
                logo_url=row[4] or '',
                team_count=row[5] or 0
            )
        finally:
            conn.close()

    @classmethod
    def get_team_default_league(cls, espn_team_id: str, any_known_league: str = 'eng.1') -> Optional[str]:
        """
        Fetch the authoritative default league for a team from ESPN.

        This makes a direct API call to get the team's actual default league,
        not the cached value which may be stale or incorrect.

        Args:
            espn_team_id: ESPN team ID (e.g., "364" for Liverpool)
            any_known_league: Any league the team plays in (needed for API path)

        Returns:
            League slug (e.g., "eng.1") or None if not found
        """
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{any_known_league}/teams/{espn_team_id}"

        try:
            resp = _get_soccer_session().get(url, timeout=10)
            if resp.status_code != 200:
                logger.debug(f"Failed to fetch team {espn_team_id}: HTTP {resp.status_code}")
                return None

            data = resp.json()
            team = data.get('team', {})
            default_league = team.get('defaultLeague', {})

            if isinstance(default_league, dict) and default_league.get('slug'):
                return default_league['slug']

            return None

        except Exception as e:
            logger.debug(f"Error fetching default league for team {espn_team_id}: {e}")
            return None

    @classmethod
    def get_league_name(cls, league_slug: str) -> str:
        """
        Get human-readable league name.

        Args:
            league_slug: League slug (e.g., "eng.1")

        Returns:
            League name (e.g., "English Premier League") or slug if not found
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT league_name FROM soccer_leagues_cache WHERE league_slug = ?",
                (league_slug,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else league_slug
        finally:
            conn.close()

    @classmethod
    def get_league_logo(cls, league_slug: str) -> Optional[str]:
        """
        Get league logo URL.

        Args:
            league_slug: League slug

        Returns:
            Logo URL or None if not found
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT league_logo_url FROM soccer_leagues_cache WHERE league_slug = ?",
                (league_slug,)
            )
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    @classmethod
    def get_cache_stats(cls) -> CacheStats:
        """
        Get cache status and statistics.

        Returns:
            CacheStats dataclass with refresh info
        """
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM soccer_cache_meta WHERE id = 1")
            row = cursor.fetchone()

            last_refresh = None
            staleness_days = 999

            if row and row[1]:  # last_full_refresh
                try:
                    last_refresh = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
                    staleness_days = (datetime.now(last_refresh.tzinfo) - last_refresh).days
                except:
                    pass

            return CacheStats(
                last_refresh=last_refresh,
                leagues_processed=row[2] or 0 if row else 0,
                teams_indexed=row[3] or 0 if row else 0,
                refresh_duration=row[4] or 0 if row else 0,
                is_stale=staleness_days > 7,
                staleness_days=staleness_days
            )
        finally:
            conn.close()

    @classmethod
    def is_cache_empty(cls) -> bool:
        """Check if cache has any data."""
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM soccer_team_leagues")
            row = cursor.fetchone()
            return row[0] == 0 if row else True
        finally:
            conn.close()

    # ==========================================================================
    # PUBLIC API: Cache Refresh
    # ==========================================================================

    @classmethod
    def refresh_cache(cls, progress_callback=None) -> Dict[str, Any]:
        """
        Refresh the entire soccer league cache.

        Fetches teams from all 244 ESPN soccer leagues and builds
        the reverse lookup map. Takes ~5 seconds with 50 threads.

        Args:
            progress_callback: Optional callback(message, percent) for progress updates

        Returns:
            Dict with refresh statistics:
            {
                'success': True/False,
                'leagues_processed': 244,
                'teams_indexed': 3413,
                'duration_seconds': 4.9,
                'error': None or error message
            }
        """
        start_time = time.time()

        def report(msg, pct):
            logger.info(f"Soccer cache refresh: {msg}")
            if progress_callback:
                progress_callback(msg, pct)

        try:
            report("Fetching league list from ESPN...", 5)

            # Step 1: Get all league slugs
            league_slugs = cls._fetch_all_league_slugs()
            if not league_slugs:
                return {'success': False, 'error': 'Failed to fetch league list'}

            report(f"Found {len(league_slugs)} leagues, fetching teams...", 15)

            # Step 2: Fetch teams from all leagues in parallel
            team_to_leagues, league_metadata = cls._fetch_all_teams(
                league_slugs,
                lambda msg, pct: report(msg, 15 + int(pct * 0.7))  # 15-85%
            )

            report(f"Indexed {len(team_to_leagues)} teams, saving to database...", 90)

            # Step 3: Save to database
            cls._save_cache(team_to_leagues, league_metadata)

            # Step 4: Update metadata
            duration = time.time() - start_time
            cls._update_cache_meta(len(league_slugs), len(team_to_leagues), duration)

            # Step 5: Clear the slug-to-code mapping cache so it gets rebuilt
            from database import clear_soccer_slug_mapping_cache
            clear_soccer_slug_mapping_cache()

            report(f"Cache refresh complete: {len(team_to_leagues)} teams in {duration:.1f}s", 100)

            return {
                'success': True,
                'leagues_processed': len(league_slugs),
                'teams_indexed': len(team_to_leagues),
                'duration_seconds': duration,
                'error': None
            }

        except Exception as e:
            logger.error(f"Soccer cache refresh failed: {e}")
            return {
                'success': False,
                'leagues_processed': 0,
                'teams_indexed': 0,
                'duration_seconds': time.time() - start_time,
                'error': str(e)
            }

    @classmethod
    def refresh_if_needed(cls, max_age_days: int = 7) -> bool:
        """
        Refresh cache if it's older than max_age_days.

        Args:
            max_age_days: Maximum cache age before refresh

        Returns:
            True if refresh was performed, False otherwise
        """
        stats = cls.get_cache_stats()

        if stats.staleness_days >= max_age_days or cls.is_cache_empty():
            logger.info(f"Soccer cache is {stats.staleness_days} days old, refreshing...")
            result = cls.refresh_cache()
            return result['success']

        return False

    # ==========================================================================
    # PRIVATE: Fetching Logic
    # ==========================================================================

    @classmethod
    def _fetch_all_league_slugs(cls) -> List[str]:
        """Fetch all soccer league slugs from ESPN."""
        try:
            resp = _get_soccer_session().get(ESPN_LEAGUES_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            league_refs = data.get('items', [])
            slugs = []

            # Fetch each league's metadata to get slug
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(cls._fetch_league_slug, ref['$ref']): ref
                    for ref in league_refs
                }

                for future in as_completed(futures):
                    slug = future.result()
                    if slug and cls._should_include_league(slug):
                        slugs.append(slug)

            logger.info(f"Found {len(slugs)} valid soccer league slugs")
            return slugs

        except Exception as e:
            logger.error(f"Failed to fetch league slugs: {e}")
            return []

    @classmethod
    def _fetch_league_slug(cls, ref_url: str) -> Optional[str]:
        """Fetch a single league's slug from its ref URL."""
        try:
            resp = _get_soccer_session().get(ref_url, timeout=10)
            if resp.status_code == 200:
                return resp.json().get('slug')
        except:
            pass
        return None

    @classmethod
    def _should_include_league(cls, slug: str) -> bool:
        """Check if league should be included (filter junk)."""
        if slug in SKIP_LEAGUE_SLUGS:
            return False
        for pattern in SKIP_LEAGUE_PATTERNS:
            if pattern in slug:
                return False
        return True

    @classmethod
    def _fetch_all_teams(
        cls,
        league_slugs: List[str],
        progress_callback=None
    ) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
        """
        Fetch teams from all leagues in parallel.

        Returns:
            Tuple of (team_to_leagues dict, league_metadata dict)
        """
        team_to_leagues = {}  # team_id -> {name, type, default_league, leagues: []}
        league_metadata = {}  # slug -> {name, abbrev, logo, team_count}

        completed = 0
        total = len(league_slugs)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(cls._fetch_league_teams, slug): slug
                for slug in league_slugs
            }

            for future in as_completed(futures):
                slug = futures[future]
                completed += 1

                if progress_callback and completed % 20 == 0:
                    pct = (completed / total) * 100
                    progress_callback(f"Processed {completed}/{total} leagues", pct)

                try:
                    result = future.result()
                    if result:
                        league_meta, teams = result

                        # Store league metadata
                        league_metadata[slug] = league_meta

                        # Add teams to reverse lookup
                        for team in teams:
                            team_id = str(team['id'])

                            if team_id not in team_to_leagues:
                                team_to_leagues[team_id] = {
                                    'name': team['name'],
                                    'abbrev': team.get('abbrev', ''),
                                    'type': team['type'],
                                    'leagues': []
                                }

                            team_to_leagues[team_id]['leagues'].append(slug)

                except Exception as e:
                    logger.warning(f"Error processing league {slug}: {e}")

        return team_to_leagues, league_metadata

    @classmethod
    def _fetch_league_teams(cls, slug: str) -> Optional[Tuple[Dict, List[Dict]]]:
        """
        Fetch all teams from a single league.

        Returns:
            Tuple of (league_metadata, teams_list) or None on error
        """
        url = ESPN_TEAMS_URL.format(slug=slug)

        try:
            resp = _get_soccer_session().get(url, timeout=10)
            if resp.status_code != 200:
                return None

            data = resp.json()

            # Extract league metadata
            league_data = data.get('sports', [{}])[0].get('leagues', [{}])[0]
            league_meta = {
                'name': league_data.get('name', ''),
                'abbrev': league_data.get('abbreviation', ''),
                'logo': league_data.get('logos', [{}])[0].get('href', '') if league_data.get('logos') else '',
                'team_count': 0
            }

            # Extract teams
            teams_raw = league_data.get('teams', [])
            teams = []

            for t in teams_raw:
                team_data = t.get('team', {})
                if not team_data.get('id'):
                    continue

                # Detect team type (club vs national)
                name = team_data.get('displayName', team_data.get('name', ''))
                location = team_data.get('location', '')
                team_type = cls._detect_team_type(name, location)

                teams.append({
                    'id': team_data['id'],
                    'name': name,
                    'abbrev': team_data.get('abbreviation', ''),
                    'type': team_type,
                })

            league_meta['team_count'] = len(teams)

            return (league_meta, teams)

        except Exception as e:
            logger.debug(f"Failed to fetch teams from {slug}: {e}")
            return None

    @classmethod
    def _detect_team_type(cls, name: str, location: str) -> str:
        """
        Detect if team is club or national.

        National teams: location == name (both are the country)
        Club teams: location is city, name is team name

        Examples:
            - Liverpool: location="Liverpool", name="Liverpool" -> hmm, need to check default league
            - England: location="England", name="England" -> national
            - Manchester United: location="Manchester", name="Manchester United" -> club
        """
        # Normalize for comparison
        name_lower = name.lower().strip()
        location_lower = location.lower().strip()

        # If location equals name exactly, likely national team
        if name_lower == location_lower:
            # But check for common club cases where city == club name
            # e.g., "Liverpool" city and "Liverpool" club
            club_city_names = {
                'liverpool', 'chelsea', 'arsenal', 'everton',
                'brighton', 'fulham', 'brentford',
            }
            if name_lower in club_city_names:
                return 'club'
            return 'national'

        return 'club'

    @classmethod
    def _get_league_tags(cls, slug: str) -> List[str]:
        """
        Get all applicable tags for a league by its slug pattern.

        Returns list of tags: ['domestic', 'club', 'league', 'mens']
        """
        slug_lower = slug.lower()
        tags = []

        for tag, patterns in LEAGUE_TAG_PATTERNS.items():
            for pattern in patterns:
                # Check if pattern is a regex
                if pattern.startswith('^') or pattern.startswith('(') or '$' in pattern:
                    try:
                        if re.search(pattern, slug_lower):
                            tags.append(tag)
                            break  # Found match for this tag, move to next tag
                    except re.error:
                        # Invalid regex, treat as literal
                        if pattern in slug_lower:
                            tags.append(tag)
                            break
                elif pattern in slug_lower:
                    tags.append(tag)
                    break  # Found match for this tag, move to next tag

        # Add 'mens' if not womens or youth
        if 'womens' not in tags and 'youth' not in tags:
            tags.append('mens')

        # Add 'club' default for domestic leagues if neither club nor national tagged
        if 'domestic' in tags and 'club' not in tags and 'national' not in tags:
            tags.append('club')

        return tags

    # ==========================================================================
    # PRIVATE: Database Operations
    # ==========================================================================

    @classmethod
    def _save_cache(cls, team_to_leagues: Dict, league_metadata: Dict):
        """Save cache data to database."""
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat() + 'Z'

        try:
            # Clear old data
            cursor.execute("DELETE FROM soccer_team_leagues")
            cursor.execute("DELETE FROM soccer_leagues_cache")

            # Insert team-league mappings (dedupe leagues per team)
            for team_id, info in team_to_leagues.items():
                unique_leagues = list(set(info['leagues']))  # Dedupe
                for league_slug in unique_leagues:
                    cursor.execute("""
                        INSERT INTO soccer_team_leagues
                        (espn_team_id, league_slug, team_name, team_abbrev, team_type, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        team_id,
                        league_slug,
                        info['name'],
                        info.get('abbrev', ''),
                        info['type'],
                        now
                    ))

            # Insert league metadata
            for slug, meta in league_metadata.items():
                tags = cls._get_league_tags(slug)
                tags_json = json.dumps(tags)
                cursor.execute("""
                    INSERT INTO soccer_leagues_cache
                    (league_slug, league_name, league_abbrev, league_tags, league_logo_url, team_count, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    slug,
                    meta['name'],
                    meta['abbrev'],
                    tags_json,
                    meta['logo'],
                    meta['team_count'],
                    now
                ))

            conn.commit()
            logger.info(f"Saved {len(team_to_leagues)} teams and {len(league_metadata)} leagues to cache")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save cache: {e}")
            raise
        finally:
            conn.close()

    @classmethod
    def _update_cache_meta(cls, leagues: int, teams: int, duration: float):
        """Update cache metadata."""
        now = datetime.utcnow().isoformat() + 'Z'

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE soccer_cache_meta SET
                    last_full_refresh = ?,
                    leagues_processed = ?,
                    teams_indexed = ?,
                    refresh_duration_seconds = ?
                WHERE id = 1
            """, (now, leagues, teams, duration))
            conn.commit()
        finally:
            conn.close()


# =============================================================================
# HELPER FUNCTIONS (Module-level)
# =============================================================================

def get_soccer_team_leagues(espn_team_id: str) -> List[str]:
    """Convenience function for getting team's leagues."""
    return SoccerMultiLeague.get_team_leagues(espn_team_id)


def get_soccer_league_name(league_slug: str) -> str:
    """Convenience function for getting league name."""
    return SoccerMultiLeague.get_league_name(league_slug)


def get_soccer_league_logo(league_slug: str) -> Optional[str]:
    """Convenience function for getting league logo."""
    return SoccerMultiLeague.get_league_logo(league_slug)


def refresh_soccer_cache() -> Dict[str, Any]:
    """Convenience function for refreshing cache."""
    return SoccerMultiLeague.refresh_cache()
