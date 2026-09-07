---
title: Variables
parent: EPG
grand_parent: User Guide
nav_order: 3
redirect_from:
  - /guide/templates/variables/
  - /guide/templates/variables.html
---

# Template Variables

Templates use variables enclosed in curly braces that get replaced with real data when EPG is generated. Teamarr provides 258 variables across 20 categories, plus [filters](#filters-transforming-variable-values) that transform any variable's value.

## Team vs Event Templates

Variables are scoped to the template type they make sense in. The template editor's variable picker only surfaces variables that apply to the template you're editing.

- **Team templates** have an "our team" perspective — the subscribed team is the anchor. These templates expose team-perspective variables like `{team}`, `{opponent}`, `{is_home}`, `{team_record}`, `{win_streak}`, `{result}`, `{odds_moneyline}`, and similar "my team vs the other team" variables.
- **Event templates** are positional — they describe a matchup without a reference team. These templates expose positional variables like `{home_team}`, `{away_team}`, `{home_team_record}`, and game-level data. Event templates additionally expose the feed-team family (`{feed_team}`, `{feed_team_short}`, `{is_home_feed}`, etc.) for feed-separated channels.
- **Shared variables** (most of the list) are available in both — positional teams, venue, date/time, playoffs, odds (excluding the team-perspective `{odds_moneyline}` pair), soccer, combat sports, and league/sport identifiers.

If you hand-type a scope-restricted variable into a template where it doesn't belong (e.g., `{team}` in an event template), it will still resolve (backward compatibility), but the picker won't offer it. Use the picker to stay within the intended scope.

Hovering any variable in the picker shows its description and an example value drawn from the current preview (so with a live preview selected, the example is real data from that event). Variables insert at the cursor of the last field you clicked into — the picker reminds you to click into a field first when none is focused.

The picker has a **search box** and a **Recently used** row at the top. Searching also understands the retired transform names — typing `pascal` or `team_name_pascal` surfaces the base variable with a hint pointing at the `|pascal` filter.

## Previewing Templates

The template editor renders a live preview of every field as you type. The **Previewing as** bar above the tabs picks which league to preview against — the leagues you've subscribed to (from the [Subscriptions](../subscriptions) tab, plus the leagues of teams you follow) are listed with their logos, grouped by sport and searchable. Before you've subscribed to anything, all available leagues are shown. It drives every preview on the page: the inline per-field previews, the condition trace on the Conditions tab, and the **Guide Preview** card in the right rail — an EPG-style card showing the title, subtitle, and description exactly as a viewer's guide would, including any [conditional rows](conditions.md) that win a field for the preview event (marked with a green target).

**Live by default.** The preview tries to render **real data** for a recent or upcoming event in the selected league, and the badge turns green **Live** with a coverage count (e.g. `137/203 variables live · 66 gaps`) — how many of the variables that apply to this kind of event the real event actually populated. A "gap" is a variable that *could* apply but the event didn't provide (variables for other sports aren't counted). If no event is available or the provider can't be reached, it falls back automatically to sample data and the badge reads **No event**.

Click the badge to toggle to **Sample** mode, which uses generic, intentionally-fictitious placeholders (the same three sample shapes — a team game, a fight card, a race — regardless of league) so you can see every variable filled even when nothing is live.

## Suffix Support

**Team templates** support suffixes to reference different games:

| Suffix | Context | Example |
|--------|---------|---------|
| (none) | Current game | `{opponent}` |
| `.next` | Next upcoming game | `{opponent.next}` |
| `.last` | Most recent game | `{opponent.last}` |

**Event templates** don't need suffixes - each channel exists for a single game, so there's no "next" or "last" to reference.

{: .note }
> **When there's no next (or last) game** — offseason, end of a season — suffixed variables resolve to empty and the usual cleanup removes leftover wrappers, so raw `{…}` braces never reach your guide. A misspelled variable name, or a suffix the variable doesn't support, still renders literally so you can spot the mistake. For a proper offseason message, use the **Offseason** idle register on the Fillers tab (enabled with generic content by default on new templates).

In the tables below, the **Suffixes** column indicates which suffixes are available:
- **base** = no suffix (current game)
- **.next** = next game
- **.last** = last game

---

## Artwork & Game Thumbs

Three template fields hold image URLs and accept the same variables as any other field:

| Field | Used for |
|-------|----------|
| **Program Art URL** (`program_art_url`) | the programme `<icon>` in the EPG (per-game artwork) |
| **Channel Logo URL** (`event_channel_logo_url`, event templates) | the Dispatcharr channel logo **and** the EPG channel icon |
| **Filler Art URL** (pregame/postgame/idle `art_url`) | artwork on filler programmes |

### Game-Thumbs base URL

Instead of writing the full image host in every template, set it **once** in
**EPG → Output → Game Thumbs → Game-Thumbs Base URL** (e.g. your
[Game Thumbs](game-thumbs) host). Templates then store only the **relative path**,
with **no leading slash**:

```
{league_id}/{away_team|pascal}/{home_team|pascal}/cover.png?style=6&logo=true&fallback=true
```

At generation the base URL — host **and port**, exactly as you entered it — is prefixed onto the
relative path. Rules:

- **Relative paths** (anything without a `scheme://`) get the base prefixed. Don't start
  them with `/` — a leading variable may resolve to an absolute URL, and a prepended `/`
  would break it. (A stray leading slash on a plain path is stripped automatically.)
- **Absolute URLs** (anything with `http://`/`https://`) are left **unchanged**, so you can
  still hardcode a one-off full URL in a single field.
- Empty base URL = no prefixing (every art field must then be a full URL).

The same reconstructed URL is sent everywhere it's needed — the EPG `<icon>` **and** the
Dispatcharr channel logo — so the guide artwork and the channel logo always match. The
live preview in the template editor applies the base URL too, so what you see matches the
generated output (and renders the actual image so you can confirm the link resolves).

## Filters: transforming variable values

Any variable accepts a `|filter` modifier that transforms its resolved value:

| Filter | Effect | `{home_team\|…}` example |
|--------|--------|--------------------------|
| `lower` | lowercase | `detroit lions` |
| `upper` | UPPERCASE | `DETROIT LIONS` |
| `title` | Title Case each word | `Detroit Lions` |
| `pascal` | PascalCase, accents folded, punctuation dropped (`/` pairings kept as `+`) | `DetroitLions` |
| `slug` | lowercase, hyphen-separated, URL-safe (`/` pairings kept as `+`) | `detroit-lions` |
| `urlencode` (alias `url`) | percent-encode for URL query strings | `Detroit%20Lions` |

Filters **chain** left-to-right: `{home_team|pascal|url}` PascalCases the name, then
URL-encodes the result. Suffixes come before the filter: `{opponent.next|upper}`.

`pascal` and `slug` are the path filters, and they treat `/` as a **pairing separator**
rather than punctuation to drop: a tennis doubles side arrives from the provider as one
name (`Isabelle Haverlag / Nika Radisic`) and renders as `IsabelleHaverlag+NikaRadisic`
(`isabelle-haverlag+nika-radisic` for `slug`) — `+` is how Game Thumbs addresses a pair,
so both players end up in the artwork. Singles names are unaffected.

- Filters are **opt-in**: variables without one are unchanged, so a variable that already
  holds a full URL is never double-encoded.
- A misspelled filter (e.g. `|urlencodee`) renders literally, just like a misspelled
  variable name, so you can spot the typo. The live preview applies filters too.
- In the template editor, hover any variable chip to preview each filter against the
  live sample and insert `{variable|filter}` in one click.

**Retired transform variables.** Ten single-purpose variables were replaced by filters
(`{team_name_pascal}`, `{home_team_pascal}`, `{away_team_pascal}`, `{team_abbrev_lower}`,
`{home_team_abbrev_lower}`, `{away_team_abbrev_lower}`, `{opponent_abbrev_lower}`,
`{feed_team_abbrev_lower}`, `{result_lower}`, `{sport_lower}`). Existing templates were
migrated automatically, and the old names remain permanent aliases — `{team_name_pascal}`
resolves as `{team_name|pascal}` forever (`{sport_lower}` maps to `{sport|slug}`, matching
its old hyphenated output).

### URL-encoding in art URLs

When a variable value goes into the **query string** of an art URL, characters like
spaces and `&` need to be percent-encoded — otherwise a value such as
`{race_name}` = `Pit Stop & Podium` truncates the URL at the `&`, so only the first
part reaches Game-Thumbs. Add `|urlencode` to any variable to encode its value:

```
f1/cover?title={race_name|urlencode}&subtitle={session_name|urlencode}&iconurl=
```

The filter encodes **only the variable's value** — the template's own `?`, `&`, and
`=` that form the URL structure stay literal.

---

## Identity

Core identifiers for teams, leagues, and matchups.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{team_name}` | Team display name | base | `Detroit Lions` |
| `{team_name_the}` | Team name with Gracenote-convention article (clubs get 'the', national teams don't) | base | `the Detroit Lions` |
| `{team_name_ranked_the}` | Team name with rank and article composed (article survives when unranked) | base | `the No. 7 Detroit Lions` |
| `{team_abbrev}` | Team abbreviation uppercase | base | `DET` |
| `{team_short}` | Team short name | base | `Lions` |
| `{opponent}` | Opponent team name | base, .next, .last | `Chicago Bears` |
| `{opponent_the}` | Opponent name with Gracenote-convention article | base, .next, .last | `the Chicago Bears` |
| `{opponent_ranked_the}` | Opponent with rank and article composed | base, .next, .last | `the No. 14 Chicago Bears` |
| `{opponent_abbrev}` | Opponent team abbreviation uppercase | base, .next, .last | `CHI` |
| `{opponent_short}` | Opponent short name | base, .next, .last | `Bears` |
| `{matchup}` | Full matchup in the sport's convention: visitor first with `@` for US team sports, home first with `v` for soccer/rugby/cricket; neutral-site games use `v` | base, .next, .last | `Chicago Bears @ Detroit Lions` · `Ipswich Town v Liverpool` |
| `{matchup_abbrev}` | Abbreviated matchup uppercase, same convention | base, .next, .last | `CHI @ DET` · `IPS v LIV` |
| `{matchup_short}` | Short-name matchup, same convention | base, .next, .last | `Bears @ Lions` · `Ipswich v Liverpool` |
| `{team1}` | First team in the matchup order: the visitor for US team sports, the home side for soccer/rugby/cricket, or whatever the [Matchup Order](../settings/general.md#matchup-order) setting says | base, .next, .last | `Chicago Bears` |
| `{team2}` | Second team in the matchup order | base, .next, .last | `Detroit Lions` |
| `{team1_short}` | First team's short name in the matchup order | base, .next, .last | `Bears` |
| `{team2_short}` | Second team's short name in the matchup order | base, .next, .last | `Lions` |
| `{team1_abbrev}` | First team's abbreviation uppercase in the matchup order | base, .next, .last | `CHI` |
| `{team2_abbrev}` | Second team's abbreviation uppercase in the matchup order | base, .next, .last | `DET` |
| `{league}` | League short alias | base | `NFL` |
| `{league_name}` | League display name from the leagues table | base | `NFL` |
| `{league_abbrev}` | League abbreviation built from the league name — existing capitals plus the first letter of each word | base | `WC` (from `World Cup`) |
| `{league_id}` | League identifier for URLs | base | `nfl` |
| `{league_code}` | Raw league code | base | `nfl` |
| `{sport}` | Sport display name | base | `Football` |
| `{gracenote_category}` | Gracenote category for EPG; customizable per league (Settings → Advanced → Gracenote Category Overrides) | base | `NFL Football` |
| `{exception_keyword}` | Exception keyword label (e.g., 'Spanish', '4K') | base | `4K` |

---

## Date & Time

Game scheduling information.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{game_date}` | Full game date | base, .next, .last | `Sunday, December 22, 2024` |
| `{game_date_short}` | Short game date | base, .next, .last | `Dec 22` |
| `{game_day}` | Day of week | base, .next, .last | `Sunday` |
| `{game_day_short}` | Short day of week | base, .next, .last | `Sun` |
| `{game_time}` | Game time formatted per user settings | base, .next, .last | `1:00 PM EST` |
| `{days_until}` | Days until game | base, .next, .last | `0` |
| `{today_tonight}` | 'today' or 'tonight' (5pm cutoff) when the game is today; 'tomorrow', the weekday, or the date when it is further out | base, .next, .last | `today` |
| `{today_tonight_title}` | Title-case form of `{today_tonight}` | base, .next, .last | `Today` |
| `{relative_day}` | Relative day: 'today', 'tonight', 'tomorrow', day of week, or date | base, .next | `tomorrow` |
| `{relative_day_title}` | Relative day (title case) | base, .next | `Tomorrow` |
| `{day}` | Game day of month, no leading zero | base, .next, .last | `22` |
| `{month}` | Game month number, no leading zero | base, .next, .last | `12` |
| `{year}` | Game year | base, .next, .last | `2024` |

---

## Venue

Stadium and location information.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{venue}` | Stadium/arena name | base, .next, .last | `Ford Field` |
| `{venue_city}` | Venue city | base, .next, .last | `Detroit` |
| `{venue_state}` | Venue state | base, .next, .last | `MI` |
| `{venue_full}` | Full venue location | base, .next, .last | `Ford Field, Detroit, MI` |

---

## Home/Away

Positional team references and home/away context.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{home_team}` | Home team name (positional) | base, .next, .last | `Detroit Lions` |
| `{home_team_the}` | Home team name with Gracenote-convention article | base, .next, .last | `the Detroit Lions` |
| `{home_team_ranked_the}` | Home team with rank and article composed (rank slots after the article; article survives when unranked) | base, .next, .last | `the No. 20 Arkansas Razorbacks` |
| `{home_team_abbrev}` | Home team abbreviation uppercase | base, .next, .last | `DET` |
| `{home_team_short}` | Home team short name | base, .next, .last | `Lions` |
| `{home_team_logo}` | Home team logo URL | base, .next, .last | ESPN logo URL |
| `{away_team}` | Away team name (positional) | base, .next, .last | `Chicago Bears` |
| `{away_team_the}` | Away team name with Gracenote-convention article | base, .next, .last | `the Chicago Bears` |
| `{away_team_ranked_the}` | Away team with rank and article composed | base, .next, .last | `the No. 14 Texas A&M Aggies` |
| `{away_team_abbrev}` | Away team abbreviation uppercase | base, .next, .last | `CHI` |
| `{away_team_short}` | Away team short name | base, .next, .last | `Bears` |
| `{away_team_logo}` | Away team logo URL | base, .next, .last | ESPN logo URL |
| `{is_home}` | 'true' if team is home, 'false' if away | base, .next, .last | `true` |
| `{is_away}` | 'true' if team is away, 'false' if home | base, .next, .last | `false` |
| `{home_away_text}` | 'at home' or 'on the road' | base, .next, .last | `at home` |
| `{vs_at}` | 'vs' if home, 'at' if away; neutral-site games read 'vs' | base, .next, .last | `vs` |
| `{at_vs}` | Perspective-free connector: 'at' for US team sports, 'vs.' otherwise; neutral-site games always read 'vs.' | base, .next, .last | `at` |
| `{home_away_verb}` | 'host' at home, 'visit' away | base, .next, .last | `host` |
| `{vs_@}` | 'vs' if home, '@' if away; neutral-site games read 'vs' | base, .next, .last | `vs` |

{: .note }
The `_the` variables emit a lowercase `the` for mid-sentence use ("take on
the Detroit Pistons"). When one opens a title or description, the renderer
capitalizes it automatically ("The Detroit Pistons host…"). National teams
("Netherlands") and individual-sport competitors never get the article,
matching Gracenote convention.

### Feed Team

When a channel is configured for a specific home or away broadcast feed (via the Feed Separation setting), these variables resolve to the team whose feed this channel carries — independent of which team is home or away on any given day. If the channel has no feed assignment, all Feed Team variables return empty strings (they disappear gracefully from templates).

These are most useful for Event EPG templates on stream-separated channels (e.g., an MLB "Home feed" channel that always shows the home broadcaster's perspective regardless of which team is home today).

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{feed_team}` | Feed team full name | base | `Baltimore Orioles` |
| `{feed_team_short}` | Feed team short name | base | `Orioles` |
| `{feed_team_abbrev}` | Feed team abbreviation uppercase | base | `BAL` |
| `{feed_team_logo}` | Feed team logo URL | base | ESPN logo URL |
| `{is_home_feed}` | `'true'` if this channel is the home team's feed, `'false'` if away, `''` if no feed | base | `true` |
| `{is_away_feed}` | `'true'` if this channel is the away team's feed, `'false'` if home, `''` if no feed | base | `false` |
| `{feed_home_away}` | `'Home'` if home feed, `'Away'` if away feed, `''` if no feed | base | `Home` |
| `{broadcast_feed}` | `'Home Team Feed'` / `'Away Team Feed'` / `''` if no feed | base | `Home Team Feed` |
| `{broadcast_feed_team}` | `'{Team Name} Feed'` or `''` if no feed | base | `Baltimore Orioles Feed` |
| `{broadcast_feed_team_short}` | `'{Team Short Name} Feed'` or `''` if no feed | base | `Orioles Feed` |
| `{broadcast_feed_team_abbrev}` | `'{TEAM ABBREV} Feed'` or `''` if no feed | base | `BAL Feed` |

Feed Team variables do **not** support `.next` / `.last` suffixes — they describe the channel's configuration, not a specific game's schedule. For per-game home/away references, use the `{home_team}` / `{away_team}` variables above.

`{broadcast_feed}` and the `{broadcast_feed_team}`/`{broadcast_feed_team_short}`/`{broadcast_feed_team_abbrev}` family are **pre-formatted** — they include the literal `" Feed"` suffix. When feed separation isn't active they return `""` as a unit, so the whole phrase disappears cleanly from the template (unlike composing `{feed_home_away} Team Feed` yourself, which would leave `"Team Feed"` orphaned).

---

## Records

Team and opponent win-loss records.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{team_record}` | Team's overall record | base | `10-4` |
| `{team_wins}` | Team's total wins | base | `10` |
| `{team_losses}` | Team's total losses | base | `4` |
| `{team_ties}` | Team's total ties/draws | base | `` |
| `{team_win_pct}` | Team's winning percentage | base | `.714` |
| `{home_record}` | Team's home record | base | `6-1` |
| `{home_win_pct}` | Team's home winning percentage | base | `.857` |
| `{away_record}` | Team's away/road record | base | `4-3` |
| `{away_win_pct}` | Team's away winning percentage | base | `.571` |
| `{opponent_record}` | Opponent's overall record | base, .next, .last | `8-6` |
| `{opponent_wins}` | Opponent's total wins | base, .next, .last | `8` |
| `{opponent_losses}` | Opponent's total losses | base, .next, .last | `6` |
| `{opponent_ties}` | Opponent's total ties/draws | base, .next, .last | `` |
| `{opponent_win_pct}` | Opponent's winning percentage | base, .next, .last | `.571` |
| `{home_team_record}` | Home team's overall record for this game | base, .next, .last | `10-4` |
| `{away_team_record}` | Away team's overall record for this game | base, .next, .last | `8-6` |
| `{home_team_seed}` | Home team's playoff seed | base, .next, .last | `2` |
| `{away_team_seed}` | Away team's playoff seed | base, .next, .last | `5` |

---

## Streaks

Current winning and losing streaks.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{streak}` | Team's current streak formatted (e.g., 'W3' or 'L2') | base | `W2` |
| `{streak_length}` | Team's streak as absolute value | base | `2` |
| `{streak_type}` | Team's streak direction: 'win' or 'loss' | base | `win` |
| `{win_streak}` | Team's winning streak length (empty if losing) | base | `2` |
| `{loss_streak}` | Team's losing streak length (empty if winning) | base | `` |
| `{opponent_streak}` | Opponent's current streak formatted | base, .next, .last | `L1` |
| `{opponent_streak_length}` | Opponent's streak as absolute value | base, .next, .last | `1` |
| `{opponent_streak_type}` | Opponent's streak direction | base, .next, .last | `loss` |
| `{opponent_win_streak}` | Opponent's winning streak (empty if losing) | base, .next, .last | `` |
| `{opponent_loss_streak}` | Opponent's losing streak (empty if winning) | base, .next, .last | `1` |
| `{home_team_streak}` | Home team's current streak formatted | base, .next, .last | `W2` |
| `{home_team_streak_length}` | Home team's streak as absolute value | base, .next, .last | `2` |
| `{home_team_win_streak}` | Home team's winning streak (empty if losing) | base, .next, .last | `2` |
| `{home_team_loss_streak}` | Home team's losing streak (empty if winning) | base, .next, .last | `` |
| `{away_team_streak}` | Away team's current streak formatted | base, .next, .last | `L1` |
| `{away_team_streak_length}` | Away team's streak as absolute value | base, .next, .last | `1` |
| `{away_team_win_streak}` | Away team's winning streak (empty if losing) | base, .next, .last | `` |
| `{away_team_loss_streak}` | Away team's losing streak (empty if winning) | base, .next, .last | `1` |

---

## Scores

Game scores and results. Empty for future games.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{team_score}` | Team's score (empty if game not started) | base, .next, .last | `31` |
| `{opponent_score}` | Opponent's score (empty if game not started) | base, .next, .last | `24` |
| `{score}` | Score, home team first. Empty if not started. | base, .next, .last | `31-24` |
| `{final_score}` | Score with team perspective (team score first) | base, .next, .last | `31-24` |
| `{home_team_score}` | Home team's score | base, .next, .last | `31` |
| `{away_team_score}` | Away team's score | base, .next, .last | `24` |
| `{score_diff}` | Score differential (+7 = won by 7, -7 = lost by 7) | base, .next, .last | `+7` |
| `{score_differential}` | Score differential as absolute value | base, .next, .last | `7` |
| `{score_differential_text}` | Score differential as text | base, .next, .last | `by 7` |
| `{event_result}` | Full event result, home team first. Empty if not final. | base, .next, .last | `Detroit Lions 31 - Chicago Bears 24` |
| `{event_result_abbrev}` | Abbreviated event result. Empty if not final. | base, .next, .last | `DET 31 - CHI 24` |
| `{winner}` | Winning team name. Empty if not final or tie. | base, .next, .last | `Detroit Lions` |
| `{winner_abbrev}` | Winning team abbreviation. Empty if not final or tie. | base, .next, .last | `DET` |
| `{loser}` | Losing team name. Empty if not final or tie. | base, .next, .last | `Chicago Bears` |
| `{loser_abbrev}` | Losing team abbreviation. Empty if not final or tie. | base, .next, .last | `CHI` |

---

## Outcome

Game result indicators. Empty for future games.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{result}` | Game result ('W', 'L', or 'T') | base, .next, .last | `W` |
| `{result_text}` | Game result as text ('defeated', 'lost to', 'tied') | base, .next, .last | `defeated` |
| `{overtime_text}` | 'in overtime' if game went to overtime, empty otherwise | base, .next, .last | `` |
| `{overtime_short}` | 'OT' if game went to overtime, empty otherwise | base, .next, .last | `` |

---

## Standings

Playoff position and standings information.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{playoff_seed}` | Team's playoff seed (e.g., '1' for 1-seed) | base | `2` |
| `{games_back}` | Games behind division/conference leader | base | `-` |
| `{opponent_playoff_seed}` | Opponent's playoff seed | base, .next, .last | `5` |
| `{opponent_games_back}` | Opponent's games behind leader | base, .next, .last | `-` |

---

## Statistics

Team scoring averages.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{team_ppg}` | Team's points per game average | base | `28.4` |
| `{team_papg}` | Team's points allowed per game average | base | `21.6` |
| `{opponent_ppg}` | Opponent's points per game average | base, .next, .last | `24.2` |
| `{opponent_papg}` | Opponent's points allowed per game average | base, .next, .last | `22.8` |
| `{home_team_ppg}` | Home team's PPG for this game | base, .next, .last | `28.4` |
| `{away_team_ppg}` | Away team's PPG for this game | base, .next, .last | `24.2` |

---

## Playoffs

Season type indicators. All providers normalize their native season codes to a canonical value so these variables behave consistently regardless of the underlying data source.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{season_type}` | Canonical season type — one of `preseason`, `regular`, `postseason`, `offseason`, or empty if unknown | base, .next, .last | `postseason` |
| `{is_playoff}` | `'true'` if postseason game | base, .next, .last | `` |
| `{is_preseason}` | `'true'` if preseason/exhibition game | base, .next, .last | `` |
| `{is_regular_season}` | `'true'` if regular season game | base, .next, .last | `true` |

**Provider coverage:**

| Provider | Playoff detection |
|----------|-------------------|
| ESPN | Full — derived from season slug (`post-season`, `semifinals`, etc.) with numeric-type fallback |
| MLB Stats | Full — `gameType` codes (`F`/`D`/`L`/`W`/`P` → postseason, `S`/`E` → preseason) |
| HockeyTech | Full — via per-season `playoff` flag (CHL, AHL, PWHL, USHL) |
| TSDB | Partial — postseason detected via special `intRound` codes (125/150/160/170/180/200) used by some leagues (NBA, NHL, IPL, European knockouts). Leagues that keep normal round numbering through finals (AFL, NRL, boxing) can't be detected and return empty. Preseason is never detected for TSDB. |

---

## Odds

Betting lines and odds (when available).

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{has_odds}` | 'true' if odds are available for this game | base, .next | `true` |
| `{odds_spread}` | Point spread | base, .next | `-3.0` |
| `{odds_moneyline}` | Team's moneyline (e.g., '-150' or '+130') | base, .next | `-150` |
| `{odds_opponent_moneyline}` | Opponent's moneyline | base, .next | `+130` |
| `{odds_over_under}` | Over/under total (e.g., '47.5') | base, .next | `48.5` |
| `{odds_provider}` | Odds provider name | base, .next | `ESPN BET` |
| `{odds_details}` | Full odds description string | base, .next | `DET -3.0, O/U 48.5` |

---

## Broadcast

TV and streaming information.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{broadcast_network}` | Primary broadcast network (first in list) | base, .next, .last | `FOX` |
| `{broadcast_simple}` | Comma-separated broadcast networks | base, .next, .last | `FOX, NFL Network` |
| `{broadcast_national_network}` | National broadcast networks only | base, .next, .last | `FOX` |
| `{is_national_broadcast}` | 'true' if game is on national TV | base, .next, .last | `true` |

---

## Summary & Context

Provider editorial/context copy for a game, passed through raw. These are **sparse by nature** — empty when the provider didn't supply them.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{game_recap}` | Postgame recap headline — short, self-contained, carries the result. Empty until a game is final | base, .next, .last | `Brunson scores 45, and New York tops Spurs for title` |
| `{game_preview}` | Pregame preview blurb. Empty once a game is final (use `{game_recap}` then) | base, .next, .last | `Toronto Blue Jays (35-38) vs. Boston Red Sox` |
| `{generated_preview}` | Opt-in sport-specific preview for baseball, football, and basketball, with a generic matchup sentence for other sports; composed from public fields and never betting information | base, .next, .last | `The Packers visit the Broncos at Empower Field...` |
| `{week}` | Provider-reported football week number | base, .next, .last | `3` |
| `{home_probable_starter}` / `{away_probable_starter}` | Baseball probable starter with ESPN-reported record and ERA | base, .next, .last | `M. Boyd (8-2, 4.02 ERA)` |
| `{home_home_runs_leader}` / `{away_home_runs_leader}` | Exact home-run leader fact | base, .next, .last | `S. Ohtani — 30 home runs` |
| `{home_batting_average_leader}` / `{away_batting_average_leader}` | Exact batting-average leader fact | base, .next, .last | `R. Arozarena — .272 batting average` |
| `{home_rbi_leader}` / `{away_rbi_leader}` | Exact RBI leader fact | base, .next, .last | `J. Ramírez — 82 RBI` |
| `{home_passing_leader}` / `{away_passing_leader}` | Exact football passing-leader fact | base, .next, .last | `B. Nix — 18/25, 246 YDS` |
| `{home_rushing_leader}` / `{away_rushing_leader}` | Exact football rushing-leader fact | base, .next, .last | `J. Dobbins — 12 CAR, 68 YDS` |
| `{home_receiving_leader}` / `{away_receiving_leader}` | Exact football receiving-leader fact | base, .next, .last | `C. Sutton — 6 REC, 84 YDS` |
| `{home_points_leader}` / `{away_points_leader}` | Exact basketball points leader fact | base, .next, .last | `K. Cardoso — 14.7 points per game` |
| `{home_rebounds_leader}` / `{away_rebounds_leader}` | Exact basketball rebounds leader fact | base, .next, .last | `K. Cardoso — 8.8 rebounds per game` |
| `{home_assists_leader}` / `{away_assists_leader}` | Exact basketball assists leader fact | base, .next, .last | `N. Cloud — 5.0 assists per game` |
| `{home_total_yards_per_game}` / `{away_total_yards_per_game}` | Football team total yards per game | base, .next, .last | `360` |
| `{home_rushing_yards_per_game}` / `{away_rushing_yards_per_game}` | Football team rushing yards per game | base, .next, .last | `162` |
| `{home_points_allowed_per_game}` / `{away_points_allowed_per_game}` | Basketball points allowed per game | base, .next, .last | `87.0` |
| `{game_event_note}` | Marquee/playoff designation. Empty for ordinary regular-season games | base, .next, .last | `NBA Finals - Game 5` |
| `{series_summary}` | Playoff/season-series state. Empty when there's no series context | base, .next, .last | `Series tied 1-1` |
| `{home_last_five}` | Home team's W-L over its last five games (populates days ahead) | base, .next, .last | `4-1` |
| `{away_last_five}` | Away team's W-L over its last five games | base, .next, .last | `2-3` |
| `{last_five_summary}` | Recent-form prose for both teams; empty without data — pair with `has_structured_preview` | base, .next, .last | `The Rays have won 2 of their last five; the Red Sox have won 4 of their last five.` |

`{generated_preview}` is not included in any starter template. To opt in, add
the `has_generated_preview` condition and use `{generated_preview}` in that
row. The formatter omits missing facts. Baseball includes the probable starter
and home-run leader, falling back to the batting-average or RBI leader when the
home-run leader is unavailable. Football includes every available passing,
rushing, and receiving leader; basketball likewise includes every available
points, rebounds, and assists leader. Records and recent form are added when
available, as are football yardage and basketball points-for/points-allowed team stats.
Hockey and other sports receive only a complete generic matchup sentence using
the away team, home team, and venue. All prose is deterministic, source-grounded,
and excludes betting data.

Dedicated prose for additional sports, including hockey, can be added separately
once representative provider payloads are available for validation.

{: .note }
Because these populate only for some games, pair them with other content or a static fallback so a template never renders blank. In main descriptions, gate them with condition rows (`has_preview`, `has_recap`, …); in filler registers, use [filler condition rows](conditions#filler-condition-rows) — the starter set's postgame `has_recap → {game_recap.last}` row is the canonical example. `{game_recap}` and `{game_event_note}` come free from the scoreboard; `{game_preview}` and `{series_summary}` come from the per-event summary fetch that EPG generation already makes (no extra API calls).

---

## Rankings

College rankings — any college league ESPN polls, including NCAAF (AP, FCS and Division II polls), NCAAM and NCAAW (AP), plus college hockey, volleyball, lacrosse and NCAA soccer.

{: .note }
Ranks come from the current published poll and are empty out of season: ESPN keeps serving a season's final poll year-round, so Teamarr ignores polls older than 45 days rather than stamping last season's ranks onto this season's listings. College basketball ranks appear once the preseason AP poll lands in November, football's in August.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{team_rank}` | Team's ranking (e.g., '5' for #5, empty if unranked) | base | `` |
| `{team_rank_display}` | Team's ranking with # prefix (e.g., '#5') | base | `` |
| `{is_ranked}` | 'true' if team is ranked, empty otherwise | base | `` |
| `{opponent_rank}` | Opponent's ranking | base, .next, .last | `` |
| `{opponent_rank_display}` | Opponent's ranking with # prefix | base, .next, .last | `` |
| `{opponent_is_ranked}` | 'true' if opponent is ranked, empty otherwise | base, .next, .last | `` |
| `{is_ranked_matchup}` | 'true' if both teams are ranked | base, .next, .last | `` |
| `{home_team_rank}` | Home team's ranking for this game | base, .next, .last | `` |
| `{away_team_rank}` | Away team's ranking for this game | base, .next, .last | `` |
| `{home_team_rank_display}` | Home team's rank in Gracenote prose form ('No. 20'), empty when unranked | base, .next, .last | `No. 20` |
| `{away_team_rank_display}` | Away team's rank in Gracenote prose form ('No. 15'), empty when unranked | base, .next, .last | `No. 15` |

---

## Conference

Conference and division information.

### Pro Leagues

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{pro_conference}` | Team's pro conference (e.g., 'NFC', 'Eastern') | base | `NFC` |
| `{pro_conference_abbrev}` | Team's pro conference abbreviation | base | `NFC` |
| `{pro_division}` | Team's pro division (e.g., 'NFC North') | base | `NFC North` |
| `{opponent_pro_conference}` | Opponent's pro conference | base, .next, .last | `NFC` |
| `{opponent_pro_conference_abbrev}` | Opponent's pro conference abbreviation | base, .next, .last | `NFC` |
| `{opponent_pro_division}` | Opponent's pro division | base, .next, .last | `NFC North` |
| `{home_team_pro_conference}` | Home team's pro conference | base, .next, .last | `NFC` |
| `{home_team_pro_conference_abbrev}` | Home team's pro conference abbreviation | base, .next, .last | `NFC` |
| `{home_team_pro_division}` | Home team's pro division | base, .next, .last | `NFC North` |
| `{away_team_pro_conference}` | Away team's pro conference | base, .next, .last | `NFC` |
| `{away_team_pro_conference_abbrev}` | Away team's pro conference abbreviation | base, .next, .last | `NFC` |
| `{away_team_pro_division}` | Away team's pro division | base, .next, .last | `NFC North` |

### College Leagues

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{college_conference}` | Team's college conference name | base | `` |
| `{college_conference_abbrev}` | Team's college conference abbreviation | base | `` |
| `{opponent_college_conference}` | Opponent's college conference | base, .next, .last | `` |
| `{opponent_college_conference_abbrev}` | Opponent's college conference abbreviation | base, .next, .last | `` |
| `{home_team_college_conference}` | Home team's college conference | base, .next, .last | `` |
| `{home_team_college_conference_abbrev}` | Home team's college conference abbreviation | base, .next, .last | `` |
| `{away_team_college_conference}` | Away team's college conference | base, .next, .last | `` |
| `{away_team_college_conference_abbrev}` | Away team's college conference abbreviation | base, .next, .last | `` |

---

## Soccer

Soccer-specific variables for teams that play in multiple competitions.

| Variable | Description | Suffixes | Sample |
|----------|-------------|----------|--------|
| `{soccer_primary_league}` | Team's home league name (e.g., 'Premier League') | base | `` |
| `{soccer_primary_league_id}` | Team's home league ID (e.g., 'eng.1') | base | `` |
| `{soccer_match_league}` | League for THIS game (may differ from primary) | base, .next, .last | `` |
| `{soccer_match_league_name}` | Full league display name for THIS game | base, .next, .last | `English Premier League` |
| `{soccer_match_league_id}` | League ID for THIS game (e.g., 'uefa.champions') | base, .next, .last | `` |
| `{soccer_match_league_logo}` | Logo URL for THIS game's league | base, .next, .last | `` |
| `{soccer_match_note}` | Provider's competition note for the match, untouched — competition name plus group/stage where present | base, .next, .last | `FIFA World Cup, Group J` |

{: .note }
Unlike `{soccer_match_league_name}` (which Teamarr builds from its league cache), `{soccer_match_note}` is the provider's raw value and carries group-level detail. It's soccer-only and empty otherwise.

{: .note }
Soccer teams often play in multiple competitions (domestic league, cups, Champions League). The `soccer_match_league` variables tell you which competition a specific game is in, while `soccer_primary_league` is the team's home league.

---

## Combat Sports

Variables for combat-sport templates (UFC/MMA and boxing). Unlike Motorsports and Tennis (which are event-template-only), combat variables appear in **both** template types' pickers — but none support `.next`/`.last` suffixes, since each event is independent.

{: .note }
**Boxing support:** the fighter/matchup/title variables in this section work for boxing events too. Card segments, bout lists, fight results, records, and weight class only populate for UFC — that data comes from ESPN's UFC feed and TheSportsDB (which serves boxing) doesn't provide it. `{event_number}` is UFC-only by definition.

### Fighters & Matchup

| Variable | Description | Sample |
|----------|-------------|--------|
| `{fighter1}` | First fighter name (headline bout) | `Alex Volkanovski` |
| `{fighter2}` | Second fighter name (headline bout) | `Diego Lopes` |
| `{fighter1_last}` | First fighter surname | `Volkanovski` |
| `{fighter2_last}` | Second fighter surname | `Lopes` |
| `{matchup_combat}` | Fight matchup — headline fighter first with 'vs' | `Alex Volkanovski vs Diego Lopes` |
| `{event_number}` | UFC event number (e.g., '314' from 'UFC 314') | `314` |
| `{event_title}` | Full event title | `UFC 314: Volkanovski vs Lopes` |
| `{fighter1_record}` | Fighter 1's record | `28-4-0` |
| `{fighter2_record}` | Fighter 2's record | `27-8-0` |
| `{weight_class}` | Weight class of the headline bout | `Featherweight` |
| `{weight_class_short}` | Weight class abbreviated | `FW` |

### Card Segments

| Variable | Description | Sample |
|----------|-------------|--------|
| `{card_segment}` | Segment code for this channel | `main_card` |
| `{card_segment_display}` | Human-readable segment name | `Main Card` |
| `{main_card_time}` | Main card start time | `10:00 PM EST` |
| `{prelims_time}` | Prelims start time | `8:00 PM EST` |
| `{early_prelims_time}` | Early prelims start time | `6:00 PM EST` |

### Fight Card

| Variable | Description | Sample |
|----------|-------------|--------|
| `{bout_count}` | Total number of bouts on the card | `14` |
| `{fight_card}` | All bouts (newline-separated) | `Alex Volkanovski vs Diego Lopes`<br>`Merab Dvalishvili vs Umar Nurmagomedov` |
| `{main_card_bouts}` | Main card bouts only | `Alex Volkanovski vs Diego Lopes`<br>`Merab Dvalishvili vs Umar Nurmagomedov` |
| `{prelims_bouts}` | Prelims bouts only | `Sean Brady vs Kelvin Gastelum`<br>`Chris Weidman vs Eryk Anders` |
| `{early_prelims_bouts}` | Early prelims bouts only | `Mauricio Ruffy vs Jamie Mullarkey` |

### Fight Results

Result variables for the headline bout — empty until the fight is final. Ideal for postgame filler on a UFC template.

| Variable | Description | Sample |
|----------|-------------|--------|
| `{fight_result}` | Result method | `Decision (Unanimous)` |
| `{fight_result_short}` | Result abbreviated | `UD` |
| `{fight_summary}` | Full result summary | `TKO R2 4:31` |
| `{finish_round}` | Round the fight ended | `3` |
| `{finish_time}` | Time in round when the fight ended | `3:48` |
| `{finish_info}` | Combined finish info | `R3 3:48` |
| `{judge_scores}` | Judge scores for decisions | `48-47, 49-46, 48-47` |

{: .note }
UFC events are split into segments (Early Prelims, Prelims, Main Card). When using segment-based channel routing, each channel gets a `{card_segment}` value indicating which segment it covers. The `{fighter1}` and `{fighter2}` variables always refer to the headline (main event) bout. Use `{matchup_combat}` for the fight-conventional "Volkanovski vs Lopes" form — the generic `{matchup}` keeps the provider's title order for individual sports ("Lopes v Volkanovski").

---

## Motorsports

F1, NASCAR, IndyCar, and MotoGP-specific variables for event templates. These are **event-only** (no `.next`/`.last` suffixes) since each race weekend is independent.

### Event & Circuit

| Variable | Description | Sample |
|----------|-------------|--------|
| `{race_name}` | Race weekend / Grand Prix name | `Monaco Grand Prix` |
| `{circuit_name}` | Circuit/track name | `Circuit de Monaco` |

### Sessions

| Variable | Description | Sample |
|----------|-------------|--------|
| `{session_name}` | This channel's session display name | `Practice 1`, `Qualifying`, `Race` |
| `{session_type}` | This channel's session code | `fp1`, `qualifying`, `race` |
| `{next_session_name}` | Display name of the next session | `Qualifying` |
| `{next_session_time}` | Start time of the next session | `9:00 AM` |

### Race Format

NASCAR-style scheduled race format (lap counts and stages). Empty for series whose provider doesn't supply them (e.g. F1).

| Variable | Description | Sample |
|----------|-------------|--------|
| `{race_laps}` | Scheduled lap count | `200` |
| `{race_distance}` | Scheduled distance in miles | `500` |
| `{stage_1_laps}` | Cumulative lap where stage 1 ends | `60` |
| `{stage_2_laps}` | Cumulative lap where stage 2 ends | `125` |
| `{stage_3_laps}` | Cumulative lap where stage 3 ends | `200` |
| `{stage_summary}` | All stage endpoints joined | `60/125/200` |

### Grid & Qualifying

| Variable | Description | Sample |
|----------|-------------|--------|
| `{pole_position}` | Driver who took pole position | `Max Verstappen` |
| `{pole_team}` | Team/constructor of the pole sitter | `Red Bull Racing` |
| `{grid}` | Full starting grid order (newline-separated) | `1. Max Verstappen (Red Bull Racing)`<br>`2. Charles Leclerc (Ferrari)` |

### Results

| Variable | Description | Sample |
|----------|-------------|--------|
| `{race_winner}` | Race winner's name | `Max Verstappen` |
| `{podium_2}` | 2nd place finisher | `Charles Leclerc` |
| `{podium_3}` | 3rd place finisher | `Lewis Hamilton` |
| `{podium}` | Top 3 finishers, combined | `1. Max Verstappen, 2. Charles Leclerc, 3. Lewis Hamilton` |
| `{results}` | Full finishing order (newline-separated) | `1. Max Verstappen (Red Bull Racing)`<br>`2. Charles Leclerc (Ferrari)` |
| `{fastest_lap_driver}` | Driver awarded fastest lap | `Lando Norris` |

{: .note }
Race weekends are split into per-session channels (Practice 1/2/3, Qualifying, Sprint, Race). Each channel gets a `{session_type}`/`{session_name}` value indicating which session it covers. Grid and results variables read from the qualifying and race sessions respectively, and are empty until those sessions have data.

---

## Tennis

ATP/WTA-specific variables for event templates. Tennis is matched **per
match** — one channel per match with the players filling the standard
home/away variables (`{home_team}` = full name, `{home_team_abbrev}` =
surname). These variables add the tournament context and are **event-only**.

| Variable | Description | Sample |
|----------|-------------|--------|
| `{player1}` | First player/pair in the matchup (no home player in tennis) | `Flavio Cobolli` |
| `{player2}` | Second player/pair in the matchup | `Alex de Minaur` |
| `{player1_last}` | First player's surname (multi-word preserved) | `Cobolli` |
| `{player2_last}` | Second player's surname | `de Minaur` |
| `{tournament_name}` | Tournament name | `Wimbledon` |
| `{tournament_name_the}` | Tournament name with its natural article | `the US Open`, `Wimbledon` |
| `{tennis_round}` | Round within the draw | `Round 4`, `Quarterfinals` |
| `{tennis_court}` | Assigned court | `Centre Court`, `No. 1 Court` |
| `{tennis_draw}` | Draw type | `Men's Singles`, `Mixed Doubles` |
| `{tennis_result}` | Prose match result once final (empty before) | `Zverev defeats Fery 6-3, 6-4, 7-6(5)` |

{: .note }
Example: `{tournament_name} {tennis_draw}: {player1_last} vs {player2_last}` renders as `Wimbledon Men's Singles: Cobolli vs de Minaur`. Like combat's `{fighter1}`/`{fighter2}`, the player variables are the idiomatic choice — the underlying home/away team variables also resolve, but tennis has no real home player.

---

## Usage Examples

### Team Template (Detroit Lions channel)

```
Title: {team_name} {vs_at} {opponent}
→ "Detroit Lions vs Chicago Bears"

Description: The {team_name} ({team_record}) host the {opponent} ({opponent_record}) at {venue}. {today_tonight_title}'s game airs on {broadcast_network}.
→ "The Detroit Lions (10-4) host the Chicago Bears (8-6) at Ford Field. Today's game airs on FOX."
```

### Event Template (game-specific channel)

```
Title: {away_team} @ {home_team}
→ "Chicago Bears @ Detroit Lions"

Description: {away_team} ({away_team_record}) at {home_team} ({home_team_record}). {home_team} is a {odds_spread} favorite.
→ "Chicago Bears (8-6) at Detroit Lions (10-4). Detroit Lions is a -3.0 favorite."
```

### Postgame Filler (team template)

```
Title: {team_name} Postgame
Description: The {team_name} {result_text.last} the {opponent.last} {final_score.last} {overtime_text.last}.
→ "The Detroit Lions defeated the Minnesota Vikings 28-21."
```

### UFC Event Template

```
Title: {event_title} - {card_segment_display}
→ "UFC 314: Volkanovski vs Lopes - Main Card"

Description: {matchup}. Main card at {main_card_time}. {bout_count} total bouts.
→ "Alex Volkanovski vs Diego Lopes. Main card at 10:00 PM EST. 14 total bouts."
```
