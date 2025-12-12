"""
Event Enricher - Single-pass event enrichment pipeline.

Consolidates all ESPN event parsing and enrichment into one place.
Replaces scattered normalization logic across orchestrator, event_matcher, and espn_client.

Usage:
    from api.espn_client import ESPNClient
    from epg.event_enricher import EventEnricher

    espn = ESPNClient()
    enricher = EventEnricher(espn)

    # Enrich a raw ESPN event
    enriched = enricher.enrich_event(raw_event, league='nfl')

    # Access normalized data
    print(enriched['home_team']['name'])
    print(enriched['odds']['spread'])
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from zoneinfo import ZoneInfo
import threading

from utils.logger import get_logger
from epg.league_config import get_league_config, parse_api_path, is_soccer_league, SoccerCompat

logger = get_logger(__name__)


class EventEnricher:
    """
    Single-pass event enrichment pipeline.

    Takes raw ESPN event data from any source (schedule, scoreboard, summary)
    and produces a fully-enriched event dict with consistent structure.

    Caching:
    - Scoreboard data cached by (league, date) - cleared per generation
    - Enriched events cached by event_id - cleared per generation
    - Team stats uses ESPNClient's built-in 6-hour cache
    """

    def __init__(self, espn_client, db_connection_func: Optional[Callable] = None):
        """
        Initialize EventEnricher.

        Args:
            espn_client: ESPNClient instance for API calls
            db_connection_func: Function that returns DB connection (for league config)
        """
        self.espn = espn_client
        self.db_connection_func = db_connection_func

        # Caches (cleared per generation)
        self._scoreboard_cache: Dict[str, Dict] = {}  # key: "sport:league:YYYYMMDD"
        self._enriched_events: Dict[str, Dict] = {}   # key: event_id

        # League config cache
        self._league_config_cache: Dict[str, Dict] = {}

        # Thread safety
        self._scoreboard_lock = threading.Lock()
        self._enriched_lock = threading.Lock()

    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================

    def clear_caches(self):
        """Clear all caches. Call at start of EPG generation."""
        with self._scoreboard_lock:
            self._scoreboard_cache.clear()
        with self._enriched_lock:
            self._enriched_events.clear()
        self._league_config_cache.clear()
        logger.debug("EventEnricher caches cleared")

    def _get_scoreboard_cached(self, sport: str, league: str, date_str: str) -> Optional[Dict]:
        """
        Get scoreboard with caching.

        Args:
            sport: Sport type (e.g., 'football')
            league: League code (e.g., 'nfl')
            date_str: Date in YYYYMMDD format

        Returns:
            Scoreboard data dict or None
        """
        cache_key = f"{sport}:{league}:{date_str}"

        # Fast path: check cache without lock
        if cache_key in self._scoreboard_cache:
            return self._scoreboard_cache[cache_key]

        # Slow path: acquire lock
        with self._scoreboard_lock:
            # Double-check after lock
            if cache_key in self._scoreboard_cache:
                return self._scoreboard_cache[cache_key]

            # Fetch from ESPN
            scoreboard_data = self.espn.get_scoreboard(sport, league, date_str)

            # Cache result (even None to avoid re-fetching failures)
            self._scoreboard_cache[cache_key] = scoreboard_data

            return scoreboard_data

    def _get_league_config(self, league_code: str) -> Optional[Dict]:
        """Get league configuration with caching."""
        return get_league_config(
            league_code,
            self.db_connection_func,
            self._league_config_cache
        )

    # =========================================================================
    # NORMALIZATION HELPERS
    # =========================================================================

    def _normalize_score(self, score_data: Any) -> Optional[int]:
        """
        Normalize score from various ESPN formats to int.

        ESPN returns scores as:
        - String: "101"
        - Int: 101
        - Dict: {"value": 101}
        - None: for future games

        Args:
            score_data: Raw score from ESPN API

        Returns:
            Integer score or None if not available
        """
        if score_data is None:
            return None
        if isinstance(score_data, dict):
            value = score_data.get('value')
            if value is None:
                return None
            return int(value) if value else 0
        if isinstance(score_data, str):
            if not score_data or not score_data.strip():
                return None
            try:
                return int(score_data)
            except ValueError:
                return None
        if isinstance(score_data, (int, float)):
            return int(score_data)
        return None

    def _normalize_broadcasts(self, broadcasts_data: Any) -> List[str]:
        """
        Normalize broadcasts to list of strings.

        ESPN returns broadcasts as:
        - List of dicts: [{"names": ["ESPN", "ABC"]}, {"names": ["ESPN2"]}]
        - List of strings: ["ESPN", "ABC"]
        - Single dict: {"name": "ESPN"}
        - None

        Args:
            broadcasts_data: Raw broadcasts from ESPN API

        Returns:
            List of broadcast network strings
        """
        if not broadcasts_data:
            return []

        result = []

        if isinstance(broadcasts_data, list):
            for item in broadcasts_data:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, dict):
                    # Handle {"names": ["ESPN", "ABC"]} format
                    names = item.get('names', [])
                    if names:
                        result.extend(names)
                    # Handle {"name": "ESPN"} format
                    elif item.get('name'):
                        result.append(item['name'])
        elif isinstance(broadcasts_data, dict):
            names = broadcasts_data.get('names', [])
            if names:
                result.extend(names)
            elif broadcasts_data.get('name'):
                result.append(broadcasts_data['name'])

        # Remove empty strings and duplicates while preserving order
        seen = set()
        deduped = []
        for b in result:
            if b and b not in seen:
                seen.add(b)
                deduped.append(b)

        return deduped

    def _extract_record(self, records_data: Any) -> Dict[str, Any]:
        """
        Extract record from ESPN records array.

        ESPN uses 'records' (plural) in scoreboard, 'record' (singular) in schedule.

        Args:
            records_data: Record data from ESPN (list or dict)

        Returns:
            Dict with summary, wins, losses, ties
        """
        if not records_data:
            return {'summary': '', 'wins': 0, 'losses': 0, 'ties': 0}

        # Handle single dict
        if isinstance(records_data, dict):
            return {
                'summary': records_data.get('summary', ''),
                'wins': records_data.get('wins', 0),
                'losses': records_data.get('losses', 0),
                'ties': records_data.get('ties', 0)
            }

        # Handle list - find 'total' or first record
        if isinstance(records_data, list) and records_data:
            total_record = next(
                (r for r in records_data if r.get('type') == 'total'),
                records_data[0]
            )
            return {
                'summary': total_record.get('summary', ''),
                'wins': total_record.get('wins', 0),
                'losses': total_record.get('losses', 0),
                'ties': total_record.get('ties', 0)
            }

        return {'summary': '', 'wins': 0, 'losses': 0, 'ties': 0}

    # =========================================================================
    # MAIN ENRICHMENT METHODS
    # =========================================================================

    def normalize_event_structure(self, raw_event: Dict, sport: str = None, league: str = None) -> Dict:
        """
        Normalize raw ESPN event into canonical structure.

        This is the single place for event parsing. Handles events from:
        - Schedule API
        - Scoreboard API
        - Event Summary API

        Args:
            raw_event: Raw event dict from ESPN API
            sport: Sport type (optional, for metadata)
            league: League code (optional, for metadata)

        Returns:
            Normalized event dict with consistent structure
        """
        if not raw_event:
            return {}

        event_id = raw_event.get('id', '')

        # Start with base fields
        event = {
            'id': event_id,
            'uid': raw_event.get('uid', ''),
            'name': raw_event.get('name', ''),
            'short_name': raw_event.get('shortName', ''),
            'date': raw_event.get('date', ''),
            'sport': sport,
            'league': league,
        }

        # Parse competition data
        competitions = raw_event.get('competitions', [])
        if not competitions:
            # Minimal event without competition data
            event['home_team'] = {}
            event['away_team'] = {}
            event['venue'] = {}
            event['broadcasts'] = []
            event['status'] = {'name': '', 'state': 'pre', 'completed': False}
            event['competitions'] = []
            return event

        comp = competitions[0]

        # === Teams ===
        competitors = comp.get('competitors', [])
        home_team = {}
        away_team = {}

        for competitor in competitors:
            team_data = competitor.get('team', {})
            # ESPN uses 'records' (plural) in scoreboard, 'record' (singular) in schedule
            records_data = competitor.get('records') or competitor.get('record') or []

            team_info = {
                'id': team_data.get('id', competitor.get('id', '')),
                'name': team_data.get('displayName') or team_data.get('name', ''),
                'abbrev': team_data.get('abbreviation', ''),
                'logo': team_data.get('logo'),
                'color': team_data.get('color'),
                'alt_color': team_data.get('alternateColor'),
                'score': self._normalize_score(competitor.get('score')),
                'record': self._extract_record(records_data),
            }

            if competitor.get('homeAway') == 'home':
                home_team = team_info
            elif competitor.get('homeAway') == 'away':
                away_team = team_info

        event['home_team'] = home_team
        event['away_team'] = away_team

        # === Venue ===
        venue_data = comp.get('venue', {})
        address = venue_data.get('address', {})
        event['venue'] = {
            'name': venue_data.get('fullName') or venue_data.get('shortName') or venue_data.get('name', ''),
            'city': address.get('city', ''),
            'state': address.get('state', ''),
            'indoor': venue_data.get('indoor', False),
        }

        # === Broadcasts ===
        event['broadcasts'] = self._normalize_broadcasts(comp.get('broadcasts'))

        # === Odds ===
        # Handle None entries in odds list (ESPN sometimes returns [None])
        odds_data = comp.get('odds', [])
        primary_odds = odds_data[0] if odds_data else None
        if primary_odds:
            event['odds'] = {
                'spread': primary_odds.get('details'),
                'over_under': primary_odds.get('overUnder'),
                'home_moneyline': primary_odds.get('homeTeamOdds', {}).get('moneyLine'),
                'away_moneyline': primary_odds.get('awayTeamOdds', {}).get('moneyLine'),
                'provider': (primary_odds.get('provider') or {}).get('name'),
            }
            event['has_odds'] = True
        else:
            event['odds'] = {}
            event['has_odds'] = False

        # === Status ===
        status_data = comp.get('status', {})
        status_type = status_data.get('type', {})
        event['status'] = {
            'name': status_type.get('name', ''),
            'state': status_type.get('state', 'pre'),
            'completed': status_type.get('completed', False),
            'detail': status_type.get('detail', ''),
            'period': status_data.get('period', 0),
        }

        # === Season ===
        season_data = raw_event.get('season', {})
        event['season'] = {
            'year': season_data.get('year'),
            'slug': season_data.get('slug', 'regular'),
        }

        # === Preserve raw competition for edge cases ===
        event['competitions'] = competitions

        # === Enrichment metadata ===
        event['_enrichment'] = {
            'is_normalized': True,
            'normalized_at': datetime.now(ZoneInfo('UTC')).isoformat(),
        }

        return event

    def enrich_with_scoreboard(
        self,
        event: Dict,
        league: str,
        date_str: str = None
    ) -> Dict:
        """
        Enrich event with scoreboard data (odds, live scores, broadcasts).

        Args:
            event: Normalized event dict
            league: League code
            date_str: Date to fetch scoreboard for (YYYYMMDD). If None, uses event date.

        Returns:
            Event dict with scoreboard enrichment
        """
        if not event or not event.get('id'):
            return event

        # Get league config
        config = self._get_league_config(league)
        if not config:
            logger.warning(f"No league config for {league}, skipping scoreboard enrichment")
            return event

        sport, api_league = parse_api_path(config['api_path'])
        if not sport or not api_league:
            return event

        # Determine date
        if not date_str:
            event_date = event.get('date', '')
            if event_date:
                try:
                    dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
                    date_str = dt.strftime('%Y%m%d')
                except (ValueError, TypeError):
                    return event
            else:
                return event

        # Fetch scoreboard
        scoreboard_data = self._get_scoreboard_cached(sport, api_league, date_str)
        if not scoreboard_data or 'events' not in scoreboard_data:
            return event

        # Find matching event in scoreboard
        event_id = event['id']
        for sb_event in scoreboard_data.get('events', []):
            if sb_event.get('id') == event_id:
                # Merge scoreboard data into event
                return self._merge_scoreboard_data(event, sb_event)

        return event

    def _merge_scoreboard_data(self, event: Dict, scoreboard_event: Dict) -> Dict:
        """
        Merge scoreboard data into existing event.

        Scoreboard provides more current data for:
        - Odds (live updates)
        - Scores (live updates)
        - Broadcasts (more complete)
        - Status (real-time)
        """
        if not scoreboard_event.get('competitions'):
            return event

        sb_comp = scoreboard_event['competitions'][0]

        # Update odds
        odds_data = sb_comp.get('odds', [])
        if odds_data:
            primary_odds = odds_data[0]
            # Handle None entries in odds list (ESPN sometimes returns [None])
            if primary_odds:
                event['odds'] = {
                    'spread': primary_odds.get('details'),
                    'over_under': primary_odds.get('overUnder'),
                    'home_moneyline': primary_odds.get('homeTeamOdds', {}).get('moneyLine'),
                    'away_moneyline': primary_odds.get('awayTeamOdds', {}).get('moneyLine'),
                    'provider': (primary_odds.get('provider') or {}).get('name'),
                }
                event['has_odds'] = True

        # Update scores and records from scoreboard (more current)
        for competitor in sb_comp.get('competitors', []):
            team_key = 'home_team' if competitor.get('homeAway') == 'home' else 'away_team'
            if team_key in event and event[team_key]:
                # Update score
                score = self._normalize_score(competitor.get('score'))
                if score is not None:
                    event[team_key]['score'] = score

                # Update record if available
                records_data = competitor.get('records') or competitor.get('record')
                if records_data:
                    event[team_key]['record'] = self._extract_record(records_data)

        # Update broadcasts (scoreboard often has more complete info)
        sb_broadcasts = self._normalize_broadcasts(sb_comp.get('broadcasts'))
        if sb_broadcasts:
            event['broadcasts'] = sb_broadcasts

        # Update status
        status_data = sb_comp.get('status', {})
        status_type = status_data.get('type', {})
        event['status'] = {
            'name': status_type.get('name', event['status'].get('name', '')),
            'state': status_type.get('state', event['status'].get('state', 'pre')),
            'completed': status_type.get('completed', event['status'].get('completed', False)),
            'detail': status_type.get('detail', event['status'].get('detail', '')),
            'period': status_data.get('period', event['status'].get('period', 0)),
        }

        # Update competitions array
        event['competitions'] = scoreboard_event['competitions']

        # Update enrichment metadata
        event['_enrichment']['has_scoreboard_data'] = True
        event['_enrichment']['scoreboard_enriched_at'] = datetime.now(ZoneInfo('UTC')).isoformat()

        return event

    def enrich_with_team_stats(self, event: Dict, league: str) -> Dict:
        """
        Enrich event with team stats (records, logos, colors, conference, rank).

        Uses ESPNClient's get_team_stats() which has built-in 6-hour caching.

        Args:
            event: Normalized event dict
            league: League code

        Returns:
            Event dict with team stats enrichment
        """
        if not event:
            return event

        config = self._get_league_config(league)
        if not config:
            return event

        sport, api_league = parse_api_path(config['api_path'])
        if not sport or not api_league:
            return event

        # Enrich both teams
        for team_key in ['home_team', 'away_team']:
            team = event.get(team_key)
            if not team or not team.get('id'):
                continue

            team_id = team['id']

            # Fetch team info (logos, colors)
            team_info = self.espn.get_team_info(sport, api_league, team_id)
            if team_info and 'team' in team_info:
                espn_team = team_info['team']

                # Update logo if not set
                if not team.get('logo'):
                    logos = espn_team.get('logos', [])
                    if logos:
                        team['logo'] = logos[0].get('href')

                # Update colors if not set
                if not team.get('color'):
                    team['color'] = espn_team.get('color')
                if not team.get('alt_color'):
                    team['alt_color'] = espn_team.get('alternateColor')

            # Fetch team stats (record, conference, rank, etc.)
            team_stats = self.espn.get_team_stats(sport, api_league, team_id)
            if team_stats:
                # Update record if team stats has better data
                stats_record = team_stats.get('record', {})
                if stats_record and stats_record.get('summary') and stats_record.get('summary') != '0-0':
                    team['record'] = stats_record

                # Add additional stats
                # Streak: ESPN provides signed value (positive=wins, negative=losses)
                streak_raw = team_stats.get('streak_count', 0)
                team['streak_raw'] = streak_raw  # Signed value for conditionals
                team['streak_count'] = abs(streak_raw)  # Absolute value for "X-game streak"
                # Formatted as "W5" or "L2" for {home_team_streak}/{away_team_streak}
                if streak_raw > 0:
                    team['streak'] = f"W{streak_raw}"
                elif streak_raw < 0:
                    team['streak'] = f"L{abs(streak_raw)}"
                else:
                    team['streak'] = ''
                team['home_record'] = team_stats.get('home_record', '')
                team['away_record'] = team_stats.get('away_record', '')
                team['division_record'] = team_stats.get('division_record', '')
                team['rank'] = team_stats.get('rank', 0)
                team['playoff_seed'] = team_stats.get('playoff_seed', 0)
                team['games_back'] = team_stats.get('games_back', 0)
                team['ppg'] = team_stats.get('ppg', 0)
                team['papg'] = team_stats.get('papg', 0)

                # Conference/division info (skip for soccer)
                if not SoccerCompat.should_skip_division(league):
                    conf_name = team_stats.get('conference_name', '')
                    conf_abbrev = team_stats.get('conference_abbrev', '')
                    div_name = team_stats.get('division_name', '')

                    # Store generic keys for backwards compatibility
                    team['conference'] = conf_name
                    team['conference_abbrev'] = conf_abbrev
                    team['division'] = div_name

                    # Store college/pro specific keys based on league type
                    is_college = 'college' in league.lower()
                    if is_college:
                        team['college_conference'] = conf_name
                        team['college_conference_abbrev'] = conf_abbrev
                    else:
                        team['pro_conference'] = conf_name
                        team['pro_conference_abbrev'] = conf_abbrev
                        team['pro_division'] = div_name

        # Update enrichment metadata
        event['_enrichment']['has_team_stats'] = True
        event['_enrichment']['team_stats_enriched_at'] = datetime.now(ZoneInfo('UTC')).isoformat()

        return event

    def enrich_event(
        self,
        raw_event: Dict,
        league: str,
        include_scoreboard: bool = True,
        include_team_stats: bool = True,
        scoreboard_date: str = None
    ) -> Dict:
        """
        Full enrichment pipeline for a raw ESPN event.

        This is the main entry point for event enrichment. Combines:
        1. normalize_event_structure() - Parse into consistent format
        2. enrich_with_scoreboard() - Add live data (odds, scores, broadcasts)
        3. enrich_with_team_stats() - Add team context (records, logos, conference)

        Args:
            raw_event: Raw event from ESPN API
            league: League code
            include_scoreboard: Whether to fetch scoreboard data (default True)
            include_team_stats: Whether to fetch team stats (default True)
            scoreboard_date: Override date for scoreboard fetch (YYYYMMDD)

        Returns:
            Fully enriched event dict
        """
        if not raw_event:
            return {}

        event_id = raw_event.get('id', '')

        # Check enriched event cache
        if event_id and event_id in self._enriched_events:
            cached = self._enriched_events[event_id]
            # Return cached if it has the requested enrichments
            enrichment = cached.get('_enrichment', {})
            if (not include_scoreboard or enrichment.get('has_scoreboard_data')) and \
               (not include_team_stats or enrichment.get('has_team_stats')):
                return cached

        # Get sport/league for normalization
        config = self._get_league_config(league)
        sport = None
        api_league = None
        if config:
            sport, api_league = parse_api_path(config['api_path'])

        # Step 1: Normalize structure
        event = self.normalize_event_structure(raw_event, sport, api_league or league)

        # Step 2: Enrich with scoreboard
        if include_scoreboard:
            event = self.enrich_with_scoreboard(event, league, scoreboard_date)

        # Step 3: Enrich with team stats
        if include_team_stats:
            event = self.enrich_with_team_stats(event, league)

        # Mark as fully enriched
        event['_enrichment']['is_fully_enriched'] = True
        event['_enrichment']['enriched_at'] = datetime.now(ZoneInfo('UTC')).isoformat()

        # Cache the enriched event
        if event_id:
            with self._enriched_lock:
                self._enriched_events[event_id] = event

        return event

    def enrich_events_batch(
        self,
        raw_events: List[Dict],
        league: str,
        include_scoreboard: bool = True,
        include_team_stats: bool = True
    ) -> List[Dict]:
        """
        Enrich multiple events efficiently.

        Pre-fetches scoreboards for all unique dates to minimize API calls.

        Args:
            raw_events: List of raw events from ESPN API
            league: League code
            include_scoreboard: Whether to fetch scoreboard data
            include_team_stats: Whether to fetch team stats

        Returns:
            List of enriched events
        """
        if not raw_events:
            return []

        # Pre-fetch scoreboards for all dates if needed
        if include_scoreboard:
            config = self._get_league_config(league)
            if config:
                sport, api_league = parse_api_path(config['api_path'])
                if sport and api_league:
                    # Collect unique dates
                    dates = set()
                    for event in raw_events:
                        event_date = event.get('date', '')
                        if event_date:
                            try:
                                dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
                                dates.add(dt.strftime('%Y%m%d'))
                            except (ValueError, TypeError):
                                pass

                    # Pre-fetch all scoreboards
                    for date_str in dates:
                        self._get_scoreboard_cached(sport, api_league, date_str)

        # Enrich each event
        enriched = []
        for raw_event in raw_events:
            enriched.append(self.enrich_event(
                raw_event, league,
                include_scoreboard=include_scoreboard,
                include_team_stats=include_team_stats
            ))

        return enriched


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_event_enricher() -> EventEnricher:
    """
    Create an EventEnricher instance with default configuration.

    Returns:
        Configured EventEnricher instance
    """
    from api.espn_client import ESPNClient
    from database import get_connection

    espn = ESPNClient()
    return EventEnricher(espn, db_connection_func=get_connection)
