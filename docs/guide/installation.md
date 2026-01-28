---
title: Installation
parent: User Guide
nav_order: 1
---

# Installation

Docker Compose is the recommended method for installation.

## Prerequisites

- Docker
- [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) (highly recommended - Teamarr is designed for tight integration with Dispatcharr)
- [Game-Thumbs](game-thumbs.md) (optional - sports matchup thumbnail and logo generation)

## Docker

**Image tags:**
- `latest` - Stable release (recommended)
- `dev` - Development branch, may contain experimental features

```yaml
services:
  teamarr:
    image: ghcr.io/pharaoh-labs/teamarr:latest
    container_name: teamarr
    restart: unless-stopped
    ports:
      - 9195:9195
    volumes:
      - ./data:/app/data
    environment:
      # UI timezone - controls time display in the Teamarr web interface
      # EPG output timezone is configured separately in Settings
      - TZ=America/New_York

      # Console log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
      # Note: File logging (data/logs/) always captures DEBUG regardless of this setting
      # - LOG_LEVEL=INFO

      # Log format: "text" or "json" (default: text)
      # Use "json" for log aggregation systems (ELK, Loki, Splunk)
      # - LOG_FORMAT=text
```

Open Teamarr at `http://<your-server>:9195`

{: .note }
Advanced users familiar with Python may run Teamarr locally without Docker. Clone the repository and run `python app.py`.
