# Consolidation Exception Keywords - Implementation Plan

## Overview

When `duplicate_event_handling` is set to `consolidate`, all streams matching the same ESPN event are combined onto a single channel. However, some streams represent fundamentally different viewing experiences (ManningCast, Prime Vision, home/away broadcasts) and should be handled differently.

This feature allows users to define keyword patterns with configurable behaviors:
- **consolidate**: Create separate channel from main event, but group streams with same keyword together
- **separate**: Create new channel for each matching stream (never consolidate)
- **ignore**: Skip streams entirely (don't create channel)

---

## Database Schema

### New Table: `consolidation_exception_keywords`

```sql
CREATE TABLE IF NOT EXISTS consolidation_exception_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    keywords TEXT NOT NULL,              -- Comma-separated variants, case-insensitive
    behavior TEXT NOT NULL DEFAULT 'consolidate',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cek_group ON consolidation_exception_keywords(group_id);
```

**Fields:**
- `group_id`: FK to event_epg_groups - which group this rule belongs to
- `keywords`: Comma-separated keyword variants (e.g., "Prime Vision, Primevision, PrimeVision")
- `behavior`: One of 'consolidate', 'separate', 'ignore'

**Constraints:**
- Keywords are matched case-insensitively as substrings
- First keyword variant is used as the "canonical" name for grouping
- Child groups inherit parent's keywords (no separate table entries needed)

---

## Database Functions

Add to `database/__init__.py`:

```python
# =============================================================================
# CONSOLIDATION EXCEPTION KEYWORDS
# =============================================================================

def get_consolidation_exception_keywords(group_id: int) -> list:
    """
    Get exception keywords for a group, including inherited from parent.

    Returns list of dicts: [{'id': 1, 'keywords': 'Prime Vision, Primevision', 'behavior': 'separate'}, ...]
    """
    conn = get_connection()

    # Check if this is a child group
    group = conn.execute(
        "SELECT parent_group_id FROM event_epg_groups WHERE id = ?",
        (group_id,)
    ).fetchone()

    # Use parent's keywords if child group
    effective_group_id = group['parent_group_id'] if group and group['parent_group_id'] else group_id

    rows = conn.execute(
        "SELECT id, keywords, behavior FROM consolidation_exception_keywords WHERE group_id = ? ORDER BY id",
        (effective_group_id,)
    ).fetchall()
    conn.close()

    return [dict(row) for row in rows]


def add_consolidation_exception_keyword(group_id: int, keywords: str, behavior: str = 'consolidate') -> int:
    """
    Add a new exception keyword entry.

    Args:
        group_id: Event EPG group ID
        keywords: Comma-separated keyword variants
        behavior: 'consolidate', 'separate', or 'ignore'

    Returns:
        New entry ID
    """
    if behavior not in ('consolidate', 'separate', 'ignore'):
        raise ValueError(f"Invalid behavior: {behavior}")

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO consolidation_exception_keywords (group_id, keywords, behavior) VALUES (?, ?, ?)",
        (group_id, keywords.strip(), behavior)
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_consolidation_exception_keyword(keyword_id: int, keywords: str = None, behavior: str = None) -> bool:
    """
    Update an existing exception keyword entry.

    Returns True if updated, False if not found.
    """
    if behavior and behavior not in ('consolidate', 'separate', 'ignore'):
        raise ValueError(f"Invalid behavior: {behavior}")

    conn = get_connection()

    updates = []
    params = []
    if keywords is not None:
        updates.append("keywords = ?")
        params.append(keywords.strip())
    if behavior is not None:
        updates.append("behavior = ?")
        params.append(behavior)

    if not updates:
        conn.close()
        return False

    params.append(keyword_id)
    cursor = conn.execute(
        f"UPDATE consolidation_exception_keywords SET {', '.join(updates)} WHERE id = ?",
        params
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def delete_consolidation_exception_keyword(keyword_id: int) -> bool:
    """
    Delete an exception keyword entry.

    Returns True if deleted, False if not found.
    """
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM consolidation_exception_keywords WHERE id = ?",
        (keyword_id,)
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def delete_all_consolidation_exception_keywords(group_id: int) -> int:
    """
    Delete all exception keywords for a group.

    Returns number of entries deleted.
    """
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM consolidation_exception_keywords WHERE group_id = ?",
        (group_id,)
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count
```

---

## Keyword Matching Logic

Add to `utils/regex_helper.py` or new `utils/keyword_matcher.py`:

```python
def check_exception_keyword(stream_name: str, keywords_list: list) -> tuple:
    """
    Check if stream matches any exception keyword.

    Args:
        stream_name: The stream name to check
        keywords_list: List of keyword dicts from get_consolidation_exception_keywords()

    Returns:
        (canonical_keyword, behavior) if match found
        (None, None) if no match

    Example:
        >>> keywords = [{'keywords': 'Prime Vision, Primevision', 'behavior': 'separate'}]
        >>> check_exception_keyword('NFL: Chiefs vs Raiders (Prime Vision)', keywords)
        ('prime vision', 'separate')
    """
    stream_lower = stream_name.lower()

    for entry in keywords_list:
        # Split comma-separated keywords, normalize
        variants = [k.strip().lower() for k in entry['keywords'].split(',') if k.strip()]

        for variant in variants:
            if variant in stream_lower:
                # Return first variant as canonical (for grouping)
                canonical = variants[0]
                return (canonical, entry['behavior'])

    return (None, None)
```

---

## Channel Lifecycle Integration

### Current Flow (in `channel_lifecycle.py`)

When processing a stream that matches an event:
1. Check if channel exists for this event
2. If exists and `duplicate_event_handling == 'consolidate'`: add stream to existing channel
3. If exists and `duplicate_event_handling == 'separate'`: create new channel
4. If exists and `duplicate_event_handling == 'ignore'`: skip stream
5. If not exists: create new channel

### New Flow with Exception Keywords

```
Stream matches Event X
    ↓
Check exception keywords for stream name
    ↓
├── Match with 'ignore' behavior
│   └── Skip stream entirely (return early)
│
├── Match with 'separate' behavior
│   └── Always create NEW channel for this stream
│       (Store keyword in managed_channel_streams.exception_keyword)
│
├── Match with 'consolidate' behavior
│   ├── Look for existing channel with SAME keyword + event
│   │   ├── Found → Add stream to that channel
│   │   └── Not found → Create new channel for this keyword
│   └── (Store keyword in managed_channel_streams.exception_keyword)
│
└── No keyword match
    └── Use normal duplicate_event_handling logic
        ├── 'consolidate' → Add to main event channel
        ├── 'separate' → Create new channel
        └── 'ignore' → Skip
```

### Database Changes for Tracking

Add column to `managed_channel_streams`:

```sql
ALTER TABLE managed_channel_streams ADD COLUMN exception_keyword TEXT;
```

This tracks which keyword caused this stream to be on this channel (for 'consolidate' grouping).

Add column to `managed_channels` for keyword-created channels:

```sql
ALTER TABLE managed_channels ADD COLUMN exception_keyword TEXT;
```

This marks channels created due to exception keywords (helps with channel naming).

### Modified Functions

**`_should_create_or_consolidate()` or similar in channel_lifecycle.py:**

```python
def determine_stream_handling(
    stream_name: str,
    event_id: str,
    group: dict,
    existing_channels: list,
    exception_keywords: list
) -> dict:
    """
    Determine how to handle a stream based on exception keywords and group settings.

    Returns:
        {
            'action': 'create' | 'consolidate' | 'ignore',
            'channel_id': int or None (for consolidate),
            'exception_keyword': str or None
        }
    """
    # Check exception keywords first
    keyword, behavior = check_exception_keyword(stream_name, exception_keywords)

    if keyword:
        if behavior == 'ignore':
            return {'action': 'ignore', 'channel_id': None, 'exception_keyword': keyword}

        elif behavior == 'separate':
            # Always create new channel
            return {'action': 'create', 'channel_id': None, 'exception_keyword': keyword}

        elif behavior == 'consolidate':
            # Look for existing channel with same keyword + event
            for ch in existing_channels:
                if (ch['espn_event_id'] == event_id and
                    ch.get('exception_keyword', '').lower() == keyword.lower()):
                    return {'action': 'consolidate', 'channel_id': ch['id'], 'exception_keyword': keyword}
            # No existing keyword channel - create new
            return {'action': 'create', 'channel_id': None, 'exception_keyword': keyword}

    # No keyword match - use group's duplicate_event_handling
    handling = group.get('duplicate_event_handling', 'consolidate')

    if handling == 'ignore':
        return {'action': 'ignore', 'channel_id': None, 'exception_keyword': None}

    elif handling == 'separate':
        return {'action': 'create', 'channel_id': None, 'exception_keyword': None}

    else:  # consolidate
        # Find main event channel (no exception_keyword)
        for ch in existing_channels:
            if ch['espn_event_id'] == event_id and not ch.get('exception_keyword'):
                return {'action': 'consolidate', 'channel_id': ch['id'], 'exception_keyword': None}
        # No existing channel - create main channel
        return {'action': 'create', 'channel_id': None, 'exception_keyword': None}
```

---

## API Endpoints

Add to `app.py`:

### GET /api/event-epg/groups/<id>/exception-keywords

```python
@app.route('/api/event-epg/groups/<int:group_id>/exception-keywords', methods=['GET'])
def api_get_exception_keywords(group_id):
    """Get all exception keywords for a group (includes inherited from parent)."""
    group = get_event_epg_group(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404

    keywords = get_consolidation_exception_keywords(group_id)

    # Check if inherited
    is_inherited = bool(group.get('parent_group_id'))

    return jsonify({
        'group_id': group_id,
        'inherited': is_inherited,
        'inherited_from': group.get('parent_group_id'),
        'keywords': keywords
    })
```

### POST /api/event-epg/groups/<id>/exception-keywords

```python
@app.route('/api/event-epg/groups/<int:group_id>/exception-keywords', methods=['POST'])
def api_add_exception_keyword(group_id):
    """Add a new exception keyword entry."""
    group = get_event_epg_group(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404

    # Child groups can't have their own keywords
    if group.get('parent_group_id'):
        return jsonify({'error': 'Child groups inherit keywords from parent'}), 400

    data = request.get_json() or {}
    keywords = data.get('keywords', '').strip()
    behavior = data.get('behavior', 'consolidate')

    if not keywords:
        return jsonify({'error': 'keywords is required'}), 400

    if behavior not in ('consolidate', 'separate', 'ignore'):
        return jsonify({'error': 'behavior must be consolidate, separate, or ignore'}), 400

    try:
        new_id = add_consolidation_exception_keyword(group_id, keywords, behavior)
        return jsonify({'success': True, 'id': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

### PUT /api/event-epg/groups/<id>/exception-keywords/<keyword_id>

```python
@app.route('/api/event-epg/groups/<int:group_id>/exception-keywords/<int:keyword_id>', methods=['PUT'])
def api_update_exception_keyword(group_id, keyword_id):
    """Update an exception keyword entry."""
    data = request.get_json() or {}

    keywords = data.get('keywords')
    behavior = data.get('behavior')

    if behavior and behavior not in ('consolidate', 'separate', 'ignore'):
        return jsonify({'error': 'behavior must be consolidate, separate, or ignore'}), 400

    try:
        updated = update_consolidation_exception_keyword(keyword_id, keywords, behavior)
        if updated:
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Keyword not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

### DELETE /api/event-epg/groups/<id>/exception-keywords/<keyword_id>

```python
@app.route('/api/event-epg/groups/<int:group_id>/exception-keywords/<int:keyword_id>', methods=['DELETE'])
def api_delete_exception_keyword(group_id, keyword_id):
    """Delete an exception keyword entry."""
    deleted = delete_consolidation_exception_keyword(keyword_id)
    if deleted:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Keyword not found'}), 404
```

---

## UI Design

### Location

Add to `templates/event_group_form.html` in the Channel Settings section, after "Duplicate Event Handling" dropdown.

### Visibility

Only show when `duplicate_event_handling` is set to `consolidate`. When set to `separate` or `ignore`, the keywords don't apply.

### HTML Structure

```html
<!-- Exception Keywords Section (only visible when duplicate handling = consolidate) -->
<div id="exception-keywords-section" class="mt-4" style="display: none;">
    <div class="d-flex justify-content-between align-items-center mb-2">
        <label class="form-label mb-0">
            <strong>Exception Keywords</strong>
            <small class="text-muted ms-2">Streams matching these keywords get special handling</small>
        </label>
    </div>

    <!-- Inherited notice for child groups -->
    {% if group and group.parent_group_id %}
    <div class="alert alert-info py-2">
        <i class="bi bi-info-circle me-1"></i>
        Keywords inherited from parent group
    </div>
    {% endif %}

    <!-- Keywords table -->
    <div class="table-responsive">
        <table class="table table-sm table-hover" id="exception-keywords-table">
            <thead>
                <tr>
                    <th style="width: 50%">Keywords (comma-separated)</th>
                    <th style="width: 35%">Behavior</th>
                    <th style="width: 15%"></th>
                </tr>
            </thead>
            <tbody id="exception-keywords-body">
                <!-- Rows populated by JavaScript -->
            </tbody>
        </table>
    </div>

    <!-- Add new keyword row (hidden for child groups) -->
    {% if not group or not group.parent_group_id %}
    <div class="input-group mt-2">
        <input type="text" id="new-keyword-input" class="form-control"
               placeholder="e.g., Prime Vision, Primevision">
        <select id="new-keyword-behavior" class="form-select" style="max-width: 150px;">
            <option value="consolidate">Consolidate</option>
            <option value="separate">Separate</option>
            <option value="ignore">Ignore</option>
        </select>
        <button type="button" class="btn btn-outline-primary" onclick="addExceptionKeyword()">
            <i class="bi bi-plus"></i> Add
        </button>
    </div>
    {% endif %}

    <!-- Help text -->
    <small class="text-muted mt-2 d-block">
        <strong>Consolidate:</strong> Group streams with same keyword on one channel<br>
        <strong>Separate:</strong> Create new channel for each matching stream<br>
        <strong>Ignore:</strong> Skip streams with this keyword entirely
    </small>
</div>
```

### JavaScript

```javascript
// Show/hide exception keywords based on duplicate handling selection
document.getElementById('duplicate_event_handling').addEventListener('change', function() {
    const section = document.getElementById('exception-keywords-section');
    section.style.display = this.value === 'consolidate' ? 'block' : 'none';
});

// Load exception keywords on page load
async function loadExceptionKeywords() {
    const groupId = {{ group.id if group else 'null' }};
    if (!groupId) return;

    try {
        const response = await fetch(`/api/event-epg/groups/${groupId}/exception-keywords`);
        const data = await response.json();

        const tbody = document.getElementById('exception-keywords-body');
        tbody.innerHTML = '';

        const isInherited = data.inherited;

        data.keywords.forEach(kw => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>
                    <input type="text" class="form-control form-control-sm"
                           value="${escapeHtml(kw.keywords)}"
                           ${isInherited ? 'disabled' : ''}
                           onchange="updateExceptionKeyword(${kw.id}, this.value, null)">
                </td>
                <td>
                    <select class="form-select form-select-sm"
                            ${isInherited ? 'disabled' : ''}
                            onchange="updateExceptionKeyword(${kw.id}, null, this.value)">
                        <option value="consolidate" ${kw.behavior === 'consolidate' ? 'selected' : ''}>Consolidate</option>
                        <option value="separate" ${kw.behavior === 'separate' ? 'selected' : ''}>Separate</option>
                        <option value="ignore" ${kw.behavior === 'ignore' ? 'selected' : ''}>Ignore</option>
                    </select>
                </td>
                <td class="text-end">
                    ${isInherited ? '' : `
                    <button type="button" class="btn btn-sm btn-outline-danger"
                            onclick="deleteExceptionKeyword(${kw.id})">
                        <i class="bi bi-trash"></i>
                    </button>
                    `}
                </td>
            `;
            tbody.appendChild(row);
        });

        if (data.keywords.length === 0 && !isInherited) {
            tbody.innerHTML = '<tr><td colspan="3" class="text-muted text-center">No exception keywords defined</td></tr>';
        }
    } catch (error) {
        console.error('Failed to load exception keywords:', error);
    }
}

async function addExceptionKeyword() {
    const groupId = {{ group.id if group else 'null' }};
    const keywords = document.getElementById('new-keyword-input').value.trim();
    const behavior = document.getElementById('new-keyword-behavior').value;

    if (!keywords) {
        alert('Please enter at least one keyword');
        return;
    }

    try {
        const response = await fetch(`/api/event-epg/groups/${groupId}/exception-keywords`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({keywords, behavior})
        });

        if (response.ok) {
            document.getElementById('new-keyword-input').value = '';
            loadExceptionKeywords();
        } else {
            const data = await response.json();
            alert(data.error || 'Failed to add keyword');
        }
    } catch (error) {
        console.error('Failed to add keyword:', error);
    }
}

async function updateExceptionKeyword(keywordId, keywords, behavior) {
    const groupId = {{ group.id if group else 'null' }};

    const body = {};
    if (keywords !== null) body.keywords = keywords;
    if (behavior !== null) body.behavior = behavior;

    try {
        await fetch(`/api/event-epg/groups/${groupId}/exception-keywords/${keywordId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
    } catch (error) {
        console.error('Failed to update keyword:', error);
    }
}

async function deleteExceptionKeyword(keywordId) {
    const groupId = {{ group.id if group else 'null' }};

    if (!confirm('Delete this keyword?')) return;

    try {
        const response = await fetch(`/api/event-epg/groups/${groupId}/exception-keywords/${keywordId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadExceptionKeywords();
        }
    } catch (error) {
        console.error('Failed to delete keyword:', error);
    }
}

// Load on page ready
document.addEventListener('DOMContentLoaded', function() {
    loadExceptionKeywords();

    // Show section if consolidate is selected
    const handling = document.getElementById('duplicate_event_handling');
    if (handling && handling.value === 'consolidate') {
        document.getElementById('exception-keywords-section').style.display = 'block';
    }
});
```

---

## Migration

Add to `database/__init__.py` in `run_migrations()`:

```python
# =========================================================================
# 13. CONSOLIDATION EXCEPTION KEYWORDS
# =========================================================================
if current_version < 13:
    # Create exception keywords table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS consolidation_exception_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            keywords TEXT NOT NULL,
            behavior TEXT NOT NULL DEFAULT 'consolidate',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES event_epg_groups(id) ON DELETE CASCADE
        )
    """)

    # Create index
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cek_group
        ON consolidation_exception_keywords(group_id)
    """)

    # Add tracking columns to managed_channels and managed_channel_streams
    add_columns_if_missing("managed_channels", [
        ("exception_keyword", "TEXT"),  # Keyword that created this channel
    ])

    add_columns_if_missing("managed_channel_streams", [
        ("exception_keyword", "TEXT"),  # Keyword that matched this stream
    ])

    conn.commit()
```

Update `CURRENT_SCHEMA_VERSION = 13` and update `schema.sql` for fresh installs.

---

## Processing Examples

### Example 1: Main event + ManningCast (consolidate behavior)

```
Group settings: duplicate_event_handling = 'consolidate'
Exception keywords: [{'keywords': 'Manning Cast, Manningcast', 'behavior': 'consolidate'}]

Streams for Event 12345 (Chiefs vs Raiders):
1. "NFL: Chiefs vs Raiders"               → Main channel (no keyword)
2. "NFL: Chiefs vs Raiders HD"            → Consolidate to main channel
3. "NFL: Chiefs vs Raiders (ManningCast)" → New channel (keyword: "manning cast")
4. "NFL: Chiefs vs Raiders ManningCast"   → Consolidate to ManningCast channel
5. "NFL: Chiefs vs Raiders (Manningcast) HD" → Consolidate to ManningCast channel

Result:
- Channel A: Main event (streams 1, 2)
- Channel B: ManningCast (streams 3, 4, 5) - exception_keyword = "manning cast"
```

### Example 2: Home/Away broadcasts (consolidate behavior)

```
Exception keywords: [{'keywords': 'Broadcast', 'behavior': 'consolidate'}]

Streams for Event 12345 (Canucks vs Blackhawks):
1. "NHL: Canucks vs Blackhawks"                    → Main channel
2. "NHL: Canucks vs Blackhawks (Canucks Broadcast)"   → New channel (keyword match)
3. "NHL: Canucks vs Blackhawks (Canucks Broadcast) HD" → Consolidate (same keyword + event)
4. "NHL: Canucks vs Blackhawks (Blackhawks Broadcast)" → New channel (same keyword but different context)

Wait - this is tricky! "Broadcast" matches all three, but we want to separate home vs away.

Solution: User should define more specific keywords:
- "Canucks Broadcast" → consolidate
- "Blackhawks Broadcast" → consolidate

Or accept that all "Broadcast" streams consolidate together (may not be desired).
```

### Example 3: Prime Vision (separate behavior)

```
Exception keywords: [{'keywords': 'Prime Vision, Primevision', 'behavior': 'separate'}]

Streams for Event 12345:
1. "NFL: Chiefs vs Raiders"              → Main channel
2. "NFL: Chiefs vs Raiders (Prime Vision)"  → New channel A (separate)
3. "NFL: Chiefs vs Raiders (Primevision) 4K" → New channel B (separate, never consolidates)

Result: 3 channels (each Prime Vision stream gets its own)
```

### Example 4: Spanish feeds (ignore behavior)

```
Exception keywords: [{'keywords': 'Spanish, Espanol, (ESP)', 'behavior': 'ignore'}]

Streams for Event 12345:
1. "NFL: Chiefs vs Raiders"           → Main channel
2. "NFL: Chiefs vs Raiders (Spanish)" → SKIPPED
3. "NFL: Chiefs vs Raiders (ESP)"     → SKIPPED

Result: 1 channel (Spanish feeds ignored)
```

---

## Testing Checklist

1. **Database**
   - [ ] Table created on fresh install
   - [ ] Migration works on existing database
   - [ ] FK constraint works (delete group deletes keywords)
   - [ ] CRUD operations work correctly

2. **API**
   - [ ] GET returns keywords (including inherited for child groups)
   - [ ] POST adds new keyword
   - [ ] POST fails for child groups
   - [ ] PUT updates keyword/behavior
   - [ ] DELETE removes keyword

3. **UI**
   - [ ] Section hidden when duplicate_handling != consolidate
   - [ ] Section shown when consolidate selected
   - [ ] Keywords load on page load
   - [ ] Add keyword works
   - [ ] Edit keyword inline works
   - [ ] Delete keyword works
   - [ ] Child groups show inherited notice and disabled fields

4. **Channel Lifecycle**
   - [ ] 'ignore' behavior skips stream entirely
   - [ ] 'separate' behavior creates new channel per stream
   - [ ] 'consolidate' behavior groups streams with same keyword
   - [ ] Main event channel still works (no keyword match)
   - [ ] exception_keyword tracked in managed_channels
   - [ ] exception_keyword tracked in managed_channel_streams
   - [ ] Child groups use parent's keywords

5. **Edge Cases**
   - [ ] Empty keywords list (normal consolidation)
   - [ ] Stream matches multiple keywords (first match wins)
   - [ ] Case insensitivity works
   - [ ] Keyword variants work (Prime Vision vs Primevision)

---

## Implementation Order

1. **Database** (schema.sql + migration + CRUD functions)
2. **Keyword matching utility** (check_exception_keyword function)
3. **API endpoints** (GET, POST, PUT, DELETE)
4. **UI** (HTML + JavaScript)
5. **Channel lifecycle integration** (the actual behavior changes)
6. **Testing**

---

## Files to Modify

| File | Changes |
|------|---------|
| `database/schema.sql` | Add table, update schema_version |
| `database/__init__.py` | Migration + CRUD functions |
| `utils/keyword_matcher.py` | New file - check_exception_keyword() |
| `app.py` | API endpoints |
| `templates/event_group_form.html` | UI section |
| `epg/channel_lifecycle.py` | Integration with channel creation |

---

## Default Keywords (Suggested Presets)

Could offer a "Load defaults" button that adds common keywords:

| Keywords | Behavior | Rationale |
|----------|----------|-----------|
| `Manning Cast, Manningcast` | consolidate | ESPN alternate broadcast |
| `Prime Vision, Primevision` | separate | Unique camera angles |
| `Megacast` | separate | Different megacast experiences |
| `Whiparound` | consolidate | Same whiparound content |
| `Broadcast` | consolidate | Group home/away feeds |

These could be stored as a constant and offered via a button, not auto-applied.
