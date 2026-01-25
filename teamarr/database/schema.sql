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
    xmltv_video JSON DEFAULT '{"enabled": false, "quality": "HDTV"}',
    xmltv_categories JSON DEFAULT '["Sports"]',
    categories_apply_to TEXT DEFAULT 'events' CHECK(categories_apply_to IN ('all', 'events')),

    -- Filler: Pre-Game (uses .next suffix for upcoming game)
    pregame_enabled BOOLEAN DEFAULT 1,
    pregame_periods JSON DEFAULT '[
        {"start_hours_before": 24, "end_hours_before": 6, "title": "Game Preview", "description": "{team_name} plays {opponent.next} in {hours_until.next} hours at {venue.next}"},
        {"start_hours_before": 6, "end_hours_before": 2, "title": "Pre-Game Coverage", "description": "{team_name} vs {opponent.next} starts at {game_time.next}"},
        {"start_hours_before": 2, "end_hours_before": 0, "title": "Game Starting Soon", "description": "{team_name} vs {opponent.next} starts in {hours_until.next} hours"}
    ]',
    pregame_fallback JSON DEFAULT '{"title": "Pregame Coverage", "subtitle": null, "description": "{team_name} plays {opponent.next} today at {game_time.next}", "art_url": null}',

    -- Filler: Post-Game (uses .last suffix for completed game)
    postgame_enabled BOOLEAN DEFAULT 1,
    postgame_periods JSON DEFAULT '[
        {"start_hours_after": 0, "end_hours_after": 3, "title": "Game Recap", "description": "{team_name} {result_text.last} {final_score.last}"},
        {"start_hours_after": 3, "end_hours_after": 12, "title": "Extended Highlights", "description": "Highlights: {team_name} {result_text.last} {final_score.last} vs {opponent.last}"},
        {"start_hours_after": 12, "end_hours_after": 24, "title": "Full Game Replay", "description": "Replay: {team_name} vs {opponent.last}"}
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
    primary_league TEXT NOT NULL,            -- Main league for schedule lookups (from API)
    leagues TEXT NOT NULL DEFAULT '[]',      -- JSON array of ALL leagues (includes primary)
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

    -- One entry per team per league (ESPN reuses IDs across leagues for different teams)
    UNIQUE(provider, provider_team_id, sport, primary_league),
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_teams_channel_id ON teams(channel_id);
CREATE INDEX IF NOT EXISTS idx_teams_active ON teams(active);
CREATE INDEX IF NOT EXISTS idx_teams_provider ON teams(provider);
CREATE INDEX IF NOT EXISTS idx_teams_sport ON teams(sport);

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
    event_match_days_ahead INTEGER DEFAULT 3,       -- Event-stream matching window forward (Event Groups only)
    event_match_days_back INTEGER DEFAULT 7,        -- Event-stream matching window backward (for weekly sports like NFL)
    epg_output_days_ahead INTEGER DEFAULT 14,       -- Days to include in final XMLTV
    epg_lookback_hours INTEGER DEFAULT 6,           -- Check for in-progress games

    -- Channel Lifecycle (for event-based EPG)
    -- Create timing: 'stream_available', 'same_day', 'day_before', '2_days_before', '3_days_before', '1_week_before'
    channel_create_timing TEXT DEFAULT 'same_day' CHECK(channel_create_timing IN ('stream_available', 'same_day', 'day_before', '2_days_before', '3_days_before', '1_week_before')),
    -- Delete timing: 'stream_removed', '6_hours_after', 'same_day', 'day_after', '2_days_after', '3_days_after', '1_week_after'
    channel_delete_timing TEXT DEFAULT 'day_after' CHECK(channel_delete_timing IN ('stream_removed', '6_hours_after', 'same_day', 'day_after', '2_days_after', '3_days_after', '1_week_after')),

    -- Filler Settings
    midnight_crossover_mode TEXT DEFAULT 'postgame' CHECK(midnight_crossover_mode IN ('postgame', 'idle')),

    -- EPG Output
    epg_timezone TEXT DEFAULT 'America/New_York',
    epg_output_path TEXT DEFAULT './data/teamarr.xml',

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
    duration_volleyball REAL DEFAULT 2.5,

    -- XMLTV
    xmltv_generator_name TEXT DEFAULT 'Teamarr',
    xmltv_generator_url TEXT DEFAULT 'https://github.com/Pharaoh-Labs/teamarr',

    -- Display Preferences
    time_format TEXT DEFAULT '12h' CHECK(time_format IN ('12h', '24h')),
    show_timezone BOOLEAN DEFAULT 1,

    -- Event-Based EPG Options
    include_final_events BOOLEAN DEFAULT 0,      -- Include completed events for today
    channel_range_start INTEGER DEFAULT 101,     -- First auto-assigned channel number
    channel_range_end INTEGER,                   -- Last auto-assigned channel (null = no limit)

    -- Default Team Filtering (for Event Groups)
    default_include_teams JSON,                  -- Global include filter [{"provider":"espn","team_id":"33","league":"nfl"}, ...]
    default_exclude_teams JSON,                  -- Global exclude filter (same format)
    default_team_filter_mode TEXT DEFAULT 'include' CHECK(default_team_filter_mode IN ('include', 'exclude')),
    team_filter_enabled BOOLEAN DEFAULT 1,       -- Master toggle to enable/disable team filtering

    -- Scheduled Generation
    cron_expression TEXT DEFAULT '0 * * * *',    -- Cron for auto EPG generation

    -- Cache Refresh Frequencies
    soccer_cache_refresh_frequency TEXT DEFAULT 'weekly',
    team_cache_refresh_frequency TEXT DEFAULT 'weekly',

    -- API
    api_timeout INTEGER DEFAULT 10,
    api_retry_count INTEGER DEFAULT 3,

    -- TheSportsDB API (optional premium key for higher limits)
    -- If not set, uses free API key with 30 req/min and 10 result limits
    -- Premium key ($9/mo) gives 100 req/min and higher limits
    tsdb_api_key TEXT,

    -- Channel ID Format
    channel_id_format TEXT DEFAULT '{team_name_pascal}.{league_id}',

    -- Generation Counter (for cache purging)
    epg_generation_counter INTEGER DEFAULT 0,

    -- Dispatcharr Integration
    dispatcharr_enabled BOOLEAN DEFAULT 0,
    dispatcharr_url TEXT,
    dispatcharr_username TEXT,
    dispatcharr_password TEXT,                -- Note: Consider encrypting in production
    dispatcharr_epg_id INTEGER,               -- Teamarr's EPG source ID in Dispatcharr
    default_channel_profile_ids JSON,         -- Default channel profiles for event channels

    -- Reconciliation Settings
    reconcile_on_epg_generation BOOLEAN DEFAULT 1,
    reconcile_on_startup BOOLEAN DEFAULT 1,
    auto_fix_orphan_teamarr BOOLEAN DEFAULT 1,    -- Auto-delete DB records for missing channels
    auto_fix_orphan_dispatcharr BOOLEAN DEFAULT 1, -- Auto-delete channels in Dispatcharr not tracked by Teamarr
    auto_fix_duplicates BOOLEAN DEFAULT 0,

    -- Duplicate Event Handling
    default_duplicate_event_handling TEXT DEFAULT 'consolidate'
        CHECK(default_duplicate_event_handling IN ('consolidate', 'separate', 'ignore')),

    -- Channel History
    channel_history_retention_days INTEGER DEFAULT 90,

    -- Background Scheduler
    scheduler_enabled BOOLEAN DEFAULT 1,
    scheduler_interval_minutes INTEGER DEFAULT 15,

    -- Stream Filtering (global defaults for event groups)
    -- Require event pattern: only match streams that look like events (have vs/@/at/date patterns)
    stream_filter_require_event_pattern BOOLEAN DEFAULT 1,
    -- Custom inclusion patterns (JSON array of regex patterns) - stream must match at least one
    stream_filter_include_patterns JSON DEFAULT '[]',
    -- Custom exclusion patterns (JSON array of regex patterns) - stream must NOT match any
    stream_filter_exclude_patterns JSON DEFAULT '[]',

    -- Channel Numbering Mode (for AUTO groups)
    -- 'strict_block': Reserve by total_stream_count (current behavior, large gaps, minimal drift)
    -- 'rational_block': Reserve by actual channel count rounded to 10 (smaller gaps, low drift)
    -- 'strict_compact': No reservation, sequential numbers (no gaps, higher drift risk)
    channel_numbering_mode TEXT DEFAULT 'strict_block'
        CHECK(channel_numbering_mode IN ('strict_block', 'rational_block', 'strict_compact')),

    -- Channel Sorting Scope (only applies to rational_block and strict_compact)
    -- 'per_group': Sort channels within each group (current behavior)
    -- 'global': Sort all AUTO channels across groups by sport/league/time
    channel_sorting_scope TEXT DEFAULT 'per_group'
        CHECK(channel_sorting_scope IN ('per_group', 'global')),

    -- Sort order for per-group scope
    -- 'sport_league_time': Sort by sport, then league, then event time
    -- 'time': Sort by event time only
    -- 'stream_order': Keep original stream order from M3U
    channel_sort_by TEXT DEFAULT 'time'
        CHECK(channel_sort_by IN ('sport_league_time', 'time', 'stream_order')),

    -- Stream Ordering Rules (for prioritizing streams within channels)
    -- JSON array of rules: [{"type": "m3u"|"group"|"regex", "value": "...", "priority": 1-99}]
    -- Rules evaluated in priority order; first match wins; non-matching streams get priority 999
    stream_ordering_rules JSON DEFAULT '[]',

    -- Schema Version
    schema_version INTEGER DEFAULT 38
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
    name TEXT NOT NULL,                      -- Unique per m3u_account_id (see index below)
    display_name TEXT,                       -- Optional display name override for UI
    group_mode TEXT DEFAULT 'single'         -- 'single' or 'multi' - preserves original mode
        CHECK(group_mode IN ('single', 'multi')),

    -- What to scan
    leagues JSON NOT NULL,                   -- ["nfl", "nba"] - leagues to scan for events

    -- Template
    template_id INTEGER,

    -- Channel Settings
    channel_start_number INTEGER,            -- Starting channel number for this group
    channel_group_id INTEGER,                -- Dispatcharr channel group to assign (when mode='static')
    channel_group_mode TEXT DEFAULT 'static' -- How to assign channel group
        CHECK(channel_group_mode IN ('static', 'sport', 'league')),
    channel_profile_ids TEXT,                -- JSON array: profile IDs and/or "{sport}", "{league}"

    -- Duplicate Event Handling (uses global lifecycle settings)
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
    m3u_account_id INTEGER,                  -- Dispatcharr M3U account ID
    m3u_account_name TEXT,                   -- M3U account name for display

    -- Processing Stats (updated by EPG generation)
    last_refresh TIMESTAMP,                  -- Last successful EPG refresh
    stream_count INTEGER DEFAULT 0,          -- Streams after filtering
    matched_count INTEGER DEFAULT 0,         -- Successfully matched to events

    -- Stream Filtering (Phase 2)
    stream_include_regex TEXT,               -- Only include streams matching this pattern
    stream_include_regex_enabled BOOLEAN DEFAULT 0,
    stream_exclude_regex TEXT,               -- Exclude streams matching this pattern
    stream_exclude_regex_enabled BOOLEAN DEFAULT 0,
    custom_regex_teams TEXT,                 -- Custom pattern to extract team names
    custom_regex_teams_enabled BOOLEAN DEFAULT 0,
    custom_regex_date TEXT,                  -- Custom pattern to extract date
    custom_regex_date_enabled BOOLEAN DEFAULT 0,
    custom_regex_time TEXT,                  -- Custom pattern to extract time
    custom_regex_time_enabled BOOLEAN DEFAULT 0,
    custom_regex_league TEXT,                -- Custom pattern to extract league hint
    custom_regex_league_enabled BOOLEAN DEFAULT 0,
    skip_builtin_filter BOOLEAN DEFAULT 0,   -- Skip built-in stream filtering (placeholder, unsupported sports, event patterns)

    -- Team Filtering (canonical team selection, inherited by children)
    include_teams JSON,                          -- Teams to include: [{"provider":"espn","team_id":"33","league":"nfl","name":"Ravens"}, ...]
    exclude_teams JSON,                          -- Teams to exclude: same format
    team_filter_mode TEXT DEFAULT 'include'      -- 'include' (whitelist) or 'exclude' (blacklist)
        CHECK(team_filter_mode IN ('include', 'exclude')),

    -- Processing Stats (updated by EPG generation)
    -- Three categories: FILTERED (pre-match), FAILED (match attempted), EXCLUDED (matched but excluded)
    filtered_stale INTEGER DEFAULT 0,           -- FILTERED: Stream marked as stale in Dispatcharr
    filtered_include_regex INTEGER DEFAULT 0,   -- FILTERED: Didn't match include regex
    filtered_exclude_regex INTEGER DEFAULT 0,   -- FILTERED: Matched exclude regex
    filtered_not_event INTEGER DEFAULT 0,       -- FILTERED: Stream doesn't look like event (placeholder)
    filtered_team INTEGER DEFAULT 0,            -- FILTERED: Team not in include/exclude list
    failed_count INTEGER DEFAULT 0,             -- FAILED: Match attempted but couldn't find event
    streams_excluded INTEGER DEFAULT 0,         -- EXCLUDED: Matched but excluded (aggregate)
    -- EXCLUDED breakdown by reason
    excluded_event_final INTEGER DEFAULT 0,         -- Event status is final
    excluded_event_past INTEGER DEFAULT 0,          -- Event already ended
    excluded_before_window INTEGER DEFAULT 0,       -- Too early to create channel
    excluded_league_not_included INTEGER DEFAULT 0, -- League not in group's leagues[]

    -- Multi-Sport Enhancements (Phase 3)
    channel_sort_order TEXT DEFAULT 'time'
        CHECK(channel_sort_order IN ('time', 'sport_time', 'league_time')),
    overlap_handling TEXT DEFAULT 'add_stream'
        CHECK(overlap_handling IN ('add_stream', 'add_only', 'create_all', 'skip')),

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
-- Allow same group name from different M3U accounts (e.g., "US - NFL" from Provider A and B)
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_epg_groups_name_account
    ON event_epg_groups(name, m3u_account_id);


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
    tvg_id TEXT NOT NULL,  -- Not UNIQUE: soft-deleted records can share tvg_id with active
    channel_name TEXT NOT NULL,
    channel_number TEXT,
    logo_url TEXT,

    -- Dispatcharr Integration
    dispatcharr_channel_id INTEGER,          -- Dispatcharr's channel ID
    dispatcharr_uuid TEXT,                   -- Dispatcharr's immutable UUID
    dispatcharr_logo_id INTEGER,             -- Uploaded logo ID in Dispatcharr

    -- Channel Settings (from group config)
    channel_group_id INTEGER,                -- Dispatcharr channel group
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

    FOREIGN KEY (event_epg_group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
    -- Note: No table-level UNIQUE on (event_id, event_provider) - use partial index instead
    -- This allows soft-deleted rows to exist alongside active ones
);

CREATE INDEX IF NOT EXISTS idx_managed_channels_group ON managed_channels(event_epg_group_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_event ON managed_channels(event_id, event_provider);
CREATE INDEX IF NOT EXISTS idx_managed_channels_expires ON managed_channels(expires_at);
CREATE INDEX IF NOT EXISTS idx_managed_channels_delete ON managed_channels(scheduled_delete_at)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_managed_channels_dispatcharr ON managed_channels(dispatcharr_channel_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_tvg ON managed_channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_managed_channels_sync ON managed_channels(sync_status);

-- Unique constraint for event channels
-- Includes primary_stream_id to support 'separate' duplicate handling mode
-- (allows multiple channels per event when each has a different primary stream)
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_unique_event
    ON managed_channels(event_epg_group_id, event_id, event_provider, COALESCE(exception_keyword, ''), primary_stream_id)
    WHERE deleted_at IS NULL;

CREATE TRIGGER IF NOT EXISTS update_managed_channels_timestamp
AFTER UPDATE ON managed_channels
BEGIN
    UPDATE managed_channels SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- =============================================================================
-- SPORTS TABLE
-- Single source of truth for sport display names
-- Used by {sport} template variable for proper casing (e.g., 'MMA' not 'mma')
-- =============================================================================

CREATE TABLE IF NOT EXISTS sports (
    sport_code TEXT PRIMARY KEY,             -- Lowercase internal code: 'football', 'mma'
    display_name TEXT NOT NULL               -- Display format: 'Football', 'MMA'
);

-- Seed sports with proper display names
INSERT OR REPLACE INTO sports (sport_code, display_name) VALUES
    ('football', 'Football'),
    ('basketball', 'Basketball'),
    ('hockey', 'Hockey'),
    ('baseball', 'Baseball'),
    ('softball', 'Softball'),
    ('soccer', 'Soccer'),
    ('mma', 'MMA'),
    ('volleyball', 'Volleyball'),
    ('lacrosse', 'Lacrosse'),
    ('cricket', 'Cricket'),
    ('rugby', 'Rugby'),
    ('boxing', 'Boxing'),
    ('tennis', 'Tennis'),
    ('golf', 'Golf'),
    ('wrestling', 'Wrestling'),
    ('racing', 'Racing'),
    ('australian-football', 'Australian Football');


-- =============================================================================
-- CHANNEL_SORT_PRIORITIES TABLE
-- User-defined sort order for sports/leagues when using global channel sorting
-- Used by channel numbering to determine channel order across all AUTO groups
-- =============================================================================

CREATE TABLE IF NOT EXISTS channel_sort_priorities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Sport/League identification
    sport TEXT NOT NULL,                     -- Sport code (e.g., 'football', 'basketball')
    league_code TEXT,                        -- NULL = sport-level priority only

    -- Sort priority (lower = earlier in channel list)
    sort_priority INTEGER NOT NULL,

    -- Unique constraint: one entry per sport/league combination
    UNIQUE(sport, league_code)
);

CREATE INDEX IF NOT EXISTS idx_channel_sort_priorities_priority
ON channel_sort_priorities(sort_priority);

CREATE TRIGGER IF NOT EXISTS update_channel_sort_priorities_timestamp
AFTER UPDATE ON channel_sort_priorities
BEGIN
    UPDATE channel_sort_priorities SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;


-- =============================================================================
-- LEAGUES TABLE
-- Single source of truth for configured leagues
-- Combines API config + display config in one table
-- =============================================================================

CREATE TABLE IF NOT EXISTS leagues (
    league_code TEXT PRIMARY KEY,            -- 'nfl', 'ohl', 'eng.1'

    -- Provider/API Configuration
    provider TEXT NOT NULL,                  -- 'espn', 'tsdb', 'cricbuzz', 'hockeytech'
    provider_league_id TEXT NOT NULL,        -- ESPN: 'football/nfl', Cricbuzz: '9241/indian-premier-league-2026'
    provider_league_name TEXT,               -- TSDB only: exact strLeague for API calls
    series_slug_pattern TEXT,                -- Cricbuzz only: base slug for auto-discovery (e.g., 'indian-premier-league')
    fallback_provider TEXT,                  -- Fallback provider if primary unavailable (e.g., 'cricbuzz' when TSDB has no premium key)
    fallback_league_id TEXT,                 -- Provider league ID for fallback (e.g., Cricbuzz series ID)
    enabled INTEGER DEFAULT 1,               -- Is this league active?

    -- Display Configuration
    display_name TEXT NOT NULL,              -- 'NFL', 'Ontario Hockey League'
    sport TEXT NOT NULL,                     -- 'football', 'hockey', 'soccer'
    logo_url TEXT,                           -- League logo URL (light mode / primary)
    logo_url_dark TEXT,                      -- League logo URL (dark mode variant)
    import_enabled INTEGER DEFAULT 0,        -- Show in Team Importer?

    -- Template Variables (manually configured, with fallbacks)
    -- {league}: league_alias if set, else display_name (e.g., 'EPL', 'NFL')
    -- {league_id}: league_id if set, else league_code (e.g., 'epl', 'nfl')
    -- {league_name}: always display_name (e.g., 'English Premier League', 'NFL')
    -- {league_code}: always league_code primary key (e.g., 'eng.1', 'nfl')
    league_alias TEXT,                       -- Short display alias for {league} (e.g., 'EPL', 'UCL')
    league_id TEXT,                          -- URL-safe identifier for {league_id} (e.g., 'epl', 'ncaabb')
    gracenote_category TEXT,                 -- Gracenote/Schedules Direct category (e.g., 'NFL Football')

    -- Matching Classification
    -- team_vs_team: Standard team sports (NFL, NBA, NHL, Soccer, etc.)
    -- event_card: Combat sports with cards (UFC, Boxing)
    -- event: Individual/tournament sports (Golf, Tennis, Racing) - future use
    event_type TEXT DEFAULT 'team_vs_team'
        CHECK(event_type IN ('team_vs_team', 'event', 'event_card')),

    -- Cache Metadata (updated by cache refresh)
    cached_team_count INTEGER DEFAULT 0,
    last_cache_refresh TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_leagues_provider ON leagues(provider);
CREATE INDEX IF NOT EXISTS idx_leagues_sport ON leagues(sport);
CREATE INDEX IF NOT EXISTS idx_leagues_import ON leagues(import_enabled);


-- =============================================================================
-- SEED: Configured Leagues (SINGLE SOURCE OF TRUTH)
-- All league configuration lives here. INSERT OR REPLACE ensures updates
-- propagate to existing databases on app startup.
--
-- Template Variable Columns:
--   league_alias: Short display name for {league} (e.g., 'EPL', 'UCL')
--                 If NULL, falls back to display_name
--   league_id:    URL-safe identifier for {league_id} (e.g., 'epl', 'ucl')
--                 If NULL, falls back to league_code
--   display_name: Full name for {league_name} (e.g., 'English Premier League')
--
-- When to set league_alias:
--   - Set when display_name is long but a common short form exists (EPL, UCL)
--   - Leave NULL when display_name is already short (NFL, NBA, MLS)
-- =============================================================================

INSERT OR REPLACE INTO leagues (league_code, provider, provider_league_id, provider_league_name, display_name, sport, logo_url, logo_url_dark, import_enabled, league_alias, league_id, event_type, gracenote_category, fallback_provider, fallback_league_id) VALUES
    -- Football (ESPN)
    ('nfl', 'espn', 'football/nfl', NULL, 'National Football League', 'football', 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png', NULL, 1, 'NFL', 'nfl', 'team_vs_team', 'NFL Football', NULL, NULL),
    ('college-football', 'espn', 'football/college-football', NULL, 'NCAA Football', 'football', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/football.png', NULL, 1, 'NCAAF', 'ncaaf', 'team_vs_team', 'College Football', NULL, NULL),
    ('ufl', 'espn', 'football/ufl', NULL, 'United Football League', 'football', 'https://a.espncdn.com/i/teamlogos/leagues/500/ufl.png', NULL, 1, 'UFL', 'ufl', 'team_vs_team', 'UFL Football', NULL, NULL),

    -- Basketball (ESPN)
    ('nba', 'espn', 'basketball/nba', NULL, 'National Basketball Association', 'basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png', NULL, 1, 'NBA', 'nba', 'team_vs_team', 'NBA Basketball', NULL, NULL),
    ('nba-development', 'espn', 'basketball/nba-development', NULL, 'NBA G League', 'basketball', 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nba_gleague.png', NULL, 1, 'G League', 'nbag', 'team_vs_team', 'NBA G League Basketball', NULL, NULL),
    ('wnba', 'espn', 'basketball/wnba', NULL, 'Women''s National Basketball Association', 'basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png', NULL, 1, 'WNBA', 'wnba', 'team_vs_team', 'WNBA Basketball', NULL, NULL),
    ('mens-college-basketball', 'espn', 'basketball/mens-college-basketball', NULL, 'NCAA Men''s Basketball', 'basketball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png', NULL, 1, 'NCAAM', 'ncaam', 'team_vs_team', 'College Basketball', NULL, NULL),
    ('womens-college-basketball', 'espn', 'basketball/womens-college-basketball', NULL, 'NCAA Women''s Basketball', 'basketball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png', NULL, 1, 'NCAAW', 'ncaaw', 'team_vs_team', 'Women''s College Basketball', NULL, NULL),

    -- Basketball (TSDB) - Leagues not on ESPN
    ('unrivaled', 'tsdb', '5622', 'Unrivaled Basketball', 'Unrivaled', 'basketball', 'https://r2.thesportsdb.com/images/media/league/badge/71mier1746291561.png', NULL, 1, NULL, 'unrivaled', 'team_vs_team', 'Unrivaled Basketball', NULL, NULL),

    -- Hockey (ESPN)
    ('nhl', 'espn', 'hockey/nhl', NULL, 'National Hockey League', 'hockey', 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png', NULL, 1, 'NHL', 'nhl', 'team_vs_team', 'NHL Hockey', NULL, NULL),
    ('mens-college-hockey', 'espn', 'hockey/mens-college-hockey', NULL, 'NCAA Men''s Ice Hockey', 'hockey', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png', NULL, 1, 'NCAA Hockey', 'ncaah', 'team_vs_team', 'College Hockey', NULL, NULL),
    ('womens-college-hockey', 'espn', 'hockey/womens-college-hockey', NULL, 'NCAA Women''s Ice Hockey', 'hockey', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png', NULL, 1, 'NCAA W Hockey', 'ncaawh', 'team_vs_team', 'Women''s College Hockey', NULL, NULL),

    -- Hockey - CHL/Canadian Major Junior (HockeyTech)
    ('chl', 'hockeytech', 'chl', NULL, 'Canadian Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/dev/assets/CHL.png', NULL, 1, 'CHL', 'chl', 'team_vs_team', NULL, NULL, NULL),
    ('ohl', 'hockeytech', 'ohl', NULL, 'Ontario Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/main/assets/OHL_LIGHTMODE.png', 'https://raw.githubusercontent.com/sethwv/game-thumbs/main/assets/OHL_DARKMODE.png', 1, 'OHL', 'ohl', 'team_vs_team', NULL, NULL, NULL),
    ('whl', 'hockeytech', 'whl', NULL, 'Western Hockey League', 'hockey', 'https://media.chl.ca/wp-content/uploads/sites/6/2023/08/18153245/Western_Hockey_League.svg_-1.png', NULL, 1, 'WHL', 'whl', 'team_vs_team', NULL, NULL, NULL),
    ('qmjhl', 'hockeytech', 'lhjmq', NULL, 'Quebec Major Junior Hockey League', 'hockey', 'https://media.chl.ca/wp-content/uploads/sites/2/2023/05/25155229/logo_q_lg.png', NULL, 1, 'QMJHL', 'qmjhl', 'team_vs_team', NULL, NULL, NULL),

    -- Hockey - Pro/Minor Pro Leagues (HockeyTech)
    ('ahl', 'hockeytech', 'ahl', NULL, 'American Hockey League', 'hockey', 'https://theahl.com/wp-content/uploads/sites/3/2025/10/AHL90_500.png', NULL, 1, 'AHL', 'ahl', 'team_vs_team', NULL, NULL, NULL),
    ('echl', 'hockeytech', 'echl', NULL, 'East Coast Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/dev/assets/ECHL.png', NULL, 1, 'ECHL', 'echl', 'team_vs_team', NULL, NULL, NULL),
    ('pwhl', 'hockeytech', 'pwhl', NULL, 'Professional Women''s Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/main/assets/PWHL.png', NULL, 1, 'PWHL', 'pwhl', 'team_vs_team', NULL, NULL, NULL),

    -- Hockey - US Junior (HockeyTech)
    ('ushl', 'hockeytech', 'ushl', NULL, 'United States Hockey League', 'hockey', 'https://dbukjj6eu5tsf.cloudfront.net/ushl.sidearmsports.com/images/responsive_2022/ushl_on-dark.svg', NULL, 1, 'USHL', 'ushl', 'team_vs_team', NULL, NULL, NULL),

    -- Hockey - Canadian Junior A (HockeyTech)
    ('ojhl', 'hockeytech', 'ojhl', NULL, 'Ontario Junior Hockey League', 'hockey', 'https://www.ojhl.ca/wp-content/uploads/sites/2/2023/04/cropped-ojhl-512.png', NULL, 1, 'OJHL', 'ojhl', 'team_vs_team', NULL, NULL, NULL),
    ('bchl', 'hockeytech', 'bchl', NULL, 'British Columbia Hockey League', 'hockey', 'https://bchl.ca/wp-content/uploads/2015/12/BCHL-Footer-Logo.png', NULL, 1, 'BCHL', 'bchl', 'team_vs_team', NULL, NULL, NULL),
    ('sjhl', 'hockeytech', 'sjhl', NULL, 'Saskatchewan Junior Hockey League', 'hockey', 'https://www.sjhl.ca/wp-content/uploads/sites/2/2019/08/SJHL_Logo_512px.png', NULL, 1, 'SJHL', 'sjhl', 'team_vs_team', NULL, NULL, NULL),
    ('ajhl', 'hockeytech', 'ajhl', NULL, 'Alberta Junior Hockey League', 'hockey', 'https://www.ajhl.ca/wp-content/uploads/sites/2/2022/05/cropped-ajhl_512.png', NULL, 1, 'AJHL', 'ajhl', 'team_vs_team', NULL, NULL, NULL),
    ('mjhl', 'hockeytech', 'mjhl', NULL, 'Manitoba Junior Hockey League', 'hockey', 'https://www.mjhlhockey.ca/wp-content/uploads/sites/2/2019/06/cropped-MJHLalternate-web-600x.png', NULL, 1, 'MJHL', 'mjhl', 'team_vs_team', NULL, NULL, NULL),
    ('mhl', 'hockeytech', 'mhl', NULL, 'Maritime Junior Hockey League', 'hockey', 'https://www.themhl.ca/wp-content/uploads/sites/2/2021/10/cropped-mhl_512.png', NULL, 1, 'MHL', 'mhl', 'team_vs_team', NULL, NULL, NULL),

    -- Hockey - European Leagues (TSDB)
    ('norwegian-hockey', 'tsdb', '4926', 'Norwegian Fjordkraft-ligaen', 'Norwegian Fjordkraft-ligaen', 'hockey', 'https://r2.thesportsdb.com/images/media/league/badge/lpfdvc1697194460.png', NULL, 1, NULL, 'norwegian-hockey', 'team_vs_team', NULL, NULL, NULL),

    -- Australian Football (TSDB)
    ('afl', 'tsdb', '4456', 'Australian AFL', 'Australian Football League', 'australian-football', 'https://r2.thesportsdb.com/images/media/league/badge/wvx4721525519372.png', NULL, 1, 'AFL', 'afl', 'team_vs_team', 'AFL', NULL, NULL),

    -- Baseball (ESPN)
    ('mlb', 'espn', 'baseball/mlb', NULL, 'Major League Baseball', 'baseball', 'https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png', NULL, 1, 'MLB', 'mlb', 'team_vs_team', 'MLB Baseball', NULL, NULL),
    ('college-baseball', 'espn', 'baseball/college-baseball', NULL, 'NCAA Baseball', 'baseball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/baseball.png', NULL, 1, NULL, 'ncaabb', 'team_vs_team', NULL, NULL, NULL),
    ('college-softball', 'espn', 'baseball/college-softball', NULL, 'NCAA Softball', 'softball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/softball.png', NULL, 1, NULL, 'ncaasbw', 'team_vs_team', NULL, NULL, NULL),

    -- Soccer (ESPN)
    ('usa.1', 'espn', 'soccer/usa.1', NULL, 'Major League Soccer', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/19.png', NULL, 1, 'MLS', 'mls', 'team_vs_team', 'MLS Soccer', NULL, NULL),
    ('usa.nwsl', 'espn', 'soccer/usa.nwsl', NULL, 'National Women''s Soccer League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2323.png', NULL, 1, 'NWSL', 'nwsl', 'team_vs_team', 'NWSL Soccer', NULL, NULL),
    ('usa.ncaa.m.1', 'espn', 'soccer/usa.ncaa.m.1', NULL, 'NCAA Men''s Soccer', 'soccer', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png', NULL, 1, 'NCAA Soccer', 'ncaas', 'team_vs_team', 'Men''s College Soccer', NULL, NULL),
    ('usa.ncaa.w.1', 'espn', 'soccer/usa.ncaa.w.1', NULL, 'NCAA Women''s Soccer', 'soccer', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png', NULL, 1, 'NCAA W Soccer', 'ncaaws', 'team_vs_team', 'Women''s College Soccer', NULL, NULL),
    ('eng.1', 'espn', 'soccer/eng.1', NULL, 'English Premier League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/23.png', NULL, 1, 'EPL', 'epl', 'team_vs_team', 'Premier League Soccer', NULL, NULL),
    ('eng.2', 'espn', 'soccer/eng.2', NULL, 'EFL Championship', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/24.png', NULL, 1, NULL, 'championship', 'team_vs_team', 'English Championship Soccer', NULL, NULL),
    ('eng.3', 'espn', 'soccer/eng.3', NULL, 'EFL League One', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/25.png', NULL, 1, NULL, 'league-one', 'team_vs_team', 'English League One Soccer', NULL, NULL),
    ('eng.4', 'espn', 'soccer/eng.4', NULL, 'EFL League Two', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/26.png', NULL, 1, NULL, 'league-two', 'team_vs_team', 'English League Two Soccer', NULL, NULL),
    ('eng.fa', 'espn', 'soccer/eng.fa', NULL, 'FA Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/40.png', NULL, 1, NULL, 'fa-cup', 'team_vs_team', 'FA Cup Soccer', NULL, NULL),
    ('eng.league_cup', 'espn', 'soccer/eng.league_cup', NULL, 'EFL Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/41.png', NULL, 1, 'Carabao Cup', 'league-cup', 'team_vs_team', 'EFL Cup Soccer', NULL, NULL),
    ('esp.1', 'espn', 'soccer/esp.1', NULL, 'La Liga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/15.png', NULL, 1, NULL, 'laliga', 'team_vs_team', 'La Liga Soccer', NULL, NULL),
    ('esp.copa_del_rey', 'espn', 'soccer/esp.copa_del_rey', NULL, 'Copa del Rey', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/79.png', NULL, 1, NULL, 'copa-del-rey', 'team_vs_team', 'Copa del Rey Soccer', NULL, NULL),
    ('ger.1', 'espn', 'soccer/ger.1', NULL, 'Bundesliga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/10.png', NULL, 1, NULL, 'bundesliga', 'team_vs_team', 'Bundesliga Soccer', NULL, NULL),
    ('ger.dfb_pokal', 'espn', 'soccer/ger.dfb_pokal', NULL, 'DFB-Pokal', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/80.png', NULL, 1, NULL, 'dfb-pokal', 'team_vs_team', 'DFB-Pokal Soccer', NULL, NULL),
    ('ita.1', 'espn', 'soccer/ita.1', NULL, 'Serie A', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/12.png', NULL, 1, NULL, 'seriea', 'team_vs_team', 'Serie A Soccer', NULL, NULL),
    ('ita.coppa_italia', 'espn', 'soccer/ita.coppa_italia', NULL, 'Coppa Italia', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/159.png', NULL, 1, NULL, 'coppa-italia', 'team_vs_team', 'Coppa Italia Soccer', NULL, NULL),
    ('fra.1', 'espn', 'soccer/fra.1', NULL, 'Ligue 1', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/9.png', NULL, 1, NULL, 'ligue1', 'team_vs_team', 'Ligue 1 Soccer', NULL, NULL),
    ('fra.coupe_de_france', 'espn', 'soccer/fra.coupe_de_france', NULL, 'Coupe de France', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/192.png', NULL, 1, NULL, 'coupe-de-france', 'team_vs_team', 'Coupe de France Soccer', NULL, NULL),
    ('uefa.champions', 'espn', 'soccer/uefa.champions', NULL, 'UEFA Champions League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2.png', NULL, 1, 'UCL', 'ucl', 'team_vs_team', 'UEFA Champions League Soccer', NULL, NULL),
    ('ksa.1', 'espn', 'soccer/ksa.1', NULL, 'Saudi Pro League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2488.png', NULL, 1, 'SPL', 'spl', 'team_vs_team', 'Saudi Pro League Soccer', NULL, NULL),
    -- Additional European Leagues
    ('ned.1', 'espn', 'soccer/ned.1', NULL, 'Eredivisie', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/35.png', NULL, 1, NULL, 'eredivisie', 'team_vs_team', 'Eredivisie Soccer', NULL, NULL),
    ('por.1', 'espn', 'soccer/por.1', NULL, 'Primeira Liga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/14.png', NULL, 1, NULL, 'primeira', 'team_vs_team', 'Primeira Liga Soccer', NULL, NULL),
    ('bel.1', 'espn', 'soccer/bel.1', NULL, 'Belgian Pro League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/144.png', NULL, 1, NULL, 'jupiler', 'team_vs_team', 'Belgian Pro League Soccer', NULL, NULL),
    ('sco.1', 'espn', 'soccer/sco.1', NULL, 'Scottish Premiership', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/29.png', NULL, 1, 'SPFL', 'spfl', 'team_vs_team', 'Scottish Premiership Soccer', NULL, NULL),
    ('tur.1', 'espn', 'soccer/tur.1', NULL, 'Turkish Süper Lig', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/18.png', NULL, 1, 'Süper Lig', 'super-lig', 'team_vs_team', 'Turkish Süper Lig Soccer', NULL, NULL),
    ('gre.1', 'espn', 'soccer/gre.1', NULL, 'Greek Super League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/68.png', NULL, 1, NULL, 'greek-super-league', 'team_vs_team', 'Greek Super League Soccer', NULL, NULL),
    ('uefa.europa', 'espn', 'soccer/uefa.europa', NULL, 'UEFA Europa League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2310.png', NULL, 1, 'UEL', 'uel', 'team_vs_team', 'UEFA Europa League Soccer', NULL, NULL),
    ('uefa.europa.conf', 'espn', 'soccer/uefa.europa.conf', NULL, 'UEFA Europa Conference League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2954.png', NULL, 1, 'UECL', 'uecl', 'team_vs_team', 'UEFA Europa Conference League Soccer', NULL, NULL),
    -- International Tournaments
    ('fifa.world', 'espn', 'soccer/fifa.world', NULL, 'FIFA World Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/4.png', NULL, 1, 'World Cup', 'world-cup', 'team_vs_team', 'FIFA World Cup Soccer', NULL, NULL),
    ('fifa.wwc', 'espn', 'soccer/fifa.wwc', NULL, 'FIFA Women''s World Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/131.png', NULL, 1, 'WWC', 'wwc', 'team_vs_team', 'FIFA Women''s World Cup Soccer', NULL, NULL),
    ('uefa.euro', 'espn', 'soccer/uefa.euro', NULL, 'UEFA European Championship', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/60.png', NULL, 1, 'Euro', 'euro', 'team_vs_team', 'UEFA Euro Soccer', NULL, NULL),
    ('conmebol.america', 'espn', 'soccer/conmebol.america', NULL, 'Copa America', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/73.png', NULL, 1, NULL, 'copa-america', 'team_vs_team', 'Copa America Soccer', NULL, NULL),
    ('concacaf.gold', 'espn', 'soccer/concacaf.gold', NULL, 'CONCACAF Gold Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/128.png', NULL, 1, 'Gold Cup', 'gold-cup', 'team_vs_team', 'CONCACAF Gold Cup Soccer', NULL, NULL),
    ('concacaf.nations.league', 'espn', 'soccer/concacaf.nations.league', NULL, 'CONCACAF Nations League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2737.png', NULL, 1, 'CNL', 'cnl', 'team_vs_team', 'CONCACAF Nations League Soccer', NULL, NULL),
    -- Americas Leagues
    ('mex.1', 'espn', 'soccer/mex.1', NULL, 'Liga MX', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/22.png', NULL, 1, NULL, 'ligamx', 'team_vs_team', 'Liga MX Soccer', NULL, NULL),
    ('arg.1', 'espn', 'soccer/arg.1', NULL, 'Argentine Liga Profesional', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/1.png', NULL, 1, 'LPA', 'lpa', 'team_vs_team', 'Argentine Liga Profesional Soccer', NULL, NULL),
    ('bra.1', 'espn', 'soccer/bra.1', NULL, 'Brazilian Serie A', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/85.png', NULL, 1, 'Brasileirao', 'brasileirao', 'team_vs_team', 'Brazilian Serie A Soccer', NULL, NULL),
    ('col.1', 'espn', 'soccer/col.1', NULL, 'Colombian Primera A', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/1543.png', NULL, 1, NULL, 'dimayor', 'team_vs_team', 'Colombian Primera A Soccer', NULL, NULL),
    ('conmebol.libertadores', 'espn', 'soccer/conmebol.libertadores', NULL, 'Copa Libertadores', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/13.png', NULL, 1, 'Libertadores', 'libertadores', 'team_vs_team', 'Copa Libertadores Soccer', NULL, NULL),
    ('conmebol.sudamericana', 'espn', 'soccer/conmebol.sudamericana', NULL, 'Copa Sudamericana', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/49.png', NULL, 1, 'Sudamericana', 'sudamericana', 'team_vs_team', 'Copa Sudamericana Soccer', NULL, NULL),
    -- Asia/Pacific Leagues
    ('jpn.1', 'espn', 'soccer/jpn.1', NULL, 'J1 League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/1842.png', NULL, 1, 'J-League', 'jleague', 'team_vs_team', 'J1 League Soccer', NULL, NULL),
    ('aus.1', 'espn', 'soccer/aus.1', NULL, 'A-League Men', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/114.png', NULL, 1, 'A-League', 'aleague', 'team_vs_team', 'A-League Men Soccer', NULL, NULL),

    -- MMA (ESPN) - Combat sport with event cards
    ('ufc', 'espn', 'mma/ufc', NULL, 'Ultimate Fighting Championship', 'mma', 'https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png', NULL, 0, 'UFC', 'ufc', 'event_card', NULL, NULL, NULL),

    -- Volleyball (ESPN)
    ('mens-college-volleyball', 'espn', 'volleyball/mens-college-volleyball', NULL, 'NCAA Men''s Volleyball', 'volleyball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png', NULL, 1, 'NCAA Volleyball', 'ncaavb', 'team_vs_team', 'Men''s College Volleyball', NULL, NULL),
    ('womens-college-volleyball', 'espn', 'volleyball/womens-college-volleyball', NULL, 'NCAA Women''s Volleyball', 'volleyball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png', NULL, 1, 'NCAA W Volleyball', 'ncaawvb', 'team_vs_team', 'Women''s College Volleyball', NULL, NULL),

    -- Lacrosse - NCAA (ESPN)
    ('mens-college-lacrosse', 'espn', 'lacrosse/mens-college-lacrosse', NULL, 'NCAA Men''s Lacrosse', 'lacrosse', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/lacrosse.png', NULL, 1, 'NCAA Lacrosse', 'ncaalax', 'team_vs_team', NULL, NULL, NULL),
    ('womens-college-lacrosse', 'espn', 'lacrosse/womens-college-lacrosse', NULL, 'NCAA Women''s Lacrosse', 'lacrosse', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/lacrosse.png', NULL, 1, 'NCAA W Lacrosse', 'ncaawlax', 'team_vs_team', NULL, NULL, NULL),

    -- Lacrosse (ESPN)
    ('nll', 'espn', 'lacrosse/nll', NULL, 'National Lacrosse League', 'lacrosse', 'https://a.espncdn.com/guid/5f77fe12-e54f-41a1-904e-77135452f348/logos/default.png', NULL, 1, 'NLL', 'nll', 'team_vs_team', NULL, NULL, NULL),
    ('pll', 'espn', 'lacrosse/pll', NULL, 'Premier Lacrosse League', 'lacrosse', 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/pll.png', NULL, 1, 'PLL', 'pll', 'team_vs_team', NULL, NULL, NULL),

    -- Cricket (TSDB primary, Cricbuzz fallback)
    -- TSDB provides teams/logos year-round; Cricbuzz provides schedules when TSDB free tier limited
    -- fallback_league_id format: 'series_id/url-slug' (changes yearly - auto-discovered by cache refresh)
    ('ipl', 'tsdb', '4460', 'Indian Premier League', 'Indian Premier League', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/gaiti11741709844.png', NULL, 1, 'IPL', 'ipl', 'team_vs_team', NULL, 'cricbuzz', '9241/indian-premier-league-2026'),
    ('bbl', 'tsdb', '4461', 'Australian Big Bash League', 'Big Bash League', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/yko7ny1546635346.png', NULL, 1, 'BBL', 'bbl', 'team_vs_team', NULL, 'cricbuzz', '10289/big-bash-league-2025-26'),
    ('sa20', 'tsdb', '5532', 'SA20', 'South Africa Twenty20', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/aakvuk1734183412.png', NULL, 1, 'SA20', 'sa20', 'team_vs_team', NULL, 'cricbuzz', '10394/sa20-2025-26'),

    -- Rugby (TSDB)
    ('nrl', 'tsdb', '4416', 'Australian National Rugby League', 'National Rugby League', 'rugby', 'https://r2.thesportsdb.com/images/media/league/badge/gsztcj1552071996.png', NULL, 1, 'NRL', 'nrl', 'team_vs_team', NULL, NULL, NULL),
    ('super-rugby', 'tsdb', '4551', 'Super Rugby', 'Super Rugby Pacific', 'rugby', 'https://r2.thesportsdb.com/images/media/league/badge/alpxhe1675871443.png', NULL, 1, 'Super Rugby', 'super-rugby', 'team_vs_team', NULL, NULL, NULL),

    -- Boxing (TSDB) - Combat sport with event cards
    ('boxing', 'tsdb', '4445', 'Boxing', 'Boxing', 'boxing', NULL, NULL, 0, NULL, 'boxing', 'event_card', NULL, NULL, NULL);

-- Cricbuzz auto-discovery patterns (base slug without year suffix)
-- These are used by cache refresh to find current season's series ID
UPDATE leagues SET series_slug_pattern = 'indian-premier-league' WHERE league_code = 'ipl';
UPDATE leagues SET series_slug_pattern = 'big-bash-league' WHERE league_code = 'bbl';
UPDATE leagues SET series_slug_pattern = 'sa20' WHERE league_code = 'sa20';


-- =============================================================================
-- STREAM_MATCH_CACHE TABLE
-- Caches stream-to-event matches to avoid expensive matching on every run.
-- Supports both successful matches and user corrections.
--
-- Fingerprint = hash of group_id + stream_id + stream_name
-- When stream name changes, hash changes, so no stale match used.
--
-- User corrections (user_corrected=1) are "pinned" and never auto-invalidated.
-- Failed matches can be cached with event_id='__FAILED__' for short TTL.
-- =============================================================================

CREATE TABLE IF NOT EXISTS stream_match_cache (
    -- Hash fingerprint for fast lookup (SHA256 truncated to 16 chars)
    fingerprint TEXT PRIMARY KEY,

    -- Original fields kept for debugging
    group_id INTEGER NOT NULL,
    stream_id INTEGER NOT NULL,
    stream_name TEXT NOT NULL,

    -- Match result (event_id='__FAILED__' for cached failed matches)
    event_id TEXT NOT NULL,
    league TEXT NOT NULL,

    -- Cached static event data (JSON blob)
    -- Contains event dict for template vars (static fields only)
    -- NULL for failed match cache entries
    cached_event_data TEXT,

    -- Match method tracking
    -- cache: hit existing cache entry
    -- user_corrected: manually corrected by user (pinned)
    -- alias: matched via user-defined alias
    -- pattern: matched via team name pattern
    -- fuzzy: matched via fuzzy string matching
    -- keyword: matched via keyword (UFC, boxing event cards)
    -- no_match: failed to match (short TTL)
    match_method TEXT DEFAULT 'fuzzy'
        CHECK(match_method IN ('cache', 'user_corrected', 'alias', 'pattern', 'fuzzy', 'keyword', 'no_match')),

    -- User correction tracking
    user_corrected BOOLEAN DEFAULT 0,
    corrected_at TIMESTAMP,

    -- Housekeeping
    last_seen_generation INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_smc_generation ON stream_match_cache(last_seen_generation);
CREATE INDEX IF NOT EXISTS idx_smc_event_id ON stream_match_cache(event_id);
CREATE INDEX IF NOT EXISTS idx_smc_user_corrected ON stream_match_cache(user_corrected) WHERE user_corrected = 1;
CREATE INDEX IF NOT EXISTS idx_smc_method ON stream_match_cache(match_method);


-- =============================================================================
-- MATCH_CORRECTIONS TABLE
-- Audit log of user corrections to stream-event matches.
-- When a user corrects an incorrect match or assigns a failed match to an event,
-- the correction is recorded here and the stream_match_cache is updated.
--
-- Correction types:
--   remapped: Changed from incorrect event to correct event
--   no_event: Stream has no corresponding event (permanently exclude)
--   excluded: Stream should be excluded from matching (e.g., talk show)
-- =============================================================================

CREATE TABLE IF NOT EXISTS match_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Stream identification
    fingerprint TEXT NOT NULL,              -- Links to stream_match_cache
    group_id INTEGER NOT NULL,
    stream_name TEXT NOT NULL,

    -- What was corrected
    incorrect_event_id TEXT NOT NULL,       -- Original (wrong) event_id or '__FAILED__'
    incorrect_league TEXT,                  -- Original league (if matched)

    -- Correction details
    correct_event_id TEXT,                  -- New event_id (NULL for no_event/excluded)
    correct_league TEXT,                    -- New league

    -- Correction type
    correction_type TEXT NOT NULL
        CHECK(correction_type IN ('remapped', 'no_event', 'excluded')),

    -- Audit
    corrected_by TEXT DEFAULT 'user',       -- 'user', 'api', 'import'
    notes TEXT,                             -- Optional explanation

    -- Unique constraint: one correction per fingerprint per original match
    UNIQUE(fingerprint, incorrect_event_id)
);

CREATE INDEX IF NOT EXISTS idx_mc_fingerprint ON match_corrections(fingerprint);
CREATE INDEX IF NOT EXISTS idx_mc_group ON match_corrections(group_id);
CREATE INDEX IF NOT EXISTS idx_mc_type ON match_corrections(correction_type);


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
-- SERVICE_CACHE TABLE
-- Persistent cache for service layer (survives restarts)
-- Same TTL logic as in-memory cache, just persisted to SQLite
-- =============================================================================

CREATE TABLE IF NOT EXISTS service_cache (
    -- Cache key (e.g., "events:nfl:2026-01-06")
    cache_key TEXT PRIMARY KEY,

    -- Cached data (JSON serialized)
    data_json TEXT NOT NULL,

    -- TTL management
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for cleanup of expired entries
CREATE INDEX IF NOT EXISTS idx_sc_expires ON service_cache(expires_at);


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
        CHECK(change_type IN ('created', 'modified', 'deleted', 'stream_added', 'stream_removed', 'verified', 'synced', 'error', 'number_swapped')),

    -- Change source
    change_source TEXT
        CHECK(change_source IN ('epg_generation', 'reconciliation', 'api', 'scheduler', 'manual', 'external_sync', 'lifecycle', 'cross_group_enforcement', 'keyword_enforcement', 'keyword_ordering')),

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

    -- Label (primary identifier, used in channel names and {exception_keyword} template variable)
    -- e.g., "Spanish", "Manningcast"
    label TEXT NOT NULL UNIQUE,

    -- Match terms (comma-separated phrases/words to match in stream names)
    -- e.g., "Spanish, En Español, (ESP), Español" or "Peyton and Eli, Manningcast, Manning"
    match_terms TEXT NOT NULL,

    -- Behavior when keyword matched
    behavior TEXT NOT NULL DEFAULT 'consolidate'
        CHECK(behavior IN ('consolidate', 'separate', 'ignore')),

    -- Status
    enabled BOOLEAN DEFAULT 1
);

-- Seed default language keywords
INSERT OR IGNORE INTO consolidation_exception_keywords (label, match_terms, behavior) VALUES
    ('Spanish', 'Spanish, En Español, (ESP), Español', 'consolidate'),
    ('French', 'French, En Français, (FRA), Français', 'consolidate'),
    ('German', 'German, (GER), Deutsch', 'consolidate'),
    ('Portuguese', 'Portuguese, (POR), Português', 'consolidate'),
    ('Italian', 'Italian, (ITA), Italiano', 'consolidate'),
    ('Japanese', 'Japanese, (JPN), 日本語', 'consolidate'),
    ('Korean', 'Korean, (KOR), 한국어', 'consolidate'),
    ('Chinese', 'Chinese, (CHN), (CHI), 中文', 'consolidate');

CREATE INDEX IF NOT EXISTS idx_exception_keywords_enabled ON consolidation_exception_keywords(enabled);
CREATE INDEX IF NOT EXISTS idx_exception_keywords_behavior ON consolidation_exception_keywords(behavior);


-- =============================================================================
-- TEAM_ALIASES TABLE
-- User-defined team name aliases for stream matching
-- Maps stream names → provider teams for edge cases where automatic matching fails
-- Examples: "Spurs" → "Tottenham Hotspur", "Man U" → "Manchester United"
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Alias Definition
    alias TEXT NOT NULL,                    -- Alias string (normalized) e.g., "spurs", "man u"
    league TEXT NOT NULL,                   -- League code (e.g., "epl", "nfl")

    -- Provider Team Mapping
    provider TEXT NOT NULL DEFAULT 'espn',  -- Provider name
    team_id TEXT NOT NULL,                  -- Provider's team ID
    team_name TEXT NOT NULL,                -- Provider's team name (e.g., "Tottenham Hotspur")

    UNIQUE(alias, league)
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_league ON team_aliases(league);
CREATE INDEX IF NOT EXISTS idx_team_aliases_alias ON team_aliases(alias);


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
    channels_active INTEGER DEFAULT 0,

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
CREATE INDEX IF NOT EXISTS idx_stats_snapshots_period ON stats_snapshots(period_start);


-- =============================================================================
-- EPG_MATCHED_STREAMS TABLE
-- Details of successfully matched streams per generation run
-- =============================================================================

CREATE TABLE IF NOT EXISTS epg_matched_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Link to processing run
    run_id INTEGER NOT NULL,

    -- Group info
    group_id INTEGER NOT NULL,
    group_name TEXT,

    -- Stream info
    stream_id INTEGER,
    stream_name TEXT NOT NULL,

    -- Event info
    event_id TEXT NOT NULL,
    event_name TEXT,
    event_date TEXT,

    -- Match details
    detected_league TEXT,
    home_team TEXT,
    away_team TEXT,
    from_cache BOOLEAN DEFAULT 0,

    -- Exclusion info (matched but not included due to league filter etc)
    excluded BOOLEAN DEFAULT 0,
    exclusion_reason TEXT,  -- e.g. 'excluded_league', 'wrong_date'

    -- Enhanced matching info (Phase 7)
    match_method TEXT,  -- 'cache', 'user_corrected', 'alias', 'pattern', 'fuzzy', 'keyword', 'direct'
    confidence REAL,    -- Match confidence score 0.0-1.0
    origin_match_method TEXT,  -- For cache hits: original method used (e.g., 'fuzzy')

    FOREIGN KEY (run_id) REFERENCES processing_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_matched_streams_run ON epg_matched_streams(run_id);
CREATE INDEX IF NOT EXISTS idx_matched_streams_group ON epg_matched_streams(group_id);
CREATE INDEX IF NOT EXISTS idx_matched_streams_method ON epg_matched_streams(match_method);


-- =============================================================================
-- EPG_FAILED_MATCHES TABLE
-- Details of streams that failed to match per generation run
-- =============================================================================

CREATE TABLE IF NOT EXISTS epg_failed_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Link to processing run
    run_id INTEGER NOT NULL,

    -- Group info
    group_id INTEGER NOT NULL,
    group_name TEXT,

    -- Stream info
    stream_id INTEGER,
    stream_name TEXT NOT NULL,

    -- Failure details
    reason TEXT NOT NULL,  -- 'unmatched', 'excluded_league', 'filtered_include', 'filtered_exclude', 'exception'
    exclusion_reason TEXT,  -- For excluded_league: specific reason
    detail TEXT,            -- Additional context

    -- Enhanced matching info (Phase 7) - what we extracted before failing
    parsed_team1 TEXT,      -- Team name extracted from stream (before match)
    parsed_team2 TEXT,      -- Opponent name extracted from stream
    detected_league TEXT,   -- League hint detected (if any)

    FOREIGN KEY (run_id) REFERENCES processing_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_failed_matches_run ON epg_failed_matches(run_id);
CREATE INDEX IF NOT EXISTS idx_failed_matches_group ON epg_failed_matches(group_id);
CREATE INDEX IF NOT EXISTS idx_failed_matches_reason ON epg_failed_matches(reason);
