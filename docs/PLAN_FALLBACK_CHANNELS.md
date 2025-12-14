# Feature Plan: Fallback Channels for Unmatched Streams

## Overview

When a stream **passes filters** (has game indicator, matches include regex, doesn't match exclude regex) but **doesn't match an ESPN event** (no game found, outside lookahead, etc.), optionally create a channel anyway using a user-selected "dummy EPG" from Dispatcharr.

**Related Issue**: #13

## Problem Statement

Users with M3U providers have streams that:
1. Pass all filtering criteria (have vs/@/at, match regex patterns)
2. Successfully parse team names
3. But don't match any ESPN event (D2/D3 schools, international teams, scheduling gaps)

Currently these streams are silently discarded. Users want the option to create channels anyway with placeholder/dummy EPG data.

## Streams Eligible for Fallback

| Reason | Currently | With Fallback |
|--------|-----------|---------------|
| `NO_GAME_FOUND` | Discarded | → Fallback channel |
| `OUTSIDE_LOOKAHEAD` | Discarded | → Fallback channel |
| `GAME_FINAL_EXCLUDED` | Discarded | → Fallback channel |
| `TEAMS_NOT_PARSED` | Discarded | Discarded (can't name channel) |
| `NO_GAME_INDICATOR` | Excluded | Excluded |
| `EXCLUDE_REGEX_MATCHED` | Excluded | Excluded |

## UI Placement

### Event Group Edit Form (`event_group_form.html`)

New section after "Channel Settings", before "Custom Regex":

```
┌─────────────────────────────────────────────────────────┐
│ ▼ Unmatched Streams                                     │
│   ┌─────────────────────────────────────────────────┐   │
│   │ ☑ Create fallback channels for unmatched streams│   │
│   │                                                 │   │
│   │ Fallback EPG Source: [Dropdown - Dummy EPGs ▼]  │   │
│   │                                                 │   │
│   │ Channel Name Format: [Stream Name ▼]            │   │
│   │   • Stream Name (as-is)                         │   │
│   │   • Parsed Teams (if available)                 │   │
│   │                                                 │   │
│   │ ℹ️ Creates channels using selected EPG source   │   │
│   │    for streams that pass filters but don't      │   │
│   │    match any ESPN event.                        │   │
│   └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Event Groups List (`event_epg.html`)
- Add small badge/icon in the "Template" or "Status" column if fallback is enabled
- Tooltip: "Fallback EPG: {source_name}"

### Preview Modal
- Add "Fallback" tab showing streams that would become fallback channels
- Show: Stream name, parsed teams (if any), reason (no_game_found, etc.)

## Database Changes

```sql
-- event_epg_groups table
ALTER TABLE event_epg_groups ADD COLUMN fallback_enabled INTEGER DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN fallback_epg_source_id INTEGER;
ALTER TABLE event_epg_groups ADD COLUMN fallback_channel_name_format TEXT DEFAULT 'stream_name';

-- managed_channels table
ALTER TABLE managed_channels ADD COLUMN is_fallback INTEGER DEFAULT 0;
-- Make espn_event_id nullable (currently NOT NULL)
```

## Backend Changes

### 1. app.py - `refresh_event_group_core()`

After matching, collect `fallback_candidates` from filtered results:

```python
# After processing match results (around line 641)
fallback_candidates = []
if group.get('fallback_enabled') and group.get('fallback_epg_source_id'):
    eligible_reasons = {
        FilterReason.NO_GAME_FOUND,
        FilterReason.OUTSIDE_LOOKAHEAD,
        FilterReason.GAME_FINAL_EXCLUDED
    }
    for result in results:
        if result['type'] == 'filtered':
            reason = normalize_reason(result.get('reason', ''))
            if reason in eligible_reasons:
                fallback_candidates.append({
                    'stream': result['stream'],
                    'teams': result.get('teams', {}),
                    'reason': reason
                })
```

### 2. channel_lifecycle.py - `process_matched_streams()`

Accept optional `fallback_streams` parameter:

```python
def process_matched_streams(
    self,
    matched_streams: List[Dict],
    group: Dict,
    template: Dict = None,
    fallback_streams: List[Dict] = None  # NEW
):
```

For fallback streams:
- Skip ESPN event validation
- Generate `tvg_id = teamarr-fallback-{channel_number}`
- Use stream name for channel name (or parsed teams if available)
- Set `is_fallback=1` in database
- No scheduled delete (manual cleanup)

### 3. channel_lifecycle.py - `associate_epg_with_channels()`

For fallback channels, use `fallback_epg_source_id` instead of Teamarr EPG source.

### 4. New API Endpoint

```
GET /api/dispatcharr/epg-sources

Response:
{
  "sources": [
    {"id": 21, "name": "Teamarr", "source_type": "xmltv"},
    {"id": 5, "name": "Dummy Sports", "source_type": "dummy"},
    ...
  ]
}
```

## Key Design Decisions

| Decision | Recommendation | Rationale |
|----------|----------------|-----------|
| tvg_id format | `teamarr-fallback-{channel_number}` | Unique, avoids collision with event IDs |
| Channel naming | Stream name by default | Most intuitive, option for parsed teams |
| Cleanup | Manual only (no auto-delete) | User controls fallback channel lifetime |
| Per-group setting | Yes | Different groups may need different fallback sources |
| Include in channel profiles | Yes | Same behavior as matched channels |

## Implementation Checklist

### Database
- [ ] Add `fallback_enabled` column to `event_epg_groups`
- [ ] Add `fallback_epg_source_id` column to `event_epg_groups`
- [ ] Add `fallback_channel_name_format` column to `event_epg_groups`
- [ ] Add `is_fallback` column to `managed_channels`
- [ ] Make `espn_event_id` nullable in `managed_channels`
- [ ] Migration script

### Backend
- [ ] Collect fallback candidates in `refresh_event_group_core()`
- [ ] Pass fallback streams to `process_matched_streams()`
- [ ] Handle fallback channel creation (skip ESPN validation)
- [ ] Generate fallback tvg_id format
- [ ] Store `is_fallback=1` flag
- [ ] Handle EPG association for fallback channels
- [ ] Add `/api/dispatcharr/epg-sources` endpoint

### Frontend
- [ ] Add "Unmatched Streams" section to event group form
- [ ] EPG source dropdown (fetch from API)
- [ ] Channel name format selector
- [ ] Badge/indicator in groups list
- [ ] "Fallback" tab in preview modal

### Testing
- [ ] Fallback stream categorization
- [ ] Channel creation without ESPN event
- [ ] EPG association with fallback source
- [ ] Normal matched behavior unchanged

## Code Locations Reference

| Purpose | File | Function/Lines |
|---------|------|----------------|
| Event matching | `app.py` | `refresh_event_group_core()` lines 628-641 |
| Filter reasons | `utils/filter_reasons.py` | `FilterReason` class |
| Channel creation | `epg/channel_lifecycle.py` | `process_matched_streams()` |
| tvg_id generation | `epg/channel_lifecycle.py` | `generate_event_tvg_id()` |
| EPG source API | `api/dispatcharr_client.py` | `EPGManager.list_sources()` |
| EPG association | `epg/channel_lifecycle.py` | `associate_epg_with_channels()` |
| Event group form | `templates/event_group_form.html` | Full form |
| Event groups list | `templates/event_epg.html` | Group table |

## Future Enhancements

- Per-reason fallback sources (different EPG for "final excluded" vs "no game found")
- Fallback channel statistics in EPG history
- Auto-cleanup option (delete after X days)
- Fallback channel indicator in Managed Channels page
