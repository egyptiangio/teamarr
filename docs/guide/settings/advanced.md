---
title: Advanced
parent: Settings
grand_parent: User Guide
nav_order: 7
---

# Advanced Settings

Local caching, API keys, XMLTV metadata, backup/restore, and update notifications.

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
| **Auto-detect branch from version** | Automatically determine which branch to check based on your version string |

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
