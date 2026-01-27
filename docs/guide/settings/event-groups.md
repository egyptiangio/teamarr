---
title: Event Groups
parent: Settings
grand_parent: User Guide
nav_order: 3
---

# Event Group Settings

Configure defaults for event-based EPG generation.

## Event Matching

### Event Lookahead

How far ahead to match streams to sporting events. Streams are matched to events within this window. Default is 3 days.

Options: 1, 3, 7, 14, or 30 days.

### Duplicate Handling

When multiple streams match the same event:

| Mode | Behavior |
|------|----------|
| **Consolidate** | Combine streams into a single channel with multiple sources |
| **Separate** | Create separate channels for each stream |
| **Ignore** | Only use the first matching stream |

## Exception Keywords

When using Consolidate mode, exception keywords allow special handling for certain streams. Streams matching these terms get sub-consolidated or separated.

Example: Spanish language streams might be consolidated separately from English streams.

| Field | Description |
|-------|-------------|
| **Label** | Display name (used in `{exception_keyword}` template variable) |
| **Match Terms** | Comma-separated terms to match in stream names |
| **Behavior** | Sub-Consolidate, Separate, or Ignore |

## Default Team Filter

A global team filter applied to all event groups that don't have their own filter.

### Filter Mode

- **Include only selected teams** - Only match events involving selected teams
- **Exclude selected teams** - Match all events except those involving selected teams

{: .note }
The filter only applies to leagues where you've made team selections. Leagues with no selections will match all events.

See [Team Filters](../teams/team-filters) for more details.
