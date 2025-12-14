-- TeamArr - Template-Based Architecture Database Schema
-- SQLite Database Structure
-- Last Updated: November 27, 2025
-- Version: 2.0.0 (Event-based EPG + Channel Lifecycle)

-- =============================================================================
-- TEMPLATES TABLE
-- Stores reusable EPG generation templates (all formatting/filler settings)
-- =============================================================================

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Template Identity
    name TEXT NOT NULL UNIQUE,           -- User-defined template name (e.g., "NBA Standard", "Generic Sports")
    sport TEXT,                           -- Optional sport filter (e.g., "basketball", "football")
    league TEXT,                          -- Optional league filter (e.g., "nba", "nfl")

    -- Programme Metadata Templates
    title_format TEXT DEFAULT '{team_name} {sport}', -- Programme title template
    subtitle_template TEXT DEFAULT '{venue_full}',    -- Programme subtitle template
    program_art_url TEXT,                             -- Optional URL template for program art (e.g., game-thumbs integration)

    -- Game Timing
    game_duration_mode TEXT DEFAULT 'sport', -- 'sport', 'default', or 'custom'
    game_duration_override REAL,          -- Custom override value (only used if mode='custom')

    -- XMLTV Flags
    flags JSON DEFAULT '{"new": true, "live": false, "date": false}',
    -- Structure: {"new": boolean, "live": boolean, "date": boolean}

    -- XMLTV Categories
    categories JSON DEFAULT '["Sports"]',
    -- Array of category strings, e.g., ["Sports", "Basketball", "HD", "Live"]
    categories_apply_to TEXT DEFAULT 'events', -- 'all' or 'events' - control category application

    -- Schedule Filler: No Game Day (deprecated, kept for backwards compatibility)
    no_game_enabled BOOLEAN DEFAULT 0,
    no_game_title TEXT DEFAULT 'No Game Today',
    no_game_description TEXT DEFAULT 'No {team_name} game scheduled today. Next game: {next_game_date} vs {next_opponent}',
    no_game_duration REAL DEFAULT 24.0,

    -- Schedule Filler: Pre-Game Periods
    pregame_enabled BOOLEAN DEFAULT 1,
    pregame_periods JSON DEFAULT '[
        {
            "start_hours_before": 24,
            "end_hours_before": 6,
            "title": "Game Preview",
            "description": "{team_name} plays {opponent} in {hours_until} hours at {venue}"
        },
        {
            "start_hours_before": 6,
            "end_hours_before": 2,
            "title": "Pre-Game Coverage",
            "description": "{team_name} vs {opponent} starts at {game_time}. {team_name} ({team_record}) looks to improve."
        },
        {
            "start_hours_before": 2,
            "end_hours_before": 0,
            "title": "Game Starting Soon",
            "description": "{team_name} vs {opponent} starts in {hours_until} hours at {venue_full}"
        }
    ]',
    pregame_title TEXT DEFAULT 'Pregame Coverage',
    pregame_subtitle TEXT,
    pregame_description TEXT DEFAULT '{team_name} plays {opponent} today at {game_time}',
    pregame_art_url TEXT,

    -- Schedule Filler: Post-Game Periods
    postgame_enabled BOOLEAN DEFAULT 1,
    postgame_periods JSON DEFAULT '[
        {
            "start_hours_after": 0,
            "end_hours_after": 3,
            "title": "Game Recap",
            "description": "{team_name} {result_text} {final_score}. Final record: {final_record}"
        },
        {
            "start_hours_after": 3,
            "end_hours_after": 12,
            "title": "Extended Highlights",
            "description": "Highlights: {team_name} {result_text} {final_score} vs {opponent}"
        },
        {
            "start_hours_after": 12,
            "end_hours_after": 24,
            "title": "Full Game Replay",
            "description": "Replay: {team_name} vs {opponent} - Final Score: {final_score}"
        }
    ]',
    postgame_title TEXT DEFAULT 'Postgame Recap',
    postgame_subtitle TEXT,
    postgame_description TEXT DEFAULT '{team_name} {result_text.last} the {opponent.last} {final_score.last} {overtime_text.last}',
    postgame_art_url TEXT,
    postgame_conditional_enabled BOOLEAN DEFAULT 0,
    postgame_description_final TEXT DEFAULT 'The {team_name} {result_text.last} the {opponent.last} {final_score.last} {overtime_text.last}',
    postgame_description_not_final TEXT DEFAULT 'The game between the {team_name} and {opponent.last} on {game_day.last} {game_date.last} has not yet ended.',

    -- Schedule Filler: Idle Days (Between Games / No Game Days)
    idle_enabled BOOLEAN DEFAULT 1,
    idle_title TEXT DEFAULT '{team_name} Programming',
    idle_subtitle TEXT,
    idle_description TEXT DEFAULT 'Next game: {game_date.next} at {game_time.next} vs {opponent.next}',
    idle_art_url TEXT,
    idle_conditional_enabled BOOLEAN DEFAULT 0,
    idle_description_final TEXT DEFAULT 'The {team_name} {result_text.last} the {opponent.last} {final_score.last}. Next: {opponent.next} on {game_date.next}',
    idle_description_not_final TEXT DEFAULT 'The {team_name} last played {opponent.last} on {game_date.last}. Next: {opponent.next} on {game_date.next}',
    idle_subtitle_offseason_enabled BOOLEAN DEFAULT 0,
    idle_subtitle_offseason TEXT,
    idle_offseason_enabled BOOLEAN DEFAULT 0,
    idle_description_offseason TEXT DEFAULT 'No upcoming {team_name} games scheduled.',

    -- Conditional Descriptions (Templates tab)
    description_options JSON DEFAULT '[]',   -- Array of conditional description templates
    -- Structure: [{"condition": "is_home", "template": "...", "priority": 50, "condition_value": "..."}]

    -- Template Type (team or event)
    template_type TEXT DEFAULT 'team' CHECK(template_type IN ('team', 'event')),

    -- Event Template Specific Fields
    channel_name TEXT,                       -- Channel name template for event templates
    channel_logo_url TEXT                    -- Channel logo URL template for event templates
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_templates_name ON templates(name);
CREATE INDEX IF NOT EXISTS idx_templates_sport ON templates(sport);
CREATE INDEX IF NOT EXISTS idx_templates_league ON templates(league);

-- =============================================================================
-- TEAMS TABLE
-- Stores team identity and template assignment (simplified from original)
-- =============================================================================

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- ESPN API Identification
    espn_team_id TEXT NOT NULL,          -- ESPN's team ID or slug (e.g., "detroit-pistons")
    league TEXT NOT NULL,                 -- League identifier (e.g., "nba", "nfl", "epl")
    sport TEXT NOT NULL,                  -- Sport type (e.g., "basketball", "football", "soccer")

    -- Team Information
    team_name TEXT NOT NULL,              -- Full team name (e.g., "Detroit Pistons")
    team_abbrev TEXT,                     -- Team abbreviation (e.g., "DET")
    team_slug TEXT,                       -- Team slug (e.g., "detroit-pistons")
    team_logo_url TEXT,                   -- URL to team logo
    team_color TEXT,                      -- Team primary color (hex)

    -- XMLTV Channel Configuration
    channel_id TEXT NOT NULL UNIQUE,      -- XMLTV channel ID (e.g., "detroit-pistons")
    channel_logo_url TEXT,                -- Optional: Override channel logo URL (uses team_logo_url if not set)

    -- Template Assignment
    template_id INTEGER,                  -- Foreign key to templates table (nullable - unassigned teams don't generate EPG)

    -- Active Status
    active BOOLEAN DEFAULT 1,             -- Is this team active for EPG generation?

    UNIQUE(espn_team_id, league),
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_teams_channel_id ON teams(channel_id);
CREATE INDEX IF NOT EXISTS idx_teams_league ON teams(league);
CREATE INDEX IF NOT EXISTS idx_teams_active ON teams(active);
CREATE INDEX IF NOT EXISTS idx_teams_template ON teams(template_id);

-- =============================================================================
-- SETTINGS TABLE
-- Global application settings (unchanged from original)
-- =============================================================================

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Single row table

    -- EPG Generation Settings
    epg_days_ahead INTEGER DEFAULT 3,       -- How many days of schedule to include
    epg_update_time TEXT DEFAULT '00:00',   -- Daily update time (HH:MM format)
    epg_output_path TEXT DEFAULT '/app/data/teamarr.xml',

    -- ESPN API Settings
    api_base_url TEXT DEFAULT 'https://site.api.espn.com/apis/site/v2/sports',
    api_timeout INTEGER DEFAULT 10,         -- Request timeout in seconds
    api_retry_count INTEGER DEFAULT 3,

    -- Cache Settings
    cache_enabled BOOLEAN DEFAULT 1,
    cache_duration_hours INTEGER DEFAULT 24,

    -- XMLTV Settings
    xmltv_generator_name TEXT DEFAULT 'Teamarr - Dynamic EPG Generator for Sports Channels',
    xmltv_generator_url TEXT DEFAULT 'http://localhost:9195',

    -- Timezone (global EPG timezone - applies to all teams)
    default_timezone TEXT DEFAULT 'America/New_York',

    -- Channel ID Format (template for auto-generating channel IDs)
    -- Available variables: {team_name_pascal}, {team_abbrev}, {team_name}, {team_slug}, {espn_team_id}, {league_id}, {league}, {sport}
    default_channel_id_format TEXT DEFAULT '{team_name_pascal}.{league_id}',

    -- Midnight Crossover Mode (how to handle filler when game crosses midnight)
    midnight_crossover_mode TEXT DEFAULT 'idle',  -- 'postgame' or 'idle'

    -- Game Duration Defaults (in hours)
    game_duration_default REAL DEFAULT 4.0,        -- Fallback if sport not specified
    game_duration_basketball REAL DEFAULT 3.0,     -- NBA, college basketball
    game_duration_football REAL DEFAULT 3.5,       -- NFL, college football
    game_duration_hockey REAL DEFAULT 3.0,         -- NHL, college hockey
    game_duration_baseball REAL DEFAULT 3.5,       -- MLB, college baseball
    game_duration_soccer REAL DEFAULT 2.5,         -- MLS, soccer

    -- Max Program Hours (global default for filler program splitting)
    max_program_hours_default REAL DEFAULT 6.0,

    -- Web App Settings
    web_port INTEGER DEFAULT 9196,
    web_host TEXT DEFAULT '0.0.0.0',

    -- Logging
    log_level TEXT DEFAULT 'INFO',

    -- Auto-generation Settings
    auto_generate_enabled BOOLEAN DEFAULT 1,
    auto_generate_frequency TEXT DEFAULT 'hourly',  -- DEPRECATED: Use cron_expression instead
    schedule_time TEXT DEFAULT '00',                -- DEPRECATED: Use cron_expression instead
    cron_expression TEXT DEFAULT '0 * * * *',       -- Cron expression for scheduling (min hour dom month dow)

    -- Dispatcharr Integration Settings
    dispatcharr_enabled BOOLEAN DEFAULT 0,
    dispatcharr_url TEXT DEFAULT 'http://localhost:9191',
    dispatcharr_username TEXT,
    dispatcharr_password TEXT,              -- Encrypted password
    dispatcharr_epg_id INTEGER,             -- Discovered EPG source ID
    dispatcharr_last_sync TEXT,             -- ISO datetime of last successful refresh

    -- Global Channel Lifecycle Settings (for event-based channels)
    channel_create_timing TEXT DEFAULT 'same_day',   -- stream_available, same_day, day_before, 2_days_before, manual
    channel_delete_timing TEXT DEFAULT 'same_day',   -- stream_removed, same_day, day_after, 2_days_after, manual

    -- Event-based EPG Settings
    include_final_events INTEGER DEFAULT 0,          -- Include completed/final events from today: 0=exclude, 1=include
    event_lookahead_days INTEGER DEFAULT 7,          -- How many days ahead to look for events (1-30)

    -- Time Format Settings
    time_format TEXT DEFAULT '12h',                  -- '12h' or '24h'
    show_timezone BOOLEAN DEFAULT 1,                 -- Show timezone abbreviation (EST, PST, etc.)

    -- Reconciliation Settings
    reconcile_on_epg_generation INTEGER DEFAULT 1,   -- Run reconciliation before EPG generation
    reconcile_on_startup INTEGER DEFAULT 1,          -- Run reconciliation on app startup
    auto_fix_orphan_teamarr INTEGER DEFAULT 1,       -- Auto-mark orphaned records as deleted
    auto_fix_orphan_dispatcharr INTEGER DEFAULT 0,   -- Auto-delete untracked channels (dangerous)
    auto_fix_duplicates INTEGER DEFAULT 0,           -- Auto-merge duplicate channels
    channel_history_retention_days INTEGER DEFAULT 90, -- Days to keep channel history

    -- Default Settings for New Groups
    default_duplicate_event_handling TEXT DEFAULT 'consolidate',  -- ignore, consolidate, separate

    -- Local Caching Settings
    soccer_cache_refresh_frequency TEXT DEFAULT 'weekly',  -- daily, every_3_days, weekly, manual
    team_cache_refresh_frequency TEXT DEFAULT 'weekly',    -- daily, every_3_days, weekly, manual

    -- Global Channel Range Settings (v29)
    channel_range_start INTEGER DEFAULT 101,    -- Starting channel number for auto assignment
    channel_range_end INTEGER DEFAULT 9999,     -- Ending channel number for auto assignment

    -- EPG Generation Counter (v23)
    epg_generation_counter INTEGER DEFAULT 0,   -- Incremented each EPG generation

    -- Schema versioning for migrations
    schema_version INTEGER DEFAULT 32,  -- Current schema version (increment with each migration)

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert default settings
INSERT OR IGNORE INTO settings (id) VALUES (1);

-- =============================================================================
-- SCHEDULE_CACHE TABLE
-- Caches ESPN API schedule responses to reduce API calls
-- =============================================================================

CREATE TABLE IF NOT EXISTS schedule_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,

    -- ESPN API Response
    api_endpoint TEXT NOT NULL,             -- The API URL that was called
    response_data JSON NOT NULL,            -- Full JSON response from ESPN

    -- Cache Metadata
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,                   -- When this cache entry expires

    -- Schedule Window
    schedule_start_date DATE,               -- First game date in response
    schedule_end_date DATE,                 -- Last game date in response

    -- Hash for change detection
    data_hash TEXT,                         -- MD5/SHA hash of response for comparison

    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_schedule_cache_team ON schedule_cache(team_id);
CREATE INDEX IF NOT EXISTS idx_schedule_cache_expires ON schedule_cache(expires_at);

-- =============================================================================
-- TEAM_STATS_CACHE TABLE
-- Caches team statistics, records, standings for template variables
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_stats_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,

    stat_type TEXT NOT NULL,                -- 'record', 'standings', 'statistics', 'roster'
    data JSON NOT NULL,                     -- Statistics data structure

    -- Cache Metadata
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,

    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    UNIQUE(team_id, stat_type)
);

CREATE INDEX IF NOT EXISTS idx_stats_cache_team ON team_stats_cache(team_id);
CREATE INDEX IF NOT EXISTS idx_stats_cache_type ON team_stats_cache(stat_type);

-- =============================================================================
-- H2H_CACHE TABLE
-- Caches head-to-head records between teams
-- =============================================================================

CREATE TABLE IF NOT EXISTS h2h_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,
    opponent_id INTEGER NOT NULL,

    -- Season Series
    season_series JSON,                     -- {"team_wins": 2, "opponent_wins": 1, "games": [...]}

    -- Previous Games
    previous_games JSON,                    -- Array of past matchups with scores/dates

    -- All-Time Record
    alltime_record JSON,                    -- {"team_wins": 145, "opponent_wins": 132}

    -- Cache Metadata
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,

    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    UNIQUE(team_id, opponent_id)
);

CREATE INDEX IF NOT EXISTS idx_h2h_team ON h2h_cache(team_id);
CREATE INDEX IF NOT EXISTS idx_h2h_opponent ON h2h_cache(opponent_id);

-- =============================================================================
-- EPG_HISTORY TABLE
-- Tracks generated EPG files for auditing and rollback
-- =============================================================================

CREATE TABLE IF NOT EXISTS epg_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Generation Info
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_path TEXT NOT NULL,
    file_size INTEGER,                      -- File size in bytes

    -- Content Metadata
    num_channels INTEGER,                   -- Number of channels in EPG
    num_programmes INTEGER,                 -- Number of programmes in EPG (includes filler)
    num_events INTEGER,                     -- Number of actual sporting events (excludes filler)
    num_pregame INTEGER DEFAULT 0,          -- Number of pregame filler programs
    num_postgame INTEGER DEFAULT 0,         -- Number of postgame filler programs
    num_idle INTEGER DEFAULT 0,             -- Number of idle filler programs
    date_range_start DATE,                  -- First programme date
    date_range_end DATE,                    -- Last programme date

    -- Teams Included
    team_ids JSON,                          -- Array of team IDs included in this EPG

    -- Generation Stats
    generation_time_seconds REAL,           -- How long generation took
    api_calls_made INTEGER,                 -- Number of ESPN API calls
    cache_hits INTEGER,                     -- Number of cache hits

    -- Team-based EPG Stats (Single Source of Truth)
    team_based_channels INTEGER DEFAULT 0,
    team_based_events INTEGER DEFAULT 0,
    team_based_pregame INTEGER DEFAULT 0,
    team_based_postgame INTEGER DEFAULT 0,
    team_based_idle INTEGER DEFAULT 0,

    -- Event-based EPG Stats
    event_based_channels INTEGER DEFAULT 0,
    event_based_events INTEGER DEFAULT 0,
    event_based_pregame INTEGER DEFAULT 0,
    event_based_postgame INTEGER DEFAULT 0,

    -- Event Filtering Stats (aggregated from all groups)
    event_filtered_no_indicator INTEGER DEFAULT 0,       -- No vs/@/at
    event_filtered_include_regex INTEGER DEFAULT 0,      -- Didn't match inclusion regex
    event_filtered_exclude_regex INTEGER DEFAULT 0,      -- Matched exclusion regex
    event_filtered_outside_lookahead INTEGER DEFAULT 0,  -- Past events
    event_filtered_final INTEGER DEFAULT 0,              -- Final events excluded
    event_filtered_league_not_enabled INTEGER DEFAULT 0, -- League not enabled
    event_filtered_unsupported_sport INTEGER DEFAULT 0,  -- Unsupported sports

    -- Quality/Error Stats
    unresolved_vars_count INTEGER DEFAULT 0,
    coverage_gaps_count INTEGER DEFAULT 0,
    warnings_json TEXT,                     -- JSON array of warning messages

    -- File Hash
    file_hash TEXT,                         -- SHA256 hash for change detection

    -- Status
    status TEXT DEFAULT 'success',          -- 'success', 'error', 'partial'
    error_message TEXT,

    -- Trigger Source (added in migration 24)
    triggered_by TEXT DEFAULT 'manual'      -- 'manual', 'scheduler', 'api'
);

CREATE INDEX IF NOT EXISTS idx_epg_history_generated ON epg_history(generated_at);

-- =============================================================================
-- ERROR_LOG TABLE
-- Logs errors and warnings during operation
-- =============================================================================

CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Error Classification
    level TEXT NOT NULL,                    -- 'ERROR', 'WARNING', 'INFO'
    category TEXT,                          -- 'API', 'DATABASE', 'GENERATION', 'WEB'

    -- Error Details
    message TEXT NOT NULL,
    details JSON,                           -- Additional context

    -- Related Objects
    team_id INTEGER,

    -- Stack Trace
    stack_trace TEXT,

    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_error_log_timestamp ON error_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_error_log_level ON error_log(level);

-- =============================================================================
-- LEAGUE_CONFIG TABLE
-- Configuration for supported leagues (mostly static data)
-- =============================================================================

CREATE TABLE IF NOT EXISTS league_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- League Identification
    league_code TEXT NOT NULL UNIQUE,       -- 'nba', 'nfl', 'epl', etc.
    league_name TEXT NOT NULL,              -- 'NBA', 'NFL', 'English Premier League'
    sport TEXT NOT NULL,                    -- 'basketball', 'football', 'soccer'

    -- ESPN API Configuration
    api_path TEXT NOT NULL,                 -- 'basketball/nba'

    -- Default Settings
    default_category TEXT,                  -- Default XMLTV category

    -- Record Format
    record_format TEXT DEFAULT 'wins-losses', -- 'wins-losses' or 'wins-losses-ties'

    -- League Logo
    logo_url TEXT,                          -- URL to league logo image

    -- Gracenote Category (v27)
    gracenote_category TEXT,                -- Gracenote/Schedules Direct category name

    -- Active Status
    active BOOLEAN DEFAULT 1
);

-- Pre-populate league configurations
-- NOTE: league_code uses ESPN slugs for consistency with multi-league detection
INSERT OR IGNORE INTO league_config (league_code, league_name, sport, api_path, default_category, record_format, logo_url) VALUES
    -- US Sports (league_code matches ESPN slug)
    ('nba', 'NBA', 'basketball', 'basketball/nba', 'Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png'),
    ('wnba', 'WNBA', 'basketball', 'basketball/wnba', 'Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png'),
    ('nba-development', 'NBA G League', 'basketball', 'basketball/nba-development', 'Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/nba_gleague.png'),
    ('nfl', 'NFL', 'football', 'football/nfl', 'Football', 'wins-losses-ties', 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png'),
    ('mlb', 'MLB', 'baseball', 'baseball/mlb', 'Baseball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png'),
    ('nhl', 'NHL', 'hockey', 'hockey/nhl', 'Hockey', 'wins-losses-ties', 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png'),
    -- College Sports (league_code uses ESPN slug)
    ('college-football', 'NCAA Football', 'football', 'football/college-football', 'College Football', 'wins-losses', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/football.png'),
    ('mens-college-basketball', 'NCAA Men''s Basketball', 'basketball', 'basketball/mens-college-basketball', 'College Basketball', 'wins-losses', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png'),
    ('womens-college-basketball', 'NCAA Women''s Basketball', 'basketball', 'basketball/womens-college-basketball', 'College Basketball', 'wins-losses', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png'),
    ('mens-college-hockey', 'NCAA Men''s Hockey', 'hockey', 'hockey/mens-college-hockey', 'Hockey', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png'),
    ('womens-college-hockey', 'NCAA Women''s Hockey', 'hockey', 'hockey/womens-college-hockey', 'Hockey', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png'),
    ('mens-college-volleyball', 'NCAA Men''s Volleyball', 'volleyball', 'volleyball/mens-college-volleyball', 'Volleyball', 'wins-losses', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png'),
    ('womens-college-volleyball', 'NCAA Women''s Volleyball', 'volleyball', 'volleyball/womens-college-volleyball', 'Volleyball', 'wins-losses', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png'),
    -- Soccer (league_code uses ESPN slug)
    ('usa.1', 'MLS', 'soccer', 'soccer/usa.1', 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/19.png'),
    ('usa.nwsl', 'NWSL', 'soccer', 'soccer/usa.nwsl', 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2323.png'),
    ('eng.1', 'English Premier League', 'soccer', 'soccer/eng.1', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/23.png'),
    ('eng.2', 'EFL Championship', 'soccer', 'soccer/eng.2', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/24.png'),
    ('eng.3', 'EFL League One', 'soccer', 'soccer/eng.3', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/25.png'),
    ('esp.1', 'La Liga', 'soccer', 'soccer/esp.1', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/15.png'),
    ('ger.1', 'Bundesliga', 'soccer', 'soccer/ger.1', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/10.png'),
    ('ita.1', 'Serie A', 'soccer', 'soccer/ita.1', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/12.png'),
    ('fra.1', 'Ligue 1', 'soccer', 'soccer/fra.1', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/9.png'),
    ('swe.1', 'Swedish Allsvenskan', 'soccer', 'soccer/swe.1', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/16.png'),
    ('uefa.champions', 'UEFA Champions League', 'soccer', 'soccer/uefa.champions', 'Soccer', 'wins-draws-losses', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2.png'),
    -- NCAA Soccer (league_code uses ESPN slug)
    ('usa.ncaa.m.1', 'NCAA Men''s Soccer', 'soccer', 'soccer/usa.ncaa.m.1', 'Soccer', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png'),
    ('usa.ncaa.w.1', 'NCAA Women''s Soccer', 'soccer', 'soccer/usa.ncaa.w.1', 'Soccer', 'wins-losses-ties', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png');

CREATE INDEX IF NOT EXISTS idx_league_code ON league_config(league_code);

-- =============================================================================
-- LEAGUE_ID_ALIASES TABLE
-- Admin-managed mappings from ESPN slugs to friendly short codes
-- Used by {league_id} template variable for consistent output
-- =============================================================================

CREATE TABLE IF NOT EXISTS league_id_aliases (
    espn_slug TEXT PRIMARY KEY,             -- ESPN API slug (e.g., 'eng.1', 'mens-college-basketball')
    alias TEXT NOT NULL,                    -- Friendly short code (e.g., 'epl', 'ncaam')
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Pre-populate league aliases for friendly {league_id} output
INSERT OR IGNORE INTO league_id_aliases (espn_slug, alias) VALUES
    -- Soccer
    ('eng.1', 'epl'),
    ('esp.1', 'laliga'),
    ('ger.1', 'bundesliga'),
    ('ita.1', 'seriea'),
    ('fra.1', 'ligue1'),
    ('usa.1', 'mls'),
    ('usa.nwsl', 'nwsl'),
    ('eng.2', 'efl'),
    ('eng.3', 'efl1'),
    ('uefa.champions', 'ucl'),
    -- College sports
    ('mens-college-basketball', 'ncaam'),
    ('womens-college-basketball', 'ncaaw'),
    ('college-football', 'ncaaf'),
    ('mens-college-hockey', 'ncaah'),
    ('womens-college-hockey', 'ncaawh'),
    ('nba-development', 'nbag'),
    ('mens-college-volleyball', 'ncaavbm'),
    ('womens-college-volleyball', 'ncaavbw'),
    ('usa.ncaa.m.1', 'ncaas'),
    ('usa.ncaa.w.1', 'ncaaws');

-- =============================================================================
-- CONDITION_PRESETS TABLE
-- Stores reusable condition templates for the preset library
-- =============================================================================

CREATE TABLE IF NOT EXISTS condition_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Preset Information
    name TEXT NOT NULL,                     -- Preset name (e.g., "Home Game")
    description TEXT,                       -- Description of what this preset does

    -- Condition Configuration
    condition_type TEXT NOT NULL,           -- 'is_home', 'is_away', 'opponent_is', etc.
    condition_value TEXT DEFAULT '',        -- Value for the condition (if applicable)
    priority INTEGER DEFAULT 50,            -- Priority level (1-100)

    -- Template
    template TEXT NOT NULL,                 -- The description template to use

    -- Usage Tracking
    usage_count INTEGER DEFAULT 0,          -- How many times this preset has been used

    -- Active Status
    active BOOLEAN DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_condition_presets_type ON condition_presets(condition_type);
CREATE INDEX IF NOT EXISTS idx_condition_presets_usage ON condition_presets(usage_count);

-- Pre-populate with example presets
INSERT OR IGNORE INTO condition_presets (id, name, description, condition_type, condition_value, priority, template) VALUES
    (1, 'Home Game', 'Standard template for home games', 'is_home', '', 75, '{team_name} hosts {opponent} at {venue}. Game starts at {game_time}.'),
    (2, 'Away Game', 'Standard template for away games', 'is_away', '', 75, '{team_name} travels to face {opponent} at {venue_full}.'),
    (3, 'Win Streak', 'Template for teams on a winning streak', 'win_streak', '3', 85, '{team_name} looks to extend their {win_streak}-game win streak against {opponent}!'),
    (4, 'Losing Streak', 'Template for teams on a losing streak', 'loss_streak', '3', 85, '{team_name} seeks to end their {loss_streak}-game losing streak vs {opponent}.'),
    (5, 'Has Betting Odds', 'Template when betting odds are available', 'has_odds', '', 80, '{team_name} vs {opponent} - {team_name} {odds_spread}, O/U {odds_over_under}');

-- =============================================================================
-- TRIGGERS
-- Automatic timestamp updates
-- =============================================================================

-- Update templates.updated_at on modification
CREATE TRIGGER IF NOT EXISTS update_templates_timestamp
AFTER UPDATE ON templates
FOR EACH ROW
BEGIN
    UPDATE templates SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- Update teams.updated_at on modification
CREATE TRIGGER IF NOT EXISTS update_teams_timestamp
AFTER UPDATE ON teams
FOR EACH ROW
BEGIN
    UPDATE teams SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- Update settings.updated_at on modification
CREATE TRIGGER IF NOT EXISTS update_settings_timestamp
AFTER UPDATE ON settings
FOR EACH ROW
BEGIN
    UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- =============================================================================
-- VIEWS
-- Useful data views for reporting and querying
-- =============================================================================

-- Active teams with template and league information
CREATE VIEW IF NOT EXISTS v_active_teams AS
SELECT
    t.id,
    t.team_name,
    t.channel_id,
    t.league,
    lc.league_name,
    lc.sport,
    t.active,
    t.template_id,
    tp.name as template_name
FROM teams t
LEFT JOIN league_config lc ON t.league = lc.league_code
LEFT JOIN templates tp ON t.template_id = tp.id
WHERE t.active = 1;

-- Template usage counts
CREATE VIEW IF NOT EXISTS v_template_usage AS
SELECT
    tp.id,
    tp.name,
    tp.sport,
    tp.league,
    COUNT(t.id) as team_count
FROM templates tp
LEFT JOIN teams t ON tp.id = t.template_id
GROUP BY tp.id, tp.name, tp.sport, tp.league
ORDER BY team_count DESC;

-- EPG generation summary
CREATE VIEW IF NOT EXISTS v_epg_summary AS
SELECT
    DATE(generated_at) as generation_date,
    COUNT(*) as generations_count,
    AVG(generation_time_seconds) as avg_generation_time,
    SUM(num_programmes) as total_programmes,
    SUM(api_calls_made) as total_api_calls
FROM epg_history
GROUP BY DATE(generated_at)
ORDER BY generation_date DESC;

-- Recent errors
CREATE VIEW IF NOT EXISTS v_recent_errors AS
SELECT
    timestamp,
    level,
    category,
    message,
    t.team_name
FROM error_log el
LEFT JOIN teams t ON el.team_id = t.id
ORDER BY timestamp DESC
LIMIT 100;

-- =============================================================================
-- EVENT EPG GROUPS TABLE (Event Channel EPG Feature)
-- Stores enabled M3U channel groups for event-based EPG generation
-- =============================================================================

CREATE TABLE IF NOT EXISTS event_epg_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Dispatcharr Integration
    dispatcharr_group_id INTEGER NOT NULL UNIQUE,  -- Group ID from Dispatcharr
    dispatcharr_account_id INTEGER NOT NULL,       -- M3U Account ID (for refresh + UI)
    group_name TEXT NOT NULL,                      -- Exact group name (e.g., "USA | NFL Backup 🏈")
    account_name TEXT,                             -- M3U account name (for UI display)

    -- League/Sport Assignment
    assigned_league TEXT NOT NULL,                 -- League code (e.g., "nfl", "epl", "nba")
    assigned_sport TEXT NOT NULL,                  -- Sport type (e.g., "football", "soccer")

    -- Template Assignment
    event_template_id INTEGER REFERENCES templates(id) ON DELETE SET NULL,

    -- Status
    enabled INTEGER DEFAULT 1,                     -- Is this group enabled for EPG generation?
    refresh_interval_minutes INTEGER DEFAULT 60,   -- How often to regenerate EPG

    -- Channel Lifecycle Settings
    channel_start INTEGER,                         -- Starting channel number for this group
    channel_create_timing TEXT DEFAULT 'same_day', -- When to create: stream_available, same_day, day_before, 2_days_before, manual
    channel_delete_timing TEXT DEFAULT 'same_day', -- When to delete: stream_removed, same_day, day_after, 2_days_after, manual
    channel_group_id INTEGER,                      -- Dispatcharr channel group to create channels in
    channel_group_name TEXT,                       -- Dispatcharr channel group name (for UI display)
    stream_profile_id INTEGER,                     -- Dispatcharr stream profile
    channel_profile_id INTEGER,                    -- Dispatcharr channel profile (legacy - single)
    channel_profile_ids TEXT,                      -- Dispatcharr channel profiles (JSON array)

    -- Parent/Child Group Relationship
    parent_group_id INTEGER REFERENCES event_epg_groups(id),  -- NULL = parent, set = child
    duplicate_event_handling TEXT DEFAULT 'consolidate',       -- ignore, consolidate, separate

    -- Multi-Sport Mode (v16)
    is_multi_sport INTEGER DEFAULT 0,              -- 1 = detect league per-stream
    enabled_leagues TEXT,                          -- JSON array of league codes (NULL = all)
    channel_sort_order TEXT DEFAULT 'time',        -- time, sport_time, league_time
    overlap_handling TEXT DEFAULT 'add_stream',    -- add_stream, add_only, create_all, skip

    -- Channel Assignment Mode (v30)
    channel_assignment_mode TEXT DEFAULT 'auto',   -- 'auto' or 'manual'
    sort_order INTEGER DEFAULT 0,                  -- For AUTO groups: drag-and-drop priority

    -- Stats (updated after each generation)
    last_refresh TIMESTAMP,                        -- Last time EPG was generated
    total_stream_count INTEGER DEFAULT 0,          -- Raw stream count from provider
    stream_count INTEGER DEFAULT 0,                -- Eligible streams (after filtering/exclusions)
    matched_count INTEGER DEFAULT 0,               -- Number of streams matched to ESPN events

    -- Filtering Stats (for match rate calculation and UI display)
    filtered_no_indicator INTEGER DEFAULT 0,       -- No vs/@/at (built-in filter)
    filtered_include_regex INTEGER DEFAULT 0,      -- Didn't match user's inclusion regex
    filtered_exclude_regex INTEGER DEFAULT 0,      -- Matched user's exclusion regex
    filtered_outside_lookahead INTEGER DEFAULT 0,  -- Date outside lookahead window (past events)
    filtered_final INTEGER DEFAULT 0,              -- Final events (when exclude setting on)
    filtered_league_not_enabled INTEGER DEFAULT 0, -- Event in league not enabled for this group
    filtered_unsupported_sport INTEGER DEFAULT 0   -- Beach soccer, boxing/MMA, futsal
);

CREATE INDEX IF NOT EXISTS idx_event_epg_groups_league ON event_epg_groups(assigned_league);
CREATE INDEX IF NOT EXISTS idx_event_epg_groups_enabled ON event_epg_groups(enabled);
-- Note: idx_eeg_parent is created in migrations after parent_group_id column is added

-- Trigger for updated_at
CREATE TRIGGER IF NOT EXISTS update_event_epg_groups_timestamp
AFTER UPDATE ON event_epg_groups
FOR EACH ROW
BEGIN
    UPDATE event_epg_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- =============================================================================
-- CONSOLIDATION EXCEPTION KEYWORDS TABLE
-- Global keywords that override default duplicate_event_handling when matched
-- =============================================================================

CREATE TABLE IF NOT EXISTS consolidation_exception_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keywords TEXT NOT NULL UNIQUE,                 -- Comma-separated keyword variants (case-insensitive)
    behavior TEXT NOT NULL DEFAULT 'consolidate',  -- consolidate, separate, ignore
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Default language keywords - user can modify or delete these
-- First keyword in each list is the "canonical" name shown in EPG variables
-- UNIQUE constraint on keywords prevents duplicates on repeated schema runs
INSERT OR IGNORE INTO consolidation_exception_keywords (keywords, behavior) VALUES
    ('Spanish, En Español, (ESP), Español', 'consolidate'),
    ('French, En Français, (FRA), Français', 'consolidate'),
    ('German, (GER), Deutsch', 'consolidate'),
    ('Portuguese, (POR), Português', 'consolidate'),
    ('Italian, (ITA), Italiano', 'consolidate'),
    ('Arabic, (ARA), العربية', 'consolidate');

-- =============================================================================
-- TEAM ALIASES TABLE (Event Channel EPG Feature)
-- User-defined team name aliases for matching stream names to ESPN teams
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Alias Definition
    alias TEXT NOT NULL,                           -- Alias string (lowercase, normalized) e.g., "spurs", "man u"
    league TEXT NOT NULL,                          -- League code (e.g., "epl", "nfl")

    -- ESPN Team Mapping
    espn_team_id TEXT NOT NULL,                    -- ESPN's team ID
    espn_team_name TEXT NOT NULL,                  -- ESPN's team name (e.g., "Tottenham Hotspur")

    UNIQUE(alias, league)
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_league ON team_aliases(league);
CREATE INDEX IF NOT EXISTS idx_team_aliases_alias ON team_aliases(alias);

-- =============================================================================
-- MANAGED CHANNELS TABLE (Channel Lifecycle Management v2)
-- Tracks channels created by Teamarr in Dispatcharr
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- ========== IDENTITY ==========
    event_epg_group_id INTEGER NOT NULL REFERENCES event_epg_groups(id) ON DELETE CASCADE,
    dispatcharr_channel_id INTEGER NOT NULL UNIQUE,
    dispatcharr_uuid TEXT UNIQUE,            -- Immutable UUID from Dispatcharr (authoritative identifier)
    espn_event_id TEXT NOT NULL,
    tvg_id TEXT NOT NULL,
    primary_stream_id INTEGER,               -- Stream that created this channel (for 'separate' mode)

    -- ========== CHANNEL SETTINGS ==========
    channel_number INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    channel_group_id INTEGER,                -- Dispatcharr channel group
    stream_profile_id INTEGER,               -- Dispatcharr stream profile
    channel_profile_id INTEGER,              -- Dispatcharr channel profile (legacy - single)
    channel_profile_ids TEXT,                -- Dispatcharr channel profiles (JSON array)
    dispatcharr_logo_id INTEGER,
    logo_url TEXT,                           -- Source URL used for logo

    -- ========== LEGACY (kept for backwards compatibility) ==========
    dispatcharr_stream_id INTEGER,           -- Primary stream (now in managed_channel_streams)

    -- ========== EVENT CONTEXT ==========
    home_team TEXT,
    home_team_abbrev TEXT,
    home_team_logo TEXT,
    away_team TEXT,
    away_team_abbrev TEXT,
    away_team_logo TEXT,
    event_date TEXT,                         -- ISO datetime (UTC)
    event_name TEXT,
    league TEXT,
    sport TEXT,
    venue TEXT,
    broadcast TEXT,

    -- ========== LIFECYCLE ==========
    scheduled_delete_at TIMESTAMP,
    deleted_at TIMESTAMP,
    delete_reason TEXT,
    logo_deleted INTEGER,                    -- 1=deleted, 0=failed, NULL=no logo

    -- ========== SYNC STATE ==========
    last_verified_at TEXT,
    sync_status TEXT DEFAULT 'created',      -- created, in_sync, drifted, orphaned
    sync_notes TEXT,

    -- ========== EXCEPTION KEYWORDS ==========
    exception_keyword TEXT                   -- Keyword that created this channel (for keyword-based grouping)
);

CREATE INDEX IF NOT EXISTS idx_managed_channels_group ON managed_channels(event_epg_group_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_event ON managed_channels(espn_event_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_delete ON managed_channels(scheduled_delete_at);
CREATE INDEX IF NOT EXISTS idx_mc_dispatcharr_id ON managed_channels(dispatcharr_channel_id);
CREATE INDEX IF NOT EXISTS idx_mc_tvg_id ON managed_channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_mc_sync_status ON managed_channels(sync_status) WHERE deleted_at IS NULL;

-- Trigger for updated_at
CREATE TRIGGER IF NOT EXISTS update_managed_channels_timestamp
AFTER UPDATE ON managed_channels
FOR EACH ROW
BEGIN
    UPDATE managed_channels SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- =============================================================================
-- MANAGED CHANNEL STREAMS TABLE (Multi-Stream Support)
-- Tracks all streams attached to a managed channel
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channel_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    managed_channel_id INTEGER NOT NULL,
    dispatcharr_stream_id INTEGER NOT NULL,

    -- ========== STREAM INFO ==========
    stream_name TEXT,
    m3u_account_id INTEGER,
    m3u_account_name TEXT,

    -- ========== SOURCE TRACKING ==========
    source_group_id INTEGER NOT NULL,        -- Which group contributed this stream
    source_group_type TEXT NOT NULL DEFAULT 'parent',  -- 'parent' or 'child'

    -- ========== ORDERING ==========
    priority INTEGER DEFAULT 0,              -- 0 = primary, higher = failover

    -- ========== LIFECYCLE ==========
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    removed_at TEXT,
    remove_reason TEXT,

    -- ========== SYNC STATE ==========
    last_verified_at TEXT,
    in_dispatcharr INTEGER DEFAULT 1,        -- 1=confirmed, 0=missing

    -- ========== EXCEPTION KEYWORDS ==========
    exception_keyword TEXT,                  -- Keyword that matched this stream

    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE,
    FOREIGN KEY (source_group_id) REFERENCES event_epg_groups(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mcs_unique
    ON managed_channel_streams(managed_channel_id, dispatcharr_stream_id)
    WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mcs_stream ON managed_channel_streams(dispatcharr_stream_id);
CREATE INDEX IF NOT EXISTS idx_mcs_source_group ON managed_channel_streams(source_group_id);
CREATE INDEX IF NOT EXISTS idx_mcs_channel ON managed_channel_streams(managed_channel_id);

-- =============================================================================
-- MANAGED CHANNEL HISTORY TABLE (Audit Trail)
-- Logs all changes to managed channels for debugging and auditing
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channel_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    managed_channel_id INTEGER NOT NULL,

    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    change_type TEXT NOT NULL,               -- created, modified, stream_added, stream_removed,
                                             -- stream_reordered, verified, drifted, deleted, restored
    change_source TEXT,                      -- epg_generation, reconciliation, manual, external_sync

    field_name TEXT,                         -- Which field changed (NULL for create/delete)
    old_value TEXT,
    new_value TEXT,

    notes TEXT,

    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id)
);

CREATE INDEX IF NOT EXISTS idx_mch_channel ON managed_channel_history(managed_channel_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_mch_time ON managed_channel_history(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_mch_type ON managed_channel_history(change_type);

-- =============================================================================
-- SOCCER TEAM LEAGUES CACHE
-- Weekly cache mapping team_id → leagues they play in
-- Enables multi-competition EPG for soccer teams
-- =============================================================================

CREATE TABLE IF NOT EXISTS soccer_team_leagues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Team-League Mapping
    espn_team_id TEXT NOT NULL,              -- "364" for Liverpool
    league_slug TEXT NOT NULL,               -- "eng.1", "uefa.champions"

    -- Team Metadata (stable, rarely changes)
    team_name TEXT,                          -- "Liverpool"
    team_abbrev TEXT,                        -- "LIV" (v28)
    team_type TEXT,                          -- "club" or "national"
    -- Note: default_league is NOT stored - fetched on-demand via get_team_default_league()

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
    league_tags TEXT,                        -- JSON array: ["domestic", "club", "league", "mens"]
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

-- =============================================================================
-- TEAM LEAGUE CACHE (Non-Soccer Sports)
-- Maps team names to leagues for multi-sport event groups
-- Parallel structure to soccer_team_leagues but for NHL, NBA, NFL, etc.
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_league_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_code TEXT NOT NULL,               -- 'nhl', 'nba', 'ncaam', etc.
    espn_team_id TEXT NOT NULL,              -- ESPN team ID
    team_name TEXT NOT NULL,                 -- "Nashville Predators"
    team_abbrev TEXT,                        -- "NSH"
    team_short_name TEXT,                    -- "Predators"
    sport TEXT NOT NULL,                     -- "hockey", "basketball", etc.
    UNIQUE(league_code, espn_team_id)
);

CREATE INDEX IF NOT EXISTS idx_tlc_name ON team_league_cache(team_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tlc_abbrev ON team_league_cache(team_abbrev COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tlc_short ON team_league_cache(team_short_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_tlc_league ON team_league_cache(league_code);

-- =============================================================================
-- TEAM LEAGUE CACHE METADATA
-- Tracks cache refresh status (single row)
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_league_cache_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_refresh TEXT,                       -- ISO datetime of last refresh
    leagues_processed INTEGER DEFAULT 0,     -- 12
    teams_indexed INTEGER DEFAULT 0          -- ~1847
);

INSERT OR IGNORE INTO team_league_cache_meta (id) VALUES (1);

-- =============================================================================
-- STREAM MATCH CACHE (v23)
-- Caches stream-to-event matches to avoid expensive tier matching on every
-- EPG generation. Only caches successful matches (with event_id).
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
    -- Contains full normalized event + team_result for template vars
    cached_event_data TEXT NOT NULL,

    -- Housekeeping
    last_seen_generation INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_smc_generation ON stream_match_cache(last_seen_generation);
CREATE INDEX IF NOT EXISTS idx_smc_event_id ON stream_match_cache(event_id);

-- =============================================================================
-- EPG FAILED MATCHES (v24)
-- Stores failed stream matches from each EPG generation for debugging.
-- Cleared at start of each generation, populated during processing.
-- =============================================================================

CREATE TABLE IF NOT EXISTS epg_failed_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    group_name TEXT NOT NULL,
    stream_id INTEGER,
    stream_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    parsed_team1 TEXT,
    parsed_team2 TEXT,
    detection_tier TEXT,
    leagues_checked TEXT,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_efm_generation ON epg_failed_matches(generation_id);
CREATE INDEX IF NOT EXISTS idx_efm_group ON epg_failed_matches(group_id);

-- =============================================================================
-- EPG MATCHED STREAMS (v24)
-- Stores successful stream matches from each EPG generation for debugging.
-- =============================================================================

CREATE TABLE IF NOT EXISTS epg_matched_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    group_name TEXT NOT NULL,
    stream_id INTEGER,
    stream_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_name TEXT,
    detected_league TEXT,
    detection_tier TEXT,
    parsed_team1 TEXT,
    parsed_team2 TEXT,
    home_team TEXT,
    away_team TEXT,
    event_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ems_generation ON epg_matched_streams(generation_id);
CREATE INDEX IF NOT EXISTS idx_ems_group ON epg_matched_streams(group_id);

-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
