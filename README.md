<p align="center">
  <img src="static/logo.svg" alt="Teamarr" width="120">
</p>

<h1 align="center">Teamarr</h1>

<p align="center"><strong>Dynamic EPG Generator for Sports Channels</strong></p>

Teamarr creates rich, dynamic Electronic Program Guide (EPG) data for your sports channels. It fetches real-time schedules from ESPN and generates XMLTV-format EPG files with intelligent descriptions that adapt based on game context—streaks, odds, matchups, and more.

---

## Features

- **Team-Based EPG** — Add your favorite teams and get 24/7 EPG coverage with pregame, live game, postgame, and idle programming
- **Event-Based EPG** — Automatically match streams from your M3U provider to ESPN events and create channels on-the-fly
- **Smart Descriptions** — Conditional templates that change based on win streaks, betting odds, home/away status, and more
- **Dispatcharr Integration** — Seamless channel lifecycle management with automatic channel creation and deletion
- **Multi-Sport Support** — NFL, NBA, NHL, MLB, MLS, college sports, Premier League, La Liga, and more

---

## Quick Start

### Docker Compose

```yaml
services:
  teamarr:
    image: ghcr.io/egyptiangio/teamarr:latest
    container_name: teamarr
    restart: unless-stopped
    ports:
      - 9195:9195
    volumes:
      - ./data:/app/data
    environment:
      - TZ=America/New_York
```

```bash
docker-compose up -d
```

Open **http://localhost:9195** in your browser.

### Image Tags

| Tag | Description |
|-----|-------------|
| `latest` | Stable release, recommended for most users |
| `dev` | Development branch with newest features, may have bugs |

---

## Dispatcharr Integration

Teamarr works best with [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) for automatic channel management. Here's how to connect them:

### Step 1: Add Teamarr EPG to Dispatcharr

1. In **Dispatcharr**, go to **Settings → EPG Sources**
2. Click **Add EPG Source**
3. Enter the Teamarr EPG URL:
   ```
   http://teamarr:9195/epg/teamarr.xml
   ```
   *(Use your Teamarr container name or IP address)*
4. Save and refresh the EPG

### Step 2: Connect Teamarr to Dispatcharr

1. In **Teamarr**, go to **Settings**
2. Under **Dispatcharr Integration**, enter:
   - **URL**: `http://dispatcharr:9191` (your Dispatcharr address)
   - **Username**: Your Dispatcharr username
   - **Password**: Your Dispatcharr password
3. Click **Test Connection**
4. Select the **Teamarr EPG** you created in Step 1 from the dropdown
5. Save settings

### Important Dispatcharr Settings

For event-based EPG (automatic channel creation), configure these in Dispatcharr:

| Setting | Recommended Value | Why |
|---------|-------------------|-----|
| **Stale Stream Retention** | `0` | Prevents old/dead streams from being mapped to new channels |

**Note**: You don't need to worry about EPG refresh intervals—Teamarr automatically triggers a Dispatcharr EPG refresh each time it generates.

---

## Team-Based EPG

Team-based EPG creates a dedicated channel for each team you follow. The channel shows relevant programming 24/7:

- **Pregame** — Builds anticipation before games
- **Live Game** — The actual event with real-time data
- **Postgame** — Recaps and results after games end
- **Idle** — Between games, showing next game info

### Adding Teams

1. Go to **Teams → Add Team**
2. Search by team name or paste an ESPN team URL
3. Assign a **Template** (controls how descriptions are formatted)
4. Save

### Smart Conditional Descriptions

This is where Teamarr shines. Instead of repetitive descriptions, you can create conditions that produce different text based on the game situation.

#### How It Works

Every description has a **priority score** (1-100). Lower numbers = higher priority. Teamarr checks conditions from lowest to highest score and uses the first one that matches.

**Default descriptions always have priority 100** and cannot be changed. You can add multiple defaults—Teamarr will randomly pick one when no conditions match.

#### Example Setup

| Type | Condition | Description Template | Priority |
|------|-----------|---------------------|----------|
| Conditional | Win streak ≥ 3 | `{team_name} looks to extend their {win_streak}-game winning streak against {opponent}!` | 10 |
| Conditional | Has odds | `{team_name} vs {opponent} • {odds_spread} • O/U {odds_over_under}` | 20 |
| Default | — | `{team_name} plays {opponent} at {game_time} on {game_date}` | 100 |
| Default | — | `{away_team} travels to {venue_city} to face {home_team}` | 100 |

#### How Teamarr Evaluates

1. **Check priority 10**: Is win streak ≥ 3?
   - ✅ Yes → Use this description
   - ❌ No → Continue...

2. **Check priority 20**: Are betting odds available?
   - ✅ Yes → Use this description
   - ❌ No → Continue...

3. **Check priority 100**: Multiple defaults exist
   - Pick one at random for variety

The result: descriptions that feel fresh and contextual, not copy-paste repetitive.

#### Creating Conditionals

1. Edit your Template
2. Go to the **Conditions** tab
3. Click **Add Condition**
4. Configure:
   - **Name**: Label for your reference
   - **Condition Type**: What to check (streak, odds, home/away, etc.)
   - **Value**: Threshold (e.g., 3 for "streak ≥ 3")
   - **Priority**: Lower = checked first (1-99)
   - **Description Template**: Text with variables

#### Available Conditions

| Condition | Description |
|-----------|-------------|
| Is home | Team is playing at home |
| Is away | Team is playing away |
| Win streak ≥ N | Team has won N+ games in a row |
| Loss streak ≥ N | Team has lost N+ games in a row |
| Home win streak ≥ N | Team has won N+ home games in a row |
| Away win streak ≥ N | Team has won N+ away games in a row |
| Has odds | Betting odds are available for this game |
| Is playoff | Game is a playoff game |
| Is preseason | Game is a preseason game |
| Is national broadcast | Game is on national TV |
| Is ranked opponent | Opponent is ranked (college) |
| Opponent contains | Opponent name contains specific text |

---

## Event-Based EPG

Event-based EPG automatically creates channels from your M3U provider streams. Perfect for providers with game-day channels like "NFL: Bears @ Lions".

### How It Works

1. Teamarr scans your M3U channel groups
2. Parses stream names to identify teams (e.g., "Bears @ Lions")
3. Matches teams to ESPN events
4. Creates channels in Dispatcharr with full EPG data
5. Deletes channels when games end

### Setting Up Event Groups

1. Go to **Event EPG → Add Group**
2. Select your **M3U Account** and **Channel Group**
3. Choose the **Sport** and **League**
4. Configure channel settings:
   - **Starting Channel Number**: Where to begin numbering
   - **Channel Group**: Dispatcharr group to create channels in
5. Assign an **Event Template** for descriptions
6. Save and click **Refresh** to test matching

### Channel Lifecycle Settings

| Setting | Options | Description |
|---------|---------|-------------|
| **Create Timing** | Stream Available, Same Day, Day Before, 2 Days Before | When to create the channel |
| **Delete Timing** | Stream Removed, Same Day, Day After, 2 Days After | When to delete the channel |

**Tip**: "Same Day" for both is recommended—channels appear on game day and disappear at midnight.

---

## Settings Reference

### EPG Generation

| Setting | Default | Description |
|---------|---------|-------------|
| **Days Ahead** | 3 | How many days of schedule to include |
| **Output Path** | `/app/data/teamarr.xml` | Where to save the EPG file |
| **Auto Generate** | Enabled | Automatically regenerate EPG on schedule |
| **Frequency** | Hourly | How often to regenerate (Hourly or Daily) |
| **Schedule Time** | :00 | For hourly: minute of hour (0-59). For daily: time (HH:MM) |

### Time & Display

| Setting | Default | Description |
|---------|---------|-------------|
| **Timezone** | America/New_York | Timezone for all times (syncs from TZ env var) |
| **Time Format** | 12h | 12-hour or 24-hour time display |
| **Show Timezone** | Yes | Include timezone abbreviation (EST, PST) |

### Game Durations

How long to block out for each sport's games:

| Sport | Default Duration |
|-------|------------------|
| Basketball | 3.0 hours |
| Football | 3.5 hours |
| Hockey | 3.0 hours |
| Baseball | 3.5 hours |
| Soccer | 2.5 hours |
| Other | 4.0 hours |

### Channel ID Format

Default: `{team_name_pascal}.{league_id}`

Available variables:
- `{team_name}` — Full team name
- `{team_name_pascal}` — PascalCase team name
- `{team_abbrev}` — Team abbreviation
- `{team_slug}` — URL-friendly team name
- `{league}` — League code
- `{league_id}` — League identifier
- `{sport}` — Sport type

### Advanced

| Setting | Default | Description |
|---------|---------|-------------|
| **Midnight Crossover** | Idle | What to show if game crosses midnight: "Postgame" or "Idle" |
| **Max Program Hours** | 6.0 | Maximum length for a single filler program block |
| **Include Final Events** | No | Show completed games from today in event-based EPG |

---

## Template Variables

Use these in your description templates:

### Game Info
| Variable | Example |
|----------|---------|
| `{team_name}` | Detroit Pistons |
| `{opponent}` | Los Angeles Lakers |
| `{home_team}` | Detroit Pistons |
| `{away_team}` | Los Angeles Lakers |
| `{game_time}` | 7:00 PM EST |
| `{game_date}` | Dec 15 |
| `{game_day}` | Sunday |

### Venue
| Variable | Example |
|----------|---------|
| `{venue}` | Little Caesars Arena |
| `{venue_city}` | Detroit |
| `{venue_state}` | MI |
| `{venue_full}` | Little Caesars Arena, Detroit, MI |

### Records & Stats
| Variable | Example |
|----------|---------|
| `{team_record}` | 15-8 |
| `{opponent_record}` | 12-11 |
| `{win_streak}` | 4 |
| `{loss_streak}` | 0 |
| `{home_record}` | 8-2 |
| `{away_record}` | 7-6 |

### Betting (when available)
| Variable | Example |
|----------|---------|
| `{odds_spread}` | -3.5 |
| `{odds_over_under}` | 221.5 |
| `{odds_moneyline}` | -150 |

### Context Suffixes

Many variables support `.next` and `.last` suffixes:
- `{opponent.next}` — Next game's opponent
- `{game_date.last}` — Last game's date
- `{final_score.last}` — Last game's final score

---

## Upgrading

Database migrations run automatically when you update. Your teams, templates, and settings are preserved.

```bash
docker-compose pull
docker-compose up -d
```

---

## Homepage Integration

Add Teamarr stats to your [Homepage](https://gethomepage.dev/) dashboard using the custom API widget:

```yaml
- Teamarr:
    icon: /icons/teamarr.svg  # or use a URL to the logo
    href: http://your-teamarr-ip:9195
    description: EPG Stats
    widget:
      type: customapi
      url: http://your-teamarr-ip:9195/api/epg-stats
      mappings:
        - label: EVENT
          field: stats.channels.event_based
        - label: TEAM
          field: stats.channels.team_based
        - label: PROGRAMS
          field: stats.total_programmes
```

This displays your event-based channel count, team-based channel count, and total EPG programmes.

---

## Support

- **Issues**: [GitHub Issues](https://github.com/egyptiangio/teamarr/issues)
- **Discussions**: [GitHub Discussions](https://github.com/egyptiangio/teamarr/discussions)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
