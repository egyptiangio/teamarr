---
title: Detection Keyword Service
parent: Architecture
grand_parent: Technical Reference
nav_order: 6
---

# Detection Keyword Service

The `DetectionKeywordService` provides centralized pattern-based detection for stream classification. This service abstracts the source of detection patterns, enabling future database-backed customization.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        API / Consumer Layer                          │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  classifier.py              │  stream_filter.py                      │
│  - classify_stream()        │  - is_placeholder()                    │
│  - is_event_card()          │  - detect_sport_hint()                 │
│  - detect_league_hint()     │  - FilterService                       │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DetectionKeywordService                           │
│  ─────────────────────────────────────────────────────────────────  │
│  Pattern Accessors:                                                  │
│  - get_combat_keywords()       - get_league_hints()                  │
│  - get_sport_hints()           - get_placeholder_patterns()          │
│  - get_card_segment_patterns() - get_exclusion_patterns()            │
│  - get_separators()                                                  │
│  ─────────────────────────────────────────────────────────────────  │
│  Detection Methods:                                                  │
│  - is_combat_sport(text)       - detect_league(text)                 │
│  - detect_sport(text)          - is_placeholder(text)                │
│  - detect_card_segment(text)   - is_excluded(text)                   │
│  - find_separator(text)                                              │
│  ─────────────────────────────────────────────────────────────────  │
│  Cache Management:                                                   │
│  - invalidate_cache()          - warm_cache()                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: constants.py          │  Phase 2: Database (future)        │
│  - COMBAT_SPORTS_KEYWORDS       │  - detection_keywords table         │
│  - LEAGUE_HINT_PATTERNS         │  - User-defined patterns            │
│  - PLACEHOLDER_PATTERNS         │  - Runtime customization            │
│  - ...                          │                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Design Principles

### 1. Layer Separation

**Classifier and filter modules should NOT:**
- Import pattern constants directly
- Compile regex patterns themselves
- Have hardcoded detection logic

**Classifier and filter modules SHOULD:**
- Call DetectionKeywordService methods
- Handle orchestration logic only
- Remain unaware of pattern sources

### 2. Pattern Sources

**Phase 1 (Current):** Patterns load from `teamarr/utilities/constants.py`
- COMBAT_SPORTS_KEYWORDS
- LEAGUE_HINT_PATTERNS
- SPORT_HINT_PATTERNS
- PLACEHOLDER_PATTERNS
- CARD_SEGMENT_PATTERNS
- COMBAT_SPORTS_EXCLUDE_PATTERNS
- GAME_SEPARATORS

**Phase 2 (Future):** Patterns load from database with constants as fallback
- `detection_keywords` table
- User-defined custom patterns
- Runtime modification without restart

### 3. Pattern Caching

Patterns are compiled once and cached at class level:
- Lazy initialization on first access
- No recompilation overhead
- `invalidate_cache()` for testing or DB updates

### 4. Word Boundary Matching

Combat sports keywords use word boundary matching (`\b`) to avoid false positives:
- "wbo" matches "WBO Championship" but NOT "Cowboys"
- "pbc" matches "PBC Boxing" but NOT embedded substrings

## Stream Classification Flow

```
Stream Name
     │
     ▼
┌─────────────────────┐
│ 1. Placeholder?     │──Yes──▶ Skip (no event info)
└─────────────────────┘
     │ No
     ▼
┌─────────────────────┐
│ 2. Combat Sports?   │──Yes──▶ EVENT_CARD category
└─────────────────────┘         (UFC, Boxing, MMA)
     │ No
     ▼
┌─────────────────────┐
│ 3. Has Separator?   │──Yes──▶ TEAM_VS_TEAM category
└─────────────────────┘         (NFL, NBA, Soccer)
     │ No
     ▼
    Fallback logic

Note: skip_builtin_filter bypasses steps 1-2 in stream_filter.py
```

## skip_builtin_filter Option

Groups can set `skip_builtin_filter=True` to bypass built-in filtering:
- Placeholder detection skipped
- Unsupported sport detection skipped
- Custom regex still applies

This allows users to match streams that would normally be filtered (e.g., individual sports like golf or tennis that Teamarr can't schedule-match but user wants in EPG).

## Usage Examples

```python
from teamarr.services.detection_keywords import DetectionKeywordService

# Check if stream is combat sports
if DetectionKeywordService.is_combat_sport("UFC 315: Main Card"):
    # Handle EVENT_CARD classification

# Detect league from stream name
league = DetectionKeywordService.detect_league("NFL: Cowboys vs Eagles")
# Returns: "nfl"

# Umbrella brands return lists
league = DetectionKeywordService.detect_league("EFL: Team A vs Team B")
# Returns: ["eng.2", "eng.3", "eng.4", "eng.fa"]

# Pre-warm cache on startup
stats = DetectionKeywordService.warm_cache()
# Returns: {'combat_keywords': 45, 'league_hints': 59, ...}
```

## File Locations

| Component | Location |
|-----------|----------|
| Service | `teamarr/services/detection_keywords.py` |
| Classifier | `teamarr/consumers/matching/classifier.py` |
| Stream Filter | `teamarr/services/stream_filter.py` |
| Constants | `teamarr/utilities/constants.py` |
| Future DB Table | `teamarr/database/schema.sql` (detection_keywords) |
