---
title: EPG Generation
parent: Settings
grand_parent: User Guide
nav_order: 5
---

# EPG Generation Settings

Configure EPG output, scheduling, and default game durations.

## Output Settings

### Output Path

Where to write the generated XMLTV file. Default: `./data/teamarr.xml`

### Output Days Ahead

How many days of EPG data to include in the output. Default: 14 days.

### EPG Start (Hours Ago)

Include events that started up to this many hours ago. Useful for catching games still in progress. Default: 6 hours.

### Include Final Events

Toggle whether to include completed/final events in the EPG output.

## Scheduled Generation

Enable automatic EPG generation on a schedule.

### Cron Expression

Standard cron format for scheduling. Common presets are available:

| Preset | Expression | Description |
|--------|------------|-------------|
| Every Hour | `0 * * * *` | Run at the top of every hour |
| Every 2 Hours | `0 */2 * * *` | Run every 2 hours |
| Every 4 Hours | `0 */4 * * *` | Run every 4 hours |
| Every 6 Hours | `0 */6 * * *` | Run every 6 hours |
| Daily at Midnight | `0 0 * * *` | Run once daily at midnight |
| Daily at 6 AM | `0 6 * * *` | Run once daily at 6 AM |

### Run Now

Manually trigger an EPG generation run.

## Default Durations

Set default event durations (in hours) for each sport. These are used when the actual event duration is unknown.

| Sport | Default |
|-------|---------|
| Basketball | 3.0 |
| Football | 3.5 |
| Hockey | 3.0 |
| Baseball | 3.5 |
| Soccer | 2.5 |
| MMA | 5.0 |
| Boxing | 4.0 |
| Tennis | 3.0 |
| Golf | 6.0 |
| Racing | 3.0 |
| Cricket | 4.0 |
