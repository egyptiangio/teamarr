# Teamarr - Sports EPG Generator

**Transform ESPN's sports schedules into professional XMLTV EPG files for your IPTV system.**

Teamarr is a self-hosted web application that generates customizable electronic program guides (EPG) for sports teams across 13 major leagues, with support for 188+ template variables to create rich, contextual descriptions.

---

## ✨ Features

- **13 Major Leagues**: NBA, NFL, MLB, NHL, MLS, EPL, La Liga, Bundesliga, Serie A, Ligue 1, NCAA Football, NCAA Men's/Women's Basketball
- **188+ Template Variables**: Team records, win streaks, head-to-head stats, player performance, and more
- **Gracenote-Standard EPG**: Professional quality XMLTV following industry best practices
- **Web-Based Configuration**: Simple, intuitive interface on port 9195
- **Automatic Updates**: Daily schedule refresh with change detection
- **ESPN API Integration**: Real-time data from ESPN's public API
- **IPTV Compatible**: Works with Plex, Jellyfin, TVHeadend, TiviMate, and more

---

## 🚀 Quick Start

### 1. Clone and Setup

```bash
cd /mnt/nvme/scratch/teamarr
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
python3 -c "from database import init_database; init_database()"
```

### 3. Start the Application

```bash
python3 app.py
```

The web interface will be available at **http://localhost:9195**

---

## 📖 Usage Guide

### Adding Your First Team

1. Navigate to **http://localhost:9195**
2. Click **"Add Team"**
3. **Option A - Quick Add**: Paste an ESPN team URL (e.g., `https://www.espn.com/nba/team/_/name/det/detroit-pistons`)
4. **Option B - Manual Entry**: Select league, enter team info

### Customizing Descriptions

Use the **Template Variable Helper** to create rich descriptions:

```
{team_name} ({team_record}) looks to extend their {win_streak} game winning streak against {opponent} ({opponent_record}) at {venue}.
```

**Result:**
```
Detroit Pistons (12-2) look to extend their 5 game winning streak against Atlanta Hawks (8-6) at State Farm Arena.
```

### Generating EPG

1. Click **"Generate EPG"** on the dashboard
2. Wait for generation to complete (~2-5 seconds)
3. Click **"Download EPG"** to get the XMLTV file

### Importing to IPTV App

**For Plex:**
1. Settings → Live TV & DVR → DVR Settings
2. EPG Data Source → XMLTV
3. Upload `teamarr.xml`

**For Jellyfin:**
1. Dashboard → Live TV → EPG
2. Add XMLTV EPG → Upload file

**For TiviMate:**
1. Settings → EPG → Add EPG
2. Select local file → Choose `teamarr.xml`

---

## 🎯 Template Variables

### Most Popular Variables

| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{team_name}` | Full team name | Detroit Pistons |
| `{team_record}` | Current record | 12-2 |
| `{opponent}` | Opponent name | Atlanta Hawks |
| `{win_streak}` | Consecutive wins | 5 |
| `{venue_full}` | Venue with city | State Farm Arena, Atlanta |
| `{game_time}` | Game time | 7:30 PM EST |
| `{season_series}` | Season matchup record | 2-1 |
| `{previous_score}` | Last matchup score | 110-105 |
| `{last_loss_opponent}` | Last loss opponent | Boston Celtics |
| `{top_scorer_name}` | Leading scorer | Cade Cunningham |

**[View all 188 variables](./TEMPLATE_VARIABLES_COMPLETE.md)**

---

## 📁 Project Structure

```
teamarr/
├── app.py                      # Flask application entry point
├── requirements.txt            # Python dependencies
├── teamarr.db                  # SQLite database
│
├── api/
│   ├── __init__.py
│   └── espn_client.py         # ESPN API wrapper
│
├── database/
│   ├── __init__.py
│   └── schema.sql             # Database schema
│
├── epg/
│   ├── __init__.py
│   ├── xmltv_generator.py     # XMLTV file generator
│   └── template_engine.py     # Variable resolution engine
│
├── templates/                  # HTML templates
│   ├── base.html
│   ├── index.html             # Dashboard
│   ├── teams.html             # Team list
│   ├── team_form.html         # Add/edit team
│   └── settings.html          # App settings
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
│
└── output/
    └── teamarr.xml            # Generated EPG file
```

---

## ⚙️ Configuration

### Database Tables

- **teams**: Team configurations and templates
- **settings**: Global app settings
- **league_config**: Pre-configured league data
- **schedule_cache**: Cached ESPN API responses
- **team_stats_cache**: Cached team statistics
- **h2h_cache**: Head-to-head matchup history
- **epg_history**: Generation audit log
- **error_log**: Application error tracking

### Settings

Access via **Settings** page or direct database:

```sql
UPDATE settings SET
    epg_days_ahead = 14,              -- Days of schedule to include
    epg_update_time = '00:00',        -- Daily update time
    cache_enabled = 1,                 -- Enable API caching
    cache_duration_hours = 24,        -- Cache refresh interval
    default_timezone = 'America/New_York'
WHERE id = 1;
```

---

## 🔧 Advanced Usage

### Adding Custom Variables

Edit `epg/template_engine.py`, line ~30 in `_build_variable_dict()`:

```python
variables['my_custom_variable'] = 'custom value'
```

That's it! Restart the app and users can use `{my_custom_variable}` immediately.

### XMLTV Channel ID Format

Channel IDs follow the pattern: `teamname.league`

Examples:
- `pistons.nba`
- `lions.nfl`
- `liverpool.epl`

**For M3U playlists**, use matching `tvg-id`:
```m3u
#EXTINF:-1 tvg-id="pistons.nba" tvg-logo="...",Detroit Pistons
http://your-stream-url
```

### API Endpoints

- `POST /generate` - Generate EPG
- `GET /download` - Download generated EPG
- `POST /api/parse-espn-url` - Parse ESPN team URL

---

## 📊 Example EPG Output

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="Teamarr Sports EPG Generator">

  <channel id="pistons.nba">
    <display-name>Detroit Pistons</display-name>
    <icon src="https://a.espncdn.com/i/teamlogos/nba/500/det.png"/>
  </channel>

  <programme start="20251119003000 +0000"
             stop="20251119033000 +0000"
             channel="pistons.nba">
    <title lang="en">Pistons Basketball</title>
    <sub-title lang="en">DET @ ATL</sub-title>
    <desc lang="en">Detroit Pistons (12-2) look to extend their 5 game winning streak against Atlanta Hawks (8-6) at State Farm Arena.</desc>
    <category lang="en">Sports</category>
    <category lang="en">Sports event</category>
    <category lang="en">Basketball</category>
    <icon src="https://a.espncdn.com/i/teamlogos/nba/500/det.png"/>
    <new/>
    <live/>
    <video>
      <present>yes</present>
      <colour>yes</colour>
      <quality>HDTV</quality>
    </video>
  </programme>

</tv>
```

---

## 🐛 Troubleshooting

### Flask won't start
```bash
# Check if port 9195 is in use
lsof -i :9195

# Kill existing process
kill $(lsof -t -i:9195)

# Restart
python3 app.py
```

### Database errors
```bash
# Reset database
python3 -c "from database import reset_database; reset_database()"
```

### ESPN API errors
- ESPN's API is unofficial and may change
- Check if team slug is correct (visit ESPN team page)
- Try using numeric team ID instead of slug

### Empty EPG
- Verify teams are marked as "Active"
- Check EPG generation history for errors
- Review error log in database

---

## 📚 Documentation

- **[Database Explanation](./DATABASE_EXPLANATION.md)** - Complete database schema guide
- **[Template Variables](./TEMPLATE_VARIABLES_COMPLETE.md)** - All 188 variables documented
- **[EPG State Messages](./EPG_STATE_MESSAGES.md)** - Pre/post-game configuration
- **[League Configuration](./LEAGUE_CONFIGURATION.md)** - 13 supported leagues
- **[ESPN API Analysis](./ESPN_API_Analysis.md)** - API endpoints and data structure
- **[Gracenote Analysis](./GRACENOTE_EPG_ANALYSIS.md)** - Professional EPG standards

---

## 🤝 Contributing

### Adding a New League

1. Add to `league_config` table:
```sql
INSERT INTO league_config (league_code, league_name, sport, api_path, default_game_duration, default_category, record_format)
VALUES ('laliga2', 'La Liga 2', 'soccer', 'soccer/esp.2', 2.0, 'Soccer', 'wins-losses-ties');
```

2. Restart app. New league appears in dropdown automatically.

### Adding Template Variables

Edit `epg/template_engine.py`:

```python
# Add one line to _build_variable_dict()
variables['new_variable_name'] = context.get('some_data', 'default')
```

---

## ⚠️ Important Notes

### ESPN API
- **Unofficial API**: ESPN's public endpoints are not officially documented
- **No Authentication**: No API key required
- **Rate Limiting**: Be reasonable with requests (caching helps)
- **Stability**: API has been stable for years but could change

### Legal
- Schedule data is factual information (public domain)
- Team logos are for display purposes only
- Not affiliated with ESPN, leagues, or teams
- Personal/non-commercial use recommended

---

## 🎯 Roadmap

- [ ] M3U playlist generation
- [ ] Multi-user support with separate EPG files
- [ ] Email notifications for schedule changes
- [ ] Webhook support for real-time updates
- [ ] Mobile-responsive UI enhancements
- [ ] Docker container for easy deployment
- [ ] REST API for external integrations

---

## 📝 License

This project is provided as-is for personal use. See individual library licenses in `requirements.txt`.

---

## 🙏 Credits

- **ESPN**: Sports data via public API
- **XMLTV**: EPG standard specification
- **Flask**: Web framework
- **SQLite**: Database engine

---

**Built with ❤️ for sports fans and cord-cutters**

**Web Interface**: http://localhost:9195
**Port**: 9195
**Database**: SQLite (`teamarr.db`)
