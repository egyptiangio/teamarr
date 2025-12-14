# Feature Plan: {unique_team} Token for Exception Keywords

## Overview

Add support for a `{unique_team}` token in exception keywords that matches any team name variant (name, city, abbreviation) from the current event and groups streams by the resolved team identity.

## Problem Statement

Users want to create a single keyword rule like `{unique_team} broadcast` that matches:
- `Detroit broadcast`
- `Lions broadcast`
- `DET broadcast`
- `Detroit Lions broadcast`
- `Dallas broadcast`
- `Cowboys broadcast`
- etc.

All variants of the same team should consolidate to the same channel, but different teams should get separate channels.

## Desired Behavior

**Keyword Rule:** `{unique_team} broadcast` → Sub-Consolidate

**For Cowboys @ Lions game:**

| Stream | Matched Team | Canonical | Channel |
|--------|--------------|-----------|---------|
| `Detroit broadcast` | Lions | `lions broadcast` | Channel A |
| `Lions broadcast` | Lions | `lions broadcast` | Channel A |
| `DET broadcast` | Lions | `lions broadcast` | Channel A |
| `Detroit Lions broadcast` | Lions | `lions broadcast` | Channel A |
| `Dallas broadcast` | Cowboys | `cowboys broadcast` | Channel B |
| `Cowboys broadcast` | Cowboys | `cowboys broadcast` | Channel B |
| `DAL broadcast` | Cowboys | `cowboys broadcast` | Channel B |

**Result:**
- Channel A: `Cowboys @ Lions (Lions Broadcast)` - all home team broadcast streams
- Channel B: `Cowboys @ Lions (Cowboys Broadcast)` - all away team broadcast streams

## Implementation Details

### 1. Token Expansion

When `{unique_team}` is in the keyword pattern, expand it to match all team name variants from the event:

```python
def get_team_variants(team: Dict) -> List[str]:
    """Get all name variants for a team."""
    variants = []

    # Full name: "Detroit Lions"
    if team.get('name'):
        variants.append(team['name'].lower())

    # Short display name: "Lions"
    if team.get('shortDisplayName'):
        variants.append(team['shortDisplayName'].lower())

    # Abbreviation: "DET"
    if team.get('abbreviation'):
        variants.append(team['abbreviation'].lower())

    # City (parsed from full name): "Detroit"
    if team.get('name') and team.get('shortDisplayName'):
        city = team['name'].replace(team['shortDisplayName'], '').strip()
        if city:
            variants.append(city.lower())

    return variants
```

### 2. Keyword Matcher Changes

Update `check_exception_keyword()` to accept event context:

```python
def check_exception_keyword(
    stream_name: str,
    keywords_list: List[Dict],
    event: Dict = None  # NEW: Optional event context for {unique_team}
) -> Tuple[Optional[str], Optional[str]]:
```

When pattern contains `{unique_team}`:
1. Get variants for home team and away team
2. For each variant, substitute into the pattern and check for match
3. If match found, determine which team matched
4. Return canonical as `{team_short_name} {remainder}`

### 3. Canonical Name Resolution

The canonical keyword should use the team's `shortDisplayName` for consistency:

| Matched Variant | Team shortDisplayName | Canonical |
|-----------------|----------------------|-----------|
| `detroit` | Lions | `lions broadcast` |
| `lions` | Lions | `lions broadcast` |
| `det` | Lions | `lions broadcast` |

### 4. Template Variable

`{exception_keyword_title}` would resolve to `Lions Broadcast` or `Cowboys Broadcast`.

### 5. Database/Channel Lookup

`find_existing_channel()` already supports `exception_keyword` parameter. The canonical will be unique per team, so channels will group correctly.

## Flow Changes

### Current Flow
```
keyword_matcher.check_exception_keyword(stream_name, keywords_list)
                                        ↑
                         Only has stream name
```

### New Flow
```
keyword_matcher.check_exception_keyword(stream_name, keywords_list, event)
                                                                     ↑
                                               Has home/away team data
```

### channel_lifecycle.py Changes

1. Pass event to `check_exception_keyword()` when event is available
2. Handle case where event is not available (fall back to current behavior)

## UI Changes

1. Add help text explaining `{unique_team}` token in Settings → Exception Keywords
2. Example: "`{unique_team} broadcast` matches any team name + 'broadcast'"

## Edge Cases

1. **Team name contains keyword pattern** - e.g., team named "Broadcast FC"
   - Solution: Match longest variant first, or require whitespace boundaries

2. **Event context not available** - During some operations
   - Solution: Skip `{unique_team}` expansion, treat as literal

3. **City not parseable** - e.g., "New York Giants" → city is "New York"
   - Solution: Parse by removing shortDisplayName from full name

4. **Abbreviation matches unrelated word** - e.g., "LAL" in "LALIGA"
   - Solution: Require word boundaries in matching

## Testing Scenarios

1. Basic: `{unique_team} broadcast` with different team name variants
2. Both teams: Streams for both home and away team broadcasts
3. Mixed: Some streams have keywords, some don't
4. No match: Stream doesn't contain any team variant
5. Multiple keywords: Different patterns like `{unique_team} feed` and `{unique_team} broadcast`

## Estimated Complexity

| Component | Effort |
|-----------|--------|
| Team variant extraction | Low |
| Keyword matcher update | Medium |
| Pass event context through | Low |
| Canonical resolution | Low |
| UI help text | Low |
| Testing | Medium |

**Total:** Medium complexity

## Future Enhancements

1. `{home}` and `{away}` tokens for explicit home/away matching
2. `{unique_team:city}` to force city in canonical name
3. `{unique_team:abbrev}` to force abbreviation in canonical name
