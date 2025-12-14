# Teamarr Roadmap & Planned Features

*Last updated: December 2024 (v1.4.0)*

---

## Table of Contents

1. [Planned Features](#planned-features)
   - [TheSportsDB Integration](#1-thesportsdb-integration-non-espn-leagues)
   - [{unique_team} Exception Keyword Token](#2-unique_team-exception-keyword-token)
2. [Technical Debt & Refactoring](#technical-debt--refactoring)
   - [Event Enrichment Consolidation](#event-enrichment-consolidation)
3. [Completed Features](#completed-features)
4. [Related Documentation](#related-documentation)

---

## Planned Features

### 1. TheSportsDB Integration (Non-ESPN Leagues)

**Goal:** Support leagues and sports not available in ESPN using TheSportsDB API.

**Status:** Planning

#### Sports & Leagues to Support

**Sports NOT in ESPN API (require TheSportsDB):**

| Sport | Example Leagues | TheSportsDB Coverage |
|-------|-----------------|----------------------|
| **Handball** | IHF World Championship, EHF Champions League, Bundesliga Handball | ✅ Full |
| **Rugby Union** | Six Nations, Rugby World Cup, Super Rugby, Premiership | ✅ Full |
| **Rugby League** | NRL, Super League, State of Origin | ✅ Full |
| **Cricket** | IPL, Test matches, ODI, T20 World Cup | ✅ Full |
| **Motorsport** | Formula 1, MotoGP, NASCAR, IndyCar, WRC | ✅ Full |
| **Combat Sports** | UFC, Boxing (EBU, WBC, IBF, WBA, WBO), Bellator, PFL | ✅ Full |
| **Australian Rules** | AFL | ✅ Full |
| **Cycling** | Tour de France, Giro d'Italia | ✅ Partial |
| **Darts** | PDC World Championship | ✅ Full |
| **Snooker** | World Championship, UK Championship | ✅ Full |
| **Beach Soccer** | FIFA Beach Soccer World Cup, Americas Winners Cup | ❌ Not available |

**Leagues in ESPN but with better TheSportsDB coverage:**

| League | ESPN Status | TheSportsDB Advantage |
|--------|-------------|----------------------|
| AHL (American Hockey League) | ❌ Not available | ✅ Full schedules, rosters |
| CHL (Canadian Hockey League) | ❌ Not available | ✅ OHL, WHL, QMJHL (see note below) |
| ECHL | ❌ Not available | ✅ Full coverage |
| KHL (Russia) | ❌ Not available | ✅ Full coverage |
| SHL (Sweden) | ❌ Not available | ✅ Full coverage |
| DEL (Germany) | ❌ Not available | ✅ Full coverage |
| Liiga (Finland) | ❌ Not available | ✅ Full coverage |
| European basketball | Limited | ✅ EuroLeague, national leagues |

#### Umbrella League Pattern: CHL Example

**CHL (Canadian Hockey League)** is a meta-league with no teams of its own:

```
CHL (umbrella - no regular season games)
├── OHL (Ontario Hockey League) - 20 teams
├── WHL (Western Hockey League) - 22 teams
└── QMJHL (Quebec Major Junior Hockey League) - 18 teams
```

**Challenge:** No provider streams "CHL" - they stream OHL/WHL/QMJHL individually. The only "CHL" event is the Memorial Cup (annual championship tournament, May/June).

**Proposed Solution - Composite League Group:**

1. **User configures 3 leagues:** OHL, WHL, QMJHL separately in TheSportsDB
2. **Optional "CHL" virtual group:** Combines all three leagues' streams
   - Similar to multi-sport event groups
   - League detection determines OHL vs WHL vs QMJHL per stream
   - `{league}` variable shows actual league (OHL), not umbrella (CHL)
3. **Memorial Cup handling:** During tournament, cross-league matchups appear
   - TheSportsDB may list as separate "Memorial Cup" league/event
   - Or games appear in each league's schedule with special event type

**TheSportsDB IDs:**
| League | TheSportsDB ID |
|--------|----------------|
| OHL | 4381 |
| WHL | 4382 |
| QMJHL | 4383 |
| Memorial Cup | TBD (may be events within each league) |

**Other Umbrella Leagues to Consider:**
- **NCAA** - Conferences are essentially sub-leagues (already handled via ESPN)
- **European club competitions** - Champions League teams come from domestic leagues
- **International tournaments** - World Cup, Olympics (national teams from various federations)

#### TheSportsDB API Reference

**Base URL:** `https://www.thesportsdb.com/api/v1/json/{api_key}/`

**Key Endpoints:**

| Endpoint | Purpose | Example |
|----------|---------|---------|
| `searchteams.php?t={name}` | Find team by name | `searchteams.php?t=Arsenal` |
| `lookupteam.php?id={id}` | Team details by ID | `lookupteam.php?id=133604` |
| `eventsnextleague.php?id={league_id}` | Next 15 events in league | `eventsnextleague.php?id=4328` |
| `eventspastleague.php?id={league_id}` | Last 15 events in league | `eventspastleague.php?id=4328` |
| `eventsseason.php?id={league_id}&s={season}` | Full season schedule | `eventsseason.php?id=4328&s=2024-2025` |
| `eventslast.php?id={team_id}` | Team's last 5 events | `eventslast.php?id=133604` |
| `eventsnext.php?id={team_id}` | Team's next 5 events | `eventsnext.php?id=133604` |
| `lookupevent.php?id={event_id}` | Event details | `lookupevent.php?id=441613` |
| `livescore.php?l={league_id}` | Live scores for league | `livescore.php?l=4328` |
| `lookuptable.php?l={league_id}&s={season}` | Standings | `lookuptable.php?l=4328&s=2024-2025` |

**Sample League IDs:**

| League | ID | Sport |
|--------|-----|-------|
| IHF World Championship (Women) | 4746 | Handball |
| EHF Champions League | 4424 | Handball |
| Bundesliga Handball | 4425 | Handball |
| AHL | 4380 | Ice Hockey |
| OHL | 4381 | Ice Hockey |
| WHL | 4382 | Ice Hockey |
| QMJHL | 4383 | Ice Hockey |
| KHL | 4063 | Ice Hockey |
| SHL | 4135 | Ice Hockey |
| Formula 1 | 4370 | Motorsport |
| UFC | 4443 | MMA |
| Six Nations | 4401 | Rugby |
| NRL | 4402 | Rugby League |

#### API & Rate Limits

| Tier | Cost | Rate Limit | Search Limit | Schedule Limit | Livescores |
|------|------|------------|--------------|----------------|------------|
| Free | $0 | 30 req/min | 2/min | 15 events, HOME only | ❌ |
| Premium | $3+/mo | 100 req/min | 10/min | 3000 events, all | ✅ |

**Critical Free Tier Limitations:**
- `eventsnext.php` and `eventslast.php` only return **HOME fixtures** on free tier
- No access to livescores API (premium only)
- Search endpoints limited to 2 requests per minute
- `eventsseason.php` returns full season but counts against rate limit

#### Caching Strategy (Essential for Free Tier)

Due to rate limits, aggressive local caching is required:

**Cache Tiers:**

| Data Type | Cache Duration | Refresh Strategy |
|-----------|----------------|------------------|
| Team info (name, logo, ID) | 7 days | Background refresh |
| League info | 7 days | Background refresh |
| Season schedule | 24 hours | On EPG generation |
| Standings | 6 hours | On EPG generation |
| Event details | 1 hour | On-demand |

**Cache Tables:**

```sql
CREATE TABLE thesportsdb_cache (
    cache_key TEXT PRIMARY KEY,     -- e.g., 'league:4380:schedule:2024-2025'
    endpoint TEXT,                   -- Original endpoint called
    data TEXT,                       -- JSON response
    created_at TIMESTAMP,
    expires_at TIMESTAMP,
    hit_count INTEGER DEFAULT 0
);

CREATE TABLE thesportsdb_teams (
    thesportsdb_id TEXT PRIMARY KEY,
    team_name TEXT,
    team_short_name TEXT,
    team_badge_url TEXT,
    league_id TEXT,
    sport TEXT,
    last_updated TIMESTAMP
);
```

**Request Budgeting per EPG Generation:**

| Operation | Requests | Notes |
|-----------|----------|-------|
| League schedule (cached) | 0-1 | Only if cache expired |
| Team lookups (cached) | 0-5 | Only for new teams |
| Event details | 0-3 | Only for active games |
| **Total per generation** | **~5-10** | Well under 30/min |

**Workaround for HOME-only Limitation:**

Since free tier only returns home fixtures, we need to:
1. Fetch schedule for BOTH teams in a matchup
2. Combine results to get complete picture
3. Cache aggressively to avoid double API calls

```python
def get_team_next_events(team_id):
    # Free tier returns only home games
    # For away games, we need to check opponent's schedule
    cached = get_cached(f'team:{team_id}:next')
    if cached:
        return cached

    response = api.eventsnext(team_id)  # 1 request
    cache_set(f'team:{team_id}:next', response, ttl=3600)
    return response
```

**Alternative: Use Season Schedule**

`eventsseason.php` returns ALL fixtures (home and away) for a league:
- More efficient than per-team calls
- One request per league per season
- Cache for 24 hours
- Parse locally to find team's games

```python
def get_league_season_events(league_id, season):
    cached = get_cached(f'league:{league_id}:season:{season}')
    if cached:
        return cached

    response = api.eventsseason(league_id, season)  # 1 request
    cache_set(f'league:{league_id}:season:{season}', response, ttl=86400)
    return response

def get_team_events(team_id, league_id, season):
    all_events = get_league_season_events(league_id, season)
    return [e for e in all_events if e['idHomeTeam'] == team_id or e['idAwayTeam'] == team_id]
```

#### Rate Limit Analysis

**Worst case (first run, no cache):**
- 3 leagues × 1 season schedule = 3 requests
- 10 team lookups = 10 requests
- Total: 13 requests (under 30/min limit)

**Typical run (with cache):**
- Most data cached = 0-3 requests
- Well under rate limit

**Verdict:** Free tier is sufficient with proper caching. Premium only needed for livescores.

#### Implementation: User-Provided API Keys

| Concern | User's Free API Key | Server-Side Cache |
|---------|---------------------|-------------------|
| Rate limits | 30/min - plenty | Unlimited but complex |
| Setup | User signs up (free) | Zero setup |
| Live scores | ✅ Works fine | ❌ Scaling problem |
| Cost to project | $0 | Dev account + hosting |
| Infrastructure | None | Server maintenance |
| Complexity | Simple | Over-engineered |

**How it works:**
1. User creates free account at TheSportsDB
2. User enters API key in Teamarr Settings
3. Teamarr uses key directly for all requests
4. Local caching reduces redundant API calls
5. Live scores just work (under rate limit)

**No server-side caching needed.** Each user's 30 req/min is more than enough for personal use.

#### Feature Parity: ESPN vs TheSportsDB

| Feature | ESPN | TheSportsDB Free | TheSportsDB Premium |
|---------|------|------------------|---------------------|
| Schedule matching | ✅ | ✅ (via season endpoint) | ✅ |
| Team logos | ✅ | ✅ | ✅ |
| Pregame EPG | ✅ | ✅ | ✅ |
| Live scores in EPG | ✅ | ❌ | ✅ |
| Postgame results | ✅ | ✅ | ✅ |
| Standings/records | ✅ | ✅ | ✅ |
| Odds | ✅ | ❌ | ❌ |
| TV broadcast info | ✅ | ✅ | ✅ |

**Free tier:** Full feature parity for pregame/postgame EPG. No live scores or odds.

**Premium tier ($3/mo):** Adds live scores. Still no odds (TheSportsDB doesn't provide).

#### Individual Sports Matching Challenge

**Combat sports (boxing, MMA) use fighter names instead of team names:**

Example stream: `En Español-Rafael Acosta vs. Franck Urquiaga (Main Card)`

| Challenge | Team Sports | Combat Sports |
|-----------|-------------|---------------|
| Entity type | Teams (stable) | Fighters (individuals) |
| Name format | Team name + city | First + Last name |
| Database size | ~30 teams/league | 1000s of fighters |
| Name variations | Few (aliases) | Many (nicknames, "La Ley", "Falito") |
| Matching strategy | Team alias lookup | Fighter name fuzzy match |

**Stream Name Patterns for Combat Sports:**

```
Boxing:    "Rafael Acosta vs. Franck Urquiaga (Main Card)"
           "Canelo Alvarez vs. Jermell Charlo - Undisputed"
           "En Español-[Fighter] vs. [Fighter]"

UFC/MMA:   "UFC 323: Dvalishvili vs. Yan 2"
           "PFL Lyon: Nemkov vs. Ferreira"
           "Bellator 123: [Fighter] vs. [Fighter]"
```

**Proposed Matching Strategy:**

1. **Event-card approach** (preferred):
   - Match by event name: "UFC 323", "PFL Lyon", "Acosta vs. Urquiaga"
   - TheSportsDB `eventsseason.php` returns full fight cards
   - Match stream to event card, not individual fighters

2. **Fighter name extraction**:
   - Parse `[Name1] vs. [Name2]` pattern
   - Normalize names (remove nicknames in parentheses)
   - Fuzzy match against TheSportsDB fighter database

3. **Hybrid approach**:
   - Try event name match first (faster, more reliable)
   - Fall back to fighter name extraction if no event match

**Sample real streams seen:**
- `ESPN+ 122 : En Español-Rafael Acosta vs. Franck Urquiaga (Main Card)` → Boxing, EBU European Lightweight Championship
- Language prefix handling already works ("En Español" stripped)
- Main Card/Prelims indicator useful for multi-channel events

#### Implementation Plan

**Phase 1: Core API Client**

New file: `api/thesportsdb_client.py`

```python
class TheSportsDBClient:
    """
    TheSportsDB API client with interface similar to ESPNClient.

    Methods mirror ESPN client where possible for consistency:
    - get_team_schedule(team_id) -> events
    - get_league_schedule(league_id, season) -> events
    - get_scoreboard(league_id) -> live events
    - get_team_info(team_id) -> team details
    - get_standings(league_id, season) -> table
    """
```

**Phase 2: Database Schema**

```sql
-- Add to league_config table
ALTER TABLE league_config ADD COLUMN api_source TEXT DEFAULT 'espn';
-- Values: 'espn', 'thesportsdb'

ALTER TABLE league_config ADD COLUMN thesportsdb_league_id TEXT;
-- TheSportsDB league ID (e.g., '4746' for IHF Women's World Championship)

-- Add to settings table
ALTER TABLE settings ADD COLUMN thesportsdb_api_key TEXT;

-- New cache table for TheSportsDB data
CREATE TABLE thesportsdb_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT,  -- JSON
    expires_at TIMESTAMP
);
```

**Phase 3: Event Normalization**

Create unified event structure that works with both APIs:

```python
@dataclass
class NormalizedEvent:
    """Unified event structure for ESPN and TheSportsDB."""
    event_id: str
    source: str  # 'espn' or 'thesportsdb'

    # Teams
    home_team_id: str
    home_team_name: str
    home_team_abbrev: str
    home_team_logo: str
    away_team_id: str
    away_team_name: str
    away_team_abbrev: str
    away_team_logo: str

    # Event details
    start_time: datetime
    status: str  # 'scheduled', 'in_progress', 'final'
    venue: str
    broadcast: str

    # Scores (if available)
    home_score: Optional[int]
    away_score: Optional[int]

    # League info
    league_id: str
    league_name: str
    sport: str
```

**Phase 4: Integration Points**

| Component | Changes Required |
|-----------|------------------|
| `league_config.py` | Route to correct API based on `api_source` |
| `event_matcher.py` | Accept NormalizedEvent, handle both sources |
| `orchestrator.py` | Use TheSportsDBClient for non-ESPN leagues |
| `team_matcher.py` | Add TheSportsDB team lookup tables |
| Settings UI | API key input field, connection test button |
| League Config UI | Dropdown for ESPN vs TheSportsDB |

**Phase 5: Team Matching for TheSportsDB**

New tables for TheSportsDB team cache (similar to soccer_team_leagues):

```sql
CREATE TABLE thesportsdb_teams (
    thesportsdb_team_id TEXT PRIMARY KEY,
    team_name TEXT,
    team_short_name TEXT,
    team_badge_url TEXT,  -- Logo
    league_id TEXT,
    sport TEXT,
    country TEXT
);

CREATE INDEX idx_thesportsdb_teams_name ON thesportsdb_teams(LOWER(team_name));
CREATE INDEX idx_thesportsdb_teams_league ON thesportsdb_teams(league_id);
```

#### UI Changes

**Settings Page:**
- New "TheSportsDB" section
- API key input (password field)
- "Test Connection" button
- Link to sign up for free API key

**League Config:**
- "API Source" dropdown: ESPN / TheSportsDB
- If TheSportsDB: League ID input + lookup helper
- Sport category selector

**Event Group Config:**
- Works seamlessly - assigned league determines API source
- Preview shows source indicator (ESPN logo vs TheSportsDB logo)

---

### 2. {unique_team} Exception Keyword Token

**Goal:** Add `{unique_team}` token for exception keywords that matches any team name variant and groups streams by resolved team identity.

**Status:** Documented - see `docs/PLAN_UNIQUE_TEAM_KEYWORD.md`

**Problem:** Users want a single keyword rule like `{unique_team} broadcast` that matches:
- `Detroit broadcast`
- `Lions broadcast`
- `DET broadcast`
- `Detroit Lions broadcast`
- `Dallas broadcast`
- `Cowboys broadcast`

All variants of the same team should consolidate to the same channel.

**Desired Behavior (Cowboys @ Lions game):**

| Stream | Matched Team | Canonical | Channel |
|--------|--------------|-----------|---------|
| `Detroit broadcast` | Lions | `lions broadcast` | Channel A |
| `Lions broadcast` | Lions | `lions broadcast` | Channel A |
| `DET broadcast` | Lions | `lions broadcast` | Channel A |
| `Dallas broadcast` | Cowboys | `cowboys broadcast` | Channel B |
| `Cowboys broadcast` | Cowboys | `cowboys broadcast` | Channel B |

**Result:** 2 channels - one per team, regardless of name variant used.

**Implementation:**
1. `{unique_team}` expands to all team name variants from event
2. Match against stream name
3. Determine which team matched (home or away)
4. Return canonical as `{team_short_name} {remainder}`
5. Template var `{exception_keyword_title}` = `Lions Broadcast` or `Cowboys Broadcast`

**Complexity:** Medium

---

## Technical Debt & Refactoring

### Event Enrichment Consolidation

**Status:** Phases 1-3 Complete - see `docs/ENRICHMENT_REFACTOR_PLAN.md`

**Completed:**
- ✅ Phase 1: Removed duplicate API calls, added thread safety to caches
- ✅ Phase 2: Created `epg/event_enricher.py` (~730 lines) with unified enrichment pipeline
- ✅ Phase 3: Migrated EventMatcher to use EventEnricher (removed ~280 lines of duplicate code)

**EventEnricher features:**
- `normalize_event_structure()` - Unified normalization from any ESPN source
- `enrich_with_scoreboard()` - Add live data (odds, scores, status)
- `enrich_with_team_stats()` - Add team context (records, conference, rank, streak)
- `enrich_event()` - Single-pass enrichment combining all steps
- Thread-safe caching with double-checked locking pattern

**Current state:** Two enrichment paths coexist:
- EventEnricher: Used by EventMatcher, available for new features
- Orchestrator: Keeps its own proven enrichment flow for team-based EPG

**Phase 4 (Deferred):** Orchestrator migration deferred due to complexity - its enrichment is tightly coupled with soccer discovery, batch processing, and filler generation.

---

## Completed Features

### v1.4.0 (December 2024)

- [x] **Multi-Sport Event Groups** - Per-stream league detection for ESPN+, ESPN Xtra
  - LeagueDetector module with tiered detection (Tier 1-4)
  - TeamLeagueCache for reverse team→league lookups
  - Date/time disambiguation for teams in multiple leagues
  - Tier 4 single-team schedule fallback for NAIA vs NCAA games
  - Support for 240+ soccer leagues via SoccerMultiLeague cache
  - Comprehensive logging with tier tracking

- [x] **Advanced Team Matching**
  - Tiered name normalization (exact → accent-stripped → number-stripped → word-overlap)
  - International team support with accent normalization (ñ, ü, é, etc.)
  - Word-overlap matching for complex team names (e.g., "1. FC Heidenheim 1846")
  - Ranking pattern support in stream names (`@ 4 Texas T`, `#8 Alabama`)
  - Language broadcast prefix stripping (En Español, Deportes, etc.)

- [x] **Stream Filtering Improvements**
  - Game indicator detection with ranking support
  - Time/date masking for accurate colon-based prefix detection
  - NCAA soccer recognition in scoreboard fallback

### v1.3.x (November-December 2024)

- [x] **Channel Lifecycle V2** - Multi-stream support, reconciliation, history tracking
- [x] **Parent/Child Group Architecture** - Stream consolidation across providers
- [x] **Soccer Multi-League Support** - 240+ leagues, weekly cache refresh
- [x] **Consolidation Exception Keywords** - Global keywords with sub-consolidate/separate/ignore behaviors
- [x] **{exception_keyword} Template Variable** - Include matched keyword in channel names
- [x] **Stream Include/Exclude Regex** - Filter streams before matching
- [x] **Parallel Processing Optimizations** - ThreadPoolExecutor for API calls
- [x] **Advanced Regex Module** - Variable-width lookbehind support
- [x] **Event Enricher Module** - Unified enrichment pipeline (Phases 1-3)
- [x] **League Code Normalization** - ESPN slugs as primary identifiers

### v1.0.0-1.2.x (October-November 2024)

- [x] **Template-Based Architecture** - Reusable formatting rules
- [x] **Variable Suffix System** - 252 variables with .next/.last support
- [x] **Conditional Descriptions** - Priority-based condition matching
- [x] **Team Import System** - Conference-based for college sports
- [x] **XMLTV Generation** - Full EPG output with metadata

---

## Related Documentation

| Document | Description |
|----------|-------------|
| `docs/PLAN_UNIQUE_TEAM_KEYWORD.md` | Detailed plan for {unique_team} token |
| `docs/PLAN_MULTISPORT_GROUPS.md` | Original multi-sport detection plan (IMPLEMENTED) |
| `docs/ENRICHMENT_REFACTOR_PLAN.md` | API call optimization phases |
| `docs/PLAN_CONSOLIDATION_EXCEPTION_KEYWORDS.md` | Original exception keywords plan (IMPLEMENTED) |
| `docs/CHANNEL_LIFECYCLE_IMPLEMENTATION_PLAN.md` | V2 channel lifecycle (IMPLEMENTED) |
| `docs/SOCCER_MULTI_LEAGUE_IMPLEMENTATION.md` | Soccer multi-league cache (IMPLEMENTED) |
| `CLAUDE.md` | Main codebase reference |
