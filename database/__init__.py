"""Database module for Teamarr - Template-Based Architecture"""
import sqlite3
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

# Database path - respects Docker volume mount at /app/data
# In Docker: /app/data/teamarr.db (persisted via volume)
# In local dev: ./data/teamarr.db (or project root if data/ doesn't exist)
def get_db_path():
    """Get database path, preferring /app/data/ for Docker compatibility"""
    # Check if we're in Docker (has /app/data directory)
    if os.path.exists('/app/data'):
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

def run_migrations(conn):
    """Run database migrations for schema updates"""
    cursor = conn.cursor()

    # Get existing columns in settings table
    cursor.execute("PRAGMA table_info(settings)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    # Dispatcharr integration columns (added in v1.0.6)
    dispatcharr_columns = [
        ("dispatcharr_enabled", "BOOLEAN DEFAULT 0"),
        ("dispatcharr_url", "TEXT DEFAULT 'http://localhost:9191'"),
        ("dispatcharr_username", "TEXT"),
        ("dispatcharr_password", "TEXT"),
        ("dispatcharr_epg_id", "INTEGER"),
        ("dispatcharr_last_sync", "TEXT"),
    ]

    migrations_run = 0
    for col_name, col_def in dispatcharr_columns:
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE settings ADD COLUMN {col_name} {col_def}")
                migrations_run += 1
                print(f"  ✅ Added column: settings.{col_name}")
            except Exception as e:
                print(f"  ⚠️ Could not add column {col_name}: {e}")

    if migrations_run > 0:
        conn.commit()
        print(f"✅ Ran {migrations_run} migration(s)")

    # Conditional postgame/idle description columns (added in v1.0.7)
    cursor.execute("PRAGMA table_info(templates)")
    template_columns = {row[1] for row in cursor.fetchall()}

    conditional_columns = [
        ("postgame_conditional_enabled", "BOOLEAN DEFAULT 0"),
        ("postgame_description_final", "TEXT DEFAULT 'The {team_name} {result_text.last} the {opponent.last} {final_score.last} {overtime_text.last}'"),
        ("postgame_description_not_final", "TEXT DEFAULT 'The game between the {team_name} and {opponent.last} on {game_day.last} {game_date.last} has not yet ended.'"),
        ("idle_conditional_enabled", "BOOLEAN DEFAULT 0"),
        ("idle_description_final", "TEXT DEFAULT 'The {team_name} {result_text.last} the {opponent.last} {final_score.last}. Next: {opponent.next} on {game_date.next}'"),
        ("idle_description_not_final", "TEXT DEFAULT 'The {team_name} last played {opponent.last} on {game_date.last}. Next: {opponent.next} on {game_date.next}'"),
    ]

    for col_name, col_def in conditional_columns:
        if col_name not in template_columns:
            try:
                cursor.execute(f"ALTER TABLE templates ADD COLUMN {col_name} {col_def}")
                print(f"  ✅ Added column: templates.{col_name}")
            except Exception as e:
                print(f"  ⚠️ Could not add column {col_name}: {e}")

    conn.commit()

    # Fix NCAA league logos (use NCAA.com sport banners)
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

# Helper functions for template operations

def _parse_template_json_fields(template: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON fields in a template dict"""
    import json

    # Fields that should be parsed from JSON
    json_fields = ['categories', 'flags', 'description_options']

    for field in json_fields:
        if field in template and template[field]:
            try:
                # Parse JSON string to Python object
                template[field] = json.loads(template[field])
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, leave as-is or set to default
                if field == 'categories':
                    template[field] = []
                elif field == 'flags':
                    template[field] = {}
                elif field == 'description_options':
                    template[field] = []

    return template

def _serialize_template_json_fields(template: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize JSON fields in a template dict to strings for database storage"""
    import json

    # Fields that should be serialized to JSON
    json_fields = ['categories', 'flags', 'description_options']

    for field in json_fields:
        if field in template and template[field] is not None:
            if not isinstance(template[field], str):
                # Serialize Python object to JSON string
                template[field] = json.dumps(template[field])

    return template

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
    """Get all templates with team count"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        results = cursor.execute("""
            SELECT
                t.*,
                COUNT(tm.id) as team_count
            FROM templates t
            LEFT JOIN teams tm ON t.id = tm.template_id
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

        # Extract fields (all are optional except name)
        fields = [
            'name', 'sport', 'league',
            'title_format', 'subtitle_template', 'program_art_url',
            'game_duration_mode', 'game_duration_override',
            'flags', 'categories', 'categories_apply_to',
            'no_game_enabled', 'no_game_title', 'no_game_description', 'no_game_duration',
            'pregame_enabled', 'pregame_periods', 'pregame_title', 'pregame_subtitle', 'pregame_description', 'pregame_art_url',
            'postgame_enabled', 'postgame_periods', 'postgame_title', 'postgame_subtitle', 'postgame_description', 'postgame_art_url',
            'idle_enabled', 'idle_title', 'idle_subtitle', 'idle_description', 'idle_art_url',
            'description_options'
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
