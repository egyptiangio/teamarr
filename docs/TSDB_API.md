# TheSportsDB API Reference

> **Last Updated:** 2025-12-19
> **Source:** https://www.thesportsdb.com/documentation

## Overview

TheSportsDB provides sports data for leagues not covered by ESPN (junior hockey, cricket, boxing, etc.).

**Free API Key:** `123`
**Rate Limit:** 30 requests/minute (free tier)

---

## API Versions

| Version | Base URL | Auth | Notes |
|---------|----------|------|-------|
| V1 | `https://www.thesportsdb.com/api/v1/json/{API_KEY}/` | URL path | Free tier available |
| V2 | `https://www.thesportsdb.com/api/v2/json/` | Header: `X-API-KEY` | **Premium only** |

---

## Critical Free Tier Limitations

### Result Limits by Endpoint

| Endpoint | Free Limit | Notes |
|----------|-----------|-------|
| `search_all_teams.php` | **10** | Teams per league capped at 10! |
| `search_all_leagues.php` | 10 | |
| `all_leagues.php` | 10 | |
| `eventsday.php` | 5 | |
| `eventsnextleague.php` | 1 | |
| `eventsnext.php` | 1 | **HOME games only** |
| `eventslast.php` | 1 | **HOME games only** |
| `lookuptable.php` | 5 | Soccer leagues only |

### Known Bugs (Free Tier)

| Endpoint | Bug Description |
|----------|-----------------|
| `lookup_all_teams.php?id={league_id}` | **BROKEN** - Returns teams from wrong league. ID 5159 (Canadian OHL) returns English League 1 teams. |
| `lookupteam.php?id={team_id}` | Often returns wrong team. Validate `idTeam` in response matches request. |

---

## Endpoints We Use

### 1. Events by Date
```
GET /eventsday.php?d={YYYY-MM-DD}&l={league_name}
```

**Parameters:**
- `d` - Date in YYYY-MM-DD format (required)
- `l` - League NAME, not ID (e.g., "Canadian OHL", "Indian Premier League")
- `s` - Sport name (optional filter)

**Response:**
```json
{
  "events": [
    {
      "idEvent": "123456",
      "strEvent": "Team A vs Team B",
      "dateEvent": "2025-01-15",
      "strTime": "19:00:00",
      "strTimestamp": "2025-01-15T19:00:00+00:00",
      "strHomeTeam": "Team A",
      "strAwayTeam": "Team B",
      "idHomeTeam": "111",
      "idAwayTeam": "222",
      "strVenue": "Arena Name",
      "strStatus": "NS"
    }
  ]
}
```

**Status Values:**
- `NS` / empty - Not Started
- `1H`, `2H`, `HT`, `ET` - In Progress
- `FT`, `AET` - Final
- `Cancelled` / `Postponed`

---

### 2. League Next Events
```
GET /eventsnextleague.php?id={league_id}
```

**Parameters:**
- `id` - League ID (numeric), NOT name

**Use Case:** Get upcoming events when `eventsday.php` returns empty (some leagues don't populate daily).

---

### 3. Teams in League
```
GET /search_all_teams.php?l={league_name}
```

**Parameters:**
- `l` - League NAME exactly as stored in TSDB (e.g., "Canadian OHL", not "OHL")

**Free Tier Limit:** 10 teams maximum!

**Response:**
```json
{
  "teams": [
    {
      "idTeam": "144346",
      "strTeam": "Barrie Colts",
      "strTeamShort": "BAR",
      "strLeague": "Canadian OHL",
      "strSport": "Ice Hockey",
      "strBadge": "https://www.thesportsdb.com/images/media/team/badge/..."
    }
  ]
}
```

---

### 4. Search Leagues
```
GET /search_all_leagues.php?c={country}&s={sport}
```

**Parameters:**
- `c` - Country name (e.g., "Canada", "India")
- `s` - Sport name (e.g., "Ice Hockey", "Cricket")

**Use Case:** Discover league IDs and names for configuration.

---

### 5. League Lookup
```
GET /lookupleague.php?id={league_id}
```

**Parameters:**
- `id` - League ID (numeric)

**Use Case:** Validate league metadata, get display name.

---

### 6. Team Search
```
GET /searchteams.php?t={team_name}
```

**Parameters:**
- `t` - Team name to search

**Free Tier Limit:** 2 results

**Use Case:** Find team ID when only name is known.

---

## League Configuration

### Required Fields

Each TSDB league needs TWO identifiers:

| Field | Used By | Example |
|-------|---------|---------|
| `provider_league_id` | `eventsnextleague.php`, `lookupleague.php` | `5159` |
| `provider_league_name` | `eventsday.php`, `search_all_teams.php` | `Canadian OHL` |

**Critical:** These MUST match TSDB's internal data exactly. Use `search_all_leagues.php` to discover correct values.

### Configured Leagues

| league_code | provider_league_id | provider_league_name | Sport |
|-------------|-------------------|---------------------|-------|
| ohl | 5159 | Canadian OHL | Ice Hockey |
| whl | 5160 | Canadian WHL | Ice Hockey |
| qmjhl | 5161 | Canadian QMJHL | Ice Hockey |
| ipl | 4460 | Indian Premier League | Cricket |
| cpl | 5176 | Caribbean Premier League | Cricket |
| bpl | 5529 | Bangladesh Premier League | Cricket |
| boxing | 4445 | Boxing | Boxing |

---

## Implementation Notes

### Why We Use League NAME, Not ID

Many V1 endpoints accept EITHER name or ID, but behave differently:

```
# By NAME - works correctly on free tier
eventsday.php?l=Canadian%20OHL       ✅ Returns OHL games
search_all_teams.php?l=Canadian%20OHL ✅ Returns OHL teams (max 10)

# By ID - often broken on free tier
lookup_all_teams.php?id=5159         ❌ Returns WRONG teams (English League 1)
```

### Caching Strategy

| Data Type | TTL | Rationale |
|-----------|-----|-----------|
| Teams in league | 24 hours | Teams rarely change mid-season |
| Events today | 30 minutes | Live scores, flex times |
| Events tomorrow | 4 hours | Possible flex scheduling |
| Events 3-7 days | 8 hours | Mostly stable |
| Events 8+ days | 24 hours | New games may appear |
| Past events | 7 days | Results don't change |

### Rate Limiting

- **Preemptive:** Track request timestamps, wait before hitting 30/min
- **Reactive:** On 429 response, wait 60 seconds and retry
- **Statistics:** Track all waits for UI feedback

---

## Workarounds for Free Tier Limits

### 10-Team Limit on `search_all_teams.php`

**Problem:** OHL has 20 teams, but free tier only returns 10.

**Solution (Implemented):** Two-phase team discovery:
1. Call `search_all_teams.php` - get up to 10 teams with full details
2. Call `eventsseason.php` - extract additional teams from scheduled games
3. Merge results, deduplicating by team ID

**Results:**
| League | Before | After |
|--------|--------|-------|
| OHL | 10 | 19 |
| WHL | 10 | 21 |
| QMJHL | 10 | 17 |

**Trade-offs:**
- Extra API call per league during cache refresh
- Teams without scheduled games in the 15-event sample may be missed
- Teams from events have less metadata (no strTeamShort)

**Implementation:** See `TSDBClient.get_teams_in_league()` in `teamarr/providers/tsdb/client.py`

### HOME-Only Schedules

**Problem:** `eventsnext.php` only returns HOME games on free tier.

**Solution:** Use `eventsday.php` across multiple days and filter by team name.

---

## Testing Endpoints

```bash
# Search for leagues by country/sport
curl "https://www.thesportsdb.com/api/v1/json/123/search_all_leagues.php?c=Canada&s=Ice%20Hockey"

# Get league metadata
curl "https://www.thesportsdb.com/api/v1/json/123/lookupleague.php?id=5159"

# Get teams (capped at 10)
curl "https://www.thesportsdb.com/api/v1/json/123/search_all_teams.php?l=Canadian%20OHL"

# Get events for a date
curl "https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d=2025-01-15&l=Canadian%20OHL"

# Get next league events
curl "https://www.thesportsdb.com/api/v1/json/123/eventsnextleague.php?id=5159"
```

---

## V2 API (Premium Only)

V2 provides higher limits and better data, but requires premium subscription ($2.50-$8/month).

**Authentication:** Header-based
```
X-API-KEY: your_premium_key
```

**Key V2 Endpoints:**
```
/list/teams/{league_id}     # 100 teams (vs 10 in V1)
/list/players/{team_id}     # 100 players
/schedule/league/{id}/{season}  # Full season (3000 limit)
/livescore/{sport}          # Live scores
```

---

## References

- **Official Docs:** https://www.thesportsdb.com/documentation
- **Pricing:** https://www.thesportsdb.com/pricing
- **Our Implementation:** `teamarr/providers/tsdb/client.py`
