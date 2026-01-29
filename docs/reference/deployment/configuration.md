---
title: Configuration
parent: Deployment
grand_parent: Technical Reference
nav_order: 2
---

# Configuration

Teamarr is configured via environment variables in your `docker-compose.yml` file.

## General Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone for date/time display and XMLTV output |
| `LOG_LEVEL` | `INFO` | Console log level: DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | `text` | Log format: `text` or `json` (for log aggregation) |

## ESPN API Settings

These settings control how Teamarr communicates with ESPN's API. Most users don't need to change these defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `ESPN_MAX_WORKERS` | `100` | Maximum parallel workers for fetching data |
| `ESPN_MAX_CONNECTIONS` | `100` | HTTP connection pool size |
| `ESPN_TIMEOUT` | `10` | Request timeout in seconds |
| `ESPN_RETRY_COUNT` | `3` | Number of retry attempts on failure |

### When to Adjust ESPN Settings

If you experience timeouts or connection failures during cache refresh or EPG generation, you may be hitting **DNS throttling** from your network setup. This commonly affects users with:

- **PiHole** or **AdGuard** DNS filtering
- Custom DNS resolvers with rate limits
- Router-level DNS throttling

**Recommended settings for DNS-throttled environments:**

```yaml
environment:
  - ESPN_MAX_WORKERS=20
  - ESPN_MAX_CONNECTIONS=20
  - ESPN_TIMEOUT=15
```

These lower values reduce the number of parallel DNS lookups, giving your DNS resolver time to process requests without throttling.

{: .note }
ESPN's API has generous rate limits that are practically impossible to hit. Connection issues are almost always caused by local DNS or network constraints, not ESPN throttling.
