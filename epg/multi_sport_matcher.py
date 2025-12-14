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
  Tier 4b+: Both teams matched but no game → Search schedules for RAW opponent name
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Union
from utils.logger import get_logger
from utils.match_result import (
    ResultCategory, FilteredReason, FailedReason, MatchedTier,
    is_filtered, is_failed
)

logger = get_logger(__name__)


def _get_reason_value(reason: Union[FilteredReason, FailedReason, str, None]) -> str:
    """Get string value from reason (enum or string)."""
    if reason is None:
        return 'unknown'
    if isinstance(reason, (FilteredReason, FailedReason)):
        return reason.value
    return str(reason)


def _log_result(
    stream_name: str,
    category: str,
    reason: Union[FilteredReason, FailedReason, str, None] = None,
    tier: str = None,
    league: str = None,
    detail: str = None,
    max_len: int = 60
):
    """
    Log a match result with consistent formatting.

    Formats:
        [FILTERED:reason] stream_name | detail
        [FAILED:reason] stream_name | detail
        [TIER X] stream_name → LEAGUE | detail
    """
    display_name = stream_name[:max_len]
    if len(stream_name) > max_len:
        display_name += '...'

    if category == 'matched' and tier:
        league_str = (league or '').upper()
        if detail:
            logger.info(f"[TIER {tier}] {display_name} → {league_str} | {detail}")
        else:
            logger.info(f"[TIER {tier}] {display_name} → {league_str}")
    elif category == 'filtered':
        reason_str = _get_reason_value(reason)
        if detail:
            logger.info(f"[FILTERED:{reason_str}] {display_name} | {detail}")
        else:
            logger.debug(f"[FILTERED:{reason_str}] {display_name}")
    elif category == 'failed':
        reason_str = _get_reason_value(reason)
        if detail:
            logger.info(f"[FAILED:{reason_str}] {display_name} | {detail}")
        else:
            logger.info(f"[FAILED:{reason_str}] {display_name}")


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

    # League not enabled (found in a league user hasn't enabled for this group)
    league_not_enabled: bool = False
    league_name: Optional[str] = None  # Friendly name for display

    # Exception keyword (stripped during matching, used for consolidation)
    exception_keyword: Optional[str] = None

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

        # Exception keyword was pre-extracted in app.py Step 2.6 and attached to stream dict
        # Team matcher's _prepare_text_for_parsing() handles stripping for matching
        result.exception_keyword = stream.get('exception_keyword')

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
            pre_found_event = None  # Event found during disambiguation (optimization)

            # Step 2: Tier 1 - If league indicator found, try that league directly
            # Note: Don't filter by enabled - we search ALL leagues, check enabled after match
            if indicator_league:
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
            # Also captures pre-found event from disambiguation to avoid redundant API call
            if not team_result or not team_result.get('matched'):
                league, teams, api_override, tier, pre_found = self._try_cache_lookup(
                    stream_name, raw_team1, raw_team2, game_date, game_time
                )
                if league:
                    detected_league = league
                    team_result = teams
                    detected_api_path_override = api_override
                    detection_tier = tier
                    pre_found_event = pre_found  # May be None if single candidate or no game found

            # Step 5: Tier 4 - Single-team schedule fallback (NAIA vs NCAA)
            # This is critical for teams not in cache but discoverable via schedule
            if not team_result or not team_result.get('matched'):
                # Ensure datetime values have timezone for accurate comparison
                from zoneinfo import ZoneInfo
                tz_game_date = game_date
                tz_game_time = game_time
                if tz_game_date and not tz_game_date.tzinfo:
                    tz_game_date = tz_game_date.replace(tzinfo=ZoneInfo('America/New_York'))
                if tz_game_time and not tz_game_time.tzinfo:
                    tz_game_time = tz_game_time.replace(tzinfo=ZoneInfo('America/New_York'))

                # Try full detection which includes Tier 4 single-team fallback
                detection_result = self.league_detector.detect(
                    stream_name=stream_name,
                    team1=raw_team1,
                    team2=raw_team2,
                    game_date=tz_game_date,
                    game_time=tz_game_time
                )

                if detection_result.detected and detection_result.league:
                    detected_league = detection_result.league
                    detection_tier = detection_result.tier_detail

                    # If Tier 4 found the event directly (has event_id), use that instead
                    # of trying _extract_teams() which may produce incorrect fuzzy matches
                    # (e.g., "calumet college" fuzzy-matching to "California Golden Bears")
                    if detection_result.event_id:
                        # Tier 4 already found the exact event - use it directly
                        team_result = {
                            'matched': True,
                            'tier4_event_id': detection_result.event_id,
                            'tier4_league': detected_league,
                            'raw_away': raw_team1,
                            'raw_home': raw_team2
                        }
                        logger.debug(f"Using Tier 4 event_id {detection_result.event_id} directly")
                    else:
                        # No event_id from Tier 4 - try to extract teams the normal way
                        team_result = self._extract_teams(stream_name, detected_league)

            # If still no match, provide diagnostic info
            if not team_result or not team_result.get('matched'):
                if raw_team1 and raw_team2:
                    diagnosis = self.league_detector.diagnose_team_match_failure(
                        raw_team1, raw_team2, stream_name=stream_name
                    )
                    result.reason = diagnosis.get('reason')
                    result.detail = diagnosis.get('detail')
                    result.parsed_teams = {'team1': raw_team1, 'team2': raw_team2}
                    # Determine category based on reason type
                    if is_filtered(result.reason):
                        _log_result(stream_name, 'filtered', result.reason, detail=result.detail)
                    else:
                        _log_result(stream_name, 'failed', result.reason, detail=result.detail)
                else:
                    result.reason = FailedReason.TEAMS_NOT_PARSED
                    _log_result(stream_name, 'failed', result.reason)
                return result

            # Step 6: If still no league but teams matched, try full detection
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
                result.reason = FailedReason.NO_LEAGUE_DETECTED
                _log_result(stream_name, 'failed', result.reason)
                return result

            # Step 7: Find event in the detected league (with enrichment)
            # Handle Tier 4 case where we have event_id directly (NAIA teams not in DB)
            if team_result.get('tier4_event_id'):
                # Tier 4 already found the event - fetch it by ID
                event_id = team_result['tier4_event_id']
                logger.debug(f"Using Tier 4 event_id {event_id} for '{stream_name[:40]}...'")

                # Get the event via event summary API
                # Note: get_event_by_id returns the event dict directly (already enriched), not wrapped
                event = self.event_matcher.get_event_by_id(
                    event_id, detected_league
                )

                if event and event.get('id'):
                    # Check if event is completed/past - apply same filtering as find_event()
                    from datetime import datetime
                    from zoneinfo import ZoneInfo
                    from utils.time_format import get_today_in_user_tz, get_user_timezone
                    from database import get_connection

                    # Get event status - check both direct status and competitions[0].status
                    status = event.get('status', {})
                    # Fallback to competitions[0].status.type if status is empty
                    if not status or not status.get('completed'):
                        comp = event.get('competitions', [{}])[0]
                        comp_status = comp.get('status', {})
                        status_type = comp_status.get('type', {})
                        if status_type.get('completed'):
                            status = {
                                'name': status_type.get('name', ''),
                                'completed': status_type.get('completed', False)
                            }
                    is_completed = status.get('completed', False) or 'FINAL' in status.get('name', '').upper()

                    if is_completed:
                        # Check if it's a past day or today's final
                        event_date_str = event.get('date', '')
                        if event_date_str:
                            try:
                                event_dt = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                                user_tz_str = get_user_timezone(get_connection)
                                user_tz = ZoneInfo(user_tz_str)
                                event_in_user_tz = event_dt.astimezone(user_tz)
                                event_day = event_in_user_tz.date()
                                today = get_today_in_user_tz(get_connection)

                                if event_day < today:
                                    # Past day completed event - always excluded
                                    event_result = {'found': False, 'reason': FilteredReason.EVENT_PAST}
                                    logger.debug(f"Tier 4 event {event_id} filtered: past completed game ({event_day})")
                                elif event_day == today and not self.config.include_final_events:
                                    # Today's final - excluded by setting
                                    event_result = {'found': False, 'reason': FilteredReason.EVENT_FINAL}
                                    logger.debug(f"Tier 4 event {event_id} filtered: today's final (excluded)")
                                else:
                                    # Today's final but include_final_events=True, or future completed (rare)
                                    event_result = {'found': True, 'event': event, 'event_id': event.get('id')}
                            except Exception as e:
                                logger.debug(f"Error checking Tier 4 event date: {e}")
                                # On error, allow the event through
                                event_result = {'found': True, 'event': event, 'event_id': event.get('id')}
                        else:
                            # No date - treat as found
                            event_result = {'found': True, 'event': event, 'event_id': event.get('id')}
                    else:
                        # Not completed - event is valid
                        event_result = {'found': True, 'event': event, 'event_id': event.get('id')}

                    # Populate team names from event for UI display (Tier 4 doesn't have team IDs)
                    # Always populate, even for filtered events, so UI can show team names
                    home_team = event.get('home_team', {})
                    away_team = event.get('away_team', {})
                    team_result['home_team_name'] = home_team.get('name', team_result.get('raw_home', '?'))
                    team_result['away_team_name'] = away_team.get('name', team_result.get('raw_away', '?'))
                    team_result['home_team_id'] = home_team.get('id')
                    team_result['away_team_id'] = away_team.get('id')
                else:
                    event_result = {'found': False, 'reason': f'Tier 4 event {event_id} not found'}
            else:
                # Normal flow - need team IDs
                away_team_id = team_result.get('away_team_id') if team_result else None
                home_team_id = team_result.get('home_team_id') if team_result else None

                if not away_team_id or not home_team_id:
                    # Team IDs missing - try Tier 4b+ schedule search before giving up
                    # This handles cases where the detected league was wrong but we can
                    # still find the game by searching all leagues the teams appear in
                    logger.debug(
                        f"Missing team IDs for '{stream_name[:40]}...', "
                        f"trying Tier 4b+ schedule search"
                    )
                    schedule_result = self._search_schedules_for_raw_opponent(
                        team_result or {}, raw_team1, raw_team2,
                        detected_league, detected_api_path_override
                    )
                    if schedule_result:
                        event_result, team_result = schedule_result
                        if event_result.get('found'):
                            # Update detected_league from the successful search
                            detected_league = team_result.get('detected_league', detected_league)
                            # event_result is already set, will be handled by success path below
                        else:
                            # Tier 4b+ found an event but it was filtered (GAME_PAST or GAME_FINAL_EXCLUDED)
                            # Propagate the result with team names populated for UI display
                            reason = event_result.get('reason', 'No game found')
                            logger.debug(f"Tier 4b+ found filtered event for '{stream_name[:40]}...': {reason}")
                            # event_result and team_result are already set, fall through to end
                    else:
                        logger.warning(f"Missing team IDs: away={away_team_id}, home={home_team_id} for stream '{stream_name}'")
                        result.reason = 'MISSING_TEAM_IDS'
                        return result
                else:
                    # Have team IDs - proceed with normal event lookup
                    # Optimization: If disambiguation already found the event, enrich it instead
                    # of calling find_and_enrich() which would re-fetch the same event
                    if pre_found_event and pre_found_event.get('found'):
                        # Event already found during disambiguation - just enrich it
                        event_result = pre_found_event
                        if self.event_matcher.enricher and event_result.get('event'):
                            event_result['event'] = self.event_matcher.enricher.enrich_event(
                                event_result['event'],
                                detected_league,
                                include_scoreboard=True,
                                include_team_stats=True
                            )
                        logger.debug(f"Using pre-found event from disambiguation for '{stream_name[:40]}...'")
                    else:
                        # No pre-found event - fetch and enrich
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
            # BUT: Skip disambiguation if original reason was GAME_PAST or GAME_FINAL_EXCLUDED
            # Those reasons mean we DID find the specified game, it was just filtered as past/final.
            # We shouldn't then go find a DIFFERENT game on a different date.
            if not event_result.get('found'):
                original_reason = event_result.get('reason')

                # Don't disambiguate if the stream's target game was found but filtered
                skip_disambiguation = original_reason in (
                    FilteredReason.EVENT_PAST,
                    FilteredReason.EVENT_FINAL,
                    'event_past', 'event_final'  # String values from FilterReason constants
                )

                if not skip_disambiguation:
                    disambig_result, team_result = self._try_team_disambiguation(
                        team_result, raw_team1, raw_team2, detected_league,
                        detected_api_path_override
                    )
                    if disambig_result.get('found'):
                        event_result = disambig_result
                    elif not disambig_result.get('reason') and original_reason:
                        # Preserve original reason if disambiguation returned no specific reason
                        event_result['reason'] = original_reason

            # Step 7b: Tier 4b+ fallback - if event still not found, try raw name schedule search
            # This handles cases where team IDs were found but WRONG (e.g., "Miami" matched
            # to Miami Hurricanes instead of Miami (OH) RedHawks). The raw opponent name
            # search can find the correct game even when team resolution was incorrect.
            if not event_result.get('found') and team_result:
                original_reason = event_result.get('reason')
                skip_schedule_search = original_reason in (
                    FilteredReason.EVENT_PAST,
                    FilteredReason.EVENT_FINAL,
                    'event_past', 'event_final'  # String values from FilterReason constants
                )

                if not skip_schedule_search:
                    logger.debug(
                        f"Event not found after disambiguation for '{stream_name[:40]}...', "
                        f"trying Tier 4b+ schedule search as fallback"
                    )
                    schedule_result = self._search_schedules_for_raw_opponent(
                        team_result, raw_team1, raw_team2,
                        detected_league, detected_api_path_override
                    )
                    if schedule_result:
                        fallback_event_result, fallback_team_result = schedule_result
                        if fallback_event_result.get('found'):
                            event_result = fallback_event_result
                            team_result = fallback_team_result
                            # Update detected_league from the successful search
                            detected_league = team_result.get('detected_league', detected_league)
                            logger.info(f"[TIER 4b+ FALLBACK] Found event via schedule search for '{stream_name[:40]}...'")
                        else:
                            # Tier 4b+ found an event but it was filtered - propagate team names for UI
                            event_result = fallback_event_result
                            team_result = fallback_team_result
                            detected_league = team_result.get('detected_league', detected_league)
                            logger.debug(f"[TIER 4b+ FALLBACK] Found filtered event for '{stream_name[:40]}...': {fallback_event_result.get('reason')}")

            if event_result.get('found'):
                # Match successful! Now check if league is enabled for this group
                team_result['detected_league'] = detected_league

                # Check if detected league is enabled
                # For professional soccer leagues, also check soccer_enabled flag (enables all pro soccer)
                # Note: NCAA soccer (usa.ncaa.m.1, usa.ncaa.w.1) is in league_config, not covered by soccer_enabled
                from epg.league_config import is_soccer_league
                league_enabled = self.league_detector.is_league_enabled(detected_league)
                if not league_enabled and self.config.soccer_enabled:
                    # soccer_enabled covers all pro soccer leagues (from soccer cache)
                    # but NOT NCAA soccer which is controlled separately via league_config
                    is_ncaa_soccer = 'ncaa' in detected_league.lower()
                    if is_soccer_league(detected_league) and not is_ncaa_soccer:
                        league_enabled = True

                if not league_enabled:
                    # League not enabled - this is a FILTERED result (not a failure)
                    league_name = self.league_detector.get_league_name(detected_league)
                    result.reason = FilteredReason.LEAGUE_NOT_ENABLED
                    result.detected_league = detected_league
                    result.league_not_enabled = True
                    result.league_name = league_name
                    result.parsed_teams = {'team1': raw_team1, 'team2': raw_team2}
                    result.detail = f"Found in {league_name} (not enabled for this group)"
                    _log_result(stream_name, 'filtered', result.reason,
                               tier=detection_tier, league=detected_league, detail=league_name)
                    return result

                # League is enabled - full success!
                result.matched = True
                result.team_result = team_result
                result.event = event_result['event']
                result.detected_league = detected_league
                result.detection_tier = detection_tier
                result.api_path_override = detected_api_path_override

                # Log the successful match
                event_name = ''
                if result.event:
                    event_name = result.event.get('name', result.event.get('shortName', ''))
                _log_result(stream_name, 'matched', tier=detection_tier,
                           league=detected_league, detail=event_name)
                return result
            else:
                # Event not found - include team info so UI knows teams WERE parsed
                result.reason = event_result.get('reason', FailedReason.NO_EVENT_FOUND)
                result.team_result = team_result  # Include team info for UI
                result.parsed_teams = {'team1': raw_team1, 'team2': raw_team2}
                result.detected_league = detected_league

                # Log based on reason category
                # EVENT_PAST/EVENT_FINAL means we DID match, just excluded (FILTERED)
                if result.reason in (FilteredReason.EVENT_PAST, FilteredReason.EVENT_FINAL,
                                    'event_past', 'event_final'):  # String values from FilterReason constants
                    _log_result(stream_name, 'filtered', result.reason,
                               tier=detection_tier, league=detected_league, detail='Event excluded')
                else:
                    # True failure - couldn't find the event
                    _log_result(stream_name, 'failed', result.reason,
                               league=detected_league, detail=f"league={detected_league}")
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
        """
        Tier 2: Try leagues within the indicated sport.

        Note: Searches ALL leagues for the sport, not just enabled ones.
        The enabled check happens after a successful match is made.
        """
        from epg.league_detector import get_sport_for_league, LEAGUE_TO_SPORT
        from database import get_soccer_slug_mapping

        if indicator_sport == 'soccer':
            # Soccer uses dedicated SoccerMultiLeague cache (240+ leagues)
            soccer_slugs = self.league_detector._find_soccer_leagues_for_teams(raw_team1, raw_team2)
            slug_to_code = get_soccer_slug_mapping()
            sport_leagues = [slug_to_code.get(slug) for slug in soccer_slugs[:10] if slug in slug_to_code]
        else:
            # Search ALL leagues for this sport, not just enabled ones
            sport_leagues = [l for l in LEAGUE_TO_SPORT.keys()
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
        """
        Tier 3: Use caches to find candidate leagues from team names.

        Note: Searches ALL leagues, not just enabled ones.
        The enabled check happens after a successful match is made.

        Returns:
            Tuple of (detected_league, team_result, api_override, detection_tier, found_event)
            - found_event: Event dict if found during disambiguation, None otherwise
        """
        # Find candidate leagues from TeamLeagueCache (non-soccer)
        # This now returns ALL leagues where both teams exist (no enabled filter)
        candidate_leagues = self.league_detector.find_candidate_leagues(
            raw_team1, raw_team2, include_soccer=False
        )
        # DO NOT filter by enabled - we search ALL leagues, check enabled after match

        # Collect all matched candidates for disambiguation
        matched_candidates = []

        # Also check soccer cache (no longer filtered by soccer_enabled)
        if True:  # Always check soccer, enabled check happens after match
            from database import get_soccer_slug_mapping
            soccer_candidates = self.league_detector.get_soccer_candidates_with_team_ids(raw_team1, raw_team2)
            for sc in soccer_candidates:
                candidate = {
                    'matched': True,
                    'away_team_id': sc['team1_id'],
                    'away_team_name': sc['team1_name'],
                    'home_team_id': sc['team2_id'],
                    'home_team_name': sc['team2_name'],
                    'game_date': game_date,
                    'game_time': game_time,
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
            # Single candidate - no event found yet, return None for found_event
            return detected_league, team_result, api_override, '3c', None
        elif len(matched_candidates) > 1:
            return self._disambiguate_candidates(
                matched_candidates, game_date, game_time
            )

        return None, None, None, None, None

    def _disambiguate_candidates(
        self, matched_candidates: List[tuple], game_date, game_time
    ) -> tuple:
        """
        Disambiguate between multiple candidate leagues by checking schedules.

        Returns:
            Tuple of (detected_league, team_result, api_override, detection_tier, found_event)
            - found_event: Event dict from find_event() if a game was found, None otherwise
              This allows the caller to skip a redundant find_and_enrich() call.
        """
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
                    from zoneinfo import ZoneInfo
                    from utils.time_format import get_user_timezone
                    from database import get_connection
                    try:
                        # Event time from ESPN is in UTC
                        event_dt = datetime.fromisoformat(
                            test_result['event_date'].replace('Z', '+00:00')
                        )
                        target_time = candidate['game_time']

                        # Get user's timezone from settings
                        user_tz_str = get_user_timezone(get_connection)
                        user_tz = ZoneInfo(user_tz_str)

                        # target_time may be a datetime or time object - extract time() if needed
                        # Build a full datetime using game_date or today, with user's timezone
                        target_date = candidate.get('game_date')
                        if target_date is None:
                            target_date = datetime.now(user_tz).date()
                        elif hasattr(target_date, 'date'):
                            target_date = target_date.date()

                        # Ensure we have a time object for combine()
                        if hasattr(target_time, 'time'):
                            # It's a datetime, extract the time component
                            target_time_obj = target_time.time()
                        else:
                            target_time_obj = target_time

                        # Create target datetime in user's timezone
                        target_dt = datetime.combine(
                            target_date, target_time_obj, tzinfo=user_tz
                        )

                        # Convert both to UTC for comparison
                        event_utc = event_dt.astimezone(ZoneInfo('UTC'))
                        target_utc = target_dt.astimezone(ZoneInfo('UTC'))

                        # Calculate difference in minutes
                        time_diff = abs((event_utc - target_utc).total_seconds() / 60)
                    except Exception as e:
                        logger.debug(f"Time comparison failed: {e}")
                # Store test_result (contains the event) along with other data
                leagues_with_games.append((league, candidate, api_path_override, test_result, time_diff))
            else:
                # Track if the game was found but is final/past
                reason = test_result.get('reason', '')
                if 'completed' in reason.lower() or 'past' in reason.lower() or 'final' in reason.lower():
                    leagues_with_final_games.append((league, candidate, api_path_override, test_result))

        detected_league = None
        team_result = None
        detected_api_path_override = None
        detection_tier = None
        found_event = None  # The event found during disambiguation

        if len(leagues_with_games) == 1:
            detected_league, team_result, detected_api_path_override, found_result, _ = leagues_with_games[0]
            found_event = found_result  # Preserve the full result including event
            detection_tier = '3c'
        elif len(leagues_with_games) > 1:
            # Multiple leagues have games - pick best time match
            leagues_with_games.sort(key=lambda x: x[4])  # Sort by time_diff (index 4)
            detected_league, team_result, detected_api_path_override, found_result, _ = leagues_with_games[0]
            found_event = found_result  # Preserve the full result including event

            # Determine tier based on what data was used
            if game_date and game_time:
                detection_tier = '3a'
            elif game_time:
                detection_tier = '3b'
            else:
                detection_tier = '3c'

            logger.debug(
                f"Multi-league disambiguation: {len(leagues_with_games)} leagues have games, "
                f"selected {detected_league} (time_diff={leagues_with_games[0][4]} mins)"
            )

        # If no active game found but we found final games, use that league
        if not detected_league and leagues_with_final_games:
            detected_league, team_result, detected_api_path_override, found_result = leagues_with_final_games[0]
            found_event = found_result
            detection_tier = '3c'
        # IMPORTANT: If no game found in ANY candidate league, do NOT set detected_league.
        # Setting a league without an event violates the principle that we only return
        # authoritative league/sport from matched events. Let downstream code (Tier 4,
        # schedule searches) continue trying other detection methods.
        # DO NOT fall back to first candidate - that would pick an arbitrary league
        # (e.g., soccer over volleyball) without evidence of a game.

        # For soccer leagues detected via cache, check if we need api_path_override
        if detected_league and not detected_api_path_override:
            for league_key, candidate, api_override in matched_candidates:
                if league_key == detected_league and api_override:
                    detected_api_path_override = api_override
                    break

        return detected_league, team_result, detected_api_path_override, detection_tier, found_event

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
        # Skip disambiguation for Tier 4 matches - they don't have team IDs
        # and we already have the exact event from schedule search
        if team_result.get('tier4_event_id'):
            return {'found': False}, team_result

        raw_away = team_result.get('raw_away', '') or raw_team1
        raw_home = team_result.get('raw_home', '') or raw_team2

        if not (raw_away and raw_home):
            return {'found': False}, team_result

        # Need team IDs for disambiguation
        if not team_result.get('away_team_id') or not team_result.get('home_team_id'):
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

        # Tier 4b+ fallback: Both teams matched but no game between them
        # Search each matched team's schedule for opponent name matching the RAW opponent string
        # This handles cases like "IU East" → IU Indianapolis (wrong), but Eastern Kentucky's
        # schedule contains "Indiana University East IU EAST at Eastern Kentucky Colonels"
        schedule_result = self._search_schedules_for_raw_opponent(
            team_result, raw_away, raw_home, detected_league, api_path_override
        )
        if schedule_result:
            return schedule_result

        return {'found': False}, team_result

    def _search_schedules_for_raw_opponent(
        self, team_result: Dict, raw_away: str, raw_home: str,
        detected_league: str, api_path_override: str
    ) -> Optional[tuple]:
        """
        Tier 4b+ fallback: Search each team's schedule across ALL their leagues.

        When both teams are matched to DB entries but no game exists between them,
        search EACH matched team's schedule across ALL leagues they appear in,
        looking for an event containing the RAW opponent string.

        This handles cases where:
        - "IU East" matched to "IU Indianapolis" (wrong - word overlap on "IU")
        - "Eastern Kentucky" matched correctly to women's college basketball
        - No game between these mismatched teams
        - But Eastern Kentucky's BASKETBALL schedule contains:
          "Indiana University East IU EAST at Eastern Kentucky Colonels"
        - We find it by checking ALL leagues Eastern Kentucky plays in.

        Args:
            team_result: Current team_result with matched team IDs
            raw_away: Raw away team string from stream name
            raw_home: Raw home team string from stream name
            detected_league: Originally detected league code (may not be correct)
            api_path_override: Optional API path override for soccer

        Returns:
            Tuple of (event_result, team_result) if found, None otherwise
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from epg.league_config import get_league_config, parse_api_path
        from epg.team_league_cache import TeamLeagueCache
        from database import get_connection

        # Normalize raw names for substring matching
        raw_away_lower = raw_away.lower().strip()
        raw_home_lower = raw_home.lower().strip()

        # Extract significant words (skip common words)
        common_words = {'college', 'university', 'state', 'city', 'the', 'of', 'at'}

        def get_primary_word(name: str) -> str:
            """Extract first significant word from team name."""
            words = [w for w in name.lower().split() if w not in common_words]
            return words[0] if words else name.lower().split()[0] if name else ''

        raw_away_primary = get_primary_word(raw_away)
        raw_home_primary = get_primary_word(raw_home)

        logger.debug(
            f"Tier 4b+ fallback: searching schedules across all leagues. "
            f"Raw: '{raw_away}' vs '{raw_home}'"
        )

        now = datetime.now(ZoneInfo('UTC'))
        cutoff_future = now + timedelta(days=14)

        # Collect all (team_id, league_code) pairs for each raw team name
        # This searches ALL leagues each team appears in
        conn = get_connection()
        cursor = conn.cursor()

        # Build list of (team_id, league, team_name, opponent_raw, opponent_primary)
        teams_to_search = []

        # For team1 (raw_away): search for events where opponent matches raw_home
        # Check both non-soccer and soccer caches
        for raw_name, opponent_raw, opponent_primary in [
            (raw_away, raw_home_lower, raw_home_primary),
            (raw_home, raw_away_lower, raw_away_primary)
        ]:
            # Search non-soccer cache (team_league_cache)
            cursor.execute("""
                SELECT espn_team_id, league_code, team_name
                FROM team_league_cache
                WHERE LOWER(team_name) LIKE ?
            """, (f'%{raw_name}%',))
            for row in cursor.fetchall():
                teams_to_search.append((
                    row[0], row[1], row[2], opponent_raw, opponent_primary
                ))

            # Also search soccer cache
            cursor.execute("""
                SELECT espn_team_id, league_slug, team_name
                FROM soccer_team_leagues
                WHERE LOWER(team_name) LIKE ?
            """, (f'%{raw_name}%',))
            for row in cursor.fetchall():
                teams_to_search.append((
                    row[0], row[1], row[2], opponent_raw, opponent_primary
                ))

        conn.close()

        if not teams_to_search:
            logger.debug("Tier 4b+ fallback: no team entries found in caches")
            return None

        logger.debug(f"Tier 4b+ fallback: checking {len(teams_to_search)} (team, league) pairs")

        candidates = []  # List of (event_id, event_dt, league_code, event_dict, via_team_name)

        for team_id, league_code, team_name, opponent_raw, opponent_primary in teams_to_search:
            try:
                # Get API path for this league
                config = get_league_config(league_code, get_connection)
                if not config:
                    continue

                sport, api_league = parse_api_path(config['api_path'])
                if not sport:
                    continue

                # Fetch schedule for this team in this league
                schedule = self.event_matcher.espn.get_team_schedule(
                    sport, api_league, str(team_id)
                )
                if not schedule or 'events' not in schedule:
                    continue

                for event in schedule.get('events', []):
                    event_name = event.get('name', '')
                    event_name_lower = event_name.lower()

                    # Check if RAW opponent name appears in event name
                    if opponent_raw not in event_name_lower and opponent_primary not in event_name_lower:
                        continue

                    # Parse event date
                    event_date_str = event.get('date', '')
                    if not event_date_str:
                        continue

                    try:
                        event_dt = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                    except Exception:
                        continue

                    # Check if within window
                    if event_dt < now - timedelta(days=1) or event_dt > cutoff_future:
                        continue

                    event_id = event.get('id')
                    logger.debug(
                        f"Tier 4b+ found: '{event_name}' in {team_name}'s {league_code} schedule "
                        f"(matched '{opponent_raw}' or '{opponent_primary}')"
                    )
                    candidates.append((event_id, event_dt, league_code, event, team_name))

            except Exception as e:
                logger.debug(f"Tier 4b+ error searching {team_name}'s {league_code} schedule: {e}")
                continue

        if not candidates:
            logger.debug("Tier 4b+ fallback: no matches found in any schedule")
            return None

        # Sort by time closest to now
        candidates.sort(key=lambda c: abs((c[1] - now).total_seconds()))
        best_event_id, best_event_dt, best_league, best_event, via_team = candidates[0]

        logger.info(
            f"Tier 4b+ match: '{best_event.get('name')}' in {best_league} "
            f"via {via_team}'s schedule (raw: '{raw_away}' vs '{raw_home}')"
        )

        # Fetch and enrich the event by ID using the correct league
        event = self.event_matcher.get_event_by_id(best_event_id, best_league)

        if event and event.get('id'):
            # Check if event is completed/past - apply same filtering as find_event()
            from utils.time_format import get_today_in_user_tz, get_user_timezone
            from database import get_connection

            # Get event status
            status = event.get('status', {})
            is_completed = status.get('completed', False) or 'FINAL' in status.get('name', '').upper()

            if is_completed:
                # Check if it's a past day or today's final
                event_date_str = event.get('date', '')
                if event_date_str:
                    try:
                        event_dt_check = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
                        user_tz_str = get_user_timezone(get_connection)
                        user_tz = ZoneInfo(user_tz_str)
                        event_in_user_tz = event_dt_check.astimezone(user_tz)
                        event_day = event_in_user_tz.date()
                        today = get_today_in_user_tz(get_connection)

                        if event_day < today:
                            # Past day completed event - always excluded
                            # Still populate team names for UI display
                            logger.debug(f"Tier 4b+ event {best_event_id} filtered: past completed game ({event_day})")
                            home_team = event.get('home_team', {})
                            away_team = event.get('away_team', {})
                            team_result['tier4b_plus'] = True
                            team_result['tier4b_plus_via'] = via_team
                            team_result['detected_league'] = best_league
                            team_result['home_team_name'] = home_team.get('name', raw_home or '?')
                            team_result['away_team_name'] = away_team.get('name', raw_away or '?')
                            team_result['home_team_id'] = home_team.get('id')
                            team_result['away_team_id'] = away_team.get('id')
                            event_result = {'found': False, 'reason': FilteredReason.EVENT_PAST}
                            return event_result, team_result
                        elif event_day == today and not self.config.include_final_events:
                            # Today's final - excluded by setting
                            # Still populate team names for UI display
                            logger.debug(f"Tier 4b+ event {best_event_id} filtered: today's final (excluded)")
                            home_team = event.get('home_team', {})
                            away_team = event.get('away_team', {})
                            team_result['tier4b_plus'] = True
                            team_result['tier4b_plus_via'] = via_team
                            team_result['detected_league'] = best_league
                            team_result['home_team_name'] = home_team.get('name', raw_home or '?')
                            team_result['away_team_name'] = away_team.get('name', raw_away or '?')
                            team_result['home_team_id'] = home_team.get('id')
                            team_result['away_team_id'] = away_team.get('id')
                            event_result = {'found': False, 'reason': FilteredReason.EVENT_FINAL}
                            return event_result, team_result
                    except Exception as e:
                        logger.debug(f"Error checking Tier 4b+ event date: {e}")
                        # On error, allow the event through

            # Update team_result with Tier 4b+ info
            team_result['tier4b_plus'] = True
            team_result['tier4b_plus_via'] = via_team
            team_result['tier4b_plus_event_id'] = best_event_id
            team_result['detected_league'] = best_league
            # Populate team names from event for UI display
            home_team = event.get('home_team', {})
            away_team = event.get('away_team', {})
            team_result['home_team_name'] = home_team.get('name', raw_home or '?')
            team_result['away_team_name'] = away_team.get('name', raw_away or '?')
            team_result['home_team_id'] = home_team.get('id')
            team_result['away_team_id'] = away_team.get('id')

            event_result = {'found': True, 'event': event, 'event_id': event.get('id')}
            return event_result, team_result

        logger.debug(f"Tier 4b+ fallback: event {best_event_id} not found via get_event_by_id")
        return None