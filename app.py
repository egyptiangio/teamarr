"""
Teamarr - Dynamic EPG Generator for Sports Channels
Flask web application for managing templates and teams
"""

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, Response, stream_with_context
import os
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    init_database, get_connection, run_migrations,
    get_all_templates, get_template, create_template, update_template, delete_template, get_template_team_count,
    get_all_teams, get_team, create_team, update_team, delete_team,
    bulk_assign_template, bulk_delete_teams, bulk_set_active,
    get_active_teams_with_templates,
    # Event EPG functions
    get_all_aliases, get_aliases_for_league, get_alias, create_alias, update_alias, delete_alias,
    get_all_event_epg_groups, get_event_epg_group, get_event_epg_group_by_dispatcharr_id,
    create_event_epg_group, update_event_epg_group, delete_event_epg_group,
    update_event_epg_group_stats, update_event_epg_group_last_refresh
)
from api.espn_client import ESPNClient
from epg.orchestrator import EPGOrchestrator
from epg.xmltv_generator import XMLTVGenerator
from utils.logger import setup_logging, get_logger
from utils import to_pascal_case
from utils.time_format import format_time as fmt_time, get_time_settings
from utils.filter_reasons import FilterReason, get_display_text, INTERNAL_REASONS
from utils.match_result import (
    FilteredReason, FailedReason, MatchedTier,
    should_record_failure, normalize_reason
)
from config import VERSION

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Global generation status for polling-based progress notifications
# This allows progress to persist across page navigation
generation_status = {
    'in_progress': False,
    'status': 'idle',
    'message': '',
    'percent': 0,
    'extra': {}  # For team_name, group_name, current, total, etc.
}

# Setup logging system
log_level = os.environ.get('LOG_LEVEL', 'DEBUG').upper()
setup_logging(app, log_level)


# Custom Jinja filter for relative time display
@app.template_filter('relative_time')
def relative_time_filter(value):
    """Convert datetime string to relative time (e.g., '5 mins ago')"""
    if not value:
        return 'Never'

    from datetime import datetime

    try:
        if isinstance(value, str):
            # Try parsing ISO format
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            dt = value

        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        diff = now - dt

        seconds = diff.total_seconds()
        if seconds < 0:
            return 'Just now'
        if seconds < 60:
            return 'Just now'
        if seconds < 3600:
            mins = int(seconds / 60)
            return f'{mins} min{"s" if mins != 1 else ""} ago'
        if seconds < 86400:
            hours = int(seconds / 3600)
            return f'{hours} hour{"s" if hours != 1 else ""} ago'
        days = int(seconds / 86400)
        return f'{days} day{"s" if days != 1 else ""} ago'
    except Exception:
        return str(value)

# Initialize database on startup
if not os.path.exists(os.path.join(os.path.dirname(__file__), 'teamarr.db')):
    app.logger.info("🔧 Initializing database...")
    init_database()
    app.logger.info("✅ Database initialized")
else:
    # Run migrations on existing database
    conn = get_connection()
    try:
        migrations = run_migrations(conn)
        if migrations > 0:
            app.logger.info(f"✅ Applied {migrations} database migration(s)")
    finally:
        conn.close()


def populate_missing_channel_group_names():
    """
    One-time data fix: populate channel_group_name for existing event groups
    by querying Dispatcharr API. Runs on startup if any groups have
    channel_group_id but no channel_group_name.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Find groups with channel_group_id but no channel_group_name
        groups_needing_names = cursor.execute("""
            SELECT id, channel_group_id
            FROM event_epg_groups
            WHERE channel_group_id IS NOT NULL
              AND (channel_group_name IS NULL OR channel_group_name = '')
        """).fetchall()

        if not groups_needing_names:
            return 0

        # Get Dispatcharr settings
        settings = cursor.execute("""
            SELECT dispatcharr_enabled, dispatcharr_url, dispatcharr_username, dispatcharr_password
            FROM settings WHERE id = 1
        """).fetchone()

        if not settings or not settings['dispatcharr_enabled']:
            return 0

        # Initialize Dispatcharr client
        from api.dispatcharr_client import ChannelManager
        try:
            channel_manager = ChannelManager(
                url=settings['dispatcharr_url'],
                username=settings['dispatcharr_username'],
                password=settings['dispatcharr_password']
            )
            channel_groups = channel_manager.get_channel_groups(exclude_m3u=True)
        except Exception as e:
            app.logger.warning(f"Could not fetch channel groups from Dispatcharr: {e}")
            return 0

        # Build lookup by ID
        group_name_map = {g['id']: g['name'] for g in channel_groups}

        # Update each group
        updated = 0
        for row in groups_needing_names:
            group_id = row['id']
            channel_group_id = row['channel_group_id']
            name = group_name_map.get(channel_group_id)
            if name:
                cursor.execute(
                    "UPDATE event_epg_groups SET channel_group_name = ? WHERE id = ?",
                    (name, group_id)
                )
                updated += 1

        conn.commit()
        return updated
    except Exception as e:
        app.logger.warning(f"Error populating channel group names: {e}")
        return 0
    finally:
        conn.close()


# Populate missing channel_group_names from Dispatcharr
try:
    populated = populate_missing_channel_group_names()
    if populated > 0:
        app.logger.info(f"✅ Populated {populated} missing channel group name(s) from Dispatcharr")
except Exception as e:
    app.logger.warning(f"Could not populate channel group names: {e}")

# Initialize EPG components
epg_orchestrator = EPGOrchestrator()
xmltv_generator = XMLTVGenerator(
    generator_name="Teamarr - Dynamic EPG Generator for Sports Channels",
    generator_url="http://localhost:9195",
    version=VERSION
)

# Scheduler thread
scheduler_thread = None
scheduler_running = False
last_run_time = None

# =============================================================================
# CONTEXT PROCESSORS
# =============================================================================

@app.context_processor
def inject_globals():
    """Make version and settings available to all templates"""
    # Get settings for time format display
    try:
        conn = get_connection()
        settings_row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        conn.close()
        settings = dict(settings_row) if settings_row else {}
    except Exception:
        settings = {}
    return dict(version=VERSION, settings=settings)

# =============================================================================
# SCHEDULER FUNCTIONS
# =============================================================================

# =============================================================================
# CORE EPG GENERATION FUNCTIONS
# =============================================================================

def refresh_event_group_core(group, m3u_manager, skip_m3u_refresh=False, epg_start_datetime=None, progress_callback=None, generation=None):
    """
    Core function to refresh a single event EPG group.

    This is the shared logic used by both manual API refresh and scheduled generation.
    By default waits for M3U refresh to complete before fetching streams.

    Args:
        group: The event EPG group dict from database
        m3u_manager: M3UAccountManager instance
        skip_m3u_refresh: If True, skip M3U refresh (use when already refreshed in batch)
        epg_start_datetime: Optional datetime for EPG start (for multi-day filler)
        progress_callback: Optional callable(processed, total, group_name) for stream progress
        generation: EPG generation counter for fingerprint cache (None = no caching)

    Returns:
        dict with keys: success, stream_count, matched_count, matched_streams,
                       epg_result, channel_results, error
    """
    from epg.team_matcher import create_matcher
    from epg.event_matcher import create_event_matcher
    from epg.event_epg_generator import generate_event_epg
    from epg.epg_consolidator import get_data_dir, after_event_epg_generation
    from database import get_template, update_event_epg_group_stats, save_failed_matches_batch, save_matched_streams_batch
    from utils.stream_filter import filter_game_streams
    from epg.stream_match_cache import StreamMatchCache, refresh_cached_event

    group_id = group['id']
    group_name = group.get('group_name', f'Group {group_id}')

    # Initialize fingerprint cache if generation provided
    stream_cache = StreamMatchCache(get_connection) if generation is not None else None
    cache_stats = {'hits': 0, 'misses': 0, 'stored': 0}

    # Collect matches for debugging
    failed_matches = []
    successful_matches = []

    # Fetch settings early for use throughout the function
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    include_final_events = bool(settings.get('include_final_events', 0))
    lookahead_days = settings.get('event_lookahead_days', 7)

    try:
        # Step 1: Refresh M3U data and wait for completion (unless already done in batch)
        if not skip_m3u_refresh:
            app.logger.debug(f"Refreshing M3U account {group['dispatcharr_account_id']} for event EPG group {group_id}")

            refresh_result = m3u_manager.wait_for_refresh(group['dispatcharr_account_id'], timeout=180)
            if not refresh_result.get('success'):
                return {
                    'success': False,
                    'error': f"M3U refresh failed: {refresh_result.get('message')}",
                    'step': 'refresh'
                }
        else:
            app.logger.debug(f"Skipping M3U refresh for group {group_id} (already refreshed in batch)")

        # Step 2: Fetch streams
        all_streams = m3u_manager.list_streams(group_name=group['group_name'])
        total_stream_count = len(all_streams)
        app.logger.debug(f"Fetched {total_stream_count} streams for group '{group['group_name']}'")

        # Step 2.5: Filter to game streams only (unless skip_builtin_filter is enabled)
        skip_builtin_filter = bool(group.get('skip_builtin_filter', 0))

        # Track granular filtering stats
        filtered_no_indicator = 0
        filtered_include_regex = 0
        filtered_exclude_regex = 0

        if skip_builtin_filter:
            # Skip built-in game indicator filter - user is using custom regex or wants all streams
            streams = all_streams
            app.logger.debug(f"Skipping built-in filter (skip_builtin_filter enabled)")
        else:
            # Apply built-in filter (must have vs/@/at indicator)
            from utils.regex_helper import get_group_filter_patterns
            include_regex, exclude_regex = get_group_filter_patterns(group)

            filter_result = filter_game_streams(
                all_streams,
                include_regex=include_regex,
                exclude_regex=exclude_regex
            )
            streams = filter_result['game_streams']
            filtered_no_indicator = filter_result['filtered_no_indicator']
            filtered_include_regex = filter_result['filtered_include_regex']
            filtered_exclude_regex = filter_result['filtered_exclude_regex']
            filtered_count = filtered_no_indicator + filtered_include_regex + filtered_exclude_regex

            if filtered_count > 0:
                app.logger.debug(f"Filtered {filtered_count} non-game streams ({filtered_no_indicator} no indicator, {filtered_include_regex} include regex, {filtered_exclude_regex} exclude regex), {len(streams)} game streams remain")

        # Step 2.6: Pre-extract exception keywords for all streams (once, before matching)
        # This avoids re-extracting in each matching code path (single-league, multi-sport, cache hit)
        from utils.keyword_matcher import strip_exception_keywords
        for stream in streams:
            _, keyword = strip_exception_keywords(stream.get('name', ''))
            stream['exception_keyword'] = keyword

        # Step 3: Match streams to ESPN events (PARALLEL for speed)
        from concurrent.futures import ThreadPoolExecutor

        # Check if any individual custom regex fields are enabled
        teams_enabled = bool(group.get('custom_regex_teams_enabled'))
        date_enabled = bool(group.get('custom_regex_date_enabled'))
        time_enabled = bool(group.get('custom_regex_time_enabled'))
        any_custom_enabled = teams_enabled or date_enabled or time_enabled

        # Check for multi-sport mode
        is_multi_sport = bool(group.get('is_multi_sport', 0))
        enabled_leagues = None
        soccer_enabled = False
        if is_multi_sport:
            # Parse enabled_leagues JSON if present
            import json
            enabled_leagues_json = group.get('enabled_leagues', '[]')
            try:
                raw_leagues = json.loads(enabled_leagues_json) if enabled_leagues_json else []
            except (json.JSONDecodeError, TypeError):
                raw_leagues = []

            # Handle soccer_all marker - means include all soccer leagues from cache
            if 'soccer_all' in raw_leagues:
                soccer_enabled = True
                enabled_leagues = [l for l in raw_leagues if l != 'soccer_all']
            else:
                enabled_leagues = raw_leagues

            # Normalize aliases to ESPN slugs (e.g., 'ncaawh' -> 'womens-college-hockey')
            from database import normalize_league_codes
            enabled_leagues = normalize_league_codes(enabled_leagues)

            if enabled_leagues or soccer_enabled:
                app.logger.debug(f"Multi-sport mode enabled with leagues: {enabled_leagues}, soccer_enabled: {soccer_enabled}")
            else:
                app.logger.warning(f"Multi-sport mode enabled but no leagues configured")
                is_multi_sport = False  # Fall back to single-league mode

        def match_single_stream_single_league(stream):
            """Match a single stream to ESPN event in assigned league - called in parallel"""
            # Create matchers per-thread for thread safety
            thread_team_matcher = create_matcher()
            thread_event_matcher = create_event_matcher(lookahead_days=lookahead_days)

            # Exception keyword was pre-extracted in Step 2.6 and attached to stream dict
            # Team matcher's _prepare_text_for_parsing() handles stripping for matching

            try:
                # Use selective regex if any individual field is enabled
                if any_custom_enabled:
                    team_result = thread_team_matcher.extract_teams_with_selective_regex(
                        stream['name'],
                        group['assigned_league'],
                        teams_pattern=group.get('custom_regex_teams'),
                        teams_enabled=teams_enabled,
                        date_pattern=group.get('custom_regex_date'),
                        date_enabled=date_enabled,
                        time_pattern=group.get('custom_regex_time'),
                        time_enabled=time_enabled
                    )
                else:
                    team_result = thread_team_matcher.extract_teams(stream['name'], group['assigned_league'])

                if team_result.get('matched'):
                    event_result = thread_event_matcher.find_and_enrich(
                        team_result['away_team_id'],
                        team_result['home_team_id'],
                        group['assigned_league'],
                        game_date=team_result.get('game_date'),
                        game_time=team_result.get('game_time'),
                        include_final_events=include_final_events
                    )

                    if event_result.get('found'):
                        return {
                            'type': 'matched',
                            'stream': stream,
                            'teams': team_result,
                            'event': event_result['event'],
                            'detected_league': group['assigned_league'],
                            'detection_tier': 'direct',
                            'exception_keyword': stream.get('exception_keyword')
                        }
                    else:
                        # No game found with primary team matches - try alternate team combinations
                        # This handles ambiguous names like "Maryland" matching multiple teams
                        raw_away = team_result.get('raw_away', '')
                        raw_home = team_result.get('raw_home', '')
                        league = group['assigned_league']

                        if raw_away and raw_home:
                            # Get all teams matching each raw name
                            all_away_teams = thread_team_matcher.get_all_matching_teams(raw_away, league, max_results=5)
                            all_home_teams = thread_team_matcher.get_all_matching_teams(raw_home, league, max_results=5)

                            # Try all combinations (skip the first one - already tried)
                            tried_pairs = {(team_result['away_team_id'], team_result['home_team_id'])}

                            for away_candidate in all_away_teams:
                                for home_candidate in all_home_teams:
                                    pair = (away_candidate['id'], home_candidate['id'])
                                    if pair in tried_pairs:
                                        continue
                                    tried_pairs.add(pair)

                                    alt_result = thread_event_matcher.find_and_enrich(
                                        away_candidate['id'],
                                        home_candidate['id'],
                                        league,
                                        game_date=team_result.get('game_date'),
                                        game_time=team_result.get('game_time'),
                                        include_final_events=include_final_events
                                    )

                                    if alt_result.get('found'):
                                        # Found a match with alternate teams - update team_result
                                        alt_team_result = team_result.copy()
                                        alt_team_result['away_team_id'] = away_candidate['id']
                                        alt_team_result['away_team_name'] = away_candidate['name']
                                        alt_team_result['away_team_abbrev'] = away_candidate.get('abbrev', '')
                                        alt_team_result['home_team_id'] = home_candidate['id']
                                        alt_team_result['home_team_name'] = home_candidate['name']
                                        alt_team_result['home_team_abbrev'] = home_candidate.get('abbrev', '')
                                        alt_team_result['disambiguated'] = True  # Flag for debugging

                                        app.logger.debug(
                                            f"Team disambiguation: '{raw_away}' vs '{raw_home}' → "
                                            f"'{away_candidate['name']}' vs '{home_candidate['name']}'"
                                        )

                                        return {
                                            'type': 'matched',
                                            'stream': stream,
                                            'teams': alt_team_result,
                                            'event': alt_result['event'],
                                            'detected_league': league,
                                            'detection_tier': 'direct',
                                            'exception_keyword': stream.get('exception_keyword')
                                        }

                        # No match found with any combination
                        reason = event_result.get('reason', '')
                        normalized = INTERNAL_REASONS.get(reason, reason)
                        return {'type': 'filtered', 'reason': normalized, 'stream': stream}
                else:
                    return {'type': 'no_teams', 'stream': stream}

            except Exception as e:
                app.logger.warning(f"Error matching stream '{stream['name']}': {e}")
                return {'type': 'error', 'stream': stream, 'error': str(e)}

        def match_single_stream_multi_sport(stream):
            """Match a single stream using multi-sport league detection - called in parallel.

            Uses consolidated MultiSportMatcher with tiered detection:
              Tier 1: League indicator + Teams → Direct match
              Tier 2: Sport indicator + Teams → Match within sport's leagues
              Tier 3a-c: Cache lookup + schedule disambiguation
              Tier 4a-b: Single-team schedule fallback (NAIA vs NCAA)
            """
            from epg.league_detector import LeagueDetector
            from epg.multi_sport_matcher import MultiSportMatcher, MatcherConfig
            from database import find_any_channel_for_event

            # Create per-thread instances
            thread_team_matcher = create_matcher()
            thread_event_matcher = create_event_matcher(lookahead_days=lookahead_days)
            thread_league_detector = LeagueDetector(
                espn_client=thread_event_matcher.espn,
                enabled_leagues=enabled_leagues,
                lookahead_days=lookahead_days
            )

            # Configure the matcher
            config = MatcherConfig(
                enabled_leagues=enabled_leagues,
                soccer_enabled=soccer_enabled,
                custom_regex_teams=group.get('custom_regex_teams'),
                custom_regex_teams_enabled=teams_enabled,
                custom_regex_date=group.get('custom_regex_date'),
                custom_regex_date_enabled=date_enabled,
                custom_regex_time=group.get('custom_regex_time'),
                custom_regex_time_enabled=time_enabled,
                include_final_events=include_final_events
            )

            matcher = MultiSportMatcher(
                team_matcher=thread_team_matcher,
                event_matcher=thread_event_matcher,
                league_detector=thread_league_detector,
                config=config
            )

            # Run the consolidated matching logic
            result = matcher.match_stream(stream)

            # Handle the result
            if result.error:
                return {'type': 'error', 'stream': stream, 'error': result.error_message}

            if not result.matched:
                # Determine result type based on reason
                if result.league_not_enabled:
                    # Found in a non-enabled league - log and exclude from match rate
                    app.logger.debug(
                        f"Stream '{stream.get('name', '')[:50]}' found in non-enabled league: {result.league_name}"
                    )
                    return {
                        'type': 'filtered',
                        'reason': FilterReason.LEAGUE_NOT_ENABLED,
                        'stream': stream,
                        'league_name': result.league_name
                    }
                elif result.reason == 'NO_LEAGUE_DETECTED':
                    return {'type': 'filtered', 'reason': 'NO_LEAGUE_DETECTED', 'stream': stream}
                elif result.reason in (FilterReason.UNSUPPORTED_BEACH_SOCCER, FilterReason.UNSUPPORTED_BOXING_MMA, FilterReason.UNSUPPORTED_FUTSAL):
                    # Unsupported sport - return as filtered so it's excluded from match rate
                    return {'type': 'filtered', 'reason': result.reason, 'stream': stream}
                elif result.parsed_teams:
                    # Teams were parsed but no event found - check if reason is excludable
                    # Use normalize_reason() to handle both enum and string values
                    reason_str = normalize_reason(result.reason)
                    if reason_str in ('event_past', 'event_final', 'league_not_enabled'):
                        # Return as filtered so it's excluded from match rate denominator
                        # Note: NO_GAME_FOUND is NOT excluded - it counts against match rate
                        return {'type': 'filtered', 'reason': result.reason, 'stream': stream}
                    else:
                        # True no_teams case - teams parsed but couldn't match to ESPN
                        # This includes NO_GAME_FOUND which counts against match rate
                        return {
                            'type': 'no_teams',
                            'stream': stream,
                            'reason': result.reason,
                            'detail': result.detail,
                            'parsed_teams': result.parsed_teams
                        }
                else:
                    # Event not found - normalize reason
                    normalized = INTERNAL_REASONS.get(result.reason, result.reason)
                    return {'type': 'filtered', 'reason': normalized, 'stream': stream}

            # Matched! Now check overlap handling (not in matcher - EPG builder specific)
            # This handles streams that match events already owned by OTHER groups
            # Only applies to multi-sport groups - single-league groups handle duplicates
            # within their own group via duplicate_event_handling
            event = result.event
            event_id = event.get('id')
            overlap_handling = group.get('overlap_handling', 'add_stream')

            # Check for existing channel in other groups (multi-sport only, except create_all mode)
            if is_multi_sport and event_id and overlap_handling in ('add_stream', 'add_only', 'skip'):
                # First try to find channel matching stream's exception keyword
                stream_keyword = result.exception_keyword
                existing_channel = None
                if stream_keyword:
                    existing_channel = find_any_channel_for_event(
                        event_id,
                        exception_keyword=stream_keyword,
                        exclude_group_id=group_id
                    )
                # If no keyword or no match, find any channel for the event
                if not existing_channel:
                    existing_channel = find_any_channel_for_event(event_id, exclude_group_id=group_id, any_keyword=True)
                if existing_channel:
                    if overlap_handling == 'skip':
                        # Skip - don't add stream to existing channel
                        return {
                            'type': 'filtered',
                            'reason': 'EVENT_OWNED_BY_OTHER_GROUP',
                            'stream': stream,
                            'existing_channel': existing_channel
                        }
                    else:
                        # add_stream or add_only - return as matched but flag for adding to existing channel
                        return {
                            'type': 'matched',
                            'stream': stream,
                            'teams': result.team_result,
                            'event': event,
                            'detected_league': result.detected_league,
                            'detection_tier': result.detection_tier,
                            'exception_keyword': result.exception_keyword,
                            'existing_channel': existing_channel  # Flag to add to this channel
                        }
                elif overlap_handling == 'add_only':
                    # add_only but no existing channel - skip this stream (no new channel creation)
                    return {
                        'type': 'filtered',
                        'reason': 'NO_EXISTING_CHANNEL',
                        'stream': stream
                    }

            # No existing channel (and not add_only) or create_all mode - return as normal match
            return {
                'type': 'matched',
                'stream': stream,
                'teams': result.team_result,
                'event': event,
                'detected_league': result.detected_league,
                'detection_tier': result.detection_tier,
                'exception_keyword': result.exception_keyword
            }

        # Select the matching function based on mode
        match_single_stream_base = match_single_stream_multi_sport if is_multi_sport else match_single_stream_single_league

        # Create ESPN client for cache refreshes (shared across threads via get_event_summary)
        from api.espn_client import ESPNClient
        espn_for_cache = ESPNClient()

        def match_with_cache(stream):
            """
            Wrapper that checks fingerprint cache before full matching.

            On cache hit: refresh dynamic fields from ESPN and return cached match
            On cache miss: run full matching, cache successful results
            """
            from database import find_any_channel_for_event
            nonlocal cache_stats
            stream_id = stream.get('id')
            stream_name = stream.get('name', '')

            # Check cache first (if caching enabled)
            if stream_cache and generation is not None:
                cached = stream_cache.get(group_id, stream_id, stream_name)
                if cached:
                    event_id, league, cached_data = cached
                    cache_stats['hits'] += 1

                    # Refresh dynamic fields (scores, status, odds)
                    refreshed = refresh_cached_event(
                        espn_for_cache,
                        cached_data,
                        league,
                        get_connection
                    )

                    if refreshed:
                        # Touch cache entry to keep it fresh
                        stream_cache.touch(group_id, stream_id, stream_name, generation)

                        # Check for cross-group consolidation (same logic as non-cached path)
                        # Only applies to multi-sport groups - single-league groups handle
                        # duplicates within their own group via duplicate_event_handling
                        refreshed_event = refreshed.get('event', {})
                        refreshed_event_id = refreshed_event.get('id')
                        overlap_handling = group.get('overlap_handling', 'add_stream')
                        existing_channel = None

                        if is_multi_sport and refreshed_event_id and overlap_handling in ('add_stream', 'add_only', 'skip'):
                            # First try to find channel matching stream's exception keyword
                            stream_keyword = stream.get('exception_keyword')
                            if stream_keyword:
                                existing_channel = find_any_channel_for_event(
                                    refreshed_event_id,
                                    exception_keyword=stream_keyword,
                                    exclude_group_id=group_id
                                )
                            # If no keyword or no match, find any channel for the event
                            if not existing_channel:
                                existing_channel = find_any_channel_for_event(refreshed_event_id, exclude_group_id=group_id, any_keyword=True)
                            if existing_channel:
                                if overlap_handling == 'skip':
                                    return {
                                        'type': 'filtered',
                                        'reason': 'EVENT_OWNED_BY_OTHER_GROUP',
                                        'stream': stream,
                                        'existing_channel': existing_channel
                                    }
                                # add_stream or add_only - continue with existing_channel set
                            elif overlap_handling == 'add_only':
                                return {
                                    'type': 'filtered',
                                    'reason': 'NO_EXISTING_CHANNEL',
                                    'stream': stream
                                }

                        # Exception keyword was pre-extracted in Step 2.6 and attached to stream dict
                        return {
                            'type': 'matched',
                            'stream': stream,
                            'teams': refreshed.get('team_result', {}),
                            'event': refreshed_event,
                            'detected_league': league,
                            'detection_tier': 'cache',
                            'from_cache': True,
                            'exception_keyword': stream.get('exception_keyword'),
                            'existing_channel': existing_channel  # For cross-group consolidation
                        }

                cache_stats['misses'] += 1

            # Cache miss - run full matching
            result = match_single_stream_base(stream)

            # Cache successful matches
            if stream_cache and generation is not None and result.get('type') == 'matched':
                event = result.get('event', {})
                event_id = event.get('id')
                detected_league = result.get('detected_league') or group.get('assigned_league', '')

                # Debug: Log cache store decision
                if not event_id:
                    app.logger.debug(f"[CACHE SKIP] No event_id for stream: {stream_name[:50]}")
                elif not detected_league:
                    app.logger.debug(f"[CACHE SKIP] No detected_league for stream: {stream_name[:50]}, event_id={event_id}")

                if event_id and detected_league:
                    # Build cached data structure
                    cached_data = {
                        'event': event,
                        'team_result': result.get('teams', {})
                    }
                    stream_cache.set(
                        group_id, stream_id, stream_name,
                        event_id, detected_league, cached_data, generation
                    )
                    cache_stats['stored'] += 1
                    app.logger.debug(f"[CACHE STORED] {stream_name[:50]} -> {event_id} ({detected_league})")

            return result

        # Process streams in parallel with progress reporting
        from concurrent.futures import as_completed
        matched_streams = []
        filtered_outside_lookahead = 0
        filtered_final = 0
        filtered_league_not_enabled = 0
        filtered_unsupported_sport = 0
        results = []

        if streams:  # Only use ThreadPoolExecutor if there are streams to process
            total_streams = len(streams)
            processed_count = 0

            with ThreadPoolExecutor(max_workers=min(total_streams, 100)) as executor:
                futures = {executor.submit(match_with_cache, s): s for s in streams}
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    processed_count += 1

                    # Report progress for every stream with name and status
                    if progress_callback:
                        stream_name = result.get('stream', {}).get('name', '')[:50]
                        matched = result.get('type') == 'matched'
                        status_icon = '✓' if matched else '✗'
                        progress_callback(
                            processed_count, total_streams, group['group_name'],
                            stream_name=stream_name,
                            stream_matched=matched,
                            stream_status=status_icon
                        )

        # Process results
        for result in results:
            if result['type'] == 'matched':
                stream = result['stream']
                event = result['event']
                teams = result['teams']
                matched_streams.append({
                    'stream': stream,
                    'teams': teams,
                    'event': event,
                    'exception_keyword': result.get('exception_keyword'),
                    'existing_channel': result.get('existing_channel')  # For cross-group consolidation
                })
                # Capture for matched streams log
                if generation is not None:
                    successful_matches.append({
                        'generation_id': generation,
                        'group_id': group_id,
                        'group_name': group_name,
                        'stream_id': stream.get('id'),
                        'stream_name': stream.get('name', ''),
                        'event_id': event.get('id', ''),
                        'event_name': event.get('name', event.get('short_name', '')),
                        'detected_league': result.get('detected_league'),
                        'detection_tier': result.get('detection_tier'),
                        'parsed_team1': teams.get('team1') if teams else None,
                        'parsed_team2': teams.get('team2') if teams else None,
                        'home_team': event.get('home_team', {}).get('name', ''),
                        'away_team': event.get('away_team', {}).get('name', ''),
                        'event_date': event.get('date', '')
                    })
            elif result['type'] == 'filtered':
                reason = result.get('reason')
                # Normalize reason to string for comparison (handles enums and strings)
                reason_str = normalize_reason(reason)

                # Count filtered reasons by category
                # Note: reason can be enum or string, reason_str is always the string value
                if reason in (FilteredReason.EVENT_FINAL,) or reason_str == 'event_final':
                    filtered_final += 1
                elif reason in (FilteredReason.EVENT_PAST, FilteredReason.EVENT_OUTSIDE_WINDOW) or reason_str in ('event_past', 'event_outside_window'):
                    filtered_outside_lookahead += 1
                elif reason in (FilteredReason.LEAGUE_NOT_ENABLED,) or reason_str == 'league_not_enabled':
                    filtered_league_not_enabled += 1
                elif reason in (FilteredReason.UNSUPPORTED_BEACH_SOCCER, FilteredReason.UNSUPPORTED_BOXING_MMA, FilteredReason.UNSUPPORTED_FUTSAL):
                    filtered_unsupported_sport += 1

                # Check if this is a FAILURE that should be recorded
                # Uses new should_record_failure() which checks against FailedReason enums
                if should_record_failure(reason) and generation is not None:
                    stream = result.get('stream', {})
                    parsed_teams = result.get('parsed_teams', {})
                    failed_matches.append({
                        'generation_id': generation,
                        'group_id': group_id,
                        'group_name': group_name,
                        'stream_id': stream.get('id'),
                        'stream_name': stream.get('name', ''),
                        'reason': reason_str,  # Store normalized string value
                        'parsed_team1': parsed_teams.get('team1') if parsed_teams else None,
                        'parsed_team2': parsed_teams.get('team2') if parsed_teams else None,
                        'detection_tier': result.get('detection_tier'),
                        'leagues_checked': result.get('leagues_checked'),
                        'detail': result.get('detail')
                    })

            elif result['type'] == 'no_teams':
                # Teams could not be parsed/matched - this is a FAILURE
                if generation is not None:
                    stream = result.get('stream', {})
                    parsed_teams = result.get('parsed_teams', {})
                    reason = result.get('reason', FailedReason.TEAMS_NOT_PARSED)
                    failed_matches.append({
                        'generation_id': generation,
                        'group_id': group_id,
                        'group_name': group_name,
                        'stream_id': stream.get('id'),
                        'stream_name': stream.get('name', ''),
                        'reason': normalize_reason(reason),  # Store normalized string value
                        'parsed_team1': parsed_teams.get('team1') if parsed_teams else None,
                        'parsed_team2': parsed_teams.get('team2') if parsed_teams else None,
                        'detection_tier': result.get('detection_tier'),
                        'leagues_checked': result.get('leagues_checked'),
                        'detail': result.get('detail')
                    })
        matched_count = len(matched_streams)

        # Save match data to database for debugging
        if failed_matches:
            try:
                save_failed_matches_batch(failed_matches)
                app.logger.debug(f"Saved {len(failed_matches)} failed matches for group {group_name}")
            except Exception as e:
                app.logger.warning(f"Could not save failed matches: {e}")

        if successful_matches:
            try:
                save_matched_streams_batch(successful_matches)
                app.logger.debug(f"Saved {len(successful_matches)} matched streams for group {group_name}")
            except Exception as e:
                app.logger.warning(f"Could not save matched streams: {e}")

        # Step 3.5: Include existing managed channels that weren't matched
        # This ensures EPG continues for channels until they're actually deleted
        # (e.g., game went final but channel hasn't been deleted yet)
        from database import get_managed_channels_for_group

        # Create event_matcher for fetching events by ID (outside thread pool)
        event_matcher = create_event_matcher(lookahead_days=lookahead_days)

        existing_channels = get_managed_channels_for_group(group_id)
        matched_event_ids = {m['event'].get('id') for m in matched_streams}

        for channel in existing_channels:
            espn_event_id = channel.get('espn_event_id')
            if not espn_event_id or espn_event_id in matched_event_ids:
                continue

            # This channel has an event not in matched_streams - fetch it
            # Pass include_final_events=True to get the event regardless of status
            try:
                # We need team IDs to fetch the event - get from channel data
                # The channel stores home_team and away_team names, not IDs
                # So we'll fetch the event directly by ID using the scoreboard API
                event_data = event_matcher.get_event_by_id(
                    espn_event_id,
                    group['assigned_league']
                )

                if event_data:
                    matched_streams.append({
                        'stream': {
                            'id': channel.get('dispatcharr_stream_id'),
                            'name': channel.get('channel_name', '')
                        },
                        'teams': {
                            'matched': True,
                            'home_team_name': channel.get('home_team', ''),
                            'away_team_name': channel.get('away_team', '')
                        },
                        'event': event_data,
                        'from_managed_channel': True  # Flag for debugging
                    })
                    app.logger.debug(f"Included final event {espn_event_id} from existing channel '{channel.get('channel_name')}'")
            except Exception as e:
                app.logger.warning(f"Could not fetch event {espn_event_id} for existing channel: {e}")

        # Calculate totals for stats
        game_stream_count = len(streams)
        total_event_excluded = filtered_outside_lookahead + filtered_final + filtered_league_not_enabled + filtered_unsupported_sport
        effective_stream_count = game_stream_count - total_event_excluded

        # Update stats with granular filtering breakdown
        update_event_epg_group_stats(
            group_id,
            stream_count=effective_stream_count,
            matched_count=matched_count,
            total_stream_count=total_stream_count,
            filtered_no_indicator=filtered_no_indicator,
            filtered_include_regex=filtered_include_regex,
            filtered_exclude_regex=filtered_exclude_regex,
            filtered_outside_lookahead=filtered_outside_lookahead,
            filtered_final=filtered_final,
            filtered_league_not_enabled=filtered_league_not_enabled,
            filtered_unsupported_sport=filtered_unsupported_sport
        )

        # Log with filtering info
        log_parts = [f"Matched {matched_count}/{effective_stream_count} streams for group '{group['group_name']}'"]
        total_filtered = filtered_no_indicator + filtered_include_regex + filtered_exclude_regex
        if total_filtered > 0:
            log_parts.append(f"{total_filtered} non-game filtered")
        if total_event_excluded > 0:
            exclude_parts = []
            if filtered_outside_lookahead > 0:
                exclude_parts.append(f"{filtered_outside_lookahead} past")
            if filtered_final > 0:
                exclude_parts.append(f"{filtered_final} final")
            if filtered_league_not_enabled > 0:
                exclude_parts.append(f"{filtered_league_not_enabled} league disabled")
            if filtered_unsupported_sport > 0:
                exclude_parts.append(f"{filtered_unsupported_sport} unsupported sport")
            log_parts.append(f"{total_event_excluded} event excluded ({', '.join(exclude_parts)})")

        # Add cache stats if caching was enabled
        if stream_cache and (cache_stats['hits'] > 0 or cache_stats['stored'] > 0):
            log_parts.append(f"cache: {cache_stats['hits']} hits, {cache_stats['stored']} stored")

        app.logger.debug(" | ".join(log_parts))

        # Check if template is assigned (child groups inherit from parent, so they don't need one)
        is_child_group = group.get('parent_group_id') is not None
        if not group.get('event_template_id') and not is_child_group:
            app.logger.debug(f"No template assigned to group '{group['group_name']}' - skipping EPG generation")
            return {
                'success': False,
                'total_stream_count': total_stream_count,
                'stream_count': game_stream_count,
                'filtered_no_indicator': filtered_no_indicator,
                'filtered_include_regex': filtered_include_regex,
                'filtered_exclude_regex': filtered_exclude_regex,
                'filtered_outside_lookahead': filtered_outside_lookahead,
                'filtered_final': filtered_final,
                'filtered_league_not_enabled': filtered_league_not_enabled,
                'filtered_unsupported_sport': filtered_unsupported_sport,
                'matched_count': matched_count,
                'matched_streams': [],
                'error': 'No event template assigned to this group'
            }

        # Step 4: Generate XMLTV (or add streams to parent for child groups)
        epg_result = None
        channel_results = None

        if matched_streams:
            # Sort matched streams by event start time (earliest first)
            sorted_matched = sorted(
                matched_streams,
                key=lambda m: m['event'].get('date', '') or ''
            )

            # Child groups: skip EPG generation, just add streams to parent channels
            if is_child_group:
                # Child groups don't generate EPG - they just add streams to parent channels
                pass  # EPG generation skipped for child groups
            else:
                # Parent groups: Generate EPG
                event_template = None
                if group.get('event_template_id'):
                    event_template = get_template(group['event_template_id'])

                # Use settings output path to derive data directory
                output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')

                epg_result = generate_event_epg(
                    matched_streams=sorted_matched,
                    group_info=group,
                    save=True,
                    data_dir=get_data_dir(output_path),
                    settings=settings,
                    template=event_template,
                    epg_start_datetime=epg_start_datetime
                )

                if not epg_result.get('success'):
                    return {
                        'success': False,
                        'total_stream_count': total_stream_count,
                        'stream_count': game_stream_count,
                        'filtered_no_indicator': filtered_no_indicator,
                        'filtered_include_regex': filtered_include_regex,
                        'filtered_exclude_regex': filtered_exclude_regex,
                        'filtered_outside_lookahead': filtered_outside_lookahead,
                        'filtered_final': filtered_final,
                        'filtered_league_not_enabled': filtered_league_not_enabled,
                        'filtered_unsupported_sport': filtered_unsupported_sport,
                        'matched_count': matched_count,
                        'matched_streams': matched_streams,
                        'error': f"EPG generation failed: {epg_result.get('error')}",
                        'step': 'generate'
                    }

                app.logger.debug(f"Generated event EPG: {epg_result.get('file_path')}")

                # Consolidate all event EPGs
                final_output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
                consolidate_result = after_event_epg_generation(group_id, final_output_path)

            # Step 5: Channel Lifecycle Management
            if is_child_group:
                # Child group: Add streams to parent group's channels
                # Uses process_child_group_streams() which handles exception keywords
                from epg.channel_lifecycle import get_lifecycle_manager

                lifecycle_mgr = get_lifecycle_manager()
                if lifecycle_mgr:
                    app.logger.debug(f"Processing child group {group_id} - adding streams to parent channels")

                    child_results = lifecycle_mgr.process_child_group_streams(group, matched_streams)

                    channel_results = {
                        'created': [],
                        'existing': [],
                        'skipped': child_results.get('skipped', []),
                        'errors': child_results.get('errors', []),
                        'streams_added': child_results.get('streams_added', [])
                    }

            elif group.get('channel_start'):
                # Parent group: Normal channel creation
                from epg.channel_lifecycle import get_lifecycle_manager

                lifecycle_mgr = get_lifecycle_manager()
                if lifecycle_mgr:
                    app.logger.debug(f"Processing channel lifecycle for group {group_id}")

                    # First, sync ALL channels with current group settings
                    # This ensures setting changes (e.g., delete_timing) are applied
                    sync_results = lifecycle_mgr.sync_group_settings(group)
                    if sync_results.get('cleared'):
                        app.logger.debug(f"Cleared delete times for {len(sync_results['cleared'])} channels (settings changed)")
                    if sync_results.get('updated'):
                        app.logger.debug(f"Synced {len(sync_results['updated'])} channels with group settings")

                    # Update scheduled delete times for matched streams (with fresh event data)
                    update_results = lifecycle_mgr.update_existing_channels(
                        matched_streams=matched_streams,
                        group=group
                    )
                    if update_results['updated']:
                        app.logger.debug(f"Updated {len(update_results['updated'])} channel delete times")

                    # Sort matched streams by event start time (earliest first)
                    # This ensures channels are numbered chronologically
                    sorted_matched = sorted(
                        matched_streams,
                        key=lambda m: m['event'].get('date', '') or ''
                    )

                    # Create new channels for matched streams
                    channel_results = lifecycle_mgr.process_matched_streams(
                        matched_streams=sorted_matched,
                        group=group,
                        template=event_template
                    )

                    if channel_results['created']:
                        app.logger.debug(f"Created {len(channel_results['created'])} channels")
                    if channel_results.get('skipped'):
                        app.logger.debug(f"Skipped {len(channel_results['skipped'])} channels")
                        # Log first few skip reasons for debugging
                        for skip in channel_results['skipped'][:3]:
                            app.logger.debug(f"  - {skip.get('stream', 'unknown')}: {skip.get('reason', 'no reason')}")
                    if channel_results.get('existing'):
                        app.logger.debug(f"Existing {len(channel_results['existing'])} channels")
                    if channel_results.get('errors'):
                        app.logger.warning(f"Errors creating {len(channel_results['errors'])} channels")
                        for err in channel_results['errors'][:3]:
                            app.logger.warning(f"  - {err.get('stream', 'unknown')}: {err.get('error', 'no error')}")

                    # Clean up channels for removed streams
                    stream_ids = [s['id'] for s in streams]
                    cleanup_results = lifecycle_mgr.cleanup_deleted_streams(group, stream_ids)
                    if cleanup_results['deleted']:
                        app.logger.debug(f"Cleaned up {len(cleanup_results['deleted'])} removed channels")

        # Cleanup removed streams even if no matched streams
        # (don't process scheduled deletions here - that happens globally at end of EPG generation)
        if not matched_streams and group.get('channel_start'):
            from epg.channel_lifecycle import get_lifecycle_manager
            lifecycle_mgr = get_lifecycle_manager()
            if lifecycle_mgr:
                stream_ids = [s['id'] for s in streams]
                cleanup_results = lifecycle_mgr.cleanup_deleted_streams(group, stream_ids)
                if cleanup_results['deleted']:
                    app.logger.debug(f"Cleaned up {len(cleanup_results['deleted'])} removed channels (no matches)")

        return {
            'success': True,
            'total_stream_count': total_stream_count,
            'stream_count': game_stream_count,
            'filtered_no_indicator': filtered_no_indicator,
            'filtered_include_regex': filtered_include_regex,
            'filtered_exclude_regex': filtered_exclude_regex,
            'filtered_outside_lookahead': filtered_outside_lookahead,
            'filtered_final': filtered_final,
            'filtered_league_not_enabled': filtered_league_not_enabled,
            'filtered_unsupported_sport': filtered_unsupported_sport,
            'matched_count': matched_count,
            'matched_streams': matched_streams,
            'epg_result': epg_result,
            'channel_results': channel_results,
            # Detailed stats for EPG history
            'programmes_generated': epg_result.get('programme_count', 0) if epg_result else 0,
            'events_count': epg_result.get('event_count', 0) if epg_result else 0,
            'pregame_count': epg_result.get('pregame_count', 0) if epg_result else 0,
            'postgame_count': epg_result.get('postgame_count', 0) if epg_result else 0,
        }

    except Exception as e:
        app.logger.error(f"Error refreshing event group '{group['group_name']}': {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'step': 'unknown'
        }


def generate_all_epg(progress_callback=None, settings=None, save_history=True, team_progress_callback=None, triggered_by='manual'):
    """
    AUTHORITATIVE EPG generation function - single source of truth for ALL EPG generation.

    Args:
        progress_callback: Optional callback for progress updates
        settings: Optional settings dict (fetched if not provided)
        save_history: Whether to save generation stats to epg_history table
        team_progress_callback: Optional callback for team-level progress
        triggered_by: What triggered this generation ('manual', 'scheduler', 'api')

    This function handles the complete EPG pipeline:
    1. Generates team-based EPG → saves to teams.xml
    2. Refreshes all enabled event groups with templates → saves to event_epg_*.xml files
    3. Consolidator merges teams.xml + all event_epg_*.xml → teamarr.xml
    4. Processes channel lifecycle (scheduled deletions)
    5. Saves statistics to epg_history (Single Source of Truth)
    6. Returns combined statistics

    All EPG generation (scheduler, manual, streaming) MUST use this function.

    Args:
        progress_callback: Optional callable(status, message, percent) for high-level progress
        settings: Optional settings dict, will be fetched from DB if not provided
        save_history: Whether to save generation stats to epg_history (default: True)
        team_progress_callback: Optional callable(current, total, team_name, message) for team-level progress

    Returns:
        dict with keys: success, team_stats, event_stats, lifecycle_stats, generation_time, error
    """
    from epg.epg_consolidator import after_team_epg_generation, get_data_dir, finalize_epg_generation
    from database import save_epg_generation_stats, clear_failed_matches, clear_matched_streams
    from epg.channel_lifecycle import get_lifecycle_manager
    from epg.stream_match_cache import StreamMatchCache, increment_generation_counter
    import hashlib

    start_time = datetime.now()

    # Increment generation counter for fingerprint cache
    current_generation = increment_generation_counter(get_connection)
    app.logger.debug(f"EPG generation #{current_generation}")

    # Clear previous match data before new generation
    clear_failed_matches()
    clear_matched_streams()

    def report_progress(status, message, percent=None, **extra):
        """Helper to report progress if callback provided"""
        if progress_callback:
            progress_callback(status, message, percent, **extra)

    # Initialize all stats
    team_stats = {
        'count': 0,
        'programmes': 0,
        'events': 0,
        'pregame': 0,
        'postgame': 0,
        'idle': 0,
        'api_calls': 0
    }
    event_stats = {
        'groups_refreshed': 0,
        'streams_matched': 0,
        'programmes': 0,
        'events': 0,
        'pregame': 0,
        'postgame': 0,
        # Filtering stats (aggregated across all groups)
        'total_streams': 0,
        'filtered_no_indicator': 0,
        'filtered_include_regex': 0,
        'filtered_exclude_regex': 0,
        'filtered_outside_lookahead': 0,
        'filtered_final': 0,
        'filtered_league_not_enabled': 0,
        'filtered_unsupported_sport': 0,
        'eligible_streams': 0
    }
    lifecycle_stats = {
        'channels_deleted': 0
    }

    try:
        # Get settings if not provided
        if settings is None:
            conn = get_connection()
            settings_row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            conn.close()
            if not settings_row:
                return {'success': False, 'error': 'Settings not configured'}
            settings = dict(settings_row)

        days_ahead = settings.get('epg_days_ahead', 14)
        epg_timezone = settings.get('default_timezone', 'America/Detroit')
        output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')

        # Calculate EPG start datetime (used for event-based filler spanning multiple days)
        try:
            tz = ZoneInfo(epg_timezone)
        except Exception:
            tz = ZoneInfo('America/Detroit')
        epg_start_datetime = datetime.now(tz).replace(minute=0, second=0, microsecond=0)

        report_progress('starting', 'Initializing EPG generation...', 0)

        # Clear caches and reset counters for fresh generation
        epg_orchestrator.espn.clear_schedule_cache()
        epg_orchestrator.espn.clear_team_info_cache()
        epg_orchestrator.espn.clear_roster_cache()
        epg_orchestrator.espn.clear_group_cache()
        epg_orchestrator.espn.clear_scoreboard_cache()
        epg_orchestrator.api_calls = 0

        # Clear Dispatcharr caches for fresh channel/logo lookups
        # (caches are still used within this generation cycle for performance)
        lifecycle_mgr = get_lifecycle_manager()
        if lifecycle_mgr:
            lifecycle_mgr.clear_cache()

        # ============================================
        # PHASE 1: Team-based EPG
        # ============================================
        report_progress('progress', 'Generating team-based EPG...', 10)

        try:
            result = epg_orchestrator.generate_epg(
                days_ahead=days_ahead,
                epg_timezone=epg_timezone,
                settings=settings,
                progress_callback=team_progress_callback  # Pass through for per-team progress
            )

            if result and result.get('teams_list'):
                team_stats['count'] = len(result['teams_list'])
                team_stats['programmes'] = result['stats'].get('num_programmes', 0)
                team_stats['events'] = result['stats'].get('num_events', 0)
                team_stats['pregame'] = result['stats'].get('num_pregame', 0)
                team_stats['postgame'] = result['stats'].get('num_postgame', 0)
                team_stats['idle'] = result['stats'].get('num_idle', 0)
                team_stats['api_calls'] = result.get('api_calls', 0)

                # Generate team XMLTV and save via consolidator
                report_progress('progress', f'Saving team EPG ({team_stats["count"]} teams)...', 45)

                xml_content = xmltv_generator.generate(
                    result['teams_list'],
                    result['all_events'],
                    settings
                )

                after_team_epg_generation(xml_content, output_path)
                app.logger.info(f"📺 Team EPG: {team_stats['programmes']} programs from {team_stats['count']} teams")
            else:
                report_progress('progress', 'No active teams configured, skipping team EPG...', 45)
                app.logger.debug("📺 Team EPG: No active teams configured")

        except Exception as e:
            app.logger.warning(f"Team EPG generation error: {e}")
            # Continue to event groups even if team EPG fails

        # ============================================
        # PHASE 2: Event-based EPG
        # ============================================
        report_progress('progress', 'Processing event groups...', 50)

        event_groups = get_all_event_epg_groups(enabled_only=True)
        # Parent groups need templates, child groups inherit from parent (template can be NULL)
        event_groups_with_templates = [
            g for g in event_groups
            if g.get('event_template_id') or g.get('parent_group_id')
        ]

        # Sort groups for processing order:
        # 1. Single-league parent groups - process first (create channels)
        # 2. Single-league child groups - process second (add streams to parent channels)
        # 3. Multi-sport groups - process last (no parent/child hierarchy, check other groups' channels)
        # This ensures parent channels exist before children try to add streams to them,
        # and multi-sport groups can check if events are already handled by single-league groups
        single_league_groups = [g for g in event_groups_with_templates if not g.get('is_multi_sport')]
        multi_sport_groups = [g for g in event_groups_with_templates if g.get('is_multi_sport')]

        # Split single-league into parents and children
        single_league_parents = [g for g in single_league_groups if not g.get('parent_group_id')]
        single_league_children = [g for g in single_league_groups if g.get('parent_group_id')]

        if multi_sport_groups or single_league_children:
            app.logger.debug(f"Processing order: {len(single_league_parents)} single-league parents, {len(single_league_children)} single-league children, {len(multi_sport_groups)} multi-sport groups")

        if event_groups_with_templates:
            # Get M3U manager
            m3u_manager = _get_m3u_manager()

            if m3u_manager:
                total_groups = len(event_groups_with_templates)

                # Step 2a: Batch refresh all unique M3U accounts in parallel
                unique_account_ids = list(set(
                    g['dispatcharr_account_id']
                    for g in event_groups_with_templates
                    if g.get('dispatcharr_account_id')
                ))

                if unique_account_ids:
                    account_count = len(unique_account_ids)
                    report_progress('progress', f'Refreshing {account_count} M3U provider(s)...', 52)
                    app.logger.info(f"🔄 Batch refreshing {account_count} unique M3U account(s) for {total_groups} event group(s)")

                    batch_refresh_result = m3u_manager.refresh_multiple_accounts(
                        unique_account_ids,
                        timeout=120
                    )

                    if batch_refresh_result.get('success'):
                        skipped = batch_refresh_result.get('skipped_count', 0)
                        refreshed = batch_refresh_result.get('succeeded_count', 0) - skipped
                        if skipped > 0:
                            app.logger.info(
                                f"✅ M3U batch refresh: {refreshed} refreshed, {skipped} skipped (recently updated) "
                                f"in {batch_refresh_result.get('duration', 0):.1f}s"
                            )
                        else:
                            app.logger.info(
                                f"✅ M3U batch refresh completed in {batch_refresh_result.get('duration', 0):.1f}s "
                                f"({batch_refresh_result.get('succeeded_count', 0)} succeeded)"
                            )
                    else:
                        failed = batch_refresh_result.get('failed_count', 0)
                        succeeded = batch_refresh_result.get('succeeded_count', 0)
                        skipped = batch_refresh_result.get('skipped_count', 0)
                        app.logger.warning(
                            f"⚠️ M3U batch refresh partial: {succeeded} succeeded, {failed} failed, {skipped} skipped"
                        )
                        # Log individual failures
                        for account_id, result in batch_refresh_result.get('results', {}).items():
                            if not result.get('success') and not result.get('skipped'):
                                app.logger.warning(f"  Account {account_id}: {result.get('message')}")

                # Step 2b: Process groups sequentially (parents first, then children)
                # Sequential processing allows stream-level progress reporting during large groups
                # Streams within each group are still processed in parallel for speed
                report_progress('progress', f'Processing {total_groups} event group(s)...', 55)

                group_results = []
                completed_count = 0
                all_groups = single_league_parents + single_league_children + multi_sport_groups

                def make_stream_progress_callback(group_idx, total_groups):
                    """Create a callback for stream-level progress within a group."""
                    def callback(processed_streams, total_streams, group_name, **kwargs):
                        # Calculate overall progress: 55-85% range divided among groups
                        # Each group gets a slice of the progress bar proportional to its position
                        group_base_percent = 55 + int(30 * group_idx / total_groups)
                        group_end_percent = 55 + int(30 * (group_idx + 1) / total_groups)
                        group_range = group_end_percent - group_base_percent

                        # Within this group's slice, show stream progress
                        stream_progress = processed_streams / total_streams if total_streams > 0 else 1
                        progress_percent = group_base_percent + int(group_range * stream_progress)

                        # Build message with stream name and status if available
                        stream_name = kwargs.get('stream_name', '')
                        stream_status = kwargs.get('stream_status', '')
                        if stream_name:
                            message = f"{group_name}: {stream_name} {stream_status} ({processed_streams}/{total_streams})"
                        else:
                            message = f"Processing {group_name}: {processed_streams}/{total_streams} streams"

                        report_progress(
                            'progress',
                            message,
                            progress_percent,
                            group_name=group_name,
                            streams_processed=processed_streams,
                            streams_total=total_streams,
                            stream_name=stream_name,
                            stream_matched=kwargs.get('stream_matched'),
                            stream_status=stream_status
                        )
                    return callback

                for group_idx, group in enumerate(all_groups):
                    try:
                        # Create stream progress callback for this group
                        stream_callback = make_stream_progress_callback(group_idx, total_groups)

                        # Initial progress message for this group
                        group_base_percent = 55 + int(30 * group_idx / total_groups)
                        report_progress(
                            'progress',
                            f"Starting group: {group['group_name']}",
                            group_base_percent,
                            group_name=group['group_name']
                        )

                        # Process the group with stream-level progress callback
                        refresh_result = refresh_event_group_core(
                            group, m3u_manager,
                            skip_m3u_refresh=True,
                            epg_start_datetime=epg_start_datetime,
                            progress_callback=stream_callback,
                            generation=current_generation
                        )
                        error = None
                    except Exception as e:
                        app.logger.warning(f"Error refreshing event group '{group['group_name']}': {e}")
                        refresh_result = None
                        error = str(e)

                    group_results.append((group, refresh_result, error))
                    completed_count += 1

                    # Aggregate stats
                    if refresh_result and refresh_result.get('success'):
                        event_stats['groups_refreshed'] += 1
                        event_stats['streams_matched'] += refresh_result.get('matched_count', 0)
                        event_stats['programmes'] += refresh_result.get('programmes_generated', 0)
                        event_stats['events'] += refresh_result.get('events_count', 0)
                        event_stats['pregame'] += refresh_result.get('pregame_count', 0)
                        event_stats['postgame'] += refresh_result.get('postgame_count', 0)
                        event_stats['total_streams'] += refresh_result.get('total_stream_count', 0)
                        event_stats['filtered_no_indicator'] += refresh_result.get('filtered_no_indicator', 0)
                        event_stats['filtered_include_regex'] += refresh_result.get('filtered_include_regex', 0)
                        event_stats['filtered_exclude_regex'] += refresh_result.get('filtered_exclude_regex', 0)
                        event_stats['filtered_outside_lookahead'] += refresh_result.get('filtered_outside_lookahead', 0)
                        event_stats['filtered_final'] += refresh_result.get('filtered_final', 0)
                        event_stats['filtered_league_not_enabled'] += refresh_result.get('filtered_league_not_enabled', 0)
                        event_stats['filtered_unsupported_sport'] += refresh_result.get('filtered_unsupported_sport', 0)
                        event_stats['eligible_streams'] += refresh_result.get('stream_count', 0)

                        # Child groups update their own last_refresh
                        if group.get('parent_group_id'):
                            update_event_epg_group_last_refresh(group['id'])

                    # Report group completion
                    progress_percent = 55 + int(30 * completed_count / total_groups)
                    report_progress(
                        'progress',
                        f"Completed {completed_count}/{total_groups} groups ({group['group_name']})",
                        progress_percent,
                        group_name=group['group_name'],
                        current=completed_count,
                        total=total_groups
                    )
            else:
                report_progress('progress', 'M3U manager not available, skipping event groups...', 85)
                app.logger.warning("M3U manager not available - skipping event groups")
        else:
            report_progress('progress', 'No event groups with templates configured...', 85)

        # ============================================
        # PHASE 3: Channel Lifecycle Processing
        # ============================================
        report_progress('progress', 'Processing channel lifecycle...', 88)

        reconciliation_stats = {
            'issues_found': 0,
            'issues_fixed': 0,
            'orphans_teamarr': 0,
            'orphans_dispatcharr': 0,
            'duplicates': 0,
            'drift': 0
        }

        try:
            lifecycle_mgr = get_lifecycle_manager()
            if lifecycle_mgr:
                # 3a: Run reconciliation if enabled
                if settings.get('reconcile_on_epg_generation', True):
                    from epg.reconciliation import run_reconciliation
                    try:
                        recon_result = run_reconciliation(auto_fix=True)
                        if recon_result:
                            summary = recon_result.summary
                            reconciliation_stats['issues_found'] = summary.get('total', 0)
                            reconciliation_stats['issues_fixed'] = summary.get('fixed', 0)
                            reconciliation_stats['orphans_teamarr'] = summary.get('orphan_teamarr', 0)
                            reconciliation_stats['orphans_dispatcharr'] = summary.get('orphan_dispatcharr', 0)
                            reconciliation_stats['duplicates'] = summary.get('duplicate', 0)
                            reconciliation_stats['drift'] = summary.get('drift', 0)

                            if reconciliation_stats['issues_found'] > 0:
                                app.logger.info(
                                    f"🔍 Reconciliation: {reconciliation_stats['issues_found']} issues found, "
                                    f"{reconciliation_stats['issues_fixed']} fixed"
                                )
                    except Exception as e:
                        app.logger.warning(f"Reconciliation error (non-fatal): {e}")

                # 3b: Clean up channels from disabled groups
                disabled_cleanup = lifecycle_mgr.cleanup_disabled_groups()
                disabled_deleted = len(disabled_cleanup.get('deleted', []))
                if disabled_deleted:
                    app.logger.info(f"🗑️ Cleaned up {disabled_deleted} channel(s) from disabled groups")

                # 3c: Process scheduled deletions
                deletion_results = lifecycle_mgr.process_scheduled_deletions()
                scheduled_deleted = len(deletion_results.get('deleted', []))
                if scheduled_deleted:
                    app.logger.info(f"🗑️ Processed {scheduled_deleted} scheduled channel deletions")

                # 3d: Enforce stream keyword placement
                keyword_results = lifecycle_mgr.enforce_stream_keyword_placement()
                streams_moved = keyword_results.get('moved', 0)

                # 3e: Enforce keyword channel ordering (keyword channels after main)
                ordering_results = lifecycle_mgr.enforce_keyword_channel_ordering()
                channels_reordered = ordering_results.get('reordered', 0)

                # 3f: Enforce cross-group consolidation (multi-sport → single-league)
                cross_group_results = lifecycle_mgr.enforce_cross_group_consolidation()
                cross_group_consolidated = cross_group_results.get('consolidated', 0)

                # 3g: Clean up orphan Dispatcharr channels (teamarr-event-* not in DB)
                orphan_cleanup = lifecycle_mgr.cleanup_orphan_dispatcharr_channels()
                orphans_deleted = orphan_cleanup.get('deleted', 0)

                lifecycle_stats['channels_deleted'] = disabled_deleted + scheduled_deleted + cross_group_results.get('channels_deleted', 0) + orphans_deleted
                lifecycle_stats['streams_moved'] = streams_moved
                lifecycle_stats['channels_reordered'] = channels_reordered
                lifecycle_stats['reconciliation'] = reconciliation_stats
        except Exception as e:
            app.logger.warning(f"Channel lifecycle processing error: {e}")

        # ============================================
        # PHASE 4: Final consolidation & History
        # ============================================
        report_progress('progress', 'Consolidating EPG files...', 95)

        # Check if we have anything
        if team_stats['count'] == 0 and event_stats['groups_refreshed'] == 0:
            return {
                'success': False,
                'error': 'No active teams or event groups with templates configured',
                'team_stats': team_stats,
                'event_stats': event_stats,
                'lifecycle_stats': lifecycle_stats
            }

        # Calculate generation time and file info
        generation_time = (datetime.now() - start_time).total_seconds()
        file_size = 0
        file_hash = ''

        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            with open(output_path, 'r') as f:
                file_hash = hashlib.md5(f.read().encode()).hexdigest()

        # Save to history (Single Source of Truth)
        if save_history:
            total_channels = team_stats['count'] + event_stats['streams_matched']
            total_programmes = team_stats['programmes'] + event_stats['programmes']
            total_events = team_stats['events'] + event_stats['events']

            save_epg_generation_stats({
                # Basic info
                'file_path': output_path,
                'file_size': file_size,
                'file_hash': file_hash,
                'generation_time_seconds': generation_time,
                'api_calls_made': team_stats.get('api_calls', 0),
                'status': 'success',
                # Totals
                'num_channels': total_channels,
                'num_events': total_events,
                'num_programmes': total_programmes,
                'num_pregame': team_stats['pregame'] + event_stats['pregame'],
                'num_postgame': team_stats['postgame'] + event_stats['postgame'],
                'num_idle': team_stats['idle'],  # Only team-based has idle
                # Team-based breakdown
                'team_based_channels': team_stats['count'],
                'team_based_events': team_stats['events'],
                'team_based_pregame': team_stats['pregame'],
                'team_based_postgame': team_stats['postgame'],
                'team_based_idle': team_stats['idle'],
                # Event-based breakdown
                'event_based_channels': event_stats['streams_matched'],
                'event_based_events': event_stats['events'],
                'event_based_pregame': event_stats['pregame'],
                'event_based_postgame': event_stats['postgame'],
                # Event-based filtering stats (aggregated across all groups)
                'event_total_streams': event_stats['total_streams'],
                'event_filtered_no_indicator': event_stats['filtered_no_indicator'],
                'event_filtered_include_regex': event_stats['filtered_include_regex'],
                'event_filtered_exclude_regex': event_stats['filtered_exclude_regex'],
                'event_filtered_outside_lookahead': event_stats['filtered_outside_lookahead'],
                'event_filtered_final': event_stats['filtered_final'],
                'event_filtered_league_not_enabled': event_stats['filtered_league_not_enabled'],
                'event_filtered_unsupported_sport': event_stats['filtered_unsupported_sport'],
                'event_eligible_streams': event_stats['eligible_streams'],
                'event_matched_streams': event_stats['streams_matched'],
                # Quality stats (not tracked here, defaults to 0)
                'unresolved_vars_count': 0,
                'coverage_gaps_count': 0,
                'warnings_json': '[]',
                # Trigger source
                'triggered_by': triggered_by
            })
            app.logger.info(f"📊 EPG history saved: {total_programmes} programmes, {total_channels} channels in {generation_time:.2f}s")

        # ============================================
        # PHASE 5: Dispatcharr Auto-Refresh (if configured)
        # ============================================
        dispatcharr_refreshed = False
        if settings.get('dispatcharr_enabled') and settings.get('dispatcharr_epg_id'):
            report_progress('progress', 'Refreshing Dispatcharr EPG...', 98)
            try:
                from api.dispatcharr_client import EPGManager

                dispatcharr_url = settings.get('dispatcharr_url')
                dispatcharr_username = settings.get('dispatcharr_username')
                dispatcharr_password = settings.get('dispatcharr_password')
                dispatcharr_epg_id = settings.get('dispatcharr_epg_id')

                app.logger.info("🔄 Refreshing Dispatcharr EPG (waiting for completion)...")
                manager = EPGManager(dispatcharr_url, dispatcharr_username, dispatcharr_password)

                # Use wait_for_refresh to ensure EPG import completes before associating
                refresh_result = manager.wait_for_refresh(dispatcharr_epg_id, timeout=60)

                if refresh_result.get('success'):
                    duration = refresh_result.get('duration', 0)
                    app.logger.info(f"✅ Dispatcharr EPG refresh completed in {duration:.1f}s")

                    # Update last sync time
                    sync_conn = get_connection()
                    sync_conn.execute(
                        "UPDATE settings SET dispatcharr_last_sync = ? WHERE id = 1",
                        (datetime.now().isoformat(),)
                    )
                    sync_conn.commit()
                    sync_conn.close()
                    dispatcharr_refreshed = True

                    # ============================================
                    # PHASE 6: Associate EPG with Managed Channels
                    # ============================================
                    # Now that EPG refresh is complete, EPGData records exist
                    # Pattern: Look up EPGData by tvg_id, call set_channel_epg()
                    report_progress('progress', 'Associating EPG with managed channels...', 99)
                    try:
                        lifecycle_mgr = get_lifecycle_manager()
                        if lifecycle_mgr:
                            assoc_results = lifecycle_mgr.associate_epg_with_channels()
                            assoc_count = len(assoc_results.get('associated', []))
                            skip_count = len(assoc_results.get('skipped', []))
                            error_count = len(assoc_results.get('errors', []))

                            if assoc_count > 0:
                                app.logger.info(f"🔗 Associated EPG with {assoc_count} managed channels")
                            if skip_count > 0:
                                app.logger.debug(f"   Skipped {skip_count} channels (no matching EPGData)")
                            if error_count > 0:
                                app.logger.warning(f"   Failed to associate {error_count} channels")
                    except Exception as e:
                        app.logger.warning(f"EPG association error: {e}")

                else:
                    app.logger.warning(f"⚠️ Dispatcharr EPG refresh failed: {refresh_result.get('message')}")
            except Exception as e:
                app.logger.error(f"❌ Dispatcharr refresh error: {e}")

        # Build completion message for progress callback
        parts = []
        if team_stats['count'] > 0:
            parts.append(f"{team_stats['count']} teams")
        if event_stats['groups_refreshed'] > 0:
            parts.append(f"{event_stats['streams_matched']} event streams from {event_stats['groups_refreshed']} groups")
        completion_msg = f"EPG generated: {', '.join(parts)} ({total_programmes} programmes in {generation_time:.1f}s)"
        if dispatcharr_refreshed:
            completion_msg += " - Dispatcharr refreshed"

        report_progress('complete', completion_msg, 100,
                       total_programmes=total_programmes,
                       total_channels=total_channels,
                       generation_time=generation_time)

        # Finalize: archive intermediate files (runs once at end of full cycle)
        finalize_epg_generation(output_path)

        # Purge stale fingerprint cache entries
        try:
            stream_cache = StreamMatchCache(get_connection)
            purged = stream_cache.purge_stale(current_generation)
            if purged > 0:
                app.logger.info(f"🗑️ Purged {purged} stale fingerprint cache entries")
        except Exception as e:
            app.logger.warning(f"Fingerprint cache purge error: {e}")

        return {
            'success': True,
            'team_stats': team_stats,
            'event_stats': event_stats,
            'lifecycle_stats': lifecycle_stats,
            'output_path': output_path,
            'generation_time': generation_time,
            'total_programmes': total_programmes,
            'total_channels': total_channels
        }

    except Exception as e:
        app.logger.error(f"EPG generation error: {e}", exc_info=True)
        generation_time = (datetime.now() - start_time).total_seconds()

        report_progress('error', f'EPG generation failed: {str(e)}', None)

        # Save error to history
        if save_history:
            try:
                from database import save_epg_generation_stats
                save_epg_generation_stats({
                    'file_path': 'failed',
                    'file_size': 0,
                    'file_hash': '',
                    'generation_time_seconds': generation_time,
                    'api_calls_made': 0,
                    'status': 'error',
                    'num_channels': 0,
                    'num_events': 0,
                    'num_programmes': 0,
                    'warnings_json': f'["{str(e)}"]',
                    'triggered_by': triggered_by
                })
            except Exception as hist_err:
                app.logger.warning(f"Failed to save error to history: {hist_err}")

        return {
            'success': False,
            'error': str(e),
            'team_stats': team_stats,
            'event_stats': event_stats,
            'lifecycle_stats': lifecycle_stats
        }


def run_scheduled_generation():
    """Run EPG generation (called by scheduler) - uses unified generate_all_epg()"""
    try:
        app.logger.info(f"🕐 Scheduled EPG generation started at {datetime.now()}")

        with app.app_context():
            result = generate_all_epg(triggered_by='scheduler')

            if result.get('success'):
                team_stats = result.get('team_stats', {})
                event_stats = result.get('event_stats', {})

                # Summary
                parts = []
                if team_stats.get('count', 0) > 0:
                    parts.append(f"{team_stats['programmes']} team programs from {team_stats['count']} teams")
                if event_stats.get('groups_refreshed', 0) > 0:
                    parts.append(f"{event_stats['streams_matched']} event streams from {event_stats['groups_refreshed']} groups")

                if parts:
                    app.logger.info(f"✅ Scheduled EPG generation completed: {', '.join(parts)}")
                else:
                    app.logger.info("✅ Scheduled EPG generation completed: No teams or event groups configured")
            else:
                app.logger.warning(f"⚠️ Scheduled EPG generation issue: {result.get('error', 'Unknown error')}")

    except Exception as e:
        app.logger.error(f"❌ Scheduler error: {e}", exc_info=True)

def get_last_epg_generation_time():
    """Get the last EPG generation time from the database (in UTC)"""
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT generated_at FROM epg_history
            WHERE status = 'success'
            ORDER BY generated_at DESC
            LIMIT 1
        """).fetchone()
        conn.close()

        if row and row['generated_at']:
            # Parse the timestamp - database stores UTC
            # Return as UTC-aware datetime
            from datetime import timezone
            dt = datetime.fromisoformat(row['generated_at'].replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return None
    except Exception as e:
        app.logger.error(f"Error getting last EPG generation time: {e}")
        return None


def _check_soccer_cache_refresh(settings: dict):
    """
    Check if soccer cache needs refresh based on settings.
    Called from scheduler loop once per day.
    """
    from epg.soccer_multi_league import SoccerMultiLeague

    frequency = settings.get('soccer_cache_refresh_frequency', 'weekly')

    # Map frequency to max age in days
    frequency_days = {
        'daily': 1,
        'every_3_days': 3,
        'weekly': 7,
        'manual': 9999  # Never auto-refresh
    }

    max_age = frequency_days.get(frequency, 7)

    if SoccerMultiLeague.refresh_if_needed(max_age):
        app.logger.info("⚽ Soccer league cache refreshed by scheduler")


def _check_team_league_cache_refresh(settings: dict):
    """
    Check if team-league cache needs refresh based on settings.
    Uses same frequency setting as soccer cache.
    Called from scheduler loop once per day.
    """
    from epg.team_league_cache import TeamLeagueCache

    # Uses same frequency setting as soccer cache
    frequency = settings.get('soccer_cache_refresh_frequency', 'weekly')

    # Map frequency to max age in days
    frequency_days = {
        'daily': 1,
        'every_3_days': 3,
        'weekly': 7,
        'manual': 9999  # Never auto-refresh
    }

    max_age = frequency_days.get(frequency, 7)

    if TeamLeagueCache.refresh_if_needed(max_age):
        app.logger.info("🏈 Team-league cache refreshed by scheduler")


def scheduler_loop():
    """Background thread that runs the scheduler using cron expressions"""
    global scheduler_running, last_run_time
    from datetime import timezone
    from croniter import croniter

    app.logger.info("🚀 EPG Auto-Generation Scheduler started")

    while scheduler_running:
        try:
            conn = get_connection()
            settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
            conn.close()

            if not settings.get('auto_generate_enabled', False):
                time.sleep(60)  # Check every minute if disabled
                continue

            # Get cron expression (default: every hour at minute 0)
            cron_expression = settings.get('cron_expression', '0 * * * *')

            # Use UTC for all comparisons to avoid timezone issues
            now = datetime.now(timezone.utc)

            # Get last run time from database (persists across restarts)
            db_last_run = get_last_epg_generation_time()

            # Use database time if available, otherwise use in-memory time
            effective_last_run = db_last_run or last_run_time
            # Ensure in-memory time is also UTC-aware
            if effective_last_run and effective_last_run.tzinfo is None:
                effective_last_run = effective_last_run.replace(tzinfo=timezone.utc)

            # Check if it's time to run based on cron expression
            should_run = False

            try:
                # Use effective_last_run as base time for croniter
                # If never run, use 24 hours ago to catch any missed runs
                base_time = effective_last_run or (now - timedelta(hours=24))

                cron = croniter(cron_expression, base_time)
                next_run = cron.get_next(datetime)

                # Make next_run timezone-aware if it isn't
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)

                if now >= next_run:
                    app.logger.info(f"⏰ Cron trigger: '{cron_expression}' - scheduled time {next_run.strftime('%H:%M')} reached")
                    should_run = True

            except Exception as e:
                app.logger.error(f"Invalid cron expression '{cron_expression}': {e}")
                # Fall back to hourly at minute 0 if cron is invalid
                if now.minute == 0 and (effective_last_run is None or now.hour != effective_last_run.hour):
                    should_run = True

            if should_run:
                run_scheduled_generation()
                last_run_time = now

            # Check cache refresh once per day at midnight UTC
            # This is separate from EPG generation frequency
            if now.hour == 0 and now.minute < 1:  # First minute of the day
                try:
                    _check_soccer_cache_refresh(settings)
                except Exception as e:
                    app.logger.warning(f"Soccer cache check failed: {e}")

                try:
                    _check_team_league_cache_refresh(settings)
                except Exception as e:
                    app.logger.warning(f"Team-league cache check failed: {e}")

            time.sleep(30)  # Check every 30 seconds

        except Exception as e:
            app.logger.error(f"❌ Scheduler loop error: {e}", exc_info=True)
            time.sleep(60)

    app.logger.info("🛑 EPG Auto-Generation Scheduler stopped")

def start_scheduler():
    """Start the scheduler background thread"""
    global scheduler_thread, scheduler_running

    if scheduler_thread and scheduler_thread.is_alive():
        app.logger.warning("⚠️  Scheduler already running")
        return

    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()
    app.logger.info("✅ Scheduler thread started")

def stop_scheduler():
    """Stop the scheduler background thread"""
    global scheduler_running
    scheduler_running = False
    app.logger.info("⏹️  Scheduler stopping...")

# =============================================================================
# DASHBOARD / HOME
# =============================================================================

@app.route('/')
def index():
    """Dashboard - overview of templates and teams"""
    conn = get_connection()
    cursor = conn.cursor()

    # Get template stats
    template_count = cursor.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
    team_template_count = cursor.execute("SELECT COUNT(*) FROM templates WHERE template_type = 'team' OR template_type IS NULL").fetchone()[0]
    event_template_count = cursor.execute("SELECT COUNT(*) FROM templates WHERE template_type = 'event'").fetchone()[0]

    # Get team stats
    team_count = cursor.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    team_league_count = cursor.execute("SELECT COUNT(DISTINCT league) FROM teams WHERE league IS NOT NULL AND league != ''").fetchone()[0]
    active_team_count = cursor.execute("SELECT COUNT(*) FROM teams WHERE active = 1").fetchone()[0]
    assigned_team_count = cursor.execute("SELECT COUNT(*) FROM teams WHERE template_id IS NOT NULL").fetchone()[0]

    # Get event group stats
    event_group_count = cursor.execute("SELECT COUNT(*) FROM event_epg_groups").fetchone()[0]
    enabled_event_group_count = cursor.execute("SELECT COUNT(*) FROM event_epg_groups WHERE enabled = 1").fetchone()[0]
    total_event_streams = cursor.execute("SELECT COALESCE(SUM(stream_count), 0) FROM event_epg_groups WHERE enabled = 1").fetchone()[0]
    matched_event_streams = cursor.execute("SELECT COALESCE(SUM(matched_count), 0) FROM event_epg_groups WHERE enabled = 1").fetchone()[0]

    # Get managed channel stats
    managed_channel_count = cursor.execute("SELECT COUNT(*) FROM managed_channels WHERE deleted_at IS NULL").fetchone()[0]
    channels_with_logos = cursor.execute("""
        SELECT COUNT(*) FROM managed_channels
        WHERE deleted_at IS NULL AND dispatcharr_logo_id IS NOT NULL
    """).fetchone()[0]
    recently_deleted_count = cursor.execute("""
        SELECT COUNT(*) FROM managed_channels
        WHERE deleted_at IS NOT NULL AND deleted_at >= datetime('now', '-24 hours')
    """).fetchone()[0]

    # Get distinct Dispatcharr channel groups with active channels
    dispatcharr_groups_count = cursor.execute("""
        SELECT COUNT(DISTINCT eg.channel_group_id)
        FROM managed_channels mc
        JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
        WHERE mc.deleted_at IS NULL AND eg.channel_group_id IS NOT NULL
    """).fetchone()[0]

    # Get leagues with logos for Teams quadrant hover dropdown
    team_leagues = cursor.execute("""
        SELECT DISTINCT t.league as league_code,
               COALESCE(lc.league_name, UPPER(t.league)) as league_name,
               lc.logo_url,
               COUNT(t.id) as team_count
        FROM teams t
        LEFT JOIN league_config lc ON LOWER(t.league) = LOWER(lc.league_code)
        WHERE t.league IS NOT NULL AND t.league != ''
        GROUP BY t.league
        ORDER BY team_count DESC, league_name
    """).fetchall()

    # Get leagues with logos for Event Groups quadrant Leagues tile hover
    event_leagues = cursor.execute("""
        SELECT DISTINCT eg.assigned_league as league_code,
               COALESCE(lc.league_name, UPPER(eg.assigned_league)) as league_name,
               lc.logo_url,
               COUNT(eg.id) as group_count
        FROM event_epg_groups eg
        LEFT JOIN league_config lc ON LOWER(eg.assigned_league) = LOWER(lc.league_code)
        WHERE eg.enabled = 1
        GROUP BY eg.assigned_league
        ORDER BY group_count DESC, league_name
    """).fetchall()

    # Get event groups for Event Groups quadrant Groups tile hover
    event_groups_list = cursor.execute("""
        SELECT eg.group_name,
               COALESCE(eg.matched_count, 0) as matched_count,
               COALESCE(eg.stream_count, 0) as stream_count
        FROM event_epg_groups eg
        WHERE eg.enabled = 1
        ORDER BY eg.group_name
    """).fetchall()

    # Get Dispatcharr channel groups for Channels quadrant Groups tile hover
    channel_groups_list = cursor.execute("""
        SELECT DISTINCT eg.channel_group_name, COUNT(mc.id) as channel_count
        FROM managed_channels mc
        JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
        WHERE mc.deleted_at IS NULL AND eg.channel_group_name IS NOT NULL
        GROUP BY eg.channel_group_name
        ORDER BY channel_count DESC, eg.channel_group_name
    """).fetchall()

    # Calculate match percentage (handle 0/0 case)
    match_percent = round((matched_event_streams / total_event_streams * 100), 0) if total_event_streams > 0 else 0

    # Get timezone from settings
    settings_row = cursor.execute("SELECT default_timezone FROM settings WHERE id = 1").fetchone()
    user_timezone = settings_row[0] if settings_row else 'America/Detroit'

    # Get latest EPG generation stats (includes all fields needed by template)
    latest_epg = cursor.execute("""
        SELECT generated_at, num_programmes, num_events, num_channels,
               generation_time_seconds, triggered_by,
               team_based_channels, event_based_channels,
               num_pregame, num_postgame, num_idle,
               event_eligible_streams, event_matched_streams
        FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 1
    """).fetchone()

    # Get last 10 EPG generations for history table with detailed stats
    epg_history = cursor.execute("""
        SELECT generated_at, num_channels, num_events, num_programmes,
               generation_time_seconds, status,
               team_based_events, event_based_events,
               team_based_pregame, event_based_pregame,
               team_based_postgame, event_based_postgame,
               team_based_idle
        FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    # Get comprehensive EPG stats from single source of truth
    from database import get_epg_stats_summary
    epg_stats = get_epg_stats_summary()

    # Convert timestamps to user's timezone
    def format_timestamp(utc_timestamp):
        if not utc_timestamp:
            return None
        # SQLite timestamps are stored as 'YYYY-MM-DD HH:MM:SS' without timezone info
        # We need to parse them as naive datetime and then treat as UTC
        dt_naive = datetime.fromisoformat(utc_timestamp.replace('Z', '+00:00'))
        # If the datetime is naive (no timezone info), assume it's UTC
        if dt_naive.tzinfo is None:
            dt_utc = dt_naive.replace(tzinfo=ZoneInfo('UTC'))
        else:
            dt_utc = dt_naive
        # Convert to user's timezone
        dt_local = dt_utc.astimezone(ZoneInfo(user_timezone))
        # Format with timezone abbreviation
        return dt_local.strftime('%Y-%m-%d %H:%M:%S %Z')

    # Format timestamps for latest EPG
    if latest_epg:
        latest_epg_dict = dict(latest_epg)
        latest_epg_dict['generated_at_formatted'] = format_timestamp(latest_epg_dict['generated_at'])
        latest_epg = latest_epg_dict

    # Format timestamps for history
    epg_history_formatted = []
    for entry in epg_history:
        entry_dict = dict(entry)
        entry_dict['generated_at_formatted'] = format_timestamp(entry_dict['generated_at'])
        epg_history_formatted.append(entry_dict)

    return render_template('index.html',
        template_count=template_count,
        team_template_count=team_template_count,
        event_template_count=event_template_count,
        team_count=team_count,
        team_league_count=team_league_count,
        active_team_count=active_team_count,
        assigned_team_count=assigned_team_count,
        event_group_count=event_group_count,
        enabled_event_group_count=enabled_event_group_count,
        total_event_streams=total_event_streams,
        matched_event_streams=matched_event_streams,
        managed_channel_count=managed_channel_count,
        channels_with_logos=channels_with_logos,
        dispatcharr_groups_count=dispatcharr_groups_count,
        recently_deleted_count=recently_deleted_count,
        latest_epg=latest_epg,
        epg_history=epg_history_formatted,
        epg_stats=epg_stats,  # Single source of truth for EPG stats
        # Hover dropdown data
        team_leagues=team_leagues,
        event_leagues=event_leagues,
        event_groups_list=event_groups_list,
        channel_groups_list=channel_groups_list,
        match_percent=match_percent
    )

# =============================================================================
# TEMPLATES MANAGEMENT
# =============================================================================

@app.route('/templates')
def templates_list():
    """List all templates with team counts"""
    templates = get_all_templates()
    return render_template('template_list.html', templates=templates)

@app.route('/templates/add', methods=['GET'])
def templates_add_form():
    """Show add template form"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('template_form.html', template=None, mode='add', description_options_json='[]', settings=settings)

@app.route('/templates/add', methods=['POST'])
def templates_add():
    """Create a new template"""
    try:
        data = _extract_template_form_data(request.form)
        # Add template_type (only set on creation, immutable after)
        data['template_type'] = request.form.get('template_type', 'team')
        app.logger.info(f"Creating new template: {data['name']} (type: {data['template_type']})")
        app.logger.debug(f"Template data: {data}")

        template_id = create_template(data)

        app.logger.info(f"✅ Template created successfully - ID: {template_id}, Name: {data['name']}")
        flash(f"Template '{data['name']}' created successfully!", 'success')
        return redirect(url_for('templates_list'))
    except Exception as e:
        app.logger.error(f"❌ Error creating template: {str(e)}", exc_info=True)
        flash(f"Error creating template: {str(e)}", 'error')
        return redirect(url_for('templates_add_form'))

@app.route('/templates/<int:template_id>/edit', methods=['GET'])
def templates_edit_form(template_id):
    """Show edit template form"""
    import json
    template = get_template(template_id)
    if not template:
        flash('Template not found', 'error')
        return redirect(url_for('templates_list'))

    # Pass description_options as JSON for JavaScript
    description_options_json = template.get('description_options', '[]') if template else '[]'

    # Get settings for default duration values
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    return render_template('template_form.html', template=template, mode='edit', description_options_json=description_options_json, settings=settings)

@app.route('/templates/<int:template_id>/edit', methods=['POST'])
def templates_edit(template_id):
    """Update an existing template"""
    try:
        data = _extract_template_form_data(request.form)

        # Ensure template_type is never changed after creation (immutable)
        # _extract_template_form_data doesn't include it, but safeguard anyway
        data.pop('template_type', None)

        # Get existing template_type for idle field clearing logic
        existing_template = get_template(template_id)
        if existing_template:
            data['template_type'] = existing_template.get('template_type', 'team')

        if update_template(template_id, data):
            flash(f"Template '{data['name']}' updated successfully!", 'success')
        else:
            flash('Template not found', 'error')
        return redirect(url_for('templates_list'))
    except Exception as e:
        flash(f"Error updating template: {str(e)}", 'error')
        return redirect(url_for('templates_edit_form', template_id=template_id))

@app.route('/templates/<int:template_id>/delete', methods=['POST'])
def templates_delete(template_id):
    """Delete a template (with warning if teams assigned)"""
    try:
        template = get_template(template_id)
        if not template:
            flash('Template not found', 'error')
            return redirect(url_for('templates_list'))

        team_count = get_template_team_count(template_id)
        app.logger.warning(f"🗑️  Deleting template: {template['name']} (ID: {template_id}, {team_count} teams affected)")

        if delete_template(template_id):
            if team_count > 0:
                app.logger.warning(f"⚠️  {team_count} team(s) are now unassigned after template deletion")
                flash(f"Template deleted. {team_count} team(s) are now unassigned.", 'warning')
            else:
                app.logger.info(f"✅ Template '{template['name']}' deleted successfully")
                flash('Template deleted successfully!', 'success')
        else:
            flash('Template not found', 'error')
    except Exception as e:
        app.logger.error(f"❌ Error deleting template: {str(e)}", exc_info=True)
        flash(f"Error deleting template: {str(e)}", 'error')
    return redirect(url_for('templates_list'))

@app.route('/templates/<int:template_id>/duplicate', methods=['POST'])
def templates_duplicate(template_id):
    """Duplicate a template with 'Copy of' prefix"""
    try:
        template = get_template(template_id)
        if not template:
            flash('Template not found', 'error')
            return redirect(url_for('templates_list'))

        # Create copy with new name
        template_data = dict(template)
        template_data['name'] = f"Copy of {template['name']}"

        # Remove fields that shouldn't be copied
        fields_to_remove = ['id', 'created_at', 'updated_at', 'team_count', 'template_name']
        for field in fields_to_remove:
            template_data.pop(field, None)

        new_template_id = create_template(template_data)
        app.logger.info(f"✅ Duplicated template '{template['name']}' to '{template_data['name']}' (ID: {new_template_id})")
        flash(f"Template duplicated successfully as '{template_data['name']}'!", 'success')
    except Exception as e:
        app.logger.error(f"❌ Error duplicating template: {str(e)}", exc_info=True)
        flash(f"Error duplicating template: {str(e)}", 'error')
    return redirect(url_for('templates_list'))

@app.route('/templates/<int:template_id>/export', methods=['GET'])
def templates_export(template_id):
    """Export template as JSON file"""
    template = get_template(template_id)
    if not template:
        flash('Template not found', 'error')
        return redirect(url_for('templates_list'))

    # Remove ID, timestamps, sport, and league for clean export
    export_data = {k: v for k, v in template.items() if k not in ['id', 'created_at', 'updated_at', 'team_count', 'sport', 'league']}

    # Create temporary JSON file
    filename = f"template_{template['name'].replace(' ', '_')}.json"
    filepath = f"/tmp/{filename}"

    with open(filepath, 'w') as f:
        json.dump(export_data, f, indent=2)

    return send_file(filepath, as_attachment=True, download_name=filename)

@app.route('/templates/import', methods=['POST'])
def templates_import():
    """Import template from JSON file"""
    try:
        if 'file' not in request.files:
            flash('No file provided', 'error')
            return redirect(url_for('templates_list'))

        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('templates_list'))

        # Read and parse JSON
        data = json.load(file)

        # Check if template name already exists
        existing = get_connection().execute("SELECT id FROM templates WHERE name = ?", (data.get('name', ''),)).fetchone()
        if existing:
            # Append timestamp to make unique
            data['name'] = f"{data['name']} (imported {datetime.now().strftime('%Y-%m-%d %H:%M')})"

        template_id = create_template(data)
        flash(f"Template '{data['name']}' imported successfully!", 'success')
    except Exception as e:
        flash(f"Error importing template: {str(e)}", 'error')

    return redirect(url_for('templates_list'))

# =============================================================================
# TEAMS MANAGEMENT
# =============================================================================

@app.route('/teams')
def teams_list():
    """List all teams"""
    teams = get_all_teams()
    templates = get_all_templates()  # For bulk assign dropdown

    # Get league logos and settings for display
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT league_code, logo_url FROM league_config")
    league_logos = {row['league_code']: row['logo_url'] for row in cursor.fetchall()}
    settings = dict(cursor.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    return render_template('team_list.html', teams=teams, templates=templates, league_logos=league_logos, settings=settings)

@app.route('/teams/import', methods=['GET'])
def teams_import():
    """Show team import UI"""
    return render_template('team_import.html')

@app.route('/teams/add', methods=['GET'])
def teams_add_form():
    """Show add team form"""
    templates = get_all_templates()
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('team_form.html', team=None, templates=templates, settings=settings, mode='add')

@app.route('/teams/add', methods=['POST'])
def teams_add():
    """Create a new team"""
    try:
        data = _extract_team_form_data(request.form)
        app.logger.info(f"Adding new team: {data['team_name']} ({data['league'].upper()})")
        app.logger.debug(f"Team data: {data}")

        team_id = create_team(data)

        template_info = f"Template: {get_template(data['template_id'])['name']}" if data.get('template_id') else "No template"
        app.logger.info(f"✅ Team added successfully - ID: {team_id}, Name: {data['team_name']}, {template_info}")
        flash(f"Team '{data['team_name']}' added successfully!", 'success')
        return redirect(url_for('teams_list'))
    except Exception as e:
        app.logger.error(f"❌ Error adding team: {str(e)}", exc_info=True)
        flash(f"Error adding team: {str(e)}", 'error')
        return redirect(url_for('teams_add_form'))

@app.route('/teams/<int:team_id>/edit', methods=['GET'])
def teams_edit_form(team_id):
    """Show edit team form"""
    team = get_team(team_id)
    if not team:
        flash('Team not found', 'error')
        return redirect(url_for('teams_list'))
    templates = get_all_templates()
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('team_form.html', team=team, templates=templates, settings=settings, mode='edit')

@app.route('/teams/<int:team_id>/edit', methods=['POST'])
def teams_edit(team_id):
    """Update an existing team"""
    try:
        data = _extract_team_form_data(request.form)
        if update_team(team_id, data):
            flash(f"Team '{data['team_name']}' updated successfully!", 'success')
        else:
            flash('Team not found', 'error')
        return redirect(url_for('teams_list'))
    except Exception as e:
        flash(f"Error updating team: {str(e)}", 'error')
        return redirect(url_for('teams_edit_form', team_id=team_id))

@app.route('/teams/<int:team_id>/delete', methods=['POST'])
def teams_delete_single(team_id):
    """Delete a single team"""
    try:
        if delete_team(team_id):
            flash('Team deleted successfully!', 'success')
        else:
            flash('Team not found', 'error')
    except Exception as e:
        flash(f"Error deleting team: {str(e)}", 'error')
    return redirect(url_for('teams_list'))

# =============================================================================
# BULK OPERATIONS
# =============================================================================

@app.route('/teams/bulk/assign-template', methods=['POST'])
def teams_bulk_assign_template():
    """Bulk assign template to teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        template_id = request.form.get('template_id')

        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        # Convert to integers
        team_ids = [int(tid) for tid in team_ids]
        template_id = int(template_id) if template_id and template_id != '' else None

        # Validate template type - only 'team' templates can be assigned to teams
        if template_id:
            template = get_template(template_id)
            if not template:
                return jsonify({'success': False, 'message': 'Template not found'})
            if template.get('template_type', 'team') != 'team':
                return jsonify({'success': False, 'message': 'Cannot assign event template to teams. Use a team template.'})

        template_name = get_template(template_id)['name'] if template_id else 'Unassigned'
        app.logger.info(f"🔗 Bulk assigning {len(team_ids)} team(s) to template: {template_name}")

        count = bulk_assign_template(team_ids, template_id)

        app.logger.info(f"✅ Bulk assignment complete - {count} team(s) assigned to '{template_name}'")

        return jsonify({
            'success': True,
            'message': f'Assigned {count} team(s) to template: {template_name}'
        })
    except Exception as e:
        app.logger.error(f"❌ Error in bulk template assignment: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/delete', methods=['POST'])
def teams_bulk_delete():
    """Bulk delete teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        team_ids = [int(tid) for tid in team_ids]
        count = bulk_delete_teams(team_ids)

        return jsonify({
            'success': True,
            'message': f'Deleted {count} team(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/activate', methods=['POST'])
def teams_bulk_activate():
    """Bulk activate teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        team_ids = [int(tid) for tid in team_ids]
        count = bulk_set_active(team_ids, True)

        return jsonify({
            'success': True,
            'message': f'Activated {count} team(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/deactivate', methods=['POST'])
def teams_bulk_deactivate():
    """Bulk deactivate teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        team_ids = [int(tid) for tid in team_ids]
        count = bulk_set_active(team_ids, False)

        return jsonify({
            'success': True,
            'message': f'Deactivated {count} team(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/change-channel-id', methods=['POST'])
def teams_bulk_change_channel_id():
    """Bulk change channel IDs based on a format template"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        format_template = request.form.get('format', '')

        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        if not format_template:
            return jsonify({'success': False, 'message': 'No format template provided'})

        team_ids = [int(tid) for tid in team_ids]
        updated_count = 0
        errors = []

        conn = get_connection()
        cursor = conn.cursor()

        for team_id in team_ids:
            try:
                # Get team data
                team = cursor.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
                if not team:
                    errors.append(f"Team ID {team_id} not found")
                    continue

                team_dict = dict(team)

                # Generate team_name_pascal (PascalCase)
                team_name = team_dict.get('team_name', '')
                team_name_pascal = to_pascal_case(team_name)

                # Get league_name for uppercase league variable
                league_name_row = cursor.execute(
                    "SELECT league_name FROM league_config WHERE league_code = ?",
                    (team_dict.get('league'),)
                ).fetchone()
                league_name = league_name_row[0] if league_name_row else team_dict.get('league', '').upper()

                # Generate channel ID from format
                channel_id = format_template \
                    .replace('{team_name_pascal}', team_name_pascal) \
                    .replace('{team_abbrev}', (team_dict.get('team_abbrev') or '').lower()) \
                    .replace('{team_name}', team_name.lower().replace(' ', '-')) \
                    .replace('{team_slug}', team_dict.get('team_slug') or team_name.lower().replace(' ', '-')) \
                    .replace('{espn_team_id}', str(team_dict.get('espn_team_id') or '')) \
                    .replace('{league_id}', (team_dict.get('league') or '').lower()) \
                    .replace('{league}', league_name) \
                    .replace('{sport}', (team_dict.get('sport') or '').lower())

                # Clean up channel ID (remove special characters, multiple dashes, etc.)
                # Preserve uppercase if format uses {team_name_pascal} or {league}
                import re
                if '{team_name_pascal}' in format_template or ('{league}' in format_template and '{league_id}' not in format_template):
                    # Allow uppercase letters (for PascalCase channel IDs)
                    channel_id = re.sub(r'[^a-zA-Z0-9.-]+', '', channel_id)
                else:
                    # Traditional: lowercase only, replace spaces with dashes
                    channel_id = re.sub(r'[^a-z0-9.-]+', '-', channel_id)
                    channel_id = re.sub(r'-+', '-', channel_id)
                    channel_id = channel_id.strip('-')

                if not channel_id:
                    errors.append(f"Generated empty channel ID for team {team_dict.get('team_name')}")
                    continue

                # Update the team's channel_id
                cursor.execute("UPDATE teams SET channel_id = ? WHERE id = ?", (channel_id, team_id))
                updated_count += 1

            except Exception as e:
                errors.append(f"Error updating team ID {team_id}: {str(e)}")

        conn.commit()
        conn.close()

        message = f'Updated {updated_count} team(s)'
        if errors:
            message += f'. Errors: {"; ".join(errors[:3])}'  # Show first 3 errors

        return jsonify({
            'success': True,
            'updated': updated_count,
            'message': message
        })
    except Exception as e:
        app.logger.error(f"❌ Error in bulk channel ID change: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/<int:team_id>/toggle-status', methods=['POST'])
def teams_toggle_status(team_id):
    """Toggle team active status"""
    try:
        active = request.form.get('active') == '1'

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE teams SET active = ? WHERE id = ?", (active, team_id))
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Team {"activated" if active else "deactivated"}'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# =============================================================================
# EPG MANAGEMENT
# =============================================================================

@app.route('/epg', methods=['GET'])
def epg_management():
    """EPG management page"""
    import os
    from database import get_epg_stats_summary

    # Get latest EPG generation info
    conn = get_connection()
    latest_epg_row = conn.execute("""
        SELECT * FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 1
    """).fetchone()
    latest_epg = dict(latest_epg_row) if latest_epg_row else None

    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    # Get EPG stats from single source of truth
    epg_stats = get_epg_stats_summary()

    # Check if EPG file exists
    epg_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
    epg_file_exists = os.path.exists(epg_path)
    epg_filename = os.path.basename(epg_path) if epg_file_exists else None
    epg_file_size = None

    if epg_file_exists:
        size_bytes = os.path.getsize(epg_path)
        if size_bytes < 1024:
            epg_file_size = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            epg_file_size = f"{size_bytes / 1024:.1f} KB"
        else:
            epg_file_size = f"{size_bytes / (1024 * 1024):.1f} MB"

    # Read EPG content for preview (full file)
    epg_content = None
    epg_total_lines = 0
    epg_analysis = None

    if epg_file_exists:
        try:
            with open(epg_path, 'r', encoding='utf-8') as f:
                epg_content = f.read()
                epg_total_lines = epg_content.count('\n')

            # Analyze EPG content
            epg_analysis = _analyze_epg_content(epg_content)

            # Override counts with authoritative values from epg_history
            # (the analysis function guesses using keywords, but we know the real counts from generation)
            if latest_epg:
                if latest_epg.get('num_events') is not None:
                    epg_analysis['total_events'] = latest_epg['num_events']
                if latest_epg.get('num_pregame') is not None:
                    epg_analysis['filler_programs']['pregame'] = latest_epg['num_pregame']
                if latest_epg.get('num_postgame') is not None:
                    epg_analysis['filler_programs']['postgame'] = latest_epg['num_postgame']
                if latest_epg.get('num_idle') is not None:
                    epg_analysis['filler_programs']['idle'] = latest_epg['num_idle']
        except Exception as e:
            app.logger.error(f"Error reading EPG file: {e}")
            epg_content = None

    # Generate EPG URL
    epg_url = f"{request.url_root}teamarr.xml"

    return render_template('epg_management.html',
                         latest_epg=latest_epg,
                         epg_file_exists=epg_file_exists,
                         epg_filename=epg_filename,
                         epg_file_size=epg_file_size,
                         epg_content=epg_content,
                         epg_total_lines=epg_total_lines,
                         epg_analysis=epg_analysis,
                         epg_stats=epg_stats,
                         epg_url=epg_url)

# =============================================================================
# CHANNELS UI
# =============================================================================

@app.route('/channels')
def channels_list():
    """Managed channels page - shows channels created by Teamarr"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    epg_timezone = settings.get('default_timezone', 'America/Detroit')
    return render_template('channels.html', epg_timezone=epg_timezone)

# =============================================================================
# EVENT GROUPS UI
# =============================================================================

@app.route('/event-groups')
def event_groups_list():
    """List all enabled event groups"""
    groups = get_all_event_epg_groups()
    aliases = get_all_aliases()

    conn = get_connection()
    cursor = conn.cursor()

    # Get leagues for alias modal dropdown
    leagues = cursor.execute("""
        SELECT league_code, league_name, sport
        FROM league_config
        WHERE active = 1
        ORDER BY sport, league_name
    """).fetchall()
    leagues = [dict(row) for row in leagues]

    # Get league logos
    cursor.execute("SELECT league_code, logo_url FROM league_config WHERE logo_url IS NOT NULL")
    league_logos = {row['league_code']: row['logo_url'] for row in cursor.fetchall()}

    # Get event templates for assignment dropdown
    event_templates = cursor.execute("""
        SELECT id, name FROM templates WHERE template_type = 'event' ORDER BY name
    """).fetchall()
    event_templates = [dict(row) for row in event_templates]

    conn.close()

    # Backfill account_name for groups that don't have it
    groups_needing_backfill = [g for g in groups if not g.get('account_name') and g.get('dispatcharr_account_id')]
    if groups_needing_backfill:
        try:
            manager = _get_m3u_manager()
            accounts = manager.list_m3u_accounts() if manager else []
            account_map = {a['id']: a['name'] for a in accounts}

            conn = get_connection()
            cursor = conn.cursor()
            for group in groups_needing_backfill:
                account_name = account_map.get(group['dispatcharr_account_id'])
                if account_name:
                    cursor.execute(
                        "UPDATE event_epg_groups SET account_name = ? WHERE id = ?",
                        (account_name, group['id'])
                    )
                    group['account_name'] = account_name
            conn.commit()
            conn.close()
        except Exception as e:
            app.logger.warning(f"Could not backfill account names: {e}")

    return render_template('event_epg.html',
                          groups=groups,
                          aliases=aliases,
                          leagues=leagues,
                          league_logos=league_logos,
                          event_templates=event_templates)


@app.route('/event-groups/import')
def event_groups_import():
    """Import event groups from Dispatcharr"""
    # Get event templates for the configure modal
    conn = get_connection()
    cursor = conn.cursor()
    templates = cursor.execute("""
        SELECT id, name FROM templates WHERE template_type = 'event' ORDER BY name
    """).fetchall()
    conn.close()

    event_templates = [dict(row) for row in templates]

    return render_template('event_groups_import.html', event_templates=event_templates)


@app.route('/event-groups/<int:group_id>/edit')
def event_group_edit(group_id):
    """Edit an existing event group"""
    group = get_event_epg_group(group_id)
    if not group:
        flash('Event group not found', 'error')
        return redirect(url_for('event_groups_list'))

    # Get event templates
    conn = get_connection()
    cursor = conn.cursor()
    templates = cursor.execute("""
        SELECT id, name FROM templates WHERE template_type = 'event' ORDER BY name
    """).fetchall()
    conn.close()

    event_templates = [dict(row) for row in templates]

    return render_template('event_group_form.html',
                          mode='edit',
                          group=group,
                          event_templates=event_templates)


@app.route('/event-groups/add')
def event_group_add():
    """Add a new event group (from import flow)"""
    # Get parameters from query string (passed from import page)
    dispatcharr_group_id = request.args.get('dispatcharr_group_id', type=int)
    dispatcharr_account_id = request.args.get('dispatcharr_account_id', type=int)
    group_name = request.args.get('group_name', '')
    account_name = request.args.get('account_name', '')

    if not dispatcharr_group_id or not dispatcharr_account_id:
        flash('Missing required parameters', 'error')
        return redirect(url_for('event_groups_import'))

    # Get event templates
    conn = get_connection()
    cursor = conn.cursor()
    templates = cursor.execute("""
        SELECT id, name FROM templates WHERE template_type = 'event' ORDER BY name
    """).fetchall()
    conn.close()

    event_templates = [dict(row) for row in templates]

    return render_template('event_group_form.html',
                          mode='add',
                          group=None,
                          dispatcharr_group_id=dispatcharr_group_id,
                          dispatcharr_account_id=dispatcharr_account_id,
                          group_name=group_name,
                          account_name=account_name,
                          event_templates=event_templates)


# =============================================================================
# SETTINGS
# =============================================================================

@app.route('/settings', methods=['GET'])
def settings_form():
    """Show settings form"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('settings.html', settings=settings)

@app.route('/settings', methods=['POST'])
def settings_update():
    """Update global settings"""
    from zoneinfo import ZoneInfo

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Update settings (extract from form)
        fields = [
            'epg_days_ahead', 'epg_update_time', 'epg_output_path',
            'default_timezone', 'default_channel_id_format', 'midnight_crossover_mode',
            'time_format', 'show_timezone',
            'game_duration_default',
            'game_duration_basketball', 'game_duration_football', 'game_duration_hockey',
            'game_duration_baseball', 'game_duration_soccer',
            'cache_enabled', 'cache_duration_hours',
            'xmltv_generator_name', 'xmltv_generator_url',
            'auto_generate_enabled', 'cron_expression',
            'dispatcharr_enabled', 'dispatcharr_url', 'dispatcharr_username',
            'dispatcharr_password', 'dispatcharr_epg_id',
            'channel_create_timing', 'channel_delete_timing', 'include_final_events',
            'event_lookahead_days', 'default_duplicate_event_handling',
            'soccer_cache_refresh_frequency'
        ]

        for field in fields:
            value = request.form.get(field)
            if value is not None:
                # Handle boolean fields (checkboxes)
                if field in ['cache_enabled', 'auto_generate_enabled', 'dispatcharr_enabled']:
                    value = 1 if value == 'on' else 0
                # Handle radio button / select boolean fields (value is '1' or '0')
                elif field in ['show_timezone', 'include_final_events']:
                    value = int(value)
                # Validate timezone before saving
                elif field == 'default_timezone':
                    value = value.strip() if value else 'America/Detroit'
                    try:
                        ZoneInfo(value)  # Validate - raises if invalid
                    except Exception:
                        flash(f'Invalid timezone: "{value}". Timezone names are case-sensitive (e.g., America/Chicago, not America/chicago).', 'error')
                        conn.close()
                        return redirect(url_for('settings_form'))
                # Validate cron expression
                elif field == 'cron_expression':
                    value = value.strip() if value else '0 * * * *'
                    # Basic validation: must have 5 fields
                    parts = value.split()
                    if len(parts) != 5:
                        flash('Invalid cron expression: must have 5 fields (minute hour day month weekday)', 'error')
                        conn.close()
                        return redirect(url_for('settings_form'))
                    # Validate with croniter
                    try:
                        from croniter import croniter
                        croniter(value)  # Raises if invalid
                    except Exception as e:
                        flash(f'Invalid cron expression: {str(e)}', 'error')
                        conn.close()
                        return redirect(url_for('settings_form'))
                # Handle numeric fields
                elif field in ['epg_days_ahead', 'cache_duration_hours', 'dispatcharr_epg_id', 'event_lookahead_days']:
                    value = int(value) if value else None
                    # Validate epg_days_ahead range
                    if field == 'epg_days_ahead' and value and (value < 1 or value > 14):
                        flash('Days to Generate must be between 1 and 14', 'error')
                        return redirect(url_for('settings_form'))
                elif field in ['game_duration_default', 'max_program_hours_default',
                               'game_duration_basketball', 'game_duration_football',
                               'game_duration_hockey', 'game_duration_baseball', 'game_duration_soccer']:
                    value = float(value)
                # Handle empty strings as NULL for optional fields
                elif field in ['dispatcharr_url', 'dispatcharr_username', 'dispatcharr_password']:
                    value = value.strip() if value else None

                cursor.execute(f"UPDATE settings SET {field} = ? WHERE id = 1", (value,))

        conn.commit()
        conn.close()

        flash('Settings updated successfully!', 'success')
    except Exception as e:
        flash(f"Error updating settings: {str(e)}", 'error')

    return redirect(url_for('settings_form'))

# =============================================================================
# DISPATCHARR INTEGRATION
# =============================================================================

@app.route('/api/dispatcharr/test', methods=['POST'])
def dispatcharr_test():
    """Test connection to Dispatcharr and return EPG sources"""
    from api.dispatcharr_client import EPGManager

    data = request.get_json()
    url = data.get('url', '').strip()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not all([url, username, password]):
        return jsonify({
            'success': False,
            'message': 'URL, username, and password are required'
        }), 400

    try:
        manager = EPGManager(url, username, password)
        result = manager.test_connection()
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Dispatcharr test error: {e}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@app.route('/api/dispatcharr/refresh-provider-names', methods=['POST'])
def dispatcharr_refresh_provider_names():
    """Refresh cached M3U provider/account names from Dispatcharr"""
    from api.dispatcharr_client import M3UManager

    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())

    if not settings.get('dispatcharr_enabled'):
        conn.close()
        return jsonify({
            'success': False,
            'message': 'Dispatcharr integration is not enabled'
        }), 400

    url = settings.get('dispatcharr_url')
    username = settings.get('dispatcharr_username')
    password = settings.get('dispatcharr_password')

    if not all([url, username, password]):
        conn.close()
        return jsonify({
            'success': False,
            'message': 'Dispatcharr credentials not configured'
        }), 400

    try:
        manager = M3UManager(url, username, password)
        accounts = manager.list_m3u_accounts()
        account_map = {a['id']: a['name'] for a in accounts}

        # Update all event_epg_groups with current provider names
        cursor = conn.cursor()
        cursor.execute("SELECT id, dispatcharr_account_id, account_name FROM event_epg_groups WHERE dispatcharr_account_id IS NOT NULL")
        groups = cursor.fetchall()

        updated_count = 0
        for group in groups:
            group_id = group[0]
            account_id = group[1]
            old_name = group[2]
            new_name = account_map.get(account_id)

            if new_name and new_name != old_name:
                cursor.execute(
                    "UPDATE event_epg_groups SET account_name = ? WHERE id = ?",
                    (new_name, group_id)
                )
                updated_count += 1

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Refreshed {len(accounts)} provider names, updated {updated_count} records',
            'providers': len(accounts),
            'updated': updated_count
        })

    except Exception as e:
        conn.close()
        app.logger.error(f"Provider name refresh error: {e}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@app.route('/api/dispatcharr/refresh', methods=['POST'])
def dispatcharr_refresh_manual():
    """Manually trigger Dispatcharr EPG refresh"""
    from api.dispatcharr_client import EPGManager

    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    if not settings.get('dispatcharr_enabled'):
        return jsonify({
            'success': False,
            'message': 'Dispatcharr integration is not enabled'
        }), 400

    url = settings.get('dispatcharr_url')
    username = settings.get('dispatcharr_username')
    password = settings.get('dispatcharr_password')
    epg_id = settings.get('dispatcharr_epg_id')

    if not all([url, username, password, epg_id]):
        return jsonify({
            'success': False,
            'message': 'Dispatcharr settings are incomplete'
        }), 400

    try:
        manager = EPGManager(url, username, password)
        result = manager.refresh(epg_id)

        if result['success']:
            # Update last sync time
            conn = get_connection()
            conn.execute(
                "UPDATE settings SET dispatcharr_last_sync = ? WHERE id = 1",
                (datetime.now().isoformat(),)
            )
            conn.commit()
            conn.close()

        return jsonify(result)

    except Exception as e:
        app.logger.error(f"Dispatcharr refresh error: {e}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


# =============================================================================
# EPG GENERATION (Consolidated - all generation uses generate_all_epg())
# =============================================================================

@app.route('/generate', methods=['POST'])
def generate_epg():
    """
    Generate EPG - DEPRECATED: redirects to /generate/stream for full pipeline.
    Kept for API compatibility (JSON clients).
    """
    app.logger.info('🚀 EPG generation requested via POST')

    # For JSON API clients, run synchronously and return result
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        result = generate_all_epg(triggered_by='api')

        if result.get('success'):
            return jsonify({
                'success': True,
                'num_programmes': result.get('total_programmes', 0),
                'num_channels': result.get('total_channels', 0),
                'generation_time': result.get('generation_time', 0),
                'team_stats': result.get('team_stats', {}),
                'event_stats': result.get('event_stats', {}),
                'lifecycle_stats': result.get('lifecycle_stats', {})
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Unknown error')
            }), 500

    # For web UI, redirect to streaming endpoint (better UX with progress)
    flash('EPG generation started. Please wait...', 'info')
    return redirect(url_for('index'))


@app.route('/generate/stream')
def generate_epg_stream():
    """
    Stream EPG generation progress using Server-Sent Events.

    This is the PRIMARY endpoint for EPG generation from the UI.
    Uses generate_all_epg() as the single source of truth for all EPG logic.

    Also updates global generation_status for polling-based clients.
    """
    import threading
    import queue

    global generation_status

    def generate():
        """Generator function for SSE stream"""
        progress_queue = queue.Queue()

        def update_global_status(status, message, percent=None, **extra):
            """Update global status for polling clients"""
            generation_status['status'] = status
            generation_status['message'] = message
            generation_status['percent'] = percent if percent is not None else generation_status['percent']
            generation_status['extra'] = extra

        def progress_callback(status, message, percent=None, **extra):
            """High-level progress callback - puts updates in queue for SSE"""
            data = {
                'status': status,
                'message': message
            }
            if percent is not None:
                data['percent'] = percent
            data.update(extra)
            progress_queue.put(data)
            # Also update global status for polling
            update_global_status(status, message, percent, **extra)

        def team_progress_callback(current, total, team_name, message):
            """Per-team progress callback - scales to 10-45% range"""
            base_percent = 10 + int((current / total) * 35) if total > 0 else 10
            data = {
                'status': 'progress',
                'current': current,
                'total': total,
                'team_name': team_name,
                'message': message,
                'percent': base_percent
            }
            progress_queue.put(data)
            # Also update global status for polling
            update_global_status('progress', message, base_percent, current=current, total=total, team_name=team_name)

        # Container for result from background thread
        result_container = {'result': None, 'done': False}

        def run_generation():
            """Run EPG generation in background thread"""
            try:
                result_container['result'] = generate_all_epg(
                    progress_callback=progress_callback,
                    team_progress_callback=team_progress_callback,
                    save_history=True
                )
            except Exception as e:
                result_container['result'] = {'success': False, 'error': str(e)}
                progress_queue.put({'status': 'error', 'message': str(e)})
                update_global_status('error', str(e))
            finally:
                result_container['done'] = True
                generation_status['in_progress'] = False
                progress_queue.put({'status': '_done'})

        # Mark generation as in progress
        generation_status['in_progress'] = True
        generation_status['status'] = 'starting'
        generation_status['message'] = 'Initializing EPG generation...'
        generation_status['percent'] = 0
        generation_status['extra'] = {}

        # Start generation thread
        generation_thread = threading.Thread(target=run_generation)
        generation_thread.start()

        # Stream progress updates
        while True:
            try:
                data = progress_queue.get(timeout=0.1)

                if data.get('status') == '_done':
                    break

                yield f"data: {json.dumps(data)}\n\n"

            except queue.Empty:
                # Send heartbeat to keep connection alive
                yield f": heartbeat\n\n"

        # Wait for thread to complete
        generation_thread.join()

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })


@app.route('/api/generation/status')
def get_generation_status():
    """
    Get current EPG generation status for polling-based progress.

    Returns JSON with:
    - in_progress: bool - whether generation is running
    - status: str - current status (starting, progress, complete, error, idle)
    - message: str - human-readable message
    - percent: int - progress percentage (0-100)
    - extra: dict - additional data (team_name, current, total, etc.)
    """
    return jsonify(generation_status)

@app.route('/download')
def download_epg():
    """Download generated EPG file"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT epg_output_path FROM settings WHERE id = 1")
        result = cursor.fetchone()
        conn.close()

        output_path = result[0] if result else '/app/data/teamarr.xml'

        if not os.path.exists(output_path):
            app.logger.warning(f'EPG file not found at {output_path}')
            flash('EPG file not found. Generate it first.', 'error')
            return redirect(url_for('index'))

        app.logger.info(f'📥 Downloading EPG file: {output_path}')
        return send_file(output_path, as_attachment=True, download_name='teamarr.xml')
    except Exception as e:
        app.logger.error(f"❌ Error downloading EPG: {str(e)}", exc_info=True)
        flash(f"Error downloading EPG: {str(e)}", 'error')
        return redirect(url_for('index'))

@app.route('/teamarr.xml')
def serve_epg():
    """Serve EPG file for IPTV clients"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT epg_output_path FROM settings WHERE id = 1")
        result = cursor.fetchone()
        conn.close()

        output_path = result[0] if result else '/app/data/teamarr.xml'

        if not os.path.exists(output_path):
            app.logger.warning(f'EPG file not found at {output_path}')
            return "EPG file not found. Generate it first.", 404

        app.logger.info(f'📡 Serving EPG file: {output_path}')
        return send_file(output_path, mimetype='application/xml')
    except Exception as e:
        app.logger.error(f"❌ Error serving EPG: {str(e)}", exc_info=True)
        return f"Error serving EPG: {str(e)}", 500

# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.route('/api/parse-espn-url', methods=['POST'])
def api_parse_espn_url():
    """Parse ESPN URL and fetch team data

    Supports multiple ESPN URL patterns:
    - Pro sports: https://www.espn.com/nba/team/_/name/det/detroit-pistons
    - College sports: https://www.espn.com/college-football/team/_/id/130/michigan-wolverines
    - Soccer clubs: https://www.espn.com/soccer/club/_/id/21422/angel-city-fc
    """
    try:
        url = request.json.get('url', '').strip()

        if not url:
            return jsonify({'success': False, 'message': 'Please provide an ESPN team URL'})

        # Fetch team data from ESPN API (handles URL parsing internally)
        espn_client = ESPNClient()
        team_data = espn_client.get_team_info_from_url(url)

        if not team_data:
            return jsonify({
                'success': False,
                'message': 'Could not fetch team data. Please verify the URL is a valid ESPN team page '
                          '(e.g., espn.com/nba/team/_/name/det/detroit-pistons or '
                          'espn.com/college-football/team/_/id/130/michigan-wolverines)'
            })

        # Fetch default channel ID format from settings and generate suggested channel_id
        conn = get_connection()
        settings = conn.execute("SELECT default_channel_id_format FROM settings WHERE id = 1").fetchone()
        conn.close()

        channel_id_format = settings['default_channel_id_format'] if settings else '{team_name_pascal}.{league_id}'

        # Generate suggested channel ID
        team_data['channel_id'] = _generate_channel_id(
            channel_id_format,
            team_name=team_data.get('team_name', ''),
            team_abbrev=team_data.get('team_abbrev', ''),
            team_slug=team_data.get('team_slug', ''),
            league=team_data.get('league', ''),
            league_name=team_data.get('league_name', ''),
            sport=team_data.get('sport', ''),
            espn_team_id=team_data.get('espn_team_id', '')
        )

        return jsonify({
            'success': True,
            'team': team_data
        })

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/templates', methods=['GET'])
def api_templates_list():
    """Get all templates (for dropdowns)"""
    templates = get_all_templates()
    return jsonify([{
        'id': t['id'],
        'name': t['name'],
        'sport': t['sport'],
        'league': t['league']
    } for t in templates])

@app.route('/api/epg-stats', methods=['GET'])
def api_epg_stats():
    """
    Get EPG generation stats summary.

    This is the single source of truth for all EPG statistics.
    Returns data formatted for dashboard tiles and UI display.
    """
    from database import get_epg_stats_summary
    try:
        summary = get_epg_stats_summary()
        return jsonify({
            'success': True,
            'stats': summary
        })
    except Exception as e:
        app.logger.error(f"Error getting EPG stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/epg-stats/live', methods=['GET'])
def api_epg_stats_live():
    """
    Get live game statistics from the EPG.

    Uses teamarr metadata comments in the XML to identify game events,
    then compares times to current datetime to calculate:
    - games_today: Events scheduled for today
    - live_now: Events currently in progress (started but not ended)

    Query params:
        type: 'team' or 'event' (default: both)
    """
    import xml.etree.ElementTree as ET
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from utils.time_format import format_time, get_time_settings

    try:
        # Get settings for timezone and EPG path
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        epg_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
        user_tz_name = settings.get('default_timezone', 'America/Detroit')
        user_tz = ZoneInfo(user_tz_name)
        time_fmt, show_tz = get_time_settings(settings)
        now = datetime.now(user_tz)
        today = now.date()

        stats = {
            'team': {'games_today': 0, 'live_now': 0, 'today_events': []},
            'event': {'games_today': 0, 'live_now': 0, 'today_events': []}
        }

        # Check if EPG file exists
        if not os.path.exists(epg_path):
            return jsonify({'success': True, 'stats': stats, 'message': 'No EPG file found'})

        # Parse the EPG XML with comments enabled
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(epg_path, parser)
        root = tree.getroot()

        # Parse XMLTV datetime format: YYYYMMDDHHmmss +ZZZZ
        def parse_xmltv_time(time_str):
            if not time_str:
                return None
            try:
                # Handle format like "20251129220000 -0500"
                parts = time_str.split()
                dt_str = parts[0]
                tz_str = parts[1] if len(parts) > 1 else '+0000'

                dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S')

                # Parse timezone offset
                tz_sign = 1 if tz_str[0] == '+' else -1
                tz_hours = int(tz_str[1:3])
                tz_mins = int(tz_str[3:5])
                tz_offset = timedelta(hours=tz_sign * tz_hours, minutes=tz_sign * tz_mins)
                dt = dt.replace(tzinfo=timezone(tz_offset))

                return dt.astimezone(user_tz)
            except Exception:
                return None

        # Process each programme element
        for programme in root.findall('.//programme'):
            # Look for teamarr metadata comment
            teamarr_type = None
            for child in programme:
                if callable(child.tag):  # Comments have callable tag (ET.Comment)
                    comment_text = child.text or ''
                    if comment_text.startswith('teamarr:'):
                        teamarr_type = comment_text[8:]  # Remove 'teamarr:' prefix
                        break

            # Skip if no teamarr metadata or if it's filler
            if not teamarr_type or 'filler' in teamarr_type:
                continue

            # Determine stat key from metadata (teams-event or event-event)
            stat_key = 'team' if teamarr_type == 'teams-event' else 'event'

            start_str = programme.get('start')
            stop_str = programme.get('stop')
            title_elem = programme.find('title')
            title = title_elem.text if title_elem is not None else ''

            start_time = parse_xmltv_time(start_str)
            stop_time = parse_xmltv_time(stop_str)

            if not start_time or not stop_time:
                continue

            start_date = start_time.date()

            # Games Today: events scheduled for today
            if start_date == today:
                stats[stat_key]['games_today'] += 1
                stats[stat_key]['today_events'].append({
                    'title': title,
                    'start': format_time(start_time, time_fmt, show_tz),
                    'start_ts': start_time.timestamp(),  # For sorting
                    'channel': programme.get('channel', '')
                })

                # Live Now: currently in progress
                if start_time <= now <= stop_time:
                    stats[stat_key]['live_now'] += 1

        # Sort events by start time (earliest first)
        for key in ['team', 'event']:
            stats[key]['today_events'].sort(key=lambda e: e.get('start_ts', 0))
            # Remove the sorting key from response
            for event in stats[key]['today_events']:
                event.pop('start_ts', None)

        return jsonify({
            'success': True,
            'stats': stats,
            'current_time': now.strftime('%Y-%m-%d %H:%M:%S %Z')
        })

    except Exception as e:
        app.logger.error(f"Error getting live EPG stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/epg-stats/history', methods=['GET'])
def api_epg_stats_history():
    """
    Get EPG generation history.

    Query params:
        limit: Number of records to return (default: 10, max: 100)
    """
    from database import get_epg_history
    try:
        limit = min(int(request.args.get('limit', 10)), 100)
        history = get_epg_history(limit=limit)
        return jsonify({
            'success': True,
            'history': history,
            'count': len(history)
        })
    except Exception as e:
        app.logger.error(f"Error getting EPG history: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/epg/failed-matches', methods=['GET'])
def api_epg_failed_matches():
    """
    Get failed matches from last EPG generation.

    Returns JSON with failures grouped by group_name, includes generation
    timestamp and total count for the Failed Matches modal.
    """
    from database import get_failed_matches, get_failed_matches_summary
    try:
        # Get summary for header info
        summary = get_failed_matches_summary()

        if summary['generation_id'] is None:
            return jsonify({
                'success': True,
                'has_failures': False,
                'generation_id': None,
                'total_count': 0,
                'groups': [],
                'by_reason': {},
                'timestamp': None
            })

        # Get all failures
        failures = get_failed_matches(summary['generation_id'])

        # Group failures by group_name
        groups = {}
        for failure in failures:
            group_name = failure['group_name']
            if group_name not in groups:
                groups[group_name] = {
                    'group_name': group_name,
                    'group_id': failure['group_id'],
                    'failures': []
                }
            groups[group_name]['failures'].append({
                'stream_id': failure['stream_id'],
                'stream_name': failure['stream_name'],
                'reason': failure['reason'],
                'parsed_team1': failure['parsed_team1'],
                'parsed_team2': failure['parsed_team2'],
                'detection_tier': failure['detection_tier'],
                'leagues_checked': failure['leagues_checked'],
                'detail': failure['detail']
            })

        # Convert to sorted list (by failure count desc)
        groups_list = sorted(
            groups.values(),
            key=lambda g: len(g['failures']),
            reverse=True
        )

        return jsonify({
            'success': True,
            'has_failures': summary['total_count'] > 0,
            'generation_id': summary['generation_id'],
            'total_count': summary['total_count'],
            'group_count': len(groups_list),
            'groups': groups_list,
            'by_reason': summary['by_reason'],
            'timestamp': summary.get('timestamp')
        })

    except Exception as e:
        app.logger.error(f"Error getting failed matches: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/epg/matched-streams', methods=['GET'])
def api_epg_matched_streams():
    """
    Get matched streams from last EPG generation.

    Returns JSON with matches grouped by group_name, includes detection tiers
    and league info for the Matched Streams modal.
    """
    from database import get_matched_streams, get_matched_streams_summary
    try:
        # Get summary for header info
        summary = get_matched_streams_summary()

        if summary['generation_id'] is None:
            return jsonify({
                'success': True,
                'has_matches': False,
                'generation_id': None,
                'total_count': 0,
                'groups': [],
                'by_tier': {},
                'by_league': {},
                'timestamp': None
            })

        # Get all matches
        matches = get_matched_streams(summary['generation_id'])

        # Group matches by group_name
        groups = {}
        for match in matches:
            group_name = match['group_name']
            if group_name not in groups:
                groups[group_name] = {
                    'group_name': group_name,
                    'group_id': match['group_id'],
                    'matches': []
                }
            groups[group_name]['matches'].append({
                'stream_id': match['stream_id'],
                'stream_name': match['stream_name'],
                'event_id': match['event_id'],
                'event_name': match['event_name'],
                'detected_league': match['detected_league'],
                'detection_tier': match['detection_tier'],
                'parsed_team1': match['parsed_team1'],
                'parsed_team2': match['parsed_team2'],
                'home_team': match['home_team'],
                'away_team': match['away_team'],
                'event_date': match['event_date']
            })

        # Convert to sorted list (by match count desc)
        groups_list = sorted(
            groups.values(),
            key=lambda g: len(g['matches']),
            reverse=True
        )

        return jsonify({
            'success': True,
            'has_matches': summary['total_count'] > 0,
            'generation_id': summary['generation_id'],
            'total_count': summary['total_count'],
            'group_count': len(groups_list),
            'groups': groups_list,
            'by_tier': summary['by_tier'],
            'by_league': summary['by_league'],
            'timestamp': summary.get('timestamp')
        })

    except Exception as e:
        app.logger.error(f"Error getting matched streams: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/variables', methods=['GET'])
def api_variables():
    """Get all template variables from variables.json with suffix availability.

    Time variables with sample_utc_by_sport are converted to user's timezone
    and formatted according to user's time_format and show_timezone settings.
    """
    import json
    from datetime import datetime as dt, timezone
    from zoneinfo import ZoneInfo

    variables_path = os.path.join(os.path.dirname(__file__), 'config', 'variables.json')
    try:
        with open(variables_path, 'r', encoding='utf-8') as f:
            variables_data = json.load(f)

        # Get user's timezone and time format settings
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()
        user_tz_name = settings.get('default_timezone', 'America/Detroit')
        user_time_format = settings.get('time_format', '12h')  # '12h' or '24h'
        user_show_tz = settings.get('show_timezone', 1) in (1, '1', True, 'true')

        try:
            user_tz = ZoneInfo(user_tz_name)
        except Exception:
            user_tz = ZoneInfo('America/Detroit')

        # Get timezone abbreviation for display
        now = dt.now(user_tz)
        tz_abbrev = now.strftime('%Z')  # e.g., "EST", "PST", "CDT"

        # Ensure all variables have available_suffixes field (should be in JSON)
        # If missing or blank, default to all three suffixes
        for var in variables_data.get('variables', []):
            if 'available_suffixes' not in var or not var['available_suffixes']:
                # Fallback: allow all three contexts if field is missing or blank
                var['available_suffixes'] = ['base', 'next', 'last']

            # Convert UTC sample times to user's timezone
            if var.get('format') and var['format'].startswith('time') and var.get('sample_utc_by_sport'):
                converted_examples = {}
                for sport, utc_time_str in var['sample_utc_by_sport'].items():
                    try:
                        # Parse UTC time (HH:MM format)
                        hour, minute = map(int, utc_time_str.split(':'))
                        # Create a UTC datetime
                        utc_dt = dt(2025, 1, 15, hour, minute, 0, tzinfo=timezone.utc)
                        # Convert to user's timezone
                        local_dt = utc_dt.astimezone(user_tz)

                        # Format based on variable type using centralized time utilities
                        if var['format'] == 'time_with_tz':
                            # Main game_time variable - honor user's time_format and show_timezone settings
                            converted_examples[sport] = fmt_time(local_dt, user_time_format, user_show_tz)
                        elif var['format'] == 'time_12h':
                            # Explicit 12h format - always 12h, never show timezone
                            converted_examples[sport] = fmt_time(local_dt, '12h', False)
                        elif var['format'] == 'time_24h':
                            # Explicit 24h format - always 24h, never show timezone
                            converted_examples[sport] = fmt_time(local_dt, '24h', False)
                        else:
                            converted_examples[sport] = fmt_time(local_dt, '12h', False)
                    except Exception as e:
                        # Fall back to original example if conversion fails
                        app.logger.warning(f"Time conversion failed for {var.get('name')}/{sport}: {e}")
                        converted_examples[sport] = var.get('examples_by_sport', {}).get(sport, utc_time_str)

                # Replace examples_by_sport with converted values
                var['examples_by_sport'] = converted_examples

        return jsonify(variables_data)
    except Exception as e:
        app.logger.error(f"Failed to load variables.json: {e}")
        return jsonify({'error': 'Failed to load variables', 'total_variables': 0, 'variables': []}), 500

@app.route('/api/condition-presets', methods=['GET'])
def api_condition_presets_list():
    """Get all condition presets"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, description, condition_type, condition_value, priority, template, usage_count
        FROM condition_presets
        WHERE active = 1
        ORDER BY name
    """)
    presets = []
    for row in cursor.fetchall():
        presets.append({
            'id': row[0],
            'name': row[1],
            'description': row[2],
            'condition_type': row[3],
            'condition_value': row[4],
            'priority': row[5],
            'template': row[6],
            'usage_count': row[7]
        })
    conn.close()
    return jsonify(presets)

@app.route('/api/condition-presets', methods=['POST'])
def api_condition_preset_create():
    """Create a new condition preset"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['name', 'condition_type', 'template']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO condition_presets (name, description, condition_type, condition_value, priority, template)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data['name'],
            data.get('description', ''),
            data['condition_type'],
            data.get('condition_value', ''),
            int(data.get('priority', 50)),
            data['template']
        ))

        conn.commit()
        preset_id = cursor.lastrowid
        conn.close()

        app.logger.info(f"Created condition preset: {data['name']} (ID: {preset_id})")
        return jsonify({'success': True, 'id': preset_id}), 201

    except Exception as e:
        app.logger.error(f"Failed to create condition preset: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/condition-presets/<int:preset_id>', methods=['DELETE'])
def api_condition_preset_delete(preset_id):
    """Delete a condition preset"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE condition_presets SET active = 0 WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()
    app.logger.info(f"Deactivated condition preset {preset_id}")
    return jsonify({'success': True})

@app.route('/api/leagues', methods=['GET'])
def api_leagues_list():
    """Get all available leagues from league_config"""
    conn = get_connection()
    cursor = conn.cursor()
    results = cursor.execute("""
        SELECT league_code, league_name, sport, api_path, logo_url
        FROM league_config
        WHERE active = 1
        ORDER BY sport, league_name
    """).fetchall()
    conn.close()

    leagues = [{'code': row[0], 'name': row[1], 'sport': row[2], 'api_path': row[3], 'logo': row[4]} for row in results]
    return jsonify({'leagues': leagues})

@app.route('/api/leagues/<league_code>/conferences', methods=['GET'])
def api_league_conferences(league_code):
    """Fetch all conferences for a college league"""
    try:
        # Get league config
        conn = get_connection()
        cursor = conn.cursor()
        league_info = cursor.execute("""
            SELECT sport, api_path, league_name
            FROM league_config
            WHERE league_code = ?
        """, (league_code,)).fetchone()
        conn.close()

        if not league_info:
            return jsonify({'error': 'League not found'}), 404

        sport = league_info[0]
        api_path = league_info[1]
        league_name = league_info[2]

        # Extract the league identifier from api_path
        league_identifier = api_path.split('/')[-1]

        # Fetch conferences from ESPN API
        espn = ESPNClient()
        conferences_data = espn.get_league_conferences(sport, league_identifier)

        if not conferences_data:
            return jsonify({'conferences': []})

        return jsonify({
            'league': {'code': league_code, 'name': league_name, 'sport': sport},
            'conferences': conferences_data
        })

    except Exception as e:
        app.logger.error(f"Error fetching conferences for league {league_code}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/leagues/<league_code>/conferences/batch', methods=['GET'])
def api_league_conferences_batch(league_code):
    """
    Fetch all conferences WITH their teams in a SINGLE API call.

    Much faster than fetching conferences then teams per conference.
    Also includes "Other Teams" for independents not in any conference.

    Returns:
        {
            "league": {...},
            "conferences": [
                {"name": "Big 12", "teams": [...]},
                {"name": "SEC", "teams": [...]},
                {"name": "— Other Teams —", "teams": [...]}  // independents
            ],
            "total_teams": 362
        }
    """
    try:
        # Get league config
        conn = get_connection()
        cursor = conn.cursor()
        league_info = cursor.execute("""
            SELECT sport, api_path, league_name
            FROM league_config
            WHERE league_code = ?
        """, (league_code,)).fetchone()
        conn.close()

        if not league_info:
            return jsonify({'error': 'League not found'}), 404

        sport = league_info[0]
        api_path = league_info[1]
        league_name = league_info[2]
        league_identifier = api_path.split('/')[-1]

        espn = ESPNClient()

        # Use the helper function that handles everything
        conferences = espn.get_all_teams_by_conference(sport, league_identifier)

        if not conferences:
            # Fall back to flat team list for non-college or on error
            all_teams = espn.get_league_teams(sport, league_identifier)
            if all_teams:
                conferences = [{
                    'name': 'All Teams',
                    'teams': sorted(all_teams, key=lambda x: x.get('name', ''))
                }]
            else:
                return jsonify({'conferences': [], 'total_teams': 0})

        total_teams = sum(len(c['teams']) for c in conferences)

        return jsonify({
            'league': {'code': league_code, 'name': league_name, 'sport': sport},
            'conferences': conferences,
            'total_teams': total_teams
        })

    except Exception as e:
        app.logger.error(f"Error fetching conferences batch for {league_code}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/leagues/<league_code>/teams', methods=['GET'])
def api_league_teams(league_code):
    """Fetch all teams for a given league from ESPN API"""
    try:
        # Check if requesting teams for a specific conference
        conference_id = request.args.get('conference')

        # Get league config
        conn = get_connection()
        cursor = conn.cursor()
        league_info = cursor.execute("""
            SELECT sport, api_path, league_name
            FROM league_config
            WHERE league_code = ?
        """, (league_code,)).fetchone()
        conn.close()

        if not league_info:
            return jsonify({'error': 'League not found'}), 404

        sport = league_info[0]
        api_path = league_info[1]
        league_name = league_info[2]

        # Fetch teams from ESPN API using the api_path (not league_code)
        # For example: 'eng.1' for EPL, not 'epl'
        espn = ESPNClient()

        # Extract the league identifier from api_path (e.g., 'soccer/eng.1' -> 'eng.1')
        league_identifier = api_path.split('/')[-1]

        # If conference ID provided, fetch teams for that conference
        if conference_id:
            teams_data = espn.get_conference_teams(sport, league_identifier, int(conference_id))
        else:
            teams_data = espn.get_league_teams(sport, league_identifier)

        if not teams_data:
            return jsonify({'error': 'Failed to fetch teams from ESPN'}), 500

        return jsonify({
            'league': {'code': league_code, 'name': league_name, 'sport': sport},
            'teams': teams_data
        })

    except Exception as e:
        app.logger.error(f"Error fetching teams for league {league_code}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/teams/imported', methods=['GET'])
def api_teams_imported():
    """Get all imported team IDs grouped by league"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        results = cursor.execute("""
            SELECT league, espn_team_id
            FROM teams
            WHERE espn_team_id IS NOT NULL
        """).fetchall()
        conn.close()

        # Group by league
        imported = {}
        for row in results:
            league = row[0]
            team_id = row[1]
            if league not in imported:
                imported[league] = []
            imported[league].append(team_id)

        return jsonify({'imported': imported})

    except Exception as e:
        app.logger.error(f"Error fetching imported teams: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/teams/bulk-import', methods=['POST'])
def api_teams_bulk_import():
    """Bulk import teams from ESPN API data"""
    try:
        data = request.get_json()
        league_code = data.get('league_code')
        sport = data.get('sport')
        teams = data.get('teams', [])

        if not league_code or not sport or not teams:
            return jsonify({'error': 'Missing required fields'}), 400

        # Fetch default channel ID format from settings
        conn = get_connection()
        settings = conn.execute("SELECT default_channel_id_format FROM settings WHERE id = 1").fetchone()
        conn.close()

        channel_id_format = settings['default_channel_id_format'] if settings else '{team_name_pascal}.{league_id}'

        imported_count = 0
        skipped_count = 0
        errors = []

        for team_data in teams:
            try:
                # Generate channel ID using the format from settings
                team_name = team_data.get('name', '')
                team_abbrev = team_data.get('abbreviation', '')
                team_slug = team_data.get('slug', '')
                espn_team_id = str(team_data.get('id', ''))

                # Build channel ID from format
                channel_id = _generate_channel_id(
                    channel_id_format,
                    team_name=team_name,
                    team_abbrev=team_abbrev,
                    team_slug=team_slug,
                    league=league_code,
                    league_name='',  # Not available in bulk import, will use league.upper() as fallback
                    sport=sport,
                    espn_team_id=espn_team_id
                )

                # Check if team already exists
                conn = get_connection()
                existing = conn.execute(
                    "SELECT id FROM teams WHERE espn_team_id = ? AND league = ?",
                    (str(team_data['id']), league_code)
                ).fetchone()
                conn.close()

                if existing:
                    skipped_count += 1
                    app.logger.info(f"Skipped {team_name} - already exists")
                    continue

                # Create team
                new_team = {
                    'espn_team_id': str(team_data['id']),
                    'league': league_code,
                    'sport': sport,
                    'team_name': team_name,
                    'team_abbrev': team_data.get('abbreviation'),
                    'team_slug': team_data.get('slug'),
                    'team_logo_url': team_data.get('logo'),
                    'team_color': team_data.get('color'),
                    'channel_id': channel_id,
                    'template_id': None,  # User assigns template later
                    'active': 1
                }

                create_team(new_team)
                imported_count += 1
                app.logger.info(f"✅ Imported {team_name}")

            except Exception as e:
                errors.append(f"{team_data.get('name', 'Unknown')}: {str(e)}")
                app.logger.error(f"Failed to import {team_data.get('name')}: {e}")

        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'errors': errors
        })

    except Exception as e:
        app.logger.error(f"Bulk import failed: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# SOCCER MULTI-LEAGUE CACHE API ENDPOINTS
# =============================================================================

@app.route('/api/soccer/cache/status', methods=['GET'])
def api_soccer_cache_status():
    """Get soccer multi-league cache status and statistics."""
    from epg.soccer_multi_league import SoccerMultiLeague

    try:
        stats = SoccerMultiLeague.get_cache_stats()
        return jsonify({
            'success': True,
            'last_refresh': stats.last_refresh.isoformat() if stats.last_refresh else None,
            'leagues_processed': stats.leagues_processed,
            'teams_indexed': stats.teams_indexed,
            'refresh_duration_seconds': stats.refresh_duration,
            'is_stale': stats.is_stale,
            'staleness_days': stats.staleness_days,
            'is_empty': SoccerMultiLeague.is_cache_empty()
        })
    except Exception as e:
        app.logger.error(f"Error getting soccer cache status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/soccer/cache/refresh', methods=['POST'])
def api_soccer_cache_refresh():
    """Manually trigger soccer multi-league cache refresh."""
    from epg.soccer_multi_league import SoccerMultiLeague

    try:
        result = SoccerMultiLeague.refresh_cache()
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error refreshing soccer cache: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/soccer/team/<team_id>/leagues', methods=['GET'])
def api_soccer_team_leagues(team_id):
    """Get all leagues for a soccer team from the cache."""
    from epg.soccer_multi_league import SoccerMultiLeague

    try:
        info = SoccerMultiLeague.get_team_info(team_id)
        if not info:
            return jsonify({'error': 'Team not found in soccer cache'}), 404

        # Get full league info for each
        leagues_info = []
        for slug in info.leagues:
            league = SoccerMultiLeague.get_league_info(slug)
            if league:
                leagues_info.append({
                    'slug': league.slug,
                    'name': league.name,
                    'abbrev': league.abbrev,
                    'tags': league.tags,
                    'category': league.category,  # Legacy
                    'logo_url': league.logo_url
                })
            else:
                leagues_info.append({
                    'slug': slug,
                    'name': slug,
                    'abbrev': '',
                    'tags': [],
                    'category': 'unknown',
                    'logo_url': ''
                })

        # Fetch authoritative default league from ESPN (not cached)
        default_league = None
        if info.leagues:
            default_league = SoccerMultiLeague.get_team_default_league(team_id, info.leagues[0])

        return jsonify({
            'success': True,
            'team_id': info.team_id,
            'team_name': info.team_name,
            'team_type': info.team_type,
            'default_league': default_league,
            'leagues': leagues_info,
            'league_count': len(leagues_info)
        })
    except Exception as e:
        app.logger.error(f"Error getting soccer team leagues: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# TEAM-LEAGUE CACHE API ENDPOINTS (Non-Soccer Sports)
# =============================================================================

@app.route('/api/cache/team-league/status', methods=['GET'])
def api_team_league_cache_status():
    """Get team-league cache status and statistics."""
    from epg.team_league_cache import TeamLeagueCache

    try:
        stats = TeamLeagueCache.get_cache_stats()
        return jsonify({
            'success': True,
            'last_refresh': stats.last_refresh.isoformat() if stats.last_refresh else None,
            'leagues_processed': stats.leagues_processed,
            'teams_indexed': stats.teams_indexed,
            'is_stale': stats.is_stale,
            'staleness_days': stats.staleness_days
        })
    except Exception as e:
        app.logger.error(f"Error getting team-league cache status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cache/team-league/refresh', methods=['POST'])
def api_team_league_cache_refresh():
    """Manually trigger team-league cache refresh."""
    from epg.team_league_cache import TeamLeagueCache

    try:
        result = TeamLeagueCache.refresh_cache()
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error refreshing team-league cache: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cache/status', methods=['GET'])
def api_combined_cache_status():
    """Get combined status for both soccer and team-league caches."""
    from epg.soccer_multi_league import SoccerMultiLeague
    from epg.team_league_cache import TeamLeagueCache

    try:
        soccer_stats = SoccerMultiLeague.get_cache_stats()
        team_stats = TeamLeagueCache.get_cache_stats()

        return jsonify({
            'success': True,
            'soccer': {
                'last_refresh': soccer_stats.last_refresh.isoformat() if soccer_stats.last_refresh else None,
                'leagues_processed': soccer_stats.leagues_processed,
                'teams_indexed': soccer_stats.teams_indexed,
                'is_stale': soccer_stats.is_stale,
                'staleness_days': soccer_stats.staleness_days
            },
            'team_league': {
                'last_refresh': team_stats.last_refresh.isoformat() if team_stats.last_refresh else None,
                'leagues_processed': team_stats.leagues_processed,
                'teams_indexed': team_stats.teams_indexed,
                'is_stale': team_stats.is_stale,
                'staleness_days': team_stats.staleness_days
            }
        })
    except Exception as e:
        app.logger.error(f"Error getting combined cache status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cache/refresh-all', methods=['POST'])
def api_refresh_all_caches():
    """Refresh both soccer and team-league caches."""
    from epg.soccer_multi_league import SoccerMultiLeague
    from epg.team_league_cache import TeamLeagueCache

    try:
        soccer_result = SoccerMultiLeague.refresh_cache()
        team_result = TeamLeagueCache.refresh_cache()

        return jsonify({
            'success': soccer_result.get('success', False) and team_result.get('success', False),
            'soccer': soccer_result,
            'team_league': team_result
        })
    except Exception as e:
        app.logger.error(f"Error refreshing caches: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cache/team-league/lookup', methods=['GET'])
def api_team_league_lookup():
    """Find candidate leagues for a team pair."""
    from epg.team_league_cache import TeamLeagueCache

    team1 = request.args.get('team1', '')
    team2 = request.args.get('team2', '')

    if not team1:
        return jsonify({'error': 'team1 parameter required'}), 400

    try:
        # Single team lookup
        if not team2:
            leagues = TeamLeagueCache.get_leagues_for_team(team1)
            return jsonify({
                'success': True,
                'team': team1,
                'leagues': list(leagues)
            })

        # Team pair lookup - find intersection
        candidates = TeamLeagueCache.find_candidate_leagues(team1, team2)
        return jsonify({
            'success': True,
            'team1': team1,
            'team2': team2,
            'candidate_leagues': candidates
        })
    except Exception as e:
        app.logger.error(f"Error in team-league lookup: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# EVENT EPG API ENDPOINTS
# =============================================================================

def _get_m3u_manager():
    """Get M3UManager instance with credentials from settings."""
    from api.dispatcharr_client import M3UManager

    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    url = settings.get('dispatcharr_url')
    username = settings.get('dispatcharr_username')
    password = settings.get('dispatcharr_password')

    if not all([url, username, password]):
        return None

    return M3UManager(url, username, password)


@app.route('/api/event-epg/dispatcharr/accounts', methods=['GET'])
def api_event_epg_dispatcharr_accounts():
    """List M3U accounts from Dispatcharr."""
    try:
        manager = _get_m3u_manager()
        if not manager:
            return jsonify({'error': 'Dispatcharr credentials not configured'}), 400

        accounts = manager.list_m3u_accounts()

        # Filter out "Custom" M3U accounts - they're auto-created by Dispatcharr
        accounts = [a for a in accounts if 'custom' not in a.get('name', '').lower()]

        # Sort alphabetically by name
        accounts.sort(key=lambda a: a.get('name', '').lower())

        return jsonify({'accounts': accounts})

    except Exception as e:
        app.logger.error(f"Error fetching M3U accounts: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/dispatcharr/groups', methods=['GET'])
def api_event_epg_dispatcharr_groups():
    """
    List channel groups from Dispatcharr.

    Query params:
        account_id: Filter by M3U account ID
        search: Filter by group name (substring match)
    """
    try:
        manager = _get_m3u_manager()
        if not manager:
            return jsonify({'error': 'Dispatcharr credentials not configured'}), 400

        account_id = request.args.get('account_id', type=int)
        search = request.args.get('search', '')

        groups = manager.list_channel_groups(search=search if search else None)

        # If account_id provided, filter groups that belong to that account
        if account_id:
            # Get the account to find which groups it contains
            account = manager.get_account(account_id)
            if account:
                # Extract group IDs from the account's channel_groups list
                account_group_ids = {cg['channel_group'] for cg in account.get('channel_groups', [])}
                groups = [g for g in groups if g['id'] in account_group_ids]
            else:
                groups = []  # Account not found

        # Enrich with enabled status from our database
        enabled_groups = {g['dispatcharr_group_id']: g for g in get_all_event_epg_groups()}
        for group in groups:
            db_group = enabled_groups.get(group['id'])
            group['event_epg_enabled'] = db_group is not None
            group['event_epg_id'] = db_group['id'] if db_group else None
            group['assigned_league'] = db_group['assigned_league'] if db_group else None

        return jsonify({'groups': groups})

    except Exception as e:
        app.logger.error(f"Error fetching channel groups: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/dispatcharr/groups/<int:group_id>/streams', methods=['GET'])
def api_event_epg_dispatcharr_streams(group_id):
    """
    Preview streams in a Dispatcharr group.

    Returns streams with team matching preview.
    If the group is configured in our database, refreshes M3U first.

    Query params:
        limit: Max streams to return (default: no limit)
        match: If 'true', attempt to match teams and find ESPN events
    """
    try:
        manager = _get_m3u_manager()
        if not manager:
            return jsonify({'error': 'Dispatcharr credentials not configured'}), 400

        limit = request.args.get('limit', None, type=int)
        do_match = request.args.get('match', 'false').lower() == 'true'

        # Check if group is configured in our database - if so, refresh M3U first
        db_group = get_event_epg_group_by_dispatcharr_id(group_id)
        if db_group and db_group.get('dispatcharr_account_id'):
            app.logger.debug(f"Refreshing M3U account {db_group['dispatcharr_account_id']} before fetching streams")
            refresh_result = manager.wait_for_refresh(db_group['dispatcharr_account_id'], timeout=180)
            if not refresh_result.get('success'):
                app.logger.warning(f"M3U refresh failed: {refresh_result.get('message')} - continuing with potentially stale data")

        # Get group info and streams
        result = manager.get_group_with_streams(group_id, stream_limit=limit)
        if not result:
            return jsonify({'error': 'Group not found'}), 404

        streams = result['streams']
        filtered_count = 0

        # If matching requested, add team extraction preview
        if do_match:
            # Get assigned league from query params or from existing config
            league = request.args.get('league')
            if not league:
                # Check if group is already configured
                db_group = get_event_epg_group_by_dispatcharr_id(group_id)
                if db_group:
                    league = db_group['assigned_league']

            if league:
                from epg.team_matcher import create_matcher
                from epg.event_matcher import create_event_matcher
                from utils.stream_filter import has_game_indicator

                # Fetch settings to get include_final_events and lookahead preferences
                conn = get_connection()
                settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
                conn.close()
                include_final_events = bool(settings.get('include_final_events', 0))
                lookahead_days = settings.get('event_lookahead_days', 7)

                team_matcher = create_matcher()
                event_matcher = create_event_matcher(lookahead_days=lookahead_days)

                # Get filtering settings from db_group if configured
                skip_builtin_filter = bool(db_group.get('skip_builtin_filter', 0)) if db_group else False
                from utils.regex_helper import compile_group_filters
                include_regex, exclude_regex = compile_group_filters(db_group)

                for stream in streams:
                    try:
                        stream_name = stream['name']

                        # Check if stream is filtered/excluded
                        is_filtered = False
                        filter_reason = None

                        if not skip_builtin_filter and not has_game_indicator(stream_name):
                            is_filtered = True
                            filter_reason = get_display_text(FilterReason.NO_GAME_INDICATOR)
                        elif include_regex and not include_regex.search(stream_name):
                            is_filtered = True
                            filter_reason = get_display_text(FilterReason.INCLUDE_REGEX_NOT_MATCHED)
                        elif exclude_regex and exclude_regex.search(stream_name):
                            is_filtered = True
                            filter_reason = get_display_text(FilterReason.EXCLUDE_REGEX_MATCHED)

                        if is_filtered:
                            filtered_count += 1
                            stream['team_match'] = {
                                'matched': False,
                                'filtered': True,
                                'reason': filter_reason
                            }
                            stream['event_match'] = {'found': False, 'filtered': True}
                            continue

                        # Extract teams from stream name
                        team_result = team_matcher.extract_teams(stream_name, league)
                        stream['team_match'] = team_result

                        # If teams matched, try to find ESPN event
                        if team_result.get('matched'):
                            event_result = event_matcher.find_event(
                                team_result['away_team_id'],
                                team_result['home_team_id'],
                                league,
                                game_date=team_result.get('game_date'),
                                game_time=team_result.get('game_time'),
                                include_final_events=include_final_events
                            )

                            # Build event match response with status info
                            event_match_data = {
                                'found': event_result['found'],
                                'event_id': event_result.get('event_id'),
                                'event_name': event_result.get('event', {}).get('name') if event_result['found'] else None,
                                'event_date': event_result.get('event_date')
                            }

                            # Add reason for not found, with better messages
                            if not event_result['found']:
                                reason = event_result.get('reason', 'No event found')
                                # Use normalize_reason() to handle both enum and string values
                                reason_str = normalize_reason(reason)

                                # Set flags and display text based on reason
                                if reason_str == 'event_final':
                                    event_match_data['is_final'] = True
                                    event_match_data['reason'] = get_display_text(FilteredReason.EVENT_FINAL)
                                elif reason_str == 'event_past':
                                    event_match_data['is_past'] = True
                                    event_match_data['reason'] = get_display_text(FilteredReason.EVENT_PAST)
                                elif reason_str == 'no_event_found':
                                    event_match_data['reason'] = get_display_text(FailedReason.NO_EVENT_FOUND, lookahead_days)
                                else:
                                    event_match_data['reason'] = get_display_text(reason)

                            stream['event_match'] = event_match_data
                        else:
                            stream['event_match'] = {'found': False, 'reason': team_result.get('reason')}

                    except Exception as e:
                        stream['team_match'] = {'matched': False, 'reason': str(e)}
                        stream['event_match'] = {'found': False}

        # Sort streams alphabetically by name
        streams.sort(key=lambda s: s.get('name', '').lower())

        return jsonify({
            'group': result['group'],
            'streams': streams,
            'total_streams': result['total_streams'],
            'filtered_count': filtered_count
        })

    except Exception as e:
        app.logger.error(f"Error fetching streams for group {group_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/dispatcharr/groups/<int:group_id>/streams/stream', methods=['GET'])
def api_event_epg_dispatcharr_streams_sse(group_id):
    """
    Stream preview of streams in a Dispatcharr group with progress updates.

    Uses Server-Sent Events to report progress during M3U refresh and matching.
    Returns stream data incrementally for real-time UI updates.

    Query params:
        limit: Max streams to process (default: no limit)
        league: League code for team matching
    """
    import threading
    import queue
    from concurrent.futures import ThreadPoolExecutor

    # Capture request args NOW, before generator executes (request context gone during streaming)
    limit = request.args.get('limit', None, type=int)
    league = request.args.get('league')

    def generate():
        """Generator function for SSE stream"""
        nonlocal league  # Allow modification from captured outer scope
        progress_queue = queue.Queue()

        def send_progress(status, message, **extra):
            """Send a progress update"""
            data = {'status': status, 'message': message}
            data.update(extra)
            progress_queue.put(data)

        def run_preview():
            """Run preview in background thread"""
            nonlocal league  # Allow modification of league from outer scope
            try:
                # Step 1: Check credentials
                send_progress('progress', 'Connecting to Dispatcharr...', percent=5)
                manager = _get_m3u_manager()
                if not manager:
                    send_progress('error', 'Dispatcharr credentials not configured')
                    return

                # Step 2: Check if we need to refresh M3U
                db_group = get_event_epg_group_by_dispatcharr_id(group_id)
                if db_group:
                    if not league:
                        league = db_group.get('assigned_league')

                    if db_group.get('dispatcharr_account_id'):
                        send_progress('progress', 'Refreshing M3U provider...', percent=10)
                        refresh_result = manager.wait_for_refresh(db_group['dispatcharr_account_id'], timeout=180)
                        if not refresh_result.get('success'):
                            app.logger.warning(f"M3U refresh failed: {refresh_result.get('message')} - continuing with potentially stale data")

                # Step 3: Fetch streams
                send_progress('progress', 'Fetching streams...', percent=40)
                result = manager.get_group_with_streams(group_id, stream_limit=limit)
                if not result:
                    send_progress('error', 'Group not found')
                    return

                streams = result['streams']
                filtered_count = 0

                # Step 4: Match teams if league specified or multi-sport group
                is_multi_sport = bool(db_group.get('is_multi_sport')) if db_group else False

                if league or is_multi_sport:
                    from epg.team_matcher import create_matcher
                    from epg.event_matcher import create_event_matcher
                    from utils.stream_filter import has_game_indicator
                    from epg.stream_match_cache import StreamMatchCache, refresh_cached_event, get_generation_counter

                    # Initialize fingerprint cache for test modal
                    # Test modal reads from cache (benefits from EPG runs) and writes with current generation
                    stream_cache = StreamMatchCache(get_connection)
                    current_generation = get_generation_counter(get_connection)
                    internal_group_id = db_group.get('id') if db_group else 0

                    # Fetch settings
                    conn = get_connection()
                    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
                    conn.close()
                    include_final_events = bool(settings.get('include_final_events', 0))
                    lookahead_days = settings.get('event_lookahead_days', 7)

                    # Get filtering settings
                    skip_builtin_filter = bool(db_group.get('skip_builtin_filter', 0)) if db_group else False
                    from utils.regex_helper import compile_group_filters
                    include_regex, exclude_regex = compile_group_filters(db_group)

                    # Check for custom regex configuration (same logic as refresh_event_group_core)
                    teams_enabled = bool(db_group.get('custom_regex_teams_enabled')) if db_group else False
                    date_enabled = bool(db_group.get('custom_regex_date_enabled')) if db_group else False
                    time_enabled = bool(db_group.get('custom_regex_time_enabled')) if db_group else False
                    any_custom_enabled = teams_enabled or date_enabled or time_enabled

                    # Multi-sport specific setup
                    enabled_leagues = []
                    soccer_enabled = False
                    if is_multi_sport:
                        import json
                        raw_leagues = json.loads(db_group.get('enabled_leagues') or '[]')
                        # Handle soccer_all marker
                        if 'soccer_all' in raw_leagues:
                            soccer_enabled = True
                            enabled_leagues = [l for l in raw_leagues if l != 'soccer_all']
                        else:
                            enabled_leagues = raw_leagues

                        # Normalize aliases to ESPN slugs (e.g., 'ncaawh' -> 'womens-college-hockey')
                        from database import normalize_league_codes
                        enabled_leagues = normalize_league_codes(enabled_leagues)

                    send_progress('progress', f'Matching {len(streams)} streams...', percent=50)

                    def match_single_stream_single(stream):
                        """Match a single stream to single league - called in parallel"""
                        # Create matchers per-thread for thread safety
                        thread_team_matcher = create_matcher()
                        thread_event_matcher = create_event_matcher(lookahead_days=lookahead_days)

                        try:
                            stream_name = stream['name']
                            stream_id = stream.get('id', 0)

                            # Check if stream is filtered/excluded
                            if not skip_builtin_filter and not has_game_indicator(stream_name):
                                return {
                                    'stream': stream,
                                    'filtered': True,
                                    'filter_reason': get_display_text(FilterReason.NO_GAME_INDICATOR)
                                }
                            if include_regex and not include_regex.search(stream_name):
                                return {
                                    'stream': stream,
                                    'filtered': True,
                                    'filter_reason': get_display_text(FilterReason.INCLUDE_REGEX_NOT_MATCHED)
                                }
                            if exclude_regex and exclude_regex.search(stream_name):
                                return {
                                    'stream': stream,
                                    'filtered': True,
                                    'filter_reason': get_display_text(FilterReason.EXCLUDE_REGEX_MATCHED)
                                }

                            # Check fingerprint cache first
                            cached = stream_cache.get(internal_group_id, stream_id, stream_name)
                            if cached:
                                event_id, cached_league, cached_data = cached
                                # Refresh dynamic fields from ESPN
                                refreshed = refresh_cached_event(
                                    thread_event_matcher.espn,
                                    cached_data,
                                    cached_league,
                                    get_connection
                                )
                                if refreshed:
                                    # Touch cache to update last_seen_generation
                                    stream_cache.touch(internal_group_id, stream_id, stream_name, current_generation)
                                    return {
                                        'stream': stream,
                                        'team_result': refreshed.get('team_result', {}),
                                        'event_result': {
                                            'found': True,
                                            'event': refreshed.get('event'),
                                            'event_id': event_id
                                        },
                                        'detected_league': cached_league,
                                        'detection_tier': 'cache',
                                        'from_cache': True
                                    }

                            # Extract teams - use custom regex if configured (same logic as refresh_event_group_core)
                            if any_custom_enabled:
                                team_result = thread_team_matcher.extract_teams_with_selective_regex(
                                    stream_name,
                                    league,
                                    teams_pattern=db_group.get('custom_regex_teams'),
                                    teams_enabled=teams_enabled,
                                    date_pattern=db_group.get('custom_regex_date'),
                                    date_enabled=date_enabled,
                                    time_pattern=db_group.get('custom_regex_time'),
                                    time_enabled=time_enabled
                                )
                            else:
                                team_result = thread_team_matcher.extract_teams(stream_name, league)

                            # If teams matched, find ESPN event
                            if team_result.get('matched'):
                                event_result = thread_event_matcher.find_event(
                                    team_result['away_team_id'],
                                    team_result['home_team_id'],
                                    league,
                                    game_date=team_result.get('game_date'),
                                    game_time=team_result.get('game_time'),
                                    include_final_events=include_final_events
                                )

                                # If no game found, try alternate team combinations (disambiguation)
                                if not event_result.get('found'):
                                    raw_away = team_result.get('raw_away', '')
                                    raw_home = team_result.get('raw_home', '')

                                    if raw_away and raw_home:
                                        all_away_teams = thread_team_matcher.get_all_matching_teams(raw_away, league, max_results=5)
                                        all_home_teams = thread_team_matcher.get_all_matching_teams(raw_home, league, max_results=5)

                                        tried_pairs = {(team_result['away_team_id'], team_result['home_team_id'])}

                                        for away_candidate in all_away_teams:
                                            for home_candidate in all_home_teams:
                                                pair = (away_candidate['id'], home_candidate['id'])
                                                if pair in tried_pairs:
                                                    continue
                                                tried_pairs.add(pair)

                                                alt_result = thread_event_matcher.find_event(
                                                    away_candidate['id'],
                                                    home_candidate['id'],
                                                    league,
                                                    game_date=team_result.get('game_date'),
                                                    game_time=team_result.get('game_time'),
                                                    include_final_events=include_final_events
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
                                                    event_result = alt_result
                                                    break
                                            if event_result.get('found'):
                                                break

                                # Cache successful match for future EPG runs
                                if event_result.get('found') and event_result.get('event'):
                                    event = event_result['event']
                                    event_id = event.get('id')
                                    if event_id:
                                        cached_data = {
                                            'event': event,
                                            'team_result': team_result
                                        }
                                        stream_cache.set(
                                            internal_group_id, stream_id, stream_name,
                                            event_id, league, cached_data, current_generation
                                        )

                                return {
                                    'stream': stream,
                                    'team_result': team_result,
                                    'event_result': event_result,
                                    'detected_league': league,
                                    'detection_tier': 'direct'
                                }
                            else:
                                return {
                                    'stream': stream,
                                    'team_result': team_result,
                                    'event_result': None,
                                    'detected_league': league,
                                    'detection_tier': 'direct'
                                }

                        except Exception as e:
                            return {
                                'stream': stream,
                                'error': str(e)
                            }

                    def match_single_stream_multi(stream):
                        """Match a single stream using multi-sport league detection - called in parallel.

                        Uses consolidated MultiSportMatcher with tiered detection:
                          Tier 1: League indicator + Teams → Direct match
                          Tier 2: Sport indicator + Teams → Match within sport's leagues
                          Tier 3a-c: Cache lookup + schedule disambiguation
                          Tier 4a-b: Single-team schedule fallback (NAIA vs NCAA)
                        """
                        from epg.league_detector import LeagueDetector
                        from epg.multi_sport_matcher import MultiSportMatcher, MatcherConfig

                        stream_name = stream['name']

                        # Pre-filter streams (test modal does this inline)
                        if not skip_builtin_filter and not has_game_indicator(stream_name):
                            return {
                                'stream': stream,
                                'filtered': True,
                                'filter_reason': get_display_text(FilterReason.NO_GAME_INDICATOR)
                            }
                        if include_regex and not include_regex.search(stream_name):
                            return {
                                'stream': stream,
                                'filtered': True,
                                'filter_reason': get_display_text(FilterReason.INCLUDE_REGEX_NOT_MATCHED)
                            }
                        if exclude_regex and exclude_regex.search(stream_name):
                            return {
                                'stream': stream,
                                'filtered': True,
                                'filter_reason': get_display_text(FilterReason.EXCLUDE_REGEX_MATCHED)
                            }

                        stream_id = stream.get('id', 0)

                        # Check fingerprint cache first
                        cached = stream_cache.get(internal_group_id, stream_id, stream_name)
                        if cached:
                            # Create minimal matcher just for ESPN client access
                            thread_event_matcher = create_event_matcher(lookahead_days=lookahead_days)
                            event_id, cached_league, cached_data = cached
                            # Refresh dynamic fields from ESPN
                            refreshed = refresh_cached_event(
                                thread_event_matcher.espn,
                                cached_data,
                                cached_league,
                                get_connection
                            )
                            if refreshed:
                                # Touch cache to update last_seen_generation
                                stream_cache.touch(internal_group_id, stream_id, stream_name, current_generation)
                                return {
                                    'stream': stream,
                                    'team_result': refreshed.get('team_result', {}),
                                    'event_result': {
                                        'found': True,
                                        'event': refreshed.get('event'),
                                        'event_id': event_id
                                    },
                                    'detected_league': cached_league,
                                    'detection_tier': 'cache',
                                    'from_cache': True
                                }

                        # Create per-thread instances
                        thread_team_matcher = create_matcher()
                        thread_event_matcher = create_event_matcher(lookahead_days=lookahead_days)
                        thread_league_detector = LeagueDetector(
                            espn_client=thread_event_matcher.espn,
                            enabled_leagues=enabled_leagues,
                            lookahead_days=lookahead_days
                        )

                        # Configure the matcher
                        config = MatcherConfig(
                            enabled_leagues=enabled_leagues,
                            soccer_enabled=soccer_enabled,
                            custom_regex_teams=db_group.get('custom_regex_teams'),
                            custom_regex_teams_enabled=teams_enabled,
                            custom_regex_date=db_group.get('custom_regex_date'),
                            custom_regex_date_enabled=date_enabled,
                            custom_regex_time=db_group.get('custom_regex_time'),
                            custom_regex_time_enabled=time_enabled,
                            include_final_events=include_final_events
                        )

                        matcher = MultiSportMatcher(
                            team_matcher=thread_team_matcher,
                            event_matcher=thread_event_matcher,
                            league_detector=thread_league_detector,
                            config=config
                        )

                        # Run the consolidated matching logic
                        result = matcher.match_stream(stream)

                        # Convert to test modal return format
                        if result.error:
                            return {'stream': stream, 'error': result.error_message}

                        if result.matched:
                            # Build event_result dict from the enriched event
                            event_result = {
                                'found': True,
                                'event': result.event,
                                'event_id': result.event.get('id') if result.event else None
                            }

                            # Cache successful match for future EPG runs
                            if result.event and result.event.get('id'):
                                event_id = result.event.get('id')
                                detected_league = result.detected_league or ''
                                cached_data = {
                                    'event': result.event,
                                    'team_result': result.team_result
                                }
                                stream_cache.set(
                                    internal_group_id, stream_id, stream_name,
                                    event_id, detected_league, cached_data, current_generation
                                )

                            return {
                                'stream': stream,
                                'team_result': result.team_result,
                                'event_result': event_result,
                                'detected_league': result.detected_league,
                                'detection_tier': result.detection_tier
                            }
                        else:
                            # Not matched - build team_result with failure info
                            if result.parsed_teams:
                                team_result = {
                                    'matched': False,
                                    'reason': result.reason,
                                    'detail': result.detail,
                                    'parsed_teams': result.parsed_teams
                                }
                            elif result.reason == 'NO_LEAGUE_DETECTED':
                                team_result = {'matched': False, 'reason': 'NO_LEAGUE_DETECTED'}
                            else:
                                team_result = {'matched': False, 'reason': result.reason or 'NO_TEAMS'}

                            # If we have team_result from matcher but event wasn't found
                            if result.team_result:
                                team_result = result.team_result
                                team_result['matched'] = True  # Teams matched, just no event
                                event_result = {'found': False, 'reason': result.reason}
                                return {
                                    'stream': stream,
                                    'team_result': team_result,
                                    'event_result': event_result,
                                    'detected_league': result.detected_league,
                                    'detection_tier': result.detection_tier
                                }

                            # Include league_not_enabled info if present
                            return_dict = {
                                'stream': stream,
                                'team_result': team_result,
                                'event_result': None,
                                'detected_league': result.detected_league,
                                'detection_tier': result.detection_tier
                            }
                            if result.league_not_enabled:
                                return_dict['league_not_enabled'] = True
                                return_dict['league_name'] = result.league_name
                            return return_dict

                    # Select the appropriate matcher
                    match_func = match_single_stream_multi if is_multi_sport else match_single_stream_single

                    # Process streams in parallel (max 100 workers)
                    match_results = []
                    if streams:
                        with ThreadPoolExecutor(max_workers=min(len(streams), 100)) as executor:
                            match_results = list(executor.map(match_func, streams))

                    # Process results
                    for match_data in match_results:
                        stream = match_data['stream']

                        if match_data.get('filtered'):
                            filtered_count += 1
                            stream['team_match'] = {
                                'matched': False,
                                'filtered': True,
                                'reason': match_data['filter_reason']
                            }
                            stream['event_match'] = {'found': False, 'filtered': True}

                        elif match_data.get('error'):
                            stream['team_match'] = {'matched': False, 'reason': match_data['error']}
                            stream['event_match'] = {'found': False}

                        else:
                            team_result = match_data.get('team_result', {})
                            event_result = match_data.get('event_result')
                            # Convert FilterReason constants to display text for team_match
                            # Include league_name for LEAGUE_NOT_ENABLED reason
                            if team_result.get('reason'):
                                league_name = match_data.get('league_name')
                                team_result['reason'] = get_display_text(
                                    team_result['reason'],
                                    league_name=league_name
                                )
                            stream['team_match'] = team_result

                            if event_result:
                                event_match_data = {
                                    'found': event_result['found'],
                                    'event_id': event_result.get('event_id'),
                                    'event_name': event_result.get('event', {}).get('name') if event_result['found'] else None,
                                    'event_date': event_result.get('event_date')
                                }

                                if not event_result['found']:
                                    reason = event_result.get('reason', 'No event found')
                                    # Use normalize_reason() to handle both enum and string values
                                    reason_str = normalize_reason(reason)
                                    if reason_str == 'event_final':
                                        event_match_data['is_final'] = True
                                        event_match_data['reason'] = get_display_text(FilteredReason.EVENT_FINAL)
                                    elif reason_str == 'event_past':
                                        event_match_data['is_past'] = True
                                        event_match_data['reason'] = get_display_text(FilteredReason.EVENT_PAST)
                                    elif reason_str == 'no_event_found':
                                        event_match_data['reason'] = get_display_text(FailedReason.NO_EVENT_FOUND, lookahead_days)
                                    else:
                                        event_match_data['reason'] = get_display_text(reason)

                                stream['event_match'] = event_match_data
                            else:
                                # Convert FilterReason constants to display text
                                raw_reason = team_result.get('reason')
                                display_reason = get_display_text(raw_reason) if raw_reason else 'No event found'
                                stream['event_match'] = {'found': False, 'reason': display_reason}

                # Sort streams alphabetically
                streams.sort(key=lambda s: s.get('name', '').lower())

                # Build custom regex info for UI display
                custom_regex_info = None
                if league and db_group:
                    exclude_enabled = bool(db_group.get('stream_exclude_regex_enabled')) and bool(db_group.get('stream_exclude_regex'))
                    # Show info if any custom pattern is enabled
                    if any_custom_enabled or exclude_enabled:
                        custom_regex_info = {
                            'teams': teams_enabled,
                            'date': date_enabled,
                            'time': time_enabled,
                            'exclude': exclude_enabled
                        }

                # Send final result with custom regex indicator
                send_progress('complete', 'Preview complete', percent=100,
                              group=result['group'],
                              streams=streams,
                              total_streams=result['total_streams'],
                              filtered_count=filtered_count,
                              using_custom_regex=any_custom_enabled if league else False,
                              custom_regex_info=custom_regex_info)

            except Exception as e:
                app.logger.error(f"Error in preview stream for group {group_id}: {e}")
                send_progress('error', str(e))
            finally:
                progress_queue.put({'status': '_done'})

        # Start preview thread
        preview_thread = threading.Thread(target=run_preview)
        preview_thread.start()

        # JSON encoder that handles datetime objects
        def json_serial(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        # Stream progress updates
        while True:
            try:
                data = progress_queue.get(timeout=0.1)
                if data.get('status') == '_done':
                    break
                yield f"data: {json.dumps(data, default=json_serial)}\n\n"
            except queue.Empty:
                yield f": heartbeat\n\n"

        preview_thread.join()

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })


# =============================================================================
# Channel Groups API (for assigning managed channels to groups)
# =============================================================================

@app.route('/api/dispatcharr/channel-groups', methods=['GET'])
def api_dispatcharr_channel_groups():
    """
    Get all channel groups from Dispatcharr.

    Query params:
        exclude_m3u: If 'true', exclude groups that originated from M3U accounts
        search: Filter by group name (case-insensitive substring)
    """
    try:
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_url'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        from api.dispatcharr_client import ChannelManager
        channel_mgr = ChannelManager(
            settings['dispatcharr_url'],
            settings.get('dispatcharr_username', ''),
            settings.get('dispatcharr_password', '')
        )

        exclude_m3u = request.args.get('exclude_m3u', 'false').lower() == 'true'
        search = request.args.get('search', '').strip().lower()

        groups = channel_mgr.get_channel_groups(exclude_m3u=exclude_m3u)

        # Apply search filter
        if search:
            groups = [g for g in groups if search in g.get('name', '').lower()]

        # Format response
        formatted = [{
            'id': g.get('id'),
            'name': g.get('name', ''),
            'm3u_account_count': g.get('m3u_account_count', 0),
            'channel_count': g.get('channel_count', 0),
            'is_m3u_group': bool(g.get('m3u_account_count', 0))
        } for g in groups]

        return jsonify({
            'groups': formatted,
            'count': len(formatted)
        })

    except Exception as e:
        app.logger.error(f"Error fetching channel groups: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/dispatcharr/channel-groups', methods=['POST'])
def api_dispatcharr_channel_groups_create():
    """
    Create a new channel group in Dispatcharr.

    Body:
        name: str (required) - Group name
    """
    try:
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_url'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'error': 'Group name is required'}), 400

        from api.dispatcharr_client import ChannelManager
        channel_mgr = ChannelManager(
            settings['dispatcharr_url'],
            settings.get('dispatcharr_username', ''),
            settings.get('dispatcharr_password', '')
        )

        result = channel_mgr.create_channel_group(data['name'])

        if result.get('success'):
            app.logger.info(f"Created channel group: {data['name']} (ID: {result.get('group_id')})")
            return jsonify({
                'success': True,
                'group_id': result.get('group_id'),
                'group': result.get('group')
            }), 201
        else:
            return jsonify({'error': result.get('error', 'Failed to create group')}), 400

    except Exception as e:
        app.logger.error(f"Error creating channel group: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/dispatcharr/stream-profiles', methods=['GET'])
def api_dispatcharr_stream_profiles():
    """
    Get all stream profiles from Dispatcharr.

    Query params:
        active_only: If 'true', only return active profiles
    """
    from api.dispatcharr_client import ChannelManager

    try:
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_url'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        channel_mgr = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )

        active_only = request.args.get('active_only', 'false').lower() == 'true'
        profiles = channel_mgr.get_stream_profiles(active_only=active_only)

        return jsonify({
            'success': True,
            'profiles': profiles
        })

    except Exception as e:
        app.logger.error(f"Error fetching stream profiles: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/dispatcharr/stream-profiles', methods=['POST'])
def api_dispatcharr_stream_profiles_create():
    """
    Create a new stream profile in Dispatcharr.

    Body:
        name: str (required) - Profile name
        command: str (optional) - Command to execute
        parameters: str (optional) - Command-line parameters
    """
    from api.dispatcharr_client import ChannelManager

    try:
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_url'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'error': 'Profile name is required'}), 400

        channel_mgr = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )

        result = channel_mgr.create_stream_profile(
            name=data['name'],
            command=data.get('command', ''),
            parameters=data.get('parameters', ''),
            is_active=data.get('is_active', True)
        )

        if result.get('success'):
            app.logger.info(f"Created stream profile: {data['name']} (ID: {result.get('profile_id')})")
            return jsonify({
                'success': True,
                'profile_id': result.get('profile_id'),
                'profile': result.get('profile')
            }), 201
        else:
            return jsonify({'error': result.get('error', 'Failed to create profile')}), 400

    except Exception as e:
        app.logger.error(f"Error creating stream profile: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/dispatcharr/channel-profiles', methods=['GET'])
def api_dispatcharr_channel_profiles():
    """
    Get all channel profiles from Dispatcharr.

    Channel profiles group channels together for organization/filtering.
    """
    from api.dispatcharr_client import ChannelManager

    try:
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_url'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        channel_mgr = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )

        profiles = channel_mgr.get_channel_profiles()

        return jsonify({
            'success': True,
            'profiles': profiles
        })

    except Exception as e:
        app.logger.error(f"Error fetching channel profiles: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/dispatcharr/channel-profiles', methods=['POST'])
def api_dispatcharr_channel_profiles_create():
    """
    Create a new channel profile in Dispatcharr.

    Body:
        name: str (required) - Profile name
    """
    from api.dispatcharr_client import ChannelManager

    try:
        data = request.get_json()
        name = data.get('name', '').strip() if data else ''

        if not name:
            return jsonify({'error': 'Profile name is required'}), 400

        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_url'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        channel_mgr = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )

        result = channel_mgr.create_channel_profile(name)

        if result.get('success'):
            return jsonify({
                'success': True,
                'profile': result.get('profile'),
                'profile_id': result.get('profile_id')
            })
        else:
            return jsonify({'error': result.get('error', 'Failed to create profile')}), 400

    except Exception as e:
        app.logger.error(f"Error creating channel profile: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/groups', methods=['GET'])
def api_event_epg_groups_list():
    """List all event EPG groups configured in Teamarr."""
    try:
        enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'
        groups = get_all_event_epg_groups(enabled_only=enabled_only)
        return jsonify({'groups': groups})
    except Exception as e:
        app.logger.error(f"Error listing event EPG groups: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/groups', methods=['POST'])
def api_event_epg_groups_create():
    """
    Create/enable a new event EPG group.

    Body:
        dispatcharr_group_id: int (required)
        dispatcharr_account_id: int (required)
        group_name: str (required) - exact name from Dispatcharr
        assigned_league: str (required for independent groups, inherited for child groups)
        assigned_sport: str (required for independent groups, inherited for child groups)
        parent_group_id: int (optional) - if set, creates a child group that inherits settings
        event_template_id: int (optional, must be an event template)
    """
    try:
        data = request.get_json()

        # Check for parent group - child groups inherit settings from parent
        parent_group_id = data.get('parent_group_id')
        if parent_group_id:
            parent = get_event_epg_group(parent_group_id)
            if not parent:
                return jsonify({'error': 'Parent group not found'}), 404
            # Child groups inherit sport/league from parent
            data['assigned_sport'] = parent['assigned_sport']
            data['assigned_league'] = parent['assigned_league']
            # Child groups don't have their own template or channel settings (inherited at runtime)
            data['event_template_id'] = None
            data['channel_start'] = None
            data['channel_group_id'] = None
            data['channel_group_name'] = None
            data['stream_profile_id'] = None
            data['channel_profile_id'] = None
            data['channel_profile_ids'] = None

        required = ['dispatcharr_group_id', 'dispatcharr_account_id', 'group_name', 'assigned_league', 'assigned_sport']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400

        # Validate channel_start doesn't exceed Dispatcharr's max (only for independent groups)
        channel_start = data.get('channel_start')
        if channel_start is not None and channel_start > 9999:
            return jsonify({'error': 'Channel start cannot exceed 9999 (Dispatcharr limit)'}), 400

        # Check if already exists
        existing = get_event_epg_group_by_dispatcharr_id(data['dispatcharr_group_id'])
        if existing:
            return jsonify({'error': 'Group already configured', 'existing_id': existing['id']}), 409

        # Validate template type - only 'event' templates can be assigned (not for child groups)
        event_template_id = data.get('event_template_id')
        if event_template_id is not None and not parent_group_id:
            template = get_template(event_template_id)
            if not template:
                return jsonify({'error': 'Template not found'}), 404
            if template.get('template_type', 'team') != 'event':
                return jsonify({
                    'error': 'Cannot assign team template to event group. Use an event template.',
                    'template_type': template.get('template_type', 'team')
                }), 400

        # Note: channel_create_timing and channel_delete_timing use global settings
        group_id = create_event_epg_group(
            dispatcharr_group_id=data['dispatcharr_group_id'],
            dispatcharr_account_id=data['dispatcharr_account_id'],
            group_name=data['group_name'],
            assigned_league=data['assigned_league'],
            assigned_sport=data['assigned_sport'],
            event_template_id=data.get('event_template_id'),  # Will be None for child groups
            account_name=data.get('account_name'),
            channel_start=data.get('channel_start'),  # Will be None for child groups
            channel_group_id=data.get('channel_group_id'),
            channel_group_name=data.get('channel_group_name'),
            stream_profile_id=data.get('stream_profile_id'),
            channel_profile_ids=data.get('channel_profile_ids'),
            custom_regex_teams=data.get('custom_regex_teams'),
            custom_regex_teams_enabled=bool(data.get('custom_regex_teams_enabled')),
            custom_regex_date=data.get('custom_regex_date'),
            custom_regex_date_enabled=bool(data.get('custom_regex_date_enabled')),
            custom_regex_time=data.get('custom_regex_time'),
            custom_regex_time_enabled=bool(data.get('custom_regex_time_enabled')),
            stream_include_regex=data.get('stream_include_regex'),
            stream_include_regex_enabled=bool(data.get('stream_include_regex_enabled')),
            stream_exclude_regex=data.get('stream_exclude_regex'),
            stream_exclude_regex_enabled=bool(data.get('stream_exclude_regex_enabled')),
            skip_builtin_filter=bool(data.get('skip_builtin_filter')),
            parent_group_id=data.get('parent_group_id'),
            is_multi_sport=bool(data.get('is_multi_sport')),
            enabled_leagues=data.get('enabled_leagues'),
            channel_sort_order=data.get('channel_sort_order', 'time'),
            overlap_handling=data.get('overlap_handling', 'add_stream')
        )

        app.logger.info(f"Created event EPG group: {data['group_name']} (ID: {group_id})")

        return jsonify({
            'success': True,
            'id': group_id,
            'message': f"Event EPG group '{data['group_name']}' created"
        }), 201

    except Exception as e:
        app.logger.error(f"Error creating event EPG group: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/groups/<int:group_id>', methods=['GET'])
def api_event_epg_groups_get(group_id):
    """Get a specific event EPG group."""
    try:
        group = get_event_epg_group(group_id)
        if not group:
            return jsonify({'error': 'Group not found'}), 404
        return jsonify(group)
    except Exception as e:
        app.logger.error(f"Error fetching event EPG group {group_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/groups/<int:group_id>', methods=['PATCH'])
def api_event_epg_groups_update(group_id):
    """
    Update an event EPG group.

    Body (all optional):
        assigned_league: str
        assigned_sport: str
        enabled: bool
        event_template_id: int (must be an event template, not team template)
        channel_start: int (if changed, deletes existing channels to recreate at new range)
        channel_group_id: int (if changed, updates existing channels to new group)
    """
    try:
        data = request.get_json()

        group = get_event_epg_group(group_id)
        if not group:
            return jsonify({'error': 'Group not found'}), 404

        # Validate template type - only 'event' templates can be assigned to event groups
        if 'event_template_id' in data and data['event_template_id'] is not None:
            template = get_template(data['event_template_id'])
            if not template:
                return jsonify({'error': 'Template not found'}), 404
            if template.get('template_type', 'team') != 'event':
                return jsonify({
                    'error': 'Cannot assign team template to event group. Use an event template.',
                    'template_type': template.get('template_type', 'team')
                }), 400

        # Validate channel_start doesn't exceed Dispatcharr's max
        if 'channel_start' in data and data['channel_start'] is not None and data['channel_start'] > 9999:
            return jsonify({'error': 'Channel start cannot exceed 9999 (Dispatcharr limit)'}), 400

        # Convert enabled to int if present
        if 'enabled' in data:
            data['enabled'] = 1 if data['enabled'] else 0

        # Remove timing fields - these use global settings only
        data.pop('channel_create_timing', None)
        data.pop('channel_delete_timing', None)

        # Track changes that require channel management
        old_channel_start = group.get('channel_start')
        old_channel_group_id = group.get('channel_group_id')
        new_channel_start = data.get('channel_start')
        new_channel_group_id = data.get('channel_group_id')

        # Handle channel_start change - delete old channels to recreate at new range
        channel_start_changed = (
            'channel_start' in data and
            new_channel_start != old_channel_start
        )

        # Handle channel_group_id change - update existing channels in Dispatcharr
        channel_group_changed = (
            'channel_group_id' in data and
            new_channel_group_id != old_channel_group_id
        )

        if update_event_epg_group(group_id, data):
            result = {'success': True, 'message': 'Group updated'}

            # If channel_start changed, delete all existing managed channels
            # They will be recreated at new channel numbers on next EPG generation
            if channel_start_changed:
                try:
                    from database import get_managed_channels_for_group
                    from epg.channel_lifecycle import get_lifecycle_manager

                    channels = get_managed_channels_for_group(group_id)
                    deleted_count = 0

                    if channels:
                        lifecycle_mgr = get_lifecycle_manager()
                        if lifecycle_mgr:
                            for channel in channels:
                                delete_result = lifecycle_mgr.delete_managed_channel(
                                    channel,
                                    reason='channel_start changed'
                                )
                                if delete_result.get('success'):
                                    deleted_count += 1
                                else:
                                    app.logger.warning(f"Failed to delete channel {channel['channel_name']}: {delete_result.get('error')}")

                            app.logger.info(f"Channel start changed for group {group_id}: deleted {deleted_count} channels (will recreate at {new_channel_start})")
                            result['channels_deleted'] = deleted_count
                            result['note'] = f'Deleted {deleted_count} channels. New channels will be created at {new_channel_start} on next EPG generation.'

                except Exception as e:
                    app.logger.error(f"Error handling channel_start change: {e}")
                    result['channel_warning'] = f'Group updated but channel cleanup failed: {e}'

            # If channel_group_id changed, update existing channels in Dispatcharr
            elif channel_group_changed and not channel_start_changed:
                try:
                    from database import get_managed_channels_for_group
                    channels = get_managed_channels_for_group(group_id)
                    updated_count = 0

                    if channels and new_channel_group_id:
                        # Get Dispatcharr settings for API calls
                        conn = get_connection()
                        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
                        conn.close()

                        from api.dispatcharr_client import ChannelManager
                        channel_mgr = ChannelManager(
                            settings['dispatcharr_url'],
                            settings.get('dispatcharr_username', ''),
                            settings.get('dispatcharr_password', '')
                        )

                        for channel in channels:
                            try:
                                # Update channel group in Dispatcharr
                                channel_mgr.update_channel(
                                    channel['dispatcharr_channel_id'],
                                    {'channel_group': new_channel_group_id}
                                )
                                updated_count += 1
                            except Exception as e:
                                app.logger.warning(f"Failed to update channel group for {channel['channel_name']}: {e}")

                        app.logger.info(f"Channel group changed for group {group_id}: updated {updated_count} channels to group {new_channel_group_id}")
                        result['channels_updated'] = updated_count

                except Exception as e:
                    app.logger.error(f"Error handling channel_group_id change: {e}")
                    result['channel_warning'] = f'Group updated but channel group reassignment failed: {e}'

            return jsonify(result)
        else:
            return jsonify({'error': 'Update failed'}), 500

    except Exception as e:
        app.logger.error(f"Error updating event EPG group {group_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/groups/<int:group_id>', methods=['DELETE'])
def api_event_epg_groups_delete(group_id):
    """
    Delete an event EPG group and its associated managed channels.

    When a group is DELETED (not disabled):
    - All associated managed channels are immediately deleted from Dispatcharr
    - Channels are marked as deleted in Teamarr database
    - Group record is deleted
    - EPG will be cleaned up at next generation
    """
    from database import get_managed_channels_for_group
    from epg.channel_lifecycle import get_lifecycle_manager

    try:
        group = get_event_epg_group(group_id)
        if not group:
            return jsonify({'error': 'Group not found'}), 404

        group_name = group['group_name']
        channels_deleted = 0
        channels_failed = 0

        # Delete all associated managed channels IMMEDIATELY using unified method
        managed_channels = get_managed_channels_for_group(group_id)
        if managed_channels:
            app.logger.info(f"Deleting {len(managed_channels)} managed channels for group '{group_name}'...")

            lifecycle_mgr = get_lifecycle_manager()
            if lifecycle_mgr:
                for channel in managed_channels:
                    delete_result = lifecycle_mgr.delete_managed_channel(
                        channel,
                        reason=f"group '{group_name}' deleted"
                    )
                    if delete_result.get('success'):
                        channels_deleted += 1
                    else:
                        channels_failed += 1
                        app.logger.warning(f"Failed to delete channel '{channel['channel_name']}': {delete_result.get('error')}")
            else:
                app.logger.warning("Could not get lifecycle manager - channels may be orphaned in Dispatcharr")

        # Delete the group record
        if delete_event_epg_group(group_id):
            message = f"Group '{group_name}' deleted"
            if channels_deleted > 0:
                message += f" with {channels_deleted} channel(s)"
            if channels_failed > 0:
                message += f" ({channels_failed} channel deletion(s) failed)"

            app.logger.info(f"Deleted event EPG group: {group_name} (ID: {group_id})")
            return jsonify({
                'success': True,
                'message': message,
                'channels_deleted': channels_deleted,
                'channels_failed': channels_failed
            })
        else:
            return jsonify({'error': 'Delete failed'}), 500

    except Exception as e:
        app.logger.error(f"Error deleting event EPG group {group_id}: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# CONSOLIDATION EXCEPTION KEYWORDS API (Global Settings)
# =============================================================================

@app.route('/api/settings/exception-keywords', methods=['GET'])
def api_get_exception_keywords():
    """Get all global exception keywords."""
    from database import get_consolidation_exception_keywords

    keywords = get_consolidation_exception_keywords()
    return jsonify({'keywords': keywords})


@app.route('/api/settings/exception-keywords', methods=['POST'])
def api_add_exception_keyword():
    """Add a new global exception keyword entry."""
    from database import add_consolidation_exception_keyword

    data = request.get_json() or {}
    keywords = (data.get('keywords') or '').strip()
    behavior = data.get('behavior', 'consolidate')

    if not keywords:
        return jsonify({'error': 'keywords is required'}), 400

    if behavior not in ('consolidate', 'separate', 'ignore'):
        return jsonify({'error': 'behavior must be consolidate, separate, or ignore'}), 400

    try:
        new_id = add_consolidation_exception_keyword(keywords, behavior)
        app.logger.info(f"Added global exception keyword '{keywords}' ({behavior})")
        return jsonify({'success': True, 'id': new_id})
    except Exception as e:
        app.logger.error(f"Error adding exception keyword: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/exception-keywords/<int:keyword_id>', methods=['PUT'])
def api_update_exception_keyword(keyword_id):
    """Update an exception keyword entry."""
    from database import update_consolidation_exception_keyword

    data = request.get_json() or {}

    keywords = data.get('keywords')
    behavior = data.get('behavior')

    if behavior and behavior not in ('consolidate', 'separate', 'ignore'):
        return jsonify({'error': 'behavior must be consolidate, separate, or ignore'}), 400

    try:
        updated = update_consolidation_exception_keyword(keyword_id, keywords, behavior)
        if updated:
            app.logger.info(f"Updated exception keyword {keyword_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Keyword not found'}), 404
    except Exception as e:
        app.logger.error(f"Error updating exception keyword: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/exception-keywords/<int:keyword_id>', methods=['DELETE'])
def api_delete_exception_keyword(keyword_id):
    """Delete an exception keyword entry."""
    from database import delete_consolidation_exception_keyword

    try:
        deleted = delete_consolidation_exception_keyword(keyword_id)
        if deleted:
            app.logger.info(f"Deleted exception keyword {keyword_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Keyword not found'}), 404
    except Exception as e:
        app.logger.error(f"Error deleting exception keyword: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/groups/<int:group_id>/test-regex', methods=['POST'])
def api_event_epg_test_regex(group_id):
    """
    Test custom regex patterns against streams in a group.

    Request body:
        {
            "teams_pattern": "(?P<team1>\\w+)\\s*@\\s*(?P<team2>\\w+)",  // required
            "date_pattern": "(\\d{1,2}/\\d{1,2})",  // optional
            "time_pattern": "(\\d{1,2}:\\d{2}(?:AM|PM)?)",  // optional
            "exclude_pattern": "...",  // optional
            "limit": 5  // optional, default tests all streams
        }

    Returns results for each stream showing:
        - Whether regex matched
        - Extracted team1/team2 values
        - Whether teams resolved to ESPN IDs
    """
    from epg.team_matcher import create_matcher
    from utils.regex_helper import compile_pattern, validate_pattern

    try:
        group = get_event_epg_group(group_id)
        if not group:
            return jsonify({'error': 'Group not found'}), 404

        data = request.get_json() or {}
        limit = data.get('limit')  # None = test all streams

        # Handle None values explicitly (can happen if frontend sends null)
        teams_pattern = (data.get('teams_pattern') or '').strip()
        date_pattern = (data.get('date_pattern') or '').strip() or None
        time_pattern = (data.get('time_pattern') or '').strip() or None
        exclude_pattern = (data.get('exclude_pattern') or '').strip() or None

        if not teams_pattern:
            return jsonify({'error': 'teams_pattern is required'}), 400

        # Validate teams pattern has required named groups
        if '(?P<team1>' not in teams_pattern or '(?P<team2>' not in teams_pattern:
            return jsonify({
                'error': 'teams_pattern must contain named groups: (?P<team1>...) and (?P<team2>...)'
            }), 400

        # Validate all provided patterns (uses regex module for advanced pattern support)
        for name, pattern in [('teams_pattern', teams_pattern),
                              ('date_pattern', date_pattern), ('time_pattern', time_pattern),
                              ('exclude_pattern', exclude_pattern)]:
            if pattern:
                is_valid, error_msg = validate_pattern(pattern)
                if not is_valid:
                    return jsonify({'error': f'Invalid {name} syntax: {error_msg}'}), 400

        # Get streams for this group from Dispatcharr
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        from api.dispatcharr_client import M3UManager
        m3u = M3UManager(
            settings['dispatcharr_url'],
            settings.get('dispatcharr_username', ''),
            settings.get('dispatcharr_password', '')
        )

        streams = m3u.list_streams(group_id=group['dispatcharr_group_id'])
        if not streams:
            return jsonify({'error': 'No streams found for this group'}), 404

        if limit:
            streams = streams[:limit]

        # Compile exclude pattern if provided
        exclude_regex = compile_pattern(exclude_pattern) if exclude_pattern else None

        # Test regex against each stream
        team_matcher = create_matcher()
        league = group['assigned_league']
        results = []
        excluded_count = 0

        for stream in streams:
            stream_name = stream.get('name', '')

            # Check exclusion pattern first
            if exclude_regex and exclude_regex.search(stream_name):
                results.append({
                    'stream_name': stream_name,
                    'matched': False,
                    'excluded': True,
                    'raw_team1': None,
                    'raw_team2': None,
                    'resolved_team1': None,
                    'resolved_team2': None,
                    'game_date': None,
                    'game_time': None,
                    'error': 'Excluded by exclusion pattern'
                })
                excluded_count += 1
                continue

            test_result = team_matcher.extract_teams_with_combined_regex(
                stream_name, league, teams_pattern, date_pattern, time_pattern
            )

            results.append({
                'stream_name': stream_name,
                'matched': test_result['matched'],
                'excluded': False,
                'raw_team1': test_result.get('raw_away'),
                'raw_team2': test_result.get('raw_home'),
                'resolved_team1': test_result.get('away_team_name'),
                'resolved_team2': test_result.get('home_team_name'),
                'game_date': str(test_result.get('game_date')) if test_result.get('game_date') else None,
                'game_time': str(test_result.get('game_time')) if test_result.get('game_time') else None,
                'error': test_result.get('reason') if not test_result['matched'] else None
            })

        # Summary stats
        matched_count = sum(1 for r in results if r['matched'])

        return jsonify({
            'success': True,
            'league': league,
            'tested': len(results),
            'matched': matched_count,
            'excluded': excluded_count,
            'results': results
        })

    except Exception as e:
        app.logger.error(f"Error testing regex for group {group_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/refresh/<int:group_id>', methods=['POST'])
def api_event_epg_refresh(group_id):
    """
    Trigger refresh and EPG generation for an event EPG group.

    Uses the shared refresh_event_group_core() function for consistency
    with scheduled generation. Always waits for M3U refresh to complete.
    """
    try:
        group = get_event_epg_group(group_id)
        if not group:
            return jsonify({'error': 'Group not found'}), 404

        manager = _get_m3u_manager()
        if not manager:
            return jsonify({'error': 'Dispatcharr credentials not configured'}), 400

        app.logger.info(f"Refreshing event EPG group {group_id}: {group['group_name']}")

        # Use the shared core function
        result = refresh_event_group_core(group, manager)

        # Build API response from core function result
        stream_count = result.get('stream_count', 0)
        matched_count = result.get('matched_count', 0)
        match_rate = f"{(matched_count / stream_count * 100):.1f}%" if stream_count else "0%"

        if not result.get('success'):
            # Handle errors
            if result.get('error') == 'No event template assigned to this group':
                return jsonify({
                    'success': False,
                    'group_id': group_id,
                    'stream_count': stream_count,
                    'matched_count': matched_count,
                    'match_rate': match_rate,
                    'message': 'No event template assigned to this group',
                    'error': 'Cannot refresh: No template assigned. Please assign an event template first.'
                })
            elif result.get('step') == 'refresh':
                return jsonify({
                    'error': result.get('error'),
                    'step': 'refresh'
                }), 500
            elif result.get('step') == 'generate':
                return jsonify({
                    'error': result.get('error'),
                    'step': 'generate',
                    'matched_count': matched_count
                }), 500
            else:
                return jsonify({
                    'error': result.get('error', 'Unknown error'),
                    'step': result.get('step', 'unknown')
                }), 500

        # Success case
        if matched_count > 0:
            # Update last refresh timestamp
            update_event_epg_group_last_refresh(group_id)

            epg_result = result.get('epg_result', {})
            channel_results = result.get('channel_results')

            response = {
                'success': True,
                'group_id': group_id,
                'stream_count': stream_count,
                'matched_count': matched_count,
                'match_rate': match_rate,
                'programmes_generated': result.get('programmes_generated', 0),
                'epg_file': epg_result.get('file_path') if epg_result else None,
                'message': f"Generated EPG for {matched_count} of {stream_count} streams"
            }

            # Add channel lifecycle info if available
            if channel_results:
                response['channels'] = {
                    'created': len(channel_results.get('created', [])),
                    'existing': len(channel_results.get('existing', [])),
                    'skipped': len(channel_results.get('skipped', [])),
                    'errors': len(channel_results.get('errors', []))
                }
                if channel_results.get('created'):
                    response['message'] += f", created {len(channel_results['created'])} channels"

            return jsonify(response)
        else:
            return jsonify({
                'success': True,
                'group_id': group_id,
                'stream_count': stream_count,
                'matched_count': 0,
                'match_rate': "0%",
                'message': "No streams matched - EPG not generated"
            })

    except Exception as e:
        app.logger.error(f"Error refreshing event EPG group {group_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/refresh/<int:group_id>/stream', methods=['GET'])
def api_event_epg_refresh_stream(group_id):
    """
    SSE endpoint for refreshing event EPG with progress updates.
    Returns Server-Sent Events with progress messages.
    """
    def generate_progress():
        try:
            group = get_event_epg_group(group_id)
            if not group:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Group not found'})}\n\n"
                return

            manager = _get_m3u_manager()
            if not manager:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Dispatcharr not configured'})}\n\n"
                return

            # Step 1: Refresh M3U
            yield f"data: {json.dumps({'type': 'progress', 'step': 1, 'total': 5, 'message': 'Refreshing M3U data...'})}\n\n"
            refresh_result = manager.wait_for_refresh(group['dispatcharr_account_id'], timeout=180)
            if not refresh_result.get('success'):
                error_msg = f"M3U refresh failed: {refresh_result.get('message')}"
                yield f"data: {json.dumps({'type': 'error', 'message': error_msg})}\n\n"
                return

            # Step 2: Fetch streams
            yield f"data: {json.dumps({'type': 'progress', 'step': 2, 'total': 5, 'message': 'Fetching streams...'})}\n\n"
            streams = manager.list_streams(group_name=group['group_name'])

            # Step 3: Match to ESPN events
            yield f"data: {json.dumps({'type': 'progress', 'step': 3, 'total': 5, 'message': f'Matching {len(streams)} streams to ESPN events...'})}\n\n"

            from epg.team_matcher import create_matcher
            from epg.event_matcher import create_event_matcher

            # Fetch settings early for use in matching and EPG generation
            conn = get_connection()
            settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
            conn.close()
            include_final_events = bool(settings.get('include_final_events', 0))
            lookahead_days = settings.get('event_lookahead_days', 7)

            team_matcher = create_matcher()
            event_matcher = create_event_matcher(lookahead_days=lookahead_days)
            matched_count = 0
            matched_streams = []

            for stream in streams:
                try:
                    team_result = team_matcher.extract_teams(stream['name'], group['assigned_league'])
                    if team_result.get('matched'):
                        event_result = event_matcher.find_and_enrich(
                            team_result['away_team_id'],
                            team_result['home_team_id'],
                            group['assigned_league'],
                            game_date=team_result.get('game_date'),
                            game_time=team_result.get('game_time'),
                            include_final_events=include_final_events
                        )
                        if event_result.get('found'):
                            matched_count += 1
                            matched_streams.append({
                                'stream': stream,
                                'teams': team_result,
                                'event': event_result['event']
                            })
                except Exception:
                    continue

            update_event_epg_group_stats(group_id, len(streams), matched_count)

            # Step 4: Generate EPG
            yield f"data: {json.dumps({'type': 'progress', 'step': 4, 'total': 5, 'message': f'Generating EPG for {matched_count} matched streams...'})}\n\n"

            if matched_streams:
                from epg.event_epg_generator import generate_event_epg
                from epg.epg_consolidator import get_data_dir, after_event_epg_generation

                # Settings already fetched at start of step 3
                final_output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')

                # Get event template if assigned
                event_template = None
                if group.get('event_template_id'):
                    event_template = get_template(group['event_template_id'])

                epg_result = generate_event_epg(
                    matched_streams=matched_streams,
                    group_info=group,
                    save=True,
                    data_dir=get_data_dir(final_output_path),
                    settings=settings,  # Include settings for timezone and time format
                    template=event_template
                )

                # Step 5: Consolidate
                yield f"data: {json.dumps({'type': 'progress', 'step': 5, 'total': 5, 'message': 'Consolidating EPG files...'})}\n\n"

                after_event_epg_generation(group_id, final_output_path)

            # Complete
            yield f"data: {json.dumps({'type': 'complete', 'stream_count': len(streams), 'matched_count': matched_count, 'message': f'Matched {matched_count}/{len(streams)} streams'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(
        stream_with_context(generate_progress()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/event-epg/<int:group_id>.xml', methods=['GET'])
def serve_event_epg(group_id):
    """
    Serve generated XMLTV file for an event EPG group.

    This endpoint is called by Dispatcharr to fetch the EPG data.
    """
    try:
        group = get_event_epg_group(group_id)
        if not group:
            return "Event EPG group not found", 404

        # Check if EPG file exists
        epg_path = os.path.join(
            os.path.dirname(__file__), 'data', f'event_epg_{group_id}.xml'
        )

        if not os.path.exists(epg_path):
            # EPG not generated yet - return minimal valid XMLTV
            app.logger.warning(f"Event EPG file not found for group {group_id}, returning empty XMLTV")
            empty_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="Teamarr Event EPG">
</tv>'''
            return Response(empty_xml, mimetype='application/xml')

        app.logger.debug(f"Serving event EPG file for group {group_id}")
        return send_file(epg_path, mimetype='application/xml')

    except Exception as e:
        app.logger.error(f"Error serving event EPG for group {group_id}: {e}")
        return f"Error: {str(e)}", 500


# =============================================================================
# TEAM ALIAS API ENDPOINTS
# =============================================================================

@app.route('/api/event-epg/aliases', methods=['GET'])
def api_event_epg_aliases_list():
    """
    List team aliases.

    Query params:
        league: Filter by league code (optional)
    """
    try:
        league = request.args.get('league')

        if league:
            aliases = get_aliases_for_league(league)
        else:
            aliases = get_all_aliases()

        return jsonify({'aliases': aliases})

    except Exception as e:
        app.logger.error(f"Error listing aliases: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/aliases', methods=['POST'])
def api_event_epg_aliases_create():
    """
    Create a new team alias.

    Body:
        alias: str (required) - the alias string (e.g., "spurs")
        league: str (required) - league code (e.g., "epl")
        espn_team_id: str (required) - ESPN team ID
        espn_team_name: str (required) - ESPN team display name
    """
    try:
        data = request.get_json()

        required = ['alias', 'league', 'espn_team_id', 'espn_team_name']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400

        alias_id = create_alias(
            alias=data['alias'],
            league=data['league'],
            espn_team_id=str(data['espn_team_id']),
            espn_team_name=data['espn_team_name']
        )

        app.logger.info(f"Created alias: '{data['alias']}' -> {data['espn_team_name']} ({data['league']})")

        return jsonify({
            'success': True,
            'id': alias_id,
            'message': f"Alias '{data['alias']}' created for {data['espn_team_name']}"
        }), 201

    except Exception as e:
        # Check for unique constraint violation
        if 'UNIQUE constraint' in str(e):
            return jsonify({'error': 'Alias already exists for this league'}), 409
        app.logger.error(f"Error creating alias: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/aliases/<int:alias_id>', methods=['GET'])
def api_event_epg_aliases_get(alias_id):
    """Get a specific alias."""
    try:
        alias = get_alias(alias_id)
        if not alias:
            return jsonify({'error': 'Alias not found'}), 404
        return jsonify(alias)
    except Exception as e:
        app.logger.error(f"Error fetching alias {alias_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/aliases/<int:alias_id>', methods=['PATCH'])
def api_event_epg_aliases_update(alias_id):
    """
    Update an alias.

    Body (all optional):
        alias: str
        league: str
        espn_team_id: str
        espn_team_name: str
    """
    try:
        data = request.get_json()

        alias = get_alias(alias_id)
        if not alias:
            return jsonify({'error': 'Alias not found'}), 404

        if update_alias(alias_id, data):
            return jsonify({'success': True, 'message': 'Alias updated'})
        else:
            return jsonify({'error': 'Update failed'}), 500

    except Exception as e:
        app.logger.error(f"Error updating alias {alias_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/aliases/<int:alias_id>', methods=['DELETE'])
def api_event_epg_aliases_delete(alias_id):
    """Delete an alias."""
    try:
        alias = get_alias(alias_id)
        if not alias:
            return jsonify({'error': 'Alias not found'}), 404

        if delete_alias(alias_id):
            app.logger.info(f"Deleted alias: '{alias['alias']}' ({alias['league']})")
            return jsonify({'success': True, 'message': 'Alias deleted'})
        else:
            return jsonify({'error': 'Delete failed'}), 500

    except Exception as e:
        app.logger.error(f"Error deleting alias {alias_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/event-epg/teams', methods=['GET'])
def api_event_epg_teams_search():
    """
    Search teams for alias creation.

    This is a convenience endpoint that wraps the existing league teams API
    for use in the alias creation UI.

    For college leagues, returns teams organized by conference using the
    /groups endpoint. Non-college leagues return a flat list.

    Query params:
        league: League code (required)
        search: Search term (optional)

    Returns:
        For college leagues:
            {
                "teams": [...],  // flat list of all teams (for search)
                "conferences": {  // teams grouped by conference (for dropdown)
                    "Big 12 Conference": [...],
                    "SEC": [...]
                }
            }
        For pro leagues:
            {"teams": [...]}
    """
    try:
        league = request.args.get('league')
        search = request.args.get('search', '').lower()

        if not league:
            return jsonify({'error': 'league parameter required'}), 400

        # Get league config
        conn = get_connection()
        cursor = conn.cursor()
        league_info = cursor.execute("""
            SELECT sport, api_path, league_name
            FROM league_config
            WHERE league_code = ?
        """, (league.lower(),)).fetchone()
        conn.close()

        if not league_info:
            return jsonify({'error': 'League not found'}), 404

        sport = league_info[0]
        api_path = league_info[1]
        league_identifier = api_path.split('/')[-1]

        espn = ESPNClient()

        # For college leagues, use the helper to get all teams by conference
        conferences_list = None
        if 'college' in league_identifier.lower():
            conferences_list = espn.get_all_teams_by_conference(sport, league_identifier)

        # Always get flat team list (includes independents for college)
        teams_data = espn.get_league_teams(sport, league_identifier)

        if not teams_data:
            return jsonify({'teams': [], 'conferences': {}})

        # Filter by search term if provided
        if search:
            teams_data = [
                t for t in teams_data
                if search in t.get('name', '').lower()
                or search in t.get('shortName', '').lower()
                or search in t.get('abbreviation', '').lower()
            ]
            # Also filter conferences if we have them
            if conferences_list:
                filtered_conferences = []
                for conf in conferences_list:
                    filtered = [
                        t for t in conf.get('teams', [])
                        if search in t.get('name', '').lower()
                        or search in t.get('shortName', '').lower()
                        or search in t.get('abbreviation', '').lower()
                    ]
                    if filtered:
                        filtered_conferences.append({
                            'name': conf['name'],
                            'teams': filtered
                        })
                conferences_list = filtered_conferences

        # Return simplified team data for dropdown
        teams = [
            {
                'id': t.get('id'),
                'name': t.get('name'),
                'shortName': t.get('shortName'),
                'abbreviation': t.get('abbreviation'),
                'logo': t.get('logo')
            }
            for t in teams_data
        ]

        result = {'teams': teams}

        # Add conference grouping for college leagues (dict format for dropdown optgroups)
        if conferences_list:
            formatted_conferences = {}
            for conf in conferences_list:
                formatted_conferences[conf['name']] = [
                    {
                        'id': t.get('id'),
                        'name': t.get('name'),
                        'shortName': t.get('shortName'),
                        'abbreviation': t.get('abbreviation'),
                        'logo': t.get('logo')
                    }
                    for t in conf.get('teams', [])
                ]
            result['conferences'] = formatted_conferences

        return jsonify(result)

    except Exception as e:
        app.logger.error(f"Error searching teams: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# CHANNEL LIFECYCLE MANAGEMENT API
# =============================================================================

@app.route('/api/channel-lifecycle/next-channel-range', methods=['GET'])
def api_next_channel_range():
    """
    Get the next available channel range start (1001, 2001, 3001, etc.).

    This is used by the UI to suggest a default channel start when
    none is specified for a new event group.

    Considers:
    - Event group channel_start values
    - Managed channel numbers
    - All channels in Dispatcharr (if configured)
    """
    try:
        from database import get_next_available_channel_range

        # Get Dispatcharr settings to query actual channels
        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        next_range = get_next_available_channel_range(
            dispatcharr_url=settings.get('dispatcharr_url'),
            dispatcharr_username=settings.get('dispatcharr_username'),
            dispatcharr_password=settings.get('dispatcharr_password')
        )

        return jsonify({
            'next_channel_range': next_range,
            'description': f'Next available range starting at {next_range}'
        })
    except Exception as e:
        app.logger.error(f"Error getting next channel range: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/status', methods=['GET'])
def api_channel_lifecycle_status():
    """
    Get status of channel lifecycle management.

    Returns counts of managed channels, pending deletions, scheduler status, etc.
    """
    try:
        from database import (
            get_all_managed_channels,
            get_channels_pending_deletion
        )
        from epg.channel_lifecycle import is_scheduler_running

        all_channels = get_all_managed_channels(include_deleted=False)
        pending_deletions = get_channels_pending_deletion()

        # Group by event_epg_group
        by_group = {}
        for ch in all_channels:
            gid = ch['event_epg_group_id']
            if gid not in by_group:
                by_group[gid] = []
            by_group[gid].append(ch)

        return jsonify({
            'total_managed_channels': len(all_channels),
            'pending_deletions': len(pending_deletions),
            'groups': len(by_group),
            'channels_by_group': {
                str(gid): len(channels) for gid, channels in by_group.items()
            },
            'scheduler_running': is_scheduler_running()
        })

    except Exception as e:
        app.logger.error(f"Error getting lifecycle status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/channels', methods=['GET'])
def api_channel_lifecycle_list():
    """
    List all managed channels with enriched data.

    Query params:
        group_id: Filter by event EPG group (optional)
        include_deleted: Include soft-deleted channels (default: false)

    Each channel includes:
        - logo_url: URL to the channel logo in Dispatcharr (if available)
        - channel_group_name: Name of the assigned Dispatcharr channel group
    """
    try:
        from database import (
            get_all_managed_channels,
            get_managed_channels_for_group,
            get_event_epg_group
        )

        group_id = request.args.get('group_id', type=int)
        include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'

        if group_id:
            channels = get_managed_channels_for_group(group_id, include_deleted=include_deleted)
        else:
            channels = get_all_managed_channels(include_deleted=include_deleted)

        # Enrich channels with logo URLs and channel group names
        if channels:
            # Get Dispatcharr settings for API calls
            conn = get_connection()
            settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
            conn.close()

            dispatcharr_url = settings.get('dispatcharr_url', '').rstrip('/')

            # Build lookup of event groups for channel_group_id
            event_group_cache = {}
            channel_group_cache = {}

            # Try to get channel groups from Dispatcharr
            try:
                from api.dispatcharr_client import ChannelManager
                channel_mgr = ChannelManager(
                    dispatcharr_url,
                    settings.get('dispatcharr_username', ''),
                    settings.get('dispatcharr_password', '')
                )
                groups = channel_mgr.get_channel_groups()
                for g in groups:
                    channel_group_cache[g['id']] = g.get('name', f"Group {g['id']}")
            except Exception as e:
                app.logger.debug(f"Could not fetch channel groups from Dispatcharr: {e}")

            for channel in channels:
                # Get logo URL if logo_id is present
                logo_id = channel.get('dispatcharr_logo_id')
                if logo_id and dispatcharr_url:
                    # Construct logo URL (Dispatcharr serves cached images at /api/channels/logos/<id>/cache/)
                    channel['logo_url'] = f"{dispatcharr_url}/api/channels/logos/{logo_id}/cache/"
                else:
                    channel['logo_url'] = None

                # Get channel group name from event group's channel_group_id
                event_group_id = channel.get('event_epg_group_id')
                if event_group_id:
                    if event_group_id not in event_group_cache:
                        event_group_cache[event_group_id] = get_event_epg_group(event_group_id)
                    event_group = event_group_cache.get(event_group_id)
                    if event_group:
                        channel_group_id = event_group.get('channel_group_id')
                        if channel_group_id and channel_group_id in channel_group_cache:
                            channel['channel_group_name'] = channel_group_cache[channel_group_id]
                        else:
                            channel['channel_group_name'] = None
                    else:
                        channel['channel_group_name'] = None
                else:
                    channel['channel_group_name'] = None

        return jsonify({
            'channels': channels,
            'count': len(channels)
        })

    except Exception as e:
        app.logger.error(f"Error listing managed channels: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/channels/<int:channel_id>', methods=['DELETE'])
def api_channel_lifecycle_delete(channel_id):
    """
    Manually delete a managed channel.

    Uses unified delete_managed_channel() which handles:
    1. Delete the channel from Dispatcharr
    2. Delete associated logo from Dispatcharr
    3. Mark it as deleted in local database
    """
    try:
        from database import get_managed_channel
        from epg.channel_lifecycle import get_lifecycle_manager

        channel = get_managed_channel(channel_id)
        if not channel:
            return jsonify({'error': 'Channel not found'}), 404

        if channel.get('deleted_at'):
            return jsonify({'error': 'Channel already deleted'}), 400

        lifecycle_mgr = get_lifecycle_manager()
        if not lifecycle_mgr:
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        # Use unified delete method
        result = lifecycle_mgr.delete_managed_channel(channel, reason='manual deletion')

        if result.get('success'):
            return jsonify({
                'success': True,
                'message': f"Deleted channel {channel['channel_number']} '{channel['channel_name']}'",
                'logo_deleted': result.get('logo_deleted', False)
            })
        else:
            return jsonify({
                'error': f"Failed to delete: {result.get('error')}"
            }), 500

    except Exception as e:
        app.logger.error(f"Error deleting channel {channel_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/process-deletions', methods=['POST'])
def api_channel_lifecycle_process_deletions():
    """
    Process all pending scheduled deletions.

    This endpoint can be called manually or via cron/scheduler to clean up
    channels that have passed their scheduled deletion time.
    """
    try:
        from epg.channel_lifecycle import get_lifecycle_manager

        lifecycle_mgr = get_lifecycle_manager()
        if not lifecycle_mgr:
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        results = lifecycle_mgr.process_scheduled_deletions()

        return jsonify({
            'success': True,
            'deleted': len(results.get('deleted', [])),
            'errors': len(results.get('errors', [])),
            'deleted_channels': results.get('deleted', []),
            'error_details': results.get('errors', [])
        })

    except Exception as e:
        app.logger.error(f"Error processing deletions: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/cleanup-old', methods=['POST'])
def api_channel_lifecycle_cleanup_old():
    """
    Hard delete old soft-deleted channel records.

    Query params:
        days: Delete records older than N days (default: 30)
    """
    try:
        from database import cleanup_old_deleted_channels

        days = request.args.get('days', 30, type=int)
        deleted_count = cleanup_old_deleted_channels(days)

        return jsonify({
            'success': True,
            'deleted_records': deleted_count,
            'message': f"Cleaned up {deleted_count} records older than {days} days"
        })

    except Exception as e:
        app.logger.error(f"Error cleaning up old records: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/history/<int:channel_id>', methods=['GET'])
def api_channel_history(channel_id):
    """
    Get history for a specific managed channel.

    Query params:
        limit: Maximum number of records (default: 100)

    Returns list of history entries with change details.
    """
    try:
        from database import get_channel_history, get_managed_channel

        # Verify channel exists
        channel = get_managed_channel(channel_id)
        if not channel:
            return jsonify({'error': 'Channel not found'}), 404

        limit = request.args.get('limit', 100, type=int)
        history = get_channel_history(channel_id, limit=limit)

        return jsonify({
            'success': True,
            'channel_id': channel_id,
            'channel_name': channel.get('channel_name'),
            'history': history
        })

    except Exception as e:
        app.logger.error(f"Error getting channel history: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/history/recent', methods=['GET'])
def api_channel_history_recent():
    """
    Get recent channel changes across all channels.

    Query params:
        hours: Look back N hours (default: 24)
        types: Comma-separated change types to filter (optional)

    Returns list of recent changes with channel info.
    """
    try:
        from database import get_recent_channel_changes

        hours = request.args.get('hours', 24, type=int)
        types_param = request.args.get('types', '')
        change_types = [t.strip() for t in types_param.split(',') if t.strip()] or None

        changes = get_recent_channel_changes(hours=hours, change_types=change_types)

        return jsonify({
            'success': True,
            'hours': hours,
            'count': len(changes),
            'changes': changes
        })

    except Exception as e:
        app.logger.error(f"Error getting recent changes: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/reconcile', methods=['POST'])
def api_channel_reconcile():
    """
    Run channel reconciliation.

    Query params:
        auto_fix: Whether to auto-fix issues (default: from settings)
        group_id: Limit to specific group (optional)

    Returns reconciliation results with issues found and fixed.
    """
    try:
        from epg.reconciliation import run_reconciliation

        auto_fix = request.args.get('auto_fix', type=lambda x: x.lower() == 'true')
        group_id = request.args.get('group_id', type=int)
        group_ids = [group_id] if group_id else None

        result = run_reconciliation(auto_fix=auto_fix, group_ids=group_ids)

        if not result:
            return jsonify({'error': 'Reconciliation not available - Dispatcharr not configured'}), 400

        return jsonify({
            'success': True,
            'summary': result.summary,
            'issues_found': [
                {
                    'type': i.issue_type,
                    'severity': i.severity,
                    'channel_name': i.channel_name,
                    'event_id': i.espn_event_id,
                    'details': i.details,
                    'suggested_action': i.suggested_action,
                    'auto_fixable': i.auto_fixable
                }
                for i in result.issues_found
            ],
            'issues_fixed': result.issues_fixed,
            'issues_skipped': result.issues_skipped,
            'errors': result.errors,
            'duration': (result.completed_at - result.started_at).total_seconds() if result.completed_at else None
        })

    except Exception as e:
        app.logger.error(f"Error running reconciliation: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/streams/<int:channel_id>', methods=['GET'])
def api_channel_streams(channel_id):
    """
    Get all streams attached to a managed channel.

    Returns list of streams with source group and priority info.
    """
    try:
        from database import get_channel_streams, get_managed_channel

        channel = get_managed_channel(channel_id)
        if not channel:
            return jsonify({'error': 'Channel not found'}), 404

        include_removed = request.args.get('include_removed', 'false').lower() == 'true'
        streams = get_channel_streams(channel_id, include_removed=include_removed)

        return jsonify({
            'success': True,
            'channel_id': channel_id,
            'channel_name': channel.get('channel_name'),
            'streams': streams
        })

    except Exception as e:
        app.logger.error(f"Error getting channel streams: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/info/<int:channel_id>', methods=['GET'])
def api_channel_info(channel_id):
    """
    Get comprehensive info for a managed channel.

    Returns all channel details including:
    - Identity (UUID, channel ID, tvg_id)
    - Dispatcharr info (name, number, group, profile)
    - Event details (ESPN event ID, teams, date, venue)
    - Lifecycle info (created, modified, scheduled delete)
    - Streams and history
    """
    try:
        from database import (
            get_managed_channel,
            get_channel_streams,
            get_channel_history,
            get_event_epg_group,
            get_connection
        )

        channel = get_managed_channel(channel_id)
        if not channel:
            return jsonify({'error': 'Channel not found'}), 404

        # Get event group info
        group = None
        if channel.get('event_epg_group_id'):
            group = get_event_epg_group(channel['event_epg_group_id'])

        # Get streams and history
        streams = get_channel_streams(channel_id, include_removed=False)
        history = get_channel_history(channel_id, limit=50)

        # Get Dispatcharr settings
        conn = get_connection()
        settings_row = conn.execute(
            "SELECT dispatcharr_url, dispatcharr_username, dispatcharr_password FROM settings WHERE id = 1"
        ).fetchone()
        conn.close()
        dispatcharr_settings = dict(settings_row) if settings_row else {}
        dispatcharr_url = dispatcharr_settings.get('dispatcharr_url', '').rstrip('/')

        # Fetch EPG data ID from Dispatcharr if we have a channel ID
        epg_data_id = None
        if channel.get('dispatcharr_channel_id') and dispatcharr_url and dispatcharr_settings.get('dispatcharr_username'):
            try:
                from api.dispatcharr_client import ChannelManager
                channel_mgr = ChannelManager(
                    dispatcharr_url,
                    dispatcharr_settings['dispatcharr_username'],
                    dispatcharr_settings.get('dispatcharr_password', '')
                )
                dispatcharr_channel = channel_mgr.get_channel(channel['dispatcharr_channel_id'])
                if dispatcharr_channel:
                    epg_data_id = dispatcharr_channel.get('epg_data_id')
            except Exception as e:
                app.logger.debug(f"Could not fetch EPG data ID from Dispatcharr: {e}")

        # Build comprehensive response
        info = {
            # Identity
            'id': channel['id'],
            'dispatcharr_channel_id': channel.get('dispatcharr_channel_id'),
            'dispatcharr_uuid': channel.get('dispatcharr_uuid'),
            'tvg_id': channel.get('tvg_id'),

            # Display
            'channel_name': channel.get('channel_name'),
            'channel_number': channel.get('channel_number'),

            # Group/Profile
            'event_epg_group_id': channel.get('event_epg_group_id'),
            'event_epg_group_name': group.get('group_name') if group else None,
            'channel_group_id': channel.get('channel_group_id'),
            'channel_profile_id': channel.get('channel_profile_id'),
            'stream_profile_id': channel.get('stream_profile_id'),

            # Event
            'espn_event_id': channel.get('espn_event_id'),
            'event_name': channel.get('event_name'),
            'event_date': channel.get('event_date'),
            'home_team': channel.get('home_team'),
            'home_team_abbrev': channel.get('home_team_abbrev'),
            'home_team_logo': channel.get('home_team_logo'),
            'away_team': channel.get('away_team'),
            'away_team_abbrev': channel.get('away_team_abbrev'),
            'away_team_logo': channel.get('away_team_logo'),
            'league': channel.get('league'),
            'sport': channel.get('sport'),
            'venue': channel.get('venue'),
            'broadcast': channel.get('broadcast'),

            # Lifecycle
            'sync_status': channel.get('sync_status'),
            'sync_message': channel.get('sync_message'),
            'created_at': channel.get('created_at'),
            'updated_at': channel.get('updated_at'),
            'scheduled_delete_at': channel.get('scheduled_delete_at'),
            'deleted_at': channel.get('deleted_at'),

            # Logo
            'dispatcharr_logo_id': channel.get('dispatcharr_logo_id'),
            'logo_url': channel.get('logo_url'),
            'logo_cache_url': f"{dispatcharr_url}/api/channels/logos/{channel.get('dispatcharr_logo_id')}/cache/" if channel.get('dispatcharr_logo_id') and dispatcharr_url else None,

            # EPG
            'epg_data_id': epg_data_id,

            # Streams
            'primary_stream_id': channel.get('primary_stream_id'),
            'streams': streams,
            'stream_count': len(streams),

            # History
            'history': history,
            'history_count': len(history)
        }

        return jsonify({
            'success': True,
            'channel': info
        })

    except Exception as e:
        app.logger.error(f"Error getting channel info: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/orphans', methods=['GET'])
def api_find_orphan_channels():
    """
    Find orphaned channels in Dispatcharr.

    Orphans are channels with teamarr-event-* tvg_id that don't match
    any managed channel by UUID (or by channel ID as fallback).

    Returns list of orphaned channels that can be deleted.
    """
    try:
        from database import get_connection
        from api.dispatcharr_client import ChannelManager

        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())

        # Get known UUIDs and channel IDs (handle missing UUID column for older DBs)
        try:
            rows = conn.execute("""
                SELECT dispatcharr_channel_id, dispatcharr_uuid
                FROM managed_channels WHERE deleted_at IS NULL
            """).fetchall()
            known_channel_ids = {row[0] for row in rows if row[0]}
            known_uuids = {row[1] for row in rows if row[1]}
        except Exception:
            # UUID column doesn't exist yet - fall back to channel ID only
            rows = conn.execute("""
                SELECT dispatcharr_channel_id
                FROM managed_channels WHERE deleted_at IS NULL
            """).fetchall()
            known_channel_ids = {row[0] for row in rows if row[0]}
            known_uuids = set()
        conn.close()

        if not settings.get('dispatcharr_enabled'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        # Get all channels from Dispatcharr
        channel_api = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )
        all_channels = channel_api.get_channels()

        # Find orphans
        # If no active managed channels exist, ALL teamarr-event channels are orphans
        no_active_channels = len(known_channel_ids) == 0 and len(known_uuids) == 0

        orphans = []
        for ch in all_channels:
            tvg_id = ch.get('tvg_id') or ''
            if not tvg_id.startswith('teamarr-event-'):
                continue

            ch_id = ch.get('id')
            ch_uuid = ch.get('uuid')

            # If no active channels, all teamarr-event channels are orphans
            # Otherwise, check if we know this channel
            is_orphan = no_active_channels
            if not is_orphan:
                is_known_by_uuid = ch_uuid and ch_uuid in known_uuids
                is_known_by_id = ch_id in known_channel_ids
                is_orphan = not is_known_by_uuid and not is_known_by_id

            if is_orphan:
                # Extract event ID from tvg_id
                event_id = tvg_id.replace('teamarr-event-', '')
                orphans.append({
                    'dispatcharr_channel_id': ch_id,
                    'uuid': ch_uuid,
                    'tvg_id': tvg_id,
                    'channel_name': ch.get('name'),
                    'channel_number': ch.get('channel_number'),
                    'espn_event_id': event_id
                })

        return jsonify({
            'success': True,
            'orphan_count': len(orphans),
            'orphans': orphans
        })

    except Exception as e:
        app.logger.error(f"Error finding orphan channels: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/orphans/cleanup', methods=['POST'])
def api_cleanup_orphan_channels():
    """
    Delete orphaned channels from Dispatcharr.

    JSON body:
        channel_ids: List of Dispatcharr channel IDs to delete (optional, deletes all if not provided)

    Uses UUID-based identification to ensure we only delete orphans.
    """
    try:
        from database import get_connection
        from api.dispatcharr_client import ChannelManager

        data = request.get_json() or {}
        channel_ids_to_delete = data.get('channel_ids')  # Optional filter

        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())

        # Get known UUIDs and channel IDs (handle missing UUID column for older DBs)
        try:
            rows = conn.execute("""
                SELECT dispatcharr_channel_id, dispatcharr_uuid
                FROM managed_channels WHERE deleted_at IS NULL
            """).fetchall()
            known_channel_ids = {row[0] for row in rows if row[0]}
            known_uuids = {row[1] for row in rows if row[1]}
        except Exception:
            # UUID column doesn't exist yet - fall back to channel ID only
            rows = conn.execute("""
                SELECT dispatcharr_channel_id
                FROM managed_channels WHERE deleted_at IS NULL
            """).fetchall()
            known_channel_ids = {row[0] for row in rows if row[0]}
            known_uuids = set()
        conn.close()

        if not settings.get('dispatcharr_enabled'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        channel_api = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )
        all_channels = channel_api.get_channels()

        # Find and delete orphans
        deleted = []
        errors = []

        for ch in all_channels:
            tvg_id = ch.get('tvg_id') or ''
            if not tvg_id.startswith('teamarr-event-'):
                continue

            ch_id = ch.get('id')
            ch_uuid = ch.get('uuid')

            # Skip if we know this channel
            is_known_by_uuid = ch_uuid and ch_uuid in known_uuids
            is_known_by_id = ch_id in known_channel_ids
            if is_known_by_uuid or is_known_by_id:
                continue

            # Skip if not in filter list (when filter provided)
            if channel_ids_to_delete is not None and ch_id not in channel_ids_to_delete:
                continue

            # Delete the orphan
            try:
                result = channel_api.delete_channel(ch_id)
                if result.get('success'):
                    deleted.append({
                        'channel_id': ch_id,
                        'channel_name': ch.get('name'),
                        'tvg_id': tvg_id
                    })
                    app.logger.info(f"Deleted orphan channel: {ch.get('name')} ({tvg_id})")
                else:
                    errors.append({
                        'channel_id': ch_id,
                        'channel_name': ch.get('name'),
                        'error': result.get('error', 'Unknown error')
                    })
            except Exception as e:
                errors.append({
                    'channel_id': ch_id,
                    'channel_name': ch.get('name'),
                    'error': str(e)
                })

        return jsonify({
            'success': True,
            'deleted_count': len(deleted),
            'deleted': deleted,
            'error_count': len(errors),
            'errors': errors
        })

    except Exception as e:
        app.logger.error(f"Error cleaning up orphan channels: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/reset', methods=['GET'])
def api_channel_lifecycle_reset_preview():
    """
    Preview all Teamarr-created channels that would be deleted by reset.

    Returns list of all channels with teamarr-event-* tvg_id, regardless
    of whether they're tracked in managed_channels.
    """
    try:
        from database import get_connection
        from api.dispatcharr_client import ChannelManager

        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        conn.close()

        if not settings.get('dispatcharr_enabled'):
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        channel_api = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )
        all_channels = channel_api.get_channels()

        # Find ALL teamarr-event channels
        teamarr_channels = []
        for ch in all_channels:
            tvg_id = ch.get('tvg_id') or ''
            if tvg_id.startswith('teamarr-event-'):
                event_id = tvg_id.replace('teamarr-event-', '')
                teamarr_channels.append({
                    'dispatcharr_channel_id': ch.get('id'),
                    'uuid': ch.get('uuid'),
                    'tvg_id': tvg_id,
                    'channel_name': ch.get('name'),
                    'channel_number': ch.get('channel_number'),
                    'espn_event_id': event_id,
                    'stream_count': len(ch.get('streams', []))
                })

        return jsonify({
            'success': True,
            'channel_count': len(teamarr_channels),
            'channels': teamarr_channels
        })

    except Exception as e:
        app.logger.error(f"Error previewing reset channels: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/reset', methods=['POST'])
def api_channel_lifecycle_reset():
    """
    Delete ALL Teamarr-created channels from Dispatcharr.

    This is a destructive operation that removes all channels with
    teamarr-event-* tvg_id, regardless of tracking state. Use this
    to clean up after issues or start fresh.

    Also clears the managed_channels table (marks all as deleted).
    """
    try:
        from database import get_connection
        from api.dispatcharr_client import ChannelManager

        conn = get_connection()
        settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())

        if not settings.get('dispatcharr_enabled'):
            conn.close()
            return jsonify({'error': 'Dispatcharr not configured'}), 400

        channel_api = ChannelManager(
            settings['dispatcharr_url'],
            settings['dispatcharr_username'],
            settings['dispatcharr_password']
        )
        all_channels = channel_api.get_channels()

        # Find and delete ALL teamarr-event channels
        deleted = []
        errors = []

        for ch in all_channels:
            tvg_id = ch.get('tvg_id') or ''
            if not tvg_id.startswith('teamarr-event-'):
                continue

            ch_id = ch.get('id')

            try:
                result = channel_api.delete_channel(ch_id)
                if result.get('success'):
                    deleted.append({
                        'channel_id': ch_id,
                        'channel_name': ch.get('name'),
                        'channel_number': ch.get('channel_number'),
                        'tvg_id': tvg_id
                    })
                    app.logger.info(f"Reset: deleted channel {ch.get('name')} ({tvg_id})")
                else:
                    errors.append({
                        'channel_id': ch_id,
                        'channel_name': ch.get('name'),
                        'error': result.get('error', 'Unknown error')
                    })
            except Exception as e:
                errors.append({
                    'channel_id': ch_id,
                    'channel_name': ch.get('name'),
                    'error': str(e)
                })

        # Mark all managed_channels as deleted
        deleted_db_count = 0
        try:
            cursor = conn.execute("""
                UPDATE managed_channels
                SET deleted_at = CURRENT_TIMESTAMP,
                    sync_status = 'reset',
                    delete_reason = 'Manual reset - all channels deleted'
                WHERE deleted_at IS NULL
            """)
            deleted_db_count = cursor.rowcount
            conn.commit()
        except Exception as e:
            app.logger.warning(f"Failed to update managed_channels during reset: {e}")

        conn.close()

        app.logger.info(f"Channel reset complete: {len(deleted)} deleted from Dispatcharr, {deleted_db_count} marked in DB")

        return jsonify({
            'success': True,
            'deleted_count': len(deleted),
            'deleted': deleted,
            'db_records_updated': deleted_db_count,
            'error_count': len(errors),
            'errors': errors
        })

    except Exception as e:
        app.logger.error(f"Error resetting channels: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/channel-lifecycle/scheduler', methods=['POST'])
def api_channel_lifecycle_scheduler():
    """
    Start or stop the background lifecycle scheduler.

    JSON body:
        action: 'start' or 'stop'
        interval: Interval in minutes (only for start, default: 15)
    """
    try:
        from epg.channel_lifecycle import (
            start_lifecycle_scheduler,
            stop_lifecycle_scheduler,
            is_scheduler_running
        )

        data = request.get_json() or {}
        action = data.get('action', 'start')

        if action == 'start':
            interval = data.get('interval', 15)
            start_lifecycle_scheduler(interval_minutes=interval)
            return jsonify({
                'success': True,
                'message': f'Scheduler started with {interval} minute interval',
                'running': is_scheduler_running()
            })
        elif action == 'stop':
            stop_lifecycle_scheduler()
            return jsonify({
                'success': True,
                'message': 'Scheduler stopped',
                'running': is_scheduler_running()
            })
        else:
            return jsonify({'error': f"Unknown action: {action}"}), 400

    except Exception as e:
        app.logger.error(f"Error controlling scheduler: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _analyze_epg_content(xml_content):
    """
    Analyze EPG XML content and return statistics

    Returns:
        dict: Analysis results with counts and findings
    """
    import re
    from datetime import datetime, timedelta
    import xml.etree.ElementTree as ET

    analysis = {
        'total_programs': 0,
        'total_events': 0,
        'filler_programs': {
            'pregame': 0,
            'postgame': 0,
            'idle': 0
        },
        'unreplaced_variables': [],
        'channels': 0,
        'coverage_gaps': [],
        'date_range': None
    }

    try:
        # Parse XML
        root = ET.fromstring(xml_content)

        # Count channels
        analysis['channels'] = len(root.findall('channel'))

        # Analyze programs
        programs = root.findall('.//programme')
        analysis['total_programs'] = len(programs)

        # Track program times per channel for gap detection
        channel_programs = {}

        for programme in programs:
            channel = programme.get('channel', '')
            start = programme.get('start', '')
            stop = programme.get('stop', '')

            # Get title and description
            title_elem = programme.find('title')
            desc_elem = programme.find('desc')
            title = title_elem.text if title_elem is not None else ''
            desc = desc_elem.text if desc_elem is not None else ''

            # Check for unreplaced variables in title and description
            for text in [title, desc]:
                if text:
                    vars_found = re.findall(r'\{[^}]+\}', text)
                    for var in vars_found:
                        if var not in analysis['unreplaced_variables']:
                            analysis['unreplaced_variables'].append(var)

            # Track for gap detection
            if channel not in channel_programs:
                channel_programs[channel] = []
            channel_programs[channel].append({
                'start': start,
                'stop': stop,
                'title': title
            })

        # Detect coverage gaps (programs that don't connect properly)
        for channel, progs in channel_programs.items():
            # Sort by start time
            progs_sorted = sorted(progs, key=lambda x: x['start'])
            for i in range(len(progs_sorted) - 1):
                current_stop = progs_sorted[i]['stop']
                next_start = progs_sorted[i + 1]['start']

                # If there's a gap (stop time doesn't match next start time)
                if current_stop != next_start:
                    try:
                        # Parse times (format: YYYYMMDDHHMMSS +0000)
                        stop_time = datetime.strptime(current_stop[:14], '%Y%m%d%H%M%S')
                        start_time = datetime.strptime(next_start[:14], '%Y%m%d%H%M%S')
                        gap_minutes = (start_time - stop_time).total_seconds() / 60

                        # Only report gaps > 1 minute (to avoid floating point rounding)
                        if gap_minutes > 1:
                            analysis['coverage_gaps'].append({
                                'channel': channel,
                                'gap_minutes': int(gap_minutes),
                                'after_program': progs_sorted[i]['title'],
                                'after_stop': progs_sorted[i]['stop'],
                                'before_program': progs_sorted[i + 1]['title'],
                                'before_start': progs_sorted[i + 1]['start']
                            })
                    except:
                        pass

        # Calculate date range
        if programs:
            try:
                start_times = [p.get('start', '') for p in programs]
                stop_times = [p.get('stop', '') for p in programs]
                if start_times and stop_times:
                    earliest = min(start_times)
                    latest = max(stop_times)
                    analysis['date_range'] = {
                        'start': earliest[:8],  # YYYYMMDD
                        'end': latest[:8]
                    }
            except:
                pass

    except Exception as e:
        app.logger.error(f"Error analyzing EPG: {e}")

    return analysis

def _generate_channel_id(format_template, **kwargs):
    """
    Generate a channel ID based on the format template from settings

    Args:
        format_template: String template with variables like '{team_abbrev}.{league}'
        **kwargs: Variables to substitute (team_name, team_abbrev, team_slug, league, sport, espn_team_id, league_name)

    Returns:
        str: Generated channel ID (lowercased, sanitized)
    """
    # Start with the format template
    channel_id = format_template

    # Get team name and convert to PascalCase
    team_name = kwargs.get('team_name', '')
    team_name_pascal = to_pascal_case(team_name)

    # Get league code and league name
    league_code = kwargs.get('league', '')
    league_name = kwargs.get('league_name', league_code.upper() if league_code else '')

    # Replace all available variables (order matters - do specific ones first)
    replacements = {
        '{team_name_pascal}': team_name_pascal,
        '{league_id}': league_code.lower(),
        '{team_abbrev}': kwargs.get('team_abbrev', '').lower(),
        '{team_name}': team_name.lower().replace(' ', '-'),
        '{team_slug}': kwargs.get('team_slug', team_name.lower().replace(' ', '-')),
        '{espn_team_id}': str(kwargs.get('espn_team_id', '')),
        '{league}': league_name,
        '{sport}': kwargs.get('sport', '').lower()
    }

    for placeholder, value in replacements.items():
        channel_id = channel_id.replace(placeholder, str(value))

    # Clean up channel ID - conditionally preserve case for PascalCase formats
    import re
    channel_id = channel_id.replace("'", "")
    if '{team_name_pascal}' in format_template or ('{league}' in format_template and '{league_id}' not in format_template):
        # Allow uppercase letters (for PascalCase channel IDs)
        channel_id = re.sub(r'[^a-zA-Z0-9.-]+', '', channel_id)
    else:
        # Traditional: lowercase only
        channel_id = channel_id.lower()
        channel_id = re.sub(r'[^a-z0-9.-]+', '-', channel_id)
        channel_id = re.sub(r'-+', '-', channel_id)
        channel_id = channel_id.strip('-')

    return channel_id

def _extract_template_form_data(form):
    """Extract template data from form submission"""
    data = {
        'name': form.get('name'),

        # Programme templates
        'title_format': form.get('title_format'),
        'subtitle_template': form.get('subtitle_template'),
        'program_art_url': form.get('program_art_url'),

        # Game settings
        'game_duration_mode': form.get('game_duration_mode', 'default'),
        'game_duration_override': float(form.get('game_duration_override')) if form.get('game_duration_override') else None,

        # XMLTV settings
        'flags': form.get('flags'),  # Already JSON string
        'categories': form.get('categories'),  # Already JSON string
        'categories_apply_to': form.get('categories_apply_to', 'events'),

        # Filler content
        'pregame_enabled': 1 if form.get('pregame_enabled') == 'on' else 0,
        'pregame_title': form.get('pregame_title'),
        'pregame_subtitle': form.get('pregame_subtitle'),
        'pregame_description': form.get('pregame_description'),
        'pregame_art_url': form.get('pregame_art_url'),
        'pregame_periods': form.get('pregame_periods'),  # JSON string

        'postgame_enabled': 1 if form.get('postgame_enabled') == 'on' else 0,
        'postgame_title': form.get('postgame_title'),
        'postgame_subtitle': form.get('postgame_subtitle'),
        'postgame_description': form.get('postgame_description'),
        'postgame_art_url': form.get('postgame_art_url'),
        'postgame_periods': form.get('postgame_periods'),  # JSON string
        'postgame_conditional_enabled': 1 if form.get('postgame_conditional_enabled') == 'on' else 0,
        'postgame_description_final': form.get('postgame_description_final'),
        'postgame_description_not_final': form.get('postgame_description_not_final'),

        'idle_enabled': 1 if form.get('idle_enabled') == 'on' else 0,
        'idle_title': form.get('idle_title'),
        'idle_subtitle': form.get('idle_subtitle'),
        'idle_description': form.get('idle_description'),
        'idle_art_url': form.get('idle_art_url'),
        'idle_conditional_enabled': 1 if form.get('idle_conditional_enabled') == 'on' else 0,
        'idle_description_final': form.get('idle_description_final'),
        'idle_description_not_final': form.get('idle_description_not_final'),

        # Conditional descriptions
        'description_options': form.get('description_options'),  # JSON string

        # Event template channel naming and logo
        'channel_name': form.get('channel_name'),
        'channel_logo_url': form.get('channel_logo_url')
    }

    return data

def _extract_team_form_data(form):
    """Extract team data from form submission"""
    data = {
        'espn_team_id': form.get('espn_team_id'),
        'league': form.get('league'),
        'sport': form.get('sport'),
        'team_name': form.get('team_name'),
        'team_abbrev': form.get('team_abbrev'),
        'team_logo_url': form.get('team_logo_url'),
        'team_color': form.get('team_color'),
        'channel_id': form.get('channel_id'),
        'channel_logo_url': form.get('channel_logo_url') if form.get('channel_logo_url') else None,
        'template_id': int(form.get('template_id')) if form.get('template_id') and form.get('template_id') != '' else None,
        'active': 1 if form.get('active') == 'on' else 0,
    }

    return data

# =============================================================================
# STARTUP INITIALIZATION
# =============================================================================

def sync_timezone_from_env():
    """Sync timezone setting from environment variable (for Docker deployments).

    Checks TZ environment variable and updates the database setting if present.
    This runs on startup so Docker compose timezone settings are honored.
    Falls back to America/Detroit if no env var and no existing setting.
    """
    env_tz = os.environ.get('TZ')
    if env_tz:
        try:
            # Validate the timezone
            from zoneinfo import ZoneInfo
            ZoneInfo(env_tz)  # Will raise if invalid

            conn = get_connection()
            conn.execute("UPDATE settings SET default_timezone = ? WHERE id = 1", (env_tz,))
            conn.commit()
            conn.close()
            app.logger.info(f"🌍 Timezone synced from TZ env var: {env_tz}")
        except Exception as e:
            app.logger.warning(f"⚠️ Invalid TZ env var '{env_tz}': {e}")
    else:
        # No env var - check if default_timezone is set, if not set to Detroit
        try:
            conn = get_connection()
            row = conn.execute("SELECT default_timezone FROM settings WHERE id = 1").fetchone()
            if not row or not row[0]:
                conn.execute("UPDATE settings SET default_timezone = 'America/Detroit' WHERE id = 1")
                conn.commit()
                app.logger.info("🌍 No TZ env var, defaulting to America/Detroit")
            conn.close()
        except Exception as e:
            app.logger.warning(f"⚠️ Could not check/set default timezone: {e}")


def initialize_soccer_cache():
    """
    Initialize soccer multi-league cache on startup.
    Always refreshes to ensure new leagues are included.
    """
    from epg.soccer_multi_league import SoccerMultiLeague

    try:
        app.logger.info("⚽ Refreshing soccer league cache on startup...")
        result = SoccerMultiLeague.refresh_cache()

        if result['success']:
            app.logger.info(f"✅ Soccer cache refreshed: {result['teams_indexed']} teams across {result['leagues_processed']} leagues ({result['duration_seconds']:.1f}s)")
        else:
            app.logger.warning(f"⚠️ Soccer cache refresh failed: {result.get('error', 'Unknown error')}")

    except Exception as e:
        app.logger.warning(f"⚠️ Soccer cache initialization skipped: {e}")


def initialize_team_league_cache():
    """
    Initialize team-league cache on startup.
    Always refreshes to ensure new leagues are included.
    """
    from epg.team_league_cache import TeamLeagueCache

    try:
        app.logger.info("🏈 Refreshing team-league cache on startup...")
        result = TeamLeagueCache.refresh_cache()

        if result['success']:
            app.logger.info(f"✅ Team-league cache refreshed: {result['teams_indexed']} teams across {result['leagues_processed']} leagues ({result['duration_seconds']:.1f}s)")
        else:
            app.logger.warning(f"⚠️ Team-league cache refresh failed: {result.get('error', 'Unknown error')}")

    except Exception as e:
        app.logger.warning(f"⚠️ Team-league cache initialization skipped: {e}")


# =============================================================================
# RUN APPLICATION
# =============================================================================

if __name__ == '__main__':
    # Sync timezone from environment variable (Docker)
    sync_timezone_from_env()

    # Initialize caches (runs in background if empty)
    initialize_soccer_cache()
    initialize_team_league_cache()

    # Start the auto-generation scheduler
    # Only start in main process, not in werkzeug reloader process
    # In Docker/production, WERKZEUG_RUN_MAIN won't be set, so check differently
    import sys
    is_reloader = sys.argv[0].endswith('flask') and '--reloader' in ' '.join(sys.argv)
    is_werkzeug_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'

    # Start scheduler only if: not running from reloader, OR we're the werkzeug child process
    if not is_reloader or is_werkzeug_child:
        start_scheduler()

    try:
        port = int(os.environ.get('PORT', 9195))
        app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)  # Disable reloader to prevent duplicates
    except KeyboardInterrupt:
        stop_scheduler()
        print("👋 Goodbye!")
