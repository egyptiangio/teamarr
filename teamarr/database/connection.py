"""Database connection management.

Simple SQLite connection handling with schema initialization.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# Default database path
DEFAULT_DB_PATH = Path("./teamarr.db")

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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
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

    Args:
        db_path: Path to database file. Uses DEFAULT_DB_PATH if not specified.
    """
    schema_sql = SCHEMA_PATH.read_text()

    with get_db(db_path) as conn:
        conn.executescript(schema_sql)


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
