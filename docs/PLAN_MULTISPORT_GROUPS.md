# Multi-Sport Event Groups Implementation Plan

## Overview

Enable event groups to match streams across multiple sports and leagues without requiring a single assigned league. This is essential for groups like ESPN+, ESPN Xtra, and similar multi-sport event feeds.

**Example Groups:**
- `LIVE | ESPN Xtra` - Contains NHL, NCAA Basketball, Soccer, Volleyball, etc.
- `USA | ESPN+` - Mixed sports with event names in stream titles
- Generic event-based provider groups

---

## Current Architecture Limitations

### Current Flow (Single League)
```
Event Group (assigned_league='nfl', assigned_sport='football')
    ↓
Get streams from Dispatcharr group
    ↓
TeamMatcher.extract_teams() with league='nfl'
    ↓
EventMatcher.find_event() with league='nfl'
    ↓
Create channel for matched event
```

### Problems
1. `assigned_league` and `assigned_sport` are required fields (NOT NULL)
2. TeamMatcher loads team data for a single league
3. EventMatcher searches events within a single league
4. No mechanism to detect which league a stream belongs to

---

## Proposed Solution

### Core Concept: Per-Stream League Detection

Instead of group-level league assignment, detect the league for each stream individually.

**Group Mode:** New "Multi-League" option. When selected:
- All supported leagues are enabled automatically
- League detection logic is enforced per stream
- No single league/sport assignment needed

```
Event Group (mode='multi_league')
    ↓
Get streams from Dispatcharr group
    ↓
For each stream:
    ↓
    0. Check for game indicator (vs, v, @, at, x)
       └── No indicator? → Skip (NO_GAME_INDICATOR)
    ↓
    1. Extract teams from stream name (league-agnostic)
    ↓
    2. Extract date/time from stream name (if present)
    ↓
    3. Find candidate leagues (where both teams exist)
    ↓
    4. Disambiguate to single league (Tier A or Tier B)
    ↓
    5. Match event in detected league
    ↓
Create channel for matched event
```

### Step 0: Game Indicator Filter (Prerequisite)

Before any league detection, streams must pass the game indicator check. This is a cheap filter that eliminates non-matchup content early.

**Valid separators:** `vs`, `v`, `@`, `at`, `x`

**Examples - PASS (has indicator):**
```
"NHL: Panthers vs. Lightning"           → "vs." found
"ESPN+ 51 : Predators @ Hurricanes"     → "@" found
"Soccer: Real Madrid at Barcelona"      → "at" found
"NCAA: Duke v Kentucky"                 → "v" found
"Liga MX: America x Guadalajara"        → "x" found
```

**Examples - SKIP (no indicator):**
```
"NHL: Panthers Broadcast"               → No separator → NO_GAME_INDICATOR
"NBA Today: NBA Today"                  → No separator → NO_GAME_INDICATOR
"Soccer: ESPN FC"                       → No separator → NO_GAME_INDICATOR
"The Pulse: Texas A&M Football"         → No separator → NO_GAME_INDICATOR
```

**Why this matters:**
- Filters out studio shows, broadcasts, highlight programs
- Cheap string check before expensive league detection
- Skipped streams don't count against match rate (excluded from denominator)
- Existing behavior preserved from single-league mode

---

## Stream Name Analysis

### Pattern 1: Explicit League Indicator (High Confidence)
Stream names with clear league/sport prefix:

```
"NHL: Panthers Broadcast"
"Soccer: Real Avila vs. Rayo"
"NCAA Men's Basketball: The Citadel vs. Davidson"
"NCAA Women's Basketball: #11 UNC vs. #2 TEX"
"Big East Conference: Butler vs. Seton Hall"
"Volleyball: Wofford vs. #1 Kentucky"
"NBA G League: Wolves vs. Gold"
```

**Detection Strategy:** Regex pattern matching on prefix before `:`

### Pattern 2: Conference Indicator (Medium-High Confidence)
Conference names often indicate the sport context:

```
"Big East Conference: Butler vs. Seton Hall"    → NCAA Basketball
"Southeastern Conference: Utah State vs. #7 Tennessee"  → NCAA Basketball/Football
"Ivy League: Maine vs. Penn"                    → NCAA (context needed)
"Sun Belt Conference: North Florida vs. GA Southern"   → NCAA
```

**Challenge:** Some conferences have multiple sports (SEC has football + basketball)

### Pattern 3: Team Name Only (Lower Confidence)
No league indicator, just team matchup:

```
"ESPN+ 51 : Nashville Predators vs. Florida Panthers"  → NHL (by team lookup)
"ESPN+ 52 : Toronto Maple Leafs vs. Carolina Hurricanes" → NHL (by team lookup)
"Tulsa vs. #5 Miami (First Round)"              → Could be multiple sports
```

**Detection Strategy:** Team-to-league reverse lookup

### Pattern 4: Embedded Date/Time
Additional disambiguation signal:

```
"US (ESPNXtra 051) | NHL: TOR vs. CAR (2025-12-04 18:30:50)"
"ESPN+ 39 : Toledo vs. #4 Indiana (First Round) @ Dec 04 05:30 PM ET"
```

**Use:** Validate against ESPN event time to confirm match

---

## Implementation Components

### 1. LeagueDetector Module (`epg/league_detector.py`)

New module responsible for detecting league from stream name.

```python
class LeagueDetector:
    """
    Detect sport/league from stream name using multiple strategies.

    Strategies (in priority order):
    1. Explicit prefix matching (NHL:, Soccer:, etc.)
    2. Conference name mapping
    3. Team-to-league reverse lookup
    4. Fallback: search all supported leagues
    """

    # Prefix patterns → (sport, league_code)
    PREFIX_PATTERNS = {
        r'^NHL\s*:': ('hockey', 'nhl'),
        r'^NBA\s*:': ('basketball', 'nba'),
        r'^NFL\s*:': ('football', 'nfl'),
        r'^MLB\s*:': ('baseball', 'mlb'),
        r'^MLS\s*:': ('soccer', 'mls'),
        r'^Soccer\s*:': ('soccer', None),  # Need team lookup for specific league
        r'^NCAA Men.?s Basketball\s*:': ('basketball', 'ncaam'),
        r'^NCAA Women.?s Basketball\s*:': ('basketball', 'ncaaw'),
        r'^NCAA Football\s*:': ('football', 'ncaaf'),
        r'^Volleyball\s*:': ('volleyball', None),
        r'^Hockey\s*:': ('hockey', None),  # Could be college
        r'^NBA G.?League\s*:': ('basketball', 'nba-g-league'),
        # ... more patterns
    }

    # Conference → likely sport (with ambiguity flag)
    CONFERENCE_PATTERNS = {
        r'^Big East Conference': ('basketball', 'ncaam', False),  # Unambiguous
        r'^Big Ten Conference': ('basketball', 'ncaam', True),   # Ambiguous (also football)
        r'^Southeastern Conference': (None, None, True),         # Very ambiguous
        r'^ACC\s*:': (None, None, True),                         # Football + Basketball
        r'^Ivy League': ('basketball', 'ncaam', False),          # Primarily basketball
        # ... more patterns
    }

    def detect(self, stream_name: str, hint_date: datetime = None) -> Optional[LeagueDetection]:
        """
        Detect league from stream name.

        Args:
            stream_name: Raw stream name
            hint_date: Optional date hint from stream (for disambiguation)

        Returns:
            LeagueDetection with sport, league, confidence, method
            or None if cannot detect
        """
        pass

    def detect_by_team_lookup(self, team1: str, team2: str) -> List[LeagueCandidate]:
        """
        Find all leagues where both teams exist.
        Returns list of candidates sorted by confidence.
        """
        pass
```

### 2. Multi-League Team Index

Build an index mapping team names → leagues for reverse lookup.

```python
class TeamLeagueIndex:
    """
    Index of team names to their leagues for reverse lookup.

    Structure:
        {
            'patriots': [('nfl', 'New England Patriots', 100)],
            'tennessee': [
                ('ncaam', 'Tennessee Volunteers', 90),
                ('ncaaw', 'Lady Volunteers', 90),
                ('nfl', 'Tennessee Titans', 90),
            ],
            'duke': [
                ('ncaam', 'Duke Blue Devils', 100),
                ('ncaaw', 'Duke Blue Devils', 100),
            ]
        }
    """

    def __init__(self, leagues_to_index: List[str]):
        """Load teams from specified leagues into index."""
        self._index = {}
        self._load_leagues(leagues_to_index)

    def find_leagues_for_teams(self, team1: str, team2: str) -> List[str]:
        """
        Find leagues where BOTH teams exist.
        Returns list of league codes.
        """
        pass
```

### 3. Database Schema Changes

#### Option A: New Group Mode Column
```sql
ALTER TABLE event_epg_groups ADD COLUMN group_mode TEXT DEFAULT 'single_league';
-- Values: 'single_league' (current), 'multi_sport'

-- Make league nullable for multi_sport mode
-- (Would require migration to handle NOT NULL constraint)
```

#### Option B: Special "Multi" League Code
```sql
-- Keep schema, use special marker value
assigned_league = 'multi'
assigned_sport = 'multi'

-- LeagueDetector handles per-stream detection
```

**Recommendation:** Option B is simpler, requires no schema changes, and maintains backwards compatibility.

### 4. Event Group Settings

New settings for multi-sport groups:

| Setting | Type | Description |
|---------|------|-------------|
| `is_multi_sport` | Boolean | Enable multi-sport detection |
| `enabled_sports` | JSON Array | Sports to search (e.g., ['hockey', 'basketball']) |
| `enabled_leagues` | JSON Array | Specific leagues to search (e.g., ['nhl', 'ncaam', 'ncaaw']) |
| `fallback_behavior` | Enum | 'skip' or 'search_all' when detection fails |

### 5. Modified Matching Flow

```python
def refresh_event_group_core(group_id: int, ...):
    group = get_event_group(group_id)

    if group.is_multi_sport:
        # Multi-sport mode: detect league per stream
        detector = LeagueDetector(enabled_leagues=group.enabled_leagues)
        team_index = TeamLeagueIndex(group.enabled_leagues)

        for stream in streams:
            # Step 1: Try to detect league from stream name
            detection = detector.detect(stream['name'])

            if detection and detection.confidence >= CONFIDENCE_THRESHOLD:
                # High confidence: use detected league
                league = detection.league
                sport = detection.sport
            else:
                # Low/no confidence: extract teams first, then lookup
                teams = extract_teams_generic(stream['name'])
                if teams:
                    candidate_leagues = team_index.find_leagues_for_teams(
                        teams['team1'], teams['team2']
                    )

                    if len(candidate_leagues) == 1:
                        # Unambiguous: single league match
                        league = candidate_leagues[0]
                    elif len(candidate_leagues) > 1:
                        # Ambiguous: try date/time disambiguation
                        league = disambiguate_by_schedule(
                            teams, candidate_leagues, stream_date
                        )
                    else:
                        # No match: skip or fallback
                        continue

            # Step 2: Match event in detected league
            matcher = get_matcher_for_league(league)
            event = matcher.find_event(teams, league)

            # Step 3: Create channel
            if event:
                create_channel(...)
    else:
        # Single league mode: existing behavior
        ...
```

---

## Two-Tiered Schedule Disambiguation

League detection uses a two-tiered approach based on whether date/time can be extracted from the stream name.

### Tier A: Date/Time Available in Stream Name

**When:** Stream name contains parseable date and time (most ESPN+ streams do)

```
"Utah State vs. #7 Tennessee (2025-12-04 18:31:00)"
  → teams: Utah State, Tennessee
  → date: 2025-12-04
  → time: 18:31
```

**Logic:**
1. Extract teams from stream name
2. Extract date/time from stream name
3. Find all leagues where both teams exist (e.g., NCAAM, NCAAW, NCAAF)
4. For each candidate league, query ESPN: "Is there a game with these teams at this date/time?"
5. **Expected result:** Exactly 1 match (unless college men's/women's at identical time - extremely rare)
6. If 1 match → use that league
7. If 0 matches → skip stream (no event found in any candidate league)
8. If >1 matches → log ambiguity, skip (edge case)

**Time tolerance:** ±30 minutes to account for slight variations in stream naming vs ESPN data.

### Tier B: No Date/Time in Stream Name

**When:** Stream name has teams but no parseable date/time

```
"ESPN+ 51 : Nashville Predators vs. Florida Panthers"
  → teams: Predators, Panthers
  → date: ???
  → time: ???
```

**Logic:**
1. Extract teams from stream name
2. Find all leagues where both teams exist
3. For each candidate league, search ESPN schedule across entire lookahead period
4. Collect all matching events across all candidate leagues
5. **Selection rule:** Pick the game **closest to now** (including games currently in progress)
6. If exactly 1 closest event → use that league and event
7. If 0 events found → skip stream
8. If tie (multiple events equally close) → skip, log for review

**Rationale for "closest to now":** Event-based streams typically show what's live or about to start. A stream named "Lakers vs. Celtics" is most likely for tonight's game, not next week's rematch.

### Detection Priority Order (No Confidence Scores)

Instead of confidence percentages, use a simple priority order:

1. **Single league match** - Both teams exist in exactly one league → use it
2. **Tier A disambiguation** - Date/time available, check schedules → use matching league
3. **Tier B disambiguation** - No date/time, search lookahead → use closest game
4. **Ambiguous** - Multiple matches, can't disambiguate → skip stream, log for review

### Additional Signals (Hints, Not Definitive)

These help narrow candidates but don't override schedule disambiguation:

**League indicators anywhere in stream name:**
- `NHL` anywhere → add NHL to candidates
- `NCAA Men's Basketball` → add NCAAM
- `Soccer` → add all soccer leagues to candidates

**Conference names:**
- `Big East Conference` → likely basketball (add NCAAM)
- `SEC` / `Big Ten` → ambiguous, add both football and basketball

**Ranking indicators:**
- `#5 Kentucky` or `#11 UNC` → likely college sports (add NCAAM, NCAAW, NCAAF)

These signals help reduce the candidate list before schedule disambiguation, but the schedule check is the final arbiter.

---

## Edge Cases and Handling Strategies

### 1. Non-Game Content (Shows/Programs)
**Examples:**
```
"Baseball: ESPN Beisbol"
"NBA Today: NBA Today"
"Football: NFL Live"
"Soccer: ESPN FC"
"The Pulse: Texas A&M Football"
```
**Detection:** No "vs", "@", or "at" separator between two entities
**Handling:** Skip - no game indicator means not a matchup stream

### 2. Single-Team Broadcasts (No Opponent Listed)
**Examples:**
```
"NHL: Panthers Broadcast"
"NHL: Predators Broadcast"
"NHL: National Broadcast"
```
**Detection:** League prefix + single team name + "Broadcast"
**Handling:**
- Extract team name
- Find that team's game at the stream's date/time
- If found, treat as that team's home/away feed

### 3. Unsupported Sports (No ESPN API)
**Examples:**
```
"Handball: Serbia vs. Faroe Is"
"Beach Soccer WorldWide: ..."
"Cricket: New Zealand vs. West Indies"
"NCAA Swimming & Diving: Big Al Invitational"
```
**Detection:** Sport prefix not in supported leagues list
**Handling:** Skip with reason "unsupported_sport"

### 4. Abbreviated Team Names
**Examples:**
```
"#3 SC vs. #22 LOU" (South Carolina vs Louisville)
"#11 UNC vs. #2 TEX" (North Carolina vs Texas)
"USA vs. LT" (South Alabama vs Louisiana Tech?)
```
**Handling:**
- Existing TeamMatcher alias system handles common abbreviations
- Rankings (`#3`, `#11`) should be stripped before team lookup
- May need to expand alias database for multi-league mode

### 5. Conference-Only Indicator (Ambiguous Sport)
**Examples:**
```
"Big East Conference: Providence vs. Xavier"
"Southeastern Conference: Utah State vs. #7 Tennessee"
"Big Ten Conference: Toledo vs. #4 Indiana"
```
**Detection:** Conference name as prefix, no explicit sport
**Handling:**
- Conference narrows candidates but doesn't determine sport
- Schedule disambiguation (Tier A/B) resolves which sport
- Most conferences only have one sport active at a time

### 6. High School Sports
**Examples:**
```
"Basketball: High School Basketball"
```
**Detection:** "High School" in stream name
**Handling:** Skip - not in ESPN APIs

### 7. Division III / Small College
**Examples:**
```
"Volleyball: NCAA Division III Women's Volleyball Championship"
"NCAA Women's Basketball: Calumet College vs. SIUE"
```
**Handling:**
- Attempt match - some D3 schools are in ESPN
- If no match found, skip with "team_not_found" reason

### 8. International Soccer (Various Leagues)
**Examples:**
```
"Soccer: Real Avila vs. Rayo" (Spanish Copa del Rey)
"Soccer: Atl. Baleares vs. Espanyol"
"Soccer: Cartagena vs. Valencia"
```
**Handling:**
- Soccer multi-league cache already indexes 240+ leagues
- Team lookup will find the correct league
- Schedule disambiguation confirms the match

### 9. Language Variants (Same Game, Different Feed)
**Examples:**
```
"Soccer: Extremadura vs. Sevilla (ESP)"
"NHL: Blackhawks vs. Kings (ESP)"
```
**Detection:** `(ESP)` or similar language tag at end
**Handling:**
- Strip language tag before team extraction
- Match to same event as English feed
- Optionally: note language in channel metadata

### 10. Multi-Event Whiparound Streams
**Examples:**
```
"NHL: TOR vs. CAR • COL vs. NYI"
"NCAA Women's Basketball: #3 SC vs. #22 LOU • #11 UNC vs. #2 TEX"
```
**Detection:** `•` (bullet) separator between matchups
**Handling:** Skip for initial implementation (future enhancement)

---

## Handling Ambiguous Cases

### Case 1: Men's vs Women's Basketball
Stream: `Tennessee vs. Kentucky (2025-12-04 19:00)`

**Strategy:**
1. Check NCAAM schedule for game at that time
2. Check NCAAW schedule for game at that time
3. If only one matches, use that
4. If both match (rare but possible), prefer NCAAM (configurable)

### Case 2: Different Sports Same Day
Stream: `USC vs. UCLA @ Dec 04`

**Strategy:**
1. Check all candidate leagues for USC vs UCLA on that date
2. Use time to disambiguate if available
3. If still ambiguous, check which sport is in-season

### Case 3: No League Indicator, Generic Teams
Stream: `Team A vs. Team B`

**Strategy:**
1. Build candidate list from team index
2. If multiple leagues, skip (or use configurable fallback)
3. Log for user review

---

## Performance Considerations

### Caching Strategy
1. **Team Index Cache**: Build once per EPG generation, reuse across streams
2. **League Detection Cache**: Cache detection results by stream name prefix
3. **Schedule Cache**: Reuse existing `ESPNClient._schedule_cache`

### Parallel Processing
Current parallel processing can continue. Each thread needs its own:
- TeamMatcher instance (already per-thread)
- LeagueDetector instance (lightweight, can share index)

### API Call Optimization
- Pre-load team data for all enabled leagues at group refresh start
- Batch schedule lookups where possible
- Use scoreboard API for current games (already implemented)

---

## Multi-Sport Group Settings

Per-group settings when multi-sport mode is enabled.

### 1. Channel Sort Order

```
Sort channels by:
○ Sport → League → Time (group related content together)
○ League → Time (group by league, then chronological)
○ Time only (current behavior - purely chronological)
```

**Implementation:** Sort key applied to `matched_streams` before channel number assignment.

**Sport priority for sorting** (hardcoded, sensible default):
1. Hockey (NHL, NCAAH)
2. Basketball (NBA, NCAAM, NCAAW, NBA-G)
3. Football (NFL, NCAAF)
4. Soccer (all leagues)
5. Baseball (MLB)
6. Volleyball (NCAAVB-W, NCAAVB-M)

### 2. Overlap Handling with Single-League Groups

When a multi-sport stream matches an event that already has a channel from a single-league group:

```
When event already has a channel from another group:
○ Add as backup stream (consolidate to existing channel)
○ Create separate channel (duplicate coverage from different source)
○ Skip (don't create, log as "already_covered")
```

**Implementation notes:**
- Multi-sport groups ALWAYS process after single-league groups (hardcoded, not configurable)
- Requires cross-group lookup: `find_any_channel_for_event(event_id)`
- Current `find_parent_channel_for_event()` only searches parent/child hierarchy - need broader search

### 3. Enabled Sports/Leagues (Per-Group)

When multi-sport mode selected, user chooses which sports/leagues to detect:

```
Enable detection for:
┌─ Hockey ──────────────────────────┐
│ ☑ NHL - National Hockey League    │
│ ☑ NCAAH - NCAA Men's Hockey       │
└───────────────────────────────────┘
┌─ Basketball ──────────────────────┐
│ ☑ NBA - National Basketball Assoc │
│ ☑ NCAAM - NCAA Men's Basketball   │
│ ☑ NCAAW - NCAA Women's Basketball │
│ ☑ NBA-G - NBA G League            │
└───────────────────────────────────┘
┌─ Football ────────────────────────┐
│ ☑ NFL - National Football League  │
│ ☑ NCAAF - NCAA Football           │
└───────────────────────────────────┘
┌─ Soccer ──────────────────────────┐
│ ☑ All Soccer Leagues (240+)       │
└───────────────────────────────────┘
┌─ Baseball ────────────────────────┐
│ ☑ MLB - Major League Baseball     │
└───────────────────────────────────┘
┌─ Volleyball ──────────────────────┐
│ ☑ NCAAVB-W - NCAA Women's VB      │
│ ☑ NCAAVB-M - NCAA Men's VB        │
└───────────────────────────────────┘
```

**Why per-group?** Different multi-sport groups may have different content mixes:
- ESPN+ might have everything
- A regional sports group might only have NHL + MLB
- User can tune detection to reduce false matches

### 4. Unmatched Stream Handling

```
When league detection fails:
○ Skip stream (default - safest)
○ Log for manual review only (skip but flag in UI)
```

### 5. Ambiguity Resolution

```
When multiple leagues match (same teams, same time):
○ Skip stream (safest - rare edge case)
○ Use sport priority order (first in priority list wins)
```

### 6. Consolidation Exception Keywords (Per-Group)

Keyword patterns with configurable behavior when streams match an existing event. Each entry supports multiple keyword variants (comma-separated) that share the same behavior.

**Applies to:** ALL event groups (single-league AND multi-sport). This is a general consolidation feature.

**Use Case:** Alternate broadcasts like ManningCast, Prime Vision, Spanish feeds are technically the same game but completely different viewing experiences. Users may want separate channels, consolidated alternate feeds, or to skip them entirely.

#### Database Schema

```sql
CREATE TABLE IF NOT EXISTS consolidation_exception_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    keywords TEXT NOT NULL,              -- Comma-separated variants (case-insensitive)
    behavior TEXT NOT NULL DEFAULT 'consolidate',  -- 'consolidate', 'separate', 'ignore'
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
);
```

#### Behaviors

| Behavior | Description |
|----------|-------------|
| `consolidate` | Create separate channel from main event, but consolidate streams with same keyword together (default) |
| `separate` | Create new channel for each matching stream (never consolidate with others) |
| `ignore` | Skip streams entirely (don't create channel) |

#### Example Configuration

| Keywords | Behavior | Use Case |
|----------|----------|----------|
| `Prime Vision, Primevision` | separate | Each Prime Vision stream gets its own channel |
| `Manning Cast, Manningcast` | consolidate | All ManningCast streams for same game on one channel |
| `Broadcast` | consolidate | Home/Away broadcasts grouped by team |
| `Spanish, Espanol, (ESP)` | ignore | Skip Spanish language feeds |

#### Processing Examples

**Example 1: `separate` behavior**
```
Streams for Event 12345 (Chiefs vs Raiders):
- "Chiefs vs Raiders"                    → Main channel (streams consolidate here)
- "Chiefs vs Raiders (Prime Vision)"     → Separate channel A
- "Chiefs vs Raiders (Prime Vision) 4K"  → Separate channel B (NOT consolidated with A)
```

**Example 2: `consolidate` behavior**
```
Streams for Event 12345 (Canucks vs Blackhawks):
- "Canucks vs Blackhawks"                      → Main channel
- "Canucks vs Blackhawks (Canucks Broadcast)"  → Channel A (keyword: "Broadcast")
- "Canucks vs Blackhawks (Canucks Broadcast) HD" → Channel A (same keyword, consolidates)
- "Canucks vs Blackhawks (Blackhawks Broadcast)" → Channel B (different team broadcast)
```

**Example 3: `ignore` behavior**
```
Streams for Event 12345:
- "Chiefs vs Raiders"           → Main channel
- "Chiefs vs Raiders (Spanish)" → Skipped entirely
- "Chiefs vs Raiders (ESP)"     → Skipped entirely
```

#### Matching Logic

```python
def get_stream_exception_behavior(stream_name: str, group_id: int) -> tuple[str | None, str | None]:
    """
    Check if stream matches any exception keyword.

    Returns:
        (canonical_keyword, behavior) if match found - canonical is first variant in list
        (None, None) if no match
    """
    exceptions = get_consolidation_exceptions(group_id)
    stream_lower = stream_name.lower()

    for exc in exceptions:
        # Split comma-separated keywords, trim whitespace
        variants = [k.strip().lower() for k in exc['keywords'].split(',')]

        for variant in variants:
            if variant in stream_lower:
                # Return first variant as canonical (for consolidate grouping)
                return (variants[0], exc['behavior'])

    return (None, None)
```

#### Child Group Inheritance

Child groups automatically inherit parent's exception keywords. This ensures consistent handling across parent and child groups for the same provider content.

```python
def get_consolidation_exceptions(group_id: int) -> list:
    """Get exceptions for group, including inherited from parent."""
    group = get_event_group(group_id)

    if group.get('parent_group_id'):
        # Child group - use parent's exceptions
        return get_exceptions_for_group(group['parent_group_id'])
    else:
        return get_exceptions_for_group(group_id)
```

#### Processing Flow

```
Stream matches event that already has a channel
    ↓
Check consolidation_exception_keywords (parent's if child group)
    ↓
├── Keyword match with 'ignore' → Skip stream entirely
├── Keyword match with 'separate' → Create NEW channel (one per stream)
├── Keyword match with 'consolidate' → Find/create channel for this keyword+event
└── No keyword match → Apply normal duplicate_event_handling mode
```

#### UI Design

```
┌─ Consolidation Exception Keywords ────────────────────────────────────┐
│                                                                        │
│  ┌────────────────────────────────┬─────────────────┬─────────┐       │
│  │ Keywords (comma-separated)     │ Behavior        │         │       │
│  ├────────────────────────────────┼─────────────────┼─────────┤       │
│  │ Prime Vision, Primevision      │ ▼ Separate      │   🗑️    │       │
│  │ Manning Cast, Manningcast      │ ▼ Consolidate   │   🗑️    │       │
│  │ Broadcast                      │ ▼ Consolidate   │   🗑️    │       │
│  │ Spanish, Espanol, (ESP)        │ ▼ Ignore        │   🗑️    │       │
│  └────────────────────────────────┴─────────────────┴─────────┘       │
│                                                                        │
│  ┌────────────────────────────────┐ ┌─────────────────┐               │
│  │                                │ │ ▼ Consolidate   │  [+ Add]      │
│  └────────────────────────────────┘ └─────────────────┘               │
│                                                                        │
│  Behaviors:                                                            │
│  • Consolidate - Group same-keyword streams on one channel (default)   │
│  • Separate - New channel per stream (never consolidate)               │
│  • Ignore - Skip these streams entirely                                │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

#### Default Keywords (Suggested)

| Keywords | Behavior | Rationale |
|----------|----------|-----------|
| `Prime Vision, Primevision` | separate | Unique camera angles, each is different |
| `Manning Cast, Manningcast` | consolidate | Same broadcast, consolidate HD/SD variants |
| `Broadcast` | consolidate | Group home/away feeds by team |
| `Megacast` | separate | Different megacast feeds are unique |
| `Whiparound` | consolidate | Same whiparound content |

---

## League Registry

The `league_config` table is the source of truth for supported leagues. The enabled leagues picker pulls from this table.

### Current Leagues (in `league_config` table)

| Sport | Leagues |
|-------|---------|
| baseball | MLB |
| basketball | NBA, NCAAM, NCAAW, WNBA |
| football | NFL, NCAAF |
| hockey | NHL |
| soccer | EPL, La Liga, Bundesliga, Serie A, Ligue 1, MLS, NWSL, EFL Championship, EFL League One |

### To Add for Multi-Sport Support

| Code | Name | Sport | API Path |
|------|------|-------|----------|
| `nba-g` | NBA G League | basketball | `basketball/nba-development` |
| `ncaavb-w` | NCAA Women's Volleyball | volleyball | `volleyball/womens-college-volleyball` |
| `ncaavb-m` | NCAA Men's Volleyball | volleyball | `volleyball/mens-college-volleyball` |
| `ncaah` | NCAA Men's Hockey | hockey | `hockey/mens-college-hockey` |

### Soccer Multi-League (Special Case)

Soccer uses `SoccerMultiLeague` cache (240+ leagues) instead of individual `league_config` entries:
- UI shows "All Soccer Leagues" toggle
- Team lookup across all indexed leagues via `SoccerMultiLeague`
- Individual soccer leagues in `league_config` still work for single-league groups

---

## Processing Order (Hardcoded)

Multi-sport groups MUST process after all single-league groups. This is not configurable.

**Rationale:**
- Single-league groups create authoritative channels for their events
- Multi-sport groups can then consolidate streams to existing channels
- Prevents multi-sport from "claiming" events before dedicated groups

**Implementation in `generate_all_epg()`:**
```python
# Sort groups: single-league first, multi-sport last
groups = get_all_event_groups()
single_league_groups = [g for g in groups if not g.is_multi_sport]
multi_sport_groups = [g for g in groups if g.is_multi_sport]

# Process in order
for group in single_league_groups:
    process_group(group)

for group in multi_sport_groups:
    process_group(group)  # Can now check for existing channels
```

---

## UI Changes

### Event Group Form
1. Add toggle: "Multi-Sport Group"
2. When enabled:
   - Hide single league/sport selector
   - Show enabled sports/leagues picker (grouped by sport)
   - Show channel sort order dropdown
   - Show overlap handling dropdown
   - Show ambiguity resolution dropdown

### Stream Preview
1. Show detected league per stream (if multi-sport mode)
2. Show detection method (prefix match, team lookup, schedule disambiguation)
3. Highlight ambiguous detections for review
4. Show "already covered" streams when overlap handling = skip

### Group Stats
1. Show breakdown by detected league
2. Show detection failure count
3. Show ambiguity count
4. Show "consolidated to existing channel" count

---

## Migration Path

### Phase 1: Detection Module
1. Create `LeagueDetector` class with prefix patterns
2. Create `TeamLeagueIndex` for reverse lookup
3. Unit tests with ESPN Xtra sample data

### Phase 2: Integration
1. Add `is_multi_sport` flag to groups
2. Modify `refresh_event_group_core()` to use detection
3. Add enabled_leagues setting

### Phase 3: Disambiguation
1. Implement conference mapping
2. Implement schedule-based disambiguation
3. Handle ambiguous cases gracefully

### Phase 4: UI
1. Add multi-sport toggle to group form
2. Update stream preview to show detection info
3. Add detection stats to group display

---

## Synthetic Test Results (Dec 2024)

Comprehensive testing against 550 real streams from ESPN Xtra (850) and ESPN+ (972).

### Stream Format Discovery

**Two distinct formats found:**

| Format | Group | Example | Prefix? | Date/Time? |
|--------|-------|---------|---------|------------|
| Format 1 | ESPN Xtra (850) | `NHL: Panthers vs. Lightning (2025-12-04 19:00:00)` | ✅ Yes | ✅ Yes |
| Format 2 | ESPN+ (972) | `ESPN+ 51 : Nashville Predators vs. Florida Panthers @ Dec 04 06:30 PM ET` | ❌ No | ✅ Yes |

**Critical Finding:** Group 972 has NO sport prefix on ANY streams (100% require team lookup).

### Detection Statistics

| Category | Count | % | Action |
|----------|-------|---|--------|
| Total streams | 550 | 100% | - |
| Whiparound (multi-game) | 14 | 2.5% | Skip (future enhancement) |
| No game indicator | 164 | 29.8% | Skip (shows/broadcasts) |
| Unsupported sports | 5 | 0.9% | Skip |
| **Matchup streams** | **367** | **66.7%** | **Process** |

Of matchup streams:
| Detection Method | Count | % of Matchups |
|------------------|-------|---------------|
| League from prefix | 49 | 13.4% |
| Sport only (ambiguous) | 9 | 2.5% |
| No prefix (team lookup required) | ~309 | **84.1%** |

**Conclusion:** TeamLeagueIndex is THE critical component. Prefix detection is only a ~16% shortcut.

### Gaps Identified

**1. Missing Conference Patterns:**
- Patriot League (2x) - basketball-focused
- Ivy League (1x) - basketball/football
- Conference USA (1x) - basketball/football
- Summit League (1x) - basketball

**2. Unsupported Sports (add to skip list):**
- NCAA Swimming & Diving
- World Champions Cup
- Beach Soccer WorldWide
- Cricket, Handball, Formula Two

**3. Character Normalization Needed:**
- `Hawai\`i` → `Hawai'i` (backtick → apostrophe)
- `Gardner_Webb` → `Gardner-Webb` (underscore → hyphen)
- `St John\`s` → `St John's`

**4. Abbreviated Team Names (verify alias coverage):**
- `USA vs. LT` → South Alabama vs Louisiana Tech
- `#3 SC vs. #22 LOU` → South Carolina vs Louisville
- `LBSU vs. UCSB` → Long Beach State vs UC Santa Barbara

### Sample Streams

**ESPN Xtra (Group 850) - With Prefix:**
```
NHL: Panthers vs. Lightning
NCAA Women's Basketball: Virginia vs. #15 Vanderbilt
Big Ten Conference: Princeton vs. #4 USC
Soccer: Victory vs. Canberra
NBA G League: Stars vs. Kings
Volleyball: Wofford vs. #1 Kentucky
```

**ESPN+ (Group 972) - No Prefix:**
```
ESPN+ 51 : Nashville Predators vs. Florida Panthers @ Dec 04 06:30 PM ET
ESPN+ 89 : Minnesota Wild vs. Calgary Flames @ Dec 04 08:30 PM ET
ESPN+ 68 : #10 Clarkson vs. St. Lawrence @ Dec 04 07:00 PM ET
ESPN+ 92 : Coppin State vs. #2 Arizona State (First Round) @ Dec 04 09:00 PM ET
```

---

## New Leagues to Add

Based on ESPN Xtra test data analysis, these sports appear frequently and have confirmed ESPN API support:

### Confirmed New Leagues (ESPN API Verified)

| League Code | League Name | Sport | API Path | Record Format | Notes |
|-------------|-------------|-------|----------|---------------|-------|
| `nba-g` | NBA G League | basketball | `basketball/nba-development` | wins-losses | 5 events today, full team list |
| `ncaavb-w` | NCAA Women's Volleyball | volleyball | `volleyball/womens-college-volleyball` | wins-losses | 16 events today, college teams |
| `ncaavb-m` | NCAA Men's Volleyball | volleyball | `volleyball/mens-college-volleyball` | wins-losses | Off-season currently, spring sport |
| `ncaah` | NCAA Men's Hockey | hockey | `hockey/mens-college-hockey` | wins-losses-ties | 1 event today, college teams |

### Files to Update for New Leagues

1. **`database/schema.sql`** - Add to `INSERT INTO league_config` values
2. **`epg/league_config.py`** - Add to `COLLEGE_LEAGUES` set (if college sport)
3. **UI auto-populates** from `league_config` table (no template changes needed)

### Sports NOT to Add (Limited/No ESPN Coverage)

| Sport | Reason |
|-------|--------|
| Handball | No ESPN API coverage found |
| Beach Soccer | No ESPN API coverage found |
| Cricket | Limited ESPN API, international only |
| Formula 2 | ESPN has F1 only |
| UFC/MMA | No team-based matchups, event structure different |

### Migration Plan for New Leagues

**Phase 1:** Add to schema and league_config (immediate)
- Can be used standalone for single-league event groups
- Team-based EPG support included

**Phase 2:** Multi-league detection (this feature)
- New leagues automatically included in multi-league mode
- Team index builds for all configured leagues

---

## Open Questions

1. ~~**Default enabled leagues**: Should all leagues be enabled by default, or require explicit selection?~~
   **RESOLVED:** All leagues enabled automatically when multi-league mode selected.

2. ~~**Confidence threshold**: What confidence level should trigger automatic matching vs. skipping?~~
   **RESOLVED:** No confidence scores. Use detection priority order with schedule disambiguation as final arbiter.

3. **Fallback league**: When detection fails, should we try a configurable "default" league?
   **Tentative:** No fallback. Skip unmatched streams and log for review.

4. **Conference-to-sport mapping**: Should this be user-configurable or hardcoded?
   **Tentative:** Hardcode common patterns. Conference hints narrow candidates but schedule disambiguation is final.

5. ~~**Multi-event streams**: How to handle streams like `"NHL: TOR vs. CAR • COL vs. NYI"` (multiple games)?~~
   **RESOLVED:** Skip for initial implementation. Add as future enhancement (whiparound coverage detection).

---

## Future Enhancements

1. **Whiparound/Multi-Game Coverage Detection**
   - Detect `•` (bullet) separator indicating multiple games in one stream
   - Example: `"NHL: TOR vs. CAR • COL vs. NYI"`
   - Parse all games mentioned (2-3 typically)
   - Validate each game exists in ESPN schedule
   - Create single channel with combined name: `"NHL Whiparound: TOR/CAR, COL/NYI"`
   - EPG description lists all games with details
   - Skip these streams in initial implementation

2. **User-defined prefix patterns**: Allow users to add custom prefix → league mappings
3. **Learning from corrections**: If user manually corrects a detection, learn from it
4. **Provider-specific patterns**: Different providers may have different naming conventions

---

## Additional Provider Format Analysis (Dec 2024)

Testing against two additional ESPN+ provider formats revealed important new patterns.

### Provider Format Summary

| Provider | Format | Example | Prefix? | Date? | Time? |
|----------|--------|---------|---------|-------|-------|
| ESPN Xtra (850) | Full | `NHL: Panthers vs. Lightning (2025-12-04 19:00:00)` | ✅ | ✅ | ✅ |
| ESPN+ (972) | Channel+Date | `ESPN+ 51 : Predators vs. Panthers @ Dec 04 06:30 PM ET` | ❌ | ✅ | ✅ |
| Provider 3 | Channel+Time | `ESPN+ 100 (D): Drake vs. Illinois State  19:00et-00:00uk` | ❌ | ❌ | ✅ |
| Provider 4 | League+Date | `ESPN+ 01 :NHL: Predators vs. Blackhawks @ Nov 28 7:30 PM` | ✅ | ✅ | ✅ |

### Provider 3: Time-Only Format (New)

**Pattern:** `ESPN+ {num} (D): {matchup}  {time_et}-{time_uk}`

```
ESPN+ 100 (D): Drake vs. Illinois State  19:00et-00:00uk
ESPN+ 102 (D): #15 Ohio State vs. #12 UConn  19:00et-00:00uk
ESPN+ 136 (D): Vancouver Canucks vs. Chicago Blackhawks (Blackhawks Broadcast) 20:00et-01:00uk
ESPN+ 141 (D): San Jose Sharks vs. Utah Mammoth  21:00et-02:00uk
```

**Key insight:** When only time is provided (no date), **infer today's date**. This promotes these streams from Tier B to **Tier A** detection (exact date/time match).

**Time parsing:** Extract ET time, combine with `datetime.now().date()`:
```python
# Pattern: "19:00et-00:00uk" at end of stream name
TIME_ONLY_PATTERN = r'(\d{1,2}:\d{2})et-\d{2}:\d{2}uk\s*$'
# Extract "19:00", parse as ET, combine with today's date
```

### Provider 4: Ideal Format (New)

**Pattern:** `ESPN+ {num} :{League}: {matchup} @ {date} {time}`

```
ESPN+ 01 :NHL: Tampa Bay Lightning vs Detroit Red Wings (Red Wings Broadcast) @ Nov 28 11:30 AM
ESPN+ 102 :NCAA Football: Louisiana Tech vs Missouri State @ Nov 29 2:00 PM
ESPN+ 103 :NCAAM: Monmouth vs Le Moyne @ Nov 29 2:00 PM
ESPN+ 113 :NCAA Women's Volleyball: Cal Poly vs Long Beach State Semifinals @ Nov 28 9:00 PM
ESPN+ 108 :2. Bundesliga: 1 FC Magdeburg vs 1 FC Nurnberg @ Nov 29 2:25 PM
```

**This is the ideal format** - has league prefix AND full date/time. Direct Tier A detection with high confidence.

### New Prefix Patterns Discovered

From Provider 4 streams, add these to `PREFIX_PATTERNS`:

```python
# Soccer - International Leagues
r'^A-League Men\s*:': ('soccer', 'aus.1'),
r'^A-League Women\s*:': ('soccer', 'aus.w'),
r'^2\.\s*Bundesliga\s*:': ('soccer', 'ger.2'),
r'^Bundesliga\s*:': ('soccer', 'ger.1'),
r'^LALIGA\s*:': ('soccer', 'esp.1'),
r'^Spanish LALIGA\s*:': ('soccer', 'esp.1'),
r'^En Español.*:': ('soccer', None),  # Spanish broadcast, need team lookup

# College Sports
r'^NCAA Football\s*:': ('football', 'ncaaf'),
r'^NCAA Women.?s Volleyball\s*:': ('volleyball', 'ncaavb-w'),
r'^CWHOC\s*:': ('hockey', 'ncaaw-hockey'),  # College Women's Hockey

# Shorthand codes
r'^NCAAM\s*:': ('basketball', 'ncaam'),
r'^NCAAW\s*:': ('basketball', 'ncaaw'),
```

### New Skip Patterns (Unsupported Sports/Content)

```python
SKIP_SPORT_PREFIXES = [
    r'^NLL\s*:',           # National Lacrosse League
    r'^IHFW\s*:',          # International Handball Federation Women
    r'^F1\s*:',            # Formula 1
    r'^F2\s*:',            # Formula 2
]

SKIP_CONTENT_PATTERNS = [
    r'ESPN FC Daily',
    r'ESPN FC$',
    r'Goal Arena',
    r'Halftime Band Performances',
    r"Ted'?s.*Notebook",
    r'Clinton and Friends',
    r'Womens College Basketball$',  # Generic placeholder, no matchup
    r'Pre-?Show',
    r'Post-?Show',
    r'Postgame Press Conference',
    r'Coachs? Show',
    r'Classic CC Meet',             # Cross country
    r'Rampage \d{4}',               # Extreme sports events
]
```

### New Edge Cases Identified

#### 1. High School Sports (State Code Pattern)
```
ESPN+ 135 (D): Oak Park (MO) vs. Liberty (MO)  20:00et-01:00uk
ESPN+ 138 (D): Rockhurst (MO) vs. Liberty North (MO)  20:00et-01:00uk
```
**Detection:** State code `(XX)` after team names on both sides
**Pattern:** `r'\([A-Z]{2}\)\s*vs\.?\s*.*\([A-Z]{2}\)'`
**Handling:** Skip - high school sports not in ESPN APIs

#### 2. Home/Away Broadcast Feeds
```
ESPN+ 136 (D): Vancouver Canucks vs. Chicago Blackhawks (Blackhawks Broadcast)
ESPN+ 137 (D): Vancouver Canucks vs. Chicago Blackhawks (Canucks Broadcast)
ESPN+ 101 :NHL: Utah Mammoth vs Dallas Stars (Mammoth Broadcast) @ Nov 28 7:30 PM
ESPN+ 102 :NHL: Utah Mammoth vs Dallas Stars (Stars Broadcast) @ Nov 28 7:30 PM
```
**Handling:** Add "Broadcast" to default `consolidation_exception_keywords` to create separate channels per broadcast feed.

#### 3. Sprint Football (Niche Variant)
```
ESPN+ 103 (D): Army vs. Cornell (Sprint Football)  19:00et-00:00uk
```
**Handling:** Not in ESPN APIs. Will fail team lookup gracefully, skip with "team_not_found".

#### 4. Exhibition Games
```
ESPN+ 143 (D): Nevada vs. Utah (Exhibition)  21:00et-02:00uk
```
**Handling:** May or may not be in ESPN. Attempt match; if not found, skip gracefully.

#### 5. Tournament Round Indicators
```
ESPN+ 104 :NCAA Football: Newberry vs West Florida Second Round @ Nov 29 2:00 PM
ESPN+ 124 :NCAA Football: Texas Permian Basin vs Western Colorado Second Round @ Nov 29 3:00 PM
```
**Handling:** Strip tournament indicators (`First Round`, `Second Round`, `Semifinals`, `Championship`) before team extraction.

#### 6. Team Name Errors in Provider Data
```
ESPN+ 141 (D): San Jose Sharks vs. Utah Mammoth  21:00et-02:00uk
```
**Issue:** "Utah Mammoth" should be "Utah Hockey Club" (NHL team).
**Handling:** Add alias `Utah Mammoth` → `Utah Hockey Club` in team aliases.

### Updated Time Parsing Patterns

```python
TIME_PATTERNS = [
    # Provider 4: "@ Nov 29 2:00 PM" or "@ Nov 28 7:30 PM"
    (r'@\s*(\w+\s+\d{1,2}\s+\d{1,2}:\d{2}\s*[AP]M)', 'date_time'),

    # Provider 3: "19:00et-00:00uk" (time only → infer today)
    (r'(\d{1,2}:\d{2})et-\d{2}:\d{2}uk\s*$', 'time_only'),

    # ESPN Xtra: "(2025-12-04 19:00:00)"
    (r'\((\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)\)', 'date_time'),

    # ESPN+ 972: "@ Dec 04 06:30 PM ET"
    (r'@\s*(\w+\s+\d{2}\s+\d{2}:\d{2}\s*[AP]M\s*ET)', 'date_time'),
]

def parse_stream_datetime(stream_name: str) -> Optional[datetime]:
    """
    Extract date/time from stream name.
    For time-only patterns, combines with today's date.
    """
    for pattern, pattern_type in TIME_PATTERNS:
        match = re.search(pattern, stream_name)
        if match:
            if pattern_type == 'time_only':
                # Combine with today's date
                time_str = match.group(1)  # e.g., "19:00"
                today = datetime.now().date()
                return datetime.combine(today, parse_time(time_str))
            else:
                return parse_datetime(match.group(1))
    return None
```

### Updated Default Consolidation Exception Keywords

Based on broadcast feed patterns found, recommended defaults for the new per-keyword behavior system:

| Keywords | Behavior | Rationale |
|----------|----------|-----------|
| `Broadcast` | consolidate | Home/away feeds grouped - e.g., all "Canucks Broadcast" streams together |
| `Prime Vision, Primevision` | separate | Each stream is unique camera angle |
| `Manning Cast, Manningcast` | consolidate | Same broadcast, group HD/SD variants |
| `Spanish, Espanol, (ESP), Deportes` | ignore | Skip Spanish feeds (or use `consolidate` if wanted) |
| `Whiparound` | consolidate | Same whiparound content |
| `Megacast` | separate | Different megacast feeds are unique |

See "Consolidation Exception Keywords (Per-Group)" section for full implementation details.

### Provider 3 vs Provider 4 Detection Flow

**Provider 3** (no prefix, time only):
```
ESPN+ 102 (D): #15 Ohio State vs. #12 UConn  19:00et-00:00uk
    ↓
1. No league prefix found
2. Extract teams: "Ohio State", "UConn"
3. Extract time: 19:00 ET → combine with today → 2024-12-04 19:00 ET
4. Find candidate leagues: NCAAM, NCAAW, NCAAF (both teams exist)
5. Tier A: Check each league for game at that date/time
6. Match found in NCAAM → use NCAAM
```

**Provider 4** (prefix + date/time):
```
ESPN+ 103 :NCAAM: Monmouth vs Le Moyne @ Nov 29 2:00 PM
    ↓
1. League prefix found: NCAAM → basketball/ncaam
2. Extract teams: "Monmouth", "Le Moyne"
3. Extract date/time: Nov 29 2:00 PM
4. Tier A: Verify game exists in NCAAM at that time
5. Match confirmed → create channel
```

### Stream Category Breakdown (Provider 3 + 4 Combined)

| Category | Provider 3 | Provider 4 | Total | Handling |
|----------|------------|------------|-------|----------|
| Valid matchups | ~50 | ~80 | ~130 | Process |
| No game indicator | ~8 | ~15 | ~23 | Skip (shows/programs) |
| Empty/placeholder | ~35 | ~5 | ~40 | Skip |
| Unsupported sports | 1 | ~10 | ~11 | Skip (NLL, IHFW, F1, F2) |
| High school | 2 | 0 | 2 | Skip |

---

## Appendix: Sample Prefix Patterns

### Major Professional Leagues

| Prefix Pattern | Sport | League | Confidence |
|----------------|-------|--------|------------|
| `NHL:` | hockey | nhl | 100% |
| `NBA:` | basketball | nba | 100% |
| `NFL:` | football | nfl | 100% |
| `MLB:` | baseball | mlb | 100% |
| `MLS:` | soccer | mls | 100% |
| `WNBA:` | basketball | wnba | 100% |
| `NBA G League:` | basketball | nba-g | 100% |

### College Sports

| Prefix Pattern | Sport | League | Confidence |
|----------------|-------|--------|------------|
| `NCAA Men's Basketball:` | basketball | ncaam | 100% |
| `NCAA Women's Basketball:` | basketball | ncaaw | 100% |
| `NCAAM:` | basketball | ncaam | 100% |
| `NCAAW:` | basketball | ncaaw | 100% |
| `NCAA Football:` | football | ncaaf | 100% |
| `NCAA Women's Volleyball:` | volleyball | ncaavb-w | 100% |
| `CWHOC:` | hockey | ncaaw-hockey | 100% |
| `College Basketball:` | basketball | ncaam | 90% |
| `College Football:` | football | ncaaf | 95% |

### Soccer - International

| Prefix Pattern | Sport | League | Confidence |
|----------------|-------|--------|------------|
| `Soccer:` | soccer | (detect) | 90% |
| `Bundesliga:` | soccer | ger.1 | 100% |
| `2. Bundesliga:` | soccer | ger.2 | 100% |
| `LALIGA:` | soccer | esp.1 | 100% |
| `Spanish LALIGA:` | soccer | esp.1 | 100% |
| `A-League Men:` | soccer | aus.1 | 100% |
| `A-League Women:` | soccer | aus.w | 100% |
| `En Español` (anywhere) | soccer | (detect) | 80% |

### Conference Indicators

| Prefix Pattern | Sport | League | Confidence |
|----------------|-------|--------|------------|
| `Big East Conference:` | basketball | ncaam | 95% |
| `Ivy League:` | basketball | ncaam | 90% |
| `Patriot League:` | basketball | ncaam | 90% |
| `Summit League:` | basketball | ncaam | 90% |
| `Big Ten Conference:` | (ambiguous) | ncaam/ncaaf | 70% |
| `SEC:` / `Southeastern Conference:` | (ambiguous) | ncaam/ncaaf | 60% |
| `ACC:` | (ambiguous) | ncaam/ncaaf | 60% |
| `Sun Belt Conference:` | (ambiguous) | ncaam/ncaaf | 70% |
| `Conference USA:` | (ambiguous) | ncaam/ncaaf | 70% |

### Generic/Ambiguous

| Prefix Pattern | Sport | League | Confidence |
|----------------|-------|--------|------------|
| `Volleyball:` | volleyball | (detect) | 80% |
| `Hockey:` | hockey | (detect) | 80% |

### Skip Patterns (Unsupported)

| Prefix Pattern | Reason |
|----------------|--------|
| `NLL:` | National Lacrosse League - no ESPN API |
| `IHFW:` | International Handball - no ESPN API |
| `F1:` | Formula 1 - not team-based matchups |
| `F2:` | Formula 2 - not team-based matchups |
| `UFC` | MMA - not team-based matchups |
| `Cricket:` | Limited ESPN coverage |
| `High School` | Not in ESPN APIs |
