---
title: Advanced
parent: Settings
grand_parent: User Guide
nav_order: 7
---

# Advanced Settings

XMLTV metadata, backup/restore, and update notifications.

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

## Game Thumbs

[Game Thumbs](https://github.com/sethwv/game-thumbs) is an optional external service by [@sethwv](https://github.com/sethwv) that generates dynamic program artwork for sports events. Teamarr templates can use game-thumbs URLs in artwork fields to display matchup images with team logos.

### Resources

- **Documentation**: [game-thumbs-docs.swvn.io](https://game-thumbs-docs.swvn.io)
- **GitHub**: [github.com/sethwv/game-thumbs](https://github.com/sethwv/game-thumbs)

### Hosted Instances

| URL | User |
|-----|------|
| `https://game-thumbs.swvn.io` | @sethwv |
| `https://sportslogos.jesmann.com` | @jesmannstlPanda |

{: .important }
Hosted instances are community-provided and may have usage limits.

### Self-Hosting

See the [GitHub repository](https://github.com/sethwv/game-thumbs) for self-hosting instructions.
