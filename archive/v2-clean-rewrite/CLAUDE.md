# Teamarr v2 - Project Constitution

## Session Bootstrap

**Last Updated:** December 13, 2025

### Current Status: 90% V1 Parity

```
Code: 79 files, ~13,300 lines | Tests: 9 files, 479 lines | Ruff: 0 issues
```

### What's Working
- Team-based EPG generation (full pipeline)
- Event-based EPG generation (full pipeline)
- **ProviderRegistry** (single registration point for all data providers)
- **Soccer multi-league aggregation** (cache-based team→leagues lookup)
- **Team/league cache** (unified cache from ESPN + TSDB, weekly refresh)
- **UFC/MMA support** (event-based, prelim/main card detection)
- **Filler generation** (pregame/postgame/idle with 6-hour time blocks)
- Template engine (161 variables across 17 categories)
- **Conditional descriptions** (priority-based template selection)
- Stream matching (fuzzy, multi-league, 82.8% match rate)
- Stream fingerprint cache (avoids re-matching known streams)
- ESPN provider with retry logic
- **TheSportsDB provider** (fallback for OHL, WHL, QMJHL, NLL, PLL, Cricket, Boxing)
- **New sports:** Rugby (7 leagues), Tennis (ATP/WTA), Golf (5 tours), Motorsport (F1/IndyCar/NASCAR), Cricket (5 T20 leagues)
- **Tiered caching** (date-aware TTLs at service + provider layers)
- Service layer caching (routing with tiered TTLs)
- Database (teams, templates, settings, stream_match_cache, team_cache, league_cache)
- REST API with Swagger docs
- **Centralized logging** (file + console, rotating, JSON option)
- Channel lifecycle manager (skeleton)
- 6-hour time block utilities

### What's NOT Working Yet
- Odds/H2H/Player leaders extraction from ESPN
- Full channel lifecycle (Dispatcharr integration)
- EPG statistics tracking
- Background scheduler

### Key Architecture

```
                    ┌──────────────────────────────┐
                    │    ProviderRegistry          │
                    │ (single registration point)  │
                    └──────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                  │
            ▼                  ▼                  ▼
      ESPNProvider      TSDBProvider        (future providers)
            │                  │
            ▼                  ▼
      SportsDataService (tiered cache) ──► Consumers ──► XMLTV
```

**Provider Selection (automatic):**
- ESPN: NFL, NBA, NHL, MLB, MLS, college, soccer (200+ leagues), UFC, rugby (7 leagues), tennis, golf (5 tours), motorsport (F1, IndyCar, NASCAR)
- TSDB: OHL, WHL, QMJHL, NLL, PLL, Cricket (IPL, BBL, CPL, T20 Blast, BPL), Boxing (fallback for ESPN gaps)

**Registration:** All providers configured in ONE file: `teamarr/providers/__init__.py`

**Tiered Cache TTLs (by date proximity):**
| Date | Service Layer | TSDB Client |
|------|---------------|-------------|
| Past | 8 hours | 7 days |
| Today | 30 minutes | 30 minutes |
| Tomorrow | 4 hours | 4 hours |
| Days 2-7 | 8 hours | 8 hours |
| Days 8+ | 8 hours | 24 hours |

**Fixed TTLs:**
- Team Schedule: 8 hours
- Single Event: 30 minutes
- Team Stats: 4 hours
- Team Info: 24 hours

### Rate Limit Handling (TSDB)

TSDB free tier has 30 requests/minute limit. The system handles this gracefully:

```python
# Never fails - always waits and continues
# Two types of rate limiting:
# 1. Preemptive: Our sliding window limiter prevents hitting API limit
# 2. Reactive: If we get 429, wait 60s and retry

# Get rate limit stats for UI feedback
stats = service.provider_stats()
tsdb_stats = stats["tsdb"]["rate_limit"]

# Example response:
{
    "total_requests": 28,
    "preemptive_waits": 2,      # Times our limiter made us wait
    "reactive_waits": 0,        # Times we hit actual 429
    "total_wait_seconds": 45.2,
    "is_rate_limited": True,    # True if any waits occurred
    "last_wait_at": "2025-12-13T10:30:00",
    "session_start": "2025-12-13T10:00:00"
}

# Reset stats at start of EPG generation
service.reset_provider_stats()
```

### Logging

Centralized logging with console + file output, rotating files, and optional JSON format.

```bash
# Environment variables
LOG_LEVEL=INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_DIR=/path/to/logs   # Default: ./logs or /app/data/logs (Docker)
LOG_FORMAT=text         # "text" or "json"
```

```python
from teamarr.utilities.logging import setup_logging, get_logger

# At startup (done automatically in FastAPI app)
setup_logging()

# In any module
logger = get_logger(__name__)
logger.info("Something happened")
logger.debug("Details: %s", data)
logger.error("Failed", exc_info=True)
```

**Log files:**
- `teamarr.log` - All logs (DEBUG+), rotating 10MB x 5
- `teamarr_errors.log` - Errors only, rotating 10MB x 3

**Noisy loggers silenced:** uvicorn, httpx, httpcore, watchfiles

### Stream Fingerprint Cache

```
Stream comes in → Compute fingerprint hash(group_id:stream_id:stream_name)
                          │
                    Cache lookup?
                    ├── HIT → Return cached event_id, refresh dynamic fields from API
                    └── MISS → Full match, cache result, return event
```

**Key Points:**
- Fingerprint changes if stream name changes → fresh match
- Entries purged after 5 generations not seen
- Endpoint: `POST /api/v1/epg/streams/match`

### Team/League Cache (NEW)

Unified cache for team and league data from both ESPN and TSDB providers.
Supports three use cases:

1. **Event matching**: "Freiburg vs Stuttgart" → candidate leagues
2. **Team multi-league**: Liverpool → [eng.1, uefa.champions, eng.fa, ...]
3. **"soccer_all"**: List all soccer leagues for event-based EPG

```python
from teamarr.consumers import (
    expand_leagues,
    find_leagues_for_stream,
    get_cache,
    refresh_cache,
)

# Expand "soccer_all" to actual league slugs
leagues = expand_leagues(["nfl", "soccer_all"])  # → ["nfl", "eng.1", "esp.1", ...]

# Find candidate leagues for a stream
candidates = find_leagues_for_stream("Freiburg vs Stuttgart", sport="soccer")
# → ["ger.1"]

# Get all leagues a team plays in
cache = get_cache()
liverpool_leagues = cache.get_team_leagues("364", "espn")
# → ["eng.1", "uefa.champions", "eng.fa", ...]

# Find leagues where both teams exist
candidates = cache.find_candidate_leagues("Lions", "Bears")
# → [("nfl", "espn")]
```

**Cache refresh:**
- Weekly refresh to handle promotion/relegation
- `POST /api/v1/cache/refresh` to trigger manually
- `GET /api/v1/cache/status` for cache statistics

**Tables:** `team_cache`, `league_cache`, `cache_meta`

### Settings (DB)
```sql
team_schedule_days_ahead = 30    -- Fetch schedule (for .next vars)
event_match_days_ahead = 7       -- Stream matching window
epg_output_days_ahead = 14       -- Days in XMLTV output
epg_lookback_hours = 6           -- Check in-progress games
epg_generation_counter = 0       -- Cache generation tracking
channel_create_timing = 'same_day'
channel_delete_timing = 'day_after'
```

### Priority Next Steps
1. **Odds/H2H extraction** - Use ESPN event summary data
2. **Channel lifecycle** - Full Dispatcharr integration

---

## Vision

Teamarr v2 is a complete rewrite of the data layer with a provider-agnostic architecture. The system fetches sports data from multiple sources (ESPN, TheSportsDB, future providers), normalizes it into a unified format, and presents it to consumers (EPG generation, channel management, UI) in a source-agnostic way.

**Users don't know or care where data comes from. They see teams, events, and EPG.**

---

## Terminology

| Term | Definition |
|------|------------|
| **Data Provider** | ESPN, TheSportsDB - provides sports data (schedules, teams, scores) |
| **M3U Provider** | IPTV provider - provides streams organized into groups |
| **Event Group** | A group of streams from an M3U provider (e.g., "ESPN+" package) |
| **League** | A sports competition (NFL, NBA, eng.1, etc.) |
| **Event** | A single game/match from a data provider |

**Key distinction:** Data providers give us sports data. M3U providers give us streams. We match streams to events.

---

## Core EPG Flows

Two equally important, first-class EPG generation modes:

### Team-Based EPG
"Show me Lions games on the Lions channel"
- User configures a team channel (e.g., "Detroit Lions")
- System fetches that team's schedule (30 days for .next vars)
- Generates EPG for configured output window (default 14 days)
- **Use case:** Dedicated channel per favorite team

### Event-Based EPG
"Show me all NFL games today, each on its own channel"
- System scans league scoreboard for events
- Dynamically creates/removes channels per event
- Matches streams to events (with fingerprint cache)
- **Use case:** Full league coverage, game-day channels

Both flows share the same provider layer and data types. Neither is secondary.

---

## Data Flow Architecture

### ESPN Endpoint Purposes & Cache TTLs

| Endpoint | Purpose | Cache TTL |
|----------|---------|-----------|
| Scoreboard | Event discovery (IDs, teams, times) | 8 hours |
| Team Schedule | Team's upcoming games | 8 hours |
| Event Summary | Status, scores, odds, H2H | 30 minutes |
| Team Info | Name, logo, record, stats | 24 hours (4hr for stats) |

### Smart Data Enrichment

```
Discovery (8hr cache)              Dynamic Data (30min cache)
┌─────────────────────┐            ┌─────────────────────────┐
│ Scoreboard/Schedule │──────────▶│ Event Summary           │
│ - Event IDs         │            │ - Status (final/live)   │
│ - Team names        │            │ - Scores                │
│ - Start times       │            │ - Odds, H2H, etc.       │
└─────────────────────┘            └─────────────────────────┘
         │                                   │
         │     Only for today/yesterday      │
         └───────────────────────────────────┘
```

---

## Stream Fingerprint Cache

Caches successful stream-to-event matches to avoid expensive re-matching on every EPG generation.

### How It Works

```python
# Fingerprint = SHA256(group_id:stream_id:stream_name)[:16]
# If stream name changes, fingerprint changes → cache miss → fresh match

cache = StreamMatchCache(get_db)
cached = cache.get(group_id, stream_id, stream_name)

if cached:
    # Cache HIT: use cached event_id, refresh dynamic fields from API
    event = service.get_event(cached.event_id, cached.league)
    cache.touch(group_id, stream_id, stream_name, generation)
else:
    # Cache MISS: perform full match
    event = matcher.match(stream_name)
    cache.set(group_id, stream_id, stream_name, event.id, league, event_data, generation)

# Purge entries not seen in 5 generations
cache.purge_stale(generation)
```

### API Endpoint

```bash
POST /api/v1/epg/streams/match
{
  "group_id": 1,
  "streams": [{"id": 100, "name": "Lions vs Bears"}, ...],
  "search_leagues": ["nfl", "nba"],
  "include_leagues": ["nfl"],
  "target_date": "2025-12-13"
}

# Response includes cache stats:
{
  "cache_hits": 45,
  "cache_misses": 5,
  "cache_hit_rate": 0.90,
  ...
}
```

---

## Filler Generation

Fills gaps between events with pregame, postgame, and idle content. Aligns to 6-hour time blocks for clean EPG.

### Filler Types

| Type | When Used | Context |
|------|-----------|---------|
| **Pregame** | Before a game | `.next` = upcoming game, `.last` = previous game |
| **Postgame** | After a game | `.next` = next game, `.last` = just-finished game |
| **Idle** | No game that day | `.next` = next scheduled game, `.last` = last game |

### 6-Hour Time Block Alignment

```
Day:    |  00:00  |  06:00  |  12:00  |  18:00  |  00:00  |
        |---------|---------|---------|---------|---------|
Block:  |    0    |    1    |    2    |    3    |    0    |

Example: Game at 7:00 PM, ends ~10:00 PM
        |  Pregame Coverage  |  Game  | Postgame  |
        |  00:00 → 19:00     | 19:00→22:00 | 22:00→00:00 |
```

### Configuration

```python
from teamarr.consumers import FillerConfig, FillerTemplate

config = FillerConfig(
    pregame_enabled=True,
    pregame_template=FillerTemplate(
        title="Pregame Coverage",
        description="{team_name} vs {opponent.next} starts at {game_time.next}",
    ),
    postgame_enabled=True,
    postgame_template=FillerTemplate(
        title="Postgame Recap",
        description="{team_name} {result_text.last} {final_score.last}",
    ),
    idle_enabled=True,
    idle_template=FillerTemplate(
        title="{team_name} Programming",
        description="Next game: {game_date.next} vs {opponent.next}",
    ),
)
```

### Midnight Crossover Mode

When a game crosses midnight and the next day has no game:
- **`postgame`**: Continue postgame content through next day
- **`idle`**: Switch to idle content at midnight

Setting: `midnight_crossover_mode` in settings table.

---

## UFC/MMA Support

UFC is an event-based sport with special handling for fight cards.

### Data Model

- Fight cards are Events (not individual bouts)
- Main event fighters mapped to `home_team`/`away_team` for compatibility
- `Event.main_card_start` stores when main card begins
- `Event.start_time` is prelims start

```python
# UFC Event structure
Event(
    id="600056266",
    name="UFC Fight Night: Royval vs. Kape",
    short_name="M. Kape vs B. Royval",
    sport="mma",
    league="ufc",
    start_time="2025-12-14T03:00:00Z",      # Prelims start
    main_card_start="2025-12-14T06:00:00Z", # Main card start
    home_team=Team(name="Manel Kape", ...),
    away_team=Team(name="Brandon Royval", ...),
)
```

### Stream Matching

Three matching strategies (in order):

1. **Fighter name matching** - Both fighters found in stream name
2. **Event name matching** - "UFC Fight Night: Royval vs. Kape" substring
3. **Single-event league matching** - UFC keywords + only one event that day

**Single-event league matching:** UFC only has ONE event per day/weekend. If a stream mentions "ufc" or "fight night" keywords and there's a UFC event that day, it matches automatically - no fighter names needed.

Example streams that match:
- "UFC Fight Night Royval vs Kape" → event name match
- "UFC FN Prelims" → abbreviation expansion + single-event league
- "PPV EVENT 01: UFC" → single-event league (contains "ufc")

### Abbreviation Expansion

Common abbreviations are expanded before matching:

| Abbreviation | Expansion |
|--------------|-----------|
| FN | Fight Night |
| UFC FN | UFC Fight Night |
| PPV | Pay Per View |
| vs, v | versus |

So "UFC FN Prelims" becomes "UFC Fight Night Prelims" before matching.

### Prelim/Main Card Detection

Stream names determine EPG duration:

| Stream Name | Detected Type | Start | Duration |
|-------------|---------------|-------|----------|
| "UFC Fight Night" | Full card | 03:00 | 5 hours |
| "UFC FN Prelims" | Prelims only | 03:00 | 3 hours |
| "UFC FN Main Card" | Main card only | 06:00 | 2.5 hours |

Detection is keyword-based: "prelim" → prelims, "main" → main card, else full event.

### Multi-League Matcher Integration

UFC works with both matchers:
- **SingleLeagueMatcher**: For UFC-dedicated streams
- **MultiLeagueMatcher**: For PPV groups with mixed sports

In multi-league mode, UFC uses the `SINGLE_EVENT_LEAGUES` optimization:
```python
SINGLE_EVENT_LEAGUES = {
    "ufc": ["ufc", "fight night"],  # Keywords to look for
}
```

### Test Command

```bash
python -c "
from datetime import date
from teamarr.services import create_default_service
from teamarr.consumers.multi_league_matcher import MultiLeagueMatcher

service = create_default_service()
matcher = MultiLeagueMatcher(service, ['ufc', 'nfl', 'nba'], include_leagues=['ufc'])
result = matcher.match_all(['UFC FN Prelims', 'Lions vs Bears'], date.today())
for r in result.results:
    print(f'{r.stream_name}: {\"MATCH\" if r.matched else \"NO MATCH\"} -> {r.event.name if r.event else \"N/A\"}')
"
```

---

## Supported Sports Reference

### Team Sports (ESPN)
| Sport | Leagues | Notes |
|-------|---------|-------|
| Football | NFL, college-football | Standard team vs team |
| Basketball | NBA, WNBA, mens-college-basketball, womens-college-basketball | Standard team vs team |
| Hockey | NHL, mens-college-hockey, womens-college-hockey | Standard team vs team |
| Baseball | MLB | Standard team vs team |
| Soccer | MLS + 200+ international leagues | Multi-league team support |

### Combat Sports
| Sport | Provider | Leagues | Notes |
|-------|----------|---------|-------|
| MMA/UFC | ESPN | ufc | Fight cards as events, prelim/main card detection |
| Boxing | TSDB | boxing | Fighters parsed from event name |

### Rugby (ESPN)
| League Code | Competition |
|-------------|-------------|
| six-nations | Six Nations Championship |
| rugby-championship | The Rugby Championship |
| premiership-rugby | Gallagher Premiership |
| united-rugby-championship | United Rugby Championship |
| super-rugby | Super Rugby Pacific |
| mlr | Major League Rugby |
| rugby-world-cup | Rugby World Cup |
| nrl | NRL (Rugby League) |

### Tournament Sports (ESPN)
These use tournament-level events (no home/away teams):

| Sport | Leagues | Duration |
|-------|---------|----------|
| Tennis | atp, wta | 3 hours |
| Golf | pga, lpga, liv, dp-world, champions-tour | 6 hours |
| Racing | f1, indycar, nascar, nascar-xfinity, nascar-truck | 3 hours |

### Cricket (TSDB)
| League Code | Competition | Provider ID |
|-------------|-------------|-------------|
| ipl | Indian Premier League | 4460 |
| bbl | Big Bash League (Australia) | 4461 |
| cpl | Caribbean Premier League | 5176 |
| t20-blast | English T20 Blast | 4463 |
| bpl | Bangladesh Premier League | 5529 |

Duration: 4 hours (T20 matches)

### Junior/Minor Leagues (TSDB)
| League | Sport | Provider ID |
|--------|-------|-------------|
| OHL | Hockey | 4380 |
| WHL | Hockey | 4381 |
| QMJHL | Hockey | 4382 |
| NLL | Lacrosse | 4944 |
| PLL | Lacrosse | 5094 |

---

## Conditional Descriptions

Select different description templates based on game conditions and priority. Enables dynamic, context-aware EPG descriptions.

### How It Works

Templates can have multiple description options with conditions. The system evaluates conditions against the game context and selects the highest-priority matching template.

```python
from teamarr.templates.resolver import TemplateResolver

resolver = TemplateResolver()

# Description options (JSON or list)
options = [
    {"condition": "win_streak", "condition_value": "5", "priority": 10,
     "template": "{team_name} on a {streak}-game win streak!"},
    {"condition": "is_home", "priority": 50,
     "template": "{team_name} hosts {opponent}"},
    {"priority": 100, "template": "{team_name} vs {opponent}"}  # Fallback
]

# Select and resolve the best template
result = resolver.resolve_conditional(options, context)
```

### Priority System

| Priority | Type | Description |
|----------|------|-------------|
| 1-49 | High priority conditions | Win streaks, ranked matchups |
| 50-99 | Normal conditions | Home/away, conference games |
| 100 | Fallback | Always matches (default) |

Lower priority number = higher precedence. If multiple conditions at the same priority match, one is randomly selected.

### Available Conditions (16 total)

**Home/Away:**
- `is_home` - Team is playing at home
- `is_away` - Team is playing away

**Streaks (require value):**
- `win_streak` - Team win streak ≥ value
- `loss_streak` - Team loss streak ≥ value
- `home_win_streak` - Home win streak ≥ value
- `home_loss_streak` - Home loss streak ≥ value
- `away_win_streak` - Away win streak ≥ value
- `away_loss_streak` - Away loss streak ≥ value

**Rankings:**
- `is_ranked_opponent` - Opponent is top 25 ranked
- `is_top_ten_matchup` - Both teams are top 10

**Season Type:**
- `is_playoff` - Playoff game
- `is_preseason` - Preseason game

**Other:**
- `is_conference_game` - Same conference (college)
- `is_national_broadcast` - Game on national TV
- `has_odds` - Betting odds available
- `opponent_name_contains` - Opponent name contains string (requires value)

### Example: Dynamic Game Descriptions

```json
[
    {
        "condition": "win_streak",
        "condition_value": "5",
        "priority": 10,
        "template": "🔥 {team_name} riding a {streak}-game win streak vs {opponent}"
    },
    {
        "condition": "is_ranked_opponent",
        "priority": 20,
        "template": "⭐ {team_name} faces ranked #{opponent_rank} {opponent}"
    },
    {
        "condition": "is_home",
        "priority": 50,
        "template": "{team_name} hosts {opponent} at {venue}"
    },
    {
        "condition": "is_away",
        "priority": 50,
        "template": "{team_name} travels to face {opponent}"
    },
    {
        "priority": 100,
        "template": "{team_name} vs {opponent}"
    }
]
```

### Database Integration

Store description options in templates table:

```sql
-- Single fallback (legacy)
INSERT INTO templates (team_id, description_template)
VALUES ('8', '{team_name} vs {opponent}');

-- Conditional descriptions (JSON)
INSERT INTO templates (team_id, description_options)
VALUES ('8', '[{"condition": "is_home", "priority": 50, "template": "Home: {team_name} vs {opponent}"}, {"priority": 100, "template": "{team_name} vs {opponent}"}]');
```

The resolver checks `description_options` first; falls back to `description_template` if not set.

---

## Core Principles

1. **Single Source of Truth** - Each piece of logic exists in ONE place
2. **Type-Driven Design** - All data structures are dataclasses with attribute access
3. **Clean Boundaries** - Providers → Service → Consumers
4. **Testability** - Mock providers, captured API responses, no live-only logic
5. **No Premature Optimization** - Simple code > clever code
6. **Maintainability Over Cleverness** - Code will be read 100x more than written
7. **Future Over Past** - Design for where we're going, not where we've been
8. **API-First Design** - All functionality exposed via documented REST API
9. **Docs Stay Current** - Documentation updated after each implementation step
10. **Single Point of Change** - Adding new capabilities requires changes in ONE place

---

## Quick Reference

### Layer Responsibilities

| Layer | Responsibility |
|-------|---------------|
| **Consumer** | Business logic (EPG, matching, channels) |
| **Service** | Routing, caching, ID translation |
| **Provider** | Fetch + normalize → dataclasses |
| **Client** | Raw HTTP + retry logic |

### Provider Interface

```python
class SportsProvider(ABC):
    @property
    def name(self) -> str: ...
    def supports_league(self, league: str) -> bool: ...
    def get_events(self, league: str, date: date) -> list[Event]: ...
    def get_team_schedule(self, team_id: str, league: str, days_ahead: int) -> list[Event]: ...
    def get_team(self, team_id: str, league: str) -> Team | None: ...
    def get_event(self, event_id: str, league: str) -> Event | None: ...
    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None: ...
    # Optional (for cache discovery):
    def get_supported_leagues(self) -> list[str]: ...  # Default: []
    def get_league_teams(self, league: str) -> list[Team]: ...  # Default: []
```

### Adding a New Provider

**Single registration point:** `teamarr/providers/__init__.py`

1. **Create provider module:**
   ```
   teamarr/providers/newprovider/
   ├── __init__.py
   ├── client.py     # HTTP client + retry logic
   └── provider.py   # NewProvider(SportsProvider)
   ```

2. **Implement SportsProvider interface:**
   ```python
   class NewProvider(SportsProvider):
       @property
       def name(self) -> str:
           return "newprovider"

       def supports_league(self, league: str) -> bool:
           return league in self.SUPPORTED_LEAGUES

       def get_supported_leagues(self) -> list[str]:
           # For cache discovery - optional but recommended
           return list(self.SUPPORTED_LEAGUES)

       # ... implement remaining methods
   ```

3. **Register in providers/__init__.py:**
   ```python
   from teamarr.providers.newprovider import NewProvider

   ProviderRegistry.register(
       name="newprovider",
       provider_class=NewProvider,
       priority=50,  # Between ESPN (0) and TSDB (100)
       enabled=True,
   )
   ```

4. **Add league mappings to database (if needed):**
   ```sql
   INSERT INTO league_provider_mappings
   (league_code, provider, provider_league_id, sport, enabled)
   VALUES ('new.league', 'newprovider', 'PROVIDER_ID', 'sport', 1);
   ```

**That's it.** The rest of the system (SportsDataService, CacheRefresher, etc.) automatically discovers and uses the new provider.

---

## Build Progress

### ✅ Complete
- [x] Team-based EPG generation
- [x] Event-based EPG generation
- [x] **Filler generation** (pregame/postgame/idle)
- [x] Template engine (161 variables)
- [x] Stream matching (fuzzy, multi-league)
- [x] Stream fingerprint cache
- [x] ESPN provider + retry logic
- [x] **TheSportsDB provider** (OHL, WHL, QMJHL, NLL, PLL, IPL, Boxing)
- [x] **ProviderRegistry** (single registration point for all providers)
- [x] **Team/league cache** (unified cache, soccer multi-league support)
- [x] **Tiered caching** (date-aware TTLs at both layers)
- [x] **Rate limit handling** (wait-and-continue with UI-ready stats)
- [x] **Centralized logging** (rotating files, JSON option, env config)
- [x] Service layer caching + routing
- [x] Database schema + connection
- [x] REST API + Swagger docs
- [x] Timezone handling (UTC storage, user TZ display)
- [x] 6-hour time block utilities
- [x] Channel lifecycle manager (skeleton)
- [x] Smart data enrichment (today/yesterday → fresh data)
- [x] **Conditional descriptions** (priority-based template selection)
- [x] **Rugby support** (Six Nations, Rugby Championship, Premiership, URC, Super Rugby, MLR, NRL)
- [x] **Boxing support** (via TSDB, fighter parsing from event names)
- [x] **Tennis support** (ATP, WTA - tournament-based events)
- [x] **Golf support** (PGA, LPGA, LIV, DP World, Champions Tour)
- [x] **Motorsport support** (F1, IndyCar, NASCAR Cup/Xfinity/Truck)
- [x] **Cricket support** (IPL, BBL, CPL, T20 Blast, BPL via TSDB)

### ⏳ Not Yet Implemented
- [ ] Odds/H2H/Player leaders extraction
- [ ] Full channel lifecycle (Dispatcharr integration)
- [ ] EPG statistics tracking
- [ ] Background scheduler

---

## Directory Structure

```
teamarrv2/
├── CLAUDE.md                 # This file - project constitution
├── DEVELOPMENT.md            # Dev setup guide
├── pyproject.toml            # Package config
│
├── teamarr/
│   ├── core/                 # Types + interfaces
│   │   ├── types.py          # Team, Event, Programme, TeamStats, etc.
│   │   └── interfaces.py     # SportsProvider ABC
│   │
│   ├── providers/
│   │   ├── __init__.py       # PROVIDER REGISTRATION (single point)
│   │   ├── registry.py       # ProviderRegistry class
│   │   ├── espn/             # ESPN data provider (primary)
│   │   │   ├── client.py     # HTTP client + retry
│   │   │   └── provider.py   # ESPNProvider implementation
│   │   └── tsdb/             # TheSportsDB provider (fallback)
│   │       ├── client.py     # HTTP client + rate limiting + caching
│   │       └── provider.py   # TSDBProvider implementation
│   │
│   ├── services/             # Service layer
│   │   └── sports_data.py    # SportsDataService (routing + caching)
│   │
│   ├── consumers/            # Business logic
│   │   ├── orchestrator.py   # EPG generation coordinator
│   │   ├── team_epg.py       # Team-based EPG (with multi-league support)
│   │   ├── event_epg.py      # Event-based EPG generator
│   │   ├── filler.py         # Filler generation (pregame/postgame/idle)
│   │   ├── channel_lifecycle.py  # Channel create/delete timing
│   │   ├── stream_match_cache.py # Fingerprint cache
│   │   ├── team_league_cache.py  # Team/league cache (ESPN + TSDB)
│   │   ├── cached_matcher.py # Cache-integrated matcher
│   │   ├── event_matcher.py  # Match queries to events
│   │   ├── single_league_matcher.py
│   │   └── multi_league_matcher.py
│   │
│   ├── templates/            # Template engine
│   │   ├── resolver.py       # Variable substitution
│   │   ├── context_builder.py
│   │   ├── context.py        # TemplateContext dataclass
│   │   └── variables/        # 19 category files (161 vars)
│   │
│   ├── utilities/
│   │   ├── cache.py          # TTLCache + TTL constants
│   │   ├── logging.py        # Centralized logging setup
│   │   ├── time_blocks.py    # 6-hour block utilities
│   │   ├── tz.py             # Timezone utilities
│   │   ├── xmltv.py          # XMLTV output
│   │   └── fuzzy_match.py    # FuzzyMatcher
│   │
│   ├── database/
│   │   ├── schema.sql        # Full schema (team_cache, league_cache, etc.)
│   │   ├── connection.py     # get_db, init_db
│   │   └── leagues.py        # League mapping queries
│   │
│   ├── api/
│   │   ├── app.py            # FastAPI app
│   │   ├── models.py         # Pydantic models
│   │   ├── dependencies.py   # DI
│   │   └── routes/           # Endpoints
│   │
│   └── config/               # Configuration
│
├── tests/                    # Test files
└── docs/                     # Documentation
```

---

## Settings Schema

```sql
-- Look Ahead Settings
team_schedule_days_ahead = 30    -- For .next vars, conditionals
event_match_days_ahead = 7       -- Event-stream matching window
epg_output_days_ahead = 14       -- Days in XMLTV output
epg_lookback_hours = 6           -- Check in-progress games

-- Cache Generation Counter
epg_generation_counter = 0       -- For cache purging

-- Channel Lifecycle
channel_create_timing = 'same_day'   -- same_day, day_before, etc.
channel_delete_timing = 'day_after'  -- same_day, day_after, etc.
midnight_crossover_mode = 'postgame' -- postgame or idle

-- EPG Output
epg_timezone = 'America/New_York'

-- Duration Defaults (hours)
duration_default = 3.0
duration_basketball = 3.0
duration_football = 3.5
duration_hockey = 3.0
duration_baseball = 3.5
duration_soccer = 2.5
duration_mma = 5.0
duration_rugby = 2.5
duration_boxing = 4.0
duration_tennis = 3.0
duration_golf = 6.0
duration_racing = 3.0
duration_cricket = 4.0
```

---

## Test Commands

```bash
# Activate venv
source .venv/bin/activate

# Run all tests
pytest

# Test team EPG
python -c "
from teamarr.services import create_default_service
from teamarr.consumers import Orchestrator, TeamChannelConfig, TeamEPGOptions

service = create_default_service()
orchestrator = Orchestrator(service)
config = TeamChannelConfig(team_id='8', league='nba', channel_id='pistons', team_name='Detroit Pistons')
result = orchestrator.generate_for_teams([config], TeamEPGOptions(output_days_ahead=7))
print(f'Generated {len(result.programmes)} programmes')
"

# Test team EPG with filler
python -c "
from teamarr.services import create_default_service
from teamarr.consumers import Orchestrator, TeamChannelConfig, TeamEPGOptions

service = create_default_service()
orchestrator = Orchestrator(service)
config = TeamChannelConfig(team_id='8', league='nba', channel_id='pistons', team_name='Detroit Pistons')
result = orchestrator.generate_for_teams([config], TeamEPGOptions(output_days_ahead=3, filler_enabled=True))
game_count = sum(1 for p in result.programmes if 'Pregame' not in p.title and 'Postgame' not in p.title and 'Programming' not in p.title)
filler_count = len(result.programmes) - game_count
print(f'Generated {len(result.programmes)} programmes ({game_count} games, {filler_count} fillers)')
"

# Test TSDB provider (OHL)
python -c "
from datetime import date
from teamarr.services import create_default_service

service = create_default_service()
events = service.get_events('ohl', date.today())
print(f'OHL events today: {len(events)}')
for e in events[:3]: print(f'  {e.home_team.name} vs {e.away_team.name}')
"

# Test stream cache
python -c "
from teamarr.database import get_db, reset_db
from teamarr.consumers import StreamMatchCache, increment_generation_counter

reset_db()
cache = StreamMatchCache(get_db)
gen = increment_generation_counter(get_db)
cache.set(1, 100, 'Lions vs Bears', '12345', 'nfl', {'id': '12345'}, gen)
entry = cache.get(1, 100, 'Lions vs Bears')
print(f'Cache test: {entry.event_id if entry else \"FAILED\"}')"

# Run API
uvicorn teamarr.api.app:app --reload
# Swagger at http://localhost:8000/docs
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/epg/xmltv | Team-based XMLTV output |
| POST | /api/v1/epg/generate | Generate team EPG |
| GET | /api/v1/epg/events/xmltv | Event-based XMLTV output |
| POST | /api/v1/epg/events/generate | Generate event EPG |
| POST | /api/v1/epg/streams/match | Match streams with fingerprint cache |
| GET/POST | /api/v1/teams | Teams CRUD |
| GET/POST | /api/v1/templates | Templates CRUD |
| **GET** | **/api/v1/cache/status** | **Get team/league cache statistics** |
| **POST** | **/api/v1/cache/refresh** | **Trigger cache refresh from providers** |
| **GET** | **/api/v1/cache/leagues** | **List cached leagues (filter by sport/provider)** |
| **GET** | **/api/v1/cache/teams/search** | **Search teams by name** |
| **GET** | **/api/v1/cache/candidate-leagues** | **Find leagues for team matchup** |

---

## Reference

v1 codebase at `../teamarr/` for reference.

Key v1 files:
- `epg/orchestrator.py` - Team EPG + filler generation
- `epg/template_engine.py` - Variable substitution (all 142 vars)
- `epg/stream_match_cache.py` - v1 fingerprint cache implementation
- `epg/channel_lifecycle.py` - Channel CRUD + Dispatcharr integration
- `api/espn_client.py` - ESPN API patterns
