# Teamarr - Dynamic Sports Team EPG Generator

**Transform ESPN's sports schedules into professional XMLTV EPG files for your IPTV system.**

Self-hosted dynamic sports EPG generator with support for 15 major leagues (NBA, NFL, MLB, NHL, MLS, NWSL, EPL, EFL Championship, La Liga, Bundesliga, Serie A, Ligue 1, NCAA Football, NCAA Men's/Women's Basketball) and 188+ template variables.

---

## Quick Start with Docker

```bash
docker run -d \
  --name teamarr \
  -p 9195:9195 \
  -v ./data:/app/data \
  -e TZ=America/New_York \
  ghcr.io/egyptiangio/teamarr:latest
```

Access the web interface at **http://localhost:9195**

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

---

## Features

- **15 Major Leagues**: NBA, NFL, MLB, NHL, MLS, NWSL, EPL, EFL Championship, La Liga, Bundesliga, Serie A, Ligue 1, NCAA Football, NCAA Men's/Women's Basketball
- **150+ Template Variables**: Team records, win streaks, head-to-head stats, player performance, and more
- **Conditional Descriptions**: Dynamic content based on team performance, rankings, and rivalries
- **Gracenote-Standard EPG**: Professional quality XMLTV following industry best practices
- **Web-Based Configuration**: Simple, intuitive interface
- **Automatic Updates**: Daily schedule refresh with change detection
- **ESPN API Integration**: Real-time data from ESPN's public API
- **IPTV Compatible**: Works with Plex, Jellyfin, TVHeadend, TiviMate, and more

---

## Usage

1. **Add a team**: Click "Add Team" → paste ESPN team URL or select manually
2. **Customize description**: Use template variables like `{team_name}`, `{team_record}`, `{win_streak}`
3. **Generate EPG**: Click "Generate EPG" → Download `teamarr.xml`
4. **Import to IPTV**: Add the EPG file to your IPTV app

### Example Description Templates

```
{team_name} ({team_record}) vs {opponent} ({opponent_record}) at {venue}
```

```
{team_name} looks to extend their {win_streak} game winning streak against {opponent}
```

```
Can the {team_name} snap their {loss_streak} game losing streak against {opponent}?
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `9195` | Web interface port |
| `TZ` | `America/New_York` | Timezone for schedules |
| `DEFAULT_DAYS_AHEAD` | `7` | Days of schedule to generate |
| `MAX_DAYS_AHEAD` | `14` | Maximum days ahead allowed |

---

## Volumes

| Path | Description |
|------|-------------|
| `/app/data` | SQLite database, configuration, and generated XMLTV files |

---

## Ports

| Port | Description |
|------|-------------|
| `9195` | Web interface |

---

## Template Variables

### Most Popular

| Variable | Example Output |
|----------|----------------|
| `{team_name}` | Detroit Pistons |
| `{team_record}` | 12-2 |
| `{opponent}` | Atlanta Hawks |
| `{win_streak}` | 5 |
| `{loss_streak}` | 3 |
| `{venue}` | State Farm Arena |
| `{game_time}` | 7:30 PM EST |
| `{season_series}` | 2-1 |
| `{rank}` | #8 |
| `{top_scorer_name}` | Cade Cunningham |

**[View all 188 variables in the docs](https://github.com/egyptiangio/teamarr)**

---

## Conditional Descriptions

Create dynamic descriptions based on team performance:

- **Win Streak (3+ games)**: "Team looks to extend their X game winning streak"
- **Loss Streak (3+ games)**: "Team looks to snap their X game losing streak"
- **Top 10 Matchup**: "Battle of top-10 teams at the arena"
- **Rivalry Game**: "Classic rivalry renewed"
- **Division Game**: "Critical division matchup"
- **Home/Away**: Different descriptions for home vs away games

---

## XMLTV Output

```xml
<programme start="20251119003000 +0000" stop="20251119033000 +0000" channel="pistons.nba">
  <title lang="en">Detroit Pistons Basketball</title>
  <sub-title lang="en">State Farm Arena, Atlanta</sub-title>
  <desc lang="en">Detroit Pistons (12-2) look to extend their 5 game winning streak against Atlanta Hawks (8-6).</desc>
  <category lang="en">Sports</category>
  <category lang="en">Basketball</category>
  <new/>
</programme>
```

---

## Support

- **Issues**: [GitHub Issues](https://github.com/egyptiangio/teamarr/issues)
- **Discussions**: [GitHub Discussions](https://github.com/egyptiangio/teamarr/discussions)

---

## License

MIT License - see [LICENSE](LICENSE) file for details

---

**Web Interface**: http://localhost:9195
**Docker Image**: `ghcr.io/egyptiangio/teamarr:latest`
