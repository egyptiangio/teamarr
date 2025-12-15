# V1 → V2 Migration & Parity Analysis

**Generated:** December 13, 2025

## Executive Summary

| Metric | V1 | V2 | Coverage |
|--------|----|----|----------|
| Lines of Code | 39,419 | 11,235 | 28% |
| Python Files | 33 | 75 | More modular |
| Schema Lines | 1,156 | 408 | 35% |
| Database Tables | 32 | ~12 | Simplified |
| Template Variables | 227+ | 161 | 71% |
| API Endpoints | 80+ | ~15 | 19% (REST API focus) |
| Feature Parity | 100% | **~70%** | In Progress |

**V2 Design Philosophy:** Provider-agnostic architecture with cleaner separation of concerns. More files but more maintainable - each module has single responsibility.

---

## Feature Parity Matrix

### Legend
- ✅ **Complete** - Fully implemented and tested
- 🔶 **Partial** - Core functionality works, missing advanced features
- ❌ **Not Started** - Not yet implemented
- 🚫 **Deprecated** - Intentionally removed or replaced

---

### Core EPG Generation

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Team-based EPG | ✅ | ✅ | Full pipeline working |
| Event-based EPG | ✅ | ✅ | Full pipeline working |
| UFC/MMA Support | ❌ | ✅ | **NEW in V2** - prelim/main card detection |
| XMLTV Output | ✅ | ✅ | Equivalent output format |
| Multi-day EPG | ✅ | ✅ | Configurable days ahead |
| Timezone handling | ✅ | ✅ | UTC storage, user TZ display |
| Sport durations | ✅ | ✅ | Per-sport configurable |

### Filler Generation

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Pregame filler | ✅ | ✅ | Working |
| Postgame filler | ✅ | ✅ | Working |
| Idle filler | ✅ | ✅ | Days without games |
| Multi-period pregame | ✅ | ❌ | V1 has multiple pregame windows |
| Multi-period postgame | ✅ | ❌ | V1 has multiple postgame windows |
| 6-hour time blocks | ✅ | ✅ | Clean alignment |
| Midnight crossover | ✅ | ✅ | postgame/idle modes |
| Filler max_hours split | ✅ | ❌ | V1 splits long fillers per sport |
| Offseason handling | ✅ | ❌ | V1 has special offseason filler |

### Template Engine

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Variable substitution | ✅ | ✅ | Working |
| .next suffix | ✅ | ✅ | Next game context |
| .last suffix | ✅ | ✅ | Previous game context |
| Game info variables | ✅ | ✅ | 8/8 parity |
| Venue variables | ✅ | ✅ | 6/6 parity |
| Record variables | ✅ | ✅ | 8/8 parity |
| Streak variables | ✅ | ✅ | 8/8 parity |
| Betting/odds variables | ✅ | 🔶 | Defined but extraction not complete |
| Ranking variables | ✅ | ✅ | 2/2 parity |
| Broadcast variables | ✅ | ✅ | 7/7 parity |
| Result variables | ✅ | ✅ | 5/5 parity |
| **Conditional descriptions** | ✅ | ✅ | 16 conditions, priority-based selection |
| **Condition presets** | ✅ | ❌ | Reusable condition sets |
| Unresolved var tracking | ✅ | ❌ | V1 tracks missing variables |

**Variable Count:** V1: 227+ | V2: 161 (~71% coverage)

### Stream Matching

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Team name extraction | ✅ | ✅ | Working |
| Fuzzy matching | ✅ | ✅ | rapidfuzz based |
| Multi-league detection | ✅ | ✅ | Working |
| Single-league matching | ✅ | ✅ | Working |
| Fingerprint cache | ✅ | ✅ | SHA256 + 5-gen purge |
| **Tier 1-4 detection** | ✅ | 🔶 | V2 has simplified tiers |
| Abbreviation expansion | ❌ | ✅ | **NEW in V2** |
| Single-event leagues | ❌ | ✅ | **NEW in V2** (UFC) |
| Exception keywords | ✅ | ✅ | Language variants |
| Game indicators | ✅ | 🔶 | vs/@/at detection |
| Regex filtering | ✅ | ❌ | Include/exclude patterns |

**Match Rate:** V1: 82.8% | V2: ~82.8% (equivalent)

### Data Providers

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| ESPN API | ✅ | ✅ | Primary provider |
| TheSportsDB | ❌ | ✅ | **NEW in V2** - fallback provider |
| Retry logic | ✅ | ✅ | Exponential backoff |
| Schedule caching | ✅ | ✅ | 8hr TTL |
| Event caching | ✅ | ✅ | 30min TTL |
| Team stats caching | ✅ | ✅ | 4hr TTL |
| Team info caching | ✅ | ✅ | 24hr TTL |
| **Tiered date caching** | ❌ | ✅ | **NEW in V2** - TTL by date proximity |
| **Rate limiting** | ❌ | ✅ | **NEW in V2** - TSDB 25 req/min |
| H2H caching | ✅ | 🔶 | V2 extraction incomplete |
| Provider stats | ❌ | ✅ | **NEW in V2** - rate limit stats |

### Leagues Supported

| Category | V1 | V2 | Notes |
|----------|----|----|-------|
| NFL | ✅ | ✅ | |
| NBA | ✅ | ✅ | |
| NHL | ✅ | ✅ | |
| MLB | ✅ | ✅ | |
| MLS | ✅ | ✅ | |
| College Football | ✅ | ✅ | |
| College Basketball | ✅ | ✅ | |
| UFC/MMA | ❌ | ✅ | **NEW in V2** |
| Soccer (240+ leagues) | ✅ | 🔶 | V2 has basic support |
| **Soccer multi-league** | ✅ | ❌ | Aggregate all team competitions |
| OHL/WHL/QMJHL | ❌ | ✅ | **NEW in V2** via TSDB |
| NLL/PLL | ❌ | ✅ | **NEW in V2** via TSDB |
| IPL | ❌ | ✅ | **NEW in V2** via TSDB |

### Dispatcharr Integration

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Channel creation | ✅ | ❌ | Full lifecycle in V1 |
| Channel deletion | ✅ | ❌ | Scheduled cleanup |
| Channel sync | ✅ | ❌ | Name/number/group/profile |
| Multi-stream support | ✅ | ❌ | Primary + failover |
| Stream profiles | ✅ | ❌ | |
| Channel profiles | ✅ | ❌ | |
| Logo management | ✅ | ❌ | Create/track logos |
| UUID reconciliation | ✅ | ❌ | Immutable channel IDs |
| Orphan detection | ✅ | ❌ | |
| Duplicate detection | ✅ | ❌ | |
| Drift detection | ✅ | ❌ | |
| M3U account refresh | ✅ | ❌ | |
| EPG refresh trigger | ✅ | ❌ | |
| **Channel lifecycle skeleton** | ❌ | ✅ | V2 has timing logic ready |

### Event EPG Groups

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Group management | ✅ | ❌ | CRUD for M3U groups |
| Parent/child groups | ✅ | ❌ | Consolidate providers |
| Duplicate handling | ✅ | ❌ | ignore/consolidate/separate |
| Exception keywords | ✅ | ❌ | Per-group overrides |
| Regex filtering | ✅ | ❌ | Include/exclude patterns |
| Stream refresh | ✅ | ❌ | Batch stream fetching |
| SSE progress | ✅ | ❌ | Real-time updates |

### Database & API

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| SQLite | ✅ | ✅ | Same storage |
| Migrations | ✅ (18) | ❌ | V2 uses single schema |
| Teams CRUD | ✅ | ✅ | |
| Templates CRUD | ✅ | ✅ | |
| Settings CRUD | ✅ | 🔶 | Basic in V2 |
| REST API | ✅ | ✅ | FastAPI in V2 (better) |
| Swagger docs | ✅ | ✅ | |
| SSE streaming | ✅ | ❌ | Progress updates |
| Web UI | ✅ | ❌ | Flask templates |
| Statistics API | ✅ | ❌ | EPG stats, history |
| Debug API | ✅ | ❌ | Failed/matched streams |

### System Features

| Feature | V1 | V2 | Notes |
|---------|----|----|-------|
| Logging | ✅ | ✅ | V2 has centralized + JSON |
| Scheduled generation | ✅ | ❌ | Cron + hourly/daily |
| Background scheduler | ✅ | ❌ | Separate thread |
| Parallel processing | ✅ | ❌ | ThreadPoolExecutor |
| Thread safety | ✅ | 🔶 | Basic in V2 |
| Notifications | ✅ | ❌ | V1 has notification system |
| Error tracking | ✅ | 🔶 | V2 has logging only |

---

## V2 New Features (Not in V1)

| Feature | Description |
|---------|-------------|
| **UFC/MMA Support** | Event-based with prelim/main card time detection |
| **TheSportsDB Provider** | Fallback for leagues ESPN doesn't cover |
| **Tiered Date Caching** | Smart TTL based on date proximity (today=30min, 8+days=24hr) |
| **Rate Limiting** | Preemptive + reactive handling for TSDB API |
| **Provider Stats** | Real-time rate limit statistics |
| **Single-Event Leagues** | Simplified matching for UFC/similar |
| **Abbreviation Expansion** | FN→Fight Night, etc. |
| **Provider Agnostic** | Clean abstraction for multiple data sources |
| **FastAPI** | Modern async REST API (better than Flask) |
| **Centralized Logging** | File + console, rotating, JSON option |
| **Better Test Structure** | Separate test files per module |

---

## Migration Priority

### Phase 1: Core Feature Parity (Current)
1. ✅ Team-based EPG
2. ✅ Event-based EPG
3. ✅ Filler generation
4. ✅ Template engine (161 vars)
5. ✅ Stream matching
6. ✅ Fingerprint cache
7. ✅ UFC support (new)
8. ✅ TheSportsDB (new)

### Phase 2: Missing Critical Features
1. ✅ **Conditional descriptions** - 16 conditions, priority-based selection
2. ❌ **Soccer multi-league** - Aggregate all team competitions
3. ❌ **Odds/H2H extraction** - Use ESPN event summary data
4. ❌ Multi-period filler (pregame/postgame windows)
5. ❌ Unresolved variable tracking

### Phase 3: Dispatcharr Integration
1. ❌ Channel creation
2. ❌ Channel deletion (scheduled)
3. ❌ Stream management
4. ❌ UUID reconciliation
5. ❌ Orphan/duplicate detection

### Phase 4: Advanced Features
1. ❌ Event EPG groups (parent/child)
2. ❌ Background scheduler
3. ❌ Parallel processing
4. ❌ Statistics/debugging APIs
5. ❌ Web UI

### Phase 5: Polish
1. ❌ Database migrations
2. ❌ Regex filtering
3. ❌ Notifications
4. ❌ Full SSE support

---

## Code Size Analysis

### V1 File Breakdown (Largest First)
```
app.py               7,836 lines  - Flask routes + EPG orchestration
database/__init__.py 5,131 lines  - All DB functions + migrations
channel_lifecycle.py 3,127 lines  - Dispatcharr channel management
orchestrator.py      2,463 lines  - Team EPG generation
league_detector.py   2,548 lines  - Multi-sport detection
dispatcharr_client.py 2,282 lines - Dispatcharr API
team_matcher.py      1,997 lines  - Stream → team extraction
espn_client.py       1,377 lines  - ESPN API wrapper
template_engine.py   1,374 lines  - Variable substitution
multi_sport_matcher.py 1,185 lines - Multi-league matching
event_epg_generator.py 1,158 lines - Event-based EPG
event_matcher.py     1,137 lines  - ESPN event lookup
schema.sql           1,156 lines  - 32 tables
```

### V2 File Breakdown (Largest First)
```
providers/espn/provider.py  563 lines  - ESPN provider
providers/tsdb/client.py    484 lines  - TSDB client + caching
services/sports_data.py     440 lines  - Service layer
providers/tsdb/provider.py  398 lines  - TSDB provider
providers/espn/client.py    355 lines  - ESPN client
templates/variables/conference.py 355 lines - Conference vars
templates/variables/records.py    285 lines - Record vars
consumers/filler/generator.py     288 lines - Filler generation
consumers/multi_league_matcher.py 287 lines - Multi-league
templates/variables/h2h.py        223 lines - H2H vars
templates/variables/identity.py   222 lines - Identity vars
schema.sql                        408 lines - ~12 tables
```

### Architecture Comparison

**V1 Architecture:**
```
app.py (monolith) → espn_client.py → orchestrator.py → template_engine.py → XMLTV
                 → dispatcharr_client.py → channel_lifecycle.py
```

**V2 Architecture:**
```
API (FastAPI)
    ↓
Service Layer (SportsDataService)
    ↓
Providers (ESPN, TSDB) - Normalized dataclasses
    ↓
Consumers (Team EPG, Event EPG, Filler)
    ↓
Templates (161 variables)
    ↓
XMLTV Output
```

**V2 is more modular:**
- 75 files vs 33 files
- Smaller files (avg 150 lines vs 1,200 lines)
- Clear separation of concerns
- Provider-agnostic design
- Type-driven with dataclasses

---

## Database Schema Comparison

### Tables in V1 but NOT in V2 (20 tables)
```
- h2h_cache                    (H2H data caching)
- condition_presets            (Reusable conditions)
- team_aliases                 (Custom team names)
- managed_channels             (Dispatcharr channels)
- managed_channel_streams      (Multi-stream support)
- managed_channel_history      (Audit trail)
- soccer_team_leagues          (Soccer team→leagues)
- soccer_leagues_cache         (Soccer league metadata)
- soccer_cache_meta            (Cache status)
- team_league_cache            (Non-soccer team→league)
- team_league_cache_meta       (Cache status)
- epg_failed_matches           (Debug: failed matches)
- epg_matched_streams          (Debug: successful matches)
- consolidation_exception_keywords
- event_epg_groups             (M3U provider groups)
- league_config                (League metadata)
- league_id_aliases            (Friendly league names)
- epg_history                  (Generation audit trail)
- error_log                    (Error tracking)
- Views (4)                    (active_teams, etc.)
```

### Tables in V2 (12 tables)
```
- teams                        (Team identity)
- templates                    (EPG templates)
- settings                     (Global settings)
- schedule_cache               (ESPN schedule cache)
- team_stats_cache             (Team stats cache)
- stream_match_cache           (Fingerprint cache)
- league_provider_mappings     (League→provider routing)
- + 5 additional configuration tables
```

---

## Estimated Effort to Full Parity

| Phase | Features | Estimate |
|-------|----------|----------|
| Phase 2 | Conditional descriptions, Soccer multi-league, H2H extraction | Medium |
| Phase 3 | Dispatcharr integration | Large |
| Phase 4 | Event groups, Scheduler, Parallel processing | Large |
| Phase 5 | Migrations, UI, Polish | Medium |

**Current Status: ~70% feature parity**
- Core EPG generation: 100%
- Template engine: 71% (vars) / 100% (conditions)
- Stream matching: 90%
- Providers: 100% (actually better with TSDB)
- Dispatcharr: 0%
- System features: 40%

---

## Recommendations

1. **Don't Port Web UI** - Consider headless API-only approach or separate frontend
2. **Skip Migrations** - V2 fresh schema is cleaner, migrations add complexity
3. **Prioritize Soccer Multi-League** - Key for soccer team channels
4. **Defer Dispatcharr** - Can run V2 alongside V1 for channel management
5. **Consider Async** - V2 FastAPI could benefit from async providers
6. **Keep V1 Running** - Run both until V2 reaches full parity

---

## Files to Reference When Porting

| V1 File | Purpose | Port to V2 |
|---------|---------|------------|
| `orchestrator.py:800-1200` | Multi-period filler | `consumers/filler/` |
| `template_engine.py:400-600` | Conditional descriptions | ✅ `templates/conditions.py` |
| `channel_lifecycle.py` | Full Dispatcharr integration | New module needed |
| `soccer_multi_league.py` | Multi-league aggregation | New module needed |
| `league_detector.py` | Tier 1-4 detection | Enhance existing matchers |
| `app.py:2000-3000` | Event EPG groups | New API routes needed |
