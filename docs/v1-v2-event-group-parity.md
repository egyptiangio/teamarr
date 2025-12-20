# Event Group V1 → V2 Parity Plan

## Overview

This document tracks the feature parity work needed to bring V2's event group functionality to match V1. V2 has a simpler architecture by design, but some V1 features are essential for production use.

## Architecture Principles

All changes must follow these principles:

1. **Layer Separation**: API routes → Services → Database
2. **Single Responsibility**: Each module/function has one clear purpose
3. **Type Safety**: Use dataclasses for internal types, Pydantic for API
4. **No Dead Code**: Remove unused code immediately
5. **Explicit > Implicit**: Prefer configuration over detection

## Current State Analysis

### Database Schema Comparison

| Column | V1 | V2 | Status |
|--------|-----|-----|--------|
| **Identity** |
| `id` | ✓ | ✓ | OK |
| `dispatcharr_group_id` | ✓ (unique M3U group ID) | `m3u_group_id` | OK (renamed) |
| `dispatcharr_account_id` | ✓ (M3U account) | `m3u_account_id` | **OK (Phase 1)** |
| `group_name` | ✓ | `name` | OK (renamed) |
| `account_name` | ✓ (display) | `m3u_account_name` | **OK (Phase 1)** |
| **League Configuration** |
| `assigned_league` | ✓ (single league) | - | **Replaced by `leagues[]`** |
| `assigned_sport` | ✓ | - | **Missing** |
| `is_multi_sport` | ✓ | - | **Missing** (derive from leagues.length) |
| `enabled_leagues` | ✓ (JSON) | `leagues` (JSON) | OK |
| **Template & Channel** |
| `event_template_id` | ✓ | `template_id` | OK |
| `channel_start` | ✓ | `channel_start_number` | OK |
| `channel_group_id` | ✓ | ✓ | OK |
| `channel_group_name` | ✓ (display) | - | **Missing** |
| `stream_profile_id` | ✓ | ✓ | OK |
| `channel_profile_ids` | ✓ | ✓ | OK |
| `channel_assignment_mode` | ✓ | ✓ | OK |
| **Lifecycle** |
| `create_timing` | ✓ | ✓ | OK |
| `delete_timing` | ✓ | ✓ | OK |
| `duplicate_event_handling` | ✓ | ✓ | OK |
| **Custom Regex** |
| `custom_regex_teams` | ✓ | - | **Missing** |
| `custom_regex_teams_enabled` | ✓ | - | **Missing** |
| `custom_regex_date` | ✓ | - | **Missing** |
| `custom_regex_date_enabled` | ✓ | - | **Missing** |
| `custom_regex_time` | ✓ | - | **Missing** |
| `custom_regex_time_enabled` | ✓ | - | **Missing** |
| `stream_include_regex` | ✓ | - | **Missing** |
| `stream_include_regex_enabled` | ✓ | - | **Missing** |
| `stream_exclude_regex` | ✓ | - | **Missing** |
| `stream_exclude_regex_enabled` | ✓ | - | **Missing** |
| `skip_builtin_filter` | ✓ | - | **Missing** |
| **Multi-Sport** |
| `channel_sort_order` | ✓ (`time`, `sport_time`, `league_time`) | - | **Missing** |
| `overlap_handling` | ✓ (`add_stream`, `add_only`, `create_all`, `skip`) | - | **Missing** |
| **Hierarchy** |
| `parent_group_id` | ✓ | ✓ | OK |
| `sort_order` | ✓ | ✓ | OK |
| **Stats** |
| `last_refresh` | ✓ | ✓ | **OK (Phase 1)** |
| `refresh_interval_minutes` | ✓ | - | **Missing** (use scheduler instead) |
| `total_stream_count` | ✓ | ✓ | OK |
| `stream_count` | ✓ (after filtering) | ✓ | **OK (Phase 1)** |
| `matched_count` | ✓ | ✓ | **OK (Phase 1)** |
| `filtered_no_indicator` | ✓ | - | **Missing** |
| `filtered_include_regex` | ✓ | - | **Missing** |
| `filtered_exclude_regex` | ✓ | - | **Missing** |
| `filtered_outside_lookahead` | ✓ | - | **Missing** |
| `filtered_final` | ✓ | - | **Missing** |
| `filtered_league_not_enabled` | ✓ | - | **Missing** |
| `filtered_unsupported_sport` | ✓ | - | **Missing** |

### V2 Simplified Approach

V2 intentionally simplifies some V1 patterns:

1. **Leagues Array**: V2 uses a `leagues[]` array instead of `assigned_league` + `is_multi_sport` + `enabled_leagues`. This is cleaner.

2. **No Sport Column**: V2 derives sport from league configuration. Acceptable since leagues→sport mapping is 1:many.

3. **Display Names**: V2 could store these optionally but can also derive from Dispatcharr lookups.

## Implementation Phases

### Phase 1: Critical Missing Features (Essential for Parity)

These are blocking features users need.

#### 1.1 M3U Account Tracking

**Why needed**: Users need to know which M3U provider a group came from.

**Schema changes**:
```sql
ALTER TABLE event_epg_groups ADD COLUMN m3u_account_id INTEGER;
ALTER TABLE event_epg_groups ADD COLUMN m3u_account_name TEXT;  -- For display
```

**Files to modify**:
- `teamarr/database/schema.sql`
- `teamarr/database/groups.py`
- `teamarr/api/routes/groups.py` (add to models)
- `frontend/src/api/types.ts`
- `frontend/src/pages/EventGroupForm.tsx`
- `frontend/src/pages/EventGroupImport.tsx`

#### 1.2 Processing Stats

**Why needed**: Users need to see last refresh time and match success rates.

**Schema changes**:
```sql
ALTER TABLE event_epg_groups ADD COLUMN last_refresh TIMESTAMP;
ALTER TABLE event_epg_groups ADD COLUMN stream_count INTEGER DEFAULT 0;  -- After filtering
ALTER TABLE event_epg_groups ADD COLUMN matched_count INTEGER DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN matched_rate REAL;  -- Computed: matched/stream
```

**Files to modify**:
- `teamarr/database/schema.sql`
- `teamarr/database/groups.py` - add `update_group_stats()`
- `teamarr/consumers/event_epg.py` - update stats after processing
- `teamarr/api/routes/groups.py` - include in response
- `frontend/src/pages/EventGroups.tsx` - show stats

### Phase 2: Stream Filtering (Important for UX)

#### 2.1 Custom Regex Support

**Why needed**: Power users need custom stream name parsing.

**Schema changes**:
```sql
ALTER TABLE event_epg_groups ADD COLUMN custom_regex_teams TEXT;
ALTER TABLE event_epg_groups ADD COLUMN custom_regex_teams_enabled BOOLEAN DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN stream_include_regex TEXT;
ALTER TABLE event_epg_groups ADD COLUMN stream_include_regex_enabled BOOLEAN DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN stream_exclude_regex TEXT;
ALTER TABLE event_epg_groups ADD COLUMN stream_exclude_regex_enabled BOOLEAN DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN skip_builtin_filter BOOLEAN DEFAULT 0;
```

**New service module**: `teamarr/services/stream_filter.py`

```python
@dataclass
class StreamFilterConfig:
    """Configuration for stream filtering."""
    include_regex: str | None
    include_enabled: bool
    exclude_regex: str | None
    exclude_enabled: bool
    custom_teams_regex: str | None
    custom_teams_enabled: bool
    skip_builtin: bool

class StreamFilter:
    """Filters streams based on regex patterns."""

    def __init__(self, config: StreamFilterConfig):
        self.config = config

    def filter(self, streams: list[Stream]) -> FilterResult:
        """Apply filters and return filtered streams with stats."""
        pass

    def extract_teams(self, stream_name: str) -> tuple[str, str] | None:
        """Extract team names using regex or builtin patterns."""
        pass
```

**Files to modify**:
- `teamarr/database/schema.sql`
- `teamarr/database/groups.py`
- `teamarr/api/routes/groups.py`
- `teamarr/consumers/event_epg.py` - use StreamFilter
- `frontend/src/pages/EventGroupForm.tsx` - add Regex tab

#### 2.2 Filtering Stats

**Why needed**: Users need to understand why streams aren't matching.

**Schema changes**:
```sql
ALTER TABLE event_epg_groups ADD COLUMN filtered_no_indicator INTEGER DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN filtered_include_regex INTEGER DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN filtered_exclude_regex INTEGER DEFAULT 0;
ALTER TABLE event_epg_groups ADD COLUMN filtered_final INTEGER DEFAULT 0;
```

#### 2.3 User-Defined Team Aliases

**Why needed**: Users need to map stream team names to provider team names for edge cases where automatic matching fails. Examples: "Spurs" → "Tottenham Hotspur", "Man U" → "Manchester United", "NYG" → "New York Giants".

**Schema changes**:
```sql
CREATE TABLE IF NOT EXISTS team_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Alias Definition
    alias TEXT NOT NULL,                    -- Alias string (normalized) e.g., "spurs", "man u"
    league TEXT NOT NULL,                   -- League code (e.g., "epl", "nfl")

    -- Provider Team Mapping
    provider TEXT NOT NULL DEFAULT 'espn',  -- Provider name
    team_id TEXT NOT NULL,                  -- Provider's team ID
    team_name TEXT NOT NULL,                -- Provider's team name (e.g., "Tottenham Hotspur")

    UNIQUE(alias, league)
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_league ON team_aliases(league);
CREATE INDEX IF NOT EXISTS idx_team_aliases_alias ON team_aliases(alias);
```

**New service module**: `teamarr/services/team_alias.py`

```python
@dataclass
class TeamAlias:
    """A user-defined team alias."""
    id: int
    alias: str
    league: str
    provider: str
    team_id: str
    team_name: str
    created_at: datetime | None = None

class TeamAliasService:
    """Manages user-defined team aliases."""

    def get_alias(self, text: str, league: str) -> TeamAlias | None:
        """Look up alias for a team name in a league."""
        pass

    def create_alias(self, alias: str, league: str, team_id: str, team_name: str) -> TeamAlias:
        """Create a new alias."""
        pass

    def delete_alias(self, alias_id: int) -> bool:
        """Delete an alias."""
        pass

    def list_aliases(self, league: str | None = None) -> list[TeamAlias]:
        """List all aliases, optionally filtered by league."""
        pass
```

**Integration with matching engine**:
- `teamarr/consumers/matching.py` should check aliases before fuzzy matching
- Priority order: exact alias match → exact team name → fuzzy match

**Files to modify**:
- `teamarr/database/schema.sql` - add team_aliases table
- `teamarr/database/aliases.py` - NEW: CRUD operations
- `teamarr/services/team_alias.py` - NEW: alias service
- `teamarr/consumers/matching.py` - integrate alias lookup
- `teamarr/api/routes/aliases.py` - NEW: REST endpoints
- `frontend/src/api/aliases.ts` - NEW: API client
- `frontend/src/pages/TeamAliases.tsx` - NEW: management UI

**API Endpoints**:
```
GET    /api/v1/aliases                    # List all aliases
GET    /api/v1/aliases?league=epl         # Filter by league
POST   /api/v1/aliases                    # Create alias
DELETE /api/v1/aliases/{id}               # Delete alias
POST   /api/v1/aliases/import             # Bulk import
GET    /api/v1/aliases/export             # Export as JSON
```

### Phase 3: Multi-Sport Enhancements (Nice to Have)

#### 3.1 Channel Sort Order

**Why needed**: Multi-sport groups need configurable ordering.

**Schema changes**:
```sql
ALTER TABLE event_epg_groups ADD COLUMN channel_sort_order TEXT DEFAULT 'time'
    CHECK(channel_sort_order IN ('time', 'sport_time', 'league_time'));
```

#### 3.2 Overlap Handling

**Why needed**: Control behavior when events overlap.

**Schema changes**:
```sql
ALTER TABLE event_epg_groups ADD COLUMN overlap_handling TEXT DEFAULT 'add_stream'
    CHECK(overlap_handling IN ('add_stream', 'add_only', 'create_all', 'skip'));
```

## Immediate Bug Fixes

### 1. Blank Screen After Import

**Symptom**: Navigating to `/event-groups/new?m3u_group_id=...` shows blank screen.

**Debug steps**:
1. Check browser console for errors
2. Verify all UI components are properly imported
3. Add error boundary to catch rendering errors

### 2. Stream Preview Reverse Order

**Symptom**: Streams displayed in reverse order in preview modal.

**Potential causes**:
- Dispatcharr API returns in reverse order by default
- Client-side sorting issue

**Fix**: Add explicit sort in API or frontend.

## File Organization

New files to create:

```
teamarr/
├── services/
│   └── stream_filter.py          # Stream filtering service
│
├── consumers/
│   └── stream_parser.py          # Stream name parsing (teams, date, time)
│
frontend/src/
├── pages/
│   └── EventGroupForm/           # Split into sub-components
│       ├── index.tsx             # Main form
│       ├── ModeStep.tsx          # Mode selection
│       ├── LeagueStep.tsx        # League selection
│       ├── SettingsStep.tsx      # Settings form
│       └── RegexStep.tsx         # Regex configuration
```

## Testing Requirements

Each phase must include:

1. **Unit tests** for new services
2. **API tests** for endpoint changes
3. **Manual testing** of UI flows

## Migration Path

For existing V2 users:

1. Schema migrations via `schema.sql` (uses `ADD COLUMN IF NOT EXISTS`)
2. Default values for all new columns
3. No data loss or breaking changes

## Progress Tracking

### Phase 1: Critical - COMPLETED (Dec 20, 2025)
- [x] 1.1 M3U Account Tracking
  - [x] Schema migration (`m3u_account_id`, `m3u_account_name`)
  - [x] Database layer (`EventEPGGroup` dataclass, `_row_to_group`, `create_group`, `update_group`)
  - [x] API layer (Pydantic models, all CRUD endpoints)
  - [x] Frontend (types, EventGroupImport, EventGroupForm)
- [x] 1.2 Processing Stats
  - [x] Schema migration (`last_refresh`, `stream_count`, `matched_count`)
  - [x] Database layer (`update_group_stats()` function)
  - [x] Consumer updates (`event_group_processor.py` calls `update_group_stats`)
  - [x] API layer (all endpoints return stats)
  - [x] Frontend (EventGroups.tsx stats tiles and table column)

### Phase 2: Stream Filtering - COMPLETE (Dec 20, 2025)
- [x] 2.1 Custom Regex
  - [x] Schema migration (stream_include/exclude_regex, custom_regex_teams, skip_builtin_filter)
  - [x] StreamFilter service (`teamarr/services/stream_filter.py`)
  - [x] Consumer integration (`event_group_processor.py` uses StreamFilter)
  - [x] API layer (Pydantic models, all CRUD endpoints)
  - [x] Frontend UI (Stream Filtering card in EventGroupForm)
- [x] 2.2 Filtering Stats
  - [x] Schema migration (filtered_include_regex, filtered_exclude_regex, filtered_no_match)
  - [x] Database layer (update_group_stats updated)
  - [x] API layer (returns filtering stats)
  - [x] Frontend types updated
- [ ] 2.3 User-Defined Team Aliases
  - [ ] Schema migration (team_aliases table)
  - [ ] Database layer (CRUD operations)
  - [ ] TeamAliasService (`teamarr/services/team_alias.py`)
  - [ ] Matching engine integration
  - [ ] API layer (REST endpoints)
  - [ ] Frontend UI (TeamAliases.tsx)

### Phase 3: Multi-Sport
- [ ] 3.1 Channel Sort Order
- [ ] 3.2 Overlap Handling

### Bug Fixes
- [x] Blank screen after import (null name in league sorting)
- [x] Stream preview order (added alphabetical sorting in API endpoint)

## Notes

- V1 reference code: `/srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr/`
- V2 codebase: `/srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarrv2/`
- This document should be updated as work progresses
