---
title: Teams
parent: Settings
grand_parent: User Guide
nav_order: 2
---

# Team Settings

Configure settings for team-based EPG generation.

## Schedule Days Ahead

How far ahead to fetch team schedules. This affects the `.next` template variables that show upcoming games. Default is 30 days.

Options: 7, 14, 30, 60, or 90 days.

## Midnight Crossover

Controls what filler content is shown when a game crosses midnight:

- **Show postgame filler** - Display postgame content after midnight
- **Show idle filler** - Display idle/off-air content after midnight

## Channel ID Format

The format string for generating channel IDs. Available variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `{team_name}` | Team name (spaces preserved) | `New York Yankees` |
| `{team_name_pascal}` | Team name in PascalCase | `NewYorkYankees` |
| `{league}` | League slug | `mlb` |
| `{league_id}` | League ID | `mlb` |

Default: `{team_name_pascal}.{league_id}`
