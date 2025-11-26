"""ESPN API Client for fetching sports schedules and team data"""
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import time
from utils.logger import get_logger

logger = get_logger(__name__)

class ESPNClient:
    """Client for ESPN's public API"""

    def __init__(self, base_url: str = "https://site.api.espn.com/apis/site/v2/sports", db_path: str = None):
        self.base_url = base_url
        self.db_path = db_path
        self.timeout = 10
        self.retry_count = 3
        self.retry_delay = 1  # seconds

        # Cache for team stats (refreshes every 6 hours)
        self._stats_cache = {}
        self._cache_duration = timedelta(hours=6)

    def _make_request(self, url: str) -> Optional[Dict]:
        """Make HTTP request with retry logic"""
        for attempt in range(self.retry_count):
            try:
                response = requests.get(url, timeout=self.timeout)
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

        Args:
            sport: Sport type (e.g., 'basketball', 'football')
            league: League identifier (e.g., 'nba', 'nfl')
            group_id: Group ID (conference or division)

        Returns:
            tuple: (name, abbreviation) or ('', '') if not found
        """
        try:
            url = f"http://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/groups/{group_id}"
            group_data = self._make_request(url)

            if group_data:
                # Get both full name and abbreviation
                name = group_data.get('shortName') or group_data.get('name', '')
                abbrev = group_data.get('abbreviation', '')
                return (name, abbrev)
            return ('', '')
        except Exception as e:
            logger.error(f"Error fetching group name for ID {group_id}: {e}")
            return ('', '')

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
        Fetch team schedule from ESPN API

        Args:
            sport: Sport type (e.g., 'basketball', 'football', 'soccer')
            league: League identifier (e.g., 'nba', 'nfl', 'eng.1')
            team_slug: Team slug/ID (e.g., 'detroit-pistons', '8')
            days_ahead: Number of days ahead to fetch

        Returns:
            Dict with team schedule data or None if failed
        """
        url = f"{self.base_url}/{sport}/{league}/teams/{team_slug}/schedule"
        return self._make_request(url)

    def get_team_info(self, sport: str, league: str, team_id: str) -> Optional[Dict]:
        """
        Fetch team information (name, logo, colors, etc.)

        Args:
            sport: Sport type
            league: League identifier
            team_id: Team ID

        Returns:
            Dict with team info or None if failed
        """
        url = f"{self.base_url}/{sport}/{league}/teams/{team_id}"
        return self._make_request(url)

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
        # Check cache first
        cache_key = f"{sport}_{league}_{team_id}"
        if cache_key in self._stats_cache:
            cached_data, cached_time = self._stats_cache[cache_key]
            if datetime.now() - cached_time < self._cache_duration:
                return cached_data

        # Fetch fresh data
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
        Fetch scoreboard for a specific date

        Args:
            sport: Sport type
            league: League identifier
            date: Date in YYYYMMDD format (default: today)

        Returns:
            Dict with scoreboard data
        """
        if date is None:
            date = datetime.now().strftime('%Y%m%d')

        url = f"{self.base_url}/{sport}/{league}/scoreboard?dates={date}"
        return self._make_request(url)

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

        # Calculate future cutoff as end of the Nth day
        cutoff_date = now.date() + timedelta(days=days_ahead - 1)
        cutoff_future = datetime.combine(cutoff_date, datetime.max.time()).replace(tzinfo=tz.utc)

        # Use provided cutoff_past_datetime or default to 6 hours ago
        if cutoff_past_datetime:
            # Convert to UTC for comparison
            cutoff_past = cutoff_past_datetime.astimezone(tz.utc) if cutoff_past_datetime.tzinfo else cutoff_past_datetime.replace(tzinfo=tz.utc)
        else:
            cutoff_past = now - timedelta(hours=6)

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

                # Venue
                'venue': {
                    'name': competition['venue']['fullName'] if 'venue' in competition else None,
                    'city': competition['venue']['address']['city'] if 'venue' in competition and 'address' in competition['venue'] else None,
                    'state': competition['venue']['address']['state'] if 'venue' in competition and 'address' in competition['venue'] else None,
                },

                # Broadcast
                'broadcasts': [b.get('names', [None])[0] for b in competition.get('broadcasts', [])],

                # Status
                'status': {
                    'name': competition['status']['type']['name'],
                    'state': competition['status']['type']['state'],
                    'completed': competition['status']['type']['completed'],
                    'detail': competition['status']['type'].get('detail'),
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

    def get_league_teams(self, sport: str, league: str) -> Optional[List[Dict]]:
        """
        Fetch all teams in a league from ESPN API

        Args:
            sport: Sport type (basketball, football, etc.)
            league: League code (nba, nfl, etc.)

        Returns:
            List of team dictionaries with id, name, abbreviation, logo, etc.
        """
        url = f"{self.base_url}/{sport}/{league}/teams"
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
