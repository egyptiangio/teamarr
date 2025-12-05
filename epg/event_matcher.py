"""
ESPN Event Matcher for Event Channel EPG

Given two team IDs (from TeamMatcher), finds the matching ESPN event
and fetches enriched event data (odds, venue, broadcast, weather).

This module bridges the gap between:
- TeamMatcher: extracts team IDs from stream names
- EPG Generation: needs full event data for XMLTV output
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from zoneinfo import ZoneInfo

from epg.league_config import get_league_config, parse_api_path, is_college_league, is_soccer_league
from utils.logger import get_logger

logger = get_logger(__name__)


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

    # Default days ahead to search for games (can be overridden via setting)
    DEFAULT_SEARCH_DAYS_AHEAD = 7

    # How many days back to search (only for in-progress games, not final)
    SEARCH_DAYS_BACK = 1

    def __init__(self, espn_client, db_connection_func=None, lookahead_days: int = None):
        """
        Initialize EventMatcher.

        Args:
            espn_client: ESPNClient instance for API calls
            db_connection_func: Function that returns DB connection (for league config)
            lookahead_days: How many days ahead to search for events (default from setting or 7)
        """
        self.espn = espn_client
        self.db_connection_func = db_connection_func
        self.lookahead_days = lookahead_days or self.DEFAULT_SEARCH_DAYS_AHEAD

        # Cache for league config
        self._league_config: Dict[str, Dict] = {}

    def _get_league_config(self, league_code: str) -> Optional[Dict]:
        """Get league configuration (sport, api_path) using shared module."""
        return get_league_config(league_code, self.db_connection_func, self._league_config)

    def _filter_matching_events(
        self,
        events: List[Dict],
        team2_id: str,
        include_final_events: bool
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Filter schedule events to find games involving team2.

        Args:
            events: Raw events from ESPN schedule API
            team2_id: ESPN team ID to match against
            include_final_events: Whether to include completed events from today

        Returns:
            Tuple of (matching_events, skip_reason)
            - matching_events: List of {event, event_date, event_id} dicts
            - skip_reason: None, 'past_game', or 'today_final' indicating why game was skipped
        """
        now = datetime.now(ZoneInfo('UTC'))
        cutoff_past = now - timedelta(days=self.SEARCH_DAYS_BACK)
        cutoff_future = now + timedelta(days=self.lookahead_days)
        today = now.date()

        matching_events = []
        skip_reason = None  # 'past_game' or 'today_final'

        # TRACE: Log search window
        logger.debug(f"[TRACE] _filter_matching_events | looking for opponent={team2_id} | window={cutoff_past.date()} to {cutoff_future.date()} | include_final={include_final_events}")

        for event in events:
            try:
                # Parse event date
                event_date_str = event.get('date', '')
                if not event_date_str:
                    continue

                event_date = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                event_name = event.get('name', 'N/A')
                event_id = event.get('id', 'N/A')

                # Skip events outside search window
                if event_date < cutoff_past or event_date > cutoff_future:
                    logger.debug(f"[TRACE]   Skipping event {event_id} ({event_name}) - outside window ({event_date.date()})")
                    continue

                # Check if this game involves team2
                competitions = event.get('competitions', [])
                if not competitions:
                    continue

                competition = competitions[0]

                # Check game status
                status = competition.get('status', {})
                status_type = status.get('type', {})
                is_completed = status_type.get('completed', False) or 'FINAL' in status_type.get('name', '').upper()

                # Check if team2 is a competitor
                competitors = competition.get('competitors', [])
                team_ids_in_game = [
                    str(c.get('team', {}).get('id', c.get('id')))
                    for c in competitors
                    if c.get('team', {}).get('id') or c.get('id')
                ]

                if str(team2_id) not in team_ids_in_game:
                    # TRACE: Log what teams ARE in this game (to help debug wrong team matches)
                    team_names_in_game = [
                        c.get('team', {}).get('displayName', c.get('team', {}).get('name', 'Unknown'))
                        for c in competitors
                    ]
                    logger.debug(f"[TRACE]   Event {event_id} on {event_date.date()}: {' vs '.join(team_names_in_game)} - opponent {team2_id} NOT in game (teams: {team_ids_in_game})")
                    continue

                # Filter completed games based on settings
                if is_completed:
                    event_day = event_date.date()
                    if event_day < today:
                        skip_reason = 'past_game'  # Game from a previous day
                        logger.debug(f"[TRACE]   Event {event_id} ({event_name}) - skipped: past completed game ({event_day})")
                        continue
                    elif event_day == today and not include_final_events:
                        skip_reason = 'today_final'  # Today's game, but finals excluded
                        logger.debug(f"[TRACE]   Event {event_id} ({event_name}) - skipped: today's final (excluded)")
                        continue

                # Found a matching game!
                logger.debug(f"[TRACE]   MATCH! Event {event_id} ({event_name}) on {event_date.strftime('%Y-%m-%d %H:%M')} | completed={is_completed}")
                matching_events.append({
                    'event': event,
                    'event_date': event_date,
                    'event_id': event.get('id')
                })

            except Exception as e:
                logger.warning(f"Error parsing event: {e}")
                continue

        return matching_events, skip_reason

    def _select_best_match(
        self,
        matching_events: List[Dict],
        game_date: Optional[datetime],
        game_time: Optional[datetime]
    ) -> Dict:
        """
        Select the best matching event based on date/time hints.

        Priority:
        1. If game_date + game_time provided: match date and closest time
        2. If game_date provided: match that date
        3. Otherwise: today's games first, then nearest upcoming

        Args:
            matching_events: Sorted list of matching events
            game_date: Optional target date from stream name
            game_time: Optional target time for double-header disambiguation

        Returns:
            Best matching event dict
        """
        if game_date:
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
                logger.debug(f"Matched game on target date {target_date}")
                return date_matches[0]
            else:
                logger.debug(f"No game on target date {target_date}, using nearest")

        # No date provided or no date match - prioritize today's games (including finals), then upcoming
        now = datetime.now(ZoneInfo('UTC'))
        today = now.date()

        # Check for today's games first
        todays_games = [e for e in matching_events if e['event_date'].date() == today]
        if todays_games:
            logger.debug(f"Selected today's game: {todays_games[0]['event_id']}")
            return todays_games[0]

        # No today's games - check for games that have already started/finished today
        # This handles timezone edge cases where a game at 7pm EST on Nov 30
        # would be 12am UTC on Dec 1, causing 'today' in UTC to miss it
        # Look for any game in the past 24 hours that's completed
        past_24h = now - timedelta(hours=24)
        recent_completed = [
            e for e in matching_events
            if e['event_date'] >= past_24h and e['event_date'] < now
        ]
        if recent_completed:
            # Sort by date descending (most recent first) and return the most recent
            recent_completed.sort(key=lambda x: x['event_date'], reverse=True)
            logger.debug(f"Selected recent completed game: {recent_completed[0]['event_id']}")
            return recent_completed[0]

        upcoming = [e for e in matching_events if e['event_date'] >= now]
        if upcoming:
            return upcoming[0]

        return matching_events[-1]

    def _search_team_schedule(
        self,
        primary_team_id: str,
        opponent_team_id: str,
        sport: str,
        api_league: str,
        include_final_events: bool
    ) -> Tuple[List[Dict], Optional[str], Optional[str]]:
        """
        Search a team's schedule for games against an opponent.

        Args:
            primary_team_id: Team whose schedule to fetch
            opponent_team_id: Opponent to search for
            sport: Sport type for API call
            api_league: League code for API call
            include_final_events: Whether to include completed events

        Returns:
            Tuple of (matching_events, skip_reason, error_reason)
            - skip_reason: None, 'past_game', or 'today_final'
        """
        schedule_data = self.espn.get_team_schedule(sport, api_league, primary_team_id)

        if not schedule_data or 'events' not in schedule_data:
            logger.debug(f"[TRACE] _search_team_schedule | team={primary_team_id} | no schedule data returned")
            return [], None, f'Could not fetch schedule for team {primary_team_id}'

        events = schedule_data.get('events', [])
        logger.debug(f"[TRACE] _search_team_schedule | team={primary_team_id} | {len(events)} total events in schedule")

        matching_events, skip_reason = self._filter_matching_events(
            events,
            opponent_team_id,
            include_final_events
        )

        logger.debug(f"[TRACE] _search_team_schedule | team={primary_team_id} vs opponent={opponent_team_id} | {len(matching_events)} matches found | skip_reason={skip_reason}")

        return matching_events, skip_reason, None

    def _search_scoreboard(
        self,
        team1_id: str,
        team2_id: str,
        sport: str,
        api_league: str,
        include_final_events: bool
    ) -> Tuple[List[Dict], Optional[str], Optional[str]]:
        """
        Search scoreboard for games between two teams.

        Used as fallback for soccer leagues where the schedule API only returns
        past results, not future fixtures. The scoreboard API has upcoming games.

        Args:
            team1_id: First team ID
            team2_id: Second team ID
            sport: Sport type for API call
            api_league: League code for API call
            include_final_events: Whether to include completed events

        Returns:
            Tuple of (matching_events, skip_reason, error_reason)
        """
        now_utc = datetime.now(ZoneInfo('UTC'))

        # Collect events from scoreboard that involve BOTH teams
        candidate_events = []

        # Search scoreboard for each day in the lookahead window
        for day_offset in range(self.lookahead_days):
            check_date = now_utc + timedelta(days=day_offset)
            date_str = check_date.strftime('%Y%m%d')

            logger.debug(f"[TRACE] _search_scoreboard | checking {date_str} for teams {team1_id} vs {team2_id}")

            try:
                scoreboard_data = self.espn.get_scoreboard(sport, api_league, date_str)
                if not scoreboard_data or 'events' not in scoreboard_data:
                    continue

                for sb_event in scoreboard_data.get('events', []):
                    # Check if this event involves both teams
                    competitions = sb_event.get('competitions', [])
                    if not competitions:
                        continue

                    competitors = competitions[0].get('competitors', [])
                    team_ids_in_event = {str(c.get('team', {}).get('id', '')) for c in competitors}

                    if str(team1_id) in team_ids_in_event and str(team2_id) in team_ids_in_event:
                        candidate_events.append(sb_event)
                        logger.debug(f"[TRACE] _search_scoreboard | candidate: {sb_event.get('name')} on {sb_event.get('date')}")

            except Exception as e:
                logger.warning(f"[TRACE] _search_scoreboard | error fetching scoreboard for {date_str}: {e}")
                continue

        if not candidate_events:
            logger.debug(f"[TRACE] _search_scoreboard | no candidates found for {team1_id} vs {team2_id}")
            return [], None, None

        # Use existing filter logic (pass team2_id but events already contain both teams)
        matching_events, skip_reason = self._filter_matching_events(
            candidate_events, team2_id, include_final_events
        )

        logger.debug(f"[TRACE] _search_scoreboard | team1={team1_id} vs team2={team2_id} | {len(matching_events)} matches found | skip_reason={skip_reason}")

        return matching_events, skip_reason, None

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

        Searches team1's schedule first, then falls back to team2's schedule
        if no game is found. This handles edge cases where one team's schedule
        might be incomplete in ESPN's API.

        Args:
            team1_id: ESPN team ID for first team (from stream name)
            team2_id: ESPN team ID for second team (from stream name)
            league: League code (e.g., 'nfl', 'epl')
            game_date: Optional target date extracted from stream name
            game_time: Optional target time for double-header disambiguation
            include_final_events: Whether to include completed events from today

        Returns:
            Dict with found, event, event_id, reason (if not found)
        """
        # TRACE: Log the search parameters
        logger.debug(f"[TRACE] find_event START | team1={team1_id} vs team2={team2_id} | league={league} | target_date={game_date.date() if game_date else None} | include_final={include_final_events}")

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
            logger.debug(f"[TRACE] find_event FAIL | reason=unknown league {league}")
            return result

        sport, api_league = parse_api_path(config['api_path'])
        if not sport or not api_league:
            result['reason'] = f'Invalid api_path for league: {league}'
            logger.debug(f"[TRACE] find_event FAIL | reason=invalid api_path")
            return result

        # Try team1's schedule first
        logger.debug(f"[TRACE] Searching team1 ({team1_id}) schedule for opponent {team2_id}")
        matching_events, skip_reason, error = self._search_team_schedule(
            team1_id, team2_id, sport, api_league, include_final_events
        )

        # If no match found on team1's schedule, try team2's schedule as fallback
        if not matching_events and not skip_reason:
            logger.debug(f"[TRACE] No match on team1 schedule, trying team2 ({team2_id}) schedule for opponent {team1_id}")
            matching_events, skip_reason, error = self._search_team_schedule(
                team2_id, team1_id, sport, api_league, include_final_events
            )
            if matching_events:
                logger.info(f"[TRACE] Found game via team2 ({team2_id}) schedule fallback")

        # Soccer fallback: schedule API only returns past results, use scoreboard for future games
        if not matching_events and not skip_reason and is_soccer_league(league):
            logger.debug(f"[TRACE] No match in schedule, trying scoreboard fallback for soccer league {league}")
            matching_events, skip_reason, error = self._search_scoreboard(
                team1_id, team2_id, sport, api_league, include_final_events
            )
            if matching_events:
                logger.info(f"[TRACE] Found game via scoreboard fallback for soccer league {league}")

        if error and not matching_events:
            result['reason'] = error
            logger.debug(f"[TRACE] find_event FAIL | reason={error}")
            return result

        if not matching_events:
            # Provide specific reason based on why game was skipped
            if skip_reason == 'past_game':
                result['reason'] = 'Game already completed (past)'
            elif skip_reason == 'today_final':
                result['reason'] = 'Game completed (excluded)'
            else:
                result['reason'] = 'No game found between teams'
            logger.debug(f"[TRACE] find_event FAIL | team1={team1_id} vs team2={team2_id} | reason={result['reason']} | skip_reason={skip_reason}")
            return result

        # Sort by date and select best match
        matching_events.sort(key=lambda x: x['event_date'])

        # TRACE: Log all matching events found
        logger.debug(f"[TRACE] Found {len(matching_events)} matching event(s):")
        for i, evt in enumerate(matching_events):
            evt_date = evt['event_date'].strftime('%Y-%m-%d %H:%M')
            evt_name = evt['event'].get('name', 'N/A')
            logger.debug(f"[TRACE]   [{i+1}] {evt_date} - {evt_name} (id={evt['event_id']})")

        best_match = self._select_best_match(matching_events, game_date, game_time)

        # Parse and return
        result['found'] = True
        result['event'] = self._parse_event(best_match['event'], sport, api_league)
        result['event_id'] = best_match['event_id']
        result['event_date'] = best_match['event_date'].isoformat()

        # TRACE: Log successful match
        logger.debug(f"[TRACE] find_event OK | selected event_id={best_match['event_id']} on {best_match['event_date'].strftime('%Y-%m-%d %H:%M')}")

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
                'detail': status_type.get('detail') or status_type.get('shortDetail'),
                'period': status.get('period', 0)  # For overtime detection
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

        sport, api_league = parse_api_path(config['api_path'])
        if not sport:
            return event

        # Get event date
        event_date_str = event.get('date')
        if not event_date_str:
            return event

        try:
            event_date = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
            date_str = event_date.strftime('%Y%m%d')
        except Exception as e:
            logger.debug(f"Could not parse event date '{event_date_str}': {e}")
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
                    'detail': status_type.get('detail') or status_type.get('shortDetail'),
                    'period': status.get('period', 0)  # For overtime detection
                }

            # Update scores and records (real-time from scoreboard)
            for competitor in comp.get('competitors', []):
                team_id = competitor.get('team', {}).get('id')
                score = competitor.get('score')
                # Scoreboard uses 'records' (plural) with 'summary' field
                records_data = competitor.get('records') or []

                if event.get('home_team', {}).get('id') == team_id:
                    event['home_team']['score'] = score
                    if records_data:
                        event['home_team']['record'] = self._extract_record(records_data)
                elif event.get('away_team', {}).get('id') == team_id:
                    event['away_team']['score'] = score
                    if records_data:
                        event['away_team']['record'] = self._extract_record(records_data)

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

            # Update broadcasts from scoreboard (more current than schedule)
            if comp.get('broadcasts'):
                broadcast_names = []
                for broadcast in comp.get('broadcasts', []):
                    names = broadcast.get('names', [])
                    if names:
                        broadcast_names.extend(names)
                if broadcast_names:
                    event['broadcasts'] = broadcast_names

        return event

    def enrich_with_team_stats(
        self,
        event: Dict,
        league: str
    ) -> Dict[str, Any]:
        """
        Enrich event with current team stats from team endpoint.

        This fills in data that the schedule/scoreboard APIs don't provide
        for future games, including:
        - Current team records (always accurate, not just for today's games)
        - Team logos and colors
        - Conference and division info
        - Rank (college), playoff seed (pro), and streak

        Args:
            event: Event dict from find_event() or enrich_event_with_scoreboard()
            league: League code

        Returns:
            Enriched event dict
        """
        config = self._get_league_config(league)
        if not config:
            return event

        sport, api_league = parse_api_path(config['api_path'])
        if not sport:
            return event

        # Determine if this is a college league
        is_college = is_college_league(league)

        # Enrich both teams using shared helper
        self._enrich_single_team(event, 'home_team', sport, api_league, is_college)
        self._enrich_single_team(event, 'away_team', sport, api_league, is_college)

        return event

    def _enrich_single_team(
        self,
        event: Dict,
        team_key: str,
        sport: str,
        api_league: str,
        is_college: bool
    ) -> None:
        """
        Enrich a single team (home or away) with stats from ESPN team endpoint.

        Args:
            event: Event dict to modify in place
            team_key: Either 'home_team' or 'away_team'
            sport: Sport code (e.g., 'football')
            api_league: League code for API (e.g., 'nfl')
            is_college: Whether this is a college league
        """
        team = event.get(team_key, {})
        if not team.get('id'):
            return

        team_id = team['id']

        # Get team info (has logos, colors)
        team_info = self.espn.get_team_info(sport, api_league, team_id)
        if team_info and 'team' in team_info:
            team_data = team_info['team']

            # Fill in logo if missing
            if not team.get('logo'):
                logos = team_data.get('logos', [])
                if logos:
                    event[team_key]['logo'] = logos[0].get('href')

            # Fill in color if missing
            if not team.get('color'):
                event[team_key]['color'] = team_data.get('color')

        # Get team stats (has current record, conference, division, rank, seed, streak)
        team_stats = self.espn.get_team_stats(sport, api_league, team_id)
        if not team_stats:
            return

        # Always use team stats record - it's the current record
        stats_record = team_stats.get('record', {})
        if stats_record and stats_record.get('summary') and stats_record.get('summary') != '0-0':
            event[team_key]['record'] = stats_record
        elif not event[team_key].get('record') or event[team_key].get('record', {}).get('summary') == '0-0':
            if stats_record:
                event[team_key]['record'] = stats_record

        # Conference and division (stored separately for college vs pro)
        if is_college:
            event[team_key]['college_conference'] = team_stats.get('conference_name', '')
            event[team_key]['college_conference_abbrev'] = team_stats.get('conference_abbrev', '')
            event[team_key]['pro_conference'] = ''
            event[team_key]['pro_conference_abbrev'] = ''
            event[team_key]['pro_division'] = ''
        else:
            event[team_key]['college_conference'] = ''
            event[team_key]['college_conference_abbrev'] = ''
            event[team_key]['pro_conference'] = team_stats.get('conference_name', '')
            event[team_key]['pro_conference_abbrev'] = team_stats.get('conference_abbrev', '')
            event[team_key]['pro_division'] = team_stats.get('division_name', '')

        # Rank (college - show #X if ranked top 25, else empty)
        rank = team_stats.get('rank', 99)
        event[team_key]['rank'] = f"#{rank}" if rank <= 25 else ''

        # Playoff seed (pro - show ordinal if seeded)
        seed = team_stats.get('playoff_seed', 0)
        event[team_key]['seed'] = self._format_ordinal(seed) if seed > 0 else ''

        # Streak (signed: positive=wins, negative=losses)
        streak_count = team_stats.get('streak_count', 0)
        if streak_count > 0:
            event[team_key]['streak'] = f"W{streak_count}"
        elif streak_count < 0:
            event[team_key]['streak'] = f"L{abs(streak_count)}"
        else:
            event[team_key]['streak'] = ''

    def _format_ordinal(self, n: int) -> str:
        """Format number with ordinal suffix (1st, 2nd, 3rd, etc.)"""
        if n == 0:
            return ''
        if 10 <= n % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
        return f"{n}{suffix}"

    def get_event_by_id(
        self,
        event_id: str,
        league: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch an event by its ESPN event ID.

        Used to get event data for existing managed channels when the
        event wouldn't normally be matched (e.g., game is final but
        channel hasn't been deleted yet).

        Tries scoreboard first (faster), then falls back to event summary
        endpoint for finished games that rolled off the scoreboard.

        Args:
            event_id: ESPN event ID
            league: League code

        Returns:
            Enriched event dict or None if not found
        """
        try:
            config = get_league_config(league)
            api_path = config['api_path']
            sport = config.get('sport', api_path.split('/')[0])

            # Use scoreboard API to get the event (faster)
            scoreboard = self.espn.get_scoreboard(api_path)
            event = None

            if scoreboard:
                # Find the event by ID
                events = scoreboard.get('events', [])
                for e in events:
                    if str(e.get('id')) == str(event_id):
                        event = e
                        break

            if not event:
                # Event not on today's scoreboard - try event summary endpoint
                # This works for finished games that rolled off the scoreboard
                logger.debug(f"Event {event_id} not on scoreboard, trying summary endpoint")
                event = self.espn.get_event_summary(sport, league, event_id)

            if not event:
                logger.debug(f"Event {event_id} not found for {league}")
                return None

            # Enrich with team stats
            event = self.enrich_with_team_stats(event, league)

            return event

        except Exception as e:
            logger.warning(f"Error fetching event {event_id} for {league}: {e}")
            return None

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
        Find event and enrich with scoreboard and team stats data.

        Convenience method that combines:
        1. find_event() - Find the event from schedule
        2. enrich_event_with_scoreboard() - Add live data (odds, scores, broadcasts)
        3. enrich_with_team_stats() - Add current records, logos, colors

        The team stats enrichment is critical because:
        - Schedule API doesn't have records for future games
        - Scoreboard API only has records for today's games
        - Team endpoint always has current records

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
            # First enrich with scoreboard (live odds, scores, broadcasts)
            result['event'] = self.enrich_event_with_scoreboard(
                result['event'],
                league
            )

            # Then enrich with team stats (records, logos, colors)
            # This is done AFTER scoreboard because team endpoint has the
            # authoritative current record, not the scoreboard
            result['event'] = self.enrich_with_team_stats(
                result['event'],
                league
            )

        return result


# Convenience function for standalone use
def create_event_matcher(lookahead_days: int = None) -> EventMatcher:
    """
    Create an EventMatcher instance with default configuration.

    Args:
        lookahead_days: How many days ahead to search for events.
                        If not provided, uses DEFAULT_SEARCH_DAYS_AHEAD (7).
    """
    from api.espn_client import ESPNClient
    from database import get_connection

    espn = ESPNClient()
    return EventMatcher(espn, db_connection_func=get_connection, lookahead_days=lookahead_days)
