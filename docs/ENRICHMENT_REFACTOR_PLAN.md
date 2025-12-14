# Event Enrichment & Variable Mapping Refactor Plan

## Problem Statement

The team-based EPG flow has significant redundancies causing:
1. **Wasted API calls** - Same scoreboard fetched 6x per team during filler generation
2. **Redundant normalization** - `_normalize_event()` called 3x per event
3. **Inconsistent data** - Multiple enrichment points can produce different results
4. **Complexity** - 6 separate enrichment methods, hard to debug

The event-based EPG flow is cleaner (single-pass enrichment) and should serve as the model.

---

## Current State Summary

### Team-Based Flow (orchestrator.py)
```
Schedule API → parse → Scoreboard enrichment (per day) → Extended schedule →
Past enrichment → Context building (3x normalize) → Filler (6x enrich)
```
**API calls per team**: 24-31 base + 6 redundant filler = 30-37 total

### Event-Based Flow (event_matcher.py)
```
find_event() → enrich_event_with_scoreboard() → enrich_with_team_stats()
```
**API calls per event**: 4-5 total (no redundancy)

---

## Refactoring Phases

### Phase 1: Eliminate Filler Enrichment Redundancy (HIGH PRIORITY)

**Problem**: `_enrich_last_game_with_score()` called 6 times in `_generate_filler_entries()`:
- Lines 1313-1315 (pregame)
- Lines 1340-1342 (pregame midnight)
- Lines 1361-1363 (postgame midnight)
- Lines 1378-1380 (idle midnight)
- Lines 1397-1399 (postgame)
- Lines 1436-1438 (idle)

**Solution**:
1. Enrich last game ONCE at start of `_generate_filler_entries()`
2. Store in local variable `enriched_last_game`
3. Pass to all filler sections instead of re-enriching

**Changes**:
```python
# In _generate_filler_entries() around line 1270:
def _generate_filler_entries(self, ...):
    # ... existing setup code ...

    # Enrich last game ONCE upfront
    enriched_last_game = None
    if last_started_game:
        enriched_last_game = self._enrich_last_game_with_score(
            last_started_game,
            extended_game_schedule,
            sport,
            league
        )

    # Then pass enriched_last_game to each filler section
    # Remove the 6 individual _enrich_last_game_with_score() calls
```

**Estimated savings**: 5 API calls per team × 7 teams = 35 API calls per EPG generation

**Risk**: Low - isolated change, easy to test

---

### Phase 2: Add Normalization Tracking (MEDIUM PRIORITY)

**Problem**: `_normalize_event()` called in `_build_full_game_context()` at line 923, which is called 3x per event (current, next, last)

**Solution**:
1. Add `_is_normalized` key to event dict after normalization
2. Check flag before normalizing

**Changes**:
```python
# In _normalize_event() around line 108:
def _normalize_event(self, event: Dict) -> Dict:
    """Normalize ESPN event structure for template consumption."""
    if event.get('_is_normalized'):
        return event  # Already normalized, skip

    # ... existing normalization code ...

    event['_is_normalized'] = True
    return event
```

**Estimated savings**: CPU only (no API calls), but cleaner code path

**Risk**: Low - defensive check, won't break existing flow

---

### Phase 3: Normalize at Enrichment Time (MEDIUM PRIORITY)

**Problem**: Events are parsed, then enriched, then normalized separately. Should normalize immediately after enrichment.

**Current flow**:
```
parse_schedule_events() → [events without normalize]
_discover_and_enrich_from_scoreboard() → [enriched but not normalized]
_build_full_game_context() → normalizes (3x per event)
```

**Solution**:
1. Normalize immediately after scoreboard enrichment in `_enrich_event_from_scoreboard_lookup()`
2. Normalize immediately after extended event enrichment in `_enrich_past_events_with_scores()`
3. Store normalized events in schedules

**Changes**:
```python
# In _enrich_event_from_scoreboard_lookup() around line 427:
def _enrich_event_from_scoreboard_lookup(self, event, scoreboard_lookup, ...):
    # ... existing enrichment code ...

    # Normalize immediately after enrichment
    event = self._normalize_event(event)
    return event

# In _enrich_past_events_with_scores() around line 877:
for date, entry in extended_game_schedule.items():
    if entry.get('event'):
        # ... enrichment code ...
        entry['event'] = self._normalize_event(entry['event'])
```

**Risk**: Medium - touches core flow, needs thorough testing

---

### Phase 4: Scoreboard Caching (MEDIUM PRIORITY)

**Problem**: Same scoreboard fetched multiple times for same date across different phases

**Solution**:
1. Add `_scoreboard_cache` similar to `_schedule_cache` in ESPNClient
2. Cache key: `{sport}:{league}:{date_str}`
3. Clear at start of each EPG generation (like schedule cache)

**Changes**:
```python
# In ESPNClient class:
def __init__(self):
    # ... existing code ...
    self._scoreboard_cache = {}
    self._scoreboard_cache_lock = threading.Lock()

def get_scoreboard(self, sport, league, date_str=None):
    cache_key = f"{sport}:{league}:{date_str or 'today'}"

    with self._scoreboard_cache_lock:
        if cache_key in self._scoreboard_cache:
            return self._scoreboard_cache[cache_key]

    # ... existing API call ...

    with self._scoreboard_cache_lock:
        self._scoreboard_cache[cache_key] = result

    return result

def clear_scoreboard_cache(self):
    with self._scoreboard_cache_lock:
        self._scoreboard_cache.clear()
```

**Estimated savings**: Varies by schedule, typically 5-10 duplicate calls per EPG

**Risk**: Low - same pattern as schedule cache

---

### Phase 5: Consolidate Enrichment Methods (LOW PRIORITY - FUTURE)

**Problem**: 6 separate enrichment methods in orchestrator.py:
- `_discover_and_enrich_from_scoreboard()`
- `_enrich_event_from_scoreboard_lookup()`
- `_fetch_and_enrich_event_with_scoreboard()`
- `_enrich_past_events_with_scores()`
- `_enrich_last_game_with_score()`
- Plus `_normalize_event()` called separately

**Long-term solution**: Create unified `EventEnricher` class like event-based flow:
```python
class EventEnricher:
    """Single-pass event enrichment (modeled after event_matcher.py pattern)"""

    def enrich(self, event: Dict, league: str) -> Dict:
        """Enrich and normalize event in one pass."""
        # 1. Fetch scoreboard data
        # 2. Merge scoreboard data
        # 3. Normalize
        # 4. Mark as enriched
        return event

    def enrich_batch(self, events: List[Dict], league: str) -> List[Dict]:
        """Batch enrich events, sharing scoreboard lookups."""
        # Group events by date
        # Fetch scoreboards once per date
        # Enrich all events for that date
        return events
```

**Risk**: High - major refactor, defer to future release

---

## Implementation Order

| Phase | Priority | Effort | Risk | Savings |
|-------|----------|--------|------|---------|
| 1. Filler redundancy | HIGH | Low | Low | 35 API calls/gen |
| 2. Normalization flag | MEDIUM | Low | Low | CPU, cleaner code |
| 3. Normalize at enrich | MEDIUM | Medium | Medium | CPU, consistency |
| 4. Scoreboard cache | MEDIUM | Low | Low | 5-10 API calls/gen |
| 5. Consolidate methods | LOW | High | High | Maintainability |

**Recommended approach**:
1. Phase 1 first (biggest impact, lowest risk)
2. Phase 2 + 4 together (both are defensive additions)
3. Phase 3 after thorough testing of 1-2
4. Phase 5 deferred to major version

---

## Testing Strategy

### Phase 1 Testing
- [ ] Generate EPG with filler enabled, verify filler content unchanged
- [ ] Check logs for scoreboard API calls (should be ~5 fewer per team)
- [ ] Verify .last variables resolve correctly in filler templates
- [ ] Test pregame, postgame, and idle filler scenarios

### Phase 2 Testing
- [ ] Add logging to `_normalize_event()` to count calls (should drop from 3x to 1x per event)
- [ ] Verify all template variables still resolve correctly
- [ ] Check that `_is_normalized` flag doesn't leak to template engine

### Phase 3 Testing
- [ ] Compare EPG output before/after (should be identical)
- [ ] Verify scores appear in filler context
- [ ] Check overtime_text resolves in all contexts

### Phase 4 Testing
- [ ] Add logging to `get_scoreboard()` to count cache hits
- [ ] Verify scoreboard data freshness not affected
- [ ] Check cache clearing at EPG generation start

---

## Rollback Plan

Each phase is isolated and can be reverted independently:
- Phase 1: Restore 6 `_enrich_last_game_with_score()` calls
- Phase 2: Remove `_is_normalized` check
- Phase 3: Remove normalize calls from enrichment methods
- Phase 4: Remove scoreboard cache

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| API calls per team | 30-37 | 20-25 |
| Normalize calls per event | 3 | 1 |
| Filler enrich calls per team | 6 | 1 |
| Scoreboard duplicate fetches | 5-10 | 0 |

---

## Files to Modify

| File | Phase | Changes |
|------|-------|---------|
| `epg/orchestrator.py` | 1, 2, 3 | Filler enrichment, normalize flag, enrich-time normalize |
| `api/espn_client.py` | 4 | Scoreboard caching |
| `epg/event_matcher.py` | - | No changes (reference implementation) |
| `epg/event_template_engine.py` | - | No changes |

---

## Notes

- Event-based flow (event_matcher.py) already follows best practices - single-pass enrichment with `find_and_enrich()`
- Team-based flow complexity comes from .next/.last context requirements
- Filler generation is the biggest source of redundancy
- Consider long-term whether team-based should adopt event-based patterns
