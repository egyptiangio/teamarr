# Teamarr v2 - Dynamic EPG Generator for Sports

## Quick Reference

**Stack**: Python/Flask, SQLite, Jinja2/vanilla JS, ESPN/TSDB APIs, Dispatcharr API
**Server**: `python3 app.py` (port 9195)
**EPG Entry**: `generate_all_epg()` in app.py

## Git Preferences

- No watermarks in commits (no "Generated with Claude Code" or "Co-Authored-By")
- Commit only - don't push unless asked
- Concise commit messages

---

## Migration Status

We are migrating from V1 (monolithic) to V2 (provider abstraction). Current state:

### ✅ Migrated to V2

| Component | Status | Notes |
|-----------|--------|-------|
| **Provider Layer** | ✅ Working | ESPNProvider + TSDBProvider registered |
| **Service Layer** | ✅ Working | SportsDataService with TTL caching |
| **Core Types** | ✅ Working | Event, Team, TeamStats dataclasses |
| **Team-Based EPG** | ✅ Working | orchestrator.py uses service layer |

**Note**: Orchestrator has bridge methods (`_event_to_dict`, `_team_to_dict`) that convert dataclasses back to dicts because template_engine.py still uses dict access. Full dataclass migration requires updating template engine.

### ❌ Still Using Old V1 Client

| Component | File | Needs Migration |
|-----------|------|-----------------|
| Event Matcher | `epg/event_matcher.py` | Uses old ESPNClient |
| Event Enricher | `epg/event_enricher.py` | Uses old ESPNClient |
| Event EPG Generator | `epg/event_epg_generator.py` | Uses old ESPNClient |
| League Detector | `epg/league_detector.py` | Uses old ESPNClient |
| Team Matcher | `epg/team_matcher.py` | Uses old ESPNClient |
| Team League Cache | `epg/team_league_cache.py` | Uses old ESPNClient |
| Stream Match Cache | `epg/stream_match_cache.py` | Uses old ESPNClient |
| App Routes | `app.py` | Some routes use old ESPNClient |

---

## Architecture

### V2 Provider Layer (USE THIS)

```
core/                     # Types + interfaces
├── types.py              # Team, Event, TeamStats dataclasses
└── interfaces.py         # SportsProvider ABC

providers/                # Multi-provider support
├── __init__.py           # Provider registration
├── registry.py           # ProviderRegistry
├── espn/                 # ESPN (priority 0 - primary)
│   ├── client.py         # HTTP client (httpx)
│   └── provider.py       # ESPNProvider
└── tsdb/                 # TheSportsDB (priority 100 - fallback)
    ├── client.py         # HTTP client with rate limiting
    └── provider.py       # TSDBProvider

services/                 # Service layer
└── sports_data.py        # SportsDataService (routing + caching)
```

**Usage:**
```python
from services import create_default_service

service = create_default_service()
events = service.get_events('nfl', date.today())  # → ESPN
events = service.get_events('ohl', date.today())  # → TSDB (auto-routed)
```

**Provider Routing:**
- ESPN: NFL, NBA, NHL, MLB, MLS, UFC, 200+ soccer leagues
- TSDB: OHL, WHL, QMJHL, NLL, PLL, Cricket, Boxing

### V1 Modules (Being Migrated)

```
app.py                    # Flask app (6700+ lines)
api/espn_client.py        # OLD monolithic ESPN client (DO NOT USE for new code)
api/dispatcharr_client.py # Dispatcharr integration

epg/
├── orchestrator.py       # Team-based EPG (✅ uses V2 service)
├── event_matcher.py      # Event matching (❌ uses V1 client)
├── event_enricher.py     # Event enrichment (❌ uses V1 client)
├── event_epg_generator.py # Event-based XMLTV (❌ uses V1 client)
├── team_matcher.py       # Team extraction (❌ uses V1 client)
├── team_league_cache.py  # Team→league cache (❌ uses V1 client)
└── ...
```

---

## EPG Generation Flow

```
generate_all_epg()
├── Phase 1: Team-based EPG → teams.xml (✅ V2)
│   └── orchestrator.py → service → provider → dataclasses
├── Phase 2: Event-based EPG → event_epg_*.xml (❌ V1)
│   └── event_matcher.py → old ESPNClient → raw dicts
├── Phase 3: Channel Lifecycle
├── Phase 4: Stats → epg_history
├── Phase 5: Dispatcharr Refresh
└── Phase 6: EPG Association
```

---

## Key Migration Tasks

1. **event_matcher.py** - Replace ESPNClient with service.get_events()
2. **event_enricher.py** - Replace ESPNClient with service.get_team_stats()
3. **team_league_cache.py** - Replace ESPNClient with service.get_league_teams()
4. **template_engine.py** - Update to use dataclass attribute access (`.name` vs `.get('name')`)
5. **Remove dict bridges** - Remove `_event_to_dict()`, `_team_to_dict()` from orchestrator
6. **Matching Engine** - Migrate to V2 events→streams approach (currently streams→events)

---

## Database

Schema version: 34 (migrations in `database/__init__.py`)

Key tables:
- `teams` - Team configurations
- `event_epg_groups` - Event group configurations
- `managed_channels` - Channel lifecycle tracking
- `league_config` - League metadata
- `team_league_cache` - Team→league reverse lookup

---

## Testing

```bash
# Activate venv
source .venv/bin/activate

# Test provider abstraction
python3 -c "
from services import create_default_service
from datetime import date
service = create_default_service()
events = service.get_events('nfl', date.today())
print(f'Got {len(events)} NFL events')
"

# Test EPG generation
python3 -c "
from app import generate_all_epg
result = generate_all_epg(save_history=False)
print(f'Success: {result.get(\"success\")}')
"

# Run Flask app
python3 app.py
```

---

## Reference

- V2 clean rewrite (for reference): `archive/v2-clean-rewrite/`
- Original V1 patterns documented in `docs/v1-patterns.md`
