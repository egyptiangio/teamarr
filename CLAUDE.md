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
│   └── routes/             # API endpoints
│
├── core/                   # Type definitions
│   ├── types.py            # Team, Event, Programme, TeamStats, Venue
│   └── interfaces.py       # SportsProvider ABC
│
├── providers/              # Data providers
│   ├── registry.py         # ProviderRegistry
│   ├── espn/               # ESPN provider (primary)
│   └── tsdb/               # TheSportsDB (fallback)
│
├── services/               # Business logic
│   └── sports_data.py      # SportsDataService
│
├── consumers/              # EPG generation
│   ├── orchestrator.py     # Generation coordinator
│   ├── team_epg.py         # Team-based EPG
│   ├── event_epg.py        # Event-based EPG
│   ├── event_matcher.py    # Stream matching
│   ├── channel_lifecycle.py
│   └── filler/             # Pregame/postgame content
│
├── templates/              # Template engine (141 variables)
│   ├── resolver.py         # Variable substitution
│   ├── context.py          # TemplateContext
│   ├── conditions.py       # Conditional description selection
│   └── variables/          # Variable extractors
│
├── dispatcharr/            # Dispatcharr integration
│   ├── client.py           # HTTP client
│   ├── auth.py             # JWT auth
│   └── managers/           # Channel, EPG, M3U managers
│
├── database/               # SQLite persistence
│   ├── schema.sql          # Table definitions
│   ├── connection.py       # Connection management
│   └── channels.py         # Managed channel CRUD
│
└── utilities/              # Shared utilities
    ├── cache.py            # TTLCache
    ├── fuzzy_match.py      # String matching
    └── xmltv.py            # XMLTV generation
```

---

## Core Types

All data flows through typed dataclasses:

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
    home_score: int | None = None
    away_score: int | None = None
    venue: Venue | None = None

@dataclass
class Programme:
    channel_id: str
    title: str
    start: datetime
    stop: datetime
    description: str | None = None
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

- Provider abstraction (ESPN + TSDB)
- Service layer with TTL caching
- Two-phase data pipeline (discovery → enrichment)
- Team-based EPG generation
- Event-based EPG generation
- Template engine (141 variables, 15 conditionals)
- Stream matching (single/multi-league)
- Stream match fingerprint cache
- Dispatcharr integration modules
- Database schema and CRUD (15 tables)
- FastAPI REST API (84 routes)
- Processing stats tracking
- Exception keywords management
- Condition presets management
- SQL injection protection

## What's Missing (To Build)

- **UI**: No frontend (will build fresh)
- **Full Dispatcharr integration**: Channel lifecycle not fully wired up
- **Integration tests**: New API endpoints need test coverage
- **E2E tests**: Full pipeline testing

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

### Session Summary (Dec 16, 2025)

**Completed this session:**
1. Added processing stats system (`processing_runs` table, stats API)
2. Added exception keywords CRUD (language variant handling)
3. Added condition presets CRUD
4. Added safe_sql module for SQL injection prevention
5. Added XMLTV channel definition support
6. Fixed test return value warnings (proper pytest assertions)
7. Codebase audit and cleanup

**Previous session (Dec 15):**
1. Simplified variable system (removed h2h, player_leaders, home/away streaks)
2. Consolidated duplicate condition systems (deleted enum-based conditional.py)
3. Fixed TSDB API key (was using old demo key `3`, now uses `123`)
4. Implemented two-phase data pipeline
5. Removed dead variables: `head_coach`, `odds_opponent_spread`

**Variable count: 141**

### Backend Validated

All core endpoints working:
- `/health` - ✅
- `/api/v1/teams` - ✅ CRUD
- `/api/v1/templates` - ✅
- `/api/v1/cache/status` - ✅
- `/api/v1/cache/refresh` - ✅ (283 leagues, 7017 teams)
- `/api/v1/cache/teams/search` - ✅
- `/api/v1/epg/generate` - ✅
- `/api/v1/epg/xmltv` - ✅

### Next: Build UI

React + TypeScript + Tailwind CSS, bundled and served by FastAPI.

Pages needed:
- Dashboard (overview, cache status)
- Teams (list, add, edit team channels)
- Event Groups (event-based EPG)
- Templates (title/description templates)
- Channels (managed channels)
- Settings (global config)

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
