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

def run_migrations(conn):
    """
    Run database migrations for schema updates.

    This function handles migrations from v1.x (dev branch) to v2.0 (dev-withevents).
    New installations get the full schema from schema.sql, so migrations only run
    for databases that existed before the event-based EPG features were added.

    Migration groups:
    1. Settings table columns (Dispatcharr, time format, lifecycle)
    2. Templates table columns (conditional descriptions, event templates)
    3. EPG History table columns (filler counts, stats breakdown)
    4. Event EPG tables (event_epg_groups, team_aliases, managed_channels)
    5. Data fixes (NCAA logos)
    """
    cursor = conn.cursor()
    migrations_run = 0

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
                    print(f"  ✅ Added column: {table_name}.{col_name}")
                except Exception as e:
                    print(f"  ⚠️ Could not add column {table_name}.{col_name}: {e}")

        conn.commit()

    def table_exists(table_name):
        """Check if a table exists"""
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        return cursor.fetchone() is not None

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
            ("custom_regex", "TEXT"),
            ("custom_regex_enabled", "INTEGER DEFAULT 0"),
            ("custom_regex_team1", "TEXT"),  # Deprecated - use custom_regex_teams
            ("custom_regex_team2", "TEXT"),  # Deprecated - use custom_regex_teams
            ("custom_regex_teams", "TEXT"),  # Combined pattern with (?P<team1>...) and (?P<team2>...)
            ("custom_regex_date", "TEXT"),
            ("custom_regex_time", "TEXT"),
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
            ("logo_deleted", "INTEGER")  # 1=deleted, 0=failed to delete, NULL=no logo was present
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

    if migrations_run > 0:
        print(f"✅ Completed {migrations_run} migration(s)")

    return migrations_run


def init_database():
    """Initialize database with schema and run migrations"""
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')

    with open(schema_path, 'r') as f:
        schema_sql = f.read()

    conn = get_connection()
    try:
        conn.executescript(schema_sql)
        conn.commit()

        # Run migrations for existing databases
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

def get_event_epg_group(group_id: int) -> Optional[Dict[str, Any]]:
    """Get an event EPG group by ID."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        result = cursor.execute(
            "SELECT * FROM event_epg_groups WHERE id = ?",
            (group_id,)
        ).fetchone()
        return dict(result) if result else None
    finally:
        conn.close()


def get_event_epg_group_by_dispatcharr_id(dispatcharr_group_id: int) -> Optional[Dict[str, Any]]:
    """Get an event EPG group by Dispatcharr group ID."""
    return db_fetch_one(
        "SELECT * FROM event_epg_groups WHERE dispatcharr_group_id = ?",
        (dispatcharr_group_id,)
    )


def get_all_event_epg_groups(enabled_only: bool = False) -> List[Dict[str, Any]]:
    """Get all event EPG groups with template names."""
    query = """
        SELECT g.*, t.name as event_template_name
        FROM event_epg_groups g
        LEFT JOIN templates t ON g.event_template_id = t.id
    """
    if enabled_only:
        query += " WHERE g.enabled = 1"
    query += " ORDER BY g.group_name"
    return db_fetch_all(query)


def create_event_epg_group(
    dispatcharr_group_id: int,
    dispatcharr_account_id: int,
    group_name: str,
    assigned_league: str,
    assigned_sport: str,
    enabled: bool = True,
    refresh_interval_minutes: int = 60,
    event_template_id: int = None,
    account_name: str = None,
    channel_start: int = None,
    channel_group_id: int = None,
    stream_profile_id: int = None,
    custom_regex: str = None,
    custom_regex_enabled: bool = False,
    custom_regex_teams: str = None,
    custom_regex_date: str = None,
    custom_regex_time: str = None
) -> int:
    """
    Create a new event EPG group.

    Args:
        event_template_id: Optional template ID (must be an 'event' type template)
        account_name: Optional M3U account name for display purposes
        channel_start: Starting channel number for auto-created channels
        channel_group_id: Dispatcharr channel group ID to assign created channels to
        stream_profile_id: Dispatcharr stream profile ID to assign to created channels
        custom_regex: Legacy single regex pattern (deprecated)
        custom_regex_enabled: Whether to use custom regex instead of built-in matching
        custom_regex_teams: Combined regex with (?P<team1>...) and (?P<team2>...) groups
        custom_regex_date: Optional regex pattern to extract game date
        custom_regex_time: Optional regex pattern to extract game time

    Returns:
        ID of created group

    Raises:
        sqlite3.IntegrityError if dispatcharr_group_id already exists
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO event_epg_groups
            (dispatcharr_group_id, dispatcharr_account_id, group_name,
             assigned_league, assigned_sport, enabled, refresh_interval_minutes,
             event_template_id, account_name, channel_start, channel_group_id,
             stream_profile_id, custom_regex, custom_regex_enabled,
             custom_regex_teams, custom_regex_date, custom_regex_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dispatcharr_group_id, dispatcharr_account_id, group_name,
                assigned_league.lower(), assigned_sport.lower(),
                1 if enabled else 0, refresh_interval_minutes,
                event_template_id, account_name, channel_start,
                channel_group_id, stream_profile_id, custom_regex,
                1 if custom_regex_enabled else 0,
                custom_regex_teams, custom_regex_date, custom_regex_time
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

        fields = [k for k in data.keys() if k != 'id']
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
    """Delete an event EPG group."""
    return db_execute("DELETE FROM event_epg_groups WHERE id = ?", (group_id,)) > 0


def update_event_epg_group_stats(
    group_id: int,
    stream_count: int,
    matched_count: int
) -> bool:
    """Update stats after EPG generation."""
    return db_execute(
        """
        UPDATE event_epg_groups
        SET stream_count = ?, matched_count = ?, last_refresh = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (stream_count, matched_count, group_id)
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

def get_managed_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    """Get a managed channel by ID."""
    return db_fetch_one("SELECT * FROM managed_channels WHERE id = ?", (channel_id,))


def get_managed_channel_by_dispatcharr_id(dispatcharr_channel_id: int) -> Optional[Dict[str, Any]]:
    """Get a managed channel by Dispatcharr channel ID."""
    return db_fetch_one(
        "SELECT * FROM managed_channels WHERE dispatcharr_channel_id = ?",
        (dispatcharr_channel_id,)
    )


def get_managed_channel_by_event(espn_event_id: str, group_id: int = None) -> Optional[Dict[str, Any]]:
    """Get a managed channel by ESPN event ID, optionally filtered by group."""
    if group_id:
        return db_fetch_one(
            "SELECT * FROM managed_channels WHERE espn_event_id = ? AND event_epg_group_id = ? AND deleted_at IS NULL",
            (espn_event_id, group_id)
        )
    else:
        return db_fetch_one(
            "SELECT * FROM managed_channels WHERE espn_event_id = ? AND deleted_at IS NULL",
            (espn_event_id,)
        )


def get_managed_channels_for_group(group_id: int, include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Get all managed channels for an event EPG group."""
    query = "SELECT * FROM managed_channels WHERE event_epg_group_id = ?"
    if not include_deleted:
        query += " AND deleted_at IS NULL"
    query += " ORDER BY channel_number"
    return db_fetch_all(query, (group_id,))


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
    return db_fetch_all(query)


def get_channels_pending_deletion() -> List[Dict[str, Any]]:
    """Get channels that are scheduled for deletion and past their delete time."""
    # Use datetime() to normalize both values for comparison
    # scheduled_delete_at is stored as ISO8601 with timezone (e.g., 2025-11-28T04:59:59+00:00)
    # CURRENT_TIMESTAMP returns YYYY-MM-DD HH:MM:SS format
    # datetime() normalizes both to comparable format
    return db_fetch_all("""
        SELECT * FROM managed_channels
        WHERE scheduled_delete_at IS NOT NULL
        AND datetime(scheduled_delete_at) <= datetime('now')
        AND deleted_at IS NULL
        ORDER BY scheduled_delete_at
    """)


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
    dispatcharr_logo_id: int = None
) -> int:
    """
    Create a new managed channel record.

    Args:
        dispatcharr_logo_id: Logo ID in Dispatcharr (for cleanup when channel is deleted)

    Returns:
        ID of created record

    Raises:
        sqlite3.IntegrityError if dispatcharr_channel_id already exists
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO managed_channels
            (event_epg_group_id, dispatcharr_channel_id, dispatcharr_stream_id,
             channel_number, channel_name, tvg_id, espn_event_id, event_date,
             home_team, away_team, scheduled_delete_at, dispatcharr_logo_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_epg_group_id, dispatcharr_channel_id, dispatcharr_stream_id,
                channel_number, channel_name, tvg_id, espn_event_id, event_date,
                home_team, away_team, scheduled_delete_at, dispatcharr_logo_id
            )
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


def get_next_available_channel_range(dispatcharr_url: str = None, dispatcharr_username: str = None, dispatcharr_password: str = None) -> int:
    """
    Calculate the next available channel range start (1001, 2001, 3001, etc.).

    This is used as a fallback when a group doesn't have a channel_start set.
    Considers:
    1. All existing event groups' channel_start values
    2. All managed channels' actual channel numbers
    3. All channels in Dispatcharr (if credentials provided)

    Returns:
        The next available 1001 multiple (e.g., 1001, 2001, 3001, etc.)
    """
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

        # Calculate next 1001 multiple after highest channel
        # E.g., if highest is 5419, next range is 6001
        if highest_channel == 0:
            return 1001

        next_range = ((int(highest_channel) // 1000) + 1) * 1000 + 1
        return int(next_range)

    finally:
        conn.close()


def get_next_channel_number(group_id: int, auto_assign: bool = True) -> Optional[int]:
    """
    Get the next available channel number for a group.

    Uses the group's channel_start and finds the next unused number.
    If the group has no channel_start and auto_assign is True, assigns
    the next available 1001 range and saves it to the group.

    Args:
        group_id: The event group ID
        auto_assign: If True, auto-assign a channel_start when missing

    Returns:
        The next available channel number, or None if disabled
    """
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
                unresolved_vars_count, coverage_gaps_count, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
