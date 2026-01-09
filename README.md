# Teamarr V2

**Dynamic EPG Generator for Sports Channels**

Teamarr generates rich XMLTV Electronic Program Guide data for your sports channels. It fetches schedules from multiple data providers (ESPN, TheSportsDB) and generates EPG files with intelligent template-based descriptions.

> **Status**: V2 Alpha - Core functionality working, feedback welcome!

## Quick Start (Docker)

```yaml
# docker-compose.yml
services:
  teamarr:
    image: ghcr.io/egyptiangio/teamarr:v2-alpha
    container_name: teamarr
    restart: unless-stopped
    ports:
      - 9195:9195
    volumes:
      - ./data:/app/data
    environment:
      - TZ=America/Detroit
```

```bash
docker compose up -d
open http://localhost:9195
```

## Features

- **Multi-Provider Support** - ESPN (primary) + TheSportsDB (fallback)
- **Team-Based EPG** - Dedicated channel per team with pregame/postgame filler
- **Event-Based EPG** - Dynamic channels per event with stream matching
- **141 Template Variables** - Rich descriptions with game context
- **Conditional Templates** - Different descriptions for home/away, day/night, etc.
- **Dispatcharr Integration** - Automatic channel lifecycle management
- **Stream Matching** - Fuzzy matching with fingerprint cache
- **React UI** - Modern web interface for configuration

## What's New in V2

- Complete rewrite with clean architecture
- FastAPI backend (was Flask)
- React + TypeScript frontend
- Multi-stage Docker build
- Improved template engine with 141 variables
- Better stream matching algorithms
- Processing statistics and history

## Supported Leagues

**ESPN**: NFL, NBA, NHL, MLB, MLS, NCAAF, NCAAM, NCAAW, WNBA, UFC, Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Champions League, 200+ soccer leagues

**TheSportsDB**: OHL, WHL, QMJHL, NLL, PLL, IPL, BBL, CPL, T20 Blast, Boxing

## Configuration

1. Open http://localhost:9195
2. Go to **Settings** and configure Dispatcharr connection
3. Import teams or create event groups
4. Create templates for EPG formatting
5. Generate EPG manually or let the scheduler run

## Data Persistence

All data is stored in `/app/data`:
- `teamarr.db` - SQLite database
- `logs/` - Application logs

Mount this directory to persist data across container restarts.

## API Documentation

Interactive API docs available at http://localhost:9195/docs

Key endpoints:
| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/v1/teams` | Team channels |
| `GET /api/v1/groups` | Event EPG groups |
| `POST /api/v1/epg/generate` | Generate EPG |
| `GET /api/v1/stats` | Processing statistics |

## Development

```bash
# Clone and setup
git clone https://github.com/egyptiangio/teamarr.git
cd teamarr
git checkout v2-alpha

# Python backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
python app.py

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

## Reporting Issues

Use the V2-specific issue templates on GitHub - they auto-label with `v2` for easy filtering.

- [V2 Bug Report](https://github.com/egyptiangio/teamarr/issues/new?template=v2_bug_report.yml)
- [V2 Feature Request](https://github.com/egyptiangio/teamarr/issues/new?template=v2_feature_request.yml)

## License

MIT
