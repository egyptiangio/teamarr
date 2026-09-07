# Agent Instructions - Teamarr

## Overview

Sports EPG generator. Uses **bd (beads)** for issue tracking. Start with `bd ready`.

## Support Bundle Contract

When changing support-bundle schemas, archive layout, collection limits, redaction, or signal codes, update the implementation, tests, user documentation, bundled `AGENTS.md`, and this instruction in the same change. Never add a generic database dump or relax exclusions for stream URLs, M3U account names, credentials, or tokens without an explicit security decision. Redaction is key-name based and recurses into JSON-typed columns (`emby_servers`, `jellyfin_servers`, …) — a new column that stores nested credentials as text is covered only because `_sanitize` parses JSON strings (#686); never bypass that path.

## CRITICAL: Database Safety

**NEVER delete `teamarr.db` or `data/teamarr.db`.** The database contains user-configured teams, templates, settings, and history that cannot be recreated. Schema changes use migrations (`INSERT OR REPLACE`, `ALTER TABLE`) - deleting the database is NEVER required and will cause data loss.

**Stack**: Python 3.11+, FastAPI, SQLite | Frontend: React + TypeScript + Vite + Tailwind

## Start of Session

1. Re-read this file and follow it exactly
2. Switch to `dev` branch: `git checkout dev && git pull`
3. Check for work: `bd ready`

If you forget this workflow after a context compaction, re-read this file before continuing.

## Local Testing

Run `./dev.sh` to start both servers in one terminal:

```bash
./dev.sh                 # fast restart — skips cache refresh
./dev.sh --update-cache  # restart with full cache refresh
```

- **Backend** (FastAPI): `http://localhost:9195` — Python venv, `app.py`
- **Frontend** (Vite HMR): `http://localhost:5173` — proxies `/api` → `:9195`

Use `:5173` during development for hot-reload. `Ctrl+C` stops both.
Re-running `./dev.sh` kills existing servers first, so it doubles as a restart.

By default the script skips the startup cache refresh for fast restarts. Pass `--update-cache` when you need fresh team/league data from providers. Cache can also be refreshed manually via the UI button.

**Always use `./dev.sh` to start or restart the dev environment.** It handles cleanup of old processes automatically.

**When to restart:**
- After making backend (Python) code changes
- If Playwright browser automation can't connect to `localhost:5173`
- After schema or configuration changes

## Quick Reference Commands

```bash
bd ready                              # Find available work
bd show <id>                          # View issue details
bd update <id> --status in_progress   # Claim work
bd close <id>                         # Complete work
bd doctor                             # Check beads health (sync issues, hooks)
```

**Beads sync is Dolt, not JSONL.** The shared source of truth is `refs/dolt/data`
on origin (set up 2026-08-29). `bd dolt pull` at session start, `bd dolt push` at
session end — **every machine, every session**, including co-devs. `.beads/*.jsonl`
is a passive export: never `bd import` it as a sync step, and its presence in a
PR does not carry beads across machines. A fresh clone gets beads with
`bd bootstrap`. A machine whose Dolt history has diverged (push says "no common
ancestor") must `bd export -o mine.jsonl` → `bd bootstrap` → `bd import -i
mine.jsonl` → `bd dolt push` — never `--force` from a non-maintainer machine.

## Development Workflow (issue-first — MANDATORY)

**Nothing gets implemented without a GitHub issue.** Every feature, bug fix, and refactor — including internally-discovered work — follows this lifecycle. No coding straight onto `dev`.

### The Lifecycle: issue → bead → claim → branch → PR → dev → release

1. **Issue first** — `gh issue create` (or an existing community issue). Applies to internal work too. *Sole exception:* trivial bookkeeping (beads sync, typo, one-line doc fix) may batch under a standing "Housekeeping" issue for the release cycle instead of individual issues.
2. **Bead it** — `bd create` referencing the issue: put `(#NNN)` in the bead title. Larger features get an epic + child beads (see Roadmap & Feature Planning). Comment the bead id on the issue so the two stay linked.
3. **Claim** — `bd update <id> --status in_progress` BEFORE writing code.
4. **Branch** — from up-to-date dev:
   ```bash
   git checkout dev && git pull
   git checkout -b <type>/<issue#>-<slug>    # type ∈ feat|fix|refactor|chore|docs
   ```
5. **Implement on the branch** — code, then docs-impact evaluation (MANDATORY — check every change against the Documentation Updates table below; doc updates ship in the same branch), then quality gates (MANDATORY):
   ```bash
   ruff check teamarr/ tests/
   pytest tests/ -v
   cd frontend && npm run build
   ```
6. **Open a PR to dev when gates are green** — CI (`test.yml`, `dependency-review`) runs **only on `pull_request`**, so a direct merge to dev skips the test gate. Internal work goes through a PR too:
   ```bash
   git push -u origin <branch>
   gh pr create --base dev --fill
   ```
   Wait for checks green, then merge via GitHub: `gh pr merge <#> --merge --delete-branch`. Then comment the dev-land hash on the issue, add the `status: on-dev` label (**keep the issue open** — it closes at release), and close the bead.
   *Exception:* trivial bookkeeping (beads sync, typo, one-line doc fix) may merge straight to dev under the standing Housekeeping issue — no PR needed.
7. **Release to main in batches** — `dev → main` happens ONLY via the Release Workflow below. **Release triggers** (any one): a user-facing regression fix is waiting on dev · ~2–3 weeks since the last release · dev is ≥25 commits ahead of main. Dev must never again pile up 100+ unreleased commits.
8. **At release** — close every `status: on-dev` issue with the release link.

### Inbound community PRs

- Triage every new PR promptly: comment with an assessment and expected timeline.
- Review-ready PRs get attention **before** starting new self-initiated work.
- Merge via GitHub when possible; take-and-fix with credit when conflicts force it. Always credit contributors in the changelog (`— thanks @user (#PR)`).

### Session rules

- **Start:** `git checkout dev && git pull` · `bd dolt pull` · `bd ready` · `gh pr list` (triage anything new).
- **End:** `bd dolt push` · everything committed AND pushed (work is incomplete until push succeeds — never stop before pushing, never say "ready to push when you are"); report whether dev currently meets a release trigger.

### Roadmap & Feature Planning

Use beads epics to plan larger features:

```bash
bd create "Feature name" --type epic --label roadmap
bd create "Implementation step 1" --parent <epic-id>
bd create "Implementation step 2" --parent <epic-id>
bd dep add <step2-id> <step1-id>    # step 2 blocked by step 1
```

When asked to plan a feature, create an epic with implementation beads that have proper blockers and predecessors. Use `bd list --label roadmap` to see the roadmap.

### Release Workflow (`/release`)

When the user says **"release"**, **"/release"**, or **"version bump"**, execute this workflow:

1. **Determine scope** — `git log origin/main..origin/dev --oneline` to see all commits in the release
2. **Ask version** — suggest patch (x.y.Z) vs minor (x.Y.0) based on scope. User decides.
3. **Quality gates** (MANDATORY):
   ```bash
   source .venv/bin/activate
   ruff check teamarr/ tests/
   pytest tests/ -v
   cd frontend && npm run build
   ```
4. **Version bump** — edit `pyproject.toml` line 7, commit "Bump version to x.y.z"
5. **Push dev** — `git push origin dev`
6. **Merge to main** — fast-forward merge:
   ```bash
   git checkout main && git pull origin main
   git merge dev --no-edit
   git push origin main
   git checkout dev
   ```
7. **Create GitHub release** — `gh release create v<version> --repo Pharaoh-Labs/teamarr --target main` with summarized release notes (not commit-by-commit — group into categories). CI notes: the tag push triggers `release.yml`, which sees the release already exists and skips (it only auto-creates releases for raw tag pushes); Docker publish and release are gated on the Tests workflow and on the tag matching `pyproject.toml`'s version — a mismatched tag will not publish.
8. **Generate Discord changelog** — use the Release Template below, output ready to paste
9. **Update plans/STATUS.md** — add release to changelog, update version

**Push-button alternative (`Cut Release` workflow, #281):** Actions tab → "Cut Release" → Run workflow from `dev`, pick patch/minor/major (or an explicit version). CI re-runs the gates, bumps `pyproject.toml` + `uv.lock`, pushes the bump to dev, fast-forwards main, pushes the tag, opens a **draft** release, and dispatches the Docker publishes for `main` and the tag (explicit dispatch — `GITHUB_TOKEN` pushes don't trigger workflows). Steps 7–9 stay manual: write curated notes on the draft and publish it, generate the Discord changelog, update `plans/STATUS.md`. After a Cut Release run, `git pull` locally — the bump commit lands on dev via the bot.

**Rules:**
- Never release with failing tests or lint errors
- Release notes should be human-readable summaries, not raw commit messages
- Group related commits into single bullet points

## Changelog Format

When asked for a changelog, **always** produce Discord-ready markdown. Two templates:

### Dev Push Template

Get version from `pyproject.toml` line 7, append `-dev+<short_hash>` of HEAD commit.

```
## 🚀 v<version>-dev+<hash> — <YYYY-MM-DD>

🐛 **Bug Fixes**
- <one-liner> (#issue) (`hash`)

✨ **New Features**
- <one-liner> (#issue) (`hash`)

⚡ **Enhancements**
- <one-liner> (#issue) (`hash`)

🎨 **UI/UX**
- <one-liner> (#issue) (`hash`)

🔧 **Under the Hood**
- <one-liner> — thanks @contributor (#PR) (`hash`)
```

### Release Template

Identical sections to the Dev Push Template, with two differences: the header is `## 🎉 v<version> — <YYYY-MM-DD>` and items carry **no commit hashes** (releases omit them).

### Rules
- Discord markdown (## headers, **bold**, \`code\`)
- Categories (in order): 🐛 Bug Fixes, ✨ New Features, ⚡ Enhancements, 🎨 UI/UX, 🔧 Under the Hood
- **Omit empty categories** — only include sections that have items
- Dev pushes include commit hashes; releases do not
- **ALWAYS include issue numbers** — append `(#123)` to items that close or relate to a GitHub issue
- **ALWAYS credit contributors** — append `— thanks @username (#PR)` for community PR contributions
- Each item is one concise line — no multi-line descriptions
- No extra commentary — just the changelog block ready to paste

## Git Remote & Preferences

**Single remote:**
| Remote | Repo | Purpose |
|--------|------|---------|
| `origin` | `Pharaoh-Labs/teamarr` | All development, releases, and PRs |

**Rules:**
- Push to `origin dev` after completing work
- No commit watermarks or co-authored-by
- Concise, focused commit messages

## Documentation Updates

**Part of every development cycle.** After implementing any change, check this table *before* running quality gates. If a row matches, updating the listed docs is as much a part of the task as the code itself — don't defer it to a future sweep and don't wait for the user to prompt. Docs commits can ride in the same commit as the code or a paired follow-up; either way, they ship together.

| Change Type | Update |
|-------------|--------|
| New/renamed/removed template variable | `teamarr/templates/variables/` docstring AND `docs/guide/epg/variables.md` AND variable-count claims in `docs/guide/epg/variables.md`, `docs/reference/architecture/template-engine.md`, `docs/index.md`, and `CLAUDE.md` ("Key Subsystems") |
| New/renamed/removed condition evaluator | `teamarr/templates/conditions.py` docstring AND `docs/guide/epg/conditions.md` AND condition-count claims |
| New/renamed/removed league | `INSERT OR REPLACE INTO leagues` in `schema.sql` AND the appropriate sport section of `docs/reference/supported-leagues.md` AND league-count claims in `docs/reference/supported-leagues.md`, `docs/reference/index.md`, `docs/index.md`, and `docs/reference/providers/<provider>.md` |
| New/renamed sport | `INSERT INTO sports` in `schema.sql` AND `docs/reference/supported-leagues.md` sport list AND sport-count claims |
| New API endpoint | Route docstring AND OpenAPI (auto) AND relevant `docs/reference/architecture/*.md` if the endpoint shape changes subsystem behavior |
| New column | `CREATE TABLE` in `schema.sql` (reconciliation handles upgrades); no doc update needed unless the column is user-visible |
| Data migration | Versioned block in `_run_migrations()`, bump `schema_version` DEFAULT, AND update schema version in `docs/reference/architecture/migrations.md` |
| New provider | Architecture section in this file AND `docs/reference/providers/<name>.md` (new file) AND `docs/reference/supported-leagues.md` provider table |
| Config/settings change | `README.md` if user-facing, AND relevant `docs/guide/settings/*.md` page |
| New feature (user-visible) | README Features section AND appropriate `docs/guide/**` page; consider creating a guide page if substantial |
| Feature removal | Remove from docs (don't leave stale references); add to release notes |

If the change touches a count referenced in docs (variables, leagues, sports, conditions, schema version), grep for the old number across `docs/`, `CLAUDE.md`, and `README.md` — don't fix just one occurrence.

Documentation epic: `bd list --parent teamarrv2-nv4`

## Single Source of Truth

| What | Where |
|------|-------|
| Version | `pyproject.toml` line 7 |
| Dependencies | `pyproject.toml` (ranges) + `uv.lock` (pinned, used by the Docker build) — run `uv lock` after any dependency change or `--frozen` builds fail |
| League configs | `teamarr/database/schema.sql` |
| Schema version | `teamarr/database/schema.sql` (v92) |
| Schema reconciliation | `teamarr/database/reconciliation.py` |
| Provider registration | `teamarr/providers/__init__.py` |

## Architecture

```
API Layer        → teamarr/api/routes/ (18 modules)
Consumer Layer   → teamarr/consumers/ (key packages: generation, team_epg, event_epg, event_group_processor/, cache/, lifecycle/, matching/, enforcement/, filler/)
Service Layer    → teamarr/services/sports_data.py
Provider Layer   → teamarr/providers/ (espn, bellmedia, squiggle, nascar, mlbstats, hockeytech, supabase, tsdb)
```

**Providers** (lower priority = tried first):
- ESPN (0) - Primary, most leagues
- Bell Media (20) - CFL; TSN public sports widget API, no key
- Squiggle (30) - AFL (Australian Football League); free, no key required
- NASCAR (35) - NASCAR Cup/O'Reilly (Xfinity)/Trucks; official cf.nascar.com schedule API, full weekend sessions, no key
- MLB Stats (40) - MiLB (Triple-A through Rookie)
- HockeyTech (50) - CHL, AHL, PWHL, USHL
- Supabase (55) - Supabase-backed leagues (CBL, etc.)
- TSDB (100) - Cricket, rugby, boxing, Scandinavian leagues, uru.2 — premium key required (#676); keyless = provider not registered

**Dispatcharr Sync Reliability** (`lifecycle/service.py`):
All `update_channel` calls go through `_safe_update_channel`, which checks `OperationResult.success` before persisting to local DB. On API failure, the DB stays unchanged so drift is re-detected on the next generation run. Profile sync also compares against Dispatcharr's actual state (`current_channel.channel_profile_ids`) for self-healing. Reconciliation (`reconciliation.py`) detects stream and profile drift as additional drift fields.

**Dispatcharr catalogs are memoized for the UI (#736).** `M3UManager.list_accounts` and `list_groups` hold a 60s memo (`_ACCOUNTS_TTL_SECONDS` / `_GROUPS_TTL_SECONDS`); the Sources list called both live on every page load, and profiling put 93% of `/api/v1/groups` inside `list_accounts` alone. A **failed** fetch is never cached (a blip must not blank account names for a minute), the memo holds the *unfiltered* list so `include_custom` / `search` / `exclude_m3u` still apply per call, and mutations invalidate explicitly — add an `invalidate_accounts_cache()` / `_groups_fetched_at = None` to any new write path against those endpoints. Connection status has its own probe: `DispatcharrFactory.probe_connection` makes ONE request on the **pooled** connection and memoizes the verdict for 15s, keyed on the same settings hash `get_connection` reconnects on. Do not point the status poll back at `test_connection` — that builds a throwaway client and makes three calls, one of which pulls every channel group just to count it (1.09s per poll, every 30s, per open tab). `test_connection` stays as-is for the Test button, which wants a real uncached round trip and those counts.

**Stream order converges every run (#712).** Array order in a `{"streams": [...]}` push IS the channel's stream priority, so every comparison of DB vs Dispatcharr streams must be *ordered* — a `set()` or `sorted()` on either side silently blinds it. `_apply_stream_ordering` reads Dispatcharr's real order once per run (`channel_mgr.get_channels()`, cached) and pushes on any difference; gating on local priority *change* alone was the #712 bug, since priorities are computed at insert time and so never change on a steady-state channel. Reconciliation's drift `expected` must be the priority order — it is written straight back to Dispatcharr by the auto-fix. The one legitimate DB↔Dispatcharr order difference is the live-event #1 pin (#232), which both the audit and reconciliation exempt while the event is live.

**`_apply_stream_ordering` runs in three phases (#735) — keep them separate.** Phase 1 (serial, one DB connection) recomputes and persists priorities and builds an `_OrderingPlan` per channel; phase 2 (still serial, still DB) takes ONE `get_all_ordered_stream_ids` scan for the post-update active sets and decides what to push; phase 3 runs the PATCHes on a bounded pool (`_ORDERING_PUSH_WORKERS`) **outside** the `with db_factory()` block, so no thread ever touches the connection. Do not move a Dispatcharr call back into the channel loop, and do not move a DB write into the push helper. The bulk reads (`get_all_channel_streams` / `get_all_ordered_stream_ids`) replace two queries per channel; the second takes an explicit `now` so every channel's attach/detach window is evaluated at one instant rather than re-reading the clock per channel. `paginated_get` fetches pages 2..N concurrently once page 1 reports a usable `count`, and on a failed page returns a contiguous **prefix**, never a list with a hole in it — callers cannot tell a hole from a smaller collection. Page size is measured from page 1's length, never assumed from the query string: `/api/epg/epgdata/` ignores `page_size` and answers with every row at once.

## Key Subsystems

**Template Engine** (`teamarr/templates/`):
- 258 variables in `variables/` (20 categories); chainable `|filter` transforms in `filters.py` (lower/upper/title/pascal/slug/urlencode) with permanent legacy aliases for 10 retired transform variables
- 33 condition evaluators in `conditions.py`
- Suffix rules: `.next`, `.last` for multi-game scenarios
- Template scope: each variable is tagged `TemplateScope.ALL` / `TEAM_ONLY` / `EVENT_ONLY` — gates variable picker by template type via `GET /variables?template_type=…`

**Settings Registry** (`teamarr/database/settings/`, bead `teamarrv2-iua3.8`):
- Each setting is declared once: a typed dataclass field in `types.py` plus a column/JSON/hook binding in `registry.py` (`GROUPS`). `read.py` and `update.py` are generic (registry-driven); group-specific behavior (validation, relayout arming, clear-to-NULL, `_NOT_PROVIDED` sentinels) lives in the update wrappers — public signatures are stable, don't change them without auditing callers.
- Adding a setting: add the column to `schema.sql` + the field to its dataclass; touch `registry.py` only if the column name differs from the field name or it needs JSON/custom parse/dump hooks. Parity tests (`tests/test_settings_registry.py`) enforce schema ↔ registry ↔ dataclass ↔ Pydantic alignment.
- API routes build responses with `to_model(Model, dataclass)` from `api/routes/settings/models.py`; frontend hooks are factory-generated with scoped cache invalidation (`frontend/src/hooks/useSettings.ts`).

**Dynamic Groups** (`teamarr/consumers/lifecycle/dynamic_resolver.py`):
- `{sport}`, `{league}`, and `{conference}` wildcards (`{conference}` = home team's NCAA conference from `provider_group_cache`, #91)
- Auto-creates in Dispatcharr

**Per-Source Matching Types** (epic `teamarrv2-ahow`):
- Each source declares which matching pipeline(s) it runs — three independent booleans on `event_epg_groups`: `name_match_enabled` (Stream Name → TEAM_VS_TEAM/EVENT_CARD/RACING categories), `team_streams_enabled` (Team → TEAM_ONLY), `epg_match_enabled` (EPG). Multi-select; ≥1 required (enforced in `api/routes/groups.py::require_matching_type`).
- Gating is by **category at the matcher router** (`matcher.py::_match_single`, reason `name_match_disabled`) — classification always runs so the types stay independent; never skip `classify_stream`. `name_match_enabled` defaults 1 (DEFAULT-1 column backfills existing sources). The hidden `is_channel_source` group is name-off (EPG/team only).
- UI: three toggles on add/edit/bulk-add/bulk-edit; color-coded Sources badges (Stream Name=sky, Team=emerald, EPG=violet). The Matched-column coverage % shows only when Stream Name is on (Team/EPG fan one stream → many events).

**Fixture Gate** (epic `teamarrv2-goax`, `teamarr/consumers/matching/identity.py`):
- Cross-sport false positives came from `token_set_ratio` weighing every token equally, so a shared **city** cleared `BOTH_TEAMS_THRESHOLD` (60) on its own — "Tampa Bay Lightning"/"Tampa Bay Rays" = 78.3. 161 such cross-league pairs exist in the 6 major pro leagues alone; "New York Mets"/"New York Jets" = 92.3.
- `TeamIdentityIndex` resolves each stream side against the **global** `team_cache` (all leagues, incl. unconfigured ones) and yields the leagues where both sides could actually meet. `_match_against_candidates` skips candidates outside that set → `FailedReason.FIXTURE_NOT_IN_LEAGUE`.
- **Veto-only, never a selector** — resolution is a strong negative signal and a weak positive one (`D-backs` resolves to "ACL D-backs"; `SF Giants` and `NY Giants` give the same 4-way tie). Ties are kept, not collapsed. Returns `None` (defer) whenever it cannot speak, so an unseeded cache is inert.
- **Only a full name is an exact identity (#619).** `team_short_name` is the bare city/school for college, MLS, NWSL and most non-US rows ("Milwaukee" = Milwaukee Panthers, "Atlanta" = Atlanta United), and TSDB stores the code as the short name ("SEA" = Seattle Orcas) — every one of those is also a normal broadcast label for the pro team. Short names, the city prefix of a full name ("new york" from "New York Mets"), and every ≥2-token leading run of a full name ("fairmont state" from "Fairmont State Falcons", #650) are *partial* readings: they widen the identity set, never narrow it. Short codes union the abbreviation table with any such row and are never exact; a bare-city `TEAM_ALIASES` key ("atlanta" → "atlanta united") is exact only when the text has no partial reading. The matcher also never vetoes a league the index has no teams for (`knows_league`).
- **Mascotless leagues must not shadow mascoted ones (#650).** NCAA soccer (`usa.ncaa.w.1`/`usa.ncaa.m.1`, ~490 rows) publishes no mascots, so its full name IS the bare school — an *exact* identity for "Fairmont State". ESPN abbreviates the SCHOOL for college ("Fairmont State Falcons" → "Fairmont St"), never the mascot, so the short-name prefix rule never fires and the football row had no route back from the school-only form. One such side narrowed the fixture to soccer and vetoed `college-football` for **20 of 73** games on the 2026-08-29 slate (512 of 1026 failures in a support bundle). Fixed by registering every ≥2-token prefix of a full name as a partial reading; prefixes stop at two tokens so a bare "north"/"saint" never enters thousands of teams.
- **One candidate loop (#660).** `_match_against_events` (single-league) and `_match_against_multi_league_events` are thin wrappers over `_match_against_candidates`; they used to be two 89%-identical copies and the #627 league-hint hatch landed in only one of them, which is how #650's single-league NCAAF sources kept vetoing. `TestPathParity` pins both entry points to the same verdict — add gates/fallbacks to the shared body, never to a wrapper.
- No schedule lookup and no new API calls: the candidate event's own existence IS the schedule evidence.
- Video-quality tags (`[1080p]`, `720p`, `(4K)`, `FHD`) are stripped from the whole stream name in `normalize_stream` before prefix handling and again at the ends of each team name in `_clean_team_name`; `_discriminating` ignores resolution tokens so a stray one can never veto (#651 — one tagged source matched 0/30, its untagged twin 21/30).
- `residual_contradicts` is the fallback for unresolvable names — generalizes `_short_name_leg_is_safe` (#569) to the full-name leg, ignoring non-discriminating residuals (club suffixes, ≤2-char noise) so "us seattle sounders a" still reaches the Sounders.
- Measured in `tests/matching/test_fixture_corpus.py`: **0 false vetoes / 200**, **322/322 crosstalk rejected**. Regenerate the corpus with `tests/matching/corpus/build_corpus.py`.
- **Tennis gate (#283, `tennis_matcher.py`)** — same veto-only shape, no alias table: a stream that names a pooled tournament (distinctive ESPN name tokens; generic open/cup/masters ignored) vetoes candidates from other tournaments → `FailedReason.TENNIS_TOURNAMENT_MISMATCH`; a stream naming none defers. Keyed on `Event.tournament_id` (season-stable ESPN id, threaded through both caches). Draw shape is validated per side: doubles pairs (`abbreviation` "A/B") match exact-only because `token_set_ratio("sinner", "Sinner/Sonego")` = 100, and a side written as a pair (`/`, `&`) never matches a singles player; `_` defers. Tournament tier selection beyond majors/all and include/exclude lists were deliberately rejected (maintenance).
- **Per-court feeds (#689, US Open 2026 live data).** ESPN+ carries a slam as one stream per court with nothing else in the name (`ESPN+ 17: Arthur Ashe Stadium @ Sep 01 11:30AM ET`); TSN+ as `US Open: Day #1 - Court 7 (ft. …)` (and `Louis Armstong Stadium`, sic). Three stacked causes, all fixed: (1) `_COURT_PATTERNS` knew only Wimbledon shapes — named show courts (`ashe`/`armstrong`/`grandstand`) and `Stadium N` → `N` added, keys shared with ESPN's `venue.court`; (2) mixed groups never reached the tennis path — `_try_mixed_group_fallbacks` (racing, then tennis) runs after a failed primary route when the group has a tennis league and the text names a **court** (never a round: "final" is everywhere), then `match_feed` still has to join that court on the day's slate; the EPG path gates on `names_tournament` and `match_program` still demands pair-or-court; (3) the normalizer's reversed `DD @ Mon` pattern ate `Court 12 @ Sep 01` as Sep 12 — it now yields when the month is followed by its own day number. Also fixed: `_named_tournaments` reduced "US Open" to the lone token `us`, so every `US:`-prefixed stream vetoed all other tournaments — distinctive tokens are ≥3 chars, or the full name as a phrase.

**EPG Program Matching** (epic `teamarrv2-183`, `teamarr/consumers/matching/epg_*.py`):
- Matches static-named linear channels (ESPN, FS1) to events via Dispatcharr's program guide (`GET /api/epg/programs/search/`, feature-detected, Dispatcharr 0.24.0+), then time-shares one stream across many event channels (attach/detach window per program).
- Opt-in: per-group `epg_match_enabled` only (no global switch as of eqz/3lp1 — EPG matching is always available; each event-group opts in). Global tuning (attach/detach buffers, `epg_stream_pre/post_buffer_minutes`, default 60) lives on the **Matching** page (`/matching`, `EpgMatchingSettings` component) as of the v2.7.0 IA overhaul — not Settings. Per-group flag also sets `skip_builtin` so static names survive filtering.
- Channel-source mode (183.9, `epg_channel_source_enabled`): additive source from streams curated onto Dispatcharr channels (each channel's own EPG), run as a hidden system group (`is_channel_source`, `ensure_channel_source_group`); excludes Teamarr's own channels and dedupes streams already in EPG-match M3U groups. Candidate builder: `_fetch_channel_source_streams`.
- `epg_resolver.py` bridges the stream `tvg_id` → program `tvg_id` namespace gap via a cascade: direct tvg_id → curated channel `epg_data_id` → strict name match (does NOT require an EPG-linked channel). `_Teamarr` source excluded.
- **Resolution inputs are run-scoped, not per-group (#734).** `_build_epg_index` runs once per event group, but the EPGData catalog, the stream→channel maps, the active-source set and the derived `EpgCatalogIndex` depend on the Dispatcharr install alone. `StreamMatching._epg_resolution_inputs` fetches and indexes them once and `process_all_groups` clears the memo alongside `_shared_events`. Keep it that way: `/api/epg/epgdata/` ignores `page_size` and answers with every row in one response (50k+ on a real install ≈ 1.5s), and rebuilding the catalog index costs ~220ms — per group, both of them, before #734. A failed fetch is deliberately NOT cached, so one blip can't disable EPG matching for the rest of the run.
- `epg_index.py` fetches by resolved tvg_id, keys by stream tvg_id; `epg_matcher.py` routes program title+sub_title (pipe-joined) through `classify_stream → TeamMatcher`.
- `MatchMethod.EPG` persisted to `managed_channel_streams.match_method` → drives the `epg_match` stream-ordering rule. EPG-matched groups show an "EPG Matched" badge.
- Tennis programmes (mf7.9, #642): `TennisMatcher.match_program` — binds only with a tournament clue AND (player pair OR court) from title|sub_title|description; pair → one match, court → that court's matches inside the programme slot; otherwise `FailedReason.TENNIS_MATCHUP_UNKNOWN`, surfaced on the linear stream's result via `_epg_tennis_unknown` in `_reconcile_epg`. Never a tournament-wide fan-out (the 2026-07-05 regression).
- Docs: `docs/guide/matching/program-matching.md`.

**Failure taxonomy** (`epg_failed_matches.reason`, #661/#662/#683): a real `FailedReason` value, or a prefixed verdict — `filtered:<FilteredReason>` (not_event, league_not_included, regex, stale) and `skipped:<exclusion>` (unclassifiable linear names, name_match_disabled, team_streams_disabled). Bare `"unmatched"` is the unreachable last resort. EPG-path misses (#683): a linear stream whose guide programmes were attempted but bound nothing gets `no_epg_program_match` with a programme summary in `detail` (counts + sample titles, recorded per tvg_id in `_compute_epg_plan`, applied in `_reconcile_epg`); tennis-unknown sets `tennis_matchup_unknown` as a real reason now (the old exclusion_reason overwrite persisted as bare unmatched). A specific name-path verdict is never overridden. `candidates_gated` = every candidate was skipped before scoring (search window / EPG anchor / sport hint); `no_event_found` = candidates were scored and none cleared the floor. `detail` carries the near-miss summary over *scored* candidates only; `exclusion_reason` rides alongside. Frontend labels: `RunHistoryTable.tsx::getFailedReasonLabel`.

## Plans & Roadmap

Feature planning lives in beads: `bd list --label roadmap`

Legacy plans in `plans/` (gitignored) may have additional context.

## Code Health Audit

**On-demand, not scheduled.** Run with: `audit`

The cyclical epic (`teamarrv2-5hq`) was **retired 2026-08-23** — the quarterly cadence collapsed after Apr 2026 and its function migrated to continuous `# TODO: PRUNE/REFACTOR` markers during normal work plus on-demand `/code-review` and `/simplify`. Do NOT create recurring audit beads. File findings as their own beads.

When the user says **"audit"**, run the full sweep:

1. **Dead API endpoints** — cross-reference every route in `teamarr/api/routes/` against the ENTIRE `frontend/src/` directory (not just `api/` — the frontend uses both structured api clients AND direct `fetch()` calls in pages/components) and backend callers. Only flag as dead if zero hits across all search patterns.
2. **Dead frontend code** — find unused exports in `frontend/src/api/`, `frontend/src/hooks/`, `frontend/src/components/`. Check for dynamic imports and lazy loading in `App.tsx` before flagging components as dead.
3. **Layer separation** — routes should only do request/response; no direct DB queries (`conn.execute`, `cursor`) in routes. Business logic belongs in services/consumers.
4. **Code quality** — god functions (200+ lines), deep nesting (4+ levels), inconsistent logging, magic numbers.
5. **Frontend hygiene** — unused components, dead hooks, stale API client functions.

6. **Test coverage before pruning** — before removing ANY code marked for pruning, verify:
   - Run `pytest tests/ -v` to confirm all existing tests pass first.
   - Search for callers/importers one more time (grep the entire codebase, not just obvious locations).
   - Check git blame — if code was added recently, it may be WIP or needed for an upcoming feature. Ask the user before removing.
   - After pruning, run `pytest tests/ -v` again and `cd frontend && npm run build` to confirm nothing broke.
   - If removing an API endpoint, also check for external consumers (Dispatcharr callbacks, webhook URLs, cron jobs calling the API).
   - **Never prune comments that explain WHY something works a certain way** — only remove commented-out dead code.
   - When in doubt, leave it and mark with `# TODO: PRUNE? — verify with user` instead of removing.

**Evaluation principles (apply these when deciding if code is dead or pruneable):**
- **"Zero callers" is necessary but not sufficient.** Also ask: does removing it lose any capability? If another endpoint/function covers the same functionality, it's safe. If it's the only way to do something, be cautious even if nothing calls it today.
- **Duplicate endpoints:** When GET and POST versions exist doing the same thing, the POST (superset — accepts optional body) is the keeper. The GET adds no unique capability.
- **Consider external consumers** that won't show up in code search: browser bookmarks, monitoring scripts, curl commands, Dispatcharr callbacks, Docker healthchecks, cron jobs. GET endpoints are especially exposed since they're URL-accessible.
- **Frontend has two calling patterns:** structured api clients (`frontend/src/api/*.ts`) and direct `fetch()` calls in pages/components. Always search the ENTIRE `frontend/src/` for URL path strings.
- **Never trust automated dead-code detection without manual verification.** The Q1 2026 audit had a high false-positive rate because agents only searched api client files, missing direct `fetch()` calls.
- **"Is it called?" is the wrong question. "Would we lose capability?" is the right one.**

**Ongoing responsibilities (during normal development):**
- When you encounter dead code while working on features/bugs, mark it with `# TODO: PRUNE — <reason>` immediately.
- When you notice layer violations or code smell, add `# TODO: REFACTOR — <reason>`.
- These TODO markers are the standing backlog — they get cleaned up at the next `audit` run, whenever the user calls one.
- After each audit, update these evaluation principles with any new lessons learned.

**Prior audit history (14 passes, Feb–Apr 2026):** `bd show teamarrv2-5hq`

## Sync Status

When asked to **"sync status"** or **"update status"**:

**Principle: one source of truth per fact.** GitHub owns issue/PR state (via labels), beads own work state, `plans/STATUS.md` owns only judgment (priorities, next steps, standing facts). Never transcribe into STATUS.md anything that `gh`/`bd` can derive live.

**Label vocabulary** (issue state lives HERE, not in prose — triagers maintain these too). Status labels track the work lifecycle; `type:` labels classify the change; two special labels flag ownership/blockers:
| Label | Meaning |
|-------|---------|
| `status: needs-triage` | No assessment or bead yet |
| `status: needs-bead` | Triaged; needs a bead before work |
| `status: ready` | Bead created; queued for work |
| `status: on-dev` | Landed on dev; closes at next release. `gh issue list --label "status: on-dev"` = release checklist |
| `status: released` | Shipped in a release |
| `contributor-led` | Community contributor driving implementation |
| `research` | Blocked on research / data-source discovery |
| `type: process` | Standing process/housekeeping issue (also `type:` bug/feature/enhancement/docs/chore/refactor/league) |

**The sync:**
1. Query live state: `gh issue list --state open`, `gh pr list`, `bd list -n 300` (beware default 50-row cap), read comment threads on anything that changed
2. Reconcile labels — new untriaged issues get `status: needs-triage`; dev-landed fixes get `status: on-dev`; fix wrong/missing labels
3. Cross-reference issues ↔ beads; file beads for triaged issues that lack them (`(#NNN)` in bead title)
4. **Rewrite `plans/STATUS.md` from scratch** (target ≤80 lines): header (version, dev-ahead count, release-trigger check, open counts), Needs Attention (≤10 curated rows of judgment), Next Work queue, Standing Facts, last 3 changelog entries. Never append-and-patch — full regeneration makes drift impossible. Prior history lives in `plans/archive/`.
5. Present summary; state whether a release trigger is met

## Adding a New League

Add to `INSERT OR REPLACE INTO leagues` in `teamarr/database/schema.sql`. Restart to apply.

## Database Schema Changes

**Adding a new column:** Just add it to the `CREATE TABLE` in `schema.sql`. Schema reconciliation (`teamarr/database/reconciliation.py`) automatically detects and adds missing columns on startup by comparing the real database against an in-memory reference built from `schema.sql`. No migration block needed.

**Data migration (transforming existing data):** Add a versioned `if current_version < N:` block in `_run_migrations()` in `database/migrations/versioned.py`. Bump the `schema_version DEFAULT` in `schema.sql`. Column additions in mixed blocks should use `_add_column_if_not_exists` as a safety net for tests that call `_run_migrations` directly.

**Table rebuild (CHECK constraint changes):** Add a pre-migration function in `init_db()` that backs up the table, drops it, and lets `executescript` recreate it. Add a restore block in `_run_migrations` keyed on the backup table's existence. See `_migrate_settings_for_v65` as the pattern.

**Startup order:** `init_db` → verify integrity → structural pre-migrations → reconcile schema → executescript → data migrations → seed cache.

## Common Commands

```bash
source .venv/bin/activate
python3 app.py                    # Run on port 9195
pytest tests/ -v                  # Run tests
ruff check teamarr/ tests/        # Lint
ruff format teamarr/              # Format
cd frontend && npm run build      # Build frontend
```

## Logging

**Configuration:** `teamarr/utilities/logging.py`

**Log directory detection** (in priority order):
1. `LOG_DIR` env var (if set)
2. `/app/data/logs` (if `/app/data` exists - Docker or host with `/app`)
3. `<project_root>/logs` (local dev fallback)

**IMPORTANT:** On this dev machine, `/app/data/` exists at the system level, so both Docker AND local dev write to `/app/data/logs/` (not `./data/logs/`).

**Log files:**
| File | Contents |
|------|----------|
| `teamarr.log` | Main log (rotating 10MB x 5) |
| `teamarr_errors.log` | Errors only (rotating 10MB x 3) |

**View recent logs:**
```bash
tail -n 100 /app/data/logs/teamarr.log      # On this dev machine
tail -n 100 ./data/logs/teamarr.log         # Standard Docker setup
docker logs --tail 100 teamarr              # Docker container stdout
```

**Environment variables** (set in docker-compose.yml):
- `LOG_LEVEL`: DEBUG, INFO, WARNING, ERROR (default: INFO for console, DEBUG for files)
- `LOG_FORMAT`: "text" or "json" (default: text)
- `LOG_DIR`: Override log directory path

**Note:** `./data/logs/` in the project directory contains stale V1 logs from Dec 2025 - these can be deleted.

## MCP Servers

**Playwright** (`@playwright/mcp`) - Browser automation for testing UI, capturing screenshots, verifying frontend changes. Tools available:
- `browser_navigate` - Navigate to URL
- `browser_click` - Click elements
- `browser_type` - Enter text in fields
- `browser_snapshot` - Get accessibility tree (preferred over screenshots)
- `browser_screenshot` - Capture page screenshot

Use for: Visual verification of UI changes, testing frontend flows, debugging styling issues.



<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY (code AND beads):
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   bd dolt push  # beads live in refs/dolt/data, not in the commits
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
