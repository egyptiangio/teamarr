"""EPG Generation Orchestrator for Teamarr - Template-Based Architecture

This module orchestrates the EPG generation process:
1. Fetches teams with templates from database
2. Merges team + template data
3. Fetches schedules via ESPN client
4. Processes events with templates
5. Generates filler content
6. Returns data ready for XMLTV generation
"""
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo
import json

from utils.logger import get_logger
from database import get_connection
from api.espn_client import ESPNClient
from epg.template_engine import TemplateEngine

logger = get_logger(__name__)


class EPGOrchestrator:
    """Orchestrates EPG generation workflow"""

    def __init__(self):
        self.espn = ESPNClient()
        self.template_engine = TemplateEngine()
        self.api_calls = 0

    def _round_to_last_hour(self, dt: datetime) -> datetime:
        """Round datetime down to the last top of hour"""
        return dt.replace(minute=0, second=0, microsecond=0)

    def _normalize_event(self, event: dict) -> dict:
        """
        Normalize ESPN API event structure to template engine format

        ESPN gives us: competitions[0].competitors[] with homeAway field
        Template expects: home_team and away_team at top level
        """
        if not event or 'competitions' not in event:
            return event

        competition = event.get('competitions', [{}])[0]
        competitors = competition.get('competitors', [])

        # Extract home and away teams from competitors array
        home_team = None
        away_team = None

        for competitor in competitors:
            # Handle score - can be a dict with 'value' key or missing
            score_data = competitor.get('score', {})
            if isinstance(score_data, dict):
                score = score_data.get('value', 0)
            else:
                score = 0

            team_data = {
                'id': competitor.get('id', ''),
                'name': competitor.get('team', {}).get('displayName', ''),
                'abbrev': competitor.get('team', {}).get('abbreviation', ''),
                'score': score,
                'record': competitor.get('record', [{}])[0] if competitor.get('record') else {}
            }

            if competitor.get('homeAway') == 'home':
                home_team = team_data
            elif competitor.get('homeAway') == 'away':
                away_team = team_data

        # Add normalized structure to event
        normalized = event.copy()
        normalized['home_team'] = home_team or {}
        normalized['away_team'] = away_team or {}

        # Also add venue from competition to top level for easier access
        if 'venue' in competition:
            normalized['venue'] = competition['venue']

        # Extract and normalize status from competition
        # Template engine expects status.name to be 'STATUS_FINAL' or 'Final'
        comp_status = competition.get('status', {})
        status_type = comp_status.get('type', {})
        normalized['status'] = {
            'name': status_type.get('name', ''),
            'state': status_type.get('state', ''),
            'completed': status_type.get('completed', False),
            'detail': status_type.get('detail', ''),
            'period': comp_status.get('period', 0)
        }

        return normalized

    def generate_epg(self, days_ahead: int = 14, epg_timezone: str = 'America/New_York', settings: dict = None, progress_callback=None, start_datetime: datetime = None) -> Dict[str, Any]:
        """
        Generate complete EPG data for all active teams with templates

        Args:
            days_ahead: Number of days to generate EPG for (1 = today only, 2 = today and tomorrow, etc.)
            epg_timezone: Timezone for EPG generation
            settings: Global settings dict (includes midnight_crossover_mode, etc.)
            progress_callback: Optional callback function(current, total, team_name, message) for progress updates
            start_datetime: Optional explicit start datetime for EPG (overrides auto-calculation)

        Returns:
            Dict with:
                - teams_list: List of team configs (merged with templates)
                - all_events: Dict mapping team_id -> list of processed events
                - api_calls: Number of API calls made
                - stats: Generation statistics
        """
        logger.info(f"Starting EPG generation: days_ahead={days_ahead}, timezone={epg_timezone}")
        start_time = datetime.now()
        self.api_calls = 0

        # Get active teams with templates
        teams_list = self._get_teams_with_templates()

        if not teams_list:
            logger.warning("No active teams with templates found")
            return {
                'teams_list': [],
                'all_events': {},
                'api_calls': 0,
                'stats': {'error': 'No active teams configured'}
            }

        logger.info(f"Processing {len(teams_list)} teams")

        # Get settings
        settings = self._get_settings()

        # Calculate EPG start datetime (single source of truth)
        epg_tz = ZoneInfo(epg_timezone)

        if start_datetime:
            # Explicit start datetime provided - use as-is
            if start_datetime.tzinfo:
                epg_start_datetime = start_datetime.astimezone(epg_tz)
            else:
                epg_start_datetime = start_datetime.replace(tzinfo=epg_tz)
            logger.info(f"EPG will start from {epg_start_datetime.strftime('%Y-%m-%d %H:%M %Z')} (explicit start_datetime)")
        else:
            # Auto-calculate: check for games in last 6 hours
            lookback_hours = 6
            epg_start_datetime = self._calculate_epg_start_time(teams_list, epg_timezone, settings, lookback_hours)

            if epg_start_datetime:
                # Found recent game - start EPG from that game's start time
                logger.info(f"EPG will start from {epg_start_datetime.strftime('%Y-%m-%d %H:%M %Z')} (in-progress game)")
            else:
                # No recent games - start from last top of hour
                now = datetime.now(epg_tz)
                epg_start_datetime = self._round_to_last_hour(now)
                logger.info(f"EPG will start from {epg_start_datetime.strftime('%Y-%m-%d %H:%M %Z')} (last top of hour)")

        # Fetch schedules for each team
        all_events = {}
        total_teams = len(teams_list)

        for idx, team in enumerate(teams_list, 1):
            team_id = str(team['id'])
            team_name = team.get('team_name', 'Unknown')
            logger.info(f"Processing team: {team_name} (ID: {team_id})")

            # Report progress
            if progress_callback:
                progress_callback(idx, total_teams, team_name, f"Processing {team_name}...")

            try:
                # Process this team's schedule
                team_events = self._process_team_schedule(
                    team,
                    days_ahead,
                    epg_timezone,
                    epg_start_datetime,
                    settings
                )

                all_events[team_id] = team_events
                logger.info(f"  Generated {len(team_events)} total programs for {team_name}")

            except Exception as e:
                logger.error(f"Error processing team {team_name}: {e}", exc_info=True)
                # Continue with other teams
                all_events[team_id] = []

        # Calculate stats
        generation_time = (datetime.now() - start_time).total_seconds()
        total_programmes = sum(len(events) for events in all_events.values())
        total_events = sum(
            len([e for e in events if e.get('status') not in ['filler']])
            for events in all_events.values()
        )

        stats = {
            'num_channels': len(teams_list),
            'num_programmes': total_programmes,
            'num_events': total_events,
            'api_calls': self.api_calls,
            'generation_time': generation_time
        }

        logger.info(f"EPG generation complete: {stats}")

        return {
            'teams_list': teams_list,
            'all_events': all_events,
            'api_calls': self.api_calls,
            'stats': stats
        }

    def _get_teams_with_templates(self) -> List[Dict[str, Any]]:
        """
        Fetch active teams WITH templates from database and merge team + template data

        Returns:
            List of team dicts with template data merged in
        """
        conn = get_connection()
        try:
            # Join teams with templates and league_config
            teams = conn.execute("""
                SELECT
                    t.*,
                    tp.*,
                    lc.league_name,
                    lc.api_path,
                    lc.sport as league_sport,
                    lc.default_category as league_category
                FROM teams t
                INNER JOIN templates tp ON t.template_id = tp.id
                LEFT JOIN league_config lc ON t.league = lc.league_code
                WHERE t.active = 1 AND t.template_id IS NOT NULL
                ORDER BY t.team_name
            """).fetchall()

            teams_list = []
            for row in teams:
                team_dict = dict(row)

                # Parse JSON fields from template
                json_fields = ['flags', 'categories', 'description_options', 'pregame_periods', 'postgame_periods']
                for field in json_fields:
                    if team_dict.get(field) and isinstance(team_dict[field], str):
                        try:
                            team_dict[field] = json.loads(team_dict[field])
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse JSON field {field} for team {team_dict.get('team_name')}")
                            team_dict[field] = None

                # Merge sport/league from team or template (team takes priority)
                if not team_dict.get('sport') and team_dict.get('league_sport'):
                    team_dict['sport'] = team_dict['league_sport']

                teams_list.append(team_dict)

            logger.info(f"Loaded {len(teams_list)} active teams with templates")
            return teams_list

        finally:
            conn.close()

    def _get_settings(self) -> Dict[str, Any]:
        """Get settings from database"""
        conn = get_connection()
        try:
            settings_row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            return dict(settings_row) if settings_row else {}
        finally:
            conn.close()

    def _normalize_scoreboard_broadcasts(self, competition: dict) -> dict:
        """
        Normalize scoreboard broadcast format to match schedule format.
        Uses template engine's _normalize_broadcast() helper to handle all ESPN API formats.

        Args:
            competition: Competition dict with broadcasts array

        Returns:
            Competition dict with normalized broadcasts
        """
        if 'broadcasts' not in competition:
            return competition

        # Use template engine's helper to normalize each broadcast
        normalized_broadcasts = [
            self.template_engine._normalize_broadcast(b)
            for b in competition['broadcasts']
        ]

        competition['broadcasts'] = normalized_broadcasts
        return competition

    def _get_api_path(self, team: dict) -> tuple[str, str]:
        """
        Determine API sport and league from team configuration

        Args:
            team: Team configuration dict

        Returns:
            (api_sport, api_league) tuple
        """
        if team.get('api_path'):
            api_parts = team['api_path'].split('/', 1)
            api_sport = api_parts[0]
            api_league = api_parts[1] if len(api_parts) > 1 else team['league']
        else:
            api_sport = team['sport']
            api_league = team['league']
        return (api_sport, api_league)

    def _determine_home_away(self, event: dict, our_team_id: str, use_name_fallback: bool = False) -> tuple[bool, dict, str]:
        """
        Determine if our team is home/away and identify opponent

        Args:
            event: Event with home_team and away_team
            our_team_id: Our team's ESPN ID
            use_name_fallback: If True, also check team name as fallback (for template_engine compatibility)

        Returns:
            (is_home, opponent, opponent_id) tuple
        """
        home_team = event.get('home_team', {})
        away_team = event.get('away_team', {})

        # Determine if our team is home
        is_home = str(home_team.get('id', '')) == str(our_team_id)

        # Apply name fallback if requested (template_engine uses this)
        if use_name_fallback and not is_home:
            is_home = home_team.get('name', '').lower().replace(' ', '-') == our_team_id

        # Determine opponent
        opponent = away_team if is_home else home_team
        opponent_id = str(opponent.get('id', ''))

        return (is_home, opponent, opponent_id)

    def _enrich_event_from_scoreboard_lookup(
        self,
        event: dict,
        scoreboard_lookup: dict,
        normalize_broadcasts: bool = True,
        set_odds_flag: bool = False
    ) -> bool:
        """
        Enrich a single event using pre-fetched scoreboard lookup

        Args:
            event: Event to enrich (modified in place)
            scoreboard_lookup: Dict mapping event ID to scoreboard event
            normalize_broadcasts: Whether to normalize broadcast format
            set_odds_flag: Whether to set has_odds flag on event

        Returns:
            True if event was enriched, False otherwise
        """
        event_id = event.get('id')
        if event_id not in scoreboard_lookup:
            return False

        scoreboard_event = scoreboard_lookup[event_id]

        # Merge scoreboard data
        if 'competitions' in scoreboard_event:
            comp = scoreboard_event['competitions'][0] if scoreboard_event['competitions'] else {}

            # Normalize broadcasts if requested
            if normalize_broadcasts:
                self._normalize_scoreboard_broadcasts(comp)

            # Merge full competition data
            event['competitions'] = scoreboard_event['competitions']

            # Set odds flag if requested
            if set_odds_flag:
                event['has_odds'] = bool(comp.get('odds'))

        # Merge other scoreboard-specific fields
        for key in ['uid', 'season', 'status']:
            if key in scoreboard_event:
                event[key] = scoreboard_event[key]

        return True

    def _fetch_and_enrich_event_with_scoreboard(
        self,
        event: dict,
        date_str: str,
        api_sport: str,
        api_league: str,
        normalize_broadcasts: bool = True,
        set_odds_flag: bool = False
    ) -> Optional[dict]:
        """
        Fetch scoreboard data for a specific date and enrich a single event

        Args:
            event: Event to enrich
            date_str: Date string in YYYYMMDD format
            api_sport: Sport type for API call
            api_league: League code for API call
            normalize_broadcasts: Whether to normalize broadcast format (default True)
            set_odds_flag: Whether to set has_odds flag on event (default False)

        Returns:
            Enriched event dict, or None if scoreboard data not found
        """
        try:
            # Fetch scoreboard for date
            scoreboard_data = self.espn.get_scoreboard(api_sport, api_league, date_str)

            if not scoreboard_data or 'events' not in scoreboard_data:
                return None

            # Parse scoreboard events
            scoreboard_events = self.espn.parse_schedule_events(scoreboard_data, 1)

            # Create lookup and enrich
            scoreboard_lookup = {e['id']: e for e in scoreboard_events}
            if self._enrich_event_from_scoreboard_lookup(event, scoreboard_lookup, normalize_broadcasts, set_odds_flag):
                return event

            # Event not found in scoreboard
            return None

        except Exception as e:
            logger.warning(f"Error enriching event with scoreboard: {e}")
            return None

    def _calculate_epg_start_time(self, teams_list: List[Dict[str, Any]], epg_timezone: str, settings: Dict[str, Any], lookback_hours: int = 6) -> Optional[datetime]:
        """
        Calculate EPG start time by checking for any games in the last N hours.

        Args:
            teams_list: List of teams to check
            epg_timezone: Timezone for EPG generation
            settings: Global settings
            lookback_hours: How many hours to look back (default: 6)

        Returns:
            The earliest game start time within the lookback window, or None if no games found
        """
        epg_tz = ZoneInfo(epg_timezone)
        now = datetime.now(epg_tz)
        lookback_cutoff = now - timedelta(hours=lookback_hours)

        earliest_game_start = None

        logger.info(f"Checking for games in the last {lookback_hours} hours...")

        for team in teams_list:
            # Determine API path
            api_sport, api_league = self._get_api_path(team)

            # Fetch schedule (ESPN returns full schedule, we filter it)
            schedule_data = self.espn.get_team_schedule(
                api_sport,
                api_league,
                team['espn_team_id'],
                14  # Days ahead parameter (doesn't affect past games in response)
            )
            self.api_calls += 1

            if not schedule_data or 'events' not in schedule_data:
                continue

            # Check each event from the last N hours
            for event in schedule_data.get('events', []):
                try:
                    # Parse game start time
                    event_date_str = event.get('date', '')
                    if not event_date_str:
                        continue

                    game_start_utc = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                    game_start = game_start_utc.astimezone(epg_tz)

                    # Include if game started within our lookback window
                    if game_start >= lookback_cutoff and game_start <= now:
                        logger.info(f"Found recent game: {team['team_name']} started at {game_start.strftime('%Y-%m-%d %H:%M %Z')}")

                        # Track the earliest game start
                        if earliest_game_start is None or game_start < earliest_game_start:
                            earliest_game_start = game_start

                except Exception as e:
                    logger.warning(f"Error checking event: {e}")
                    continue

        if earliest_game_start:
            logger.info(f"Earliest recent game starts at: {earliest_game_start.strftime('%Y-%m-%d %H:%M %Z')}")
        else:
            logger.info(f"No games found in the last {lookback_hours} hours")

        return earliest_game_start

    def _process_team_schedule(
        self,
        team: Dict[str, Any],
        days_ahead: int,
        epg_timezone: str,
        epg_start_datetime: datetime,
        settings: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Process a single team's schedule: fetch data, process events, generate filler

        Args:
            team: Team configuration
            days_ahead: Days to look forward
            epg_timezone: EPG timezone
            epg_start_datetime: Start datetime for EPG (synchronized for events and filler)
            settings: Global settings

        Returns:
            List of processed event dicts (games + filler), sorted by start time
        """
        # Determine correct API path (sport/league) for ESPN API calls
        # Use api_path from league_config if available, otherwise fall back to team's sport/league
        api_sport, api_league = self._get_api_path(team)

        # Fetch team stats (record, standings, etc.)
        team_data = self.espn.get_team_info(api_sport, api_league, team['espn_team_id'])
        team_stats_basic = self.espn.get_team_stats(api_sport, api_league, team['espn_team_id'])
        self.api_calls += 1

        # Extract team logo from ESPN data if not already set
        if team_data and 'team' in team_data and not team.get('team_logo_url'):
            logos = team_data['team'].get('logos', [])
            if logos and len(logos) > 0:
                team['team_logo_url'] = logos[0].get('href', '')

        # Fetch enhanced team stats (streaks, PPG, standings, home/away records)
        enhanced_stats = self.espn.get_team_stats(api_sport, api_league, team['espn_team_id'])
        self.api_calls += 1

        # Merge basic and enhanced stats
        team_stats = {**team_stats_basic, **enhanced_stats}

        # Fetch schedule from ESPN
        schedule_data = self.espn.get_team_schedule(
            api_sport,
            api_league,
            team['espn_team_id'],
            days_ahead
        )
        self.api_calls += 1

        # Fetch extended schedule for context (next/last game info beyond EPG window)
        extended_schedule_data = self.espn.get_team_schedule(
            api_sport,
            api_league,
            team['espn_team_id'],
            30  # Look 30 days ahead for context
        )
        self.api_calls += 1

        if not schedule_data:
            logger.warning(f"No schedule data for team {team.get('team_name')}")
            return []

        # Parse events using epg_start_datetime as the cutoff (single source of truth)
        logger.debug(f"Parsing events with cutoff_past_datetime={epg_start_datetime.strftime('%Y-%m-%d %H:%M %Z')}")
        events = self.espn.parse_schedule_events(schedule_data, days_ahead, cutoff_past_datetime=epg_start_datetime)

        # Enrich today's games with scoreboard data (odds, conferenceCompetition, etc.)
        api_counter = {'count': self.api_calls}
        events = self._enrich_with_scoreboard(events, team, api_counter, epg_timezone)
        self.api_calls = api_counter['count']

        # Parse extended events (for context only - look back 30 days for last game info)
        epg_tz = ZoneInfo(epg_timezone)
        context_cutoff = datetime.now(epg_tz) - timedelta(days=30)
        extended_events = self.espn.parse_schedule_events(
            extended_schedule_data,
            days_ahead=30,
            cutoff_past_datetime=context_cutoff
        ) if extended_schedule_data else []

        # Enrich past events with scoreboard data to get actual scores
        if extended_events:
            extended_events = self._enrich_past_events_with_scores(
                extended_events,
                team,
                epg_timezone
            )

        # Cache for opponent stats to avoid duplicate API calls
        opponent_stats_cache = {}

        # Process each event (add templates, times)
        processed_events = []
        for event in events:
            # Identify opponent
            our_team_id = str(team_data.get('team', {}).get('id', '')) if team_data else ''
            # Determine which team is the opponent
            is_home, opponent, opp_id = self._determine_home_away(event, our_team_id)

            # Fetch opponent stats if not already in cache
            if opp_id and opp_id not in opponent_stats_cache:
                # Fetch enhanced opponent stats
                opp_enhanced = self.espn.get_team_stats(api_sport, api_league, opp_id)
                self.api_calls += 1
                opponent_stats_cache[opp_id] = opp_enhanced

            opponent_stats = opponent_stats_cache.get(opp_id, {})

            processed = self._process_event(
                event,
                team,
                team_stats,
                opponent_stats,
                epg_timezone,
                schedule_data,
                settings,
                extended_events=extended_events  # Pass raw extended events for next/last game lookup
            )
            if processed:
                processed_events.append(processed)

        # Generate filler entries (pregame/postgame/idle)
        # Pass extended events for next/last game context
        filler_api_counter = {'count': self.api_calls}
        filler_entries = self._generate_filler_entries(
            team,
            processed_events,
            days_ahead,
            team_stats,
            epg_timezone,
            extended_events,
            epg_start_datetime,  # Pass datetime instead of date for precise synchronization
            team.get('api_path', ''),
            settings,
            schedule_data,
            api_sport,
            api_league,
            filler_api_counter
        )
        self.api_calls = filler_api_counter['count']

        # Combine game events and filler entries, then sort by start time
        combined_events = processed_events + filler_entries
        combined_events.sort(key=lambda x: x['start_datetime'])

        return combined_events

    def _enrich_with_scoreboard(
        self,
        events: List[dict],
        team: dict,
        api_calls_counter: dict,
        epg_timezone: str = 'America/New_York'
    ) -> List[dict]:
        """
        Enrich today's games with scoreboard data (odds, conferenceCompetition, etc.)

        Args:
            events: List of parsed schedule events
            team: Team configuration dict
            api_calls_counter: Dict with 'count' key to track API calls
            epg_timezone: User's EPG timezone

        Returns:
            List of enriched events (today's games have scoreboard data merged)
        """
        # Determine correct API path (sport/league) for ESPN API calls
        api_sport, api_league = self._get_api_path(team)

        # Get today's date string in USER'S timezone (not UTC!)
        user_tz = ZoneInfo(epg_timezone)
        today_str = datetime.now(user_tz).strftime('%Y%m%d')

        # Check if any events are today (in user's timezone)
        has_today_games = False
        for event in events:
            try:
                # Parse UTC event date and convert to user's timezone
                event_date_utc = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                event_date_local = event_date_utc.astimezone(user_tz)
                event_date_str = event_date_local.strftime('%Y%m%d')

                if event_date_str == today_str:
                    has_today_games = True
                    break
            except Exception as e:
                logger.debug(f"Error checking event date: {e}")
                continue

        # If no today's games, return unchanged
        if not has_today_games:
            logger.debug(f"No today's games for {team.get('team_name', 'team')}, skipping scoreboard enrichment")
            return events

        # Fetch scoreboard for today
        logger.info(f"Fetching scoreboard for {team.get('team_name', 'team')} (today: {today_str} in {epg_timezone})")
        scoreboard_data = self.espn.get_scoreboard(api_sport, api_league, today_str)
        api_calls_counter['count'] += 1

        if not scoreboard_data or 'events' not in scoreboard_data:
            return events

        # Parse scoreboard events
        scoreboard_events = self.espn.parse_schedule_events(scoreboard_data, 1)
        logger.info(f"Found {len(scoreboard_events)} scoreboard events")

        # Create lookup by event ID
        scoreboard_lookup = {e['id']: e for e in scoreboard_events}

        # Merge scoreboard data into schedule events
        enriched_count = 0
        for event in events:
            # Enrich using helper function (set odds flag for today's games)
            if self._enrich_event_from_scoreboard_lookup(event, scoreboard_lookup, normalize_broadcasts=True, set_odds_flag=True):
                logger.debug(f"Enriched event {event.get('id')}: has_odds={event.get('has_odds', False)}")
                enriched_count += 1

        logger.info(f"Enriched {enriched_count}/{len(events)} events with scoreboard data")
        return events

    def _enrich_past_events_with_scores(
        self,
        extended_events: List[dict],
        team: dict,
        epg_timezone: str
    ) -> List[dict]:
        """
        Enrich past events with scoreboard data to get actual scores

        Args:
            extended_events: List of all events (including past)
            team: Team configuration
            epg_timezone: EPG timezone

        Returns:
            List of events with scores updated
        """
        # Determine correct API path (sport/league) for ESPN API calls
        api_sport, api_league = self._get_api_path(team)

        now_utc = datetime.now(ZoneInfo('UTC'))

        # Filter to past events using the 'date' string field
        past_events = []
        for e in extended_events:
            if e.get('date'):
                try:
                    event_dt = datetime.fromisoformat(e['date'].replace('Z', '+00:00'))
                    if event_dt < now_utc:
                        past_events.append(e)
                except Exception:
                    pass

        if not past_events:
            return extended_events

        # Group by date
        past_by_date = {}
        for event in past_events:
            try:
                event_dt = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                date_str = event_dt.strftime('%Y%m%d')
                if date_str not in past_by_date:
                    past_by_date[date_str] = []
                past_by_date[date_str].append(event)
            except Exception:
                pass

        # Fetch scoreboards for last 7 days (to control API calls)
        for date_str in sorted(past_by_date.keys(), reverse=True)[:7]:
            scoreboard_data = self.espn.get_scoreboard(api_sport, api_league, date_str)
            self.api_calls += 1

            if scoreboard_data and 'events' in scoreboard_data:
                # Parse scoreboard events
                scoreboard_events = self.espn.parse_schedule_events(scoreboard_data, 1)
                scoreboard_lookup = {e['id']: e for e in scoreboard_events}

                # Enrich events for this date using the helper (no broadcast normalization for past events)
                for event in past_by_date[date_str]:
                    self._enrich_event_from_scoreboard_lookup(event, scoreboard_lookup, normalize_broadcasts=False, set_odds_flag=False)

        return extended_events

    def _build_full_game_context(
        self,
        event: dict,
        team: dict,
        team_stats: dict = None,
        schedule_data: dict = None,
        api_path: str = ''
    ) -> dict:
        """
        Build a complete game context with all enriched data

        This helper fetches and calculates all data needed for template resolution:
        - opponent_stats (from ESPN API)
        - h2h data (calculated from schedule)
        - streaks (calculated from schedule)
        - head_coach (from roster API)
        - player_leaders (extracted from game data)

        Args:
            event: ESPN game event
            team: Team configuration (includes espn_team_id, sport, league, etc.)
            team_stats: Team's season stats
            schedule_data: Full schedule data for h2h/streak calculations
            api_path: League API path for ESPN calls

        Returns:
            Dictionary with all context data populated
        """
        # Determine correct API path (sport/league) for ESPN API calls
        api_sport, api_league = self._get_api_path(team)

        if not event:
            return {
                'game': None,
                'opponent_stats': {},
                'h2h': {},
                'streaks': {},
                'head_coach': '',
                'player_leaders': {}
            }

        # Normalize ESPN API structure (competitors[] -> home_team/away_team)
        event = self._normalize_event(event)

        our_team_id = str(team.get('espn_team_id', ''))

        # Identify opponent from event
        is_home, opponent, opponent_id = self._determine_home_away(event, our_team_id)

        # Fetch opponent stats
        opponent_stats = {}
        if opponent_id and api_sport and api_league:
            try:
                opponent_stats = self.espn.get_team_stats(
                    api_sport,
                    api_league,
                    opponent_id
                ) or {}
                self.api_calls += 1
                logger.debug(f"Fetched opponent stats for team {opponent_id}")
            except Exception as e:
                logger.warning(f"Could not fetch opponent stats for {opponent_id}: {e}")
                opponent_stats = {}

        # Calculate h2h data
        h2h = {}
        if schedule_data and opponent_id:
            h2h = self._calculate_h2h(our_team_id, opponent_id, schedule_data)

        # Calculate streaks
        streaks = {}
        if schedule_data:
            streaks = self._calculate_home_away_streaks(our_team_id, schedule_data)
        else:
            streaks = {'home_streak': '', 'away_streak': '', 'last_5_record': '', 'last_10_record': ''}

        # Get head coach
        head_coach = ''
        if our_team_id and api_path:
            head_coach = self._get_head_coach(our_team_id, api_path)

        # Extract player leaders
        player_leaders = {}
        if 'competitions' in event and event['competitions']:
            # Extract league from api_path (format: "sport/league")
            league = api_path.split('/')[-1] if api_path else ''

            player_leaders = self._extract_player_leaders(
                event['competitions'][0],
                our_team_id,
                team.get('sport', ''),
                league
            )

        return {
            'game': event,
            'opponent_stats': opponent_stats,
            'h2h': h2h,
            'streaks': streaks,
            'head_coach': head_coach,
            'player_leaders': player_leaders
        }

    def _process_event(
        self,
        event: dict,
        team: dict,
        team_stats: dict = None,
        opponent_stats: dict = None,
        epg_timezone: str = 'America/New_York',
        schedule_data: dict = None,
        settings: dict = None,
        extended_events: List[dict] = None
    ) -> dict:
        """
        Process a single event - add templates, calculate times

        Now builds three complete game contexts:
        - current_game: This event (for actual games only)
        - next_game: Next scheduled game after this event
        - last_game: Last completed game before this event
        """
        # Parse game datetime
        game_datetime = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
        program_date = game_datetime.astimezone(ZoneInfo(epg_timezone)).date()

        # Calculate end time using game duration helper
        game_duration_hours = self._get_game_duration(team, settings or {})
        end_datetime = game_datetime + timedelta(hours=game_duration_hours)

        # Get our team ID and API path
        our_team_id = str(team.get('espn_team_id', ''))
        api_path = team.get('api_path', '')

        # Build CURRENT game context using helper
        current_context = self._build_full_game_context(
            event=event,
            team=team,
            team_stats=team_stats,
            schedule_data=schedule_data,
            api_path=api_path
        )

        # Override opponent_stats with passed value if provided (for caching)
        if opponent_stats:
            current_context['opponent_stats'] = opponent_stats

        # Find NEXT game (relative to this event's date, not real-world date)
        next_event = None
        if extended_events:
            # Find first game after this event's date
            for ext_event in extended_events:
                ext_date_str = ext_event.get('date')
                if ext_date_str:
                    try:
                        ext_datetime = datetime.fromisoformat(ext_date_str.replace('Z', '+00:00'))
                        ext_date = ext_datetime.astimezone(ZoneInfo(epg_timezone)).date()
                        if ext_date > program_date:
                            next_event = ext_event
                            break
                    except:
                        continue

        # Enrich NEXT event with scoreboard data (for odds, broadcasts, etc.)
        if next_event:
            api_counter = {'count': self.api_calls}
            enriched = self._enrich_with_scoreboard([next_event], team, api_counter, epg_timezone)
            if enriched:
                next_event = enriched[0]
            self.api_calls = api_counter['count']

        # Build NEXT game context
        next_context = self._build_full_game_context(
            event=next_event,
            team=team,
            team_stats=team_stats,
            schedule_data=schedule_data,
            api_path=api_path
        ) if next_event else {
            'game': None,
            'opponent_stats': {},
            'h2h': {},
            'streaks': {},
            'head_coach': '',
            'player_leaders': {}
        }

        # Find LAST game (relative to this event's date, not real-world date)
        last_event = None
        if extended_events:
            # Find most recent completed game before this event's date
            for ext_event in reversed(extended_events):
                ext_date_str = ext_event.get('date')
                ext_status = ext_event.get('status', {})
                is_completed = ext_status.get('completed', False) or ext_status.get('state') == 'post'

                if ext_date_str and is_completed:
                    try:
                        ext_datetime = datetime.fromisoformat(ext_date_str.replace('Z', '+00:00'))
                        ext_date = ext_datetime.astimezone(ZoneInfo(epg_timezone)).date()
                        if ext_date < program_date:
                            last_event = ext_event
                            break
                    except:
                        continue

        # Build LAST game context
        last_context = self._build_full_game_context(
            event=last_event,
            team=team,
            team_stats=team_stats,
            schedule_data=schedule_data,
            api_path=api_path
        ) if last_event else {
            'game': None,
            'opponent_stats': {},
            'h2h': {},
            'streaks': {},
            'head_coach': '',
            'player_leaders': {}
        }

        # Build complete context with all three game contexts
        context = {
            # Current game (this event)
            'game': event,
            'team_config': team,
            'team_stats': team_stats or {},
            'opponent_stats': current_context.get('opponent_stats', {}),
            'h2h': current_context.get('h2h', {}),
            'streaks': current_context.get('streaks', {}),
            'head_coach': current_context.get('head_coach', ''),
            'player_leaders': current_context.get('player_leaders', {}),
            'epg_timezone': epg_timezone,
            'program_datetime': game_datetime,

            # Next game context
            'next_game': next_context,

            # Last game context
            'last_game': last_context
        }

        # Resolve templates
        title = self.template_engine.resolve(team.get('title_format', '{team_name} Basketball'), context)
        subtitle = self.template_engine.resolve(team.get('subtitle_template', '{venue_full}'), context)

        # Resolve program art URL if configured
        program_art_url_template = team.get('program_art_url', '')
        program_art_url = self.template_engine.resolve(program_art_url_template, context) if program_art_url_template else None

        # Select description template based on conditional logic and fallbacks
        description_options = team.get('description_options', '[]')
        selected_description_template = self.template_engine.select_description(
            description_options,
            context
        )

        # Resolve the selected description template
        description = self.template_engine.resolve(selected_description_template, context)

        # Determine status
        status_name = event['status']['name']
        if 'SCHEDULED' in status_name or status_name == 'STATUS_SCHEDULED':
            status = 'scheduled'
        elif 'PROGRESS' in status_name or status_name == 'STATUS_IN_PROGRESS':
            status = 'in_progress'
        elif 'FINAL' in status_name or status_name == 'STATUS_FINAL':
            status = 'final'
        else:
            status = 'scheduled'

        # Build template variables for category resolution in XMLTV
        template_vars = self.template_engine._build_variable_dict(context)

        return {
            'start_datetime': game_datetime,
            'end_datetime': end_datetime,
            'title': title,
            'subtitle': subtitle,
            'description': description,
            'program_art_url': program_art_url,  # Resolved program art URL (or None)
            'status': status,
            'game': event,  # Preserve raw game data for filler programs
            'context': template_vars  # Include template variables for category resolution
        }

    def _generate_filler_entries(
        self,
        team: dict,
        game_events: List[dict],
        days_ahead: int,
        team_stats: dict = None,
        epg_timezone: str = 'America/New_York',
        extended_events: List[dict] = None,
        epg_start_datetime: datetime = None,
        api_path: str = '',
        settings: dict = None,
        schedule_data: dict = None,
        api_sport: str = None,
        api_league: str = None,
        api_calls_counter: dict = None
    ) -> List[dict]:
        """
        Generate pregame, postgame, and idle EPG entries to fill gaps

        Args:
            team: Team configuration with filler settings
            game_events: List of actual game events in EPG window (sorted by date)
            days_ahead: Number of days in EPG window (1 = today only, 2 = today and tomorrow, etc.)
            team_stats: Team stats for template resolution
            epg_timezone: Timezone for EPG generation
            extended_events: Extended list of game events (beyond EPG window) for next/last game context
            epg_start_datetime: Start datetime for EPG generation (defaults to now if not specified)
            api_path: League API path
            settings: Global settings
            schedule_data: Full schedule data for context
            api_sport: Sport type for ESPN API calls (e.g., 'basketball', 'soccer')
            api_league: League code for ESPN API calls (e.g., 'nba', 'eng.1')
            api_calls_counter: Dict with 'count' key to track API calls made during filler generation

        Returns:
            List of filler event dictionaries
        """
        filler_entries = []

        # Initialize API calls counter if not provided
        if api_calls_counter is None:
            api_calls_counter = {'count': 0}

        # Use EPG timezone for filler generation
        team_tz = ZoneInfo(epg_timezone)

        # Filler now aligns with 6-hour time blocks (0000, 0600, 1200, 1800)
        # Max hours is fixed at 6.0 for time-block alignment
        max_hours = 6.0

        # Get midnight crossover mode from global settings (not per-template)
        midnight_mode = settings.get('midnight_crossover_mode', 'idle') if settings else 'idle'

        # Build date range for EPG window - synchronized with event parsing
        now = datetime.now(team_tz)
        if epg_start_datetime is None:
            first_day_start = self._round_to_last_hour(now)
            start_date = now.date()
        else:
            # Convert to team timezone - this is the exact start time for filler on first day
            first_day_start = epg_start_datetime.astimezone(team_tz)
            start_date = first_day_start.date()

        # Calculate end_date based on today + (days_ahead - 1)
        end_date = now.date() + timedelta(days=days_ahead - 1)

        # Create a set of game dates for quick lookup (only EPG window games)
        game_dates = set()
        game_schedule = {}  # date -> game info (only EPG window)

        for event in game_events:
            game_dt = event['start_datetime'].astimezone(team_tz)
            game_date = game_dt.date()
            game_dates.add(game_date)

            # Store game info for this date
            if game_date not in game_schedule:
                game_schedule[game_date] = []
            game_schedule[game_date].append({
                'start': event['start_datetime'],
                'end': event['end_datetime'],
                'event': event.get('game', event)  # Get raw game data if available
            })

        # Build extended schedule for next/last game context (beyond EPG window)
        extended_game_dates = set()
        extended_game_schedule = {}

        if extended_events:
            for event in extended_events:
                # Extended events are raw ESPN events with 'date' field, not processed events
                # Parse the date field to get datetime
                event_date_str = event.get('date', '')
                if not event_date_str:
                    continue

                # Parse ISO format date (e.g., "2025-11-26T22:00Z")
                game_dt_utc = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                game_dt = game_dt_utc.astimezone(team_tz)
                game_date = game_dt.date()
                extended_game_dates.add(game_date)

                # Calculate end time using default duration
                duration_hours = self._get_game_duration(team, settings)
                end_dt = game_dt + timedelta(hours=duration_hours)

                if game_date not in extended_game_schedule:
                    extended_game_schedule[game_date] = []
                extended_game_schedule[game_date].append({
                    'start': game_dt,
                    'end': end_dt,
                    'event': event  # Store raw event
                })

        # Process each day in the EPG window
        current_date = start_date
        while current_date <= end_date:
            # First day: start from epg_start_datetime; subsequent days: start from midnight
            if current_date == start_date:
                day_start = first_day_start
            else:
                day_start = datetime.combine(current_date, datetime.min.time()).replace(tzinfo=team_tz)
            day_end = datetime.combine(current_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=team_tz)

            if current_date in game_dates:
                # This day has game(s)
                games_today = sorted(game_schedule[current_date], key=lambda x: x['start'])

                # PREGAME: Fill from midnight to first game start
                if team.get('pregame_enabled', True):
                    first_game_start = games_today[0]['start']

                    # Check if previous day's game crossed into today
                    skip_pregame = False
                    prev_date = current_date - timedelta(days=1)
                    if prev_date in game_dates:
                        prev_games = game_schedule[prev_date]
                        if prev_games:
                            last_prev_game = sorted(prev_games, key=lambda x: x['end'])[-1]
                            if last_prev_game['end'] > day_start:
                                skip_pregame = True

                    # Only create pregame if not already filled by previous day's midnight crossing
                    if not skip_pregame and day_start < first_game_start:
                        # Find last game before this pregame period
                        last_game = self._find_last_game(
                            current_date,
                            extended_game_schedule or game_schedule,
                            extended_game_dates or game_dates
                        )

                        # Enrich last game with scoreboard data to get final scores
                        last_game = self._enrich_last_game_with_score(
                            last_game, api_sport, api_league, api_calls_counter, epg_timezone
                        )

                        pregame_entries = self._create_filler_chunks(
                            day_start, first_game_start, max_hours,
                            team, 'pregame', games_today[0]['event'], team_stats,
                            last_game, epg_timezone, api_path, schedule_data
                        )
                        filler_entries.extend(pregame_entries)

                # POSTGAME: Fill from last game end to midnight (or next game start if game crosses midnight)
                if team.get('postgame_enabled', True):
                    last_game_end = games_today[-1]['end']

                    # Check if game crosses midnight
                    if last_game_end > day_end:
                        # Game crosses midnight - check next day for games
                        next_date = current_date + timedelta(days=1)

                        if next_date in game_dates:
                            # Next day HAS a game - use PREGAME filler
                            next_day_games = sorted(game_schedule[next_date], key=lambda x: x['start'])
                            first_next_game_start = next_day_games[0]['start']

                            if last_game_end < first_next_game_start:
                                pregame_entries = self._create_filler_chunks(
                                    last_game_end, first_next_game_start, max_hours,
                                    team, 'pregame', next_day_games[0]['event'], team_stats,
                                    games_today[-1]['event'], epg_timezone, api_path, schedule_data
                                )
                                filler_entries.extend(pregame_entries)
                        else:
                            # No game next day - apply midnight_crossover_mode
                            next_day_end = day_end + timedelta(days=1)

                            if midnight_mode == 'postgame':
                                postgame_entries = self._create_filler_chunks(
                                    last_game_end, next_day_end, max_hours,
                                    team, 'postgame', games_today[-1]['event'], team_stats,
                                    games_today[-1]['event'], epg_timezone, api_path, schedule_data
                                )
                                filler_entries.extend(postgame_entries)
                            elif midnight_mode == 'idle':
                                if team.get('idle_enabled', True):
                                    next_game = self._find_next_game(
                                        next_date,
                                        extended_game_schedule or game_schedule,
                                        extended_game_dates or game_dates
                                    )
                                    idle_entries = self._create_filler_chunks(
                                        last_game_end, next_day_end, max_hours,
                                        team, 'idle', next_game, team_stats,
                                        games_today[-1]['event'], epg_timezone, api_path, schedule_data
                                    )
                                    filler_entries.extend(idle_entries)
                    else:
                        # Game ends before midnight - fill to midnight with postgame
                        if last_game_end < day_end:
                            postgame_entries = self._create_filler_chunks(
                                last_game_end, day_end, max_hours,
                                team, 'postgame', games_today[-1]['event'], team_stats,
                                games_today[-1]['event'], epg_timezone, api_path, schedule_data
                            )
                            filler_entries.extend(postgame_entries)

            else:
                # IDLE: No game today - check if previous day's game crossed midnight
                prev_date = current_date - timedelta(days=1)

                # Check if we should skip this day due to midnight crossover
                skip_idle = False
                if prev_date in game_dates:
                    prev_games = game_schedule[prev_date]
                    if prev_games:
                        last_prev_game = sorted(prev_games, key=lambda x: x['end'])[-1]
                        if last_prev_game['end'] > day_start:
                            skip_idle = True

                if not skip_idle and team.get('idle_enabled', True):
                    # Find next game after current_date
                    next_game = self._find_next_game(
                        current_date,
                        extended_game_schedule or game_schedule,
                        extended_game_dates or game_dates
                    )

                    # Find last game before current_date
                    last_game = self._find_last_game(
                        current_date,
                        extended_game_schedule or game_schedule,
                        extended_game_dates or game_dates
                    )

                    # Enrich last game with scoreboard data to get final scores
                    last_game = self._enrich_last_game_with_score(
                        last_game, api_sport, api_league, api_calls_counter, epg_timezone
                    )

                    # For idle days, create exactly 4 programs aligned to time blocks
                    # (0000-0600, 0600-1200, 1200-1800, 1800-0000)
                    idle_entries = self._create_filler_chunks(
                        day_start, day_end, max_hours,
                        team, 'idle', next_game, team_stats,
                        last_game, epg_timezone, api_path, schedule_data
                    )
                    filler_entries.extend(idle_entries)

            current_date += timedelta(days=1)

        return filler_entries

    def _get_next_time_block(self, dt: datetime) -> datetime:
        """
        Get the next 6-hour time block boundary (0000, 0600, 1200, 1800)

        Args:
            dt: Current datetime

        Returns:
            Datetime of next time block boundary
        """
        # Time blocks start at hours: 0, 6, 12, 18
        time_blocks = [0, 6, 12, 18]

        current_hour = dt.hour

        # Find the next block
        for block_hour in time_blocks:
            if current_hour < block_hour:
                # Next block is today
                return dt.replace(hour=block_hour, minute=0, second=0, microsecond=0)

        # No more blocks today, return first block of next day
        next_day = dt + timedelta(days=1)
        return next_day.replace(hour=0, minute=0, second=0, microsecond=0)

    def _create_filler_chunks(
        self,
        start_dt: datetime,
        end_dt: datetime,
        max_hours: float,
        team: dict,
        filler_type: str,
        game_event: dict = None,
        team_stats: dict = None,
        last_game_event: dict = None,
        epg_timezone: str = 'America/New_York',
        api_path: str = '',
        schedule_data: dict = None
    ) -> List[dict]:
        """
        Create filler EPG entries, splitting into chunks based on max_hours

        Args:
            start_dt: Start datetime
            end_dt: End datetime
            max_hours: Maximum hours per entry
            team: Team configuration
            filler_type: 'pregame', 'postgame', or 'idle'
            game_event: Associated game event (for pregame/postgame)
            team_stats: Team stats for template resolution
            last_game_event: Last game event (for context)
            epg_timezone: EPG timezone
            api_path: League API path

        Returns:
            List of filler event dictionaries
        """
        chunks = []

        # Use time-block alignment instead of evenly dividing by max_hours
        # Filler extends to next time block boundary (0000, 0600, 1200, 1800)
        time_blocks_list = []

        current_start = start_dt
        while current_start < end_dt:
            # Find the next time block boundary
            next_block = self._get_next_time_block(current_start)

            # Don't go past end_dt
            chunk_end = min(next_block, end_dt)

            time_blocks_list.append((current_start, chunk_end))
            current_start = chunk_end

        num_chunks = len(time_blocks_list)

        # Get templates for this filler type
        title_template = team.get(f'{filler_type}_title', f'{filler_type.capitalize()} Coverage')
        subtitle_template = team.get(f'{filler_type}_subtitle', '')
        art_url_template = team.get(f'{filler_type}_art_url', '')

        # Description template - check for conditional mode (postgame/idle only)
        desc_template = team.get(f'{filler_type}_description', '')
        if filler_type in ['postgame', 'idle'] and team.get(f'{filler_type}_conditional_enabled'):
            # Check if last game is final (consistent with template_engine logic)
            last_game_status = (last_game_event or {}).get('status', {})
            is_last_game_final = last_game_status.get('name', '') in ['STATUS_FINAL', 'Final']

            if is_last_game_final:
                desc_template = team.get(f'{filler_type}_description_final', desc_template)
            else:
                desc_template = team.get(f'{filler_type}_description_not_final', desc_template)

        # Get program datetime for relative next/last game finding
        program_datetime = start_dt
        program_date = start_dt.astimezone(ZoneInfo(epg_timezone)).date()

        # Build CURRENT game context (only for pregame/postgame, None for idle)
        current_event = game_event if filler_type != 'idle' else None

        # Note: For pregame/postgame, we DON'T need to build full context for current
        # because it's handled by the associated game event itself.
        # For filler, we only need next/last contexts.

        # Build context for template resolution
        context = {
            'team_config': team,
            'team_stats': team_stats or {},
            'opponent_stats': {},  # Will be populated for next/last games
            'h2h': {},
            'epg_timezone': epg_timezone,
            'program_datetime': program_datetime
        }

        # Set current game (None for idle, raw event for pregame/postgame)
        if current_event:
            context['game'] = current_event
        else:
            context['game'] = None

        # Build NEXT game context using helper
        # For pregame/idle, the game_event IS the next game
        # For postgame, we need to find the actual next game
        next_event = game_event if filler_type in ['idle', 'pregame'] else None

        next_context = self._build_full_game_context(
            event=next_event,
            team=team,
            team_stats=team_stats,
            schedule_data=schedule_data,
            api_path=api_path
        ) if next_event else {
            'game': None,
            'opponent_stats': {},
            'h2h': {},
            'streaks': {},
            'head_coach': '',
            'player_leaders': {}
        }

        context['next_game'] = next_context

        # Build LAST game context using helper
        last_context = self._build_full_game_context(
            event=last_game_event,
            team=team,
            team_stats=team_stats,
            schedule_data=schedule_data,
            api_path=api_path
        ) if last_game_event else {
            'game': None,
            'opponent_stats': {},
            'h2h': {},
            'streaks': {},
            'head_coach': '',
            'player_leaders': {}
        }

        context['last_game'] = last_context

        # Build template variables for category resolution
        template_vars = self.template_engine._build_variable_dict(context)

        # Create chunks using time block boundaries
        for i, (chunk_start, chunk_end) in enumerate(time_blocks_list):
            current_start = chunk_start
            current_end = chunk_end

            # Resolve templates
            title = self.template_engine.resolve(title_template, context)
            subtitle = self.template_engine.resolve(subtitle_template, context) if subtitle_template else ''
            description = self.template_engine.resolve(desc_template, context)
            program_art_url = self.template_engine.resolve(art_url_template, context) if art_url_template else None

            chunks.append({
                'start_datetime': current_start,
                'end_datetime': current_end,
                'title': title,
                'subtitle': subtitle,
                'description': description,
                'program_art_url': program_art_url,
                'status': 'filler',  # Special status to identify filler content
                'filler_type': filler_type,
                'context': template_vars  # Include template variables for category resolution
            })

            current_start = current_end

        return chunks

    # ========================================================================
    # HELPER FUNCTIONS (ported from old app.py)
    # ========================================================================

    def _find_next_game(self, current_date: date, game_schedule: dict, game_dates: set) -> Optional[dict]:
        """Find the next game after the given date"""
        for future_date in sorted(game_dates):
            if future_date > current_date:
                future_games = game_schedule[future_date]
                if future_games:
                    # Return the earliest game on that date
                    return sorted(future_games, key=lambda x: x['start'])[0]['event']
        return None

    def _find_last_game(self, current_date: date, game_schedule: dict, game_dates: set) -> Optional[dict]:
        """Find the most recent game before the given date"""
        for past_date in sorted(game_dates, reverse=True):
            if past_date < current_date:
                past_games = game_schedule[past_date]
                if past_games:
                    # Return the latest game on that date
                    return sorted(past_games, key=lambda x: x['end'])[-1]['event']
        return None

    def _enrich_last_game_with_score(self, last_game: Optional[dict], api_sport: str, api_league: str,
                                      api_calls_counter: dict, epg_timezone: str = 'America/New_York') -> Optional[dict]:
        """
        Enrich last game event with scoreboard data to get final scores

        Args:
            last_game: The last game event from schedule (may not have scores)
            api_sport: Sport type (e.g., 'basketball', 'football')
            api_league: League code (e.g., 'nba', 'nfl')
            api_calls_counter: Counter for API calls
            epg_timezone: Timezone for date formatting

        Returns:
            Enriched game event with scores, or original if enrichment fails
        """
        if not last_game:
            return None

        # Can't enrich without API sport/league info
        if not api_sport or not api_league:
            logger.debug("Missing api_sport or api_league, cannot enrich last game")
            return last_game

        try:
            # Get the game date
            game_date_str = last_game.get('date', '')
            if not game_date_str:
                logger.debug("Last game has no date, cannot enrich")
                return last_game

            # Parse game date to get the date string for scoreboard API
            from zoneinfo import ZoneInfo
            game_date_utc = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
            game_date_local = game_date_utc.astimezone(ZoneInfo(epg_timezone))
            date_str = game_date_local.strftime('%Y%m%d')

            # Fetch and enrich using base function
            logger.debug(f"Fetching scoreboard for last game on {date_str}")
            enriched = self._fetch_and_enrich_event_with_scoreboard(
                last_game, date_str, api_sport, api_league,
                normalize_broadcasts=True,
                set_odds_flag=False
            )
            api_calls_counter['count'] += 1

            if enriched:
                logger.debug(f"Enriched last game {last_game.get('id')} with scoreboard data")
                return enriched
            else:
                logger.debug(f"Last game {last_game.get('id')} not found in scoreboard for {date_str}")
                return last_game

        except Exception as e:
            logger.warning(f"Error enriching last game with score: {e}")
            return last_game

    def _calculate_home_away_streaks(self, our_team_id: str, schedule_data: dict) -> dict:
        """Calculate current home and away win/loss streaks"""
        if not schedule_data or 'events' not in schedule_data:
            return {
                'home_streak': '',
                'away_streak': '',
                'last_5_record': '',
                'last_10_record': ''
            }

        # Collect completed games by location and overall
        home_games = []
        away_games = []
        all_games = []

        for event in schedule_data['events']:
            try:
                comp = event.get('competitions', [{}])[0]
                status = comp.get('status', {}).get('type', {})

                # Only completed games
                if not status.get('completed', False):
                    continue

                # Find our team in competitors
                competitors = comp.get('competitors', [])
                our_team = None
                for c in competitors:
                    if str(c.get('team', {}).get('id')) == str(our_team_id):
                        our_team = c
                        break

                if not our_team:
                    continue

                # Categorize by home/away
                home_away = our_team.get('homeAway', '').lower()
                won = our_team.get('winner', False)
                game_date = event.get('date', '')

                game_data = {'date': game_date, 'won': won}
                all_games.append(game_data)

                if home_away == 'home':
                    home_games.append(game_data)
                elif home_away == 'away':
                    away_games.append(game_data)

            except (KeyError, IndexError, TypeError):
                continue

        # Helper to calculate streak
        def calc_streak(games, location_text):
            if not games:
                return ""

            games.sort(key=lambda x: x['date'], reverse=True)
            is_winning = games[0]['won']
            count = 0

            for game in games:
                if game['won'] == is_winning:
                    count += 1
                else:
                    break

            # Return streak in W/L format (W3 = 3 wins, L2 = 2 losses)
            # Always show streak, even if count is 1
            if is_winning:
                return f"W{count}"
            else:
                return f"L{count}"

        # Calculate last 5 and last 10 records
        all_games.sort(key=lambda x: x['date'], reverse=True)

        last_5 = all_games[:5]
        if len(last_5) >= 5:
            wins_5 = sum(1 for g in last_5 if g['won'])
            losses_5 = 5 - wins_5
            last_5_record = f"{wins_5}-{losses_5}"
        else:
            last_5_record = ''

        last_10 = all_games[:10]
        if len(last_10) >= 10:
            wins_10 = sum(1 for g in last_10 if g['won'])
            losses_10 = 10 - wins_10
            last_10_record = f"{wins_10}-{losses_10}"
        else:
            last_10_record = ''

        return {
            'home_streak': calc_streak(home_games, "at home"),
            'away_streak': calc_streak(away_games, "on road"),
            'last_5_record': last_5_record,
            'last_10_record': last_10_record
        }

    def _get_head_coach(self, team_id: str, league: str) -> str:
        """Fetch head coach name from roster API"""
        try:
            url = f"{self.espn.base_url}/{league}/teams/{team_id}/roster"
            roster_data = self.espn._make_request(url)

            if roster_data and 'coach' in roster_data and roster_data['coach']:
                coach = roster_data['coach'][0]
                first = coach.get('firstName', '')
                last = coach.get('lastName', '')
                return f"{first} {last}".strip()
        except Exception as e:
            logger.debug(f"Error fetching coach for team {team_id}: {e}")

        return ''

    def _get_games_played(self, competitor: dict) -> int:
        """Get number of games played from competitor's record"""
        if 'records' not in competitor:
            return 0

        for record in competitor['records']:
            if record.get('name') == 'overall':
                summary = record.get('summary', '0-0')
                try:
                    parts = summary.replace('-', ' ').split()
                    return sum(int(p) for p in parts if p.isdigit())
                except (ValueError, AttributeError):
                    return 0

        return 0

    def _is_season_stats(self, leader_category: dict, game_status: str) -> bool:
        """Determine if leader data represents season stats or game stats"""
        category_name = leader_category.get('name', '')

        # NBA: Category name changes
        if 'PerGame' in category_name:
            return True  # pointsPerGame = season average

        # NFL: Leaders only present for scheduled games
        if 'Leader' in category_name:
            return True  # passingLeader = season totals

        # If game is completed, assume game stats
        if game_status in ['STATUS_FINAL', 'STATUS_FULL_TIME']:
            return False

        # Default: scheduled/in-progress games have season stats
        return True

    def _map_basketball_game_leaders(self, leaders_data: list) -> dict:
        """Map NBA/NCAAB/WNBA/NCAAW game stats (only available for completed games)"""
        result = {}

        for category in leaders_data:
            if not category.get('leaders'):
                continue

            player = category['leaders'][0]
            athlete = player['athlete']
            game_stat = player['value']

            if category['name'] == 'points':
                result['basketball_scoring_leader_name'] = athlete['displayName']
                result['basketball_scoring_leader_points'] = f"{game_stat:.0f}"

        return result

    def _map_football_game_leaders(self, leaders_data: list) -> dict:
        """Map NFL/NCAAF game stats (only available for completed games)"""
        result = {}

        for category in leaders_data:
            if not category.get('leaders'):
                continue

            player = category['leaders'][0]
            athlete = player['athlete']
            display_value = player.get('displayValue', '')

            if category['name'] in ['passingLeader', 'passingYards']:
                result['football_passing_leader_name'] = athlete['displayName']
                result['football_passing_leader_stats'] = display_value

            elif category['name'] in ['rushingLeader', 'rushingYards']:
                result['football_rushing_leader_name'] = athlete['displayName']
                result['football_rushing_leader_stats'] = display_value

            elif category['name'] in ['receivingLeader', 'receivingYards']:
                result['football_receiving_leader_name'] = athlete['displayName']
                result['football_receiving_leader_stats'] = display_value

        return result

    def _map_hockey_season_leaders(self, leaders_data: list, games_played: int) -> dict:
        """Map NHL season stats"""
        result = {}

        for category in leaders_data:
            if not category.get('leaders'):
                continue

            player = category['leaders'][0]
            athlete = player['athlete']
            total_value = player['value']
            per_game_value = total_value / games_played if games_played > 0 else 0
            position = athlete.get('position', {}).get('abbreviation', '')

            if category['name'] in ['goals', 'goalsStat']:
                result['hockey_top_scorer_name'] = athlete['displayName']
                result['hockey_top_scorer_position'] = position
                result['hockey_top_scorer_goals'] = f"{total_value:.0f}"
                result['hockey_top_scorer_gpg'] = f"{per_game_value:.1f}"

            elif category['name'] in ['assists', 'assistsStat']:
                result['hockey_top_playmaker_name'] = athlete['displayName']
                result['hockey_top_playmaker_position'] = position
                result['hockey_top_playmaker_assists'] = f"{total_value:.0f}"
                result['hockey_top_playmaker_apg'] = f"{per_game_value:.1f}"

        return result

    def _map_baseball_leaders(self, leaders_data: list, games_played: int) -> dict:
        """Map MLB season stats"""
        result = {}
        return result

    def _extract_player_leaders(self, competition: dict, team_id: str, sport: str, league: str) -> dict:
        """
        Extract player leaders from competition data

        Returns game leaders for completed games only (.last suffix).
        For scheduled/future games, returns empty dict.
        """
        # Find our team's competitor
        competitor = None
        for comp in competition.get('competitors', []):
            if str(comp['team']['id']) == str(team_id):
                competitor = comp
                break

        if not competitor or 'leaders' not in competitor:
            return {}

        leaders_data = competitor['leaders']
        if not leaders_data or not isinstance(leaders_data, list) or len(leaders_data) == 0:
            return {}

        # Determine game status
        game_status = competition.get('status', {}).get('type', {}).get('name', '')

        # Determine if this is season or game stats
        is_season = self._is_season_stats(leaders_data[0], game_status)

        # Get games played for calculations
        games_played = self._get_games_played(competitor)

        # Map based on sport and data type
        if sport == 'basketball':
            if is_season:
                return {}  # Basketball leaders only available for completed games
            else:
                return self._map_basketball_game_leaders(leaders_data)

        elif sport == 'football':
            if is_season:
                return {}  # Football leaders only available for completed games
            else:
                return self._map_football_game_leaders(leaders_data)

        elif sport == 'hockey':
            if is_season:
                return self._map_hockey_season_leaders(leaders_data, games_played)
            else:
                return {}  # Could add game leaders for hockey if needed

        elif sport == 'baseball':
            return self._map_baseball_leaders(leaders_data, games_played)

        return {}

    def _calculate_h2h(self, our_team_id: str, opponent_id: str, schedule_data: dict) -> dict:
        """Calculate head-to-head data from team schedule"""
        if not schedule_data or 'events' not in schedule_data:
            return {'season_series': {}, 'previous_game': {}}

        # Find all completed games against this opponent
        h2h_games = []
        for event in schedule_data['events']:
            try:
                comp = event.get('competitions', [{}])[0]
                status = comp.get('status', {}).get('type', {})

                # Only look at completed games
                if not status.get('completed', False):
                    continue

                # Check if this game involves the opponent
                competitors = comp.get('competitors', [])
                opponent_in_game = any(
                    str(c.get('team', {}).get('id')) == str(opponent_id)
                    for c in competitors
                )

                if opponent_in_game:
                    h2h_games.append(event)
            except:
                continue

        # Calculate series record
        team_wins = 0
        opp_wins = 0

        for event in h2h_games:
            try:
                comp = event.get('competitions', [{}])[0]
                for competitor in comp.get('competitors', []):
                    team_id = str(competitor.get('team', {}).get('id'))
                    if team_id == str(our_team_id):
                        if competitor.get('winner', False):
                            team_wins += 1
                    elif team_id == str(opponent_id):
                        if competitor.get('winner', False):
                            opp_wins += 1
            except:
                continue

        # Build season series data
        season_series = {
            'team_wins': team_wins,
            'opponent_wins': opp_wins,
            'games': h2h_games
        }

        # Get most recent game
        previous_game = {}
        if h2h_games:
            recent = h2h_games[0]  # Schedule is already sorted
            try:
                comp = recent.get('competitions', [{}])[0]

                # Find our team and opponent in competitors
                our_team = None
                opp_team = None
                for competitor in comp.get('competitors', []):
                    team_id = str(competitor.get('team', {}).get('id'))
                    if team_id == str(our_team_id):
                        our_team = competitor
                    elif team_id == str(opponent_id):
                        opp_team = competitor

                if our_team and opp_team:
                    # Handle score being either a number or dict
                    our_score_raw = our_team.get('score', 0)
                    opp_score_raw = opp_team.get('score', 0)

                    if isinstance(our_score_raw, dict):
                        our_score = int(our_score_raw.get('value', 0) or our_score_raw.get('displayValue', '0'))
                    else:
                        our_score = int(our_score_raw) if our_score_raw else 0

                    if isinstance(opp_score_raw, dict):
                        opp_score = int(opp_score_raw.get('value', 0) or opp_score_raw.get('displayValue', '0'))
                    else:
                        opp_score = int(opp_score_raw) if opp_score_raw else 0

                    if our_score > opp_score:
                        result = 'Win'
                        winner = our_team.get('team', {}).get('displayName', '')
                        loser = opp_team.get('team', {}).get('displayName', '')
                    elif opp_score > our_score:
                        result = 'Loss'
                        winner = opp_team.get('team', {}).get('displayName', '')
                        loser = our_team.get('team', {}).get('displayName', '')
                    else:
                        result = 'Tie'
                        winner = ''
                        loser = ''

                    # Parse date
                    game_date_str = recent.get('date', '')
                    if game_date_str:
                        try:
                            game_dt = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                            date_formatted = game_dt.strftime('%B %d, %Y')
                            days_since = (datetime.now(ZoneInfo('UTC')) - game_dt).days
                        except:
                            date_formatted = game_date_str
                            days_since = 0
                    else:
                        date_formatted = ''
                        days_since = 0

                    # Determine home/away
                    our_home_away = our_team.get('homeAway', '')

                    # Get team abbreviations
                    our_abbrev = our_team.get('team', {}).get('abbreviation', 'TBD')
                    opp_abbrev = opp_team.get('team', {}).get('abbreviation', 'TBD')

                    # Build abbreviated score
                    if our_home_away == 'away':
                        score_abbrev = f"{our_abbrev} {our_score} @ {opp_abbrev} {opp_score}"
                    elif our_home_away == 'home':
                        score_abbrev = f"{our_abbrev} {our_score} vs {opp_abbrev} {opp_score}"
                    else:
                        score_abbrev = f"{our_abbrev} {our_score} - {opp_abbrev} {opp_score}"

                    previous_game = {
                        'result': result,
                        'score': f"{our_score}-{opp_score}",
                        'score_abbrev': score_abbrev,
                        'winner': winner,
                        'loser': loser,
                        'date': date_formatted,
                        'venue': comp.get('venue', {}).get('fullName', ''),
                        'venue_city': comp.get('venue', {}).get('address', {}).get('city', ''),
                        'days_since': days_since
                    }
            except:
                pass

        return {
            'season_series': season_series,
            'previous_game': previous_game
        }

    def _get_game_duration(self, team: dict, settings: dict) -> float:
        """
        Get game duration for a team based on mode

        Args:
            team: Team dict with sport, game_duration_mode, and game_duration_override fields
            settings: Settings dict with game_duration_default field

        Returns:
            Game duration in hours
        """
        mode = team.get('game_duration_mode', 'default')

        if mode == 'custom':
            # Use custom override from template
            return float(team.get('game_duration_override', 4.0))
        elif mode == 'sport':
            # Use sport-specific value from settings
            sport = team.get('sport', 'basketball').lower()
            sport_key = f'game_duration_{sport}'
            return float(settings.get(sport_key, settings.get('game_duration_default', 4.0)))
        else:  # mode == 'default'
            # Use global default from settings
            return float(settings.get('game_duration_default', 4.0))
