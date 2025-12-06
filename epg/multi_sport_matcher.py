"""
Multi-Sport Stream Matcher

Consolidated matching logic for multi-sport/multi-league event groups.
Used by both EPG generation and test modal preview to ensure consistent results.

Tiered Detection Flow:
  Tier 1: League indicator + Teams → Direct match (e.g., "NHL: Bruins vs Rangers")
  Tier 2: Sport indicator + Teams → Match within sport's leagues
  Tier 3a: Teams + Date + Time → Exact schedule match across all candidate leagues
  Tier 3b: Teams + Time only → Infer today, schedule match
  Tier 3c: Teams only → Closest game to now
  Tier 4a: One team + Date/Time → Search schedule for opponent by name
  Tier 4b: One team only → Search schedule for opponent, closest game
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MatchResult:
    """Result of matching a stream to an ESPN event."""

    # Match status
    matched: bool = False
    error: bool = False

    # Stream info (always set)
    stream: Dict = field(default_factory=dict)

    # Match details (when matched=True)
    team_result: Optional[Dict] = None
    event: Optional[Dict] = None
    detected_league: Optional[str] = None
    detection_tier: Optional[str] = None
    api_path_override: Optional[str] = None

    # Reason for no match (when matched=False and error=False)
    reason: Optional[str] = None
    detail: Optional[str] = None
    parsed_teams: Optional[Dict] = None

    # Error info (when error=True)
    error_message: Optional[str] = None


@dataclass
class MatcherConfig:
    """Configuration for MultiSportMatcher."""

    # Enabled leagues for matching
    enabled_leagues: List[str] = field(default_factory=list)
    soccer_enabled: bool = False

    # Custom regex settings
    custom_regex_teams: Optional[str] = None
    custom_regex_teams_enabled: bool = False
    custom_regex_date: Optional[str] = None
    custom_regex_date_enabled: bool = False
    custom_regex_time: Optional[str] = None
    custom_regex_time_enabled: bool = False

    # Event filtering
    include_final_events: bool = False

    @property
    def any_custom_enabled(self) -> bool:
        return (self.custom_regex_teams_enabled or
                self.custom_regex_date_enabled or
                self.custom_regex_time_enabled)


class MultiSportMatcher:
    """
    Matches streams to ESPN events using tiered multi-sport detection.

    This class consolidates the matching logic used by both:
    - EPG generation (refresh_event_group_core)
    - Test modal preview (api_event_epg_dispatcharr_streams_sse)

    Note: Filtering (game indicator, include/exclude regex) and overlap handling
    are NOT done here - they are handled by the caller. This class only does
    the core team/event matching.

    Usage:
        config = MatcherConfig(
            enabled_leagues=['nfl', 'nba', 'nhl'],
            soccer_enabled=True,
        )
        matcher = MultiSportMatcher(
            team_matcher=team_matcher,
            event_matcher=event_matcher,
            league_detector=league_detector,
            config=config
        )
        result = matcher.match_stream(stream)
    """

    def __init__(
        self,
        team_matcher,
        event_matcher,
        league_detector,
        config: MatcherConfig
    ):
        """
        Initialize MultiSportMatcher.

        Args:
            team_matcher: TeamMatcher instance for extracting teams from stream names
            event_matcher: EventMatcher instance for finding ESPN events
            league_detector: LeagueDetector instance for tiered league detection
            config: MatcherConfig with enabled leagues, regex settings, etc.
        """
        self.team_matcher = team_matcher
        self.event_matcher = event_matcher
        self.league_detector = league_detector
        self.config = config

    def match_stream(self, stream: Dict) -> MatchResult:
        """
        Match a stream to an ESPN event using tiered detection.

        Args:
            stream: Stream dict with at least 'name' key

        Returns:
            MatchResult with match status and details
        """
        result = MatchResult(stream=stream)
        stream_name = stream.get('name', '')

        try:
            # Step 1: Extract raw matchup data (teams, date, time, league indicator)
            raw_matchup = self.team_matcher.extract_raw_matchup(
                stream_name,
                custom_regex_teams=self.config.custom_regex_teams,
                custom_regex_teams_enabled=self.config.custom_regex_teams_enabled,
                custom_regex_date=self.config.custom_regex_date,
                custom_regex_date_enabled=self.config.custom_regex_date_enabled,
                custom_regex_time=self.config.custom_regex_time,
                custom_regex_time_enabled=self.config.custom_regex_time_enabled
            )

            if not raw_matchup.get('success'):
                result.reason = raw_matchup.get('reason', 'NO_TEAMS')
                return result

            raw_team1 = raw_matchup['team1']
            raw_team2 = raw_matchup['team2']
            game_date = raw_matchup['game_date']
            game_time = raw_matchup['game_time']
            indicator_league = raw_matchup['detected_league']
            indicator_sport = raw_matchup['detected_sport']

            team_result = None
            detected_league = None
            detected_api_path_override = None
            detection_tier = None

            # Step 2: Tier 1 - If league indicator found, try that league directly
            if indicator_league and indicator_league in (
                self.config.enabled_leagues + (['soccer'] if self.config.soccer_enabled else [])
            ):
                team_result = self._extract_teams(stream_name, indicator_league)
                if team_result.get('matched'):
                    detected_league = indicator_league
                    detection_tier = '1'

            # Step 3: Tier 2 - If sport indicator found, try leagues within that sport
            if (not team_result or not team_result.get('matched')) and indicator_sport:
                league, teams, tier = self._try_sport_leagues(
                    stream_name, indicator_sport, raw_team1, raw_team2
                )
                if league:
                    detected_league = league
                    team_result = teams
                    detection_tier = tier

            # Step 4: Tier 3 - Use caches to find candidate leagues from team names
            if not team_result or not team_result.get('matched'):
                league, teams, api_override, tier = self._try_cache_lookup(
                    stream_name, raw_team1, raw_team2, game_date, game_time
                )
                if league:
                    detected_league = league
                    team_result = teams
                    detected_api_path_override = api_override
                    detection_tier = tier

            # If no match yet, provide diagnostic info
            if not team_result or not team_result.get('matched'):
                if raw_team1 and raw_team2:
                    diagnosis = self.league_detector.diagnose_team_match_failure(raw_team1, raw_team2)
                    result.reason = diagnosis.get('reason')
                    result.detail = diagnosis.get('detail')
                    result.parsed_teams = {'team1': raw_team1, 'team2': raw_team2}
                else:
                    result.reason = 'NO_TEAMS'
                return result

            # Step 5: If still no league, try full detection (includes Tier 4)
            if not detected_league:
                league, tier = self._try_full_detection(stream_name, team_result)
                if league:
                    detected_league = league
                    detection_tier = tier
                    # Re-extract teams with the detected league if different
                    if detected_league != team_result.get('league'):
                        new_result = self._extract_teams(stream_name, detected_league)
                        if new_result.get('matched'):
                            team_result = new_result

            if not detected_league:
                result.reason = 'NO_LEAGUE_DETECTED'
                return result

            # Step 6: Find event in the detected league (with enrichment)
            # Defensive check: ensure team_result has required keys
            away_team_id = team_result.get('away_team_id') if team_result else None
            home_team_id = team_result.get('home_team_id') if team_result else None

            if not away_team_id or not home_team_id:
                logger.warning(f"Missing team IDs: away={away_team_id}, home={home_team_id} for stream '{stream_name}'")
                result.reason = 'MISSING_TEAM_IDS'
                return result

            event_result = self.event_matcher.find_and_enrich(
                away_team_id,
                home_team_id,
                detected_league,
                game_date=team_result.get('game_date'),
                game_time=team_result.get('game_time'),
                include_final_events=self.config.include_final_events,
                api_path_override=detected_api_path_override
            )

            # Defensive check: ensure event_result is a dict
            if event_result is None:
                logger.warning(f"find_and_enrich returned None for stream '{stream_name}'")
                event_result = {'found': False, 'reason': 'Enricher returned None'}

            # Step 7: If no game found, try alternate team combinations (disambiguation)
            if not event_result.get('found'):
                event_result, team_result = self._try_team_disambiguation(
                    team_result, raw_team1, raw_team2, detected_league,
                    detected_api_path_override
                )

            # Defensive check after disambiguation
            if event_result is None:
                logger.warning(f"Team disambiguation returned None event_result for stream '{stream_name}'")
                event_result = {'found': False, 'reason': 'Disambiguation returned None'}

            if event_result.get('found'):
                # Success!
                team_result['detected_league'] = detected_league

                if detection_tier:
                    logger.debug(f"[TIER {detection_tier}] {stream_name[:50]}... → {detected_league.upper()}")

                result.matched = True
                result.team_result = team_result
                result.event = event_result['event']
                result.detected_league = detected_league
                result.detection_tier = detection_tier
                result.api_path_override = detected_api_path_override
                return result
            else:
                # Event not found - return the reason
                result.reason = event_result.get('reason', 'No game found')
                return result

        except Exception as e:
            import traceback
            logger.warning(f"Error matching multi-sport stream '{stream_name}': {e}")
            logger.debug(f"Full traceback for '{stream_name}':\n{traceback.format_exc()}")
            result.error = True
            result.error_message = str(e)
            return result

    def _extract_teams(self, stream_name: str, league: str) -> Dict:
        """Extract teams using appropriate method based on config."""
        if self.config.any_custom_enabled:
            return self.team_matcher.extract_teams_with_selective_regex(
                stream_name, league,
                teams_pattern=self.config.custom_regex_teams,
                teams_enabled=self.config.custom_regex_teams_enabled,
                date_pattern=self.config.custom_regex_date,
                date_enabled=self.config.custom_regex_date_enabled,
                time_pattern=self.config.custom_regex_time,
                time_enabled=self.config.custom_regex_time_enabled
            )
        else:
            return self.team_matcher.extract_teams(stream_name, league)

    def _try_sport_leagues(
        self, stream_name: str, indicator_sport: str, raw_team1: str, raw_team2: str
    ) -> tuple:
        """Tier 2: Try leagues within the indicated sport."""
        from epg.league_detector import get_sport_for_league
        from database import get_soccer_slug_mapping

        if indicator_sport == 'soccer' and self.config.soccer_enabled:
            # Soccer uses dedicated SoccerMultiLeague cache (240+ leagues)
            soccer_slugs = self.league_detector._find_soccer_leagues_for_teams(raw_team1, raw_team2)
            slug_to_code = get_soccer_slug_mapping()
            sport_leagues = [slug_to_code.get(slug) for slug in soccer_slugs[:10] if slug in slug_to_code]
        else:
            sport_leagues = [l for l in self.config.enabled_leagues
                           if get_sport_for_league(l) == indicator_sport]

        for league in sport_leagues:
            candidate = self._extract_teams(stream_name, league)
            if candidate.get('matched'):
                return league, candidate, '2'

        return None, None, None

    def _try_cache_lookup(
        self, stream_name: str, raw_team1: str, raw_team2: str,
        game_date, game_time
    ) -> tuple:
        """Tier 3: Use caches to find candidate leagues from team names."""
        # Find candidate leagues from TeamLeagueCache (non-soccer)
        candidate_leagues = self.league_detector.find_candidate_leagues(
            raw_team1, raw_team2, include_soccer=False
        )
        # Filter to enabled leagues
        candidate_leagues = [l for l in candidate_leagues if l in self.config.enabled_leagues]

        # Collect all matched candidates for disambiguation
        matched_candidates = []

        # Also check soccer cache if enabled
        if self.config.soccer_enabled:
            from database import get_soccer_slug_mapping
            soccer_candidates = self.league_detector.get_soccer_candidates_with_team_ids(raw_team1, raw_team2)
            for sc in soccer_candidates:
                candidate = {
                    'matched': True,
                    'away_team_id': sc['team1_id'],
                    'away_team_name': sc['team1_name'],
                    'home_team_id': sc['team2_id'],
                    'home_team_name': sc['team2_name'],
                    'game_date': None,
                    'game_time': None,
                    'league': sc['league_code'] or sc['league_slug']
                }
                league_key = sc['league_code'] or sc['league_slug']
                matched_candidates.append((league_key, candidate, sc['api_path_override']))

        # Try each non-soccer candidate league
        for league in candidate_leagues:
            candidate = self._extract_teams(stream_name, league)
            if candidate.get('matched'):
                matched_candidates.append((league, candidate, None))

        # Disambiguate if needed
        if len(matched_candidates) == 1:
            detected_league, team_result, api_override = matched_candidates[0]
            return detected_league, team_result, api_override, '3c'
        elif len(matched_candidates) > 1:
            return self._disambiguate_candidates(
                matched_candidates, game_date, game_time
            )

        return None, None, None, None

    def _disambiguate_candidates(
        self, matched_candidates: List[tuple], game_date, game_time
    ) -> tuple:
        """Disambiguate between multiple candidate leagues by checking schedules."""
        leagues_with_games = []
        leagues_with_final_games = []

        for league, candidate, api_path_override in matched_candidates:
            test_result = self.event_matcher.find_event(
                candidate['away_team_id'],
                candidate['home_team_id'],
                league,
                game_date=candidate.get('game_date'),
                game_time=candidate.get('game_time'),
                include_final_events=self.config.include_final_events,
                api_path_override=api_path_override
            )

            if test_result.get('found'):
                # Calculate time difference if we have target time
                time_diff = float('inf')
                if candidate.get('game_time') and test_result.get('event_date'):
                    from datetime import datetime
                    try:
                        event_dt = datetime.fromisoformat(
                            test_result['event_date'].replace('Z', '+00:00')
                        )
                        target_time = candidate['game_time']
                        event_mins = event_dt.hour * 60 + event_dt.minute
                        target_mins = target_time.hour * 60 + target_time.minute
                        time_diff = abs(event_mins - target_mins)
                    except Exception:
                        pass
                leagues_with_games.append((league, candidate, test_result, time_diff))
            else:
                # Track if the game was found but is final/past
                reason = test_result.get('reason', '')
                if 'completed' in reason.lower() or 'past' in reason.lower() or 'final' in reason.lower():
                    leagues_with_final_games.append((league, candidate, test_result))

        detected_league = None
        team_result = None
        detected_api_path_override = None
        detection_tier = None

        if len(leagues_with_games) == 1:
            detected_league, team_result, _, _ = leagues_with_games[0]
            detection_tier = '3c'
        elif len(leagues_with_games) > 1:
            # Multiple leagues have games - pick best time match
            leagues_with_games.sort(key=lambda x: x[3])
            detected_league, team_result, _, _ = leagues_with_games[0]

            # Determine tier based on what data was used
            if game_date and game_time:
                detection_tier = '3a'
            elif game_time:
                detection_tier = '3b'
            else:
                detection_tier = '3c'

            logger.debug(
                f"Multi-league disambiguation: {len(leagues_with_games)} leagues have games, "
                f"selected {detected_league} (time_diff={leagues_with_games[0][3]} mins)"
            )

        # If no active game found but we found final games, use that league
        if not detected_league and leagues_with_final_games:
            detected_league, team_result, _ = leagues_with_final_games[0]
            detection_tier = '3c'
        # If no game found in any league, fall back to first match
        elif not detected_league and matched_candidates:
            detected_league, team_result, api_override = matched_candidates[0]
            detected_api_path_override = api_override
            detection_tier = '3c'

        # For soccer leagues detected via cache, check if we need api_path_override
        if detected_league and not detected_api_path_override:
            for league_key, candidate, api_override in matched_candidates:
                if league_key == detected_league and api_override:
                    detected_api_path_override = api_override
                    break

        return detected_league, team_result, detected_api_path_override, detection_tier

    def _try_full_detection(self, stream_name: str, team_result: Dict) -> tuple:
        """Step 5: Try full detection with team IDs (includes Tier 4)."""
        full_detection = self.league_detector.detect(
            stream_name=stream_name,
            team1=team_result.get('away_team_name', ''),
            team2=team_result.get('home_team_name', ''),
            team1_id=team_result.get('away_team_id'),
            team2_id=team_result.get('home_team_id'),
            game_date=team_result.get('game_date'),
            game_time=team_result.get('game_time')
        )

        if full_detection.detected:
            return full_detection.league, full_detection.tier_detail or str(full_detection.tier)

        return None, None

    def _try_team_disambiguation(
        self, team_result: Dict, raw_team1: str, raw_team2: str,
        detected_league: str, api_path_override: str
    ) -> tuple:
        """Try alternate team combinations for ambiguous team names."""
        raw_away = team_result.get('raw_away', '') or raw_team1
        raw_home = team_result.get('raw_home', '') or raw_team2

        if not (raw_away and raw_home):
            return {'found': False}, team_result

        # Get all teams matching each raw name
        all_away_teams = self.team_matcher.get_all_matching_teams(
            raw_away, detected_league, max_results=5
        )
        all_home_teams = self.team_matcher.get_all_matching_teams(
            raw_home, detected_league, max_results=5
        )

        # Try all combinations (skip the first one - already tried)
        tried_pairs = {(team_result['away_team_id'], team_result['home_team_id'])}

        for away_candidate in all_away_teams:
            for home_candidate in all_home_teams:
                pair = (away_candidate['id'], home_candidate['id'])
                if pair in tried_pairs:
                    continue
                tried_pairs.add(pair)

                alt_result = self.event_matcher.find_and_enrich(
                    away_candidate['id'],
                    home_candidate['id'],
                    detected_league,
                    game_date=team_result.get('game_date'),
                    game_time=team_result.get('game_time'),
                    include_final_events=self.config.include_final_events,
                    api_path_override=api_path_override
                )

                if alt_result.get('found'):
                    # Update team_result with alternate teams
                    team_result['away_team_id'] = away_candidate['id']
                    team_result['away_team_name'] = away_candidate['name']
                    team_result['away_team_abbrev'] = away_candidate.get('abbrev', '')
                    team_result['home_team_id'] = home_candidate['id']
                    team_result['home_team_name'] = home_candidate['name']
                    team_result['home_team_abbrev'] = home_candidate.get('abbrev', '')
                    team_result['disambiguated'] = True

                    logger.debug(
                        f"Team disambiguation: '{raw_away}' vs '{raw_home}' → "
                        f"'{away_candidate['name']}' vs '{home_candidate['name']}'"
                    )
                    return alt_result, team_result

        return {'found': False}, team_result
