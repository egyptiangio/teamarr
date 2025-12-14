# Channel Lifecycle Management - Complete Implementation Plan

## Executive Summary

This document outlines a comprehensive overhaul of Teamarr's channel lifecycle management system. The goal is to achieve **authoritative, gap-free tracking** of all managed channels with full audit history, support for parent/child group relationships, flexible duplicate event handling, and robust reconciliation with Dispatcharr.

---

## Table of Contents

1. [Current Problems](#1-current-problems)
2. [Design Principles](#2-design-principles)
3. [Schema Changes](#3-schema-changes)
4. [Feature: Duplicate Event Handling](#4-feature-duplicate-event-handling)
5. [Feature: Parent/Child Groups](#5-feature-parentchild-groups)
6. [Feature: Multi-Stream Channels](#6-feature-multi-stream-channels)
7. [Feature: Reconciliation System](#7-feature-reconciliation-system)
8. [Feature: Audit History](#8-feature-audit-history)
9. [Migration Plan](#9-migration-plan)
10. [Settings & Configuration](#10-settings--configuration)
11. [UI Changes](#11-ui-changes)
12. [API Changes](#12-api-changes)
13. [Processing Flow Changes](#13-processing-flow-changes)
14. [Edge Cases & Error Handling](#14-edge-cases--error-handling)
15. [Testing Strategy](#15-testing-strategy)
16. [Rollback Plan](#16-rollback-plan)
17. [Implementation Phases](#17-implementation-phases)

---

## 1. Current Problems

### 1.1 Orphaned Records in Teamarr
- `managed_channels` table contains records pointing to Dispatcharr channel IDs that no longer exist
- These records have `deleted_at IS NULL` so they appear "active"
- Causes: channels deleted directly in Dispatcharr UI, deletion succeeded but DB update failed, previous code bugs

### 1.2 Untracked Channels in Dispatcharr
- Channels exist in Dispatcharr with `teamarr-event-*` tvg_id pattern but have no corresponding `managed_channels` record
- Teamarr doesn't know about them, can't manage their lifecycle
- Causes: channel created but DB insert failed, managed_channels record was deleted

### 1.3 Duplicate Channels
- Multiple channels in Dispatcharr for the same ESPN event, same channel number
- Both have the same `tvg_id` (correct, same event) but only one is tracked
- Causes: `get_next_channel_number()` only checks Teamarr DB, not Dispatcharr directly

### 1.4 No Reconciliation
- No logic to verify Dispatcharr channels still exist
- No logic to detect drift (channel settings changed externally)
- No logic to clean up orphaned records
- No detection of untracked channels

### 1.5 Limited Stream Tracking
- Only tracks single `dispatcharr_stream_id` per channel
- No support for multiple streams (failover) per channel
- No tracking of which streams are attached

### 1.6 No Audit Trail
- No history of changes to channels
- Can't answer "what changed and when?"
- Difficult to debug issues

---

## 2. Design Principles

### 2.1 Authoritative Marker
The `tvg_id` field in Dispatcharr identifies Teamarr-managed channels:
```
tvg_id = "teamarr-event-{espn_event_id}"
```

This marker:
- Lives IN Dispatcharr (survives Teamarr DB issues)
- Contains the ESPN event ID (can reconstruct context)
- Is unique per event (for EPG matching)

### 2.2 Unique Channel Identity
A managed channel is uniquely identified by:
```
(event_epg_group_id, espn_event_id, primary_stream_id)
```

Where `primary_stream_id` is:
- NULL for `ignore` and `consolidate` modes (one channel per event per group)
- Set for `separate` mode (one channel per event per stream per group)

### 2.3 Complete Fingerprint
Track ALL data about every channel:
- Identity (IDs, tvg_id, event, group)
- Settings (number, name, groups, profiles, logo)
- Event context (teams, date, league, sport, venue, broadcast)
- Streams (all attached streams with priority and source)
- Lifecycle (created, updated, scheduled delete, deleted)
- Sync state (last verified, status, drift notes)
- History (every change logged)

### 2.4 One Channel Per Event Per Group (Default)
Unless `separate` mode is enabled:
- One channel per ESPN event per Teamarr group
- Multiple streams attached for failover
- Child groups add streams, not channels

### 2.5 Reconciliation as Safety Net
Regular reconciliation detects and handles:
- Orphans in both directions
- Duplicates
- Data drift
- Missing streams

---

## 3. Schema Changes

### 3.1 Enhanced `managed_channels` Table

```sql
-- Drop old table and recreate (migration will preserve data)
CREATE TABLE managed_channels_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- ========== IDENTITY ==========
    dispatcharr_channel_id INTEGER NOT NULL,
    event_epg_group_id INTEGER NOT NULL,
    espn_event_id TEXT NOT NULL,
    tvg_id TEXT NOT NULL,
    primary_stream_id INTEGER,                    -- Stream that created this channel

    -- ========== CHANNEL SETTINGS ==========
    channel_number INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    channel_group_id INTEGER,                     -- Dispatcharr channel group
    stream_profile_id INTEGER,                    -- Dispatcharr stream profile
    channel_profile_id INTEGER,                   -- Dispatcharr channel profile
    dispatcharr_logo_id INTEGER,
    logo_url TEXT,                                -- Source URL used for logo

    -- ========== EVENT CONTEXT ==========
    home_team TEXT,
    home_team_abbrev TEXT,
    home_team_logo TEXT,
    away_team TEXT,
    away_team_abbrev TEXT,
    away_team_logo TEXT,
    event_date TEXT,                              -- ISO datetime (UTC)
    event_name TEXT,
    league TEXT,
    sport TEXT,
    venue TEXT,
    broadcast TEXT,

    -- ========== LIFECYCLE ==========
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    scheduled_delete_at TEXT,
    deleted_at TEXT,
    delete_reason TEXT,
    logo_deleted INTEGER,                         -- 1=deleted, 0=failed, NULL=no logo

    -- ========== SYNC STATE ==========
    last_verified_at TEXT,
    sync_status TEXT DEFAULT 'created',           -- created, in_sync, drifted, orphaned
    sync_notes TEXT,

    FOREIGN KEY (event_epg_group_id) REFERENCES event_epg_groups(id)
);

-- Unique constraint: one channel per (group, event, primary_stream) when not deleted
CREATE UNIQUE INDEX idx_managed_channels_unique
    ON managed_channels_new(event_epg_group_id, espn_event_id, COALESCE(primary_stream_id, 0))
    WHERE deleted_at IS NULL;

-- Additional indexes
CREATE INDEX idx_mc_dispatcharr_id ON managed_channels_new(dispatcharr_channel_id);
CREATE INDEX idx_mc_tvg_id ON managed_channels_new(tvg_id);
CREATE INDEX idx_mc_group_active ON managed_channels_new(event_epg_group_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_mc_pending_delete ON managed_channels_new(scheduled_delete_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_mc_sync_status ON managed_channels_new(sync_status) WHERE deleted_at IS NULL;
CREATE INDEX idx_mc_event ON managed_channels_new(espn_event_id);
```

### 3.2 New `managed_channel_streams` Table

```sql
CREATE TABLE managed_channel_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    managed_channel_id INTEGER NOT NULL,
    dispatcharr_stream_id INTEGER NOT NULL,

    -- ========== STREAM INFO ==========
    stream_name TEXT,
    m3u_account_id INTEGER,
    m3u_account_name TEXT,

    -- ========== SOURCE TRACKING ==========
    source_group_id INTEGER NOT NULL,             -- Which group contributed this stream
    source_group_type TEXT NOT NULL,              -- 'parent' or 'child'

    -- ========== ORDERING ==========
    priority INTEGER DEFAULT 0,                   -- 0 = primary, higher = failover

    -- ========== LIFECYCLE ==========
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    removed_at TEXT,
    remove_reason TEXT,

    -- ========== SYNC STATE ==========
    last_verified_at TEXT,
    in_dispatcharr INTEGER DEFAULT 1,             -- 1=confirmed, 0=missing

    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE,
    FOREIGN KEY (source_group_id) REFERENCES event_epg_groups(id)
);

CREATE UNIQUE INDEX idx_mcs_unique
    ON managed_channel_streams(managed_channel_id, dispatcharr_stream_id)
    WHERE removed_at IS NULL;

CREATE INDEX idx_mcs_stream ON managed_channel_streams(dispatcharr_stream_id);
CREATE INDEX idx_mcs_source_group ON managed_channel_streams(source_group_id);
```

### 3.3 New `managed_channel_history` Table

```sql
CREATE TABLE managed_channel_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    managed_channel_id INTEGER NOT NULL,

    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    change_type TEXT NOT NULL,                    -- created, modified, stream_added, stream_removed,
                                                  -- stream_reordered, verified, drifted, deleted, restored
    change_source TEXT,                           -- epg_generation, reconciliation, manual, external_sync

    field_name TEXT,                              -- Which field changed (NULL for create/delete)
    old_value TEXT,
    new_value TEXT,

    notes TEXT,

    FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id)
);

CREATE INDEX idx_mch_channel ON managed_channel_history(managed_channel_id, changed_at DESC);
CREATE INDEX idx_mch_time ON managed_channel_history(changed_at DESC);
CREATE INDEX idx_mch_type ON managed_channel_history(change_type);
```

### 3.4 Enhanced `event_epg_groups` Table

```sql
-- Add new columns
ALTER TABLE event_epg_groups ADD COLUMN parent_group_id INTEGER REFERENCES event_epg_groups(id);
ALTER TABLE event_epg_groups ADD COLUMN duplicate_event_handling TEXT DEFAULT 'consolidate';
-- Values: 'ignore', 'consolidate', 'separate'

-- Index for finding children
CREATE INDEX idx_eeg_parent ON event_epg_groups(parent_group_id) WHERE parent_group_id IS NOT NULL;
```

---

## 4. Feature: Duplicate Event Handling

### 4.1 Overview

When multiple streams in a single group match the same ESPN event, the `duplicate_event_handling` setting determines behavior:

| Mode | Channels Created | Streams Per Channel | Use Case |
|------|-----------------|---------------------|----------|
| `ignore` | 1 | 1 (first match) | Simple, predictable |
| `consolidate` | 1 | All matches | Failover within group |
| `separate` | N (one per stream) | 1 each | Different feeds (home/away/4K) |

### 4.2 Mode: `ignore`

```
Matched streams for Event 401772785:
  - Stream A: "NFL: 49ers vs Browns"
  - Stream B: "NFL: 49ers vs Browns (Backup)"
  - Stream C: "NFL: SF @ CLE HD"

Processing:
  1. Stream A → No channel exists → CREATE channel 9001, primary_stream_id=A
  2. Stream B → Channel exists for event → SKIP
  3. Stream C → Channel exists for event → SKIP

Result:
  Channel 9001: streams=[A], primary_stream_id=A
```

### 4.3 Mode: `consolidate`

```
Matched streams for Event 401772785:
  - Stream A: "NFL: 49ers vs Browns"
  - Stream B: "NFL: 49ers vs Browns (Backup)"
  - Stream C: "NFL: SF @ CLE HD"

Processing:
  1. Stream A → No channel exists → CREATE channel 9001, primary_stream_id=A
  2. Stream B → Channel exists → ADD stream B to channel (priority 1)
  3. Stream C → Channel exists → ADD stream C to channel (priority 2)

Result:
  Channel 9001: streams=[A, B, C], primary_stream_id=A
```

### 4.4 Mode: `separate`

```
Matched streams for Event 401772785:
  - Stream A: "NFL: 49ers vs Browns"
  - Stream B: "NFL: 49ers vs Browns (Backup)"
  - Stream C: "NFL: SF @ CLE HD"

Processing:
  1. Stream A → No channel with primary_stream_id=A → CREATE channel 9001
  2. Stream B → No channel with primary_stream_id=B → CREATE channel 9002
  3. Stream C → No channel with primary_stream_id=C → CREATE channel 9003

Result:
  Channel 9001: streams=[A], primary_stream_id=A
  Channel 9002: streams=[B], primary_stream_id=B
  Channel 9003: streams=[C], primary_stream_id=C

Note: All three have tvg_id="teamarr-event-401772785" (same EPG data)
```

### 4.5 Lookup Logic

```python
def find_existing_channel(group_id: int, event_id: str, stream_id: int, mode: str):
    """Find existing channel based on duplicate handling mode."""

    if mode == 'separate':
        # Must match specific stream
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND primary_stream_id = ?
              AND deleted_at IS NULL
        """, (group_id, event_id, stream_id))
    else:
        # Any channel for this event in this group
        return db_fetch_one("""
            SELECT * FROM managed_channels
            WHERE event_epg_group_id = ?
              AND espn_event_id = ?
              AND deleted_at IS NULL
        """, (group_id, event_id))
```

### 4.6 Changing Modes

**From `ignore` to `consolidate`:**
- Safe. Next EPG generation will add additional streams to existing channels.

**From `consolidate` to `ignore`:**
- Safe. Existing multi-stream channels continue working. New events get single stream.
- Optional: Offer to remove non-primary streams from existing channels.

**From `ignore`/`consolidate` to `separate`:**
- Complex. Existing channels have `primary_stream_id=NULL`.
- Migration: Set `primary_stream_id` to first stream in `managed_channel_streams`.
- New matching will create additional channels for other streams.

**From `separate` to `ignore`/`consolidate`:**
- Complex. Multiple channels exist for same event.
- Migration: Keep one (lowest channel_number?), mark others for deletion.
- Or: Warn user and require manual cleanup first.

---

## 5. Feature: Parent/Child Groups

### 5.1 Overview

A child group's streams are added to its parent's channels as failover streams. Child groups never create channels.

```
Parent: "NFL Premium" (channel_start: 9001)
├── Creates channels for matched events
│
├── Child: "NFL Backup Provider"
│   └── Matched streams added to parent's channels
│
└── Child: "NFL International"
    └── Matched streams added to parent's channels
```

### 5.2 Constraints

1. **One level deep**: A child cannot have children (no nesting)
2. **Same sport**: Child must have same `assigned_sport` as parent
3. **Single parent**: A child has exactly one parent
4. **No channel creation**: Child groups have no `channel_start` and create no channels
5. **Supplemental only**: If parent has no channel for an event, child's stream is skipped

### 5.3 Schema Validation

```python
def validate_parent_child(child_group: dict, parent_group: dict) -> tuple[bool, str]:
    """Validate parent/child relationship."""

    # Parent cannot be a child itself
    if parent_group.get('parent_group_id'):
        return False, "Cannot assign a child group as parent (no nesting)"

    # Same sport required
    if child_group.get('assigned_sport') != parent_group.get('assigned_sport'):
        return False, f"Child must have same sport as parent ({parent_group['assigned_sport']})"

    # Child cannot have existing channels
    existing_channels = get_managed_channels_for_group(child_group['id'])
    if existing_channels:
        return False, f"Group has {len(existing_channels)} existing channels. Cannot convert to child."

    return True, "Valid"
```

### 5.4 Processing Flow

```python
def process_event_groups():
    """Process all event groups in correct order."""

    # Phase 1: Process all PARENT groups (create channels)
    parent_groups = get_groups_where(parent_group_id=None, enabled=True)
    for group in parent_groups:
        process_parent_group(group)

    # Phase 2: Process all CHILD groups (add streams to parent channels)
    child_groups = get_groups_where(parent_group_id__not=None, enabled=True)
    for child in child_groups:
        process_child_group(child)


def process_child_group(child_group: dict):
    """Add child's matched streams to parent's channels."""

    parent = get_event_epg_group(child_group['parent_group_id'])
    matched_streams = match_streams_to_events(child_group)

    for matched in matched_streams:
        event_id = matched['event']['id']
        stream = matched['stream']

        # Find parent's channel for this event
        parent_channel = get_managed_channel_by_event(event_id, parent['id'])

        if not parent_channel:
            # Parent has no channel for this event - skip
            log.debug(f"Child stream '{stream['name']}' skipped - no parent channel for event {event_id}")
            continue

        # Check if stream already attached
        existing_streams = get_channel_streams(parent_channel['id'])
        if stream['id'] in [s['dispatcharr_stream_id'] for s in existing_streams]:
            continue

        # Add stream to parent's channel
        add_stream_to_channel(
            managed_channel_id=parent_channel['id'],
            stream_id=stream['id'],
            stream_name=stream['name'],
            source_group_id=child_group['id'],
            source_group_type='child',
            priority=len(existing_streams)  # Next priority after existing
        )
```

### 5.5 UI for Child Groups

When a group is marked as a child:
- Hide `channel_start` field (not applicable)
- Hide `duplicate_event_handling` field (follows parent)
- Show parent group name prominently
- Show info: "Streams from this group will be added as failover to [Parent Name] channels"

### 5.6 Import Flow for Child Groups

When importing a Dispatcharr group:

```
Import Group Modal:
┌─────────────────────────────────────────────────────┐
│ Import: "NFL Backup Streams"                        │
│                                                     │
│ Group Type:                                         │
│ ○ Parent (creates channels)                         │
│ ● Child (adds streams to existing channels)         │
│                                                     │
│ Parent Group: [NFL Premium ▼]                       │
│   (Only shows groups with same sport)               │
│                                                     │
│ Sport: [Football ▼]                                 │
│ League: [NFL ▼]                                     │
│                                                     │
│ [Cancel] [Import]                                   │
└─────────────────────────────────────────────────────┘
```

---

## 6. Feature: Multi-Stream Channels

### 6.1 Overview

A single channel can have multiple streams attached for failover. Dispatcharr handles failover automatically.

```
Channel 9001: "49ers vs Browns"
├── Stream 0 (priority 0): "NFL: 49ers vs Browns" (parent, primary)
├── Stream 1 (priority 1): "NFL: 49ers vs Browns Backup" (parent, consolidated)
└── Stream 2 (priority 2): "NFL-B: SF @ CLE" (child)
```

### 6.2 Stream Priority

- Priority 0 = primary stream (first to try)
- Priority 1, 2, 3... = failover order
- Parent streams come before child streams
- Within parent: order of matching (first matched = lowest priority number)
- Within child groups: order of child group processing, then order of matching

### 6.3 Adding Streams

```python
def add_stream_to_channel(
    managed_channel_id: int,
    stream_id: int,
    stream_name: str,
    source_group_id: int,
    source_group_type: str,  # 'parent' or 'child'
    priority: int = None,
    m3u_account_id: int = None
):
    """Add a stream to a managed channel."""

    # Auto-assign priority if not specified
    if priority is None:
        max_priority = db_fetch_one("""
            SELECT MAX(priority) as max_p FROM managed_channel_streams
            WHERE managed_channel_id = ? AND removed_at IS NULL
        """, (managed_channel_id,))
        priority = (max_priority['max_p'] or -1) + 1

    # Insert stream record
    db_execute("""
        INSERT INTO managed_channel_streams
        (managed_channel_id, dispatcharr_stream_id, stream_name,
         source_group_id, source_group_type, priority, m3u_account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (managed_channel_id, stream_id, stream_name,
          source_group_id, source_group_type, priority, m3u_account_id))

    # Update Dispatcharr
    all_streams = get_channel_streams_ordered(managed_channel_id)
    stream_ids = [s['dispatcharr_stream_id'] for s in all_streams]
    dispatcharr_api.assign_streams(
        get_dispatcharr_channel_id(managed_channel_id),
        stream_ids
    )

    # Log history
    log_channel_history(
        managed_channel_id,
        change_type='stream_added',
        field_name='streams',
        new_value=str(stream_id),
        notes=f"Added stream '{stream_name}' from {source_group_type} group"
    )
```

### 6.4 Removing Streams

```python
def remove_stream_from_channel(
    managed_channel_id: int,
    stream_id: int,
    reason: str = None
):
    """Remove a stream from a managed channel."""

    # Soft delete the stream record
    db_execute("""
        UPDATE managed_channel_streams
        SET removed_at = CURRENT_TIMESTAMP, remove_reason = ?
        WHERE managed_channel_id = ? AND dispatcharr_stream_id = ? AND removed_at IS NULL
    """, (reason, managed_channel_id, stream_id))

    # Re-sequence remaining streams
    remaining = get_channel_streams_ordered(managed_channel_id)
    for i, stream in enumerate(remaining):
        if stream['priority'] != i:
            db_execute("""
                UPDATE managed_channel_streams SET priority = ?
                WHERE id = ?
            """, (i, stream['id']))

    # Update Dispatcharr
    stream_ids = [s['dispatcharr_stream_id'] for s in remaining]
    dispatcharr_api.assign_streams(
        get_dispatcharr_channel_id(managed_channel_id),
        stream_ids
    )

    # Log history
    log_channel_history(
        managed_channel_id,
        change_type='stream_removed',
        field_name='streams',
        old_value=str(stream_id),
        notes=f"Removed stream: {reason}"
    )
```

---

## 7. Feature: Reconciliation System

### 7.1 Overview

Reconciliation compares Teamarr's state to Dispatcharr's state and detects/handles discrepancies.

### 7.2 Reconciliation Types

| Type | Description | Detection | Action |
|------|-------------|-----------|--------|
| Orphan in Teamarr | managed_channels points to deleted Dispatcharr channel | Query Dispatcharr by ID → not found | Mark as orphaned/deleted |
| Orphan in Dispatcharr | Channel has teamarr-* tvg_id but no managed_channels record | Query Dispatcharr for teamarr-*, compare to DB | Delete from Dispatcharr |
| Duplicate in Group | Multiple channels for same event in same group | Query managed_channels grouped by (group, event) | Keep one, delete others, merge streams |
| Duplicate in Dispatcharr | Multiple channels with same tvg_id | Query Dispatcharr grouped by tvg_id | Delete untracked ones |
| Data Drift | Channel settings changed externally | Compare fingerprint to Dispatcharr | Update sync_status, optionally restore |
| Stream Drift | Streams added/removed externally | Compare managed_channel_streams to Dispatcharr | Update tracking, optionally restore |
| Data Mismatch | managed_channels.espn_event_id doesn't match tvg_id | Extract event from tvg_id, compare | Flag for review |

### 7.3 Full Reconciliation Function

```python
def full_reconciliation(dry_run: bool = False) -> dict:
    """
    Complete reconciliation of Teamarr state with Dispatcharr.

    Args:
        dry_run: If True, report issues but don't fix them

    Returns:
        Dict with counts and details of each issue type
    """
    results = {
        'orphan_in_teamarr': [],
        'orphan_in_dispatcharr': [],
        'duplicate_in_group': [],
        'duplicate_in_dispatcharr': [],
        'data_drift': [],
        'stream_drift': [],
        'data_mismatch': [],
        'in_sync': 0,
        'actions_taken': []
    }

    # Get all Dispatcharr channels with teamarr-* tvg_id
    all_dispatcharr = dispatcharr_api.get_all_channels()
    teamarr_channels = {
        c['id']: c for c in all_dispatcharr
        if (c.get('tvg_id') or '').startswith('teamarr-event-')
    }

    # Get all active managed_channels
    all_managed = get_all_managed_channels(include_deleted=False)
    managed_by_dispatcharr_id = {m['dispatcharr_channel_id']: m for m in all_managed}

    # ===== CHECK 1: Orphans in Teamarr =====
    for managed in all_managed:
        if managed['dispatcharr_channel_id'] not in teamarr_channels:
            results['orphan_in_teamarr'].append(managed)
            if not dry_run:
                mark_channel_orphaned(managed['id'], 'Channel not found in Dispatcharr')
                results['actions_taken'].append(f"Marked channel {managed['id']} as orphaned")

    # ===== CHECK 2: Orphans in Dispatcharr =====
    for disp_id, channel in teamarr_channels.items():
        if disp_id not in managed_by_dispatcharr_id:
            results['orphan_in_dispatcharr'].append(channel)
            if not dry_run:
                dispatcharr_api.delete_channel(disp_id)
                results['actions_taken'].append(f"Deleted untracked Dispatcharr channel {disp_id}")

    # ===== CHECK 3: Duplicates in Group =====
    from collections import defaultdict
    by_group_event = defaultdict(list)
    for managed in all_managed:
        # Skip orphans (already handled)
        if managed['dispatcharr_channel_id'] not in teamarr_channels:
            continue
        key = (managed['event_epg_group_id'], managed['espn_event_id'])
        by_group_event[key].append(managed)

    for (group_id, event_id), channels in by_group_event.items():
        if len(channels) > 1:
            # Check if this is 'separate' mode (duplicates allowed)
            group = get_event_epg_group(group_id)
            if group.get('duplicate_event_handling') == 'separate':
                # Verify each has different primary_stream_id
                primary_streams = [c['primary_stream_id'] for c in channels]
                if len(set(primary_streams)) == len(primary_streams):
                    continue  # Valid separate mode

            results['duplicate_in_group'].append(channels)
            if not dry_run:
                # Keep first (lowest channel_number), delete others
                channels.sort(key=lambda c: c['channel_number'])
                keeper = channels[0]
                for dupe in channels[1:]:
                    # Merge streams to keeper
                    dupe_streams = get_channel_streams(dupe['id'])
                    for stream in dupe_streams:
                        add_stream_to_channel(keeper['id'], stream['dispatcharr_stream_id'], ...)
                    # Delete dupe
                    delete_managed_channel(dupe, reason='Duplicate merged')
                    results['actions_taken'].append(f"Merged duplicate {dupe['id']} into {keeper['id']}")

    # ===== CHECK 4: Duplicates in Dispatcharr (same tvg_id) =====
    by_tvg_id = defaultdict(list)
    for channel in teamarr_channels.values():
        by_tvg_id[channel['tvg_id']].append(channel)

    for tvg_id, channels in by_tvg_id.items():
        if len(channels) > 1:
            # Check which are tracked
            tracked = [c for c in channels if c['id'] in managed_by_dispatcharr_id]
            untracked = [c for c in channels if c['id'] not in managed_by_dispatcharr_id]

            if untracked:
                results['duplicate_in_dispatcharr'].append({
                    'tvg_id': tvg_id,
                    'tracked': tracked,
                    'untracked': untracked
                })
                if not dry_run:
                    for channel in untracked:
                        dispatcharr_api.delete_channel(channel['id'])
                        results['actions_taken'].append(f"Deleted untracked duplicate {channel['id']}")

    # ===== CHECK 5: Data Drift =====
    for managed in all_managed:
        disp_channel = teamarr_channels.get(managed['dispatcharr_channel_id'])
        if not disp_channel:
            continue  # Already handled as orphan

        drifts = compare_channel_state(managed, disp_channel)
        if drifts:
            results['data_drift'].append({'managed': managed, 'drifts': drifts})
            if not dry_run:
                update_sync_status(managed['id'], 'drifted', '; '.join(drifts))
                for drift in drifts:
                    log_channel_history(managed['id'], 'drifted', notes=drift)
        else:
            results['in_sync'] += 1
            update_sync_status(managed['id'], 'in_sync')

    # ===== CHECK 6: Stream Drift =====
    for managed in all_managed:
        if managed['dispatcharr_channel_id'] not in teamarr_channels:
            continue

        our_streams = set(s['dispatcharr_stream_id'] for s in get_channel_streams(managed['id']))
        their_streams = set(s['id'] for s in dispatcharr_api.get_channel_streams(managed['dispatcharr_channel_id']))

        added_externally = their_streams - our_streams
        removed_externally = our_streams - their_streams

        if added_externally or removed_externally:
            results['stream_drift'].append({
                'managed': managed,
                'added': list(added_externally),
                'removed': list(removed_externally)
            })

    # ===== CHECK 7: Data Mismatch (tvg_id vs espn_event_id) =====
    for managed in all_managed:
        disp_channel = teamarr_channels.get(managed['dispatcharr_channel_id'])
        if not disp_channel:
            continue

        expected_tvg = f"teamarr-event-{managed['espn_event_id']}"
        actual_tvg = disp_channel.get('tvg_id')

        if actual_tvg and actual_tvg != expected_tvg:
            results['data_mismatch'].append({
                'managed': managed,
                'expected_tvg': expected_tvg,
                'actual_tvg': actual_tvg
            })

    return results


def compare_channel_state(managed: dict, dispatcharr: dict) -> list:
    """Compare managed channel fingerprint to Dispatcharr state."""
    drifts = []

    if managed['channel_number'] != dispatcharr.get('channel_number'):
        drifts.append(f"channel_number: {managed['channel_number']} → {dispatcharr.get('channel_number')}")

    if managed['channel_name'] != dispatcharr.get('name'):
        drifts.append(f"channel_name changed")

    if managed['tvg_id'] != dispatcharr.get('tvg_id'):
        drifts.append(f"tvg_id: {managed['tvg_id']} → {dispatcharr.get('tvg_id')}")

    if managed['channel_group_id'] != dispatcharr.get('group'):
        drifts.append(f"group: {managed['channel_group_id']} → {dispatcharr.get('group')}")

    # Add more field comparisons as needed

    return drifts
```

### 7.4 Reconciliation Triggers

1. **On EPG Generation**: Run lightweight reconciliation before processing
2. **Scheduled**: Optional scheduled full reconciliation (setting)
3. **Manual**: User can trigger from UI
4. **On Startup**: Detect orphans/duplicates on app start

### 7.5 Reconciliation Settings

```python
# In settings table
reconciliation_settings = {
    'reconcile_on_epg_generation': True,    # Run before each EPG generation
    'reconcile_on_startup': True,           # Run on app start
    'auto_fix_orphan_teamarr': True,        # Auto-mark orphans as deleted
    'auto_fix_orphan_dispatcharr': False,   # Auto-delete untracked channels (dangerous)
    'auto_fix_duplicates': False,           # Auto-merge duplicates
    'reconciliation_schedule': None,        # Cron expression for scheduled reconciliation
}
```

---

## 8. Feature: Audit History

### 8.1 Overview

Every change to a managed channel is logged for debugging and auditing.

### 8.2 Change Types

| Type | Description | Fields Logged |
|------|-------------|---------------|
| `created` | Channel created | All initial values |
| `modified` | Channel settings changed | field_name, old_value, new_value |
| `stream_added` | Stream attached to channel | stream_id, stream_name, source_group |
| `stream_removed` | Stream detached from channel | stream_id, reason |
| `stream_reordered` | Stream priority changed | old_priority, new_priority |
| `verified` | Reconciliation confirmed in_sync | - |
| `drifted` | Reconciliation detected drift | drift details |
| `deleted` | Channel deleted | delete_reason |
| `restored` | Channel restored (if implemented) | - |

### 8.3 Logging Function

```python
def log_channel_history(
    managed_channel_id: int,
    change_type: str,
    change_source: str = None,
    field_name: str = None,
    old_value: str = None,
    new_value: str = None,
    notes: str = None
):
    """Log a change to channel history."""

    db_execute("""
        INSERT INTO managed_channel_history
        (managed_channel_id, change_type, change_source, field_name, old_value, new_value, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (managed_channel_id, change_type, change_source, field_name, old_value, new_value, notes))
```

### 8.4 Querying History

```python
def get_channel_history(managed_channel_id: int, limit: int = 100) -> list:
    """Get history for a specific channel."""
    return db_fetch_all("""
        SELECT * FROM managed_channel_history
        WHERE managed_channel_id = ?
        ORDER BY changed_at DESC
        LIMIT ?
    """, (managed_channel_id, limit))


def get_recent_changes(hours: int = 24, change_types: list = None) -> list:
    """Get recent changes across all channels."""
    query = """
        SELECT mch.*, mc.channel_name, mc.channel_number
        FROM managed_channel_history mch
        JOIN managed_channels mc ON mch.managed_channel_id = mc.id
        WHERE mch.changed_at >= datetime('now', ?)
    """
    params = [f'-{hours} hours']

    if change_types:
        query += f" AND mch.change_type IN ({','.join('?' * len(change_types))})"
        params.extend(change_types)

    query += " ORDER BY mch.changed_at DESC"

    return db_fetch_all(query, params)
```

---

## 9. Migration Plan

### 9.1 Overview

Migrate existing data to new schema while preserving all information and maintaining backward compatibility.

### 9.2 Pre-Migration Backup

```python
def backup_before_migration():
    """Create backup of all affected tables."""
    import shutil
    from datetime import datetime

    db_path = get_db_path()
    backup_path = f"{db_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Database backed up to {backup_path}")
    return backup_path
```

### 9.3 Schema Migration Steps

```python
def migrate_channel_lifecycle_v2():
    """
    Migration for enhanced channel lifecycle management.

    Steps:
    1. Backup database
    2. Create new tables
    3. Migrate managed_channels data
    4. Populate managed_channel_streams from existing data
    5. Add new columns to event_epg_groups
    6. Run initial reconciliation
    7. Clean up orphans and duplicates
    """

    backup_path = backup_before_migration()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Step 1: Create managed_channel_streams table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS managed_channel_streams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                managed_channel_id INTEGER NOT NULL,
                dispatcharr_stream_id INTEGER NOT NULL,
                stream_name TEXT,
                m3u_account_id INTEGER,
                m3u_account_name TEXT,
                source_group_id INTEGER,
                source_group_type TEXT DEFAULT 'parent',
                priority INTEGER DEFAULT 0,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                removed_at TEXT,
                remove_reason TEXT,
                last_verified_at TEXT,
                in_dispatcharr INTEGER DEFAULT 1,
                FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id) ON DELETE CASCADE
            )
        """)

        # Step 2: Create managed_channel_history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS managed_channel_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                managed_channel_id INTEGER NOT NULL,
                changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                change_type TEXT NOT NULL,
                change_source TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                notes TEXT,
                FOREIGN KEY (managed_channel_id) REFERENCES managed_channels(id)
            )
        """)

        # Step 3: Add new columns to managed_channels
        new_columns = [
            ("primary_stream_id", "INTEGER"),
            ("tvg_id", "TEXT"),
            ("logo_url", "TEXT"),
            ("home_team_abbrev", "TEXT"),
            ("home_team_logo", "TEXT"),
            ("away_team_abbrev", "TEXT"),
            ("away_team_logo", "TEXT"),
            ("event_name", "TEXT"),
            ("league", "TEXT"),
            ("sport", "TEXT"),
            ("venue", "TEXT"),
            ("broadcast", "TEXT"),
            ("updated_at", "TEXT"),
            ("delete_reason", "TEXT"),
            ("last_verified_at", "TEXT"),
            ("sync_status", "TEXT DEFAULT 'migrated'"),
            ("sync_notes", "TEXT"),
        ]

        existing_columns = {row[1] for row in cursor.execute("PRAGMA table_info(managed_channels)")}

        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                cursor.execute(f"ALTER TABLE managed_channels ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added column {col_name} to managed_channels")

        # Step 4: Populate tvg_id from espn_event_id
        cursor.execute("""
            UPDATE managed_channels
            SET tvg_id = 'teamarr-event-' || espn_event_id
            WHERE tvg_id IS NULL AND espn_event_id IS NOT NULL
        """)

        # Step 5: Migrate existing stream references to managed_channel_streams
        cursor.execute("""
            INSERT INTO managed_channel_streams
            (managed_channel_id, dispatcharr_stream_id, source_group_id, source_group_type, priority)
            SELECT id, dispatcharr_stream_id, event_epg_group_id, 'parent', 0
            FROM managed_channels
            WHERE dispatcharr_stream_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM managed_channel_streams
                  WHERE managed_channel_id = managed_channels.id
                    AND dispatcharr_stream_id = managed_channels.dispatcharr_stream_id
              )
        """)
        logger.info("Migrated existing streams to managed_channel_streams")

        # Step 6: Add new columns to event_epg_groups
        group_columns = [
            ("parent_group_id", "INTEGER"),
            ("duplicate_event_handling", "TEXT DEFAULT 'consolidate'"),
        ]

        existing_group_cols = {row[1] for row in cursor.execute("PRAGMA table_info(event_epg_groups)")}

        for col_name, col_type in group_columns:
            if col_name not in existing_group_cols:
                cursor.execute(f"ALTER TABLE event_epg_groups ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added column {col_name} to event_epg_groups")

        # Step 7: Create indexes
        indexes = [
            ("idx_mc_dispatcharr_id", "managed_channels(dispatcharr_channel_id)"),
            ("idx_mc_tvg_id", "managed_channels(tvg_id)"),
            ("idx_mc_event", "managed_channels(espn_event_id)"),
            ("idx_mcs_stream", "managed_channel_streams(dispatcharr_stream_id)"),
            ("idx_mcs_channel", "managed_channel_streams(managed_channel_id)"),
            ("idx_mch_channel", "managed_channel_history(managed_channel_id)"),
        ]

        for idx_name, idx_def in indexes:
            try:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}")
            except Exception as e:
                logger.warning(f"Could not create index {idx_name}: {e}")

        # Step 8: Log migration to history
        cursor.execute("""
            INSERT INTO managed_channel_history
            (managed_channel_id, change_type, change_source, notes)
            SELECT id, 'migrated', 'schema_migration', 'Migrated to v2 schema'
            FROM managed_channels
        """)

        conn.commit()
        logger.info("Migration completed successfully")

        # Step 9: Run initial reconciliation
        logger.info("Running post-migration reconciliation...")
        results = full_reconciliation(dry_run=True)
        logger.info(f"Reconciliation results: {results}")

        return True

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        logger.info(f"Database can be restored from {backup_path}")
        raise

    finally:
        conn.close()
```

### 9.4 Post-Migration Cleanup

```python
def post_migration_cleanup():
    """
    Clean up issues found during migration.

    This is separate from migration so user can review first.
    """

    # Run reconciliation with fixes enabled
    results = full_reconciliation(dry_run=False)

    cleanup_summary = {
        'orphans_marked_deleted': len(results['orphan_in_teamarr']),
        'untracked_deleted': len(results['orphan_in_dispatcharr']),
        'duplicates_merged': len(results['duplicate_in_group']),
        'actions': results['actions_taken']
    }

    return cleanup_summary
```

### 9.5 Migration UI Flow

```
Settings Page:
┌─────────────────────────────────────────────────────┐
│ Database Migration Required                         │
│                                                     │
│ A database migration is needed to enable new        │
│ channel lifecycle management features.              │
│                                                     │
│ What will happen:                                   │
│ • Database will be backed up                        │
│ • New tables created for stream and history tracking│
│ • Existing data migrated to new format              │
│ • Reconciliation will detect any issues             │
│                                                     │
│ ⚠️  This cannot be undone (but backup will be saved) │
│                                                     │
│ [Cancel] [Run Migration]                            │
└─────────────────────────────────────────────────────┘

After Migration:
┌─────────────────────────────────────────────────────┐
│ Migration Complete                                  │
│                                                     │
│ Issues Found:                                       │
│ • 19 orphaned records in Teamarr                   │
│ • 27 untracked channels in Dispatcharr             │
│ • 11 duplicate channel pairs                        │
│                                                     │
│ [View Details] [Run Cleanup] [Skip for Now]        │
└─────────────────────────────────────────────────────┘
```

---

## 10. Settings & Configuration

### 10.1 Global Settings (settings table)

```python
new_global_settings = {
    # Reconciliation
    'reconcile_on_epg_generation': ('BOOLEAN', True),
    'reconcile_on_startup': ('BOOLEAN', True),
    'auto_fix_orphan_teamarr': ('BOOLEAN', True),
    'auto_fix_orphan_dispatcharr': ('BOOLEAN', False),
    'auto_fix_duplicates': ('BOOLEAN', False),

    # History retention
    'channel_history_retention_days': ('INTEGER', 90),

    # Default duplicate handling for new groups
    'default_duplicate_event_handling': ('TEXT', 'consolidate'),
}
```

### 10.2 Per-Group Settings (event_epg_groups table)

```python
per_group_settings = {
    'parent_group_id': 'INTEGER',           # FK to parent group (NULL = is parent)
    'duplicate_event_handling': 'TEXT',     # 'ignore', 'consolidate', 'separate'
}
```

### 10.3 Settings UI

```
Global Settings:
┌─────────────────────────────────────────────────────┐
│ Channel Lifecycle Management                        │
│                                                     │
│ Reconciliation                                      │
│ ┌─────────────────────────────────────────────────┐ │
│ │ ☑ Run reconciliation before each EPG generation │ │
│ │ ☑ Run reconciliation on startup                 │ │
│ │ ☑ Auto-fix orphaned records in Teamarr          │ │
│ │ ☐ Auto-delete untracked channels in Dispatcharr │ │
│ │ ☐ Auto-merge duplicate channels                 │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ History                                             │
│ ┌─────────────────────────────────────────────────┐ │
│ │ Keep channel history for [90] days              │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ Defaults for New Groups                             │
│ ┌─────────────────────────────────────────────────┐ │
│ │ Duplicate event handling: [Consolidate ▼]       │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ [Save Settings]                                     │
└─────────────────────────────────────────────────────┘
```

---

## 11. UI Changes

### 11.1 Event Group Form

```
Event Group Form (Create/Edit):
┌─────────────────────────────────────────────────────┐
│ Basic Settings                                      │
│ ├── Dispatcharr Group: [NFL Streams ▼]             │
│ ├── Sport: [Football ▼]                            │
│ └── League: [NFL ▼]                                │
│                                                     │
│ Group Type                                          │
│ ├── ○ Parent Group (creates channels)              │
│ │   ├── Channel Start: [9001]                      │
│ │   ├── Channel Group: [Sports ▼]                  │
│ │   └── Duplicate Events: [Consolidate ▼]          │
│ │       • Ignore: First stream only                │
│ │       • Consolidate: One channel, all streams    │
│ │       • Separate: One channel per stream         │
│ │                                                   │
│ └── ○ Child Group (adds streams to parent)         │
│     └── Parent Group: [NFL Premium ▼]              │
│         (Only groups with same sport shown)        │
│                                                     │
│ [Cancel] [Save]                                     │
└─────────────────────────────────────────────────────┘
```

### 11.2 Managed Channels Table (Enhanced)

```
Managed Channels:
┌────────┬────────────────────────────┬─────────┬──────────┬────────────┬────────┐
│ Number │ Name                       │ Streams │ Status   │ Delete At  │ Actions│
├────────┼────────────────────────────┼─────────┼──────────┼────────────┼────────┤
│ 9001   │ 49ers @ Browns             │ 3       │ ✓ Synced │ Dec 2 1AM  │ ⋮      │
│ 9002   │ Saints @ Dolphins          │ 2       │ ⚠ Drift  │ Dec 2 1AM  │ ⋮      │
│ 9003   │ Rams @ Panthers            │ 1       │ ✓ Synced │ Dec 2 1AM  │ ⋮      │
└────────┴────────────────────────────┴─────────┴──────────┴────────────┴────────┘

Expanded Row (click to expand):
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Channel 9001: 49ers @ Browns                                                    │
│                                                                                 │
│ Identity                              │ Event                                   │
│ ├── Dispatcharr ID: 2818              │ ├── ESPN Event: 401772785              │
│ ├── tvg_id: teamarr-event-401772785   │ ├── Date: Dec 1, 2024 1:00 PM          │
│ └── Group: NFL Premium                │ ├── Home: Cleveland Browns             │
│                                       │ └── Away: San Francisco 49ers          │
│                                                                                 │
│ Streams (3)                                                                     │
│ ├── 0: NFL: 49ers vs Browns (parent, primary)                                  │
│ ├── 1: NFL: 49ers vs Browns Backup (parent)                                    │
│ └── 2: NFL-B: SF @ CLE (child: NFL Backup)                                     │
│                                                                                 │
│ Lifecycle                             │ Sync Status                             │
│ ├── Created: Nov 30, 10:00 AM         │ ├── Status: ✓ In Sync                  │
│ ├── Updated: Nov 30, 10:05 AM         │ ├── Last Verified: Nov 30, 12:00 PM    │
│ └── Delete At: Dec 2, 1:00 AM         │ └── Notes: -                           │
│                                                                                 │
│ [View History] [Edit] [Delete]                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 11.3 Channel History Modal

```
Channel History: 9001 - 49ers @ Browns
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Timestamp           │ Type          │ Details                                   │
├─────────────────────┼───────────────┼───────────────────────────────────────────┤
│ Nov 30, 12:00 PM    │ verified      │ Confirmed in sync with Dispatcharr        │
│ Nov 30, 10:05 AM    │ stream_added  │ Added "NFL-B: SF @ CLE" (child group)     │
│ Nov 30, 10:02 AM    │ stream_added  │ Added "NFL: 49ers vs Browns Backup"       │
│ Nov 30, 10:00 AM    │ stream_added  │ Added "NFL: 49ers vs Browns" (primary)    │
│ Nov 30, 10:00 AM    │ created       │ Channel created from EPG generation       │
└─────────────────────────────────────────────────────────────────────────────────┘
│ [Load More] [Export]                                                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 11.4 Reconciliation Dashboard

```
Reconciliation Status:
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Last Run: Nov 30, 2024 12:00 PM (2 hours ago)                                  │
│                                                                                 │
│ Summary                                                                         │
│ ├── Total Managed Channels: 46                                                 │
│ ├── In Sync: 42 ✓                                                              │
│ ├── Drifted: 2 ⚠                                                               │
│ ├── Orphaned (Teamarr): 1 ✗                                                    │
│ └── Orphaned (Dispatcharr): 1 ✗                                                │
│                                                                                 │
│ Issues                                                                          │
│ ┌───────────────────────────────────────────────────────────────────────────┐  │
│ │ ⚠ Channel 9002 drifted: channel_name changed externally                   │  │
│ │   [View] [Restore] [Ignore]                                               │  │
│ │                                                                           │  │
│ │ ⚠ Channel 9005 drifted: stream removed externally                        │  │
│ │   [View] [Restore] [Ignore]                                               │  │
│ │                                                                           │  │
│ │ ✗ Managed channel 9010 orphaned: Dispatcharr channel deleted              │  │
│ │   [Mark Deleted] [Recreate]                                               │  │
│ │                                                                           │  │
│ │ ✗ Untracked channel in Dispatcharr: ID 2850, tvg_id teamarr-event-...    │  │
│ │   [Adopt] [Delete from Dispatcharr]                                       │  │
│ └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│ [Run Reconciliation Now] [Auto-Fix All] [View Full Report]                     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 11.5 Import Group Flow

```
Step 1: Select Dispatcharr Group
┌─────────────────────────────────────────────────────┐
│ Select a Dispatcharr stream group to import:        │
│                                                     │
│ ○ NFL Streams (523 streams)                         │
│ ○ NFL Backup (245 streams)                          │
│ ○ NBA Streams (312 streams)                         │
│                                                     │
│ [Cancel] [Next]                                     │
└─────────────────────────────────────────────────────┘

Step 2: Configure Group
┌─────────────────────────────────────────────────────┐
│ Configure: NFL Backup                               │
│                                                     │
│ Sport: [Football ▼]                                 │
│ League: [NFL ▼]                                     │
│                                                     │
│ Group Type:                                         │
│ ○ Parent Group (creates channels)                   │
│   └── Channel Start: [____]                         │
│                                                     │
│ ● Child Group (adds streams to parent channels)     │
│   └── Parent: [NFL Streams (NFL) ▼]                │
│       ℹ Only showing NFL parent groups              │
│                                                     │
│ [Back] [Import]                                     │
└─────────────────────────────────────────────────────┘
```

---

## 12. API Changes

### 12.1 New Endpoints

```python
# Reconciliation
@app.route('/api/reconciliation/status', methods=['GET'])
def get_reconciliation_status():
    """Get current reconciliation status and last run results."""

@app.route('/api/reconciliation/run', methods=['POST'])
def run_reconciliation():
    """Run reconciliation (optional dry_run parameter)."""

@app.route('/api/reconciliation/fix', methods=['POST'])
def fix_reconciliation_issue():
    """Fix a specific reconciliation issue."""

# Channel History
@app.route('/api/managed-channels/<int:id>/history', methods=['GET'])
def get_channel_history(id):
    """Get history for a specific channel."""

@app.route('/api/managed-channels/history/recent', methods=['GET'])
def get_recent_changes():
    """Get recent changes across all channels."""

# Channel Streams
@app.route('/api/managed-channels/<int:id>/streams', methods=['GET'])
def get_channel_streams(id):
    """Get streams attached to a channel."""

@app.route('/api/managed-channels/<int:id>/streams', methods=['POST'])
def add_channel_stream(id):
    """Add a stream to a channel."""

@app.route('/api/managed-channels/<int:id>/streams/<int:stream_id>', methods=['DELETE'])
def remove_channel_stream(id, stream_id):
    """Remove a stream from a channel."""
```

### 12.2 Modified Endpoints

```python
# Event EPG Groups - add parent_group_id and duplicate_event_handling
@app.route('/api/event-epg/groups', methods=['POST'])
@app.route('/api/event-epg/groups/<int:id>', methods=['PUT'])

# Managed Channels - return enhanced data
@app.route('/api/managed-channels', methods=['GET'])
@app.route('/api/managed-channels/<int:id>', methods=['GET'])
```

---

## 13. Processing Flow Changes

### 13.1 EPG Generation Flow (Updated)

```python
def generate_all_epg():
    """Updated EPG generation flow."""

    # Phase 0: Pre-flight reconciliation
    if settings.get('reconcile_on_epg_generation'):
        reconciliation_results = lightweight_reconciliation()
        if reconciliation_results['critical_issues']:
            handle_critical_issues(reconciliation_results)

    # Phase 1: Team-based EPG (unchanged)
    generate_team_epg()

    # Phase 2: Event-based EPG - Parent groups
    parent_groups = get_parent_groups(enabled=True)
    for group in parent_groups:
        process_parent_group(group)

    # Phase 3: Event-based EPG - Child groups
    child_groups = get_child_groups(enabled=True)
    for child in child_groups:
        process_child_group(child)

    # Phase 4: Channel lifecycle (deletions, syncs)
    process_channel_lifecycle()

    # Phase 5: Post-generation verification
    verify_all_channels()

    # Phase 6: Dispatcharr refresh and EPG association
    refresh_and_associate()
```

### 13.2 Parent Group Processing (Updated)

```python
def process_parent_group(group: dict):
    """Process a parent event group."""

    mode = group.get('duplicate_event_handling', 'consolidate')
    matched_streams = match_streams_to_events(group)

    for matched in matched_streams:
        event = matched['event']
        stream = matched['stream']
        event_id = event['id']
        stream_id = stream['id']

        # Find existing channel based on mode
        existing = find_existing_channel(group['id'], event_id, stream_id, mode)

        if existing:
            if mode == 'consolidate':
                # Add stream if not already attached
                add_stream_if_not_exists(existing, stream, group, 'parent')

            # Sync settings regardless of mode
            sync_channel_settings(existing, event, stream, group)
            continue

        # Create new channel
        channel = create_managed_channel(
            group=group,
            event=event,
            stream=stream,
            primary_stream_id=stream_id if mode == 'separate' else None
        )

        # Add stream to tracking
        add_stream_to_channel(
            channel['id'], stream_id, stream['name'],
            source_group_id=group['id'],
            source_group_type='parent',
            priority=0
        )

        # Log creation
        log_channel_history(channel['id'], 'created', source='epg_generation')
```

### 13.3 Child Group Processing

```python
def process_child_group(child: dict):
    """Process a child event group - add streams to parent channels."""

    parent = get_event_epg_group(child['parent_group_id'])
    matched_streams = match_streams_to_events(child)

    results = {'added': 0, 'skipped_no_parent': 0, 'skipped_exists': 0}

    for matched in matched_streams:
        event = matched['event']
        stream = matched['stream']
        event_id = event['id']

        # Find parent's channel for this event
        parent_channel = find_parent_channel_for_event(parent['id'], event_id)

        if not parent_channel:
            # Parent has no channel for this event
            results['skipped_no_parent'] += 1
            logger.debug(f"Child stream '{stream['name']}' skipped - no parent channel for {event_id}")
            continue

        # Check if stream already attached
        if stream_already_attached(parent_channel['id'], stream['id']):
            results['skipped_exists'] += 1
            continue

        # Get current max priority
        current_streams = get_channel_streams(parent_channel['id'])
        next_priority = max((s['priority'] for s in current_streams), default=-1) + 1

        # Add stream
        add_stream_to_channel(
            parent_channel['id'],
            stream['id'],
            stream['name'],
            source_group_id=child['id'],
            source_group_type='child',
            priority=next_priority
        )

        results['added'] += 1

        log_channel_history(
            parent_channel['id'],
            'stream_added',
            source='epg_generation',
            notes=f"Added from child group '{child.get('name', child['id'])}'"
        )

    logger.info(f"Child group {child['id']}: added {results['added']}, "
                f"skipped (no parent): {results['skipped_no_parent']}, "
                f"skipped (exists): {results['skipped_exists']}")

    return results
```

---

## 14. Edge Cases & Error Handling

### 14.1 Child Group Edge Cases

| Scenario | Handling |
|----------|----------|
| Child matches event parent doesn't have | Skip stream (child is supplemental only) |
| Parent deleted while child exists | Child becomes orphaned - prompt user to reassign or delete |
| Child's parent_group_id set to invalid ID | Validation error on save |
| Attempt to nest children (child of child) | Validation error on save |
| Child has different sport than parent | Validation error on save |
| Change parent of existing child | Allow, but warn if child has contributed streams to old parent |

### 14.2 Duplicate Handling Edge Cases

| Scenario | Handling |
|----------|----------|
| Change mode from separate to consolidate with existing channels | Prompt: keep all, merge, or delete extras |
| Change mode from consolidate to separate | Existing channels keep multiple streams; new matches create new channels |
| Same stream matches same event twice (regex overlap) | Deduplicate in matching phase |
| Stream removed from M3U but attached to channel | Mark stream as `in_dispatcharr=0`, keep record for history |

### 14.3 Reconciliation Edge Cases

| Scenario | Handling |
|----------|----------|
| Channel deleted from Dispatcharr during EPG generation | Detect as orphan, don't recreate in same run |
| Channel settings changed in Dispatcharr during EPG generation | Next sync will detect drift |
| Two Teamarr instances managing same Dispatcharr | Undefined behavior - not supported |
| Dispatcharr API unavailable during reconciliation | Retry with backoff, eventually mark reconciliation as failed |
| Thousands of channels to reconcile | Batch processing with progress reporting |

### 14.4 Stream Management Edge Cases

| Scenario | Handling |
|----------|----------|
| Add stream that already exists on channel | No-op, return success |
| Remove last stream from channel | Warn user, require confirmation |
| Reorder streams changes priority | Update all affected priorities, update Dispatcharr |
| Stream's M3U account deleted | Mark stream as orphaned, keep attached for history |

### 14.5 Error Recovery

```python
def safe_channel_operation(operation, channel_id, *args, **kwargs):
    """Wrapper for channel operations with error recovery."""

    try:
        result = operation(channel_id, *args, **kwargs)
        return {'success': True, 'result': result}

    except DispatcharrAPIError as e:
        logger.error(f"Dispatcharr API error for channel {channel_id}: {e}")

        # Mark channel as needing attention
        update_sync_status(channel_id, 'error', str(e))
        log_channel_history(channel_id, 'error', notes=str(e))

        return {'success': False, 'error': str(e), 'recoverable': True}

    except DatabaseError as e:
        logger.error(f"Database error for channel {channel_id}: {e}")

        # Attempt to log to a fallback location
        log_to_file(f"DB_ERROR: channel {channel_id}: {e}")

        return {'success': False, 'error': str(e), 'recoverable': False}
```

---

## 15. Testing Strategy

### 15.1 Unit Tests

```python
# Test duplicate event handling modes
def test_ignore_mode_skips_second_stream():
    """In ignore mode, second stream for same event is skipped."""

def test_consolidate_mode_adds_streams():
    """In consolidate mode, streams are added to existing channel."""

def test_separate_mode_creates_multiple_channels():
    """In separate mode, each stream gets its own channel."""

# Test parent/child relationships
def test_child_stream_added_to_parent_channel():
    """Child stream is added to parent's channel."""

def test_child_stream_skipped_when_no_parent_channel():
    """Child stream is skipped if parent has no channel for event."""

def test_child_cannot_be_nested():
    """Cannot set parent_group_id on a group that has children."""

# Test reconciliation
def test_detect_orphan_in_teamarr():
    """Detect when managed_channels points to deleted Dispatcharr channel."""

def test_detect_orphan_in_dispatcharr():
    """Detect when Dispatcharr has teamarr channel with no managed_channels record."""

def test_detect_duplicate_in_group():
    """Detect multiple channels for same event in same group."""
```

### 15.2 Integration Tests

```python
# Full flow tests
def test_full_epg_generation_with_parent_child():
    """Run full EPG generation with parent and child groups."""

def test_full_reconciliation_flow():
    """Run reconciliation and verify all issues detected."""

def test_migration_preserves_data():
    """Verify migration doesn't lose any data."""
```

### 15.3 Manual Test Cases

1. Create parent group, generate EPG, verify channels created
2. Create child group, generate EPG, verify streams added to parent channels
3. Change duplicate_event_handling mode, verify behavior changes
4. Delete channel from Dispatcharr, run reconciliation, verify orphan detected
5. Manually add stream to channel in Dispatcharr, run reconciliation, verify drift detected
6. Run migration on database with existing data, verify no data loss
7. Test import flow with child group option

---

## 16. Rollback Plan

### 16.1 Database Rollback

If migration fails or causes issues:

```bash
# Restore from backup
cp /app/data/teamarr.db.backup.YYYYMMDD_HHMMSS /app/data/teamarr.db
```

### 16.2 Feature Flags

Implement feature flags for gradual rollout:

```python
FEATURE_FLAGS = {
    'new_channel_lifecycle': False,      # Master switch
    'multi_stream_channels': False,      # Enable multi-stream support
    'parent_child_groups': False,        # Enable parent/child relationships
    'reconciliation': False,             # Enable reconciliation system
    'audit_history': False,              # Enable history logging
}
```

### 16.3 Rollback Steps

1. Disable feature flags
2. Stop EPG generation
3. Restore database backup if needed
4. Restart application
5. Investigate issue
6. Fix and re-deploy

---

## 17. Implementation Phases

### Phase 1: Schema & Migration (Week 1)

**Goal**: Migrate to new schema without changing behavior

1. Create new tables (managed_channel_streams, managed_channel_history)
2. Add new columns to existing tables
3. Migrate existing data
4. Add database indexes
5. Test migration on copy of production data
6. Deploy migration with feature flags disabled

**Deliverables**:
- Migration script
- Backup/restore procedures
- Migration UI

### Phase 2: Multi-Stream Support (Week 2)

**Goal**: Support multiple streams per channel

1. Update channel creation to track primary_stream_id
2. Implement add_stream_to_channel / remove_stream_to_channel
3. Update Dispatcharr sync to handle multiple streams
4. Add stream display to managed channels UI
5. Implement stream reordering

**Deliverables**:
- Stream management functions
- Updated channel creation flow
- Stream UI in managed channels table

### Phase 3: Duplicate Event Handling (Week 3)

**Goal**: Implement ignore/consolidate/separate modes

1. Add duplicate_event_handling to group form
2. Implement mode-specific channel lookup
3. Implement mode-specific channel creation
4. Handle mode changes on existing groups
5. Add mode display to groups list

**Deliverables**:
- Group form with mode selection
- Mode-specific processing logic
- Mode change handling

### Phase 4: Parent/Child Groups (Week 4)

**Goal**: Implement parent/child group relationships

1. Add parent_group_id to group form
2. Implement child group validation
3. Implement child group processing in EPG generation
4. Update group list to show hierarchy
5. Update import flow with child option

**Deliverables**:
- Parent/child group management
- Child group processing
- Import flow changes

### Phase 5: Reconciliation System (Week 5)

**Goal**: Implement full reconciliation

1. Implement reconciliation detection logic
2. Implement reconciliation fix actions
3. Add reconciliation settings
4. Add reconciliation dashboard UI
5. Integrate reconciliation into EPG generation

**Deliverables**:
- Reconciliation engine
- Reconciliation UI
- Settings integration

### Phase 6: Audit History & Polish (Week 6)

**Goal**: Complete audit history and polish

1. Implement history logging throughout codebase
2. Add history UI to channel details
3. Add recent changes view
4. Performance optimization
5. Documentation updates
6. Final testing and bug fixes

**Deliverables**:
- Complete audit history
- History UI
- Updated documentation
- Production-ready release

---

## Appendix A: Database Schema Diagram

```
┌─────────────────────────┐       ┌─────────────────────────┐
│   event_epg_groups      │       │    managed_channels     │
├─────────────────────────┤       ├─────────────────────────┤
│ id (PK)                 │──┐    │ id (PK)                 │
│ dispatcharr_group_id    │  │    │ dispatcharr_channel_id  │
│ assigned_sport          │  │    │ event_epg_group_id (FK) │──┐
│ assigned_league         │  │    │ espn_event_id           │  │
│ parent_group_id (FK)    │──┘    │ tvg_id                  │  │
│ duplicate_event_handling│       │ primary_stream_id       │  │
│ channel_start           │       │ channel_number          │  │
│ channel_group_id        │       │ channel_name            │  │
│ ...                     │       │ ...                     │  │
└─────────────────────────┘       │ sync_status             │  │
         │                        │ ...                     │  │
         │                        └─────────────────────────┘  │
         │                                   │                 │
         │                                   │ 1:N             │
         │                                   ▼                 │
         │                        ┌─────────────────────────┐  │
         │                        │ managed_channel_streams │  │
         │                        ├─────────────────────────┤  │
         │                        │ id (PK)                 │  │
         │                        │ managed_channel_id (FK) │  │
         │                        │ dispatcharr_stream_id   │  │
         │                        │ source_group_id (FK)    │──┘
         │                        │ source_group_type       │
         │                        │ priority                │
         │                        │ ...                     │
         │                        └─────────────────────────┘
         │
         │                        ┌─────────────────────────┐
         │                        │ managed_channel_history │
         │                        ├─────────────────────────┤
         │                        │ id (PK)                 │
         └───────────────────────▶│ managed_channel_id (FK) │
                                  │ changed_at              │
                                  │ change_type             │
                                  │ field_name              │
                                  │ old_value               │
                                  │ new_value               │
                                  │ ...                     │
                                  └─────────────────────────┘
```

---

## Appendix B: State Diagram

```
Channel States:

    ┌──────────┐
    │ created  │◀─── Channel created
    └────┬─────┘
         │
         ▼ (reconciliation)
    ┌──────────┐
    │ in_sync  │◀─── Verified OK
    └────┬─────┘
         │
         ▼ (drift detected)
    ┌──────────┐
    │ drifted  │◀─── Settings changed externally
    └────┬─────┘
         │
         ├──▶ (restore) ──▶ in_sync
         │
         ▼ (channel missing)
    ┌──────────┐
    │ orphaned │◀─── Dispatcharr channel deleted
    └────┬─────┘
         │
         ▼ (cleanup)
    ┌──────────┐
    │ deleted  │◀─── Soft deleted
    └──────────┘
```

---

## Appendix C: Glossary

| Term | Definition |
|------|------------|
| Parent Group | An event EPG group that creates channels |
| Child Group | An event EPG group that adds streams to parent's channels |
| Primary Stream | The stream that triggered channel creation (for 'separate' mode) |
| Failover Stream | Additional stream attached for redundancy |
| Orphan (Teamarr) | managed_channels record pointing to non-existent Dispatcharr channel |
| Orphan (Dispatcharr) | Channel with teamarr-* tvg_id but no managed_channels record |
| Drift | Difference between Teamarr's expected state and Dispatcharr's actual state |
| Reconciliation | Process of comparing and aligning Teamarr and Dispatcharr states |
| tvg_id | Channel identifier for EPG matching, format: teamarr-event-{espn_event_id} |
| Fingerprint | Complete tracked state of a managed channel |

---

## Appendix D: Implementation Progress Tracker

**Status Legend**: ⬜ Not Started | 🟡 In Progress | ✅ Complete | ⏸️ Blocked

### Phase 1: Schema & Migration
| Task | Status | Notes |
|------|--------|-------|
| Create `managed_channel_streams` table | ✅ | Added to schema.sql and migrations |
| Create `managed_channel_history` table | ✅ | Added to schema.sql and migrations |
| Add new columns to `managed_channels` | ✅ | 15+ new columns for sync/context |
| Add new columns to `event_epg_groups` | ✅ | parent_group_id, duplicate_event_handling |
| Create database indexes | ✅ | All indexes created |
| Write migration function | ✅ | Section 7 in run_migrations() |
| Add DB helper functions | ✅ | Stream, history, lookup, parent/child |
| Test migration on existing data | ✅ | Tested with graceful fallbacks |
| Add backwards compatibility fallbacks | ✅ | try/except for dispatcharr_uuid queries |

### Phase 2: Multi-Stream Support
| Task | Status | Notes |
|------|--------|-------|
| Update channel creation to track primary_stream_id | ✅ | Added to create_managed_channel() |
| Implement add_stream_to_channel() | ✅ | In database/__init__.py |
| Implement remove_stream_from_channel() | ✅ | In database/__init__.py |
| Update Dispatcharr sync for multiple streams | ✅ | consolidate mode adds streams |
| Add stream display to managed channels UI | ⬜ | Future enhancement |

### Phase 3: Duplicate Event Handling
| Task | Status | Notes |
|------|--------|-------|
| Add duplicate_event_handling to schema | ✅ | Added to event_epg_groups |
| Implement mode-specific channel lookup | ✅ | find_existing_channel() with mode |
| Implement mode-specific channel creation | ✅ | In process_matched_streams() |
| Handle consolidate mode stream additions | ✅ | Adds streams to Dispatcharr + DB |

### Phase 4: Parent/Child Groups
| Task | Status | Notes |
|------|--------|-------|
| Add parent_group_id to schema | ✅ | Migration done |
| Implement validation functions | ✅ | validate_parent_child_relationship() |
| Implement child group processing in EPG | ✅ | Child streams added to parent channels |
| Process parents before children | ✅ | Sorted in generate_all_epg() |
| Add parent_group_id to group form UI | ✅ | Parent selector in add/edit mode |
| Enforce inheritance for child groups | ✅ | Backend sets inherited fields NULL |
| Show "↳ inherited" badges in UI | ✅ | Template & Channel Start columns |
| Auto-expand regex section for child edit | ✅ | Only editable section for children |

### Phase 5: Reconciliation System
| Task | Status | Notes |
|------|--------|-------|
| Implement reconciliation detection logic | ✅ | epg/reconciliation.py |
| Implement reconciliation fix actions | ✅ | Auto-fix for orphans, drift |
| Add reconciliation settings | ✅ | Added to settings table |
| Integrate into EPG generation | ✅ | Phase 3a in generate_all_epg() |
| Add reconciliation API endpoint | ✅ | POST /api/channel-lifecycle/reconcile |
| Add reconciliation dashboard UI | ⬜ | Future enhancement |

### Phase 6: Audit History & Polish
| Task | Status | Notes |
|------|--------|-------|
| Implement history logging | ✅ | log_channel_history() on create/delete/modify |
| Channel history API | ✅ | GET /api/channel-lifecycle/history/{id} |
| Recent changes API | ✅ | GET /api/channel-lifecycle/history/recent |
| Channel streams API | ✅ | GET /api/channel-lifecycle/streams/{id} |
| System default duplicate handling | ✅ | get_global_lifecycle_settings() |
| Comprehensive channel info modal | ✅ | GET /api/channel-lifecycle/info/{id} |
| Add history UI to channel details | ✅ | Collapsible history section in info modal |
| Add recent changes view | ⬜ | Future enhancement |

### Phase 7: UUID-Based Identification
| Task | Status | Notes |
|------|--------|-------|
| Capture UUID on channel creation | ✅ | Stored in dispatcharr_uuid column |
| Use UUID in reconciliation | ✅ | Primary identifier, tvg_id as fallback |
| Backfill UUIDs for existing channels | ✅ | Done in reconciliation during EPG gen |
| Orphan detection by UUID | ✅ | GET /api/channel-lifecycle/orphans |
| One-click orphan cleanup | ✅ | POST /api/channel-lifecycle/orphans/cleanup |
| Handle null tvg_id from API | ✅ | `ch.get('tvg_id') or ''` pattern |
| Handle missing UUID column (old DB) | ✅ | try/except fallback in all UUID queries |

---

*Document Version: 1.3*
*Last Updated: November 30, 2024*
*Author: Claude (AI Assistant)*
