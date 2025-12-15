# Teamarr V2

**Dynamic EPG Generator for Sports Channels**

Teamarr generates rich XMLTV Electronic Program Guide data for your sports channels. It fetches schedules from multiple data providers (ESPN, TheSportsDB) and generates EPG files with intelligent descriptions.

> **Status**: API-only backend - UI in development

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start server
python3 app.py

# API docs
open http://localhost:9195/docs
```

## Features

- **Multi-Provider Support** — ESPN (primary) + TheSportsDB (fallback)
- **Team-Based EPG** — Dedicated channel per team with filler content
- **Event-Based EPG** — Dynamic channels per event, stream matching
- **161 Template Variables** — Conditional descriptions based on game context
- **Dispatcharr Integration** — Automatic channel lifecycle management
- **Stream Matching** — Fuzzy matching with fingerprint cache

## Architecture

```
API Layer (FastAPI)
        ↓
Consumer Layer (EPG generators, stream matchers)
        ↓
Service Layer (provider routing, caching)
        ↓
Provider Layer (ESPN, TheSportsDB)
```

See [CLAUDE.md](CLAUDE.md) for full architecture documentation.

## Supported Leagues

**ESPN**: NFL, NBA, NHL, MLB, MLS, NCAAF, NCAAM, WNBA, UFC, Premier League, La Liga, Bundesliga, Serie A, Champions League, 200+ soccer leagues

**TheSportsDB**: OHL, WHL, QMJHL, NLL, PLL, IPL, BBL, CPL, Boxing

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/v1/teams` | List teams |
| `POST /api/v1/epg/generate` | Generate EPG |
| `GET /api/v1/cache/stats` | Cache statistics |

Full API documentation at http://localhost:9195/docs

## Development

```bash
# Run with auto-reload
uvicorn teamarr.api.app:app --reload --port 9195

# Run tests
pytest
```

## License

MIT
