"""
Team-to-League Cache for Non-Soccer Sports

Provides reverse-lookup cache for non-soccer teams to find their leagues.
Parallel structure to SoccerMultiLeague but for NHL, NBA, NFL, MLB, etc.

Usage:
    from epg.team_league_cache import TeamLeagueCache

    # Find candidate leagues for a team pair
    leagues = TeamLeagueCache.find_candidate_leagues("Predators", "Panthers")
    # Returns: ['nhl'] (both teams exist in NHL)

    # Get all leagues for a team name
    leagues = TeamLeagueCache.get_leagues_for_team("Tennessee")
    # Returns: ['nfl', 'ncaam', 'ncaaw', 'ncaaf'] (Titans, Volunteers, Lady Vols)

    # Refresh the cache
    TeamLeagueCache.refresh_cache()
"""

import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Any, Tuple
from dataclasses import dataclass

from database import get_connection
from api.espn_client import ESPNClient
from epg.league_config import get_league_config, is_soccer_league
from utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Non-soccer leagues to index (uses ESPN API slugs)
# These must match league_config.league_code values
NON_SOCCER_LEAGUES = [
    'nhl', 'nba', 'nba-development', 'wnba',
    'nfl', 'college-football',
    'mens-college-basketball', 'womens-college-basketball',
    'mlb',
    'mens-college-hockey', 'womens-college-hockey',
    'womens-college-volleyball', 'mens-college-volleyball',
    'usa.ncaa.m.1', 'usa.ncaa.w.1',  # NCAA soccer treated like other college sports
]

# Thread pool size for parallel fetching
MAX_WORKERS = 100


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CacheStats:
    """Cache refresh statistics."""
    last_refresh: Optional[datetime]
    leagues_processed: int
    teams_indexed: int
    is_stale: bool
    staleness_days: int


@dataclass
class TeamInfo:
    """Team information from cache."""
    espn_team_id: str
    team_name: str
    team_abbrev: str
    team_short_name: str
    sport: str
    leagues: List[str]


# =============================================================================
# MAIN CLASS
# =============================================================================

class TeamLeagueCache:
    """
    Manages the non-soccer team-to-league cache.

    All methods are static/class methods - no instance needed.
    Parallel structure to SoccerMultiLeague.
    """

    # ==========================================================================
    # PUBLIC API: Cache Queries
    # ==========================================================================

    @classmethod
    def get_leagues_for_team(cls, team_name: str) -> Set[str]:
        """
        Find all leagues a team name could belong to.

        Matches against team_name, team_abbrev, and team_short_name.
        Case-insensitive. Handles punctuation differences (apostrophes, periods)
        and common abbreviation variants (st/st., mt/mt., ft/ft.).

        Args:
            team_name: Team name to search (e.g., "Predators", "NSH", "Nashville")

        Returns:
            Set of league codes (e.g., {'nhl'})
        """
        if not team_name:
            return set()

        # Import here to avoid circular imports
        from epg.league_detector import get_abbreviation_variants

        # Get all abbreviation variants (with/without periods in st/st., mt/mt., etc.)
        variants = get_abbreviation_variants(team_name)
        results = set()

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Try each variant
            for variant in variants:
                cursor.execute("""
                    SELECT DISTINCT league_code FROM team_league_cache
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_abbrev) = ?
                       OR LOWER(team_short_name) LIKE ?
                """, (f'%{variant}%', variant, f'%{variant}%'))
                for row in cursor.fetchall():
                    results.add(row[0])

                if results:
                    return results

            # Fallback: Also try normalized search (strip ALL punctuation)
            # This handles cases like "mount st mary's" matching "Mount St. Mary's"
            import re
            team_normalized = re.sub(r"[.'`]", '', team_name.lower().strip())

            if team_normalized not in variants:
                cursor.execute("""
                    SELECT DISTINCT league_code FROM team_league_cache
                    WHERE REPLACE(REPLACE(REPLACE(LOWER(team_name), '.', ''), '''', ''), '`', '') LIKE ?
                       OR REPLACE(REPLACE(REPLACE(LOWER(team_short_name), '.', ''), '''', ''), '`', '') LIKE ?
                """, (f'%{team_normalized}%', f'%{team_normalized}%'))
                results = {row[0] for row in cursor.fetchall()}

            return results
        finally:
            conn.close()

    @classmethod
    def find_candidate_leagues(cls, team1: str, team2: str, enabled_leagues: List[str] = None) -> List[str]:
        """
        Find leagues where BOTH teams exist.

        Args:
            team1: First team name
            team2: Second team name
            enabled_leagues: Optional filter - only return these leagues

        Returns:
            List of league codes where both teams exist
        """
        leagues1 = cls.get_leagues_for_team(team1)
        leagues2 = cls.get_leagues_for_team(team2)

        # Intersection - leagues where both exist
        candidates = leagues1 & leagues2

        # Filter by enabled leagues if specified
        if enabled_leagues:
            enabled_set = set(enabled_leagues)
            candidates = candidates & enabled_set

        return list(candidates)

    @classmethod
    def get_team_info(cls, team_name: str) -> List[TeamInfo]:
        """
        Get full team info for all matches of a team name.

        Handles punctuation differences (apostrophes, periods)
        and common abbreviation variants (st/st., mt/mt., ft/ft.).

        Args:
            team_name: Team name to search

        Returns:
            List of TeamInfo objects for all matching teams
        """
        if not team_name:
            return []

        # Import here to avoid circular imports
        from epg.league_detector import get_abbreviation_variants

        # Get all abbreviation variants (with/without periods in st/st., mt/mt., etc.)
        variants = get_abbreviation_variants(team_name)
        rows = []

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Try each variant
            for variant in variants:
                cursor.execute("""
                    SELECT espn_team_id, team_name, team_abbrev, team_short_name, sport, league_code
                    FROM team_league_cache
                    WHERE LOWER(team_name) LIKE ?
                       OR LOWER(team_abbrev) = ?
                       OR LOWER(team_short_name) LIKE ?
                """, (f'%{variant}%', variant, f'%{variant}%'))
                variant_rows = cursor.fetchall()
                if variant_rows:
                    rows.extend(variant_rows)
                    break  # Found results, stop searching variants

            # Fallback: try normalized search (strip ALL punctuation)
            if not rows:
                import re
                team_normalized = re.sub(r"[.'`]", '', team_name.lower().strip())
                if team_normalized not in variants:
                    cursor.execute("""
                        SELECT espn_team_id, team_name, team_abbrev, team_short_name, sport, league_code
                        FROM team_league_cache
                        WHERE REPLACE(REPLACE(REPLACE(LOWER(team_name), '.', ''), '''', ''), '`', '') LIKE ?
                           OR REPLACE(REPLACE(REPLACE(LOWER(team_short_name), '.', ''), '''', ''), '`', '') LIKE ?
                    """, (f'%{team_normalized}%', f'%{team_normalized}%'))
                    rows = cursor.fetchall()

            # Group by team_id
            teams_by_id = {}
            for row in rows:
                team_id = row[0]
                if team_id not in teams_by_id:
                    teams_by_id[team_id] = {
                        'espn_team_id': team_id,
                        'team_name': row[1],
                        'team_abbrev': row[2] or '',
                        'team_short_name': row[3] or '',
                        'sport': row[4],
                        'leagues': []
                    }
                teams_by_id[team_id]['leagues'].append(row[5])

            return [TeamInfo(**info) for info in teams_by_id.values()]
        finally:
            conn.close()

    @classmethod
    def get_team_id_for_league(cls, team_name: str, league_code: str) -> Optional[str]:
        """
        Get the ESPN team ID for a specific team in a specific league.

        This is critical because the same team name (e.g., "Iowa State Cyclones")
        can have different ESPN IDs in different leagues:
        - ID 66 in college-football, mens-college-basketball, womens-college-volleyball
        - ID 20535 in usa.ncaa.w.1 (women's soccer)

        Uses tiered matching (parallel structure to get_soccer_team_ids_for_league):
        1. Direct match with abbreviation variants (st/st., mt/mt.)
        2. Accent-normalized match (Atletico -> Atlético)
        3. Number-stripped match (SV Elversberg -> SV 07 Elversberg)
        4. Article-stripped match (Atlético de Madrid -> Atlético Madrid)

        Args:
            team_name: Team name to search
            league_code: League to find the team in

        Returns:
            ESPN team ID if found, None otherwise
        """
        if not team_name or not league_code:
            return None

        # Import here to avoid circular imports
        from epg.league_detector import (
            get_abbreviation_variants, strip_accents, normalize_team_name
        )
        import re

        team_lower = team_name.lower().strip()
        team_accent_stripped = strip_accents(team_lower)
        team_stripped = normalize_team_name(team_lower, strip_articles=False)
        team_normalized = normalize_team_name(team_lower, strip_articles=True)

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Tier 1: Direct match with abbreviation variants (st/st., mt/mt.)
            for variant in get_abbreviation_variants(team_name):
                cursor.execute("""
                    SELECT espn_team_id, team_name
                    FROM team_league_cache
                    WHERE league_code = ?
                      AND (LOWER(team_name) LIKE ?
                           OR LOWER(team_abbrev) = ?
                           OR LOWER(team_short_name) LIKE ?
                           OR INSTR(?, LOWER(team_name)) > 0)
                    ORDER BY LENGTH(team_name) ASC
                    LIMIT 1
                """, (league_code, f'%{variant}%', variant, f'%{variant}%', variant))
                row = cursor.fetchone()
                if row:
                    logger.debug(f"Team '{team_name}' matched via direct lookup in {league_code}: {row[1]} (ID {row[0]})")
                    return str(row[0])

            # Tier 2: Accent-normalized match
            # Handles "Atletico" (stream) matching "Atlético" (DB)
            cursor.execute("""
                SELECT espn_team_id, team_name FROM team_league_cache
                WHERE league_code = ?
            """, (league_code,))
            for row in cursor.fetchall():
                db_normalized = strip_accents(row[1].lower())
                if team_accent_stripped in db_normalized or db_normalized in team_accent_stripped:
                    logger.debug(f"Team '{team_name}' matched via accent-stripping in {league_code}: {row[1]} (ID {row[0]})")
                    return str(row[0])

            # Tier 3: Number-stripped match
            # Handles "SV Elversberg" (stream) matching "SV 07 Elversberg" (DB)
            if team_stripped != team_lower:
                # SQL expression to strip numbers from team_name
                sql_strip_numbers = """
                    TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                            LOWER(team_name), '0', ''), '1', ''), '2', ''), '3', ''), '4', ''),
                        '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), '  ', ' '))
                """
                cursor.execute(f"""
                    SELECT espn_team_id, team_name FROM team_league_cache
                    WHERE league_code = ? AND (
                        {sql_strip_numbers} LIKE ?
                        OR {sql_strip_numbers} LIKE ?
                        OR INSTR(?, {sql_strip_numbers}) > 0
                    )
                    ORDER BY LENGTH(team_name) ASC
                    LIMIT 1
                """, (league_code, f'%{team_stripped}%', f'{team_stripped}%', team_stripped))
                row = cursor.fetchone()
                if row:
                    logger.debug(f"Team '{team_name}' matched via number-stripping in {league_code}: {row[1]} (ID {row[0]})")
                    return str(row[0])

            # Tier 4: Article-stripped match (de, del, da, do, di, du)
            if team_normalized != team_stripped:
                cursor.execute("""
                    SELECT espn_team_id, team_name FROM team_league_cache
                    WHERE league_code = ?
                """, (league_code,))
                for row in cursor.fetchall():
                    db_normalized = strip_accents(row[1].lower())
                    # Strip articles from DB value too
                    db_articles_stripped = re.sub(r'\b(de|del|da|do|di|du)\b', '', db_normalized, flags=re.I)
                    db_articles_stripped = re.sub(r'\s+', ' ', db_articles_stripped).strip()
                    if team_normalized in db_articles_stripped:
                        logger.debug(f"Team '{team_name}' matched via article-stripping in {league_code}: {row[1]} (ID {row[0]})")
                        return str(row[0])

            # No match found
            logger.debug(f"Team '{team_name}' not found in {league_code}")
            return None
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
            cursor.execute("SELECT * FROM team_league_cache_meta WHERE id = 1")
            row = cursor.fetchone()

            last_refresh = None
            staleness_days = 999

            if row and row[1]:  # last_refresh
                try:
                    last_refresh = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
                    staleness_days = (datetime.now(last_refresh.tzinfo) - last_refresh).days
                except:
                    pass

            return CacheStats(
                last_refresh=last_refresh,
                leagues_processed=row[2] or 0 if row else 0,
                teams_indexed=row[3] or 0 if row else 0,
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
            cursor.execute("SELECT COUNT(*) FROM team_league_cache")
            row = cursor.fetchone()
            return row[0] == 0 if row else True
        except:
            return True
        finally:
            conn.close()

    # ==========================================================================
    # PUBLIC API: Cache Refresh
    # ==========================================================================

    @classmethod
    def refresh_cache(cls, progress_callback=None) -> Dict[str, Any]:
        """
        Refresh the entire team-league cache.

        Fetches teams from all non-soccer leagues and builds
        the reverse lookup map.

        Args:
            progress_callback: Optional callback(message, percent) for progress updates

        Returns:
            Dict with refresh statistics:
            {
                'success': True/False,
                'leagues_processed': 12,
                'teams_indexed': 1847,
                'duration_seconds': 3.2,
                'error': None or error message
            }
        """
        start_time = time.time()

        def report(msg, pct):
            logger.info(f"Team-league cache refresh: {msg}")
            if progress_callback:
                progress_callback(msg, pct)

        try:
            report("Starting team-league cache refresh...", 5)

            # Get list of non-soccer leagues from config
            leagues_to_index = cls._get_leagues_to_index()
            if not leagues_to_index:
                return {'success': False, 'error': 'No leagues to index'}

            report(f"Indexing {len(leagues_to_index)} leagues...", 10)

            # Fetch teams from all leagues in parallel
            all_teams = []
            completed = 0
            total = len(leagues_to_index)

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(cls._fetch_league_teams, league): league
                    for league in leagues_to_index
                }

                for future in as_completed(futures):
                    league = futures[future]
                    completed += 1

                    pct = 10 + int((completed / total) * 70)  # 10-80%
                    report(f"Processed {completed}/{total} leagues", pct)

                    try:
                        teams = future.result()
                        if teams:
                            all_teams.extend(teams)
                    except Exception as e:
                        logger.warning(f"Error fetching teams from {league}: {e}")

            report(f"Indexed {len(all_teams)} teams, saving to database...", 85)

            # Save to database
            cls._save_cache(all_teams)

            # Update metadata
            duration = time.time() - start_time
            cls._update_cache_meta(len(leagues_to_index), len(all_teams), duration)

            report(f"Cache refresh complete: {len(all_teams)} teams in {duration:.1f}s", 100)

            return {
                'success': True,
                'leagues_processed': len(leagues_to_index),
                'teams_indexed': len(all_teams),
                'duration_seconds': duration,
                'error': None
            }

        except Exception as e:
            logger.error(f"Team-league cache refresh failed: {e}")
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
            logger.info(f"Team-league cache is {stats.staleness_days} days old, refreshing...")
            result = cls.refresh_cache()
            return result['success']

        return False

    # ==========================================================================
    # PRIVATE: Fetching Logic
    # ==========================================================================

    @classmethod
    def _get_leagues_to_index(cls) -> List[str]:
        """Get list of non-soccer leagues to index."""
        # Filter to only leagues that exist in league_config
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT league_code FROM league_config WHERE active = 1")
            db_leagues = {row[0] for row in cursor.fetchall()}

            # Return intersection of our list and what's in DB
            return [l for l in NON_SOCCER_LEAGUES if l in db_leagues]
        finally:
            conn.close()

    @classmethod
    def _fetch_league_teams(cls, league_code: str) -> List[Dict]:
        """
        Fetch all teams from a single league.

        Returns:
            List of team dicts with league_code, espn_team_id, team_name, etc.
        """
        # Get league config
        config = get_league_config(league_code, get_connection)
        if not config:
            logger.warning(f"No config found for league {league_code}")
            return []

        sport = config['sport']
        api_path = config['api_path']

        # Parse api_path to get ESPN sport/league
        parts = api_path.split('/')
        if len(parts) != 2:
            logger.warning(f"Invalid api_path for {league_code}: {api_path}")
            return []

        espn_sport, espn_league = parts

        try:
            # Use ESPNClient to fetch teams
            client = ESPNClient()

            # For college leagues, try conference-based fetching first
            from epg.league_config import is_college_league
            teams_data = []

            if is_college_league(league_code):
                conferences_data = client.get_all_teams_by_conference(espn_sport, espn_league)

                if conferences_data:
                    # Flatten conference structure: [{name, teams: [...]}] -> [team, team, ...]
                    for conf in conferences_data:
                        conf_teams = conf.get('teams', [])
                        teams_data.extend(conf_teams)

                    # For college-football, also fetch FCS teams via limit=1000 endpoint
                    # FBS teams come from standings API (conferences), FCS teams aren't in standings
                    if league_code == 'college-football':
                        fbs_ids = {str(t.get('id')) for t in teams_data}
                        all_cfb_teams = client.get_league_teams(espn_sport, espn_league)
                        if all_cfb_teams:
                            fcs_count = 0
                            for team in all_cfb_teams:
                                team_id = str(team.get('id', ''))
                                if team_id and team_id not in fbs_ids:
                                    teams_data.append(team)
                                    fcs_count += 1
                            if fcs_count > 0:
                                logger.info(f"Added {fcs_count} FCS teams to college-football cache")
                else:
                    # Fallback to direct teams endpoint (e.g., NCAA soccer has no conferences)
                    logger.info(f"No conferences for {league_code}, using direct teams endpoint")
                    teams_data = client.get_league_teams(espn_sport, espn_league)
            else:
                teams_data = client.get_league_teams(espn_sport, espn_league)

            if not teams_data:
                logger.warning(f"No teams returned for {league_code}")
                return []

            # Transform to our format
            teams = []
            for team in teams_data:
                team_id = team.get('id') or team.get('team', {}).get('id')
                if not team_id:
                    continue

                # Handle nested team structure from some endpoints
                team_data = team.get('team', team)

                teams.append({
                    'league_code': league_code,
                    'espn_team_id': str(team_id),
                    'team_name': team_data.get('displayName') or team_data.get('name', ''),
                    'team_abbrev': team_data.get('abbreviation') or team_data.get('abbrev', ''),
                    'team_short_name': team_data.get('shortDisplayName') or team_data.get('shortName', ''),
                    'sport': sport,
                })

            logger.debug(f"Fetched {len(teams)} teams from {league_code}")
            return teams

        except Exception as e:
            logger.error(f"Error fetching teams from {league_code}: {e}")
            return []

    # ==========================================================================
    # PRIVATE: Database Operations
    # ==========================================================================

    @classmethod
    def _save_cache(cls, teams: List[Dict]):
        """Save cache data to database."""
        conn = get_connection()
        cursor = conn.cursor()

        try:
            # Clear old data
            cursor.execute("DELETE FROM team_league_cache")

            # Deduplicate by (league_code, espn_team_id) - some endpoints return duplicates
            seen = set()
            unique_teams = []
            for team in teams:
                key = (team['league_code'], team['espn_team_id'])
                if key not in seen:
                    seen.add(key)
                    unique_teams.append(team)

            # Insert teams
            for team in unique_teams:
                cursor.execute("""
                    INSERT INTO team_league_cache
                    (league_code, espn_team_id, team_name, team_abbrev, team_short_name, sport)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    team['league_code'],
                    team['espn_team_id'],
                    team['team_name'],
                    team['team_abbrev'],
                    team['team_short_name'],
                    team['sport'],
                ))

            conn.commit()
            logger.info(f"Saved {len(unique_teams)} teams to team_league_cache (deduped from {len(teams)})")

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

            # Ensure row exists
            cursor.execute("INSERT OR IGNORE INTO team_league_cache_meta (id) VALUES (1)")

            cursor.execute("""
                UPDATE team_league_cache_meta SET
                    last_refresh = ?,
                    leagues_processed = ?,
                    teams_indexed = ?
                WHERE id = 1
            """, (now, leagues, teams))
            conn.commit()
        finally:
            conn.close()


# =============================================================================
# HELPER FUNCTIONS (Module-level)
# =============================================================================

def get_leagues_for_team(team_name: str) -> Set[str]:
    """Convenience function for getting team's leagues."""
    return TeamLeagueCache.get_leagues_for_team(team_name)


def find_candidate_leagues(team1: str, team2: str, enabled_leagues: List[str] = None) -> List[str]:
    """Convenience function for finding candidate leagues for a team pair."""
    return TeamLeagueCache.find_candidate_leagues(team1, team2, enabled_leagues)


def refresh_team_league_cache() -> Dict[str, Any]:
    """Convenience function for refreshing cache."""
    return TeamLeagueCache.refresh_cache()
