"""Database connection management.

Simple SQLite connection handling with schema initialization.
"""

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "teamarr.db"

# Schema file location
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a database connection.

    Args:
        db_path: Path to database file. Uses DEFAULT_DB_PATH if not specified.

    Returns:
        SQLite connection with row factory set to sqlite3.Row
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # timeout=30: Wait up to 30 seconds if database is locked by another connection
    # check_same_thread=False: Allow connection to be used across threads (required for FastAPI)
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Enable Write-Ahead Logging for better concurrent access
    # WAL allows readers to not block writers and vice versa
    conn.execute("PRAGMA journal_mode=WAL")

    # Wait up to 30 seconds if a table is locked (milliseconds)
    conn.execute("PRAGMA busy_timeout=30000")

    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


@contextmanager
def get_db(db_path: Path | str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections.

    Usage:
        with get_db() as conn:
            cursor = conn.execute("SELECT * FROM teams")
            teams = cursor.fetchall()
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    """Initialize database with schema.

    Creates tables if they don't exist. Safe to call multiple times.
    Also seeds TSDB cache from distributed seed file if needed.

    Args:
        db_path: Path to database file. Uses DEFAULT_DB_PATH if not specified.

    Raises:
        RuntimeError: If database file exists but is not a valid V2 database
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    schema_sql = SCHEMA_PATH.read_text()

    try:
        with get_db(db_path) as conn:
            # First, verify this is a valid V2-compatible database by checking integrity
            # and querying a core table. This catches both corruption AND V1 databases.
            _verify_database_integrity(conn, path)

            # Pre-migration: rename league_id_alias -> league_id before schema.sql runs
            # (schema.sql references league_id column in INSERT OR REPLACE)
            _rename_league_id_column_if_needed(conn)

            # Pre-migration: add league_alias column before schema.sql runs
            # (schema.sql INSERT OR REPLACE references league_alias column)
            _add_league_alias_column_if_needed(conn)

            # Pre-migration: add gracenote_category column before schema.sql runs
            # (schema.sql INSERT OR REPLACE references gracenote_category column)
            _add_gracenote_category_column_if_needed(conn)

            # Pre-migration: add logo_url_dark column before schema.sql runs
            # (schema.sql INSERT OR REPLACE references logo_url_dark column)
            _add_logo_url_dark_column_if_needed(conn)

            # Pre-migration: add series_slug_pattern column before schema.sql runs
            # (schema.sql UPDATE statements reference series_slug_pattern column)
            _add_series_slug_pattern_column_if_needed(conn)

            # Pre-migration: add fallback_provider and fallback_league_id columns
            # (schema.sql INSERT OR REPLACE references these columns)
            _add_fallback_columns_if_needed(conn)

            # Apply schema (creates tables if missing, INSERT OR REPLACE updates seed data)
            conn.executescript(schema_sql)
            # Run remaining migrations for existing databases
            _run_migrations(conn)
            # Seed TSDB cache if empty or incomplete
            _seed_tsdb_cache_if_needed(conn)

            # Final verification: ensure settings table exists and is queryable
            conn.execute("SELECT id FROM settings LIMIT 1")
    except sqlite3.DatabaseError as e:
        if "file is not a database" in str(e):
            logger.error(
                f"Database file '{path}' exists but is not compatible with Teamarr V2. "
                "This usually means you're trying to use a V1 database. "
                "V2 requires a fresh database - please either:\n"
                "  1. Use a different data directory for V2, or\n"
                "  2. Backup and delete the existing database file"
            )
            raise RuntimeError(
                f"Incompatible database file at '{path}'. "
                "V2 is not compatible with V1 databases. "
                "Please use a fresh data directory or delete the existing database."
            ) from e
        raise


def _verify_database_integrity(conn: sqlite3.Connection, path: Path) -> None:
    """Verify database is valid and compatible with V2.

    This runs BEFORE schema initialization to catch:
    1. Corrupt database files ("file is not a database")
    2. V1 databases (different schema, incompatible)

    Args:
        conn: Database connection
        path: Path to database file for error messages

    Raises:
        RuntimeError: If database is a V1 database
        sqlite3.DatabaseError: If database file is corrupt
    """
    # Force an actual read from the file to detect corruption early
    # PRAGMA integrity_check would be thorough but slow; just query sqlite_master
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 100")
        existing_tables = {row["name"] for row in cursor.fetchall()}
    except sqlite3.DatabaseError:
        # Let the outer handler deal with "file is not a database" errors
        raise

    # Check for V1-specific tables that indicate an incompatible database
    # These tables exist only in V1 and NOT in V2
    v1_indicators = {
        "schedule_cache",  # V1 caching
        "league_config",  # V1 league configuration
        "h2h_cache",  # V1 head-to-head (removed in V2)
        "error_log",  # V1 error logging
        "soccer_cache_meta",  # V1 soccer-specific cache
        "team_stats_cache",  # V1 stats cache
    }
    v1_tables_found = v1_indicators & existing_tables

    if v1_tables_found:
        logger.error(
            f"Database file '{path}' appears to be a V1 database. "
            f"Found V1-specific tables: {v1_tables_found}. "
            "V2 requires a fresh database - please either:\n"
            "  1. Use a different data directory for V2, or\n"
            "  2. Backup and delete the existing database file"
        )
        raise RuntimeError(
            f"V1 database detected at '{path}'. "
            f"Found V1 tables: {v1_tables_found}. "
            "V2 is not compatible with V1 databases. "
            "Please use a fresh data directory or delete the existing database."
        )


def _rename_league_id_column_if_needed(conn: sqlite3.Connection) -> None:
    """Rename league_id_alias -> league_id if needed.

    This MUST run before schema.sql because schema.sql INSERT OR REPLACE
    statements reference the new column name.
    """
    # Check if leagues table exists
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'")
    if not cursor.fetchone():
        return  # Fresh database, schema.sql will create table with correct column

    # Check if old column exists
    cursor = conn.execute("PRAGMA table_info(leagues)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "league_id_alias" in columns and "league_id" not in columns:
        conn.execute("ALTER TABLE leagues RENAME COLUMN league_id_alias TO league_id")
        logger.info("Renamed leagues.league_id_alias -> league_id")


def _add_league_alias_column_if_needed(conn: sqlite3.Connection) -> None:
    """Add league_alias column if it doesn't exist.

    This MUST run before schema.sql because schema.sql INSERT OR REPLACE
    statements reference the league_alias column.
    """
    # Check if leagues table exists
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'")
    if not cursor.fetchone():
        return  # Fresh database, schema.sql will create table with correct column

    # Check if column exists
    cursor = conn.execute("PRAGMA table_info(leagues)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "league_alias" not in columns:
        conn.execute("ALTER TABLE leagues ADD COLUMN league_alias TEXT")
        logger.info("Added leagues.league_alias column")


def _add_gracenote_category_column_if_needed(conn: sqlite3.Connection) -> None:
    """Add gracenote_category column if it doesn't exist.

    This MUST run before schema.sql because schema.sql INSERT OR REPLACE
    statements reference the gracenote_category column.
    """
    # Check if leagues table exists
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'")
    if not cursor.fetchone():
        return  # Fresh database, schema.sql will create table with correct column

    # Check if column exists
    cursor = conn.execute("PRAGMA table_info(leagues)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "gracenote_category" not in columns:
        conn.execute("ALTER TABLE leagues ADD COLUMN gracenote_category TEXT")
        logger.info("Added leagues.gracenote_category column")


def _add_logo_url_dark_column_if_needed(conn: sqlite3.Connection) -> None:
    """Add logo_url_dark column if it doesn't exist.

    This MUST run before schema.sql because schema.sql INSERT OR REPLACE
    statements reference the logo_url_dark column.
    """
    # Check if leagues table exists
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'")
    if not cursor.fetchone():
        return  # Fresh database, schema.sql will create table with correct column

    # Check if column exists
    cursor = conn.execute("PRAGMA table_info(leagues)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "logo_url_dark" not in columns:
        conn.execute("ALTER TABLE leagues ADD COLUMN logo_url_dark TEXT")
        logger.info("Added leagues.logo_url_dark column")


def _add_series_slug_pattern_column_if_needed(conn: sqlite3.Connection) -> None:
    """Add series_slug_pattern column if it doesn't exist.

    This is needed for Cricbuzz auto-discovery of current season series IDs.
    MUST run before schema.sql because UPDATE statements reference this column.
    """
    # Check if leagues table exists
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'")
    if not cursor.fetchone():
        return  # Fresh database, schema.sql will create table with correct column

    # Check if column exists
    cursor = conn.execute("PRAGMA table_info(leagues)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "series_slug_pattern" not in columns:
        conn.execute("ALTER TABLE leagues ADD COLUMN series_slug_pattern TEXT")
        logger.info("Added leagues.series_slug_pattern column")


def _add_fallback_columns_if_needed(conn: sqlite3.Connection) -> None:
    """Add fallback_provider and fallback_league_id columns if they don't exist.

    These columns enable provider fallback for leagues where the primary provider
    may have limited availability (e.g., TSDB premium vs free tier for cricket).
    MUST run before schema.sql because INSERT OR REPLACE references these columns.
    """
    # Check if leagues table exists
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leagues'")
    if not cursor.fetchone():
        return  # Fresh database, schema.sql will create table with correct columns

    # Check which columns exist
    cursor = conn.execute("PRAGMA table_info(leagues)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "fallback_provider" not in columns:
        conn.execute("ALTER TABLE leagues ADD COLUMN fallback_provider TEXT")
        logger.info("Added leagues.fallback_provider column")

    if "fallback_league_id" not in columns:
        conn.execute("ALTER TABLE leagues ADD COLUMN fallback_league_id TEXT")
        logger.info("Added leagues.fallback_league_id column")


def _seed_tsdb_cache_if_needed(conn: sqlite3.Connection) -> None:
    """Seed TSDB cache from distributed seed file if needed."""
    from teamarr.database.seed import seed_if_needed

    result = seed_if_needed(conn)
    if result and result.get("seeded"):
        logger.info(
            f"Seeded TSDB cache: {result.get('teams_added', 0)} teams, "
            f"{result.get('leagues_added', 0)} leagues"
        )


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run database migrations for existing databases.

    Uses schema_version in settings table to track applied migrations.
    Safe to call multiple times - checks version before running.

    Schema versions:
    - 2: Initial V2 schema
    - 3: Teams consolidated (league -> primary_league + leagues array)
    - 4: Added eng.2 (Championship), eng.3 (League One), nrl leagues; fixed NRL logo
    - 5: Renamed league_id_alias -> league_id
    - 6: Added league_alias column, fixed managed_channels UNIQUE constraint
    - 7: Added gracenote_category column
    - 8: Added custom_regex_date/time columns to event_epg_groups
    - 9: Added keyword_ordering to change_source CHECK constraint
    - 10: Updated channel timing CHECK constraints
    - 11: Removed UNIQUE constraint from tvg_id
    - 12: Removed per-group timing settings
    - 13: Added display_name to event_epg_groups
    - 14: Added streams_excluded to event_epg_groups
    - 15: Renamed filtered_no_match -> failed_count (clearer stat categories)
    - 16-22: Various additions (see individual migrations)
    - 23: Added default_channel_profile_ids to settings
    """
    # Get current schema version
    try:
        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        current_version = row["schema_version"] if row else 2
    except Exception:
        current_version = 2

    # Migration: Add description_template to templates table (pre-versioning)
    _add_column_if_not_exists(
        conn, "templates", "description_template", "TEXT DEFAULT '{matchup} | {venue_full}'"
    )

    # Migration: Add tsdb_api_key to settings table (pre-versioning)
    _add_column_if_not_exists(conn, "settings", "tsdb_api_key", "TEXT")

    # Migration: Add origin_match_method to epg_matched_streams (for cache hit origin tracking)
    _add_column_if_not_exists(conn, "epg_matched_streams", "origin_match_method", "TEXT")

    # Version 3: teams.league (TEXT) -> teams.primary_league + teams.leagues (JSON array)
    if current_version < 3:
        if _migrate_teams_to_leagues_array(conn):
            conn.execute("UPDATE settings SET schema_version = 3 WHERE id = 1")
            logger.info("Schema upgraded to version 3")
            current_version = 3

    # Version 4: Add new leagues (eng.2, eng.3, nrl) and fix NRL logo
    if current_version < 4:
        # Add EFL Championship (eng.2)
        conn.execute("""
            INSERT OR IGNORE INTO leagues
            (league_code, provider, provider_league_id, provider_league_name,
             display_name, sport, logo_url, import_enabled, league_id)
            VALUES ('eng.2', 'espn', 'soccer/eng.2', NULL, 'EFL Championship',
                    'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/24.png',
                    1, 'championship')
        """)

        # Add EFL League One (eng.3)
        conn.execute("""
            INSERT OR IGNORE INTO leagues
            (league_code, provider, provider_league_id, provider_league_name,
             display_name, sport, logo_url, import_enabled, league_id)
            VALUES ('eng.3', 'espn', 'soccer/eng.3', NULL, 'EFL League One',
                    'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/25.png',
                    1, 'league-one')
        """)

        # Add NRL (National Rugby League)
        conn.execute("""
            INSERT OR IGNORE INTO leagues
            (league_code, provider, provider_league_id, provider_league_name,
             display_name, sport, logo_url, import_enabled, league_id)
            VALUES ('nrl', 'tsdb', '4416', 'Australian National Rugby League',
                    'National Rugby League', 'Rugby',
                    'https://r2.thesportsdb.com/images/media/league/badge/gsztcj1552071996.png',
                    1, 'nrl')
        """)

        # Fix NRL logo URL if it was set to the old (404) URL
        conn.execute("""
            UPDATE leagues
            SET logo_url = 'https://r2.thesportsdb.com/images/media/league/badge/gsztcj1552071996.png'
            WHERE league_code = 'nrl'
              AND logo_url = 'https://r2.thesportsdb.com/images/media/league/badge/89o6hc1596121022.png'
        """)
        conn.execute("""
            UPDATE league_cache
            SET logo_url = 'https://r2.thesportsdb.com/images/media/league/badge/gsztcj1552071996.png'
            WHERE league_slug = 'nrl'
              AND logo_url = 'https://r2.thesportsdb.com/images/media/league/badge/89o6hc1596121022.png'
        """)

        conn.execute("UPDATE settings SET schema_version = 4 WHERE id = 1")
        logger.info("Schema upgraded to version 4 (added eng.2, eng.3, nrl leagues)")
        current_version = 4

    # Version 5: league_id_alias -> league_id (rename done in pre-migration)
    if current_version < 5:
        conn.execute("UPDATE settings SET schema_version = 5 WHERE id = 1")
        logger.info("Schema upgraded to version 5")
        current_version = 5

    # Version 6: Add league_alias column + fix managed_channels UNIQUE constraint
    if current_version < 6:
        _add_column_if_not_exists(conn, "leagues", "league_alias", "TEXT")

        # Remove table-level UNIQUE constraint from managed_channels
        # This allows soft-deleted rows to coexist with new rows for same event
        # The partial unique index (idx_mc_unique_event) handles uniqueness for active rows
        _recreate_managed_channels_without_unique_constraint(conn)

        conn.execute("UPDATE settings SET schema_version = 6 WHERE id = 1")
        logger.info("Schema upgraded to version 6 (league_alias, managed_channels fix)")
        current_version = 6

    # Version 7: gracenote_category column (handled in pre-migration)
    if current_version < 7:
        # Column is added by _add_gracenote_category_column_if_needed before schema.sql
        conn.execute("UPDATE settings SET schema_version = 7 WHERE id = 1")
        logger.info("Schema upgraded to version 7 (gracenote_category)")
        current_version = 7

    # Version 8: Add custom_regex_date/time columns to event_epg_groups
    if current_version < 8:
        _add_column_if_not_exists(conn, "event_epg_groups", "custom_regex_date", "TEXT")
        _add_column_if_not_exists(
            conn, "event_epg_groups", "custom_regex_date_enabled", "BOOLEAN DEFAULT 0"
        )
        _add_column_if_not_exists(conn, "event_epg_groups", "custom_regex_time", "TEXT")
        _add_column_if_not_exists(
            conn, "event_epg_groups", "custom_regex_time_enabled", "BOOLEAN DEFAULT 0"
        )
        conn.execute("UPDATE settings SET schema_version = 8 WHERE id = 1")
        logger.info("Schema upgraded to version 8 (custom_regex_date/time)")
        current_version = 8

    # Version 9: Add 'keyword_ordering' to change_source CHECK constraint
    # and 'number_swapped' to change_type
    if current_version < 9:
        # SQLite can't alter CHECK constraints, so we recreate the table
        # IMPORTANT: Column order must match the original table exactly
        conn.executescript("""
            -- Create temp table with updated constraints (same column order as original)
            CREATE TABLE managed_channel_history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                managed_channel_id INTEGER NOT NULL,
                change_type TEXT NOT NULL
                    CHECK(change_type IN ('created', 'modified', 'deleted', 'stream_added', 'stream_removed', 'verified', 'synced', 'error', 'number_swapped')),
                change_source TEXT
                    CHECK(change_source IN ('epg_generation', 'reconciliation', 'api', 'scheduler', 'manual', 'external_sync', 'lifecycle', 'cross_group_enforcement', 'keyword_enforcement', 'keyword_ordering')),
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE
            );

            -- Copy existing data
            INSERT INTO managed_channel_history_new
            SELECT * FROM managed_channel_history;

            -- Swap tables
            DROP TABLE managed_channel_history;
            ALTER TABLE managed_channel_history_new RENAME TO managed_channel_history;

            -- Recreate indexes
            CREATE INDEX IF NOT EXISTS idx_mch_channel
            ON managed_channel_history(managed_channel_id, changed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_mch_type
            ON managed_channel_history(change_type);
        """)
        conn.execute("UPDATE settings SET schema_version = 9 WHERE id = 1")
        logger.info("Schema upgraded to version 9 (keyword_ordering change_source)")
        current_version = 9

    # Version 10: Update channel timing CHECK constraints
    # - Remove 'manual' from create/delete timing
    # - Add '6_hours_after' to delete timing
    if current_version < 10:
        _update_channel_timing_constraints(conn)
        conn.execute("UPDATE settings SET schema_version = 10 WHERE id = 1")
        logger.info("Schema upgraded to version 10 (channel timing constraints)")
        current_version = 10

    # Version 11: Remove UNIQUE constraint from tvg_id
    # V1 parity: soft-deleted records can coexist with active records having same tvg_id
    if current_version < 11:
        _remove_tvg_id_unique_constraint(conn)
        conn.execute("UPDATE settings SET schema_version = 11 WHERE id = 1")
        logger.info("Schema upgraded to version 11 (removed tvg_id UNIQUE constraint)")
        current_version = 11

    # Version 12: Remove per-group timing settings
    # All groups now use global settings from Settings table
    if current_version < 12:
        _remove_group_timing_columns(conn)
        conn.execute("UPDATE settings SET schema_version = 12 WHERE id = 1")
        logger.info("Schema upgraded to version 12 (removed per-group timing settings)")
        current_version = 12

    # Version 13: Add display_name column to event_epg_groups
    # Optional display name override for UI (prefers this over M3U group name)
    if current_version < 13:
        _add_column_if_not_exists(conn, "event_epg_groups", "display_name", "TEXT")
        conn.execute("UPDATE settings SET schema_version = 13 WHERE id = 1")
        logger.info("Schema upgraded to version 13 (event_epg_groups.display_name)")
        current_version = 13

    # Version 14: Add streams_excluded column to event_epg_groups
    # Tracks matched-but-excluded streams (past/final/before-create-window)
    if current_version < 14:
        _add_column_if_not_exists(
            conn, "event_epg_groups", "streams_excluded", "INTEGER DEFAULT 0"
        )
        conn.execute("UPDATE settings SET schema_version = 14 WHERE id = 1")
        logger.info("Schema upgraded to version 14 (event_epg_groups.streams_excluded)")
        current_version = 14

    # Version 15: Rename filtered_no_match -> failed_count for clearer naming
    # Reflects that this is FAILED category (match attempted but couldn't find event)
    if current_version < 15:
        _rename_filtered_no_match_to_failed_count(conn)
        conn.execute("UPDATE settings SET schema_version = 15 WHERE id = 1")
        logger.info("Schema upgraded to version 15 (filtered_no_match -> failed_count)")
        current_version = 15

    # Version 16: Add excluded reason breakdown columns
    # Tracks individual exclusion reasons for UI breakdown display
    if current_version < 16:
        _add_column_if_not_exists(
            conn, "event_epg_groups", "excluded_event_final", "INTEGER DEFAULT 0"
        )
        _add_column_if_not_exists(
            conn, "event_epg_groups", "excluded_event_past", "INTEGER DEFAULT 0"
        )
        _add_column_if_not_exists(
            conn, "event_epg_groups", "excluded_before_window", "INTEGER DEFAULT 0"
        )
        _add_column_if_not_exists(
            conn, "event_epg_groups", "excluded_league_not_included", "INTEGER DEFAULT 0"
        )
        conn.execute("UPDATE settings SET schema_version = 16 WHERE id = 1")
        logger.info("Schema upgraded to version 16 (excluded breakdown columns)")
        current_version = 16

    # Version 17: Add event_match_days_back to settings
    # Allows looking further back for weekly sports like NFL (default 7 days)
    if current_version < 17:
        _add_column_if_not_exists(
            conn, "settings", "event_match_days_back", "INTEGER DEFAULT 7"
        )
        conn.execute("UPDATE settings SET schema_version = 17 WHERE id = 1")
        logger.info("Schema upgraded to version 17 (settings.event_match_days_back)")
        current_version = 17

    # Version 18: Add duration_volleyball to settings
    # Volleyball game duration setting (default 2.5 hours)
    if current_version < 18:
        _add_column_if_not_exists(
            conn, "settings", "duration_volleyball", "REAL DEFAULT 2.5"
        )
        conn.execute("UPDATE settings SET schema_version = 18 WHERE id = 1")
        logger.info("Schema upgraded to version 18 (settings.duration_volleyball)")
        current_version = 18

    # Version 19: Add xmltv_video to templates
    # Video quality metadata for XMLTV output (HD/SD/aspect ratio)
    if current_version < 19:
        _add_column_if_not_exists(
            conn,
            "templates",
            "xmltv_video",
            """JSON DEFAULT '{"enabled": false, "quality": "HDTV"}'""",
        )
        conn.execute("UPDATE settings SET schema_version = 19 WHERE id = 1")
        logger.info("Schema upgraded to version 19 (templates.xmltv_video)")
        current_version = 19

    # Version 20: Add group_mode to event_epg_groups
    # Preserves whether group was created as 'single' or 'multi' league
    if current_version < 20:
        _add_column_if_not_exists(
            conn,
            "event_epg_groups",
            "group_mode",
            "TEXT DEFAULT 'single' CHECK(group_mode IN ('single', 'multi'))",
        )
        # Migrate existing groups: set mode based on current league count
        conn.execute("""
            UPDATE event_epg_groups
            SET group_mode = CASE
                WHEN json_array_length(leagues) > 1 THEN 'multi'
                ELSE 'single'
            END
            WHERE group_mode IS NULL OR group_mode = 'single'
        """)
        conn.execute("UPDATE settings SET schema_version = 20 WHERE id = 1")
        logger.info("Schema upgraded to version 20 (event_epg_groups.group_mode)")
        current_version = 20

    # Version 21: Add team filtering columns to event_epg_groups
    # Canonical team selection (not regex) for filtering events by team
    if current_version < 21:
        _add_column_if_not_exists(conn, "event_epg_groups", "include_teams", "JSON")
        _add_column_if_not_exists(conn, "event_epg_groups", "exclude_teams", "JSON")
        _add_column_if_not_exists(
            conn,
            "event_epg_groups",
            "team_filter_mode",
            "TEXT DEFAULT 'include' CHECK(team_filter_mode IN ('include', 'exclude'))",
        )
        _add_column_if_not_exists(
            conn, "event_epg_groups", "filtered_team", "INTEGER DEFAULT 0"
        )
        conn.execute("UPDATE settings SET schema_version = 21 WHERE id = 1")
        logger.info("Schema upgraded to version 21 (team filtering columns)")
        current_version = 21

    # Version 22: Add default team filter columns to settings
    # Global default team filter applied to groups without their own filter
    if current_version < 22:
        _add_column_if_not_exists(conn, "settings", "default_include_teams", "JSON")
        _add_column_if_not_exists(conn, "settings", "default_exclude_teams", "JSON")
        _add_column_if_not_exists(
            conn,
            "settings",
            "default_team_filter_mode",
            "TEXT DEFAULT 'include'",
        )
        conn.execute("UPDATE settings SET schema_version = 22 WHERE id = 1")
        logger.info("Schema upgraded to version 22 (default team filter settings)")
        current_version = 22

    # Version 23: Add default_channel_profile_ids to settings
    # Default Dispatcharr channel profiles for new event channels
    if current_version < 23:
        _add_column_if_not_exists(
            conn, "settings", "default_channel_profile_ids", "JSON"
        )
        conn.execute("UPDATE settings SET schema_version = 23 WHERE id = 1")
        logger.info("Schema upgraded to version 23 (default_channel_profile_ids)")
        current_version = 23


def _rename_filtered_no_match_to_failed_count(conn: sqlite3.Connection) -> None:
    """Rename filtered_no_match column to failed_count.

    This clarifies the stat tracking categories:
    - FILTERED: Pre-match filtering (regex, not_event)
    - FAILED: Match attempted but couldn't find event (this column)
    - EXCLUDED: Matched but excluded (timing/config)
    """
    # Check if the old column exists
    cursor = conn.execute("PRAGMA table_info(event_epg_groups)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "filtered_no_match" not in columns:
        # Already renamed or fresh database
        if "failed_count" not in columns:
            # Add the column if missing entirely (shouldn't happen with schema.sql)
            conn.execute(
                "ALTER TABLE event_epg_groups ADD COLUMN failed_count INTEGER DEFAULT 0"
            )
            logger.info("Added event_epg_groups.failed_count column")
        return

    # SQLite doesn't support RENAME COLUMN in older versions, so we use the full approach
    # First add new column, copy data, then we could drop old column but SQLite doesn't support DROP COLUMN
    # So we recreate the table or just leave both (simpler approach: just add column and copy)
    if "failed_count" not in columns:
        conn.execute(
            "ALTER TABLE event_epg_groups ADD COLUMN failed_count INTEGER DEFAULT 0"
        )

    # Copy data from old column to new
    conn.execute(
        "UPDATE event_epg_groups SET failed_count = filtered_no_match WHERE failed_count = 0"
    )
    logger.info("Migrated filtered_no_match -> failed_count")


def _remove_tvg_id_unique_constraint(conn: sqlite3.Connection) -> None:
    """Remove UNIQUE constraint from tvg_id column.

    SQLite requires table recreation to remove inline UNIQUE constraints.
    This allows soft-deleted records to coexist with new active records
    having the same tvg_id (V1 parity).
    """
    # Check if unique constraint exists on tvg_id
    cursor = conn.execute("""
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'managed_channels'
    """)
    row = cursor.fetchone()
    if not row or "tvg_id TEXT NOT NULL UNIQUE" not in (row[0] or ""):
        logger.debug("tvg_id UNIQUE constraint not present, skipping migration")
        return

    logger.info("Removing UNIQUE constraint from managed_channels.tvg_id")

    # Disable foreign keys for table recreation
    conn.execute("PRAGMA foreign_keys = OFF")

    try:
        # Create new table without UNIQUE on tvg_id
        conn.execute("""
            CREATE TABLE managed_channels_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                event_epg_group_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                event_provider TEXT NOT NULL,
                tvg_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                channel_number TEXT,
                logo_url TEXT,
                dispatcharr_channel_id INTEGER,
                dispatcharr_uuid TEXT,
                dispatcharr_logo_id INTEGER,
                channel_group_id INTEGER,
                stream_profile_id INTEGER,
                channel_profile_ids TEXT,
                primary_stream_id INTEGER,
                exception_keyword TEXT,
                home_team TEXT,
                home_team_abbrev TEXT,
                home_team_logo TEXT,
                away_team TEXT,
                away_team_abbrev TEXT,
                away_team_logo TEXT,
                event_date TIMESTAMP,
                event_name TEXT,
                league TEXT,
                sport TEXT,
                venue TEXT,
                broadcast TEXT,
                scheduled_delete_at TIMESTAMP,
                deleted_at TIMESTAMP,
                delete_reason TEXT,
                sync_status TEXT DEFAULT 'pending' CHECK(sync_status IN (
                    'pending', 'created', 'in_sync', 'drifted', 'orphaned', 'error')),
                sync_message TEXT,
                last_verified_at TIMESTAMP,
                expires_at TIMESTAMP,
                external_channel_id INTEGER,
                FOREIGN KEY (event_epg_group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
            )
        """)

        # Copy data
        conn.execute("""
            INSERT INTO managed_channels_new
            SELECT * FROM managed_channels
        """)

        # Drop old table and rename
        conn.execute("DROP TABLE managed_channels")
        conn.execute("ALTER TABLE managed_channels_new RENAME TO managed_channels")

        # Recreate indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_group
            ON managed_channels(event_epg_group_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_event
            ON managed_channels(event_id, event_provider)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_expires
            ON managed_channels(expires_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_delete
            ON managed_channels(scheduled_delete_at) WHERE deleted_at IS NULL
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_dispatcharr
            ON managed_channels(dispatcharr_channel_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_tvg
            ON managed_channels(tvg_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_managed_channels_sync
            ON managed_channels(sync_status)
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_unique_event
            ON managed_channels(
                event_epg_group_id, event_id, event_provider,
                COALESCE(exception_keyword, '')
            ) WHERE deleted_at IS NULL
        """)

        # Recreate trigger
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS update_managed_channels_timestamp
            AFTER UPDATE ON managed_channels
            BEGIN
                UPDATE managed_channels SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        conn.commit()
        logger.info("Successfully removed UNIQUE constraint from tvg_id")

    finally:
        # Re-enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_teams_to_leagues_array(conn: sqlite3.Connection) -> bool:
    """Migrate teams table from single league to leagues JSON array.

    Consolidates teams by (provider, provider_team_id, sport) with all
    their leagues merged into a JSON array.

    Returns:
        True if migration was performed, False if already migrated
    """
    import json

    # Check if table has old 'league' column
    cursor = conn.execute("PRAGMA table_info(teams)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "league" not in columns or "leagues" in columns:
        # Already migrated or fresh database
        return False

    logger.info("Migrating teams table: league -> leagues array")

    # Get all existing teams grouped by (provider, provider_team_id, sport)
    cursor = conn.execute("""
        SELECT provider, provider_team_id, sport,
               GROUP_CONCAT(league) as leagues_concat,
               team_name, team_abbrev, team_logo_url, team_color,
               channel_id, channel_logo_url, template_id, active,
               MIN(created_at) as created_at,
               MAX(updated_at) as updated_at
        FROM teams
        GROUP BY provider, provider_team_id, sport
    """)
    rows = cursor.fetchall()

    # Create new table with leagues array
    conn.execute("""
        CREATE TABLE teams_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provider TEXT NOT NULL DEFAULT 'espn',
            provider_team_id TEXT NOT NULL,
            primary_league TEXT NOT NULL,
            leagues TEXT NOT NULL DEFAULT '[]',
            sport TEXT NOT NULL,
            team_name TEXT NOT NULL,
            team_abbrev TEXT,
            team_logo_url TEXT,
            team_color TEXT,
            channel_id TEXT NOT NULL UNIQUE,
            channel_logo_url TEXT,
            template_id INTEGER,
            active BOOLEAN DEFAULT 1,
            UNIQUE(provider, provider_team_id, sport),
            FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
        )
    """)

    # Insert consolidated rows
    for row in rows:
        # Convert comma-separated leagues to JSON array
        leagues_list = list(set(row["leagues_concat"].split(","))) if row["leagues_concat"] else []
        leagues_sorted = sorted(leagues_list)
        leagues_json = json.dumps(leagues_sorted)
        # Use first league as primary (will be updated by API if needed)
        primary_league = leagues_sorted[0] if leagues_sorted else ""

        conn.execute(
            """
            INSERT INTO teams_new (
                provider, provider_team_id, primary_league, leagues, sport,
                team_name, team_abbrev, team_logo_url, team_color,
                channel_id, channel_logo_url, template_id, active,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["provider"],
                row["provider_team_id"],
                primary_league,
                leagues_json,
                row["sport"],
                row["team_name"],
                row["team_abbrev"],
                row["team_logo_url"],
                row["team_color"],
                row["channel_id"],
                row["channel_logo_url"],
                row["template_id"],
                row["active"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    # Drop old table and rename new one
    conn.execute("DROP TABLE teams")
    conn.execute("ALTER TABLE teams_new RENAME TO teams")

    # Recreate indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_channel_id ON teams(channel_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_active ON teams(active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_provider ON teams(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_sport ON teams(sport)")

    # Recreate trigger
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS update_teams_timestamp
        AFTER UPDATE ON teams
        BEGIN
            UPDATE teams SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END
    """)

    logger.info(f"Migrated {len(rows)} teams with leagues arrays")
    return True


def _add_column_if_not_exists(
    conn: sqlite3.Connection, table: str, column: str, column_def: str
) -> None:
    """Add a column to a table if it doesn't exist.

    Args:
        conn: Database connection
        table: Table name
        column: Column name to add
        column_def: Column definition (type and default)
    """
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = {row["name"] for row in cursor.fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")


def _recreate_managed_channels_without_unique_constraint(
    conn: sqlite3.Connection,
) -> None:
    """Recreate managed_channels table without table-level UNIQUE constraint.

    The table had UNIQUE(event_epg_group_id, event_id, event_provider) which
    prevented creating new channels when soft-deleted ones existed.

    The partial unique index (idx_mc_unique_event) handles uniqueness for
    active (non-deleted) rows only.
    """
    # Check if table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='managed_channels'"
    )
    if not cursor.fetchone():
        return

    # Check if the problematic constraint exists
    cursor = conn.execute("PRAGMA index_list(managed_channels)")
    indices = {row[1] for row in cursor.fetchall()}
    if "sqlite_autoindex_managed_channels_2" not in indices:
        # Constraint already removed
        return

    logger.info("Recreating managed_channels table to remove UNIQUE constraint...")

    conn.executescript("""
        PRAGMA foreign_keys = OFF;

        CREATE TABLE managed_channels_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_epg_group_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            event_provider TEXT NOT NULL DEFAULT 'espn',
            tvg_id TEXT,
            channel_name TEXT NOT NULL,
            channel_number INTEGER,
            logo_url TEXT,
            dispatcharr_channel_id INTEGER,
            dispatcharr_uuid TEXT,
            dispatcharr_logo_id INTEGER,
            channel_group_id INTEGER,
            stream_profile_id INTEGER,
            channel_profile_ids TEXT,
            primary_stream_id INTEGER,
            exception_keyword TEXT,
            home_team TEXT,
            home_team_abbrev TEXT,
            home_team_logo TEXT,
            away_team TEXT,
            away_team_abbrev TEXT,
            away_team_logo TEXT,
            event_date TIMESTAMP,
            event_name TEXT,
            league TEXT,
            sport TEXT,
            venue TEXT,
            broadcast TEXT,
            scheduled_delete_at TIMESTAMP,
            deleted_at TIMESTAMP,
            delete_reason TEXT,
            sync_status TEXT DEFAULT 'pending',
            sync_message TEXT,
            last_verified_at TIMESTAMP,
            expires_at TIMESTAMP,
            external_channel_id INTEGER,
            FOREIGN KEY (event_epg_group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
        );

        INSERT INTO managed_channels_new SELECT * FROM managed_channels;
        DROP TABLE managed_channels;
        ALTER TABLE managed_channels_new RENAME TO managed_channels;

        CREATE INDEX IF NOT EXISTS idx_managed_channels_group ON managed_channels(event_epg_group_id);
        CREATE INDEX IF NOT EXISTS idx_managed_channels_event ON managed_channels(event_id, event_provider);
        CREATE INDEX IF NOT EXISTS idx_managed_channels_expires ON managed_channels(expires_at);
        CREATE INDEX IF NOT EXISTS idx_managed_channels_delete ON managed_channels(scheduled_delete_at) WHERE deleted_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_managed_channels_dispatcharr ON managed_channels(dispatcharr_channel_id);
        CREATE INDEX IF NOT EXISTS idx_managed_channels_tvg ON managed_channels(tvg_id);
        CREATE INDEX IF NOT EXISTS idx_managed_channels_sync ON managed_channels(sync_status);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_unique_event
            ON managed_channels(event_epg_group_id, event_id, event_provider, COALESCE(exception_keyword, ''))
            WHERE deleted_at IS NULL;

        CREATE TRIGGER IF NOT EXISTS update_managed_channels_timestamp
        AFTER UPDATE ON managed_channels
        BEGIN
            UPDATE managed_channels SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END;

        PRAGMA foreign_keys = ON;
    """)

    logger.info("managed_channels table recreated without UNIQUE constraint")


def _update_channel_timing_constraints(conn: sqlite3.Connection) -> None:
    """Update CHECK constraints for channel_create_timing and channel_delete_timing.

    Removes 'manual' option from both and adds '6_hours_after' to delete timing.

    Since SQLite can't ALTER CHECK constraints, we just:
    1. Fix any 'manual' values to safe defaults
    2. Let schema.sql handle constraints for fresh databases

    The actual constraint removal happens by recreating the table, but we skip
    that for existing databases to avoid data loss. The constraint is relaxed
    by schema.sql for fresh databases.
    """
    # Fix any existing 'manual' values to safe defaults
    # This is the critical migration - convert unsupported values
    conn.execute("""
        UPDATE settings
        SET channel_create_timing = 'same_day'
        WHERE channel_create_timing = 'manual'
    """)
    conn.execute("""
        UPDATE settings
        SET channel_delete_timing = 'day_after'
        WHERE channel_delete_timing = 'manual'
    """)

    # For existing databases, we can't easily remove CHECK constraints
    # without risking data loss. The constraint prevents invalid values,
    # but '6_hours_after' is now valid, so we do a minimal table recreation.
    #
    # Note: This uses the safe approach of copying via a temp table.
    cursor = conn.execute("PRAGMA table_info(settings)")
    columns = [row["name"] for row in cursor.fetchall()]

    # Check if we already migrated (no CHECK constraint issue)
    # Try inserting a test value - if it fails, we need to migrate
    try:
        conn.execute(
            "UPDATE settings SET channel_delete_timing = '6_hours_after' WHERE 1=0"
        )
        # If this succeeds (even with 0 rows), constraint allows the value
        logger.info("Channel timing constraints already updated")
        return
    except Exception:
        pass  # Need to migrate

    # Get current CREATE TABLE statement
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='settings'"
    )
    row = cursor.fetchone()
    if not row or not row["sql"]:
        logger.info("Settings table not found, skipping constraint migration")
        return

    import re

    create_sql = row["sql"]

    # Modify CREATE statement to remove CHECK constraints on timing fields
    create_sql = re.sub(
        r"channel_create_timing TEXT DEFAULT '[^']*' CHECK\([^)]+\)",
        "channel_create_timing TEXT DEFAULT 'same_day'",
        create_sql,
    )
    create_sql = re.sub(
        r"channel_delete_timing TEXT DEFAULT '[^']*' CHECK\([^)]+\)",
        "channel_delete_timing TEXT DEFAULT 'day_after'",
        create_sql,
    )

    # Perform atomic table swap
    column_list = ", ".join(columns)
    conn.executescript(f"""
        PRAGMA foreign_keys = OFF;

        ALTER TABLE settings RENAME TO settings_old;

        {create_sql};

        INSERT INTO settings ({column_list})
        SELECT {column_list} FROM settings_old;

        DROP TABLE settings_old;

        PRAGMA foreign_keys = ON;
    """)

    logger.info("Updated settings table CHECK constraints for channel timing")


def _remove_group_timing_columns(conn: sqlite3.Connection) -> None:
    """Remove create_timing and delete_timing columns from event_epg_groups.

    These columns are no longer used - all groups now use global settings
    from the Settings table.
    """
    # Check if columns exist
    cursor = conn.execute("PRAGMA table_info(event_epg_groups)")
    columns = {row[1] for row in cursor.fetchall()}

    columns_to_drop = {"create_timing", "delete_timing"}
    columns_present = columns_to_drop & columns

    if not columns_present:
        logger.debug("Per-group timing columns already removed, skipping migration")
        return

    logger.info(f"Removing per-group timing columns: {columns_present}")

    # SQLite 3.35.0+ supports DROP COLUMN
    # Use try/except in case of older SQLite versions
    try:
        for col in columns_present:
            conn.execute(f"ALTER TABLE event_epg_groups DROP COLUMN {col}")
        logger.info("Successfully dropped timing columns using ALTER TABLE DROP COLUMN")
    except sqlite3.OperationalError as e:
        logger.warning(f"DROP COLUMN not supported ({e}), using table recreation")
        _remove_group_timing_columns_via_recreation(conn, columns_present)


def _remove_group_timing_columns_via_recreation(
    conn: sqlite3.Connection, columns_to_remove: set[str]
) -> None:
    """Fallback for SQLite < 3.35.0: recreate table without timing columns."""
    # Get current columns
    cursor = conn.execute("PRAGMA table_info(event_epg_groups)")
    all_columns = [row[1] for row in cursor.fetchall()]
    keep_columns = [c for c in all_columns if c not in columns_to_remove]

    # Get current table schema
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_epg_groups'"
    )
    create_sql = cursor.fetchone()[0]

    # Remove the timing column definitions from CREATE statement
    import re

    create_sql = re.sub(
        r",?\s*create_timing TEXT DEFAULT '[^']*'\s*CHECK\([^)]+\)",
        "",
        create_sql,
    )
    create_sql = re.sub(
        r",?\s*delete_timing TEXT DEFAULT '[^']*'\s*CHECK\([^)]+\)",
        "",
        create_sql,
    )

    # Perform atomic table swap
    column_list = ", ".join(keep_columns)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("ALTER TABLE event_epg_groups RENAME TO event_epg_groups_old")
        conn.execute(create_sql.replace("event_epg_groups", "event_epg_groups_new"))
        conn.execute(
            f"INSERT INTO event_epg_groups_new ({column_list}) "
            f"SELECT {column_list} FROM event_epg_groups_old"
        )
        conn.execute("DROP TABLE event_epg_groups_old")
        conn.execute("ALTER TABLE event_epg_groups_new RENAME TO event_epg_groups")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    logger.info("Recreated event_epg_groups table without timing columns")


def reset_db(db_path: Path | str | None = None) -> None:
    """Reset database - drops all tables and reinitializes.

    WARNING: This deletes all data!

    Args:
        db_path: Path to database file. Uses DEFAULT_DB_PATH if not specified.
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH

    if path.exists():
        path.unlink()

    init_db(path)
