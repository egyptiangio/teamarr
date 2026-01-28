---
title: Dashboard
parent: User Guide
nav_order: 2
---

# Dashboard

The dashboard provides an at-a-glance overview of your Teamarr setup, statistics, and EPG generation history.

## Quick Actions

Located in the top-right corner, these buttons provide shortcuts to common tasks:

| Action | Description |
|--------|-------------|
| **Create Template** | Jump to the template creation form |
| **Import Teams** | Import teams from the league cache |
| **Import Event Group** | Import a stream group from Dispatcharr |
| **Generate EPG** | Manually trigger EPG generation |

## Statistics Quadrants

The dashboard displays four quadrants with detailed statistics. Some stats have tooltips with additional breakdowns - hover to view.

### Teams

| Stat | Description | Tooltip |
|------|-------------|---------|
| **Total** | Number of teams configured | None |
| **Leagues** | Number of unique leagues | League breakdown with logos |
| **Active** | Teams with upcoming or recent games | None |
| **Assigned** | Teams assigned to a Dispatcharr channel | None |

### Event Groups

| Stat | Description | Tooltip |
|------|-------------|---------|
| **Groups** | Number of event groups configured | Per-group match rates |
| **Leagues** | Unique leagues across all groups | League breakdown with logos |
| **Streams** | Total streams across all groups | None |
| **Matched** | Streams matched to real events | Match rate by group |

### EPG

| Stat | Description | Tooltip |
|------|-------------|---------|
| **Channels** | Total channels in EPG | Team vs event breakdown |
| **Events** | Number of game programmes | Team vs event breakdown |
| **Filler** | Filler programmes | Pregame/postgame/idle breakdown |
| **Total** | Total programmes in the EPG | None |

### Channels

| Stat | Description | Tooltip |
|------|-------------|---------|
| **Active** | Channels currently active in Dispatcharr | None |
| **Logos** | Channels with logo URLs | None |
| **Groups** | Channel groups in use | Group breakdown |
| **Deleted 24h** | Channels deleted in the last 24 hours (event cleanup) | None |

## EPG Generation History

A table showing recent EPG generation runs with:

| Column | Description |
|--------|-------------|
| **Status** | Success (✓), failed (✗), or running (spinner) |
| **Generated At** | Timestamp of the run |
| **Teams** | Number of teams processed |
| **Events** | Number of game programmes created |
| **Filler** | Total filler programmes (pregame + postgame + idle) |
| **Managed Channels** | Channels created or updated |
| **Duration** | How long the generation took |
| **Size** | XMLTV file size |

## Getting Started Guide

When no teams or templates are configured, the dashboard displays a getting started guide with four steps:

1. **Configure Settings** - Connect to Dispatcharr, set EPG output path and timezone
2. **Create Templates** - Define title/description formats using variables
3. **Add Teams** - Import teams for team-based EPG (one channel per team)
4. **Create Event Groups** - Import stream groups for event-based EPG (dynamic channels)