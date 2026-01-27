---
title: Channel Management
parent: Settings
grand_parent: User Guide
nav_order: 4
---

# Channel Management Settings

Configure channel lifecycle, numbering, and stream ordering.

## Channel Lifecycle

Controls when channels are created and deleted in Dispatcharr.

### Create Timing

When to create channels before events:

- **When stream available** - Create as soon as stream appears
- **Same day** - Create on the day of the event
- **Day before** through **1 week before** - Create in advance

### Delete Timing

When to delete channels after events:

- **When stream removed** - Delete when stream disappears
- **6 hours after** - Delete 6 hours after event ends
- **Same day** through **1 week after** - Keep channel for a period after

## Channel Numbering

Configure how channel numbers are assigned to Auto groups.

### Channel Range

Set the starting channel number and optional maximum. Channels are assigned sequentially starting from this number.

### Numbering Mode

| Mode | Description | Gaps | Drift Risk |
|------|-------------|------|------------|
| **Strict Block** | Reserve blocks by total stream count per group | Large | Minimal |
| **Rational Block** | Reserve blocks by actual channel count (rounded to 10) | Small | Low |
| **Strict Compact** | No reservation, sequential numbers | None | Higher |

### Sorting Scope

- **Per Group** - Sort channels within each event group separately
- **Global** - Sort all AUTO channels together by sport/league priority

### Sort By

- **Event Time** - Sort by when events start
- **Sport → League → Time** - Group by sport, then league, then time
- **Stream Order** - Keep original M3U stream order

## Stream Ordering

Configure priority rules for ordering streams within consolidated channels.

See [Stream Ordering](../event-groups/stream-matching/ordering) for details.
