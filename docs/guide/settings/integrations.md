---
title: Integrations
parent: Settings
grand_parent: User Guide
nav_order: 6
---

# Integration Settings

Configure connections to external services.

## Dispatcharr Integration

Connect Teamarr to Dispatcharr for automatic channel management.

### Connection Settings

| Field | Description |
|-------|-------------|
| **Enable** | Toggle Dispatcharr integration on/off |
| **URL** | Dispatcharr server URL (e.g., `http://localhost:5000`) |
| **Username** | Dispatcharr login username |
| **Password** | Dispatcharr login password |

Use the **Test** button to verify your connection.

### EPG Source

Select which EPG source in Dispatcharr to associate with Teamarr-managed channels.

### Default Channel Profiles

Select which channel profiles to assign to Teamarr-managed channels by default. Individual event groups can override this setting.

- **All profiles selected** - Channels appear in all profiles
- **None selected** - Channels don't appear in any profile
- **Specific profiles** - Channels appear only in selected profiles

{: .note }
Profile assignment is enforced on every EPG generation run.

See [Dispatcharr Integration](../dispatcharr-integration) for setup details.

## Local Caching

Teamarr caches team and league data from ESPN and TheSportsDB to improve performance.

### Cache Status

View the current cache state:
- Number of leagues and teams cached
- Last refresh time and duration
- Stale indicator if cache needs refresh

### Refresh Cache

Manually refresh the cache to pull the latest team and league data.

## TheSportsDB API Key

Optional premium API key for higher rate limits. The free tier works for most users.

Get a premium key at [thesportsdb.com/pricing](https://www.thesportsdb.com/pricing).
