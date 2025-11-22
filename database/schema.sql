-- TeamArr Sports EPG Generator Database Schema
-- SQLite Database Structure
-- Last Updated: November 18, 2025

-- =============================================================================
-- TEAMS TABLE
-- Stores user-configured sports teams and all EPG generation settings
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
    team_logo_url TEXT,                   -- URL to team logo
    team_color TEXT,                      -- Team primary color (hex)

    -- XMLTV Channel Configuration
    channel_id TEXT NOT NULL UNIQUE,      -- XMLTV channel ID (e.g., "detroit-pistons")

    -- Programme Metadata
    title_format TEXT DEFAULT '{team_name} Basketball', -- Programme title template
    description_template TEXT,            -- Programme description template with variables
    subtitle_template TEXT DEFAULT '{venue_full}',    -- Programme subtitle template

    -- Game Timing
    game_duration_mode TEXT DEFAULT 'default', -- 'default', 'sport', or 'custom'
    game_duration_override REAL,          -- Custom override value (only used if mode='custom')
    timezone TEXT DEFAULT 'America/New_York', -- User's preferred timezone

    -- XMLTV Flags
    flags JSON DEFAULT '{"new": true, "live": false, "premiere": false}',
    -- Structure: {"new": boolean, "live": boolean, "premiere": boolean, "subtitles": boolean}

    -- XMLTV Categories
    categories JSON DEFAULT '["Sports"]',
    -- Array of category strings, e.g., ["Sports", "Basketball", "HD", "Live"]

    -- Schedule Filler: No Game Day
    no_game_enabled BOOLEAN DEFAULT 1,
    no_game_title TEXT DEFAULT 'No Game Today',
    no_game_description TEXT DEFAULT 'No {team_name} game scheduled today. Next game: {next_game_date} vs {next_opponent}',
    no_game_duration REAL DEFAULT 24.0,   -- How many hours to show (24 = all day)

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

    -- Schedule Filler: Idle Days (Between Games / No Game Days)
    idle_enabled BOOLEAN DEFAULT 1,
    idle_title TEXT DEFAULT '{team_name} Programming',
    idle_description TEXT DEFAULT 'Next game: {next_date} at {next_time} vs {next_opponent}',

    -- Simplified Pregame/Postgame Templates (alternative to complex JSON periods)
    pregame_title TEXT DEFAULT 'Pregame Coverage',
    pregame_description TEXT DEFAULT '{team_name} plays {opponent} today at {game_time}',
    postgame_title TEXT DEFAULT 'Postgame Recap',
    postgame_description TEXT DEFAULT '{team_name} {result_text} {opponent} - Final: {final_score}',

    -- Template Variable Features
    enable_records BOOLEAN DEFAULT 1,         -- Enable {team_record}, {opponent_record}
    enable_streaks BOOLEAN DEFAULT 1,         -- Enable {win_streak}, {loss_streak}
    enable_head_to_head BOOLEAN DEFAULT 1,    -- Enable {previous_score}, {season_series}
    enable_standings BOOLEAN DEFAULT 1,       -- Enable {team_rank}, {playoff_seed}
    enable_statistics BOOLEAN DEFAULT 1,      -- Enable {team_ppg}, {team_papg}
    enable_players BOOLEAN DEFAULT 1,         -- Enable {top_scorer_name}, etc.

    -- Conditional Descriptions (Templates tab)
    description_options JSON DEFAULT '[]',    -- Array of conditional description templates
    -- Structure: [{"condition_type": "is_home", "template": "...", "priority": 50, "condition_value": "..."}]

    -- Program Display Settings
    midnight_crossover_mode TEXT DEFAULT 'postgame',  -- How to handle games crossing midnight
    max_program_hours_mode TEXT DEFAULT 'default',    -- 'default' or 'custom'
    max_program_hours REAL DEFAULT 6.0,       -- Maximum duration for a single program (used when mode='custom')
    categories_apply_to TEXT DEFAULT 'events', -- 'all' or 'events' - control category application

    -- Active Status
    active BOOLEAN DEFAULT 1,                 -- Is this team active for EPG generation?

    UNIQUE(espn_team_id, league)
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_teams_channel_id ON teams(channel_id);
CREATE INDEX IF NOT EXISTS idx_teams_league ON teams(league);
CREATE INDEX IF NOT EXISTS idx_teams_active ON teams(active);

-- =============================================================================
-- SETTINGS TABLE
-- Global application settings
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
    xmltv_generator_name TEXT DEFAULT 'Teamarr - Dynamic Sports Team EPG Generator',
    xmltv_generator_url TEXT DEFAULT 'http://localhost:9195',

    -- Timezone
    default_timezone TEXT DEFAULT 'America/New_York',

    -- Game Duration (global default in hours)
    game_duration_default REAL DEFAULT 4.0,

    -- Max Program Hours (global default for filler program splitting)
    max_program_hours_default REAL DEFAULT 6.0,

    -- Web App Settings
    web_port INTEGER DEFAULT 9195,
    web_host TEXT DEFAULT '0.0.0.0',

    -- Logging
    log_level TEXT DEFAULT 'INFO',

    -- Auto-generation Settings
    auto_generate_enabled BOOLEAN DEFAULT 1,
    auto_generate_frequency TEXT DEFAULT 'hourly',

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

    -- Active Status
    active BOOLEAN DEFAULT 1
);

-- Pre-populate league configurations
INSERT OR IGNORE INTO league_config (league_code, league_name, sport, api_path, default_game_duration, default_category, record_format) VALUES
    ('nba', 'NBA', 'basketball', 'basketball/nba', 3.0, 'Basketball', 'wins-losses'),
    ('nfl', 'NFL', 'football', 'football/nfl', 3.5, 'Football', 'wins-losses-ties'),
    ('mlb', 'MLB', 'baseball', 'baseball/mlb', 3.5, 'Baseball', 'wins-losses'),
    ('nhl', 'NHL', 'hockey', 'hockey/nhl', 3.0, 'Hockey', 'wins-losses-ties'),
    ('mls', 'MLS', 'soccer', 'soccer/usa.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('nwsl', 'NWSL', 'soccer', 'soccer/usa.w.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('epl', 'English Premier League', 'soccer', 'soccer/eng.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('efl', 'EFL Championship', 'soccer', 'soccer/eng.2', 2.0, 'Soccer', 'wins-losses-ties'),
    ('laliga', 'La Liga', 'soccer', 'soccer/esp.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('bundesliga', 'Bundesliga', 'soccer', 'soccer/ger.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('seriea', 'Serie A', 'soccer', 'soccer/ita.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('ligue1', 'Ligue 1', 'soccer', 'soccer/fra.1', 2.0, 'Soccer', 'wins-losses-ties'),
    ('ncaaf', 'NCAA Football', 'football', 'football/college-football', 4.0, 'College Football', 'wins-losses'),
    ('ncaam', 'NCAA Men''s Basketball', 'basketball', 'basketball/mens-college-basketball', 2.5, 'College Basketball', 'wins-losses'),
    ('ncaaw', 'NCAA Women''s Basketball', 'basketball', 'basketball/womens-college-basketball', 2.5, 'College Basketball', 'wins-losses');

CREATE INDEX IF NOT EXISTS idx_league_code ON league_config(league_code);

-- =============================================================================
-- TRIGGERS
-- Automatic timestamp updates
-- =============================================================================

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

-- Active teams with league information
CREATE VIEW IF NOT EXISTS v_active_teams AS
SELECT
    t.id,
    t.team_name,
    t.channel_id,
    t.league,
    lc.league_name,
    lc.sport,
    t.active
FROM teams t
LEFT JOIN league_config lc ON t.league = lc.league_code
WHERE t.active = 1;

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
-- SCHEMA MIGRATIONS
-- =============================================================================

-- NOTE: The following ALTER TABLE statements are for migrating existing databases.
-- They should NOT be executed on fresh databases (columns already exist in CREATE TABLE above).
-- Uncomment these lines ONLY when migrating an existing database.

-- ALTER TABLE teams ADD COLUMN pregame_title TEXT DEFAULT 'Pregame Coverage';
-- ALTER TABLE teams ADD COLUMN pregame_description TEXT DEFAULT '{team_name} plays {opponent} today at {game_time}';
-- ALTER TABLE teams ADD COLUMN postgame_title TEXT DEFAULT 'Postgame Recap';
-- ALTER TABLE teams ADD COLUMN postgame_description TEXT DEFAULT '{team_name} {result_text} {opponent} - Final: {final_score}';
-- ALTER TABLE teams ADD COLUMN midnight_crossover_mode TEXT DEFAULT 'postgame';
-- ALTER TABLE teams ADD COLUMN max_program_hours REAL DEFAULT 6.0;
-- ALTER TABLE teams ADD COLUMN categories_apply_to TEXT DEFAULT 'all';
-- ALTER TABLE settings ADD COLUMN max_program_hours_default REAL DEFAULT 6.0;
-- ALTER TABLE teams ADD COLUMN max_program_hours_mode TEXT DEFAULT 'default';

-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
