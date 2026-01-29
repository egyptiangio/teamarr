---
title: Dispatcharr Integration
parent: Settings
grand_parent: User Guide
nav_order: 6
---

# Dispatcharr Integration

Configure connection to Dispatcharr for automatic channel management.

## Connection Settings

Server URL and credentials for connecting to Dispatcharr.

| Field | Description |
|-------|-------------|
| **Enable** | Toggle Dispatcharr integration on/off |
| **URL** | Dispatcharr server URL (e.g., `http://localhost:9191`) |
| **Username** | Dispatcharr login username |
| **Password** | Dispatcharr login password |

Use the **Test** button to verify your connection.

### Connection Status

A status badge shows the current connection state:

| Status | Description |
|--------|-------------|
| **Connected** | Successfully communicating with Dispatcharr |
| **Disconnected** | Configured but unable to connect |
| **Error** | Connection failed (hover for error details) |
| **Not Configured** | Integration not yet set up |

## EPG Source

Select which EPG source in Dispatcharr to associate with Teamarr-managed channels.

This links your Teamarr channels to a specific EPG source in Dispatcharr, ensuring the correct guide data is displayed.

## Default Channel Profiles

Select which channel profiles to assign to Teamarr-managed channels by default. Individual [event groups can override](../event-groups/creating-groups#channel-profiles) this setting.

- **All profiles selected** - Channels appear in all profiles
- **None selected** - Channels don't appear in any profile
- **Specific profiles** - Channels appear only in selected profiles

### Dynamic Wildcards

In addition to selecting specific profiles, you can use wildcards that dynamically create and assign profiles based on the event:

| Wildcard | Description | Example |
|----------|-------------|---------|
| `{sport}` | Creates/assigns profile named after the sport | `football`, `basketball` |
| `{league}` | Creates/assigns profile named after the league | `nfl`, `nba`, `epl` |

For example, selecting profiles `[1, {sport}]` would assign all channels to profile 1, plus dynamically create and assign to a sport-specific profile.

{: .note }
Profile assignment is enforced on every EPG generation run. Wildcard profiles are created in Dispatcharr automatically if they don't exist.

## Logo Cleanup

When enabled, removes **all** unused logos from Dispatcharr after EPG generation.

{: .warning }
This affects all unused logos in Dispatcharr, not just ones uploaded by Teamarr. Use with caution if you have manually uploaded logos that are not actively assigned to channels.

See [Dispatcharr Integration Guide](../dispatcharr-integration) for complete setup details.
