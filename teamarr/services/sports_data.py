"""Sports data service layer.

Routes requests to appropriate providers with caching.
Consumers call this service - never providers directly.

Uses PersistentTTLCache for all caching:
- Fast in-memory operations (no SQLite during generation)
- Background flush to SQLite every 2 minutes
- Persists across restarts
- Call flush_cache() after EPG generation for immediate persistence
"""

import logging
import threading
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast, overload

from teamarr.core import GENERATED_PREVIEW_FIELDS, Event, SportsProvider, Team, TeamStats
from teamarr.database import get_db
from teamarr.database.provider_cache import (
    dict_to_event,
    dict_to_stats,
    dict_to_team,
    event_to_dict,
    stats_to_dict,
    team_to_dict,
)
from teamarr.database.team_cache import get_team_identity
from teamarr.providers import ProviderRegistry
from teamarr.utilities.cache import (
    CACHE_TTL_NEGATIVE,
    CACHE_TTL_RANKINGS,
    CACHE_TTL_SCHEDULE,
    CACHE_TTL_SINGLE_EVENT,
    CACHE_TTL_TEAM_INFO,
    CACHE_TTL_TEAM_STATS,
    PersistentTTLCache,
    get_events_cache_ttl,
    make_cache_key,
)
from teamarr.utilities.event_dates import (
    event_intersects,
    provider_day_buckets,
    user_day_window,
)
from teamarr.utilities.event_status import is_event_final
from teamarr.utilities.tz import get_user_timezone

logger = logging.getLogger(__name__)

# Coalesce window for refresh_event_status. The same event is matched to many
# channels and re-checked by the filler, so during one generation run an event
# can be refreshed dozens-to-hundreds of times — each call invalidating the
# event cache and re-hitting the provider summary endpoint serially. The marker
# lives in the shared cache (so the teams and event-group passes coordinate),
# and the window is short enough that separate scheduled runs still pull fresh
# scores. Must stay well under CACHE_TTL_SINGLE_EVENT so get_event can serve the
# cached event during the window.
REFRESH_COALESCE_TTL = 300  # seconds

# Negative-cache marker for get_event. A failed provider fetch must also be
# cached: the refresh coalesce marker only skips the cache *delete*, so without
# a negative entry every per-channel refresh of an event whose summary fetch
# fails (e.g. ESPN 404) falls through to another serial provider call —
# hundreds of live 404s per run for a single event.
_EVENT_NOT_FOUND = {"__event_not_found__": True}

# Negative-cache marker for get_team / get_team_stats. Lookups that return
# nothing (off-season teams, leagues without records) were never cached, so
# every generation run repeated the same failing provider calls — a steady
# ~250+ `espn:teams` calls per hourly run. Cached with CACHE_TTL_NEGATIVE so
# a team that comes back into season is picked up within a few hours.
_NOT_FOUND = {"__not_found__": True}


# Sentinel distinguishing "no usable cache entry" from a legitimately cached
# empty result (e.g. a league with no games that day, cached as []). A distinct
# class (not bare object()) lets callers narrow the load_from_cache() union with
# isinstance, so the real payload type flows through without a type: ignore.
class _CacheMiss:
    __slots__ = ()


_CACHE_MISS = _CacheMiss()

# In-memory memo for team_cache identity lookups. Enrichment runs for the home
# and away team of every event on every get_events cache hit, so without this
# each degraded team costs a fresh SQLite connection (+3 PRAGMAs) per event —
# multiplied by streams × leagues × dates in the multi-league match fallback.
# Team identity is effectively static; the TTL bounds staleness from mid-run
# short-name heals. Misses (None) are memoized too: a team absent from
# team_cache would otherwise re-query on every event it appears in.
_TEAM_IDENTITY_MEMO: dict[tuple[str, str, str], tuple[float, dict | None]] = {}
_TEAM_IDENTITY_MEMO_TTL = 900.0  # seconds
_TEAM_IDENTITY_MEMO_MAX = 8192


def _cached_team_identity(provider: str, team_id: str, league: str) -> dict | None:
    key = (provider, team_id, league)
    now = time.monotonic()
    hit = _TEAM_IDENTITY_MEMO.get(key)
    if hit is not None and now - hit[0] < _TEAM_IDENTITY_MEMO_TTL:
        return hit[1]

    with get_db() as conn:
        cached = get_team_identity(conn, provider, team_id, league)

    if len(_TEAM_IDENTITY_MEMO) >= _TEAM_IDENTITY_MEMO_MAX:
        _TEAM_IDENTITY_MEMO.clear()
    _TEAM_IDENTITY_MEMO[key] = (now, cached)
    return cached


def clear_team_identity_memo() -> None:
    """Drop the team-identity memo so the next lookup re-reads team_cache.

    Called by database.team_cache.invalidate_team_identity_caches after the
    table is written; see that function for why the caches are dropped together.
    """
    _TEAM_IDENTITY_MEMO.clear()


def _backfill_team_from_cache(team: Team | None, league: str) -> Team | None:
    """Patch a Team's short_name/abbreviation/name from team_cache when missing.

    Some provider endpoints return degraded team data (e.g. ESPN's summary
    endpoint omits `shortDisplayName`). team_cache is seeded from each
    provider's `/teams` endpoint where these fields are reliably populated,
    so it's the canonical source — fall back to it whenever a field is empty.
    """
    if team is None or not team.id:
        return team
    if team.short_name and team.abbreviation and team.name:
        return team

    try:
        cached = _cached_team_identity(team.provider, team.id, league)
    except Exception as e:
        logger.debug("[TEAM_BACKFILL] lookup failed for %s/%s: %s", team.provider, team.id, e)
        return team

    if not cached:
        return team

    return replace(
        team,
        short_name=team.short_name or cached.get("short_name") or "",
        abbreviation=team.abbreviation or cached.get("abbreviation") or "",
        name=team.name or cached.get("name") or "",
        logo_url=team.logo_url or cached.get("logo_url"),
    )


@overload
def _enrich_event_teams(event: Event) -> Event: ...
@overload
def _enrich_event_teams(event: None) -> None: ...
def _enrich_event_teams(event: Event | None) -> Event | None:
    """Backfill home_team and away_team from team_cache where fields are empty."""
    if event is None:
        return event
    home = _backfill_team_from_cache(event.home_team, event.league)
    away = _backfill_team_from_cache(event.away_team, event.league)
    if home is event.home_team and away is event.away_team:
        return event
    return replace(event, home_team=home, away_team=away)


def _team_dict_is_stale(team_dict: dict | None) -> bool:
    """A team dict is stale if it has a populated name but no short_name.

    Every modern provider populates short_name (falling back to the full name
    when no shorter form exists). A row with name set but short_name empty
    was written before the field flowed end-to-end and should be re-fetched.
    """
    if not isinstance(team_dict, dict):
        return False
    return bool(team_dict.get("name")) and not team_dict.get("short_name")


def _event_dict_is_stale(event_dict: dict) -> bool:
    """Detect cached events written before short_name flowed end-to-end."""
    return _team_dict_is_stale(event_dict.get("home_team")) or _team_dict_is_stale(
        event_dict.get("away_team")
    )


# Singleton cache instance - shared across all SportsDataService instances
# This ensures one in-memory cache with background persistence
_shared_cache: PersistentTTLCache | None = None
_cache_lock = threading.Lock()


def _get_shared_cache() -> PersistentTTLCache:
    """Get or create the shared cache singleton."""
    global _shared_cache
    if _shared_cache is None:
        with _cache_lock:
            if _shared_cache is None:
                _shared_cache = PersistentTTLCache()
                logger.info("[CACHE] Initialized shared service cache")
    return _shared_cache


def flush_shared_cache() -> int:
    """Flush the shared cache to SQLite.

    Call after EPG generation for immediate persistence.
    Returns number of entries written.
    """
    if _shared_cache is not None:
        return _shared_cache.flush()
    return 0


def _ensure_registry_initialized() -> None:
    """Ensure ProviderRegistry is initialized with dependencies.

    Called automatically by create_default_service() to ensure providers
    have access to league mappings from the database.
    """
    if ProviderRegistry.is_initialized():
        return

    from teamarr.services.league_mappings import init_league_mapping_service

    league_mapping_service = init_league_mapping_service(get_db)
    ProviderRegistry.initialize(league_mapping_service)
    logger.info("[STARTUP] Auto-initialized ProviderRegistry with league mappings")


def create_default_service() -> "SportsDataService":
    """Create SportsDataService with providers from registry.

    Providers are registered in teamarr/providers/__init__.py.
    Priority is determined by registration order and priority values.

    Automatically initializes ProviderRegistry if not already done
    (e.g., when called from CLI or scheduler outside FastAPI context).
    """
    # Ensure registry is initialized with database league mappings
    _ensure_registry_initialized()

    # Get all enabled providers from the registry, sorted by priority
    providers = ProviderRegistry.get_all()
    return SportsDataService(providers=providers)


class SportsDataService:
    """Service layer for sports data access.

    Provides a unified interface to sports data regardless of provider.
    Handles provider selection, fallback, and caching.

    Cache TTLs (optimized for hourly EPG regeneration):
    - Scoreboard (league events): 8 hours - daily schedule rarely changes
    - Team schedules: 8 hours - games rarely added/removed
    - Single event: 30 minutes - fresh scores/odds for current games
    - Team stats: 4 hours - record/standings change infrequently
    - Team info: 24 hours - static team data
    """

    def __init__(self, providers: list[SportsProvider] | None = None):
        self._providers: list[SportsProvider] = providers or []
        self._cache = _get_shared_cache()
        # Let providers that scan day-by-day (ESPN team schedules) reuse the
        # cached, league-wide get_events so a league's daily scoreboard is
        # fetched once per run instead of once per team. Providers that don't
        # expose the hook are unaffected.
        for provider in self._providers:
            setter = getattr(provider, "set_cached_events_fn", None)
            if callable(setter):
                setter(self.get_events)

    def add_provider(self, provider: SportsProvider) -> None:
        """Register a provider."""
        self._providers.append(provider)

    def get_events(self, league: str, target_date: date, cache_only: bool = False) -> list[Event]:
        """Get all events for a league on a given date.

        Args:
            league: League code
            target_date: Date to get events for
            cache_only: If True, only return cached events (no API calls).
                       Use for older dates where we don't want to fetch.

        Returns:
            List of events (may be empty if cache_only and not cached)
        """
        # v2 (#590): results are filtered to the user-local day window below,
        # so the cache entry's meaning depends on the user's timezone — key it
        # by tz and version the namespace so pre-#590 entries (filtered by
        # provider calendars) can't mask newly discoverable events.
        cache_key = make_cache_key(
            "events_v2", league, target_date.isoformat(), str(get_user_timezone())
        )

        def load_from_cache() -> list[Event] | _CacheMiss:
            """Return cached events, or _CACHE_MISS if absent/stale/corrupt."""
            cached = self._cache.get(cache_key)
            if cached is None:
                return _CACHE_MISS
            if isinstance(cached, list) and any(
                _event_dict_is_stale(e) for e in cached if isinstance(e, dict)
            ):
                logger.debug(
                    "[CACHE_STALE] %s — team data missing short_name, re-fetching",
                    cache_key,
                )
                self._cache.delete(cache_key)
                return _CACHE_MISS
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return [_enrich_event_teams(dict_to_event(e)) for e in cached]
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)
                return _CACHE_MISS

        hit = load_from_cache()
        if not isinstance(hit, _CacheMiss):
            return hit

        # If cache_only, don't fetch from API
        if cache_only:
            return []

        # Single-flight: one thread fetches a given (league, date); concurrent
        # callers (parallel stream matching, the team scan) wait and read the
        # freshly cached result instead of each issuing a duplicate scoreboard
        # request.
        with self._cache.lock_key(cache_key):
            hit = load_from_cache()
            if not isinstance(hit, _CacheMiss):
                return hit

            # Iterate through providers
            for provider in self._providers:
                if provider.supports_league(league):
                    # THE date-membership seam (#590): the user-local day
                    # window decides what "on target_date" means, exactly once,
                    # for every provider. The superset it filters is built by
                    # unioning the provider's own day buckets (#601) — a
                    # day-bucketed API can't honour "return ±1 day" on its own.
                    window = user_day_window(target_date)
                    events = [
                        e
                        for e in self._fetch_provider_span(provider, league, target_date)
                        if event_intersects(e, window)
                    ]
                    # Check if all events are final (for past dates, enables 30-day
                    # cache). Empty list counts as "all final" (nothing to update).
                    all_final = len(events) == 0 or all(is_event_final(e) for e in events)
                    ttl = get_events_cache_ttl(target_date, all_events_final=all_final)
                    # Cache ALL results including empty lists to avoid repeated API
                    # calls for leagues with no events on a given day
                    self._cache.set(cache_key, [event_to_dict(e) for e in events], ttl)
                    return [_enrich_event_teams(e) for e in events]
        return []

    def _fetch_provider_span(
        self, provider: SportsProvider, league: str, target_date: date
    ) -> list[Event]:
        """Union a provider's day buckets around ``target_date``, deduplicated.

        The date seam (#590) filters against the user's local day but the fetch
        is bucketed by the provider's calendar, so one bucket is not enough for
        users far from the API's home region — see
        :func:`provider_day_buckets`. Buckets are cached individually, so the
        fan-out costs roughly one extra provider call per league per run rather
        than 3x: consecutive target dates share buckets (D+1 of one day is D of
        the next).

        A failing neighbour bucket must never cost us the day actually asked
        for, so each fetch is isolated.
        """
        seen: set[tuple[str | None, str | None]] = set()
        events: list[Event] = []
        for day in provider_day_buckets(target_date):
            try:
                bucket = self._fetch_provider_day(provider, league, day)
            except Exception as e:  # noqa: BLE001 - one bad bucket ≠ lost day
                logger.warning(
                    "[EVENTS] %s bucket %s for %s failed: %s",
                    type(provider).__name__,
                    day,
                    league,
                    e,
                )
                continue
            for event in bucket:
                key = (event.provider, event.id)
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)
        return events

    def _fetch_provider_day(
        self, provider: SportsProvider, league: str, day: date
    ) -> list[Event]:
        """One provider-day bucket, cached raw (pre-filter, timezone-free).

        Keyed by provider + league + provider-day, so it is independent of the
        user's timezone — unlike the ``events_v2`` entry above it, which caches
        the resolved user-day answer. Both layers are wanted: this one keeps
        the #601 fan-out cheap, that one keeps the filtered result hot.

        Single-flighted on its own key. The outer lock is per user-day, and
        adjacent user-days share buckets, so without this two concurrent
        callers for different target dates would each fetch the buckets they
        overlap on — duplicate scoreboard requests during parallel stream
        matching. Lock order is always events_v2 → events_raw, never the
        reverse, so the nesting cannot cycle.
        """
        raw_key = make_cache_key(
            "events_raw", type(provider).__name__, league, day.isoformat()
        )

        def load_bucket() -> list[Event] | _CacheMiss:
            cached = self._cache.get(raw_key)
            if not isinstance(cached, list):
                return _CACHE_MISS
            if any(_event_dict_is_stale(e) for e in cached if isinstance(e, dict)):
                return _CACHE_MISS
            try:
                return [dict_to_event(e) for e in cached]
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Raw bucket deserialization failed: %s", e)
                return _CACHE_MISS

        hit = load_bucket()
        if not isinstance(hit, _CacheMiss):
            return hit

        with self._cache.lock_key(raw_key):
            hit = load_bucket()
            if not isinstance(hit, _CacheMiss):
                return hit

            events = provider.get_events(league, day)
            all_final = len(events) == 0 or all(is_event_final(e) for e in events)
            self._cache.set(
                raw_key,
                [event_to_dict(e) for e in events],
                get_events_cache_ttl(day, all_events_final=all_final),
            )
            return events

    def get_sample_event(self, league: str) -> Event | None:
        """Pick the single best real event for a template sample preview.

        Selection rule (applies to ALL providers): prefer the most-recent
        FINAL game with two identifiable teams, so postgame variables (recap,
        scores, outcome, margin) populate — a just-completed game is the richest
        sample. Falls back to the nearest upcoming/in-progress game when nothing
        recent has finished.

        Candidate gathering is provider-aware only for *efficiency*: TSDB exposes
        a 2-call recent+upcoming bulk fetch (``get_sample_candidates``) so the
        preview can't hammer its rate-limited API; every other provider
        uses a small bounded scan of recent + near-future days (which captures
        their finals just the same).
        """
        candidates: list[Event] = []
        today = date.today()
        chosen = None
        for provider in self._providers:
            if not provider.supports_league(league):
                continue
            chosen = provider
            bulk = getattr(provider, "get_sample_candidates", None)
            if callable(bulk):
                candidates = cast("list[Event]", bulk(league))
            else:
                # Recent days first (their finals), then a couple upcoming.
                for d in (
                    today,
                    today - timedelta(days=1),
                    today - timedelta(days=2),
                    today + timedelta(days=1),
                    today + timedelta(days=7),
                ):
                    candidates.extend(self.get_events(league, d))
            break

        candidates = [e for e in candidates if e.home_team and e.away_team]

        finals = [e for e in candidates if is_event_final(e)]
        if finals:
            # Most-recently-completed game in the slate is the richest sample.
            return _enrich_event_teams(max(finals, key=lambda e: e.start_time))

        # No recent final in the primary slate — between seasons, try a deep
        # look-back for the last completed game (e.g. NFL in June → the Super
        # Bowl). A finished game populates every postgame variable.
        deep = getattr(chosen, "get_recent_final", None) if chosen else None
        if callable(deep):
            ev = cast("Event | None", deep(league))
            if ev and ev.home_team and ev.away_team:
                return _enrich_event_teams(ev)

        if not candidates:
            return None
        # Else the nearest game to now (in-progress or soonest upcoming).
        now = datetime.now(UTC)
        return _enrich_event_teams(
            min(candidates, key=lambda e: abs((e.start_time - now).total_seconds()))
        )

    def get_team_schedule(
        self,
        team_id: str,
        league: str,
        days_ahead: int = 14,
    ) -> list[Event]:
        """Get schedule for a team (past and future games)."""
        cache_key = make_cache_key("schedule", league, team_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, list) and any(
                _event_dict_is_stale(e) for e in cached if isinstance(e, dict)
            ):
                logger.debug(
                    "[CACHE_STALE] %s — team data missing short_name, re-fetching",
                    cache_key,
                )
                self._cache.delete(cache_key)
            else:
                logger.debug("[CACHE_HIT] %s", cache_key)
                try:
                    return [_enrich_event_teams(dict_to_event(e)) for e in cached]
                except (KeyError, TypeError) as e:
                    logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                events = provider.get_team_schedule(team_id, league, days_ahead)
                if events:
                    # Serialize to dict before caching
                    serialized = [event_to_dict(e) for e in events]
                    self._cache.set(cache_key, serialized, CACHE_TTL_SCHEDULE)
                    return [_enrich_event_teams(e) for e in events]
        return []

    def get_team(self, team_id: str, league: str) -> Team | None:
        """Get team details."""
        cache_key = make_cache_key("team", league, team_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached == _NOT_FOUND:
                logger.debug("[CACHE_HIT] %s (negative)", cache_key)
                return None
            if _team_dict_is_stale(cached):
                logger.debug(
                    "[CACHE_STALE] %s — team data missing short_name, re-fetching",
                    cache_key,
                )
                self._cache.delete(cache_key)
            else:
                logger.debug("[CACHE_HIT] %s", cache_key)
                try:
                    return dict_to_team(cached)
                except (KeyError, TypeError) as e:
                    logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                team = provider.get_team(team_id, league)
                if team:
                    # Serialize to dict before caching
                    self._cache.set(cache_key, team_to_dict(team), CACHE_TTL_TEAM_INFO)
                    return team
        self._cache.set(cache_key, _NOT_FOUND, CACHE_TTL_NEGATIVE)
        return None

    def get_event(self, event_id: str, league: str) -> Event | None:
        """Get a specific event by ID.

        Uses shorter TTL (30min) since this is called for fresh scores/odds.
        """
        # Guard against empty event_id which would cause malformed API requests
        if not event_id:
            logger.warning(
                "[SPORTS_DATA] get_event called with empty event_id for league %s", league
            )
            return None

        cache_key = make_cache_key("event", league, event_id)

        def load_from_cache() -> Event | None | _CacheMiss:
            """Return cached event, or _CACHE_MISS if absent/stale/corrupt."""
            cached = self._cache.get(cache_key)
            if cached is None:
                return _CACHE_MISS
            if isinstance(cached, dict) and cached.get("__event_not_found__"):
                logger.debug("[CACHE_HIT] %s (negative — provider miss)", cache_key)
                return None
            if isinstance(cached, dict) and _event_dict_is_stale(cached):
                logger.debug(
                    "[CACHE_STALE] %s — team data missing short_name, re-fetching",
                    cache_key,
                )
                self._cache.delete(cache_key)
                return _CACHE_MISS
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return _enrich_event_teams(dict_to_event(cached))
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)
                return _CACHE_MISS

        hit = load_from_cache()
        if not isinstance(hit, _CacheMiss):
            return hit

        # Single-flight: collapse concurrent identical summary fetches (the
        # coalesce marker in refresh_event_status has a check-then-act race when
        # two threads enter together) into one upstream request.
        with self._cache.lock_key(cache_key):
            hit = load_from_cache()
            if not isinstance(hit, _CacheMiss):
                return hit

            for provider in self._providers:
                if provider.supports_league(league):
                    event = provider.get_event(event_id, league)
                    if event:
                        # Serialize to dict before caching
                        self._cache.set(cache_key, event_to_dict(event), CACHE_TTL_SINGLE_EVENT)
                        return _enrich_event_teams(event)

            # Short TTL: don't mask an event that becomes available, just absorb
            # the per-channel refresh fan-out within one coalesce window.
            self._cache.set(cache_key, _EVENT_NOT_FOUND, REFRESH_COALESCE_TTL)
        return None

    # Fields refreshed onto the original event by refresh_event_status. Anything
    # not listed here is preserved from the original — teams, start_time, league,
    # sport, season_type, etc. don't change between fetches and the summary
    # endpoint may return degraded versions of them (e.g. ESPN omits
    # shortDisplayName), so overwriting would be destructive, not additive.
    _REFRESH_FIELDS = (
        "status",
        "home_score",
        "away_score",
        "broadcasts",
        "odds_data",
        "fight_result_method",
        "finish_round",
        "finish_time",
        # Per-event editorial copy — only the summary endpoint carries these, so
        # they must overlay from the fresh fetch (the scoreboard-parsed original
        # has them empty). The summary call is already made here; zero extra cost.
        "game_preview",
        *GENERATED_PREVIEW_FIELDS,
    )

    def refresh_event_status(self, event: Event) -> Event:
        """Overlay fresh status (and other game-state fields) onto event.

        The summary endpoint can return a strict subset of what scoreboard
        returned — most notably ESPN's summary omits shortDisplayName, so
        replacing the event wholesale wipes team short_names. This function
        instead fetches fresh data and overlays only the fields that
        legitimately change during a game (status, scores, broadcasts,
        odds, fight result), preserving everything else from the original.

        Args:
            event: Event with potentially stale status from schedule/scoreboard cache

        Returns:
            Event with refreshed game-state fields, original team/identity data
        """
        if not event:
            return event

        # Coalesce repeated refreshes of the same event within a run. Normally we
        # invalidate the event cache to force a fresh provider fetch, but the same
        # event is refreshed once per channel (and again by the filler), so a
        # popular event would otherwise trigger many identical serial summary
        # fetches. Skip the invalidating delete when we've already refreshed this
        # event inside the coalesce window — get_event then serves the fresh-enough
        # copy from the (30-min) event cache. The marker is in the shared cache so
        # the teams and event-group passes coordinate.
        cache_key = make_cache_key("event", event.league, event.id)
        coalesce_key = make_cache_key("event_refresh", event.league, event.id)
        if not self._cache.get(coalesce_key):
            self._cache.delete(cache_key)
            self._cache.set(coalesce_key, True, REFRESH_COALESCE_TTL)

        fresh_event = self.get_event(event.id, event.league)
        if not fresh_event:
            logger.debug("[SPORTS_DATA] Could not refresh event %s, using cached status", event.id)
            return event

        logger.debug(
            "[REFRESH] event=%s status: %s → %s",
            event.id,
            event.status.state if event.status else "N/A",
            fresh_event.status.state if fresh_event.status else "N/A",
        )

        # Build the overlay: take each refresh field from the fresh event when
        # it has a meaningful value, otherwise fall back to the original. This
        # is what makes the merge additive — an empty/None value in the fresh
        # response never clobbers data we already had.
        overlay: dict = {}
        for field_name in self._REFRESH_FIELDS:
            fresh_val = getattr(fresh_event, field_name, None)
            orig_val = getattr(event, field_name, None)
            if field_name == "status":
                # Status is the whole point of the refresh — always take fresh
                # when present, even if state is unchanged (other status fields
                # like clock/period may have updated).
                overlay[field_name] = fresh_val if fresh_val is not None else orig_val
            elif field_name in ("home_score", "away_score"):
                # A score of 0 is a real value (#680): the falsy check below
                # made a shutout side's fresh 0 fall back to the original —
                # None before the game — so {home_team_score}/{final_score}
                # rendered empty. For scores, only None means "not provided".
                overlay[field_name] = fresh_val if fresh_val is not None else orig_val
            elif field_name in GENERATED_PREVIEW_FIELDS:
                # Generated-preview facts are a pregame snapshot, not a live box score.
                # Never attach summary facts first observed after kickoff, and
                # never replace a snapshot already carried by this event.
                fresh_state = fresh_event.status.state if fresh_event.status else ""
                if fresh_state in {"in_progress", "in", "live", "final", "post"}:
                    overlay[field_name] = orig_val
                else:
                    overlay[field_name] = orig_val or fresh_val
            else:
                overlay[field_name] = fresh_val if fresh_val else orig_val
        return replace(event, **overlay)

    # --- Days-ahead structured preview enrichment (tvnk.15, #329) ---
    # Matched events already carry structured-preview fields via the
    # refresh_event_status overlay above (same summary payload). This path
    # exists for FUTURE games nothing refreshes — a team channel's next game
    # days out — and is deliberately budgeted: each miss costs one per-event
    # summary call.
    PREVIEW_LOOKAHEAD_DAYS = 7
    PREVIEW_CACHE_TTL = 6 * 3600  # parsed preview fields; clamped to gametime
    PREVIEW_SPARSE_CACHE_TTL = 30 * 60  # retry after ESPN publishes richer facts
    PREVIEW_SNAPSHOT_AFTER_START_TTL = 12 * 3600
    PREVIEW_FETCH_BUDGET = 40  # summary fetches per budget window
    PREVIEW_BUDGET_WINDOW = 3600

    _PREVIEW_FIELDS = ("game_preview", *GENERATED_PREVIEW_FIELDS)

    def enrich_event_preview(self, event: Event) -> Event:
        """Overlay days-ahead preview fields onto a future event, cheaply.

        Gate: only future events within PREVIEW_LOOKAHEAD_DAYS that don't
        already carry structured-preview data. Cache: parsed fields per event
        (TTL capped at gametime). Budget: at most PREVIEW_FETCH_BUDGET summary
        fetches per window — when exhausted, events render without Tier-2 data
        until the next window (the template chain falls back to constructed
        prose, so this degrades gracefully).
        """
        if not event or not event.start_time:
            return event
        preview_key = make_cache_key("event_preview_v2", event.league, event.id)
        cached = self._cache.get(preview_key)
        if isinstance(cached, dict):
            return replace(
                event,
                **{f: cached.get(f) or getattr(event, f) for f in self._PREVIEW_FIELDS},
            )
        from teamarr.templates.generated_preview import has_generated_preview_enrichment

        if has_generated_preview_enrichment(event):
            return event  # already fully enriched (e.g. via refresh overlay)

        now = datetime.now(UTC)
        start = event.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        seconds_to_start = (start - now).total_seconds()
        if seconds_to_start <= 0 or seconds_to_start > self.PREVIEW_LOOKAHEAD_DAYS * 86400:
            return event

        budget_key = make_cache_key("event_preview_budget", "window")
        spent = self._cache.get(budget_key) or 0
        if spent >= self.PREVIEW_FETCH_BUDGET:
            logger.debug(
                "[PREVIEW] budget exhausted (%d/%d) — skipping enrich for %s",
                spent,
                self.PREVIEW_FETCH_BUDGET,
                event.id,
            )
            return event
        self._cache.set(budget_key, spent + 1, self.PREVIEW_BUDGET_WINDOW)

        fresh = self.get_event(event.id, event.league)
        fields = {
            field_name: getattr(fresh, field_name, None) if fresh else None
            for field_name in self._PREVIEW_FIELDS
        }
        candidate = replace(
            event,
            **{
                field_name: value if value not in (None, "") else getattr(event, field_name)
                for field_name, value in fields.items()
            },
        )
        complete = has_generated_preview_enrichment(candidate)
        cache_ttl = self.PREVIEW_CACHE_TTL if complete else self.PREVIEW_SPARSE_CACHE_TTL
        # Complete snapshots deliberately survive kickoff. A later live
        # summary contains in-game leaders/team stats and must not rewrite the
        # guide description. Event-id scoping naturally drops the snapshot
        # when the channel advances to its next game.
        ttl = cache_ttl
        if complete:
            ttl += self.PREVIEW_SNAPSHOT_AFTER_START_TTL
        # Negative results cache too — a league without lastFiveGames data
        # shouldn't re-spend budget every run.
        self._cache.set(preview_key, fields, ttl)
        if not any(fields.values()):
            return event
        return replace(
            event,
            **{
                field_name: value if value not in (None, "") else getattr(event, field_name)
                for field_name, value in fields.items()
            },
        )

    def get_team_stats(self, team_id: str, league: str) -> TeamStats | None:
        """Get detailed team statistics."""
        cache_key = make_cache_key("stats", league, team_id)

        # Check cache (deserialize from dict)
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached == _NOT_FOUND:
                logger.debug("[CACHE_HIT] %s (negative)", cache_key)
                return None
            logger.debug("[CACHE_HIT] %s", cache_key)
            try:
                return dict_to_stats(cached)
            except (KeyError, TypeError) as e:
                logger.warning("[CACHE_ERROR] Deserialization failed: %s", e)

        # Fetch from provider
        for provider in self._providers:
            if provider.supports_league(league):
                stats = provider.get_team_stats(team_id, league)
                if stats:
                    if stats.rank is None:
                        rank = self.get_rankings(league).get(team_id)
                        if rank:
                            stats = replace(stats, rank=rank)
                    # Serialize to dict before caching
                    self._cache.set(cache_key, stats_to_dict(stats), CACHE_TTL_TEAM_STATS)
                    return stats
        self._cache.set(cache_key, _NOT_FOUND, CACHE_TTL_NEGATIVE)
        return None

    def get_rankings(self, league: str) -> dict[str, int]:
        """Get the league's poll rankings as {team_id: rank}.

        One fetch per league feeds every team's rank (#710) — ESPN's per-team
        payload has no rank field, so the poll endpoint is the only source. The
        empty result is cached too: most leagues publish no poll at all, and
        re-asking them once per team would be the expensive way to learn that.
        """
        cache_key = make_cache_key("rankings", league)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("[CACHE_HIT] %s", cache_key)
            return cached

        for provider in self._providers:
            if provider.supports_league(league):
                rankings = provider.get_rankings(league)
                self._cache.set(cache_key, rankings, CACHE_TTL_RANKINGS)
                return rankings
        return {}

    # Cache management

    def get_provider_name(self, league: str) -> str | None:
        """Get the provider name that handles a league.

        Returns provider name (e.g., 'espn', 'tsdb') or None if no provider.
        """
        for provider in self._providers:
            if provider.supports_league(league):
                return provider.name
        return None

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

    def flush_cache(self) -> int:
        """Flush dirty cache entries to SQLite.

        Call after EPG generation for immediate persistence.
        Returns number of entries written.
        """
        return self._cache.flush()

    def invalidate_team(self, team_id: str, league: str) -> None:
        """Invalidate all cached data for a team."""
        self._cache.delete(make_cache_key("team", league, team_id))
        self._cache.delete(make_cache_key("stats", league, team_id))
        self._cache.delete(make_cache_key("schedule", league, team_id))

    def provider_stats(self) -> dict:
        """Get statistics from all providers for UI feedback.

        Returns a dict with provider-specific stats including:
        - Rate limit status (TSDB)
        - Cache statistics (if provider has internal cache)

        Example response:
        {
            "espn": {"name": "espn", "has_rate_limit": False},
            "tsdb": {
                "name": "tsdb",
                "has_rate_limit": True,
                "rate_limit": {
                    "total_requests": 10,
                    "is_rate_limited": True,
                    "total_wait_seconds": 45.2,
                    ...
                },
                "cache": {"total_entries": 5, ...}
            }
        }
        """
        stats = {}
        for provider in self._providers:
            provider_stats: dict = {"name": provider.name, "has_rate_limit": False}

            # Check for TSDB-specific stats
            if hasattr(provider, "_client"):
                client: Any = getattr(provider, "_client", None)
                if hasattr(client, "rate_limit_stats"):
                    provider_stats["has_rate_limit"] = True
                    provider_stats["rate_limit"] = client.rate_limit_stats().to_dict()
                if hasattr(client, "cache_stats"):
                    provider_stats["cache"] = client.cache_stats()

            stats[provider.name] = provider_stats

        return stats

    def reset_provider_stats(self) -> None:
        """Reset provider statistics (call at start of EPG generation).

        Resets rate limit counters so each generation has clean stats.
        """
        for provider in self._providers:
            if hasattr(provider, "_client"):
                client: Any = getattr(provider, "_client", None)
                if hasattr(client, "reset_rate_limit_stats"):
                    client.reset_rate_limit_stats()

    def prewarm_tsdb_leagues(self, leagues: list[str], days_ahead: int = 14) -> None:
        """Pre-warm TSDB events cache for multiple leagues.

        Fetches events for each league/day upfront, populating the cache.
        This ensures all subsequent get_team_schedule calls are cache hits.

        NOTE: Team name lookup uses seeded database cache (not API), so we
        only need to pre-warm events, not teams. This saves 2 API calls per league.

        Args:
            leagues: List of canonical league codes to pre-warm
            days_ahead: Number of days to pre-warm (default 14, matches get_team_schedule)
        """
        from datetime import timedelta

        if not leagues:
            return

        # Find TSDB provider
        tsdb_provider = None
        for provider in self._providers:
            if provider.name == "tsdb":
                tsdb_provider = provider
                break

        if not tsdb_provider:
            logger.debug("[PREWARM] No TSDB provider registered, skipping pre-warm")
            return

        unique_leagues = list(set(leagues))
        today = date.today()

        # Cap to TSDB's max days (same as provider)
        days_ahead = min(days_ahead, 14)

        # Warm the provider-day buckets the seam will actually ask for, not the
        # target dates: requesting target dates today..today+N-1 fetches buckets
        # today-1..today+N (#601). Warming only the target dates leaves the two
        # edge buckets to fetch on demand mid-run.
        bucket_days = range(-1, days_ahead + 1)
        total_calls = len(unique_leagues) * len(bucket_days)
        logger.info(
            "[PREWARM] TSDB: %d leagues × %d day buckets = ~%d API calls",
            len(unique_leagues),
            len(bucket_days),
            total_calls,
        )

        for league in unique_leagues:
            if not tsdb_provider.supports_league(league):
                continue

            # Pre-warm events cache for each day
            # Team names come from seeded database cache (no API needed)
            for i in bucket_days:
                target_date = today + timedelta(days=i)
                # Use get_events which goes through provider → client cache
                tsdb_provider.get_events(league, target_date)

            logger.debug("[PREWARM] TSDB league %s: %d buckets", league, len(bucket_days))
