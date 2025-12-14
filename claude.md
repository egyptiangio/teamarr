# TeamArr Project Context

> **NOTE**: This file (`claude.md`) is LOCAL ONLY and should NOT be committed to the repository. It contains development notes and context for Claude Code sessions. Changes to this file should never trigger git commit prompts.

## Overview
TeamArr is an EPG (Electronic Program Guide) generator for sports teams. It creates XMLTV files for Plex/Jellyfin that show team schedules, pregame/postgame content, and idle-day programming.

## Project Setup

### Environment
- **Deployment**: Docker container running on remote server
- **Local Project Path**: `/mnt/docker/stacks/teamarr`
- **Container Name**: `teamarr`
- **Development Path**: `/srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr`

### Git Branches
- **main**: Production-ready releases (tagged with version numbers)
- **dev**: Development branch - all work happens here first
  - Auto-versioning: Shows as `X.Y.Z-dev+{commit-sha}` (e.g., `1.0.2-dev+27a59b5`)
  - Docker rebuilds automatically from this branch

### Version Management
- **Current Version**: Defined in `config.py` as `BASE_VERSION = "1.0.2"`
- **Auto-versioning**: `config.py` automatically appends branch/commit info:
  - `dev` branch: `1.0.2-dev+27a59b5`
  - `main` branch: `1.0.2` (clean version)
  - Other branches: `1.0.2-{branchname}`

## Key Architecture

### Main Components
- **app.py**: Flask web interface and API endpoints
- **epg/orchestrator.py**: Core EPG generation logic
- **api/espn_client.py**: ESPN API wrapper for fetching team data
- **templates/**: Jinja2 templates for EPG metadata
- **db.py**: SQLite database for team configuration

### Data Flow
1. Teams are configured via web UI (stored in SQLite)
2. Scheduler triggers EPG generation hourly/daily
3. Orchestrator fetches schedule from ESPN API
4. Events are processed and enriched with scoreboard data
5. Filler entries (pregame/postgame/idle) are generated
6. XMLTV file is written to /config/teamarr.xml

## Important Patterns

### Scoreboard Enrichment
- Today's games enriched via `_enrich_with_scoreboard()`
- Past events (last 7 days) enriched via `_enrich_past_events_with_scores()`
- Last game context enriched on-demand via `_enrich_last_game_with_score()`

### API Tracking
- Track API calls using `self.api_calls` counter
- Pass `api_calls_counter` dict for nested operations
- Sync counter back after nested calls complete

### Variable Scope
- `api_sport`, `api_league` defined in `_process_team_schedule()`
- Must be passed explicitly to nested functions like `_generate_filler_entries()`
- Handle None values defensively (teams may have incomplete config)

## Common Issues

### NameError in EPG Generation
**Symptom**: EPG generates 0 programs, logs show `NameError: name 'api_sport' is not defined`
**Cause**: Variables not in scope within nested functions
**Fix**: Pass `api_sport`, `api_league`, `api_calls_counter` as parameters

### Missing Scores in Context Variables
**Symptom**: Template variables like `.last.score` are empty
**Cause**: Past events not enriched with scoreboard data
**Fix**: Ensure `_enrich_last_game_with_score()` is called with proper parameters

### Duplicate EPG Entries
**Symptom**: Same game appears multiple times
**Cause**: Midnight crossover logic not accounting for extended games
**Fix**: Check `skip_pregame` and `skip_idle` flags based on previous day's games

## Testing

### Docker Environment
- Container name: `teamarr`
- Config path: `/mnt/docker/stacks/teamarr`
- Logs: `docker logs teamarr --tail 100`
- Restart: `docker restart teamarr` (after pushing to dev)

### Manual EPG Generation
```bash
# Via web UI: Settings > Generate EPG Now
# Via API: POST /api/generate
# Check logs for errors and API call counts
```

## Development Workflow

### Daily Development (Workflow 1: Release Tags Only)

This is the recommended workflow for day-to-day development:

#### Working on Features/Fixes
1. **Make changes** in development directory
2. **Commit to dev branch** (no watermark in commits):
   ```bash
   git add <files>
   git commit -m "Description of changes"
   ```
3. **Push to GitHub**:
   ```bash
   git push origin dev
   ```
4. **Docker auto-rebuilds** from dev branch
5. **Monitor logs** for errors:
   ```bash
   docker logs teamarr --tail 100 -f
   ```
6. **Test EPG output** in Plex/Jellyfin

#### When Ready for Release
When you have a stable set of features ready to release:

1. **Update version** in `config.py`:
   ```python
   BASE_VERSION = "1.0.3"  # Increment from 1.0.2
   ```

2. **Commit version bump**:
   ```bash
   git add config.py
   git commit -m "Bump version to 1.0.3"
   git push origin dev
   ```

3. **Merge to main**:
   ```bash
   git checkout main
   git merge dev
   git push origin main
   ```

4. **Create release tag**:
   ```bash
   git tag -a v1.0.3 -m "Release v1.0.3 - Feature summary"
   git push origin --tags
   ```

5. **Return to dev branch**:
   ```bash
   git checkout dev
   ```

#### Key Principles
- ✅ **Commit freely to dev** - No need to tag every commit
- ✅ **Version shows commit hash** - Auto-versioning handles dev builds (`1.0.2-dev+27a59b5`)
- ✅ **Tag only releases** - When merging to main and ready for production
- ✅ **No watermarks** - Keep commits clean (no "Generated with Claude Code" footer)
- ✅ **Semantic versioning**:
  - MAJOR: Breaking changes
  - MINOR: New features (backward compatible)
  - PATCH: Bug fixes

---

### Local Development (dev-withevents branch)

**Status**: ACTIVE for Event Channel EPG feature development (Nov 2025)

For faster iteration during feature development, we run the app locally instead of pushing to Docker.

#### Setup (One-Time)
```bash
# 1. Stop Docker container
docker stop teamarr

# 2. Create local data directory (if not exists)
mkdir -p /srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr/data

# 3. Copy database from Docker stack (preserves your teams/settings)
cp /mnt/docker/stacks/teamarr/data/teamarr.db \
   /srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr/data/

# 4. Update EPG output path for local paths
sqlite3 data/teamarr.db "UPDATE settings SET epg_output_path = './data/teamarr.xml' WHERE id = 1;"
```

#### Running Locally
```bash
cd /srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr

# Start the app
python3 app.py

# App runs at http://localhost:9195
```

#### Path Differences
| Resource | Docker | Local Dev |
|----------|--------|-----------|
| Database | `/app/data/teamarr.db` | `./data/teamarr.db` |
| EPG Output | `/app/data/teamarr.xml` | `./data/teamarr.xml` |
| Logs | `/app/data/logs/` | `./data/logs/` (auto-created) |

#### Important Notes
- **Databases are separate**: Local and Docker have independent databases
- **Schema unchanged**: Only setting values differ (paths)
- **Git ignores data/**: `*.db` and `data/` are in `.gitignore`
- **No impact on Docker**: When you restart Docker, it uses its own database

#### Switching Back to Docker
```bash
# Stop local app (Ctrl+C)

# Update Docker database EPG path if needed (usually not necessary)
# sqlite3 /mnt/docker/stacks/teamarr/data/teamarr.db \
#   "UPDATE settings SET epg_output_path = '/app/data/teamarr.xml' WHERE id = 1;"

# Start Docker container
docker start teamarr
```

#### When to Sync Databases
If you make database changes locally that you want in Docker:
```bash
# Stop both
docker stop teamarr

# Copy local to Docker (overwrites Docker database!)
cp ./data/teamarr.db /mnt/docker/stacks/teamarr/data/

# Update paths back to Docker paths
sqlite3 /mnt/docker/stacks/teamarr/data/teamarr.db \
  "UPDATE settings SET epg_output_path = '/app/data/teamarr.xml' WHERE id = 1;"

# Restart Docker
docker start teamarr
```

## Important Notes

- Always pass required parameters explicitly (avoid relying on closure)
- Document new parameters in docstrings
- Handle None values defensively
- Track API calls to avoid rate limiting
- Test with multiple leagues (NFL, NBA, Soccer, etc.)

## Recent Enhancements

### Variable Selector with Suffix Intelligence (Nov 2025)
**Feature**: Smart variable insertion based on suffix availability

**Single-Context Variables** (immediate insertion):
- `.last` only (e.g., `result`, `score`) → Display as `result.last`, insert immediately
- `base` only (e.g., `team_name`, `league`) → Display as `team_name`, insert immediately

**Multi-Context Variables** (popup selector):
- `base+.next` (e.g., `odds_spread`) → Show popup with 2 options
- All three (e.g., `opponent`, `game_date`) → Show popup with 3 options
- Popup shows: variable name, available forms with context descriptions
- Theme-compliant styling with hover states

**Implementation**:
- `/api/variables` enriches each variable with `available_suffixes` array
- Frontend stores `window.variableSuffixMap` from API
- `showSuffixSelector()` creates positioned popup on click
- `selectSuffix()` inserts chosen form

### Interactive Issue Detection (Nov 2025)
**Feature**: Clickable EPG issues that highlight problems in XML preview

**Unresolved Variables**:
- Click variable → searches and highlights in XML
- Uses existing `searchXML()` functionality
- Yellow highlight on matches

**Coverage Gaps**:
- Click gap → highlights entire `<programme>` XML blocks on both sides
- Red highlight: Programme ending before gap
- Blue highlight: Programme starting after gap
- Gap display shows time range: "60 minutes gap (11:00 to 12:00)"
- Matches programmes by `start` and `stop` attributes for precision
- Scrolls to show highlighted blocks in center of viewport

**Implementation**:
- `app.py`: Gap analysis includes `after_stop` and `before_start` timestamps
- `highlightGap()`: Parses XML line-by-line, tracks programme blocks, applies color-coded highlights
- Issues box has `max-height: 80vh` with `overflow-y: auto` for many issues

## Next Steps / Planned Improvements

### 1. Multi-League Support for Teams
**Goal**: Allow teams that play in multiple leagues (e.g., European soccer clubs in domestic league + Champions League) to have games from all competitions shown in a single channel.

**Current Limitation**:
- Each team is configured with a single `api_path` (sport/league)
- Liverpool can show Premier League OR Champions League, not both
- Users must create separate channels for each competition

**Example Teams Affected**:
- Liverpool: Premier League (eng.1) + Champions League (uefa.champions)
- Real Madrid: La Liga (esp.1) + Champions League (uefa.champions)
- Barcelona: La Liga (esp.1) + Champions League (uefa.champions)
- European clubs also play domestic cups, super cups, etc.

**ESPN API Competition Catalog** (Confirmed Working):

*UEFA European Competitions*:
- `soccer/uefa.champions` - UEFA Champions League (top tier)
- `soccer/uefa.europa` - UEFA Europa League (second tier)
- `soccer/uefa.europaconf` - UEFA Europa Conference League (third tier)

*English Domestic Cups*:
- `soccer/eng.1` - Premier League (domestic league)
- `soccer/eng.fa` - FA Cup
- `soccer/eng.league_cup` - Carabao Cup / EFL Cup

*Spanish Domestic Cups*:
- `soccer/esp.1` - La Liga (domestic league)
- `soccer/esp.copa_del_rey` - Copa del Rey

*French Domestic Cups*:
- `soccer/fra.1` - Ligue 1 (domestic league)
- `soccer/fra.coupe_de_france` - Coupe de France

*German Domestic Cups*:
- `soccer/ger.1` - Bundesliga (domestic league)
- `soccer/ger.dfb_pokal` - DFB-Pokal

*Italian Domestic Cups*:
- `soccer/ita.1` - Serie A (domestic league)
- `soccer/ita.coppa_italia` - Coppa Italia

*Global Competitions*:
- `soccer/fifa.cwc` - FIFA Club World Cup

**Real World Example** (Real Madrid, Current Season):
- ✓ La Liga: 13 games
- ✓ Champions League: 4 games
- ✓ FIFA Club World Cup: 6 games
- **Total: 23 games across 3 competitions**

**Scope Reality**:
A typical **top-tier European club** participates in:
1. Domestic League (38 games) - e.g., Premier League, La Liga
2. Domestic Cup (5-10 games) - e.g., FA Cup, Copa del Rey
3. Domestic League Cup (3-6 games) - England only (Carabao Cup)
4. UEFA Competition (6-13 games) - Champions League, Europa League, or Conference League
5. UEFA Super Cup (1 game) - If UEFA competition winners
6. FIFA Club World Cup (2-3 games) - If Champions League winners

**That's 5-6 different competitions in a single season**, requiring:
- Multiple API paths per team
- Schedule merging across 3-6 different endpoints
- Per-game league context (can't just store at team level)
- Competition-specific template conditionals
- UI to display all competitions clearly

**Proposed Solution**:
Option A - Multiple API Paths per Team:
- Change `api_path` from string to array: `["soccer/eng.1", "soccer/uefa.champions"]`
- Fetch schedule from each league and merge chronologically
- Deduplicate events by ID (same game shouldn't appear twice)
- Add league context to template variables (`.league_name`, `.competition`)

Option B - Smart League Detection:
- Query ESPN team endpoint to discover all competitions team plays in
- Automatically fetch schedules from all active leagues
- User can optionally filter which leagues to include

**Implementation Considerations**:
- API call multiplication (3-6x calls per team for top European clubs)
- Merging schedules with different date ranges
- Template variables need competition context (which league is this game?)
- Channel naming convention (still just "Liverpool" or "Liverpool Multi-League"?)
- Handling overlapping schedules (rare but possible)
- Performance impact: With 9 teams across 5 competitions each = 45 API calls per EPG generation

**Template Variables Needed**:
- `{competition_name}` - "Premier League", "UEFA Champions League"
- `{competition_abbrev}` - "EPL", "UCL"
- `{league_logo}` - Competition logo URL
- Variables for distinguishing domestic vs European games

**Additional Considerations for Implementation**:

1. **Team Import UI Enhancement**:
   - Create separate import tab/section for UEFA Champions League
   - May need similar tabs for other European competitions (Europa League, Conference League)
   - Users can import from multiple leagues independently
   - UI should clearly show which leagues each team is imported from

2. **Intelligent Team Merging**:
   - When team imported from EPL (e.g., Liverpool) also exists in Champions League
   - System should detect same team across leagues (match by ESPN team ID or team name)
   - Automatically merge into single team profile with multiple leagues
   - UI considerations:
     - Show all leagues team participates in
     - Allow enabling/disabling specific leagues per team
     - Visual indicator of multi-league teams
     - **Teams Table Display**: In the league column, show ALL leagues for multi-league teams
       - Display format: "Premier League, Champions League" or "EPL, UCL" (comma-separated)
       - Most pressing use case: Champions League teams showing both domestic + European competitions
       - Clear visual that this team has games from multiple sources
   - Database schema:
     - May need `team_leagues` junction table instead of single `api_path` field
     - Or store as JSON array in existing field
     - Need to track which leagues are active for each team

3. **Per-Game League Context**:
   - **CRITICAL**: League info must be available on a per-game basis, not just per-team
   - Template variables need game-specific league context:
     - `{game.league_name}` - Which league is THIS game in?
     - `{next_game.league_name}` - Which league is the next game?
     - `{last_game.league_name}` - Which league was the last game?
   - This is essential for European soccer but overkill for US sports (teams only in one league)
   - Implementation:
     - Event objects must include league metadata
     - Next/last game context must preserve league info
     - Filler templates need access to per-game league data

4. **League-Based Conditionals**:
   - Template system needs new conditional operators:
     - `is_champions_league` - Is this a Champions League match?
     - `is_domestic_league` - Is this a domestic league match?
     - `is_european_competition` - Is this any European competition?
     - `league_equals:<league_code>` - Match specific league
   - Use cases:
     - Different pregame templates for Champions League vs domestic games
     - Different titles/subtitles based on competition importance
     - Different artwork for European nights
   - Examples:
     ```
     Condition: is_champions_league
     Template: "UEFA Champions League: {our_team_name} vs {opponent_name}"

     Condition: is_domestic_league
     Template: "{our_team_name} Football - {league_name}"
     ```

5. **Schedule Merging Logic**:
   - Fetch schedules from all leagues team participates in
   - Merge chronologically by game date/time
   - Handle edge cases:
     - Same team playing multiple games in one day (rare, but possible with rescheduling)
     - Overlapping pregame/postgame windows
     - Filler generation when games are close together
   - Preserve league context through entire flow:
     - Schedule fetch → Event parsing → Enrichment → Template resolution → XMLTV output

6. **API Call Optimization**:
   - Multi-league teams will require 3-6x API calls (domestic + UEFA + cups)
   - Real-world example: 9 European teams × 5 competitions = 45 schedule API calls per EPG generation
   - Plus enrichment calls for scoreboard data (odds, scores)
   - Need aggressive caching strategy (cache schedules for at least 1 hour)
   - Consider batching calls where possible
   - Track API usage per league to avoid rate limiting
   - May need rate limiting protection / exponential backoff

7. **User Experience**:
   - Clear visual distinction in UI which games are from which league
   - EPG output should show competition name in program metadata
   - Channel guide should clearly indicate league for each game
   - Settings to control league priority/ordering

**Scope Note**: This is primarily needed for European soccer where teams compete in multiple leagues simultaneously. US sports (NFL, NBA, MLB, NHL) have single-league team memberships, so this adds complexity for a specific use case. Implementation should be designed to not complicate the simple single-league scenario.

### 2. Dispatcharr Event Channel EPG (M3U Group Enrichment)
**Goal**: Generate rich EPG data for event-based channels in Dispatcharr by matching channel names to ESPN events.

**Problem Statement**:
- IPTV providers create event-based channels (e.g., "Giants at Patriots") that show specific games
- These channels have no EPG data or only generic placeholders
- Channel names contain team matchups but lack dates/times
- Channels appear/disappear based on game schedules
- Need to match these dynamic channels to ESPN events for proper EPG

---

#### API Discovery (Nov 26, 2025)

**Dispatcharr API Endpoints Needed**:

| Endpoint | Purpose | Key Fields |
|----------|---------|------------|
| `GET /api/m3u/accounts/` | List M3U accounts | `id`, `name`, `channel_groups[]` |
| `GET /api/channels/groups/` | List all channel groups | `id`, `name`, `channel_count`, `m3u_accounts[]` |
| `GET /api/channels/streams/` | List streams (filterable) | `id`, `name`, `channel_group`, `m3u_account`, `tvg_id`, `url` |
| `GET /api/channels/streams/groups/` | List group names (strings) | Returns array of group name strings |
| `POST /api/m3u/refresh/{account_id}/` | Trigger M3U refresh | Forces fresh pull from provider |

**Stream Object Schema**:
```json
{
  "id": 1080712,
  "name": "NFL  | 16 -8:15PM Giants at Patriots",
  "url": "https://provider.com/live/user/pass/12345.ts",
  "m3u_account": 4,
  "channel_group": 951,
  "tvg_id": "NFL16.us",
  "logo_url": "https://...",
  "current_viewers": 0,
  "last_seen": "2025-11-26T14:19:23Z"
}
```

**Channel Group Schema**:
```json
{
  "id": 951,
  "name": "USA | NFL Backup 🏈",
  "channel_count": 0,
  "m3u_account_count": 1,
  "m3u_accounts": [
    {
      "channel_group": 951,
      "enabled": true,
      "custom_properties": {"xc_id": "115"}
    }
  ]
}
```

**Key Discovery**: `channel_count` in groups is often 0 even when streams exist. Streams are the raw M3U entries; channels are what Dispatcharr creates from them. Query streams directly, not channel counts.

---

#### Discovered Event Channel Patterns

**Group 951 (USA | NFL Backup 🏈)** - 30 event-style streams:
```
NFL  | 16 -8:15PM Giants at Patriots
NFL  | 15 - 8:20PM Broncos at Commanders
NFL  | 05 - 1PM Vikings at Packers
NFL  | 01 - 1PM Packers at Lions
```

**Pattern**: `NFL  | {channel_num} -{time} {away_team} at {home_team}`

**Group 921 (NFL Game Pass 🏈)** - 18 event-style streams:
```
NFL Game Pass 03: Minnesota Vikings  vs  Green Bay Packers @ 08:20
NFL Game Pass 12: Philadelphia Eagles  vs  Dallas Cowboys @ 08:00
```

**Pattern**: `NFL Game Pass {num}: {away_team}  vs  {home_team} @ {time}`

**Group 377 (UK Sky+)** - 12 event-style streams:
```
(UK) (Sky+ 11) | NFL: Buffalo Bills @ Pittsburgh Steelers (2025-11-30)
(UK) (Sky+ 10) | NFL: Las Vegas Raiders @ Los Angeles Chargers (2025-11-30)
```

**Pattern**: `(UK) (Sky+ {num}) | NFL: {away_team} @ {home_team} ({date})`

---

#### Proof of Concept: ESPN Event Matching

**Input**: `NFL  | 16 -8:15PM Giants at Patriots`

**Parsing**:
```python
# Regex: Skip time, capture teams
match = re.search(r'(?:AM|PM)\s+(\w+)\s+(?:at|vs\.?|@)\s+(\w+)', channel_name)
# Result: away="Giants", home="Patriots"
```

**ESPN API Flow**:
```python
# 1. Map team names to ESPN IDs
NFL_TEAMS = {'giants': '19', 'patriots': '17', ...}
home_id = NFL_TEAMS['patriots']  # 17

# 2. Get home team schedule
GET https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/17/schedule

# 3. Find game with both teams
for event in schedule['events']:
    if 'giants' in event competitors:
        return event['id']  # 401772821

# 4. Fetch full event data
GET https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=401772821
```

**Output**:
```
Event ID:    401772821
Event Name:  New York Giants at New England Patriots
Date:        2025-12-02T01:15Z (Week 13)
Venue:       Gillette Stadium, Foxborough
Broadcast:   ESPN
Weather:     27°F
Odds:        NE -7.5, O/U 46.5
Records:     NYG 2-10 vs NE 10-2
```

---

#### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TEAMARR UI                                         │
│                                                                              │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐           │
│  │ M3U Accounts    │ → │ Channel Groups  │ → │ Stream Preview  │           │
│  │ (from Dispat.)  │   │ (filter by M3U) │   │ (in group)      │           │
│  └─────────────────┘   └─────────────────┘   └─────────────────┘           │
│           │                     │                     │                     │
│           ▼                     ▼                     ▼                     │
│  [Select Account]      [Filter by Name]      [Preview Streams]              │
│  - IPTV - Infinity     - "NFL"               - NFL | 01 - Packers...       │
│  - IPTV - MSX          - "NBA"               - NFL | 02 - Bears...         │
│  - IPTV - FlareTV      - "NHL"               - NFL | 03 - Cowboys...       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    ASSIGN LEAGUE TO GROUP                            │   │
│  │  Group: "USA | NFL Backup 🏈" (30 streams)                          │   │
│  │  Assigned League: [NFL ▼]                                            │   │
│  │  Refresh Interval: [Hourly ▼]                                        │   │
│  │  [Enable] [Save]                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        HOURLY BACKGROUND JOB                                 │
│                                                                              │
│  1. POST /api/m3u/refresh/{account_id}/     ← Force Dispatcharr refresh     │
│  2. GET /api/channels/streams/?search=...   ← Get streams in enabled groups │
│  3. Parse channel names → Extract teams                                      │
│  4. Match teams → ESPN schedule API → Get event IDs                         │
│  5. Fetch event summaries → Full game data                                  │
│  6. Generate XMLTV using existing TemplateEngine                            │
│  7. Serve EPG at /event-epg/{group_id}.xml                                  │
│                                                                              │
│  Dispatcharr EPG Source → Points to Teamarr's /event-epg/{group_id}.xml    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

#### Database Schema (Final - Simplified)

**Design Decisions**:
- No `event_epg_streams` table - streams are ephemeral (change daily), fetch fresh each EPG generation
- No event_id caching - always do fresh matching to get latest ESPN data (odds change, games reschedule)
- User aliases only - no built-in aliases shipped, users discover what they need

```sql
-- Enabled M3U groups for event EPG generation
CREATE TABLE event_epg_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Dispatcharr Integration
    dispatcharr_group_id INTEGER NOT NULL UNIQUE,  -- Group ID from Dispatcharr
    dispatcharr_account_id INTEGER NOT NULL,       -- M3U Account ID (for refresh + UI)
    group_name TEXT NOT NULL,                      -- Exact group name (e.g., "USA | NFL Backup 🏈")

    -- League/Sport Assignment
    assigned_league TEXT NOT NULL,                 -- League code (e.g., "nfl", "epl", "nba")
    assigned_sport TEXT NOT NULL,                  -- Sport type (e.g., "football", "soccer")

    -- Status
    enabled INTEGER DEFAULT 1,                     -- Is this group enabled for EPG generation?
    refresh_interval_minutes INTEGER DEFAULT 60,   -- How often to regenerate EPG

    -- Stats (updated after each generation)
    last_refresh TIMESTAMP,                        -- Last time EPG was generated
    stream_count INTEGER DEFAULT 0,                -- Number of streams in group
    matched_count INTEGER DEFAULT 0                -- Number of streams matched to ESPN events
);

-- User-defined team name aliases
CREATE TABLE team_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Alias Definition
    alias TEXT NOT NULL,                           -- Alias string (lowercase, normalized) e.g., "spurs"
    league TEXT NOT NULL,                          -- League code (e.g., "epl", "nfl")

    -- ESPN Team Mapping
    espn_team_id TEXT NOT NULL,                    -- ESPN's team ID
    espn_team_name TEXT NOT NULL,                  -- ESPN's team name (e.g., "Tottenham Hotspur")

    UNIQUE(alias, league)
);
```

**EPG Generation Flow** (no caching):
```
1. For each enabled group:
   a. wait_for_refresh(account_id) - ensure fresh M3U data
   b. list_streams(group_name) - get current streams from Dispatcharr
   c. For each stream:
      - Parse team names from stream.name
      - Look up user aliases if needed
      - Match to ESPN schedule → get event_id
      - Fetch ESPN event summary (fresh data with odds, weather, etc.)
   d. Generate XMLTV
   e. Update group stats (stream_count, matched_count, last_refresh)
```

---

#### API Endpoints (Teamarr)

**Group Management**:
```
GET  /api/event-epg/dispatcharr/accounts
     → List M3U accounts from Dispatcharr

GET  /api/event-epg/dispatcharr/groups?account_id={id}&search={name}
     → List groups, filterable by M3U account and name search

GET  /api/event-epg/dispatcharr/groups/{group_id}/streams?limit=50
     → Preview streams in a group (for UI preview)

POST /api/event-epg/groups
     → Enable a group for event EPG generation
     Body: {dispatcharr_group_id, dispatcharr_account_id, assigned_league, assigned_sport}

GET  /api/event-epg/groups
     → List enabled groups

DELETE /api/event-epg/groups/{id}
     → Disable/remove a group
```

**EPG Generation**:
```
POST /api/event-epg/refresh/{group_id}
     → Manually trigger refresh + EPG generation for a group

GET  /api/event-epg/groups/{group_id}/streams
     → List streams with their ESPN match status

GET  /event-epg/{group_id}.xml
     → Serve generated XMLTV for Dispatcharr to consume
```

---

#### Channel Name Parser (Simplified)

**Core Insight**: We only need to extract team names. ESPN provides everything else (date, time, venue, odds, broadcast).

**Discovered Patterns** (Nov 26, 2025):
```
Provider 4 (Infinity):
  NFL Game Pass 05: New York Giants  vs  Detroit Lions @ 01:00 PM ET
  NFL  | 16 -8:15PM Giants at Patriots
  NBA 07: Utah Jazz vs Los Angeles Lakers @ 08:00 PM ET
  NCAAB 52: Maryland vs 8 Alabama @ Nov 26 11:59 PM ET

Provider 3 (FlareTV):
  11/23 - 1:00 PM EST | New England Patriots at Cincinnati Bengals
  (UK) (Dazn 070) | Patriots @ Bengals (2025-11-23 18:00:05)
  (UK) (Sky+ 08) | NFL: Philadelphia Eagles @ Dallas Cowboys (2025-11-23)
  US (Peacock 023) | Clippers vs. Lakers (2025-11-25 23:00:05)
  CA (TSN+ 050) | NBA on TSN+: Pistons vs. Celtics (2025-11-26 17:00)

Provider 2 (MSX):
  New York Giants
  Los Angeles Lakers
  Pittsburgh Penguins
```

**Algorithm** (hardcoded, no user regex needed):
```python
class TeamMatcher:
    """Extract team names from channel names using team database."""

    # Separators that indicate team vs team
    SEPARATORS = [' vs ', ' vs. ', ' at ', ' @ ', ' v ', ' v. ']

    def extract_teams(self, channel_name: str, league: str) -> dict:
        """
        Extract team names from any channel name format.

        Steps:
        1. Normalize: lowercase, strip unicode decorators
        2. Find separator (vs/at/@)
        3. Split into left/right parts
        4. Match each part against team database
        5. Return team IDs or None if no match

        Returns:
            {
                'away_team_id': '19',
                'home_team_id': '17',
                'away_team_name': 'New York Giants',
                'home_team_name': 'New England Patriots',
                'matched': True,
                'confidence': 1.0  # 1.0 = exact match, <1.0 = fuzzy
            }
        """
        normalized = self._normalize(channel_name)

        # Find separator
        sep_pos = -1
        used_sep = None
        for sep in self.SEPARATORS:
            pos = normalized.find(sep)
            if pos > 0:
                sep_pos = pos
                used_sep = sep
                break

        if sep_pos < 0:
            return {'matched': False, 'reason': 'No separator found'}

        left_part = normalized[:sep_pos].strip()
        right_part = normalized[sep_pos + len(used_sep):].strip()

        # Match teams
        away = self._find_team(left_part, league)
        home = self._find_team(right_part, league)

        if not away or not home:
            return {'matched': False, 'reason': f'Team not found: {left_part if not away else right_part}'}

        return {
            'away_team_id': away['id'],
            'home_team_id': home['id'],
            'away_team_name': away['name'],
            'home_team_name': home['name'],
            'matched': True,
            'confidence': min(away.get('confidence', 1.0), home.get('confidence', 1.0))
        }

    def _normalize(self, text: str) -> str:
        """Remove noise from channel name."""
        import re
        text = text.lower()
        # Remove common prefixes
        text = re.sub(r'^(nfl|nba|nhl|mlb|ncaa[fb]?|soccer)\s*', '', text)
        # Remove channel numbers and times
        text = re.sub(r'\d{1,2}:\d{2}\s*(am|pm|et|est|pt|pst)?', '', text, flags=re.I)
        text = re.sub(r'\d{1,2}/\d{1,2}(/\d{2,4})?', '', text)
        # Remove parenthetical info
        text = re.sub(r'\([^)]+\)', '', text)
        # Remove special chars but keep spaces
        text = re.sub(r'[|:\-#]+', ' ', text)
        # Remove rankings (e.g., "#8 Alabama", "8 Alabama")
        text = re.sub(r'#?\d+\s+(?=[a-z])', '', text)
        return ' '.join(text.split())

    def _find_team(self, text: str, league: str) -> dict:
        """Match text to a team in the database."""
        teams = TEAM_DATABASE.get(league, {})

        # Try exact matches first
        for key, team in teams.items():
            if key in text or team['name'].lower() in text:
                return {**team, 'confidence': 1.0}

            # Check abbreviation
            if team.get('abbrev', '').lower() in text.split():
                return {**team, 'confidence': 1.0}

            # Check city
            if team.get('city', '').lower() in text:
                return {**team, 'confidence': 0.9}

        # No match
        return None
```

**Team Database Structure**:
```python
TEAM_DATABASE = {
    'nfl': {
        'giants': {'id': '19', 'name': 'New York Giants', 'abbrev': 'NYG', 'city': 'New York'},
        'patriots': {'id': '17', 'name': 'New England Patriots', 'abbrev': 'NE', 'city': 'Boston'},
        'packers': {'id': '9', 'name': 'Green Bay Packers', 'abbrev': 'GB', 'city': 'Green Bay'},
        'lions': {'id': '8', 'name': 'Detroit Lions', 'abbrev': 'DET', 'city': 'Detroit'},
        # ... all 32 NFL teams
    },
    'nba': {
        'lakers': {'id': '13', 'name': 'Los Angeles Lakers', 'abbrev': 'LAL', 'city': 'Los Angeles'},
        'celtics': {'id': '2', 'name': 'Boston Celtics', 'abbrev': 'BOS', 'city': 'Boston'},
        'pistons': {'id': '8', 'name': 'Detroit Pistons', 'abbrev': 'DET', 'city': 'Detroit'},
        # ... all 30 NBA teams
    },
    'nhl': {
        'penguins': {'id': '5', 'name': 'Pittsburgh Penguins', 'abbrev': 'PIT', 'city': 'Pittsburgh'},
        'redwings': {'id': '17', 'name': 'Detroit Red Wings', 'abbrev': 'DET', 'city': 'Detroit'},
        # ... all 32 NHL teams
    },
    # ... MLB, MLS, etc.
}
```

**Matching Flow**:
```
"NFL Game Pass 05: New York Giants  vs  Detroit Lions @ 01:00 PM ET"
                    ↓ normalize()
"new york giants vs detroit lions"
                    ↓ find separator
"new york giants" [vs] "detroit lions"
                    ↓ find_team()
ESPN IDs: 19, 8
                    ↓ ESPN API
Event + all metadata (date, time, odds, venue, broadcast)
```

---

#### EPG-to-Stream Matching (Precise via Stream ID)

**Discovery** (Nov 26, 2025): Dispatcharr links channels to EPG via `epg_data_id`, not just tvg_id matching.

**Key Fields**:
- **Channel**: `epg_data_id` (links to EPG Data entry)
- **EPG Data**: `tvg_id` (used for matching), `epg_source` (which source it came from)
- **EPG Program**: `tvg_id` (links program to channel)

**Approach for Precise Stream Matching**:
```
1. Generate XMLTV with channel ID = stream_id
   <channel id="stream_1084369">
     <display-name>New York Giants at Detroit Lions</display-name>
   </channel>

2. Configure Dispatcharr EPG source pointing to our endpoint
   URL: http://teamarr:9195/event-epg/{group_id}.xml

3. After import, Dispatcharr creates EPG Data with tvg_id="stream_1084369"

4. PATCH channel's tvg_id to match:
   PATCH /api/channels/channels/{ch_id}/ {"tvg_id": "stream_1084369"}

5. Dispatcharr auto-links channel → EPG Data → Programs
```

**Alternative: Direct Assignment**:
```
1. After XMLTV import, lookup EPG Data ID by tvg_id
2. PATCH channel's epg_data_id directly
   PATCH /api/channels/channels/{ch_id}/ {"epg_data_id": 136789}
```

---

#### ESPN Event Matcher

```python
def find_event_by_teams(espn_client, sport: str, league: str,
                        away_team_id: str, home_team_id: str) -> dict:
    """
    Find event between two teams using ESPN schedule API.

    Algorithm:
    1. Fetch home team's schedule
    2. Search for game where opponent = away team
    3. Return event ID for full data fetch

    Returns:
        {'event_id': '401772821', 'found': True}
        or {'found': False, 'reason': 'No matching event'}
    """
    # Get home team schedule
    schedule = espn_client.get_team_schedule(sport, league, home_team_id)

    for event in schedule.get('events', []):
        for competitor in event.get('competitions', [{}])[0].get('competitors', []):
            if competitor.get('team', {}).get('id') == away_team_id:
                return {
                    'event_id': event['id'],
                    'event_name': event.get('name'),
                    'event_date': event.get('date'),
                    'found': True
                }

    return {'found': False, 'reason': 'No matching event in schedule'}
```

**Once we have event_id, ESPN provides everything**:
- Teams (names, abbreviations, records, logos)
- Venue, weather, broadcast
- Odds, over/under, moneyline
- Game status, score (if in progress)

No parsing needed from channel name - ESPN is authoritative.

---

#### UI Wireframe

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Event-Based EPG                                                    [+ Add]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─── Filter ──────────────────────────────────────────────────────────┐   │
│  │ M3U Account: [All Accounts        ▼]                                │   │
│  │ Search:      [NFL________________] [🔍]                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─── Available Groups ────────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  ☐ USA | NFL Backup 🏈          Infinity    30 streams   [Preview]  │   │
│  │  ☐ NFL Game Pass 🏈             Infinity    18 streams   [Preview]  │   │
│  │  ☐ Sports | NFL (L)             MSX         36 streams   [Preview]  │   │
│  │  ☐ Sports | NFL                 MSX         37 streams   [Preview]  │   │
│  │  ☐ UK | Sky Sports NFL          FlareTV     12 streams   [Preview]  │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─── Enabled Groups ──────────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  Group                    League   Streams   Matched   Last Refresh │   │
│  │  ─────────────────────────────────────────────────────────────────  │   │
│  │  USA | NFL Backup 🏈      NFL      30        28 (93%)  5 mins ago   │   │
│  │  Sports | NBA (L)         NBA      24        22 (92%)  12 mins ago  │   │
│  │                                                                      │   │
│  │  [Refresh All] [Generate EPG]                                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─── Stream Preview Modal ───────────────────────────────────────────────────┐
│ USA | NFL Backup 🏈 (30 streams)                                      [X]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Stream Name                              Parsed Teams        ESPN Match   │
│  ─────────────────────────────────────────────────────────────────────────  │
│  NFL | 01 - 1PM Packers at Lions         GB @ DET            ✓ 401772819  │
│  NFL | 02 - 1PM Bears at Vikings         CHI @ MIN           ✓ 401772820  │
│  NFL | 03 - 4PM Cowboys at Eagles        DAL @ PHI           ✓ 401772821  │
│  NFL | 04 - 4PM Raiders at Chargers      LV @ LAC            ✓ 401772822  │
│  NFL | 05 - 8PM Giants at Patriots       NYG @ NE            ✓ 401772823  │
│  NFL | 16 - TBD                          —                   ✗ No match   │
│                                                                             │
│                                                [Close] [Enable This Group] │
└─────────────────────────────────────────────────────────────────────────────┘

┌─── Stream Preview with Unmatched Teams (EPL example) ──────────────────────┐
│ EPL Live (12 streams)                                                 [X]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Stream Name                         Parsed Teams         ESPN Match       │
│  ─────────────────────────────────────────────────────────────────────────  │
│  EPL: Liverpool vs Man City          Liverpool @ Man City  ✓ 401234567    │
│  EPL: Spurs vs Arsenal               ??? @ Arsenal         ⚠ [Fix Match]  │
│  EPL: Notts Forest vs Chelsea        ??? @ Chelsea         ⚠ [Fix Match]  │
│  EPL: Wolves vs Everton              Wolves @ Everton      ✓ 401234569    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─── Fix Match Modal ────────────────────────────────────────────────────────┐
│ Unrecognized Team: "Spurs"                                            [X]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  The team "Spurs" was not found in the EPL database.                       │
│                                                                             │
│  Select correct team:                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Tottenham Hotspur                                                 ▼ │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ☑ Save "spurs" as alias for future matches                                │
│                                                                             │
│                                              [Cancel] [Save & Re-match]    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

#### User-Defined Team Aliases

**Problem**: IPTV providers use inconsistent team names (e.g., "Spurs" for Tottenham, "Notts Forest" for Nottingham Forest, "Man U" for Manchester United).

**Solution**: Allow users to create aliases through the UI when unmatched teams are discovered.

**Design Decision**: User-provided aliases only. No built-in aliases shipped with the app. Users discover what aliases they need through the matching preview UI.

**Two Ways to Create Aliases**:
1. **Dedicated UI**: Alias management page (CRUD for alias→team mappings)
2. **On-the-fly**: When enabling a group, preview shows unmatched teams with [Fix] button

**Database Schema**: See "Database Schema (Final - Simplified)" section above.

**Matching Priority**:
```python
def _find_team(self, text: str, league: str) -> dict:
    text_lower = text.lower().strip()

    # 1. Check user-defined aliases first (most specific)
    alias = db.query(
        "SELECT * FROM team_aliases WHERE alias = ? AND league = ?",
        text_lower, league
    )
    if alias:
        return {'id': alias['espn_team_id'], 'name': alias['espn_team_name'], 'confidence': 1.0}

    # 2. Check built-in team database (hardcoded team names, abbreviations, cities)
    team = self._check_builtin_database(text_lower, league)
    if team:
        return team

    # 3. No match - UI will prompt user to create alias
    return None
```

**API Endpoints for Aliases**:
```
GET  /api/event-epg/aliases?league=epl
     → List all aliases for a league

POST /api/event-epg/aliases
     → Create new alias
     Body: {"alias": "spurs", "league": "epl", "espn_team_id": "367", "espn_team_name": "Tottenham Hotspur"}

DELETE /api/event-epg/aliases/{id}
     → Remove an alias

GET  /api/event-epg/teams?league=epl&search=tot
     → Search teams for alias creation dropdown
```

**Benefits**:
- Self-healing: Once added, alias works forever
- Per-league scoping: "Spurs" in EPL ≠ "Spurs" in NBA (San Antonio)
- No code deploys needed for new aliases
- User discovers edge cases we didn't anticipate
- Can export/import aliases between instances

---

#### Branch Strategy

**Feature Branch**: `dev-withevents`
- Created from: `dev`
- Purpose: Develop Event Channel EPG feature in isolation
- Versioning: Same as `dev` branch (e.g., `1.1.0-dev-withevents+abc1234`)
- Merge path: `dev-withevents` → `dev` → `main`

**Workflow**:
```
main (production releases)
  ↑
  │ merge when stable
  │
dev (integration branch)
  ↑
  │ merge when feature complete
  │
dev-withevents (feature branch) ← YOU ARE HERE
```

**Commands**:
```bash
# Create feature branch
git checkout dev
git checkout -b dev-withevents
git push -u origin dev-withevents

# Daily work
git add . && git commit -m "..." && git push

# When feature is ready
git checkout dev
git merge dev-withevents
git push origin dev

# When ready for production
git checkout main
git merge dev
git tag -a v1.2.0 -m "Event Channel EPG feature"
git push origin main --tags
```

---

#### Quick Reference

**Dispatcharr Connection** (stored in `settings` table):
- URL: `http://192.168.0.10:9191`
- Credentials: In database (`dispatcharr_username`, `dispatcharr_password`)
- Auth endpoint: `POST /api/accounts/token/` → returns `{access: "jwt..."}`

**M3U Accounts in Dispatcharr**:
| ID | Name |
|----|------|
| 2 | IPTV - MSX |
| 3 | IPTV - FlareTV Onyx |
| 4 | IPTV - Infinity |

**Key Files**:
- `api/dispatcharr_client.py` - Dispatcharr client with `DispatcharrAuth` (JIT auth) + `M3UManager` (Phase 1)
- `api/espn_client.py` - ESPN API wrapper (dynamic conference fetching)
- `epg/orchestrator.py` - EPG generation logic (legacy team-based)
- `epg/template_engine.py` - Variable resolution
- `epg/team_matcher.py` - **NEW** Stream name → ESPN team ID matching (Phase 3)
- `epg/event_matcher.py` - **NEW** Team IDs → ESPN event lookup (Phase 4)
- `database/__init__.py` - DB connection, migrations, alias/group CRUD
- `database/schema.sql` - Full schema including `event_epg_groups`, `team_aliases`, `league_config`

---

#### Implementation Phases

**Phase 1: Dispatcharr Integration Module** ✅ COMPLETED (Nov 26, 2025)
- [x] Created `M3UManager` class in `api/dispatcharr_client.py`:
  - [x] `list_m3u_accounts()` - Get all M3U accounts
  - [x] `list_channel_groups(search=None)` - List groups with optional name filter
  - [x] `list_streams(group_name=None, group_id=None, account_id=None)` - Get streams
  - [x] `get_group_with_streams(group_id)` - Preview helper for UI
  - [x] `refresh_m3u_account(account_id)` - Trigger async refresh
  - [x] `wait_for_refresh(account_id, timeout)` - Trigger and wait for completion
  - [x] `test_connection()` - Verify connectivity

**Key API Findings (Phase 1)**:
- `channel_group_name` filter works (exact match including emoji), NOT `channel_group` by ID
- Streams API returns paginated data: `{count, next, previous, results}`
- Use `page_size=1000` for larger fetches
- Refresh status: poll `updated_at` field until it changes, check `status` field
- Status values: `parsing` (in progress), `success`, `error`

**Phase 2: Database Schema** ✅ COMPLETED (Nov 26, 2025)
- [x] Created `event_epg_groups` table (schema.sql + migration)
- [x] Created `team_aliases` table (schema.sql + migration)
- [x] Added indexes for performance
- [x] **DECISION**: No `event_epg_streams` table - streams are ephemeral, fetch fresh each time

**Schema Design Decisions (Phase 2)**:
- Store `dispatcharr_account_id` for refresh operations + UI display
- No event_id caching - always do fresh matching each EPG generation
- No streams table - avoids database bloat (streams change daily)
- Aliases are user-provided only (no built-in aliases shipped)

**Phase 3: Team Matcher & Aliases** ✅ COMPLETED (Nov 26, 2025)
- [x] Created `epg/team_matcher.py` with `TeamMatcher` class
- [x] **DECISION**: Use dynamic ESPN team fetch via `get_league_teams()` instead of hardcoded database
  - Handles relegation/promotion automatically (European soccer)
  - Works with all leagues already integrated into Teamarr
  - 1-hour cache per league to minimize API calls
- [x] Implemented normalize stream name → find separator → match teams
- [x] User alias lookup from `team_aliases` table (checked before ESPN data)
- [x] Added alias CRUD functions to `database/__init__.py`
- [x] Added event EPG group CRUD functions to `database/__init__.py`

**Key Implementation Details (Phase 3)**:
- `TeamMatcher.extract_teams(stream_name, league)` - Main entry point
- Normalizes messy stream names (removes times, dates, channel prefixes, etc.)
- Separator detection: `vs`, `vs.`, `at`, `@`, `v`, `v.`
- Multi-name matching: full name, short name, abbreviation, slug, city
- Tested with NFL, NBA, NHL, EPL - all working including nicknames (Spurs, Wolves, Man City)

**Phase 4: ESPN Event Matcher** ✅ COMPLETED (Nov 26, 2025)
- [x] Created `epg/event_matcher.py` with `EventMatcher` class
- [x] Implemented schedule-based event lookup (given two team IDs, find ANY game between them)
- [x] Added event summary fetching with scoreboard enrichment (odds, venue, broadcast, weather)
- [x] Filters out completed/FINAL games (only returns upcoming or in-progress)
- [x] 30-day search window ahead (covers most scheduling scenarios)
- [x] Date extraction from stream names (`extract_date_from_text()`)
- [x] Time extraction for double-header disambiguation (`extract_time_from_text()`)

**Key Implementation Details (Phase 4)**:
- `EventMatcher.find_event(team1_id, team2_id, league, game_date, game_time)` - Finds game
- `EventMatcher.find_and_enrich()` - Combines find + scoreboard enrichment
- Matching priority: 1) exact date+time, 2) exact date, 3) next upcoming game
- Date formats: ISO (2025-11-30), US (11/30, 11/30/25), text (Nov 30)
- Time formats: 12-hour (8:15PM), 24-hour (20:15), with timezone (8:15 PM ET)
- Skips games with `status.completed=True` or `status.name` containing 'FINAL'
- Returns parsed event with: venue, teams, records, odds, broadcasts, weather, status

**Team Matching Algorithm** (improved):
- Priority 1: Exact match (text == search_name)
- Priority 2: Prefix match (text starts with search_name or vice versa)
- Priority 3: Whole word match (with `\b` word boundaries)
- Priority 4: Substring match (lower confidence)
- Fixes issues like "kansas" matching "Kansas" not "Central Arkansas"

**College League Support**:
- NCAAM/NCAAW/NCAAF use conference-based team fetching
- **Dynamic conference fetching** from ESPN standings API (replaces hardcoded IDs)
- Fetches all 31 D1 conferences → 364 teams for NCAAM (includes AAC with Memphis)
- Reuses existing `get_league_conferences()` and `get_conference_teams()` APIs
- Uses league_config DB table for league definitions (same as legacy team import)

**Double-Header Handling**:
- If multiple games on same date, uses `game_time` to pick closest match
- Falls back to first game if no time provided

**Test Results** (Nov 26, 2025):
- NCAAM Team Matching: 100% (5/5 streams - Duke, Memphis, UConn, Kansas, UCLA)
- NCAAF Team Matching: 100% (4/4 streams - Alabama, Ohio State, Texas, Notre Dame)
- Event Finding: Works when games scheduled (e.g., Ohio State vs Michigan Nov 29)
- Fixed: Connecticut/UConn now matches, Memphis Tigers (AAC) now fetched

**Phase 5: Backend API Endpoints** ✅ COMPLETED (Nov 26, 2025)
- [x] Dispatcharr proxy endpoints (accounts, groups, streams)
- [x] Event EPG group CRUD (`POST/GET/PATCH/DELETE /api/event-epg/groups`)
- [x] Team alias CRUD (`POST/GET/DELETE /api/event-epg/aliases`)
- [x] EPG generation trigger (`POST /api/event-epg/refresh/{group_id}`)
- [x] Team search endpoint (`GET /api/event-epg/teams?league=&search=`)
- [x] XML serving endpoint (`GET /event-epg/{group_id}.xml`)

**Phase 6: EPG Generation & Template System** ✅ COMPLETED (Nov 26, 2025)
- [x] Created `epg/event_epg_generator.py` with `EventEPGGenerator` class
- [x] Created `epg/event_template_engine.py` for event-specific variable resolution
- [x] Created `epg/epg_consolidator.py` for EPG file pipeline management
- [x] Template type system (team vs event templates with validation)
- [x] 11 new event-specific variables (home_team_score, event_result, winner/loser, etc.)
- [x] Variables.json updated with context tags (team-only, event-only, both)
- [x] Watermarking centralized in consolidator (removed from individual generators)
- [x] Settings-based duration (uses `game_duration_{sport}` from settings)
- [x] Categories only from template (no hardcoded defaults)

**EPG Pipeline Architecture**:
```
Team Generator  → teams.xml   ─┐
                               ├─→ teamarr.xml (with VERSION watermark)
Event Generator → events.xml  ─┘
```

**Database Changes (Phase 6)**:
- `templates.template_type` column (team/event, immutable after creation)
- `event_epg_groups.event_template_id` column (FK to templates)
- API validation prevents cross-assignment (team templates → teams only, event templates → event groups only)

**Phase 7: UI Implementation**
- [ ] Create `templates/event_epg.html`
- [ ] M3U account dropdown filter
- [ ] Group name search filter
- [ ] Stream preview modal with match status
- [ ] "Fix Match" modal for unmatched teams (alias creation)
- [ ] Enable/disable group management
- [ ] Refresh and status indicators

**Phase 8: Background Scheduler**
- [ ] Add hourly job for enabled groups
- [ ] Trigger Dispatcharr M3U refresh before processing
- [ ] Update stream matches
- [ ] Regenerate EPG files
- [ ] Log statistics and errors

---

#### Template Variables for Event Channels

Event channels can use most existing template variables since we fetch full ESPN event data. Key variables:

| Variable | Source | Example |
|----------|--------|---------|
| `{channel_name}` | Stream | `NFL \| 16 -8:15PM Giants at Patriots` |
| `{channel_tvg_id}` | Stream | `NFL16.us` |
| `{away_team}` | ESPN | `New York Giants` |
| `{home_team}` | ESPN | `New England Patriots` |
| `{away_abbrev}` | ESPN | `NYG` |
| `{home_abbrev}` | ESPN | `NE` |
| `{away_record}` | ESPN | `2-10` |
| `{home_record}` | ESPN | `10-2` |
| `{venue}` | ESPN | `Gillette Stadium` |
| `{broadcast}` | ESPN | `ESPN` |
| `{odds_spread}` | ESPN | `NE -7.5` |
| `{odds_over_under}` | ESPN | `46.5` |
| `{weather_temp}` | ESPN | `27°F` |
| `{game_date}` | ESPN | `Mon Dec 2` |
| `{game_time}` | ESPN | `8:15 PM ET` |

---

#### Configuration

```python
# Default settings (can be overridden in UI)
EVENT_EPG_CONFIG = {
    'default_refresh_interval_minutes': 60,
    'max_streams_per_group': 100,
    'match_confidence_threshold': 0.7,  # Minimum confidence to use match
    'cache_event_data_minutes': 30,     # How long to cache ESPN data
    'generate_epg_hours_ahead': 48,     # EPG coverage window
}
```

---

#### Scope Notes

- **Not a full M3U proxy**: We don't recreate M3U files, just generate EPG
- **Dispatcharr handles M3U**: Auth, refresh, stream management all in Dispatcharr
- **Teamarr generates EPG**: Uses Dispatcharr's stream data to create rich XMLTV
- **Event-centric, not team-centric**: Different model from existing team EPG
- **Shared infrastructure**: Uses existing ESPN client, template engine, scheduler

## Recent Changes (Nov 24, 2025)

### Broadcast Format Normalization Fix
**Issue**: NCAAM teams (Tennessee Volunteers) not generating events due to `AttributeError: 'str' object has no attribute 'get'`

**Root Cause**: ESPN API returns broadcasts in multiple formats:
1. String: `"ESPN"`, `"ABC"` (NCAAM, some other sports)
2. Scoreboard dict: `{"market": "national", "names": ["ESPN"]}`
3. Schedule dict: `{"market": {"type": "National"}, "media": {"shortName": "ESPN"}}`
4. Schedule dict (no market): `{"type": {...}, "media": {"shortName": "NBC"}}` (EPL, Serie A)

Code was only handling dict formats, causing crashes on string broadcasts.

**Solution**:
- Created `_normalize_broadcast()` helper in `epg/template_engine.py:788` as single source of truth
- Handles all 4 ESPN API broadcast formats defensively
- Updated all broadcast processing functions:
  - `_get_broadcast_simple()` - line 844
  - `_get_broadcast_network()` - line 930
  - `_get_broadcast_national_network()` - line 995
  - `_is_national_broadcast()` - line 1029
  - `is_national_broadcast` condition - line 1230
- Refactored orchestrator's `_normalize_scoreboard_broadcasts()` to use template engine helper (line 265)

**Testing**:
- ✅ Tested against all major leagues: NFL, NBA, MLB, NHL, MLS, WNBA, NCAAM, NCAAF
- ✅ Tested against all European soccer leagues: EPL, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Liga MX, Champions League
- ✅ Confirmed all current broadcast formats handled correctly

**Commits**:
- `52683df` - Fix broadcast format handling across all ESPN API endpoints
- `af607ac` - Fix remaining broadcast normalization issues

**Status**: Pushed to dev branch, awaiting Docker image rebuild to verify NCAAM fix in production.

### EPG Start DateTime - Single Source of Truth (Nov 24, 2025)
**Issue**: NCAAM EPG was starting at midnight instead of including in-progress games within the 6-hour lookback period.

**Root Cause**: The `days_behind` parameter was calculated by converting hours to integer days:
```python
days_behind = int(time_diff.total_seconds() / 86400)  # 5 hours → 0 days
```
This lost precision - a 5-hour lookback became 0 days, filtering out all past events.

**Solution**: Implemented single source of truth `epg_start_datetime`:
1. **Calculation** (in `generate_epg()`):
   - If game found in last 6 hours → use exact game start time
   - If no game found → use last top of hour (round down)
   - Optional `start_datetime` parameter to override

2. **Usage** (passed directly everywhere):
   - `parse_schedule_events(cutoff_past_datetime=epg_start_datetime)` - filters events
   - `_generate_filler_entries(epg_start_datetime)` - starts filler on first day

3. **Filler alignment**:
   - First day: starts at `epg_start_datetime`
   - Subsequent days: starts at midnight (00:00)
   - Chunks align to 6-hour blocks (0000, 0600, 1200, 1800)

**Files changed**:
- `api/espn_client.py`: `parse_schedule_events()` now accepts `cutoff_past_datetime` instead of `days_behind`
- `epg/orchestrator.py`: Added `_round_to_last_hour()`, updated `generate_epg()`, `_process_team_schedule()`, `_generate_filler_entries()`

**Removed**:
- `days_behind` parameter and conversion logic
- Unused `_get_time_block_start()` helper

## TODOs

- [ ] Regenerate new, valid sample data for all vars in `variables.json` from actual API pulls, formatted exactly as it would be in production

### Dispatcharr Integration Documentation
**Created**: `docs/DISPATCHARR_API_INTEGRATION.md`
- Comprehensive guide for future Dispatcharr auto-refresh feature
- Includes JWT authentication, JIT token management, EPG management
- Full implementation checklist (7 phases, unchecked)
- Code examples ready to use when implementing feature
- Added `docs/` to `.gitignore` to keep integration notes local

---

## Bootstrap Prompt for Next Session

```
I'm continuing work on Teamarr - a sports EPG generator. Read claude.md for full context.

**Current Task**: Implementing "Dispatcharr Event Channel EPG" feature on branch `dev-withevents`

**Completed** (Phases 1-6):
- Phase 1: Dispatcharr Integration (M3UManager class in api/dispatcharr_client.py)
- Phase 2: Database Schema (event_epg_groups + team_aliases tables)
- Phase 3: Team Matcher (epg/team_matcher.py) - dynamic ESPN team fetch, alias support
- Phase 4: Event Matcher (epg/event_matcher.py) - finds ESPN events by team IDs
- Phase 5: Backend API Endpoints (16 routes in app.py)
- Phase 6: EPG Generation & Template System:
  - epg/event_epg_generator.py - generates XMLTV for event streams
  - epg/event_template_engine.py - resolves event-specific variables
  - epg/epg_consolidator.py - merges teams.xml + events.xml → teamarr.xml
  - Template type system (team vs event templates with API validation)
  - 11 new event variables in config/variables.json with context tags

**Key Architecture**:
- EPG Pipeline: teams.xml + events.xml → teamarr.xml (consolidator adds VERSION watermark)
- Template types are immutable after creation (team templates for teams, event templates for event groups)
- Event durations use settings-based `game_duration_{sport}` (same as team EPG)
- Categories only from template (no hardcoded defaults)
- Fallback title/channel name uses stream name from Dispatcharr

**Ready for Phase 7**: UI Implementation
- Create templates/event_epg.html
- M3U account dropdown, group search, stream preview modal
- "Fix Match" modal for creating team aliases
- Enable/disable group management

**Key Files**:
- app.py:1713-2328 - Event EPG API endpoints
- epg/event_epg_generator.py - EventEPGGenerator class
- epg/event_template_engine.py - EventTemplateEngine class (positional home/away vars)
- epg/epg_consolidator.py - after_team_epg_generation(), after_event_epg_generation()
- database/__init__.py - event_epg_groups, team_aliases CRUD + migrations

**To run locally**:
cd /srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr
python3 app.py
# App at http://localhost:9195

**To test event EPG generation**:
python3 -c "
from epg.event_epg_generator import generate_event_epg

test_event = {
    'date': '2025-01-15T20:00:00Z',
    'home_team': {'name': 'Patriots', 'abbrev': 'NE', 'score': 24},
    'away_team': {'name': 'Giants', 'abbrev': 'NYG', 'score': 17},
    'venue': {'name': 'Gillette Stadium'},
    'status': {'state': 'pre'}
}
test_stream = {'id': 1, 'name': 'NFL: Giants @ Patriots'}
test_group = {'id': 1, 'assigned_sport': 'football', 'assigned_league': 'nfl'}

result = generate_event_epg([{'stream': test_stream, 'event': test_event}], test_group, save=False)
print(result['xml_content'][:500])
"
```
