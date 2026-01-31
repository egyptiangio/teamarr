---
title: Advanced
parent: Settings
grand_parent: User Guide
nav_order: 7
---

# Advanced Settings

Update notifications, backup/restore, local caching, scheduled maintenance, and API configuration.

## Update Notifications

Teamarr can check for new versions and notify you when updates are available.

### Current Version

Displays your current version and the latest available version. For dev builds, shows commit hashes; for stable builds, shows version numbers.

The release date of the latest version is shown in your configured timezone.

### Settings

| Setting | Description |
|---------|-------------|
| **Enable Automatic Update Checks** | Toggle update checking on/off |
| **Notify about stable releases** | Get notified about new stable versions |
| **Notify about dev builds** | Get notified about new dev commits (if running dev) |

### Check Now

Manually trigger an update check. Results are cached for 1 hour.

## Backup & Restore

### Download Backup

Download a complete backup of your Teamarr database, including:
- All teams and their configurations
- Templates and presets
- Event groups
- Settings

### Restore Backup

Upload a `.db` backup file to restore. A backup of your current data is automatically created before restoring.

{: .warning }
Restoring a backup replaces ALL current data. The application needs to be restarted after restore.

## Local Caching

Teamarr caches team and league data from ESPN and TheSportsDB to improve performance and enable offline matching.

### Cache Status

View the current cache state:
- **Leagues** - Number of leagues cached
- **Teams** - Number of teams cached
- **Last Refresh Duration** - How long the last refresh took
- **Last Refresh** - When the cache was last updated

A **Stale** badge appears if the cache needs refreshing.

### Refresh Cache

Manually refresh the cache to pull the latest team and league data. This fetches data from ESPN and TheSportsDB APIs.

{: .note }
Cache refresh runs automatically on first startup. Manual refresh is useful after adding new leagues or when team rosters change significantly.

## Scheduled Channel Reset

For users experiencing stale channel logos in Jellyfin. Schedule a periodic purge of all Teamarr channels before your media server's guide refresh.

{: .note }
Leave this disabled if you're not experiencing logo caching issues.

### How It Works

1. Enable scheduled reset and set a cron schedule (e.g., `30 3 * * *` for 3:30 AM daily)
2. When the reset schedule fires, Teamarr purges all its channels from Dispatcharr
3. On the next EPG generation, channels are recreated with fresh data
4. Set your Jellyfin guide refresh to run shortly after (e.g., 4:00 AM)

### Settings

| Setting | Description |
|---------|-------------|
| **Enable Scheduled Channel Reset** | Toggle the scheduled reset on/off |
| **Reset Schedule (Cron Expression)** | When to run the reset (standard cron format) |

### Preset Schedules

Quick buttons for common schedules (half-hour to avoid conflicts with typical on-the-hour tasks):
- **Daily 2:30 AM** - `30 2 * * *`
- **Daily 3:30 AM** - `30 3 * * *`
- **Daily 4:30 AM** - `30 4 * * *`
- **Daily 5:30 AM** - `30 5 * * *`

### Use Case

Some media servers (notably Jellyfin) cache channel logos aggressively. When logos change, the old cached version persists unless the channel is deleted during a guide refresh. This feature solves that by scheduling a complete channel purge before your media server refreshes its guide data.

## TheSportsDB API Key

Optional premium API key for higher rate limits on TheSportsDB.

| Tier | Rate Limit | Result Limits |
|------|------------|---------------|
| **Free** | 30 requests/min | Lower |
| **Premium** ($9/mo) | 100 requests/min | Higher |

The free tier works for most users. Get a premium key at [thesportsdb.com/pricing](https://www.thesportsdb.com/pricing).

## XMLTV Generator Metadata

Customize the generator information included in the XMLTV output file.

| Field | Default |
|-------|---------|
| **Generator Name** | Teamarr |
| **Generator URL** | https://github.com/Pharaoh-Labs/teamarr |

These values appear in the XMLTV file header and are used by some media servers to identify the EPG source.
