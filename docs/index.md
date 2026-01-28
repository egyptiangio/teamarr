---
title: Home
layout: home
nav_order: 1
---

<img src="assets/images/logo.svg" alt="Teamarr" width="80" height="80">

# Teamarr

Sports EPG Generator for Dispatcharr.

## What is Teamarr?

Teamarr is an add-on for [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) that generates enriched EPG for sports channels. While EPG generation can technically work standalone, Teamarr is designed for use alongside Dispatcharr.

Teamarr pulls rich sports data from providers (ESPN, TSDB, HockeyTech, etc.) - schedules, venues, records, scores, standings, broadcasts, and more - and uses it to manage your IPTV sports channels in Dispatcharr.

**Two workflows:**

- **Event-based** - Many IPTV providers offer sports through ephemeral streams created to serve a single game. These appear around game time (often the morning of, or a day before) and disappear after the event concludes (typically end of day or the following morning). Streams are typically organized into groups - either by league (NFL, NBA, NHL) or by source (ESPN+, DAZN, TSN+, FloSports). A group may contain events from a single sport or league, or intermixed events from multiple leagues and sports. As long as stream names contain enough information to match to real-world events (e.g., "NFL: Bills vs Dolphins" or "DAZN: Man City vs Arsenal"), these IPTV groups can be used as source groups in Teamarr.

- **Team-based** - Some IPTV providers offer persistent channels dedicated to a single team (e.g., "New York Yankees", "LA Lakers"). These channels exist continuously but only have programming when that team plays. Teamarr looks up the team's schedule and populates the guide with their upcoming games.

**Example:**

Your IPTV stream says:
```
NFL: KC vs PHI
```

Teamarr matches it to real data and generates:
```
Channel: Chiefs vs Eagles - 6:30 PM ET
EPG:     Kansas City Chiefs @ Philadelphia Eagles
         Lincoln Financial Field, Philadelphia, PA
         Chiefs (11-1) vs Eagles (10-2)
         Broadcast: NBC, Peacock
```

**What Teamarr doesn't do:**

- **Linear/traditional TV channels** - Teamarr does not support 24/7 channels like TSN, ESPN, or Sportsnet where game info lives in EPG metadata, not the stream name. Matching them would require parsing external EPG sources and correlating schedules - a fundamentally different architecture.

- **Create team-based channels** - Team channels are static and already exist in your IPTV provider. Teamarr only generates EPG for them.

- **Match incomplete stream names** - If your IPTV provider doesn't include enough information in the stream name to identify the event (e.g., just "NBA 1" with no teams listed), Teamarr cannot match it.

## Features

- **280+ leagues, 10+ sports** - Football, basketball, hockey, baseball, soccer (200+ leagues), cricket, lacrosse, MMA, rugby, and more
- **160+ template variables** - Customize channel names and EPG with team records, scores, venues, broadcasts, standings, playoff status, and more
- **Flexible matching** - Aliases, fuzzy matching, and configurable stream ordering to handle inconsistent IPTV naming
- **Channel groups & profiles** - Use existing Dispatcharr groups/profiles or create them dynamically using variables and wildcards
- **Smart sorting** - Configurable stream and channel sorting modes based on priority rules
- **Scheduled automation** - Cron-based EPG generation and channel lifecycle management

## Quick Links

- [User Guide](guide/) - Get started with Teamarr
- [Technical Reference](reference/) - Architecture and API documentation
