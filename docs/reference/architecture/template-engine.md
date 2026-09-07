---
title: Template Engine
parent: Architecture
grand_parent: Technical Reference
nav_order: 6
---

# Template Engine

The template engine resolves `{variable}` placeholders in EPG titles, descriptions, and filler content. It supports 258 variables across 20 categories (plus chainable `|filter` value transforms), 33 condition evaluators, suffix rules for multi-game context, and template-type scoping for the variable picker.

## Architecture

```
TemplateResolver
  ├── VariableRegistry (258 variables, 20 categories)
  ├── ConditionEvaluator (33 evaluators)
  └── ContextBuilder (Event + Team → TemplateContext)
```

## Variable Resolution Pipeline

1. Parse `{variable}` and `{variable.suffix}` patterns from template string
2. Look up each variable in the `VariableRegistry`
3. Check the variable's `SuffixRules` to determine which game contexts are valid
4. Call the variable's extractor function with the appropriate `GameContext`
5. Replace placeholders with resolved values
6. Clean up artifacts (empty parentheses, double spaces, trailing punctuation)

## Suffix Rules

Each variable declares which game contexts it supports:

| Suffix | Context | Example |
|--------|---------|---------|
| `{var}` (base) | Current/next game | `{game_date}` → `"Mar 15"` |
| `{var.next}` | Next scheduled game | `{game_date.next}` → `"Mar 18"` |
| `{var.last}` | Last completed game | `{game_date.last}` → `"Mar 12"` |

| Rule | Base | .next | .last | Used By |
|------|------|-------|-------|---------|
| `ALL` | Yes | Yes | Yes | Most variables (opponent, game_date, scores) |
| `BASE_ONLY` | Yes | No | No | Team constants (team_name, league, sport) |
| `BASE_NEXT_ONLY` | Yes | Yes | No | Odds (no odds for past games) |
| `LAST_ONLY` | No | No | Yes | Deprecated — no variables use it |

## Template Scope

Orthogonal to suffix rules. Each variable also declares which template type(s) it is valid in, mirroring the existing `template_type` concept (`'team'` / `'event'`) used on the templates table and the conditions endpoint. This gates variable picker availability per template.

| Scope | Team picker | Event picker | Used By |
|-------|-------------|--------------|---------|
| `ALL` (default) | Yes | Yes | Positional and game-level variables (home_team, venue, odds_spread, is_playoff, etc.) |
| `TEAM_ONLY` | Yes | No | "Our team" perspective (team_name, opponent, is_home, team_record, win_streak, result, odds_moneyline, etc.) |
| `EVENT_ONLY` | No | Yes | Feed separation (feed_team, feed_team_short, is_home_feed, feed_home_away, etc.) |

The registry exposes `filter_by_template_type(template_type)` which returns the valid subset. Unknown values and `None` return all variables (fail-open, matches the conditions endpoint's behavior). Today the filter only applies to the picker via `GET /variables?template_type=…`; hand-typed out-of-scope variables still resolve at render time (backward compatibility).

Declare scope on the decorator:

```python
@register_variable(
    name="opponent",
    category=Category.IDENTITY,
    suffix_rules=SuffixRules.ALL,
    description="Opponent team name",
    scope=TemplateScope.TEAM_ONLY,
)
```

## Variable Categories

| Category | Count | Key Variables |
|----------|-------|---------------|
| **Home/Away** | 30 | is_home, vs_at, home_team_name, away_team_short |
| **Combat** | 28 | fighter1, fighter2, card_segment, round_number, fight_result_method |
| **Identity** | 21 | team_name, opponent, league, sport, team_short, matchup_short |
| **Motorsports** | 21 | race_name, circuit_name, session_name, pole_position, race_winner |
| **Conference** | 20 | college_conference, pro_division, division_abbrev |
| **Records** | 18 | team_record, opponent_record, team_wins, team_losses |
| **Streaks** | 18 | win_streak, loss_streak, streak_detail, streak_emoji |
| **Scores** | 15 | team_score, opponent_score, final_score, score_differential |
| **DateTime** | 13 | game_date, game_time, days_until, hours_until |
| **Rankings** | 11 | team_rank, opponent_rank, is_ranked, rank_text |
| **Tennis** | 10 | tournament_name, tennis_round, player1, player2 |
| **Summary** | 7 | game_recap, game_preview, game_event_note, series_summary |
| **Odds** | 7 | odds_spread, odds_over_under, odds_moneyline_team |
| **Soccer** | 7 | soccer_match_league, soccer_group_name, soccer_match_matchday |
| **Statistics** | 6 | team_ppg, opponent_ppg, team_wpct |
| **Outcome** | 4 | result, result_text, result_emoji, final_status |
| **Broadcast** | 4 | broadcast_simple, network, market |
| **Venue** | 4 | venue_full, venue_name, venue_city |
| **Standings** | 4 | playoff_seed, games_back |
| **Playoffs** | 4 | is_playoff, is_preseason, season_type |

Variables are registered via decorator in `teamarr/templates/variables/` (one file per category).

## Filters

Any variable can be piped through chainable value transforms: `{home_team|pascal}`, `{league|upper}`. Six filters are defined in `teamarr/templates/filters.py`: `lower`, `upper`, `title`, `pascal`, `slug`, `urlencode`. Permanent legacy aliases live in `teamarr/templates/resolver.py` for 10 retired transform variables (e.g. `{home_team_pascal}` resolves as `{home_team|pascal}`).

## Condition Evaluators

33 evaluators for conditional descriptions. Lower priority number = evaluated first. Priority 100 is the default (always matches).

| Condition | Description | Value Param |
|-----------|-------------|-------------|
| `always` | Legacy: always true | No |
| `is_home` | Team playing at home | No |
| `is_away` | Team playing away | No |
| `win_streak` | On N+ game win streak | Min streak length |
| `loss_streak` | On N+ game loss streak | Min streak length |
| `is_ranked` | Team ranked top 25 | No |
| `is_ranked_opponent` | Opponent ranked top 25 | No |
| `is_ranked_matchup` | Both teams top 25 | No |
| `is_top_ten_matchup` | Both teams top 10 | No |
| `is_conference_game` | Same conference (college) | No |
| `is_playoff` | Playoff game | No |
| `is_preseason` | Preseason game | No |
| `is_national_broadcast` | National TV (ABC, ESPN, NBC, etc.) | No |
| `has_odds` | Betting odds available | No |
| `opponent_name_contains` | Opponent name includes string | Search string |
| `is_knockout` | KO/TKO finish (MMA) | No |
| `is_submission` | Submission finish (MMA) | No |
| `is_decision` | Decision (MMA) | No |
| `is_finish` | Any finish (KO/TKO/sub) | No |
| `went_distance` | Went all rounds (MMA) | No |
| `is_final` | Reference game exists and is final | No |
| `is_not_final` | Reference game exists and is not final | No |
| `has_recap` | Provider postgame recap headline available | No |
| `has_preview` | Provider pregame preview blurb available | No |
| `has_generated_preview` | Optional generated pregame description available | No |
| `has_structured_preview` | Structured preview data (recent form) available | No |
| `is_neutral_site` | Game at a neutral site (bowls, tournaments) | No |
| `has_event_note` | Provider marquee/playoff note available | No |
| `has_match_note` | Provider soccer competition note available | No |
| `is_race_session` | Racing: session is the race itself | No |
| `is_qualifying_session` | Racing: session is (sprint) qualifying | No |
| `has_results` | Racing: session finished with results | No |
| `league_is` | Event's league in comma-separated list | League codes (e.g. `cfb,nfl`) |
| `sport_is` | Event's sport in comma-separated list | Sport codes (e.g. `basketball,hockey`) |

### Conditional Description Selection

Templates can define multiple descriptions with conditions and priorities:

```json
[
  {"condition": "win_streak", "condition_value": "5", "priority": 10,
   "template": "{team_name} riding a {win_streak}-game win streak!"},
  {"condition": "is_playoff", "priority": 20,
   "template": "Playoff {sport}: {team_name} vs {opponent}"},
  {"priority": 100,
   "template": "{team_name} vs {opponent}"}
]
```

The selector evaluates conditions by priority (lowest first). If multiple match at the same priority, one is chosen randomly. The selected template is then passed through variable resolution.

## Template Context

The context builder assembles a `TemplateContext` from events and team data:

| Component | Contents | Variables |
|-----------|----------|-----------|
| `TeamChannelContext` | Team identity (name, league, sport, logo) | `BASE_ONLY` vars |
| `GameContext` (current) | Current/next event, home/away, opponent, odds | `ALL` + `BASE_NEXT_ONLY` vars |
| `GameContext` (.next) | Next scheduled game | `.next` suffix vars |
| `GameContext` (.last) | Last completed game | `.last` suffix vars |
| `TeamStats` | Season record, standings, streak | `ALL` vars |

## Three Parallel Resolution Paths

Template resolution happens in three places that **must stay in sync** (channel creation, channel sync, EPG generation) — see [Consumer Layer](consumer-layer#channel-lifecycle) for the table.

## Art URL Reconstruction (game-thumbs base URL)

Art/icon fields (`program_art_url`, `event_channel_logo_url`, and filler
`art_url`) can store **relative paths** (e.g. `/{league_id}/{away_team|pascal}/{home_team|pascal}/cover.png`).
A single configured **base URL** (`settings.art_base_url`, set in EPG → Output →
Game Thumbs) is prefixed onto them at resolution time so the deployment-specific
host:port lives in one place. See [Game Thumbs](../../guide/epg/game-thumbs) and the
[Gracenote-modeled template design](gracenote-template-design).

The reconstruction is centralized so it reaches **every** consumer identically:

| Piece | Role |
|-------|------|
| `utilities/art_url.py` → `apply_art_base_url(value, base)` | the single join helper — prefixes the base onto relative values; absolute URLs (`scheme://…`) pass through unchanged; **idempotent** |
| `TemplateResolver.resolve_art(template, ctx)` | the one art entry point — `resolve()` then `apply_art_base_url()`; `art_base_url` injected via the resolver constructor |
| `utilities/art_url.py` → `read_art_base_url(db_factory)` | reads the setting once; processors inject it into each resolver |

Every art sink calls `resolve_art` (or the shared helper): EPG programme `<icon>`
and channel `<icon>` (event/team EPG + `xmltv.py` as an idempotent safety net),
Dispatcharr channel logos (`lifecycle/naming._resolve_logo_url`), and fillers.
This guarantees the EPG icon and the Dispatcharr channel logo never diverge.
`create_template`/`update_template` keep new art relative on write.

## Sample Data & Live Preview

The variable picker previews each `{variable}` against sample values. Two sources, with live preferred by default:

**Static sample — three shapes.** Every league resolves (by sport, via `resolve_shape`) to one of three generic, **fictitious** shapes rather than a per-league profile:

| Shape | Sport(s) | Sample identity |
|-------|----------|-----------------|
| `team` | all team sports (incl. soccer) | Greenwich Mean Time @ Flint Tropics |
| `combat` | boxing, MMA | Little Mac vs Super Macho Man (WVBA) |
| `racing` | motorsport | Ricky Bobby / Lightning McQueen (Piston Cup) |

Each shape is a kitchen-sink: every variable that applies to it is filled (the `team` shape carries both pro *and* college fields so either template type previews fully). Identities are invented on purpose, so a sample never looks like a real (and likely wrong-league) event — a regression test guards against any real franchise/RSN leaking in.

**Live preview.** When live is on, the picker fetches a real recent/upcoming event (`get_sample_event`, provider-aware, cached) and shows its actual values. A variable the real event can't fill is **surfaced as a gap** — left empty and counted — rather than masked with the fictitious sample, so users don't get a false sense of availability. Gaps are scoped to **categories relevant to the event's shape** (a basketball preview doesn't flag empty combat/racing variables), and the picker shows live coverage (`live_populated`/`live_total`). Any failure (no event, provider down) falls back silently to the static sample.

Combat leagues follow the same finished-first cascade: candidates come from a ±7-day scoreboard range, with a deep lookback (35-day windows) for the last finished card — the only sample that fills `fight_result`/`finish_*`. Failing all of that, the static WVBA shape renders.

See `GET /variables/samples` (`live`, `gaps`, `live_populated`, `live_total`).

**Server-side render (`POST /templates/preview`).** The editor's rendered
previews come from the backend: the endpoint runs the SAME `TemplateResolver`
and conditional selector that EPG generation uses, against the same live event
context as `/variables/samples` (shared cache in `templates/preview.py`),
falling back to static samples when no live event is available. The response
also carries a **condition trace** (per row: matched, selected, reason) so the
editor can show which conditional-description row fires and why. The frontend
keeps a client-side substitution (`createResolver`) only as an instant
optimistic layer while the debounced server render is in flight.

## File Locations

| File | Purpose |
|------|---------|
| `templates/resolver.py` | Variable resolution pipeline |
| `templates/conditions.py` | 33 condition evaluators |
| `templates/context.py` | Context dataclasses (Odds, GameContext, TemplateContext) |
| `templates/context_builder.py` | Build TemplateContext from Event + Team |
| `templates/variables/` | 20 category modules with 258 variable definitions |
| `templates/variables/registry.py` | VariableRegistry singleton |
| `templates/filters.py` | Chainable filter transforms (legacy aliases live in `resolver.py`) |
| `templates/validation.py` | Template validation |
| `templates/sample_data.py` | 3-shape fictitious sample values + `resolve_shape` for UI preview |
| `templates/preview.py` | Live-context builder + cache shared by `/variables/samples` and `/templates/preview` |
| `utilities/art_url.py` | Game-thumbs base URL join helper + reader (`apply_art_base_url`, `read_art_base_url`) |
| `utilities/xmltv.py` | XMLTV serialization (applies art base as an idempotent safety net) |
