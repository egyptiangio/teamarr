"""Database module for Teamarr - Template-Based Architecture"""
import sqlite3
import os
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Database path - respects Docker volume mount at /app/data
# In Docker: /app/data/teamarr.db (persisted via volume)
# In local dev: ./data/teamarr.db (or project root if data/ doesn't exist)
def is_running_in_docker():
    """Check if we're actually running inside a Docker container"""
    # Check for .dockerenv file (most reliable)
    if os.path.exists('/.dockerenv'):
        return True
    # Check cgroup for docker/container indicators
    try:
        with open('/proc/1/cgroup', 'r') as f:
            return 'docker' in f.read() or 'container' in f.read()
    except:
        pass
    return False


def get_db_path():
    """Get database path, preferring /app/data/ for Docker compatibility"""
    # Only use /app/data if we're actually in Docker
    if is_running_in_docker() and os.path.exists('/app/data'):
        return '/app/data/teamarr.db'

    # Check if we have a local data directory
    base_dir = os.path.dirname(os.path.dirname(__file__))
    data_dir = os.path.join(base_dir, 'data')
    if os.path.exists(data_dir):
        return os.path.join(data_dir, 'teamarr.db')

    # Fallback to project root (backward compatible)
    return os.path.join(base_dir, 'teamarr.db')

DB_PATH = get_db_path()

def get_connection():
    """Get database connection with row factory for dict-like access"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# DATABASE HELPER UTILITIES - Consolidated patterns for connection handling
# =============================================================================

from contextlib import contextmanager

@contextmanager
def db_connection():
    """
    Context manager for database connections.

    Usage:
        with db_connection() as conn:
            result = conn.execute("SELECT * FROM teams").fetchall()
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def db_fetch_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    """
    Execute a query and return a single row as a dict.

    Args:
        query: SQL query string
        params: Query parameters

    Returns:
        Dict of the row, or None if not found
    """
    with db_connection() as conn:
        result = conn.execute(query, params).fetchone()
        return dict(result) if result else None


def db_fetch_all(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """
    Execute a query and return all rows as a list of dicts.

    Args:
        query: SQL query string
        params: Query parameters

    Returns:
        List of dicts
    """
    with db_connection() as conn:
        results = conn.execute(query, params).fetchall()
        return [dict(row) for row in results]


def db_execute(query: str, params: tuple = ()) -> int:
    """
    Execute a mutation query (INSERT/UPDATE/DELETE) and return affected rows.

    Args:
        query: SQL query string
        params: Query parameters

    Returns:
        Number of rows affected
    """
    with db_connection() as conn:
        conn.execute(query, params)
        conn.commit()
        return conn.total_changes


def db_insert(query: str, params: tuple = ()) -> int:
    """
    Execute an INSERT query and return the last inserted row ID.

    Args:
        query: SQL INSERT query string
        params: Query parameters

    Returns:
        ID of the inserted row
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

# =============================================================================
# LEAGUE CODE NORMALIZATION
# =============================================================================
# ESPN slugs are the single source of truth (stored in league_config.league_code).
# The league_id_aliases table maps friendly aliases back to ESPN slugs.
# These helpers ensure we always work with ESPN slugs internally.

# Cache for alias -> slug mapping (populated on first use)
_alias_to_slug_cache: Optional[Dict[str, str]] = None

# Hardcoded alias variants that map to ESPN slugs
# This handles cases where multiple aliases should map to the same slug
# (the DB table only stores one alias per slug for template variable output)
_ALIAS_VARIANTS = {
    # College basketball
    'ncaam': 'mens-college-basketball',
    'ncaaw': 'womens-college-basketball',
    # College football
    'ncaaf': 'college-football',
    # College hockey
    'ncaah': 'mens-college-hockey',
    'ncaawh': 'womens-college-hockey',
    # College volleyball (multiple alias formats)
    'ncaavbm': 'mens-college-volleyball',
    'ncaavb-m': 'mens-college-volleyball',
    'ncaavb': 'mens-college-volleyball',
    'ncaavbw': 'womens-college-volleyball',
    'ncaavb-w': 'womens-college-volleyball',
    'ncaawvb': 'womens-college-volleyball',
    # College soccer
    'ncaas': 'usa.ncaa.m.1',
    'ncaaws': 'usa.ncaa.w.1',
    # NBA G-League (multiple alias formats)
    'nbag': 'nba-development',
    'nba-g': 'nba-development',
    # Soccer leagues
    'epl': 'eng.1',
    'laliga': 'esp.1',
    'bundesliga': 'ger.1',
    'seriea': 'ita.1',
    'ligue1': 'fra.1',
    'mls': 'usa.1',
    'nwsl': 'usa.nwsl',
    'efl': 'eng.2',
    'efl1': 'eng.3',
    'ucl': 'uefa.champions',
}


def get_alias_to_slug_mapping() -> Dict[str, str]:
    """
    Get mapping from aliases to ESPN slugs.

    Combines hardcoded aliases (for variant handling) with DB aliases.
    DB aliases take precedence if there's a conflict.

    Returns:
        Dict mapping alias -> espn_slug (e.g., {'ncaaw': 'womens-college-basketball'})
    """
    global _alias_to_slug_cache
    if _alias_to_slug_cache is not None:
        return _alias_to_slug_cache

    # Start with hardcoded variants
    _alias_to_slug_cache = _ALIAS_VARIANTS.copy()

    # Add DB aliases (reverse the slug->alias to alias->slug)
    try:
        rows = db_fetch_all("SELECT espn_slug, alias FROM league_id_aliases")
        for row in rows:
            _alias_to_slug_cache[row['alias']] = row['espn_slug']
    except Exception:
        pass

    return _alias_to_slug_cache


def normalize_league_code(code: str) -> str:
    """
    Normalize a league code to its ESPN slug.

    If the code is an alias, returns the ESPN slug.
    If already an ESPN slug (or unknown), returns as-is.

    Args:
        code: League code (alias or ESPN slug)

    Returns:
        ESPN slug
    """
    if not code:
        return code

    mapping = get_alias_to_slug_mapping()
    return mapping.get(code, code)


def normalize_league_codes(codes: List[str]) -> List[str]:
    """
    Normalize a list of league codes to ESPN slugs.

    Args:
        codes: List of league codes (aliases or ESPN slugs)

    Returns:
        List of ESPN slugs (preserves order, deduplicates)
    """
    if not codes:
        return codes

    mapping = get_alias_to_slug_mapping()
    seen = set()
    result = []
    for code in codes:
        normalized = mapping.get(code, code)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def clear_alias_cache():
    """Clear the alias cache (call after modifying league_id_aliases)."""
    global _alias_to_slug_cache, _slug_to_alias_cache
    _alias_to_slug_cache = None
    _slug_to_alias_cache = None


# Cache for slug -> alias mapping (for display purposes)
_slug_to_alias_cache: Optional[Dict[str, str]] = None


def get_slug_to_alias_mapping() -> Dict[str, str]:
    """
    Get mapping from ESPN slugs to canonical aliases.

    This is used for display purposes (e.g., {league_id} template variable).
    Returns the canonical alias from the league_id_aliases table.

    Returns:
        Dict mapping espn_slug -> alias (e.g., {'womens-college-basketball': 'ncaaw'})
    """
    global _slug_to_alias_cache
    if _slug_to_alias_cache is not None:
        return _slug_to_alias_cache

    _slug_to_alias_cache = {}
    try:
        rows = db_fetch_all("SELECT espn_slug, alias FROM league_id_aliases")
        for row in rows:
            _slug_to_alias_cache[row['espn_slug']] = row['alias']
    except Exception:
        pass

    return _slug_to_alias_cache


def get_league_alias(slug: str) -> str:
    """
    Get the friendly alias for an ESPN slug.

    If no alias exists, returns the slug as-is.

    Args:
        slug: ESPN slug (e.g., 'womens-college-basketball')

    Returns:
        Alias (e.g., 'ncaaw') or original slug if no alias exists
    """
    if not slug:
        return slug

    mapping = get_slug_to_alias_mapping()
    return mapping.get(slug, slug)


def get_gracenote_category(league_code: str, league_name: str = '', sport: str = '') -> str:
    """
    Get the Gracenote-compatible category for a league.

    First checks league_config.gracenote_category for a curated value.
    Falls back to auto-generated "{league_name} {Sport}" format.

    Args:
        league_code: ESPN league slug (e.g., 'nfl', 'mens-college-basketball', 'eng.1')
        league_name: Display name of the league (fallback for unknown leagues)
        sport: Sport name (e.g., 'football', 'basketball', 'soccer')

    Returns:
        Gracenote-compatible category string (e.g., 'NFL Football', 'College Basketball')
    """
    if not league_code:
        # If no league code, try to construct from league_name and sport
        if league_name and sport:
            sport_display = sport.capitalize()
            return f"{league_name} {sport_display}"
        return ''

    # Check league_config for curated gracenote_category
    result = db_fetch_one(
        "SELECT gracenote_category, league_name FROM league_config WHERE league_code = ?",
        (league_code,)
    )

    if result and result.get('gracenote_category'):
        return result['gracenote_category']

    # Fallback: auto-generate from league_name and sport
    # Use league_name from DB if available, otherwise use provided league_name
    display_name = (result.get('league_name') if result else None) or league_name
    if display_name and sport:
        sport_display = sport.capitalize()
        return f"{display_name} {sport_display}"

    # Last resort: just return league_name or empty
    return display_name or ''


# =============================================================================
# SCHEMA VERSIONING
# =============================================================================
# Each migration has a version number. Migrations only run if current version < target version.
# This ensures migrations are idempotent and can be safely re-run.
#
# Version History:
#   0: Base schema (original tables - teams, templates, settings, etc.)
#   1: Dispatcharr integration + time format + lifecycle settings
#   2: Template enhancements (conditional descriptions, event support)
#   3: EPG history enhancements (filler counts, stats breakdown)
#   4: Event EPG tables (event_epg_groups, team_aliases, managed_channels)
#   5: Event EPG groups enhancements (custom regex, filtering stats)
#   6: Managed channels enhancements (logo tracking)
#   7: Data fixes (NCAA logos, per-group timing cleanup)
#   8: Channel Lifecycle V2 (multi-stream, history, reconciliation, parent groups)
#   9-11: Various enhancements (see schema.sql comments)
#   12: Soccer multi-league cache tables (with league_tags JSON array)
#   15: Team-league cache tables for non-soccer sports
#   16: Multi-sport event groups (is_multi_sport, enabled_leagues, etc.)
#   23: Stream fingerprint cache for EPG generation optimization
# =============================================================================

CURRENT_SCHEMA_VERSION = 32


def get_schema_version(conn) -> int:
    """Get current schema version from database."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT schema_version FROM settings WHERE id = 1")
        row = cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
    except Exception:
        pass
    return 0  # No version = version 0 (original schema)


def set_schema_version(conn, version: int):
    """Update schema version in database."""
    cursor = conn.cursor()
    try:
        # First ensure the column exists
        cursor.execute("PRAGMA table_info(settings)")
        columns = {row[1] for row in cursor.fetchall()}
        if 'schema_version' not in columns:
            cursor.execute("ALTER TABLE settings ADD COLUMN schema_version INTEGER DEFAULT 0")
            conn.commit()

        cursor.execute("UPDATE settings SET schema_version = ? WHERE id = 1", (version,))
        conn.commit()
    except Exception as e:
        print(f"  ⚠️ Could not set schema version: {e}")


def run_migrations(conn):
    """
    Run database migrations based on schema version.

    Migrations are versioned and only run if current version < migration version.
    Each migration is idempotent (safe to run multiple times).

    IMPORTANT: Idempotent column additions run ALWAYS (regardless of version) to
    handle cases where schema.sql is missing columns. Version checks are only used
    for one-time data migrations or table creation.
    """
    cursor = conn.cursor()
    current_version = get_schema_version(conn)
    migrations_run = 0

    print(f"  📊 Current schema version: {current_version}, target: {CURRENT_SCHEMA_VERSION}")

    # Helper functions for migrations
    def add_columns_if_missing(table_name, columns):
        """Helper to add multiple columns to a table if they don't exist"""
        nonlocal migrations_run
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing = {row[1] for row in cursor.fetchall()}

        for col_name, col_def in columns:
            if col_name not in existing:
                try:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
                    migrations_run += 1
                    print(f"    ✅ Added column: {table_name}.{col_name}")
                except Exception as e:
                    print(f"    ⚠️ Could not add column {table_name}.{col_name}: {e}")

        conn.commit()

    def table_exists(table_name):
        """Check if a table exists"""
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        return cursor.fetchone() is not None

    def create_index_if_not_exists(index_name, table_name, columns, where_clause=None):
        """Create an index if it doesn't exist"""
        try:
            sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({columns})"
            if where_clause:
                sql += f" WHERE {where_clause}"
            cursor.execute(sql)
            conn.commit()
        except Exception as e:
            print(f"    ⚠️ Could not create index {index_name}: {e}")

    # =========================================================================
    # 1. SETTINGS TABLE MIGRATIONS
    # =========================================================================
    settings_columns = [
        # Dispatcharr integration
        ("dispatcharr_enabled", "BOOLEAN DEFAULT 0"),
        ("dispatcharr_url", "TEXT DEFAULT 'http://localhost:9191'"),
        ("dispatcharr_username", "TEXT"),
        ("dispatcharr_password", "TEXT"),
        ("dispatcharr_epg_id", "INTEGER"),
        ("dispatcharr_last_sync", "TEXT"),
        # Time format
        ("time_format", "TEXT DEFAULT '12h'"),
        ("show_timezone", "BOOLEAN DEFAULT 1"),
        # Channel lifecycle
        ("channel_create_timing", "TEXT DEFAULT 'same_day'"),
        ("channel_delete_timing", "TEXT DEFAULT 'same_day'"),
        ("include_final_events", "INTEGER DEFAULT 0"),
        # Event matching
        ("event_lookahead_days", "INTEGER DEFAULT 7"),
        # Scheduler time configuration
        ("schedule_time", "TEXT DEFAULT '00'"),  # For hourly: minute (00-59), for daily: HH:MM
    ]
    add_columns_if_missing("settings", settings_columns)

    # =========================================================================
    # 2. TEMPLATES TABLE MIGRATIONS
    # =========================================================================
    templates_columns = [
        # Conditional descriptions
        ("postgame_conditional_enabled", "BOOLEAN DEFAULT 0"),
        ("postgame_description_final", "TEXT DEFAULT 'The {team_name} {result_text.last} the {opponent.last} {final_score.last} {overtime_text.last}'"),
        ("postgame_description_not_final", "TEXT DEFAULT 'The game between the {team_name} and {opponent.last} on {game_day.last} {game_date.last} has not yet ended.'"),
        ("idle_conditional_enabled", "BOOLEAN DEFAULT 0"),
        ("idle_description_final", "TEXT DEFAULT 'The {team_name} {result_text.last} the {opponent.last} {final_score.last}. Next: {opponent.next} on {game_date.next}'"),
        ("idle_description_not_final", "TEXT DEFAULT 'The {team_name} last played {opponent.last} on {game_date.last}. Next: {opponent.next} on {game_date.next}'"),
        # Event template support
        ("template_type", "TEXT DEFAULT 'team'"),
        ("channel_name", "TEXT"),
        ("channel_logo_url", "TEXT"),
    ]
    add_columns_if_missing("templates", templates_columns)

    # =========================================================================
    # 3. EPG HISTORY TABLE MIGRATIONS
    # =========================================================================
    epg_history_columns = [
        # Filler counts
        ("num_pregame", "INTEGER DEFAULT 0"),
        ("num_postgame", "INTEGER DEFAULT 0"),
        ("num_idle", "INTEGER DEFAULT 0"),
        # Team-based breakdown
        ("team_based_channels", "INTEGER DEFAULT 0"),
        ("team_based_events", "INTEGER DEFAULT 0"),
        ("team_based_pregame", "INTEGER DEFAULT 0"),
        ("team_based_postgame", "INTEGER DEFAULT 0"),
        ("team_based_idle", "INTEGER DEFAULT 0"),
        # Event-based breakdown
        ("event_based_channels", "INTEGER DEFAULT 0"),
        ("event_based_events", "INTEGER DEFAULT 0"),
        ("event_based_pregame", "INTEGER DEFAULT 0"),
        ("event_based_postgame", "INTEGER DEFAULT 0"),
        # Quality stats
        ("unresolved_vars_count", "INTEGER DEFAULT 0"),
        ("coverage_gaps_count", "INTEGER DEFAULT 0"),
        ("warnings_json", "TEXT"),
        # Event-based filtering stats (aggregated across all groups)
        ("event_total_streams", "INTEGER DEFAULT 0"),  # Sum of raw provider streams
        ("event_filtered_no_indicator", "INTEGER DEFAULT 0"),  # Sum filtered by built-in
        ("event_filtered_include_regex", "INTEGER DEFAULT 0"),  # Sum not matching inclusion regex
        ("event_filtered_exclude_regex", "INTEGER DEFAULT 0"),  # Sum filtered by exclusion regex
        ("event_filtered_outside_lookahead", "INTEGER DEFAULT 0"),  # Sum outside date range
        ("event_filtered_final", "INTEGER DEFAULT 0"),  # Sum of final events excluded
        ("event_eligible_streams", "INTEGER DEFAULT 0"),  # Sum of streams that passed filters
        ("event_matched_streams", "INTEGER DEFAULT 0"),  # Sum of ESPN matches
    ]
    add_columns_if_missing("epg_history", epg_history_columns)

    # =========================================================================
    # 4. EVENT EPG TABLES (create if missing)
    # =========================================================================

    # 4a. event_epg_groups table
    if not table_exists("event_epg_groups"):
        try:
            cursor.execute("""
                CREATE TABLE event_epg_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    dispatcharr_group_id INTEGER NOT NULL UNIQUE,
                    dispatcharr_account_id INTEGER NOT NULL,
                    group_name TEXT NOT NULL,
                    account_name TEXT,
                    assigned_league TEXT NOT NULL,
                    assigned_sport TEXT NOT NULL,
                    event_template_id INTEGER REFERENCES templates(id) ON DELETE SET NULL,
                    enabled INTEGER DEFAULT 1,
                    refresh_interval_minutes INTEGER DEFAULT 60,
                    channel_start INTEGER,
                    channel_create_timing TEXT DEFAULT 'same_day',
                    channel_delete_timing TEXT DEFAULT 'same_day',
                    channel_group_id INTEGER,
                    custom_regex TEXT,
                    custom_regex_enabled INTEGER DEFAULT 0,
                    custom_regex_team1 TEXT,
                    custom_regex_team2 TEXT,
                    custom_regex_date TEXT,
                    custom_regex_time TEXT,
                    last_refresh TIMESTAMP,
                    stream_count INTEGER DEFAULT 0,
                    matched_count INTEGER DEFAULT 0
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_epg_groups_league ON event_epg_groups(assigned_league)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_epg_groups_enabled ON event_epg_groups(enabled)")
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_event_epg_groups_timestamp
                AFTER UPDATE ON event_epg_groups FOR EACH ROW
                BEGIN UPDATE event_epg_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END
            """)
            migrations_run += 1
            print("  ✅ Created table: event_epg_groups")
        except Exception as e:
            print(f"  ⚠️ Could not create event_epg_groups table: {e}")
        conn.commit()
    else:
        # Add columns to existing event_epg_groups table
        event_group_columns = [
            ("event_template_id", "INTEGER REFERENCES templates(id) ON DELETE SET NULL"),
            ("channel_start", "INTEGER"),
            ("channel_create_timing", "TEXT DEFAULT 'same_day'"),
            ("channel_delete_timing", "TEXT DEFAULT 'same_day'"),
            ("account_name", "TEXT"),
            ("channel_group_id", "INTEGER"),
            ("stream_profile_id", "INTEGER"),
            ("channel_profile_id", "INTEGER"),  # Legacy - single profile
            ("channel_profile_ids", "TEXT"),  # JSON array of profile IDs
            ("custom_regex", "TEXT"),  # Deprecated - legacy single regex
            ("custom_regex_enabled", "INTEGER DEFAULT 0"),  # Deprecated - use individual enables
            ("custom_regex_team1", "TEXT"),  # Deprecated - use custom_regex_teams
            ("custom_regex_team2", "TEXT"),  # Deprecated - use custom_regex_teams
            ("custom_regex_teams", "TEXT"),  # Combined pattern with (?P<team1>...) and (?P<team2>...)
            ("custom_regex_teams_enabled", "INTEGER DEFAULT 0"),  # Enable custom teams regex
            ("custom_regex_date", "TEXT"),
            ("custom_regex_date_enabled", "INTEGER DEFAULT 0"),  # Enable custom date regex
            ("custom_regex_time", "TEXT"),
            ("custom_regex_time_enabled", "INTEGER DEFAULT 0"),  # Enable custom time regex
            ("stream_include_regex", "TEXT"),  # User regex to include only matching streams
            ("stream_include_regex_enabled", "INTEGER DEFAULT 0"),  # Enable inclusion regex
            ("stream_exclude_regex", "TEXT"),  # User regex to exclude streams from matching
            ("stream_exclude_regex_enabled", "INTEGER DEFAULT 0"),  # Enable exclusion regex
            ("skip_builtin_filter", "INTEGER DEFAULT 0"),  # Skip built-in game indicator filter
            # Filtering stats (per-group breakdown)
            ("total_stream_count", "INTEGER DEFAULT 0"),  # Raw count from provider
            ("filtered_no_indicator", "INTEGER DEFAULT 0"),  # No vs/@/at (built-in filter)
            ("filtered_include_regex", "INTEGER DEFAULT 0"),  # Didn't match user's inclusion regex
            ("filtered_exclude_regex", "INTEGER DEFAULT 0"),  # Matched user's exclusion regex
            ("filtered_outside_lookahead", "INTEGER DEFAULT 0"),  # Date outside lookahead window
            ("filtered_final", "INTEGER DEFAULT 0"),  # Final events (when exclude setting on)
            # Note: stream_count is now "eligible" streams, matched_count already exists
            ("channel_group_name", "TEXT"),  # Dispatcharr channel group name (for UI display)
        ]
        add_columns_if_missing("event_epg_groups", event_group_columns)

    # 4b. team_aliases table
    if not table_exists("team_aliases"):
        try:
            cursor.execute("""
                CREATE TABLE team_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alias TEXT NOT NULL,
                    league TEXT NOT NULL,
                    espn_team_id TEXT NOT NULL,
                    espn_team_name TEXT NOT NULL,
                    UNIQUE(alias, league)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_team_aliases_league ON team_aliases(league)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_team_aliases_alias ON team_aliases(alias)")
            migrations_run += 1
            print("  ✅ Created table: team_aliases")
        except Exception as e:
            print(f"  ⚠️ Could not create team_aliases table: {e}")
        conn.commit()

    # 4c. managed_channels table
    if not table_exists("managed_channels"):
        try:
            cursor.execute("""
                CREATE TABLE managed_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    event_epg_group_id INTEGER NOT NULL REFERENCES event_epg_groups(id) ON DELETE CASCADE,
                    dispatcharr_channel_id INTEGER NOT NULL UNIQUE,
                    dispatcharr_stream_id INTEGER NOT NULL,
                    dispatcharr_logo_id INTEGER,
                    channel_number INTEGER NOT NULL,
                    channel_name TEXT NOT NULL,
                    tvg_id TEXT,
                    espn_event_id TEXT,
                    event_date TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    scheduled_delete_at TIMESTAMP,
                    deleted_at TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_managed_channels_group ON managed_channels(event_epg_group_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_managed_channels_event ON managed_channels(espn_event_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_managed_channels_delete ON managed_channels(scheduled_delete_at)")
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_managed_channels_timestamp
                AFTER UPDATE ON managed_channels FOR EACH ROW
                BEGIN UPDATE managed_channels SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END
            """)
            migrations_run += 1
            print("  ✅ Created table: managed_channels")
        except Exception as e:
            print(f"  ⚠️ Could not create managed_channels table: {e}")
        conn.commit()
    else:
        # Add columns if missing
        add_columns_if_missing("managed_channels", [
            ("dispatcharr_logo_id", "INTEGER"),
            ("logo_deleted", "INTEGER"),  # 1=deleted, 0=failed to delete, NULL=no logo was present
            ("channel_profile_id", "INTEGER")  # Track which channel profile the channel was added to
        ])

    # =========================================================================
    # 5. DATA FIXES
    # =========================================================================

    # Fix NCAA league logos (use NCAA.com sport banners instead of broken ESPN URLs)
    ncaa_logo_fixes = [
        ("ncaaf", "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/football.png"),
        ("ncaam", "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png"),
        ("ncaaw", "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png"),
    ]
    for league_code, logo_url in ncaa_logo_fixes:
        try:
            cursor.execute(
                "UPDATE league_config SET logo_url = ? WHERE league_code = ? AND logo_url != ?",
                (logo_url, league_code, logo_url)
            )
            if cursor.rowcount > 0:
                print(f"  ✅ Fixed logo for {league_code}")
        except Exception:
            pass
    conn.commit()

    # =========================================================================
    # 6. CLEAR PER-GROUP TIMING SETTINGS (now global-only)
    # =========================================================================
    # Per-group channel_create_timing and channel_delete_timing are no longer used.
    # All groups now use the global settings. Clear stale per-group values.
    try:
        cursor.execute("""
            UPDATE event_epg_groups
            SET channel_create_timing = NULL, channel_delete_timing = NULL
            WHERE channel_create_timing IS NOT NULL OR channel_delete_timing IS NOT NULL
        """)
        if cursor.rowcount > 0:
            migrations_run += 1
            print(f"  ✅ Cleared per-group timing settings from {cursor.rowcount} groups (now using global settings)")
        conn.commit()
    except Exception as e:
        print(f"  ⚠️ Could not clear per-group timing settings: {e}")

    # =========================================================================
    # 7. CHANNEL LIFECYCLE V2 - Enhanced tracking & reconciliation
    # =========================================================================

    # 7a. Create managed_channel_streams table (multi-stream support)
    if not table_exists("managed_channel_streams"):
        try:
            cursor.execute("""
                CREATE TABLE managed_channel_streams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    managed_channel_id INTEGER NOT NULL,
                    dispatcharr_stream_id INTEGER NOT NULL,

                    -- Stream info
                    stream_name TEXT,
                    m3u_account_id INTEGER,
                    m3u_account_name TEXT,

                    -- Source tracking
                    source_group_id INTEGER NOT NULL,
                    source_group_type TEXT NOT NULL DEFAULT 'parent',

                    -- Ordering
                    priority INTEGER DEFAULT 0,

                    -- Lifecycle
                    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    removed_at TEXT,
                    remove_reason TEXT,

                    -- Sync state
                    last_verified_at TEXT,
                    in_dispatcharr INTEGER DEFAULT 1,

                    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE,
                    FOREIGN KEY (source_group_id) REFERENCES event_epg_groups(id)
                )
            """)
            migrations_run += 1
            print("  ✅ Created table: managed_channel_streams")

            # Create indexes
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mcs_unique
                ON managed_channel_streams(managed_channel_id, dispatcharr_stream_id)
                WHERE removed_at IS NULL
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mcs_stream ON managed_channel_streams(dispatcharr_stream_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mcs_source_group ON managed_channel_streams(source_group_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mcs_channel ON managed_channel_streams(managed_channel_id)")
        except Exception as e:
            print(f"  ⚠️ Could not create managed_channel_streams table: {e}")
        conn.commit()

    # 7b. Create managed_channel_history table (audit trail)
    if not table_exists("managed_channel_history"):
        try:
            cursor.execute("""
                CREATE TABLE managed_channel_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    managed_channel_id INTEGER NOT NULL,

                    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    change_type TEXT NOT NULL,
                    change_source TEXT,

                    field_name TEXT,
                    old_value TEXT,
                    new_value TEXT,

                    notes TEXT,

                    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id)
                )
            """)
            migrations_run += 1
            print("  ✅ Created table: managed_channel_history")

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mch_channel ON managed_channel_history(managed_channel_id, changed_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mch_time ON managed_channel_history(changed_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mch_type ON managed_channel_history(change_type)")
        except Exception as e:
            print(f"  ⚠️ Could not create managed_channel_history table: {e}")
        conn.commit()

    # 7c. Add new columns to managed_channels
    managed_channels_v2_columns = [
        # Identity
        ("dispatcharr_uuid", "TEXT"),  # Immutable UUID from Dispatcharr
        ("primary_stream_id", "INTEGER"),
        ("logo_url", "TEXT"),

        # Event context
        ("home_team_abbrev", "TEXT"),
        ("home_team_logo", "TEXT"),
        ("away_team_abbrev", "TEXT"),
        ("away_team_logo", "TEXT"),
        ("event_name", "TEXT"),
        ("league", "TEXT"),
        ("sport", "TEXT"),
        ("venue", "TEXT"),
        ("broadcast", "TEXT"),

        # Lifecycle
        ("delete_reason", "TEXT"),

        # Sync state
        ("last_verified_at", "TEXT"),
        ("sync_status", "TEXT DEFAULT 'created'"),
        ("sync_notes", "TEXT"),

        # Channel settings tracking
        ("channel_group_id", "INTEGER"),
        ("stream_profile_id", "INTEGER"),
    ]
    add_columns_if_missing("managed_channels", managed_channels_v2_columns)

    # 7d. Add new columns to event_epg_groups (parent/child, duplicate handling)
    event_epg_groups_v2_columns = [
        ("parent_group_id", "INTEGER"),
        ("duplicate_event_handling", "TEXT DEFAULT 'consolidate'"),
    ]
    add_columns_if_missing("event_epg_groups", event_epg_groups_v2_columns)

    # 7e. Add reconciliation settings
    reconciliation_settings = [
        ("reconcile_on_epg_generation", "INTEGER DEFAULT 1"),
        ("reconcile_on_startup", "INTEGER DEFAULT 1"),
        ("auto_fix_orphan_teamarr", "INTEGER DEFAULT 1"),
        ("auto_fix_orphan_dispatcharr", "INTEGER DEFAULT 0"),
        ("auto_fix_duplicates", "INTEGER DEFAULT 0"),
        ("channel_history_retention_days", "INTEGER DEFAULT 90"),
        ("default_duplicate_event_handling", "TEXT DEFAULT 'consolidate'"),
    ]
    add_columns_if_missing("settings", reconciliation_settings)

    # 7f. Populate tvg_id from espn_event_id where missing
    try:
        cursor.execute("""
            UPDATE managed_channels
            SET tvg_id = 'teamarr-event-' || espn_event_id
            WHERE tvg_id IS NULL AND espn_event_id IS NOT NULL
        """)
        if cursor.rowcount > 0:
            print(f"  ✅ Populated tvg_id for {cursor.rowcount} managed channels")
        conn.commit()
    except Exception as e:
        print(f"  ⚠️ Could not populate tvg_id: {e}")

    # 7g. Migrate existing stream references to managed_channel_streams
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO managed_channel_streams
            (managed_channel_id, dispatcharr_stream_id, source_group_id, source_group_type, priority)
            SELECT id, dispatcharr_stream_id, event_epg_group_id, 'parent', 0
            FROM managed_channels
            WHERE dispatcharr_stream_id IS NOT NULL
              AND deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM managed_channel_streams mcs
                  WHERE mcs.managed_channel_id = managed_channels.id
                    AND mcs.dispatcharr_stream_id = managed_channels.dispatcharr_stream_id
              )
        """)
        if cursor.rowcount > 0:
            migrations_run += 1
            print(f"  ✅ Migrated {cursor.rowcount} existing streams to managed_channel_streams")
        conn.commit()
    except Exception as e:
        print(f"  ⚠️ Could not migrate streams: {e}")

    # 7h. Create additional indexes for managed_channels
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mc_dispatcharr_id ON managed_channels(dispatcharr_channel_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mc_tvg_id ON managed_channels(tvg_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mc_sync_status ON managed_channels(sync_status) WHERE deleted_at IS NULL")
    except Exception as e:
        print(f"  ⚠️ Could not create managed_channels indexes: {e}")
    conn.commit()

    # 7i. Create index for parent/child groups
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_eeg_parent ON event_epg_groups(parent_group_id) WHERE parent_group_id IS NOT NULL")
    except Exception as e:
        print(f"  ⚠️ Could not create parent group index: {e}")
    conn.commit()

    # =========================================================================
    # 11. MULTI-CHANNEL PROFILE SUPPORT
    # =========================================================================
    # Add channel_profile_ids column to event_epg_groups (JSON array of profile IDs)
    # Note: add_columns_if_missing is idempotent, runs always
    add_columns_if_missing("event_epg_groups", [
        ("channel_profile_ids", "TEXT"),  # JSON array, e.g. "[1, 2, 3]"
    ])

    # Add channel_profile_ids column to managed_channels (tracks which profiles channel was added to)
    add_columns_if_missing("managed_channels", [
        ("channel_profile_ids", "TEXT"),  # JSON array, e.g. "[1, 2, 3]"
    ])

    # Migrate existing single channel_profile_id to channel_profile_ids array
    # Note: This is idempotent (only updates rows where channel_profile_ids is NULL/empty)
    try:
        # For event_epg_groups
        cursor.execute("""
            UPDATE event_epg_groups
            SET channel_profile_ids = '[' || channel_profile_id || ']'
            WHERE channel_profile_id IS NOT NULL
              AND (channel_profile_ids IS NULL OR channel_profile_ids = '')
        """)
        if cursor.rowcount > 0:
            print(f"  ✅ Migrated {cursor.rowcount} event group(s) to multi-profile format")

        # For managed_channels
        cursor.execute("""
            UPDATE managed_channels
            SET channel_profile_ids = '[' || channel_profile_id || ']'
            WHERE channel_profile_id IS NOT NULL
              AND (channel_profile_ids IS NULL OR channel_profile_ids = '')
        """)
        if cursor.rowcount > 0:
            print(f"  ✅ Migrated {cursor.rowcount} managed channel(s) to multi-profile format")

        conn.commit()
    except Exception as e:
        print(f"  ⚠️ Could not migrate channel profiles: {e}")

    # =========================================================================
    # 12. SOCCER MULTI-LEAGUE CACHE TABLES
    # =========================================================================
    if current_version < 12:
        print("  🔄 Running migration 12: Soccer multi-league cache tables...")

        # 12a. soccer_team_leagues table
        if not table_exists("soccer_team_leagues"):
            try:
                cursor.execute("""
                    CREATE TABLE soccer_team_leagues (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        espn_team_id TEXT NOT NULL,
                        league_slug TEXT NOT NULL,
                        team_name TEXT,
                        team_type TEXT,
                        default_league TEXT,
                        last_seen TEXT,
                        UNIQUE(espn_team_id, league_slug)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_stl_team ON soccer_team_leagues(espn_team_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_stl_league ON soccer_team_leagues(league_slug)")
                migrations_run += 1
                print("    ✅ Created table: soccer_team_leagues")
            except Exception as e:
                print(f"    ⚠️ Could not create soccer_team_leagues table: {e}")
            conn.commit()

        # 12b. soccer_leagues_cache table
        if not table_exists("soccer_leagues_cache"):
            try:
                cursor.execute("""
                    CREATE TABLE soccer_leagues_cache (
                        league_slug TEXT PRIMARY KEY,
                        league_name TEXT,
                        league_abbrev TEXT,
                        league_tags TEXT,
                        league_logo_url TEXT,
                        team_count INTEGER,
                        last_seen TEXT
                    )
                """)
                migrations_run += 1
                print("    ✅ Created table: soccer_leagues_cache")
            except Exception as e:
                print(f"    ⚠️ Could not create soccer_leagues_cache table: {e}")
            conn.commit()

    # 12b-fix. Rename league_category to league_tags if old column exists (dev migration)
    # This handles dev databases that ran migration 12 before the tags refactor
    # Runs ALWAYS (outside version check) to fix dev databases
    if table_exists("soccer_leagues_cache"):
        try:
            cursor.execute("PRAGMA table_info(soccer_leagues_cache)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'league_category' in columns and 'league_tags' not in columns:
                # SQLite doesn't support RENAME COLUMN in older versions, recreate table
                cursor.execute("""
                    CREATE TABLE soccer_leagues_cache_new (
                        league_slug TEXT PRIMARY KEY,
                        league_name TEXT,
                        league_abbrev TEXT,
                        league_tags TEXT,
                        league_logo_url TEXT,
                        team_count INTEGER,
                        last_seen TEXT
                    )
                """)
                cursor.execute("""
                    INSERT INTO soccer_leagues_cache_new
                    SELECT league_slug, league_name, league_abbrev, league_category, league_logo_url, team_count, last_seen
                    FROM soccer_leagues_cache
                """)
                cursor.execute("DROP TABLE soccer_leagues_cache")
                cursor.execute("ALTER TABLE soccer_leagues_cache_new RENAME TO soccer_leagues_cache")
                conn.commit()
                print("  ✅ Renamed league_category to league_tags (dev migration)")
        except Exception as e:
            print(f"  ⚠️ Could not rename league_category column: {e}")

    # =========================================================================
    # 12. SOCCER MULTI-LEAGUE CACHE TABLES (continued)
    # =========================================================================
    if current_version < 12:
        # 12c. soccer_cache_meta table
        if not table_exists("soccer_cache_meta"):
            try:
                cursor.execute("""
                    CREATE TABLE soccer_cache_meta (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        last_full_refresh TEXT,
                        leagues_processed INTEGER,
                        teams_indexed INTEGER,
                        refresh_duration_seconds REAL,
                        next_scheduled_refresh TEXT
                    )
                """)
                cursor.execute("INSERT OR IGNORE INTO soccer_cache_meta (id) VALUES (1)")
                migrations_run += 1
                print("    ✅ Created table: soccer_cache_meta")
            except Exception as e:
                print(f"    ⚠️ Could not create soccer_cache_meta table: {e}")
            conn.commit()

        # 12d. Settings column for cache frequency
        add_columns_if_missing("settings", [
            ("soccer_cache_refresh_frequency", "TEXT DEFAULT 'weekly'"),
        ])

    # =========================================================================
    # 13. CONSOLIDATION EXCEPTION KEYWORDS (per-group version - deprecated)
    # =========================================================================
    if current_version < 13:
        print("  🔄 Running migration 13: Consolidation exception keywords...")

        # 13a. Create consolidation_exception_keywords table
        if not table_exists("consolidation_exception_keywords"):
            try:
                cursor.execute("""
                    CREATE TABLE consolidation_exception_keywords (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id INTEGER NOT NULL,
                        keywords TEXT NOT NULL,
                        behavior TEXT NOT NULL DEFAULT 'consolidate',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_cek_group ON consolidation_exception_keywords(group_id)")
                migrations_run += 1
                print("    ✅ Created table: consolidation_exception_keywords")
            except Exception as e:
                print(f"    ⚠️ Could not create consolidation_exception_keywords table: {e}")
            conn.commit()

        # 13b. Add exception_keyword column to managed_channels
        add_columns_if_missing("managed_channels", [
            ("exception_keyword", "TEXT"),
        ])

        # 13c. Add exception_keyword column to managed_channel_streams
        add_columns_if_missing("managed_channel_streams", [
            ("exception_keyword", "TEXT"),
        ])

        conn.commit()

    # =========================================================================
    # 14. MAKE EXCEPTION KEYWORDS GLOBAL (remove group_id)
    # =========================================================================
    if current_version < 14:
        print("  🔄 Running migration 14: Make exception keywords global...")

        # Drop group_id column by recreating the table (SQLite doesn't support DROP COLUMN directly)
        if table_exists("consolidation_exception_keywords"):
            try:
                # Create new table without group_id
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS consolidation_exception_keywords_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        keywords TEXT NOT NULL,
                        behavior TEXT NOT NULL DEFAULT 'consolidate',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Copy data (ignoring duplicates since keywords are now global)
                cursor.execute("""
                    INSERT INTO consolidation_exception_keywords_new (keywords, behavior, created_at)
                    SELECT DISTINCT keywords, behavior, created_at
                    FROM consolidation_exception_keywords
                """)

                # Drop old table and rename
                cursor.execute("DROP TABLE consolidation_exception_keywords")
                cursor.execute("ALTER TABLE consolidation_exception_keywords_new RENAME TO consolidation_exception_keywords")

                # Drop old index (no longer needed)
                cursor.execute("DROP INDEX IF EXISTS idx_cek_group")

                migrations_run += 1
                print("    ✅ Migrated consolidation_exception_keywords to global (removed group_id)")
            except Exception as e:
                print(f"    ⚠️ Could not migrate exception keywords table: {e}")
            conn.commit()

    # =========================================================================
    # 15. TEAM LEAGUE CACHE TABLES (Multi-Sport Support)
    # =========================================================================
    if current_version < 15:
        print("  🔄 Running migration 15: Create team league cache tables...")

        # Create team_league_cache table
        if not table_exists("team_league_cache"):
            try:
                cursor.execute("""
                    CREATE TABLE team_league_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        league_code TEXT NOT NULL,
                        espn_team_id TEXT NOT NULL,
                        team_name TEXT NOT NULL,
                        team_abbrev TEXT,
                        team_short_name TEXT,
                        sport TEXT NOT NULL,
                        UNIQUE(league_code, espn_team_id)
                    )
                """)
                migrations_run += 1
                print("    ✅ Created team_league_cache table")
            except Exception as e:
                print(f"    ⚠️ Could not create team_league_cache table: {e}")

        # Create indexes for team_league_cache
        create_index_if_not_exists("idx_tlc_name", "team_league_cache", "team_name COLLATE NOCASE")
        create_index_if_not_exists("idx_tlc_abbrev", "team_league_cache", "team_abbrev COLLATE NOCASE")
        create_index_if_not_exists("idx_tlc_short", "team_league_cache", "team_short_name COLLATE NOCASE")
        create_index_if_not_exists("idx_tlc_league", "team_league_cache", "league_code")

        # Create team_league_cache_meta table
        if not table_exists("team_league_cache_meta"):
            try:
                cursor.execute("""
                    CREATE TABLE team_league_cache_meta (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        last_refresh TEXT,
                        leagues_processed INTEGER DEFAULT 0,
                        teams_indexed INTEGER DEFAULT 0
                    )
                """)
                cursor.execute("INSERT OR IGNORE INTO team_league_cache_meta (id) VALUES (1)")
                migrations_run += 1
                print("    ✅ Created team_league_cache_meta table")
            except Exception as e:
                print(f"    ⚠️ Could not create team_league_cache_meta table: {e}")

        conn.commit()

    # =========================================================================
    # 16. MULTI-SPORT EVENT GROUPS
    # =========================================================================
    if current_version < 16:
        print("  📦 Running migration 16: Multi-sport event groups...")
        cursor = conn.cursor()

        # Add multi-sport columns to event_epg_groups
        add_columns_if_missing('event_epg_groups', [
            ('is_multi_sport', 'INTEGER DEFAULT 0'),
            ('enabled_leagues', 'TEXT'),  # JSON array, NULL = all leagues
            ('channel_sort_order', "TEXT DEFAULT 'time'"),  # time, sport_time, league_time
            ('overlap_handling', "TEXT DEFAULT 'add_stream'"),  # add_stream, skip, create_all
        ])
        migrations_run += 1
        print("    ✅ Added multi-sport columns to event_epg_groups")

        # Note: consolidation_exception_keywords table is global (not per-group)
        # It was already created in an earlier migration, no changes needed

        # Add team_cache_refresh_frequency to settings
        add_columns_if_missing('settings', [
            ('team_cache_refresh_frequency', "TEXT DEFAULT 'weekly'"),
        ])
        print("    ✅ Added team_cache_refresh_frequency to settings")

        conn.commit()

    # =========================================================================
    # 17. NCAA SOCCER LEAGUES (legacy - replaced by migration 18)
    # =========================================================================
    # Skipped - migration 18 adds these with correct ESPN slugs

    # =========================================================================
    # 18. LEAGUE CODE NORMALIZATION - Use ESPN slugs as league_code
    # =========================================================================
    if current_version < 18:
        print("  🔄 Running migration 18: Normalizing league codes to ESPN slugs...")

        # 18a. Create league_id_aliases table
        if not table_exists("league_id_aliases"):
            try:
                cursor.execute("""
                    CREATE TABLE league_id_aliases (
                        espn_slug TEXT PRIMARY KEY,
                        alias TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                migrations_run += 1
                print("    ✅ Created table: league_id_aliases")
            except Exception as e:
                print(f"    ⚠️ Could not create league_id_aliases table: {e}")
            conn.commit()

        # 18b. Define league code mappings (old_code -> new_espn_slug)
        league_code_mappings = [
            # Basketball
            ('nba-g', 'nba-development'),
            ('ncaam', 'mens-college-basketball'),
            ('ncaaw', 'womens-college-basketball'),
            # Football
            ('ncaaf', 'college-football'),
            # Hockey
            ('ncaah', 'mens-college-hockey'),
            # Soccer
            ('epl', 'eng.1'),
            ('laliga', 'esp.1'),
            ('bundesliga', 'ger.1'),
            ('seriea', 'ita.1'),
            ('ligue1', 'fra.1'),
            ('mls', 'usa.1'),
            ('nwsl', 'usa.nwsl'),
            ('efl', 'eng.2'),
            ('efl1', 'eng.3'),
            # Volleyball
            ('ncaavb-m', 'mens-college-volleyball'),
            ('ncaavb-w', 'womens-college-volleyball'),
        ]

        # 18c. Update league_config with new league_codes
        for old_code, new_code in league_code_mappings:
            try:
                cursor.execute(
                    "UPDATE league_config SET league_code = ? WHERE league_code = ?",
                    (new_code, old_code)
                )
                if cursor.rowcount > 0:
                    print(f"    ✅ Updated league_config: {old_code} → {new_code}")
            except Exception as e:
                print(f"    ⚠️ Could not update league_config {old_code}: {e}")
        conn.commit()

        # 18d. Update teams table references
        for old_code, new_code in league_code_mappings:
            try:
                cursor.execute(
                    "UPDATE teams SET league = ? WHERE league = ?",
                    (new_code, old_code)
                )
                if cursor.rowcount > 0:
                    print(f"    ✅ Updated teams: {old_code} → {new_code} ({cursor.rowcount} rows)")
            except Exception as e:
                print(f"    ⚠️ Could not update teams {old_code}: {e}")
        conn.commit()

        # 18e. Update event_epg_groups references
        for old_code, new_code in league_code_mappings:
            try:
                cursor.execute(
                    "UPDATE event_epg_groups SET assigned_league = ? WHERE assigned_league = ?",
                    (new_code, old_code)
                )
                if cursor.rowcount > 0:
                    print(f"    ✅ Updated event_epg_groups: {old_code} → {new_code} ({cursor.rowcount} rows)")
            except Exception as e:
                print(f"    ⚠️ Could not update event_epg_groups {old_code}: {e}")
        conn.commit()

        # 18f. Seed league_id_aliases with friendly names
        # This table stores ONE canonical alias per ESPN slug (for {league_id} template variable)
        # Additional alias variants are handled by _ALIAS_VARIANTS dict in the code
        aliases = [
            # Soccer
            ('eng.1', 'epl'),
            ('esp.1', 'laliga'),
            ('ger.1', 'bundesliga'),
            ('ita.1', 'seriea'),
            ('fra.1', 'ligue1'),
            ('usa.1', 'mls'),
            ('usa.nwsl', 'nwsl'),
            ('eng.2', 'efl'),
            ('eng.3', 'efl1'),
            ('uefa.champions', 'ucl'),
            # College sports
            ('mens-college-basketball', 'ncaam'),
            ('womens-college-basketball', 'ncaaw'),
            ('college-football', 'ncaaf'),
            ('mens-college-hockey', 'ncaah'),
            ('nba-development', 'nbag'),
            ('mens-college-volleyball', 'ncaavbm'),
            ('womens-college-volleyball', 'ncaavbw'),
            ('usa.ncaa.m.1', 'ncaas'),
            ('usa.ncaa.w.1', 'ncaaws'),
        ]
        for espn_slug, alias in aliases:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO league_id_aliases (espn_slug, alias) VALUES (?, ?)",
                    (espn_slug, alias)
                )
            except Exception as e:
                print(f"    ⚠️ Could not insert alias {espn_slug}: {e}")
        conn.commit()
        print(f"    ✅ Seeded {len(aliases)} league aliases")

        # 18g. Add new leagues to league_config
        new_leagues = [
            # UEFA Champions League
            ('uefa.champions', 'UEFA Champions League', 'soccer', 'soccer/uefa.champions', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2.png'),
            # NCAA Soccer (with ESPN slugs as league_code)
            ('usa.ncaa.m.1', 'NCAA Men\'s Soccer', 'soccer', 'soccer/usa.ncaa.m.1', 'Soccer', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png'),
            ('usa.ncaa.w.1', 'NCAA Women\'s Soccer', 'soccer', 'soccer/usa.ncaa.w.1', 'Soccer', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png'),
        ]
        for league_code, league_name, sport, api_path, category, record_format, logo_url in new_leagues:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO league_config
                    (league_code, league_name, sport, api_path, default_category, record_format, logo_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (league_code, league_name, sport, api_path, category, record_format, logo_url))
                if cursor.rowcount > 0:
                    print(f"    ✅ Added league: {league_name}")
            except Exception as e:
                print(f"    ⚠️ Could not add league {league_code}: {e}")
        conn.commit()

        # 18h. Fix consolidation_exception_keywords table (make global, remove group_id)
        # Migration 14 was supposed to do this but may not have run
        try:
            cursor.execute("PRAGMA table_info(consolidation_exception_keywords)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'group_id' in columns:
                # Recreate table without group_id
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS consolidation_exception_keywords_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        keywords TEXT NOT NULL,
                        behavior TEXT NOT NULL DEFAULT 'consolidate',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Copy existing data (deduplicate by keywords)
                cursor.execute("""
                    INSERT OR IGNORE INTO consolidation_exception_keywords_new (keywords, behavior, created_at)
                    SELECT DISTINCT keywords, behavior, created_at
                    FROM consolidation_exception_keywords
                """)
                cursor.execute("DROP TABLE consolidation_exception_keywords")
                cursor.execute("ALTER TABLE consolidation_exception_keywords_new RENAME TO consolidation_exception_keywords")
                conn.commit()
                print("    ✅ Fixed consolidation_exception_keywords table (removed group_id)")
        except Exception as e:
            print(f"    ⚠️ Could not fix exception keywords table: {e}")

        # 18i. Clean up system keywords from DB (now defined in code)
        # Language keywords are now managed in utils/keyword_matcher.py SYSTEM_KEYWORDS
        # Delete all language-related entries that were incorrectly seeded
        try:
            language_patterns = [
                '%Español%', '%Spanish%', '%(ESP)%',
                '%Français%', '%French%', '%(FRA)%',
                '%German%', '%Deutsch%', '%(GER)%',
                '%Portuguese%', '%Português%', '%(POR)%',
                '%Italian%', '%Italiano%', '%(ITA)%',
                '%Arabic%', '%العربية%', '%(ARA)%',
            ]
            deleted_count = 0
            for pattern in language_patterns:
                cursor.execute(
                    "DELETE FROM consolidation_exception_keywords WHERE keywords LIKE ?",
                    (pattern,)
                )
                deleted_count += cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                print(f"    ✅ Cleaned up {deleted_count} system keywords from DB (now in code)")
        except Exception as e:
            print(f"    ⚠️ Could not clean up system keywords: {e}")

        # 18j. Update team_league_cache to use ESPN slugs
        # This cache stores league_code which needs to match league_config
        tlc_mappings = [
            ('ncaam', 'mens-college-basketball'),
            ('ncaaw', 'womens-college-basketball'),
            ('ncaaf', 'college-football'),
            ('ncaah', 'mens-college-hockey'),
            ('ncaavb-m', 'mens-college-volleyball'),
            ('ncaavb-w', 'womens-college-volleyball'),
            ('ncaas', 'usa.ncaa.m.1'),
            ('ncaaws', 'usa.ncaa.w.1'),
        ]
        for old_code, new_code in tlc_mappings:
            try:
                cursor.execute(
                    "UPDATE team_league_cache SET league_code = ? WHERE league_code = ?",
                    (new_code, old_code)
                )
                if cursor.rowcount > 0:
                    print(f"    ✅ Updated team_league_cache: {old_code} → {new_code} ({cursor.rowcount} rows)")
            except Exception as e:
                print(f"    ⚠️ Could not update team_league_cache {old_code}: {e}")
        conn.commit()

        migrations_run += 1
        print("    ✅ Migration 18 complete: League codes normalized to ESPN slugs")

    # =========================================================================
    # 19. ADD WOMENS-COLLEGE-HOCKEY TO LEAGUE_CONFIG
    # =========================================================================
    if current_version < 19:
        print("  🔄 Running migration 19: Add womens-college-hockey league")

        # Add womens-college-hockey to league_config if not exists
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO league_config
                (league_code, league_name, sport, api_path, default_category, record_format, logo_url)
                VALUES
                ('womens-college-hockey', 'NCAA Women''s Hockey', 'hockey', 'hockey/womens-college-hockey',
                 'Hockey', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/hockey.png')
            """)
            if cursor.rowcount > 0:
                print("    ✅ Added womens-college-hockey to league_config")
            else:
                print("    ⏭️  womens-college-hockey already exists in league_config")
        except Exception as e:
            print(f"    ⚠️ Could not add womens-college-hockey to league_config: {e}")

        # Add alias for womens-college-hockey
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO league_id_aliases (espn_slug, alias)
                VALUES ('womens-college-hockey', 'ncaawh')
            """)
            if cursor.rowcount > 0:
                print("    ✅ Added ncaawh alias for womens-college-hockey")
        except Exception as e:
            print(f"    ⚠️ Could not add ncaawh alias: {e}")

        conn.commit()
        migrations_run += 1
        print("    ✅ Migration 19 complete: Added womens-college-hockey league")

    # =========================================================================
    # 20. ADD FILTERED_LEAGUE_NOT_ENABLED COLUMN
    # =========================================================================
    if current_version < 20:
        print("  🔄 Running migration 20: Add filtered_league_not_enabled column...")

        # Add column to event_epg_groups for per-group stats
        add_columns_if_missing('event_epg_groups', [
            ('filtered_league_not_enabled', 'INTEGER DEFAULT 0'),
        ])

        # Add column to epg_history for aggregate stats
        add_columns_if_missing('epg_history', [
            ('event_filtered_league_not_enabled', 'INTEGER DEFAULT 0'),
        ])

        conn.commit()
        migrations_run += 1
        print("    ✅ Migration 20 complete: Added filtered_league_not_enabled tracking")

    # =========================================================================
    # 21. ADD FILTERED_UNSUPPORTED_SPORT COLUMN
    # =========================================================================
    if current_version < 21:
        print("  🔄 Running migration 21: Add filtered_unsupported_sport tracking")

        # Add column to event_epg_groups for per-group stats
        add_columns_if_missing("event_epg_groups", [
            ("filtered_unsupported_sport", "INTEGER DEFAULT 0"),
        ])

        # Add column to epg_history for aggregate stats
        add_columns_if_missing("epg_history", [
            ("event_filtered_unsupported_sport", "INTEGER DEFAULT 0"),
        ])

        conn.commit()
        migrations_run += 1
        print("    ✅ Migration 21 complete: Added filtered_unsupported_sport tracking")

    # =========================================================================
    # 22. Clean up system keywords from consolidation_exception_keywords table
    # =========================================================================
    # Language keywords are now defined in SYSTEM_KEYWORDS (utils/keyword_matcher.py)
    # They were accidentally seeded into the DB multiple times - remove them
    if current_version < 22:
        print("  🔄 Running migration 22: Clean up system keywords from DB")
        try:
            language_patterns = [
                '%Español%', '%Spanish%', '%(ESP)%',
                '%Français%', '%French%', '%(FRA)%',
                '%German%', '%Deutsch%', '%(GER)%',
                '%Portuguese%', '%Português%', '%(POR)%',
                '%Italian%', '%Italiano%', '%(ITA)%',
                '%Arabic%', '%العربية%', '%(ARA)%',
            ]
            deleted_count = 0
            for pattern in language_patterns:
                cursor.execute(
                    "DELETE FROM consolidation_exception_keywords WHERE keywords LIKE ?",
                    (pattern,)
                )
                deleted_count += cursor.rowcount
            conn.commit()
            migrations_run += 1
            if deleted_count > 0:
                print(f"    ✅ Migration 22 complete: Removed {deleted_count} system keywords (now in code)")
            else:
                print("    ✅ Migration 22 complete: No system keywords to remove")
        except Exception as e:
            print(f"    ⚠️ Migration 22 warning: {e}")
            migrations_run += 1

    # =========================================================================
    # 23. Stream Fingerprint Cache
    # =========================================================================
    # Caches stream-to-event matches to avoid expensive tier matching on every
    # EPG generation. Only caches successful matches (with event_id).
    # Fingerprint = group_id + stream_id + stream_name
    if current_version < 23:
        print("  🔄 Migration 23: Creating stream fingerprint cache...")
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stream_match_cache (
                    -- Hash fingerprint for fast lookup (SHA256 truncated to 16 chars)
                    fingerprint TEXT PRIMARY KEY,

                    -- Original fields kept for debugging
                    group_id INTEGER NOT NULL,
                    stream_id INTEGER NOT NULL,
                    stream_name TEXT NOT NULL,

                    -- Match result
                    event_id TEXT NOT NULL,
                    league TEXT NOT NULL,

                    -- Cached static event data (JSON blob)
                    -- Contains full normalized event + team_result for template vars
                    cached_event_data TEXT NOT NULL,

                    -- Housekeeping
                    last_seen_generation INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index for purge queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_smc_generation
                ON stream_match_cache(last_seen_generation)
            """)

            # Index for event_id lookups (useful for debugging)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_smc_event_id
                ON stream_match_cache(event_id)
            """)

            # Add epg_generation_counter to settings if not exists
            add_columns_if_missing('settings', [
                ('epg_generation_counter', 'INTEGER DEFAULT 0'),
            ])

            conn.commit()
            migrations_run += 1
            print("    ✅ Migration 23 complete: Stream fingerprint cache created")
        except Exception as e:
            print(f"    ⚠️ Migration 23 error: {e}")
            conn.rollback()

    # =========================================================================
    # 24. EPG Failed Matches Table
    # =========================================================================
    # Stores failed stream matches from each EPG generation for debugging.
    # Cleared at start of each generation, populated during processing.
    if current_version < 24:
        print("  🔄 Migration 24: Creating EPG match tracking tables...")
        try:
            # Failed matches table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS epg_failed_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    group_name TEXT NOT NULL,
                    stream_id INTEGER,
                    stream_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    parsed_team1 TEXT,
                    parsed_team2 TEXT,
                    detection_tier TEXT,
                    leagues_checked TEXT,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index for generation lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_efm_generation
                ON epg_failed_matches(generation_id)
            """)

            # Index for group-based queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_efm_group
                ON epg_failed_matches(group_id)
            """)

            # Matched streams table (successful matches)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS epg_matched_streams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    group_name TEXT NOT NULL,
                    stream_id INTEGER,
                    stream_name TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_name TEXT,
                    detected_league TEXT,
                    detection_tier TEXT,
                    parsed_team1 TEXT,
                    parsed_team2 TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    event_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index for generation lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ems_generation
                ON epg_matched_streams(generation_id)
            """)

            # Index for group-based queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ems_group
                ON epg_matched_streams(group_id)
            """)

            # Add triggered_by column to epg_history
            add_columns_if_missing('epg_history', [
                ('triggered_by', "TEXT DEFAULT 'manual'"),  # 'manual', 'scheduler', 'api'
            ])

            conn.commit()
            migrations_run += 1
            print("    ✅ Migration 24 complete: EPG match tracking tables + triggered_by column")
        except Exception as e:
            print(f"    ⚠️ Migration 24 error: {e}")
            conn.rollback()

    # =========================================================================
    # 25. Fix duplicate language keywords in consolidation_exception_keywords
    # =========================================================================
    # Language keywords were being inserted on every startup due to missing
    # UNIQUE constraint. This migration:
    # 1. Deduplicates existing rows (keeps lowest ID for each keyword)
    # 2. Recreates table with UNIQUE constraint
    # 3. Re-inserts default language keywords (INSERT OR IGNORE now works)
    if current_version < 25:
        print("  🔄 Migration 25: Fixing duplicate exception keywords...")
        try:
            # Step 1: Get unique keywords (keep first occurrence of each)
            cursor.execute("""
                SELECT MIN(id) as id, keywords, behavior, MIN(created_at) as created_at
                FROM consolidation_exception_keywords
                GROUP BY keywords
            """)
            unique_keywords = cursor.fetchall()

            # Step 2: Recreate table with UNIQUE constraint
            cursor.execute("DROP TABLE IF EXISTS consolidation_exception_keywords")
            cursor.execute("""
                CREATE TABLE consolidation_exception_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keywords TEXT NOT NULL UNIQUE,
                    behavior TEXT NOT NULL DEFAULT 'consolidate',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Step 3: Re-insert unique user keywords (excluding language defaults)
            language_patterns = [
                'En Español', 'En Français', '(GER)', '(POR)', '(ITA)', '(ARA)',
                'Spanish', 'French', 'German', 'Portuguese', 'Italian', 'Arabic'
            ]
            user_keywords = []
            for row in unique_keywords:
                keywords_str = row[1]
                is_language = any(pattern in keywords_str for pattern in language_patterns)
                if not is_language:
                    user_keywords.append((row[1], row[2], row[3]))

            if user_keywords:
                cursor.executemany(
                    "INSERT INTO consolidation_exception_keywords (keywords, behavior, created_at) VALUES (?, ?, ?)",
                    user_keywords
                )
                print(f"    📝 Preserved {len(user_keywords)} user-defined keyword(s)")

            # Step 4: Insert default language keywords (fresh, no duplicates)
            # First keyword is canonical (shown in EPG variables) - use English names
            default_keywords = [
                ('Spanish, En Español, (ESP), Español', 'consolidate'),
                ('French, En Français, (FRA), Français', 'consolidate'),
                ('German, (GER), Deutsch', 'consolidate'),
                ('Portuguese, (POR), Português', 'consolidate'),
                ('Italian, (ITA), Italiano', 'consolidate'),
                ('Arabic, (ARA), العربية', 'consolidate'),
            ]
            cursor.executemany(
                "INSERT OR IGNORE INTO consolidation_exception_keywords (keywords, behavior) VALUES (?, ?)",
                default_keywords
            )

            conn.commit()
            migrations_run += 1

            # Count what we ended up with
            cursor.execute("SELECT COUNT(*) FROM consolidation_exception_keywords")
            final_count = cursor.fetchone()[0]
            print(f"    ✅ Migration 25 complete: {final_count} exception keywords (duplicates removed, UNIQUE constraint added)")

        except Exception as e:
            print(f"    ⚠️ Migration 25 error: {e}")
            conn.rollback()

    # =========================================================================
    # 26. Cron-based scheduler (replaces auto_generate_frequency + schedule_time)
    # =========================================================================
    if current_version < 26:
        print("  🔄 Migration 26: Converting to cron-based scheduling...")
        try:
            # Add cron_expression column
            add_columns_if_missing("settings", [
                ("cron_expression", "TEXT DEFAULT '0 * * * *'"),
            ])

            # Convert existing settings to cron expression
            cursor.execute("""
                SELECT auto_generate_frequency, schedule_time FROM settings WHERE id = 1
            """)
            row = cursor.fetchone()

            if row:
                frequency = row[0] or 'hourly'
                schedule_time = row[1] or '00'

                if frequency == 'hourly':
                    # schedule_time is minute (0-59)
                    try:
                        minute = int(schedule_time) if schedule_time else 0
                        minute = max(0, min(59, minute))
                    except ValueError:
                        minute = 0
                    cron_expr = f"{minute} * * * *"
                else:  # daily
                    # schedule_time is HH:MM or just HH
                    try:
                        if ':' in (schedule_time or ''):
                            parts = schedule_time.split(':')
                            hour = int(parts[0])
                            minute = int(parts[1]) if len(parts) > 1 else 0
                        else:
                            hour = int(schedule_time) if schedule_time else 0
                            minute = 0
                        hour = max(0, min(23, hour))
                        minute = max(0, min(59, minute))
                    except ValueError:
                        hour = 0
                        minute = 0
                    cron_expr = f"{minute} {hour} * * *"

                cursor.execute("""
                    UPDATE settings SET cron_expression = ? WHERE id = 1
                """, (cron_expr,))
                print(f"    📝 Converted {frequency} @ {schedule_time} → cron '{cron_expr}'")

            conn.commit()
            migrations_run += 1
            print("    ✅ Migration 26 complete: Cron-based scheduling enabled")

        except Exception as e:
            print(f"    ⚠️ Migration 26 error: {e}")
            conn.rollback()

    # =========================================================================
    # 27. Add gracenote_category column to league_config
    # =========================================================================
    if current_version < 27:
        print("    🔄 Running migration 27: Add gracenote_category to league_config")
        try:
            # Add gracenote_category column
            add_columns_if_missing("league_config", [
                ("gracenote_category", "TEXT"),
            ])

            # Populate gracenote_category for all existing leagues
            gracenote_mappings = {
                # Baseball
                'mlb': 'MLB Baseball',
                # Basketball
                'nba': 'NBA Basketball',
                'nba-development': 'NBA G League Basketball',
                'mens-college-basketball': 'College Basketball',
                'womens-college-basketball': "Women's College Basketball",
                'wnba': 'WNBA Basketball',
                # Football
                'nfl': 'NFL Football',
                'college-football': 'College Football',
                # Hockey
                'nhl': 'NHL Hockey',
                'mens-college-hockey': 'College Hockey',
                'womens-college-hockey': "Women's College Hockey",
                # Soccer - Top leagues
                'eng.1': 'Premier League Soccer',
                'eng.2': 'English Championship Soccer',
                'eng.3': 'English League One Soccer',
                'ger.1': 'Bundesliga Soccer',
                'esp.1': 'La Liga Soccer',
                'fra.1': 'Ligue 1 Soccer',
                'ita.1': 'Serie A Soccer',
                'ned.1': 'Eredivisie Soccer',
                'ksa.1': 'Saudi Pro League Soccer',
                'usa.1': 'MLS Soccer',
                'usa.nwsl': 'NWSL Soccer',
                'usa.ncaa.m.1': "Men's College Soccer",
                'usa.ncaa.w.1': "Women's College Soccer",
                'uefa.champions': 'UEFA Champions League Soccer',
                # Volleyball
                'mens-college-volleyball': "Men's College Volleyball",
                'womens-college-volleyball': "Women's College Volleyball",
            }

            for league_code, category in gracenote_mappings.items():
                cursor.execute(
                    "UPDATE league_config SET gracenote_category = ? WHERE league_code = ?",
                    (category, league_code)
                )

            conn.commit()
            migrations_run += 1
            print("    ✅ Migration 27 complete: gracenote_category added to league_config")

        except Exception as e:
            print(f"    ⚠️ Migration 27 error: {e}")
            conn.rollback()

    # =========================================================================
    # REPAIR: Ensure critical columns exist (catches failed migrations)
    # =========================================================================
    # Some columns may have failed to be added during their migration due to
    # timing issues. This section ensures they exist regardless of schema version.
    repair_columns = [
        ('epg_history', 'triggered_by', "TEXT DEFAULT 'manual'"),
    ]
    for table, col_name, col_def in repair_columns:
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        if col_name not in existing:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                conn.commit()
                print(f"    🔧 Repaired missing column: {table}.{col_name}")
            except Exception as e:
                print(f"    ⚠️ Could not repair column {table}.{col_name}: {e}")

    # =========================================================================
    # 28. Add team_abbrev column to soccer_team_leagues for abbreviation matching
    # =========================================================================
    if current_version < 28:
        print("    🔄 Running migration 28: Add team_abbrev to soccer_team_leagues")
        try:
            add_columns_if_missing("soccer_team_leagues", [
                ("team_abbrev", "TEXT"),
            ])

            # Note: Existing rows will have NULL abbreviation until next cache refresh.
            # The soccer cache refresh will populate this column.
            print("    ℹ️  Run a soccer cache refresh to populate team abbreviations")

            migrations_run += 1
        except Exception as e:
            print(f"    ⚠️ Migration 28 failed: {e}")

    # =========================================================================
    # 29. Global channel range settings
    # =========================================================================
    if current_version < 29:
        print("    🔄 Running migration 29: Add global channel range settings")
        try:
            add_columns_if_missing("settings", [
                ("channel_range_start", "INTEGER DEFAULT 101"),
                ("channel_range_end", "INTEGER DEFAULT 9999"),
            ])

            migrations_run += 1
        except Exception as e:
            print(f"    ⚠️ Migration 29 failed: {e}")

    # =========================================================================
    # 30. Channel assignment mode and sort order for event groups
    # =========================================================================
    if current_version < 30:
        print("    🔄 Running migration 30: Add channel assignment mode and sort order")
        try:
            # channel_assignment_mode: 'auto' or 'manual'
            # sort_order: integer for drag-and-drop ordering (lower = higher priority for auto assignment)
            add_columns_if_missing("event_epg_groups", [
                ("channel_assignment_mode", "TEXT DEFAULT 'manual'"),
                ("sort_order", "INTEGER DEFAULT 0"),
            ])

            # Migrate existing PARENT groups: all groups with channel_start become 'manual'
            # This preserves backwards compatibility - existing setups keep working
            # Child groups inherit from parent, so they should have NULL
            cursor.execute("""
                UPDATE event_epg_groups
                SET channel_assignment_mode = 'manual'
                WHERE channel_start IS NOT NULL
                AND parent_group_id IS NULL
            """)

            # Parent groups without channel_start get 'auto' mode
            cursor.execute("""
                UPDATE event_epg_groups
                SET channel_assignment_mode = 'auto'
                WHERE channel_start IS NULL
                AND parent_group_id IS NULL
            """)

            # Child groups: clear channel_assignment_mode and channel_start (they inherit)
            cursor.execute("""
                UPDATE event_epg_groups
                SET channel_assignment_mode = NULL, channel_start = NULL
                WHERE parent_group_id IS NOT NULL
            """)

            # Set initial sort_order based on current ID order
            cursor.execute("""
                UPDATE event_epg_groups
                SET sort_order = id
                WHERE parent_group_id IS NULL
            """)

            conn.commit()
            print("    ℹ️  Existing groups with channel_start migrated to 'manual' mode")

            migrations_run += 1
        except Exception as e:
            print(f"    ⚠️ Migration 30 failed: {e}")

    # =========================================================================
    # 31. IDLE OFFSEASON SUPPORT
    # =========================================================================
    if current_version < 31:
        print("    🔄 Running migration 31: Add idle offseason support for templates")
        try:
            # Add offseason-specific idle fields to templates
            # When no games exist in the 30-day lookahead, use these instead of regular idle
            add_columns_if_missing("templates", [
                ("idle_offseason_enabled", "BOOLEAN DEFAULT 0"),
                ("idle_description_offseason", "TEXT DEFAULT 'No upcoming {team_name} games scheduled.'"),
            ])

            conn.commit()
            migrations_run += 1
        except Exception as e:
            print(f"    ⚠️ Migration 31 failed: {e}")

    # =========================================================================
    # 32. IDLE OFFSEASON SUBTITLE
    # =========================================================================
    if current_version < 32:
        print("    🔄 Running migration 32: Add idle offseason subtitle for templates")
        try:
            add_columns_if_missing("templates", [
                ("idle_subtitle_offseason_enabled", "BOOLEAN DEFAULT 0"),
                ("idle_subtitle_offseason", "TEXT"),
            ])

            conn.commit()
            migrations_run += 1
        except Exception as e:
            print(f"    ⚠️ Migration 32 failed: {e}")

    # =========================================================================
    # UPDATE SCHEMA VERSION
    # =========================================================================
    # All migrations complete - update version to current
    if current_version < CURRENT_SCHEMA_VERSION:
        set_schema_version(conn, CURRENT_SCHEMA_VERSION)
        print(f"  📊 Schema version updated: {current_version} → {CURRENT_SCHEMA_VERSION}")

    if migrations_run > 0:
        print(f"  ✅ Completed {migrations_run} migration(s)")
    else:
        print(f"  ✅ All migrations already applied")

    return migrations_run


def init_database():
    """
    Initialize database with schema and run migrations.

    Flow for existing databases:
      1. Run migrations first (adds new columns to existing tables)
      2. Run schema.sql (CREATE TABLE IF NOT EXISTS - no-ops, but creates indexes)

    Flow for fresh installations:
      1. Run schema.sql (creates all tables with current schema, version=8)
      2. Run migrations (sees version=8, skips all migrations)
    """
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')

    with open(schema_path, 'r') as f:
        schema_sql = f.read()

    conn = get_connection()
    try:
        # Check if this is an existing database (has tables already)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        is_existing_db = cursor.fetchone() is not None

        if is_existing_db:
            print("  📦 Existing database detected - running migrations first")
            # Existing database: run migrations FIRST to add new columns
            # This ensures columns exist before any index creation
            run_migrations(conn)
        else:
            print("  🆕 Fresh installation - creating database schema")

        # Run schema.sql (CREATE TABLE IF NOT EXISTS, indexes, etc.)
        conn.executescript(schema_sql)
        conn.commit()

        if not is_existing_db:
            # Fresh install: run migrations after to set version (mostly no-ops)
            run_migrations(conn)

        # Sync timezone from environment variable if set
        env_tz = os.environ.get('TZ')
        if env_tz:
            conn.execute(
                "UPDATE settings SET default_timezone = ? WHERE id = 1",
                (env_tz,)
            )
            conn.commit()
            print(f"✅ Database initialized successfully at {DB_PATH} (timezone: {env_tz})")
        else:
            print(f"✅ Database initialized successfully at {DB_PATH}")

    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        raise
    finally:
        conn.close()

def reset_database():
    """Drop all tables and reinitialize (for development)"""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"🗑️  Removed existing database at {DB_PATH}")
    init_database()

# =============================================================================
# JSON FIELD HANDLER - Centralized JSON serialization/deserialization
# =============================================================================

# Configuration for JSON fields with their default values
JSON_FIELD_DEFAULTS = {
    'categories': [],
    'flags': {},
    'description_options': [],
    'warnings_json': [],  # Used in epg_history
}


def parse_json_fields(record: Dict[str, Any], fields: List[str] = None) -> Dict[str, Any]:
    """
    Parse JSON string fields in a database record.

    Args:
        record: Dictionary from database row
        fields: List of field names to parse (defaults to JSON_FIELD_DEFAULTS keys)

    Returns:
        Record with JSON fields parsed to Python objects
    """
    if not record:
        return record

    fields_to_parse = fields or list(JSON_FIELD_DEFAULTS.keys())

    for field in fields_to_parse:
        if field in record and record[field]:
            try:
                if isinstance(record[field], str):
                    record[field] = json.loads(record[field])
            except (json.JSONDecodeError, TypeError):
                # Use default if parsing fails
                record[field] = JSON_FIELD_DEFAULTS.get(field, None)

    return record


def serialize_json_fields(record: Dict[str, Any], fields: List[str] = None) -> Dict[str, Any]:
    """
    Serialize Python objects to JSON strings for database storage.

    Args:
        record: Dictionary to be stored in database
        fields: List of field names to serialize (defaults to JSON_FIELD_DEFAULTS keys)

    Returns:
        Record with JSON fields serialized to strings
    """
    if not record:
        return record

    fields_to_serialize = fields or list(JSON_FIELD_DEFAULTS.keys())

    for field in fields_to_serialize:
        if field in record and record[field] is not None:
            if not isinstance(record[field], str):
                record[field] = json.dumps(record[field])

    return record


# Backwards compatibility aliases
def _parse_template_json_fields(template: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON fields in a template dict (backwards compatible wrapper)"""
    return parse_json_fields(template, ['categories', 'flags', 'description_options'])


def _serialize_template_json_fields(template: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize JSON fields in a template dict (backwards compatible wrapper)"""
    return serialize_json_fields(template, ['categories', 'flags', 'description_options'])

def get_template(template_id: int) -> Optional[Dict[str, Any]]:
    """Get template by ID"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        result = cursor.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        if result:
            template = dict(result)
            return _parse_template_json_fields(template)
        return None
    finally:
        conn.close()

def get_all_templates() -> List[Dict[str, Any]]:
    """Get all templates with team count and group count"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        results = cursor.execute("""
            SELECT
                t.*,
                COUNT(DISTINCT tm.id) as team_count,
                COUNT(DISTINCT eg.id) as group_count
            FROM templates t
            LEFT JOIN teams tm ON t.id = tm.template_id
            LEFT JOIN event_epg_groups eg ON t.id = eg.event_template_id
            GROUP BY t.id
            ORDER BY t.name
        """).fetchall()
        return [_parse_template_json_fields(dict(row)) for row in results]
    finally:
        conn.close()

def create_template(data: Dict[str, Any]) -> int:
    """Create a new template and return its ID"""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Serialize JSON fields to strings for database storage
        data = _serialize_template_json_fields(data.copy())

        # Event templates don't have idle - clear those fields
        if data.get('template_type') == 'event':
            data['idle_enabled'] = False
            data['idle_title'] = ''
            data['idle_subtitle'] = ''
            data['idle_description'] = ''
            data['idle_art_url'] = ''
            data['idle_conditional_enabled'] = False
            data['idle_description_final'] = ''
            data['idle_description_not_final'] = ''

        # Extract fields (all are optional except name)
        fields = [
            'name', 'template_type', 'sport', 'league',
            'title_format', 'subtitle_template', 'program_art_url',
            'game_duration_mode', 'game_duration_override',
            'flags', 'categories', 'categories_apply_to',
            'no_game_enabled', 'no_game_title', 'no_game_description', 'no_game_duration',
            'pregame_enabled', 'pregame_periods', 'pregame_title', 'pregame_subtitle', 'pregame_description', 'pregame_art_url',
            'postgame_enabled', 'postgame_periods', 'postgame_title', 'postgame_subtitle', 'postgame_description', 'postgame_art_url',
            'postgame_conditional_enabled', 'postgame_description_final', 'postgame_description_not_final',
            'idle_enabled', 'idle_title', 'idle_subtitle', 'idle_description', 'idle_art_url',
            'idle_conditional_enabled', 'idle_description_final', 'idle_description_not_final',
            'description_options',
            'channel_name', 'channel_logo_url'
        ]

        # Build INSERT statement dynamically
        present_fields = [f for f in fields if f in data]
        placeholders = ', '.join(['?' for _ in present_fields])
        field_names = ', '.join(present_fields)

        cursor.execute(f"""
            INSERT INTO templates ({field_names})
            VALUES ({placeholders})
        """, [data[f] for f in present_fields])

        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def update_template(template_id: int, data: Dict[str, Any]) -> bool:
    """Update an existing template"""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Serialize JSON fields to strings for database storage
        data = _serialize_template_json_fields(data.copy())

        # Event templates don't have idle - clear those fields
        if data.get('template_type') == 'event':
            data['idle_enabled'] = False
            data['idle_title'] = ''
            data['idle_subtitle'] = ''
            data['idle_description'] = ''
            data['idle_art_url'] = ''
            data['idle_conditional_enabled'] = False
            data['idle_description_final'] = ''
            data['idle_description_not_final'] = ''

        # Build UPDATE statement from provided fields
        fields = [k for k in data.keys() if k != 'id']
        set_clause = ', '.join([f"{f} = ?" for f in fields])
        values = [data[f] for f in fields] + [template_id]

        cursor.execute(f"""
            UPDATE templates
            SET {set_clause}
            WHERE id = ?
        """, values)

        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_template(template_id: int) -> bool:
    """
    Delete a template. Teams assigned to this template will have template_id set to NULL
    due to ON DELETE SET NULL foreign key constraint.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def get_template_team_count(template_id: int) -> int:
    """Get count of teams using this template"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        result = cursor.execute("""
            SELECT COUNT(*) FROM teams WHERE template_id = ?
        """, (template_id,)).fetchone()
        return result[0] if result else 0
    finally:
        conn.close()

# Helper functions for team operations

def get_team(team_id: int) -> Optional[Dict[str, Any]]:
    """Get team by ID with template and league information"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        result = cursor.execute("""
            SELECT
                t.*,
                tp.name as template_name,
                lc.league_name
            FROM teams t
            LEFT JOIN templates tp ON t.template_id = tp.id
            LEFT JOIN league_config lc ON t.league = lc.league_code
            WHERE t.id = ?
        """, (team_id,)).fetchone()
        return dict(result) if result else None
    finally:
        conn.close()

def get_all_teams() -> List[Dict[str, Any]]:
    """Get all teams with template information"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        results = cursor.execute("""
            SELECT
                t.*,
                tp.name as template_name,
                lc.league_name,
                lc.sport
            FROM teams t
            LEFT JOIN templates tp ON t.template_id = tp.id
            LEFT JOIN league_config lc ON t.league = lc.league_code
            ORDER BY t.team_name
        """).fetchall()
        return [dict(row) for row in results]
    finally:
        conn.close()

def get_active_teams_with_templates() -> List[Dict[str, Any]]:
    """
    Get all active teams that have a template assigned.
    Used for EPG generation (filters out unassigned teams).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        results = cursor.execute("""
            SELECT
                t.*,
                tp.*
            FROM teams t
            INNER JOIN templates tp ON t.template_id = tp.id
            WHERE t.active = 1 AND t.template_id IS NOT NULL
            ORDER BY t.team_name
        """).fetchall()

        # Convert to list of dicts, merging team and template data
        teams_with_templates = []
        for row in results:
            row_dict = dict(row)
            # Separate team and template data
            team_data = {k: v for k, v in row_dict.items() if not k.startswith('template_')}
            template_data = {k: v for k, v in row_dict.items() if k.startswith('template_')}

            teams_with_templates.append({
                'team': team_data,
                'template': template_data
            })

        return teams_with_templates
    finally:
        conn.close()

def create_team(data: Dict[str, Any]) -> int:
    """Create a new team and return its ID"""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        fields = [
            'espn_team_id', 'league', 'sport',
            'team_name', 'team_abbrev', 'team_slug', 'team_logo_url', 'team_color',
            'channel_id', 'channel_logo_url', 'template_id', 'active'
        ]

        present_fields = [f for f in fields if f in data]
        placeholders = ', '.join(['?' for _ in present_fields])
        field_names = ', '.join(present_fields)

        cursor.execute(f"""
            INSERT INTO teams ({field_names})
            VALUES ({placeholders})
        """, [data[f] for f in present_fields])

        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def update_team(team_id: int, data: Dict[str, Any]) -> bool:
    """Update an existing team"""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        fields = [k for k in data.keys() if k != 'id']
        set_clause = ', '.join([f"{f} = ?" for f in fields])
        values = [data[f] for f in fields] + [team_id]

        cursor.execute(f"""
            UPDATE teams
            SET {set_clause}
            WHERE id = ?
        """, values)

        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def delete_team(team_id: int) -> bool:
    """Delete a team"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def bulk_assign_template(team_ids: List[int], template_id: Optional[int]) -> int:
    """Assign a template to multiple teams. Returns count of teams updated."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        placeholders = ', '.join(['?' for _ in team_ids])
        cursor.execute(f"""
            UPDATE teams
            SET template_id = ?
            WHERE id IN ({placeholders})
        """, [template_id] + team_ids)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def bulk_delete_teams(team_ids: List[int]) -> int:
    """Delete multiple teams. Returns count of teams deleted."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        placeholders = ', '.join(['?' for _ in team_ids])
        cursor.execute(f"DELETE FROM teams WHERE id IN ({placeholders})", team_ids)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

def bulk_set_active(team_ids: List[int], active: bool) -> int:
    """Set active status for multiple teams. Returns count of teams updated."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        placeholders = ', '.join(['?' for _ in team_ids])
        cursor.execute(f"""
            UPDATE teams
            SET active = ?
            WHERE id IN ({placeholders})
        """, [1 if active else 0] + team_ids)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# =============================================================================
# Team Alias Functions (for Event Channel EPG)
# =============================================================================

def get_alias(alias_id: int) -> Optional[Dict[str, Any]]:
    """Get a team alias by ID."""
    return db_fetch_one("SELECT * FROM team_aliases WHERE id = ?", (alias_id,))


def get_aliases_for_league(league: str) -> List[Dict[str, Any]]:
    """Get all team aliases for a specific league."""
    return db_fetch_all(
        "SELECT * FROM team_aliases WHERE league = ? ORDER BY alias",
        (league.lower(),)
    )


def get_all_aliases() -> List[Dict[str, Any]]:
    """Get all team aliases."""
    return db_fetch_all("SELECT * FROM team_aliases ORDER BY league, alias")


def create_alias(alias: str, league: str, espn_team_id: str, espn_team_name: str) -> int:
    """
    Create a new team alias.

    Args:
        alias: The alias string (will be normalized to lowercase)
        league: League code (e.g., 'nfl', 'epl')
        espn_team_id: ESPN team ID
        espn_team_name: ESPN team display name

    Returns:
        ID of created alias

    Raises:
        sqlite3.IntegrityError if alias already exists for this league
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO team_aliases (alias, league, espn_team_id, espn_team_name)
            VALUES (?, ?, ?, ?)
            """,
            (alias.lower().strip(), league.lower(), espn_team_id, espn_team_name)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_alias(alias_id: int, data: Dict[str, Any]) -> bool:
    """
    Update an existing alias.

    Args:
        alias_id: Alias ID to update
        data: Dict with fields to update (alias, league, espn_team_id, espn_team_name)

    Returns:
        True if updated, False if not found
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Normalize alias if provided
        if 'alias' in data:
            data['alias'] = data['alias'].lower().strip()
        if 'league' in data:
            data['league'] = data['league'].lower()

        fields = [k for k in data.keys() if k != 'id']
        if not fields:
            return False

        set_clause = ', '.join([f"{f} = ?" for f in fields])
        values = [data[f] for f in fields] + [alias_id]

        cursor.execute(f"""
            UPDATE team_aliases
            SET {set_clause}
            WHERE id = ?
        """, values)

        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_alias(alias_id: int) -> bool:
    """Delete a team alias."""
    return db_execute("DELETE FROM team_aliases WHERE id = ?", (alias_id,)) > 0


def find_alias(alias: str, league: str) -> Optional[Dict[str, Any]]:
    """
    Find an alias by alias string and league.

    Args:
        alias: Alias string to look up
        league: League code

    Returns:
        Alias dict or None if not found
    """
    return db_fetch_one(
        "SELECT * FROM team_aliases WHERE alias = ? AND league = ?",
        (alias.lower().strip(), league.lower())
    )


def bulk_create_aliases(aliases: List[Dict[str, str]]) -> int:
    """
    Create multiple aliases at once.

    Args:
        aliases: List of dicts with alias, league, espn_team_id, espn_team_name

    Returns:
        Count of aliases created (skips duplicates)
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        created = 0

        for a in aliases:
            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO team_aliases
                    (alias, league, espn_team_id, espn_team_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        a['alias'].lower().strip(),
                        a['league'].lower(),
                        a['espn_team_id'],
                        a['espn_team_name']
                    )
                )
                if cursor.rowcount > 0:
                    created += 1
            except Exception as e:
                print(f"Error creating alias {a.get('alias')}: {e}")
                continue

        conn.commit()
        return created
    finally:
        conn.close()


# =============================================================================
# Event EPG Group Functions (for Event Channel EPG)
# =============================================================================

def _parse_event_group_json_fields(group: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON fields in an event EPG group dict and normalize league codes."""
    if not group:
        return group

    # Parse channel_profile_ids JSON
    if 'channel_profile_ids' in group and group['channel_profile_ids']:
        try:
            group['channel_profile_ids'] = json.loads(group['channel_profile_ids'])
        except (json.JSONDecodeError, TypeError):
            group['channel_profile_ids'] = []
    else:
        group['channel_profile_ids'] = []

    # Parse and normalize enabled_leagues JSON (alias -> ESPN slug)
    if 'enabled_leagues' in group and group['enabled_leagues']:
        try:
            leagues = json.loads(group['enabled_leagues'])
            # Normalize aliases to ESPN slugs (single source of truth)
            group['enabled_leagues'] = json.dumps(normalize_league_codes(leagues))
        except (json.JSONDecodeError, TypeError):
            pass

    return group


def get_event_epg_group(group_id: int) -> Optional[Dict[str, Any]]:
    """Get an event EPG group by ID."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        result = cursor.execute(
            "SELECT * FROM event_epg_groups WHERE id = ?",
            (group_id,)
        ).fetchone()
        return _parse_event_group_json_fields(dict(result)) if result else None
    finally:
        conn.close()


def get_event_epg_group_by_dispatcharr_id(dispatcharr_group_id: int) -> Optional[Dict[str, Any]]:
    """Get an event EPG group by Dispatcharr group ID."""
    result = db_fetch_one(
        "SELECT * FROM event_epg_groups WHERE dispatcharr_group_id = ?",
        (dispatcharr_group_id,)
    )
    return _parse_event_group_json_fields(result) if result else None


def get_all_event_epg_groups(enabled_only: bool = False) -> List[Dict[str, Any]]:
    """Get all event EPG groups with template names and parent info."""
    query = """
        SELECT g.*, t.name as event_template_name,
               pg.group_name as parent_group_name
        FROM event_epg_groups g
        LEFT JOIN templates t ON g.event_template_id = t.id
        LEFT JOIN event_epg_groups pg ON g.parent_group_id = pg.id
    """
    if enabled_only:
        query += " WHERE g.enabled = 1"
    query += " ORDER BY g.group_name"
    groups = db_fetch_all(query)
    return [_parse_event_group_json_fields(g) for g in groups]


def create_event_epg_group(
    dispatcharr_group_id: int,
    dispatcharr_account_id: int,
    group_name: str,
    assigned_league: str,
    assigned_sport: str,
    enabled: bool = True,
    event_template_id: int = None,
    account_name: str = None,
    channel_start: int = None,
    channel_group_id: int = None,
    channel_group_name: str = None,
    stream_profile_id: int = None,
    channel_profile_ids: list = None,
    custom_regex_teams: str = None,
    custom_regex_teams_enabled: bool = False,
    custom_regex_date: str = None,
    custom_regex_date_enabled: bool = False,
    custom_regex_time: str = None,
    custom_regex_time_enabled: bool = False,
    stream_include_regex: str = None,
    stream_include_regex_enabled: bool = False,
    stream_exclude_regex: str = None,
    stream_exclude_regex_enabled: bool = False,
    skip_builtin_filter: bool = False,
    parent_group_id: int = None,
    is_multi_sport: bool = False,
    enabled_leagues: str = None,
    channel_sort_order: str = 'time',
    overlap_handling: str = 'add_stream',
    channel_assignment_mode: str = 'auto'
) -> int:
    """
    Create a new event EPG group.

    Args:
        event_template_id: Optional template ID (must be an 'event' type template)
        account_name: Optional M3U account name for display purposes
        channel_start: Starting channel number for auto-created channels
        channel_group_id: Dispatcharr channel group ID to assign created channels to
        channel_group_name: Dispatcharr channel group name (for UI display)
        stream_profile_id: Dispatcharr stream profile ID to assign to created channels
        channel_profile_ids: List of Dispatcharr channel profile IDs to add created channels to
        custom_regex_teams: Combined regex with (?P<team1>...) and (?P<team2>...) groups
        custom_regex_teams_enabled: Enable custom teams regex
        custom_regex_date: Optional regex pattern to extract game date
        custom_regex_date_enabled: Enable custom date regex
        custom_regex_time: Optional regex pattern to extract game time
        custom_regex_time_enabled: Enable custom time regex
        stream_include_regex: Optional regex to include only matching streams
        stream_include_regex_enabled: Enable inclusion regex
        stream_exclude_regex: Optional regex to exclude streams from matching
        stream_exclude_regex_enabled: Enable exclusion regex
        skip_builtin_filter: Skip built-in game indicator filter

    Returns:
        ID of created group

    Raises:
        sqlite3.IntegrityError if dispatcharr_group_id already exists
    """
    # For MANUAL mode: auto-assign channel_start if not explicitly provided
    # For AUTO mode: channel_start should remain NULL (assigned dynamically during EPG generation)
    if channel_assignment_mode == 'manual' and not channel_start:
        channel_start, error_msg = get_next_available_channel_range()
        if channel_start:
            logger.info(f"Auto-assigned channel_start {channel_start} for new MANUAL group '{group_name}'")
        elif error_msg:
            logger.warning(f"Could not auto-assign channel_start for '{group_name}': {error_msg}")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Convert channel_profile_ids list to JSON string
        channel_profile_ids_json = json.dumps(channel_profile_ids) if channel_profile_ids else None

        # Normalize enabled_leagues to ESPN slugs (single source of truth)
        enabled_leagues_normalized = enabled_leagues
        if enabled_leagues:
            try:
                leagues = json.loads(enabled_leagues) if isinstance(enabled_leagues, str) else enabled_leagues
                enabled_leagues_normalized = json.dumps(normalize_league_codes(leagues))
            except (json.JSONDecodeError, TypeError):
                pass

        cursor.execute(
            """
            INSERT INTO event_epg_groups
            (dispatcharr_group_id, dispatcharr_account_id, group_name,
             assigned_league, assigned_sport, enabled,
             event_template_id, account_name, channel_start, channel_group_id,
             channel_group_name, stream_profile_id, channel_profile_ids,
             custom_regex_teams, custom_regex_teams_enabled,
             custom_regex_date, custom_regex_date_enabled,
             custom_regex_time, custom_regex_time_enabled,
             stream_include_regex, stream_include_regex_enabled,
             stream_exclude_regex, stream_exclude_regex_enabled,
             skip_builtin_filter, parent_group_id,
             is_multi_sport, enabled_leagues, channel_sort_order, overlap_handling,
             channel_assignment_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dispatcharr_group_id, dispatcharr_account_id, group_name,
                assigned_league.lower(), assigned_sport.lower(),
                1 if enabled else 0,
                event_template_id, account_name, channel_start,
                channel_group_id, channel_group_name, stream_profile_id, channel_profile_ids_json,
                custom_regex_teams, 1 if custom_regex_teams_enabled else 0,
                custom_regex_date, 1 if custom_regex_date_enabled else 0,
                custom_regex_time, 1 if custom_regex_time_enabled else 0,
                stream_include_regex, 1 if stream_include_regex_enabled else 0,
                stream_exclude_regex, 1 if stream_exclude_regex_enabled else 0,
                1 if skip_builtin_filter else 0, parent_group_id,
                1 if is_multi_sport else 0, enabled_leagues_normalized, channel_sort_order, overlap_handling,
                channel_assignment_mode
            )
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_event_epg_group(group_id: int, data: Dict[str, Any]) -> bool:
    """Update an event EPG group."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Normalize league/sport if provided
        if 'assigned_league' in data:
            data['assigned_league'] = data['assigned_league'].lower()
        if 'assigned_sport' in data:
            data['assigned_sport'] = data['assigned_sport'].lower()

        # When switching to AUTO mode, clear any existing channel_start
        # (AUTO mode calculates channel_start dynamically)
        if data.get('channel_assignment_mode') == 'auto':
            data['channel_start'] = None

        # Convert channel_profile_ids list to JSON string
        if 'channel_profile_ids' in data:
            if isinstance(data['channel_profile_ids'], list):
                data['channel_profile_ids'] = json.dumps(data['channel_profile_ids']) if data['channel_profile_ids'] else None
            # If it's already a string (JSON), leave it as is

        # Normalize enabled_leagues to ESPN slugs (single source of truth)
        if 'enabled_leagues' in data and data['enabled_leagues']:
            try:
                leagues = json.loads(data['enabled_leagues']) if isinstance(data['enabled_leagues'], str) else data['enabled_leagues']
                data['enabled_leagues'] = json.dumps(normalize_league_codes(leagues))
            except (json.JSONDecodeError, TypeError):
                pass

        # Exclude fields that aren't actual columns
        exclude_fields = {'id', 'group_id'}
        fields = [k for k in data.keys() if k not in exclude_fields]
        if not fields:
            return False

        set_clause = ', '.join([f"{f} = ?" for f in fields])
        values = [data[f] for f in fields] + [group_id]

        cursor.execute(f"""
            UPDATE event_epg_groups
            SET {set_clause}
            WHERE id = ?
        """, values)

        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_event_epg_group(group_id: int) -> bool:
    """Delete an event EPG group and any child groups that reference it."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # First delete any child groups that have this as parent
        cursor.execute("DELETE FROM event_epg_groups WHERE parent_group_id = ?", (group_id,))
        children_deleted = cursor.rowcount

        # Then delete the group itself
        cursor.execute("DELETE FROM event_epg_groups WHERE id = ?", (group_id,))
        group_deleted = cursor.rowcount > 0

        conn.commit()

        if children_deleted > 0:
            import logging
            logging.getLogger(__name__).info(f"Deleted {children_deleted} child group(s) when deleting parent group {group_id}")

        return group_deleted
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def update_event_epg_group_stats(
    group_id: int,
    stream_count: int,
    matched_count: int,
    total_stream_count: int = None,
    filtered_no_indicator: int = None,
    filtered_include_regex: int = None,
    filtered_exclude_regex: int = None,
    filtered_outside_lookahead: int = None,
    filtered_final: int = None,
    filtered_league_not_enabled: int = None,
    filtered_unsupported_sport: int = None
) -> bool:
    """
    Update stats after EPG generation.

    Args:
        group_id: Event group ID
        stream_count: Eligible streams (after all filtering)
        matched_count: Streams matched to ESPN events
        total_stream_count: Raw count from provider (optional)
        filtered_no_indicator: Streams without vs/@/at (optional)
        filtered_include_regex: Streams not matching inclusion regex (optional)
        filtered_exclude_regex: Streams matching exclusion regex (optional)
        filtered_outside_lookahead: Streams outside date range (optional)
        filtered_final: Final events excluded by setting (optional)
        filtered_league_not_enabled: Streams in non-enabled leagues (optional)
        filtered_unsupported_sport: Streams for unsupported sports (beach soccer, boxing/MMA, futsal)

    Returns:
        True if update succeeded
    """
    # Build dynamic update based on provided values
    fields = ["stream_count = ?", "matched_count = ?", "last_refresh = CURRENT_TIMESTAMP"]
    values = [stream_count, matched_count]

    if total_stream_count is not None:
        fields.append("total_stream_count = ?")
        values.append(total_stream_count)
    if filtered_no_indicator is not None:
        fields.append("filtered_no_indicator = ?")
        values.append(filtered_no_indicator)
    if filtered_include_regex is not None:
        fields.append("filtered_include_regex = ?")
        values.append(filtered_include_regex)
    if filtered_exclude_regex is not None:
        fields.append("filtered_exclude_regex = ?")
        values.append(filtered_exclude_regex)
    if filtered_outside_lookahead is not None:
        fields.append("filtered_outside_lookahead = ?")
        values.append(filtered_outside_lookahead)
    if filtered_final is not None:
        fields.append("filtered_final = ?")
        values.append(filtered_final)
    if filtered_league_not_enabled is not None:
        fields.append("filtered_league_not_enabled = ?")
        values.append(filtered_league_not_enabled)
    if filtered_unsupported_sport is not None:
        fields.append("filtered_unsupported_sport = ?")
        values.append(filtered_unsupported_sport)

    values.append(group_id)

    return db_execute(
        f"UPDATE event_epg_groups SET {', '.join(fields)} WHERE id = ?",
        tuple(values)
    ) > 0


def update_event_epg_group_last_refresh(group_id: int) -> bool:
    """Update only the last_refresh timestamp (without changing stats)."""
    return db_execute(
        "UPDATE event_epg_groups SET last_refresh = CURRENT_TIMESTAMP WHERE id = ?",
        (group_id,)
    ) > 0


def clear_auto_group_channel_starts() -> int:
    """
    Clear channel_start for all AUTO groups.

    AUTO groups calculate channel_start dynamically at EPG generation time,
    so any stored value should be cleared to ensure correct behavior.

    Returns:
        Number of groups updated
    """
    return db_execute(
        """UPDATE event_epg_groups
           SET channel_start = NULL
           WHERE channel_assignment_mode = 'auto'
             AND channel_start IS NOT NULL"""
    )


# =============================================================================
# Managed Channels Functions (for Channel Lifecycle Management)
# =============================================================================

def _parse_managed_channel_json_fields(channel: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON fields in a managed channel dict."""
    if channel and 'channel_profile_ids' in channel and channel['channel_profile_ids']:
        try:
            channel['channel_profile_ids'] = json.loads(channel['channel_profile_ids'])
        except (json.JSONDecodeError, TypeError):
            channel['channel_profile_ids'] = []
    elif channel:
        channel['channel_profile_ids'] = []
    return channel


def get_managed_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    """Get a managed channel by ID."""
    result = db_fetch_one("SELECT * FROM managed_channels WHERE id = ?", (channel_id,))
    return _parse_managed_channel_json_fields(result) if result else None


def get_managed_channel_by_dispatcharr_id(dispatcharr_channel_id: int) -> Optional[Dict[str, Any]]:
    """Get a managed channel by Dispatcharr channel ID."""
    result = db_fetch_one(
        "SELECT * FROM managed_channels WHERE dispatcharr_channel_id = ?",
        (dispatcharr_channel_id,)
    )
    return _parse_managed_channel_json_fields(result) if result else None


def get_managed_channel_by_event(espn_event_id: str, group_id: int = None) -> Optional[Dict[str, Any]]:
    """Get a managed channel by ESPN event ID, optionally filtered by group."""
    if group_id:
        result = db_fetch_one(
            "SELECT * FROM managed_channels WHERE espn_event_id = ? AND event_epg_group_id = ? AND deleted_at IS NULL",
            (espn_event_id, group_id)
        )
    else:
        result = db_fetch_one(
            "SELECT * FROM managed_channels WHERE espn_event_id = ? AND deleted_at IS NULL",
            (espn_event_id,)
        )
    return _parse_managed_channel_json_fields(result) if result else None


def get_managed_channels_for_group(group_id: int, include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Get all managed channels for an event EPG group."""
    query = "SELECT * FROM managed_channels WHERE event_epg_group_id = ?"
    if not include_deleted:
        query += " AND deleted_at IS NULL"
    query += " ORDER BY channel_number"
    channels = db_fetch_all(query, (group_id,))
    return [_parse_managed_channel_json_fields(c) for c in channels]


def get_all_managed_channels(include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Get all managed channels with group info and global timing settings."""
    query = """
        SELECT mc.*,
               eg.group_name,
               s.channel_delete_timing
        FROM managed_channels mc
        LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
        LEFT JOIN settings s ON s.id = 1
    """
    if not include_deleted:
        query += " WHERE mc.deleted_at IS NULL"
    query += " ORDER BY mc.channel_number ASC"
    channels = db_fetch_all(query)
    return [_parse_managed_channel_json_fields(c) for c in channels]


def get_channels_pending_deletion() -> List[Dict[str, Any]]:
    """Get channels that are scheduled for deletion and past their delete time."""
    # Use datetime() to normalize both values for comparison
    # scheduled_delete_at is stored as ISO8601 with timezone (e.g., 2025-11-28T04:59:59+00:00)
    # CURRENT_TIMESTAMP returns YYYY-MM-DD HH:MM:SS format
    # datetime() normalizes both to comparable format
    channels = db_fetch_all("""
        SELECT * FROM managed_channels
        WHERE scheduled_delete_at IS NOT NULL
        AND datetime(scheduled_delete_at) <= datetime('now')
        AND deleted_at IS NULL
        ORDER BY scheduled_delete_at
    """)
    return [_parse_managed_channel_json_fields(c) for c in channels]


def create_managed_channel(
    event_epg_group_id: int,
    dispatcharr_channel_id: int,
    dispatcharr_stream_id: int,
    channel_number: int,
    channel_name: str,
    tvg_id: str = None,
    espn_event_id: str = None,
    event_date: str = None,
    home_team: str = None,
    away_team: str = None,
    scheduled_delete_at: str = None,
    dispatcharr_logo_id: int = None,
    channel_profile_ids: list = None,  # List of channel profile IDs
    dispatcharr_uuid: str = None,  # Immutable UUID from Dispatcharr
    # V2 fields
    primary_stream_id: int = None,
    channel_group_id: int = None,
    stream_profile_id: int = None,
    logo_url: str = None,
    home_team_abbrev: str = None,
    home_team_logo: str = None,
    away_team_abbrev: str = None,
    away_team_logo: str = None,
    event_name: str = None,
    league: str = None,
    sport: str = None,
    venue: str = None,
    broadcast: str = None,
    sync_status: str = 'created',
    exception_keyword: str = None  # For keyword-based consolidation exceptions
) -> int:
    """
    Create a new managed channel record.

    Args:
        event_epg_group_id: Event EPG group that owns this channel
        dispatcharr_channel_id: Channel ID in Dispatcharr
        dispatcharr_stream_id: Primary stream ID (legacy, also added to managed_channel_streams)
        channel_number: Channel number
        channel_name: Channel display name
        tvg_id: EPG channel ID (format: teamarr-event-{espn_event_id})
        espn_event_id: ESPN event ID
        event_date: Event date/time in ISO format
        home_team: Home team name
        away_team: Away team name
        scheduled_delete_at: When to delete the channel
        dispatcharr_logo_id: Logo ID in Dispatcharr (for cleanup)
        channel_profile_ids: List of channel profile IDs the channel was added to
        primary_stream_id: Stream that created this channel (for 'separate' mode)
        channel_group_id: Dispatcharr channel group ID
        stream_profile_id: Dispatcharr stream profile ID
        logo_url: Source URL used for logo
        home_team_abbrev: Home team abbreviation
        home_team_logo: Home team logo URL
        away_team_abbrev: Away team abbreviation
        away_team_logo: Away team logo URL
        event_name: Event name (e.g., "49ers vs Browns")
        league: League code
        sport: Sport type
        venue: Venue name
        broadcast: Broadcast info
        sync_status: Initial sync status (default: 'created')

    Returns:
        ID of created record

    Raises:
        sqlite3.IntegrityError if dispatcharr_channel_id already exists
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Build column/value lists dynamically to handle schema variations
        # Core columns that always exist
        # Convert channel_profile_ids list to JSON string
        channel_profile_ids_json = json.dumps(channel_profile_ids) if channel_profile_ids else None

        columns = [
            'event_epg_group_id', 'dispatcharr_channel_id', 'dispatcharr_stream_id',
            'channel_number', 'channel_name', 'tvg_id', 'espn_event_id', 'event_date',
            'home_team', 'away_team', 'scheduled_delete_at', 'dispatcharr_logo_id',
            'channel_profile_ids'
        ]
        values = [
            event_epg_group_id, dispatcharr_channel_id, dispatcharr_stream_id,
            channel_number, channel_name, tvg_id, espn_event_id, event_date,
            home_team, away_team, scheduled_delete_at, dispatcharr_logo_id,
            channel_profile_ids_json
        ]

        # Check which optional columns exist in schema
        cursor.execute("PRAGMA table_info(managed_channels)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        # Optional v2 columns - only include if they exist
        optional_columns = [
            ('dispatcharr_uuid', dispatcharr_uuid),
            ('primary_stream_id', primary_stream_id),
            ('channel_group_id', channel_group_id),
            ('stream_profile_id', stream_profile_id),
            ('logo_url', logo_url),
            ('home_team_abbrev', home_team_abbrev),
            ('home_team_logo', home_team_logo),
            ('away_team_abbrev', away_team_abbrev),
            ('away_team_logo', away_team_logo),
            ('event_name', event_name),
            ('league', league),
            ('sport', sport),
            ('venue', venue),
            ('broadcast', broadcast),
            ('sync_status', sync_status),
            ('exception_keyword', exception_keyword),
        ]

        for col_name, col_value in optional_columns:
            if col_name in existing_columns:
                columns.append(col_name)
                values.append(col_value)

        placeholders = ', '.join(['?' for _ in columns])
        column_list = ', '.join(columns)

        cursor.execute(
            f"INSERT INTO managed_channels ({column_list}) VALUES ({placeholders})",
            values
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_managed_channel(channel_id: int, data: Dict[str, Any]) -> bool:
    """Update a managed channel record."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        fields = [k for k in data.keys() if k != 'id']
        if not fields:
            return False

        set_clause = ', '.join([f"{f} = ?" for f in fields])
        values = [data[f] for f in fields] + [channel_id]

        cursor.execute(f"""
            UPDATE managed_channels
            SET {set_clause}
            WHERE id = ?
        """, values)

        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_managed_channel_deleted(channel_id: int, logo_deleted: bool = None) -> bool:
    """
    Mark a managed channel as deleted (soft delete).

    Args:
        channel_id: ID of the managed channel
        logo_deleted: True if logo was deleted, False if deletion failed, None if no logo

    Returns:
        True if channel was marked as deleted
    """
    # Convert bool to int for SQLite (True=1, False=0, None=NULL)
    logo_deleted_val = None if logo_deleted is None else (1 if logo_deleted else 0)

    return db_execute(
        "UPDATE managed_channels SET deleted_at = CURRENT_TIMESTAMP, logo_deleted = ? WHERE id = ?",
        (logo_deleted_val, channel_id)
    ) > 0


def delete_managed_channel(channel_id: int) -> bool:
    """Hard delete a managed channel record."""
    return db_execute("DELETE FROM managed_channels WHERE id = ?", (channel_id,)) > 0


def get_global_channel_range() -> tuple[int, Optional[int]]:
    """
    Get the global channel range settings for Teamarr-managed channels.

    Returns:
        Tuple of (range_start, range_end) where:
        - range_start defaults to 101
        - range_end is None if not set (means use Dispatcharr max of 9999)
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT channel_range_start, channel_range_end FROM settings WHERE id = 1"
        ).fetchone()
        if row:
            start = row['channel_range_start'] or 101
            end = row['channel_range_end']  # None if not set = no limit
            return (start, end)
        return (101, None)
    except Exception:
        return (101, None)
    finally:
        conn.close()


def get_next_available_channel_range(dispatcharr_url: str = None, dispatcharr_username: str = None, dispatcharr_password: str = None) -> tuple[Optional[int], Optional[str]]:
    """
    Calculate the next available channel range start that won't conflict with existing groups.

    Smart allocation strategy:
    1. Respect global channel_range_start and channel_range_end settings
    2. Build a map of all reserved channel ranges (each group reserves channel_start + total_stream_count)
    3. Find the highest reserved channel within the global range
    4. Return next clean x01 starting point after all reserved ranges

    This prevents conflicts when groups grow - each group reserves space for ALL its streams
    (including placeholders and ineligible), not just currently active channels.

    Uses 100-channel intervals starting at x01 (101, 201, 301, etc.).

    Returns:
        Tuple of (next_channel_number, error_message):
        - (int, None) on success
        - (None, str) on failure with explanation
    """
    DISPATCHARR_MAX = 9999

    # Get global range settings
    global_start, global_end = get_global_channel_range()

    # If no end set, use Dispatcharr max
    effective_end = global_end if global_end else DISPATCHARR_MAX

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Get all groups with their channel_start and total_stream_count (potential max channels)
        groups = cursor.execute("""
            SELECT id, group_name, channel_start, total_stream_count, stream_count
            FROM event_epg_groups
            WHERE channel_start IS NOT NULL AND parent_group_id IS NULL
        """).fetchall()

        # Calculate highest reserved channel considering each group's full potential
        # Start from global_start - 1 so first allocation gets global_start's x01
        highest_reserved = global_start - 1
        highest_group_name = None

        for group in groups:
            channel_start = group['channel_start']
            # Skip groups outside our global range
            if channel_start < global_start or channel_start > effective_end:
                continue

            # Use total_stream_count (all streams) as reserved space, fall back to stream_count
            # Minimum reservation of 10 for safety margin
            reserved_count = max(
                group['total_stream_count'] or 0,
                group['stream_count'] or 0,
                10
            )
            group_max_channel = channel_start + reserved_count - 1

            if group_max_channel > highest_reserved:
                highest_reserved = group_max_channel
                highest_group_name = group['group_name']

        # Also check actual managed channels within global range
        row = cursor.execute("""
            SELECT MAX(channel_number) as max_num FROM managed_channels
            WHERE deleted_at IS NULL AND channel_number >= ? AND channel_number <= ?
        """, (global_start, effective_end)).fetchone()

        if row and row['max_num'] and row['max_num'] > highest_reserved:
            highest_reserved = row['max_num']
            highest_group_name = None  # It's from managed channels, not a group

        # Check Dispatcharr for channels within our global range
        if dispatcharr_url:
            try:
                from api.dispatcharr_client import ChannelManager
                channel_mgr = ChannelManager(dispatcharr_url, dispatcharr_username or '', dispatcharr_password or '')
                channels = channel_mgr.get_channels()
                for ch in channels:
                    ch_num = ch.get('channel_number')
                    if ch_num and global_start <= ch_num <= effective_end and ch_num > highest_reserved:
                        highest_reserved = ch_num
                        highest_group_name = None
            except Exception as e:
                logger.debug(f"Could not query Dispatcharr channels: {e}")

        # Calculate next x01 after highest reserved channel
        if highest_reserved < global_start:
            # No channels yet in range, start at the first x01 at or after global_start
            next_range = ((global_start - 1) // 100 + 1) * 100 + 1
            if next_range < global_start:
                next_range += 100
        else:
            next_range = ((int(highest_reserved) // 100) + 1) * 100 + 1

        # Check we don't exceed the effective end
        if next_range > effective_end:
            if global_end:
                error_msg = (
                    f"Channel range exhausted. Next available would be {next_range}, "
                    f"but your configured range ends at {global_end}. "
                    f"Increase 'Channel Range End' in Settings, or manually set a channel start for this group."
                )
            else:
                error_msg = (
                    f"Channel range exhausted. Next available would be {next_range}, "
                    f"but Dispatcharr's maximum is {DISPATCHARR_MAX}. "
                    f"You'll need to manually reassign existing groups to lower channel numbers."
                )
            logger.warning(f"Cannot auto-assign channel range: {error_msg}")
            return (None, error_msg)

        range_desc = f"{global_start}-{global_end}" if global_end else f"{global_start}+"
        logger.debug(f"Channel range calculation: global=({range_desc}), highest_reserved={highest_reserved}, next_range={next_range}")
        return (int(next_range), None)

    finally:
        conn.close()


def get_next_channel_number(group_id: int, auto_assign: bool = True) -> Optional[int]:
    """
    Get the next available channel number for a group.

    For MANUAL groups: Uses the group's channel_start and finds the next unused number.
    For AUTO groups: Calculates effective channel_start based on sort_order and
    total_stream_count of preceding AUTO groups, using the global channel range.

    Args:
        group_id: The event group ID
        auto_assign: If True, auto-assign a channel_start when missing (MANUAL mode only)

    Returns:
        The next available channel number, or None if disabled or would exceed 9999
    """
    MAX_CHANNEL = 9999

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Get the group's channel_start and assignment mode
        group = cursor.execute(
            """SELECT channel_start, channel_assignment_mode, sort_order, total_stream_count
               FROM event_epg_groups WHERE id = ?""",
            (group_id,)
        ).fetchone()

        if not group:
            return None

        channel_start = group['channel_start']
        assignment_mode = group['channel_assignment_mode'] or 'manual'

        # For AUTO mode, calculate effective channel_start dynamically
        # Also calculate block_end to enforce range limit
        block_end = None
        if assignment_mode == 'auto':
            channel_start = _calculate_auto_channel_start(cursor, group_id, group['sort_order'])
            if not channel_start:
                logger.warning(f"Could not calculate auto channel_start for group {group_id}")
                return None
            # Calculate block_end based on stream count
            stream_count = group['total_stream_count'] or 0
            blocks_needed = (stream_count + 9) // 10 if stream_count > 0 else 1
            range_size = blocks_needed * 10
            block_end = channel_start + range_size - 1

        # For MANUAL mode with no channel_start, auto-assign if enabled
        elif not channel_start and auto_assign:
            channel_start, error_msg = get_next_available_channel_range()
            if channel_start:
                # Save to the group
                cursor.execute(
                    "UPDATE event_epg_groups SET channel_start = ? WHERE id = ?",
                    (channel_start, group_id)
                )
                conn.commit()
                logger.info(f"Auto-assigned channel_start {channel_start} to MANUAL group {group_id}")
            elif error_msg:
                logger.warning(f"Could not auto-assign channel_start for group {group_id}: {error_msg}")

        if not channel_start:
            return None

        # Get all active channel numbers for this group
        used_numbers = cursor.execute(
            """
            SELECT channel_number FROM managed_channels
            WHERE event_epg_group_id = ? AND deleted_at IS NULL
            ORDER BY channel_number
            """,
            (group_id,)
        ).fetchall()

        used_set = {row['channel_number'] for row in used_numbers}

        # Find the first available number starting from channel_start
        next_num = channel_start
        while next_num in used_set:
            next_num += 1
            # Enforce AUTO mode block limit
            if block_end and next_num > block_end:
                logger.warning(
                    f"Cannot allocate channel for AUTO group {group_id}: "
                    f"would exceed block_end {block_end} (range {channel_start}-{block_end})"
                )
                return None
            # Enforce Dispatcharr's max channel limit
            if next_num > MAX_CHANNEL:
                logger.warning(f"Cannot allocate channel for group {group_id}: would exceed {MAX_CHANNEL}")
                return None

        # Final check for AUTO mode block limit
        if block_end and next_num > block_end:
            logger.warning(
                f"Cannot allocate channel for AUTO group {group_id}: "
                f"{next_num} exceeds block_end {block_end}"
            )
            return None
        # Final check (in case channel_start itself exceeds limit)
        if next_num > MAX_CHANNEL:
            logger.warning(f"Cannot allocate channel for group {group_id}: {next_num} exceeds {MAX_CHANNEL}")
            return None

        return next_num
    finally:
        conn.close()


def _calculate_auto_channel_start(cursor, group_id: int, sort_order: int) -> Optional[int]:
    """
    Calculate effective channel_start for an AUTO group based on sort_order.

    AUTO groups are allocated channel blocks in 10-channel increments (xxx1-xxx0).
    Each group starts at a clean xx01 boundary based on how many blocks preceding
    groups need (rounded up from their total_stream_count to next 10).

    Example with range_start=9001:
    - Group 1 (16 streams): 9001 (needs 2 blocks of 10)
    - Group 2 (20 streams): 9021 (needs 2 blocks of 10)
    - Group 3 (250 streams): 9041 (needs 25 blocks of 10)
    - Group 4 (31 streams): 9291 (needs 4 blocks of 10)

    Args:
        cursor: Database cursor
        group_id: The target group's ID
        sort_order: The target group's sort_order

    Returns:
        The calculated channel_start, or None if range exhausted
    """
    # Get global channel range settings
    settings = cursor.execute(
        "SELECT channel_range_start, channel_range_end FROM settings WHERE id = 1"
    ).fetchone()

    if not settings:
        return None

    range_start = settings['channel_range_start'] or 101
    range_end = settings['channel_range_end']  # Can be None (no limit)

    # Get all AUTO groups sorted by sort_order, excluding child groups
    auto_groups = cursor.execute(
        """
        SELECT id, sort_order, total_stream_count
        FROM event_epg_groups
        WHERE channel_assignment_mode = 'auto'
          AND parent_group_id IS NULL
          AND enabled = 1
        ORDER BY sort_order ASC
        """
    ).fetchall()

    # Calculate cumulative blocks for this group
    # Each group reserves ceil(stream_count / 10) blocks of 10 channels
    cumulative_blocks = 0
    for g in auto_groups:
        if g['id'] == group_id:
            break
        # Reserve blocks for this preceding group (minimum 1 block if any streams)
        stream_count = g['total_stream_count'] or 0
        if stream_count > 0:
            # Round up to next 10: ceil(stream_count / 10)
            blocks_needed = (stream_count + 9) // 10
            cumulative_blocks += blocks_needed

    # Each block is 10 channels
    effective_start = range_start + (cumulative_blocks * 10)

    # Check if we exceed the range
    if range_end and effective_start > range_end:
        logger.warning(
            f"AUTO group {group_id} cannot fit in global range: "
            f"calculated start {effective_start} exceeds range_end {range_end}"
        )
        return None

    return effective_start


def get_auto_group_block_start(group_id: int) -> Optional[int]:
    """
    Get the calculated block start for an AUTO group.

    This returns where the group's channel block SHOULD start based on
    sort_order and preceding groups, regardless of what channels currently exist.

    Args:
        group_id: The AUTO group's ID

    Returns:
        The block start channel number, or None if not an AUTO group or error
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Get the group's assignment mode and sort_order
        group = cursor.execute(
            "SELECT channel_assignment_mode, sort_order FROM event_epg_groups WHERE id = ?",
            (group_id,)
        ).fetchone()

        if not group or group['channel_assignment_mode'] != 'auto':
            return None

        # Use the existing calculation function
        return _calculate_auto_channel_start(cursor, group_id, group['sort_order'])
    finally:
        conn.close()


def cleanup_old_deleted_channels(days_old: int = 30) -> int:
    """
    Hard delete managed channel records that were soft-deleted more than N days ago.

    Returns count of records deleted.
    """
    return db_execute(
        """
        DELETE FROM managed_channels
        WHERE deleted_at IS NOT NULL
        AND deleted_at < datetime('now', ? || ' days')
        """,
        (f"-{days_old}",)
    )


# Module-level cache for soccer slug mapping (built once per session)
_soccer_slug_mapping_cache = None


def get_soccer_slug_mapping() -> Dict[str, str]:
    """
    Get mapping from ESPN soccer league slugs (esp.1) to league_config codes (laliga).

    This is used by multi-sport mode to translate soccer_team_leagues cache results
    to codes that TeamMatcher can use.

    Returns:
        Dict mapping ESPN slug to league_config code, e.g.:
        {'esp.1': 'laliga', 'eng.1': 'epl', 'ger.1': 'bundesliga', ...}
    """
    global _soccer_slug_mapping_cache

    if _soccer_slug_mapping_cache is not None:
        return _soccer_slug_mapping_cache

    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT league_code, api_path
            FROM league_config
            WHERE sport = 'soccer' AND api_path IS NOT NULL
        """).fetchall()

        mapping = {}
        for row in rows:
            # api_path is like "soccer/esp.1", extract "esp.1"
            api_path = row['api_path']
            if '/' in api_path:
                slug = api_path.split('/')[-1]
                mapping[slug] = row['league_code']

        _soccer_slug_mapping_cache = mapping
        return mapping
    finally:
        conn.close()


def clear_soccer_slug_mapping_cache():
    """Clear the soccer slug mapping cache. Call when soccer cache is refreshed."""
    global _soccer_slug_mapping_cache
    _soccer_slug_mapping_cache = None


# =============================================================================
# EPG Generation Stats Functions (Single Source of Truth)
# =============================================================================

def save_epg_generation_stats(stats: Dict[str, Any]) -> int:
    """
    Save comprehensive EPG generation stats to epg_history.

    This is the single source of truth for all EPG generation statistics.

    Args:
        stats: Dict with the following keys:
            # Basic info
            file_path: str - path to generated EPG file
            file_size: int - file size in bytes
            file_hash: str - SHA256 hash of file
            generation_time_seconds: float - how long generation took
            api_calls_made: int - number of API calls
            status: str - 'success', 'error', or 'partial'
            error_message: str - error message if status is 'error'

            # Legacy totals (maintained for backwards compatibility)
            num_channels: int - total channels
            num_programmes: int - total programmes
            num_events: int - total events (with ESPN ID)
            num_pregame: int - total pregame filler
            num_postgame: int - total postgame filler
            num_idle: int - total idle filler

            # Team-based EPG breakdown
            team_based_channels: int
            team_based_events: int
            team_based_pregame: int
            team_based_postgame: int
            team_based_idle: int

            # Event-based EPG breakdown
            event_based_channels: int
            event_based_events: int
            event_based_pregame: int
            event_based_postgame: int

            # Event-based filtering stats (aggregated across all groups)
            event_total_streams: int - raw streams from provider
            event_filtered_no_indicator: int - streams without vs/@/at
            event_filtered_include_regex: int - streams not matching inclusion regex
            event_filtered_exclude_regex: int - streams matching exclusion regex
            event_filtered_outside_lookahead: int - past games
            event_filtered_final: int - final events (when excluded)
            event_eligible_streams: int - streams that passed all filters
            event_matched_streams: int - streams matched to ESPN events

            # Quality/Error stats
            unresolved_vars_count: int
            coverage_gaps_count: int
            warnings_json: str (JSON array)

    Returns:
        ID of the created epg_history record
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Build the INSERT statement with all available columns
        cursor.execute("""
            INSERT INTO epg_history (
                file_path, file_size, file_hash,
                generation_time_seconds, api_calls_made,
                status, error_message, triggered_by,
                num_channels, num_programmes, num_events,
                num_pregame, num_postgame, num_idle,
                team_based_channels, team_based_events,
                team_based_pregame, team_based_postgame, team_based_idle,
                event_based_channels, event_based_events,
                event_based_pregame, event_based_postgame,
                event_total_streams, event_filtered_no_indicator,
                event_filtered_include_regex, event_filtered_exclude_regex,
                event_filtered_outside_lookahead,
                event_filtered_final, event_filtered_league_not_enabled,
                event_filtered_unsupported_sport,
                event_eligible_streams, event_matched_streams,
                unresolved_vars_count, coverage_gaps_count, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stats.get('file_path', ''),
            stats.get('file_size', 0),
            stats.get('file_hash', ''),
            stats.get('generation_time_seconds', 0),
            stats.get('api_calls_made', 0),
            stats.get('status', 'success'),
            stats.get('error_message'),
            stats.get('triggered_by', 'manual'),  # 'manual', 'scheduler', 'api'
            # Legacy totals
            stats.get('num_channels', 0),
            stats.get('num_programmes', 0),
            stats.get('num_events', 0),
            stats.get('num_pregame', 0),
            stats.get('num_postgame', 0),
            stats.get('num_idle', 0),
            # Team-based breakdown
            stats.get('team_based_channels', 0),
            stats.get('team_based_events', 0),
            stats.get('team_based_pregame', 0),
            stats.get('team_based_postgame', 0),
            stats.get('team_based_idle', 0),
            # Event-based breakdown
            stats.get('event_based_channels', 0),
            stats.get('event_based_events', 0),
            stats.get('event_based_pregame', 0),
            stats.get('event_based_postgame', 0),
            # Event-based filtering stats
            stats.get('event_total_streams', 0),
            stats.get('event_filtered_no_indicator', 0),
            stats.get('event_filtered_include_regex', 0),
            stats.get('event_filtered_exclude_regex', 0),
            stats.get('event_filtered_outside_lookahead', 0),
            stats.get('event_filtered_final', 0),
            stats.get('event_filtered_league_not_enabled', 0),
            stats.get('event_filtered_unsupported_sport', 0),
            stats.get('event_eligible_streams', 0),
            stats.get('event_matched_streams', 0),
            # Quality stats
            stats.get('unresolved_vars_count', 0),
            stats.get('coverage_gaps_count', 0),
            stats.get('warnings_json'),
        ))

        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _parse_epg_history_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Parse EPG history record, extracting warnings from warnings_json."""
    if record:
        parse_json_fields(record, ['warnings_json'])
        # Copy to expected 'warnings' key
        record['warnings'] = record.get('warnings_json', []) or []
    return record


def get_latest_epg_stats() -> Optional[Dict[str, Any]]:
    """
    Get the most recent EPG generation stats.

    This is the single source of truth for displaying EPG stats in the UI.

    Returns:
        Dict with all stats columns, or None if no history exists
    """
    stats = db_fetch_one("""
        SELECT * FROM epg_history
        WHERE status = 'success'
        ORDER BY generated_at DESC
        LIMIT 1
    """)
    return _parse_epg_history_record(stats)


def get_epg_history(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get recent EPG generation history.

    Args:
        limit: Number of records to return

    Returns:
        List of epg_history dicts, newest first
    """
    results = db_fetch_all("""
        SELECT * FROM epg_history
        ORDER BY generated_at DESC
        LIMIT ?
    """, (limit,))
    return [_parse_epg_history_record(r) for r in results]


def get_epg_stats_summary() -> Dict[str, Any]:
    """
    Get a summary of EPG stats for the dashboard.

    Returns aggregated stats from the most recent successful generation,
    formatted for display in UI tiles.

    Returns:
        Dict with:
            - last_generated: ISO datetime string
            - total_channels: int
            - total_programmes: int
            - events: dict with team_based and event_based counts
            - filler: dict with pregame, postgame, idle counts (broken down by type)
            - quality: dict with unresolved_vars, coverage_gaps, warnings_count
    """
    latest = get_latest_epg_stats()

    if not latest:
        return {
            'last_generated': None,
            'total_channels': 0,
            'total_programmes': 0,
            'channels': {
                'total': 0,
                'team_based': 0,
                'event_based': 0,
            },
            'events': {
                'total': 0,
                'team_based': 0,
                'event_based': 0,
            },
            'filler': {
                'total': 0,
                'pregame': {'total': 0, 'team_based': 0, 'event_based': 0},
                'postgame': {'total': 0, 'team_based': 0, 'event_based': 0},
                'idle': {'total': 0, 'team_based': 0, 'event_based': 0},
            },
            'quality': {
                'unresolved_vars': 0,
                'coverage_gaps': 0,
                'warnings_count': 0,
            },
            'generation_time': 0,
        }

    # Calculate totals from breakdowns (use breakdown values if available, else legacy)
    team_channels = latest.get('team_based_channels', 0) or 0
    event_channels = latest.get('event_based_channels', 0) or 0

    team_events = latest.get('team_based_events', 0) or 0
    event_events = latest.get('event_based_events', 0) or 0
    total_events = team_events + event_events if (team_events or event_events) else (latest.get('num_events', 0) or 0)

    team_pregame = latest.get('team_based_pregame', 0) or 0
    event_pregame = latest.get('event_based_pregame', 0) or 0
    total_pregame = team_pregame + event_pregame if (team_pregame or event_pregame) else (latest.get('num_pregame', 0) or 0)

    team_postgame = latest.get('team_based_postgame', 0) or 0
    event_postgame = latest.get('event_based_postgame', 0) or 0
    total_postgame = team_postgame + event_postgame if (team_postgame or event_postgame) else (latest.get('num_postgame', 0) or 0)

    team_idle = latest.get('team_based_idle', 0) or 0
    # Event-based doesn't have idle
    total_idle = team_idle

    return {
        'last_generated': latest.get('generated_at'),
        'total_channels': latest.get('num_channels', 0) or 0,
        'total_programmes': latest.get('num_programmes', 0) or 0,
        'channels': {
            'total': latest.get('num_channels', 0) or 0,
            'team_based': team_channels,
            'event_based': event_channels,
        },
        'events': {
            'total': total_events,
            'team_based': team_events,
            'event_based': event_events,
        },
        'filler': {
            'total': total_pregame + total_postgame + total_idle,
            'pregame': {
                'total': total_pregame,
                'team_based': team_pregame,
                'event_based': event_pregame,
            },
            'postgame': {
                'total': total_postgame,
                'team_based': team_postgame,
                'event_based': event_postgame,
            },
            'idle': {
                'total': total_idle,
                'team_based': team_idle,
                'event_based': 0,
            },
        },
        'quality': {
            'unresolved_vars': latest.get('unresolved_vars_count', 0) or 0,
            'coverage_gaps': latest.get('coverage_gaps_count', 0) or 0,
            'warnings_count': len(latest.get('warnings', [])),
        },
        'generation_time': latest.get('generation_time_seconds', 0) or 0,
    }


# =============================================================================
# Channel Lifecycle V2 - Stream Management Functions
# =============================================================================

def get_all_managed_channel_streams() -> List[Dict[str, Any]]:
    """Get all streams across all active channels with channel info for keyword enforcement.

    Returns stream records joined with channel info needed for keyword placement checks.
    """
    return db_fetch_all("""
        SELECT
            mcs.*,
            mc.espn_event_id,
            mc.event_epg_group_id,
            mc.exception_keyword as channel_exception_keyword,
            mc.channel_name,
            mc.dispatcharr_channel_id
        FROM managed_channel_streams mcs
        JOIN managed_channels mc ON mcs.managed_channel_id = mc.id
        WHERE mcs.removed_at IS NULL
          AND mc.deleted_at IS NULL
    """)


def get_channel_streams(managed_channel_id: int, include_removed: bool = False) -> List[Dict[str, Any]]:
    """Get all streams attached to a managed channel, ordered by priority."""
    query = """
        SELECT mcs.*, eg.group_name as source_group_name
        FROM managed_channel_streams mcs
        LEFT JOIN event_epg_groups eg ON mcs.source_group_id = eg.id
        WHERE mcs.managed_channel_id = ?
    """
    if not include_removed:
        query += " AND mcs.removed_at IS NULL"
    query += " ORDER BY mcs.priority"
    return db_fetch_all(query, (managed_channel_id,))


def add_stream_to_channel(
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    source_group_id: int,
    stream_name: str = None,
    source_group_type: str = 'parent',
    priority: int = None,
    m3u_account_id: int = None,
    m3u_account_name: str = None,
    exception_keyword: str = None
) -> int:
    """
    Add a stream to a managed channel.

    Args:
        managed_channel_id: ID of the managed channel
        dispatcharr_stream_id: Dispatcharr stream ID
        source_group_id: Event EPG group that contributed this stream
        stream_name: Stream name for display
        source_group_type: 'parent' or 'child'
        priority: Stream priority (0=primary, higher=failover). Auto-assigned if None.
        m3u_account_id: M3U account ID
        m3u_account_name: M3U account name
        exception_keyword: Keyword that matched this stream (for keyword-based consolidation)

    Returns:
        ID of the created stream record
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Auto-assign priority if not specified
        if priority is None:
            result = cursor.execute("""
                SELECT MAX(priority) as max_p FROM managed_channel_streams
                WHERE managed_channel_id = ? AND removed_at IS NULL
            """, (managed_channel_id,)).fetchone()
            priority = (result['max_p'] or -1) + 1 if result and result['max_p'] is not None else 0

        # Check if exception_keyword column exists (backward compatibility)
        cursor.execute("PRAGMA table_info(managed_channel_streams)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'exception_keyword' in existing_columns:
            cursor.execute("""
                INSERT INTO managed_channel_streams
                (managed_channel_id, dispatcharr_stream_id, stream_name,
                 source_group_id, source_group_type, priority,
                 m3u_account_id, m3u_account_name, exception_keyword)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (managed_channel_id, dispatcharr_stream_id, stream_name,
                  source_group_id, source_group_type, priority,
                  m3u_account_id, m3u_account_name, exception_keyword))
        else:
            cursor.execute("""
                INSERT INTO managed_channel_streams
                (managed_channel_id, dispatcharr_stream_id, stream_name,
                 source_group_id, source_group_type, priority,
                 m3u_account_id, m3u_account_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (managed_channel_id, dispatcharr_stream_id, stream_name,
                  source_group_id, source_group_type, priority,
                  m3u_account_id, m3u_account_name))

        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def remove_stream_from_channel(
    managed_channel_id: int,
    dispatcharr_stream_id: int,
    reason: str = None
) -> bool:
    """
    Remove a stream from a managed channel (soft delete).

    Args:
        managed_channel_id: ID of the managed channel
        dispatcharr_stream_id: Stream ID to remove
        reason: Reason for removal

    Returns:
        True if stream was removed
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Soft delete the stream
        cursor.execute("""
            UPDATE managed_channel_streams
            SET removed_at = CURRENT_TIMESTAMP, remove_reason = ?
            WHERE managed_channel_id = ? AND dispatcharr_stream_id = ? AND removed_at IS NULL
        """, (reason, managed_channel_id, dispatcharr_stream_id))

        if cursor.rowcount == 0:
            return False

        # Re-sequence remaining streams
        remaining = cursor.execute("""
            SELECT id, priority FROM managed_channel_streams
            WHERE managed_channel_id = ? AND removed_at IS NULL
            ORDER BY priority
        """, (managed_channel_id,)).fetchall()

        for i, stream in enumerate(remaining):
            if stream['priority'] != i:
                cursor.execute("""
                    UPDATE managed_channel_streams SET priority = ? WHERE id = ?
                """, (i, stream['id']))

        conn.commit()
        return True
    finally:
        conn.close()


def stream_exists_on_channel(managed_channel_id: int, dispatcharr_stream_id: int) -> bool:
    """Check if a stream is already attached to a channel."""
    result = db_fetch_one("""
        SELECT 1 FROM managed_channel_streams
        WHERE managed_channel_id = ? AND dispatcharr_stream_id = ? AND removed_at IS NULL
    """, (managed_channel_id, dispatcharr_stream_id))
    return result is not None


# =============================================================================
# Channel Lifecycle V2 - History/Audit Functions
# =============================================================================

def log_channel_history(
    managed_channel_id: int,
    change_type: str,
    change_source: str = None,
    field_name: str = None,
    old_value: str = None,
    new_value: str = None,
    notes: str = None
) -> int:
    """
    Log a change to channel history.

    Args:
        managed_channel_id: ID of the managed channel
        change_type: Type of change (created, modified, stream_added, stream_removed,
                     stream_reordered, verified, drifted, deleted, restored)
        change_source: Source of change (epg_generation, reconciliation, manual, external_sync)
        field_name: Name of field that changed (for modified type)
        old_value: Previous value
        new_value: New value
        notes: Additional notes

    Returns:
        ID of the history record
    """
    return db_insert("""
        INSERT INTO managed_channel_history
        (managed_channel_id, change_type, change_source, field_name, old_value, new_value, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (managed_channel_id, change_type, change_source, field_name, old_value, new_value, notes))


def get_channel_history(managed_channel_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """Get history for a specific channel."""
    return db_fetch_all("""
        SELECT * FROM managed_channel_history
        WHERE managed_channel_id = ?
        ORDER BY changed_at DESC
        LIMIT ?
    """, (managed_channel_id, limit))


def get_recent_channel_changes(hours: int = 24, change_types: List[str] = None) -> List[Dict[str, Any]]:
    """Get recent changes across all channels."""
    query = """
        SELECT mch.*, mc.channel_name, mc.channel_number
        FROM managed_channel_history mch
        JOIN managed_channels mc ON mch.managed_channel_id = mc.id
        WHERE mch.changed_at >= datetime('now', ?)
    """
    params = [f'-{hours} hours']

    if change_types:
        placeholders = ','.join('?' * len(change_types))
        query += f" AND mch.change_type IN ({placeholders})"
        params.extend(change_types)

    query += " ORDER BY mch.changed_at DESC"

    return db_fetch_all(query, tuple(params))


def get_channels_needing_reorder() -> List[Dict[str, Any]]:
    """Find events where keyword channel has a lower number than main channel.

    Returns list of dicts with 'main_channel' and 'keyword_channel' that need swapping.
    """
    # Find all events with both main and keyword channels
    rows = db_fetch_all("""
        SELECT
            m.id as main_id,
            m.channel_number as main_number,
            m.dispatcharr_channel_id as main_dispatcharr_id,
            m.espn_event_id,
            k.id as keyword_id,
            k.channel_number as keyword_number,
            k.dispatcharr_channel_id as keyword_dispatcharr_id,
            k.exception_keyword
        FROM managed_channels m
        JOIN managed_channels k ON m.espn_event_id = k.espn_event_id
                               AND m.event_epg_group_id = k.event_epg_group_id
        WHERE m.deleted_at IS NULL
          AND k.deleted_at IS NULL
          AND (m.exception_keyword IS NULL OR m.exception_keyword = '')
          AND k.exception_keyword IS NOT NULL
          AND k.exception_keyword != ''
          AND k.channel_number < m.channel_number
    """)

    results = []
    for row in rows:
        results.append({
            'main_channel': {
                'id': row['main_id'],
                'channel_number': row['main_number'],
                'dispatcharr_channel_id': row['main_dispatcharr_id'],
                'espn_event_id': row['espn_event_id']
            },
            'keyword_channel': {
                'id': row['keyword_id'],
                'channel_number': row['keyword_number'],
                'dispatcharr_channel_id': row['keyword_dispatcharr_id'],
                'exception_keyword': row['exception_keyword']
            }
        })
    return results


def cleanup_old_channel_history(days: int = 90) -> int:
    """Delete channel history older than N days. Returns count deleted."""
    return db_execute("""
        DELETE FROM managed_channel_history
        WHERE changed_at < datetime('now', ? || ' days')
    """, (f"-{days}",))


# =============================================================================
# Channel Lifecycle V2 - Channel Lookup Functions (Duplicate Handling)
# =============================================================================

def find_existing_channel(
    group_id: int,
    event_id: str,
    stream_id: int = None,
    mode: str = 'consolidate',
    exception_keyword: str = None
) -> Optional[Dict[str, Any]]:
    """
    Find existing channel based on duplicate handling mode.

    Args:
        group_id: Event EPG group ID
        event_id: ESPN event ID
        stream_id: Stream ID (only used for 'separate' mode)
        mode: Duplicate handling mode ('ignore', 'consolidate', 'separate')
        exception_keyword: Canonical keyword for keyword-based consolidation

    Returns:
        Managed channel dict if found, None otherwise
    """
    if mode == 'separate':
        # Must match specific stream
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND primary_stream_id = ?
              AND deleted_at IS NULL
        """, (group_id, event_id, stream_id))
    elif exception_keyword:
        # Keyword-based consolidation: find channel for this event+keyword
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND exception_keyword = ?
              AND deleted_at IS NULL
        """, (group_id, event_id, exception_keyword))
    else:
        # Any channel for this event in this group (without exception_keyword)
        # When in consolidate mode without keywords, find a channel without a keyword
        # This ensures keyword streams and non-keyword streams don't mix
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND (exception_keyword IS NULL OR exception_keyword = '')
              AND deleted_at IS NULL
        """, (group_id, event_id))


def find_parent_channel_for_event(parent_group_id: int, event_id: str, exception_keyword: str = None) -> Optional[Dict[str, Any]]:
    """Find a parent group's channel for a given event (used by child groups).

    Args:
        parent_group_id: The parent group ID
        event_id: The ESPN event ID
        exception_keyword: Optional exception keyword to match (for sub-consolidated channels)

    Returns:
        The matching managed channel record, or None if not found
    """
    if exception_keyword:
        # Look for channel with matching keyword
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND exception_keyword = ?
              AND deleted_at IS NULL
        """, (parent_group_id, event_id, exception_keyword))
    else:
        # Look for main channel (no keyword)
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND (exception_keyword IS NULL OR exception_keyword = '')
              AND deleted_at IS NULL
        """, (parent_group_id, event_id))


def find_any_channel_for_event(
    event_id: str,
    exception_keyword: str = None,
    exclude_group_id: int = None,
    any_keyword: bool = False
) -> Optional[Dict[str, Any]]:
    """Find any group's channel for a given event (used by multi-sport groups for overlap handling).

    Unlike find_parent_channel_for_event which only searches within a parent group,
    this searches across ALL groups to find if an event already has a channel.

    Args:
        event_id: The ESPN event ID
        exception_keyword: Optional exception keyword to match (ignored if any_keyword=True)
        exclude_group_id: Optional group ID to exclude from search (usually the current group)
        any_keyword: If True, find any channel for the event regardless of exception_keyword

    Returns:
        The matching managed channel record, or None if not found
    """
    # For cross-group consolidation, find ANY channel for the event (ignore keywords)
    if any_keyword:
        if exclude_group_id:
            return db_fetch_one("""
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.espn_event_id = ?
                  AND mc.event_epg_group_id != ?
                  AND mc.deleted_at IS NULL
                ORDER BY mc.created_at ASC
                LIMIT 1
            """, (event_id, exclude_group_id))
        else:
            return db_fetch_one("""
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.espn_event_id = ?
                  AND mc.deleted_at IS NULL
                ORDER BY mc.created_at ASC
                LIMIT 1
            """, (event_id,))

    if exception_keyword:
        if exclude_group_id:
            return db_fetch_one("""
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.espn_event_id = ?
                  AND mc.exception_keyword = ?
                  AND mc.event_epg_group_id != ?
                  AND mc.deleted_at IS NULL
            """, (event_id, exception_keyword, exclude_group_id))
        else:
            return db_fetch_one("""
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.espn_event_id = ?
                  AND mc.exception_keyword = ?
                  AND mc.deleted_at IS NULL
            """, (event_id, exception_keyword))
    else:
        # Look for main channel (no keyword)
        if exclude_group_id:
            return db_fetch_one("""
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.espn_event_id = ?
                  AND (mc.exception_keyword IS NULL OR mc.exception_keyword = '')
                  AND mc.event_epg_group_id != ?
                  AND mc.deleted_at IS NULL
            """, (event_id, exclude_group_id))
        else:
            return db_fetch_one("""
                SELECT mc.*, eg.group_name
                FROM managed_channels mc
                LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
                WHERE mc.espn_event_id = ?
                  AND (mc.exception_keyword IS NULL OR mc.exception_keyword = '')
                  AND mc.deleted_at IS NULL
            """, (event_id,))


def get_channels_by_sync_status(status: str) -> List[Dict[str, Any]]:
    """Get all channels with a specific sync status."""
    return db_fetch_all("""
        SELECT mc.*, eg.group_name
        FROM managed_channels mc
        LEFT JOIN event_epg_groups eg ON mc.event_epg_group_id = eg.id
        WHERE mc.sync_status = ? AND mc.deleted_at IS NULL
        ORDER BY mc.channel_number
    """, (status,))


def update_channel_sync_status(
    channel_id: int,
    status: str,
    notes: str = None
) -> bool:
    """Update a channel's sync status."""
    return db_execute("""
        UPDATE managed_channels
        SET sync_status = ?, sync_notes = ?, last_verified_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (status, notes, channel_id)) > 0


# =============================================================================
# Channel Lifecycle V2 - Parent/Child Group Functions
# =============================================================================

def get_parent_groups(enabled_only: bool = True) -> List[Dict[str, Any]]:
    """Get all parent groups (groups with parent_group_id IS NULL)."""
    query = """
        SELECT g.*, t.name as event_template_name
        FROM event_epg_groups g
        LEFT JOIN templates t ON g.event_template_id = t.id
        WHERE g.parent_group_id IS NULL
    """
    if enabled_only:
        query += " AND g.enabled = 1"
    query += " ORDER BY g.group_name"
    return db_fetch_all(query)


def get_child_groups(parent_id: int = None, enabled_only: bool = True) -> List[Dict[str, Any]]:
    """
    Get child groups, optionally filtered by parent.

    Args:
        parent_id: If specified, only return children of this parent
        enabled_only: Only return enabled groups

    Returns:
        List of child groups
    """
    if parent_id:
        query = """
            SELECT g.*, t.name as event_template_name,
                   pg.group_name as parent_group_name
            FROM event_epg_groups g
            LEFT JOIN templates t ON g.event_template_id = t.id
            LEFT JOIN event_epg_groups pg ON g.parent_group_id = pg.id
            WHERE g.parent_group_id = ?
        """
        params = [parent_id]
    else:
        query = """
            SELECT g.*, t.name as event_template_name,
                   pg.group_name as parent_group_name
            FROM event_epg_groups g
            LEFT JOIN templates t ON g.event_template_id = t.id
            LEFT JOIN event_epg_groups pg ON g.parent_group_id = pg.id
            WHERE g.parent_group_id IS NOT NULL
        """
        params = []

    if enabled_only:
        query += " AND g.enabled = 1"
    query += " ORDER BY g.group_name"

    return db_fetch_all(query, tuple(params))


def validate_parent_child_relationship(child_group: Dict, parent_group: Dict) -> tuple:
    """
    Validate parent/child relationship constraints.

    Args:
        child_group: The group to become a child
        parent_group: The proposed parent group

    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    # Parent cannot be a child itself
    if parent_group.get('parent_group_id'):
        return False, "Cannot assign a child group as parent (no nesting)"

    # Same sport required
    if child_group.get('assigned_sport') != parent_group.get('assigned_sport'):
        return False, f"Child must have same sport as parent ({parent_group.get('assigned_sport')})"

    # Child cannot have existing channels
    existing_channels = get_managed_channels_for_group(child_group['id'])
    if existing_channels:
        return False, f"Group has {len(existing_channels)} existing channels. Cannot convert to child."

    return True, "Valid"


def get_potential_parents_for_sport(sport: str, exclude_group_id: int = None) -> List[Dict[str, Any]]:
    """
    Get groups that could serve as parents for a new child group with given sport.

    Args:
        sport: Sport type (e.g., 'football', 'basketball')
        exclude_group_id: Group ID to exclude (the group being edited)

    Returns:
        List of potential parent groups
    """
    query = """
        SELECT id, group_name, assigned_league, assigned_sport
        FROM event_epg_groups
        WHERE parent_group_id IS NULL
          AND assigned_sport = ?
          AND enabled = 1
    """
    params = [sport.lower()]

    if exclude_group_id:
        query += " AND id != ?"
        params.append(exclude_group_id)

    query += " ORDER BY group_name"
    return db_fetch_all(query, tuple(params))


# =============================================================================
# CONSOLIDATION EXCEPTION KEYWORDS (Global)
# =============================================================================

def get_consolidation_exception_keywords() -> List[Dict[str, Any]]:
    """
    Get all global exception keywords.

    Returns:
        List of dicts: [{'id': 1, 'keywords': 'Prime Vision, Primevision', 'behavior': 'separate'}, ...]
    """
    return db_fetch_all(
        "SELECT id, keywords, behavior FROM consolidation_exception_keywords ORDER BY id"
    )


def add_consolidation_exception_keyword(keywords: str, behavior: str = 'consolidate') -> int:
    """
    Add a new global exception keyword entry.

    Args:
        keywords: Comma-separated keyword variants
        behavior: 'consolidate', 'separate', or 'ignore'

    Returns:
        New entry ID
    """
    if behavior not in ('consolidate', 'separate', 'ignore'):
        raise ValueError(f"Invalid behavior: {behavior}")

    return db_insert(
        "INSERT INTO consolidation_exception_keywords (keywords, behavior) VALUES (?, ?)",
        (keywords.strip(), behavior)
    )


def update_consolidation_exception_keyword(keyword_id: int, keywords: str = None, behavior: str = None) -> bool:
    """
    Update an existing exception keyword entry.

    Args:
        keyword_id: ID of the keyword entry
        keywords: New keywords (optional)
        behavior: New behavior (optional)

    Returns:
        True if updated, False if not found
    """
    if behavior and behavior not in ('consolidate', 'separate', 'ignore'):
        raise ValueError(f"Invalid behavior: {behavior}")

    updates = []
    params = []
    if keywords is not None:
        updates.append("keywords = ?")
        params.append(keywords.strip())
    if behavior is not None:
        updates.append("behavior = ?")
        params.append(behavior)

    if not updates:
        return False

    params.append(keyword_id)

    with db_connection() as conn:
        cursor = conn.execute(
            f"UPDATE consolidation_exception_keywords SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_consolidation_exception_keyword(keyword_id: int) -> bool:
    """
    Delete an exception keyword entry.

    Args:
        keyword_id: ID of the keyword entry

    Returns:
        True if deleted, False if not found
    """
    with db_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM consolidation_exception_keywords WHERE id = ?",
            (keyword_id,)
        )
        conn.commit()
        return cursor.rowcount > 0


# =============================================================================
# EPG Failed Matches Functions
# =============================================================================

def clear_failed_matches(generation_id: int = None):
    """
    Clear failed matches, optionally for a specific generation.

    Args:
        generation_id: If provided, only clear for that generation.
                      If None, clear all failed matches.
    """
    with db_connection() as conn:
        if generation_id is not None:
            conn.execute(
                "DELETE FROM epg_failed_matches WHERE generation_id = ?",
                (generation_id,)
            )
        else:
            conn.execute("DELETE FROM epg_failed_matches")
        conn.commit()


def save_failed_match(
    generation_id: int,
    group_id: int,
    group_name: str,
    stream_name: str,
    reason: str,
    stream_id: int = None,
    parsed_team1: str = None,
    parsed_team2: str = None,
    detection_tier: str = None,
    leagues_checked: str = None,
    detail: str = None
):
    """
    Save a failed stream match for debugging.

    Args:
        generation_id: EPG generation counter
        group_id: Event group ID
        group_name: Event group name (for display)
        stream_name: Full stream name
        reason: Failure reason code
        stream_id: Dispatcharr stream ID (optional)
        parsed_team1: First parsed team name (optional)
        parsed_team2: Second parsed team name (optional)
        detection_tier: Tier reached before failure (optional)
        leagues_checked: Comma-separated leagues tried (optional)
        detail: Additional context (optional)
    """
    with db_connection() as conn:
        conn.execute("""
            INSERT INTO epg_failed_matches (
                generation_id, group_id, group_name, stream_id, stream_name,
                reason, parsed_team1, parsed_team2, detection_tier,
                leagues_checked, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            generation_id, group_id, group_name, stream_id, stream_name,
            reason, parsed_team1, parsed_team2, detection_tier,
            leagues_checked, detail
        ))
        conn.commit()


def save_failed_matches_batch(failures: list):
    """
    Save multiple failed matches in a single transaction.

    Args:
        failures: List of dicts with keys matching save_failed_match params
    """
    if not failures:
        return

    with db_connection() as conn:
        conn.executemany("""
            INSERT INTO epg_failed_matches (
                generation_id, group_id, group_name, stream_id, stream_name,
                reason, parsed_team1, parsed_team2, detection_tier,
                leagues_checked, detail
            ) VALUES (
                :generation_id, :group_id, :group_name, :stream_id, :stream_name,
                :reason, :parsed_team1, :parsed_team2, :detection_tier,
                :leagues_checked, :detail
            )
        """, failures)
        conn.commit()


def get_failed_matches(generation_id: int = None) -> List[Dict]:
    """
    Get failed matches, optionally for a specific generation.

    Args:
        generation_id: If provided, get for that generation.
                      If None, get for the most recent generation.

    Returns:
        List of failed match dicts
    """
    with db_connection() as conn:
        if generation_id is None:
            # Get most recent generation
            row = conn.execute(
                "SELECT MAX(generation_id) FROM epg_failed_matches"
            ).fetchone()
            if not row or row[0] is None:
                return []
            generation_id = row[0]

        cursor = conn.execute("""
            SELECT
                id, generation_id, group_id, group_name, stream_id, stream_name,
                reason, parsed_team1, parsed_team2, detection_tier,
                leagues_checked, detail, created_at
            FROM epg_failed_matches
            WHERE generation_id = ?
            ORDER BY group_name, stream_name
        """, (generation_id,))

        return [dict(row) for row in cursor.fetchall()]


def get_failed_matches_summary() -> Dict:
    """
    Get a summary of failed matches from the most recent generation.

    Returns:
        Dict with:
            - generation_id: int
            - total_count: int
            - by_group: Dict[group_name, count]
            - by_reason: Dict[reason, count]
            - timestamp: str (ISO format)
    """
    with db_connection() as conn:
        # Get most recent generation
        row = conn.execute(
            "SELECT MAX(generation_id) FROM epg_failed_matches"
        ).fetchone()
        if not row or row[0] is None:
            return {'generation_id': None, 'total_count': 0, 'by_group': {}, 'by_reason': {}}

        generation_id = row[0]

        # Get total count
        total = conn.execute(
            "SELECT COUNT(*) FROM epg_failed_matches WHERE generation_id = ?",
            (generation_id,)
        ).fetchone()[0]

        # Get counts by group
        by_group = {}
        for row in conn.execute("""
            SELECT group_name, COUNT(*) as cnt
            FROM epg_failed_matches
            WHERE generation_id = ?
            GROUP BY group_name
            ORDER BY cnt DESC
        """, (generation_id,)):
            by_group[row['group_name']] = row['cnt']

        # Get counts by reason
        by_reason = {}
        for row in conn.execute("""
            SELECT reason, COUNT(*) as cnt
            FROM epg_failed_matches
            WHERE generation_id = ?
            GROUP BY reason
            ORDER BY cnt DESC
        """, (generation_id,)):
            by_reason[row['reason']] = row['cnt']

        # Get timestamp
        timestamp_row = conn.execute(
            "SELECT MIN(created_at) FROM epg_failed_matches WHERE generation_id = ?",
            (generation_id,)
        ).fetchone()
        timestamp = timestamp_row[0] if timestamp_row else None

        return {
            'generation_id': generation_id,
            'total_count': total,
            'by_group': by_group,
            'by_reason': by_reason,
            'timestamp': timestamp
        }


# =============================================================================
# EPG Matched Streams Functions
# =============================================================================

def clear_matched_streams(generation_id: int = None):
    """
    Clear matched streams, optionally for a specific generation.

    Args:
        generation_id: If provided, only clear for that generation.
                      If None, clear all matched streams.
    """
    with db_connection() as conn:
        if generation_id is not None:
            conn.execute(
                "DELETE FROM epg_matched_streams WHERE generation_id = ?",
                (generation_id,)
            )
        else:
            conn.execute("DELETE FROM epg_matched_streams")
        conn.commit()


def save_matched_streams_batch(matches: list):
    """
    Save multiple matched streams in a single transaction.

    Args:
        matches: List of dicts with keys:
            - generation_id, group_id, group_name, stream_id, stream_name
            - event_id, event_name, detected_league, detection_tier
            - parsed_team1, parsed_team2, home_team, away_team, event_date
    """
    if not matches:
        return

    with db_connection() as conn:
        conn.executemany("""
            INSERT INTO epg_matched_streams (
                generation_id, group_id, group_name, stream_id, stream_name,
                event_id, event_name, detected_league, detection_tier,
                parsed_team1, parsed_team2, home_team, away_team, event_date
            ) VALUES (
                :generation_id, :group_id, :group_name, :stream_id, :stream_name,
                :event_id, :event_name, :detected_league, :detection_tier,
                :parsed_team1, :parsed_team2, :home_team, :away_team, :event_date
            )
        """, matches)
        conn.commit()


def get_matched_streams(generation_id: int = None) -> List[Dict]:
    """
    Get matched streams, optionally for a specific generation.

    Args:
        generation_id: If provided, get for that generation.
                      If None, get for the most recent generation.

    Returns:
        List of matched stream dicts
    """
    with db_connection() as conn:
        if generation_id is None:
            # Get most recent generation
            row = conn.execute(
                "SELECT MAX(generation_id) FROM epg_matched_streams"
            ).fetchone()
            if not row or row[0] is None:
                return []
            generation_id = row[0]

        cursor = conn.execute("""
            SELECT
                id, generation_id, group_id, group_name, stream_id, stream_name,
                event_id, event_name, detected_league, detection_tier,
                parsed_team1, parsed_team2, home_team, away_team, event_date,
                created_at
            FROM epg_matched_streams
            WHERE generation_id = ?
            ORDER BY group_name, event_date, stream_name
        """, (generation_id,))

        return [dict(row) for row in cursor.fetchall()]


def get_matched_streams_summary() -> Dict:
    """
    Get a summary of matched streams from the most recent generation.

    Returns:
        Dict with:
            - generation_id: int
            - total_count: int
            - by_group: Dict[group_name, count]
            - by_tier: Dict[tier, count]
            - by_league: Dict[league, count]
            - timestamp: str (ISO format)
    """
    with db_connection() as conn:
        # Get most recent generation
        row = conn.execute(
            "SELECT MAX(generation_id) FROM epg_matched_streams"
        ).fetchone()
        if not row or row[0] is None:
            return {'generation_id': None, 'total_count': 0, 'by_group': {}, 'by_tier': {}, 'by_league': {}}

        generation_id = row[0]

        # Get total count
        total = conn.execute(
            "SELECT COUNT(*) FROM epg_matched_streams WHERE generation_id = ?",
            (generation_id,)
        ).fetchone()[0]

        # Get counts by group
        by_group = {}
        for row in conn.execute("""
            SELECT group_name, COUNT(*) as cnt
            FROM epg_matched_streams
            WHERE generation_id = ?
            GROUP BY group_name
            ORDER BY cnt DESC
        """, (generation_id,)):
            by_group[row['group_name']] = row['cnt']

        # Get counts by tier
        by_tier = {}
        for row in conn.execute("""
            SELECT COALESCE(detection_tier, 'unknown') as tier, COUNT(*) as cnt
            FROM epg_matched_streams
            WHERE generation_id = ?
            GROUP BY tier
            ORDER BY cnt DESC
        """, (generation_id,)):
            by_tier[row['tier']] = row['cnt']

        # Get counts by league
        by_league = {}
        for row in conn.execute("""
            SELECT COALESCE(detected_league, 'unknown') as league, COUNT(*) as cnt
            FROM epg_matched_streams
            WHERE generation_id = ?
            GROUP BY league
            ORDER BY cnt DESC
        """, (generation_id,)):
            by_league[row['league']] = row['cnt']

        # Get timestamp
        timestamp_row = conn.execute(
            "SELECT MIN(created_at) FROM epg_matched_streams WHERE generation_id = ?",
            (generation_id,)
        ).fetchone()
        timestamp = timestamp_row[0] if timestamp_row else None

        return {
            'generation_id': generation_id,
            'total_count': total,
            'by_group': by_group,
            'by_tier': by_tier,
            'by_league': by_league,
            'timestamp': timestamp
        }
