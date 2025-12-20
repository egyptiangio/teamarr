# Teamarr V2 - Sports EPG Generator

> **Breaking Change**: Fresh V2 rewrite - no backward compatibility with V1

## Quick Start

```bash
source .venv/bin/activate
PORT=9198 python3 app.py    # Dev server (9195 is prod V1)
open http://localhost:9198/docs  # Swagger API docs
```

**Stack**: Python 3.11+, FastAPI, SQLite, httpx

## Git Preferences

- No commit watermarks or co-authored-by
- Commit only, don't push unless asked
- Concise, focused commit messages

---

## V1 Reference

V1 codebase at `../teamarr/` for reference only. Key V1 files:
- `epg/orchestrator.py` - Team EPG + filler generation
- `epg/template_engine.py` - Variable substitution (142 vars)
- `epg/channel_lifecycle.py` - Channel CRUD + Dispatcharr
- `api/espn_client.py` - ESPN API patterns
- `api/dispatcharr_client.py` - Dispatcharr integration

**We use V1 as reference to understand current functionality, then rewrite fresh for V2.**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Layer (FastAPI)                       │
│  teamarr/api/routes/{teams, templates, epg, matching, channels} │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                      Consumer Layer                              │
│  teamarr/consumers/{orchestrator, team_epg, event_epg, ...}     │
│  - EPG generation (team-based, event-based)                     │
│  - Stream matching and caching                                  │
│  - Channel lifecycle management                                 │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                      Service Layer                               │
│  teamarr/services/sports_data.py                                │
│  - Provider routing and fallback                                │
│  - TTL caching (date-aware)                                     │
│  - Unified data access                                          │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                      Provider Layer                              │
│  teamarr/providers/{espn, tsdb}/                                │
│  - SportsProvider ABC implementation                            │
│  - ESPN (primary) + TheSportsDB (fallback)                      │
│  - Provider registry for dynamic routing                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
teamarr/
├── api/                    # FastAPI REST API
│   ├── app.py              # Application factory
│   ├── models.py           # Pydantic models
│   └── routes/             # API endpoints (teams, templates, epg, channels, etc.)
│
├── core/                   # Type definitions (lowest layer)
│   ├── types.py            # Team, Event, Programme, TemplateConfig, etc.
│   ├── interfaces.py       # SportsProvider ABC
│   └── filler_types.py     # FillerConfig, FillerTemplate
│
├── providers/              # Data providers
│   ├── registry.py         # ProviderRegistry
│   ├── espn/               # ESPN provider (primary)
│   └── tsdb/               # TheSportsDB (fallback)
│
├── services/               # Business logic
│   └── sports_data.py      # SportsDataService with TTL caching
│
├── consumers/              # EPG generation (highest layer)
│   ├── orchestrator.py     # Generation coordinator
│   ├── team_epg.py         # Team-based EPG
│   ├── event_epg.py        # Event-based EPG
│   ├── child_processor.py  # Child group stream processing
│   ├── lifecycle/          # Channel lifecycle service
│   ├── enforcement/        # Post-processing enforcers
│   │   ├── keywords.py     # Keyword-based stream placement
│   │   └── cross_group.py  # Cross-group consolidation
│   ├── reconciliation.py   # Channel reconciler
│   ├── scheduler.py        # Background scheduler
│   ├── cache/              # Team/league cache
│   └── filler/             # Pregame/postgame content
│
├── templates/              # Template engine (141 variables)
│   ├── resolver.py         # Variable substitution
│   ├── context.py          # TemplateContext, TeamChannelContext
│   ├── conditions.py       # Conditional description selection
│   └── variables/          # Variable extractors by category
│
├── dispatcharr/            # Dispatcharr integration
│   ├── client.py           # HTTP client with retry
│   ├── auth.py             # JWT token management
│   ├── factory.py          # Connection factory
│   └── managers/           # Channel, EPG, Logo, M3U managers
│
├── database/               # SQLite persistence
│   ├── schema.sql          # Table definitions (15 tables)
│   ├── connection.py       # Connection management
│   ├── channels/           # Managed channel CRUD
│   ├── settings/           # Settings CRUD
│   └── templates.py        # Template CRUD
│
└── utilities/              # Shared utilities
    ├── cache.py            # TTLCache
    ├── fuzzy_match.py      # FuzzyMatchResult, FuzzyMatcher
    └── xmltv.py            # XMLTV generation
```

---

## Core Types

All data flows through typed dataclasses in `core/types.py`:

```python
@dataclass(frozen=True)
class Team:
    id: str
    provider: str  # "espn" or "tsdb"
    name: str
    short_name: str
    abbreviation: str
    league: str
    sport: str
    logo_url: str | None = None

@dataclass
class Event:
    id: str
    provider: str
    name: str
    start_time: datetime
    home_team: Team
    away_team: Team
    status: EventStatus
    league: str
    sport: str

@dataclass
class Programme:
    channel_id: str
    title: str
    start: datetime
    stop: datetime
    description: str | None = None

@dataclass
class TemplateConfig:
    title_format: str = "{away_team} @ {home_team}"
    description_format: str = "{matchup} | {venue_full}"
    subtitle_format: str = "{venue_full}"
    category: str = "Sports"
```

---

## Provider Interface

```python
class SportsProvider(ABC):
    @property
    def name(self) -> str: ...
    def supports_league(self, league: str) -> bool: ...
    def get_events(self, league: str, date: date) -> list[Event]: ...
    def get_team_schedule(self, team_id: str, league: str, days: int) -> list[Event]: ...
    def get_team(self, team_id: str, league: str) -> Team | None: ...
    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None: ...
```

---

## Service Layer Usage

```python
from teamarr.services import create_default_service

service = create_default_service()

# Automatic provider routing
events = service.get_events('nfl', date.today())  # → ESPN
events = service.get_events('ohl', date.today())  # → TSDB
```

---

## What's Complete

**Backend:**
- Provider abstraction (ESPN + TSDB)
- Service layer with TTL caching
- Two-phase data pipeline (discovery → enrichment)
- Team-based EPG generation
- Event-based EPG generation
- Template engine (141 variables, 15 conditionals)
- Stream matching (single/multi-league)
- Stream match fingerprint cache
- Dispatcharr integration (channels, EPG, logos, M3U)
- Channel lifecycle service (create/delete timing)
- Channel reconciliation (orphan/duplicate detection)
- Database schema and CRUD (15 tables)
- FastAPI REST API (102 routes)
- Processing stats tracking
- Exception keywords management
- Condition presets management
- SQL injection protection
- Child/parent group stream handling
- Cross-group stream consolidation
- Keyword enforcement for stream placement

**Code Quality (Dec 19, 2025):**
- Layer isolation verified - no violations
- Unified duplicate classes (`ExceptionKeyword`, `EventTemplateConfig`)
- Renamed confusing duplicates (`FuzzyMatchResult`, `StreamCacheEntry`, `TeamChannelContext`)
- Removed dead code directories

## What's Missing (To Build)

**UI Gaps (vs V1):**

EPG Page missing features:
- Last Generation Summary Bar with clickable matched/failed stats
- Matched Streams Modal (shows all matched streams from last run)
- Failed Matches Modal (shows all failed/unmatched streams)
- EPG Analysis Section:
  - Filler breakdown (pregame/postgame/idle counts)
  - Date range display
  - Detected Issues:
    - Unreplaced template variables (clickable to highlight in XML)
    - Coverage gaps (time gaps between programmes)
- EPG Preview (XML viewer with search, line numbers, word wrap)

Other pages may have similar gaps - need V1 comparison audit

**V1 Features Not Ported (by design):**
- Tiered League Detection (Tier 1-4) - V2 uses explicit league configuration instead
- Soccer Multi-League Cache (team→leagues) - May add if needed for multi-competition soccer

**Testing:**
- Integration tests for newer API endpoints
- E2E tests for full pipeline

---

## V1 vs V2 Architecture Differences

| Feature | V1 Approach | V2 Approach |
|---------|-------------|-------------|
| Multi-Sport Groups | No assigned leagues; tiered detection at runtime | Explicit `leagues[]` array configured upfront |
| Stream Matching | Parse stream → extract teams → find events | Fetch events → generate patterns → match streams |
| League Detection | Tier 1-4 system (indicators, cache lookups) | Direct league configuration per group |
| Soccer Multi-League | Dedicated cache: team → all competitions | Not implemented (explicit league config) |

V2's approach is simpler and more explicit - users configure which leagues to search rather than relying on runtime detection.

---

## Provider Coverage

**ESPN**: NFL, NBA, NHL, MLB, MLS, NCAAF, NCAAM, NCAAW, WNBA, UFC, EPL, La Liga, Bundesliga, Serie A, Ligue 1, Champions League, 200+ soccer leagues

**TSDB**: OHL, WHL, QMJHL, NLL, PLL, IPL, BBL, CPL, T20 Blast, Boxing

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `templates` | EPG templates (title, description, filler) |
| `teams` | Team channel configurations |
| `settings` | Global settings (singleton) |
| `event_epg_groups` | Event-based EPG groups |
| `managed_channels` | Dynamic channels |
| `managed_channel_streams` | Multi-stream per channel |
| `managed_channel_history` | Channel lifecycle audit trail |
| `league_provider_mappings` | League → provider routing |
| `stream_match_cache` | Fingerprint cache |
| `team_cache` | Team → league lookup |
| `league_cache` | League metadata |
| `processing_runs` | EPG generation stats per run |
| `stats_snapshots` | Aggregate stats for dashboards |
| `condition_presets` | Saved condition configurations |
| `consolidation_exception_keywords` | Language/variant keywords |

---

## API Endpoints

```
GET  /health                      # Health check
GET  /api/v1/teams                # List/create teams
GET  /api/v1/templates            # List/create templates
POST /api/v1/epg/generate         # Generate EPG
GET  /api/v1/epg/xmltv            # Get XMLTV output
GET  /api/v1/cache/status         # Cache statistics
POST /api/v1/cache/refresh        # Refresh league/team cache
GET  /api/v1/cache/teams/search   # Search teams
GET  /api/v1/matching/events      # Preview stream matches
GET  /api/v1/groups               # Event EPG groups CRUD
GET  /api/v1/channels             # Managed channels CRUD
GET  /api/v1/settings             # Global settings
GET  /api/v1/stats                # Processing run statistics
GET  /api/v1/stats/history        # Stats history for charts
GET  /api/v1/keywords             # Exception keywords CRUD
GET  /api/v1/presets              # Condition presets CRUD
```

Full docs: http://localhost:9198/docs

---

## Current Status

### UI Progress (Dec 19, 2025)

**Frontend Stack:** React + TypeScript + Vite + Tailwind CSS + TanStack Query

**Pages Built (basic functionality):**
- **Dashboard** - Stats tiles, EPG history, Getting Started guide
- **Settings** - All sections functional
- **Teams** - Search, filters, bulk actions, import
- **Team Import** - League sidebar, team grid, bulk import
- **Templates** - List, CRUD, import/export
- **Template Form** - 5 tabs, variable picker, live preview
- **Event Groups** - List, filters, stats
- **Event Group Import** - M3U accounts, groups, stream preview
- **Event Group Form** - 3-step wizard
- **EPG Management** - Generate/Download/URL cards, run history
- **Channels** - Managed channels list, sync, reconciliation

**UI Gaps vs V1 (needs work):**

| Page | Missing Features |
|------|-----------------|
| EPG | Matched/failed streams modals, EPG analysis (filler breakdown, coverage gaps, unreplaced variables) |
| Dashboard | Rich tooltips with hover tables (partially done) |
| Others | Need detailed V1 comparison |

**Components Built:**
- `RichTooltip` - Hover cards
- `Checkbox` - With onClick support
- `VariablePicker` - Dropdown with search, categories

### Backend Status

**All core endpoints working (102 routes):**
- Teams, Templates, Groups, Channels - Full CRUD
- EPG generation and XMLTV delivery
- Dispatcharr integration (test, M3U, channels, EPG sources)
- Stats and run history
- Cache management (283 leagues, 7270 teams)

### V1 Parity Status

| Component | Backend | UI | Notes |
|-----------|---------|-----|-------|
| Teams | ✅ | ✅ | Full CRUD, bulk import |
| Templates | ✅ | ✅ | 5 tabs, variable picker |
| Event Groups | ✅ | 🟡 | Basic functionality |
| EPG Management | ✅ | 🟡 | Missing analysis features |
| Channels | ✅ | ✅ | Lifecycle, reconciliation |
| Settings | ✅ | ✅ | All sections |
| Dispatcharr | ✅ | ✅ | Integration complete |
| Dashboard | ✅ | 🟡 | Missing rich tooltips |

**Legend:** ✅ Complete | 🟡 Partial | ❌ Missing

---

## Two-Phase Data Pipeline

```
DISCOVERY (batch, cheap)              ENRICHMENT (per-event, ESPN only)
├── Scoreboard: 1 call = N events     └── Summary: odds ~1 week out
├── Schedule: 1 call = full season        - 30min cache
└── 8hr cache                             - TSDB skipped (no value)
```

**ESPN endpoints:**
| Endpoint | Has Odds | Use Case |
|----------|----------|----------|
| Scoreboard | Same-day only | Event EPG discovery |
| Schedule | Never | Team EPG discovery |
| Summary | ~1 week out | Enrichment |

**TSDB quirks:**
- Free API key is `123` (not `3`)
- `eventsday.php` needs league NAME
- `eventsnextleague.php` needs league ID
- `lookupevent.php` works but returns same data as eventsday (skip)
- `lookupteam.php` broken on free tier

---

## Decisions Made

- UI: React + TypeScript + Tailwind CSS
- Dev port: 9198 (9195 is prod V1)
- Deployment: Single container (bundled static files)
- No backward compatibility with V1
- Two-phase: Discovery → Enrichment (ESPN only)
- Simplicity: Removed h2h, player_leaders, home/away streaks
