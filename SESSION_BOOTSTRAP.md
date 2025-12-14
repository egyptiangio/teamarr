# Session Bootstrap - Teamarr V2 Provider Migration

**Last Updated:** December 14, 2025

## What We're Building

Teamarr V2 is migrating the V1 codebase to use a clean provider abstraction layer with native dataclasses. The goal is:

1. **Provider abstraction**: Data providers (ESPN, TheSportsDB) behind a unified interface
2. **Native dataclasses**: Replace dict-based data flow with typed dataclasses
3. **Service layer**: Caching, routing, provider selection abstracted from consumers
4. **Preserve V1 UI**: Keep the working UI while refactoring internals

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        V1 UI (Flask)                            │
│                    (Preserved, working)                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   EPG Orchestrator                              │
│           (MIGRATED to use service layer)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  ESPNClient     │  │ SportsDataService│ │  templates_v2   │
│  (Legacy - 9    │  │ (Primary source)│  │  (Ready)        │
│   calls remain) │  │                 │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌────────────┐     ┌────────────┐     ┌────────────┐
    │ESPNProvider│     │TSDBProvider│     │  (Future)  │
    │ Priority:0 │     │Priority:100│     │            │
    └────────────┘     └────────────┘     └────────────┘
```

## Migration Progress

### Completed This Session

1. **`_get_team_info_via_service()`** - Team info via provider layer
2. **`_get_team_stats_via_service()`** - Team stats via provider layer
3. **`_get_events_cached()`** - Events for date with caching (dataclass)
4. **`_get_schedule_data_via_service()`** - Schedule in raw format (bridge)
5. **`_event_to_raw_dict()`** - Event dataclass → raw ESPN format
6. **`_fetch_soccer_multi_league_schedules_v2()`** - Soccer events as dataclasses
7. **`_calculate_h2h_from_events()`** - H2H from Event dataclasses
8. **`_calculate_streaks_from_events()`** - Streaks from Event dataclasses
9. **`_calculate_epg_start_time()`** - Now uses service layer
10. **`_process_team_schedule()`** - Now uses service layer for schedules

### Remaining Legacy ESPN Calls (9 total)

```
Line 77:   get_scoreboard() - in _get_scoreboard_cached (legacy method)
Line 961:  parse_schedule_events() - in enrichment code
Line 1270: get_team_schedule() - in _fetch_soccer_multi_league_schedules (old)
Line 1279: get_team_schedule() - in _fetch_soccer_multi_league_schedules (old)
Line 1296: parse_schedule_events() - in old soccer method
Line 1310: parse_schedule_events() - in old soccer method
Line 1488: _parse_event() - in scoreboard enrichment
Line 1601: parse_schedule_events() - in enrichment
Line 2661: get_team_roster() - roster data
```

### Bridge Methods Added

| Method | Input | Output | Purpose |
|--------|-------|--------|---------|
| `_event_to_dict()` | Event | dict (normalized) | For template engine |
| `_event_to_raw_dict()` | Event | dict (ESPN format) | For h2h/streaks legacy |
| `_team_to_dict()` | Team | dict | For template engine |
| `_stats_to_dict()` | TeamStats | dict | For template engine |
| `_filter_events_by_cutoff()` | List[Event] | List[Event] | Date filtering |

## Key Files Modified

```
epg/orchestrator.py  - Main migration target
├── Bridge methods added (lines 90-400)
├── _calculate_epg_start_time() migrated (line 945)
├── _process_team_schedule() migrated (lines 1044-1095)
└── _fetch_soccer_multi_league_schedules_v2() added (line 1307)
```

## Test Commands

```bash
# Activate venv
source .venv/bin/activate

# Test service layer
python -c "
from epg.orchestrator import EPGOrchestrator
from datetime import date

orch = EPGOrchestrator()
events = orch._get_events_cached('nfl', date.today())
print(f'NFL events: {len(events)}')
print(f'Type: {type(events[0]).__name__ if events else None}')
"

# Test EPG generation
python -c "
from epg.orchestrator import EPGOrchestrator
from datetime import datetime
from zoneinfo import ZoneInfo

orch = EPGOrchestrator()
teams = orch._get_teams_with_templates()
if teams:
    team = teams[0]
    settings = orch._get_settings()
    epg_tz = ZoneInfo('America/Detroit')
    epg_start = datetime.now(epg_tz).replace(hour=0, minute=0, second=0)
    events = orch._process_team_schedule(team, 3, 'America/Detroit', epg_start, settings)
    print(f'Generated {len(events)} events/filler')
"

# Run app on test port
python app.py --port 9199
```

## Next Steps

1. **Update enrichment methods** to use `_get_events_cached()` instead of `_get_scoreboard_cached()`
2. **Update `_process_event()`** to work with Event dataclasses
3. **Update `_generate_filler_entries()`** to work with Event dataclasses
4. **Remove old `_fetch_soccer_multi_league_schedules()`** once V2 is stable
5. **Integrate templates_v2** for full dataclass template support
6. **Fix 'No league config found' warnings**

## Database Notes

- **Production V1**: `/mnt/docker/stacks/teamarr/data/teamarr.db` (port 9195)
- **Development V2**: `./data/teamarr.db` (port 9199)
- **Schema**: `stream_match_cache.fingerprint` is PRIMARY KEY
- **Added**: `league_provider_mappings` table for provider routing

## Known Issues

- "No league config found for football/nfl" warnings (cosmetic, doesn't affect functionality)
- Some enrichment code still uses legacy ESPN client (9 calls remaining)
