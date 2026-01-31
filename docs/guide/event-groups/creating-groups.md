---
title: Creating Groups
parent: Event Groups
grand_parent: User Guide
nav_order: 1
---

# Creating Event Groups

Event groups define how Teamarr matches M3U streams to sporting events and creates channels in Dispatcharr.

## Basic Settings

### Name

A descriptive name for the group (e.g., "Sports Package", "Premium Events").

### M3U Account

Select which Dispatcharr M3U account to pull streams from.

### Stream Group

Select which stream group within the M3U account to use, or "All Groups" to include all streams.

### Template

Select a template to format the EPG data for matched events. Templates control how channel names, descriptions, and program info are displayed.

### Channel Mode

| Mode | Description |
|------|-------------|
| **Auto** | Teamarr automatically assigns channel numbers from the configured range |
| **Manual** | You specify a fixed starting channel number |

## Sports & Leagues

Select which sports and leagues this group should match. Only events from selected leagues will be matched to streams.

You can select entire sports (all leagues) or specific leagues within a sport.

### Soccer Shortcuts

For soccer, the league picker includes quick-select options:

- 'Top Domestic + Champions Leagues' (EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL, UEL, UECL)
- 'Other Soccer Leagues' mass-select (expand to pick individual leagues)
- Overall soccer 'Select All' / 'Clear All' is still available

## Channel Profiles

Override the [global default channel profiles](../settings/integrations#default-channel-profiles) for this group.

- **Use Default** - Inherit from global settings
- **Custom Selection** - Choose specific profiles for this group

### Dynamic Wildcards

In addition to specific profiles, you can use wildcards that dynamically create and assign profiles:

| Wildcard | Description | Example |
|----------|-------------|---------|
| `{sport}` | Profile named after the sport | `football`, `basketball` |
| `{league}` | Profile named after the league | `nfl`, `nba`, `epl` |

Wildcard profiles are created automatically in Dispatcharr if they don't exist.

## Team Filter

Override the [global default team filter](../settings/event-groups#default-team-filter) for this group.

- **Use Default** - Inherit from global settings
- **Custom Filter** - Define include/exclude teams specific to this group

## Stream Matching

Configure how streams are matched to events. See [Stream Matching](stream-matching/) for details.

## Advanced Options

### Enabled

Toggle the group on/off without deleting it.

### Priority

When multiple groups could match the same stream, higher priority groups are checked first.
