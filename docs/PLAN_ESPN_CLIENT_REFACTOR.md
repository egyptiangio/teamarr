# ESPN Client Refactor Plan

## Overview

Consolidate ESPN API interactions into a predictable, single-pass enrichment pipeline. Eliminate duplicate API calls, scattered normalization logic, and inconsistent data structures.

**Current State**: ESPN data flows through 3+ different enrichment paths (orchestrator, event_matcher, espn_client) with duplicate API calls, scattered normalization, and no unified "enriched event" concept.

**Target State**: Single `EventEnricher` class that produces fully-enriched events in one pass, with smart caching to eliminate redundant API calls.

---

## Goals

1. **Single enrichment path** - All event enrichment goes through one pipeline
2. **Eliminate duplicate API calls** - No more calling `get_team_stats()` twice or fetching same scoreboard multiple times
3. **Consistent data structure** - Every enriched event has the same shape, regardless of source
4. **Smart caching** - Scoreboard cached by date, team stats cached with TTL, enriched events cached by ID
5. **Backwards compatible** - Keep dict-based structures, no breaking changes to template engine

---

## Architecture

### Current Flow (Problematic)

```
                    ┌─────────────────┐
                    │   ESPNClient    │
                    │ (raw API calls) │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ↓                    ↓                    ↓
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  orchestrator │    │ event_matcher │    │   app.py      │
│               │    │               │    │   routes      │
│ - _normalize_ │    │ - enrich_     │    │               │
│   event()     │    │   event_with_ │    │ (no enrich)   │
│ - _enrich_    │    │   scoreboard  │    │               │
│   from_score- │    │ - enrich_     │    │               │
│   board()     │    │   with_team_  │    │               │
│ - _build_full │    │   stats()     │    │               │
│   _game_ctx() │    │               │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
        │                    │                    │
        ↓                    ↓                    ↓
   [Different event structures, different enrichment levels]
```

### Target Flow (Consolidated)

```
                    ┌─────────────────┐
                    │   ESPNClient    │
                    │ (raw API only)  │
                    │                 │
                    │ - get_scoreboard│
                    │ - get_schedule  │
                    │ - get_team_stats│
                    │ - get_team_info │
                    └────────┬────────┘
                             │
                             ↓
                    ┌─────────────────┐
                    │  EventEnricher  │
                    │ (single pipeline)│
                    │                 │
                    │ - enrich_event()│
                    │ - Caching layer │
                    │ - Normalization │
                    └────────┬────────┘
                             │
                             ↓
                    ┌─────────────────┐
                    │ EnrichedEvent   │
                    │ (consistent dict)│
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ↓                    ↓                    ↓
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  orchestrator │    │ event_matcher │    │   app.py      │
│               │    │               │    │   routes      │
│ (uses enriched│    │ (uses enriched│    │               │
│  events)      │    │  events)      │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
```

---

## Enriched Event Structure

The canonical structure for a fully-enriched event (dict-based for backwards compatibility):

```python
{
    # === Identity ===
    'id': '401234567',                    # ESPN event ID
    'uid': 's:20~l:28~e:401234567',       # ESPN UID
    'name': 'Detroit Lions at Chicago Bears',
    'short_name': 'DET @ CHI',

    # === Timing ===
    'date': '2025-12-06T18:00Z',          # ISO8601 string (raw from ESPN)
    'start_datetime': datetime(...),       # Python datetime (parsed)

    # === Status ===
    'status': {
        'name': 'STATUS_SCHEDULED',        # or STATUS_IN_PROGRESS, STATUS_FINAL
        'state': 'pre',                    # pre, in, post
        'completed': False,
        'detail': 'Sat, December 6th at 1:00 PM EST',
        'period': 0,                       # quarter/period number
    },

    # === Teams (fully enriched) ===
    'home_team': {
        'id': '3',
        'name': 'Chicago Bears',
        'abbrev': 'CHI',
        'logo': 'https://a.espncdn.com/...png',
        'color': '0B162A',
        'alt_color': 'C83803',
        'score': None,                     # or int if in-progress/final
        'record': {
            'summary': '4-8',
            'wins': 4,
            'losses': 8,
            'ties': 0,
        },
        'home_record': {'summary': '2-4', ...},
        'away_record': {'summary': '2-4', ...},
        'streak': {'type': 'loss', 'length': 3},
        'conference': 'NFC',
        'division': 'NFC North',
        'rank': 0,                         # AP/CFP rank (0 if unranked)
        'seed': 0,                         # playoff seed
    },
    'away_team': {
        # Same structure as home_team
    },

    # === Venue ===
    'venue': {
        'name': 'Soldier Field',
        'city': 'Chicago',
        'state': 'IL',
        'indoor': False,
    },

    # === Broadcast ===
    'broadcasts': ['FOX', 'NFL Network'],  # List of strings, normalized

    # === Odds (if available) ===
    'odds': {
        'spread': -3.5,                    # negative = home favored
        'over_under': 45.5,
        'home_ml': -175,
        'away_ml': +155,
        'provider': 'consensus',
    },
    'has_odds': True,

    # === Sport/League Context ===
    'sport': 'football',
    'league': 'nfl',

    # === Enrichment Metadata ===
    '_enrichment': {
        'is_enriched': True,
        'source': 'scoreboard',            # 'schedule', 'scoreboard', 'summary'
        'enriched_at': '2025-12-05T22:00Z',
        'has_team_stats': True,
        'has_scoreboard_data': True,
    },

    # === Raw data (preserved for edge cases) ===
    'competitions': [...],                 # Original ESPN competitions array
}
```

---

## New File: `epg/event_enricher.py`

### Class Structure

```python
class EventEnricher:
    """
    Single-pass event enrichment pipeline.

    Takes raw ESPN event data from any source (schedule, scoreboard, summary)
    and produces a fully-enriched event dict with consistent structure.

    Caching:
    - Scoreboard data cached by (league, date) - cleared per generation
    - Team stats cached by team_id with 6-hour TTL
    - Enriched events cached by event_id - cleared per generation
    """

    def __init__(self, espn_client: ESPNClient, db_connection_func=None):
        self.espn = espn_client
        self.db_connection_func = db_connection_func

        # Caches (cleared per generation)
        self._scoreboard_cache: Dict[str, Dict] = {}  # key: "league:YYYYMMDD"
        self._enriched_events: Dict[str, Dict] = {}   # key: event_id

        # Thread safety
        self._scoreboard_lock = threading.Lock()
        self._enriched_lock = threading.Lock()

    # === Main Entry Points ===

    def enrich_event(
        self,
        raw_event: Dict,
        league: str,
        include_team_stats: bool = True,
        include_scoreboard: bool = True,
    ) -> Dict:
        """
        Enrich a raw ESPN event into canonical structure.

        This is the SINGLE entry point for all event enrichment.
        Handles events from schedule API, scoreboard API, or event summary.
        """
        pass

    def enrich_events_batch(
        self,
        raw_events: List[Dict],
        league: str,
        **kwargs
    ) -> List[Dict]:
        """Enrich multiple events efficiently (parallel team stats fetching)."""
        pass

    def get_enriched_event_by_id(
        self,
        event_id: str,
        league: str,
    ) -> Optional[Dict]:
        """
        Get enriched event by ESPN ID.

        Checks cache first, then tries scoreboard, then event summary endpoint.
        """
        pass

    # === Cache Management ===

    def clear_caches(self):
        """Clear all caches. Call at start of EPG generation."""
        pass

    def prefetch_scoreboards(
        self,
        league: str,
        dates: List[str],
    ):
        """
        Pre-fetch scoreboard data for multiple dates.

        Call this at start of generation to batch scoreboard fetches
        instead of fetching on-demand during enrichment.
        """
        pass

    # === Internal Methods ===

    def _normalize_event_structure(self, raw_event: Dict) -> Dict:
        """Normalize raw ESPN event into base structure."""
        pass

    def _enrich_with_scoreboard(self, event: Dict, league: str) -> Dict:
        """Add scoreboard data (odds, live scores, broadcasts)."""
        pass

    def _enrich_team(self, team: Dict, league: str) -> Dict:
        """Enrich single team with stats, logo, colors."""
        pass

    def _normalize_score(self, score: Any) -> Optional[int]:
        """Normalize score from string/int/dict to int."""
        pass

    def _normalize_broadcasts(self, broadcasts: Any) -> List[str]:
        """Normalize broadcasts to list of strings."""
        pass

    def _get_scoreboard_cached(self, league: str, date: str) -> Dict:
        """Get scoreboard with caching."""
        pass
```

---

## Implementation Phases

### Phase 1: Quick Wins (No Architecture Changes)

**Goal**: Fix immediate issues without restructuring.

**Tasks**:

1. **Remove duplicate `get_team_stats()` call**
   - Location: `orchestrator.py` line 579
   - The call at line 569 already fetches team stats
   - Impact: Eliminates 1 API call per team

2. **Add thread safety to stats cache**
   - Location: `espn_client.py` `_stats_cache`
   - Add `threading.Lock()` like schedule cache has
   - Impact: Prevents duplicate calls in parallel processing

3. **Cache scoreboard by date in orchestrator**
   - Location: `orchestrator.py` `_discover_and_enrich_from_scoreboard()`
   - Store fetched scoreboards in dict, reuse in `_enrich_past_events_with_scores()`
   - Impact: Eliminates duplicate scoreboard fetches for same date

**Estimated changes**: ~50 lines across 2 files

---

### Phase 2: Create EventEnricher Foundation

**Goal**: Create the new enrichment class with core functionality.

**Tasks**:

1. **Create `epg/event_enricher.py`**
   - Implement `EventEnricher` class
   - Implement `_normalize_event_structure()` - consolidate from orchestrator._normalize_event() and espn_client._parse_event()
   - Implement `_normalize_score()` - single place for score parsing
   - Implement `_normalize_broadcasts()` - single place for broadcast normalization

2. **Implement caching layer**
   - Scoreboard cache with thread-safe access
   - Integration with existing team stats cache in ESPNClient
   - Enriched event cache by ID

3. **Implement `enrich_event()` main method**
   - Parse raw event into base structure
   - Optionally add scoreboard data
   - Optionally add team stats
   - Return fully enriched event

4. **Add `prefetch_scoreboards()` optimization**
   - Fetch multiple dates in parallel at generation start
   - Store in cache for later use

**Estimated changes**: ~400 lines new file

---

### Phase 3: Migrate EventMatcher

**Goal**: EventMatcher uses EventEnricher instead of its own enrichment.

**Tasks**:

1. **Remove from EventMatcher**:
   - `enrich_event_with_scoreboard()` method
   - `enrich_with_team_stats()` method
   - `_enrich_single_team()` method
   - `_merge_scoreboard_data()` method

2. **Update EventMatcher to use EventEnricher**:
   - Accept `EventEnricher` instance in constructor
   - `find_and_enrich()` calls `enricher.enrich_event()` instead of multiple methods
   - Share scoreboard cache between find and enrich steps

3. **Update callers**:
   - `app.py` - pass enricher to EventMatcher
   - Factory function `create_event_matcher()` creates enricher too

**Estimated changes**: ~200 lines removed, ~50 lines added

---

### Phase 4: Migrate Orchestrator

**Goal**: Orchestrator uses EventEnricher for all event processing.

**Tasks**:

1. **Remove from EPGOrchestrator**:
   - `_normalize_event()` method
   - `_enrich_event_from_scoreboard_lookup()` method
   - `_normalize_scoreboard_broadcasts()` method
   - Local scoreboard fetching in `_discover_and_enrich_from_scoreboard()`
   - `_fetch_and_enrich_event_with_scoreboard()` method

2. **Update orchestrator to use EventEnricher**:
   - Create enricher at start of `generate_epg()`
   - Call `enricher.prefetch_scoreboards()` with all dates in EPG window
   - Use `enricher.enrich_events_batch()` for schedule events
   - Use `enricher.get_enriched_event_by_id()` for existing channel events

3. **Simplify `_process_team_schedule()`**:
   - Fetch raw schedule
   - Pass to enricher for batch enrichment
   - No more manual scoreboard merging

4. **Simplify `_generate_filler_entries()`**:
   - Remove `_enrich_last_game_with_score()` calls
   - Events already enriched, just use them

**Estimated changes**: ~400 lines removed, ~100 lines added

---

### Phase 5: Cleanup and Documentation

**Goal**: Remove dead code, update documentation.

**Tasks**:

1. **Remove unused methods from ESPNClient**:
   - `_parse_event()` - moved to EventEnricher
   - Any other methods now handled by EventEnricher

2. **Update CLAUDE.md**:
   - Document EventEnricher usage
   - Update data flow diagrams
   - Add enriched event structure reference

3. **Add type hints**:
   - TypedDict for EnrichedEvent structure
   - Better IDE support and documentation

**Estimated changes**: ~100 lines removed, ~50 lines documentation

---

## Migration Strategy

### Parallel Operation

During migration, both old and new code paths can coexist:

```python
# orchestrator.py during migration
def _process_team_schedule(self, team, ...):
    # New path (feature flagged)
    if self.use_event_enricher:
        events = self.enricher.enrich_events_batch(raw_events, league)
    else:
        # Old path (existing code)
        events = self._discover_and_enrich_from_scoreboard(...)
```

### Testing Strategy

1. **Phase 1**: Run existing tests, verify no regressions
2. **Phase 2**: Unit tests for EventEnricher methods
3. **Phase 3**: Integration test - compare EventMatcher output before/after
4. **Phase 4**: Integration test - compare full EPG output before/after
5. **Phase 5**: Remove feature flags, run full regression

### Rollback Plan

Each phase is independently deployable:
- Phase 1: Simple revert of line removals
- Phase 2: EventEnricher exists but isn't used yet
- Phase 3-4: Feature flag to switch between old/new paths

---

## API Call Reduction Analysis

### Current API Calls Per Team (Team-Based EPG)

| Call | Count | Notes |
|------|-------|-------|
| get_team_info() | 1 | Team logo |
| get_team_stats() | 2 | Duplicate! |
| get_team_schedule() | 2 | Main + extended |
| get_scoreboard() | 7-14 | Per day in window |
| get_scoreboard() (past) | 1-7 | Per past event |
| get_scoreboard() (filler) | 1-6 | Per filler block |
| get_team_stats() (opponent) | 1-5 | Per unique opponent |
| **Total** | **15-37** | Per team |

### After Refactor

| Call | Count | Notes |
|------|-------|-------|
| get_team_info() | 1 | Team logo |
| get_team_stats() | 1 | Removed duplicate |
| get_team_schedule() | 2 | Main + extended |
| get_scoreboard() | 7-14 | Per day (cached, reused) |
| get_team_stats() (opponent) | 1-5 | Per unique opponent (cached) |
| **Total** | **12-23** | Per team |

**Reduction**: ~30-40% fewer API calls

### Current API Calls Per Stream (Event-Based EPG)

| Call | Count | Notes |
|------|-------|-------|
| get_team_schedule() | 1-2 | Find event |
| get_scoreboard() | 1-7 | Soccer fallback |
| get_scoreboard() | 1 | Enrich (duplicate!) |
| get_team_info() | 2 | Both teams |
| get_team_stats() | 2 | Both teams |
| **Total** | **7-14** | Per matched stream |

### After Refactor

| Call | Count | Notes |
|------|-------|-------|
| get_team_schedule() | 1-2 | Find event |
| get_scoreboard() | 0-7 | Soccer fallback (cached) |
| get_team_info() | 2 | Both teams (cached) |
| get_team_stats() | 2 | Both teams (cached) |
| **Total** | **5-13** | Per matched stream |

**Reduction**: ~15-20% fewer API calls (more with cache hits)

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Template variables break | Medium | High | Extensive testing of all variables |
| Performance regression | Low | Medium | Benchmark before/after |
| Soccer edge cases | Medium | Medium | Test all soccer leagues specifically |
| Cache memory growth | Low | Low | Clear caches per generation |
| Thread safety issues | Low | High | Use locks consistently |

---

## Success Metrics

1. **API calls reduced** by 30%+ per generation
2. **No duplicate enrichment** - each event enriched exactly once
3. **Consistent data structure** - all events have same shape
4. **No template regressions** - all variables still work
5. **Code reduction** - net reduction of ~200+ lines

---

## Timeline Estimate

| Phase | Scope | Estimate |
|-------|-------|----------|
| Phase 1 | Quick wins | 1-2 hours |
| Phase 2 | EventEnricher foundation | 4-6 hours |
| Phase 3 | Migrate EventMatcher | 2-3 hours |
| Phase 4 | Migrate Orchestrator | 4-6 hours |
| Phase 5 | Cleanup | 1-2 hours |
| **Total** | | **12-19 hours** |

---

## Soccer-Specific Considerations

Soccer leagues have fundamentally different ESPN API behavior that must be preserved:

### Data Source Differences

| Aspect | US Sports (NFL, NBA, etc.) | Soccer (EPL, MLS, etc.) |
|--------|---------------------------|-------------------------|
| **Schedule API** | Returns past + future games | Returns **past games only** |
| **Future games** | From schedule endpoint | From **scoreboard endpoint** |
| **Record format** | W-L or W-L-T | W-D-L (draws in middle) |
| **Coach data** | Reliable | Unreliable (skip) |
| **Division data** | Reliable | Stale/unreliable (skip) |
| **Multi-league** | Single league per team | Teams play in 3-5+ competitions |

### EventEnricher Must Handle

1. **Primary data source selection**:
   ```python
   def _get_events_for_team(self, team_id: str, league: str, ...) -> List[Dict]:
       if is_soccer_league(league):
           # Soccer: scoreboard is primary source for future games
           return self._get_events_from_scoreboard(...)
       else:
           # US sports: schedule is primary source
           return self._get_events_from_schedule(...)
   ```

2. **Soccer multi-league aggregation**:
   - Soccer teams play in multiple competitions (Premier League + FA Cup + Champions League)
   - `SoccerMultiLeague` cache maps team_id → all their leagues
   - EventEnricher should fetch from ALL leagues when enriching soccer team events

3. **Record format normalization**:
   ```python
   def _format_record(self, wins: int, losses: int, draws: int, league: str) -> str:
       if is_soccer_league(league):
           return f"{wins}-{draws}-{losses}"  # W-D-L
       elif draws > 0:
           return f"{wins}-{losses}-{draws}"  # W-L-T
       else:
           return f"{wins}-{losses}"          # W-L
   ```

4. **Disabled fields for soccer**:
   ```python
   def _enrich_team(self, team: Dict, league: str) -> Dict:
       if SoccerCompat.should_skip_coach(league):
           team['head_coach'] = ''
       if SoccerCompat.should_skip_division(league):
           team['division'] = ''
           team['conference'] = ''
   ```

5. **Scoreboard-first event finding** (already implemented in Phase 1 fix):
   - `EventMatcher.find_event()` now falls back to scoreboard for soccer
   - EventEnricher should use same pattern

### Integration with SoccerMultiLeague

The existing `SoccerMultiLeague` class handles:
- Weekly cache of team → leagues mappings
- `get_team_leagues(team_id)` returns all competitions

EventEnricher should:
```python
def prefetch_scoreboards(self, team_id: str, league: str, dates: List[str]):
    if is_soccer_league(league):
        # Fetch from ALL leagues this team plays in
        all_leagues = SoccerMultiLeague.get_team_leagues(team_id)
        for league_slug in all_leagues:
            for date in dates:
                self._get_scoreboard_cached(league_slug, date)
    else:
        # Single league fetch
        for date in dates:
            self._get_scoreboard_cached(league, date)
```

---

## Dataclasses Recommendation

### Honest Assessment

**Should we transition to dataclasses?**

**No, not as part of this refactor.**

### Reasoning

1. **High Risk, Moderate Benefit**
   - Every file that accesses event data would need changes
   - Template engine uses `event.get('field')` pattern extensively
   - Risk of subtle bugs in data access patterns
   - Benefit is mainly IDE autocomplete and documentation

2. **Dict Access is Deeply Embedded**
   ```python
   # Current pattern (used hundreds of times):
   home_team = event.get('home_team', {})
   name = home_team.get('name', 'Unknown')

   # Would become:
   home_team = event.home_team
   name = home_team.name if home_team else 'Unknown'
   ```

3. **Template Engine Compatibility**
   - `template_engine.py` builds context dicts from events
   - Changing to dataclasses would require `.asdict()` conversions
   - Or rewriting template variable resolution

4. **The Real Value is Elsewhere**
   - Eliminating duplicate API calls: **High value, low risk**
   - Single enrichment path: **High value, medium risk**
   - Dataclasses: **Low value, high risk**

### Alternative: TypedDict for Documentation

We can get documentation benefits without breaking changes:

```python
from typing import TypedDict, Optional, List

class TeamDict(TypedDict, total=False):
    id: str
    name: str
    abbrev: str
    logo: Optional[str]
    color: Optional[str]
    record: dict
    # ... etc

class EnrichedEventDict(TypedDict, total=False):
    id: str
    name: str
    date: str
    home_team: TeamDict
    away_team: TeamDict
    # ... etc

def enrich_event(raw_event: Dict) -> EnrichedEventDict:
    """Returns fully enriched event."""
    pass
```

This gives:
- IDE autocomplete when accessing fields
- Documentation of expected structure
- No runtime changes (TypedDict is just a type hint)
- Full backwards compatibility

### Future Consideration

Dataclasses could be considered for a **future major version** if:
- We want to enforce immutability
- We're doing a larger rewrite anyway
- We have comprehensive test coverage to catch regressions

For now, focus on the high-value, lower-risk improvements.

---

## Open Questions

1. **Should EventEnricher be a singleton?**
   - Pro: Share caches across all callers
   - Con: Harder to test, global state
   - Recommendation: Pass instance explicitly, but create once per generation

2. **Should we move team_stats cache to EventEnricher?**
   - Pro: All caching in one place
   - Con: ESPNClient already has working cache
   - Recommendation: Keep in ESPNClient, EventEnricher just uses it

3. **How to handle partial enrichment?**
   - Some callers may not need team stats (just finding events)
   - Recommendation: `include_team_stats=False` parameter

4. **Should enriched events be immutable?**
   - Pro: Prevents accidental modification, safer caching
   - Con: Some code may modify events after enrichment
   - Recommendation: Document as "treat as immutable", enforce later if needed

---

## Implementation Progress

### Completed (December 2024)

#### Phase 1: Quick Wins ✅
- **Commit**: Removed duplicate `get_team_stats()` call in orchestrator.py (lines 569 and 579 were identical)
- **Commit**: Added `_stats_cache_lock` for thread safety in `ESPNClient.get_team_stats()`
- **Commit**: Added scoreboard caching in orchestrator (`_scoreboard_cache`, `_get_scoreboard_cached()`)
- **Files modified**: `epg/orchestrator.py`, `api/espn_client.py`

#### Phase 2: Create EventEnricher Foundation ✅
- **New file**: `epg/event_enricher.py` (~730 lines)
- **Features**:
  - `normalize_event_structure()` - Unified normalization from any ESPN source
  - `enrich_with_scoreboard()` - Add live data (odds, scores, status)
  - `enrich_with_team_stats()` - Add team context (records, conference, rank, streak)
  - `enrich_event()` - Single-pass enrichment combining all steps
  - `enrich_events_batch()` - Efficient batch processing with scoreboard prefetch
  - Thread-safe caching with double-checked locking pattern
  - Soccer-aware (uses SoccerCompat for record format, disabled fields)

#### Phase 3: Migrate EventMatcher ✅
- **Commit**: Refactored EventMatcher to use EventEnricher
- **Changes**:
  - Added `enricher` parameter to `EventMatcher.__init__`
  - Updated `find_and_enrich()` to use `enricher.enrich_event()`
  - Updated `get_event_by_id()` to use enricher
  - Updated `create_event_matcher()` factory to create EventEnricher
  - Removed ~280 lines of deprecated enrichment methods:
    - `enrich_event_with_scoreboard()`
    - `_merge_scoreboard_data()`
    - `enrich_with_team_stats()`
    - `_enrich_single_team()`
    - `_format_ordinal()`
- **Files modified**: `epg/event_matcher.py`

### Pending (Future Work)

#### Phase 4: Migrate Orchestrator (Optional) ⏸️
**Status**: Deferred due to complexity and risk

**Assessment**: The orchestrator has tightly integrated enrichment that serves multiple purposes:
1. **Discovery** - Finding games via scoreboard when schedule API is empty (soccer)
2. **Batch enrichment** - Enriching multiple events efficiently using pre-fetched scoreboard
3. **Normalization** - Converting ESPN's competitor array to home_team/away_team
4. **Filler generation** - Complex integration with `_enrich_last_game_with_score()`

**Risk factors**:
- High risk of breaking team-based EPG flow
- Orchestrator handles soccer multi-league, midnight crossover, filler generation
- Methods like `_discover_and_enrich_from_scoreboard()` combine discovery + enrichment efficiently
- Estimated 4-6 hours of work with extensive testing needed

**Recommendation**: Keep orchestrator's current enrichment infrastructure (it works). New code paths can use EventEnricher directly.

#### Phase 5: Cleanup and Documentation ⏸️
Waiting on Phase 4 decision.

---

## Current Architecture State

After Phases 1-3, the codebase has two enrichment paths:

```
┌─────────────────────────────────────────────────────────────────┐
│                        ESPNClient                                │
│              (raw API calls + team stats caching)                │
└─────────────────────┬─────────────────────┬─────────────────────┘
                      │                     │
                      ▼                     ▼
         ┌────────────────────┐   ┌────────────────────┐
         │   EventEnricher    │   │   Orchestrator     │
         │  (new unified path)│   │ (existing path)    │
         │                    │   │                    │
         │ - EventMatcher uses│   │ - Team-based EPG   │
         │ - Event-based EPG  │   │ - Filler generation│
         │   (future)         │   │ - Soccer discovery │
         └────────────────────┘   └────────────────────┘
```

Both paths work correctly. EventEnricher is available for new features and EventMatcher, while the orchestrator continues to use its proven enrichment flow.
