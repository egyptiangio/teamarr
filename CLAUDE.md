# Teamarr V2 - Sports EPG Generator

> **Breaking Change**: Fresh V2 rewrite - no backward compatibility with V1

## Quick Start

```bash
python3 app.py              # Start server (port 9195)
open http://localhost:9195/docs  # Swagger API docs
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
├── templates/              # Template engine (161 variables)
│   ├── resolver.py         # Variable substitution
│   ├── context.py          # TemplateContext
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
- Team-based EPG generation
- Event-based EPG generation
- Template engine (161 variables)
- Stream matching (single/multi-league)
- Stream match fingerprint cache
- Dispatcharr integration modules
- Database schema and CRUD
- FastAPI REST API

## What's Missing (To Build)

- **UI**: No frontend (will build fresh)
- **Full Dispatcharr integration**: Channel lifecycle not wired up
- **Scheduler**: Background EPG generation
- **Full API coverage**: Some endpoints need implementation
- **Tests**: Need expansion

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
| `league_provider_mappings` | League → provider routing |
| `stream_match_cache` | Fingerprint cache |
| `team_cache` | Team → league lookup |
| `league_cache` | League metadata |

---

## API Endpoints

```
GET  /health                    # Health check
GET  /api/v1/teams              # List teams
POST /api/v1/teams              # Create team
GET  /api/v1/templates          # List templates
POST /api/v1/epg/generate       # Generate EPG
GET  /api/v1/cache/stats        # Cache statistics
POST /api/v1/cache/clear        # Clear cache
GET  /api/v1/matching/preview   # Preview matches
```

Full docs: http://localhost:9195/docs
