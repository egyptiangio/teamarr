"""
Shared stream matching logic for both EPG generation and test modal.

This module provides a single source of truth for stream-to-ESPN-event matching,
ensuring consistent behavior between the test/preview modal and actual EPG generation.

Usage:
    from epg.stream_matcher import match_stream_single_league, MatchConfig, MatchResult

    config = MatchConfig(
        league='mens-college-basketball',
        lookahead_days=3,
        include_final_events=False,
        custom_regex_teams=None,
        # ... etc
    )
    result = match_stream_single_league(stream, config)
    if result.matched:
        # Use result.event, result.team_result, etc.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class MatchConfig:
    """Configuration for stream matching."""
    league: str
    lookahead_days: int = 3
    include_final_events: bool = False

    # Custom regex settings
    custom_regex_teams: Optional[str] = None
    custom_regex_teams_enabled: bool = False
    custom_regex_date: Optional[str] = None
    custom_regex_date_enabled: bool = False
    custom_regex_time: Optional[str] = None
    custom_regex_time_enabled: bool = False

    @property
    def any_custom_enabled(self) -> bool:
        return (self.custom_regex_teams_enabled or
                self.custom_regex_date_enabled or
                self.custom_regex_time_enabled)


@dataclass
class MatchResult:
    """Result of stream matching - unified format for EPG gen and test modal."""
    # Outcome
    matched: bool = False
    filtered: bool = False
    error: bool = False

    # Stream info
    stream: Optional[Dict] = None

    # Match details (when matched=True)
    event: Optional[Dict] = None
    team_result: Optional[Dict] = None
    detected_league: Optional[str] = None
    detection_tier: Optional[str] = None  # 'direct', 'name_match', 'cache', etc.

    # Failure details (when matched=False)
    reason: Optional[str] = None
    filter_reason: Optional[str] = None
    parsed_teams: Optional[Dict] = None

    # Error details
    error_message: Optional[str] = None

    # Metadata
    exception_keyword: Optional[str] = None
    from_cache: bool = False


def match_stream_single_league(
    stream: Dict,
    config: MatchConfig,
    team_matcher,
    event_matcher,
    stream_cache=None,
    internal_group_id: int = None,
    current_generation: int = None
) -> MatchResult:
    """
    Match a single stream to an ESPN event in the assigned league.

    This is the single source of truth for single-league stream matching,
    used by both EPG generation and the test/preview modal.

    Args:
        stream: Stream dict with 'name' and 'id' keys
        config: MatchConfig with league and regex settings
        team_matcher: TeamMatcher instance
        event_matcher: EventMatcher instance
        stream_cache: Optional fingerprint cache
        internal_group_id: Group ID for cache keys
        current_generation: Generation number for cache

    Returns:
        MatchResult with match outcome and details
    """
    stream_name = stream.get('name', '')
    stream_id = stream.get('id', 0)

    result = MatchResult(stream=stream)

    try:
        # Check fingerprint cache first (if provided)
        if stream_cache and internal_group_id is not None:
            cached = stream_cache.get(internal_group_id, stream_id, stream_name)
            if cached:
                event_id, cached_league, cached_data = cached
                # Import here to avoid circular imports
                from epg.stream_match_cache import refresh_cached_event
                from database import get_connection

                refreshed = refresh_cached_event(
                    event_matcher.espn,
                    cached_data,
                    cached_league,
                    get_connection
                )
                if refreshed:
                    # Touch cache to update last_seen_generation
                    if current_generation is not None:
                        stream_cache.touch(internal_group_id, stream_id, stream_name, current_generation)

                    result.matched = True
                    result.event = refreshed.get('event')
                    result.team_result = refreshed.get('team_result', {})
                    result.detected_league = cached_league
                    result.detection_tier = 'cache'
                    result.from_cache = True
                    return result

        # Extract teams using appropriate method
        if config.any_custom_enabled:
            team_result = team_matcher.extract_teams_with_selective_regex(
                stream_name,
                config.league,
                teams_pattern=config.custom_regex_teams,
                teams_enabled=config.custom_regex_teams_enabled,
                date_pattern=config.custom_regex_date,
                date_enabled=config.custom_regex_date_enabled,
                time_pattern=config.custom_regex_time,
                time_enabled=config.custom_regex_time_enabled
            )
        else:
            team_result = team_matcher.extract_teams(stream_name, config.league)

        # If teams matched to ESPN IDs, find event
        if team_result.get('matched'):
            event_result = event_matcher.find_and_enrich(
                team_result['away_team_id'],
                team_result['home_team_id'],
                config.league,
                game_date=team_result.get('game_date'),
                game_time=team_result.get('game_time'),
                include_final_events=config.include_final_events
            )

            if event_result.get('found'):
                result.matched = True
                result.event = event_result['event']
                result.team_result = team_result
                result.detected_league = config.league
                result.detection_tier = 'direct'
                result.exception_keyword = stream.get('exception_keyword')

                # Cache successful match
                _cache_match(stream_cache, internal_group_id, stream_id, stream_name,
                            event_result['event'], team_result, config.league, current_generation)
                return result

            # No game found - try alternate team combinations (disambiguation)
            raw_away = team_result.get('raw_away', '')
            raw_home = team_result.get('raw_home', '')

            if raw_away and raw_home:
                disambiguated = _try_disambiguation(
                    team_matcher, event_matcher, team_result,
                    raw_away, raw_home, config
                )
                if disambiguated:
                    result.matched = True
                    result.event = disambiguated['event']
                    result.team_result = disambiguated['team_result']
                    result.detected_league = config.league
                    result.detection_tier = 'direct'
                    result.exception_keyword = stream.get('exception_keyword')

                    # Cache successful match
                    _cache_match(stream_cache, internal_group_id, stream_id, stream_name,
                                disambiguated['event'], disambiguated['team_result'],
                                config.league, current_generation)
                    return result

            # No match found with any combination
            result.filtered = True
            reason = event_result.get('reason', '')
            # Normalize internal reasons
            INTERNAL_REASONS = {
                'game_past': 'event_past',
                'game_final': 'event_final',
                'no_game_found': 'no_event_found',
            }
            result.reason = INTERNAL_REASONS.get(reason, reason)
            return result

        else:
            # Teams could not be matched to ESPN by ID
            # Try name-based scoreboard search as fallback (for small colleges)
            raw_away = team_result.get('raw_away')
            raw_home = team_result.get('raw_home')

            if raw_away and raw_home:
                name_result = event_matcher.find_and_enrich_by_names(
                    raw_away,
                    raw_home,
                    config.league,
                    game_date=team_result.get('game_date'),
                    game_time=team_result.get('game_time'),
                    include_final_events=config.include_final_events
                )

                if name_result.get('found'):
                    # Success via name-based matching!
                    synthetic_team_result = _build_synthetic_team_result(
                        name_result['event'], raw_away, raw_home
                    )

                    logger.debug(
                        f"Name-based match: '{raw_away}' vs '{raw_home}' → "
                        f"'{synthetic_team_result['away_team_name']}' vs '{synthetic_team_result['home_team_name']}'"
                    )

                    result.matched = True
                    result.event = name_result['event']
                    result.team_result = synthetic_team_result
                    result.detected_league = config.league
                    result.detection_tier = 'name_match'
                    result.exception_keyword = stream.get('exception_keyword')

                    # Cache successful match
                    _cache_match(stream_cache, internal_group_id, stream_id, stream_name,
                                name_result['event'], synthetic_team_result,
                                config.league, current_generation)
                    return result

            # Name-based search also failed
            result.reason = team_result.get('reason')
            result.parsed_teams = {
                'team1': raw_away,
                'team2': raw_home
            }
            return result

    except Exception as e:
        logger.warning(f"Error matching stream '{stream_name}': {e}")
        result.error = True
        result.error_message = str(e)
        return result


def _try_disambiguation(team_matcher, event_matcher, team_result, raw_away, raw_home, config):
    """Try alternate team combinations when primary match fails."""
    league = config.league

    all_away_teams = team_matcher.get_all_matching_teams(raw_away, league, max_results=5)
    all_home_teams = team_matcher.get_all_matching_teams(raw_home, league, max_results=5)

    tried_pairs = {(team_result['away_team_id'], team_result['home_team_id'])}

    for away_candidate in all_away_teams:
        for home_candidate in all_home_teams:
            pair = (away_candidate['id'], home_candidate['id'])
            if pair in tried_pairs:
                continue
            tried_pairs.add(pair)

            alt_result = event_matcher.find_and_enrich(
                away_candidate['id'],
                home_candidate['id'],
                league,
                game_date=team_result.get('game_date'),
                game_time=team_result.get('game_time'),
                include_final_events=config.include_final_events
            )

            if alt_result.get('found'):
                # Found a match with alternate teams
                alt_team_result = team_result.copy()
                alt_team_result['away_team_id'] = away_candidate['id']
                alt_team_result['away_team_name'] = away_candidate['name']
                alt_team_result['away_team_abbrev'] = away_candidate.get('abbrev', '')
                alt_team_result['home_team_id'] = home_candidate['id']
                alt_team_result['home_team_name'] = home_candidate['name']
                alt_team_result['home_team_abbrev'] = home_candidate.get('abbrev', '')
                alt_team_result['disambiguated'] = True

                logger.debug(
                    f"Team disambiguation: '{raw_away}' vs '{raw_home}' → "
                    f"'{away_candidate['name']}' vs '{home_candidate['name']}'"
                )

                return {
                    'event': alt_result['event'],
                    'team_result': alt_team_result
                }

    return None


def _build_synthetic_team_result(event, raw_away, raw_home):
    """Build team_result dict from event data for name-based matches."""
    competitions = event.get('competitions', [{}])
    competitors = competitions[0].get('competitors', []) if competitions else []

    away_team = next((c for c in competitors if c.get('homeAway') == 'away'), {})
    home_team = next((c for c in competitors if c.get('homeAway') == 'home'), {})

    return {
        'matched': True,
        'away_team_id': away_team.get('team', {}).get('id'),
        'away_team_name': away_team.get('team', {}).get('displayName', raw_away),
        'away_team_abbrev': away_team.get('team', {}).get('abbreviation', ''),
        'home_team_id': home_team.get('team', {}).get('id'),
        'home_team_name': home_team.get('team', {}).get('displayName', raw_home),
        'home_team_abbrev': home_team.get('team', {}).get('abbreviation', ''),
        'raw_away': raw_away,
        'raw_home': raw_home,
        'name_matched': True
    }


def _cache_match(stream_cache, group_id, stream_id, stream_name, event, team_result, league, generation):
    """Cache a successful match for future EPG runs."""
    if not stream_cache or group_id is None:
        return

    event_id = event.get('id') if event else None
    if not event_id:
        return

    cached_data = {
        'event': event,
        'team_result': team_result
    }
    stream_cache.set(group_id, stream_id, stream_name, event_id, league, cached_data, generation)
