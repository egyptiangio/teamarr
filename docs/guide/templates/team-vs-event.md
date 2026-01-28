---
title: Team vs Event
parent: Templates
grand_parent: User Guide
nav_order: 1
---

# Team vs Event Templates

Teamarr supports two template types designed for different EPG workflows. Understanding the difference is essential for setting up your system correctly.

## Team Templates

Team templates are for **persistent channels dedicated to a specific team**.

### How They Work

- Each team gets its own channel that exists 24/7
- The template is assigned to a specific team, so Teamarr knows the "viewpoint"
- Content is written from that team's perspective using variables like `{team_name}` and `{opponent}`
- Whether the team is home or away, `{team_name}` is always your team and `{opponent}` is always the other team
- Filler content (pregame, postgame, idle) keeps the channel populated even when no game is live

### Use Cases

- "Detroit Lions" channel showing all Lions games
- "LA Lakers" channel with Lakers schedule
- Regional sports network style - one channel per team

### Variable Context

Team templates have access to three game contexts:

| Context | Suffix | Example | Use Case |
|---------|--------|---------|----------|
| Current | (none) | `{opponent}` | During a game or when one game today |
| Next | `.next` | `{opponent.next}` | Pregame content, idle content |
| Last | `.last` | `{opponent.last}` | Postgame content, recaps |

This allows rich content like:
- Pregame: "Next up: {team_name} vs {opponent.next} at {game_time.next}"
- Postgame: "{team_name} {result_text.last} the {opponent.last} {final_score.last}"
- Idle: "No game today. Next: {game_date.next} vs {opponent.next}"

### Assigned To

Team templates are assigned to **Teams** in the Teams page.

One template can be shared across multiple teams - useful when you want consistent formatting across a league or even multiple leagues and sports.

---

## Event Templates

Event templates are for **dynamic channels created per-game**.

### How They Work

- Channels are created when a matching stream appears
- Each channel represents one specific game
- There's no "team viewpoint" - the channel isn't associated with a specific team
- Content uses positional variables: `{home_team}` and `{away_team}` based on who's hosting
- `{home_team}` is always the team playing at their venue, `{away_team}` is always the visiting team
- Channels are deleted after the game ends (configurable)

### Use Cases

- IPTV providers with game-specific streams ("NFL: Bills vs Dolphins")
- Sports packages where streams appear/disappear based on live games
- Event groups from Dispatcharr M3U accounts

### Variable Context

Event templates reference only the current event - there's no "next" or "last" game because the channel only exists for one game.

| Context | Suffix | Example |
|---------|--------|---------|
| Current | (none) | `{home_team}`, `{away_team}`, `{venue}` |

Variables use positional naming:
- `{home_team}` - the home team
- `{away_team}` - the away team
- `{home_team_record}` - home team's record
- `{away_team_record}` - away team's record

### Assigned To

Event templates are assigned to **Event Groups** in the Event Groups page.

Each event group can have one template that applies to all matched events in that group.

---

## Comparison

| Feature | Team Templates | Event Templates |
|---------|---------------|-----------------|
| Channel lifetime | Persistent (24/7) | Temporary (per game) |
| Perspective | Team-specific ("our team") | Positional (home/away) |
| Suffix support | `.next`, `.last` | None needed |
| Idle content | Yes | No |
| Assigned to | Teams | Event Groups |
| Variables | `{team_name}`, `{opponent}` | `{home_team}`, `{away_team}` |

## Choosing the Right Type

**Use Team Templates when:**
- You want dedicated channels for specific teams
- You need 24/7 channel presence with filler content
- You're building a team-centric viewing experience

**Use Event Templates when:**
- Your IPTV provider has game-specific streams
- Channels should appear/disappear with live events
- You're matching streams from event groups

Many setups use both - team templates for favorite teams and event templates for catching other games from IPTV streams.
