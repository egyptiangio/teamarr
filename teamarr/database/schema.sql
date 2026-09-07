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
    xmltv_categories JSON DEFAULT '["Sports", "Sports event"]',
    -- Independent category list applied only to filler programmes (pregame/postgame/idle).
    -- Empty list = no <category> tags on filler. Replaced the old `categories_apply_to`
    -- gate in v72: previously 'all' duplicated xmltv_categories onto filler; now you set
    -- filler categories explicitly (e.g., ["Series"] for Emby guide-view compat).
    xmltv_filler_categories JSON DEFAULT '[]',

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
    -- Conditional filler rows (#420, epic cajd): per-register condition rows in
    -- the hehg.2 shape ({condition, condition_value, template, title?, subtitle?,
    -- priority, label}). Replace the legacy final/not-final switch columns above,
    -- which stay in place unread (v80 migration converts; rollback-safe).
    -- Postgame default mirrors the new-template form seed (cajd.4/cajd.6):
    -- recap-when-published over the constructed base. Existing DBs keep their
    -- '[]' default (reconciliation never alters existing columns) — the form
    -- always sends the fields, so the default only covers column-less INSERTs.
    pregame_conditional_rows JSON DEFAULT '[]',
    postgame_conditional_rows JSON DEFAULT '[{"condition": "has_recap", "template": "{game_recap.last}", "priority": 10, "label": "Recap (provider)"}]',
    idle_conditional_rows JSON DEFAULT '[]',
    -- Offseason register seeded enabled (#418): with it off, idle content
    -- renders {*.next} literals into real guides once a team has no next game.
    -- description_enabled is the master toggle; title stays unset so it falls
    -- back to the idle title (which carries no .next).
    idle_offseason JSON DEFAULT '{"title_enabled": false, "title": null, "subtitle_enabled": true, "subtitle": "No upcoming game currently on schedule", "description_enabled": true, "description": "No upcoming {team_name} games scheduled."}',

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
    -- Create timing: 'same_day' (midnight of event day), 'before_event' (event_start - pre_buffer)
    channel_create_timing TEXT DEFAULT 'same_day' CHECK(channel_create_timing IN ('same_day', 'before_event')),
    -- Delete timing: 'same_day' (end of day, midnight-crossover aware), 'after_event' (event_end + post_buffer)
    channel_delete_timing TEXT DEFAULT 'same_day' CHECK(channel_delete_timing IN ('same_day', 'after_event')),
    -- Buffer minutes for before_event create timing (default 60 = 1 hour before event)
    channel_pre_buffer_minutes INTEGER DEFAULT 60,
    -- Buffer minutes for after_event delete timing and same_day midnight crossover (default 60)
    channel_post_buffer_minutes INTEGER DEFAULT 60,

    -- EPG program-data matching master switch (epic teamarrv2-183.6). Default
    -- OFF (opt-in); also feature-gated on the connected Dispatcharr exposing
    -- /api/epg/programs/search/. Per-group epg_match_enabled has no effect unless
    -- this global switch is on.
    epg_match_enabled BOOLEAN DEFAULT 0,

    -- XC (Xtream) provider EPG fallback (epic teamarrv2-crs). Default OFF
    -- (opt-in). EPG matching normally requires a valid stream-to-EPG mapping in
    -- Dispatcharr (curated channel link or imported-guide name match). As a
    -- backup, when a stream's M3U account is an Xtream panel, Teamarr can fetch
    -- the provider's own xmltv.php independently and match against it. Costs a
    -- (cached) provider-EPG download per XC account per run.
    epg_xtream_fallback_enabled BOOLEAN DEFAULT 0,
    -- How long a downloaded XC provider EPG is reused before re-fetching (hours).
    -- The provider's xmltv.php is cached on disk per M3U account; a re-download
    -- happens only when the cache is older than this. Default 24h.
    epg_xtream_cache_hours INTEGER DEFAULT 24,

    -- EPG channel-source mode (epic teamarrv2-183.9). When enabled, an additional
    -- system-managed source ("Dispatcharr Channels") feeds EPG matching from the
    -- streams already assigned to curated Dispatcharr channels (using each
    -- channel's own EPG), alongside the per-group M3U-group EPG matching. Teamarr's
    -- own output channels are excluded (they are OUTPUT, not INPUT).
    epg_channel_source_enabled BOOLEAN DEFAULT 0,
    -- Which Dispatcharr channel groups to include as channel-source candidates
    -- (JSON array of channel_group ids). Empty array = include all groups
    -- (back-compatible). Scoping to selected groups skips EPG-matching work for
    -- undesired groups (faster generation) and drives the "Dispatcharr Group"
    -- stream-ordering rule. (epic teamarrv2-ybt.2)
    epg_channel_source_groups TEXT DEFAULT '[]',
    -- Optional stream-profile overrides. Each item has a target_type and stable
    -- target_id, e.g. {"target_type":"dispatcharr_channel_group","target_id":7,
    -- "stream_profile_id":3}. Current UI supports Dispatcharr channel groups;
    -- this discriminated shape also permits future Sources-tab target types.
    stream_profile_overrides TEXT DEFAULT '[]',

    -- EPG stream time-windowing buffers (epic teamarrv2-183.5).
    -- SEPARATE from the channel create/delete buffers above: these apply to the
    -- attach/detach window of time-shared linear streams (EPG matching), so one
    -- linear stream attaches to an event channel only near game time. Global
    -- pre-attach / post-detach minutes applied to the EPG program slot.
    epg_stream_pre_buffer_minutes INTEGER DEFAULT 60,
    epg_stream_post_buffer_minutes INTEGER DEFAULT 60,

    -- Tennis: only match grand-slam tournaments (#283)
    tennis_majors_only INTEGER DEFAULT 0,

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

    -- Art base URL: optional prefix for relative art/gamethumb paths in templates.
    -- When set, template art values that are not already absolute (http(s)://)
    -- are joined onto this base at render time. Empty = no prefixing (legacy).
    art_base_url TEXT DEFAULT '',

    -- Display Preferences
    time_format TEXT DEFAULT '12h' CHECK(time_format IN ('12h', '24h')),
    show_timezone BOOLEAN DEFAULT 1,
    -- Sport naming (#691): 'us' = Football / Soccer; 'international' =
    -- American Football / Football. Applied to the sports.display_name map
    -- everywhere it is read (templates, {sport} channel groups, UI labels).
    sport_naming TEXT DEFAULT 'us' CHECK(sport_naming IN ('us', 'international')),
    -- Matchup order (#692): 'auto' = the sport's convention (visitor first for
    -- US team sports, home first for soccer/rugby/cricket); 'away_first' /
    -- 'home_first' force it. Governs {matchup*} and {team1}/{team2}; a
    -- per-league override lives on subscription_league_config.
    matchup_order TEXT DEFAULT 'auto' CHECK(matchup_order IN ('auto', 'away_first', 'home_first')),

    -- Event-Based EPG Options
    include_final_events BOOLEAN DEFAULT 0,      -- Include completed events for today
    channel_range_start INTEGER DEFAULT 101,     -- First auto-assigned channel number
    channel_range_end INTEGER,                   -- Last auto-assigned channel (null = no limit)

    -- Default Team Filtering (for Event Groups)
    default_include_teams JSON,                  -- Global include filter [{"provider":"espn","team_id":"33","league":"nfl"}, ...]
    default_exclude_teams JSON,                  -- Global exclude filter (same format)
    default_team_filter_mode TEXT DEFAULT 'include' CHECK(default_team_filter_mode IN ('include', 'exclude')),
    team_filter_enabled BOOLEAN DEFAULT 1,       -- Master toggle to enable/disable team filtering
    default_bypass_filter_for_playoffs BOOLEAN DEFAULT 0, -- Include all playoff and All-Star games regardless of team filter

    -- Scheduled Generation
    cron_expression TEXT DEFAULT '0 * * * *',    -- Cron for auto EPG generation

    -- Cache Refresh Frequencies
    soccer_cache_refresh_frequency TEXT DEFAULT 'weekly',
    team_cache_refresh_frequency TEXT DEFAULT 'weekly',

    -- API
    api_timeout INTEGER DEFAULT 30,
    api_retry_count INTEGER DEFAULT 5,

    -- TheSportsDB API (optional premium key for higher limits)
    -- If not set, uses free API key with 30 req/min and 10 result limits
    -- Premium key ($9/mo) gives 100 req/min and higher limits
    tsdb_api_key TEXT,

    -- SOCKS5 proxy policy for provider upstreams only. All providers use it
    -- when enabled unless their name appears in proxy_excluded_providers.
    proxy_enabled BOOLEAN DEFAULT 0,
    proxy_url TEXT,
    proxy_user_agent TEXT,
    proxy_excluded_providers JSON DEFAULT '[]',

    -- Channel ID Format
    channel_id_format TEXT DEFAULT '{team_name|pascal}.{league_id}',

    -- Generation Counter (for cache purging)
    epg_generation_counter INTEGER DEFAULT 0,

    -- Dispatcharr Integration
    dispatcharr_enabled BOOLEAN DEFAULT 0,
    dispatcharr_url TEXT,
    dispatcharr_username TEXT,
    dispatcharr_password TEXT,                -- Note: Consider encrypting in production
    dispatcharr_epg_id INTEGER,               -- Teamarr's EPG source ID in Dispatcharr
    default_channel_profile_ids JSON,         -- Default channel profiles for event channels
    default_stream_profile_id INTEGER,        -- Default stream profile for event channels
    default_channel_group_id INTEGER,         -- Default channel group for event channels
    default_channel_group_mode TEXT DEFAULT 'static', -- 'static', 'sport', 'league', or custom pattern
    cleanup_unused_logos BOOLEAN DEFAULT 0,   -- Call Dispatcharr's cleanup API after generation

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

    -- Scheduled Channel Reset (for Jellyfin logo cache issues)
    -- When enabled, purges all Teamarr channels from Dispatcharr on the specified schedule
    channel_reset_enabled BOOLEAN DEFAULT 0,
    channel_reset_cron TEXT DEFAULT NULL,

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

    -- Postponed Event Label
    -- When true, prepends "Postponed: " to channel name, EPG title, subtitle, and description
    -- for events with status.state = "postponed"
    prepend_postponed_label BOOLEAN DEFAULT 1,

    -- Update Check Settings
    -- Allows users to receive notifications about new versions
    update_check_enabled BOOLEAN DEFAULT 1,              -- Master toggle for update checking
    update_notify_stable BOOLEAN DEFAULT 1,              -- Notify about stable releases
    update_notify_dev BOOLEAN DEFAULT 1,                 -- Notify about dev builds (if running dev)
    update_github_owner TEXT DEFAULT 'Pharaoh-Labs',     -- GitHub repo owner (for forks)
    update_github_repo TEXT DEFAULT 'teamarr',           -- GitHub repo name (for forks)
    update_dev_branch TEXT DEFAULT 'dev',                -- Branch to check for dev builds
    update_auto_detect_branch BOOLEAN DEFAULT 1,         -- Auto-detect branch from version string

    -- Scheduled Backup Settings
    -- Automatic database backups with rotation and protection
    scheduled_backup_enabled BOOLEAN DEFAULT 0,          -- Master toggle for scheduled backups
    scheduled_backup_cron TEXT DEFAULT '0 3 * * *',      -- Cron expression (default: 3 AM daily)
    scheduled_backup_max_count INTEGER DEFAULT 7,        -- Maximum backups to keep (rotation)
    scheduled_backup_path TEXT DEFAULT './data/backups', -- Directory for backup files

    -- Gold Zone (Olympics Special Feature)
    -- Consolidates all "Gold Zone" streams into a single channel with external EPG
    gold_zone_enabled BOOLEAN DEFAULT 0,
    gold_zone_channel_number INTEGER,
    gold_zone_channel_group_id INTEGER,
    gold_zone_channel_profile_ids TEXT,    -- JSON array of profile IDs
    gold_zone_stream_profile_id INTEGER,

    -- Global Channel Mode (replaces per-group channel_assignment_mode)
    -- 'auto': Sequential numbering from channel_range_start by sort priority
    -- 'manual': Per-league starting channel numbers from league_channel_starts
    global_channel_mode TEXT DEFAULT 'auto'
        CHECK(global_channel_mode IN ('auto', 'manual')),

    -- Per-league channel start numbers for MANUAL mode
    -- JSON: {"nfl": 1001, "nba": 2001, "nhl": 3001}
    league_channel_starts JSON DEFAULT '{}',

    -- Global stream consolidation (replaces per-group duplicate_event_handling + overlap_handling)
    -- 'consolidate': Merge all streams for same event into one channel
    -- 'separate': Each stream gets its own channel
    global_consolidation_mode TEXT DEFAULT 'consolidate'
        CHECK(global_consolidation_mode IN ('consolidate', 'separate')),

    -- Channel Numbering Stability Mode (how existing channel numbers behave across runs)
    -- 'compact': Re-sort all channels into contiguous priority order every run (legacy default).
    --            Tidy guide, but a live channel's number can shift when events start/end.
    -- 'gap':     Sticky + gap-aware. Existing channels keep their number for their whole
    --            lifecycle; new channels slot into a free number in their sorted neighborhood
    --            (using channel_gap_size spacing) or append. Deleted slots are reused.
    -- 'strict':  Sticky + no-drift. Existing channels never move; new channels that would
    --            displace others are appended to the end of the used range. Gaps are reclaimed
    --            only at the daily reset.
    -- For 'gap'/'strict', a full re-layout (the only time existing channels move) runs on the
    -- first generation at/after channel_daily_reset_time each day, if channel_daily_reset_enabled.
    channel_stability_mode TEXT DEFAULT 'compact'
        CHECK(channel_stability_mode IN ('compact', 'gap', 'strict')),
    channel_gap_size INTEGER DEFAULT 3,                 -- Spacing between channels in 'gap' mode (1 = none)
    channel_daily_reset_enabled BOOLEAN DEFAULT 1,      -- Run the periodic full re-layout (gap/strict only)
    channel_daily_reset_time TEXT DEFAULT '04:00',      -- Local HH:MM low-traffic window for the reset
    last_channel_reset_at TEXT,                          -- Internal: timestamp of last full reset
    -- Internal: one-shot "re-grid on the next generation" flag. Set by the manual
    -- "Re-grid now" action and auto-armed when a setting that only takes effect at
    -- re-layout changes (gap size, stability mode, sort priority). Bypasses the
    -- daily time gate and reset_enabled; cleared once the re-layout runs.
    force_channel_relayout_pending BOOLEAN DEFAULT 0,

    -- Feed Separation (HOME/AWAY stream detection)
    -- When enabled, detects feed indicators in stream names and creates separate channels per feed
    feed_separation_enabled BOOLEAN DEFAULT 0,          -- Master toggle (off by default)
    feed_separation_sports JSON DEFAULT '[]',           -- Sport codes to split ([] = every sport, #732)
    feed_home_terms JSON DEFAULT '["HOME"]',            -- Terms that indicate home feed
    feed_away_terms JSON DEFAULT '["AWAY"]',            -- Terms that indicate away feed
    feed_detect_team_names BOOLEAN DEFAULT 1,           -- Also detect team names as feed indicators
    feed_label_style TEXT DEFAULT 'team_name'           -- How to label feeds in channel names
        CHECK(feed_label_style IN ('team_name', 'short_name', 'home_away')),

    -- Emby Integration (Live TV Guide Refresh)
    -- emby_servers is a JSON list of {name, url, username, password, api_key}
    -- entries (#471 multi-server fan-out). Pre-v83 scalar columns may linger
    -- in upgraded databases; they are unread.
    emby_enabled BOOLEAN DEFAULT 0,
    emby_servers JSON,

    -- Jellyfin Integration (Live TV Guide Refresh)
    -- jellyfin_servers: same shape as emby_servers (#471)
    jellyfin_enabled BOOLEAN DEFAULT 0,
    jellyfin_servers JSON,

    -- Channels DVR Integration (M3U Source + XMLTV Lineup Refresh)
    -- Local API is unauthenticated by Channels DVR design; no credentials stored.
    -- channelsdvr_servers is a JSON list of {name, url, source_name, lineup_id}
    -- entries (#381 multi-server fan-out); each lineup_id refreshes that
    -- server's XMLTV guide — without it CDVR updates channels but leaves the
    -- EPG stale. Pre-v82 scalar columns (channelsdvr_url/source_name/lineup_id)
    -- may linger in upgraded databases; they are unread.
    channelsdvr_enabled BOOLEAN DEFAULT 0,
    channelsdvr_servers JSON,

    -- Schema Version
    schema_version INTEGER DEFAULT 92
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
    soccer_mode TEXT DEFAULT NULL            -- NULL (non-soccer), 'all' (auto-subscribe), 'teams' (follow teams), 'manual' (explicit)
        CHECK(soccer_mode IS NULL OR soccer_mode IN ('all', 'teams', 'manual')),
    soccer_followed_teams JSON DEFAULT NULL, -- [{provider, team_id, name}] for teams mode - auto-discovers their competitions

    -- Template
    template_id INTEGER,

    -- Channel Settings
    channel_start_number INTEGER,            -- Starting channel number for this group
    channel_group_id INTEGER,                -- Dispatcharr channel group to assign (when mode='static')
    channel_group_mode TEXT DEFAULT 'static', -- 'static' or pattern like '{sport}', '{league}', '{sport} | {league}'
    channel_profile_ids TEXT,                -- JSON array: profile IDs and/or patterns like "{sport}", "{league}"
    stream_profile_id INTEGER,               -- Stream profile for transcoding/proxy (overrides global default)
    stream_timezone TEXT,                    -- Timezone for interpreting dates/times in stream names (e.g., 'America/New_York')

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

    -- Group-name pattern binding (#450): when enabled, the source is bound to a
    -- regex over live M3U group NAMES instead of the pinned m3u_group_id. The
    -- pattern re-resolves to live group ids at stream-fetch time, so provider
    -- renames (which always spawn a NEW Dispatcharr group id) re-bind
    -- automatically. Scope: M3U-provided groups only.
    m3u_group_name_pattern TEXT,
    m3u_group_name_pattern_enabled BOOLEAN DEFAULT 0,

    -- Stale-source detection (lylt): a group is "stale" when its M3U source
    -- channel-group no longer exists in Dispatcharr (deleted/renamed). Distinct
    -- from off-season (group exists, zero current streams). Updated during the
    -- post-generation reconcile pass; source_last_seen powers the UI's "last
    -- seen" hint, source_missing=1 marks it stale.
    source_last_seen TIMESTAMP,              -- Last time the M3U source group was found in Dispatcharr
    source_missing INTEGER DEFAULT 0,        -- 1 = source channel-group no longer exists (stale)

    -- Processing Stats (updated by EPG generation)
    last_refresh TIMESTAMP,                  -- Last successful EPG refresh
    stream_count INTEGER DEFAULT 0,          -- Streams after filtering
    matched_count INTEGER DEFAULT 0,         -- Distinct streams matched to ≥1 event (coverage)
    match_result_count INTEGER DEFAULT 0,    -- Total matched results produced (volume; EPG fans out)

    -- Stream Filtering (Phase 2)
    stream_include_regex TEXT,               -- Only include streams matching this pattern
    stream_include_regex_enabled BOOLEAN DEFAULT 0,
    stream_exclude_regex TEXT,               -- Exclude streams matching this pattern
    stream_exclude_regex_enabled BOOLEAN DEFAULT 0,
    custom_regex_teams TEXT,                 -- Custom pattern to extract team names
    custom_regex_teams_enabled BOOLEAN DEFAULT 0,
    custom_regex_date TEXT,                  -- Custom pattern to extract date
    custom_regex_date_enabled BOOLEAN DEFAULT 0,
    custom_regex_month TEXT,                 -- Custom pattern to extract month separately
    custom_regex_month_enabled BOOLEAN DEFAULT 0,
    custom_regex_day TEXT,                   -- Custom pattern to extract day separately
    custom_regex_day_enabled BOOLEAN DEFAULT 0,
    custom_regex_time TEXT,                  -- Custom pattern to extract time
    custom_regex_time_enabled BOOLEAN DEFAULT 0,
    custom_regex_league TEXT,                -- Custom pattern to extract league hint
    custom_regex_league_enabled BOOLEAN DEFAULT 0,
    -- EVENT_CARD specific regex (UFC, Boxing, MMA)
    custom_regex_fighters TEXT,              -- Custom pattern to extract fighter names (?P<fighter1>...) (?P<fighter2>...)
    custom_regex_fighters_enabled BOOLEAN DEFAULT 0,
    custom_regex_event_name TEXT,            -- Custom pattern to extract event name (?P<event_name>...)
    custom_regex_event_name_enabled BOOLEAN DEFAULT 0,

    -- Custom Regex organized by event type (replaces flat custom_regex_* columns)
    -- Structure: {"team_vs_team": {"teams": {"pattern": "...", "enabled": true}, ...},
    --             "event_card": {"fighters": {...}, "event_name": {...}, ...}}
    custom_regex_config JSON,

    skip_builtin_filter BOOLEAN DEFAULT 0,   -- Skip built-in stream filtering (placeholder, unsupported sports, event patterns)

    -- Team Filtering (canonical team selection, inherited by children)
    include_teams JSON,                          -- Teams to include: [{"provider":"espn","team_id":"33","league":"nfl","name":"Ravens"}, ...]
    exclude_teams JSON,                          -- Teams to exclude: same format
    team_filter_mode TEXT DEFAULT 'include'      -- 'include' (whitelist) or 'exclude' (blacklist)
        CHECK(team_filter_mode IN ('include', 'exclude')),
    bypass_filter_for_playoffs BOOLEAN,          -- NULL=use default, 0=disabled, 1=enabled (include all playoff and All-Star games)
    name_match_enabled BOOLEAN DEFAULT 1,        -- (ahow) Match streams whose name identifies a specific event (TEAM_VS_TEAM/EVENT_CARD/RACING) — the default matching type. DEFAULT 1 backfills existing sources on upgrade. One of three declared matching types alongside team_streams_enabled (Team) and epg_match_enabled (EPG).
    team_streams_enabled BOOLEAN DEFAULT 0,      -- Allow team-branded streams (e.g. "NHL | Toronto Maple Leafs") to match events
    epg_match_enabled BOOLEAN DEFAULT 0,         -- (183.6) Use Dispatcharr EPG program data to match static-named linear streams (ESPN, NBA1) and time-window them. Requires a Dispatcharr build with /api/epg/programs/search/ (0.24.0+). No global switch — per-source opt-in (3lp1).
    is_channel_source BOOLEAN DEFAULT 0,         -- (183.9) System-managed source group whose candidate streams come from curated Dispatcharr channels (their assigned streams + each channel's own EPG) instead of an M3U group. Auto-created/toggled by settings.epg_channel_source_enabled; hidden from the Event Groups UI.

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

    -- Per-Group Subscription Overrides (NULL = inherit global subscription)
    subscription_leagues JSON,                   -- Override subscribed leagues for this group
    subscription_soccer_mode TEXT                 -- Override soccer mode for this group
        CHECK(subscription_soccer_mode IS NULL OR subscription_soccer_mode IN ('all', 'teams', 'manual')),
    subscription_soccer_followed_teams JSON,     -- Override followed teams for this group

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
-- GROUP_TEMPLATES TABLE
-- Multi-template assignment per group with sport/league specificity
-- Resolution order: leagues match > sports match > default (both NULL)
-- =============================================================================

CREATE TABLE IF NOT EXISTS group_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    sports JSON,                              -- NULL = any, or ["mma", "boxing"]
    leagues JSON,                             -- NULL = any, or ["ufc", "bellator"]

    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_templates_group_id ON group_templates(group_id);


-- =============================================================================
-- SPORTS SUBSCRIPTION TABLE (singleton, id=1)
-- Global sports/league subscription replaces per-group league configuration
-- =============================================================================

CREATE TABLE IF NOT EXISTS sports_subscription (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    leagues JSON NOT NULL DEFAULT '[]',
    soccer_mode TEXT DEFAULT NULL
        CHECK(soccer_mode IS NULL OR soccer_mode IN ('all', 'teams', 'manual')),
    soccer_followed_teams JSON DEFAULT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO sports_subscription (id) VALUES (1);


-- =============================================================================
-- SUBSCRIPTION TEMPLATES TABLE
-- Global template assignments (replaces per-group group_templates)
-- Resolution order: leagues match > sports match > default (both NULL)
-- =============================================================================

CREATE TABLE IF NOT EXISTS subscription_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    sports JSON,                              -- NULL = any, or ["mma", "boxing"]
    leagues JSON,                             -- NULL = any, or ["ufc", "bellator"]

    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE
);


-- =============================================================================
-- SUBSCRIPTION LEAGUE CONFIG TABLE
-- Per-league overrides for channel profiles and channel groups
-- Fallback chain: per-league → global default → Dispatcharr default
-- =============================================================================

CREATE TABLE IF NOT EXISTS subscription_league_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_code TEXT NOT NULL UNIQUE,
    channel_profile_ids JSON DEFAULT NULL,     -- NULL = use global default
    channel_group_id INTEGER DEFAULT NULL,     -- NULL = use global default
    channel_group_mode TEXT DEFAULT NULL,       -- NULL = use global default ('static', 'sport', 'league', or custom pattern)
    -- Matchup order override (#692): NULL = use the global setting
    matchup_order TEXT DEFAULT NULL
        CHECK(matchup_order IS NULL OR matchup_order IN ('auto', 'away_first', 'home_first'))
);


-- =============================================================================
-- MANAGED_CHANNELS TABLE
-- Dynamically created channels for event-based EPG
-- =============================================================================

CREATE TABLE IF NOT EXISTS managed_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Source Group (which event group supplied the first matched stream — provenance, not ownership)
    event_epg_group_id INTEGER,

    -- Event Reference (provider-agnostic)
    event_id TEXT NOT NULL,
    event_provider TEXT NOT NULL,

    -- Channel Info
    tvg_id TEXT NOT NULL,  -- Not UNIQUE: soft-deleted records can share tvg_id with active
    channel_name TEXT NOT NULL,
    channel_number TEXT,
    -- Stability lock (gap/strict modes only; ignored in compact mode):
    -- 0 = not yet placed by the stability allocator (newly created, or mode just enabled)
    -- 1 = number finalized and sticky — never moved except by the daily reset re-layout
    channel_number_locked INTEGER DEFAULT 0,
    logo_url TEXT,

    -- Dispatcharr Integration
    dispatcharr_channel_id INTEGER,          -- Dispatcharr's channel ID
    dispatcharr_uuid TEXT,                   -- Dispatcharr's immutable UUID
    dispatcharr_logo_id INTEGER,             -- Uploaded logo ID in Dispatcharr

    -- Channel Settings (resolved from subscription config)
    channel_group_id INTEGER,                -- Dispatcharr channel group
    channel_profile_ids TEXT,                -- JSON array of channel profile IDs

    -- Primary stream (first/main stream for this channel)
    primary_stream_id INTEGER,

    -- Exception keyword that matched (for consolidation override)
    exception_keyword TEXT,

    -- Feed team (for feed separation: HOME/AWAY → team identity)
    feed_team_id TEXT,                       -- Provider team ID (e.g., "1" for Arsenal)

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
    event_end_estimate TIMESTAMP,            -- (#533/#522) session-aware estimated event end, set at creation from get_event_end_time(). The delete-time recalc applies timing policy to THIS instead of re-deriving start+sport_duration, which is session-blind and would cut a race weekend short after Friday practice. NULL = unknown (pre-column rows) -> recalc falls back to the naive derivation.
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

    FOREIGN KEY (event_epg_group_id) REFERENCES event_epg_groups(id) ON DELETE SET NULL
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

-- Event-scoped unique: one channel per (event, keyword, feed_team, stream) regardless of source group
-- Includes primary_stream_id to support 'separate' duplicate handling mode
-- (allows multiple channels per event when each has a different primary stream, keyword, or feed team)
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_unique_event_v2
    ON managed_channels(event_id, event_provider, COALESCE(exception_keyword, ''), COALESCE(feed_team_id, ''), primary_stream_id)
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
-- CHANNEL PRIORITY TEAMS
-- A team-level sort tier that floats a followed team's channels to the very top
-- of the global channel list, ahead of all sport/league/time ordering. Purely an
-- ordering preference — unrelated to the Teams page or EPG generation.
-- Matched against managed_channels.home_team/away_team by (sport, team_name).
-- =============================================================================

CREATE TABLE IF NOT EXISTS channel_priority_teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Team identity (from team_cache)
    provider TEXT NOT NULL,                  -- 'espn' or 'tsdb'
    provider_team_id TEXT NOT NULL,          -- Provider's team ID
    team_name TEXT NOT NULL,                 -- Display + match key (e.g., 'Liverpool')
    league TEXT,                             -- League slug the team was picked from
    sport TEXT NOT NULL,                     -- Sport code (scopes name matching)
    -- How far the team floats: top of everything, of its sport, or of its league.
    -- DEFAULT 'all' preserves pre-scope behaviour for existing rows; new rows
    -- are created with 'league' (see priority_teams.add_priority_team).
    scope TEXT NOT NULL DEFAULT 'all' CHECK(scope IN ('all', 'sport', 'league')),

    -- One entry per team-in-league
    UNIQUE(provider, provider_team_id, league)
);


-- =============================================================================
-- NUMBERING EXCEPTIONS (pinned blocks, #333)
-- A scoped pin (team / league / sport) that numbers its channels from `start`.
-- Every channel resolves to exactly one lane (most specific wins, then
-- sort_order); unmatched channels use the global channel range. Rows sharing
-- start + label form a group (e.g. "Big events": World Cup + Olympics at 850).
-- See docs/reference/architecture/channel-numbering.md.
-- =============================================================================

CREATE TABLE IF NOT EXISTS numbering_exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    scope TEXT NOT NULL CHECK(scope IN ('team', 'league', 'sport')),
    sport TEXT NOT NULL,                     -- always set; team + league pins are sport-scoped
    league_code TEXT,                        -- scope='league'
    team_name TEXT,                          -- scope='team': match key (case-insensitive, either side)
    provider TEXT,                           -- scope='team': identity from team_cache
    provider_team_id TEXT,                   -- scope='team'

    start INTEGER NOT NULL,                  -- first channel number of the block
    "end" INTEGER,                           -- NULL = spill forward; set = overflow to default lane
    label TEXT,                              -- group label (rows sharing start + label)
    sort_order INTEGER NOT NULL DEFAULT 0,   -- tie-break within a precedence level (UI drag order)
    enabled BOOLEAN DEFAULT 1
);


-- =============================================================================
-- LEAGUES TABLE
-- Single source of truth for configured leagues
-- Combines API config + display config in one table
-- =============================================================================

CREATE TABLE IF NOT EXISTS leagues (
    league_code TEXT PRIMARY KEY,            -- 'nfl', 'ohl', 'eng.1'

    -- Provider/API Configuration
    provider TEXT NOT NULL,                  -- 'espn', 'tsdb', 'hockeytech', 'mlbstats'
    provider_league_id TEXT NOT NULL,        -- ESPN: 'football/nfl', TSDB: '4460', MLB: '117'
    provider_league_name TEXT,               -- TSDB only: exact strLeague for API calls
    series_slug_pattern TEXT,                -- Reserved for future use
    fallback_provider TEXT,                  -- Reserved for future use
    fallback_league_id TEXT,                 -- Reserved for future use
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

    -- RETIRED (#676, v92): TSDB is premium-key only, so the free/premium
    -- tier split is gone. Column kept one release as a rollback aid — no
    -- reader remains, values are NULL, and seeding writes NULL. Drop at v93+.
    tsdb_tier TEXT CHECK(tsdb_tier IN ('free', 'premium')),

    -- Matching Classification
    -- team_vs_team: Standard team sports (NFL, NBA, NHL, Soccer, etc.)
    -- event_card: Combat sports with cards (UFC, Boxing)
    -- event: Individual/tournament sports (Golf, Tennis, Racing) - future use
    event_type TEXT DEFAULT 'team_vs_team'
        CHECK(event_type IN ('team_vs_team', 'event', 'event_card')),

    -- Cache Metadata (updated by cache refresh)
    cached_team_count INTEGER DEFAULT 0,
    last_cache_refresh TIMESTAMP,

    -- Custom League Flag
    -- 1: user-added via the UI (TSDB-only, premium-gated; see epic teamarrv2-eqz).
    --    Lives only in the DB, not schema.sql. The CRUD API only ever mutates or
    --    deletes rows with is_custom=1, so built-in leagues can't be touched.
    -- 0: built-in league seeded from schema.sql.
    is_custom INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_leagues_provider ON leagues(provider);
CREATE INDEX IF NOT EXISTS idx_leagues_sport ON leagues(sport);
CREATE INDEX IF NOT EXISTS idx_leagues_import ON leagues(import_enabled);

-- User overrides for built-in league display fields (#371). Lives OUTSIDE
-- the leagues table: the seed below replaces whole rows (INSERT OR REPLACE)
-- on every startup, so any user edit made directly to leagues would be wiped
-- (the #194 lesson). Overrides here win over curated values at read time.
CREATE TABLE IF NOT EXISTS league_overrides (
    league_code TEXT PRIMARY KEY,
    gracenote_category TEXT,                 -- overrides {gracenote_category}
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


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

INSERT OR REPLACE INTO leagues (league_code, provider, provider_league_id, provider_league_name, display_name, sport, logo_url, logo_url_dark, import_enabled, league_alias, league_id, event_type, gracenote_category, fallback_provider, fallback_league_id, tsdb_tier, enabled) VALUES
    -- Football (ESPN)
    ('nfl', 'espn', 'football/nfl', NULL, 'National Football League', 'football', 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png', NULL, 1, 'NFL', 'nfl', 'team_vs_team', 'NFL Football', NULL, NULL, NULL, 1),
    ('college-football', 'espn', 'football/college-football', NULL, 'NCAA Football', 'football', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/football.png', NULL, 1, 'NCAAF', 'ncaaf', 'team_vs_team', 'College Football', NULL, NULL, NULL, 1),
    ('ufl', 'espn', 'football/ufl', NULL, 'United Football League', 'football', 'https://a.espncdn.com/i/teamlogos/leagues/500/ufl.png', NULL, 1, 'UFL', 'ufl', 'team_vs_team', 'UFL Football', NULL, NULL, NULL, 1),
    ('cfl', 'bellmedia', 'cfl', NULL, 'Canadian Football League', 'football', 'https://r2.thesportsdb.com/images/media/league/badge/ffypv51488739128.png', NULL, 1, 'CFL', 'cfl', 'team_vs_team', 'CFL Football', NULL, NULL, NULL, 1),

    -- Basketball (ESPN)
    ('nba', 'espn', 'basketball/nba', NULL, 'National Basketball Association', 'basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png', NULL, 1, 'NBA', 'nba', 'team_vs_team', 'NBA Basketball', NULL, NULL, NULL, 1),
    ('nba-development', 'espn', 'basketball/nba-development', NULL, 'NBA G League', 'basketball', 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/nba_gleague.png', NULL, 1, 'G League', 'nbag', 'team_vs_team', 'NBA G League Basketball', NULL, NULL, NULL, 1),
    ('wnba', 'espn', 'basketball/wnba', NULL, 'Women''s National Basketball Association', 'basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png', NULL, 1, 'WNBA', 'wnba', 'team_vs_team', 'WNBA Basketball', NULL, NULL, NULL, 1),
    ('mens-college-basketball', 'espn', 'basketball/mens-college-basketball', NULL, 'NCAA Men''s Basketball', 'basketball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png', NULL, 1, 'NCAAM', 'ncaam', 'team_vs_team', 'College Basketball', NULL, NULL, NULL, 1),
    ('womens-college-basketball', 'espn', 'basketball/womens-college-basketball', NULL, 'NCAA Women''s Basketball', 'basketball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png', NULL, 1, 'NCAAW', 'ncaaw', 'team_vs_team', 'Women''s College Basketball', NULL, NULL, NULL, 1),
    ('nbl', 'espn', 'basketball/nbl', NULL, 'National Basketball League (Australia)', 'basketball', 'https://a.espncdn.com/i/teamlogos/leagues/500/nbl.png', 'https://a.espncdn.com/i/teamlogos/leagues/500-dark/nbl.png', 1, 'NBL', 'nbl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Basketball (TSDB) - Leagues not on ESPN
    -- FIBA World Cups: ESPN's basketball/fiba only tracks the World Cup final
    -- event itself (dead between tournaments); TSDB tracks qualifiers year-round.
    ('fiba', 'tsdb', '4549', 'FIBA Basketball World Cup', 'FIBA Basketball World Cup', 'basketball', 'https://r2.thesportsdb.com/images/media/league/badge/x45gjq1764423537.png', NULL, 1, 'FIBA', 'fiba', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('fiba-women', 'tsdb', '4891', 'FIBA Womens World Cup', 'FIBA Women''s Basketball World Cup', 'basketball', 'https://r2.thesportsdb.com/images/media/league/badge/tlkdaq1726930250.png', NULL, 1, 'FIBA W', 'fibaw', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('unrivaled', 'tsdb', '5622', 'Unrivaled Basketball', 'Unrivaled', 'basketball', 'https://r2.thesportsdb.com/images/media/league/badge/71mier1746291561.png', NULL, 1, NULL, 'unrivaled', 'team_vs_team', 'Unrivaled Basketball', NULL, NULL, NULL, 1),

    -- Hockey (ESPN)
    ('nhl', 'espn', 'hockey/nhl', NULL, 'National Hockey League', 'hockey', 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png', NULL, 1, 'NHL', 'nhl', 'team_vs_team', 'NHL Hockey', NULL, NULL, NULL, 1),
    ('mens-college-hockey', 'espn', 'hockey/mens-college-hockey', NULL, 'NCAA Men''s Ice Hockey', 'hockey', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png', NULL, 1, 'NCAA Hockey', 'ncaah', 'team_vs_team', 'College Hockey', NULL, NULL, NULL, 1),
    ('womens-college-hockey', 'espn', 'hockey/womens-college-hockey', NULL, 'NCAA Women''s Ice Hockey', 'hockey', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png', NULL, 1, 'NCAA W Hockey', 'ncaawh', 'team_vs_team', 'Women''s College Hockey', NULL, NULL, NULL, 1),

    -- Hockey - Olympics (ESPN)
    ('olympics-mens-ice-hockey', 'espn', 'hockey/olympics-mens-ice-hockey', NULL, 'Men''s Ice Hockey - Olympics', 'hockey', '/olympics-2026.png', NULL, 1, 'Olympic Hockey', 'olymh', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('olympics-womens-ice-hockey', 'espn', 'hockey/olympics-womens-ice-hockey', NULL, 'Women''s Ice Hockey - Olympics', 'hockey', '/olympics-2026.png', NULL, 1, 'Olympic W Hockey', 'olywh', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Hockey - CHL/Canadian Major Junior (HockeyTech)
    ('chl', 'hockeytech', 'chl', NULL, 'Canadian Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/dev/assets/CHL.png', NULL, 1, 'CHL', 'chl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ohl', 'hockeytech', 'ohl', NULL, 'Ontario Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/main/assets/OHL_LIGHTMODE.png', 'https://raw.githubusercontent.com/sethwv/game-thumbs/main/assets/OHL_DARKMODE.png', 1, 'OHL', 'ohl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('whl', 'hockeytech', 'whl', NULL, 'Western Hockey League', 'hockey', 'https://media.chl.ca/wp-content/uploads/sites/6/2023/08/18153245/Western_Hockey_League.svg_-1.png', NULL, 1, 'WHL', 'whl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('qmjhl', 'hockeytech', 'lhjmq', NULL, 'Quebec Major Junior Hockey League', 'hockey', 'https://media.chl.ca/wp-content/uploads/sites/2/2023/05/25155229/logo_q_lg.png', NULL, 1, 'QMJHL', 'qmjhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Hockey - Pro/Minor Pro Leagues (HockeyTech)
    ('ahl', 'hockeytech', 'ahl', NULL, 'American Hockey League', 'hockey', 'https://theahl.com/wp-content/uploads/sites/3/2025/10/AHL90_500.png', NULL, 1, 'AHL', 'ahl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('echl', 'hockeytech', 'echl', NULL, 'East Coast Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/dev/assets/ECHL.png', NULL, 1, 'ECHL', 'echl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('pwhl', 'hockeytech', 'pwhl', NULL, 'Professional Women''s Hockey League', 'hockey', 'https://raw.githubusercontent.com/sethwv/game-thumbs/main/assets/PWHL.png', NULL, 1, 'PWHL', 'pwhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Hockey - US Junior (HockeyTech)
    ('ushl', 'hockeytech', 'ushl', NULL, 'United States Hockey League', 'hockey', 'https://dbukjj6eu5tsf.cloudfront.net/ushl.sidearmsports.com/images/responsive_2022/ushl_on-dark.svg', NULL, 1, 'USHL', 'ushl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Hockey - Canadian Junior A (HockeyTech)
    ('ojhl', 'hockeytech', 'ojhl', NULL, 'Ontario Junior Hockey League', 'hockey', 'https://www.ojhl.ca/wp-content/uploads/sites/2/2023/04/cropped-ojhl-512.png', NULL, 1, 'OJHL', 'ojhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bchl', 'hockeytech', 'bchl', NULL, 'British Columbia Hockey League', 'hockey', 'https://bchl.ca/wp-content/uploads/2015/12/BCHL-Footer-Logo.png', NULL, 1, 'BCHL', 'bchl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('sjhl', 'hockeytech', 'sjhl', NULL, 'Saskatchewan Junior Hockey League', 'hockey', 'https://www.sjhl.ca/wp-content/uploads/sites/2/2019/08/SJHL_Logo_512px.png', NULL, 1, 'SJHL', 'sjhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ajhl', 'hockeytech', 'ajhl', NULL, 'Alberta Junior Hockey League', 'hockey', 'https://www.ajhl.ca/wp-content/uploads/sites/2/2022/05/cropped-ajhl_512.png', NULL, 1, 'AJHL', 'ajhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('mjhl', 'hockeytech', 'mjhl', NULL, 'Manitoba Junior Hockey League', 'hockey', 'https://www.mjhlhockey.ca/wp-content/uploads/sites/2/2019/06/cropped-MJHLalternate-web-600x.png', NULL, 1, 'MJHL', 'mjhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('mhl', 'hockeytech', 'mhl', NULL, 'Maritime Junior Hockey League', 'hockey', 'https://www.themhl.ca/wp-content/uploads/sites/2/2021/10/cropped-mhl_512.png', NULL, 1, 'MHL', 'mhl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('gohl', 'hockeytech', 'gojhl', NULL, 'Greater Ontario Hockey League', 'hockey', 'https://www.gohl.ca/wp-content/uploads/sites/2/2025/09/cropped-GOHLLogoTeamNavBar.png', NULL, 1, 'GOHL', 'gohl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Hockey - European Leagues (TSDB)
    ('norwegian-hockey', 'tsdb', '4926', 'Norwegian Fjordkraft-ligaen', 'Norwegian Fjordkraft-ligaen', 'hockey', 'https://r2.thesportsdb.com/images/media/league/badge/lpfdvc1697194460.png', NULL, 1, NULL, 'norwegian-hockey', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('shl', 'tsdb', '4419', 'Swedish Hockey League', 'Swedish Hockey League', 'hockey', 'https://r2.thesportsdb.com/images/media/league/badge/95fnqb1547547893.png', NULL, 1, 'SHL', 'shl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Australian Football (TSDB)
    ('afl', 'squiggle', 'afl', NULL, 'Australian Football League', 'australian-football', 'https://r2.thesportsdb.com/images/media/league/badge/wvx4721525519372.png', NULL, 1, 'AFL', 'afl', 'team_vs_team', 'AFL', NULL, NULL, NULL, 1),

    -- Baseball (ESPN)
    ('mlb', 'espn', 'baseball/mlb', NULL, 'Major League Baseball', 'baseball', 'https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png', NULL, 1, 'MLB', 'mlb', 'team_vs_team', 'MLB Baseball', NULL, NULL, NULL, 1),
    -- gracenote_category: real Gracenote titles all MiLB levels 'Minor League
    -- Baseball' — never the level name or 'MiLB' (tvnk.8 capture note).
    ('milb-aaa', 'mlbstats', '11', NULL, 'Triple-A', 'baseball', 'https://images.ctfassets.net/iiozhi00a8lc/7eeTdW5zGYe0sW2ZlIle7E/990464d4b5e4e3b3a65cb6c56ce808ea/milb-alt.svg', NULL, 1, 'AAA', 'milb-aaa', 'team_vs_team', 'Minor League Baseball', NULL, NULL, NULL, 1),
    ('milb-aa', 'mlbstats', '12', NULL, 'Double-A', 'baseball', 'https://images.ctfassets.net/iiozhi00a8lc/7eeTdW5zGYe0sW2ZlIle7E/990464d4b5e4e3b3a65cb6c56ce808ea/milb-alt.svg', NULL, 1, 'AA', 'milb-aa', 'team_vs_team', 'Minor League Baseball', NULL, NULL, NULL, 1),
    ('milb-high-a', 'mlbstats', '13', NULL, 'High-A', 'baseball', 'https://images.ctfassets.net/iiozhi00a8lc/7eeTdW5zGYe0sW2ZlIle7E/990464d4b5e4e3b3a65cb6c56ce808ea/milb-alt.svg', NULL, 1, 'High-A', 'milb-high-a', 'team_vs_team', 'Minor League Baseball', NULL, NULL, NULL, 1),
    ('milb-a', 'mlbstats', '14', NULL, 'Single-A', 'baseball', 'https://images.ctfassets.net/iiozhi00a8lc/7eeTdW5zGYe0sW2ZlIle7E/990464d4b5e4e3b3a65cb6c56ce808ea/milb-alt.svg', NULL, 1, 'Single-A', 'milb-a', 'team_vs_team', 'Minor League Baseball', NULL, NULL, NULL, 1),
    ('rookie', 'mlbstats', '16', NULL, 'Rookie', 'baseball', 'https://images.ctfassets.net/iiozhi00a8lc/7eeTdW5zGYe0sW2ZlIle7E/990464d4b5e4e3b3a65cb6c56ce808ea/milb-alt.svg', NULL, 1, 'Rookie', 'rookie', 'team_vs_team', 'Minor League Baseball', NULL, NULL, NULL, 1),
    ('college-baseball', 'espn', 'baseball/college-baseball', NULL, 'NCAA Baseball', 'baseball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/baseball.png', NULL, 1, NULL, 'ncaabb', 'team_vs_team', 'College Baseball', NULL, NULL, NULL, 1),
    ('college-softball', 'espn', 'baseball/college-softball', NULL, 'NCAA Softball', 'softball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/softball.png', NULL, 1, NULL, 'ncaasbw', 'team_vs_team', 'College Softball', NULL, NULL, NULL, 1),
    -- ESPN serves no dedicated WBC league logo (only a generic baseball icon), so hardcode the Wikimedia Commons mark.
    ('world-baseball-classic', 'espn', 'baseball/world-baseball-classic', NULL, 'World Baseball Classic', 'baseball', 'https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/World_Baseball_Classic_logo.svg/500px-World_Baseball_Classic_logo.svg.png', NULL, 1, 'WBC', 'wbc', 'team_vs_team', 'World Baseball Classic', NULL, NULL, NULL, 1),
    ('cbl', 'supabase', 'https://cbl.ca', NULL, 'Canadian Baseball League', 'baseball', 'https://upload.wikimedia.org/wikipedia/en/thumb/1/1e/Canadian_Baseball_League.svg/1280px-Canadian_Baseball_League.svg.png', NULL, 1, 'CBL', 'cbl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    -- WPBL (Women's Pro Baseball League, #284): TSDB-only — ESPN airs games but exposes no API data. 4 teams, ~30-game season.
    ('wpbl', 'tsdb', '5929', 'WPBL', 'WPBL', 'baseball', 'https://r2.thesportsdb.com/images/media/league/badge/rkx1371785226521.png', NULL, 1, 'WPBL', 'wpbl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    -- LLB (#556): Little League Baseball World Series. As with WBC, ESPN serves only a generic
    -- baseball icon for this league, so hardcode the Wikimedia mark. Teams are regional all-star
    -- squads ESPN re-seeds each season, and the calendar is whitelisted to ~3 weeks each August.
    ('llb', 'espn', 'baseball/llb', NULL, 'Little League Baseball', 'baseball', 'https://upload.wikimedia.org/wikipedia/en/thumb/8/8c/Little_League_logo.svg/500px-Little_League_logo.svg.png', NULL, 1, 'LLB', 'llb', 'team_vs_team', 'Little League Baseball', NULL, NULL, NULL, 1),

    -- Soccer (ESPN)
    ('usa.1', 'espn', 'soccer/usa.1', NULL, 'Major League Soccer', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/19.png', NULL, 1, 'MLS', 'mls', 'team_vs_team', 'MLS Soccer', NULL, NULL, NULL, 1),
    ('usa.nwsl', 'espn', 'soccer/usa.nwsl', NULL, 'National Women''s Soccer League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2323.png', NULL, 1, 'NWSL', 'nwsl', 'team_vs_team', 'NWSL Soccer', NULL, NULL, NULL, 1),
    ('usa.ncaa.m.1', 'espn', 'soccer/usa.ncaa.m.1', NULL, 'NCAA Men''s Soccer', 'soccer', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png', NULL, 1, 'NCAA Soccer', 'ncaas', 'team_vs_team', 'Men''s College Soccer', NULL, NULL, NULL, 1),
    ('usa.ncaa.w.1', 'espn', 'soccer/usa.ncaa.w.1', NULL, 'NCAA Women''s Soccer', 'soccer', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/soccer.png', NULL, 1, 'NCAA W Soccer', 'ncaaws', 'team_vs_team', 'Women''s College Soccer', NULL, NULL, NULL, 1),
    ('eng.1', 'espn', 'soccer/eng.1', NULL, 'English Premier League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/23.png', NULL, 1, 'EPL', 'epl', 'team_vs_team', 'Premier League Soccer', NULL, NULL, NULL, 1),
    ('eng.2', 'espn', 'soccer/eng.2', NULL, 'EFL Championship', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/24.png', NULL, 1, NULL, 'championship', 'team_vs_team', 'English Championship Soccer', NULL, NULL, NULL, 1),
    ('eng.3', 'espn', 'soccer/eng.3', NULL, 'EFL League One', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/25.png', NULL, 1, NULL, 'league-one', 'team_vs_team', 'English League One Soccer', NULL, NULL, NULL, 1),
    ('eng.4', 'espn', 'soccer/eng.4', NULL, 'EFL League Two', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/26.png', NULL, 1, NULL, 'league-two', 'team_vs_team', 'English League Two Soccer', NULL, NULL, NULL, 1),
    ('eng.fa', 'espn', 'soccer/eng.fa', NULL, 'FA Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/40.png', NULL, 1, NULL, 'fa-cup', 'team_vs_team', 'FA Cup Soccer', NULL, NULL, NULL, 1),
    ('eng.league_cup', 'espn', 'soccer/eng.league_cup', NULL, 'EFL Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/41.png', NULL, 1, 'Carabao Cup', 'league-cup', 'team_vs_team', 'EFL Cup Soccer', NULL, NULL, NULL, 1),
    ('esp.1', 'espn', 'soccer/esp.1', NULL, 'La Liga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/15.png', NULL, 1, NULL, 'laliga', 'team_vs_team', 'La Liga Soccer', NULL, NULL, NULL, 1),
    ('esp.copa_del_rey', 'espn', 'soccer/esp.copa_del_rey', NULL, 'Copa del Rey', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/79.png', NULL, 1, NULL, 'copa-del-rey', 'team_vs_team', 'Copa del Rey Soccer', NULL, NULL, NULL, 1),
    ('ger.1', 'espn', 'soccer/ger.1', NULL, 'Bundesliga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/10.png', NULL, 1, NULL, 'bundesliga', 'team_vs_team', 'Bundesliga Soccer', NULL, NULL, NULL, 1),
    ('ger.2', 'espn', 'soccer/ger.2', NULL, '2. Bundesliga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/9.png', NULL, 1, NULL, '2-bundesliga', 'team_vs_team', '2. Bundesliga Soccer', NULL, NULL, NULL, 1),
    ('ger.dfb_pokal', 'espn', 'soccer/ger.dfb_pokal', NULL, 'DFB-Pokal', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/80.png', NULL, 1, NULL, 'dfb-pokal', 'team_vs_team', 'DFB-Pokal Soccer', NULL, NULL, NULL, 1),
    ('ita.1', 'espn', 'soccer/ita.1', NULL, 'Serie A', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/12.png', NULL, 1, NULL, 'seriea', 'team_vs_team', 'Serie A Soccer', NULL, NULL, NULL, 1),
    ('ita.coppa_italia', 'espn', 'soccer/ita.coppa_italia', NULL, 'Coppa Italia', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/159.png', NULL, 1, NULL, 'coppa-italia', 'team_vs_team', 'Coppa Italia Soccer', NULL, NULL, NULL, 1),
    ('fra.1', 'espn', 'soccer/fra.1', NULL, 'Ligue 1', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/9.png', NULL, 1, NULL, 'ligue1', 'team_vs_team', 'Ligue 1 Soccer', NULL, NULL, NULL, 1),
    ('fra.coupe_de_france', 'espn', 'soccer/fra.coupe_de_france', NULL, 'Coupe de France', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/192.png', NULL, 1, NULL, 'coupe-de-france', 'team_vs_team', 'Coupe de France Soccer', NULL, NULL, NULL, 1),
    ('uefa.champions', 'espn', 'soccer/uefa.champions', NULL, 'UEFA Champions League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2.png', NULL, 1, 'UCL', 'ucl', 'team_vs_team', 'UEFA Champions League Soccer', NULL, NULL, NULL, 1),
    ('ksa.1', 'espn', 'soccer/ksa.1', NULL, 'Saudi Pro League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2488.png', NULL, 1, 'SPL', 'spl', 'team_vs_team', 'Saudi Pro League Soccer', NULL, NULL, NULL, 1),
    -- Additional European Leagues
    ('ned.1', 'espn', 'soccer/ned.1', NULL, 'Eredivisie', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/35.png', NULL, 1, NULL, 'eredivisie', 'team_vs_team', 'Eredivisie Soccer', NULL, NULL, NULL, 1),
    ('por.1', 'espn', 'soccer/por.1', NULL, 'Primeira Liga', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/14.png', NULL, 1, NULL, 'primeira', 'team_vs_team', 'Primeira Liga Soccer', NULL, NULL, NULL, 1),
    ('bel.1', 'espn', 'soccer/bel.1', NULL, 'Belgian Pro League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/144.png', NULL, 1, NULL, 'jupiler', 'team_vs_team', 'Belgian Pro League Soccer', NULL, NULL, NULL, 1),
    ('sco.1', 'espn', 'soccer/sco.1', NULL, 'Scottish Premiership', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/29.png', NULL, 1, 'SPFL', 'spfl', 'team_vs_team', 'Scottish Premiership Soccer', NULL, NULL, NULL, 1),
    ('sui.1', 'espn', 'soccer/sui.1', NULL, 'Swiss Super League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/17.png', NULL, 1, NULL, 'swiss-super-league', 'team_vs_team', 'Swiss Super League Soccer', NULL, NULL, NULL, 1),
    ('tur.1', 'espn', 'soccer/tur.1', NULL, 'Turkish Süper Lig', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/18.png', NULL, 1, 'Süper Lig', 'super-lig', 'team_vs_team', 'Turkish Süper Lig Soccer', NULL, NULL, NULL, 1),
    ('gre.1', 'espn', 'soccer/gre.1', NULL, 'Greek Super League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/68.png', NULL, 1, NULL, 'greek-super-league', 'team_vs_team', 'Greek Super League Soccer', NULL, NULL, NULL, 1),
    ('uefa.europa', 'espn', 'soccer/uefa.europa', NULL, 'UEFA Europa League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2310.png', NULL, 1, 'UEL', 'uel', 'team_vs_team', 'UEFA Europa League Soccer', NULL, NULL, NULL, 1),
    ('uefa.europa.conf', 'espn', 'soccer/uefa.europa.conf', NULL, 'UEFA Europa Conference League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2954.png', NULL, 1, 'UECL', 'uecl', 'team_vs_team', 'UEFA Europa Conference League Soccer', NULL, NULL, NULL, 1),
    -- International Tournaments
    -- gracenote_category: real Gracenote brands national-team tournaments WITHOUT the
    -- sport suffix (captured: 'FIFA World Cup 2026', not 'FIFA World Cup Soccer').
    -- The year is deliberately not baked in (would go stale) — templates compose it
    -- via '{gracenote_category} {year}'. Club competitions keep the ' Soccer' suffix
    -- (captured: 'Premier League Soccer').
    ('fifa.world', 'espn', 'soccer/fifa.world', NULL, 'FIFA World Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/4.png', NULL, 1, 'World Cup', 'world-cup', 'team_vs_team', 'FIFA World Cup', NULL, NULL, NULL, 1),
    ('fifa.wwc', 'espn', 'soccer/fifa.wwc', NULL, 'FIFA Women''s World Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/131.png', NULL, 1, 'WWC', 'wwc', 'team_vs_team', 'FIFA Women''s World Cup', NULL, NULL, NULL, 1),
    ('uefa.euro', 'espn', 'soccer/uefa.euro', NULL, 'UEFA European Championship', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/60.png', NULL, 1, 'Euro', 'euro', 'team_vs_team', 'UEFA Euro', NULL, NULL, NULL, 1),
    ('conmebol.america', 'espn', 'soccer/conmebol.america', NULL, 'Copa America', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/73.png', NULL, 1, NULL, 'copa-america', 'team_vs_team', 'Copa America', NULL, NULL, NULL, 1),
    ('concacaf.gold', 'espn', 'soccer/concacaf.gold', NULL, 'CONCACAF Gold Cup', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/128.png', NULL, 1, 'Gold Cup', 'gold-cup', 'team_vs_team', 'CONCACAF Gold Cup', NULL, NULL, NULL, 1),
    ('concacaf.nations.league', 'espn', 'soccer/concacaf.nations.league', NULL, 'CONCACAF Nations League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2737.png', NULL, 1, 'CNL', 'cnl', 'team_vs_team', 'CONCACAF Nations League', NULL, NULL, NULL, 1),
    -- Americas Leagues
    ('mex.1', 'espn', 'soccer/mex.1', NULL, 'Liga MX', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/22.png', NULL, 1, NULL, 'ligamx', 'team_vs_team', 'Liga MX Soccer', NULL, NULL, NULL, 1),
    ('arg.1', 'espn', 'soccer/arg.1', NULL, 'Argentine Liga Profesional', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/1.png', NULL, 1, 'LPA', 'lpa', 'team_vs_team', 'Argentine Liga Profesional Soccer', NULL, NULL, NULL, 1),
    ('bra.1', 'espn', 'soccer/bra.1', NULL, 'Brazilian Serie A', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/85.png', NULL, 1, 'Brasileirao', 'brasileirao', 'team_vs_team', 'Brazilian Serie A Soccer', NULL, NULL, NULL, 1),
    -- Brazilian state championships (#60) — ESPN covers these 4 (free, richer data); the other 23 are TSDB-only (see premium block below)
    ('bra.camp.carioca', 'espn', 'soccer/bra.camp.carioca', NULL, 'Brazilian Campeonato Carioca', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2265.png', NULL, 1, 'Carioca', 'carioca', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.paulista', 'espn', 'soccer/bra.camp.paulista', NULL, 'Brazilian Campeonato Paulista', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2322.png', NULL, 1, 'Paulista', 'paulista', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.gaucho', 'espn', 'soccer/bra.camp.gaucho', NULL, 'Brazilian Campeonato Gaucho', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2272.png', NULL, 1, 'Gaucho', 'gaucho', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.mineiro', 'espn', 'soccer/bra.camp.mineiro', NULL, 'Brazilian Campeonato Mineiro', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/2360.png', NULL, 1, 'Mineiro', 'mineiro', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('col.1', 'espn', 'soccer/col.1', NULL, 'Colombian Primera A', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/1543.png', NULL, 1, NULL, 'dimayor', 'team_vs_team', 'Colombian Primera A Soccer', NULL, NULL, NULL, 1),
    ('conmebol.libertadores', 'espn', 'soccer/conmebol.libertadores', NULL, 'Copa Libertadores', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/13.png', NULL, 1, 'Libertadores', 'libertadores', 'team_vs_team', 'Copa Libertadores Soccer', NULL, NULL, NULL, 1),
    ('conmebol.sudamericana', 'espn', 'soccer/conmebol.sudamericana', NULL, 'Copa Sudamericana', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/49.png', NULL, 1, 'Sudamericana', 'sudamericana', 'team_vs_team', 'Copa Sudamericana Soccer', NULL, NULL, NULL, 1),
    -- Asia/Pacific Leagues
    ('jpn.1', 'espn', 'soccer/jpn.1', NULL, 'J1 League', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/1842.png', NULL, 1, 'J-League', 'jleague', 'team_vs_team', 'J1 League Soccer', NULL, NULL, NULL, 1),
    ('aus.1', 'espn', 'soccer/aus.1', NULL, 'A-League Men', 'soccer', 'https://a.espncdn.com/i/leaguelogos/soccer/500/114.png', NULL, 1, 'A-League', 'aleague', 'team_vs_team', 'A-League Men Soccer', NULL, NULL, NULL, 1),

    -- Soccer (TSDB Premium) - Leagues requiring premium key for full event coverage
    -- uru.2: ESPN data is severely stale (2011 roster, 2010 scoreboard) — TSDB only
    ('uru.2', 'tsdb', '5072', 'Uruguayan Segunda División', 'AUF Segunda', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/htc3kb1740672581.png', NULL, 1, NULL, 'uru.2', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('svenska-cupen', 'tsdb', '4756', 'Svenska Cupen', 'Svenska Cupen', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/p37u1n1694211430.png', NULL, 1, NULL, 'svenska-cupen', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    -- Brazilian state championships (#60) — the 23 states ESPN does NOT cover (Carioca/Paulista/Gaucho/Mineiro live on ESPN above); idLeague + badge validated against TSDB search_all_leagues.php, 2026 fixtures confirmed
    ('bra.camp.acreano', 'tsdb', '5676', 'Brazilian Campeonato Acreano', 'Brazilian Campeonato Acreano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/4d9le21754492204.png', NULL, 1, 'Acreano', 'acreano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.alagoano', 'tsdb', '5677', 'Brazilian Campeonato Alagoano', 'Brazilian Campeonato Alagoano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/2tcrj61767438744.png', NULL, 1, 'Alagoano', 'alagoano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.amapaense', 'tsdb', '5678', 'Brazilian Campeonato Amapaense', 'Brazilian Campeonato Amapaense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/6sy3xd1754493984.png', NULL, 1, 'Amapaense', 'amapaense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.amazonense', 'tsdb', '5679', 'Brazilian Campeonato Amazonense', 'Brazilian Campeonato Amazonense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/t9nfej1754494955.png', NULL, 1, 'Amazonense', 'amazonense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.baiano', 'tsdb', '5684', 'Brazilian Campeonato Baiano', 'Brazilian Campeonato Baiano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/ehk7hw1756631450.png', NULL, 1, 'Baiano', 'baiano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.brasiliense', 'tsdb', '5685', 'Brazilian Campeonato Brasiliense', 'Brazilian Campeonato Brasiliense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/7z9n1v1756632496.png', NULL, 1, 'Brasiliense', 'brasiliense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.capixaba', 'tsdb', '5686', 'Brazilian Campeonato Capixaba', 'Brazilian Campeonato Capixaba', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/5f2rcm1756633054.png', NULL, 1, 'Capixaba', 'capixaba', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.catarinense', 'tsdb', '5687', 'Brazilian Campeonato Catarinense', 'Brazilian Campeonato Catarinense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/rvn16n1756635839.png', NULL, 1, 'Catarinense', 'catarinense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.cearense', 'tsdb', '5689', 'Brazilian Campeonato Cearense', 'Brazilian Campeonato Cearense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/gm4k7s1756639011.png', NULL, 1, 'Cearense', 'cearense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.goiano', 'tsdb', '5760', 'Brazilian Campeonato Goiano', 'Brazilian Campeonato Goiano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/8c7y7a1766562491.png', NULL, 1, 'Goiano', 'goiano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.maranhense', 'tsdb', '5761', 'Brazilian Campeonato Maranhense', 'Brazilian Campeonato Maranhense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/elyo6m1766564369.png', NULL, 1, 'Maranhense', 'maranhense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.matogrossense', 'tsdb', '5762', 'Brazilian Campeonato MatoGrossense', 'Brazilian Campeonato MatoGrossense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/sdodne1766565649.png', NULL, 1, 'MatoGrossense', 'matogrossense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.paraense', 'tsdb', '5764', 'Brazilian Campeonato Paraense', 'Brazilian Campeonato Paraense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/2l12dd1766567504.png', NULL, 1, 'Paraense', 'paraense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.paraibano', 'tsdb', '5765', 'Brazilian Campeonato Paraibano', 'Brazilian Campeonato Paraibano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/35w0h11766570162.png', NULL, 1, 'Paraibano', 'paraibano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.paranaense', 'tsdb', '5766', 'Brazilian Campeonato Paranaense', 'Brazilian Campeonato Paranaense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/fp5qis1766570793.png', NULL, 1, 'Paranaense', 'paranaense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.pernambucano', 'tsdb', '5768', 'Brazilian Campeonato Pernambucano', 'Brazilian Campeonato Pernambucano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/2rleuk1766573797.png', NULL, 1, 'Pernambucano', 'pernambucano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.piauiense', 'tsdb', '5769', 'Brazilian Campeonato Piauiense', 'Brazilian Campeonato Piauiense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/xvlw7i1766575848.png', NULL, 1, 'Piauiense', 'piauiense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.potiguar', 'tsdb', '5770', 'Brazilian Campeonato Potiguar', 'Brazilian Campeonato Potiguar', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/dw5y8q1766577811.png', NULL, 1, 'Potiguar', 'potiguar', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.rondoniense', 'tsdb', '5771', 'Brazilian Campeonato Rondoniense', 'Brazilian Campeonato Rondoniense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/vnv80t1766578247.png', NULL, 1, 'Rondoniense', 'rondoniense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.roraimense', 'tsdb', '5772', 'Brazilian Campeonato Roraimense', 'Brazilian Campeonato Roraimense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/rxf2n11766581225.png', NULL, 1, 'Roraimense', 'roraimense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.sergipano', 'tsdb', '5773', 'Brazilian Campeonato Sergipano', 'Brazilian Campeonato Sergipano', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/g5e1ig1766584230.png', NULL, 1, 'Sergipano', 'sergipano', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.sulmatogrossense', 'tsdb', '5774', 'Brazilian Campeonato SulMatoGrossense', 'Brazilian Campeonato SulMatoGrossense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/rzdwrs1766584859.png', NULL, 1, 'SulMatoGrossense', 'sulmatogrossense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bra.camp.tocantinense', 'tsdb', '5775', 'Brazilian Campeonato Tocantinense', 'Brazilian Campeonato Tocantinense', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/ghbod11766585463.png', NULL, 1, 'Tocantinense', 'tocantinense', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    -- Community league requests (#220-229) — provider_league_name validated against TSDB lookupleague.php (strLeague exact)
    ('can.1', 'tsdb', '4820', 'Canadian Premier League', 'Canadian Premier League', 'soccer', 'https://r2.thesportsdb.com/images/media/league/logo/7jqvqs1589104556.png', NULL, 1, NULL, 'can.1', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('swe.2', 'tsdb', '4403', 'Swedish Superettan', 'Swedish Superettan', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/uvzmu21707459258.png', NULL, 1, NULL, 'swe.2', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('swe.3.n', 'tsdb', '4674', 'Swedish Division 1 North', 'Swedish Division 1 North', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/w8f05c1579901188.png', NULL, 1, NULL, 'swe.3.n', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('swe.3.s', 'tsdb', '4845', 'Swedish Division 1 South', 'Swedish Division 1 South', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/w8f05c1579901188.png', NULL, 1, NULL, 'swe.3.s', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ven.2', 'tsdb', '5659', 'Venezuelan Segunda Division', 'Venezuelan Segunda División', 'soccer', 'https://r2.thesportsdb.com/images/media/league/logo/9tgsja1754302332.png', NULL, 1, NULL, 'ven.2', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('gam.1', 'tsdb', '5238', 'Gambia GFA League', 'Gambia GFA League', 'soccer', 'https://r2.thesportsdb.com/images/media/league/badge/tqdf9k1645215996.png', NULL, 1, NULL, 'gam.1', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ice.1', 'tsdb', '4642', 'Icelandic Úrvalsdeild karla', 'Icelandic Úrvalsdeild karla', 'soccer', 'https://r2.thesportsdb.com/images/media/league/logo/7z7rcg1686156462.png', NULL, 1, NULL, 'ice.1', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ice.2', 'tsdb', '4906', 'Icelandic 1 deild karla', 'Icelandic 1 deild karla', 'soccer', 'https://r2.thesportsdb.com/images/media/league/logo/ent23s1614355568.png', NULL, 1, NULL, 'ice.2', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('arb.1', 'tsdb', '5230', 'Aruban Division di Honor', 'Aruban Division di Honor', 'soccer', 'https://r2.thesportsdb.com/images/media/league/logo/1uwxfa1645196203.png', NULL, 1, NULL, 'arb.1', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('nifl.1', 'tsdb', '4659', 'Northern Irish Premiership', 'Northern Irish Premiership', 'soccer', 'https://r2.thesportsdb.com/images/media/league/logo/at2i0n1625851036.png', NULL, 1, NULL, 'nifl.1', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- MMA (ESPN) - Combat sport with event cards
    ('ufc', 'espn', 'mma/ufc', NULL, 'Ultimate Fighting Championship', 'mma', 'https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png', NULL, 0, 'UFC', 'ufc', 'event_card', NULL, NULL, NULL, NULL, 1),

    -- Volleyball (ESPN)
    ('mens-college-volleyball', 'espn', 'volleyball/mens-college-volleyball', NULL, 'NCAA Men''s Volleyball', 'volleyball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png', NULL, 1, 'NCAA Volleyball', 'ncaavb', 'team_vs_team', 'Men''s College Volleyball', NULL, NULL, NULL, 1),
    ('womens-college-volleyball', 'espn', 'volleyball/womens-college-volleyball', NULL, 'NCAA Women''s Volleyball', 'volleyball', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/volleyball.png', NULL, 1, 'NCAA W Volleyball', 'ncaawvb', 'team_vs_team', 'Women''s College Volleyball', NULL, NULL, NULL, 1),

    -- Lacrosse - NCAA (ESPN)
    ('mens-college-lacrosse', 'espn', 'lacrosse/mens-college-lacrosse', NULL, 'NCAA Men''s Lacrosse', 'lacrosse', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/lacrosse.png', NULL, 1, 'NCAA Lacrosse', 'ncaalax', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('womens-college-lacrosse', 'espn', 'lacrosse/womens-college-lacrosse', NULL, 'NCAA Women''s Lacrosse', 'lacrosse', 'https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/lacrosse.png', NULL, 1, 'NCAA W Lacrosse', 'ncaawlax', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Lacrosse (ESPN)
    ('nll', 'espn', 'lacrosse/nll', NULL, 'National Lacrosse League', 'lacrosse', 'https://a.espncdn.com/guid/5f77fe12-e54f-41a1-904e-77135452f348/logos/default.png', NULL, 1, 'NLL', 'nll', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('pll', 'espn', 'lacrosse/pll', NULL, 'Premier Lacrosse League', 'lacrosse', 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/pll.png', NULL, 1, 'PLL', 'pll', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Cricket (TSDB) - Premium tier, requires TSDB premium key for full event coverage
    ('ipl', 'tsdb', '4460', 'Indian Premier League', 'Indian Premier League', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/gaiti11741709844.png', NULL, 1, 'IPL', 'ipl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('bbl', 'tsdb', '4461', 'Australian Big Bash League', 'Big Bash League', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/yko7ny1546635346.png', NULL, 1, 'BBL', 'bbl', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('sa20', 'tsdb', '5532', 'SA20', 'South Africa Twenty20', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/aakvuk1734183412.png', NULL, 1, 'SA20', 'sa20', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    -- MLC: short US T20 season fits the free rolling next-events window (eventsnextleague), so no premium key needed.
    ('mlc', 'tsdb', '5401', 'Major League Cricket', 'Major League Cricket', 'cricket', 'https://r2.thesportsdb.com/images/media/league/badge/mbbos01689159510.png', NULL, 1, 'MLC', 'mlc', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Rugby (ESPN)
    ('rwc',   'espn', 'rugby/164205',    NULL, 'Rugby World Cup',                 'rugby', 'https://upload.wikimedia.org/wikipedia/commons/a/a3/Rugby_World_Cup_Logo%2C_used_post_RWC_2023.svg', NULL, 1, 'RWC',   'rwc',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('wrwc',  'espn', 'rugby/289237',    NULL, 'Women''s Rugby World Cup',        'rugby', 'https://upload.wikimedia.org/wikipedia/commons/6/66/Rugby_World_Cup_footer_logo_%28post-2023%29.svg', NULL, 1, 'WRWC',  'wrwc',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('6n',    'espn', 'rugby/180659',    NULL, 'Six Nations',                     'rugby', 'https://upload.wikimedia.org/wikipedia/commons/7/72/Guinness_Six_Nations_logo.png', NULL, 1, '6N',    '6n',    'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('trc',   'espn', 'rugby/244293',    NULL, 'The Rugby Championship',          'rugby', 'https://upload.wikimedia.org/wikipedia/commons/6/69/The_Rugby_Championship_logo_%28white_background%29.png', NULL, 1, 'TRC',   'trc',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('natchamp', 'espn', 'rugby/17567',  NULL, 'Nations Championship',            'rugby', 'https://upload.wikimedia.org/wikipedia/commons/2/20/2026_Nations_Championship_Text_Logo.svg', NULL, 1, 'NC', 'natchamp', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('super-rugby', 'espn', 'rugby/242041', NULL, 'Super Rugby Pacific',          'rugby', 'https://upload.wikimedia.org/wikipedia/en/2/25/Super_Rugby_Pacific_logo.png', NULL, 1, 'SRP',   'srp',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('urc',   'espn', 'rugby/270557',    NULL, 'United Rugby Championship',       'rugby', 'https://upload.wikimedia.org/wikipedia/commons/d/d5/United_Rugby_Championship_logo.png', NULL, 1, 'URC',   'urc',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('prem',  'espn', 'rugby/267979',    NULL, 'Gallagher Premiership',           'rugby', 'https://upload.wikimedia.org/wikipedia/commons/7/76/PREM_Rugby_logo_2025.png', NULL, 1, 'PREM',  'prem',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('top14', 'espn', 'rugby/270559',    NULL, 'French Top 14',                   'rugby', 'https://upload.wikimedia.org/wikipedia/commons/7/7d/Top_14_Logo.svg', NULL, 1, 'TOP14', 'top14', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ercc',  'espn', 'rugby/271937',    NULL, 'European Rugby Champions Cup',    'rugby', 'https://upload.wikimedia.org/wikipedia/en/6/65/InvestecChampionsCupLogo.svg', NULL, 1, 'ERCC',  'ercc',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('epcr',  'espn', 'rugby/272073',    NULL, 'European Rugby Challenge Cup',    'rugby', 'https://upload.wikimedia.org/wikipedia/commons/1/17/EPCR_Logo.png', NULL, 1, 'EPCR',  'epcr',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('mlr',   'espn', 'rugby/289262',    NULL, 'Major League Rugby',              'rugby', 'https://upload.wikimedia.org/wikipedia/commons/b/b8/MLR_logo.png', NULL, 1, 'MLR',   'mlr',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('cc',    'espn', 'rugby/270555',    NULL, 'Currie Cup',                      'rugby', 'https://upload.wikimedia.org/wikipedia/en/b/bd/Currie_Cup_logo.svg', NULL, 1, 'CC',    'cc',    'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('npc',   'espn', 'rugby/270563',    NULL, 'National Provincial Championship', 'rugby', 'https://upload.wikimedia.org/wikipedia/en/8/8e/NPC-Logo_50_Years.png', NULL, 1, 'NPC',   'npc',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('urba',  'espn', 'rugby/2009',      NULL, 'URBA Primera A',                  'rugby', 'https://upload.wikimedia.org/wikipedia/en/1/1f/Urba_logo.png', NULL, 1, 'URBA',  'urba',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('itm',   'espn', 'rugby/289234',    NULL, 'International Test Match',        'rugby', 'https://upload.wikimedia.org/wikipedia/en/9/97/World_Rugby_logo.svg', NULL, 1, 'ITM',   'itm',   'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('lions', 'espn', 'rugby/268565',    NULL, 'British and Irish Lions Tour',    'rugby', 'https://upload.wikimedia.org/wikipedia/en/9/93/British_%26_Irish_Lions_logo_%282023%29.svg', NULL, 1, 'Lions', 'lions', 'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('om7s',  'espn', 'rugby/282',       NULL, 'Olympic Men''s Rugby Sevens',     'rugby', 'https://upload.wikimedia.org/wikipedia/commons/f/f2/Rugby_sevens_pictogram.svg', NULL, 1, 'OM7S',  'om7s',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('ow7s',  'espn', 'rugby/283',       NULL, 'Olympic Women''s Rugby Sevens',   'rugby', 'https://upload.wikimedia.org/wikipedia/commons/f/f2/Rugby_sevens_pictogram.svg', NULL, 1, 'OW7S',  'ow7s',  'team_vs_team', NULL, NULL, NULL, NULL, 1),
    ('nrl',   'espn', 'rugby-league/3',  NULL, 'National Rugby League',           'rugby', 'https://upload.wikimedia.org/wikipedia/en/5/50/National_Rugby_League.svg', NULL, 1, 'NRL',   'nrl',   'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Rugby League (TSDB) - ESPN's rugby-league tree carries only the NRL, so Super League comes from TSDB.
    -- Premium tier: free-tier eventsseason caps at 5 events and eventspastleague returns 1, so a keyless fetch is near-empty.
    ('super-league', 'tsdb', '4415', 'English Rugby League Super League', 'English Rugby League Super League', 'rugby', 'https://r2.thesportsdb.com/images/media/league/badge/gp2sfv1641835011.png', NULL, 1, 'SL', 'super-league', 'team_vs_team', NULL, NULL, NULL, NULL, 1),

    -- Boxing (TSDB) - Combat sport with event cards
    ('boxing', 'tsdb', '4445', 'Boxing', 'Boxing', 'boxing', NULL, NULL, 0, NULL, 'boxing', 'event_card', NULL, NULL, NULL, NULL, 1),

    -- Motorsports (ESPN) - Race weekends with multi-driver sessions, no home/away
    -- 'f1' is the fully-implemented reference league. IndyCar verified 2026-07
    -- (bead h31.3): ESPN exposes the race session ONLY (no practice/qualifying
    -- anywhere in the payload) — the untyped-competition fallback maps it to a
    -- single 'race' session by design. MotoGP remains unverified (h31.2, TSDB).
    ('f1', 'espn', 'racing/f1', NULL, 'Formula 1', 'racing', 'https://a.espncdn.com/i/teamlogos/leagues/500/f1.png', NULL, 0, 'F1', 'f1', 'event', 'Formula 1 Racing', NULL, NULL, NULL, 1),

    -- Motorsports (NASCAR API) - authoritative session schedules from cf.nascar.com.
    -- provider_league_id encodes the NASCAR series number (1=Cup, 2=ORAP, 3=Trucks).
    -- The NASCAR provider uses hardcoded URL patterns; this field is for reference only.
    -- gracenote_category NULL: real Gracenote titles NASCAR by full series name
    -- (captured: 'NASCAR Craftsman Truck Series'), which display_name already is —
    -- the event_type-aware fallback serves it without duplicating the string here.
    ('nascar-cup',      'nascar', '1', NULL, 'NASCAR Cup Series',                    'racing', 'https://a.espncdn.com/combiner/i?img=/redesign/assets/img/icons/ESPN-icon-NASCAR.png', NULL, 0, 'NASCAR Cup',   'nascar-cup',      'event', NULL, NULL, NULL, NULL, 1),
    ('nascar-xfinity',  'nascar', '2', NULL, 'NASCAR O''Reilly Auto Parts Series',   'racing', 'https://a.espncdn.com/combiner/i?img=/redesign/assets/img/icons/ESPN-icon-NASCAR.png', NULL, 0, 'NASCAR ORAP',  'nascar-xfinity',  'event', NULL, NULL, NULL, NULL, 1),
    ('nascar-truck',    'nascar', '3', NULL, 'NASCAR Craftsman Truck Series',         'racing', 'https://a.espncdn.com/combiner/i?img=/redesign/assets/img/icons/ESPN-icon-NASCAR.png', NULL, 0, 'NASCAR Trucks','nascar-truck',    'event', NULL, NULL, NULL, NULL, 1),
    ('indycar', 'espn', 'racing/irl', NULL, 'IndyCar Series', 'racing', 'https://a.espncdn.com/combiner/i?img=/i/espn/teamlogos/500/indycar_series.png', NULL, 0, 'IndyCar', 'indycar', 'event', 'IndyCar Racing', NULL, NULL, NULL, 1),
    -- Disabled: ESPN's racing/motogp scoreboard endpoint returns HTTP 400 (no usable schedule/logo data).
    -- Re-enable once migrated to TSDB (idLeague 4407) - planned v2 feature alongside IMSA/WEC session grouping.
    ('motogp', 'espn', 'racing/motogp', NULL, 'MotoGP', 'racing', 'https://a.espncdn.com/i/teamlogos/leagues/500/motogp.png', NULL, 0, 'MotoGP', 'motogp', 'event', 'MotoGP Racing', NULL, NULL, NULL, 0),

    -- Motorsports (TSDB) - session schedules grouped from TheSportsDB's flat
    -- per-event-per-session season data (teamarr/providers/tsdb/racing.py).
    ('imsa', 'tsdb', '4488', 'IMSA SportsCar Championship', 'IMSA WeatherTech SportsCar Championship', 'racing', 'https://r2.thesportsdb.com/images/media/league/badge/t3fpd41536244390.png', NULL, 0, 'IMSA', 'imsa', 'event', NULL, NULL, NULL, NULL, 1),
    ('wec', 'tsdb', '4413', 'WEC', 'FIA World Endurance Championship', 'racing', 'https://r2.thesportsdb.com/images/media/league/badge/2fjrko1705526433.png', NULL, 0, 'WEC', 'wec', 'event', NULL, NULL, NULL, NULL, 1),

    -- Tennis (ESPN) - One Event per MATCH (players as home/away), parsed from
    -- tournament groupings (teamarr/providers/espn/tennis.py). Grand slams are
    -- served on both endpoints; each league keeps only its own draw types
    -- (atp: men's + mixed doubles, wta: women's) so subscribing both never
    -- duplicates a match. import_enabled=0: players aren't importable teams.
    ('atp', 'espn', 'tennis/atp', NULL, 'ATP Tour', 'tennis', 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/42/Letters_ATP.svg/500px-Letters_ATP.svg.png', NULL, 0, 'ATP', 'atp', 'event', 'Tennis', NULL, NULL, NULL, 1),
    ('wta', 'espn', 'tennis/wta', NULL, 'WTA Tour', 'tennis', 'https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/WTA_2025.svg/500px-WTA_2025.svg.png', NULL, 0, 'WTA', 'wta', 'event', 'Tennis', NULL, NULL, NULL, 1);

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
    -- direct: unambiguous non-fuzzy match (racing single-event, tennis surname pair)
    -- epg: matched via EPG program title (epic 183)
    match_method TEXT DEFAULT 'fuzzy'
        CHECK(match_method IN ('cache', 'user_corrected', 'alias', 'pattern', 'fuzzy', 'keyword', 'no_match', 'direct', 'epg')),

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
-- Provider group cache (#91, epic y5l8): conferences/divisions from the ESPN
-- core-API season tree. Refreshed alongside team cache. Framed generically as
-- "provider groups" so pro divisions can ride the same tables later; currently
-- populated for NCAA football/basketball only (ESPN has no conference data for
-- other college sports).
-- =============================================================================

CREATE TABLE IF NOT EXISTS provider_group_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,               -- 'espn'
    league TEXT NOT NULL,                 -- league slug: 'college-football', 'mens-college-basketball'
    group_key TEXT NOT NULL,              -- provider's stable group id (SEC=8, Big Ten=5, ...)
    group_name TEXT NOT NULL,             -- 'Southeastern Conference'
    group_abbrev TEXT,                    -- 'SEC' (core tree shortName)
    -- Parent group in the provider's season tree: the division the conference
    -- sits under (football 80/81 = FBS/FCS, basketball 50 = NCAA Division I).
    -- Powers the {division} channel-group wildcard (#717).
    parent_key TEXT,                      -- '80'
    parent_name TEXT,                     -- 'FBS'
    season INTEGER,                       -- season year the tree was read from
    last_refreshed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(provider, league, group_key)
);

CREATE TABLE IF NOT EXISTS provider_group_members (
    group_cache_id INTEGER NOT NULL REFERENCES provider_group_cache(id) ON DELETE CASCADE,
    provider_team_id TEXT NOT NULL,       -- joins team_cache.provider_team_id

    UNIQUE(group_cache_id, provider_team_id)
);

CREATE INDEX IF NOT EXISTS idx_pgc_league ON provider_group_cache(provider, league);
CREATE INDEX IF NOT EXISTS idx_pgm_team ON provider_group_members(provider_team_id);


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
    -- Cache key (e.g., "events_v2:nfl:2026-01-06:America/Detroit")
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
    match_type TEXT DEFAULT 'event'          -- 'event' (TEAM_VS_TEAM) or 'team' (TEAM_ONLY)
        CHECK(match_type IN ('event', 'team')),
    match_method TEXT,                        -- how the stream was matched: 'epg', 'fuzzy', 'cache', etc. (drives the epg_match stream-ordering rule)
    feed_team_id TEXT,                        -- (#489) resolved feed/matched team (provider team id, same namespace as managed_channels.feed_team_id); drives team_feed/not_team_feed ordering rules ahead of the name regex. NULL = no team resolved.
    feed_side TEXT                            -- (#533) which side this feed is: 'home', 'away', or NULL = UNKNOWN. Tri-state by design — NULL is a real value (no feed signal, or a sport with no sides), never "not home therefore away". Drives home_feed/away_feed ordering rules; unknown matches neither.
        CHECK(feed_side IN ('home', 'away')),
    dispatcharr_channel_group TEXT,           -- (ybt.3) the DP channel's own group name, for channel-source streams; drives the 'dispatcharr_group' stream-ordering rule. NULL for non-channel-source streams.
    dispatcharr_channel_group_id INTEGER,     -- Stable DP channel-group id for channel-source stream-profile overrides. NULL for non-channel-source streams.

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

    -- Time-windowed membership (epic teamarrv2-183.5).
    -- NULL = full-life membership (default; dedicated/name-matched streams stay
    -- attached for the channel's whole life). Non-NULL = time-shared linear
    -- stream that is only active in Dispatcharr while attach_at <= now < detach_at
    -- (derived from the matched EPG program slot +/- the global stream buffers).
    attach_at TIMESTAMP,
    detach_at TIMESTAMP,

    -- Stream stats cached from Dispatcharr (stream_stats JSON on the Stream object)
    stream_stats JSON DEFAULT NULL,
    stream_stats_updated_at TIMESTAMP DEFAULT NULL,

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
-- DETECTION_KEYWORDS TABLE
-- User-defined detection keywords for stream classification
-- Extends the built-in patterns in DetectionKeywordService
-- =============================================================================

CREATE TABLE IF NOT EXISTS detection_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Keyword category determines how it's used in classification
    category TEXT NOT NULL CHECK(category IN (
        'event_type_keywords',  -- Keywords that detect event type (target_value = EVENT_CARD, etc.)
        'league_hints',         -- Patterns that map to league code(s)
        'sport_hints',          -- Patterns that map to sport name
        'placeholders',         -- Patterns for placeholder/filler streams
        'card_segments',        -- Patterns for UFC card segments (prelims, main)
        'exclusions',           -- Patterns to exclude from matching (weigh-ins, etc.)
        'separators'            -- Game separators (vs, @, at)
    )),

    -- The keyword or pattern to match
    keyword TEXT NOT NULL,          -- Plain text keyword or regex pattern
    is_regex BOOLEAN DEFAULT 0,     -- If true, keyword is treated as regex

    -- Target value (meaning depends on category)
    -- league_hints: league code or JSON array of codes (e.g., "nfl" or '["eng.2","eng.3"]')
    -- sport_hints: sport name (e.g., "Hockey")
    -- card_segments: segment name (e.g., "main_card")
    -- Others: unused (NULL)
    target_value TEXT,

    -- Control flags
    enabled BOOLEAN DEFAULT 1,
    priority INTEGER DEFAULT 0,     -- Higher priority checked first (within category)

    -- Optional metadata
    description TEXT,               -- User notes about this keyword

    UNIQUE(category, keyword)
);

CREATE INDEX IF NOT EXISTS idx_detection_keywords_category ON detection_keywords(category);
CREATE INDEX IF NOT EXISTS idx_detection_keywords_enabled ON detection_keywords(enabled);


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
-- DELETED_DEFAULT_TEMPLATES TABLE (#487)
-- Tombstones for starter-set template names the user deleted or renamed
-- away. seed_default_templates skips these, so user intent survives
-- restarts. Cleared by the explicit "Restore starter templates" action.
-- =============================================================================

CREATE TABLE IF NOT EXISTS deleted_default_templates (
    name TEXT PRIMARY KEY,
    deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    -- Run identification. One row per full generation ('full_epg'); per-group
    -- sub-runs were retired in v90 (#645) — their breakdown lives in
    -- extra_metrics.groups. group_id/team_id are legacy, always NULL.
    run_type TEXT NOT NULL,
    run_id TEXT,             -- Optional unique run identifier (UUID)
    group_id INTEGER,
    team_id INTEGER,

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
-- LIFETIME_STATS TABLE
-- Singleton accumulator for all-time full-EPG generation totals.
-- processing_runs is pruned to a rolling window (cleanup_old_runs) and can be
-- cleared from the UI, so lifetime totals cannot be derived from it. Instead,
-- cleanup_old_runs/clear_all_runs fold the sums of full_epg rows into this row
-- BEFORE deleting them; get_current_stats reports lifetime + live.
-- =============================================================================

CREATE TABLE IF NOT EXISTS lifetime_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    runs INTEGER DEFAULT 0,
    successful_runs INTEGER DEFAULT 0,
    failed_runs INTEGER DEFAULT 0,
    streams_matched INTEGER DEFAULT 0,
    streams_unmatched INTEGER DEFAULT 0,
    streams_cached INTEGER DEFAULT 0,
    channels_created INTEGER DEFAULT 0,
    channels_deleted INTEGER DEFAULT 0,
    programmes_total INTEGER DEFAULT 0,
    programmes_events INTEGER DEFAULT 0,
    programmes_pregame INTEGER DEFAULT 0,
    programmes_postgame INTEGER DEFAULT 0,
    programmes_idle INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO lifetime_stats (id) VALUES (1);


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

    -- Feed separation (Phase 1 detection result)
    feed_hint TEXT,  -- 'home', 'away', or NULL (from HOME/AWAY token detection)

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


-- =============================================================================
-- SCHEMA MIGRATIONS
-- Run on every startup - must be idempotent (safe to run multiple times)
-- =============================================================================

-- v47: Add custom_regex_config column to event_epg_groups (JSON subcategories)
-- This replaces the flat custom_regex_* columns with organized event-type structure
-- Old columns kept for migration/backwards compatibility

-- Add custom_regex_config column if it doesn't exist (SQLite workaround)
-- Note: SQLite will fail silently if column exists - this is expected behavior
-- We wrap in a trigger-like check by using a temp table

-- v47: Migrate combat_sports category to event_type_keywords
-- Set target_value to EVENT_CARD for existing combat_sports keywords
UPDATE detection_keywords
SET category = 'event_type_keywords',
    target_value = COALESCE(target_value, 'EVENT_CARD')
WHERE category = 'combat_sports';

-- v48: Migrate group.template_id to group_templates table
-- Creates a default template assignment (sports=NULL, leagues=NULL) for each group
-- that has a template_id set but no entries in group_templates yet
INSERT INTO group_templates (group_id, template_id, sports, leagues)
SELECT id, template_id, NULL, NULL
FROM event_epg_groups
WHERE template_id IS NOT NULL
  AND id NOT IN (SELECT DISTINCT group_id FROM group_templates);

-- v65: Event-anchored channel lifecycle overhaul
-- Consolidate 6 create timing modes → 2 (same_day, before_event)
-- Consolidate 7 delete timing modes → 2 (same_day, after_event)
-- Add configurable buffer minutes for event-anchored timing
-- NOTE: Migration handled in Python (_run_migrations) because SQLite CHECK
-- constraints are baked at table creation and require table recreation to update.

-- v66: TSDB tiered provider model
-- Add tsdb_tier column to leagues table for free/premium classification
-- Free tier: 5 events/day/league, 30 req/min (key "123")
-- Premium tier: full event coverage, 100 req/min (>3 digit key)
-- (Retired at v92: TSDB became premium-key only, #676.)

-- v67: Remove Cricbuzz provider
-- Cricket leagues now use TSDB exclusively (no Cricbuzz fallback)
-- Clear fallback_provider, fallback_league_id, series_slug_pattern for cricket leagues
