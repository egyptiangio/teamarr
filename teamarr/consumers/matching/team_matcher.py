"""Team vs Team stream matcher.

Matches streams that contain team matchups (vs/@/at) to provider events.
Supports two modes:
- Single-league: Search only the authoritative league (team EPG)
- Multi-league: Detect league hint, search enabled leagues (event EPG)
"""

import logging
import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz

from teamarr.consumers.matching import MATCH_WINDOW_DAYS
from teamarr.consumers.matching.candidate_index import (
    CandidateTokenIndex,
    tokenize,
)
from teamarr.consumers.matching.classifier import ClassifiedStream, StreamCategory
from teamarr.consumers.matching.constants import (
    ABBREVIATION_STOPWORDS,
    ALTERNATE_TEAM_CODES,
    BOTH_TEAMS_THRESHOLD,
    HIGH_CONFIDENCE_THRESHOLD,
    NEAR_MISS_DETAIL_MAX,
    SHORT_CODE_MAX_LEN,
)
from teamarr.consumers.matching.country_resolver import (
    _normalize as _normalize_country,
)
from teamarr.consumers.matching.country_resolver import (
    get_country_resolver,
)
from teamarr.consumers.matching.identity import TeamIdentityIndex, residual_contradicts
from teamarr.consumers.matching.normalizer import normalize_for_matching
from teamarr.consumers.matching.result import (
    FailedReason,
    FilteredReason,
    MatchMethod,
    MatchOutcome,
)
from teamarr.consumers.stream_match_cache import StreamMatchCache, event_to_cache_data
from teamarr.core.types import Event, EventStatus, RacingResult, RacingSession, Team, Venue
from teamarr.services.sports_data import SportsDataService
from teamarr.utilities.constants import TEAM_ALIASES
from teamarr.utilities.fuzzy_match import get_matcher, normalize_text

logger = logging.getLogger(__name__)

# EPG anchored matching (bead t5e). A live broadcast's EPG program starts at ~the
# event's official start; encores/replays/"classic" re-airs and the next game in a
# series air later. When an anchor instant is supplied (the program's start), a
# candidate event must fall within this tolerance of it to match — the definitive,
# category-independent guard against binding a stream to an encore or the wrong
# occurrence.
#
# 90 minutes (chosen 2026-06-03): a team-sport event always runs >90 min, so the
# earliest an encore can START is >90 min after the live start — outside the gate.
# Meanwhile ±90 min absorbs the usual broadcast-vs-scheduled-start skew (pre-game
# lead-in). Tighter than an hours-wide window on purpose: it also excludes the
# OTHER game of a same-day doubleheader (hours apart). Trade-off: if a provider's
# guide lists the live program >90 min off the event start, that event simply gets
# no EPG stream (safe no-match) rather than a wrong-occurrence bind.
ANCHOR_MATCH_TOLERANCE_SECONDS = 90 * 60

# "All-Star(s)" token used to recognise All-Star pseudo-teams in event names.
_ALL_STAR_TEAM_RE = re.compile(r"all[\s\-]?stars?", re.IGNORECASE)


def is_all_star_event(event: Event) -> bool:
    """True when both competitors are All-Star squads.

    ESPN names both sides of an All-Star game with an "All-Star(s)" token
    ("American All-Stars"/"National All-Stars" for MLB, "MLS All-Stars"/"Liga MX
    All-Stars" for MLS). Requiring the token on *both* sides is a precise,
    name-agnostic signal that survives the yearly change of opponents and does
    not misfire on a regular game against an all-star-branded club.
    """
    home = event.home_team.name if event.home_team else ""
    away = event.away_team.name if event.away_team else ""
    return bool(_ALL_STAR_TEAM_RE.search(home)) and bool(_ALL_STAR_TEAM_RE.search(away))


def _sport_hint_matches(sport_hint: str | list[str], event_sport: str) -> bool:
    """Check if a sport hint matches an event's sport.

    Handles both single hints ("Hockey") and multi-sport hints
    (["Soccer", "Football"]) for ambiguous terms.
    """
    event_lower = event_sport.lower()
    if isinstance(sport_hint, list):
        return event_lower in [s.lower() for s in sport_hint]
    return event_lower == sport_hint.lower()


# Type alias for user-defined aliases: (alias_text, league) -> team_name
UserAliasCache = dict[tuple[str, str], str]


# Built-in aliases keyed by matcher-normalized text (#480): TEAM_ALIASES keys
# are hand-written and a few contain punctuation ("miami-oh", "texas a&m-cc")
# that the stream side never has after normalize_for_matching — those entries
# could never fire. Lookups go through this view so store and lookup agree.
_NORMALIZED_TEAM_ALIASES: dict[str, str] = {
    normalize_text(k): v for k, v in TEAM_ALIASES.items()
}


class _Unresolved:
    """Sentinel for the alias memo: None is a real, cacheable answer ("no alias").

    A class rather than a bare ``object()`` so callers can narrow the ``get()``
    union with ``isinstance`` and the real payload type flows through — the same
    pattern as ``_CacheMiss`` in services/sports_data.py.
    """

    __slots__ = ()


_UNRESOLVED = _Unresolved()


@dataclass(frozen=True)
class _DateWindow:
    """The +/- day window some candidate builders filter to.

    One object rather than three keyword arguments because the three only ever
    travel together: a caller either filters by date or does not. Grouping them
    makes that an invariant the type checker enforces, instead of three
    independently-optional values the body has to assume are consistent.
    Frozen so it can key the candidate memo.
    """

    user_tz: ZoneInfo
    target_date: date
    days: int


# Process-wide memo for the team identity index (#609). Rebuilt on expiry so a
# team_cache refresh is picked up without a restart; the window is generous
# because team identity barely moves and a generation run is far shorter.
def _token_index_enabled() -> bool:
    """Whether candidate narrowing is on (#747). Default OFF.

    Read per call rather than captured at import so a soak can be started and
    stopped by restarting the process, and so tests can toggle it with
    monkeypatch without reloading the module. Matches the ``ESPN_MAX_WORKERS``
    convention for perf knobs that are not user-facing settings.
    """
    return os.environ.get("TEAMARR_TOKEN_INDEX", "").strip().lower() in {"1", "true", "yes", "on"}


_IDENTITY_INDEX_TTL = 900.0  # seconds
_identity_index_cache: tuple[float, TeamIdentityIndex] | None = None
_identity_index_lock = threading.Lock()


def _shared_identity_index(db_factory: Any) -> TeamIdentityIndex | None:
    """The shared identity index, rebuilt at most once per TTL window.

    Returns None — never a partial or empty index — when team_cache cannot be
    read or has not been seeded, so the fixture gate stays inert rather than
    vetoing every candidate (epic goax).
    """
    global _identity_index_cache

    now = monotonic()
    cached = _identity_index_cache
    if cached is not None and now - cached[0] < _IDENTITY_INDEX_TTL:
        return cached[1]

    with _identity_index_lock:
        # Re-check: another thread may have rebuilt it while we waited.
        cached = _identity_index_cache
        if cached is not None and monotonic() - cached[0] < _IDENTITY_INDEX_TTL:
            return cached[1]

        try:
            with db_factory() as conn:
                index = TeamIdentityIndex.from_db(conn)
        except Exception as e:
            logger.warning("[FIXTURE] Could not build team identity index: %s", e)
            return None

        if not len(index):
            logger.warning("[FIXTURE] team_cache is empty — fixture checking disabled")
            return None

        logger.debug("[FIXTURE] Team identity index: %d surface forms", len(index))
        _identity_index_cache = (monotonic(), index)
        return index


def reset_identity_index_cache() -> None:
    """Drop the shared index so the next lookup rebuilds it.

    Production callers should not reach for this directly — write to team_cache
    and call ``database.team_cache.invalidate_team_identity_caches``, which
    drops this and the enrichment memo together. Exposed separately for tests
    and so that helper has something to call.
    """
    global _identity_index_cache
    _identity_index_cache = None


@lru_cache(maxsize=32768)
def _local_date(instant: datetime, tz: ZoneInfo) -> date:
    """The calendar date an instant falls on in ``tz``, memoized.

    Every candidate event is converted at least twice per stream (the search
    window and the date-proximity ranking), so a batch redoes the same
    ``astimezone`` once per (stream x event) when it depends only on the event.
    Kick-off times cluster hard on the hour, so the distinct-key count is far
    smaller than the event count.
    """
    return instant.astimezone(tz).date()


def _is_short_code(normalized: str) -> bool:
    """A single token this short is an abbreviation, not a team name (#472)."""
    return len(normalized) <= SHORT_CODE_MAX_LEN and " " not in normalized


def _resolve_alt_codes(tokens: set[str]) -> set[str]:
    """Expand stream tokens with canonical provider codes (AZ -> ARI, ...)."""
    return tokens | {
        ALTERNATE_TEAM_CODES[t] for t in tokens if t in ALTERNATE_TEAM_CODES
    }


@lru_cache(maxsize=16384)
def _code_tokens(team_name: str) -> frozenset[str]:
    """Alt-code-resolved tokens of a stream side, memoized.

    The abbreviation check runs once per (stream x candidate event), but its
    stream-side tokens depend only on the stream — normalizing and splitting
    them per candidate is work the whole batch repeats.
    """
    return frozenset(_resolve_alt_codes(set(normalize_text(team_name).split())))


def _abbrev_equals(stream_code: str, event_abbrev: str | None) -> bool:
    """Does a short stream code equal the event team's abbreviation (#472)?

    Disallows matching if either code is an abbreviation stop word (#705).
    """
    if not event_abbrev:
        return False
    code = ALTERNATE_TEAM_CODES.get(stream_code, stream_code)
    norm_event = normalize_text(event_abbrev)
    if code in ABBREVIATION_STOPWORDS or norm_event in ABBREVIATION_STOPWORDS:
        return False
    return code == norm_event


def _initialism(tokens: list[str]) -> str:
    """First letter of each token, in order ("san francisco" -> "sf")."""
    return "".join(t[0] for t in tokens if t)


@lru_cache(maxsize=16384)
def _short_name_leg_is_safe(stream_norm: str, name_norm: str, short_norm: str, abbrev: str) -> bool:
    """May the short_name score stand in for this stream candidate? (#569)

    Memoized: a pure function of four already-normalized strings, called once
    per (stream x candidate event x side) over a small pool of distinct inputs.

    token_set_ratio returns 100 whenever the short name is a token subset of
    the stream — so a bare nickname short_name makes every team sharing that
    nickname interchangeable. ESPN hands both the NFL and MLB Giants
    short_name "Giants", so "san francisco giants" scored 100 against the New
    York Giants (57 on the full name) and SF streams landed on NFL channels.

    The discriminator is whatever the full name carries beyond the nickname
    ("new york" for NYG). Accept the short_name leg only when the stream's own
    residual tokens are consistent with that discriminator — its own words,
    its initialism ("ny"), or the provider abbreviation. This keeps #480's
    "d backs" working, keeps "SF Giants" on the MLB side, and additionally
    pins "NY Giants" to the NFL side, which is ambiguous today.
    """
    short_tokens = set(short_norm.split())
    name_tokens = name_norm.split()
    discriminator = [t for t in name_tokens if t not in short_tokens]
    stream_residual = [t for t in stream_norm.split() if t not in short_tokens]

    # Bare nickname on either side carries no location to contradict.
    if not stream_residual or not discriminator:
        return True

    allowed = set(discriminator)
    allowed.add(_initialism(discriminator))
    allowed.add(_initialism(name_tokens))
    if abbrev:
        allowed.add(abbrev)
    return all(token in allowed for token in stream_residual)


def _best_name_score(stream_norm: str, event_team) -> float:
    """token_set_ratio against the best of the team's name and short_name.

    Thin wrapper over the memoized kernel below: a Team object is not hashable
    on identity in a useful way here (the same team arrives as a fresh instance
    from every event it appears in), but the three fields the score depends on
    are. A team plays several games inside the match window, so within one
    stream's pass over the candidate events the same (side, team) pair recurs
    once per fixture — and again for every stream sharing a normalized side.

    Official nicknames often share no words with the full name — ESPN's
    short_name for Arizona is literally "D-backs", which scores ~50 against
    "Arizona Diamondbacks" (#480). Streams use whichever form the provider
    liked, so both are fair game — but the short_name leg is gated by
    _short_name_leg_is_safe so a shared nickname can't erase the location
    that tells two teams apart (#569).

    The full-name leg carries the mirror-image gate (epic goax): when the stream
    and the team share tokens but each keeps words the other lacks, those
    residual words ARE the discriminator and they disagree — "tampa bay
    LIGHTNING" is not "tampa bay RAYS", however much city they share. This is
    the fallback for streams the identity index cannot resolve (an uncached
    league, a team the provider has not seeded); the fixture gate handles the
    rest. Aliases are unaffected: _side_score scores them separately and takes
    the max, so "Anaheim Angels" still reaches the Angels via its alias.
    """
    return _best_name_score_cached(
        stream_norm,
        event_team.name,
        getattr(event_team, "short_name", None) or "",
        getattr(event_team, "abbreviation", "") or "",
    )


def _has_shared_name_token(stream_norm: str, event_team) -> bool:
    """Whether a fuzzy side match has a meaningful token in common.

    Low fuzzy scores can result from coincidental character overlap alone,
    such as ``Barrie Colts`` and ``Erie Otters``. A partial team name should
    still contain at least one meaningful word from its full or short name.
    """
    stream_tokens = {token for token in stream_norm.split() if len(token) > 2}
    if not stream_tokens:
        return False

    names = [event_team.name, getattr(event_team, "short_name", None) or ""]
    return any(
        stream_tokens & {token for token in normalize_text(name).split() if len(token) > 2}
        for name in names
    )


@lru_cache(maxsize=65536)
def _best_name_score_cached(
    stream_norm: str, team_name: str, team_short: str, team_abbrev: str
) -> float:
    """Memoized kernel of :func:`_best_name_score` — see its docstring."""
    name_norm = normalize_text(team_name)
    score = 0.0 if residual_contradicts(stream_norm, name_norm) else fuzz.token_set_ratio(
        stream_norm, name_norm
    )

    short = team_short
    if not short or short == team_name:
        return score

    short_norm = normalize_text(short)
    short_score = fuzz.token_set_ratio(stream_norm, short_norm)
    if short_score <= score:
        return score

    abbrev = normalize_text(team_abbrev)
    if _short_name_leg_is_safe(stream_norm, name_norm, short_norm, abbrev):
        return short_score
    return score


@dataclass
class MatchContext:
    """Context for a matching attempt."""

    stream_name: str
    stream_id: int
    group_id: int
    target_date: date
    generation: int
    user_tz: ZoneInfo
    classified: ClassifiedStream  # From classifier

    # Optional fields (must come after required fields)
    stream_tz: ZoneInfo | None = None  # TZ for stream dates
    team1: str | None = None  # Extracted team names (from classifier)
    team2: str | None = None

    # EPG matching (bead t5e): absolute broadcast instant of the matched program.
    # When set, same-team candidate events are ranked by absolute time proximity
    # to this anchor (nearest wins, tolerance-bounded) instead of by calendar
    # date — so a series game whose title repeats across nights, or a post-game
    # encore airing, binds to the correct occurrence. The match cache is bypassed
    # for anchored matches (same title, different instants must not collide).
    anchor_dt: "datetime | None" = None

    # Sport durations for ongoing event detection (hours)
    sport_durations: dict[str, float] = field(default_factory=dict)

    def is_event_in_search_window(self, event: "Event") -> bool:
        """Check if an event falls within the 30-day search window for matching.

        V2 uses full 30-day cache for matching to support stats tracking.
        The lifecycle layer will categorize matched-but-past events as EXCLUDED,
        allowing users to see that streams matched correctly even if events are over.

        Final/completed status is NOT checked here - lifecycle handles exclusions.
        """
        event_date = _local_date(event.start_time, self.user_tz)

        earliest_date = self.target_date - timedelta(days=MATCH_WINDOW_DAYS)

        return event_date >= earliest_date


class TeamMatcher:
    """Matches team-vs-team streams to provider events.

    Flow:
    1. Check user-corrected cache (pinned)
    2. Check algorithmic cache
    3. Match via: aliases → patterns → fuzzy
    4. Validate date
    5. Cache result
    """

    def __init__(
        self,
        service: SportsDataService,
        cache: StreamMatchCache,
        db_factory: Any = None,
        days_ahead: int = 3,
    ):
        """Initialize matcher.

        Args:
            service: Sports data service for event/team lookups
            cache: Stream match cache
            db_factory: Optional database factory for alias lookups
            days_ahead: Days to look ahead for events (default 3)
        """
        self._service = service
        self._cache = cache
        self._db = db_factory
        self._fuzzy = get_matcher()
        self._days_ahead = days_ahead
        # Load user-defined aliases from database
        # Forward cache: (alias, league) -> canonical
        self._user_aliases: UserAliasCache = self._load_user_aliases()
        # Reverse cache: alias -> [(canonical, league), ...]
        # Enables finding canonical name without knowing league first
        self._reverse_aliases: dict[str, list[tuple[str, str]]] = self._build_reverse_cache()
        # Locale-aware country name resolver (e.g. "brasil" → "Brazil")
        # Process-wide (see get_country_resolver): read-only after construction,
        # and ~17ms to build — which was 92% of this constructor's cost, paid
        # once per event group.
        self._country_resolver = get_country_resolver()
        # Memoize country resolution per team name: it's deterministic and the
        # same names are re-checked against every candidate event. Without this
        # the [ALIAS] log line repeats once per candidate (147x in #256).
        self._country_resolve_cache: dict[str, str | None] = {}
        # Memo for _resolve_alias, keyed (team_name, league). Cleared by
        # reload_aliases so a user's edit takes effect immediately.
        self._alias_resolve_cache: dict[tuple[str, str | None], str | None] = {}
        # Memo for the flattened prefetched-candidate list (see
        # _prefetched_candidates). Keyed by league set + date window, and reset
        # whenever a different prefetch dict arrives.
        self._candidates_source: dict[str, list[Event]] | None = None
        self._candidates_memo: dict[
            tuple[tuple[str, ...], _DateWindow | None], tuple[tuple[str, Event], ...]
        ] = {}
        # Window-filtered candidates per (candidate tuple, target date, tz).
        # Loop-invariant across the streams of a batch (#742/#747).
        self._in_window_memo: dict[tuple[int, Any, Any], tuple[tuple[str, Event], ...]] = {}
        # Token index per in-window candidate tuple (#747), keyed by identity.
        # Shared by every stream in the batch — see candidate_index.
        self._token_index_memo: dict[int, CandidateTokenIndex] = {}
        # league -> count per candidate tuple, for the narrowed fixture count (#747).
        self._league_counts_memo: dict[int, dict[str, int]] = {}
        # Global team identity index (epic goax), built lazily on first use so
        # matchers constructed without a db_factory (tests, racing/tennis paths)
        # cost nothing. _identity_loaded distinguishes "not tried yet" from
        # "tried and unavailable".
        self._identity_index: TeamIdentityIndex | None = None
        self._identity_loaded = False

    def reload_aliases(self) -> None:
        """Reload aliases from database.

        Call this after alias CRUD operations to update the in-memory caches.
        Rebuilds both the forward cache (alias, league) -> canonical and
        the reverse cache alias -> [(canonical, league), ...].
        """
        self._user_aliases = self._load_user_aliases()
        self._reverse_aliases = self._build_reverse_cache()
        self._alias_resolve_cache.clear()
        logger.info(
            "[ALIAS] Reloaded aliases: %d forward, %d reverse entries",
            len(self._user_aliases),
            len(self._reverse_aliases),
        )

    def match_single_league(
        self,
        classified: ClassifiedStream,
        league: str,
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
        sport_durations: dict[str, float] | None = None,
        stream_tz: ZoneInfo | None = None,
        anchor_dt: "datetime | None" = None,
    ) -> MatchOutcome:
        """Single-league matching - search only the specified league.

        Used for team EPG where the league is known from the team config.

        Args:
            classified: Pre-classified stream
            league: Authoritative league code
            target_date: Date to match events for
            group_id: Event group ID (for caching)
            stream_id: Stream ID (for caching)
            generation: Cache generation counter
            user_tz: User timezone for date validation
            sport_durations: Sport duration settings for ongoing event detection
            stream_tz: Timezone for interpreting stream dates (from stream or group)

        Returns:
            MatchOutcome with result
        """
        if classified.category != StreamCategory.TEAM_VS_TEAM:
            return MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
            )

        ctx = MatchContext(
            stream_name=classified.normalized.original,
            stream_id=stream_id,
            group_id=group_id,
            target_date=target_date,
            generation=generation,
            user_tz=user_tz,
            stream_tz=stream_tz,
            classified=classified,
            team1=classified.team1,
            team2=classified.team2,
            sport_durations=sport_durations or {},
            anchor_dt=anchor_dt,
        )

        # Check cache first
        cache_result = self._check_cache(ctx)
        if cache_result:
            return cache_result

        # Fetch events from MATCH_WINDOW_DAYS back to days_ahead
        # - Today + future: fetch from API (ESPN)
        # - Past: always use cache
        # - TSDB leagues: always cache-only
        is_tsdb = self._service.get_provider_name(league) == "tsdb"
        events = []
        for offset in range(-MATCH_WINDOW_DAYS, self._days_ahead + 1):
            fetch_date = target_date + timedelta(days=offset)
            # Today and future: fetch from API; Past/TSDB: cache only
            cache_only = is_tsdb or offset < 0
            events.extend(self._service.get_events(league, fetch_date, cache_only=cache_only))

        if not events:
            return MatchOutcome.failed(
                FailedReason.NO_EVENT_FOUND,
                stream_name=ctx.stream_name,
                stream_id=stream_id,
                detail=f"No events in {league} for {target_date}",
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # Try to match (is_event_ongoing filters out completed yesterday events)
        result = self._match_against_events(ctx, events, league)

        # Cache successful matches
        if result.is_matched and result.event:
            self._cache_result(ctx, result)

        return result

    def match_multi_league(
        self,
        classified: ClassifiedStream,
        enabled_leagues: list[str],
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
        sport_durations: dict[str, float] | None = None,
        prefetched_events: dict[str, list["Event"]] | None = None,
        stream_tz: ZoneInfo | None = None,
        anchor_dt: "datetime | None" = None,
    ) -> MatchOutcome:
        """Multi-league matching with league hint detection.

        Used for event EPG groups with multiple leagues configured.

        Strategy:
        1. Check cache
        2. Detect league hint from stream name
           - If hint not in enabled_leagues → FILTERED:LEAGUE_NOT_INCLUDED
           - If hint in enabled_leagues → search only that league
        3. If no hint, search all enabled leagues
        4. Match and cache

        Args:
            classified: Pre-classified stream
            enabled_leagues: List of league codes enabled for this group
            target_date: Date to match events for
            group_id: Event group ID (for caching)
            stream_id: Stream ID (for caching)
            generation: Cache generation counter
            user_tz: User timezone for date validation
            sport_durations: Sport duration settings for ongoing event detection
            prefetched_events: Optional pre-fetched events by league (for performance)
            stream_tz: Timezone for interpreting stream dates (from stream or group)

        Returns:
            MatchOutcome with result
        """
        if classified.category != StreamCategory.TEAM_VS_TEAM:
            return MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
            )

        ctx = MatchContext(
            stream_name=classified.normalized.original,
            stream_id=stream_id,
            group_id=group_id,
            target_date=target_date,
            generation=generation,
            user_tz=user_tz,
            stream_tz=stream_tz,
            classified=classified,
            team1=classified.team1,
            team2=classified.team2,
            sport_durations=sport_durations or {},
            anchor_dt=anchor_dt,
        )

        # Check cache first
        cache_result = self._check_cache(ctx)
        if cache_result:
            return cache_result

        # Detect league hint (can be single league or list for umbrella brands like EFL)
        league_hint = classified.league_hint

        if league_hint:
            # Normalize to list for uniform handling
            hint_leagues = [league_hint] if isinstance(league_hint, str) else league_hint
            # Filter to only leagues that are enabled for this group
            valid_leagues = [lg for lg in hint_leagues if lg in enabled_leagues]

            if not valid_leagues:
                # None of the hinted leagues are enabled
                hint_display = (
                    league_hint if isinstance(league_hint, str) else ", ".join(league_hint)
                )
                return MatchOutcome.filtered(
                    FilteredReason.LEAGUE_NOT_INCLUDED,
                    stream_name=ctx.stream_name,
                    stream_id=stream_id,
                    detail=f"League '{hint_display}' not in enabled leagues",
                )
            # Narrow search to valid hinted leagues
            leagues_to_search = valid_leagues
        else:
            # No hint, search all enabled leagues
            leagues_to_search = enabled_leagues

        # Use prefetched events if available (much faster for multi-stream matching)
        # Otherwise, fetch events: use full 30-day cache for matching
        all_events: Sequence[tuple[str, Event]] = []

        if prefetched_events:
            # Use pre-fetched events (already fetched once for all streams)
            all_events = self._prefetched_candidates(prefetched_events, leagues_to_search)
        else:
            # Fallback: fetch events per-stream (slower, used when no prefetch)
            for league in leagues_to_search:
                is_tsdb = self._service.get_provider_name(league) == "tsdb"
                for offset in range(-MATCH_WINDOW_DAYS, self._days_ahead + 1):
                    fetch_date = target_date + timedelta(days=offset)
                    # Today and future: fetch from API; Past/TSDB: cache only
                    cache_only = is_tsdb or offset < 0
                    events = self._service.get_events(league, fetch_date, cache_only=cache_only)
                    for event in events:
                        all_events.append((league, event))

        if not all_events:
            return MatchOutcome.failed(
                FailedReason.NO_EVENT_FOUND,
                stream_name=ctx.stream_name,
                stream_id=stream_id,
                detail=f"No events in any league for {target_date}",
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # Try to match against all events
        result = self._match_against_multi_league_events(ctx, all_events)

        # If match failed with NO_EVENT_FOUND, try reverse alias resolution
        # This handles cases where classifier couldn't detect league but user has aliases
        if result.is_failed and result.failed_reason in (
            FailedReason.NO_EVENT_FOUND,
            FailedReason.CANDIDATES_GATED,
        ):
            retry_result = self._try_reverse_alias_match(ctx, all_events, leagues_to_search)
            if retry_result and retry_result.is_matched:
                result = retry_result

        # Cache successful matches
        if result.is_matched and result.event:
            self._cache_result(ctx, result)

        return result

    def match_team_only(
        self,
        classified: ClassifiedStream,
        enabled_leagues: list[str],
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
        sport_durations: dict[str, float] | None = None,
        prefetched_events: dict[str, list[Event]] | None = None,
        stream_tz: ZoneInfo | None = None,
        anchor_dt: "datetime | None" = None,
    ) -> list[MatchOutcome]:
        """Match a single-team branded stream (TEAM_ONLY) to all its events in the window.

        Unlike TEAM_VS_TEAM, the stream carries one team's brand (e.g.
        "NHL | Toronto Maple Leafs") and should be added to every event where
        that team plays within the date window. Returns one MatchOutcome per
        matched event so the caller can fan out to multiple channels.

        Args:
            classified: Pre-classified stream (category must be TEAM_ONLY)
            enabled_leagues: League codes subscribed for this group
            target_date: Date to anchor the search window
            group_id: Event group ID (for caching)
            stream_id: Stream ID (for caching)
            generation: Cache generation counter
            user_tz: User timezone for date validation
            sport_durations: Sport duration settings
            prefetched_events: Optional pre-fetched events by league
            stream_tz: Timezone for interpreting stream dates

        Returns:
            List of MatchOutcome — one per matched event, or a single
            filtered/failed outcome if nothing matched.
        """
        if classified.category != StreamCategory.TEAM_ONLY:
            return [MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
            )]

        stream_name = classified.normalized.original

        # Narrow search by league hint (same logic as match_multi_league)
        league_hint = classified.league_hint
        if league_hint:
            hint_leagues = [league_hint] if isinstance(league_hint, str) else league_hint
            valid_leagues = [lg for lg in hint_leagues if lg in enabled_leagues]
            if not valid_leagues:
                hint_display = (
                    league_hint if isinstance(league_hint, str) else ", ".join(league_hint)
                )
                return [MatchOutcome.filtered(
                    FilteredReason.LEAGUE_NOT_INCLUDED,
                    stream_name=stream_name,
                    stream_id=stream_id,
                    detail=f"League '{hint_display}' not in enabled leagues",
                )]
            leagues_to_search = valid_leagues
        else:
            leagues_to_search = enabled_leagues

        # Narrow date window to ±2 days to minimise false positives.
        window_days = 2
        all_events: Sequence[tuple[str, Event]] = []
        if prefetched_events:
            all_events = self._prefetched_candidates(
                prefetched_events,
                leagues_to_search,
                window=_DateWindow(user_tz, target_date, window_days),
            )
        else:
            is_tsdb_map = {
                lg: self._service.get_provider_name(lg) == "tsdb"
                for lg in leagues_to_search
            }
            for league in leagues_to_search:
                for offset in range(-window_days, window_days + 1):
                    fetch_date = target_date + timedelta(days=offset)
                    cache_only = is_tsdb_map[league] or offset < 0
                    events = self._service.get_events(league, fetch_date, cache_only=cache_only)
                    for event in events:
                        all_events.append((league, event))

        if not all_events:
            return [MatchOutcome.failed(
                FailedReason.NO_EVENT_FOUND,
                stream_name=stream_name,
                stream_id=stream_id,
                detail=f"No events in window ±{window_days}d for {target_date}",
                parsed_team1=classified.team1,
            )]

        team_norm = normalize_for_matching(classified.team1) if classified.team1 else None
        if not team_norm:
            return [MatchOutcome.failed(
                FailedReason.TEAMS_NOT_PARSED,
                stream_name=stream_name,
                stream_id=stream_id,
                detail="No team candidate extracted",
            )]

        matched_outcomes: list[MatchOutcome] = []
        seen_event_ids: set[str] = set()

        for league, event in all_events:
            if event.id in seen_event_ids:
                continue

            # EPG anchored matching (bead t5e): gate to the live occurrence near
            # the program's broadcast instant (excludes encores / wrong night).
            if anchor_dt is not None:
                anchor_skew = abs((event.start_time - anchor_dt).total_seconds())
                if anchor_skew > ANCHOR_MATCH_TOLERANCE_SECONDS:
                    continue
            score, side = self._score_single_team_against_event(team_norm, event)
            if score is None:
                continue
            seen_event_ids.add(event.id)
            logger.debug(
                "[TEAM_ONLY] Matched: stream_id=%d team='%s' event=%s league=%s conf=%.0f%%",
                stream_id,
                classified.team1,
                event.id,
                league,
                score,
            )
            matched_outcomes.append(MatchOutcome.matched(
                MatchMethod.FUZZY,
                event,
                detected_league=league,
                confidence=score / 100.0,
                stream_name=stream_name,
                stream_id=stream_id,
                parsed_team1=classified.team1,
                # Which event side the branded team is (#489) — the lifecycle
                # persists that side's team id per-stream for ordering rules.
                matched_side=side,
            ))

        if matched_outcomes:
            return matched_outcomes

        return [MatchOutcome.failed(
            FailedReason.NO_EVENT_FOUND,
            stream_name=stream_name,
            stream_id=stream_id,
            detail=f"No event found for team '{classified.team1}'",
            parsed_team1=classified.team1,
        )]

    def match_all_star(
        self,
        classified: ClassifiedStream,
        enabled_leagues: list[str],
        target_date: date,
        group_id: int,
        stream_id: int,
        generation: int,
        user_tz: ZoneInfo,
        sport_durations: dict[str, float] | None = None,
        prefetched_events: dict[str, list[Event]] | None = None,
        stream_tz: ZoneInfo | None = None,
        anchor_dt: "datetime | None" = None,
    ) -> list[MatchOutcome]:
        """Match an All-Star stream (ALL_STAR) to the league's All-Star event.

        ESPN serves All-Star games inside the normal league scoreboard as two
        pseudo-teams whose names both carry an "All-Star(s)" token. We resolve
        the classified stream to the event in the hinted league(s) whose
        competitors are both All-Star squads (see ``is_all_star_event``) —
        name-agnostic, so the yearly-varying opponent needs no hardcoding.
        There is one All-Star event per league per season, so this normally
        returns a single outcome.

        Args:
            classified: Pre-classified stream (category must be ALL_STAR)
            enabled_leagues: League codes subscribed for this group
            target_date: Date to anchor the search window
            group_id: Event group ID (unused; kept for call-site symmetry)
            stream_id: Stream ID (for logging/outcomes)
            generation: Cache generation counter (unused; symmetry)
            user_tz: User timezone for the date window
            sport_durations: Sport duration settings (unused; symmetry)
            prefetched_events: Optional pre-fetched events by league
            stream_tz: Timezone for interpreting stream dates (unused; symmetry)
            anchor_dt: EPG path — gate to the live occurrence near this instant

        Returns:
            List of MatchOutcome — one per matched All-Star event, or a single
            filtered/failed outcome if nothing matched.
        """
        if classified.category != StreamCategory.ALL_STAR:
            return [MatchOutcome.filtered(
                FilteredReason.NOT_EVENT,
                stream_name=classified.normalized.original,
                stream_id=stream_id,
            )]

        stream_name = classified.normalized.original

        # An ALL_STAR classification always carries a league hint (enforced by
        # the classifier); narrow to the hinted leagues this group subscribes to.
        league_hint = classified.league_hint
        hint_leagues = (
            [league_hint] if isinstance(league_hint, str) else list(league_hint or [])
        )
        leagues_to_search = [lg for lg in hint_leagues if lg in enabled_leagues]
        if not leagues_to_search:
            hint_display = ", ".join(hint_leagues) if hint_leagues else "?"
            return [MatchOutcome.filtered(
                FilteredReason.LEAGUE_NOT_INCLUDED,
                stream_name=stream_name,
                stream_id=stream_id,
                detail=f"League '{hint_display}' not in enabled leagues",
            )]

        # Narrow date window to ±2 days to minimise false positives.
        window_days = 2
        all_events: Sequence[tuple[str, Event]] = []
        if prefetched_events:
            all_events = self._prefetched_candidates(
                prefetched_events,
                leagues_to_search,
                window=_DateWindow(user_tz, target_date, window_days),
            )
        else:
            for league in leagues_to_search:
                is_tsdb = self._service.get_provider_name(league) == "tsdb"
                for offset in range(-window_days, window_days + 1):
                    fetch_date = target_date + timedelta(days=offset)
                    cache_only = is_tsdb or offset < 0
                    events = self._service.get_events(league, fetch_date, cache_only=cache_only)
                    for event in events:
                        all_events.append((league, event))

        matched_outcomes: list[MatchOutcome] = []
        seen_event_ids: set[str] = set()
        for league, event in all_events:
            if event.id in seen_event_ids:
                continue
            if not is_all_star_event(event):
                continue
            # EPG anchored matching (bead t5e): gate to the live occurrence near
            # the program's broadcast instant.
            if anchor_dt is not None:
                anchor_skew = abs((event.start_time - anchor_dt).total_seconds())
                if anchor_skew > ANCHOR_MATCH_TOLERANCE_SECONDS:
                    continue
            seen_event_ids.add(event.id)
            logger.debug(
                "[ALL_STAR] Matched: stream_id=%d league=%s event=%s (%s vs %s)",
                stream_id,
                league,
                event.id,
                event.away_team.name,
                event.home_team.name,
            )
            matched_outcomes.append(MatchOutcome.matched(
                MatchMethod.FUZZY,
                event,
                detected_league=league,
                confidence=1.0,
                stream_name=stream_name,
                stream_id=stream_id,
            ))

        if matched_outcomes:
            return matched_outcomes

        return [MatchOutcome.failed(
            FailedReason.NO_EVENT_FOUND,
            stream_name=stream_name,
            stream_id=stream_id,
            detail=f"No All-Star event in window ±{window_days}d for {target_date}",
        )]

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    def _prefetched_candidates(
        self,
        prefetched_events: dict[str, list[Event]],
        leagues_to_search: list[str],
        *,
        window: _DateWindow | None = None,
    ) -> tuple[tuple[str, Event], ...]:
        """Flattened ``[(league, event)]`` candidates, memoized for the batch.

        Every stream used to rebuild this list from the same prefetch — tens of
        thousands of tuple allocations per stream, plus an ``astimezone`` per
        event for the date-windowed callers. The list depends only on the
        prefetch, the leagues being searched and the window, and streams share
        those (the league set varies only when a stream carries a league hint),
        so it is built once per distinct key instead of once per stream.

        Returns a tuple, not a list: the result is shared by every stream in
        the batch, so a caller that mutated it would corrupt the candidates of
        every stream after it. Immutability makes that a TypeError instead of
        a quietly wrong guide.

        Freshness rests on ``prefetched_events`` being *replaced* rather than
        mutated in place — see the note at its producer,
        ``StreamMatcher._prefetch_events``.
        """
        if self._candidates_source is not prefetched_events:
            self._candidates_source = prefetched_events
            self._candidates_memo = {}
            self._in_window_memo = {}
            self._token_index_memo = {}
            self._league_counts_memo = {}

        key = (tuple(leagues_to_search), window)
        cached = self._candidates_memo.get(key)
        if cached is not None:
            return cached

        candidates: list[tuple[str, Event]] = []
        for league in leagues_to_search:
            for event in prefetched_events.get(league, ()):
                if window is not None:
                    event_date = _local_date(event.start_time, window.user_tz)
                    if abs((event_date - window.target_date).days) > window.days:
                        continue
                candidates.append((league, event))

        frozen = tuple(candidates)
        self._candidates_memo[key] = frozen
        return frozen

    def _check_cache(self, ctx: MatchContext) -> MatchOutcome | None:
        """Check cache for existing match.

        User-corrected entries are always trusted (pinned).
        Algorithmic entries are validated against date.
        """
        # Anchored (EPG) matches are keyed only by title in the cache, but two
        # programs with the same title (a series' Game 1/Game 2, or a live airing
        # + its encore) must resolve to different events by their own instant.
        # Skip the cache so each program is matched fresh against its anchor.
        if ctx.anchor_dt is not None:
            return None

        entry = self._cache.get(ctx.group_id, ctx.stream_id, ctx.stream_name)
        if not entry:
            return None

        # Touch the cache entry to keep it fresh
        self._cache.touch(ctx.group_id, ctx.stream_id, ctx.stream_name, ctx.generation)

        # Reconstruct event from cached data
        event = self._reconstruct_event(entry.cached_data)
        if not event:
            # Cache entry is invalid
            logger.debug(
                "[MATCH_CACHE] Invalid: failed to reconstruct event for stream=%d", ctx.stream_id
            )
            self._cache.delete(ctx.group_id, ctx.stream_id, ctx.stream_name)
            return None

        # User-corrected entries are pinned - always trust them regardless of date
        if entry.user_corrected:
            logger.debug(
                "[CACHE_HIT] stream_id=%d event=%s (user corrected)",
                ctx.stream_id,
                event.id,
            )
            return MatchOutcome.matched(
                MatchMethod.USER_CORRECTED,
                event,
                detected_league=entry.league,
                confidence=1.0,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # V1 Parity: Cached events from yesterday should be re-matched to get fresh status.
        # The cached event has OLD status from when it was cached, which may have
        # changed to "final". Re-matching ensures we get current status from ESPN.
        event_date = event.start_time.astimezone(ctx.user_tz).date()
        if event_date < ctx.target_date:
            # Event is from a previous day - invalidate cache to get fresh status
            logger.debug(
                "[MATCH_CACHE] Stale: event from %s < target %s", event_date, ctx.target_date
            )
            return None

        # Today's events: use cache (final status handled in _outcome_to_result)
        if event_date != ctx.target_date:
            logger.debug(
                "[MATCH_CACHE] Mismatch: event from %s != target %s", event_date, ctx.target_date
            )
            return None

        logger.debug(
            "[CACHE_HIT] stream_id=%d event=%s",
            ctx.stream_id,
            event.id,
        )
        return MatchOutcome.matched(
            MatchMethod.CACHE,
            event,
            detected_league=entry.league,
            confidence=1.0,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            parsed_team1=ctx.team1,
            parsed_team2=ctx.team2,
            origin_match_method=entry.match_method,  # Original method (fuzzy, alias, etc.)
        )

    def _match_against_events(
        self,
        ctx: MatchContext,
        events: list[Event],
        league: str,
    ) -> MatchOutcome:
        """Single-league entry point: every candidate carries ``league``."""
        return self._match_against_candidates(ctx, [(league, event) for event in events])

    def _match_against_multi_league_events(
        self,
        ctx: MatchContext,
        events: Sequence[tuple[str, Event]],
    ) -> MatchOutcome:
        """Multi-league entry point: candidates already carry their league."""
        return self._match_against_candidates(ctx, events)

    def _league_counts(
        self, candidates: Sequence[tuple[str, Event]]
    ) -> dict[str, int]:
        """``league -> candidate count``, memoized for the shared batch tuple (#747)."""
        if not isinstance(candidates, tuple):
            counts: dict[str, int] = {}
            for _lg, event in candidates:
                counts[event.league] = counts.get(event.league, 0) + 1
            return counts

        cached = self._league_counts_memo.get(id(candidates))
        if cached is None:
            cached = {}
            for _lg, event in candidates:
                cached[event.league] = cached.get(event.league, 0) + 1
            self._league_counts_memo[id(candidates)] = cached
        return cached

    def _fixture_rejected_outside(
        self,
        in_window: Sequence[tuple[str, Event]],
        kept: Sequence[tuple[str, Event]],
        fixture_leagues: set[str],
        hinted_leagues: set[str],
    ) -> int:
        """How many narrowed-away candidates the fixture gate would have vetoed.

        See the call site for why this exists. Counting by league rather than by
        candidate is exact for the gate itself, because ``_fixture_vetoes`` reads
        nothing but the league.
        """
        total = self._league_counts(in_window)
        kept_counts = self._league_counts(kept)
        rejected = 0
        for league, count in total.items():
            remaining = count - kept_counts.get(league, 0)
            if not remaining or league in hinted_leagues:
                continue
            if self._fixture_vetoes(fixture_leagues, league):
                rejected += remaining
        return rejected

    def _in_window_candidates(
        self,
        ctx: MatchContext,
        events: Sequence[tuple[str, Event]],
    ) -> Sequence[tuple[str, Event]]:
        """``events`` restricted to the search window, memoized per batch (#742/#747).

        The window depends only on the candidate set and the batch's
        target_date/timezone, never on the stream, so every stream in a batch
        gets the same answer. Memoizing it removes a full pass per stream — and
        it is what gives the token index a candidate sequence with a STABLE
        identity to key on, which is the difference between building the index
        once per batch and once per stream.

        Only the shared tuple from ``_prefetched_candidates`` is memoized. A
        per-stream list (the single-league fallback) has no stable identity, so
        it is filtered inline exactly as before.
        """
        if not isinstance(events, tuple):
            return [pair for pair in events if ctx.is_event_in_search_window(pair[1])]

        key = (id(events), ctx.target_date, ctx.user_tz)
        cached = self._in_window_memo.get(key)
        if cached is None:
            cached = tuple(
                pair for pair in events if ctx.is_event_in_search_window(pair[1])
            )
            self._in_window_memo[key] = cached
        return cached

    def _narrow_candidates(
        self,
        ctx: MatchContext,
        events: Sequence[tuple[str, Event]],
    ) -> Sequence[tuple[str, Event]]:
        """Shrink the candidate list to events sharing a word with the stream (#747).

        Returns ``events`` unchanged whenever the index cannot speak, so this is
        an optimization that can only remove candidates no scorer could have
        accepted — an event sharing no word with the stream cannot clear a
        ``token_set_ratio`` floor.

        Deliberately narrow in scope:

        * **Off by default** — gated on ``TEAMARR_TOKEN_INDEX``.
        * **TEAM_VS_TEAM only.** Tennis, racing and event-card streams match
          through their own routes, and the safety measurement behind this did
          not cover them; they keep the full scan.
        * **Only the shared candidate tuple.** ``_prefetched_candidates``
          returns one tuple per batch, so the index is built once and reused by
          every stream. A per-stream list (the single-league fallback path) is
          both unindexed and already small, so it is left alone — building an
          index for one stream would cost more than the scan it saves.
        * **No tokens, no opinion.** A stream that tokenizes to nothing keeps
          the full list rather than matching nothing.
        """
        if not _token_index_enabled():
            return events
        if ctx.classified.category != StreamCategory.TEAM_VS_TEAM:
            return events
        if not isinstance(events, tuple):
            return events

        tokens = tokenize(f"{ctx.team1 or ''} {ctx.team2 or ''}")
        if not tokens:
            return events

        index = self._token_index_memo.get(id(events))
        if index is None:
            index = CandidateTokenIndex(events)
            self._token_index_memo[id(events)] = index

        narrowed = index.narrow(tokens, events)
        return events if narrowed is None else narrowed

    def _match_against_candidates(
        self,
        ctx: MatchContext,
        events: Sequence[tuple[str, Event]],
    ) -> MatchOutcome:
        """Score a stream against ``(league, event)`` candidates and pick the best.

        The ONE candidate loop (#660). Both the single-league and multi-league
        entry points delegate here, so a gate or fallback added to matching
        lands on every source type at once. It used to be two ~250-line
        copies that were 89% identical and had already drifted: the #627
        league-hint hatch below was added to the multi-league copy only, and
        the single-league copy — the one an "NCAAF only" source uses — kept
        vetoing (#650). ``TestPathParity`` in ``tests/matching/test_fixture_gate.py``
        pins the two entry points to the same verdict.

        Uses whole-name token_set_ratio matching with the following strategy:
        1. Try alias match first (100% confidence for known abbreviations)
        2. Fall back to token_set_ratio between extracted teams and event name
        3. If no match, strip parentheticals from raw names and retry
           (handles noise like "(Baseball)", "(Available outside Ottawa Region)"
           without breaking legitimate disambiguators like "Miami (OH)")
        4. Rank by: score > time proximity > date proximity
        """
        team1_normalized = normalize_for_matching(ctx.team1) if ctx.team1 else None
        team2_normalized = normalize_for_matching(ctx.team2) if ctx.team2 else None

        if not team1_normalized and not team2_normalized:
            return MatchOutcome.failed(
                FailedReason.TEAMS_NOT_PARSED,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                detail="No team names extracted",
            )

        # Pre-compute parenthetical-stripped versions from RAW names for fallback.
        # normalize_for_matching strips parens as punctuation, so we must strip
        # from the raw names first, then normalize — otherwise the fallback
        # can never detect that parentheticals were removed.
        fallback_t1, fallback_t2, has_stripped_fallback = self._prepare_stripped_fallback(
            ctx.team1, ctx.team2, team1_normalized, team2_normalized
        )
        pipe_t1, pipe_t2, has_pipe_fallback = self._prepare_pipe_fallback(
            ctx.team1, ctx.team2, team1_normalized, team2_normalized
        )

        # Check if we have date validation from the stream
        has_date_validation = ctx.classified.normalized.extracted_date is not None

        best_match: Event | None = None
        best_league: str | None = None
        best_method: MatchMethod = MatchMethod.FUZZY
        best_confidence: float = 0.0
        best_is_future: bool = False  # Whether best match is today or future
        best_date_distance: int = 999  # Absolute days from target_date
        best_time_distance: int = 999999  # Seconds from stream time (for doubleheaders)
        best_anchor_dist: int = 999999999  # Seconds from EPG anchor (bead t5e)
        best_stream_date_dist: int = 999  # Days from the stream's declared date (#474)
        date_rejected = 0  # Candidates gated by a trusted stream date (#474)
        fixture_rejected = 0  # Candidates in a league these teams never meet in
        # Candidates skipped before scoring (window / EPG anchor / sport hint),
        # and the ones that actually reached the scorer (#662). The near-miss
        # summary reads the latter: reporting 100/100 for an event the loop
        # never scored sent triage after the fixture gate for a window miss.
        gated_rejected = 0
        scored: list[Event] = []

        # Which leagues could these two sides actually play each other in (epic
        # goax)? Resolved once per stream, not per candidate — it depends only on
        # the stream's own names. None = identity resolution had nothing to say.
        # A multi-league source offers candidates from every league it covers,
        # so a shared city has many more wrong events to land on there.
        fixture_leagues = self._fixture_leagues(ctx)


        league_hint = ctx.classified.league_hint
        hinted_leagues = (
            {league_hint} if isinstance(league_hint, str) else set(league_hint or [])
        )

        # Validate events are within the search window (lifecycle handles
        # exclusions). Hoisted out of the loop below (#742): the window depends
        # only on ctx.target_date and the event, so re-deciding it for every
        # STREAM in the group re-answered the same question 772k times in a
        # profiled run — and rejected nothing, because the prefetch is bounded
        # by the same MATCH_WINDOW_DAYS this gate tests. It stays as a real
        # gate rather than being deleted: candidates also arrive from
        # shared_events, filled by other groups whose target_date may differ.
        in_window = self._in_window_candidates(ctx, events)
        gated_rejected += len(events) - len(in_window)

        # Token narrowing (#747) runs AFTER the window gate on purpose, so
        # gated_rejected keeps counting exactly what it always did and the
        # failure taxonomy below reads the same with the flag on or off.
        candidates = self._narrow_candidates(ctx, in_window)
        narrowed_away = len(in_window) - len(candidates)

        # Narrowing removed candidates the loop would have counted, and the
        # fixture gate is the one whose count the user sees: a stream naming two
        # real teams that never meet reports FIXTURE_NOT_IN_LEAGUE rather than a
        # generic "no event found" — the difference between a useful answer and
        # sending someone hunting for a scheduling gap that isn't there (epic
        # goax). Restore it without re-walking candidates: _fixture_vetoes reads
        # nothing but the LEAGUE, so per-league counts answer it in O(leagues).
        #
        # This mirrors the loop's own gate exactly, league-hint hatch (#627)
        # included — dropping the hatch flipped one measured stream from
        # NO_EVENT_FOUND to FIXTURE_NOT_IN_LEAGUE. It remains an approximation
        # in one direction only: a narrowed-away candidate that the anchor or
        # sport-hint gate would have rejected before the fixture gate is counted
        # here as a fixture rejection. Those fire on EPG-anchored matches and on
        # sport-hinted streams without a league hint, and across the measured
        # corpus every stream now reports the reason the full scan reports.
        if narrowed_away and fixture_leagues is not None:
            fixture_rejected += self._fixture_rejected_outside(
                in_window, candidates, fixture_leagues, hinted_leagues
            )

        for league, event in candidates:
            # EPG anchored matching (bead t5e): the candidate must air within the
            # tolerance of the program's broadcast instant, else it is a different
            # occurrence — an encore/replay or the next game in the series. This is
            # the definitive, category-independent guard against encore binding.
            anchor_dist = 0
            if ctx.anchor_dt is not None:
                anchor_dist = abs(int((event.start_time - ctx.anchor_dt).total_seconds()))
                if anchor_dist > ANCHOR_MATCH_TOLERANCE_SECONDS:
                    gated_rejected += 1
                    continue

            event_date = _local_date(event.start_time, ctx.user_tz)

            # Date validation from the stream (#474). A trusted date (built-in
            # extraction, declared component groups, or a learned per-source
            # format) gates candidates with ±1 day of tolerance for provider
            # timezone day-boundaries. An untrusted date (blind per-string
            # format guess) never rejects — it ranks candidates instead, so a
            # misread date can no longer zero out the whole group.
            stream_date_dist = 0
            if ctx.classified.normalized.extracted_date:
                # The date in the stream name is in the provider's timezone
                compare_tz = ctx.stream_tz or ctx.user_tz
                event_date_in_stream_tz = _local_date(event.start_time, compare_tz)
                stream_date_dist = abs(
                    (
                        ctx.classified.normalized.extracted_date
                        - event_date_in_stream_tz
                    ).days
                )

            # Check for sport mismatch from stream (if detected)
            # Skip when league hint is present - league is more specific and avoids
            # sport naming inconsistencies (e.g., "Football" vs "soccer")
            if ctx.classified.sport_hint and not ctx.classified.league_hint:
                if not _sport_hint_matches(ctx.classified.sport_hint, event.sport):
                    gated_rejected += 1
                    continue

            # Fixture gate (epic goax). Both stream sides name real teams, and
            # this event's league is not one where they could meet — so no score
            # against it can be meaningful. This is what stops an NHL stream from
            # riding a shared city into an MLB channel: "Tampa Bay Lightning" vs
            # "Tampa Bay Rays" scores 78 on text alone, but the Lightning play in
            # exactly one league and it is not this one.
            #
            # An explicit league hint already narrowed this stream to the
            # candidate's league (#627). It is stronger evidence than an
            # incomplete cross-league identity index; unhinted streams retain
            # the gate. The multi-league caller has already narrowed the search
            # to the hinted leagues, so there the hatch simply disables the gate
            # for hinted streams; in the single-league path it fires only when
            # the hint names the configured league — the honest guard (#660).
            if (
                self._fixture_vetoes(fixture_leagues, event.league)
                and event.league not in hinted_leagues
            ):
                fixture_rejected += 1
                continue

            scored.append(event)

            # Try alias match first (100% confidence)
            match_result = self._check_alias_match(team1_normalized, team2_normalized, event)

            # Fall back to whole-name matching using extracted teams
            if not match_result:
                match_result = self._match_teams_to_event(
                    team1_normalized, team2_normalized, event, has_date_validation
                )

            # Fallback: retry with parentheticals stripped from raw names
            # Handles noise like "(Baseball)", "(03.10 /4PM PT)" without
            # breaking legitimate disambiguators like "Miami (OH)" (tried above)
            if not match_result and has_stripped_fallback:
                match_result = self._match_teams_to_event(
                    fallback_t1, fallback_t2, event, has_date_validation
                )

            # Fallback: retry with pipe metadata trimmed (#652). Last tier, so
            # a name that matches intact never reaches it.
            if not match_result and has_pipe_fallback:
                match_result = self._match_teams_to_event(
                    pipe_t1, pipe_t2, event, has_date_validation
                )

            # Trusted-date gate (#474), applied AFTER team scoring (#480):
            # only candidates whose teams actually matched count as date
            # rejections, so DATE_MISMATCH is reported only when the date is
            # what killed an otherwise-good match — not whenever unrelated
            # games elsewhere in the window were skipped.
            if (
                match_result
                and stream_date_dist > 1
                and ctx.classified.normalized.extracted_date_trusted
            ):
                date_rejected += 1
                continue

            if match_result:
                method, score = match_result

                # Calculate date metrics for comparison
                days_from_target = (event_date - ctx.target_date).days
                is_future = days_from_target >= 0  # Today or future
                abs_distance = abs(days_from_target)

                # Calculate time proximity for doubleheader disambiguation
                # Use stream_tz if available - the time in stream name is in provider's timezone
                time_distance = 999999
                if ctx.classified.normalized.extracted_time:
                    time_tz = ctx.stream_tz or ctx.user_tz
                    ref_date = event.start_time.astimezone(time_tz).date()
                    stream_dt = datetime.combine(
                        ref_date, ctx.classified.normalized.extracted_time, tzinfo=time_tz
                    )
                    time_distance = abs(
                        int((event.start_time.astimezone(time_tz) - stream_dt).total_seconds())
                    )

                # Ranking: score > time proximity > future over past > date proximity.
                # For EPG anchored matches, nearest to the program instant wins
                # outright (the encore/series guard already gated the candidates).
                is_better = False
                if score > best_confidence:
                    is_better = True
                elif score == best_confidence:
                    if stream_date_dist != best_stream_date_dist:
                        # Agreement with the stream's declared date is the
                        # strongest equal-score disambiguator (#474)
                        is_better = stream_date_dist < best_stream_date_dist
                    elif ctx.anchor_dt is not None:
                        is_better = anchor_dist < best_anchor_dist
                    elif time_distance < best_time_distance:
                        # Closer to stream time wins (doubleheader case)
                        is_better = True
                    elif time_distance == best_time_distance:
                        if is_future and not best_is_future:
                            # Future beats past
                            is_better = True
                        elif is_future == best_is_future and abs_distance < best_date_distance:
                            # Same future/past status, prefer closer
                            is_better = True

                if is_better:
                    best_match = event
                    best_league = league
                    best_method = method
                    best_confidence = score
                    best_is_future = is_future
                    best_date_distance = abs_distance
                    best_anchor_dist = anchor_dist
                    best_time_distance = time_distance
                    best_stream_date_dist = stream_date_dist

        if best_match and best_league:
            logger.debug(
                "[MATCHED] stream_id=%d method=%s event=%s league=%s confidence=%.0f%%",
                ctx.stream_id,
                best_method.value,
                best_match.id,
                best_league,
                best_confidence,
            )
            return MatchOutcome.matched(
                best_method,
                best_match,
                detected_league=best_league,
                confidence=best_confidence / 100.0,
                stream_name=ctx.stream_name,
                stream_id=ctx.stream_id,
                parsed_team1=ctx.team1,
                parsed_team2=ctx.team2,
            )

        # No match found
        if team1_normalized and not team2_normalized:
            reason = FailedReason.TEAM2_NOT_FOUND
        elif team2_normalized and not team1_normalized:
            reason = FailedReason.TEAM1_NOT_FOUND
        elif date_rejected:
            # Candidates existed but every one was gated by the stream's
            # date — say so instead of a generic "no event found" (#474)
            reason = FailedReason.DATE_MISMATCH
        elif fixture_rejected:
            # The stream names two real teams and this league is not where they
            # meet (epic goax). "No event found" would send the user hunting for
            # a scheduling gap that isn't there.
            reason = FailedReason.FIXTURE_NOT_IN_LEAGUE
        elif gated_rejected and not scored and not narrowed_away:
            # Every candidate was skipped before scoring — nothing was ever
            # compared, so "no event found" would be a claim about scores that
            # never happened (#662).
            #
            # Token-narrowed candidates (#747) are excluded from this branch:
            # they were not "skipped before scoring" in the sense this reason
            # means. Sharing no word with the stream IS a comparison — it proves
            # the candidate could not clear the floor — so the honest answer
            # stays NO_EVENT_FOUND, which is what the same stream reports with
            # the flag off.
            reason = FailedReason.CANDIDATES_GATED
        else:
            reason = FailedReason.NO_EVENT_FOUND

        logger.debug(
            "[FAILED] stream_id=%d reason=%s teams=%s/%s",
            ctx.stream_id,
            reason.value,
            ctx.team1,
            ctx.team2,
        )
        near_miss = self._near_miss_summary(
            ctx,
            scored,
            team1_normalized,
            team2_normalized,
            date_rejected,
        )
        if gated_rejected:
            near_miss = f"{near_miss}; gated={gated_rejected}"
        logger.debug("[NEAR_MISS] stream_id=%d %s", ctx.stream_id, near_miss)
        return MatchOutcome.failed(
            reason,
            stream_name=ctx.stream_name,
            stream_id=ctx.stream_id,
            detail=near_miss,
            parsed_team1=ctx.team1,
            parsed_team2=ctx.team2,
        )

    def _near_miss_summary(
        self,
        ctx: MatchContext,
        candidates: list[Event],
        team1_norm: str | None,
        team2_norm: str | None,
        date_rejected: int,
    ) -> str:
        """Why the closest candidate lost, as one line (#480, persisted by #661).

        A bare "reason=no_event_found" hides everything a bug report needs:
        which candidate came closest, the per-side scores vs the threshold,
        and whether aliases resolved. This reports the single best-scoring
        candidate, enough to diagnose misses like 'D-backs' scoring 50 against
        the Diamondbacks.

        Returned rather than logged (#661). It used to go only to a DEBUG log,
        where two things destroyed it: installs that raise LOG_LEVEL never
        computed it at all, and the support bundle caps log tails at 256KB, so
        78 of 11,279 failures kept their line. Meanwhile the structured record
        the bundle ships complete had an empty `detail` column. The caller now
        puts this on the MatchOutcome, so the evidence travels with the failure.

        Cost: bounded at 300 candidates, and only ever runs on the failure
        path. The per-side scores come from the same memoized kernel the match
        loop just used, so the pairs are overwhelmingly cache hits.
        """

        def side(stream_norm: str | None, team) -> float:
            if not stream_norm:
                return 0.0
            if _is_short_code(stream_norm):
                return 100.0 if _abbrev_equals(stream_norm, team.abbreviation) else 0.0
            return _best_name_score(stream_norm, team)

        best: tuple[float, float, float, Event] | None = None
        for event in candidates[:300]:
            o1 = min(side(team1_norm, event.home_team), side(team2_norm, event.away_team))
            o2 = min(side(team1_norm, event.away_team), side(team2_norm, event.home_team))
            pair = max(
                (o1, side(team1_norm, event.home_team), side(team2_norm, event.away_team)),
                (o2, side(team1_norm, event.away_team), side(team2_norm, event.home_team)),
            )
            if best is None or pair[0] > best[0]:
                best = (pair[0], pair[1], pair[2], event)

        if best is None:
            return f"no candidates in window; date_gated={date_rejected}"

        _, s1, s2, event = best
        # Resolve against the candidate's league — user aliases are
        # league-scoped, so league=None under-reports ("alias2=none" for an
        # alias that WOULD fire in the real path).
        alias1 = self._resolve_alias(team1_norm, event.league) if team1_norm else None
        alias2 = self._resolve_alias(team2_norm, event.league) if team2_norm else None
        summary = (
            f"best='{event.home_team.name} vs {event.away_team.name}' "
            f"({event.league} {event.start_time.date()}) "
            f"scores {ctx.team1}={s1:.0f} / {ctx.team2}={s2:.0f} "
            f"(need {BOTH_TEAMS_THRESHOLD:.0f}) "
            f"alias1={alias1 or 'none'} alias2={alias2 or 'none'} "
            f"date_gated={date_rejected}"
        )
        return summary[:NEAR_MISS_DETAIL_MAX]

    def _check_abbreviation_match(
        self,
        team1: str | None,
        team2: str | None,
        event: Event,
    ) -> tuple[MatchMethod, float] | None:
        """Check if stream teams exactly match event team abbreviations as tokens.

        Handles tournament-style streams where team codes appear as tokens:
        "SWE" matches abbreviation "SWE", "ITA (M Group B)" contains token "ita"
        matching "ITA".

        With BOTH teams extracted, 2-letter abbreviations are allowed (#472):
        requiring the two stream teams to hit DIFFERENT event abbreviations
        makes noise hits vanishingly unlikely, and MLB's official codes (SF,
        SD, KC, TB) are 2 letters — the old >=3 guard made those teams
        unmatchable by code. Single-team streams keep the >=3 guard (a lone
        2-letter token really is noise-prone). Well-known alternate codes
        (AZ for ARI, Baseball-Reference forms) resolve via
        ALTERNATE_TEAM_CODES.
        """
        home_abbr = (
            normalize_text(event.home_team.abbreviation)
            if event.home_team.abbreviation
            else ""
        )
        away_abbr = (
            normalize_text(event.away_team.abbreviation)
            if event.away_team.abbreviation
            else ""
        )

        if not home_abbr or not away_abbr or len(home_abbr) < 2 or len(away_abbr) < 2:
            return None

        t1_tokens = _code_tokens(team1) if team1 else frozenset()
        t2_tokens = _code_tokens(team2) if team2 else frozenset()

        # Both teams must match different event teams
        if team1 and team2:
            def _valid_abbr_hit(abbr: str, tokens: frozenset[str]) -> bool:
                if not abbr:
                    return False
                if abbr in ABBREVIATION_STOPWORDS and len(tokens) > 1:
                    return False
                return abbr in tokens

            opt1 = _valid_abbr_hit(home_abbr, t1_tokens) and _valid_abbr_hit(away_abbr, t2_tokens)
            opt2 = _valid_abbr_hit(away_abbr, t1_tokens) and _valid_abbr_hit(home_abbr, t2_tokens)
            if opt1 or opt2:
                return (MatchMethod.FUZZY, 100.0)
        elif len(home_abbr) >= 3 and len(away_abbr) >= 3:
            if team1:
                if (
                    home_abbr not in ABBREVIATION_STOPWORDS
                    and home_abbr in t1_tokens
                ) or (
                    away_abbr not in ABBREVIATION_STOPWORDS
                    and away_abbr in t1_tokens
                ):
                    return (MatchMethod.FUZZY, 100.0)
            elif team2:
                if (
                    home_abbr not in ABBREVIATION_STOPWORDS
                    and home_abbr in t2_tokens
                ) or (
                    away_abbr not in ABBREVIATION_STOPWORDS
                    and away_abbr in t2_tokens
                ):
                    return (MatchMethod.FUZZY, 100.0)

        return None

    def _match_teams_to_event(
        self,
        team1: str | None,
        team2: str | None,
        event: Event,
        has_date_validation: bool = False,
    ) -> tuple[MatchMethod, float] | None:
        """Match extracted team names against event teams.

        When both teams are extracted, requires BOTH to match different event teams.
        This prevents "Marist vs Sacred Heart" from matching "Jessup vs Sacred Heart"
        just because one team name overlaps.

        Args:
            team1: First extracted team name (normalized)
            team2: Second extracted team name (normalized)
            event: Event to match against
            has_date_validation: True if stream has extracted date (lower threshold)

        Returns:
            Tuple of (method, confidence) if matched, None otherwise
        """
        # Try exact abbreviation token match (tournament/international streams)
        abbr_result = self._check_abbreviation_match(team1, team2, event)
        if abbr_result:
            return abbr_result

        # Try fuzzy matching with team names
        return self._score_teams_against_event(team1, team2, event)

    @staticmethod
    def _strip_parentheticals(name: str) -> str:
        """Strip parenthetical content from team name.

        Used as fallback when matching fails with parentheticals intact.
        Example: "Ottawa (Available outside region)" → "Ottawa"
                 "Texas State (Baseball) (03.10 /4PM PT)" → "Texas State"
        """
        return re.sub(r"\s*\([^)]*\)", "", name).strip()

    def _prepare_stripped_fallback(
        self,
        raw_team1: str | None,
        raw_team2: str | None,
        norm_team1: str | None,
        norm_team2: str | None,
    ) -> tuple[str | None, str | None, bool]:
        """Pre-compute parenthetical-stripped team names for fallback matching.

        Strips parentheticals from the RAW (pre-normalization) team names, then
        normalizes the result. This is necessary because normalize_for_matching()
        removes parentheses as punctuation, flattening "(Baseball)" into extra
        tokens rather than removing the content entirely.

        Returns:
            Tuple of (stripped_t1, stripped_t2, has_fallback) where has_fallback
            is True if the stripped versions differ from the originals.
        """
        fallback_t1 = norm_team1
        fallback_t2 = norm_team2

        if raw_team1 and "(" in raw_team1:
            stripped = self._strip_parentheticals(raw_team1)
            if stripped:
                fallback_t1 = normalize_for_matching(stripped)

        if raw_team2 and "(" in raw_team2:
            stripped = self._strip_parentheticals(raw_team2)
            if stripped:
                fallback_t2 = normalize_for_matching(stripped)

        has_fallback = fallback_t1 != norm_team1 or fallback_t2 != norm_team2
        return fallback_t1, fallback_t2, has_fallback

    @staticmethod
    def _leading_pipe_segment(name: str) -> str:
        """The text before the first "|", if it is substantial enough to be a team.

        Minimum 3 chars matches extract_teams_from_separator's own floor — even
        the shortest real abbreviations (USC, LSU, BYU) clear it.
        """
        head = name.split("|")[0].strip()
        return head if len(head) >= 3 else name

    def _prepare_pipe_fallback(
        self,
        raw_team1: str | None,
        raw_team2: str | None,
        norm_team1: str | None,
        norm_team2: str | None,
    ) -> tuple[str | None, str | None, bool]:
        """Pre-compute pipe-trimmed team names for fallback matching (#652).

        `_clean_team_name` deliberately keeps pipe content, on the stated
        expectation that "the matcher will try both sides of the pipe and pick
        the one that matches". No such code existed: this method is it. The
        counter-claim that token_set_ratio handles pipes "naturally" is false —
        the extra tokens are scored against the team, and they cost more than
        the threshold allows:

            "WAGNER | 8.29 | NEC FRONT ROW"  vs "Wagner Seahawks"  = 57.1
            "WAGNER"                         vs "Wagner Seahawks"  = 100.0

        with BOTH_TEAMS_THRESHOLD at 60. Streams that do match this shape today
        do so by luck — "UNLV | 8.29 | FOX" survives only via the abbreviation
        path, and "VIRGINIA | 8.29 | ESPN" clears the floor by 1.5 points.

        Split from the RAW names for the same reason the parenthetical fallback
        does: normalize_for_matching drops "|" as punctuation, so the boundary
        is already gone by the time we hold the normalized form.

        Only the LEADING segment is taken. By this point the classifier has
        already moved any prefix noise (league hint, sport, provider, bare
        datetime) to the back, so what remains before the first pipe is the
        team and what follows is date/time/channel metadata. Trying every
        segment instead would let a venue or network name score as a team.
        """
        fallback_t1 = norm_team1
        fallback_t2 = norm_team2

        if raw_team1 and "|" in raw_team1:
            fallback_t1 = normalize_for_matching(self._leading_pipe_segment(raw_team1))

        if raw_team2 and "|" in raw_team2:
            fallback_t2 = normalize_for_matching(self._leading_pipe_segment(raw_team2))

        has_fallback = fallback_t1 != norm_team1 or fallback_t2 != norm_team2
        return fallback_t1, fallback_t2, has_fallback

    def _score_teams_against_event(
        self,
        team1: str | None,
        team2: str | None,
        event: Event,
    ) -> tuple[MatchMethod, float] | None:
        """Score team names against event teams.

        When both teams are extracted, requires BOTH to match different event teams.

        Args:
            team1: First extracted team name
            team2: Second extracted team name
            event: Event to match against

        Returns:
            Tuple of (method, confidence) if matched, None otherwise
        """

        # Pipe-separated content is NOT handled here (#652). This function
        # scores whatever it is given; token_set_ratio charges for the extra
        # tokens rather than ignoring them ("WAGNER | 8.29 | NEC FRONT ROW"
        # scores 57.1 against "Wagner Seahawks", under the 60 floor), so a
        # short tail like "| Golden 1 Center" survives only by luck. The
        # trimming lives in _prepare_pipe_fallback, tried as the last tier of
        # the candidate loop so an intact name never reaches it.

        if team1 and team2:
            # BOTH teams extracted - require both to match different event teams
            t1_norm = normalize_text(team1)
            t2_norm = normalize_text(team2)

            # Score each stream team against each event team. Short codes
            # score by abbreviation equality ONLY (#472): token_set_ratio
            # gives a spurious 100 when a code is a literal word of an
            # unrelated name ("SEA" in "Portland Sea Dogs") and useless
            # scores for real abbreviations ("SF" vs the Giants = 9).
            def _side_score(stream_norm: str, event_team) -> float:
                # Per-side alias resolution (#480 round 2): an alias is a
                # statement about ONE team, so its canonical name scores
                # this side directly — a single-sided alias must be able to
                # carry its side while the opponent matches by fuzz/abbrev.
                # (_check_alias_match's both-sides path remains as the fast
                # path when every side is aliased.)
                alias_score = 0.0
                canonical = self._resolve_alias(stream_norm, event.league)
                if canonical:
                    alias_score = _best_name_score(canonical, event_team)
                if _is_short_code(stream_norm):
                    base = (
                        100.0
                        if _abbrev_equals(stream_norm, event_team.abbreviation)
                        else 0.0
                    )
                else:
                    base = _best_name_score(stream_norm, event_team)
                score = max(base, alias_score)
                if (
                    not canonical
                    and BOTH_TEAMS_THRESHOLD <= score < HIGH_CONFIDENCE_THRESHOLD
                    and not _has_shared_name_token(stream_norm, event_team)
                ):
                    return 0.0
                return score

            t1_vs_home = _side_score(t1_norm, event.home_team)
            t1_vs_away = _side_score(t1_norm, event.away_team)

            # Short-circuit: the final score can never exceed team1's best side.
            #
            #   best = max(min(t1h, t2a), min(t1a, t2h))  and  min(x, y) <= x
            #   =>   best <= max(t1h, t1a)
            #
            # so when team1 clears neither side, no value of t2 can rescue the
            # pair and the two remaining side scores are wasted. The result is
            # identical — this is the same inequality the min() already encodes,
            # just evaluated before paying for it. Most candidates in a run are
            # unrelated fixtures, so this is the common path: it halves the
            # per-pair scoring work, and with it the alias lookups, short-code
            # checks and fuzzy comparisons underneath (#742).
            if max(t1_vs_home, t1_vs_away) < BOTH_TEAMS_THRESHOLD:
                return None

            t2_vs_home = _side_score(t2_norm, event.home_team)
            t2_vs_away = _side_score(t2_norm, event.away_team)

            # Try both valid assignments (each stream team matches a different event team)
            # Option 1: team1 → home, team2 → away
            # Option 2: team1 → away, team2 → home
            # Use min() to require BOTH teams to have good matches
            option1_score = min(t1_vs_home, t2_vs_away)
            option2_score = min(t1_vs_away, t2_vs_home)

            best_score = max(option1_score, option2_score)

            # Use dedicated threshold for both-teams matching (lower because min() is strict)
            if best_score >= BOTH_TEAMS_THRESHOLD:
                return (MatchMethod.FUZZY, best_score)
            return None

        elif team1 or team2:
            # Only ONE team extracted - fall back to matching against full event name
            # Use stricter threshold since we have less confidence
            single_team = team1 or team2
            single_norm = normalize_text(single_team)

            # Per-side alias resolution (#480 round 2): the canonical name
            # scores against the combined event name like any full name.
            canonical = self._resolve_alias(single_norm, event.league)
            if canonical:
                event_norm_full = normalize_text(
                    f"{event.home_team.name} vs {event.away_team.name}"
                )
                alias_score = fuzz.token_set_ratio(canonical, event_norm_full)
                if alias_score >= HIGH_CONFIDENCE_THRESHOLD:
                    return (MatchMethod.FUZZY, alias_score)

            # Short codes never fuzzy-match a combined event name (#472):
            # abbreviation equality is the only evidence they can offer, and
            # the single-team abbreviation path deliberately requires >=3
            # chars — a lone 2-letter token is noise.
            if _is_short_code(single_norm):
                if len(single_norm) >= 3 and (
                    _abbrev_equals(single_norm, event.home_team.abbreviation)
                    or _abbrev_equals(single_norm, event.away_team.abbreviation)
                ):
                    return (MatchMethod.FUZZY, 100.0)
                return None

            event_name = f"{event.home_team.name} vs {event.away_team.name}"
            event_norm = normalize_text(event_name)

            score = fuzz.token_set_ratio(single_norm, event_norm)

            # For single-team matches, always require high confidence
            if score >= HIGH_CONFIDENCE_THRESHOLD:
                return (MatchMethod.FUZZY, score)
            return None

        return None

    def _score_single_team_against_event(
        self,
        team_norm: str,
        event: "Event",
    ) -> tuple[float, str] | tuple[None, None]:
        """Score a single team name against an event's home and away teams.

        For TEAM_ONLY streams. Returns the best score and which side matched,
        but only when the team clearly matches ONE side and not the other.
        This guards against the (practically impossible) case where the same
        team name scores high on both sides of an event.

        Args:
            team_norm: Normalized candidate team name from the stream
            event: Event to match against

        Returns:
            (score, side) where side is "home" or "away", or (None, None)
        """
        if _is_short_code(team_norm):
            # Short codes match only by abbreviation equality (#472), and a
            # lone 2-letter token stays unmatchable (noise guard).
            if len(team_norm) < 3:
                return None, None
            home_score = (
                100.0 if _abbrev_equals(team_norm, event.home_team.abbreviation) else 0.0
            )
            away_score = (
                100.0 if _abbrev_equals(team_norm, event.away_team.abbreviation) else 0.0
            )
        else:
            home_score = _best_name_score(team_norm, event.home_team)
            away_score = _best_name_score(team_norm, event.away_team)

        # Per-side alias resolution (#480 round 2)
        canonical = self._resolve_alias(team_norm, event.league)
        if canonical:
            home_score = max(home_score, _best_name_score(canonical, event.home_team))
            away_score = max(away_score, _best_name_score(canonical, event.away_team))

        home_matches = home_score >= HIGH_CONFIDENCE_THRESHOLD
        away_matches = away_score >= HIGH_CONFIDENCE_THRESHOLD

        # Require exactly one side to match (not both)
        if home_matches and not away_matches:
            return home_score, "home"
        if away_matches and not home_matches:
            return away_score, "away"

        return None, None

    def _resolve_alias(self, team_name: str, league: str | None) -> str | None:
        """Resolve a team name to its canonical form via alias lookup.

        Priority:
        1. User-defined aliases (database, league-specific) — a user's
           deliberate mapping outranks shipped defaults (#480)
        2. Built-in aliases (TEAM_ALIASES constant) - league-agnostic
        3. International country name auto-resolution (e.g. "brasil" → "Brazil")

        Args:
            team_name: The team name to look up
            league: The league code for user-defined alias lookup

        Returns:
            Canonical team name if alias found, None otherwise
        """
        # Memoized per (name, league): _side_score resolves both stream sides
        # against every candidate event's league, so the same lookup repeats
        # once per candidate over a pool of at most a few leagues.
        memo_key = (team_name, league)
        cached = self._alias_resolve_cache.get(memo_key, _UNRESOLVED)
        if not isinstance(cached, _Unresolved):
            return cached
        resolved = self._resolve_alias_uncached(team_name, league)
        self._alias_resolve_cache[memo_key] = resolved
        return resolved

    def _resolve_alias_uncached(self, team_name: str, league: str | None) -> str | None:
        """Alias resolution proper — see :meth:`_resolve_alias`."""
        normalized = normalize_text(team_name)

        # User-defined aliases first — deliberate user mappings outrank
        # shipped defaults (#480)
        if league and self._user_aliases:
            user_canonical = self._lookup_user_alias(normalized, league)
            if user_canonical:
                return user_canonical

        # Then built-in aliases (league-agnostic)
        canonical = _NORMALIZED_TEAM_ALIASES.get(normalized)
        if canonical:
            return canonical

        # Finally, try automatic country name resolution for national-team sports.
        # Memoized: the same stream team names are re-checked against every
        # candidate event, so resolve + log once per unique name, not per
        # candidate (the 147x [ALIAS] spam in #256). Self-maps (e.g. an English
        # name that resolves to itself) are not logged — they carry no signal.
        if team_name not in self._country_resolve_cache:
            country_canonical = self._country_resolver.resolve(team_name)
            self._country_resolve_cache[team_name] = country_canonical
            if country_canonical and country_canonical != _normalize_country(team_name):
                logger.debug(
                    "[ALIAS] Country name resolved: %r → %r",
                    team_name,
                    country_canonical,
                )
        return self._country_resolve_cache[team_name]

    def _check_alias_match(
        self,
        team1: str | None,
        team2: str | None,
        event: Event,
    ) -> tuple[MatchMethod, float] | None:
        """Check if extracted teams match via alias lookup.

        Aliases provide 100% confidence matches for known abbreviations:
        "Man U" → "Manchester United"

        Checks both built-in aliases (constants.py) and user-defined aliases
        (database). User-defined aliases are league-specific.

        Args:
            team1: First extracted team name (normalized)
            team2: Second extracted team name (normalized)
            event: Event to match against

        Returns:
            Tuple of (ALIAS, 100.0) if both teams match via alias, None otherwise
        """
        if not team1 and not team2:
            return None

        # Get event league for user-defined alias lookup
        event_league = event.league

        # Resolve aliases BEFORE building patterns. Aliases are rare, but this
        # runs once per (stream x candidate event) pair, so generating the two
        # pattern lists first meant allocating them for every pair whose stream
        # has no alias at all — the single largest wasted allocation in the
        # match loop. Both sides must resolve when both were extracted (the
        # return conditions below), so a half-resolved pair can bail just as
        # early as an unresolved one.
        canonical1 = self._resolve_alias(team1, event_league) if team1 else None
        canonical2 = self._resolve_alias(team2, event_league) if team2 else None

        if team1 and team2:
            if not (canonical1 and canonical2):
                return None
        elif not (canonical1 or canonical2):
            return None

        # Generate patterns for alias checking
        home_patterns = self._fuzzy.generate_team_patterns(event.home_team)
        away_patterns = self._fuzzy.generate_team_patterns(event.away_team)

        def _hits(canonical: str) -> bool:
            return any(canonical in tp.pattern for tp in home_patterns) or any(
                canonical in tp.pattern for tp in away_patterns
            )

        team1_match = bool(canonical1) and _hits(canonical1)
        team2_match = bool(canonical2) and _hits(canonical2)

        # Need both teams to match via alias (if both were extracted)
        if team1 and team2:
            if team1_match and team2_match:
                return (MatchMethod.ALIAS, 100.0)
        elif team1 and team1_match:
            return (MatchMethod.ALIAS, 100.0)
        elif team2 and team2_match:
            return (MatchMethod.ALIAS, 100.0)

        return None

    def _get_identity_index(self) -> TeamIdentityIndex | None:
        """The global team identity index, shared across matchers (#609).

        Absent without a db_factory, and absent (with a warning) if team_cache
        has not been seeded yet — a fresh install matches before its first cache
        refresh, and an empty index must not veto everything.

        Building one costs ~47ms — an unfiltered scan of team_cache (11k rows on
        a real install) plus normalising every surface form. A matcher is
        constructed per event group, so that was ~16s of a run spent rebuilding
        the same index. It is now memoized per process behind a TTL, the same
        shape as _cached_team_identity in services/sports_data.py and for the
        same reason: team identity is effectively static, and the TTL bounds how
        long a mid-run cache refresh stays invisible.

        The instance flag is kept so a matcher that has already resolved the
        index does not re-check the TTL on every candidate.
        """
        if self._identity_loaded:
            return self._identity_index
        self._identity_loaded = True

        if not self._db:
            return None

        index = _shared_identity_index(self._db)
        if index is None:
            return None

        self._identity_index = index
        return index

    def _fixture_leagues(self, ctx: MatchContext) -> set[str] | None:
        """Leagues where this stream's two sides could actually meet.

        Returns None when identity resolution can say nothing — an unseeded
        cache, an unresolvable name, a one-sided stream — in which case matching
        proceeds exactly as before. An empty set is a real answer, not a
        failure: both sides named real teams that share no league at all.
        """
        index = self._get_identity_index()
        if index is None:
            return None
        return index.fixture_leagues(ctx.team1, ctx.team2)

    def _fixture_vetoes(self, fixture_leagues: set[str] | None, league: str) -> bool:
        """Should the fixture gate refuse a candidate from ``league``?

        Only when identity resolution spoke (``fixture_leagues`` is not None),
        this league is not among the answers, AND the index actually knows the
        league's teams. A league the cache has never seen — custom, unseeded,
        added since the last refresh — cannot be judged, only deferred (#619).
        """
        if fixture_leagues is None or league in fixture_leagues:
            return False
        index = self._identity_index
        return index is not None and index.knows_league(league)

    def _load_user_aliases(self) -> UserAliasCache:
        """Load user-defined aliases from database into memory cache.

        Aliases are keyed by (alias_text, league) for efficient lookup.
        Called once at matcher initialization.

        Returns:
            Dict mapping (alias, league) -> team_name
        """
        if not self._db:
            return {}

        try:
            from teamarr.database.aliases import list_aliases

            with self._db() as conn:
                aliases = list_aliases(conn)

            cache: UserAliasCache = {}
            for alias in aliases:
                # Key by (matcher-normalized alias, lowercased league). The
                # lookup side is normalize_for_matching output — storing the
                # raw lowercased text meant any alias containing punctuation
                # ("D-backs", "St. Louis") could never fire (#480).
                key = (normalize_text(alias.alias), alias.league.lower())
                cache[key] = normalize_text(alias.team_name)

            if cache:
                logger.debug("[ALIAS] Loaded %d user-defined aliases from database", len(cache))
            return cache

        except Exception as e:
            logger.warning("[ALIAS] Failed to load user aliases from database: %s", e)
            return {}

    def _build_reverse_cache(self) -> dict[str, list[tuple[str, str]]]:
        """Build reverse alias lookup: alias_text -> [(canonical, league), ...]

        Enables finding canonical name without knowing league first.
        This is critical for multi-league groups where the classifier can't
        detect the league from the stream name.

        Returns:
            Dict mapping normalized alias to list of (canonical_name, league) tuples
        """
        reverse: dict[str, list[tuple[str, str]]] = {}
        for (alias, league), canonical in self._user_aliases.items():
            if alias not in reverse:
                reverse[alias] = []
            reverse[alias].append((canonical, league))

        if reverse:
            logger.debug(
                "[ALIAS] Built reverse cache with %d unique aliases",
                len(reverse),
            )
        return reverse

    def _reverse_resolve_alias(self, team_name: str) -> list[tuple[str, str | None]]:
        """Resolve team name to ALL canonical forms via reverse lookup.

        Returns all matching aliases across all leagues, enabling the caller
        to try matching against each candidate. This is the key to solving
        the multi-league matching problem when league_hint is None.

        Args:
            team_name: Extracted team name to check

        Returns:
            List of (canonical_name, league) tuples. League is None for built-in aliases.
            Empty list if no alias found.
        """
        if not team_name:
            return []

        results: list[tuple[str, str | None]] = []
        normalized = team_name.lower()

        # Check built-in aliases first (already league-agnostic)
        canonical = TEAM_ALIASES.get(normalized)
        if canonical:
            results.append((canonical, None))

        # Check reverse cache - returns ALL leagues where this alias exists
        if self._reverse_aliases:
            matches = self._reverse_aliases.get(normalized, [])
            results.extend(matches)

        return results

    def _try_reverse_alias_match(
        self,
        ctx: MatchContext,
        events: Sequence[tuple[str, Event]],
        enabled_leagues: list[str],
    ) -> MatchOutcome | None:
        """Try matching with reverse alias resolution.

        When initial matching fails and we don't know the league, check if either
        team name is a user-defined alias. If so, we get both the canonical name
        AND the league from the alias, then retry matching with that information.

        Args:
            ctx: Match context with team names
            events: List of (league, event) tuples to match against
            enabled_leagues: List of enabled league codes

        Returns:
            Successful MatchOutcome if reverse alias helps, None otherwise
        """
        if not ctx.team1 and not ctx.team2:
            return None

        # Try reverse alias resolution for both teams
        team1_aliases = self._reverse_resolve_alias(ctx.team1) if ctx.team1 else []
        team2_aliases = self._reverse_resolve_alias(ctx.team2) if ctx.team2 else []

        if not team1_aliases and not team2_aliases:
            return None

        # Collect candidate leagues from aliases (only those that are enabled)
        candidate_leagues: set[str] = set()
        for _canonical, league in team1_aliases + team2_aliases:
            if league and league.lower() in [lg.lower() for lg in enabled_leagues]:
                candidate_leagues.add(league.lower())

        logger.debug(
            "[REVERSE_ALIAS] team1=%s → %s, team2=%s → %s, candidates=%s",
            ctx.team1,
            team1_aliases,
            ctx.team2,
            team2_aliases,
            candidate_leagues,
        )

        if not candidate_leagues and not any(lg is None for _, lg in team1_aliases + team2_aliases):
            # No enabled leagues from aliases and no built-in aliases
            return None

        # Filter events to candidate leagues (if any league-specific aliases found)
        if candidate_leagues:
            league_events = [(lg, ev) for lg, ev in events if lg.lower() in candidate_leagues]
        else:
            league_events = events

        if not league_events:
            return None

        # Try each alias combination until one matches
        # Use original team name if no alias, otherwise try each alias
        team1_candidates = team1_aliases if team1_aliases else [(ctx.team1, None)]
        team2_candidates = team2_aliases if team2_aliases else [(ctx.team2, None)]

        for canonical1, _league1 in team1_candidates:
            for canonical2, _league2 in team2_candidates:
                # Build retry context with resolved names
                retry_ctx = MatchContext(
                    stream_name=ctx.stream_name,
                    stream_id=ctx.stream_id,
                    group_id=ctx.group_id,
                    target_date=ctx.target_date,
                    generation=ctx.generation,
                    user_tz=ctx.user_tz,
                    stream_tz=ctx.stream_tz,
                    classified=ctx.classified,
                    team1=canonical1,
                    team2=canonical2,
                    sport_durations=ctx.sport_durations,
                )

                retry_result = self._match_against_multi_league_events(retry_ctx, league_events)

                if retry_result.is_matched:
                    logger.info(
                        "[REVERSE_ALIAS_MATCH] stream_id=%d '%s/%s' → '%s/%s' in %s",
                        ctx.stream_id,
                        ctx.team1,
                        ctx.team2,
                        canonical1,
                        canonical2,
                        retry_result.detected_league,
                    )
                    # Update parsed team info to show original stream names
                    retry_result.parsed_team1 = ctx.team1
                    retry_result.parsed_team2 = ctx.team2
                    return retry_result

        return None

    def _lookup_user_alias(self, team_name: str, league: str) -> str | None:
        """Look up a team name in user-defined aliases.

        Args:
            team_name: The team name to look up (will be normalized)
            league: The league code to filter by

        Returns:
            Canonical team name if alias found, None otherwise
        """
        if not self._user_aliases:
            return None

        key = (normalize_text(team_name), league.lower())
        return self._user_aliases.get(key)

    def _disambiguate_by_time(
        self,
        events: list[Event],
        stream_time: time,
        user_tz: ZoneInfo,
    ) -> Event | None:
        """Pick event closest to stream time for doubleheaders."""
        if len(events) <= 1:
            return events[0] if events else None

        # Combine stream time with event date
        ref_date = events[0].start_time.astimezone(user_tz).date()
        stream_dt = datetime.combine(ref_date, stream_time, tzinfo=user_tz)

        return min(events, key=lambda e: abs(e.start_time.astimezone(user_tz) - stream_dt))

    def _cache_result(self, ctx: MatchContext, result: MatchOutcome) -> None:
        """Cache a successful match."""
        if not result.event:
            return

        cached_data = event_to_cache_data(result.event)

        # Store the original match method so we can show "Cache (origin: fuzzy)" etc.
        match_method_value = result.match_method.value if result.match_method else None

        self._cache.set(
            group_id=ctx.group_id,
            stream_id=ctx.stream_id,
            stream_name=ctx.stream_name,
            event_id=result.event.id,
            league=result.detected_league or result.event.league,
            cached_data=cached_data,
            generation=ctx.generation,
            match_method=match_method_value,
        )

    def _reconstruct_event(self, cached_data: dict[str, Any]) -> Event | None:
        """Reconstruct Event from cached dict."""
        try:
            # Handle datetime parsing
            start_time = cached_data.get("start_time")
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)
            if not isinstance(start_time, datetime):
                return None  # missing/invalid start_time -> treat as cache miss

            # Reconstruct teams (use `or {}` to handle explicit None values)
            home_data = cached_data.get("home_team") or {}
            away_data = cached_data.get("away_team") or {}

            home_team = Team(
                id=home_data.get("id", ""),
                provider=home_data.get("provider", ""),
                name=home_data.get("name", ""),
                short_name=home_data.get("short_name", ""),
                abbreviation=home_data.get("abbreviation", ""),
                league=home_data.get("league", ""),
                sport=home_data.get("sport", ""),
                logo_url=home_data.get("logo_url"),
                color=home_data.get("color"),
            )

            away_team = Team(
                id=away_data.get("id", ""),
                provider=away_data.get("provider", ""),
                name=away_data.get("name", ""),
                short_name=away_data.get("short_name", ""),
                abbreviation=away_data.get("abbreviation", ""),
                league=away_data.get("league", ""),
                sport=away_data.get("sport", ""),
                logo_url=away_data.get("logo_url"),
                color=away_data.get("color"),
            )


            status_data = cached_data.get("status") or {}
            status = EventStatus(
                state=status_data.get("state", "scheduled"),
                detail=status_data.get("detail"),
                period=status_data.get("period"),
                clock=status_data.get("clock"),
            )

            # Handle broadcast/broadcasts field compatibility
            broadcast_val = cached_data.get("broadcasts") or cached_data.get("broadcast")
            broadcasts = (
                broadcast_val
                if isinstance(broadcast_val, list)
                else [broadcast_val]
                if broadcast_val
                else []
            )

            # Reconstruct Venue from dict if present

            venue_data = cached_data.get("venue")
            venue = None
            if venue_data:
                if isinstance(venue_data, dict):
                    venue = Venue(
                        name=venue_data.get("name", ""),
                        city=venue_data.get("city"),
                        state=venue_data.get("state"),
                        country=venue_data.get("country"),
                    )
                else:
                    venue = venue_data  # Already a Venue

            # Reconstruct segment_times for UFC events
            # Use `or {}` to handle both missing key AND explicit None value
            segment_times_data = cached_data.get("segment_times") or {}
            segment_times = {}
            for seg_name, seg_time in segment_times_data.items():
                if isinstance(seg_time, str):
                    segment_times[seg_name] = datetime.fromisoformat(seg_time)
                elif seg_time is not None:
                    segment_times[seg_name] = seg_time

            # Parse main_card_start if present
            main_card_start = cached_data.get("main_card_start")
            if isinstance(main_card_start, str):
                main_card_start = datetime.fromisoformat(main_card_start)

            # Reconstruct racing sessions, if present

            sessions = []
            for session_data in cached_data.get("sessions") or []:
                session_start = session_data.get("start_time")
                if isinstance(session_start, str):
                    session_start = datetime.fromisoformat(session_start)
                results = [
                    RacingResult(
                        driver_name=r.get("driver_name", ""),
                        team_name=r.get("team_name"),
                        position=r.get("position"),
                        grid_position=r.get("grid_position"),
                        points=r.get("points"),
                        fastest_lap=r.get("fastest_lap", False),
                        status=r.get("status"),
                    )
                    for r in session_data.get("results") or []
                ]
                sessions.append(
                    RacingSession(
                        code=session_data.get("code", ""),
                        name=session_data.get("name", ""),
                        start_time=session_start,
                        results=results,
                    )
                )

            # Self-heal stale cache rows: every modern provider populates
            # short_name (falling back to the full name when no shorter form
            # exists), so a row with name set but short_name empty is data
            # written before the field flowed end-to-end. Treat as cache miss
            # so the matcher re-fetches and re-caches with proper data.
            for team in (home_team, away_team):
                if team.name and not team.short_name:
                    logger.debug(
                        "[MATCH_CACHE] Stale: team %r has name but no short_name; "
                        "invalidating",
                        team.name,
                    )
                    return None

            return Event(
                id=cached_data.get("id", ""),
                provider=cached_data.get("provider", ""),
                name=cached_data.get("name", ""),
                short_name=cached_data.get("short_name", ""),
                start_time=start_time,
                home_team=home_team,
                away_team=away_team,
                status=status,
                league=cached_data.get("league", ""),
                sport=cached_data.get("sport", ""),
                season_type=cached_data.get("season_type"),
                venue=venue,
                broadcasts=broadcasts,
                segment_times=segment_times,
                main_card_start=main_card_start,
                circuit_name=cached_data.get("circuit_name"),
                sessions=sessions,
                tournament_id=cached_data.get("tournament_id"),
                tournament_name=cached_data.get("tournament_name"),
                round_name=cached_data.get("round_name"),
                court=cached_data.get("court"),
                draw_type=cached_data.get("draw_type"),
                is_major=bool(cached_data.get("is_major", False)),
            )
        except Exception as e:
            logger.warning("[MATCH_CACHE] Failed to reconstruct event from cache: %s", e)
            return None
