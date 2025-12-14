# Status: Name-Based Scoreboard Matching Fix

**Date:** 2025-12-10
**Branch:** `dev`
**Commit:** `99e84ce`

---

## ✅ COMPLETE - All Tests Passed

### Results Summary

| Test | Result |
|------|--------|
| Container rebuilt | ✅ |
| NAME-MATCH logs show event names | ✅ |
| "College of Biblical Studies vs Nicholls" matches | ✅ |
| Match rate improved | ✅ **30/38 → 36/38** |
| No `_enrichment` errors | ✅ |

### Verified NAME-MATCH Logs

```
[NAME-MATCH] Found event by team names: 'college of biblical studies' vs 'nicholls' → College Of Biblical Studies Ambassadors at Nicholls Colonels
[NAME-MATCH] Found event by team names: 'old westbury' vs 'hofstra' → Old Westbury Panthers at Hofstra Pride
[NAME-MATCH] Found event by team names: 'saint leo' vs 'florida atlantic' → Saint Leo Monarchs at Florida Atlantic Owls
[NAME-MATCH] Found event by team names: 'fisher college' vs 'umass lowell' → Fisher College Eagles at UMass Lowell River Hawks
[NAME-MATCH] Found event by team names: 'tougaloo' vs 'tulane' → Tougaloo Bulldogs at Tulane Green Wave
[NAME-MATCH] Found event by team names: 'bethesda' vs 'cal state fullerton' → Bethesda University Flames at Cal State Fullerton Titans
```

### Remaining Unmatched (2/38)

One known unmatched stream: "Salish Kootenai College vs Montana" - this game may not exist on ESPN's scoreboard (NAIA team playing D1).

---

## The Problem (Solved)

Single-league event groups (like NCAAM) were showing ~30/38 matched instead of ~36/38. The missing matches were small college teams (NAIA, D2/D3) that aren't in ESPN's `/teams` database, so ID-based matching failed.

## The Solution

Added name-based scoreboard matching as a fallback. When team IDs can't be found, we search the scoreboard by team names instead.

---

## What We Did

### 1. Fixed `categorize_team_matcher_reason()` enum handling
**File:** `utils/match_result.py`

The "Failed Matches" modal was broken because the function called `.lower()` on `FailedReason` enum objects. Added `isinstance()` checks to handle enums properly.

### 2. Created shared `stream_matcher.py` module
**File:** `epg/stream_matcher.py` (NEW)

- New module with `MatchConfig` and `MatchResult` dataclasses
- `match_stream_single_league()` is now the single source of truth for single-league stream matching
- Used by both EPG generation AND test modal for consistency

### 3. Added name-based scoreboard matching
**File:** `epg/event_matcher.py`

- `find_event_by_team_names()` - searches scoreboard by team names instead of IDs
- `find_and_enrich_by_names()` - wrapper that adds enrichment
- This is the fallback for NAIA, D2/D3, small colleges not in ESPN's `/teams` database

### 4. Fixed `_filter_matching_events()` for name-based matching
**File:** `epg/event_matcher.py`

Added `if team2_id is not None and` check so name-based matches (which pass `None`) don't fail the opponent ID filter.

### 5. Fixed `_select_best_match()` return value extraction
**File:** `epg/event_matcher.py`

`_select_best_match()` returns a wrapper dict `{'event': {...}, 'event_date': ..., 'event_id': ...}`. Fixed `find_event_by_team_names()` to extract `best_event['event']` instead of using the wrapper directly.

### 6. Added team aliases
**File:** `epg/team_matcher.py`

- `Albany` → `UAlbany`
- `St Leo` → `Saint Leo`

---

## Files Changed

```
app.py                   | 391 +++++++++++++-------------------------------
database/__init__.py     |  37 ++++-
epg/channel_lifecycle.py | 179 +++++++++++++++++++++-
epg/event_matcher.py     | 193 ++++++++++++++++++++++-
epg/stream_matcher.py    | 356 ++++++++++++++++++++++++++++++++++++++++++ (NEW)
epg/team_matcher.py      |   4 +
utils/match_result.py    |  47 ++++++
```
