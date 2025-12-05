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
# =============================================================================

CURRENT_SCHEMA_VERSION = 14


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
    """Parse JSON fields in an event EPG group dict."""
    if group and 'channel_profile_ids' in group and group['channel_profile_ids']:
        try:
            group['channel_profile_ids'] = json.loads(group['channel_profile_ids'])
        except (json.JSONDecodeError, TypeError):
            group['channel_profile_ids'] = []
    elif group:
        group['channel_profile_ids'] = []
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
    parent_group_id: int = None
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
    # Auto-assign channel_start if not provided
    if not channel_start:
        channel_start = get_next_available_channel_range()
        if channel_start:
            logger.info(f"Auto-assigned channel_start {channel_start} for new group '{group_name}'")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Convert channel_profile_ids list to JSON string
        channel_profile_ids_json = json.dumps(channel_profile_ids) if channel_profile_ids else None

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
             skip_builtin_filter, parent_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if skip_builtin_filter else 0, parent_group_id
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

        # Convert channel_profile_ids list to JSON string
        if 'channel_profile_ids' in data:
            if isinstance(data['channel_profile_ids'], list):
                data['channel_profile_ids'] = json.dumps(data['channel_profile_ids']) if data['channel_profile_ids'] else None
            # If it's already a string (JSON), leave it as is

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
    filtered_final: int = None
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
    query += " ORDER BY mc.event_epg_group_id, mc.channel_number"
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


def get_next_available_channel_range(dispatcharr_url: str = None, dispatcharr_username: str = None, dispatcharr_password: str = None) -> Optional[int]:
    """
    Calculate the next available channel range start (101, 201, 301, etc.).

    This is used as a fallback when a group doesn't have a channel_start set.
    Considers:
    1. All existing event groups' channel_start values
    2. All managed channels' actual channel numbers
    3. All channels in Dispatcharr (if credentials provided)

    Uses 100-channel intervals to maximize available ranges (Dispatcharr max is 9999).
    E.g., highest is 5135 -> next is 5201, highest is 777 -> next is 801.

    Returns:
        The next available x01 channel number, or None if no range available (would exceed 9999)
    """
    MAX_CHANNEL = 9999

    conn = get_connection()
    try:
        cursor = conn.cursor()
        highest_channel = 0

        # 1. Check event_epg_groups channel_start values
        rows = cursor.execute("""
            SELECT channel_start FROM event_epg_groups
            WHERE channel_start IS NOT NULL
        """).fetchall()

        for row in rows:
            if row['channel_start'] and row['channel_start'] > highest_channel:
                highest_channel = row['channel_start']

        # 2. Check managed_channels for actual channel numbers
        row = cursor.execute("""
            SELECT MAX(channel_number) as max_num FROM managed_channels
            WHERE deleted_at IS NULL
        """).fetchone()

        if row and row['max_num'] and row['max_num'] > highest_channel:
            highest_channel = row['max_num']

        # 3. If Dispatcharr credentials available, check all channels there
        if dispatcharr_url:
            try:
                from api.dispatcharr_client import ChannelManager
                channel_mgr = ChannelManager(dispatcharr_url, dispatcharr_username or '', dispatcharr_password or '')
                channels = channel_mgr.get_channels()
                for ch in channels:
                    ch_num = ch.get('channel_number')
                    if ch_num and ch_num > highest_channel:
                        highest_channel = ch_num
            except Exception as e:
                logger.debug(f"Could not query Dispatcharr channels: {e}")

        # Calculate next x01 after highest channel (100-channel intervals)
        # E.g., highest is 5135 -> 5201, highest is 777 -> 801
        if highest_channel == 0:
            return 101

        next_range = ((int(highest_channel) // 100) + 1) * 100 + 1

        # Check we don't exceed Dispatcharr's max channel limit
        if next_range > MAX_CHANNEL:
            logger.warning(f"Cannot auto-assign channel range: next would be {next_range}, max is {MAX_CHANNEL}")
            return None

        return int(next_range)

    finally:
        conn.close()


def get_next_channel_number(group_id: int, auto_assign: bool = True) -> Optional[int]:
    """
    Get the next available channel number for a group.

    Uses the group's channel_start and finds the next unused number.
    If the group has no channel_start and auto_assign is True, assigns
    the next available 100-channel range (x01) and saves it to the group.

    Args:
        group_id: The event group ID
        auto_assign: If True, auto-assign a channel_start when missing

    Returns:
        The next available channel number, or None if disabled or would exceed 9999
    """
    MAX_CHANNEL = 9999

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Get the group's channel_start
        group = cursor.execute(
            "SELECT channel_start FROM event_epg_groups WHERE id = ?",
            (group_id,)
        ).fetchone()

        if not group:
            return None

        channel_start = group['channel_start']

        # If no channel_start, auto-assign the next available range
        if not channel_start and auto_assign:
            channel_start = get_next_available_channel_range()
            # Save to the group
            cursor.execute(
                "UPDATE event_epg_groups SET channel_start = ? WHERE id = ?",
                (channel_start, group_id)
            )
            conn.commit()
            logger.info(f"Auto-assigned channel_start {channel_start} to group {group_id}")

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
            # Enforce Dispatcharr's max channel limit
            if next_num > MAX_CHANNEL:
                logger.warning(f"Cannot allocate channel for group {group_id}: would exceed {MAX_CHANNEL}")
                return None

        # Final check (in case channel_start itself exceeds limit)
        if next_num > MAX_CHANNEL:
            logger.warning(f"Cannot allocate channel for group {group_id}: {next_num} exceeds {MAX_CHANNEL}")
            return None

        return next_num
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
                status, error_message,
                num_channels, num_programmes, num_events,
                num_pregame, num_postgame, num_idle,
                team_based_channels, team_based_events,
                team_based_pregame, team_based_postgame, team_based_idle,
                event_based_channels, event_based_events,
                event_based_pregame, event_based_postgame,
                event_total_streams, event_filtered_no_indicator,
                event_filtered_include_regex, event_filtered_exclude_regex,
                event_filtered_outside_lookahead,
                event_filtered_final, event_eligible_streams, event_matched_streams,
                unresolved_vars_count, coverage_gaps_count, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stats.get('file_path', ''),
            stats.get('file_size', 0),
            stats.get('file_hash', ''),
            stats.get('generation_time_seconds', 0),
            stats.get('api_calls_made', 0),
            stats.get('status', 'success'),
            stats.get('error_message'),
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
