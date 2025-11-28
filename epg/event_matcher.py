"""
ESPN Event Matcher for Event Channel EPG

Given two team IDs (from TeamMatcher), finds the matching ESPN event
and fetches enriched event data (odds, venue, broadcast, weather).

This module bridges the gap between:
- TeamMatcher: extracts team IDs from stream names
- EPG Generation: needs full event data for XMLTV output
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class EventMatcher:
    """
    Find ESPN events by team matchup and fetch enriched event data.

    Usage:
        from api.espn_client import ESPNClient
        from epg.event_matcher import EventMatcher

        espn = ESPNClient()
        matcher = EventMatcher(espn)

        # Find event by team IDs
        result = matcher.find_event(
            away_team_id='19',  # Giants
            home_team_id='17',  # Patriots
            league='nfl'
        )

        if result['found']:
            print(f"Event: {result['event']['name']}")
            print(f"Date: {result['event']['date']}")
            print(f"Venue: {result['event']['venue']['name']}")
    """

    # How many days ahead to search for games (30 days covers most scheduling)
    SEARCH_DAYS_AHEAD = 30

    # How many days back to search (only for in-progress games, not final)
    SEARCH_DAYS_BACK = 1

    def __init__(self, espn_client, db_connection_func=None):
        """
        Initialize EventMatcher.

        Args:
            espn_client: ESPNClient instance for API calls
            db_connection_func: Function that returns DB connection (for league config)
        """
        self.espn = espn_client
        self.db_connection_func = db_connection_func

        # Cache for league config
        self._league_config: Dict[str, Dict] = {}

    def _get_league_config(self, league_code: str) -> Optional[Dict]:
        """Get league configuration (sport, api_path) from database."""
        if league_code in self._league_config:
            return self._league_config[league_code]

        if not self.db_connection_func:
            # Fallback to common mappings
            fallback = {
                'nfl': {'sport': 'football', 'api_path': 'football/nfl'},
                'nba': {'sport': 'basketball', 'api_path': 'basketball/nba'},
                'nhl': {'sport': 'hockey', 'api_path': 'hockey/nhl'},
                'mlb': {'sport': 'baseball', 'api_path': 'baseball/mlb'},
                'mls': {'sport': 'soccer', 'api_path': 'soccer/usa.1'},
                'epl': {'sport': 'soccer', 'api_path': 'soccer/eng.1'},
                'laliga': {'sport': 'soccer', 'api_path': 'soccer/esp.1'},
                'bundesliga': {'sport': 'soccer', 'api_path': 'soccer/ger.1'},
                'seriea': {'sport': 'soccer', 'api_path': 'soccer/ita.1'},
                'ligue1': {'sport': 'soccer', 'api_path': 'soccer/fra.1'},
                # College sports
                'ncaam': {'sport': 'basketball', 'api_path': 'basketball/mens-college-basketball'},
                'ncaaw': {'sport': 'basketball', 'api_path': 'basketball/womens-college-basketball'},
                'ncaaf': {'sport': 'football', 'api_path': 'football/college-football'},
            }
            return fallback.get(league_code.lower())

        try:
            conn = self.db_connection_func()
            cursor = conn.cursor()
            result = cursor.execute(
                "SELECT sport, api_path FROM league_config WHERE league_code = ?",
                (league_code.lower(),)
            ).fetchone()
            conn.close()

            if result:
                config = {'sport': result[0], 'api_path': result[1]}
                self._league_config[league_code] = config
                return config
            return None
        except Exception as e:
            logger.error(f"Error fetching league config for {league_code}: {e}")
            return None

    def _parse_api_path(self, api_path: str) -> Tuple[str, str]:
        """Parse api_path into (sport, league) tuple."""
        parts = api_path.split('/')
        if len(parts) == 2:
            return parts[0], parts[1]
        return None, None

    def find_event(
        self,
        team1_id: str,
        team2_id: str,
        league: str,
        game_date: datetime = None,
        game_time: datetime = None,
        include_final_events: bool = False
    ) -> Dict[str, Any]:
        """
        Find an ESPN event between two teams.

        Searches team1's schedule for any game against team2, regardless
        of which team is home or away.

        Matching priority:
        1. If game_date + game_time provided: match exact date and closest time (for double-headers)
        2. If game_date provided: match that specific date
        3. Otherwise: return nearest upcoming game

        Args:
            team1_id: ESPN team ID for first team (from stream name)
            team2_id: ESPN team ID for second team (from stream name)
            league: League code (e.g., 'nfl', 'epl')
            game_date: Optional target date extracted from stream name
            game_time: Optional target time for double-header disambiguation
            include_final_events: Whether to include completed events from today (default False)

        Returns:
            Dict with:
            - found: bool
            - event: Full event dict (if found)
            - event_id: ESPN event ID (if found)
            - reason: Error message (if not found)
        """
        result = {
            'found': False,
            'team1_id': team1_id,
            'team2_id': team2_id,
            'league': league
        }

        # Get league config
        config = self._get_league_config(league)
        if not config:
            result['reason'] = f'Unknown league: {league}'
            return result

        sport, api_league = self._parse_api_path(config['api_path'])
        if not sport or not api_league:
            result['reason'] = f'Invalid api_path for league: {league}'
            return result

        # Fetch team1's schedule (contains all games including vs team2)
        logger.debug(f"Fetching schedule for team {team1_id} in {league}")
        schedule_data = self.espn.get_team_schedule(sport, api_league, team1_id)

        if not schedule_data or 'events' not in schedule_data:
            result['reason'] = f'Could not fetch schedule for team {team1_id}'
            return result

        # Search date range
        # Search window is based on current time
        now = datetime.now(ZoneInfo('UTC'))
        cutoff_past = now - timedelta(days=self.SEARCH_DAYS_BACK)
        cutoff_future = now + timedelta(days=self.SEARCH_DAYS_AHEAD)

        # Search for any game involving team2 (regardless of home/away)
        matching_events = []
        skipped_completed_game = False  # Track if we skipped a completed game from previous day

        for event in schedule_data.get('events', []):
            try:
                # Parse event date
                event_date_str = event.get('date', '')
                if not event_date_str:
                    continue

                event_date = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))

                # Skip events outside search window
                if event_date < cutoff_past or event_date > cutoff_future:
                    continue

                # Check if this game involves team2
                competitions = event.get('competitions', [])
                if not competitions:
                    continue

                # Check game status - allow completed games from today or later
                status = competitions[0].get('status', {})
                status_type = status.get('type', {})
                is_completed = status_type.get('completed', False) or 'FINAL' in status_type.get('name', '').upper()

                competition = competitions[0]
                competitors = competition.get('competitors', [])

                # Find if team2_id is one of the competitors
                team_ids_in_game = [
                    c.get('team', {}).get('id', c.get('id'))
                    for c in competitors
                ]

                # Convert to strings for comparison
                team_ids_in_game = [str(tid) for tid in team_ids_in_game if tid]

                # Check if this game involves team2
                if str(team2_id) not in team_ids_in_game:
                    continue

                # Filter completed games based on settings
                if is_completed:
                    today = datetime.now(ZoneInfo('UTC')).date()
                    event_day = event_date.date()
                    if event_day < today:
                        # Always skip completed games from previous days
                        skipped_completed_game = True
                        continue
                    elif event_day == today and not include_final_events:
                        # Skip completed games from today unless include_final_events is True
                        skipped_completed_game = True
                        continue

                # Found a matching game!
                matching_events.append({
                    'event': event,
                    'event_date': event_date,
                    'event_id': event.get('id')
                })

            except Exception as e:
                logger.warning(f"Error parsing event: {e}")
                continue

        if not matching_events:
            if skipped_completed_game:
                result['reason'] = 'Game completed (excluded)'
            else:
                result['reason'] = f'No game found between teams'
            return result

        # Sort by date
        matching_events.sort(key=lambda x: x['event_date'])

        # Select best match based on available info
        best_match = None

        if game_date:
            # If we have a target date, find games on that date
            target_date = game_date.date()
            date_matches = [
                e for e in matching_events
                if e['event_date'].date() == target_date
            ]

            if date_matches:
                if game_time and len(date_matches) > 1:
                    # Double-header: find game closest to target time
                    target_hour = game_time.hour + game_time.minute / 60
                    date_matches.sort(key=lambda e: abs(
                        (e['event_date'].hour + e['event_date'].minute / 60) - target_hour
                    ))
                best_match = date_matches[0]
                logger.debug(f"Matched game on target date {target_date}")
            else:
                # No exact date match, fall back to nearest upcoming
                logger.debug(f"No game on target date {target_date}, using nearest")

        if not best_match:
            # No date provided or no date match - prioritize today's games, then nearest upcoming
            now = datetime.now(ZoneInfo('UTC'))
            today = now.date()

            # First, check for any games TODAY (even if already started)
            todays_games = [e for e in matching_events if e['event_date'].date() == today]
            if todays_games:
                best_match = todays_games[0]  # First game today
                logger.debug(f"Selected today's game: {best_match['event_id']}")
            else:
                # No games today - use nearest upcoming
                upcoming = [e for e in matching_events if e['event_date'] >= now]
                if upcoming:
                    best_match = upcoming[0]  # Nearest upcoming
                else:
                    best_match = matching_events[-1]  # Most recent (shouldn't happen with FINAL filter)

        # Parse the event into our standard format
        parsed_event = self._parse_event(best_match['event'], sport, api_league)

        result['found'] = True
        result['event'] = parsed_event
        result['event_id'] = best_match['event_id']
        result['event_date'] = best_match['event_date'].isoformat()

        return result

    def _parse_event(self, raw_event: Dict, sport: str, league: str) -> Dict[str, Any]:
        """
        Parse raw ESPN event into structured format.

        Extracts all relevant fields for EPG generation.
        """
        event = {
            'id': raw_event.get('id'),
            'uid': raw_event.get('uid'),
            'name': raw_event.get('name'),
            'short_name': raw_event.get('shortName'),
            'date': raw_event.get('date'),
            'sport': sport,
            'league': league
        }

        # Parse competition data
        competitions = raw_event.get('competitions', [])
        if competitions:
            comp = competitions[0]

            # Venue
            venue = comp.get('venue', {})
            event['venue'] = {
                'name': venue.get('fullName') or venue.get('shortName'),
                'city': venue.get('address', {}).get('city'),
                'state': venue.get('address', {}).get('state'),
                'indoor': venue.get('indoor', False)
            }

            # Competitors (teams)
            competitors = comp.get('competitors', [])
            for competitor in competitors:
                team_data = competitor.get('team', {})
                # ESPN uses 'records' (plural) in scoreboard API, 'record' (singular) in schedule API
                records_data = competitor.get('records') or competitor.get('record') or []
                team_info = {
                    'id': team_data.get('id'),
                    'name': team_data.get('displayName') or team_data.get('name'),
                    'abbrev': team_data.get('abbreviation'),  # Use 'abbrev' for consistency with template engine
                    'logo': team_data.get('logo'),
                    'color': team_data.get('color'),
                    'score': competitor.get('score'),
                    'record': self._extract_record(records_data)
                }

                if competitor.get('homeAway') == 'home':
                    event['home_team'] = team_info
                else:
                    event['away_team'] = team_info

            # Broadcasts
            broadcasts = comp.get('broadcasts', [])
            event['broadcasts'] = []
            for b in broadcasts:
                names = b.get('names', [])
                if names:
                    event['broadcasts'].extend(names)
                elif b.get('name'):
                    event['broadcasts'].append(b.get('name'))

            # Odds
            odds = comp.get('odds', [])
            if odds:
                primary_odds = odds[0]
                event['odds'] = {
                    'spread': primary_odds.get('details'),
                    'over_under': primary_odds.get('overUnder'),
                    'home_moneyline': primary_odds.get('homeTeamOdds', {}).get('moneyLine'),
                    'away_moneyline': primary_odds.get('awayTeamOdds', {}).get('moneyLine'),
                    'provider': primary_odds.get('provider', {}).get('name')
                }

            # Status
            status = comp.get('status', {})
            status_type = status.get('type', {})
            event['status'] = {
                'name': status_type.get('name'),
                'state': status_type.get('state'),
                'completed': status_type.get('completed', False),
                'detail': status_type.get('detail') or status_type.get('shortDetail')
            }

            # Weather (outdoor venues)
            weather = comp.get('weather', {})
            if weather:
                event['weather'] = {
                    'temperature': weather.get('temperature'),
                    'display': weather.get('displayValue'),
                    'condition': weather.get('conditionId')
                }

            # Store raw competition for template engine
            event['competitions'] = [comp]

        # Season info
        season = raw_event.get('season', {})
        event['season'] = {
            'year': season.get('year'),
            'type': season.get('type'),
            'slug': season.get('slug')
        }

        return event

    def _extract_record(self, records: List[Dict]) -> Dict[str, Any]:
        """Extract team record from records array."""
        result = {'summary': '0-0', 'wins': 0, 'losses': 0, 'ties': 0}

        if not records:
            return result

        # Find overall record
        for record in records:
            if record.get('type') == 'total' or record.get('name') == 'overall':
                result['summary'] = record.get('summary') or record.get('displayValue', '0-0')

                # Parse wins/losses from summary if not provided
                summary = result['summary']
                if '-' in summary:
                    parts = summary.split('-')
                    try:
                        result['wins'] = int(parts[0])
                        result['losses'] = int(parts[1])
                        if len(parts) > 2:
                            result['ties'] = int(parts[2])
                    except (ValueError, IndexError):
                        pass
                break

        return result

    def enrich_event_with_scoreboard(
        self,
        event: Dict,
        league: str
    ) -> Dict[str, Any]:
        """
        Enrich an event with live scoreboard data.

        Fetches the scoreboard for the event's date and merges
        additional data like live odds, real-time scores, etc.

        Args:
            event: Event dict from find_event()
            league: League code

        Returns:
            Enriched event dict
        """
        config = self._get_league_config(league)
        if not config:
            return event

        sport, api_league = self._parse_api_path(config['api_path'])
        if not sport:
            return event

        # Get event date
        event_date_str = event.get('date')
        if not event_date_str:
            return event

        try:
            event_date = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
            date_str = event_date.strftime('%Y%m%d')
        except Exception:
            return event

        # Fetch scoreboard
        logger.debug(f"Fetching scoreboard for {date_str} in {league}")
        scoreboard_data = self.espn.get_scoreboard(sport, api_league, date_str)

        if not scoreboard_data or 'events' not in scoreboard_data:
            return event

        # Find matching event in scoreboard
        event_id = event.get('id')
        for sb_event in scoreboard_data.get('events', []):
            if sb_event.get('id') == event_id:
                # Merge scoreboard data
                return self._merge_scoreboard_data(event, sb_event)

        return event

    def _merge_scoreboard_data(self, event: Dict, scoreboard_event: Dict) -> Dict:
        """Merge scoreboard data into event."""
        # Update competitions with scoreboard data (has more real-time info)
        if 'competitions' in scoreboard_event:
            comp = scoreboard_event['competitions'][0] if scoreboard_event['competitions'] else {}

            # Update odds from scoreboard (more current)
            if comp.get('odds'):
                odds = comp['odds'][0]
                event['odds'] = {
                    'spread': odds.get('details'),
                    'over_under': odds.get('overUnder'),
                    'home_moneyline': odds.get('homeTeamOdds', {}).get('moneyLine'),
                    'away_moneyline': odds.get('awayTeamOdds', {}).get('moneyLine'),
                    'provider': odds.get('provider', {}).get('name')
                }

            # Update status (real-time)
            if comp.get('status'):
                status = comp['status']
                status_type = status.get('type', {})
                event['status'] = {
                    'name': status_type.get('name'),
                    'state': status_type.get('state'),
                    'completed': status_type.get('completed', False),
                    'detail': status_type.get('detail') or status_type.get('shortDetail')
                }

            # Update scores (real-time)
            for competitor in comp.get('competitors', []):
                team_id = competitor.get('team', {}).get('id')
                score = competitor.get('score')

                if event.get('home_team', {}).get('id') == team_id:
                    event['home_team']['score'] = score
                elif event.get('away_team', {}).get('id') == team_id:
                    event['away_team']['score'] = score

            # Update weather
            if comp.get('weather'):
                weather = comp['weather']
                event['weather'] = {
                    'temperature': weather.get('temperature'),
                    'display': weather.get('displayValue'),
                    'condition': weather.get('conditionId')
                }

            # Store updated competition
            event['competitions'] = [comp]

        return event

    def find_and_enrich(
        self,
        team1_id: str,
        team2_id: str,
        league: str,
        game_date: datetime = None,
        game_time: datetime = None,
        include_final_events: bool = False
    ) -> Dict[str, Any]:
        """
        Find event and enrich with scoreboard data in one call.

        Convenience method that combines find_event() and enrich_event_with_scoreboard().

        Args:
            team1_id: ESPN team ID for first team (from stream name)
            team2_id: ESPN team ID for second team (from stream name)
            league: League code
            game_date: Optional target date from stream name
            game_time: Optional target time for double-header disambiguation
            include_final_events: Whether to include completed events from today (default False)

        Returns:
            Result dict with enriched event (if found)
        """
        result = self.find_event(
            team1_id, team2_id, league,
            game_date=game_date,
            game_time=game_time,
            include_final_events=include_final_events
        )

        if result['found']:
            result['event'] = self.enrich_event_with_scoreboard(
                result['event'],
                league
            )

        return result


# Convenience function for standalone use
def create_event_matcher():
    """Create an EventMatcher instance with default configuration."""
    from api.espn_client import ESPNClient
    from database import get_connection

    espn = ESPNClient()
    return EventMatcher(espn, db_connection_func=get_connection)
