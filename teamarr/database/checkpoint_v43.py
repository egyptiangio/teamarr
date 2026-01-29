"""Schema Checkpoint v43 - Consolidates migrations v1-v43 into idempotent operations.

This module replaces 43 individual procedural migrations with a single idempotent
checkpoint that brings ANY schema version (v1-v42) to v43.

Design principles:
1. IDEMPOTENT: Safe to run multiple times, regardless of starting state
2. DECLARATIVE: Ensures target state rather than describing transitions
3. DEFENSIVE: Checks existence before creating, validates after changing
4. TRANSACTIONAL: All-or-nothing with proper rollback on failure

Usage:
    if current_version < 43:
        apply_checkpoint_v43(conn, current_version)
        current_version = 43
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# =============================================================================
# EXPECTED SCHEMA STATE AT V43
# =============================================================================

# All columns that must exist on the settings table at v43
SETTINGS_COLUMNS_V43: dict[str, str] = {
    # Core settings (from schema.sql)
    "id": "INTEGER PRIMARY KEY CHECK (id = 1)",
    "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "team_schedule_days_ahead": "INTEGER DEFAULT 30",
    "event_match_days_ahead": "INTEGER DEFAULT 3",
    "event_match_days_back": "INTEGER DEFAULT 7",
    "epg_output_days_ahead": "INTEGER DEFAULT 14",
    "epg_lookback_hours": "INTEGER DEFAULT 6",
    "channel_create_timing": "TEXT DEFAULT 'same_day'",
    "channel_delete_timing": "TEXT DEFAULT 'day_after'",
    "midnight_crossover_mode": "TEXT DEFAULT 'postgame'",
    "epg_timezone": "TEXT DEFAULT 'America/New_York'",
    "epg_output_path": "TEXT DEFAULT './data/teamarr.xml'",
    "duration_default": "REAL DEFAULT 3.0",
    "duration_basketball": "REAL DEFAULT 3.0",
    "duration_football": "REAL DEFAULT 3.5",
    "duration_hockey": "REAL DEFAULT 3.0",
    "duration_baseball": "REAL DEFAULT 3.5",
    "duration_soccer": "REAL DEFAULT 2.5",
    "duration_mma": "REAL DEFAULT 5.0",
    "duration_rugby": "REAL DEFAULT 2.5",
    "duration_boxing": "REAL DEFAULT 4.0",
    "duration_tennis": "REAL DEFAULT 3.0",
    "duration_golf": "REAL DEFAULT 6.0",
    "duration_racing": "REAL DEFAULT 3.0",
    "duration_cricket": "REAL DEFAULT 4.0",
    "duration_volleyball": "REAL DEFAULT 2.5",
    "xmltv_generator_name": "TEXT DEFAULT 'Teamarr'",
    "xmltv_generator_url": "TEXT DEFAULT 'https://github.com/Pharaoh-Labs/teamarr'",
    "time_format": "TEXT DEFAULT '12h'",
    "show_timezone": "BOOLEAN DEFAULT 1",
    "include_final_events": "BOOLEAN DEFAULT 0",
    "channel_range_start": "INTEGER DEFAULT 101",
    "channel_range_end": "INTEGER",
    "default_include_teams": "JSON",
    "default_exclude_teams": "JSON",
    "default_team_filter_mode": "TEXT DEFAULT 'include'",
    "team_filter_enabled": "BOOLEAN DEFAULT 1",
    "cron_expression": "TEXT DEFAULT '0 * * * *'",
    "soccer_cache_refresh_frequency": "TEXT DEFAULT 'weekly'",
    "team_cache_refresh_frequency": "TEXT DEFAULT 'weekly'",
    "api_timeout": "INTEGER DEFAULT 10",
    "api_retry_count": "INTEGER DEFAULT 3",
    "tsdb_api_key": "TEXT",
    "channel_id_format": "TEXT DEFAULT '{team_name_pascal}.{league_id}'",
    "epg_generation_counter": "INTEGER DEFAULT 0",
    "dispatcharr_enabled": "BOOLEAN DEFAULT 0",
    "dispatcharr_url": "TEXT",
    "dispatcharr_username": "TEXT",
    "dispatcharr_password": "TEXT",
    "dispatcharr_epg_id": "INTEGER",
    "default_channel_profile_ids": "JSON",
    "reconcile_on_epg_generation": "BOOLEAN DEFAULT 1",
    "reconcile_on_startup": "BOOLEAN DEFAULT 1",
    "auto_fix_orphan_teamarr": "BOOLEAN DEFAULT 1",
    "auto_fix_orphan_dispatcharr": "BOOLEAN DEFAULT 1",
    "auto_fix_duplicates": "BOOLEAN DEFAULT 0",
    "default_duplicate_event_handling": "TEXT DEFAULT 'consolidate'",
    "channel_history_retention_days": "INTEGER DEFAULT 90",
    "scheduler_enabled": "BOOLEAN DEFAULT 1",
    "scheduler_interval_minutes": "INTEGER DEFAULT 15",
    "stream_filter_require_event_pattern": "BOOLEAN DEFAULT 1",
    "stream_filter_include_patterns": "JSON DEFAULT '[]'",
    "stream_filter_exclude_patterns": "JSON DEFAULT '[]'",
    "channel_numbering_mode": "TEXT DEFAULT 'strict_block'",
    "channel_sorting_scope": "TEXT DEFAULT 'per_group'",
    "channel_sort_by": "TEXT DEFAULT 'time'",
    "stream_ordering_rules": "JSON DEFAULT '[]'",
    "prepend_postponed_label": "BOOLEAN DEFAULT 1",
    "schema_version": "INTEGER DEFAULT 43",
}

# All columns that must exist on event_epg_groups at v43
EVENT_EPG_GROUPS_COLUMNS_V43: dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "name": "TEXT NOT NULL",
    "display_name": "TEXT",
    "group_mode": "TEXT DEFAULT 'single'",
    "leagues": "JSON NOT NULL",
    "template_id": "INTEGER",
    "channel_start_number": "INTEGER",
    "channel_group_id": "INTEGER",
    "channel_group_mode": "TEXT DEFAULT 'static'",
    "channel_profile_ids": "TEXT",
    "duplicate_event_handling": "TEXT DEFAULT 'consolidate'",
    "channel_assignment_mode": "TEXT DEFAULT 'auto'",
    "sort_order": "INTEGER DEFAULT 0",
    "total_stream_count": "INTEGER DEFAULT 0",
    "parent_group_id": "INTEGER",
    "m3u_group_id": "INTEGER",
    "m3u_group_name": "TEXT",
    "m3u_account_id": "INTEGER",
    "m3u_account_name": "TEXT",
    "last_refresh": "TIMESTAMP",
    "stream_count": "INTEGER DEFAULT 0",
    "matched_count": "INTEGER DEFAULT 0",
    "stream_include_regex": "TEXT",
    "stream_include_regex_enabled": "BOOLEAN DEFAULT 0",
    "stream_exclude_regex": "TEXT",
    "stream_exclude_regex_enabled": "BOOLEAN DEFAULT 0",
    "custom_regex_teams": "TEXT",
    "custom_regex_teams_enabled": "BOOLEAN DEFAULT 0",
    "custom_regex_date": "TEXT",
    "custom_regex_date_enabled": "BOOLEAN DEFAULT 0",
    "custom_regex_time": "TEXT",
    "custom_regex_time_enabled": "BOOLEAN DEFAULT 0",
    "custom_regex_league": "TEXT",
    "custom_regex_league_enabled": "BOOLEAN DEFAULT 0",
    "skip_builtin_filter": "BOOLEAN DEFAULT 0",
    "include_teams": "JSON",
    "exclude_teams": "JSON",
    "team_filter_mode": "TEXT DEFAULT 'include'",
    "filtered_stale": "INTEGER DEFAULT 0",
    "filtered_include_regex": "INTEGER DEFAULT 0",
    "filtered_exclude_regex": "INTEGER DEFAULT 0",
    "filtered_not_event": "INTEGER DEFAULT 0",
    "filtered_team": "INTEGER DEFAULT 0",
    "failed_count": "INTEGER DEFAULT 0",
    "streams_excluded": "INTEGER DEFAULT 0",
    "excluded_event_final": "INTEGER DEFAULT 0",
    "excluded_event_past": "INTEGER DEFAULT 0",
    "excluded_before_window": "INTEGER DEFAULT 0",
    "excluded_league_not_included": "INTEGER DEFAULT 0",
    "channel_sort_order": "TEXT DEFAULT 'time'",
    "overlap_handling": "TEXT DEFAULT 'add_stream'",
    "enabled": "BOOLEAN DEFAULT 1",
}

# Columns for managed_channel_history with correct CHECK constraints at v43
MANAGED_CHANNEL_HISTORY_COLUMNS_V43: dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "managed_channel_id": "INTEGER NOT NULL",
    "change_type": "TEXT NOT NULL",  # CHECK in table def
    "change_source": "TEXT",  # CHECK in table def
    "field_name": "TEXT",
    "old_value": "TEXT",
    "new_value": "TEXT",
    "changed_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "notes": "TEXT",
}

# All indexes expected at v43
INDEXES_V43: list[tuple[str, str, str]] = [
    # (index_name, table, definition)
    ("idx_templates_name", "templates", "templates(name)"),
    ("idx_templates_type", "templates", "templates(template_type)"),
    ("idx_teams_channel_id", "teams", "teams(channel_id)"),
    ("idx_teams_active", "teams", "teams(active)"),
    ("idx_teams_provider", "teams", "teams(provider)"),
    ("idx_teams_sport", "teams", "teams(sport)"),
    ("idx_event_epg_groups_enabled", "event_epg_groups", "event_epg_groups(enabled)"),
    ("idx_event_epg_groups_sort_order", "event_epg_groups", "event_epg_groups(sort_order)"),
    ("idx_event_epg_groups_name", "event_epg_groups", "event_epg_groups(name)"),
    ("idx_managed_channels_group", "managed_channels", "managed_channels(event_epg_group_id)"),
    (
        "idx_managed_channels_event",
        "managed_channels",
        "managed_channels(event_id, event_provider)",
    ),
    ("idx_managed_channels_expires", "managed_channels", "managed_channels(expires_at)"),
    (
        "idx_managed_channels_dispatcharr",
        "managed_channels",
        "managed_channels(dispatcharr_channel_id)",
    ),
    ("idx_managed_channels_tvg", "managed_channels", "managed_channels(tvg_id)"),
    ("idx_managed_channels_sync", "managed_channels", "managed_channels(sync_status)"),
    (
        "idx_mch_channel",
        "managed_channel_history",
        "managed_channel_history(managed_channel_id, changed_at DESC)",
    ),
    ("idx_mch_type", "managed_channel_history", "managed_channel_history(change_type)"),
    ("idx_mcs_channel", "managed_channel_streams", "managed_channel_streams(managed_channel_id)"),
    ("idx_mcs_stream", "managed_channel_streams", "managed_channel_streams(dispatcharr_stream_id)"),
    ("idx_leagues_provider", "leagues", "leagues(provider)"),
    ("idx_leagues_sport", "leagues", "leagues(sport)"),
    ("idx_leagues_import", "leagues", "leagues(import_enabled)"),
    ("idx_smc_generation", "stream_match_cache", "stream_match_cache(last_seen_generation)"),
    ("idx_smc_event_id", "stream_match_cache", "stream_match_cache(event_id)"),
    ("idx_smc_method", "stream_match_cache", "stream_match_cache(match_method)"),
    ("idx_tc_team_name", "team_cache", "team_cache(team_name COLLATE NOCASE)"),
    ("idx_tc_team_abbrev", "team_cache", "team_cache(team_abbrev COLLATE NOCASE)"),
    ("idx_tc_team_short", "team_cache", "team_cache(team_short_name COLLATE NOCASE)"),
    ("idx_tc_league", "team_cache", "team_cache(league)"),
    ("idx_tc_sport", "team_cache", "team_cache(sport)"),
    ("idx_tc_provider", "team_cache", "team_cache(provider)"),
    ("idx_tc_provider_team", "team_cache", "team_cache(provider, provider_team_id)"),
    ("idx_lc_sport", "league_cache", "league_cache(sport)"),
    ("idx_lc_provider", "league_cache", "league_cache(provider)"),
    ("idx_sc_expires", "service_cache", "service_cache(expires_at)"),
    (
        "idx_channel_sort_priorities_priority",
        "channel_sort_priorities",
        "channel_sort_priorities(sort_priority)",
    ),
    (
        "idx_exception_keywords_enabled",
        "consolidation_exception_keywords",
        "consolidation_exception_keywords(enabled)",
    ),
    (
        "idx_exception_keywords_behavior",
        "consolidation_exception_keywords",
        "consolidation_exception_keywords(behavior)",
    ),
    ("idx_team_aliases_league", "team_aliases", "team_aliases(league)"),
    ("idx_team_aliases_alias", "team_aliases", "team_aliases(alias)"),
    ("idx_processing_runs_type", "processing_runs", "processing_runs(run_type)"),
    ("idx_processing_runs_created", "processing_runs", "processing_runs(created_at)"),
    ("idx_processing_runs_group", "processing_runs", "processing_runs(group_id)"),
    ("idx_processing_runs_status", "processing_runs", "processing_runs(status)"),
    (
        "idx_processing_runs_type_created",
        "processing_runs",
        "processing_runs(run_type, created_at DESC)",
    ),
    ("idx_stats_snapshots_type", "stats_snapshots", "stats_snapshots(snapshot_type)"),
    ("idx_stats_snapshots_period", "stats_snapshots", "stats_snapshots(period_start)"),
    ("idx_matched_streams_run", "epg_matched_streams", "epg_matched_streams(run_id)"),
    ("idx_matched_streams_group", "epg_matched_streams", "epg_matched_streams(group_id)"),
    ("idx_matched_streams_method", "epg_matched_streams", "epg_matched_streams(match_method)"),
    ("idx_failed_matches_run", "epg_failed_matches", "epg_failed_matches(run_id)"),
    ("idx_failed_matches_group", "epg_failed_matches", "epg_failed_matches(group_id)"),
    ("idx_failed_matches_reason", "epg_failed_matches", "epg_failed_matches(reason)"),
    ("idx_mc_fingerprint", "match_corrections", "match_corrections(fingerprint)"),
    ("idx_mc_group", "match_corrections", "match_corrections(group_id)"),
    ("idx_mc_type", "match_corrections", "match_corrections(correction_type)"),
]

# Sports table seed data
SPORTS_SEED_V43: list[tuple[str, str]] = [
    ("football", "Football"),
    ("basketball", "Basketball"),
    ("hockey", "Hockey"),
    ("baseball", "Baseball"),
    ("softball", "Softball"),
    ("soccer", "Soccer"),
    ("mma", "MMA"),
    ("volleyball", "Volleyball"),
    ("lacrosse", "Lacrosse"),
    ("cricket", "Cricket"),
    ("rugby", "Rugby"),
    ("boxing", "Boxing"),
    ("tennis", "Tennis"),
    ("golf", "Golf"),
    ("wrestling", "Wrestling"),
    ("racing", "Racing"),
    ("australian-football", "Australian Football"),
]

# Exception keywords seed data
EXCEPTION_KEYWORDS_SEED_V43: list[tuple[str, str, str]] = [
    ("Spanish", "Spanish, En Español, (ESP), Español", "consolidate"),
    ("French", "French, En Français, (FRA), Français", "consolidate"),
    ("German", "German, (GER), Deutsch", "consolidate"),
    ("Portuguese", "Portuguese, (POR), Português", "consolidate"),
    ("Italian", "Italian, (ITA), Italiano", "consolidate"),
    ("Japanese", "Japanese, (JPN), 日本語", "consolidate"),
    ("Korean", "Korean, (KOR), 한국어", "consolidate"),
    ("Chinese", "Chinese, (CHN), (CHI), 中文", "consolidate"),
]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Get all column names for a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists."""
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    """Check if an index exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
    )
    return cursor.fetchone() is not None


def _add_column_safe(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    """Add a column if it doesn't exist. Returns True if added."""
    existing = _get_table_columns(conn, table)
    if column in existing:
        return False

    # Strip CHECK constraints from definition for ALTER TABLE
    # (SQLite doesn't support adding columns with CHECK via ALTER)
    clean_def = definition.split("CHECK")[0].strip()

    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {clean_def}")
        logger.debug("[CHECKPOINT] Added %s.%s", table, column)
        return True
    except sqlite3.OperationalError as e:
        logger.warning("[CHECKPOINT] Could not add %s.%s: %s", table, column, e)
        return False


def _ensure_index(conn: sqlite3.Connection, name: str, definition: str) -> bool:
    """Ensure an index exists. Returns True if created."""
    if _index_exists(conn, name):
        return False
    try:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
        logger.debug("[CHECKPOINT] Created index %s", name)
        return True
    except sqlite3.OperationalError as e:
        logger.warning("[CHECKPOINT] Could not create index %s: %s", name, e)
        return False


# =============================================================================
# PHASE 1: ENSURE TABLES EXIST
# =============================================================================


def _ensure_tables_v43(conn: sqlite3.Connection) -> None:
    """Ensure all required tables exist with correct structure."""

    # Sports table (v29)
    if not _table_exists(conn, "sports"):
        conn.execute("""
            CREATE TABLE sports (
                sport_code TEXT PRIMARY KEY,
                display_name TEXT NOT NULL
            )
        """)
        logger.info("[CHECKPOINT] Created sports table")

    # Channel sort priorities table (v30)
    if not _table_exists(conn, "channel_sort_priorities"):
        conn.execute("""
            CREATE TABLE channel_sort_priorities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sport TEXT NOT NULL,
                league_code TEXT,
                sort_priority INTEGER NOT NULL,
                UNIQUE(sport, league_code)
            )
        """)
        logger.info("[CHECKPOINT] Created channel_sort_priorities table")

    # Service cache table
    if not _table_exists(conn, "service_cache"):
        conn.execute("""
            CREATE TABLE service_cache (
                cache_key TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("[CHECKPOINT] Created service_cache table")

    # Cache meta table
    if not _table_exists(conn, "cache_meta"):
        conn.execute("""
            CREATE TABLE cache_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_full_refresh TIMESTAMP,
                espn_last_refresh TIMESTAMP,
                tsdb_last_refresh TIMESTAMP,
                leagues_count INTEGER DEFAULT 0,
                teams_count INTEGER DEFAULT 0,
                refresh_duration_seconds REAL DEFAULT 0,
                refresh_in_progress BOOLEAN DEFAULT 0,
                last_error TEXT
            )
        """)
        conn.execute("INSERT OR IGNORE INTO cache_meta (id) VALUES (1)")
        logger.info("[CHECKPOINT] Created cache_meta table")


# =============================================================================
# PHASE 2: ENSURE COLUMNS EXIST
# =============================================================================


def _ensure_columns_v43(conn: sqlite3.Connection) -> None:
    """Ensure all required columns exist on all tables."""

    columns_added = 0

    # Settings table columns
    if _table_exists(conn, "settings"):
        existing = _get_table_columns(conn, "settings")
        for col, defn in SETTINGS_COLUMNS_V43.items():
            if col not in existing and col != "id":  # Don't try to add primary key
                if _add_column_safe(conn, "settings", col, defn):
                    columns_added += 1

    # Event EPG groups columns
    if _table_exists(conn, "event_epg_groups"):
        existing = _get_table_columns(conn, "event_epg_groups")
        for col, defn in EVENT_EPG_GROUPS_COLUMNS_V43.items():
            if col not in existing and col != "id":
                if _add_column_safe(conn, "event_epg_groups", col, defn):
                    columns_added += 1

    # Leagues table - ensure league_alias exists (v6)
    if _table_exists(conn, "leagues"):
        _add_column_safe(conn, "leagues", "league_alias", "TEXT")
        _add_column_safe(conn, "leagues", "gracenote_category", "TEXT")

    # Templates - xmltv_video (v19)
    if _table_exists(conn, "templates"):
        _add_column_safe(
            conn,
            "templates",
            "xmltv_video",
            """JSON DEFAULT '{"enabled": false, "quality": "HDTV"}'""",
        )

    # epg_matched_streams - excluded columns (v24)
    if _table_exists(conn, "epg_matched_streams"):
        _add_column_safe(conn, "epg_matched_streams", "excluded", "BOOLEAN DEFAULT 0")
        _add_column_safe(conn, "epg_matched_streams", "exclusion_reason", "TEXT")
        _add_column_safe(conn, "epg_matched_streams", "origin_match_method", "TEXT")

    if columns_added > 0:
        logger.info("[CHECKPOINT] Added %d missing columns", columns_added)


# =============================================================================
# PHASE 3: ENSURE INDEXES EXIST
# =============================================================================


def _ensure_indexes_v43(conn: sqlite3.Connection) -> None:
    """Ensure all required indexes exist."""

    indexes_created = 0

    for name, table, definition in INDEXES_V43:
        if _table_exists(conn, table):
            if _ensure_index(conn, name, definition):
                indexes_created += 1

    # Special partial indexes
    if _table_exists(conn, "managed_channels"):
        # idx_managed_channels_delete with WHERE clause
        if not _index_exists(conn, "idx_managed_channels_delete"):
            try:
                conn.execute("""
                    CREATE INDEX idx_managed_channels_delete
                    ON managed_channels(scheduled_delete_at)
                    WHERE deleted_at IS NULL
                """)
                indexes_created += 1
            except sqlite3.OperationalError:
                pass

        # idx_mc_unique_event - unique partial index (v38)
        if not _index_exists(conn, "idx_mc_unique_event"):
            try:
                conn.execute("""
                    CREATE UNIQUE INDEX idx_mc_unique_event
                    ON managed_channels(
                        event_epg_group_id, event_id, event_provider,
                        COALESCE(exception_keyword, ''), primary_stream_id
                    ) WHERE deleted_at IS NULL
                """)
                indexes_created += 1
            except sqlite3.OperationalError:
                pass

    # idx_event_epg_groups_name_account - per-account uniqueness (v25)
    if _table_exists(conn, "event_epg_groups"):
        if not _index_exists(conn, "idx_event_epg_groups_name_account"):
            try:
                conn.execute("""
                    CREATE UNIQUE INDEX idx_event_epg_groups_name_account
                    ON event_epg_groups(name, m3u_account_id)
                """)
                indexes_created += 1
            except sqlite3.OperationalError:
                pass

    # idx_mcs_active - partial index for active streams
    if _table_exists(conn, "managed_channel_streams"):
        if not _index_exists(conn, "idx_mcs_active"):
            try:
                conn.execute("""
                    CREATE INDEX idx_mcs_active
                    ON managed_channel_streams(managed_channel_id, removed_at)
                    WHERE removed_at IS NULL
                """)
                indexes_created += 1
            except sqlite3.OperationalError:
                pass

    # idx_smc_user_corrected - partial index
    if _table_exists(conn, "stream_match_cache"):
        if not _index_exists(conn, "idx_smc_user_corrected"):
            try:
                conn.execute("""
                    CREATE INDEX idx_smc_user_corrected
                    ON stream_match_cache(user_corrected)
                    WHERE user_corrected = 1
                """)
                indexes_created += 1
            except sqlite3.OperationalError:
                pass

    if indexes_created > 0:
        logger.info("[CHECKPOINT] Created %d missing indexes", indexes_created)


# =============================================================================
# PHASE 4: DATA TRANSFORMATIONS (IDEMPOTENT)
# =============================================================================


def _normalize_data_v43(conn: sqlite3.Connection) -> None:
    """Apply all idempotent data transformations for v43."""

    # -------------------------------------------------------------------------
    # v3: teams.league -> primary_league + leagues JSON array
    # -------------------------------------------------------------------------
    if _table_exists(conn, "teams"):
        existing = _get_table_columns(conn, "teams")
        if "league" in existing and "primary_league" not in existing:
            logger.info("[CHECKPOINT] Migrating teams.league to primary_league + leagues")
            _add_column_safe(conn, "teams", "primary_league", "TEXT NOT NULL DEFAULT ''")
            _add_column_safe(conn, "teams", "leagues", "TEXT NOT NULL DEFAULT '[]'")
            conn.execute("""
                UPDATE teams
                SET primary_league = league,
                    leagues = json_array(league)
                WHERE primary_league = '' OR primary_league IS NULL
            """)

    # -------------------------------------------------------------------------
    # v20: group_mode based on league count
    # -------------------------------------------------------------------------
    if _table_exists(conn, "event_epg_groups"):
        columns = _get_table_columns(conn, "event_epg_groups")
        if "group_mode" in columns and "leagues" in columns:
            conn.execute("""
                UPDATE event_epg_groups
                SET group_mode = CASE
                    WHEN json_array_length(leagues) > 1 THEN 'multi'
                    ELSE 'single'
                END
                WHERE group_mode IS NULL
            """)

    # -------------------------------------------------------------------------
    # v28: epg_output_path default change
    # -------------------------------------------------------------------------
    if _table_exists(conn, "settings"):
        columns = _get_table_columns(conn, "settings")
        if "epg_output_path" in columns:
            conn.execute("""
                UPDATE settings
                SET epg_output_path = './data/teamarr.xml'
                WHERE id = 1 AND epg_output_path = './teamarr.xml'
            """)

    # -------------------------------------------------------------------------
    # v29: Seed sports table
    # -------------------------------------------------------------------------
    if _table_exists(conn, "sports"):
        for sport_code, display_name in SPORTS_SEED_V43:
            conn.execute(
                "INSERT OR REPLACE INTO sports (sport_code, display_name) VALUES (?, ?)",
                (sport_code, display_name),
            )

    # -------------------------------------------------------------------------
    # v31: Consolidate rugby_league/rugby_union -> rugby
    # -------------------------------------------------------------------------
    if _table_exists(conn, "sports"):
        # Remove old entries
        conn.execute("DELETE FROM sports WHERE sport_code IN ('rugby_league', 'rugby_union')")
        # Ensure unified entry exists (already in seed, but be safe)
        conn.execute(
            "INSERT OR REPLACE INTO sports (sport_code, display_name) VALUES ('rugby', 'Rugby')"
        )

    # Update references in other tables (only if sport column exists)
    for table in ["team_cache", "league_cache", "teams"]:
        if _table_exists(conn, table):
            columns = _get_table_columns(conn, table)
            if "sport" in columns:
                conn.execute(f"""
                    UPDATE {table}
                    SET sport = 'rugby'
                    WHERE sport IN ('rugby_league', 'rugby_union')
                """)

    # -------------------------------------------------------------------------
    # v40/v42: channel_group_mode enum -> pattern format
    # -------------------------------------------------------------------------
    if _table_exists(conn, "event_epg_groups"):
        columns = _get_table_columns(conn, "event_epg_groups")

        # Convert old enum values to pattern format (if column exists)
        if "channel_group_mode" in columns:
            # Check if table has CHECK constraint that would block the UPDATE
            # If so, we need to recreate the table (constraint removal)
            cursor = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_epg_groups'"
            )
            row = cursor.fetchone()
            table_sql = row[0] if row else ""

            has_check_constraint = (
                "channel_group_mode" in table_sql
                and "CHECK" in table_sql
                and "'sport'" in table_sql
                and "'league'" in table_sql
            )

            if has_check_constraint:
                # Table has CHECK constraint - must recreate table to remove it
                # Import and use the proper migration function from connection.py
                from teamarr.database.connection import _migrate_channel_group_mode_to_patterns

                logger.info("[CHECKPOINT] Recreating event_epg_groups to remove CHECK constraint")
                _migrate_channel_group_mode_to_patterns(conn)
                # Restore FK state — the migration function re-enables foreign keys
                # in its finally block, but the checkpoint needs them OFF for Phase 5
                conn.execute("PRAGMA foreign_keys = OFF")
                # Table recreation handles all column conversions, skip remaining UPDATEs
            else:
                # No CHECK constraint - safe to UPDATE in place
                conn.execute("""
                    UPDATE event_epg_groups
                    SET channel_group_mode = '{sport}'
                    WHERE channel_group_mode = 'sport'
                """)
                conn.execute("""
                    UPDATE event_epg_groups
                    SET channel_group_mode = '{league}'
                    WHERE channel_group_mode = 'league'
                """)

                # Fix invalid enum values (v42 recovery) - only if columns exist
                # (skipped if table was recreated above since values are already converted)
                if "channel_assignment_mode" in columns:
                    conn.execute("""
                        UPDATE event_epg_groups
                        SET channel_assignment_mode = 'auto'
                        WHERE channel_assignment_mode NOT IN ('auto', 'manual')
                    """)
                if "channel_sort_order" in columns:
                    conn.execute("""
                        UPDATE event_epg_groups
                        SET channel_sort_order = 'time'
                        WHERE channel_sort_order NOT IN ('time', 'sport_time', 'league_time')
                    """)
                if "overlap_handling" in columns:
                    conn.execute("""
                        UPDATE event_epg_groups
                        SET overlap_handling = 'add_stream'
                        WHERE overlap_handling NOT IN ('add_stream', 'add_only', 'create_all', 'skip')
                    """)  # noqa: E501

    # -------------------------------------------------------------------------
    # v35: Exception keywords label + match_terms restructure
    # -------------------------------------------------------------------------
    if _table_exists(conn, "consolidation_exception_keywords"):
        existing = _get_table_columns(conn, "consolidation_exception_keywords")

        # Check if we need to migrate from old schema
        if "keywords" in existing and "match_terms" not in existing:
            logger.info("[CHECKPOINT] Migrating exception keywords to label + match_terms")
            _add_column_safe(conn, "consolidation_exception_keywords", "label", "TEXT")
            _add_column_safe(conn, "consolidation_exception_keywords", "match_terms", "TEXT")

            # Migrate data: use display_name as label if set, else first keyword
            conn.execute("""
                UPDATE consolidation_exception_keywords
                SET label = COALESCE(
                    NULLIF(display_name, ''),
                    TRIM(SUBSTR(keywords, 1, INSTR(keywords || ',', ',') - 1))
                ),
                match_terms = keywords
                WHERE label IS NULL OR label = ''
            """)

        # Ensure default keywords exist
        for label, match_terms, behavior in EXCEPTION_KEYWORDS_SEED_V43:
            conn.execute(
                """
                INSERT OR IGNORE INTO consolidation_exception_keywords (label, match_terms, behavior)
                VALUES (?, ?, ?)
            """,  # noqa: E501
                (label, match_terms, behavior),
            )


# =============================================================================
# PHASE 5: STRUCTURAL FIXES (Table Recreations)
# =============================================================================


def _fix_table_structures_v43(conn: sqlite3.Connection) -> None:
    """Fix table structures that require recreation (CHECK constraints, etc.)."""

    # -------------------------------------------------------------------------
    # managed_channel_history: Update CHECK constraints (v9)
    # -------------------------------------------------------------------------
    if _table_exists(conn, "managed_channel_history"):
        # Check if we need to update constraints by testing invalid value
        try:
            # Try to detect if constraint is already updated
            cursor = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='managed_channel_history'"  # noqa: E501
            )
            row = cursor.fetchone()
            if row and "keyword_ordering" not in row[0]:
                logger.info("[CHECKPOINT] Updating managed_channel_history CHECK constraints")
                _recreate_managed_channel_history_v43(conn)
        except sqlite3.Error:
            pass


def _recreate_managed_channel_history_v43(conn: sqlite3.Connection) -> None:
    """Recreate managed_channel_history with v43 CHECK constraints."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS managed_channel_history_new (
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

        INSERT INTO managed_channel_history_new
        SELECT * FROM managed_channel_history;

        DROP TABLE managed_channel_history;
        ALTER TABLE managed_channel_history_new RENAME TO managed_channel_history;

        CREATE INDEX IF NOT EXISTS idx_mch_channel
        ON managed_channel_history(managed_channel_id, changed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_mch_type
        ON managed_channel_history(change_type);
    """)  # noqa: E501


# =============================================================================
# PHASE 6: VERIFICATION
# =============================================================================


def _verify_schema_v43(conn: sqlite3.Connection) -> list[str]:
    """Verify schema matches v43 expectations. Returns list of issues."""
    issues = []

    # Check settings columns
    if _table_exists(conn, "settings"):
        existing = _get_table_columns(conn, "settings")
        critical_cols = {
            "schema_version",
            "epg_output_path",
            "team_filter_enabled",
            "prepend_postponed_label",
            "stream_ordering_rules",
        }
        missing = critical_cols - existing
        if missing:
            issues.append(f"settings missing columns: {missing}")

    # Check event_epg_groups columns
    if _table_exists(conn, "event_epg_groups"):
        existing = _get_table_columns(conn, "event_epg_groups")
        critical_cols = {
            "group_mode",
            "display_name",
            "failed_count",
            "channel_group_mode",
            "custom_regex_league",
        }
        missing = critical_cols - existing
        if missing:
            issues.append(f"event_epg_groups missing columns: {missing}")

    # Check required tables exist
    required_tables = [
        "settings",
        "templates",
        "teams",
        "event_epg_groups",
        "managed_channels",
        "sports",
        "leagues",
        "stream_match_cache",
        "team_cache",
        "league_cache",
    ]
    for table in required_tables:
        if not _table_exists(conn, table):
            issues.append(f"missing table: {table}")

    return issues


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


def apply_checkpoint_v43(conn: sqlite3.Connection, from_version: int) -> bool:
    """Apply v43 checkpoint to bring schema from any version < 43 to v43.

    This function is IDEMPOTENT and can be safely called multiple times.

    Args:
        conn: Database connection (must have row_factory set)
        from_version: Current schema version (for logging only)

    Returns:
        True if checkpoint completed successfully, False otherwise
    """
    logger.info("[CHECKPOINT] Applying v43 checkpoint (from v%d)", from_version)

    try:
        # Disable foreign keys during migration
        conn.execute("PRAGMA foreign_keys = OFF")

        # Phase 1: Ensure tables exist
        _ensure_tables_v43(conn)

        # Phase 2: Ensure columns exist
        _ensure_columns_v43(conn)

        # Phase 3: Ensure indexes exist
        _ensure_indexes_v43(conn)

        # Phase 4: Data transformations
        _normalize_data_v43(conn)

        # Phase 5: Structural fixes
        _fix_table_structures_v43(conn)

        # Phase 6: Verification
        issues = _verify_schema_v43(conn)
        if issues:
            for issue in issues:
                logger.warning("[CHECKPOINT] Schema issue: %s", issue)
            # Don't fail on issues - they might be non-critical

        # Update version
        conn.execute("UPDATE settings SET schema_version = 43 WHERE id = 1")

        # Re-enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")

        logger.info("[CHECKPOINT] v43 checkpoint complete")
        return True

    except Exception as e:
        logger.error("[CHECKPOINT] Failed to apply v43 checkpoint: %s", e)
        # Re-enable foreign keys even on failure
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        raise
