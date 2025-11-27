# Teamarr Bootstrap Prompt

Use this to quickly get a new Claude session up to speed.

---

## Quick Context

**Teamarr** is a Flask-based web app that generates XMLTV EPG data for sports channels. It integrates with:
- **ESPN API** - fetches team schedules and event data
- **Dispatcharr** - IPTV channel/stream management system

**Two EPG modes:**
1. **Team-based** - One team per channel, generates pregame/game/postgame/filler programs
2. **Event-based** - Parses stream names like "Panthers @ 49ers" to find matchups from Dispatcharr groups

**Server:** Python/Flask on port 9195

**Branch:** `dev-withevents`

---

## Running the App

```bash
cd /srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr
python3 app.py
```

---

## Navigation Flow

Dashboard -> Templates -> Teams -> Events -> EPG -> Channels -> Settings

- **Dashboard**: Overview stats, quick links to each section
- **Templates**: EPG formatting (title, description, filler). Two types: "team" and "event"
- **Teams**: Add teams with assigned templates for team-based EPG
- **Events**: Import Dispatcharr channel groups, assign templates, configure lifecycle
- **EPG**: Generate all EPG (teams + events), view/download XML
- **Channels**: Managed channels created by Teamarr (lifecycle management)
- **Settings**: Dispatcharr URL, timezone, schedule, team aliases

---

## Key Architecture: Single Source of Truth

**`generate_all_epg()` is the AUTHORITATIVE function for ALL EPG generation.**

```
generate_all_epg(progress_callback, team_progress_callback, settings, save_history)
├── Phase 1: Team-based EPG → epg_orchestrator.generate_epg()
├── Phase 2: Event-based EPG → refresh_event_group_core() for each group
├── Phase 3: Channel Lifecycle → process_scheduled_deletions()
├── Phase 4: History & Stats → save_epg_generation_stats() (single source of truth)
└── Phase 5: Dispatcharr Refresh → EPGManager.refresh() if configured
```

**All entry points use this function:**
- UI "Generate EPG" → `/generate/stream` (SSE) → `generate_all_epg()`
- Scheduler → `run_scheduled_generation()` → `generate_all_epg()`
- API → `/generate` POST → `generate_all_epg()`

---

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | Main Flask app, `generate_all_epg()`, `refresh_event_group_core()`, all routes |
| `epg/orchestrator.py` | Team-based EPG orchestration |
| `epg/event_epg_generator.py` | Generates XMLTV for event-based streams |
| `epg/team_matcher.py` | Extracts team names from stream names |
| `epg/event_matcher.py` | Finds ESPN events for detected team matchups |
| `epg/epg_consolidator.py` | Merges team + event EPGs into final output |
| `epg/channel_lifecycle.py` | Channel creation/deletion scheduling |
| `api/dispatcharr_client.py` | Dispatcharr API client (M3U + ChannelManager + EPGManager) |
| `database/__init__.py` | `save_epg_generation_stats()` - history saving |

---

## Core Functions in app.py

```python
# AUTHORITATIVE EPG generation function - ALL paths use this
generate_all_epg(progress_callback=None, settings=None, save_history=True, team_progress_callback=None)

# Refresh a single event group (M3U, matching, EPG gen, channels)
refresh_event_group_core(group, m3u_manager, wait_for_m3u=True)
```

---

## Channel Lifecycle System

Teamarr creates/deletes channels in Dispatcharr based on event timing.

**Per-group settings:**
- `channel_start` - Starting channel number for this group
- `channel_create_timing` - When to create (day_of, day_before, 2_days_before, week_before)
- `channel_delete_timing` - When to delete (stream_removed, end_of_day, end_of_next_day, manual)

**Key lifecycle methods in `epg/channel_lifecycle.py`:**
- `sync_group_settings(group)` - Ensures setting changes are honored (runs every EPG gen)
- `update_existing_channels(streams, group)` - Updates delete times with fresh event data
- `process_matched_streams(streams, group, template)` - Creates new channels
- `process_scheduled_deletions()` - Deletes channels past their scheduled time

**Sport durations (for calculating event end times):**
- Football: 4h, Basketball: 3h, Hockey: 3h, Baseball: 4h, Soccer: 2.5h

---

## SSE Progress Streaming

The `/generate/stream` endpoint uses Server-Sent Events:

```javascript
// Frontend expects these status values:
{ status: 'starting', message: '...', percent: 0 }
{ status: 'progress', message: '...', percent: N, team_name: '...', current: N, total: N }
{ status: 'progress', message: '...', percent: N, group_name: '...', current: N, total: N }
{ status: 'complete', message: '...', percent: 100, total_programmes: N }
{ status: 'error', message: '...' }
```

---

## Database Tables

- `templates` - EPG formatting templates (team or event type)
- `teams` - User's tracked teams with template assignment
- `event_epg_groups` - Imported Dispatcharr groups with lifecycle settings
- `managed_channels` - Channels created by Teamarr (tracks scheduled_delete_at)
- `team_aliases` - User-defined aliases for team name matching
- `epg_history` - Generation history (single source of truth for stats)

---

## EPG Output Files

All in `/app/data/` (or `./data/` when running locally):
- `teams.xml` - Team-based EPG
- `events.xml` - Merged event-based EPG (all groups)
- `teamarr.xml` - Final consolidated EPG (teams + events)

---

## Common Tasks

**Add new template variable:**
1. Add to `config/variables.json`
2. Implement in `epg/event_template_engine.py` (event) or `epg/template_engine.py` (team)
3. Add UI help text in `templates/template_form.html`

**Debug EPG generation:**
1. Check logs for progress messages
2. Query `epg_history` table for stats
3. Use `/api/epg-stats` endpoint for real-time EPG analysis

**Debug stream matching:**
1. Use "Test" button on Events page
2. Check logs for `team_matcher` and `event_matcher` debug messages
3. Add team aliases in Settings if names don't match

**Fix lifecycle issues:**
1. Check Channels tab for scheduled delete times
2. Verify group settings (delete_timing) in Events edit modal
3. Check `managed_channels` table for `scheduled_delete_at` values
4. Run EPG generation to trigger `sync_group_settings()`

---

## Recent Decisions

- **Single Source of Truth** - `generate_all_epg()` is the ONLY EPG generation function
- **History Saving** - Only saved in `generate_all_epg()`, never duplicated elsewhere
- **SSE Streaming** - `/generate/stream` uses callbacks into `generate_all_epg()`
- **Settings sync** - Every EPG generation syncs all channels with current group settings
- **Delete timing** - Uses actual sport durations, deletes at 23:59 of event END day

---

*Read CLAUDE.md for complete technical documentation.*
