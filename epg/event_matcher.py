"""
ESPN Event Matcher for Event Channel EPG

Given two team IDs (from TeamMatcher), finds the matching ESPN event
and fetches enriched event data (odds, venue, broadcast, weather).

This module bridges the gap between:
- TeamMatcher: extracts team IDs from stream names
- EPG Generation: needs full event data for XMLTV output

Enrichment is delegated to EventEnricher for consistency with other EPG paths.
"""

import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple, TYPE_CHECKING
from zoneinfo import ZoneInfo

from epg.league_config import get_league_config, parse_api_path, is_college_league, is_soccer_league
from utils.logger import get_logger

if TYPE_CHECKING:
    from epg.event_enricher import EventEnricher

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

    def __init__(
        self,
        espn_client,
        db_connection_func=None,
        lookahead_days: int = None,
        enricher: 'EventEnricher' = None
    ):
        """
        Initialize EventMatcher.

        Args:
            espn_client: ESPNClient instance for API calls
            db_connection_func: Function that returns DB connection (for league config)
            lookahead_days: How many days ahead to search for events (default from setting or 7)
            enricher: EventEnricher instance for event enrichment (optional, created if not provided)
        """
        self.espn = espn_client
        self.db_connection_func = db_connection_func
        self.lookahead_days = lookahead_days or self.DEFAULT_SEARCH_DAYS_AHEAD
        self.enricher = enricher

        # Cache for league config
        self._league_config: Dict[str, Dict] = {}

        # Scoreboard cache: key = "sport:league:YYYYMMDD" → scoreboard data
        # Thread-safe with double-checked locking
        self._scoreboard_cache: Dict[str, Optional[Dict]] = {}
        self._scoreboard_cache_lock = threading.Lock()

        # Counters for monitoring scoreboard-first effectiveness
        self._scoreboard_hits = 0
        self._scoreboard_misses = 0
        self._schedule_fallbacks = 0
        self._counters_lock = threading.Lock()

    def _get_league_config(self, league_code: str) -> Optional[Dict]:
        """Get league configuration (sport, api_path) using shared module."""
        return get_league_config(league_code, self.db_connection_func, self._league_config)

    def clear_scoreboard_cache(self):
        """Clear scoreboard cache. Call at start of each EPG generation."""
        with self._scoreboard_cache_lock:
            self._scoreboard_cache.clear()

    def get_matching_stats(self) -> Dict[str, int]:
        """
        Get scoreboard-first matching statistics.

        Returns dict with:
        - scoreboard_hits: Number of matches found via scoreboard
        - scoreboard_misses: Number of times scoreboard had no match
        - schedule_fallbacks: Number of matches found via schedule after scoreboard miss
        """
        with self._counters_lock:
            return {
                'scoreboard_hits': self._scoreboard_hits,
                'scoreboard_misses': self._scoreboard_misses,
                'schedule_fallbacks': self._schedule_fallbacks
            }

    def reset_matching_stats(self):
        """Reset matching statistics. Call at start of each EPG generation."""
        with self._counters_lock:
            self._scoreboard_hits = 0
            self._scoreboard_misses = 0
            self._schedule_fallbacks = 0

    def _get_scoreboard_cached(self, sport: str, api_league: str, date_str: str) -> Optional[Dict]:
        """
        Get scoreboard data with thread-safe caching.

        Args:
            sport: Sport name (e.g., 'soccer', 'football')
            api_league: API league path (e.g., 'eng.1', 'nfl')
            date_str: Date string in YYYYMMDD format

        Returns:
            Scoreboard data dict or None if fetch failed
        """
        cache_key = f"{sport}:{api_league}:{date_str}"

        # Fast path: check without lock
        if cache_key in self._scoreboard_cache:
            return self._scoreboard_cache[cache_key]

        # Slow path: acquire lock and fetch
        with self._scoreboard_cache_lock:
            # Double-check after acquiring lock
            if cache_key in self._scoreboard_cache:
                return self._scoreboard_cache[cache_key]

            # Fetch from API
            try:
                scoreboard_data = self.espn.get_scoreboard(sport, api_league, date_str)
                self._scoreboard_cache[cache_key] = scoreboard_data
                return scoreboard_data
            except Exception as e:
                logger.warning(f"Error fetching scoreboard for {cache_key}: {e}")
                self._scoreboard_cache[cache_key] = None
                return None

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
        from utils.time_format import get_today_in_user_tz, get_user_timezone

        now = datetime.now(ZoneInfo('UTC'))
        cutoff_past = now - timedelta(days=self.SEARCH_DAYS_BACK)
        cutoff_future = now + timedelta(days=self.lookahead_days)

        # Use user's timezone for "today" to avoid games becoming "yesterday"
        # when UTC crosses midnight but user's local time hasn't
        today = get_today_in_user_tz(self.db_connection_func)

        # Get user timezone for event date conversion
        user_tz_str = get_user_timezone(self.db_connection_func)
        try:
            user_tz = ZoneInfo(user_tz_str)
        except Exception:
            user_tz = ZoneInfo('America/Detroit')

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

                # Only filter by team2_id if one is provided
                # (name-based matching passes None because we already matched by name)
                if team2_id is not None and str(team2_id) not in team_ids_in_game:
                    # TRACE: Log what teams ARE in this game (to help debug wrong team matches)
                    team_names_in_game = [
                        c.get('team', {}).get('displayName', c.get('team', {}).get('name', 'Unknown'))
                        for c in competitors
                    ]
                    logger.debug(f"[TRACE]   Event {event_id} on {event_date.date()}: {' vs '.join(team_names_in_game)} - opponent {team2_id} NOT in game (teams: {team_ids_in_game})")
                    continue

                # Filter completed games based on settings
                if is_completed:
                    # Convert event_date to user timezone for day comparison
                    # This ensures a 7pm EST game on Dec 6 (which is 12am UTC Dec 7)
                    # is still considered "Dec 6" for a user in EST
                    event_in_user_tz = event_date.astimezone(user_tz)
                    event_day = event_in_user_tz.date()

                    logger.debug(f"[TRACE]   Event {event_id} is_completed=True | event_day={event_day} | today={today} | event_in_user_tz={event_in_user_tz}")

                    # Past day completed events are ALWAYS excluded
                    if event_day < today:
                        skip_reason = 'past_game'  # Game from a previous day
                        logger.debug(f"[TRACE]   Event {event_id} ({event_name}) - skipped: past completed game ({event_day} < {today})")
                        continue
                    # Same day finals: honor the include_final_events setting
                    elif event_day == today and not include_final_events:
                        skip_reason = 'today_final'  # Today's game, but finals excluded
                        logger.debug(f"[TRACE]   Event {event_id} ({event_name}) - skipped: today's final (excluded)")
                        continue
                    else:
                        logger.debug(f"[TRACE]   Event {event_id} completed but PASSING filter | event_day={event_day} | today={today} | include_final={include_final_events}")

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
        include_final_events: bool,
        game_time: datetime = None
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
            game_time: Optional target time for disambiguation (if provided, only
                      early-exit on exact time match to handle same-name teams
                      in different leagues like men's vs women's hockey)

        Returns:
            Tuple of (matching_events, skip_reason, error_reason)
        """
        now_utc = datetime.now(ZoneInfo('UTC'))

        # Collect events from scoreboard that involve BOTH teams
        candidate_events = []

        # Search scoreboard for each day in the lookahead window
        # Start from -1 (yesterday) to handle timezone edge cases where games
        # listed for Dec 6 in user's local time are Dec 6 in ESPN scoreboard
        # but current UTC date is already Dec 7
        exact_time_match_found = False
        for day_offset in range(-1, self.lookahead_days):
            check_date = now_utc + timedelta(days=day_offset)
            date_str = check_date.strftime('%Y%m%d')

            logger.debug(f"[TRACE] _search_scoreboard | checking {date_str} for teams {team1_id} vs {team2_id}")

            # Use cached scoreboard fetch
            scoreboard_data = self._get_scoreboard_cached(sport, api_league, date_str)
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

                    # Only early exit if we have an exact time match
                    # This handles the edge case where men's and women's teams with
                    # the same name play on the same day (e.g., hockey doubleheaders)
                    if game_time:
                        try:
                            event_date_str = sb_event.get('date', '')
                            event_dt = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                            # Check if times match within 5 minutes
                            time_diff = abs((event_dt - game_time).total_seconds())
                            if time_diff < 300:  # 5 minutes tolerance
                                logger.debug(f"[TRACE] _search_scoreboard | exact time match, early exit")
                                exact_time_match_found = True
                                break
                        except (ValueError, TypeError):
                            pass
                    # No game_time provided - keep collecting candidates for later selection
                    # Don't break here; there might be multiple games (men's + women's)

            if exact_time_match_found:
                break  # Found exact time match, no need to check more days

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
        include_final_events: bool = False,
        api_path_override: str = None
    ) -> Dict[str, Any]:
        """
        Find an ESPN event between two teams.

        Searches team1's schedule first, then falls back to team2's schedule
        if no game is found. This handles edge cases where one team's schedule
        might be incomplete in ESPN's API.

        Args:
            team1_id: ESPN team ID for first team (from stream name)
            team2_id: ESPN team ID for second team (from stream name)
            league: League code (e.g., 'nfl', 'epl') or ESPN slug (e.g., 'eng.w.1')
            game_date: Optional target date extracted from stream name
            game_time: Optional target time for double-header disambiguation
            include_final_events: Whether to include completed events from today
            api_path_override: Optional API path override for leagues not in
                              league_config (e.g., 'soccer/eng.w.1')

        Returns:
            Dict with found, event, event_id, reason (if not found)
        """
        # TRACE: Log the search parameters
        logger.debug(f"[TRACE] find_event START | team1={team1_id} vs team2={team2_id} | league={league} | target_date={game_date.date() if game_date else None} | include_final={include_final_events} | api_override={api_path_override}")

        result = {
            'found': False,
            'team1_id': team1_id,
            'team2_id': team2_id,
            'league': league
        }

        # Use api_path override if provided (for unmapped soccer leagues)
        if api_path_override:
            sport, api_league = parse_api_path(api_path_override)
            if not sport or not api_league:
                result['reason'] = f'Invalid api_path_override: {api_path_override}'
                logger.debug(f"[TRACE] find_event FAIL | reason=invalid api_path_override")
                return result
        else:
            # Get league config from database
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

        # ================================================================
        # SCOREBOARD FIRST: Check scoreboard (fast, cached, has today's games)
        # ================================================================
        # The scoreboard is fetched once per league/date and cached. For pro sports
        # it has all games. For college sports, we use groups param to get all D1 games.
        # This eliminates ~1200 schedule API calls per EPG generation.
        logger.debug(f"[TRACE] Checking scoreboard for {team1_id} vs {team2_id} in {league}")
        matching_events, skip_reason, error = self._search_scoreboard(
            team1_id, team2_id, sport, api_league, include_final_events, game_time
        )

        if matching_events:
            logger.debug(f"[SCOREBOARD HIT] Found {team1_id} vs {team2_id} in {league}")
            with self._counters_lock:
                self._scoreboard_hits += 1

        # ================================================================
        # SCHEDULE FALLBACK: If not on scoreboard (D2/D3/NAIA, far future, etc.)
        # ================================================================
        # Scoreboard may miss: D2/D3/NAIA games, games > 7 days out, exhibitions
        if not matching_events and not skip_reason:
            logger.debug(f"[SCOREBOARD MISS] Falling back to schedule for {team1_id} vs {team2_id}")
            with self._counters_lock:
                self._scoreboard_misses += 1

            # Try team1's schedule
            matching_events, skip_reason, error = self._search_team_schedule(
                team1_id, team2_id, sport, api_league, include_final_events
            )

            # If no match, try team2's schedule
            if not matching_events and not skip_reason:
                matching_events, skip_reason, error = self._search_team_schedule(
                    team2_id, team1_id, sport, api_league, include_final_events
                )

            if matching_events:
                logger.info(f"[SCHEDULE FALLBACK] Found via schedule after scoreboard miss")
                with self._counters_lock:
                    self._schedule_fallbacks += 1

        if error and not matching_events:
            result['reason'] = error
            logger.debug(f"[TRACE] find_event FAIL | reason={error}")
            return result

        if not matching_events:
            # Use FilterReason constants for consistent messaging and match rate exclusion
            from utils.filter_reasons import FilterReason
            if skip_reason == 'past_game':
                result['reason'] = FilterReason.GAME_PAST
                # Event found but excluded (past game) - use EXCLUDED not FAIL
                logger.debug(f"[TRACE] find_event EXCLUDED | team1={team1_id} vs team2={team2_id} | reason=game_past")
            elif skip_reason == 'today_final':
                result['reason'] = FilterReason.GAME_FINAL_EXCLUDED
                # Event found but excluded (today's final) - use EXCLUDED not FAIL
                logger.debug(f"[TRACE] find_event EXCLUDED | team1={team1_id} vs team2={team2_id} | reason=today_final")
            else:
                result['reason'] = FilterReason.NO_GAME_FOUND
                # True failure - no event found at all
                logger.debug(f"[TRACE] find_event NOT_FOUND | team1={team1_id} vs team2={team2_id}")
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

        # If stream explicitly specified a date and we had to match a different date,
        # AND the original date's game was filtered (past/final), return that reason instead.
        # This prevents matching "Yale vs Brown @ Dec 06" to a Dec 07 game when Dec 06 game is past.
        if game_date and skip_reason:
            target_date = game_date.date()
            match_date = best_match['event_date'].date()
            if target_date != match_date:
                from utils.filter_reasons import FilterReason
                if skip_reason == 'past_game':
                    result['reason'] = FilterReason.GAME_PAST
                    logger.debug(f"[TRACE] find_event EXCLUDED | target_date={target_date} game was past, ignoring {match_date} match")
                elif skip_reason == 'today_final':
                    result['reason'] = FilterReason.GAME_FINAL_EXCLUDED
                    logger.debug(f"[TRACE] find_event EXCLUDED | target_date={target_date} game was final, ignoring {match_date} match")
                return result

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

            # Odds - handle None entries in odds list (ESPN sometimes returns [None])
            odds = comp.get('odds', [])
            primary_odds = odds[0] if odds else None
            if primary_odds:
                event['odds'] = {
                    'spread': primary_odds.get('details'),
                    'over_under': primary_odds.get('overUnder'),
                    'home_moneyline': primary_odds.get('homeTeamOdds', {}).get('moneyLine'),
                    'away_moneyline': primary_odds.get('awayTeamOdds', {}).get('moneyLine'),
                    'provider': (primary_odds.get('provider') or {}).get('name')
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
            config = self._get_league_config(league)
            if config:
                api_path = config['api_path']
                sport = config.get('sport', api_path.split('/')[0])
                api_league = api_path.split('/')[-1] if '/' in api_path else league
            else:
                # Fallback for soccer leagues not in league_config but in soccer cache
                # (e.g., eng.fa, esp.copa_del_rey, etc.)
                from database import get_connection
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM soccer_leagues_cache WHERE league_slug = ?",
                    (league,)
                )
                is_soccer = cursor.fetchone() is not None
                conn.close()

                if is_soccer:
                    sport = 'soccer'
                    api_league = league
                    logger.debug(f"get_event_by_id: using soccer fallback for {league}")
                else:
                    logger.warning(f"No config for league {league}")
                    return None

            # Use scoreboard API to get the event (faster)
            scoreboard = self.espn.get_scoreboard(sport, api_league)
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

            # Enrich using EventEnricher if available
            if self.enricher:
                event = self.enricher.enrich_event(
                    event,
                    league,
                    include_scoreboard=False,  # Already from scoreboard
                    include_team_stats=True
                )

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
        include_final_events: bool = False,
        api_path_override: str = None
    ) -> Dict[str, Any]:
        """
        Find event and enrich with scoreboard and team stats data.

        Convenience method that combines:
        1. find_event() - Find the event from schedule/scoreboard
        2. EventEnricher.enrich_event() - Add live data and team context

        Args:
            team1_id: ESPN team ID for first team (from stream name)
            team2_id: ESPN team ID for second team (from stream name)
            league: League code
            game_date: Optional target date from stream name
            game_time: Optional target time for double-header disambiguation
            include_final_events: Whether to include completed events from today (default False)
            api_path_override: Optional API path override for leagues not in
                              league_config (e.g., 'soccer/eng.w.1')

        Returns:
            Result dict with enriched event (if found)
        """
        result = self.find_event(
            team1_id, team2_id, league,
            game_date=game_date,
            game_time=game_time,
            include_final_events=include_final_events,
            api_path_override=api_path_override
        )

        if result['found'] and self.enricher:
            # Use EventEnricher for consistent enrichment
            # The raw event from find_event() may already be partially parsed,
            # but enricher handles both raw and parsed events
            result['event'] = self.enricher.enrich_event(
                result['event'],
                league,
                include_scoreboard=True,
                include_team_stats=True
            )

        return result

    def find_event_by_team_names(
        self,
        team1_name: str,
        team2_name: str,
        league: str,
        game_date: datetime = None,
        game_time: datetime = None,
        include_final_events: bool = False
    ) -> Dict[str, Any]:
        """
        Find an ESPN event by searching team NAMES on the scoreboard.

        This is a fallback for when teams are not in ESPN's /teams database
        but their games ARE on the scoreboard (common for small college teams,
        NAIA schools, D2/D3 teams, etc.).

        Instead of looking up team IDs first, this searches the scoreboard
        directly for events where the event name contains both team names.

        Args:
            team1_name: First team name from stream (e.g., "Albany", "Fisher College")
            team2_name: Second team name from stream (e.g., "Yale", "UMass Lowell")
            league: League code (e.g., 'mens-college-basketball')
            game_date: Optional target date extracted from stream name
            game_time: Optional target time for disambiguation
            include_final_events: Whether to include completed events

        Returns:
            Dict with found, event, event_id, reason (if not found)
        """
        import unicodedata

        result = {
            'found': False,
            'team1_name': team1_name,
            'team2_name': team2_name,
            'league': league
        }

        logger.debug(
            f"[TRACE] find_event_by_team_names START | "
            f"team1='{team1_name}' vs team2='{team2_name}' | league={league}"
        )

        # Get league config
        config = self._get_league_config(league)
        if not config:
            result['reason'] = f'Unknown league: {league}'
            return result

        sport, api_league = parse_api_path(config['api_path'])
        if not sport or not api_league:
            result['reason'] = f'Invalid api_path for league: {league}'
            return result

        # Normalize team names for matching (lowercase, strip accents)
        def normalize_name(name: str) -> str:
            # Lowercase and strip whitespace
            name = name.lower().strip()
            # Remove accents (é → e, ñ → n, etc.)
            name = unicodedata.normalize('NFD', name)
            name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
            # Common abbreviations
            name = name.replace('st.', 'saint').replace('st ', 'saint ')
            return name

        # Apply team aliases (stream name → ESPN canonical name)
        # These map common stream abbreviations to ESPN's official team names
        TEAM_ALIASES = {
            'albany': 'ualbany',
            'st leo': 'saint leo',
            'st. leo': 'saint leo',
        }

        team1_norm = normalize_name(team1_name)
        team2_norm = normalize_name(team2_name)

        # Apply aliases if present
        team1_norm = TEAM_ALIASES.get(team1_norm, team1_norm)
        team2_norm = TEAM_ALIASES.get(team2_norm, team2_norm)

        # Search scoreboards for the target date range
        from zoneinfo import ZoneInfo
        from utils.time_format import get_user_timezone

        tz = ZoneInfo(get_user_timezone(self.db_connection_func))

        now = datetime.now(tz)

        # If game_date provided, search that day; otherwise search today and tomorrow
        if game_date:
            dates_to_check = [game_date]
        else:
            dates_to_check = [now, now + timedelta(days=1)]

        candidate_events = []

        for check_date in dates_to_check:
            date_str = check_date.strftime('%Y%m%d')

            # Get scoreboard (uses cache)
            scoreboard = self._get_scoreboard_cached(sport, api_league, date_str)
            if not scoreboard:
                continue

            events = scoreboard.get('events', [])

            for event in events:
                event_name = event.get('name', '') or event.get('shortName', '')
                event_name_norm = normalize_name(event_name)

                # Check if BOTH team names appear in the event name
                if team1_norm in event_name_norm and team2_norm in event_name_norm:
                    logger.debug(
                        f"[TRACE] find_event_by_team_names MATCH | "
                        f"'{team1_name}' + '{team2_name}' found in '{event_name}'"
                    )
                    candidate_events.append(event)

        if not candidate_events:
            result['reason'] = 'no_game_found'
            logger.debug(
                f"[TRACE] find_event_by_team_names FAIL | "
                f"no event with both '{team1_name}' and '{team2_name}'"
            )
            return result

        # Filter by state if needed
        matching_events, skip_reason = self._filter_matching_events(
            candidate_events,
            None,  # No opponent ID filter needed
            include_final_events
        )

        if not matching_events:
            result['reason'] = skip_reason or 'no_game_found'
            return result

        # Select best match (closest to game_time if provided)
        best_event = self._select_best_match(
            matching_events, game_date, game_time
        )

        if best_event:
            # best_event is a wrapper dict {'event': {...}, 'event_date': ..., 'event_id': ...}
            # Extract the actual event from the wrapper
            actual_event = best_event['event']
            result['found'] = True
            result['event'] = actual_event
            result['event_id'] = actual_event.get('id')
            logger.info(
                f"[NAME-MATCH] Found event by team names: "
                f"'{team1_name}' vs '{team2_name}' → {actual_event.get('name')}"
            )

        return result

    def find_and_enrich_by_names(
        self,
        team1_name: str,
        team2_name: str,
        league: str,
        game_date: datetime = None,
        game_time: datetime = None,
        include_final_events: bool = False
    ) -> Dict[str, Any]:
        """
        Find event by team names and enrich with scoreboard data.

        This is the name-based equivalent of find_and_enrich().
        Use when teams are not in ESPN's /teams database.
        """
        result = self.find_event_by_team_names(
            team1_name, team2_name, league,
            game_date=game_date,
            game_time=game_time,
            include_final_events=include_final_events
        )

        if result['found'] and self.enricher:
            result['event'] = self.enricher.enrich_event(
                result['event'],
                league,
                include_scoreboard=True,
                include_team_stats=False  # Can't do team stats without IDs
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
    from epg.event_enricher import EventEnricher

    espn = ESPNClient()
    enricher = EventEnricher(espn, db_connection_func=get_connection)
    return EventMatcher(
        espn,
        db_connection_func=get_connection,
        lookahead_days=lookahead_days,
        enricher=enricher
    )
