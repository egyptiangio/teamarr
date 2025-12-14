# Failed Matches Modal - Implementation Plan

## Overview
Add a "Show Failed Matches" button to the EPG tab that opens a modal displaying all unmatched streams from the last EPG generation. Provides a one-stop shop for debugging match failures.

## UI Design

### Button Location (EPG Tab)
```
[Generate EPG]   [Show Failed Matches (47)]   Last run: 2 min ago
```
- Badge shows total failure count
- Disabled/hidden if no failures recorded

### Modal Layout
```
┌─────────────────────────────────────────────────────────────────────────┐
│  Failed Matches from Last EPG Run                            [X] Close  │
├─────────────────────────────────────────────────────────────────────────┤
│  Generated: 2024-12-07 14:32:05 EST                                     │
│  Total Failed: 47 streams across 3 groups                               │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │ ══════════════════════════════════════════════════════════════════ ││
│  │ USA | ESPN+ (38 failed)                                            ││
│  │ ══════════════════════════════════════════════════════════════════ ││
│  │                                                                     ││
│  │ ESPN+ 21 : South Korea vs. Czechia (Main Round)                    ││
│  │   Reason: NO_LEAGUE_DETECTED                                       ││
│  │   Parsed: team1="South Korea" team2="Czechia"                      ││
│  │   Tier: None (no matching league found)                            ││
│  │                                                                     ││
│  │ ESPN+ 89 : Yale vs. Montana State                                  ││
│  │   Reason: NO_GAME_FOUND                                            ││
│  │   Parsed: team1="Yale" team2="Montana State"                       ││
│  │   Tier: 3c (both teams in cache, no schedule match)                ││
│  │   Leagues checked: college-football, mens-college-basketball       ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  [Copy to Clipboard]                                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Data Captured Per Failure

| Field | Description |
|-------|-------------|
| group_name | Event group name (e.g., "USA \| ESPN+") |
| stream_name | Full stream name |
| stream_id | Dispatcharr stream ID |
| reason | Failure reason code (NO_LEAGUE_DETECTED, NO_TEAMS, etc.) |
| parsed_team1 | Extracted team 1 name (if any) |
| parsed_team2 | Extracted team 2 name (if any) |
| detection_tier | Tier reached before failure (for multi-sport) |
| leagues_checked | Comma-separated list of leagues attempted |
| detail | Additional context (e.g., "teams not in league") |

## Implementation Steps

### 1. Database Migration (migration 24)

```sql
CREATE TABLE epg_failed_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL,  -- Links to epg_history.id or generation counter
    group_id INTEGER NOT NULL,
    group_name TEXT NOT NULL,
    stream_id INTEGER,
    stream_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    parsed_team1 TEXT,
    parsed_team2 TEXT,
    detection_tier TEXT,
    leagues_checked TEXT,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_epg_failed_matches_generation ON epg_failed_matches(generation_id);
```

### 2. Backend Changes

**app.py - refresh_event_group_core()**
- After matching loop, collect failures into list
- Store failures to `epg_failed_matches` table
- Clear previous generation's failures at start of `generate_all_epg()`

**New endpoint: GET /api/epg/failed-matches**
```python
@app.route('/api/epg/failed-matches')
def api_epg_failed_matches():
    """Get failed matches from last EPG generation."""
    # Returns JSON with failures grouped by group_name
    # Includes generation timestamp and total count
```

### 3. Frontend Changes

**templates/index.html**
- Add "Show Failed Matches" button next to Generate EPG
- Badge showing failure count (fetched from API)
- Modal with scrollable textarea (monospace font)
- Copy to Clipboard button

**static/js/epg.js (or inline)**
- Fetch `/api/epg/failed-matches` on button click
- Format failures as plain text
- Populate modal textarea
- Copy button uses `navigator.clipboard.writeText()`

## Failure Reasons Reference

| Reason | Description |
|--------|-------------|
| NO_GAME_INDICATOR | Stream lacks vs/@/at pattern |
| NO_TEAMS | Could not parse team names |
| NO_LEAGUE_DETECTED | Multi-sport: no league indicator or cache hit |
| NO_GAME_FOUND | Teams matched but no event in lookahead window |
| GAME_PAST | Event already completed (when excluding finals) |
| GAME_FINAL_EXCLUDED | Final game excluded by setting |
| LEAGUE_NOT_ENABLED | Found league but not in enabled list |
| UNSUPPORTED_SPORT | Boxing, MMA, beach soccer, futsal |

## Output Format (Copyable Text)

```
Failed Matches from EPG Generation
Generated: 2024-12-07 14:32:05 EST
Total: 47 failures across 3 groups

════════════════════════════════════════════════════════════════
USA | ESPN+ (38 failed)
════════════════════════════════════════════════════════════════

ESPN+ 21 : South Korea vs. Czechia (Main Round)
  Reason: NO_LEAGUE_DETECTED
  Parsed: team1="South Korea" team2="Czechia"
  Tier: None

ESPN+ 89 : Yale vs. Montana State
  Reason: NO_GAME_FOUND
  Parsed: team1="Yale" team2="Montana State"
  Tier: 3c
  Leagues: college-football, mens-college-basketball

════════════════════════════════════════════════════════════════
USA | ESPN Xtra (9 failed)
════════════════════════════════════════════════════════════════

Xtra 12 : Boxing: Smith vs. Jones
  Reason: UNSUPPORTED_SPORT
  Detail: boxing not supported
```

## Future Enhancements

- Filter by group or reason
- Link to test modal for individual stream re-test
- Export as JSON for automated analysis
- Trend tracking (failures over time)
