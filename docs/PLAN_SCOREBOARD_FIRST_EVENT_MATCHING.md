# Implementation Plan: Scoreboard-First Event Matching

## Executive Summary

Refactor event-based EPG matching to use the scoreboard endpoint as the primary data source, falling back to schedule endpoint only when necessary (college sports). This eliminates ~1200 schedule API calls per EPG generation for pro sports.

## Current State Analysis

### Current Flow (Schedule-Centric)
```
Stream: "Chiefs vs Raiders"
    ↓
1. Extract teams: "Chiefs", "Raiders"
    ↓
2. Resolve team IDs via TeamLeagueCache
    ↓
3. Call get_team_schedule(team1_id) → ~45KB response, 82 events
    ↓
4. Search schedule for opponent match
    ↓
5. If not found, call get_team_schedule(team2_id)
    ↓
6. If still not found, fall back to scoreboard (8 days)
    ↓
7. Return matched event
```

**Problem**: For ESPN+ with 200+ streams across many sports, this results in:
- ~1200 unique schedule API calls
- ~814 cache hits (41% hit rate)
- Each schedule response is 10-50KB
- Total: ~60-100MB of data transfer per generation

### Proposed Flow (Scoreboard-First)
```
Stream: "Chiefs vs Raiders"
    ↓
1. Extract teams: "Chiefs", "Raiders"
    ↓
2. Resolve team IDs via TeamLeagueCache
    ↓
3. Search scoreboard cache (already fetched per-league/date)
    ↓
4. If found → Return matched event
    ↓
5. If NOT found AND is_college_sport:
       → Fall back to schedule endpoint
    ↓
6. Return matched event or None
```

## ESPN API Endpoint Analysis

### Scoreboard Coverage by Sport

| Sport | Scoreboard Coverage | Notes |
|-------|---------------------|-------|
| NFL | ✅ Complete | All games for any date |
| NBA | ✅ Complete | All games for any date |
| NHL | ✅ Complete | All games for any date |
| MLB | ✅ Complete | All games for any date |
| Soccer (all leagues) | ✅ Complete | All games for any date |
| NCAAM | ✅ Complete* | Requires `groups=50` parameter for all D1 games |
| NCAAW | ✅ Complete* | Requires `groups=50` parameter for all D1 games |
| NCAAF | ⚠️ TBD | Needs testing with groups parameter |
| College Hockey | ⚠️ TBD | Needs testing with groups parameter |

**Key Discovery**: Adding `groups=50` (Division I) to college basketball scoreboard requests returns ALL D1 games, not just featured ones:
- Without `groups=50`: ~5-10 featured games
- With `groups=50`: ~48-61 games (all D1 games for that date)

**Important**: Scoreboard dates are UTC-based. A game at 8:00 PM EST on Dec 20 appears on the Dec 20 scoreboard (since 8 PM EST = 1:00 AM UTC Dec 21, but ESPN uses the local date).

### College Sports Groups Parameter

The `groups` parameter unlocks full scoreboard data for college sports:

| Sport | League Code | Group ID | Effect |
|-------|-------------|----------|--------|
| NCAAM | mens-college-basketball | `50` | All D1 games (48-61 vs 5-10 without) |
| NCAAW | womens-college-basketball | `50` | All D1 games |
| NCAAF | college-football | `80` | FBS games (needs testing) |
| College Hockey | mens-college-hockey | `50` | D1 games (needs testing) |

**Example**:
```
# Without groups - only 5-10 featured games
/scoreboard?dates=20251220

# With groups=50 - all 48-61 D1 games
/scoreboard?dates=20251220&groups=50
```

**This is a major finding**: With the correct `groups` parameter, we can get complete scoreboard data for college sports, eliminating the need for schedule endpoint fallback.

### Data Comparison: Scoreboard vs Schedule

**Scoreboard HAS (that Schedule lacks):**
- `odds` - Full betting lines (spread, moneyline, over/under)
- `leaders` - Game leaders (passing yards, rushing, etc.)
- `geoBroadcasts` - Detailed regional broadcast info
- `weather` - Outdoor game weather conditions

**Both endpoints provide:**
- `venue` - Full venue info (name, city, state)
- `broadcasts` - TV network info
- `competitors[].team` - Team name, abbreviation, logo, ID
- `competitors[].records` - Team records
- `date`, `status` - Game timing and state

**Schedule HAS (that Scoreboard lacks):**
- Historical games (for h2h, streaks calculation)
- Full season schedule

## Variable Impact Analysis

### Event-Based Template Variables

Event-based EPG uses a SUBSET of team-based variables. Key categories:

#### 1. Game-Level Variables (FROM EVENT DATA)
These come directly from the matched event - **no change needed**:
- `{home_team}`, `{away_team}`, `{home_team_abbrev}`, `{away_team_abbrev}`
- `{home_team_logo}`, `{away_team_logo}`
- `{venue}`, `{venue_city}`, `{venue_state}`
- `{broadcast}`, `{broadcast_simple}`
- `{game_date}`, `{game_time}`, `{game_datetime}`
- `{event_name}`, `{event_short_name}`

#### 2. Odds Variables (SCOREBOARD ONLY - GAIN)
Currently may be missing in schedule-first flow:
- `{odds_spread}`, `{odds_moneyline}`, `{odds_over_under}`
- `{odds_provider}`, `{favorite}`, `{underdog}`

**With scoreboard-first: These will be MORE reliably populated.**

#### 3. Team Stats Variables (FROM TEAM API)
These require separate `/teams/{id}` calls regardless of scoreboard vs schedule:
- `{home_team_record}`, `{away_team_record}`
- `{home_team_rank}`, `{away_team_rank}` (college)
- `{home_team_streak}`, `{away_team_streak}`
- Conference/division info

**No change needed** - these come from team info API, not schedule.

#### 4. Team-Centric Variables (NOT USED IN EVENT-BASED)
These are team-based EPG specific and NOT relevant for event-based:
- `{opponent}`, `{team_name}` (requires "our team" context)
- `{h2h_*}`, `{season_series_*}` (requires historical schedule)
- `{last_5_record}`, `{last_10_record}` (requires historical schedule)
- `{rematch_*}` (requires previous game lookup)

**These are already empty/unused in event-based templates.**

### Variables to Prune from Event-Based Flow

The following variables should be formally excluded from event-based template documentation since they require team-centric historical data:

```
# Team-centric (require "our team" perspective)
opponent, opponent_abbrev, opponent_record, opponent_rank
team_name, team_abbrev, team_record (ambiguous without team context)

# Historical/H2H (require schedule history)
h2h_record, h2h_wins, h2h_losses
season_series_record, season_series_wins, season_series_losses
last_meeting_result, last_meeting_score, last_meeting_date
rematch_result, rematch_opponent_score

# Streak calculations (require schedule history)
last_5_record, last_10_record
home_streak, away_streak (team-specific)
```

## Implementation Plan

### Design Principles

1. **Scoreboard-first, schedule-fallback** - Try scoreboard (fast, cached), fall back to schedule when needed
2. **Preserve Tier 4 logic** - Critical for disambiguation and one-team-only scenarios
3. **Lazy caching** - Populate scoreboard cache on-demand as leagues are encountered
4. **Graceful degradation** - D2/D3/NAIA/exhibitions fall back to schedule seamlessly

### Phase 1: Scoreboard Cache Enhancement

**File: `api/espn_client.py`**

#### 1.1 Enhanced Scoreboard Fetching with Groups Parameter
```python
# Group IDs for college sports (D1 = full scoreboard)
COLLEGE_GROUP_IDS = {
    'mens-college-basketball': '50',
    'womens-college-basketball': '50',
    'college-football': '80',  # FBS
    'mens-college-hockey': '50',
    'womens-college-hockey': '50',
}

def get_scoreboard(self, sport: str, league: str, date_str: str) -> Optional[Dict]:
    """
    Fetch scoreboard for a league/date, using groups param for college sports.
    """
    # Check class-level cache first
    cache_key = (sport, league, date_str)
    if cache_key in self._scoreboard_cache:
        return self._scoreboard_cache[cache_key]

    url = f"{self.base_url}/{sport}/{league}/scoreboard?dates={date_str}"

    # Add groups param for college sports to get ALL games
    if league in COLLEGE_GROUP_IDS:
        url += f"&groups={COLLEGE_GROUP_IDS[league]}"

    result = self._make_request(url)
    self._scoreboard_cache[cache_key] = result
    return result
```

#### 1.2 Scoreboard Search by Team IDs
```python
def find_event_in_scoreboard_cache(
    self,
    sport: str,
    league: str,
    team1_id: str,
    team2_id: str,
    date_str: str
) -> Optional[Dict]:
    """
    Search cached scoreboard for an event matching two teams.
    Returns matched event or None.
    """
    scoreboard = self.get_scoreboard(sport, league, date_str)  # Uses cache
    if not scoreboard:
        return None

    for event in scoreboard.get('events', []):
        competitors = event.get('competitions', [{}])[0].get('competitors', [])
        event_team_ids = {str(c.get('team', {}).get('id', '')) for c in competitors}

        if str(team1_id) in event_team_ids and str(team2_id) in event_team_ids:
            return event

    return None

def find_team_event_in_scoreboard_cache(
    self,
    sport: str,
    league: str,
    team_id: str,
    date_str: str
) -> List[Dict]:
    """
    Search cached scoreboard for all events involving a single team.
    Used for Tier 4 matching (one team known, looking for opponent).
    Returns list of matching events.
    """
    scoreboard = self.get_scoreboard(sport, league, date_str)  # Uses cache
    if not scoreboard:
        return []

    matches = []
    for event in scoreboard.get('events', []):
        competitors = event.get('competitions', [{}])[0].get('competitors', [])
        event_team_ids = {str(c.get('team', {}).get('id', '')) for c in competitors}

        if str(team_id) in event_team_ids:
            matches.append(event)

    return matches
```

### Phase 2: Event Matcher Refactor

**File: `epg/event_matcher.py`**

#### 2.1 New Primary Method: `find_event_scoreboard_first()`
```python
def find_event_scoreboard_first(
    self,
    team1_id: str,
    team2_id: str,
    sport: str,
    league: str,
    target_date: date = None,
    days_range: int = 8,
    include_final: bool = True
) -> Optional[Dict]:
    """
    Find event using scoreboard-first strategy with schedule fallback.

    Strategy:
    1. Search scoreboard cache for team matchup (across date range)
    2. If not found → Fall back to schedule endpoint
    3. Return matched event or None

    The fallback handles:
    - D2/D3/NAIA games not on D1 scoreboard
    - Exhibitions, postponed games
    - Any scoreboard gaps
    """
    # Determine date range to search
    if target_date:
        dates_to_check = [target_date]
    else:
        today = date.today()
        dates_to_check = [today + timedelta(days=i) for i in range(days_range)]

    # FAST PATH: Check scoreboard cache
    for check_date in dates_to_check:
        date_str = check_date.strftime('%Y%m%d')
        event = self.espn_client.find_event_in_scoreboard_cache(
            sport, league, team1_id, team2_id, date_str
        )
        if event:
            if include_final or not self._is_completed(event):
                logger.debug(f"[SCOREBOARD HIT] Found {team1_id} vs {team2_id} on {date_str}")
                return event

    # SLOW PATH: Fall back to schedule endpoint
    logger.debug(f"[SCOREBOARD MISS] Falling back to schedule for {team1_id} vs {team2_id}")
    return self._search_schedule_for_matchup(
        team1_id, team2_id, sport, league, target_date, include_final
    )
```

#### 2.2 Tier 4 Support: Single-Team Scoreboard Search
```python
def find_team_events_scoreboard_first(
    self,
    team_id: str,
    sport: str,
    league: str,
    target_date: date = None,
    days_range: int = 8
) -> List[Dict]:
    """
    Find all events for a single team using scoreboard-first strategy.
    Used for Tier 4 matching when only one team is resolved.

    Returns list of events, which caller can then filter by opponent name.
    Falls back to schedule if scoreboard has no matches.
    """
    if target_date:
        dates_to_check = [target_date]
    else:
        today = date.today()
        dates_to_check = [today + timedelta(days=i) for i in range(days_range)]

    # FAST PATH: Check scoreboard cache
    all_events = []
    for check_date in dates_to_check:
        date_str = check_date.strftime('%Y%m%d')
        events = self.espn_client.find_team_event_in_scoreboard_cache(
            sport, league, team_id, date_str
        )
        all_events.extend(events)

    if all_events:
        logger.debug(f"[SCOREBOARD HIT] Found {len(all_events)} events for team {team_id}")
        return all_events

    # SLOW PATH: Fall back to schedule endpoint
    logger.debug(f"[SCOREBOARD MISS] Falling back to schedule for team {team_id}")
    return self._get_team_schedule_events(team_id, sport, league, target_date, days_range)
```

### Phase 3: Multi-Sport Matcher Integration

**File: `epg/multi_sport_matcher.py`**

#### 3.1 Current Tiered Detection System

```
Detection Tiers (from league_detector.py):

Tier 1: League indicator + Teams → Direct match
        e.g., "NHL: Predators vs Panthers" → NHL

Tier 2: Sport indicator + Teams → Match within sport's leagues
        e.g., "Hockey: Predators vs Panthers" → search hockey leagues

Tier 3a: Both teams in cache + Date + Time + GAME FOUND → Exact schedule match
Tier 3b: Both teams in cache + Time only + GAME FOUND → Infer today, schedule match
Tier 3c: Both teams in cache + GAME FOUND → Closest game to now

Tier 4a: Both teams in cache but NO GAME between them
         → Search schedules for RAW opponent name
         (handles wrong team match, e.g., "IU East" → "IU Indianapolis")

Tier 4b: One team in cache + Date/Time
         → Search schedule for opponent by name, exact time

Tier 4c: One team in cache only
         → Search schedule for opponent by name, closest game

Tier 4b+: Fallback when Tier 3 found teams but no game
          → Search each team's schedule across ALL their leagues
```

#### 3.2 Updated Flow with Scoreboard-First

```
Stream: "ESPN+ 76 : Rider vs. Quinnipiac @ Dec 07 02:00 PM"

TIER 3a/3b/3c (Both teams resolved, looking for game):
├── TeamLeagueCache lookup: Rider → NCAAM (id: 2520)
├── TeamLeagueCache lookup: Quinnipiac → NCAAM (id: 2514)
├── Same league! Call find_event_scoreboard_first(2520, 2514, "mens-college-basketball")
│   ├── FAST PATH: Check scoreboard cache for Dec 7 → HIT! Return event
│   └── SLOW PATH: If miss → fall back to schedule automatically
└── Return matched event

TIER 4a (Both teams resolved, but NO GAME between them):
├── Example: "Miami vs Florida Atlantic"
│   ├── "Miami" resolves to Miami OH (2414) in NCAAM
│   ├── "Florida Atlantic" resolves to FAU (2226) in NCAAM
│   ├── Tier 3: find_event_scoreboard_first(2414, 2226) → NO MATCH (wrong Miami!)
│   └── Progress to Tier 4a...
│
├── Search each team's events for RAW opponent name:
│   ├── find_team_events_scoreboard_first(2414, NCAAM) → Get Miami OH's games
│   │   └── Check if any opponent contains "Florida Atlantic" → NO
│   ├── find_team_events_scoreboard_first(2226, NCAAM) → Get FAU's games
│   │   └── Check if any opponent contains "Miami" → YES! Miami FL (2390)
│   │   └── (If scoreboard miss → fall back to schedule for this team)
│   └── Return correct event: Miami FL vs FAU

TIER 4b/4c (Only one team in cache):
├── Example: "Yale vs Montana State" (NAIA game)
│   ├── "Yale" resolves to Yale (43) in mens-college-basketball
│   ├── "Montana State" → NOT in D1 cache (it's NAIA)
│   └── Progress to Tier 4b/4c...
│
├── Search known team's events for unknown opponent name:
│   ├── find_team_events_scoreboard_first(43, NCAAM) → Get Yale's games
│   │   └── Check if any opponent contains "Montana State" → Found!
│   │   └── (If scoreboard miss → fall back to schedule for Yale)
│   └── Return matched event

TIER 4b+ (Fallback across ALL leagues):
├── When Tier 3/4 fails, search each team's schedules across ALL their leagues
├── Uses schedule endpoint (not scoreboard) since we're searching exhaustively
└── Handles multi-league teams like soccer clubs in domestic + European competitions
```

#### 3.3 Code Changes

```python
# ============================================================
# TIER 3a/3b/3c: Both teams resolved, find game between them
# ============================================================
def _find_event_tier3(self, team1_id, team2_id, league, target_date, target_time):
    """
    Tier 3: Both teams resolved, try direct matchup.
    Uses scoreboard-first with schedule fallback.
    """
    sport = self._get_sport_for_league(league)
    return self.event_matcher.find_event_scoreboard_first(
        team1_id, team2_id, sport, league, target_date
    )

# ============================================================
# TIER 4a: Both teams resolved but NO GAME - search by raw name
# ============================================================
def _find_event_tier4a(self, team1_info, team2_info, raw_team1, raw_team2, target_date):
    """
    Tier 4a: Both teams matched but to WRONG teams (no game between them).
    Search each team's events for the RAW opponent name string.
    """
    # Try team1's events, looking for raw_team2 name
    for team_entry in team1_info:
        sport = self._get_sport_for_league(team_entry['league'])
        events = self.event_matcher.find_team_events_scoreboard_first(
            team_entry['team_id'], sport, team_entry['league'], target_date
        )
        for event in events:
            opponent = self._get_opponent_from_event(event, team_entry['team_id'])
            if self._name_matches(raw_team2, opponent):
                return event

    # Try team2's events, looking for raw_team1 name
    for team_entry in team2_info:
        sport = self._get_sport_for_league(team_entry['league'])
        events = self.event_matcher.find_team_events_scoreboard_first(
            team_entry['team_id'], sport, team_entry['league'], target_date
        )
        for event in events:
            opponent = self._get_opponent_from_event(event, team_entry['team_id'])
            if self._name_matches(raw_team1, opponent):
                return event

    return None

# ============================================================
# TIER 4b/4c: One team resolved, search for unknown opponent
# ============================================================
def _find_event_tier4bc(self, team_id, league, opponent_name, target_date, target_time):
    """
    Tier 4b/4c: One team resolved, search for opponent by name.
    4b = exact time match, 4c = closest game.
    """
    sport = self._get_sport_for_league(league)

    # Get all events for the known team (scoreboard-first with fallback)
    events = self.event_matcher.find_team_events_scoreboard_first(
        team_id, sport, league, target_date
    )

    # Search events for opponent name match
    matches = []
    for event in events:
        opponent = self._get_opponent_from_event(event, team_id)
        if self._name_matches(opponent_name, opponent):
            matches.append(event)

    if not matches:
        return None

    # Tier 4b: If we have target_time, find exact match
    if target_time:
        for event in matches:
            if self._time_matches(event, target_time):
                return event  # Tier 4b exact time match

    # Tier 4c: Return closest game to now
    return self._select_closest_game(matches)

# ============================================================
# TIER 4b+: Exhaustive fallback across ALL leagues
# ============================================================
def _find_event_tier4b_plus(self, team1_entries, team2_entries, raw_team1, raw_team2):
    """
    Tier 4b+ fallback: Search each team's schedule across ALL their leagues.
    This is the last resort - uses schedule endpoint directly for exhaustive search.

    NOTE: This tier intentionally uses schedule endpoint (not scoreboard)
    because it's searching across ALL leagues a team plays in, which may
    include leagues we haven't fetched scoreboards for.
    """
    # Existing implementation - no change needed
    # This is already schedule-based and should remain so
    pass
```

**Key Design Decisions:**

1. **Tiers 3, 4a, 4b, 4c**: Use `scoreboard-first` with automatic schedule fallback
2. **Tier 4b+**: Remains schedule-only (exhaustive search across all leagues)
3. **Fallback is transparent**: Calling code doesn't know/care if scoreboard or schedule was used
4. **Cache benefits compound**: Multiple streams hitting same league share cached scoreboards

### Phase 4: Caching Strategy

**No prefetching** - Scoreboards are cached lazily as encountered:

```python
# Per-generation cache (cleared at start of each EPG generation)
# Already exists as ESPNClient._scoreboard_cache

# Cache population flow:
# 1. First NCAAM stream → fetch NCAAM scoreboard for Dec 7, cache it
# 2. Second NCAAM stream → cache hit
# 3. First NFL stream → fetch NFL scoreboard for Dec 7, cache it
# 4. etc.

# Cache key: (sport, league, date_str)
# Example entries:
#   ("basketball", "mens-college-basketball", "20251207"): {...}
#   ("football", "nfl", "20251207"): {...}
```

**Why lazy over prefetch:**
- Don't know which leagues will be needed until we process streams
- Multi-sport groups have unpredictable league distribution
- Lazy caching naturally handles whatever leagues appear

### Phase 5: Variable Enrichment Refactor

**File: `epg/event_enricher.py`**

#### 5.1 Ensure Team Stats Are Fetched Separately
The enricher should continue to fetch team stats via `/teams/{id}` endpoint for:
- Team records (already in scoreboard, but verify)
- Team ranks (college sports)
- Conference/division info
- Streaks

```python
def enrich_event(self, event: Dict, sport: str, league: str) -> Dict:
    """
    Enrich event with additional data.

    Data sources:
    1. Event itself (from scoreboard) - venue, broadcast, odds, date
    2. Team info API - ranks, conference, records (if not in event)
    3. Skip: schedule-based h2h, streaks (not relevant for event-based)
    """
```

### Phase 6: Documentation Updates

#### 6.1 Update `config/variables.json`
Add `event_based_only: false` flag to variables not supported in event-based:
```json
{
  "name": "h2h_record",
  "event_based_only": false,
  "note": "Requires team-centric historical data, not available in event-based EPG"
}
```

#### 6.2 Update UI Variable Picker
Filter out team-centric variables when editing event-based templates.

## Performance Projections

### Current State
- ~1200 schedule API calls per generation
- ~45KB average response size
- ~54MB total data transfer for schedules
- ~88 seconds for team-based EPG generation

### Projected State (Scoreboard-First)

**Assumptions:**
- ESPN+ group: ~200 streams across ~25 leagues
- 8-day lookahead window
- ~30% of streams are D2/D3/NAIA (require schedule fallback)

**API Call Breakdown:**
| Category | Current | Projected | Notes |
|----------|---------|-----------|-------|
| Pro sports (NFL/NBA/NHL/Soccer) | ~300 schedule | ~40 scoreboard | 8 days × 5 leagues |
| College D1 (NCAAM/NCAAW/CFB) | ~600 schedule | ~80 scoreboard | 8 days × 10 leagues |
| D2/D3/NAIA/other | ~300 schedule | ~300 schedule | Fallback required |
| **Total** | **~1200** | **~420** | **65% reduction** |

**Data Transfer:**
- Scoreboard: ~120 calls × 20KB = ~2.4MB
- Schedule fallback: ~300 calls × 45KB = ~13.5MB
- **Total: ~16MB** (vs ~54MB currently = **70% reduction**)

### For Pro-Sports-Only Groups (NFL, NBA, NHL, Soccer)
- **~95% reduction in API calls**
- 0 schedule calls needed
- Scoreboard data already richer (has odds, weather)

### For College-Heavy Groups (ESPN+)
- **~50-65% reduction in API calls**
- D1 games hit scoreboard cache
- D2/D3/NAIA fall back to schedule (unavoidable)
- Cache hit rate improves as more streams share leagues

### Worst Case: All D2/D3/NAIA
- No improvement (all fall back to schedule)
- No regression either (same behavior as current)
- Scoreboard check adds minimal overhead (~10ms per miss)

## Safe Implementation Strategy

### Overview

The change is **surgical**: we invert the search order in `find_event()` from:
```
Schedule(team1) → Schedule(team2) → Scoreboard
```
to:
```
Scoreboard → Schedule(team1) → Schedule(team2)
```

This is a **single method change** in `event_matcher.py`. All callers (`multi_sport_matcher.py`, `league_detector.py`, etc.) continue to call `find_event()` exactly as before.

### Step 1: Add `groups` Parameter to `get_scoreboard()` (espn_client.py)

**Current code (line 627):**
```python
url = f"{self.base_url}/{sport}/{league}/scoreboard?dates={date}"
```

**New code:**
```python
# Group IDs for college sports (D1 = full scoreboard)
COLLEGE_SCOREBOARD_GROUPS = {
    'mens-college-basketball': '50',
    'womens-college-basketball': '50',
    'college-football': '80',
    'mens-college-hockey': '50',
    'womens-college-hockey': '50',
}

def get_scoreboard(self, sport: str, league: str, date: str = None) -> Optional[Dict]:
    # ... existing cache check code ...

    # Build URL with optional groups param for college sports
    url = f"{self.base_url}/{sport}/{league}/scoreboard?dates={date}"
    if league in COLLEGE_SCOREBOARD_GROUPS:
        url += f"&groups={COLLEGE_SCOREBOARD_GROUPS[league]}"

    result = self._make_request(url)
    # ... existing cache store code ...
```

**Risk**: LOW - Only adds query param, no functional change to caching or return structure.

### Step 2: Invert Search Order in `find_event()` (event_matcher.py)

**Current code (lines 498-521):**
```python
# Try team1's schedule first (future games)
matching_events, skip_reason, error = self._search_team_schedule(
    team1_id, team2_id, sport, api_league, include_final_events
)

# If no match found on team1's schedule, try team2's schedule
if not matching_events and not skip_reason:
    matching_events, skip_reason, error = self._search_team_schedule(
        team2_id, team1_id, sport, api_league, include_final_events
    )

# Scoreboard fallback
if not matching_events and not skip_reason:
    matching_events, skip_reason, error = self._search_scoreboard(
        team1_id, team2_id, sport, api_league, include_final_events
    )
```

**New code:**
```python
# SCOREBOARD FIRST: Check scoreboard (fast, cached, has today's games)
logger.debug(f"[TRACE] Checking scoreboard for {team1_id} vs {team2_id}")
matching_events, skip_reason, error = self._search_scoreboard(
    team1_id, team2_id, sport, api_league, include_final_events
)

if matching_events:
    logger.debug(f"[SCOREBOARD HIT] Found {team1_id} vs {team2_id} in {league}")

# SCHEDULE FALLBACK: If not on scoreboard (D2/D3/NAIA, far future, etc.)
if not matching_events and not skip_reason:
    logger.debug(f"[SCOREBOARD MISS] Falling back to schedule for {team1_id} vs {team2_id}")

    # Try team1's schedule
    matching_events, skip_reason, error = self._search_team_schedule(
        team1_id, team2_id, sport, api_league, include_final_events
    )

    # If no match, try team2's schedule
    if not matching_events and not skip_reason:
        matching_events, skip_reason, error = self._search_team_schedule(
            team2_id, team1_id, sport, api_league, include_final_events
        )
        if matching_events:
            logger.info(f"[SCHEDULE FALLBACK] Found via team2 ({team2_id}) schedule")
```

**Risk**: LOW - Same methods called, same return values, just different order.

### Step 3: No Changes Required to These Files

| File | Why No Change |
|------|---------------|
| `multi_sport_matcher.py` | Calls `find_event()` - unchanged interface |
| `league_detector.py` | Uses its own `_search_schedules()` which already does both |
| `orchestrator.py` | Calls `EventMatcher.find_event()` |
| `event_enricher.py` | Works on events after they're found |
| `channel_lifecycle.py` | Works on events after they're found |

### Step 4: Testing Checklist

Before deploying:

1. **Unit Test: Pro Sports**
   - NFL game today → should hit scoreboard
   - NBA game tomorrow → should hit scoreboard
   - NHL game next week → should hit scoreboard

2. **Unit Test: College D1**
   - NCAAM game today (with groups=50) → should hit scoreboard
   - NCAAW game tomorrow → should hit scoreboard
   - Verify groups param is in URL

3. **Unit Test: D2/D3/NAIA (Schedule Fallback)**
   - D2 basketball game → scoreboard miss → schedule fallback → found
   - NAIA game → scoreboard miss → schedule fallback → found

4. **Integration Test: Full EPG Generation**
   - Run EPG generation
   - Compare match rates before/after
   - Compare generation time
   - Check logs for SCOREBOARD HIT vs SCOREBOARD MISS ratio

5. **Edge Cases**
   - Timezone edge case (game at 11pm EST = next day UTC)
   - Postponed game
   - Double-header disambiguation

### Step 5: Logging for Validation

Add counters to track scoreboard vs schedule usage:

```python
# In EventMatcher.__init__():
self._scoreboard_hits = 0
self._scoreboard_misses = 0
self._schedule_fallbacks = 0

# In find_event():
if matching_events and came_from_scoreboard:
    self._scoreboard_hits += 1
else:
    self._scoreboard_misses += 1
    if matching_events:
        self._schedule_fallbacks += 1

# Log summary at end of EPG generation
logger.info(f"Event matching stats: scoreboard_hits={self._scoreboard_hits}, "
            f"scoreboard_misses={self._scoreboard_misses}, "
            f"schedule_fallbacks={self._schedule_fallbacks}")
```

### Rollback Plan

If issues occur:
1. Revert the search order in `find_event()` back to schedule-first
2. Keep the `groups` parameter change (it's safe and improves college coverage)

### Files Changed Summary

| File | Change | Lines |
|------|--------|-------|
| `api/espn_client.py` | Add `groups` param for college | ~5 lines |
| `epg/event_matcher.py` | Invert search order | ~15 lines |

**Total: ~20 lines changed**

## Testing Plan

### Unit Tests
1. `test_scoreboard_event_search()` - Find known games
2. `test_scoreboard_miss_schedule_fallback()` - College sports fallback
3. `test_scoreboard_cache_efficiency()` - Verify cache hits

### Integration Tests
1. Run full EPG generation with scoreboard-first
2. Compare matched events vs schedule-based approach
3. Verify all template variables still populate correctly

### Performance Tests
1. Measure API call reduction
2. Measure generation time reduction
3. Monitor cache hit rates

## Risks and Mitigations

### Risk 1: Scoreboard Missing Games
**Mitigation**: Always fall back to schedule for college sports. Monitor for any pro sports gaps.

### Risk 2: Variable Population Regression
**Mitigation**: Comprehensive testing of all event-based templates before/after.

### Risk 3: Cache Staleness
**Mitigation**: Scoreboards are already cached per-generation with appropriate TTL.

## Open Questions

1. **College Sports Full Schedule**: Is there an alternative ESPN endpoint that returns all college games for a date? (Research needed)

2. **Scoreboard Pagination**: Does ESPN scoreboard have hidden pagination for days with many games? (Appears not based on testing)

3. **International Soccer**: Some minor leagues may have incomplete scoreboards - need to test coverage.

## Appendix: ESPN Endpoint Reference

### Scoreboard
```
GET /apis/site/v2/sports/{sport}/{league}/scoreboard?dates={YYYYMMDD}
```
- Returns all games for pro sports
- Returns only featured games for college sports
- Includes odds, weather, leaders

### Schedule (Team-Specific)
```
GET /apis/site/v2/sports/{sport}/{league}/teams/{id}/schedule
```
- Returns full season schedule for one team
- No odds, limited broadcast info
- Required for college sports matching

### Team Info
```
GET /apis/site/v2/sports/{sport}/{league}/teams/{id}
```
- Returns team details, record, rank, conference
- Still needed for variable enrichment regardless of matching strategy
