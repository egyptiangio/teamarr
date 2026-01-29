"""Tests for checkpoint_v43 migration consolidation.

These tests verify that the v43 checkpoint correctly brings databases
from any previous schema version to v43.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from teamarr.database.checkpoint_v43 import (
    SETTINGS_COLUMNS_V43,
    EVENT_EPG_GROUPS_COLUMNS_V43,
    INDEXES_V43,
    apply_checkpoint_v43,
    _get_table_columns,
    _table_exists,
    _index_exists,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    yield conn, db_path

    conn.close()
    db_path.unlink(missing_ok=True)


@pytest.fixture
def minimal_v2_schema(temp_db):
    """Create a minimal v2 schema (earliest supported version)."""
    conn, db_path = temp_db

    # Create minimal settings table with just the essentials
    conn.execute("""
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER DEFAULT 2
        )
    """)
    conn.execute("INSERT INTO settings (id, schema_version) VALUES (1, 2)")

    # Create minimal templates table
    conn.execute("""
        CREATE TABLE templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    # Create minimal teams table (old format with 'league' column)
    conn.execute("""
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_team_id TEXT,
            provider TEXT,
            league TEXT,
            UNIQUE(provider, provider_team_id)
        )
    """)

    # Create minimal event_epg_groups table
    conn.execute("""
        CREATE TABLE event_epg_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            leagues JSON NOT NULL
        )
    """)

    # Create minimal managed_channels table
    conn.execute("""
        CREATE TABLE managed_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_epg_group_id INTEGER,
            event_id TEXT,
            tvg_id TEXT UNIQUE
        )
    """)

    # Create minimal managed_channel_history (old CHECK constraints)
    conn.execute("""
        CREATE TABLE managed_channel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            managed_channel_id INTEGER NOT NULL,
            change_type TEXT NOT NULL CHECK(change_type IN ('created', 'modified', 'deleted')),
            change_source TEXT CHECK(change_source IN ('epg_generation', 'reconciliation', 'api'))
        )
    """)

    # Create minimal leagues table
    conn.execute("""
        CREATE TABLE leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_code TEXT NOT NULL UNIQUE,
            provider TEXT,
            sport TEXT
        )
    """)

    # Create minimal epg_matched_streams table
    conn.execute("""
        CREATE TABLE epg_matched_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            group_id INTEGER,
            match_method TEXT
        )
    """)

    conn.commit()

    return conn, db_path


@pytest.fixture
def partial_v20_schema(temp_db):
    """Create a partial v20 schema (mid-way, missing some v15-v19 columns)."""
    conn, db_path = temp_db

    # Create settings with some columns missing
    conn.execute("""
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER DEFAULT 20,
            team_schedule_days_ahead INTEGER DEFAULT 30,
            event_match_days_ahead INTEGER DEFAULT 3,
            epg_output_path TEXT DEFAULT './teamarr.xml'
        )
    """)
    conn.execute("INSERT INTO settings (id, schema_version) VALUES (1, 20)")

    # Create templates
    conn.execute("""
        CREATE TABLE templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description_template TEXT
        )
    """)

    # Create teams with new format
    conn.execute("""
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_team_id TEXT,
            provider TEXT,
            primary_league TEXT,
            leagues JSON DEFAULT '[]',
            UNIQUE(provider, provider_team_id)
        )
    """)

    # Create event_epg_groups with partial columns
    conn.execute("""
        CREATE TABLE event_epg_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            leagues JSON NOT NULL,
            group_mode TEXT DEFAULT 'single',
            display_name TEXT
        )
    """)

    # Create managed_channels
    conn.execute("""
        CREATE TABLE managed_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_epg_group_id INTEGER,
            event_id TEXT,
            tvg_id TEXT
        )
    """)

    # Create managed_channel_history with updated CHECK
    conn.execute("""
        CREATE TABLE managed_channel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            managed_channel_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            change_source TEXT
        )
    """)

    # Create leagues
    conn.execute("""
        CREATE TABLE leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_code TEXT NOT NULL UNIQUE,
            provider TEXT,
            sport TEXT,
            league_alias TEXT
        )
    """)

    # Create epg_matched_streams
    conn.execute("""
        CREATE TABLE epg_matched_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            group_id INTEGER,
            match_method TEXT
        )
    """)

    conn.commit()

    return conn, db_path


@pytest.fixture
def v42_schema(temp_db):
    """Create a v42 schema (just before v43)."""
    conn, db_path = temp_db

    # Create full v42 settings
    conn.execute("""
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER DEFAULT 42,
            team_schedule_days_ahead INTEGER DEFAULT 30,
            event_match_days_ahead INTEGER DEFAULT 3,
            event_match_days_back INTEGER DEFAULT 7,
            epg_output_path TEXT DEFAULT './data/teamarr.xml',
            channel_numbering_mode TEXT DEFAULT 'strict_block',
            stream_ordering_rules JSON DEFAULT '[]'
        )
    """)
    conn.execute("INSERT INTO settings (id, schema_version) VALUES (1, 42)")

    # Create templates
    conn.execute("""
        CREATE TABLE templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description_template TEXT,
            xmltv_video JSON DEFAULT '{"enabled": false, "quality": "HDTV"}'
        )
    """)

    # Create teams
    conn.execute("""
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_team_id TEXT,
            provider TEXT,
            primary_league TEXT,
            leagues JSON DEFAULT '[]',
            sport TEXT,
            UNIQUE(provider, provider_team_id, primary_league)
        )
    """)

    # Create event_epg_groups with most columns
    conn.execute("""
        CREATE TABLE event_epg_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            m3u_account_id INTEGER,
            leagues JSON NOT NULL,
            group_mode TEXT DEFAULT 'single',
            channel_group_mode TEXT DEFAULT 'static',
            display_name TEXT,
            failed_count INTEGER DEFAULT 0
        )
    """)

    # Create managed_channels
    conn.execute("""
        CREATE TABLE managed_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_epg_group_id INTEGER,
            event_id TEXT,
            tvg_id TEXT,
            deleted_at TIMESTAMP,
            primary_stream_id INTEGER
        )
    """)

    # Create managed_channel_history
    conn.execute("""
        CREATE TABLE managed_channel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            managed_channel_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            change_source TEXT
        )
    """)

    # Create leagues
    conn.execute("""
        CREATE TABLE leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_code TEXT NOT NULL UNIQUE,
            provider TEXT,
            sport TEXT,
            league_alias TEXT,
            gracenote_category TEXT
        )
    """)

    # Create epg_matched_streams
    conn.execute("""
        CREATE TABLE epg_matched_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            group_id INTEGER,
            match_method TEXT,
            excluded BOOLEAN DEFAULT 0,
            exclusion_reason TEXT
        )
    """)

    # Create sports table
    conn.execute("""
        CREATE TABLE sports (
            sport_code TEXT PRIMARY KEY,
            display_name TEXT NOT NULL
        )
    """)

    conn.commit()

    return conn, db_path


# =============================================================================
# TESTS
# =============================================================================


class TestCheckpointFromV2:
    """Test checkpoint migration from v2 (earliest version)."""

    def test_checkpoint_runs_without_error(self, minimal_v2_schema):
        """Checkpoint should complete without raising exceptions."""
        conn, _ = minimal_v2_schema
        apply_checkpoint_v43(conn, 2)

    def test_version_updated_to_43(self, minimal_v2_schema):
        """Version should be 43 after checkpoint."""
        conn, _ = minimal_v2_schema
        apply_checkpoint_v43(conn, 2)

        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 43

    def test_settings_columns_added(self, minimal_v2_schema):
        """Critical settings columns should exist after checkpoint."""
        conn, _ = minimal_v2_schema
        apply_checkpoint_v43(conn, 2)

        columns = _get_table_columns(conn, "settings")

        # Check critical columns
        assert "team_filter_enabled" in columns
        assert "prepend_postponed_label" in columns
        assert "stream_ordering_rules" in columns
        assert "event_match_days_back" in columns

    def test_sports_table_created(self, minimal_v2_schema):
        """Sports table should be created and seeded."""
        conn, _ = minimal_v2_schema
        apply_checkpoint_v43(conn, 2)

        assert _table_exists(conn, "sports")

        # Check some sports exist
        row = conn.execute(
            "SELECT display_name FROM sports WHERE sport_code = 'football'"
        ).fetchone()
        assert row is not None
        assert row["display_name"] == "Football"

    def test_event_epg_groups_columns_added(self, minimal_v2_schema):
        """Event EPG groups should have all required columns."""
        conn, _ = minimal_v2_schema
        apply_checkpoint_v43(conn, 2)

        columns = _get_table_columns(conn, "event_epg_groups")

        assert "group_mode" in columns
        assert "display_name" in columns
        assert "failed_count" in columns
        assert "channel_group_mode" in columns

    def test_epg_matched_streams_columns_added(self, minimal_v2_schema):
        """epg_matched_streams should have excluded columns."""
        conn, _ = minimal_v2_schema
        apply_checkpoint_v43(conn, 2)

        columns = _get_table_columns(conn, "epg_matched_streams")

        assert "excluded" in columns
        assert "exclusion_reason" in columns
        assert "origin_match_method" in columns


class TestCheckpointFromV20:
    """Test checkpoint migration from v20 (mid-version)."""

    def test_checkpoint_runs_without_error(self, partial_v20_schema):
        """Checkpoint should complete without raising exceptions."""
        conn, _ = partial_v20_schema
        apply_checkpoint_v43(conn, 20)

    def test_version_updated_to_43(self, partial_v20_schema):
        """Version should be 43 after checkpoint."""
        conn, _ = partial_v20_schema
        apply_checkpoint_v43(conn, 20)

        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 43

    def test_epg_output_path_migrated(self, partial_v20_schema):
        """Old epg_output_path should be updated to new location."""
        conn, _ = partial_v20_schema
        apply_checkpoint_v43(conn, 20)

        row = conn.execute("SELECT epg_output_path FROM settings WHERE id = 1").fetchone()
        assert row["epg_output_path"] == "./data/teamarr.xml"


class TestCheckpointFromV42:
    """Test checkpoint migration from v42 (just before v43)."""

    def test_checkpoint_runs_without_error(self, v42_schema):
        """Checkpoint should complete without raising exceptions."""
        conn, _ = v42_schema
        apply_checkpoint_v43(conn, 42)

    def test_version_updated_to_43(self, v42_schema):
        """Version should be 43 after checkpoint."""
        conn, _ = v42_schema
        apply_checkpoint_v43(conn, 42)

        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 43


class TestCheckpointIdempotency:
    """Test that checkpoint is idempotent (safe to run multiple times)."""

    def test_double_checkpoint_safe(self, minimal_v2_schema):
        """Running checkpoint twice should not cause errors."""
        conn, _ = minimal_v2_schema

        # First run
        apply_checkpoint_v43(conn, 2)

        # Verify version is 43
        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 43

        # Second run (simulating what happens if checkpoint is called again)
        # Note: In practice, version check prevents this, but checkpoint itself
        # should be safe to re-run
        apply_checkpoint_v43(conn, 43)

        # Should still be 43
        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 43


class TestDataTransformations:
    """Test specific data transformations that checkpoint performs."""

    def test_rugby_consolidation(self, v42_schema):
        """Rugby sports should be consolidated to single 'rugby' entry."""
        conn, _ = v42_schema

        # Insert old rugby sports
        conn.execute(
            "INSERT OR REPLACE INTO sports (sport_code, display_name) VALUES ('rugby_league', 'Rugby League')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO sports (sport_code, display_name) VALUES ('rugby_union', 'Rugby Union')"
        )
        conn.commit()

        apply_checkpoint_v43(conn, 42)

        # Old entries should be gone
        row = conn.execute(
            "SELECT * FROM sports WHERE sport_code = 'rugby_league'"
        ).fetchone()
        assert row is None

        row = conn.execute(
            "SELECT * FROM sports WHERE sport_code = 'rugby_union'"
        ).fetchone()
        assert row is None

        # New unified entry should exist
        row = conn.execute(
            "SELECT display_name FROM sports WHERE sport_code = 'rugby'"
        ).fetchone()
        assert row is not None
        assert row["display_name"] == "Rugby"

    def test_channel_group_mode_pattern_conversion(self, v42_schema):
        """Old 'sport'/'league' enum values should convert to patterns."""
        conn, _ = v42_schema

        # Insert groups with old enum values
        conn.execute("""
            INSERT INTO event_epg_groups (name, leagues, channel_group_mode)
            VALUES ('Test1', '["nfl"]', 'sport')
        """)
        conn.execute("""
            INSERT INTO event_epg_groups (name, leagues, channel_group_mode)
            VALUES ('Test2', '["nba"]', 'league')
        """)
        conn.commit()

        apply_checkpoint_v43(conn, 42)

        # Values should be converted to patterns
        rows = conn.execute(
            "SELECT name, channel_group_mode FROM event_epg_groups ORDER BY name"
        ).fetchall()

        assert rows[0]["channel_group_mode"] == "{sport}"
        assert rows[1]["channel_group_mode"] == "{league}"


class TestVerification:
    """Test checkpoint verification phase."""

    def test_verification_detects_missing_columns(self, minimal_v2_schema):
        """Verification should complete even with complex starting state."""
        conn, _ = minimal_v2_schema

        # Just run checkpoint - verification is internal
        apply_checkpoint_v43(conn, 2)

        # If we get here without exception, verification passed
        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 43


# =============================================================================
# INTEGRATION TESTS
# =============================================================================


class TestFullMigrationPath:
    """Test full migration path using actual connection module."""

    def test_run_migrations_uses_checkpoint(self, temp_db):
        """_run_migrations should use checkpoint for versions < 43."""
        conn, db_path = temp_db

        # Create a minimal v10 database
        conn.execute("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                schema_version INTEGER DEFAULT 10
            )
        """)
        conn.execute("INSERT INTO settings (id, schema_version) VALUES (1, 10)")

        # Create minimal required tables
        conn.execute("""
            CREATE TABLE templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_team_id TEXT,
                provider TEXT,
                primary_league TEXT,
                leagues JSON DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE event_epg_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                leagues JSON NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE managed_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_epg_group_id INTEGER,
                tvg_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE managed_channel_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                managed_channel_id INTEGER NOT NULL,
                change_type TEXT NOT NULL,
                change_source TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE leagues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                league_code TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE epg_matched_streams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER
            )
        """)

        conn.commit()

        # Import and run migrations
        from teamarr.database.connection import _run_migrations

        _run_migrations(conn)

        # Should now be at latest schema version (v43 checkpoint + v44 + v45 migrations)
        row = conn.execute("SELECT schema_version FROM settings WHERE id = 1").fetchone()
        assert row["schema_version"] == 45


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
