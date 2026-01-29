"""Seed database with distributed TSDB cache.

On first run (or when cache is empty), seeds the team_cache and league_cache
tables from the pre-generated tsdb_seed.json file. This provides complete
TSDB team data without requiring users to have a premium API key.

The seed file is generated using a premium key and distributed with the app.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to seed file (relative to project root)
SEED_FILE = Path(__file__).parent.parent.parent / "data" / "tsdb_seed.json"


def load_tsdb_seed() -> dict | None:
    """Load TSDB seed data from file.

    Returns:
        Dict with 'leagues' and 'teams' lists, or None if unavailable
    """
    if not SEED_FILE.exists():
        logger.debug("[SEED] TSDB seed file not found: %s", SEED_FILE)
        return None

    try:
        with open(SEED_FILE) as f:
            data = json.load(f)
        # Add provider field to teams if missing (for merge compatibility)
        for team in data.get("teams", []):
            if "provider" not in team:
                team["provider"] = "tsdb"
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.error("[SEED] Failed to read TSDB seed file: %s", e)
        return None


def seed_tsdb_cache(conn) -> dict:
    """Seed TSDB cache from distributed seed file.

    Args:
        conn: Database connection

    Returns:
        Dict with seeding results
    """
    if not SEED_FILE.exists():
        logger.warning("[SEED] TSDB seed file not found: %s", SEED_FILE)
        return {"seeded": False, "reason": "seed_file_missing"}

    try:
        with open(SEED_FILE) as f:
            seed_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("[SEED] Failed to read TSDB seed file: %s", e)
        return {"seeded": False, "reason": "seed_file_error", "error": str(e)}

    cursor = conn.cursor()
    now = datetime.utcnow().isoformat() + "Z"

    # Seed leagues
    leagues_added = 0
    for league in seed_data.get("leagues", []):
        cursor.execute(
            """
            INSERT OR IGNORE INTO league_cache
            (league_slug, provider, league_name, sport, logo_url, team_count, last_refreshed)
            VALUES (?, 'tsdb', ?, ?, NULL, ?, ?)
            """,
            (
                league["code"],
                league.get("provider_league_name"),
                league.get("sport"),
                league.get("team_count", 0),
                now,
            ),
        )
        if cursor.rowcount > 0:
            leagues_added += 1

    # Seed teams
    teams_added = 0
    for team in seed_data.get("teams", []):
        cursor.execute(
            """
            INSERT OR IGNORE INTO team_cache
            (team_name, team_abbrev, team_short_name, provider, provider_team_id,
             league, sport, logo_url, last_seen)
            VALUES (?, ?, NULL, 'tsdb', ?, ?, ?, ?, ?)
            """,
            (
                team["team_name"],
                team.get("team_abbrev"),
                team["provider_team_id"],
                team["league"],
                team.get("sport"),
                team.get("logo_url"),
                now,
            ),
        )
        if cursor.rowcount > 0:
            teams_added += 1

    # Update cached_team_count in leagues table
    cursor.execute(
        """
        UPDATE leagues SET cached_team_count = (
            SELECT COUNT(*) FROM team_cache
            WHERE team_cache.league = leagues.league_code
            AND team_cache.provider = 'tsdb'
        ), last_cache_refresh = ?
        WHERE provider = 'tsdb'
        """,
        (now,),
    )

    logger.info(
        "[SEED] TSDB cache seeded: %d leagues, %d teams (from %s)",
        leagues_added,
        teams_added,
        seed_data.get("generated_at", "unknown"),
    )

    return {
        "seeded": True,
        "leagues_added": leagues_added,
        "teams_added": teams_added,
        "seed_generated_at": seed_data.get("generated_at"),
    }


def should_seed_tsdb_cache(conn) -> bool:
    """Check if TSDB cache needs seeding.

    Returns True if:
    - No TSDB teams in cache, OR
    - TSDB team count is significantly lower than seed file

    Args:
        conn: Database connection

    Returns:
        True if seeding is recommended
    """
    cursor = conn.cursor()

    # Check current TSDB team count
    cursor.execute("SELECT COUNT(*) FROM team_cache WHERE provider = 'tsdb'")
    current_count = cursor.fetchone()[0]

    if current_count == 0:
        return True

    # Check seed file team count
    if not SEED_FILE.exists():
        return False

    try:
        with open(SEED_FILE) as f:
            seed_data = json.load(f)
        seed_count = len(seed_data.get("teams", []))

        # Seed if we have significantly fewer teams than the seed file
        # (accounts for free tier limitations)
        if current_count < seed_count * 0.8:
            logger.info(
                "[SEED] TSDB cache has %d teams, seed has %d. Recommending re-seed.",
                current_count,
                seed_count,
            )
            return True

    except (OSError, json.JSONDecodeError):
        pass

    return False


def seed_if_needed(conn) -> dict | None:
    """Seed TSDB cache if needed.

    Args:
        conn: Database connection

    Returns:
        Seeding result dict, or None if not needed
    """
    if should_seed_tsdb_cache(conn):
        return seed_tsdb_cache(conn)
    return None
