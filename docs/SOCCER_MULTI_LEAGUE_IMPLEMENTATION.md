# Soccer Multi-League Implementation Guide

## Complete Technical Specification

**Version:** 1.0
**Last Updated:** November 30, 2024
**Status:** Implementation Ready

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State Analysis](#2-current-state-analysis)
3. [Target Architecture](#3-target-architecture)
4. [Database Schema](#4-database-schema)
5. [New Module: soccer_multi_league.py](#5-new-module-soccer_multi_leaguepy)
6. [Integration Points](#6-integration-points)
7. [Migration Pathway](#7-migration-pathway)
8. [UI/UX Changes](#8-uiux-changes)
9. [EPG Generation Changes](#9-epg-generation-changes)
10. [Edge Cases & Error Handling](#10-edge-cases--error-handling)
11. [Settings & Configuration](#11-settings--configuration)
12. [Testing Plan](#12-testing-plan)
13. [Implementation Checklist](#13-implementation-checklist)

---

## 1. Executive Summary

### The Problem
Soccer teams play in multiple competitions simultaneously:
- Liverpool plays in: Premier League, FA Cup, League Cup, Champions League, Community Shield, Friendlies
- ESPN has no "all competitions" endpoint - must query each league separately
- Current Teamarr implementation only queries the team's default league, missing cup/European games

### The Solution
**Weekly reverse-lookup cache:**
1. Fetch teams from ALL 244 ESPN soccer leagues (~5 seconds)
2. Build map: `team_id → [list of league slugs]`
3. Store in SQLite for instant lookups
4. EPG generation queries only relevant leagues per team
5. No hardcoding - all discovery from ESPN API

### Key Metrics (Tested)
| Metric | Value |
|--------|-------|
| Total ESPN soccer leagues | 246 |
| After filtering junk | 244 |
| Time to build full cache | ~5 seconds |
| Unique teams indexed | 3,413 |
| Teams in multiple leagues | 1,255 |
| Max leagues per team | 7 |

---

## 2. Current State Analysis

### 2.1 Current Database Schema

**teams table:**
```sql
CREATE TABLE teams (
    id INTEGER PRIMARY KEY,
    espn_team_id TEXT NOT NULL,      -- "364" for Liverpool
    league TEXT NOT NULL,             -- "eng.1" (single league!)
    sport TEXT NOT NULL,              -- "soccer"
    team_name TEXT NOT NULL,
    template_id INTEGER,
    active BOOLEAN DEFAULT 1,
    -- ... other fields
);
```

**league_config table:**
```sql
CREATE TABLE league_config (
    league_code TEXT NOT NULL UNIQUE,  -- "epl", "mls"
    league_name TEXT NOT NULL,          -- "English Premier League"
    sport TEXT NOT NULL,                -- "soccer"
    api_path TEXT NOT NULL,             -- "soccer/eng.1"
    logo_url TEXT,
    -- ... other fields
);
-- Contains ~17 curated leagues (user-facing dropdown)
```

### 2.2 Current EPG Generation Flow

```
EPGOrchestrator.generate_epg()
  └── _process_team_schedule(team)
        └── espn_client.get_team_schedule(sport, league, team_id)
              └── Fetches from SINGLE league only!
```

**Problem:** Liverpool in `eng.1` only gets Premier League matches. Champions League, FA Cup, etc. are missed.

### 2.3 Current Files Structure

```
teamarr/
├── app.py                      # Routes, scheduler, EPG orchestration
├── database/
│   ├── __init__.py             # All DB functions
│   └── schema.sql              # Table definitions
├── epg/
│   ├── orchestrator.py         # Team-based EPG generation
│   ├── league_config.py        # SoccerCompat class, is_soccer_league()
│   ├── template_engine.py      # Variable substitution
│   └── ...
├── api/
│   └── espn_client.py          # ESPN API wrapper
└── templates/
    └── ...                     # Jinja2 templates
```

---

## 3. Target Architecture

### 3.1 New Components

```
teamarr/
├── epg/
│   └── soccer_multi_league.py   # NEW: Cache management + multi-league lookups
├── database/
│   ├── __init__.py              # MODIFIED: Add cache helper functions
│   └── schema.sql               # MODIFIED: Add cache tables
└── app.py                       # MODIFIED: Scheduler integration, migration
```

### 3.2 Data Flow After Implementation

```
┌─────────────────────────────────────────────────────────────────┐
│                    WEEKLY CACHE REFRESH                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. Fetch all 244 league slugs from ESPN                        │
│  2. For each league: fetch all teams (parallel, 50 threads)     │
│  3. Build reverse map: team_id → [league slugs]                 │
│  4. Store in soccer_team_leagues table                          │
│  5. Store league metadata in soccer_leagues_cache table         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    EPG GENERATION (per team)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. Check if team is soccer: is_soccer_league(team.league)      │
│  2. If soccer: lookup leagues from soccer_team_leagues cache    │
│     → Liverpool (364) → ['eng.1', 'uefa.champions', 'eng.fa'..] │
│  3. For each league: fetch schedule                             │
│  4. Merge & dedupe events by ESPN event ID                      │
│  5. Generate EPG entries (with correct league icon per event!)  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Database Schema

### 4.1 New Tables

Add to `database/schema.sql`:

```sql
-- =============================================================================
-- SOCCER TEAM LEAGUES CACHE
-- Weekly cache mapping team_id → leagues they play in
-- =============================================================================

CREATE TABLE IF NOT EXISTS soccer_team_leagues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Team-League Mapping
    espn_team_id TEXT NOT NULL,              -- "364" for Liverpool
    league_slug TEXT NOT NULL,               -- "eng.1", "uefa.champions"

    -- Team Metadata (stable, rarely changes)
    team_name TEXT,                          -- "Liverpool"
    team_type TEXT,                          -- "club" or "national"
    default_league TEXT,                     -- "eng.1" (from ESPN defaultLeague)

    -- Cache Metadata
    last_seen TEXT,                          -- ISO datetime when last seen in this league

    UNIQUE(espn_team_id, league_slug)
);

CREATE INDEX IF NOT EXISTS idx_stl_team ON soccer_team_leagues(espn_team_id);
CREATE INDEX IF NOT EXISTS idx_stl_league ON soccer_team_leagues(league_slug);

-- =============================================================================
-- SOCCER LEAGUES CACHE
-- Metadata about each league (slug → name, category, logo)
-- =============================================================================

CREATE TABLE IF NOT EXISTS soccer_leagues_cache (
    league_slug TEXT PRIMARY KEY,            -- "eng.1", "uefa.champions"
    league_name TEXT,                        -- "English Premier League"
    league_abbrev TEXT,                      -- "EPL"
    league_category TEXT,                    -- "domestic", "domestic_cup", "continental_club", etc.
    league_logo_url TEXT,                    -- URL to league logo
    team_count INTEGER,                      -- Number of teams in league
    last_seen TEXT                           -- ISO datetime
);

-- =============================================================================
-- SOCCER CACHE METADATA
-- Tracks cache refresh status (single row)
-- =============================================================================

CREATE TABLE IF NOT EXISTS soccer_cache_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_full_refresh TEXT,                  -- ISO datetime of last refresh
    leagues_processed INTEGER,               -- 244
    teams_indexed INTEGER,                   -- 3413
    refresh_duration_seconds REAL,           -- 4.9
    next_scheduled_refresh TEXT              -- ISO datetime of next scheduled
);

INSERT OR IGNORE INTO soccer_cache_meta (id) VALUES (1);
```

### 4.2 Settings Table Addition

Add to `settings` table:

```sql
-- Soccer multi-league cache settings
ALTER TABLE settings ADD COLUMN soccer_cache_refresh_frequency TEXT DEFAULT 'weekly';
-- Options: 'daily', 'every_3_days', 'weekly', 'manual'
```

### 4.3 Migration in database/__init__.py

Add to `run_migrations()`:

```python
# =============================================================================
# 7. SOCCER MULTI-LEAGUE CACHE TABLES
# =============================================================================

# 7a. soccer_team_leagues table
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
        print("  ✅ Created table: soccer_team_leagues")
    except Exception as e:
        print(f"  ⚠️ Could not create soccer_team_leagues table: {e}")
    conn.commit()

# 7b. soccer_leagues_cache table
if not table_exists("soccer_leagues_cache"):
    try:
        cursor.execute("""
            CREATE TABLE soccer_leagues_cache (
                league_slug TEXT PRIMARY KEY,
                league_name TEXT,
                league_abbrev TEXT,
                league_category TEXT,
                league_logo_url TEXT,
                team_count INTEGER,
                last_seen TEXT
            )
        """)
        migrations_run += 1
        print("  ✅ Created table: soccer_leagues_cache")
    except Exception as e:
        print(f"  ⚠️ Could not create soccer_leagues_cache table: {e}")
    conn.commit()

# 7c. soccer_cache_meta table
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
        print("  ✅ Created table: soccer_cache_meta")
    except Exception as e:
        print(f"  ⚠️ Could not create soccer_cache_meta table: {e}")
    conn.commit()

# 7d. Settings column for cache frequency
add_columns_if_missing("settings", [
    ("soccer_cache_refresh_frequency", "TEXT DEFAULT 'weekly'"),
])
```

---

## 5. New Module: soccer_multi_league.py

Create `epg/soccer_multi_league.py`:

```python
"""
Soccer Multi-League Support

Provides reverse-lookup cache for soccer teams to find all leagues they play in.
Solves the problem of soccer teams playing in multiple competitions simultaneously.

Usage:
    from epg.soccer_multi_league import SoccerMultiLeague

    # Get leagues for a team
    leagues = SoccerMultiLeague.get_team_leagues("364")  # Liverpool
    # Returns: ['eng.1', 'uefa.champions', 'eng.fa', 'eng.league_cup', ...]

    # Refresh the cache
    SoccerMultiLeague.refresh_cache()
"""

import requests
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from database import get_connection, db_fetch_all, db_fetch_one, db_execute
from utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# ESPN API endpoints
ESPN_LEAGUES_URL = "https://sports.core.api.espn.com/v2/sports/soccer/leagues?limit=500"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams"
ESPN_LEAGUE_DETAIL_URL = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/{slug}"

# League slugs to skip (junk leagues)
SKIP_LEAGUE_SLUGS = {'nonfifa'}
SKIP_LEAGUE_PATTERNS = ['not_used']

# League category detection patterns
LEAGUE_CATEGORY_PATTERNS = {
    'domestic': [
        r'^[a-z]{3}\.[1-9]$',           # eng.1, esp.1, ger.1
        r'^[a-z]{3}\.[1-9][0-9]?$',     # eng.10 (if exists)
    ],
    'domestic_cup': [
        'fa', 'copa', 'cup', 'pokal', 'coupe', 'coppa',
        'league_cup', 'charity', 'shield', 'supercup', 'super_cup',
    ],
    'continental_club': [
        'uefa.champions', 'uefa.europa', 'uefa.europa.conf',
        'conmebol.libertadores', 'conmebol.sudamericana',
        'concacaf.champions', 'afc.champions', 'caf.champions',
    ],
    'continental_national': [
        'uefa.euro', 'uefa.nations', 'conmebol.america',
        'concacaf.gold', 'concacaf.nations', 'afc.asian_cup',
        'caf.nations', 'ofc.nations',
    ],
    'world_club': [
        'fifa.cwc', 'fifa.club',
    ],
    'world_national': [
        'fifa.world', 'fifa.worldq', 'fifa.confederations',
    ],
    'friendly': [
        'friendly', 'world_challenge',
    ],
    'qualifier': [
        'qual', 'euroq', 'worldq',
    ],
}

# Thread pool size for parallel fetching
MAX_WORKERS = 50


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class LeagueInfo:
    """League metadata from cache."""
    slug: str
    name: str
    abbrev: str
    category: str
    logo_url: str
    team_count: int


@dataclass
class TeamLeagueInfo:
    """Team's league membership info."""
    team_id: str
    team_name: str
    team_type: str  # 'club' or 'national'
    default_league: str
    leagues: List[str]


@dataclass
class CacheStats:
    """Cache refresh statistics."""
    last_refresh: Optional[datetime]
    leagues_processed: int
    teams_indexed: int
    refresh_duration: float
    is_stale: bool
    staleness_days: int


# =============================================================================
# MAIN CLASS
# =============================================================================

class SoccerMultiLeague:
    """
    Manages the soccer multi-league cache.

    All methods are static/class methods - no instance needed.
    """

    # ==========================================================================
    # PUBLIC API: Cache Queries
    # ==========================================================================

    @classmethod
    def get_team_leagues(cls, espn_team_id: str) -> List[str]:
        """
        Get all league slugs for a soccer team.

        Args:
            espn_team_id: ESPN team ID (e.g., "364" for Liverpool)

        Returns:
            List of league slugs (e.g., ['eng.1', 'uefa.champions', 'eng.fa'])
            Returns empty list if team not found in cache.
        """
        rows = db_fetch_all(
            "SELECT league_slug FROM soccer_team_leagues WHERE espn_team_id = ?",
            (str(espn_team_id),)
        )
        return [row['league_slug'] for row in rows]

    @classmethod
    def get_team_info(cls, espn_team_id: str) -> Optional[TeamLeagueInfo]:
        """
        Get full team info including all leagues.

        Args:
            espn_team_id: ESPN team ID

        Returns:
            TeamLeagueInfo dataclass or None if not found
        """
        rows = db_fetch_all(
            """SELECT league_slug, team_name, team_type, default_league
               FROM soccer_team_leagues WHERE espn_team_id = ?""",
            (str(espn_team_id),)
        )

        if not rows:
            return None

        first = rows[0]
        return TeamLeagueInfo(
            team_id=str(espn_team_id),
            team_name=first['team_name'] or '',
            team_type=first['team_type'] or 'club',
            default_league=first['default_league'] or '',
            leagues=[row['league_slug'] for row in rows]
        )

    @classmethod
    def get_league_info(cls, league_slug: str) -> Optional[LeagueInfo]:
        """
        Get league metadata by slug.

        Args:
            league_slug: League slug (e.g., "eng.1", "uefa.champions")

        Returns:
            LeagueInfo dataclass or None if not found
        """
        row = db_fetch_one(
            "SELECT * FROM soccer_leagues_cache WHERE league_slug = ?",
            (league_slug,)
        )

        if not row:
            return None

        return LeagueInfo(
            slug=row['league_slug'],
            name=row['league_name'] or league_slug,
            abbrev=row['league_abbrev'] or '',
            category=row['league_category'] or 'unknown',
            logo_url=row['league_logo_url'] or '',
            team_count=row['team_count'] or 0
        )

    @classmethod
    def get_league_name(cls, league_slug: str) -> str:
        """
        Get human-readable league name.

        Args:
            league_slug: League slug (e.g., "eng.1")

        Returns:
            League name (e.g., "English Premier League") or slug if not found
        """
        row = db_fetch_one(
            "SELECT league_name FROM soccer_leagues_cache WHERE league_slug = ?",
            (league_slug,)
        )
        return row['league_name'] if row and row['league_name'] else league_slug

    @classmethod
    def get_league_logo(cls, league_slug: str) -> Optional[str]:
        """
        Get league logo URL.

        Args:
            league_slug: League slug

        Returns:
            Logo URL or None if not found
        """
        row = db_fetch_one(
            "SELECT league_logo_url FROM soccer_leagues_cache WHERE league_slug = ?",
            (league_slug,)
        )
        return row['league_logo_url'] if row else None

    @classmethod
    def get_cache_stats(cls) -> CacheStats:
        """
        Get cache status and statistics.

        Returns:
            CacheStats dataclass with refresh info
        """
        row = db_fetch_one("SELECT * FROM soccer_cache_meta WHERE id = 1")

        last_refresh = None
        staleness_days = 999

        if row and row['last_full_refresh']:
            try:
                last_refresh = datetime.fromisoformat(row['last_full_refresh'].replace('Z', '+00:00'))
                staleness_days = (datetime.now(last_refresh.tzinfo) - last_refresh).days
            except:
                pass

        return CacheStats(
            last_refresh=last_refresh,
            leagues_processed=row['leagues_processed'] or 0 if row else 0,
            teams_indexed=row['teams_indexed'] or 0 if row else 0,
            refresh_duration=row['refresh_duration_seconds'] or 0 if row else 0,
            is_stale=staleness_days > 7,
            staleness_days=staleness_days
        )

    @classmethod
    def is_cache_empty(cls) -> bool:
        """Check if cache has any data."""
        row = db_fetch_one("SELECT COUNT(*) as cnt FROM soccer_team_leagues")
        return row['cnt'] == 0 if row else True

    # ==========================================================================
    # PUBLIC API: Cache Refresh
    # ==========================================================================

    @classmethod
    def refresh_cache(cls, progress_callback=None) -> Dict[str, Any]:
        """
        Refresh the entire soccer league cache.

        Fetches teams from all 244 ESPN soccer leagues and builds
        the reverse lookup map. Takes ~5 seconds with 50 threads.

        Args:
            progress_callback: Optional callback(message, percent) for progress updates

        Returns:
            Dict with refresh statistics:
            {
                'success': True/False,
                'leagues_processed': 244,
                'teams_indexed': 3413,
                'duration_seconds': 4.9,
                'error': None or error message
            }
        """
        start_time = time.time()

        def report(msg, pct):
            logger.info(f"Soccer cache refresh: {msg}")
            if progress_callback:
                progress_callback(msg, pct)

        try:
            report("Fetching league list from ESPN...", 5)

            # Step 1: Get all league slugs
            league_slugs = cls._fetch_all_league_slugs()
            if not league_slugs:
                return {'success': False, 'error': 'Failed to fetch league list'}

            report(f"Found {len(league_slugs)} leagues, fetching teams...", 15)

            # Step 2: Fetch teams from all leagues in parallel
            team_to_leagues, league_metadata = cls._fetch_all_teams(
                league_slugs,
                lambda msg, pct: report(msg, 15 + int(pct * 0.7))  # 15-85%
            )

            report(f"Indexed {len(team_to_leagues)} teams, saving to database...", 90)

            # Step 3: Save to database
            cls._save_cache(team_to_leagues, league_metadata)

            # Step 4: Update metadata
            duration = time.time() - start_time
            cls._update_cache_meta(len(league_slugs), len(team_to_leagues), duration)

            report(f"Cache refresh complete: {len(team_to_leagues)} teams in {duration:.1f}s", 100)

            return {
                'success': True,
                'leagues_processed': len(league_slugs),
                'teams_indexed': len(team_to_leagues),
                'duration_seconds': duration,
                'error': None
            }

        except Exception as e:
            logger.error(f"Soccer cache refresh failed: {e}")
            return {
                'success': False,
                'leagues_processed': 0,
                'teams_indexed': 0,
                'duration_seconds': time.time() - start_time,
                'error': str(e)
            }

    @classmethod
    def refresh_if_needed(cls, max_age_days: int = 7) -> bool:
        """
        Refresh cache if it's older than max_age_days.

        Args:
            max_age_days: Maximum cache age before refresh

        Returns:
            True if refresh was performed, False otherwise
        """
        stats = cls.get_cache_stats()

        if stats.staleness_days >= max_age_days or cls.is_cache_empty():
            logger.info(f"Soccer cache is {stats.staleness_days} days old, refreshing...")
            result = cls.refresh_cache()
            return result['success']

        return False

    # ==========================================================================
    # PRIVATE: Fetching Logic
    # ==========================================================================

    @classmethod
    def _fetch_all_league_slugs(cls) -> List[str]:
        """Fetch all soccer league slugs from ESPN."""
        try:
            resp = requests.get(ESPN_LEAGUES_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            league_refs = data.get('items', [])
            slugs = []

            # Fetch each league's metadata to get slug
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(cls._fetch_league_slug, ref['$ref']): ref
                    for ref in league_refs
                }

                for future in as_completed(futures):
                    slug = future.result()
                    if slug and cls._should_include_league(slug):
                        slugs.append(slug)

            logger.info(f"Found {len(slugs)} valid soccer league slugs")
            return slugs

        except Exception as e:
            logger.error(f"Failed to fetch league slugs: {e}")
            return []

    @classmethod
    def _fetch_league_slug(cls, ref_url: str) -> Optional[str]:
        """Fetch a single league's slug from its ref URL."""
        try:
            resp = requests.get(ref_url, timeout=10)
            if resp.status_code == 200:
                return resp.json().get('slug')
        except:
            pass
        return None

    @classmethod
    def _should_include_league(cls, slug: str) -> bool:
        """Check if league should be included (filter junk)."""
        if slug in SKIP_LEAGUE_SLUGS:
            return False
        for pattern in SKIP_LEAGUE_PATTERNS:
            if pattern in slug:
                return False
        return True

    @classmethod
    def _fetch_all_teams(
        cls,
        league_slugs: List[str],
        progress_callback=None
    ) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
        """
        Fetch teams from all leagues in parallel.

        Returns:
            Tuple of (team_to_leagues dict, league_metadata dict)
        """
        team_to_leagues = {}  # team_id -> {name, type, default_league, leagues: []}
        league_metadata = {}  # slug -> {name, abbrev, logo, team_count}

        completed = 0
        total = len(league_slugs)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(cls._fetch_league_teams, slug): slug
                for slug in league_slugs
            }

            for future in as_completed(futures):
                slug = futures[future]
                completed += 1

                if progress_callback and completed % 20 == 0:
                    pct = (completed / total) * 100
                    progress_callback(f"Processed {completed}/{total} leagues", pct)

                try:
                    result = future.result()
                    if result:
                        league_meta, teams = result

                        # Store league metadata
                        league_metadata[slug] = league_meta

                        # Add teams to reverse lookup
                        for team in teams:
                            team_id = str(team['id'])

                            if team_id not in team_to_leagues:
                                team_to_leagues[team_id] = {
                                    'name': team['name'],
                                    'type': team['type'],
                                    'default_league': team.get('default_league', ''),
                                    'leagues': []
                                }

                            team_to_leagues[team_id]['leagues'].append(slug)

                except Exception as e:
                    logger.warning(f"Error processing league {slug}: {e}")

        return team_to_leagues, league_metadata

    @classmethod
    def _fetch_league_teams(cls, slug: str) -> Optional[Tuple[Dict, List[Dict]]]:
        """
        Fetch all teams from a single league.

        Returns:
            Tuple of (league_metadata, teams_list) or None on error
        """
        url = ESPN_TEAMS_URL.format(slug=slug)

        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None

            data = resp.json()

            # Extract league metadata
            league_data = data.get('sports', [{}])[0].get('leagues', [{}])[0]
            league_meta = {
                'name': league_data.get('name', ''),
                'abbrev': league_data.get('abbreviation', ''),
                'logo': league_data.get('logos', [{}])[0].get('href', '') if league_data.get('logos') else '',
                'team_count': 0
            }

            # Extract teams
            teams_raw = league_data.get('teams', [])
            teams = []

            for t in teams_raw:
                team_data = t.get('team', {})
                if not team_data.get('id'):
                    continue

                # Detect team type (club vs national)
                name = team_data.get('displayName', team_data.get('name', ''))
                location = team_data.get('location', '')
                team_type = cls._detect_team_type(name, location)

                teams.append({
                    'id': team_data['id'],
                    'name': name,
                    'type': team_type,
                    'default_league': team_data.get('defaultLeague', {}).get('slug', slug),
                })

            league_meta['team_count'] = len(teams)

            return (league_meta, teams)

        except Exception as e:
            logger.debug(f"Failed to fetch teams from {slug}: {e}")
            return None

    @classmethod
    def _detect_team_type(cls, name: str, location: str) -> str:
        """
        Detect if team is club or national.

        National teams: location == name (both are the country)
        Club teams: location is city, name is team name

        Examples:
            - Liverpool: location="Liverpool", name="Liverpool" -> hmm, need to check default league
            - England: location="England", name="England" -> national
            - Manchester United: location="Manchester", name="Manchester United" -> club
        """
        # Normalize for comparison
        name_lower = name.lower().strip()
        location_lower = location.lower().strip()

        # If location equals name exactly, likely national team
        if name_lower == location_lower:
            # But check for common club cases where city == club name
            # e.g., "Liverpool" city and "Liverpool" club
            club_city_names = {
                'liverpool', 'chelsea', 'arsenal', 'everton',
                'brighton', 'fulham', 'brentford',
            }
            if name_lower in club_city_names:
                return 'club'
            return 'national'

        return 'club'

    @classmethod
    def _categorize_league(cls, slug: str) -> str:
        """
        Categorize a league by its slug pattern.

        Returns category: domestic, domestic_cup, continental_club, etc.
        """
        slug_lower = slug.lower()

        for category, patterns in LEAGUE_CATEGORY_PATTERNS.items():
            for pattern in patterns:
                if pattern in slug_lower:
                    return category

        # Default to domestic for country.number pattern
        import re
        if re.match(r'^[a-z]{3}\.\d+$', slug_lower):
            return 'domestic'

        return 'other'

    # ==========================================================================
    # PRIVATE: Database Operations
    # ==========================================================================

    @classmethod
    def _save_cache(cls, team_to_leagues: Dict, league_metadata: Dict):
        """Save cache data to database."""
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat() + 'Z'

        try:
            # Clear old data
            cursor.execute("DELETE FROM soccer_team_leagues")
            cursor.execute("DELETE FROM soccer_leagues_cache")

            # Insert team-league mappings
            for team_id, info in team_to_leagues.items():
                for league_slug in info['leagues']:
                    cursor.execute("""
                        INSERT INTO soccer_team_leagues
                        (espn_team_id, league_slug, team_name, team_type, default_league, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        team_id,
                        league_slug,
                        info['name'],
                        info['type'],
                        info['default_league'],
                        now
                    ))

            # Insert league metadata
            for slug, meta in league_metadata.items():
                category = cls._categorize_league(slug)
                cursor.execute("""
                    INSERT INTO soccer_leagues_cache
                    (league_slug, league_name, league_abbrev, league_category, league_logo_url, team_count, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    slug,
                    meta['name'],
                    meta['abbrev'],
                    category,
                    meta['logo'],
                    meta['team_count'],
                    now
                ))

            conn.commit()
            logger.info(f"Saved {len(team_to_leagues)} teams and {len(league_metadata)} leagues to cache")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save cache: {e}")
            raise
        finally:
            conn.close()

    @classmethod
    def _update_cache_meta(cls, leagues: int, teams: int, duration: float):
        """Update cache metadata."""
        now = datetime.utcnow().isoformat() + 'Z'

        db_execute("""
            UPDATE soccer_cache_meta SET
                last_full_refresh = ?,
                leagues_processed = ?,
                teams_indexed = ?,
                refresh_duration_seconds = ?
            WHERE id = 1
        """, (now, leagues, teams, duration))


# =============================================================================
# HELPER FUNCTIONS (Module-level)
# =============================================================================

def get_soccer_team_leagues(espn_team_id: str) -> List[str]:
    """Convenience function for getting team's leagues."""
    return SoccerMultiLeague.get_team_leagues(espn_team_id)


def get_soccer_league_name(league_slug: str) -> str:
    """Convenience function for getting league name."""
    return SoccerMultiLeague.get_league_name(league_slug)


def get_soccer_league_logo(league_slug: str) -> Optional[str]:
    """Convenience function for getting league logo."""
    return SoccerMultiLeague.get_league_logo(league_slug)


def refresh_soccer_cache() -> Dict[str, Any]:
    """Convenience function for refreshing cache."""
    return SoccerMultiLeague.refresh_cache()
```

---

## 6. Integration Points

### 6.1 Database Helper Functions

Add to `database/__init__.py`:

```python
# =============================================================================
# Soccer Multi-League Cache Functions
# =============================================================================

def get_soccer_team_leagues(espn_team_id: str) -> List[str]:
    """Get all league slugs for a soccer team from cache."""
    return db_fetch_all(
        "SELECT league_slug FROM soccer_team_leagues WHERE espn_team_id = ?",
        (str(espn_team_id),)
    )


def get_soccer_league_info(league_slug: str) -> Optional[Dict[str, Any]]:
    """Get league metadata from cache."""
    return db_fetch_one(
        "SELECT * FROM soccer_leagues_cache WHERE league_slug = ?",
        (league_slug,)
    )


def get_soccer_cache_stats() -> Dict[str, Any]:
    """Get cache metadata."""
    return db_fetch_one("SELECT * FROM soccer_cache_meta WHERE id = 1")


def is_soccer_cache_empty() -> bool:
    """Check if soccer cache has data."""
    row = db_fetch_one("SELECT COUNT(*) as cnt FROM soccer_team_leagues")
    return row['cnt'] == 0 if row else True
```

### 6.2 App.py Scheduler Integration

Add to `app.py`:

```python
from epg.soccer_multi_league import SoccerMultiLeague

def check_soccer_cache_refresh():
    """
    Check if soccer cache needs refresh based on settings.
    Called from scheduler loop.
    """
    settings = get_settings()
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
        logger.info("Soccer league cache refreshed by scheduler")


# In scheduler_loop():
def scheduler_loop():
    # ... existing code ...

    # Check soccer cache refresh (once per day at midnight)
    if current_minute == 0 and current_hour == 0:
        check_soccer_cache_refresh()
```

### 6.3 Startup Migration

Add to `app.py` initialization:

```python
def initialize_soccer_cache():
    """
    Initialize soccer cache on startup.
    Runs migration for existing soccer teams.
    """
    from epg.soccer_multi_league import SoccerMultiLeague
    from epg.league_config import is_soccer_league

    # Check if cache is empty
    if SoccerMultiLeague.is_cache_empty():
        logger.info("Soccer league cache is empty, performing initial build...")
        result = SoccerMultiLeague.refresh_cache()

        if result['success']:
            logger.info(f"✅ Built soccer cache: {result['teams_indexed']} teams across {result['leagues_processed']} leagues")
        else:
            logger.error(f"❌ Failed to build soccer cache: {result['error']}")
            return

    # Migrate existing soccer teams
    migrate_existing_soccer_teams()


def migrate_existing_soccer_teams():
    """
    Migrate existing soccer teams to multi-league system.
    Logs summary of teams found in cache.
    """
    from epg.soccer_multi_league import SoccerMultiLeague
    from epg.league_config import is_soccer_league

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Find all soccer teams
        soccer_teams = cursor.execute("""
            SELECT id, espn_team_id, team_name, league
            FROM teams
            WHERE sport = 'soccer' OR league LIKE '%.%'
        """).fetchall()

        if not soccer_teams:
            logger.info("No existing soccer teams to migrate")
            return

        migrated = 0
        missing = []

        for team in soccer_teams:
            team_id = team['espn_team_id']
            leagues = SoccerMultiLeague.get_team_leagues(team_id)

            if leagues:
                migrated += 1
                old_count = 1  # They had single league before
                new_count = len(leagues)

                if new_count > old_count:
                    logger.info(f"  ✅ {team['team_name']}: {old_count} → {new_count} leagues")
            else:
                missing.append(team['team_name'])

        # Log summary
        logger.info(f"Soccer migration complete: {migrated} teams migrated")
        if missing:
            logger.warning(f"  ⚠️ {len(missing)} teams not found in cache: {', '.join(missing[:5])}...")

    finally:
        conn.close()


# Call during app initialization
# Add to the startup sequence after database init
initialize_soccer_cache()
```

---

## 7. Migration Pathway

### 7.1 Migration Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      APP STARTUP                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. Run database migrations (create tables if missing)          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Check: Is soccer_team_leagues table empty?                   │
│    YES → Run full cache refresh (~5 seconds)                    │
│    NO  → Skip to step 3                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Find existing soccer teams in teams table                    │
│    WHERE sport = 'soccer' OR league LIKE '%.%'                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. For each team: lookup in cache, log migration                │
│    • Liverpool: 1 → 6 leagues ✅                                │
│    • Man City: 1 → 5 leagues ✅                                 │
│    • Unknown Team: not in cache ⚠️                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Log summary:                                                 │
│    "Soccer migration complete: 12 teams migrated"               │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 No Schema Changes to teams Table

The `teams` table does NOT need modification. The multi-league lookup is handled at EPG generation time by querying the cache. The `league` column remains as the team's "default" or "home" league.

---

## 8. UI/UX Changes

### 8.1 Team Import Flow

When importing a soccer team, show league membership from cache:

```
┌─────────────────────────────────────────────────────────────────┐
│  Import Team: Liverpool                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Liverpool appears in 6 competitions                            │
│  (as of Nov 30, 2024 - 2 days ago)                             │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ ⚽ eng.1         English Premier League                  │  │
│  │ 🏆 uefa.champions UEFA Champions League                  │  │
│  │ 🏆 eng.fa        FA Cup                                  │  │
│  │ 🏆 eng.league_cup Carabao Cup                           │  │
│  │ 🏆 eng.charity   Community Shield                        │  │
│  │ ⚽ club.friendly  Club Friendlies                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ⓘ EPG will include matches from all competitions              │
│                                                                 │
│  [Accept]        [Refresh Mappings]        [Cancel]            │
└─────────────────────────────────────────────────────────────────┘
```

**If cache is stale (>7 days):**
```
⚠️ League mappings are 14 days old. Consider refreshing.
[Refresh Now]
```

**If team not in cache:**
```
⚠️ Liverpool not found in league cache.
   This may be a new team or the cache needs refreshing.
[Refresh & Retry]  [Import Anyway (single league)]  [Cancel]
```

### 8.2 Settings Page Addition

Add to Settings page:

```
┌─────────────────────────────────────────────────────────────────┐
│  Soccer Multi-League Settings                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Cache Refresh Frequency:                                       │
│  ┌─────────────────────────────────────────────┐               │
│  │ ○ Daily                                      │               │
│  │ ○ Every 3 Days                              │               │
│  │ ● Weekly (recommended)                      │               │
│  │ ○ Manual Only                               │               │
│  └─────────────────────────────────────────────┘               │
│                                                                 │
│  Cache Status:                                                  │
│  • Last refresh: Nov 30, 2024 3:00 AM (2 days ago)             │
│  • Leagues indexed: 244                                         │
│  • Teams indexed: 3,413                                         │
│  • Refresh duration: 4.9 seconds                               │
│                                                                 │
│  [Refresh Now]                                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 8.3 API Endpoints

Add new endpoints:

```python
@app.route('/api/soccer/cache/status', methods=['GET'])
def get_soccer_cache_status():
    """Get soccer cache status and statistics."""
    stats = SoccerMultiLeague.get_cache_stats()
    return jsonify({
        'last_refresh': stats.last_refresh.isoformat() if stats.last_refresh else None,
        'leagues_processed': stats.leagues_processed,
        'teams_indexed': stats.teams_indexed,
        'refresh_duration_seconds': stats.refresh_duration,
        'is_stale': stats.is_stale,
        'staleness_days': stats.staleness_days
    })


@app.route('/api/soccer/cache/refresh', methods=['POST'])
def refresh_soccer_cache():
    """Manually trigger soccer cache refresh."""
    result = SoccerMultiLeague.refresh_cache()
    return jsonify(result)


@app.route('/api/soccer/team/<team_id>/leagues', methods=['GET'])
def get_team_leagues(team_id):
    """Get all leagues for a soccer team."""
    info = SoccerMultiLeague.get_team_info(team_id)
    if not info:
        return jsonify({'error': 'Team not found in cache'}), 404

    # Get full league info for each
    leagues_info = []
    for slug in info.leagues:
        league = SoccerMultiLeague.get_league_info(slug)
        if league:
            leagues_info.append({
                'slug': league.slug,
                'name': league.name,
                'category': league.category,
                'logo_url': league.logo_url
            })

    return jsonify({
        'team_id': info.team_id,
        'team_name': info.team_name,
        'team_type': info.team_type,
        'default_league': info.default_league,
        'leagues': leagues_info
    })
```

---

## 9. EPG Generation Changes

### 9.1 Modified Team Schedule Fetching

Update `epg/orchestrator.py`:

```python
from epg.league_config import is_soccer_league
from epg.soccer_multi_league import SoccerMultiLeague

def _process_team_schedule(self, team: Dict, template: Dict) -> List[Dict]:
    """Process a single team's schedule."""

    league = team.get('league', '')
    sport = team.get('sport', '')
    team_id = team.get('espn_team_id', '')

    # Check if this is a soccer team needing multi-league lookup
    if is_soccer_league(league):
        return self._process_soccer_team_schedule(team, template)
    else:
        return self._process_single_league_schedule(team, template)


def _process_soccer_team_schedule(self, team: Dict, template: Dict) -> List[Dict]:
    """
    Process soccer team schedule across ALL competitions.

    Uses the multi-league cache to find all leagues, then fetches
    and merges schedules from each.
    """
    team_id = team.get('espn_team_id', '')
    team_name = team.get('team_name', '')

    # Get leagues from cache
    leagues = SoccerMultiLeague.get_team_leagues(team_id)

    if not leagues:
        # Fallback to single league if not in cache
        logger.warning(f"Soccer team {team_name} ({team_id}) not in multi-league cache, using default league")
        leagues = [team.get('league', '')]

    logger.debug(f"Fetching {team_name} schedule from {len(leagues)} leagues: {leagues}")

    # Fetch schedules from all leagues
    all_events = []
    seen_event_ids = set()

    for league_slug in leagues:
        try:
            # Parse api_path for this league
            api_sport, api_league = 'soccer', league_slug

            schedule = self.espn_client.get_team_schedule(
                api_sport, api_league, team_id,
                days_ahead=60  # Extended for soccer fixtures
            )

            if schedule:
                events = self.espn_client.parse_schedule_events(
                    schedule,
                    days_ahead=self.days_ahead,
                    cutoff_past_datetime=self.epg_start_datetime
                )

                # Dedupe by event ID and track source league
                for event in events:
                    event_id = event.get('id')
                    if event_id and event_id not in seen_event_ids:
                        seen_event_ids.add(event_id)
                        # IMPORTANT: Track which league this event is from
                        event['_source_league'] = league_slug
                        all_events.append(event)

        except Exception as e:
            logger.warning(f"Error fetching {league_slug} schedule for {team_name}: {e}")

    # Sort by date
    all_events.sort(key=lambda e: e.get('date', ''))

    logger.info(f"Found {len(all_events)} events for {team_name} across {len(leagues)} leagues")

    # Process events into EPG entries
    return self._generate_team_epg_entries(team, template, all_events)
```

### 9.2 League-Specific Icons/Logos

**Critical:** Each EPG entry must show the icon for the SPECIFIC competition, not just the team's default league.

Update template engine to include league-specific variables:

```python
# In template_engine.py - add to event context

def build_event_context(event: Dict, team: Dict, ...) -> Dict:
    """Build template variable context for an event."""

    # Get source league from event (set during multi-league fetch)
    source_league = event.get('_source_league', team.get('league', ''))

    # Get league info from cache
    league_info = SoccerMultiLeague.get_league_info(source_league)

    context = {
        # ... existing variables ...

        # Competition-specific variables (NEW)
        'competition_name': league_info.name if league_info else source_league,
        'competition_slug': source_league,
        'competition_logo': league_info.logo_url if league_info else '',
        'competition_category': league_info.category if league_info else '',

        # For program art - competition logo takes precedence for soccer
        'event_logo': league_info.logo_url if league_info else team.get('team_logo_url', ''),
    }

    return context
```

**Template Usage:**
```
Title: {team_name} vs {opponent}
Subtitle: {competition_name}  ← Shows "UEFA Champions League" not "Premier League"
Art URL: {competition_logo}   ← Shows Champions League logo, not team logo
```

### 9.3 EPG Programme Generation

Ensure each `<programme>` element uses the correct competition metadata:

```python
def _create_programme_element(self, event: Dict, team: Dict, context: Dict) -> ET.Element:
    """Create XMLTV programme element."""

    programme = ET.Element('programme')

    # ... existing code ...

    # Icon element - use competition logo for soccer events
    if context.get('competition_logo'):
        icon = ET.SubElement(programme, 'icon')
        icon.set('src', context['competition_logo'])
    elif context.get('team_logo'):
        icon = ET.SubElement(programme, 'icon')
        icon.set('src', context['team_logo'])

    # Category can include competition
    category = ET.SubElement(programme, 'category')
    category.text = context.get('competition_name', 'Soccer')

    return programme
```

---

## 10. Edge Cases & Error Handling

### 10.1 Team Not in Cache

**Scenario:** User imports a team that isn't in the cache (new team, obscure league, cache stale).

**Solution:**
```python
def import_soccer_team(espn_team_id: str) -> Dict:
    """Import a soccer team with cache validation."""

    leagues = SoccerMultiLeague.get_team_leagues(espn_team_id)

    if not leagues:
        # Option 1: Refresh entire cache
        logger.info(f"Team {espn_team_id} not in cache, triggering refresh...")
        result = SoccerMultiLeague.refresh_cache()

        if result['success']:
            leagues = SoccerMultiLeague.get_team_leagues(espn_team_id)

        if not leagues:
            # Still not found - import with default league only
            return {
                'success': True,
                'warning': 'Team not found in multi-league cache. Using single league.',
                'leagues': []
            }

    return {
        'success': True,
        'leagues': leagues
    }
```

### 10.2 Stale Cache

**Scenario:** Cache is older than the configured refresh frequency.

**Solution:** Check staleness at multiple points:

1. **Scheduler:** Daily check at midnight
2. **Team Import:** Show warning if stale
3. **EPG Generation:** Log warning but proceed

```python
def check_cache_freshness() -> Dict:
    """Check if cache is fresh enough."""
    stats = SoccerMultiLeague.get_cache_stats()

    return {
        'is_fresh': not stats.is_stale,
        'days_old': stats.staleness_days,
        'last_refresh': stats.last_refresh,
        'recommendation': 'refresh' if stats.is_stale else 'ok'
    }
```

### 10.3 Mid-Season Cup Draws

**Scenario:** FA Cup draw happens mid-week, team now appears in `eng.fa` but cache is from last week.

**Solution:**
1. Cache refresh will catch it within the configured frequency
2. Manual "Refresh Now" button for immediate update
3. Import UI shows cache date so user knows if it's stale

### 10.4 Relegated/Promoted Teams

**Scenario:** Team relegated from Premier League to Championship.

**Solution:** Cache refresh naturally handles this - the team will appear in `eng.2` instead of `eng.1` after the new season starts.

### 10.5 API Failures During Cache Refresh

**Scenario:** ESPN API partially fails during refresh.

**Solution:**
```python
def refresh_cache(self, ...):
    # ... in the fetch loop ...

    errors = []
    for future in as_completed(futures):
        try:
            result = future.result()
            # ... process ...
        except Exception as e:
            errors.append(str(e))

    # Log errors but don't fail if most succeeded
    if errors and len(errors) < len(league_slugs) * 0.1:  # <10% failure
        logger.warning(f"Cache refresh completed with {len(errors)} errors")
    elif errors:
        logger.error(f"Cache refresh had significant failures: {len(errors)} errors")
        return {'success': False, 'error': f'{len(errors)} API failures'}

    return {'success': True, ...}
```

### 10.6 Empty Leagues

**Scenario:** Some leagues return 0 teams (inactive, off-season).

**Solution:** This is normal - 6 of 244 leagues are empty. Log but don't error.

---

## 11. Settings & Configuration

### 11.1 New Settings

Add to settings table:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `soccer_cache_refresh_frequency` | TEXT | 'weekly' | How often to auto-refresh: 'daily', 'every_3_days', 'weekly', 'manual' |

### 11.2 Settings UI

```html
<!-- In settings.html -->
<div class="setting-group">
    <h3>Soccer Multi-League</h3>

    <div class="setting-item">
        <label>Cache Refresh Frequency</label>
        <select name="soccer_cache_refresh_frequency">
            <option value="daily">Daily</option>
            <option value="every_3_days">Every 3 Days</option>
            <option value="weekly" selected>Weekly (Recommended)</option>
            <option value="manual">Manual Only</option>
        </select>
        <p class="help-text">How often to refresh the league mappings cache.</p>
    </div>

    <div class="setting-item">
        <label>Cache Status</label>
        <div id="soccer-cache-status">
            <!-- Populated by JS -->
        </div>
        <button onclick="refreshSoccerCache()">Refresh Now</button>
    </div>
</div>
```

---

## 12. Testing Plan

### 12.1 Unit Tests

```python
# tests/test_soccer_multi_league.py

def test_cache_refresh():
    """Test full cache refresh."""
    result = SoccerMultiLeague.refresh_cache()
    assert result['success']
    assert result['teams_indexed'] > 3000
    assert result['leagues_processed'] > 240


def test_team_lookup():
    """Test team league lookup."""
    # Liverpool
    leagues = SoccerMultiLeague.get_team_leagues("364")
    assert 'eng.1' in leagues
    assert len(leagues) >= 1


def test_league_categorization():
    """Test league category detection."""
    assert SoccerMultiLeague._categorize_league('eng.1') == 'domestic'
    assert SoccerMultiLeague._categorize_league('uefa.champions') == 'continental_club'
    assert SoccerMultiLeague._categorize_league('fifa.world') == 'world_national'
    assert SoccerMultiLeague._categorize_league('eng.fa') == 'domestic_cup'


def test_team_type_detection():
    """Test club vs national detection."""
    assert SoccerMultiLeague._detect_team_type('England', 'England') == 'national'
    assert SoccerMultiLeague._detect_team_type('Liverpool', 'Liverpool') == 'club'
    assert SoccerMultiLeague._detect_team_type('Manchester United', 'Manchester') == 'club'


def test_cache_staleness():
    """Test cache staleness detection."""
    stats = SoccerMultiLeague.get_cache_stats()
    assert hasattr(stats, 'is_stale')
    assert hasattr(stats, 'staleness_days')
```

### 12.2 Integration Tests

```python
def test_epg_generation_multi_league():
    """Test EPG generation uses multi-league lookup for soccer."""
    # Add Liverpool
    team = create_team(espn_team_id="364", league="eng.1", sport="soccer", ...)

    # Generate EPG
    epg = generate_team_epg(team)

    # Verify events from multiple leagues
    leagues_found = set(e['_source_league'] for e in epg['events'])
    assert 'eng.1' in leagues_found  # Premier League
    # Other leagues depend on current fixtures


def test_migration_existing_teams():
    """Test migration of existing soccer teams."""
    # Create team with old single-league setup
    team = create_team(espn_team_id="364", league="eng.1", sport="soccer", ...)

    # Run migration
    migrate_existing_soccer_teams()

    # Verify team now has multi-league access
    leagues = SoccerMultiLeague.get_team_leagues("364")
    assert len(leagues) > 1
```

### 12.3 Manual Testing Checklist

- [ ] Fresh install: Cache builds automatically on first startup
- [ ] Import Liverpool: Shows 6+ leagues in UI
- [ ] Generate EPG: Events from multiple competitions appear
- [ ] Event icons: Champions League game shows CL logo, not EPL logo
- [ ] Settings: Refresh frequency changes work
- [ ] Manual refresh: "Refresh Now" button works
- [ ] Stale cache: Warning appears when cache >7 days old
- [ ] Migration: Existing soccer teams log migration summary

---

## 13. Implementation Checklist

### Phase 1: Database Schema
- [ ] Add `soccer_team_leagues` table to schema.sql
- [ ] Add `soccer_leagues_cache` table to schema.sql
- [ ] Add `soccer_cache_meta` table to schema.sql
- [ ] Add migration code to `database/__init__.py`
- [ ] Add `soccer_cache_refresh_frequency` to settings table

### Phase 2: Core Module
- [ ] Create `epg/soccer_multi_league.py`
- [ ] Implement `SoccerMultiLeague` class
- [ ] Implement cache refresh logic
- [ ] Implement team lookup functions
- [ ] Implement league categorization
- [ ] Add helper functions to `database/__init__.py`

### Phase 3: Integration
- [ ] Add startup initialization in `app.py`
- [ ] Add migration function for existing teams
- [ ] Add scheduler integration for periodic refresh
- [ ] Add API endpoints for cache status/refresh

### Phase 4: EPG Generation
- [ ] Modify `orchestrator.py` for multi-league schedule fetching
- [ ] Add `_source_league` tracking to events
- [ ] Update template engine with competition variables
- [ ] Ensure programme icons use competition logo

### Phase 5: UI/UX
- [ ] Update team import flow to show leagues
- [ ] Add cache status to settings page
- [ ] Add manual refresh button
- [ ] Add stale cache warnings

### Phase 6: Testing
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Manual testing per checklist
- [ ] Test migration from clean state
- [ ] Test migration from existing teams

---

## Appendix A: ESPN API Reference

### League List Endpoint
```
GET https://sports.core.api.espn.com/v2/sports/soccer/leagues?limit=500
Returns: 246 league references
```

### League Teams Endpoint
```
GET https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams
Returns: All teams in the league with metadata
```

### Team Schedule Endpoint
```
GET https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/{team_id}/schedule
Returns: Team's schedule in that specific league
Note: Same team_id works across all league slugs
```

---

## Appendix B: Sample Data

### Liverpool (ID 364) Leagues
```
eng.1           - English Premier League
uefa.champions  - UEFA Champions League
eng.fa          - FA Cup (when drawn)
eng.league_cup  - Carabao Cup
eng.charity     - Community Shield
club.friendly   - Club Friendlies
jpn.world_challenge - Japan Tour (if scheduled)
```

### League Categories Distribution
```
domestic: 72 leagues
domestic_cup: 45 leagues
continental_club: 18 leagues
continental_national: 22 leagues
world_club: 3 leagues
world_national: 12 leagues
friendly: 8 leagues
qualifier: 25 leagues
other: 39 leagues
```

---

**End of Implementation Guide**
