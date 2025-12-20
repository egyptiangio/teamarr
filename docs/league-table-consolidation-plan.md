# League Table Consolidation Plan

## Overview

Consolidate `league_api_config` + `league_display` into a single `leagues` table while keeping `league_cache` for discovered leagues and `team_cache` for team data.

## Current State (Before)

```
league_api_config (API config)     league_display (display config)
         │                                    │
         └──────────────┬─────────────────────┘
                        │
                   COALESCE JOIN
                        │
                        ▼
                  league_cache (discovered)
                        │
                        ▼
                   team_cache (teams)
```

**Problems:**
- 3 tables for league config (confusing)
- COALESCE merge strategy adds complexity
- Not clear where to add a new league

## Target State (After)

```
┌─────────────────────────────────────────────────────────────────┐
│  leagues (~30 explicitly configured)                            │
│  - Single source of truth for configured leagues                │
│  - API config + display config in one table                     │
│  - import_enabled controls Team Importer visibility             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ For display: prefer leagues, fallback to league_cache
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  league_cache (~300 discovered from providers)                  │
│  - Populated by cache refresh                                   │
│  - Mostly soccer leagues for multi-league matching              │
│  - Minimal info from provider APIs                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  team_cache (teams from ALL leagues)                            │
│  - One row per team-league combo (multi-league support)         │
│  - Used for Team Importer, event matching                       │
└─────────────────────────────────────────────────────────────────┘
```

## New `leagues` Table Schema

```sql
CREATE TABLE leagues (
    league_code TEXT PRIMARY KEY,        -- 'nfl', 'ohl', 'eng.1'

    -- Provider/API Configuration
    provider TEXT NOT NULL,              -- 'espn' or 'tsdb'
    provider_league_id TEXT NOT NULL,    -- ESPN: 'football/nfl', TSDB: '5159'
    provider_league_name TEXT,           -- TSDB only: exact strLeague value
    enabled INTEGER DEFAULT 1,           -- Is this league active?

    -- Display Configuration
    display_name TEXT NOT NULL,          -- 'NFL', 'Ontario Hockey League'
    sport TEXT NOT NULL,                 -- 'Football', 'Hockey', 'Soccer'
    logo_url TEXT,                       -- League logo URL
    import_enabled INTEGER DEFAULT 0,    -- Show in Team Importer?

    -- Cache Metadata (updated by cache refresh)
    cached_team_count INTEGER DEFAULT 0,
    last_cache_refresh TIMESTAMP
);
```

## League Categories

### Team Sports (import_enabled = 1)
- NFL, NBA, NHL, MLB, MLS, WNBA
- College: NCAAF, NCAAM, NCAAW, hockey, volleyball
- Junior Hockey: OHL, WHL, QMJHL
- Soccer: EPL, La Liga, Bundesliga, Serie A, Ligue 1, Champions League
- Lacrosse: NLL, PLL
- Cricket: IPL, CPL, BPL

### Non-Team Sports (import_enabled = 0)
- UFC (MMA)
- F1, IndyCar, NASCAR (Racing)
- PGA, LPGA (Golf)
- ATP, WTA (Tennis)
- Boxing

### Discovered Only (not in leagues table)
- ~300 soccer leagues (eng.2, ger.2, fra.2, etc.)
- Auto-discovered by ESPN soccer league API
- Available for multi-league event matching
- Not shown in Team Importer

## Implementation Steps

### Phase 1: Schema Changes - COMPLETE
1. [x] Create new `leagues` table in schema.sql
2. [x] Add seed data for all configured leagues (32 leagues)
3. [x] Keep `league_cache` table (for discovered leagues)
4. [x] Keep `team_cache` table (unchanged)
5. [x] Remove `league_api_config` table
6. [x] Remove `league_display` table

### Phase 2: Backend - League Mapping Service - COMPLETE
1. [x] LeagueMapping dataclass unchanged (already correct)
2. [x] Update `LeagueMappingService` to query `leagues` table
3. [x] Update `database/leagues.py` queries

### Phase 3: Backend - Cache System - COMPLETE
1. [x] Update `TeamLeagueCache.get_all_leagues()` query logic:
   - For Team Importer: query `leagues WHERE import_enabled = 1`
   - For general use: UNION `leagues` + `league_cache`
2. [x] Update `CacheRefresher` to update `leagues.cached_team_count`
3. [x] Keep league_cache population for discovered leagues

### Phase 4: Backend - Provider Updates - COMPLETE
1. [x] Verify ESPN provider works with new table
2. [x] Verify TSDB provider works with new table
3. [x] Test `get_supported_leagues()` returns configured leagues
4. [x] Test `get_league_teams()` works for all configured leagues

### Phase 5: API Routes - COMPLETE
1. [x] Update `/cache/leagues` endpoint with `import_only` param
2. [x] Update any endpoints that query league config
3. [x] Existing `/cache/team-leagues/{provider}/{id}` endpoint works

### Phase 6: Testing - COMPLETE
1. [x] Test Team Importer shows correct leagues (30 with import_enabled=1)
2. [x] Test cache refresh works (updates cached_team_count)
3. [x] Test event matching with discovered leagues (UNION query)
4. [x] Test multi-league team display (existing endpoint)
5. [x] Test EPG generation for team sports (NFL, NBA, etc.)
6. [x] Test EPG generation for non-team sports (UFC, Boxing excluded from import)

## Query Patterns

### Team Importer - Get leagues to show
```sql
SELECT * FROM leagues WHERE import_enabled = 1 ORDER BY sport, display_name
```

### Event Matching - Get all available leagues
```sql
SELECT league_code as slug, provider, display_name, sport FROM leagues WHERE enabled = 1
UNION
SELECT league_slug as slug, provider, league_name, sport FROM league_cache
```

### Get team's leagues with display names (for badge/tooltip)
```sql
SELECT tc.league,
       COALESCE(l.display_name, lc.league_name, tc.league) as display_name,
       COALESCE(l.logo_url, lc.logo_url) as logo_url
FROM team_cache tc
LEFT JOIN leagues l ON tc.league = l.league_code
LEFT JOIN league_cache lc ON tc.league = lc.league_slug
WHERE tc.provider = ? AND tc.provider_team_id = ?
ORDER BY l.league_code IS NULL, display_name  -- configured leagues first
```

### Provider - Get league config for API calls
```sql
SELECT league_code, provider, provider_league_id, provider_league_name
FROM leagues
WHERE league_code = ? AND provider = ? AND enabled = 1
```

## Files to Modify

### Schema
- `teamarr/database/schema.sql` - New leagues table, remove old tables

### Backend
- `teamarr/database/leagues.py` - Update queries
- `teamarr/services/league_mappings.py` - Update LeagueMappingService
- `teamarr/consumers/cache/queries.py` - Update TeamLeagueCache
- `teamarr/consumers/cache/refresh.py` - Update CacheRefresher
- `teamarr/providers/espn/provider.py` - Verify works
- `teamarr/providers/tsdb/provider.py` - Verify works
- `teamarr/api/routes/cache.py` - Update endpoints

### Frontend (if needed)
- `frontend/src/pages/TeamImport.tsx` - Should work with API changes
- `frontend/src/pages/Teams.tsx` - League badge/tooltip

## Rollback Plan

If issues arise:
1. Restore old schema with 3 tables
2. Restore old query files from git
3. Re-run schema init

## Success Criteria

1. Single `leagues` table for all configured leagues
2. Team Importer shows only import_enabled leagues
3. Cache refresh populates team_cache from all configured leagues
4. Event matching works with discovered soccer leagues
5. Multi-league teams show correct badge/tooltip
6. EPG generation works for both team and non-team sports
