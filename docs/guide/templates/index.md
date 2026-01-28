---
title: Templates
parent: User Guide
nav_order: 4
has_children: true
---

# Templates

Templates define how your EPG content looks - the titles, descriptions, and artwork for programmes in your guide.

## What Templates Do

When Teamarr generates EPG, it uses templates to create programme entries. Templates contain:

- **Title, subtitle, and description formats** using variables like `{team_name}`, `{opponent}`, `{game_time}`
- **Filler content** for pregame, postgame, and idle periods
- **Conditional logic** to show different descriptions based on game context
- **XMLTV metadata** like categories and flags

A single template can be assigned to multiple teams or event groups.

## Template Types

### Team Templates

For **team-based EPG** where each team has a dedicated channel (e.g., "Detroit Lions", "LA Lakers").

- Channel persists 24/7
- Content shown from the team's perspective ("we play the Bears")
- Includes idle content for days without games
- Supports `.next` and `.last` suffixes to reference upcoming/previous games

### Event Templates

For **event-based EPG** where channels are created dynamically for each game.

- Channels appear around game time and disappear after
- Content is positional ("away team @ home team") rather than team-specific
- No idle content needed (no channel when no game)
- No `.next` or `.last` suffixes needed - each channel references only one event
- Used with event groups that match streams to real events

## Template Form Tabs

The template editor has five tabs:

| Tab | Purpose |
|-----|---------|
| **Basic Info** | Template name and event duration settings |
| **Defaults** | Title, subtitle, description(s), artwork URL, and channel name/logo (event templates) |
| **Conditions** | Rules that show different descriptions based on game context (team templates only) |
| **Fillers** | Pregame, postgame, and idle content with optional conditional logic |
| **Other EPG Options** | XMLTV categories, tags (new/live/date), and video quality |

## Variables

Templates use variables enclosed in curly braces that get replaced with real data:

```
{team_name} vs {opponent} at {venue}
→ "Detroit Lions vs Chicago Bears at Ford Field"
```

**Team templates** support suffixes for multi-game context:
- `{opponent}` - current game's opponent
- `{opponent.next}` - next game's opponent
- `{opponent.last}` - last game's opponent

**Event templates** don't use suffixes - each channel exists for a single event, so there's no "next" or "last" game to reference.

See [Variables](variables) for the complete list of 161 available variables.

## Filler Content

Team templates support filler programmes for non-game periods:

| Filler | When It Shows |
|--------|---------------|
| **Pregame** | Hours before game starts (configurable) |
| **Postgame** | After game ends until midnight or next programme |
| **Idle** | Days with no games scheduled |

Each filler has its own title, subtitle, description, and artwork URL.

## Conditions

Conditions let you show different descriptions based on game context:

- Team on a win streak? Show "🔥 5-game win streak!"
- Playing at home? Show "Home game at {venue}"
- Ranked matchup? Show "Top 25 showdown"

Conditions have priorities - the first matching condition wins.

See [Conditions](conditions) for available condition types.

## Getting Started

1. Go to **Templates** and click **Create Template**
2. Choose **Team** or **Event** type (this cannot be changed later)
3. Fill in the defaults with your preferred formats
4. Optionally configure fillers and conditions
5. Save and assign the template to teams or event groups
