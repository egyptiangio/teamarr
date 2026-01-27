---
title: General
parent: Settings
grand_parent: User Guide
nav_order: 1
---

# General Settings

Configure timezone, time format, and display preferences.

## Timezones

Teamarr uses two timezone settings:

### UI Display Timezone

The timezone used for displaying times in the web interface. This is set via the `TZ` environment variable and cannot be changed in the UI.

```yaml
# docker-compose.yml example
environment:
  - TZ=America/New_York
```

### EPG Output Timezone

The timezone used for EPG output and template variables like `{game_time}`. This can be configured in the UI.

{: .note }
If both timezones are configured differently, the UI will show an info box explaining which timezone is used where.

## Time Format

Choose between 12-hour (3:45 PM) or 24-hour (15:45) time format. This applies to both the UI and EPG output.

## Show Timezone Abbreviation

Toggle whether to display timezone abbreviations (EST, PST, etc.) alongside times.
