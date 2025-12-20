-- Teamarr v2 Database Schema
-- SQLite Database Structure
--
-- Design principles:
--   - Provider-agnostic (no espn_ prefixes)
--   - JSON for complex nested structures
--   - Templates maintain v1 feature parity for export/import
--   - Timestamps on all tables

-- =============================================================================
-- TEMPLATES TABLE
-- EPG generation templates - controls titles, descriptions, filler content
-- Full v1 feature parity for migration support
-- =============================================================================

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Identity
    name TEXT NOT NULL UNIQUE,
    template_type TEXT DEFAULT 'team' CHECK(template_type IN ('team', 'event')),
    sport TEXT,                              -- Optional filter (basketball, football, etc.)
    league TEXT,                             -- Optional filter (nba, nfl, etc.)

    -- Programme Formatting
    title_format TEXT DEFAULT '{team_name} {sport}',
    subtitle_template TEXT DEFAULT '{venue_full}',
    description_template TEXT DEFAULT '{matchup} | {venue_full}',
    program_art_url TEXT,

    -- Game Duration
    game_duration_mode TEXT DEFAULT 'sport' CHECK(game_duration_mode IN ('sport', 'default', 'custom')),
    game_duration_override REAL,

    -- XMLTV Metadata
    xmltv_flags JSON DEFAULT '{"new": true, "live": false, "date": false}',
    xmltv_categories JSON DEFAULT '["Sports"]',
    categories_apply_to TEXT DEFAULT 'events' CHECK(categories_apply_to IN ('all', 'events')),

    -- Filler: Pre-Game
    pregame_enabled BOOLEAN DEFAULT 1,
    pregame_periods JSON DEFAULT '[
        {"start_hours_before": 24, "end_hours_before": 6, "title": "Game Preview", "description": "{team_name} plays {opponent} in {hours_until} hours at {venue}"},
        {"start_hours_before": 6, "end_hours_before": 2, "title": "Pre-Game Coverage", "description": "{team_name} vs {opponent} starts at {game_time}"},
        {"start_hours_before": 2, "end_hours_before": 0, "title": "Game Starting Soon", "description": "{team_name} vs {opponent} starts in {hours_until} hours"}
    ]',
    pregame_fallback JSON DEFAULT '{"title": "Pregame Coverage", "subtitle": null, "description": "{team_name} plays {opponent} today at {game_time}", "art_url": null}',

    -- Filler: Post-Game
    postgame_enabled BOOLEAN DEFAULT 1,
    postgame_periods JSON DEFAULT '[
        {"start_hours_after": 0, "end_hours_after": 3, "title": "Game Recap", "description": "{team_name} {result_text} {final_score}"},
        {"start_hours_after": 3, "end_hours_after": 12, "title": "Extended Highlights", "description": "Highlights: {team_name} {result_text} {final_score} vs {opponent}"},
        {"start_hours_after": 12, "end_hours_after": 24, "title": "Full Game Replay", "description": "Replay: {team_name} vs {opponent}"}
    ]',
    postgame_fallback JSON DEFAULT '{"title": "Postgame Recap", "subtitle": null, "description": "{team_name} {result_text.last} the {opponent.last} {final_score.last}", "art_url": null}',
    postgame_conditional JSON DEFAULT '{"enabled": false, "description_final": null, "description_not_final": null}',

    -- Filler: Idle (between games)
    idle_enabled BOOLEAN DEFAULT 1,
    idle_content JSON DEFAULT '{"title": "{team_name} Programming", "subtitle": null, "description": "Next game: {game_date.next} at {game_time.next} vs {opponent.next}", "art_url": null}',
    idle_conditional JSON DEFAULT '{"enabled": false, "description_final": null, "description_not_final": null}',
    idle_offseason JSON DEFAULT '{"title_enabled": false, "title": null, "subtitle_enabled": false, "subtitle": null, "description_enabled": false, "description": "No upcoming {team_name} games scheduled."}',

    -- Conditional Descriptions (advanced)
    conditional_descriptions JSON DEFAULT '[]',
    -- Structure: [{"condition": "is_home", "template": "...", "priority": 50, "condition_value": "..."}]

    -- Event Template Specific (for event-based EPG)
    event_channel_name TEXT,
    event_channel_logo_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_templates_name ON templates(name);
CREATE INDEX IF NOT EXISTS idx_templates_type ON templates(template_type);

-- Trigger to auto-update timestamp
CREATE TRIGGER IF NOT EXISTS update_templates_timestamp
AFTER UPDATE ON templates
BEGIN
    UPDATE templates SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- =============================================================================
-- TEAMS TABLE
-- Team channel configurations - provider-agnostic
-- =============================================================================

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Provider Identification (agnostic)
    provider TEXT NOT NULL DEFAULT 'espn',   -- espn, thesportsdb, etc.
    provider_team_id TEXT NOT NULL,          -- Provider's team ID
    league TEXT NOT NULL,                    -- League code (nfl, nba, eng.1, etc.)
    sport TEXT NOT NULL,                     -- Sport (football, basketball, soccer, etc.)

    -- Team Display Info
    team_name TEXT NOT NULL,
    team_abbrev TEXT,
    team_logo_url TEXT,
    team_color TEXT,

    -- Channel Configuration
    channel_id TEXT NOT NULL UNIQUE,         -- XMLTV channel ID
    channel_logo_url TEXT,                   -- Override logo (uses team_logo_url if null)

    -- Template Assignment
    template_id INTEGER,

    -- Status
    active BOOLEAN DEFAULT 1,

    UNIQUE(provider, provider_team_id, league),
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_teams_channel_id ON teams(channel_id);
CREATE INDEX IF NOT EXISTS idx_teams_league ON teams(league);
CREATE INDEX IF NOT EXISTS idx_teams_active ON teams(active);
CREATE INDEX IF NOT EXISTS idx_teams_provider ON teams(provider);

CREATE TRIGGER IF NOT EXISTS update_teams_timestamp
AFTER UPDATE ON teams
BEGIN
    UPDATE teams SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- =============================================================================
-- SETTINGS TABLE
-- Global application settings (single row)
-- =============================================================================

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Look Ahead Settings
    team_schedule_days_ahead INTEGER DEFAULT 30,    -- How far to fetch team schedules (for .next vars, conditionals)
    event_match_days_ahead INTEGER DEFAULT 3,       -- Event-stream matching window (Event Groups only)
    epg_output_days_ahead INTEGER DEFAULT 14,       -- Days to include in final XMLTV
    epg_lookback_hours INTEGER DEFAULT 6,           -- Check for in-progress games

    -- Channel Lifecycle (for event-based EPG)
    -- Create timing: 'stream_available', 'same_day', 'day_before', '2_days_before', '3_days_before', '1_week_before', 'manual'
    channel_create_timing TEXT DEFAULT 'same_day' CHECK(channel_create_timing IN ('stream_available', 'same_day', 'day_before', '2_days_before', '3_days_before', '1_week_before', 'manual')),
    -- Delete timing: 'stream_removed', 'same_day', 'day_after', '2_days_after', '3_days_after', '1_week_after', 'manual'
    channel_delete_timing TEXT DEFAULT 'day_after' CHECK(channel_delete_timing IN ('stream_removed', 'same_day', 'day_after', '2_days_after', '3_days_after', '1_week_after', 'manual')),

    -- Filler Settings
    midnight_crossover_mode TEXT DEFAULT 'postgame' CHECK(midnight_crossover_mode IN ('postgame', 'idle')),

    -- EPG Output
    epg_timezone TEXT DEFAULT 'America/New_York',
    epg_output_path TEXT DEFAULT './teamarr.xml',

    -- Game Duration Defaults (hours)
    duration_default REAL DEFAULT 3.0,
    duration_basketball REAL DEFAULT 3.0,
    duration_football REAL DEFAULT 3.5,
    duration_hockey REAL DEFAULT 3.0,
    duration_baseball REAL DEFAULT 3.5,
    duration_soccer REAL DEFAULT 2.5,
    duration_mma REAL DEFAULT 5.0,
    duration_rugby REAL DEFAULT 2.5,
    duration_boxing REAL DEFAULT 4.0,
    duration_tennis REAL DEFAULT 3.0,
    duration_golf REAL DEFAULT 6.0,
    duration_racing REAL DEFAULT 3.0,
    duration_cricket REAL DEFAULT 4.0,  -- T20 matches ~3-4 hours

    -- XMLTV
    xmltv_generator_name TEXT DEFAULT 'Teamarr v2',
    xmltv_generator_url TEXT DEFAULT 'https://github.com/your-repo/teamarr',

    -- Display Preferences
    time_format TEXT DEFAULT '12h' CHECK(time_format IN ('12h', '24h')),
    show_timezone BOOLEAN DEFAULT 1,

    -- Event-Based EPG Options
    include_final_events BOOLEAN DEFAULT 0,      -- Include completed events for today
    channel_range_start INTEGER DEFAULT 101,     -- First auto-assigned channel number
    channel_range_end INTEGER,                   -- Last auto-assigned channel (null = no limit)

    -- Scheduled Generation
    cron_expression TEXT DEFAULT '0 * * * *',    -- Cron for auto EPG generation

    -- Cache Refresh Frequencies
    soccer_cache_refresh_frequency TEXT DEFAULT 'weekly',
    team_cache_refresh_frequency TEXT DEFAULT 'weekly',

    -- API
    api_timeout INTEGER DEFAULT 10,
    api_retry_count INTEGER DEFAULT 3,

    -- Channel ID Format
    channel_id_format TEXT DEFAULT '{team_name_pascal}.{league}',

    -- Generation Counter (for cache purging)
    epg_generation_counter INTEGER DEFAULT 0,

    -- Dispatcharr Integration
    dispatcharr_enabled BOOLEAN DEFAULT 0,
    dispatcharr_url TEXT,
    dispatcharr_username TEXT,
    dispatcharr_password TEXT,                -- Note: Consider encrypting in production
    dispatcharr_epg_id INTEGER,               -- Teamarr's EPG source ID in Dispatcharr

    -- Reconciliation Settings
    reconcile_on_epg_generation BOOLEAN DEFAULT 1,
    reconcile_on_startup BOOLEAN DEFAULT 1,
    auto_fix_orphan_teamarr BOOLEAN DEFAULT 1,    -- Auto-delete DB records for missing channels
    auto_fix_orphan_dispatcharr BOOLEAN DEFAULT 0, -- DANGEROUS: Auto-delete untracked channels
    auto_fix_duplicates BOOLEAN DEFAULT 0,

    -- Duplicate Event Handling
    default_duplicate_event_handling TEXT DEFAULT 'consolidate'
        CHECK(default_duplicate_event_handling IN ('consolidate', 'separate', 'ignore')),

    -- Channel History
    channel_history_retention_days INTEGER DEFAULT 90,

    -- Background Scheduler
    scheduler_enabled BOOLEAN DEFAULT 1,
    scheduler_interval_minutes INTEGER DEFAULT 15,

    -- Schema Version
    schema_version INTEGER DEFAULT 2
);

-- Insert default settings
INSERT OR IGNORE INTO settings (id) VALUES (1);

CREATE TRIGGER IF NOT EXISTS update_settings_timestamp
AFTER UPDATE ON settings
BEGIN
    UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- =============================================================================
-- EVENT_EPG_GROUPS TABLE
-- Configuration for event-based EPG generation
-- =============================================================================

CREATE TABLE IF NOT EXISTS event_epg_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Identity
    name TEXT NOT NULL UNIQUE,

    -- What to scan
    leagues JSON NOT NULL,                   -- ["nfl", "nba"] - leagues to scan for events

    -- Template
    template_id INTEGER,

    -- Channel Settings
    channel_start_number INTEGER,            -- Starting channel number for this group
    channel_group_id INTEGER,                -- Dispatcharr channel group to assign
    stream_profile_id INTEGER,               -- Dispatcharr stream profile
    channel_profile_ids TEXT,                -- JSON array of channel profile IDs

    -- Lifecycle Settings (override global)
    create_timing TEXT DEFAULT 'same_day'
        CHECK(create_timing IN ('stream_available', 'same_day', 'day_before', '2_days_before', '3_days_before', '1_week_before', 'manual')),
    delete_timing TEXT DEFAULT 'same_day'
        CHECK(delete_timing IN ('stream_removed', 'same_day', 'day_after', '2_days_after', '3_days_after', '1_week_after', 'manual')),

    -- Duplicate Event Handling (override global)
    duplicate_event_handling TEXT DEFAULT 'consolidate'
        CHECK(duplicate_event_handling IN ('consolidate', 'separate', 'ignore')),

    -- Channel Assignment Mode
    channel_assignment_mode TEXT DEFAULT 'auto'
        CHECK(channel_assignment_mode IN ('auto', 'manual')),

    -- Channel Numbering (for AUTO mode)
    sort_order INTEGER DEFAULT 0,            -- Ordering for AUTO channel allocation
    total_stream_count INTEGER DEFAULT 0,    -- Expected streams (for range reservation)
    parent_group_id INTEGER,                 -- Parent group for child group relationships

    -- M3U Group Binding (for stream discovery)
    m3u_group_id INTEGER,                    -- Dispatcharr M3U group to scan
    m3u_group_name TEXT,

    -- Status
    enabled BOOLEAN DEFAULT 1,

    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

CREATE TRIGGER IF NOT EXISTS update_event_epg_groups_timestamp
AFTER UPDATE ON event_epg_groups
BEGIN
    UPDATE event_epg_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE INDEX IF NOT EXISTS idx_event_epg_groups_enabled ON event_epg_groups(enabled);
CREATE INDEX IF NOT EXISTS idx_event_epg_groups_sort_order ON event_epg_groups(sort_order);
CREATE INDEX IF NOT EXISTS idx_event_epg_groups_name ON event_epg_groups(name);


-- =============================================================================
-- MANAGED_CHANNELS TABLE
-- Dynamically created channels for event-based EPG
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Parent Group
    event_epg_group_id INTEGER NOT NULL,

    -- Event Reference (provider-agnostic)
    event_id TEXT NOT NULL,
    event_provider TEXT NOT NULL,

    -- Channel Info
    tvg_id TEXT NOT NULL UNIQUE,
    channel_name TEXT NOT NULL,
    channel_number TEXT,
    logo_url TEXT,

    -- Dispatcharr Integration
    dispatcharr_channel_id INTEGER,          -- Dispatcharr's channel ID
    dispatcharr_uuid TEXT,                   -- Dispatcharr's immutable UUID
    dispatcharr_logo_id INTEGER,             -- Uploaded logo ID in Dispatcharr

    -- Channel Settings (from group config)
    channel_group_id INTEGER,                -- Dispatcharr channel group
    stream_profile_id INTEGER,               -- Dispatcharr stream profile
    channel_profile_ids TEXT,                -- JSON array of channel profile IDs

    -- Primary stream (first/main stream for this channel)
    primary_stream_id INTEGER,

    -- Exception keyword that matched (for consolidation override)
    exception_keyword TEXT,

    -- Event Context (cached for display)
    home_team TEXT,
    home_team_abbrev TEXT,
    home_team_logo TEXT,
    away_team TEXT,
    away_team_abbrev TEXT,
    away_team_logo TEXT,
    event_date TIMESTAMP,                    -- Event start time (UTC)
    event_name TEXT,
    league TEXT,
    sport TEXT,
    venue TEXT,
    broadcast TEXT,

    -- Lifecycle
    scheduled_delete_at TIMESTAMP,           -- When to delete (based on delete_timing)
    deleted_at TIMESTAMP,                    -- When actually deleted
    delete_reason TEXT,                      -- Why deleted (expired, stream_removed, manual, etc.)

    -- Sync Status
    sync_status TEXT DEFAULT 'pending'       -- pending, created, in_sync, drifted, orphaned, error
        CHECK(sync_status IN ('pending', 'created', 'in_sync', 'drifted', 'orphaned', 'error')),
    sync_message TEXT,                       -- Last sync message/error
    last_verified_at TIMESTAMP,              -- Last reconciliation check

    -- Legacy (for backwards compatibility)
    expires_at TIMESTAMP,
    external_channel_id INTEGER,             -- Alias for dispatcharr_channel_id

    FOREIGN KEY (event_epg_group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE,
    UNIQUE(event_epg_group_id, event_id, event_provider)
);

CREATE INDEX IF NOT EXISTS idx_managed_channels_group ON managed_channels(event_epg_group_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_event ON managed_channels(event_id, event_provider);
CREATE INDEX IF NOT EXISTS idx_managed_channels_expires ON managed_channels(expires_at);
CREATE INDEX IF NOT EXISTS idx_managed_channels_delete ON managed_channels(scheduled_delete_at)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_managed_channels_dispatcharr ON managed_channels(dispatcharr_channel_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_tvg ON managed_channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_sync ON managed_channels(sync_status);

CREATE TRIGGER IF NOT EXISTS update_managed_channels_timestamp
AFTER UPDATE ON managed_channels
BEGIN
    UPDATE managed_channels SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- =============================================================================
-- LEAGUES TABLE
-- Single source of truth for configured leagues
-- Combines API config + display config in one table
-- =============================================================================

CREATE TABLE IF NOT EXISTS leagues (
    league_code TEXT PRIMARY KEY,            -- 'nfl', 'ohl', 'eng.1'

    -- Provider/API Configuration
    provider TEXT NOT NULL,                  -- 'espn' or 'tsdb'
    provider_league_id TEXT NOT NULL,        -- ESPN: 'football/nfl', TSDB: '5159'
    provider_league_name TEXT,               -- TSDB only: exact strLeague for API calls
    enabled INTEGER DEFAULT 1,               -- Is this league active?

    -- Display Configuration
    display_name TEXT NOT NULL,              -- 'NFL', 'Ontario Hockey League'
    sport TEXT NOT NULL,                     -- 'Football', 'Hockey', 'Soccer'
    logo_url TEXT,                           -- League logo URL
    import_enabled INTEGER DEFAULT 0,        -- Show in Team Importer?

    -- Cache Metadata (updated by cache refresh)
    cached_team_count INTEGER DEFAULT 0,
    last_cache_refresh TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_leagues_provider ON leagues(provider);
CREATE INDEX IF NOT EXISTS idx_leagues_sport ON leagues(sport);
CREATE INDEX IF NOT EXISTS idx_leagues_import ON leagues(import_enabled);


-- =============================================================================
-- SEED: Configured Leagues
-- All explicitly configured leagues with API + display config
-- =============================================================================

INSERT OR IGNORE INTO leagues (league_code, provider, provider_league_id, provider_league_name, display_name, sport, logo_url, import_enabled) VALUES
    -- Football (ESPN)
    ('nfl', 'espn', 'football/nfl', NULL, 'NFL', 'Football', 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png', 1),
    ('college-football', 'espn', 'football/college-football', NULL, 'NCAA Football', 'Football', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/football.png', 1),

    -- Basketball (ESPN)
    ('nba', 'espn', 'basketball/nba', NULL, 'NBA', 'Basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png', 1),
    ('nba-development', 'espn', 'basketball/nba-development', NULL, 'NBA G League', 'Basketball', 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nba_gleague.png', 1),
    ('wnba', 'espn', 'basketball/wnba', NULL, 'WNBA', 'Basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png', 1),
    ('mens-college-basketball', 'espn', 'basketball/mens-college-basketball', NULL, 'NCAA Men''s Basketball', 'Basketball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png', 1),
    ('womens-college-basketball', 'espn', 'basketball/womens-college-basketball', NULL, 'NCAA Women''s Basketball', 'Basketball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png', 1),

    -- Hockey (ESPN)
    ('nhl', 'espn', 'hockey/nhl', NULL, 'NHL', 'Hockey', 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png', 1),
    ('mens-college-hockey', 'espn', 'hockey/mens-college-hockey', NULL, 'NCAA Men''s Ice Hockey', 'Hockey', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png', 1),
    ('womens-college-hockey', 'espn', 'hockey/womens-college-hockey', NULL, 'NCAA Women''s Ice Hockey', 'Hockey', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png', 1),

    -- Hockey - Canadian Junior (TSDB)
    ('ohl', 'tsdb', '5159', 'Canadian OHL', 'Ontario Hockey League', 'Hockey', 'https://r2.thesportsdb.com/images/media/league/badge/y4z5ks1644535179.png', 1),
    ('whl', 'tsdb', '5160', 'Canadian WHL', 'Western Hockey League', 'Hockey', 'https://r2.thesportsdb.com/images/media/league/badge/94w3kx1644535361.png', 1),
    ('qmjhl', 'tsdb', '5161', 'Canadian QMJHL', 'Quebec Major Junior Hockey League', 'Hockey', 'https://r2.thesportsdb.com/images/media/league/badge/3nofen1644535248.png', 1),

    -- Baseball (ESPN)
    ('mlb', 'espn', 'baseball/mlb', NULL, 'MLB', 'Baseball', 'https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png', 1),

    -- Soccer (ESPN)
    ('usa.1', 'espn', 'soccer/usa.1', NULL, 'MLS', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/19.png', 1),
    ('usa.ncaa.m.1', 'espn', 'soccer/usa.ncaa.m.1', NULL, 'NCAA Men''s Soccer', 'Soccer', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png', 1),
    ('usa.ncaa.w.1', 'espn', 'soccer/usa.ncaa.w.1', NULL, 'NCAA Women''s Soccer', 'Soccer', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png', 1),
    ('eng.1', 'espn', 'soccer/eng.1', NULL, 'English Premier League', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/23.png', 1),
    ('esp.1', 'espn', 'soccer/esp.1', NULL, 'La Liga', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/15.png', 1),
    ('ger.1', 'espn', 'soccer/ger.1', NULL, 'Bundesliga', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/10.png', 1),
    ('ita.1', 'espn', 'soccer/ita.1', NULL, 'Serie A', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/12.png', 1),
    ('fra.1', 'espn', 'soccer/fra.1', NULL, 'Ligue 1', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/9.png', 1),
    ('uefa.champions', 'espn', 'soccer/uefa.champions', NULL, 'UEFA Champions League', 'Soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2.png', 1),

    -- MMA (ESPN) - Non-team sport, import_enabled = 0
    ('ufc', 'espn', 'mma/ufc', NULL, 'UFC', 'MMA', 'https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png', 0),

    -- Volleyball (ESPN)
    ('mens-college-volleyball', 'espn', 'volleyball/mens-college-volleyball', NULL, 'NCAA Men''s Volleyball', 'Volleyball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png', 1),
    ('womens-college-volleyball', 'espn', 'volleyball/womens-college-volleyball', NULL, 'NCAA Women''s Volleyball', 'Volleyball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png', 1),

    -- Lacrosse (ESPN)
    ('nll', 'espn', 'lacrosse/nll', NULL, 'National Lacrosse League', 'Lacrosse', 'https://a.espncdn.com/guid/5f77fe12-e54f-41a1-904e-77135452f348/logos/default.png', 1),
    ('pll', 'espn', 'lacrosse/pll', NULL, 'Premier Lacrosse League', 'Lacrosse', 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/pll.png', 1),

    -- Cricket (TSDB)
    ('ipl', 'tsdb', '4460', 'Indian Premier League', 'Indian Premier League', 'Cricket', NULL, 1),
    ('cpl', 'tsdb', '5176', 'Caribbean Premier League', 'Caribbean Premier League', 'Cricket', NULL, 1),
    ('bpl', 'tsdb', '5529', 'Bangladesh Premier League', 'Bangladesh Premier League', 'Cricket', NULL, 1),

    -- Boxing (TSDB) - Non-team sport, import_enabled = 0
    ('boxing', 'tsdb', '4445', 'Boxing', 'Boxing', 'Boxing', NULL, 0);


-- =============================================================================
-- STREAM_MATCH_CACHE TABLE
-- Caches stream-to-event matches to avoid expensive matching on every run.
-- Only caches successful matches.
--
-- Fingerprint = hash of group_id + stream_id + stream_name
-- When stream name changes, hash changes, so no stale match used.
-- =============================================================================

CREATE TABLE IF NOT EXISTS stream_match_cache (
    -- Hash fingerprint for fast lookup (SHA256 truncated to 16 chars)
    fingerprint TEXT PRIMARY KEY,

    -- Original fields kept for debugging
    group_id INTEGER NOT NULL,
    stream_id INTEGER NOT NULL,
    stream_name TEXT NOT NULL,

    -- Match result
    event_id TEXT NOT NULL,
    league TEXT NOT NULL,

    -- Cached static event data (JSON blob)
    -- Contains event dict for template vars (static fields only)
    cached_event_data TEXT NOT NULL,

    -- Housekeeping
    last_seen_generation INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_smc_generation ON stream_match_cache(last_seen_generation);
CREATE INDEX IF NOT EXISTS idx_smc_event_id ON stream_match_cache(event_id);


-- =============================================================================
-- TEAM_CACHE TABLE
-- Unified cache of all teams from all providers (ESPN + TSDB)
-- Used for:
--   1. Event matching: "Freiburg vs Stuttgart" → which league?
--   2. Team multi-league: Liverpool → [eng.1, uefa.champions, eng.fa, ...]
--
-- Refresh weekly to handle promotion/relegation
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Team identity
    team_name TEXT NOT NULL,              -- "Liverpool", "SC Freiburg II"
    team_abbrev TEXT,                     -- "LIV", "SCF"
    team_short_name TEXT,                 -- "Liverpool", "Freiburg II"

    -- Provider-specific
    provider TEXT NOT NULL,               -- 'espn' or 'tsdb'
    provider_team_id TEXT NOT NULL,       -- Provider's team ID

    -- League membership (one row per team-league combo)
    league TEXT NOT NULL,                 -- League slug: 'eng.1', 'ger.3', 'nhl'
    sport TEXT NOT NULL,                  -- 'soccer', 'hockey', 'football'

    -- Metadata
    logo_url TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(provider, provider_team_id, league)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_tc_team_name ON team_cache(team_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tc_team_abbrev ON team_cache(team_abbrev COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tc_team_short ON team_cache(team_short_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tc_league ON team_cache(league);
CREATE INDEX IF NOT EXISTS idx_tc_sport ON team_cache(sport);
CREATE INDEX IF NOT EXISTS idx_tc_provider ON team_cache(provider);
CREATE INDEX IF NOT EXISTS idx_tc_provider_team ON team_cache(provider, provider_team_id);


-- =============================================================================
-- LEAGUE_CACHE TABLE
-- Unified cache of all leagues from all providers (ESPN + TSDB)
-- Used for:
--   1. "soccer_all" event matching: iterate all soccer leagues
--   2. League metadata: names, logos for display
--
-- Refresh weekly
-- =============================================================================

CREATE TABLE IF NOT EXISTS league_cache (
    -- League identity
    league_slug TEXT NOT NULL,            -- 'eng.1', 'ger.3', 'nhl'
    provider TEXT NOT NULL,               -- Primary provider for this league

    -- Metadata
    league_name TEXT,                     -- 'English Premier League'
    sport TEXT NOT NULL,                  -- 'soccer', 'hockey', 'football'
    logo_url TEXT,
    team_count INTEGER DEFAULT 0,

    -- Timestamps
    last_refreshed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (league_slug, provider)
);

CREATE INDEX IF NOT EXISTS idx_lc_sport ON league_cache(sport);
CREATE INDEX IF NOT EXISTS idx_lc_provider ON league_cache(provider);


-- =============================================================================
-- CACHE_META TABLE
-- Tracks refresh status for team_cache and league_cache
-- =============================================================================

CREATE TABLE IF NOT EXISTS cache_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),

    -- Last refresh timestamps
    last_full_refresh TIMESTAMP,
    espn_last_refresh TIMESTAMP,
    tsdb_last_refresh TIMESTAMP,

    -- Stats
    leagues_count INTEGER DEFAULT 0,
    teams_count INTEGER DEFAULT 0,
    refresh_duration_seconds REAL DEFAULT 0,

    -- Status
    refresh_in_progress BOOLEAN DEFAULT 0,
    last_error TEXT
);

INSERT OR IGNORE INTO cache_meta (id) VALUES (1);


-- =============================================================================
-- MANAGED_CHANNEL_STREAMS TABLE
-- Multi-stream support for managed channels with priority ordering
-- Each channel can have multiple streams (failover support)
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channel_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Parent channel
    managed_channel_id INTEGER NOT NULL,

    -- Stream info
    dispatcharr_stream_id INTEGER NOT NULL,
    stream_name TEXT,

    -- Source tracking
    source_group_id INTEGER,                 -- Which M3U group provided this stream
    source_group_type TEXT DEFAULT 'parent'  -- 'parent', 'child', 'cross_group'
        CHECK(source_group_type IN ('parent', 'child', 'cross_group')),

    -- Priority (0 = primary, higher = failover)
    priority INTEGER DEFAULT 0,

    -- M3U account info (for display)
    m3u_account_id INTEGER,
    m3u_account_name TEXT,

    -- Exception keyword that matched this stream
    exception_keyword TEXT,

    -- Lifecycle
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    removed_at TIMESTAMP,
    remove_reason TEXT,

    -- Sync status
    last_verified_at TIMESTAMP,
    in_dispatcharr BOOLEAN DEFAULT 1,

    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mcs_channel ON managed_channel_streams(managed_channel_id);
CREATE INDEX IF NOT EXISTS idx_mcs_stream ON managed_channel_streams(dispatcharr_stream_id);
CREATE INDEX IF NOT EXISTS idx_mcs_active ON managed_channel_streams(managed_channel_id, removed_at)
    WHERE removed_at IS NULL;


-- =============================================================================
-- MANAGED_CHANNEL_HISTORY TABLE
-- Audit trail for channel lifecycle changes
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channel_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    managed_channel_id INTEGER NOT NULL,

    -- Change type
    change_type TEXT NOT NULL
        CHECK(change_type IN ('created', 'modified', 'deleted', 'stream_added', 'stream_removed', 'verified', 'synced', 'error')),

    -- Change source
    change_source TEXT
        CHECK(change_source IN ('epg_generation', 'reconciliation', 'api', 'scheduler', 'manual', 'external_sync')),

    -- Change details
    field_name TEXT,                         -- For 'modified': which field changed
    old_value TEXT,
    new_value TEXT,

    -- Timestamps
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Notes
    notes TEXT,

    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mch_channel ON managed_channel_history(managed_channel_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_mch_type ON managed_channel_history(change_type);


-- =============================================================================
-- CONSOLIDATION_EXCEPTION_KEYWORDS TABLE
-- Keywords that trigger separate channel creation (language variants, etc.)
-- =============================================================================

CREATE TABLE IF NOT EXISTS consolidation_exception_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Keyword variants (comma-separated)
    -- e.g., "Spanish, En Español, (ESP), Español"
    keywords TEXT NOT NULL UNIQUE,

    -- Behavior when keyword matched
    behavior TEXT NOT NULL DEFAULT 'consolidate'
        CHECK(behavior IN ('consolidate', 'separate', 'ignore')),

    -- Display name for UI
    display_name TEXT,

    -- Status
    enabled BOOLEAN DEFAULT 1
);

-- Seed default language keywords
INSERT OR IGNORE INTO consolidation_exception_keywords (keywords, display_name, behavior) VALUES
    ('Spanish, En Español, (ESP), Español', 'Spanish', 'consolidate'),
    ('French, En Français, (FRA), Français', 'French', 'consolidate'),
    ('German, (GER), Deutsch', 'German', 'consolidate'),
    ('Portuguese, (POR), Português', 'Portuguese', 'consolidate'),
    ('Italian, (ITA), Italiano', 'Italian', 'consolidate'),
    ('Japanese, (JPN), 日本語', 'Japanese', 'consolidate'),
    ('Korean, (KOR), 한국어', 'Korean', 'consolidate'),
    ('Chinese, (CHN), (CHI), 中文', 'Chinese', 'consolidate');

CREATE INDEX IF NOT EXISTS idx_exception_keywords_enabled ON consolidation_exception_keywords(enabled);
CREATE INDEX IF NOT EXISTS idx_exception_keywords_behavior ON consolidation_exception_keywords(behavior);


-- =============================================================================
-- CONDITION_PRESETS TABLE
-- Saved condition configurations for template descriptions
-- =============================================================================

CREATE TABLE IF NOT EXISTS condition_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Preset identity
    name TEXT NOT NULL UNIQUE,
    description TEXT,

    -- Condition configuration (JSON array)
    -- e.g., [{"condition": "win_streak", "value": "5", "priority": 10, "template": "..."}]
    conditions JSON NOT NULL DEFAULT '[]'
);


-- =============================================================================
-- EVENT_EPG_XMLTV TABLE
-- Stores generated XMLTV content per event group
-- Allows XMLTV to be served at a predictable URL for Dispatcharr to fetch
-- =============================================================================

CREATE TABLE IF NOT EXISTS event_epg_xmltv (
    group_id INTEGER PRIMARY KEY,
    xmltv_content TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
);


-- =============================================================================
-- TEAM_EPG_XMLTV TABLE
-- Stores generated XMLTV content per team
-- Allows XMLTV to be served and merged with event group XMLTV
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_epg_xmltv (
    team_id INTEGER PRIMARY KEY,
    xmltv_content TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);


-- =============================================================================
-- PROCESSING_RUNS TABLE
-- Stores historical stats from each processing run
-- Scalable design: core fields + JSON for extensibility
-- =============================================================================

CREATE TABLE IF NOT EXISTS processing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Run identification
    run_type TEXT NOT NULL,  -- 'event_group', 'team_epg', 'batch', 'reconciliation', 'scheduler'
    run_id TEXT,             -- Optional unique run identifier (UUID)
    group_id INTEGER,        -- For event_group runs
    team_id INTEGER,         -- For team_epg runs

    -- Timing
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    duration_ms INTEGER,     -- Computed duration in milliseconds

    -- Status
    status TEXT NOT NULL DEFAULT 'running',  -- 'running', 'completed', 'failed', 'partial'
    error_message TEXT,

    -- Core metrics (commonly queried, indexed)
    streams_fetched INTEGER DEFAULT 0,
    streams_matched INTEGER DEFAULT 0,
    streams_unmatched INTEGER DEFAULT 0,
    streams_cached INTEGER DEFAULT 0,       -- Used fingerprint cache

    channels_created INTEGER DEFAULT 0,
    channels_updated INTEGER DEFAULT 0,
    channels_deleted INTEGER DEFAULT 0,
    channels_skipped INTEGER DEFAULT 0,
    channels_errors INTEGER DEFAULT 0,

    programmes_total INTEGER DEFAULT 0,
    programmes_events INTEGER DEFAULT 0,
    programmes_pregame INTEGER DEFAULT 0,
    programmes_postgame INTEGER DEFAULT 0,
    programmes_idle INTEGER DEFAULT 0,

    xmltv_size_bytes INTEGER DEFAULT 0,

    -- Extensible metrics (JSON blob for future additions)
    -- Example: {"api_calls": 5, "cache_hits": 10, "enrichment_time_ms": 500}
    extra_metrics JSON DEFAULT '{}',

    -- Foreign keys
    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE SET NULL,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_processing_runs_type ON processing_runs(run_type);
CREATE INDEX IF NOT EXISTS idx_processing_runs_created ON processing_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_processing_runs_group ON processing_runs(group_id);
CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON processing_runs(status);
-- Composite index for filtering by type and ordering by date
CREATE INDEX IF NOT EXISTS idx_processing_runs_type_created ON processing_runs(run_type, created_at DESC);


-- =============================================================================
-- STATS_SNAPSHOTS TABLE
-- Periodic snapshots of aggregate stats (for dashboards)
-- =============================================================================

CREATE TABLE IF NOT EXISTS stats_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Snapshot type
    snapshot_type TEXT NOT NULL,  -- 'hourly', 'daily', 'weekly'
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,

    -- Aggregate counts
    total_runs INTEGER DEFAULT 0,
    successful_runs INTEGER DEFAULT 0,
    failed_runs INTEGER DEFAULT 0,

    total_streams_matched INTEGER DEFAULT 0,
    total_streams_unmatched INTEGER DEFAULT 0,
    total_channels_created INTEGER DEFAULT 0,
    total_programmes_generated INTEGER DEFAULT 0,

    -- Breakdown by type
    programmes_by_type JSON DEFAULT '{}',  -- {"events": N, "pregame": N, "postgame": N, "idle": N}

    -- Performance
    avg_duration_ms INTEGER DEFAULT 0,
    max_duration_ms INTEGER DEFAULT 0,

    -- Extensible
    extra_stats JSON DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_stats_snapshots_type ON stats_snapshots(snapshot_type);
CREATE INDEX IF NOT EXISTS idx_stats_snapshots_period ON stats_snapshots(period_start)
