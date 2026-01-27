---
title: Dashboard
parent: User Guide
nav_order: 2
---

# Dashboard

The dashboard provides an at-a-glance overview of your Teamarr setup and current status.

## Version & Updates

Shows your current Teamarr version. When updates are available, a notification badge appears with a link to the latest release.

Configure update notifications in [Settings → Advanced](settings/advanced#update-notifications).

## EPG Status

### Last Generation

Shows when the EPG was last generated and the duration it took.

### Next Scheduled Run

If scheduled generation is enabled, shows when the next automatic run will occur.

### Generate Now

Manually trigger an EPG generation run. Progress is shown in real-time.

## Statistics

Quick counts of your configured items:

| Stat | Description |
|------|-------------|
| **Teams** | Number of teams configured for team-based EPG |
| **Event Groups** | Number of event groups configured |
| **Channels** | Total channels currently in Dispatcharr |
| **Templates** | Number of templates available |

## Quick Actions

- **Generate EPG** - Run EPG generation immediately
- **Refresh Cache** - Update team and league data from providers
- **View Channels** - Jump to the Channels page

## Dispatcharr Connection

Shows the connection status to your Dispatcharr instance:

- **Connected** - Successfully communicating with Dispatcharr
- **Disconnected** - Unable to reach Dispatcharr (check Settings → Integrations)
- **Not Configured** - Dispatcharr integration not set up yet
