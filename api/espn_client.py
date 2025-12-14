"""ESPN API Client for fetching sports schedules and team data"""
import requests
from requests.adapters import HTTPAdapter
import json
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import time
from utils.logger import get_logger
from epg.league_config import SoccerCompat

logger = get_logger(__name__)


# Module-level HTTP session for connection pooling across all ESPNClient instances
_espn_session: Optional[requests.Session] = None
_espn_session_lock = threading.Lock()


def _get_espn_session() -> requests.Session:
    """Get or create the shared ESPN HTTP session with connection pooling."""
    global _espn_session
    if _espn_session is None:
        with _espn_session_lock:
            if _espn_session is None:
                session = requests.Session()
                # Configure connection pooling for ESPN's multiple domains
                adapter = HTTPAdapter(
                    pool_connections=10,   # Number of connection pools (per host)
                    pool_maxsize=100,      # Max connections per pool (matches ThreadPoolExecutor workers)
                    max_retries=0          # We handle retries ourselves
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)
                _espn_session = session
                logger.debug("ESPN HTTP session created with connection pooling")
    return _espn_session


class ESPNClient:
    """Client for ESPN's public API

    Caches are class-level (shared across all instances) to prevent redundant API calls
    when multiple EventMatcher, EventEnricher, LeagueDetector instances are created
    (especially in parallel processing). This reduced scoreboard calls from 1000+ to ~20
    per EPG generation cycle.
    """

    # Group IDs for college sports scoreboards
    # Adding groups param unlocks full D1 scoreboard (all games vs just featured)
    # Without groups: ~5-10 featured games; with groups=50: ~48-61 all D1 games
    COLLEGE_SCOREBOARD_GROUPS = {
        'mens-college-basketball': '50',
        'womens-college-basketball': '50',
        'college-football': '80',  # FBS
        'mens-college-hockey': '50',
        'womens-college-hockey': '50',
    }

    # Class-level caches shared across all instances
    # Key: (sport, league, date), Value: scoreboard data
    _scoreboard_cache: Dict[tuple, Optional[Dict]] = {}
    _scoreboard_cache_lock = threading.Lock()

    # Key: (sport, league, team_slug), Value: schedule data
    _schedule_cache: Dict[tuple, Optional[Dict]] = {}
    _schedule_cache_lock = threading.Lock()

    # Key: (sport, league, team_id), Value: team info data
    _team_info_cache: Dict[tuple, Optional[Dict]] = {}
    _team_info_cache_lock = threading.Lock()

    # Key: (league, team_id), Value: roster data
    _roster_cache: Dict[tuple, Optional[Dict]] = {}
    _roster_cache_lock = threading.Lock()

    # Key: (sport, league, group_id), Value: (name, abbreviation)
    _group_cache: Dict[tuple, tuple] = {}
    _group_cache_lock = threading.Lock()

    # Cache for team stats (refreshes every 6 hours) - instance level is OK
    # since this is long-lived and not cleared per-generation

    def __init__(self, base_url: str = "https://site.api.espn.com/apis/site/v2/sports", db_path: str = None):
        self.base_url = base_url
        self.db_path = db_path
        self.timeout = 10
        self.retry_count = 3
        self.retry_delay = 1  # seconds

        # Use shared session for connection pooling
        self._session = _get_espn_session()

        # Instance-level cache for team stats (refreshes every 6 hours)
        # This is OK as instance-level since it's long-lived
        self._stats_cache = {}
        self._stats_cache_instance_lock = threading.Lock()
        self._cache_duration = timedelta(hours=6)

    def _make_request(self, url: str) -> Optional[Dict]:
        """Make HTTP request with retry logic and connection pooling"""
        for attempt in range(self.retry_count):
            try:
                response = self._session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt < self.retry_count - 1:
                    logger.warning(f"ESPN API request failed (attempt {attempt + 1}/{self.retry_count}): {e}")
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                else:
                    logger.error(f"ESPN API request failed after {self.retry_count} attempts: {e}")
                    return None

    def _get_group_name(self, sport: str, league: str, group_id: str) -> tuple:
        """
        Fetch group (conference or division) name and abbreviation from ESPN core API.

        Results are cached per-generation to avoid redundant API calls.
        Thread-safe via double-checked locking.

        Args:
            sport: Sport type (e.g., 'basketball', 'football')
            league: League identifier (e.g., 'nba', 'nfl')
            group_id: Group ID (conference or division)

        Returns:
            tuple: (name, abbreviation) or ('', '') if not found
        """
        cache_key = (sport, league, str(group_id))

        # Fast path: check cache without lock
        if cache_key in self._group_cache:
            logger.debug(f"Group cache hit for {sport}/{league}/group/{group_id}")
            return self._group_cache[cache_key]

        # Slow path: acquire lock for cache miss
        with self._group_cache_lock:
            # Double-check after acquiring lock
            if cache_key in self._group_cache:
                logger.debug(f"Group cache hit (after lock) for {sport}/{league}/group/{group_id}")
                return self._group_cache[cache_key]

            try:
                url = f"http://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/groups/{group_id}"
                group_data = self._make_request(url)

                if group_data:
                    # Get both full name and abbreviation
                    name = group_data.get('shortName') or group_data.get('name', '')
                    abbrev = group_data.get('abbreviation', '')
                    result = (name, abbrev)
                else:
                    result = ('', '')
            except Exception as e:
                logger.error(f"Error fetching group name for ID {group_id}: {e}")
                result = ('', '')

            # Cache the result (even failures to avoid re-fetching)
            self._group_cache[cache_key] = result
            return result

    def _extract_record(self, record_list: List) -> Dict:
        """Extract win-loss record from competitor record array"""
        record = {'summary': '0-0', 'wins': 0, 'losses': 0, 'ties': 0}

        if not record_list:
            return record

        # Find the "total" record type
        for rec in record_list:
            if rec.get('type') == 'total':
                record['summary'] = rec.get('displayValue', '0-0')

                # Try to extract wins/losses from stats if available
                if 'stats' in rec:
                    for stat in rec['stats']:
                        if stat.get('name') == 'wins':
                            record['wins'] = int(stat.get('value', 0))
                        elif stat.get('name') == 'losses':
                            record['losses'] = int(stat.get('value', 0))
                        elif stat.get('name') == 'ties':
                            record['ties'] = int(stat.get('value', 0))

                # If no stats, parse from summary (e.g., "9-5" or "9-5-1")
                if record['wins'] == 0 and record['losses'] == 0 and '-' in record['summary']:
                    parts = record['summary'].split('-')
                    if len(parts) >= 2:
                        try:
                            record['wins'] = int(parts[0])
                            record['losses'] = int(parts[1])
                            if len(parts) == 3:
                                record['ties'] = int(parts[2])
                        except ValueError:
                            pass

                break

        return record

    def get_team_schedule(self, sport: str, league: str, team_slug: str, days_ahead: int = 14) -> Optional[Dict]:
        """
        Fetch team schedule from ESPN API with caching.

        Schedule data is cached per-generation to avoid redundant API calls
        when the same team is referenced multiple times (e.g., opponent lookups).
        Thread-safe via double-checked locking.

        Args:
            sport: Sport type (e.g., 'basketball', 'football', 'soccer')
            league: League identifier (e.g., 'nba', 'nfl', 'eng.1')
            team_slug: Team slug/ID (e.g., 'detroit-pistons', '8')
            days_ahead: Number of days ahead to fetch

        Returns:
            Dict with team schedule data or None if failed
        """
        cache_key = (sport, league, str(team_slug))

        # Fast path: check cache without lock
        if cache_key in self._schedule_cache:
            logger.debug(f"Schedule cache hit for {sport}/{league}/{team_slug}")
            return self._schedule_cache[cache_key]

        # Slow path: acquire lock for cache miss
        with self._schedule_cache_lock:
            # Double-check after acquiring lock (another thread may have populated)
            if cache_key in self._schedule_cache:
                logger.debug(f"Schedule cache hit (after lock) for {sport}/{league}/{team_slug}")
                return self._schedule_cache[cache_key]

            # Fetch from API
            url = f"{self.base_url}/{sport}/{league}/teams/{team_slug}/schedule"
            result = self._make_request(url)

            # Cache the result (even if None to avoid re-fetching failures)
            self._schedule_cache[cache_key] = result
            return result

    def clear_schedule_cache(self):
        """Clear the schedule cache. Call this at the start of each EPG generation."""
        with ESPNClient._schedule_cache_lock:
            ESPNClient._schedule_cache.clear()
        logger.debug("Schedule cache cleared")

    def clear_team_info_cache(self):
        """Clear the team info cache. Call this at the start of each EPG generation."""
        with ESPNClient._team_info_cache_lock:
            ESPNClient._team_info_cache.clear()
        logger.debug("Team info cache cleared")

    def clear_roster_cache(self):
        """Clear the roster cache. Call this at the start of each EPG generation."""
        with ESPNClient._roster_cache_lock:
            ESPNClient._roster_cache.clear()
        logger.debug("Roster cache cleared")

    def clear_group_cache(self):
        """Clear the group name cache. Call this at the start of each EPG generation."""
        with ESPNClient._group_cache_lock:
            ESPNClient._group_cache.clear()
        logger.debug("Group cache cleared")

    def clear_scoreboard_cache(self):
        """Clear the scoreboard cache. Call this at the start of each EPG generation."""
        with ESPNClient._scoreboard_cache_lock:
            ESPNClient._scoreboard_cache.clear()
        logger.debug("Scoreboard cache cleared")

    def get_team_info(self, sport: str, league: str, team_id: str) -> Optional[Dict]:
        """
        Fetch team information (name, logo, colors, etc.) with caching.

        Team info is cached per-generation to avoid redundant API calls
        when the same team is referenced multiple times (e.g., opponent lookups,
        stats fetches that call get_team_info internally).
        Thread-safe via double-checked locking.

        Args:
            sport: Sport type
            league: League identifier
            team_id: Team ID

        Returns:
            Dict with team info or None if failed
        """
        cache_key = (sport, league, str(team_id))

        # Fast path: check cache without lock
        if cache_key in self._team_info_cache:
            logger.debug(f"Team info cache hit for {sport}/{league}/{team_id}")
            return self._team_info_cache[cache_key]

        # Slow path: acquire lock for cache miss
        with self._team_info_cache_lock:
            # Double-check after acquiring lock (another thread may have populated)
            if cache_key in self._team_info_cache:
                logger.debug(f"Team info cache hit (after lock) for {sport}/{league}/{team_id}")
                return self._team_info_cache[cache_key]

            # Fetch from API
            url = f"{self.base_url}/{sport}/{league}/teams/{team_id}"
            result = self._make_request(url)

            # Cache the result (even if None to avoid re-fetching failures)
            self._team_info_cache[cache_key] = result
            return result

    def get_team_roster(self, league: str, team_id: str) -> Optional[Dict]:
        """
        Fetch team roster data with caching.

        Roster data is cached per-generation to avoid redundant API calls
        when the same team's roster is needed multiple times (e.g., coach lookups).
        Thread-safe via double-checked locking.

        Args:
            league: League path (e.g., 'football/nfl', 'basketball/nba')
            team_id: Team ID

        Returns:
            Dict with roster data or None if failed
        """
        cache_key = (league, str(team_id))

        # Fast path: check cache without lock
        if cache_key in self._roster_cache:
            logger.debug(f"Roster cache hit for {league}/{team_id}")
            return self._roster_cache[cache_key]

        # Slow path: acquire lock for cache miss
        with self._roster_cache_lock:
            # Double-check after acquiring lock
            if cache_key in self._roster_cache:
                logger.debug(f"Roster cache hit (after lock) for {league}/{team_id}")
                return self._roster_cache[cache_key]

            # Fetch from API
            url = f"{self.base_url}/{league}/teams/{team_id}/roster"
            result = self._make_request(url)

            # Cache the result (even if None to avoid re-fetching failures)
            self._roster_cache[cache_key] = result
            return result

    def get_team_record(self, sport: str, league: str, team_id: str) -> Optional[Dict]:
        """
        Fetch current team record and standings

        Args:
            sport: Sport type
            league: League identifier
            team_id: Team ID

        Returns:
            Dict with record data (wins, losses, ties, winPercent, etc.)
        """
        team_data = self.get_team_info(sport, league, team_id)
        if team_data and 'team' in team_data:
            return team_data['team'].get('record')
        return None

    def get_team_stats(self, sport: str, league: str, team_id: str) -> Dict[str, Any]:
        """
        Fetch detailed team statistics including streaks, PPG, standings

        This method fetches data from the team endpoint which includes:
        - Streak count
        - Points per game (PPG)
        - Points allowed per game (PAPG)
        - Playoff seed
        - Games behind
        - Home/away/division records

        Args:
            sport: Sport type (e.g., 'basketball', 'football')
            league: League identifier (e.g., 'nba', 'nfl')
            team_id: Team ID

        Returns:
            Dict with team statistics:
            {
                'streak_count': 11,
                'ppg': 118.9,
                'papg': 112.1,
                'playoff_seed': 1,
                'games_back': 0.0,
                'home_record': '7-1',
                'away_record': '6-1',
                'division_record': '3-1'
            }
        """
        # Check cache first (fast path without lock)
        cache_key = f"{sport}_{league}_{team_id}"
        if cache_key in self._stats_cache:
            cached_data, cached_time = self._stats_cache[cache_key]
            if datetime.now() - cached_time < self._cache_duration:
                return cached_data

        # Slow path: acquire lock for cache miss
        with self._stats_cache_instance_lock:
            # Double-check after acquiring lock (another thread may have populated)
            if cache_key in self._stats_cache:
                cached_data, cached_time = self._stats_cache[cache_key]
                if datetime.now() - cached_time < self._cache_duration:
                    return cached_data

            # Fetch fresh data (inside lock to prevent duplicate fetches)
            return self._fetch_team_stats_uncached(sport, league, team_id, cache_key)

    def _fetch_team_stats_uncached(
        self,
        sport: str,
        league: str,
        team_id: str,
        cache_key: str
    ) -> Dict:
        """Internal method to fetch team stats without cache check. Called with lock held."""
        team_data = self.get_team_info(sport, league, team_id)

        # Default empty stats
        stats = {
            'streak_count': 0,
            'ppg': 0.0,
            'papg': 0.0,
            'playoff_seed': 0,
            'games_back': 0.0,
            'home_record': '0-0',
            'away_record': '0-0',
            'division_record': ''
        }

        if not team_data or 'team' not in team_data:
            return stats

        # Parse record items
        record = team_data['team'].get('record', {})
        record_items = record.get('items', [])

        if not record_items:
            return stats

        # Get overall stats (type='total')
        overall = next((r for r in record_items if r.get('type') == 'total'), None)
        if overall and 'stats' in overall:
            stat_dict = {s['name']: s['value'] for s in overall['stats']}

            stats['streak_count'] = int(stat_dict.get('streak', 0))
            stats['ppg'] = float(stat_dict.get('avgPointsFor', 0))
            stats['papg'] = float(stat_dict.get('avgPointsAgainst', 0))
            stats['playoff_seed'] = int(stat_dict.get('playoffSeed', 0))
            stats['games_back'] = float(stat_dict.get('gamesBehind', 0))

            # Extract overall record (wins-losses)
            stats['record'] = {
                'summary': overall.get('summary', '0-0'),
                'wins': int(stat_dict.get('wins', 0)),
                'losses': int(stat_dict.get('losses', 0)),
                'ties': int(stat_dict.get('ties', 0)),
                'winPercent': float(stat_dict.get('winPercent', 0))
            }

        # Get home record (type='home')
        home_rec = next((r for r in record_items if r.get('type') == 'home'), None)
        if home_rec:
            stats['home_record'] = home_rec.get('summary', '0-0')

        # Get away record (type='road')
        away_rec = next((r for r in record_items if r.get('type') == 'road'), None)
        if away_rec:
            stats['away_record'] = away_rec.get('summary', '0-0')

        # Fallback: Build home/away records from stats in total record (needed for soccer)
        # Soccer leagues don't have separate home/road record items, but the stats exist
        # Check if overall record uses W-D-L format (3 parts) to maintain consistency
        overall_summary = stats['record'].get('summary', '0-0')
        uses_draws = len(overall_summary.split('-')) == 3

        if stats['home_record'] == '0-0' and overall:
            home_wins = int(stat_dict.get('homeWins', 0))
            home_losses = int(stat_dict.get('homeLosses', 0))
            home_ties = int(stat_dict.get('homeTies', 0))
            if home_wins or home_losses or home_ties:
                if uses_draws:
                    # Soccer: W-D-L format (draws in middle)
                    stats['home_record'] = f"{home_wins}-{home_ties}-{home_losses}"
                elif home_ties > 0:
                    stats['home_record'] = f"{home_wins}-{home_losses}-{home_ties}"
                else:
                    stats['home_record'] = f"{home_wins}-{home_losses}"

        if stats['away_record'] == '0-0' and overall:
            away_wins = int(stat_dict.get('awayWins', 0))
            away_losses = int(stat_dict.get('awayLosses', 0))
            away_ties = int(stat_dict.get('awayTies', 0))
            if away_wins or away_losses or away_ties:
                if uses_draws:
                    # Soccer: W-D-L format (draws in middle)
                    stats['away_record'] = f"{away_wins}-{away_ties}-{away_losses}"
                elif away_ties > 0:
                    stats['away_record'] = f"{away_wins}-{away_losses}-{away_ties}"
                else:
                    stats['away_record'] = f"{away_wins}-{away_losses}"

        # Get division record (type='division') - may not exist for all sports
        div_rec = next((r for r in record_items if r.get('type') == 'division'), None)
        if div_rec:
            stats['division_record'] = div_rec.get('summary', '')

        # Get team rank (for college sports) - returns 99 if unranked
        stats['rank'] = team_data['team'].get('rank', 99)

        # =====================================================================
        # CONFERENCE/DIVISION INFORMATION
        # =====================================================================
        # ESPN API Structure for Conference/Division Data:
        #
        # Team API provides groups structure at: team['groups']
        # - groups.id: Division or conference ID
        # - groups.parent.id: Parent conference ID (if division exists)
        # - groups.isConference: True if groups.id is conference, False if division
        #
        # Conference/Division Names fetched via Core API:
        # http://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/groups/{id}
        # Returns: {name, shortName, abbreviation, isConference}
        #
        # Structure by Sport Type:
        # ┌─────────────┬──────────────┬─────────────────┬──────────────────┬─────────────────┐
        # │ Sport Type  │ groups.id    │ groups.parent   │ isConference     │ Variable Uses   │
        # ├─────────────┼──────────────┼─────────────────┼──────────────────┼─────────────────┤
        # │ NFL         │ Division     │ Conference      │ false            │ Division        │
        # │             │ (10="NFC     │ (7="NFC")       │                  │ ("NFC North")   │
        # │             │ North")      │                 │                  │                 │
        # ├─────────────┼──────────────┼─────────────────┼──────────────────┼─────────────────┤
        # │ NBA         │ Division     │ Conference      │ false            │ Division        │
        # │             │ (5="SE Div") │ (2="Eastern")   │                  │ ("Southeast")   │
        # ├─────────────┼──────────────┼─────────────────┼──────────────────┼─────────────────┤
        # │ College FB  │ Subdivision  │ Conference      │ false            │ Conference      │
        # │             │ (168="Sun    │ (37="Sun Belt") │                  │ ("Sun Belt")    │
        # │             │ Belt-West")  │                 │                  │                 │
        # ├─────────────┼──────────────┼─────────────────┼──────────────────┼─────────────────┤
        # │ Independent │ Conference   │ None            │ true             │ Conference      │
        # │ College     │ (Only groups │                 │                  │                 │
        # │             │ .id exists)  │                 │                  │                 │
        # └─────────────┴──────────────┴─────────────────┴──────────────────┴─────────────────┘
        #
        # Logic:
        # 1. If isConference=true: Use groups.id as conference (no division)
        # 2. Otherwise: Fetch both division (groups.id) and conference (groups.parent.id)
        # 3. For {conference_or_division_name} variable:
        #    - Pro sports: Use division (more specific for fan interest)
        #    - College: Use conference (divisions less relevant)
        #
        # Skip division/conference lookup for soccer - they don't have meaningful
        # divisions/conferences and the API returns stale/garbage data
        if not SoccerCompat.should_skip_division(league):
            groups = team_data['team'].get('groups', {})
            division_id = groups.get('id', '')
            conference_id = groups.get('parent', {}).get('id', '')
            is_conference = groups.get('isConference', False)

            # If groups.id is actually the conference (no parent), use it directly
            if is_conference and division_id:
                conference_name, conference_abbrev = self._get_group_name(sport, league, division_id)
                if conference_name:
                    stats['conference_id'] = division_id
                    stats['conference_name'] = conference_name
                    stats['conference_abbrev'] = conference_abbrev
            else:
                # Fetch division name (if exists and not a conference)
                if division_id:
                    division_name, division_abbrev = self._get_group_name(sport, league, division_id)
                    if division_name:
                        stats['division_id'] = division_id
                        stats['division_name'] = division_name
                        stats['division_abbrev'] = division_abbrev

                # Fetch parent conference name
                if conference_id:
                    conference_name, conference_abbrev = self._get_group_name(sport, league, conference_id)
                    if conference_name:
                        stats['conference_id'] = conference_id
                        stats['conference_full_name'] = conference_name
                        stats['conference_abbrev'] = conference_abbrev

                        # For {conference_or_division_name} variable:
                        # - Pro sports (NFL/NBA): prefer division name (more specific)
                        # - College: prefer conference name (divisions are less meaningful)
                        # Heuristic: if league contains 'college', use conference; otherwise use division
                        if 'college' in league.lower():
                            stats['conference_name'] = conference_name
                        elif division_id:
                            stats['conference_name'] = stats.get('division_name', conference_name)
                        else:
                            stats['conference_name'] = conference_name

        # Cache the result
        self._stats_cache[cache_key] = (stats, datetime.now())

        return stats


    def get_scoreboard(self, sport: str, league: str, date: str = None) -> Optional[Dict]:
        """
        Fetch scoreboard for a specific date with caching.

        Scoreboard data is cached per-generation to avoid redundant API calls
        during multi-sport disambiguation (same league/date checked many times).
        Thread-safe via double-checked locking.

        Args:
            sport: Sport type
            league: League identifier
            date: Date in YYYYMMDD format (default: today)

        Returns:
            Dict with scoreboard data
        """
        if date is None:
            date = datetime.now().strftime('%Y%m%d')

        cache_key = (sport, league, date)

        # Fast path: check cache without lock
        if cache_key in self._scoreboard_cache:
            logger.debug(f"Scoreboard cache hit for {sport}/{league}/{date}")
            return self._scoreboard_cache[cache_key]

        # Slow path: acquire lock for cache miss
        with self._scoreboard_cache_lock:
            # Double-check after acquiring lock
            if cache_key in self._scoreboard_cache:
                logger.debug(f"Scoreboard cache hit (after lock) for {sport}/{league}/{date}")
                return self._scoreboard_cache[cache_key]

            # Build URL with optional groups param for college sports
            # This unlocks full D1 scoreboard (all games vs just featured)
            url = f"{self.base_url}/{sport}/{league}/scoreboard?dates={date}"
            if league in self.COLLEGE_SCOREBOARD_GROUPS:
                url += f"&groups={self.COLLEGE_SCOREBOARD_GROUPS[league]}"

            result = self._make_request(url)

            # Cache the result (even if None to avoid re-fetching failures)
            self._scoreboard_cache[cache_key] = result
            return result

    def get_event_summary(self, sport: str, league: str, event_id: str) -> Optional[Dict]:
        """
        Fetch a single event by ID using the event summary endpoint.

        This is more reliable than scoreboard for finished games since the
        scoreboard only shows current day's games.

        Args:
            sport: Sport type (e.g., 'football', 'hockey')
            league: League identifier (e.g., 'nfl', 'nhl')
            event_id: ESPN event ID

        Returns:
            Dict with event data (same structure as scoreboard events)
        """
        url = f"{self.base_url}/{sport}/{league}/summary?event={event_id}"
        data = self._make_request(url)

        if not data:
            return None

        # The summary endpoint returns a different structure than scoreboard
        # We need to reconstruct a scoreboard-like event object
        try:
            header = data.get('header', {})
            competitions = header.get('competitions', [{}])
            competition = competitions[0] if competitions else {}

            # Build event structure matching scoreboard format
            event = {
                'id': str(event_id),
                'uid': header.get('uid', ''),
                'date': competition.get('date', header.get('gameDate', '')),
                'name': header.get('gameNote', ''),
                'shortName': '',
                'competitions': [{
                    'id': competition.get('id', event_id),
                    'date': competition.get('date', ''),
                    'competitors': competition.get('competitors', []),
                    'venue': competition.get('venue', {}),
                    'broadcasts': competition.get('broadcasts', []),
                    'status': competition.get('status', {}),
                    'odds': data.get('predictor', {}).get('odds', [])
                }]
            }

            # Build name from competitors if not available
            competitors = competition.get('competitors', [])
            if len(competitors) >= 2:
                home = next((c for c in competitors if c.get('homeAway') == 'home'), competitors[0])
                away = next((c for c in competitors if c.get('homeAway') == 'away'), competitors[1])
                home_name = home.get('team', {}).get('displayName', '')
                away_name = away.get('team', {}).get('displayName', '')
                event['name'] = f"{away_name} at {home_name}"
                event['shortName'] = f"{away.get('team', {}).get('abbreviation', '')} @ {home.get('team', {}).get('abbreviation', '')}"

            return event

        except Exception as e:
            logger.warning(f"Error parsing event summary for {event_id}: {e}")
            return None

    def parse_schedule_events(self, schedule_data: Dict, days_ahead: int = 14, cutoff_past_datetime: datetime = None) -> List[Dict]:
        """
        Parse schedule data and extract relevant event information

        Args:
            schedule_data: Raw schedule data from ESPN API
            days_ahead: Number of days to include (1 = today only, 2 = today and tomorrow, etc.)
            cutoff_past_datetime: Earliest event datetime to include (events before this are filtered out)

        Returns:
            List of parsed event dictionaries
        """
        if not schedule_data or 'events' not in schedule_data:
            return []

        events = []
        from datetime import timezone as tz
        now = datetime.now(tz.utc)

        # Use provided cutoff_past_datetime or default to 6 hours ago
        if cutoff_past_datetime:
            # Convert to UTC for comparison
            cutoff_past = cutoff_past_datetime.astimezone(tz.utc) if cutoff_past_datetime.tzinfo else cutoff_past_datetime.replace(tzinfo=tz.utc)
            # Calculate future cutoff from cutoff_past_datetime to ensure consistent date range
            # IMPORTANT: Use the date in the ORIGINAL timezone (not UTC) to match filler's
            # start_date calculation in _generate_filler_entries. Otherwise, early morning
            # UTC times (e.g., 3 AM UTC = 10 PM EST previous day) create a 1-day mismatch
            # where events include day N but filler only processes up to day N-1.
            reference_date = cutoff_past_datetime.date()
        else:
            cutoff_past = now - timedelta(hours=6)
            reference_date = now.date()

        # Calculate future cutoff as end of the Nth day from reference date
        cutoff_date = reference_date + timedelta(days=days_ahead - 1)
        cutoff_future = datetime.combine(cutoff_date, datetime.max.time()).replace(tzinfo=tz.utc)

        for event in schedule_data.get('events', []):
            try:
                event_date = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))

                # Skip events outside our window
                if event_date < cutoff_past:
                    continue
                if event_date > cutoff_future:
                    continue

                parsed_event = self._parse_event(event)
                if parsed_event:
                    events.append(parsed_event)

            except Exception as e:
                logger.warning(f"Error parsing event: {e}")
                continue

        return events

    def _parse_event(self, event: Dict) -> Optional[Dict]:
        """Parse a single event into simplified structure"""
        try:
            competition = event['competitions'][0]
            competitors = competition['competitors']

            # Determine home and away teams
            home_team = next((c for c in competitors if c.get('homeAway') == 'home'), competitors[0])
            away_team = next((c for c in competitors if c.get('homeAway') == 'away'), competitors[1])

            parsed = {
                'id': event['id'],
                'uid': event.get('uid'),
                'date': event['date'],
                'name': event['name'],
                'short_name': event.get('shortName'),

                # Teams
                'home_team': {
                    'id': home_team['team']['id'],
                    'name': home_team['team']['displayName'],
                    'abbrev': home_team['team']['abbreviation'],
                    'logo': home_team['team'].get('logo'),
                    'color': home_team['team'].get('color'),
                    'score': home_team.get('score'),
                    'record': self._extract_record(home_team.get('record', [])),
                },
                'away_team': {
                    'id': away_team['team']['id'],
                    'name': away_team['team']['displayName'],
                    'abbrev': away_team['team']['abbreviation'],
                    'logo': away_team['team'].get('logo'),
                    'color': away_team['team'].get('color'),
                    'score': away_team.get('score'),
                    'record': self._extract_record(away_team.get('record', [])),
                },

                # Venue (handle international venues which may not have state/address)
                'venue': {
                    'name': competition.get('venue', {}).get('fullName'),
                    'city': competition.get('venue', {}).get('address', {}).get('city'),
                    'state': competition.get('venue', {}).get('address', {}).get('state'),
                },

                # Broadcast
                'broadcasts': [b.get('names', [None])[0] for b in competition.get('broadcasts', [])],

                # Status
                'status': {
                    'name': competition['status']['type'].get('name', ''),
                    'state': competition['status']['type'].get('state', 'pre'),
                    'completed': competition['status']['type'].get('completed', False),
                    'detail': competition['status']['type'].get('detail'),
                    'period': competition['status'].get('period', 0),  # For overtime detection
                },

                # Season
                'season': {
                    'year': event['season']['year'],
                    'slug': event['season'].get('slug', 'regular'),
                },

                # Competitions (needed for odds, rankings, etc.)
                'competitions': [competition]  # Pass through full competition object
            }

            return parsed

        except Exception as e:
            logger.warning(f"Error parsing event details: {e}")
            return None

    def get_league_standings(self, sport: str, league: str) -> Optional[Dict]:
        """
        Fetch league standings

        Args:
            sport: Sport type
            league: League identifier

        Returns:
            Dict with standings data
        """
        url = f"{self.base_url}/{sport}/{league}/standings"
        return self._make_request(url)

    def extract_team_from_url(self, url: str) -> Optional[Dict]:
        """
        Extract team information from ESPN URL

        Supports multiple URL patterns:
        - Pro sports: https://www.espn.com/nba/team/_/name/det/detroit-pistons
        - College sports: https://www.espn.com/college-football/team/_/id/130/michigan-wolverines
        - Soccer clubs: https://www.espn.com/soccer/club/_/id/21422/angel-city-fc

        Args:
            url: ESPN team URL

        Returns:
            Dict with sport, league, team_slug extracted from URL
        """
        import re

        # Try pattern 1: espn.com/{sport}/team/_/name/{team_slug}[/optional-name]
        pattern_name = r'espn\.com/([^/]+)/(?:team|club)/_/name/([^/]+)(?:/([^/]+))?'
        match = re.search(pattern_name, url)

        if match:
            sport_code = match.group(1)
            team_slug = match.group(2)
        else:
            # Try pattern 2: espn.com/{sport}/team|club/_/id/{team_id}[/optional-name]
            pattern_id = r'espn\.com/([^/]+)/(?:team|club)/_/id/(\d+)(?:/([^/]+))?'
            match = re.search(pattern_id, url)

            if not match:
                return None

            sport_code = match.group(1)
            team_slug = match.group(2)  # Use numeric ID as slug

        # Map ESPN URL sport codes to API paths
        sport_mapping = {
            'nba': ('basketball', 'nba'),
            'wnba': ('basketball', 'wnba'),
            'nfl': ('football', 'nfl'),
            'mlb': ('baseball', 'mlb'),
            'nhl': ('hockey', 'nhl'),
            'mls': ('soccer', 'usa.1'),
            'soccer': ('soccer', 'eng.1'),  # Default to EPL, may need refinement
            'mens-college-basketball': ('basketball', 'mens-college-basketball'),
            'womens-college-basketball': ('basketball', 'womens-college-basketball'),
            'college-football': ('football', 'college-football'),
        }

        if sport_code in sport_mapping:
            sport, league = sport_mapping[sport_code]
            return {
                'sport': sport,
                'league': league,
                'team_slug': team_slug
            }

        return None

    def get_team_info_from_url(self, url: str) -> Optional[Dict]:
        """
        Fetch team information from an ESPN URL

        Parses the URL to extract sport/league/team identifiers,
        then fetches the full team data from the ESPN API.

        Args:
            url: ESPN team URL (e.g., https://www.espn.com/nba/team/_/name/det/detroit-pistons)

        Returns:
            Dict with team data formatted for Teamarr team creation:
            {
                'team_name': 'Detroit Pistons',
                'team_abbrev': 'DET',
                'team_slug': 'det',
                'league': 'nba',
                'sport': 'basketball',
                'espn_team_id': '8',
                'team_logo_url': 'https://...',
                'team_color': '006BB6'
            }
        """
        # Extract sport, league, and team slug from URL
        url_info = self.extract_team_from_url(url)
        if not url_info:
            logger.warning(f"Could not parse ESPN URL: {url}")
            return None

        sport = url_info['sport']
        league = url_info['league']
        team_slug = url_info['team_slug']

        # For soccer URLs, detect the correct league using the multi-league cache
        # Soccer URLs don't include league info, so we need to look it up
        if sport == 'soccer' and team_slug.isdigit():
            try:
                from epg.soccer_multi_league import SoccerMultiLeague
                # Get team's default (primary) league from ESPN
                default_league = SoccerMultiLeague.get_team_default_league(team_slug, league)
                if default_league:
                    logger.debug(f"Soccer team {team_slug}: detected default league {default_league}")
                    league = default_league
            except Exception as e:
                logger.warning(f"Could not detect soccer league for team {team_slug}: {e}")
                # Continue with fallback league

        # Fetch team data from ESPN API
        # Try by slug first, fall back to ID if slug is numeric
        team_data = self.get_team_info(sport, league, team_slug)

        if not team_data or 'team' not in team_data:
            logger.warning(f"Could not fetch team data for {team_slug} in {league}")
            return None

        team = team_data['team']

        # Extract logo URL
        logo_url = None
        if team.get('logos') and len(team['logos']) > 0:
            logo_url = team['logos'][0].get('href', '')

        # Store ESPN slugs directly (single source of truth)
        # Aliases are handled by normalize_league_code() when reading

        return {
            'team_name': team.get('displayName') or team.get('name', ''),
            'team_abbrev': team.get('abbreviation', ''),
            'team_slug': team.get('slug', team_slug),
            'league': league,
            'sport': sport,
            'espn_team_id': str(team.get('id', '')),
            'team_logo_url': logo_url or '',
            'team_color': team.get('color', '')
        }

    def get_league_teams(self, sport: str, league: str) -> Optional[List[Dict]]:
        """
        Fetch all teams in a league from ESPN API

        Args:
            sport: Sport type (basketball, football, etc.)
            league: League code (nba, nfl, etc.)

        Returns:
            List of team dictionaries with id, name, abbreviation, logo, etc.
        """
        # ESPN defaults to 50 teams, but college sports have many more
        # College football has 740+ teams (FBS + FCS + DII/DIII)
        # Use limit=1000 to get all teams
        url = f"{self.base_url}/{sport}/{league}/teams?limit=1000"
        logger.info(f"Fetching teams for {league.upper()}: {url}")

        try:
            response = self._make_request(url)
            if not response or 'sports' not in response:
                logger.warning(f"No teams data found for {league}")
                return None

            teams = []

            # ESPN API structure: sports -> leagues -> teams
            for sport_obj in response.get('sports', []):
                for league_obj in sport_obj.get('leagues', []):
                    for team in league_obj.get('teams', []):
                        team_data = team.get('team', {})

                        teams.append({
                            'id': team_data.get('id'),
                            'slug': team_data.get('slug'),
                            'name': team_data.get('displayName') or team_data.get('name'),
                            'abbreviation': team_data.get('abbreviation'),
                            'shortName': team_data.get('shortDisplayName'),
                            'logo': team_data.get('logos', [{}])[0].get('href') if team_data.get('logos') else None,
                            'color': team_data.get('color'),
                            'alternateColor': team_data.get('alternateColor')
                        })

            logger.info(f"Found {len(teams)} teams for {league.upper()}")
            return teams

        except Exception as e:
            logger.error(f"Error fetching teams for {league}: {e}")
            return None

    def get_league_conferences(self, sport: str, league: str) -> Optional[List[Dict]]:
        """
        Fetch all conferences for a college league dynamically from ESPN API.

        Uses the standings API to get current conference list, which automatically
        handles conference changes (realignment, new conferences, etc.).

        Args:
            sport: Sport type (e.g., 'football', 'basketball')
            league: League code (e.g., 'college-football', 'mens-college-basketball')

        Returns:
            List of conference dictionaries with id, name, abbreviation, logo
        """
        # Only support college leagues
        college_leagues = ['college-football', 'mens-college-basketball', 'womens-college-basketball']
        if league not in college_leagues:
            return None

        # Fetch conferences dynamically from standings API
        # This endpoint returns all current conferences in the 'children' array
        standings_url = f"https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings"
        logger.info(f"Fetching conferences for {league} from standings API")

        try:
            standings_data = self._make_request(standings_url)
            if not standings_data or 'children' not in standings_data:
                logger.warning(f"No conference data found in standings for {league}")
                return None

            conference_ids = [int(child.get('id')) for child in standings_data.get('children', []) if child.get('id')]
            logger.info(f"Found {len(conference_ids)} conferences from standings API: {conference_ids}")

        except Exception as e:
            logger.error(f"Failed to fetch conferences from standings API: {e}")
            return None

        # Now fetch detailed info for each conference
        conferences = []
        season_year = datetime.now().year

        for conf_id in conference_ids:
            try:
                url = f"http://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/seasons/{season_year}/types/2/groups/{conf_id}"
                response = self._make_request(url)

                if response:
                    # Don't fetch team count upfront - it's fetched on-demand when user expands conference
                    conferences.append({
                        'id': conf_id,
                        'name': response.get('name'),
                        'abbreviation': response.get('abbreviation'),
                        'logo': response.get('logos', [{}])[0].get('href') if response.get('logos') else None,
                        'team_count': None  # Will be fetched when user expands conference
                    })
            except Exception as e:
                logger.warning(f"Error fetching conference {conf_id}: {e}")
                continue

        # Sort conferences alphabetically by name
        conferences.sort(key=lambda x: x['name'] or '')

        logger.info(f"Found {len(conferences)} conferences for {league}")
        return conferences if conferences else None

    def get_conference_teams(self, sport: str, league: str, conference_id: int) -> Optional[List[Dict]]:
        """
        Fetch all teams in a specific conference

        Args:
            sport: Sport type (e.g., 'football', 'basketball')
            league: League code (e.g., 'college-football')
            conference_id: ESPN conference group ID

        Returns:
            List of team dictionaries with id, name, abbreviation, logo, etc.
        """
        season_year = datetime.now().year
        url = f"http://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/seasons/{season_year}/types/2/groups/{conference_id}/teams?limit=50"

        logger.info(f"Fetching teams for conference {conference_id}")

        try:
            response = self._make_request(url)
            if not response or 'items' not in response:
                logger.warning(f"No teams data found for conference {conference_id}")
                return None

            teams = []

            # ESPN Core API returns team references that need to be fetched individually
            for item in response.get('items', []):
                if '$ref' in item:
                    try:
                        team_url = item['$ref']
                        team_data = self._make_request(team_url)

                        if team_data:
                            teams.append({
                                'id': team_data.get('id'),
                                'slug': team_data.get('slug'),
                                'name': team_data.get('displayName') or team_data.get('name'),
                                'abbreviation': team_data.get('abbreviation'),
                                'shortName': team_data.get('shortDisplayName'),
                                'logo': team_data.get('logos', [{}])[0].get('href') if team_data.get('logos') else None,
                                'color': team_data.get('color'),
                                'alternateColor': team_data.get('alternateColor')
                            })
                    except Exception as e:
                        logger.warning(f"Error fetching team from {item.get('$ref')}: {e}")
                        continue

            logger.info(f"Found {len(teams)} teams in conference {conference_id}")
            return teams

        except Exception as e:
            logger.error(f"Error fetching teams for conference {conference_id}: {e}")
            return None

    def get_teams_by_conference_batch(self, sport: str, league: str) -> Optional[Dict[str, List[Dict]]]:
        """
        Fetch all teams organized by conference in a SINGLE API call using /groups endpoint.

        Much faster than iterating through conferences individually. Returns teams
        organized by conference name for filtering purposes.

        Note: The /groups endpoint returns ~299 of ~362 teams for NCAAM. Teams not
        in any conference (independents, transitioning teams) won't be included.
        Use get_league_teams() with limit=500 if you need ALL teams.

        Args:
            sport: Sport type (e.g., 'basketball', 'football')
            league: League code (e.g., 'mens-college-basketball', 'college-football')

        Returns:
            Dictionary mapping conference names to lists of team dicts:
            {
                "Big 12 Conference": [{"id": "...", "name": "...", ...}, ...],
                "SEC": [...],
                ...
            }
            Returns None on error (including for non-college leagues).
        """
        # College leagues have "college" in their api_path - detect dynamically
        # This covers: college-football, mens-college-basketball, womens-college-basketball,
        # and any future college sports
        if 'college' not in league.lower():
            logger.debug(f"get_teams_by_conference_batch only supports college leagues, not {league}")
            return None

        url = f"{self.base_url}/{sport}/{league}/groups"
        logger.info(f"Fetching teams by conference for {league} via /groups endpoint")

        try:
            response = self._make_request(url)
            if not response or 'groups' not in response:
                logger.warning(f"No groups data found for {league}")
                return None

            conferences = {}

            # Structure: groups[].children[].teams[]
            for group in response.get('groups', []):
                for child in group.get('children', []):
                    conf_name = child.get('name', 'Unknown')
                    teams_data = child.get('teams', [])

                    if not teams_data:
                        continue

                    teams = []
                    for t in teams_data:
                        teams.append({
                            'id': t.get('id'),
                            'slug': t.get('slug'),
                            'name': t.get('displayName') or t.get('name'),
                            'abbreviation': t.get('abbreviation'),
                            'shortName': t.get('shortDisplayName'),
                            'logo': t.get('logos', [{}])[0].get('href') if t.get('logos') else None,
                            'color': t.get('color'),
                            'alternateColor': t.get('alternateColor')
                        })

                    if teams:
                        conferences[conf_name] = teams

            total_teams = sum(len(t) for t in conferences.values())
            logger.info(f"Found {total_teams} teams across {len(conferences)} conferences for {league}")
            return conferences

        except Exception as e:
            logger.error(f"Error fetching teams by conference for {league}: {e}")
            return None

    def get_all_teams_by_conference(self, sport: str, league: str) -> Optional[List[Dict]]:
        """
        Get ALL teams for a college league, organized by conference, with independents included.

        This is the recommended helper for college team imports. It:
        - For basketball: Uses /groups endpoint (has conferences with teams)
        - For football: Uses standings API (FBS conferences with teams)
        - Adds any teams not in conferences to "Other Teams"
        - Returns a sorted list of conference dicts ready for UI display

        Args:
            sport: Sport type (e.g., 'basketball', 'football')
            league: League code (e.g., 'mens-college-basketball', 'college-football')

        Returns:
            List of conference dicts sorted alphabetically (Other Teams at end):
            [
                {"name": "Big 12 Conference", "teams": [...]},
                {"name": "SEC", "teams": [...]},
                {"name": "— Other Teams —", "teams": [...]}
            ]
            Returns None on error or for non-college leagues.
        """
        # Only works for college leagues
        if 'college' not in league.lower():
            logger.debug(f"get_all_teams_by_conference only supports college leagues, not {league}")
            return None

        try:
            # College football uses standings API (FBS only, has full team data)
            # College basketball uses /groups endpoint (has conferences with teams)
            if league == 'college-football':
                return self._get_football_teams_by_conference(sport, league)
            else:
                return self._get_basketball_teams_by_conference(sport, league)

        except Exception as e:
            logger.error(f"Error in get_all_teams_by_conference for {league}: {e}")
            return None

    def _get_football_teams_by_conference(self, sport: str, league: str) -> Optional[List[Dict]]:
        """
        Get FBS football teams organized by conference using standings API.

        The /groups endpoint for football doesn't have conference-level data,
        but the standings API provides FBS conferences with full team data.
        """
        standings_url = f"https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings"
        logger.info(f"Fetching FBS teams by conference from standings API")

        try:
            standings_data = self._make_request(standings_url)
            if not standings_data or 'children' not in standings_data:
                logger.warning(f"No standings data found for {league}")
                return None

            result = []

            for child in standings_data.get('children', []):
                conf_name = child.get('name', 'Unknown')
                entries = child.get('standings', {}).get('entries', [])

                if not entries:
                    continue

                teams = []
                for entry in entries:
                    team_data = entry.get('team', {})
                    teams.append({
                        'id': team_data.get('id'),
                        'slug': team_data.get('slug'),
                        'name': team_data.get('displayName') or team_data.get('name'),
                        'abbreviation': team_data.get('abbreviation'),
                        'shortName': team_data.get('shortDisplayName'),
                        'logo': team_data.get('logos', [{}])[0].get('href') if team_data.get('logos') else None,
                        'color': team_data.get('color'),
                        'alternateColor': team_data.get('alternateColor')
                    })

                if teams:
                    result.append({
                        'name': conf_name,
                        'teams': sorted(teams, key=lambda x: x.get('name', ''))
                    })

            # Sort conferences alphabetically, but keep Independents at end
            result.sort(key=lambda x: ('zzz' if 'independent' in x['name'].lower() else x['name'].lower()))

            total = sum(len(c['teams']) for c in result)
            logger.info(f"_get_football_teams_by_conference: {len(result)} conferences, {total} FBS teams")
            return result

        except Exception as e:
            logger.error(f"Error fetching football teams by conference: {e}")
            return None

    def _get_basketball_teams_by_conference(self, sport: str, league: str) -> Optional[List[Dict]]:
        """
        Get basketball teams organized by conference using /groups endpoint.
        Also includes independents not in any conference.
        """
        # Get teams organized by conference (single API call)
        conferences_data = self.get_teams_by_conference_batch(sport, league)

        # Get ALL teams (includes independents not in conferences)
        all_teams = self.get_league_teams(sport, league)

        if not all_teams:
            return None

        result = []

        if conferences_data:
            # Track which teams are in conferences
            conf_team_ids = set()

            # Add each conference with its teams (sorted alphabetically)
            for conf_name in sorted(conferences_data.keys()):
                conf_teams = conferences_data[conf_name]
                for t in conf_teams:
                    conf_team_ids.add(str(t.get('id')))

                result.append({
                    'name': conf_name,
                    'teams': sorted(conf_teams, key=lambda x: x.get('name', ''))
                })

            # Find teams not in any conference (independents/transitioning)
            other_teams = [t for t in all_teams if str(t.get('id')) not in conf_team_ids]
            if other_teams:
                result.append({
                    'name': '— Other Teams —',
                    'teams': sorted(other_teams, key=lambda x: x.get('name', ''))
                })

            total = sum(len(c['teams']) for c in result)
            logger.info(f"_get_basketball_teams_by_conference: {len(result)} conferences, {total} teams for {league}")
        else:
            # No conference data available - return flat list
            result.append({
                'name': 'All Teams',
                'teams': sorted(all_teams, key=lambda x: x.get('name', ''))
            })
            logger.info(f"_get_basketball_teams_by_conference: no conference data, {len(all_teams)} teams for {league}")

        return result
