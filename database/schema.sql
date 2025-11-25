-- TeamArr - Template-Based Architecture Database Schema
-- SQLite Database Structure
-- Last Updated: November 22, 2025

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
    game_duration_mode TEXT DEFAULT 'default', -- 'default', 'sport', or 'custom'
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
    postgame_description TEXT DEFAULT '{team_name} {result_text} {opponent} - Final: {final_score}',
    postgame_art_url TEXT,

    -- Schedule Filler: Idle Days (Between Games / No Game Days)
    idle_enabled BOOLEAN DEFAULT 1,
    idle_title TEXT DEFAULT '{team_name} Programming',
    idle_subtitle TEXT,
    idle_description TEXT DEFAULT 'Next game: {next_date} at {next_time} vs {next_opponent}',
    idle_art_url TEXT,

    -- Conditional Descriptions (Templates tab)
    description_options JSON DEFAULT '[]'    -- Array of conditional description templates
    -- Structure: [{"condition": "is_home", "template": "...", "priority": 50, "condition_value": "..."}]
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
    xmltv_generator_name TEXT DEFAULT 'Teamarr - Dynamic EPG Generator for Sports Team Channels',
    xmltv_generator_url TEXT DEFAULT 'http://localhost:9195',

    -- Timezone (global EPG timezone - applies to all teams)
    default_timezone TEXT DEFAULT 'America/New_York',

    -- Channel ID Format (template for auto-generating channel IDs)
    -- Available variables: {team_name_pascal}, {team_abbrev}, {team_name}, {team_slug}, {espn_team_id}, {league_id}, {league}, {sport}
    default_channel_id_format TEXT DEFAULT '{team_abbrev}.{league_id}',

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
    auto_generate_frequency TEXT DEFAULT 'hourly',

    -- Dispatcharr Integration Settings
    dispatcharr_enabled BOOLEAN DEFAULT 0,
    dispatcharr_url TEXT DEFAULT 'http://localhost:9191',
    dispatcharr_username TEXT,
    dispatcharr_password TEXT,              -- Encrypted password
    dispatcharr_epg_id INTEGER,             -- Discovered EPG source ID
    dispatcharr_last_sync TEXT,             -- ISO datetime of last successful refresh

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
    date_range_start DATE,                  -- First programme date
    date_range_end DATE,                    -- Last programme date

    -- Teams Included
    team_ids JSON,                          -- Array of team IDs included in this EPG

    -- Generation Stats
    generation_time_seconds REAL,          -- How long generation took
    api_calls_made INTEGER,                -- Number of ESPN API calls
    cache_hits INTEGER,                     -- Number of cache hits

    -- File Hash
    file_hash TEXT,                         -- SHA256 hash for change detection

    -- Status
    status TEXT DEFAULT 'success',          -- 'success', 'error', 'partial'
    error_message TEXT
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
    default_game_duration REAL,             -- Default hours for this sport
    default_category TEXT,                  -- Default XMLTV category

    -- Record Format
    record_format TEXT DEFAULT 'wins-losses', -- 'wins-losses' or 'wins-losses-ties'

    -- League Logo
    logo_url TEXT,                          -- URL to league logo image

    -- Active Status
    active BOOLEAN DEFAULT 1
);

-- Pre-populate league configurations
INSERT OR IGNORE INTO league_config (league_code, league_name, sport, api_path, default_game_duration, default_category, record_format, logo_url) VALUES
    ('nba', 'NBA', 'basketball', 'basketball/nba', 3.0, 'Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png'),
    ('wnba', 'WNBA', 'basketball', 'basketball/wnba', 3.0, 'Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png'),
    ('nfl', 'NFL', 'football', 'football/nfl', 3.5, 'Football', 'wins-losses-ties', 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png'),
    ('mlb', 'MLB', 'baseball', 'baseball/mlb', 3.5, 'Baseball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png'),
    ('nhl', 'NHL', 'hockey', 'hockey/nhl', 3.0, 'Hockey', 'wins-losses-ties', 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png'),
    ('mls', 'MLS', 'soccer', 'soccer/usa.1', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/19.png'),
    ('nwsl', 'NWSL', 'soccer', 'soccer/usa.nwsl', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2323.png'),
    ('epl', 'English Premier League', 'soccer', 'soccer/eng.1', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/23.png'),
    ('efl', 'EFL Championship', 'soccer', 'soccer/eng.2', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/24.png'),
    ('laliga', 'La Liga', 'soccer', 'soccer/esp.1', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/15.png'),
    ('bundesliga', 'Bundesliga', 'soccer', 'soccer/ger.1', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/10.png'),
    ('seriea', 'Serie A', 'soccer', 'soccer/ita.1', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/12.png'),
    ('ligue1', 'Ligue 1', 'soccer', 'soccer/fra.1', 2.0, 'Soccer', 'wins-losses-ties', 'https://a.espncdn.com/i/leaguelogos/soccer/500/9.png'),
    ('ncaaf', 'NCAA Football', 'football', 'football/college-football', 4.0, 'College Football', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/ncaa.png'),
    ('ncaam', 'NCAA Men''s Basketball', 'basketball', 'basketball/mens-college-basketball', 2.5, 'College Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/ncaa.png'),
    ('ncaaw', 'NCAA Women''s Basketball', 'basketball', 'basketball/womens-college-basketball', 2.5, 'College Basketball', 'wins-losses', 'https://a.espncdn.com/i/teamlogos/leagues/500/ncaa.png');

CREATE INDEX IF NOT EXISTS idx_league_code ON league_config(league_code);

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
-- END OF SCHEMA
-- =============================================================================
